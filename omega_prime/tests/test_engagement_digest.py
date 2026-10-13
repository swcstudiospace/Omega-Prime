"""Weekly engagement digest (PLAN-G3): heartbeat kind + --summary + durable block.

Hermetic ``tmp_path`` fixtures with injected clocks — no wall-clock, no
repo-root writes, no network. The report script is exercised as a
subprocess for ``--summary`` and in-process (via :func:`summarize`) by
the injected tick checkers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omega_prime.cron.heartbeat import (
    ENGAGEMENT_DIGEST_INTERVAL_SECONDS,
    ENGAGEMENT_DIGEST_KIND,
    clear_engagement_digest,
    list_engagement_digests,
    schedule_engagement_digest,
    tick_engagement_digest,
)
from omega_prime.cron.scheduler import JobStore
from sidecar.simulation import load_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "engagement-report.py"

SIDECAR_REPORTS_ENV = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"

T0 = "2026-10-01T00:00:00+00:00"
T1 = "2026-10-05T12:00:00+00:00"
T2 = "2026-10-08T12:00:00+00:00"

SUMMARY_RE = re.compile(r"^runs=\d+ pass=\d+% gaps=\d+ findings=\+\d+/-\d+$")

T_STAMP = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC).timestamp()


def _store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "cron" / "jobs.json")


def _child_env() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items() if key != SIDECAR_REPORTS_ENV
    }


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=_child_env(),
        cwd=str(REPO_ROOT),
    )


def seed_transitions(directory: Path, rows: list[dict[str, str]]) -> None:
    path = directory / "transitions.ndjson"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def seed_last_run(directory: Path) -> None:
    cases, errors = load_suite()
    assert not errors
    observations = {}
    for case in cases:
        expected = case.get("expected")
        detection = expected.get("detection") if isinstance(expected, dict) else ""
        observations[str(case["case_id"])] = {
            "observed_detection": str(detection),
            "response": "loopback-seam",
        }
    (directory / "last-run.json").write_text(
        json.dumps({"suite": "smoke", "observations": observations}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _finding_row(
    case_id: str, response: str, digest: str, ts: str
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "class": "test",
        "severity": "low",
        "observed_detection": "x",
        "verdict": "fail",
        "response": response,
        "dedupe_hash": digest,
        "occurrence_count": 1,
        "ts": ts,
        "last_seen": ts,
    }


def seed_findings(directory: Path, rows: list[dict[str, object]]) -> None:
    path = directory / "findings.ndjson"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def test_schedule_validates_interval(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="interval"):
        schedule_engagement_digest(store, interval_seconds=0, due_at=0)
    with pytest.raises(ValueError, match="interval"):
        schedule_engagement_digest(store, interval_seconds=-5, due_at=0)


def test_schedule_list_clear_roundtrip(tmp_path):
    store = _store(tmp_path)
    job_id = schedule_engagement_digest(store, due_at=T_STAMP)
    jobs = list_engagement_digests(store)
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["kind"] == ENGAGEMENT_DIGEST_KIND
    assert jobs[0]["interval_seconds"] == ENGAGEMENT_DIGEST_INTERVAL_SECONDS
    assert len(list_engagement_digests(_store(tmp_path))) == 1
    assert clear_engagement_digest(store, job_id) == {"cleared": job_id}
    assert list_engagement_digests(store) == []
    assert "error" in clear_engagement_digest(store, job_id)


def test_tick_fires_each_due_job_once(tmp_path):
    store = _store(tmp_path)
    first = schedule_engagement_digest(store, due_at=T_STAMP)
    second = schedule_engagement_digest(store, due_at=T_STAMP)
    seen = []

    def checker(reports_dir, now):
        seen.append((reports_dir, now))
        return {"state": "ok", "line": "runs=1 pass=100% gaps=0 findings=+0/-0"}

    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    fired = tick_engagement_digest(store, now, checker)
    assert {job["id"] for job in fired} == {first, second}
    assert len(seen) == 2
    assert all(isinstance(moment, datetime) for _, moment in seen)
    for job in fired:
        assert job["last_ran_at"] == T_STAMP
        assert job["due_at"] == T_STAMP + ENGAGEMENT_DIGEST_INTERVAL_SECONDS
        assert len(job["history"]) == 1
        assert job["last_result"]["state"] == "ok"
    # Same instant again: claimed/history dedupe fires zero.
    assert tick_engagement_digest(store, now, checker) == []
    assert len(seen) == 2


def test_tick_custom_interval_and_naive_clock(tmp_path):
    store = _store(tmp_path)
    job_id = schedule_engagement_digest(store, interval_seconds=60, due_at=T_STAMP)
    naive = datetime(2026, 10, 8, 12, 0, 0)  # naive ⇒ UTC
    fired = tick_engagement_digest(store, naive, lambda d, n: {"state": "ok"})
    assert [job["id"] for job in fired] == [job_id]
    assert fired[0]["due_at"] == T_STAMP + 60
    assert fired[0]["last_ran_at"] == T_STAMP


def _summarize_fn():
    """The real ``summarize()`` loaded from the report script."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "engagement_report_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.summarize


def test_tick_empty_reports_dir_records_no_reports(tmp_path, monkeypatch):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    monkeypatch.setenv(SIDECAR_REPORTS_ENV, str(fixture))
    store = _store(tmp_path)
    job_id = schedule_engagement_digest(store, due_at=T_STAMP)
    summarize = _summarize_fn()
    seen = []

    def checker(reports_dir, now):
        seen.append(reports_dir)
        result = summarize(Path(reports_dir), None)
        return {"state": result["state"], "line": result["line"]}

    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    fired = tick_engagement_digest(store, now, checker)
    assert [job["id"] for job in fired] == [job_id]
    assert seen == [str(fixture)]
    assert fired[0]["last_result"] == {
        "state": "no-reports",
        "line": "runs=0 pass=0% gaps=0 findings=+0/-0",
    }


def test_tick_missing_reports_dir_records_no_reports(tmp_path, monkeypatch):
    missing = tmp_path / "absent"
    monkeypatch.setenv(SIDECAR_REPORTS_ENV, str(missing))
    store = _store(tmp_path)
    schedule_engagement_digest(store, due_at=T_STAMP)
    summarize = _summarize_fn()
    fired = tick_engagement_digest(
        store, T_STAMP, lambda d, n: summarize(Path(d), None)
    )
    assert len(fired) == 1
    assert fired[0]["last_result"]["state"] == "no-reports"


def test_tick_checker_exception_recorded_without_corruption(tmp_path):
    store = _store(tmp_path)
    job_id = schedule_engagement_digest(store, due_at=T_STAMP)

    def checker(reports_dir, now):
        raise RuntimeError("boom")

    fired = tick_engagement_digest(store, T_STAMP, checker)
    assert [job["id"] for job in fired] == [job_id]
    assert fired[0]["last_result"] == {"state": "error", "error": "boom"}
    assert len(fired[0]["history"]) == 1
    # The store reloads cleanly with history intact.
    reloaded = [job for job in _store(tmp_path).jobs if job["id"] == job_id]
    assert len(reloaded) == 1
    assert reloaded[0]["last_result"]["state"] == "error"


def test_summary_matches_full_report_values(tmp_path):
    from sidecar.findings import dedupe_hash

    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(
        fixture,
        [
            {"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T1},
            {"cause": "run-completed", "from": "RUNNING", "to": "IDLE", "ts": T1},
            {"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T2},
            {"cause": "run-completed", "from": "RUNNING", "to": "IDLE", "ts": T2},
        ],
    )
    seed_last_run(fixture)
    hash_abuse = dedupe_hash("abuse-01", "resp-a")
    hash_jailbreak = dedupe_hash("jailbreak-01", "resp-j")
    hash_old = "0" * 64
    seed_findings(
        fixture,
        [
            _finding_row("abuse-01", "resp-a", hash_abuse, T1),
            _finding_row("jailbreak-01", "resp-j", hash_jailbreak, T2),
        ],
    )
    out_dir = fixture / "reports"
    out_dir.mkdir()
    (out_dir / "engagement-2026-09-24_2026-10-01.md").write_text(
        "# Engagement report\n\n"
        "<!-- findings-snapshot: "
        f"{json.dumps({hash_abuse: 'abuse-01', hash_old: 'old-01'}, sort_keys=True)} -->\n",
        encoding="utf-8",
    )

    before = sorted(str(path) for path in fixture.rglob("*"))
    summary = run_script("--summary", "--reports-dir", str(fixture), "--since", T0)
    assert summary.returncode == 0, summary.stderr
    assert SUMMARY_RE.match(summary.stdout.strip())
    # --summary writes no files: the fixture tree is byte-identical after.
    assert sorted(str(path) for path in fixture.rglob("*")) == before

    full = run_script("--reports-dir", str(fixture), "--since", T0)
    assert full.returncode == 0, full.stderr
    runs_match = re.search(r"- runs in window: (\d+)", full.stdout)
    assert runs_match is not None
    runs = int(runs_match.group(1))
    gap_lines = [
        line
        for line in _section(full.stdout, "## Detection gaps")
        if line.startswith("- ") and line != "- none"
    ]
    delta = re.search(r"- new: (\d+), rescinded: (\d+)", full.stdout)
    assert delta is not None
    wins = total = 0
    for line in _section(full.stdout, "## Per-technique pass rates"):
        match = re.match(r"\| \S+ \| (\d+)/(\d+)", line)
        if match:
            wins += int(match.group(1))
            total += int(match.group(2))
    pct = round(100 * wins / total) if total else 0
    expected = (
        f"runs={runs} pass={pct}% gaps={len(gap_lines)} "
        f"findings=+{delta.group(1)}/-{delta.group(2)}"
    )
    assert summary.stdout.strip() == expected


def _section(stdout: str, heading: str) -> list[str]:
    lines = stdout.splitlines()
    start = lines.index(heading) + 1
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") or line.startswith("# "):
            break
        body.append(line)
    return body


def test_summary_empty_and_missing_dirs_are_zeros(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    for target in (str(fixture), str(tmp_path / "absent")):
        proc = run_script("--summary", "--reports-dir", target, "--since", T0)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "runs=0 pass=0% gaps=0 findings=+0/-0"


def test_summary_corrupt_findings_is_exit_1(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(
        fixture,
        [{"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T1}],
    )
    (fixture / "findings.ndjson").write_text("not json\n", encoding="utf-8")
    proc = run_script("--summary", "--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_durable_status_carries_engagement_block(tmp_path, monkeypatch):
    import json as _json

    from omega_prime.tools.durable_surface import register_durable_surface_tools
    from omega_prime.tools.registry import ToolRegistry

    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(
        fixture,
        [{"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T1}],
    )
    seed_last_run(fixture)
    monkeypatch.setenv(SIDECAR_REPORTS_ENV, str(fixture))

    registry = ToolRegistry()
    register_durable_surface_tools(registry, tmp_path)
    body = _json.loads(registry.dispatch("durable_status", {}))
    assert body["journal"] == "absent"
    assert "roe" in body  # journal/checkpoint keys unchanged
    engagement = body["engagement"]
    assert engagement["state"] == "ok"
    assert SUMMARY_RE.match(engagement["line"])
    assert engagement["report"] is None  # no baseline report on disk yet
    assert engagement["generated_at"] == T1


def test_durable_status_no_reports_state(tmp_path, monkeypatch):
    import json as _json

    from omega_prime.tools.durable_surface import register_durable_surface_tools
    from omega_prime.tools.registry import ToolRegistry

    monkeypatch.setenv(SIDECAR_REPORTS_ENV, str(tmp_path / "absent"))
    registry = ToolRegistry()
    register_durable_surface_tools(registry, tmp_path)
    body = _json.loads(registry.dispatch("durable_status", {}))
    assert body["journal"] == "absent"
    assert body["engagement"] == {
        "state": "no-reports",
        "line": "runs=0 pass=0% gaps=0 findings=+0/-0",
        "report": None,
        "generated_at": None,
    }


def test_tick_ignores_other_kinds(tmp_path):
    store = _store(tmp_path)
    store.schedule("plain job", T_STAMP)
    calls = []
    assert tick_engagement_digest(store, T_STAMP, lambda d, n: calls.append(1)) == []
    assert calls == []

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


# --------------------------------------------------------------------------
# --digest (batch-18 R3): stdout-only plain-text digest, at most 5 lines
# --------------------------------------------------------------------------

DIGEST_ZERO = "runs=0 pass=0% gaps=0 findings=+0/-0"
DIGEST_COVERAGE_RE = re.compile(
    r"^coverage covered=\d+/\d+ gappy=\d+ unexercised=\d+ risk=\d+\.\d$"
)
SCHEMA_VOCAB = {
    "abuse",
    "auth-bypass",
    "cost-abuse",
    "jailbreak",
    "malformed-input",
    "prompt-injection",
}


def _digest_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "engagement_report_digest_under_test", str(SCRIPT)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_digest_history(directory: Path, wrong: dict[str, str] | None = None) -> None:
    """Two runs of history; every case passes except the ``wrong`` ids."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(
        directory,
        [
            {"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T1},
            {"cause": "run-completed", "from": "RUNNING", "to": "IDLE", "ts": T1},
            {"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T2},
            {"cause": "run-completed", "from": "RUNNING", "to": "IDLE", "ts": T2},
        ],
    )
    seed_last_run(directory)
    if wrong:
        path = directory / "last-run.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        for case_id, got in wrong.items():
            document["observations"][case_id]["observed_detection"] = got
        path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")


def _digest(fixture: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return run_script("--digest", "--reports-dir", str(fixture), "--since", T0, *extra)


def test_digest_empty_and_missing_history_is_zero_not_error(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    for target in (fixture, tmp_path / "absent"):
        proc = _digest(target)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == [
            DIGEST_ZERO,
            "coverage covered=0/0 gappy=0 unexercised=0 risk=0.0",
            "worst=none",
        ]
        assert proc.stdout.endswith("worst=none\n")


def test_digest_all_pass_line_shapes(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)

    proc = _digest(fixture)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert proc.stdout.endswith("\n")
    assert SUMMARY_RE.match(lines[0])
    assert lines[0].startswith("runs=2 pass=100% gaps=0 ")
    assert lines[1] == "coverage covered=6/6 gappy=0 unexercised=0 risk=100.0"
    assert lines[2] == "worst=none"
    assert len(lines) == 3  # no gate line without --threshold/--baseline
    # L1 is exactly the --summary line.
    summary = run_script("--summary", "--reports-dir", str(fixture), "--since", T0)
    assert summary.stdout.strip() == lines[0]


def test_digest_gappy_lists_worst_schema_classes_by_weighted_points(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture, wrong={"abuse-01": "x", "auth-bypass-01": "x"})

    proc = _digest(fixture)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert SUMMARY_RE.match(lines[0])
    assert DIGEST_COVERAGE_RE.match(lines[1])
    assert lines[1].startswith("coverage covered=4/6 gappy=2 unexercised=0 risk=")
    # auth-bypass is critical (10 pts) and outranks abuse (high, 5 pts).
    assert lines[2] == "worst=auth-bypass,abuse"


def test_digest_gate_pass_and_fail_exit_codes(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture, wrong={"abuse-01": "x"})

    passing = _digest(fixture, "--threshold", "gate:50")
    assert passing.returncode == 0, passing.stderr
    lines = passing.stdout.splitlines()
    assert len(lines) == 4
    assert lines[3].startswith("Gate: PASS (pass-rate ")
    assert passing.stderr == ""

    failing = _digest(fixture, "--threshold", "gate:100")
    assert failing.returncode == 3
    fail_lines = failing.stdout.splitlines()
    assert fail_lines[:3] == lines[:3]
    assert fail_lines[3] == "Gate: FAIL"
    assert failing.stderr.startswith("gate: FAIL (pass-rate ")

    floor = _digest(fixture, "--baseline", "abuse=90")
    assert floor.returncode == 3
    assert floor.stdout.splitlines()[3].startswith("Gate: FAIL (floors: abuse ")


def test_digest_unknown_baseline_class_exits_2(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)

    proc = _digest(fixture, "--baseline", "nope=50")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "unknown --baseline class 'nope'" in proc.stderr


def test_digest_report_error_exits_1(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(
        fixture,
        [{"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T1}],
    )
    (fixture / "findings.ndjson").write_text("not json\n", encoding="utf-8")

    proc = _digest(fixture)
    assert proc.returncode == 1
    assert "error:" in proc.stderr
    assert proc.stdout == ""


def test_digest_conflicts_with_json_and_summary(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)

    with_json = _digest(fixture, "--json")
    assert with_json.returncode == 1
    assert with_json.stdout == ""
    assert with_json.stderr.strip() == "error: --digest cannot be combined with --json"
    with_summary = _digest(fixture, "--summary")
    assert with_summary.returncode == 1
    assert with_summary.stdout == ""
    assert (
        with_summary.stderr.strip()
        == "error: --digest cannot be combined with --summary"
    )


def test_digest_is_deterministic_writes_no_files_and_is_short(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture, wrong={"abuse-01": "x"})
    before = sorted(str(path) for path in fixture.rglob("*"))

    first = _digest(fixture, "--threshold", "gate-risk:10", "--baseline", "abuse=0")
    second = _digest(fixture, "--threshold", "gate-risk:10", "--baseline", "abuse=0")
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    assert 1 <= len(first.stdout.splitlines()) <= 5
    assert "\r" not in first.stdout
    # --digest ignores the file-writing flags exactly like --summary.
    ignored = _digest(fixture, "--out", str(tmp_path / "ignored.md"))
    assert ignored.returncode == 0, ignored.stderr
    assert not (tmp_path / "ignored.md").exists()
    assert sorted(str(path) for path in fixture.rglob("*")) == before


def test_digest_never_carries_finding_free_text(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)
    evil = "ev\nil\x1b[31mRED\x1b[0m\r\nworst=pwned"
    seed_findings(
        fixture,
        [
            {
                **_finding_row("abuse-01", evil, "a" * 64, T1),
                "case_id": evil,
                "class": evil,
            }
        ],
    )

    proc = _digest(fixture)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) == 3
    assert "\x1b" not in proc.stdout
    assert "\r" not in proc.stdout
    assert "pwned" not in proc.stdout
    assert "RED" not in proc.stdout
    assert lines[2] == "worst=none"


def test_digest_maps_unknown_classes_to_other(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    module = _digest_module()
    fixture = tmp_path / "reports"
    fixture.mkdir()
    evil = "evil\n\x1b[31mclass"
    stamp = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    window = {
        "window_start": stamp,
        "window_end": stamp,
        "runs": 1,
        "cases": [
            ("x-01", "detected", "", evil),
            ("x-02", "detected", "", evil + "2"),
            ("abuse-01", "detected", "", "abuse"),
        ],
        "observations": {},
        "gaps": [],
        "pass_rows": [("x-01", "0/0 (n/a)")],
        "current_findings": {},
        "history_empty": False,
    }
    monkeypatch.setattr(module, "_collect_window", lambda *_args: window)

    result = module.build_digest(fixture, T0)
    assert result["state"] == "ok"
    assert result["gate"] is None
    worst_line = result["lines"][2]
    assert worst_line == "worst=abuse,other"
    assert all("\x1b" not in line and "\n" not in line for line in result["lines"])
    assert set(worst_line.removeprefix("worst=").split(",")) <= SCHEMA_VOCAB | {"other"}


def test_digest_composes_with_export_jsonl(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)
    out = tmp_path / "coverage.jsonl"

    proc = _digest(fixture, "--export-jsonl", str(out))
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.splitlines()) == 3
    assert not (fixture / "reports").exists()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["class"] for row in rows] == sorted(SCHEMA_VOCAB)
    assert proc.stdout == _digest(fixture).stdout

    bad = _digest(fixture, "--export-jsonl", str(fixture))
    assert bad.returncode == 1
    assert "cannot write --export-jsonl export" in bad.stderr


def test_build_digest_pure_contract(tmp_path):
    module = _digest_module()
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture, wrong={"abuse-01": "x"})

    result = module.build_digest(fixture, T0)
    assert list(result) == ["state", "lines", "gate"]
    assert result["state"] == "ok"
    assert result["gate"] is None
    assert len(result["lines"]) == 3
    assert all(not line.endswith("\n") for line in result["lines"])

    gated = module.build_digest(
        fixture, T0, threshold="gate:100", baselines=[("abuse", 10.0)]
    )
    assert gated["gate"]["passed"] is False
    assert gated["lines"][-1] == gated["gate"]["line"]
    assert len(gated["lines"]) == 4

    with pytest.raises(module.BaselineUsageError):
        module.build_digest(fixture, T0, baselines=[("nope", 1.0)])
    empty = module.build_digest(tmp_path / "absent", T0)
    assert empty == {
        "state": "no-reports",
        "lines": [
            DIGEST_ZERO,
            "coverage covered=0/0 gappy=0 unexercised=0 risk=0.0",
            "worst=none",
        ],
        "gate": None,
    }
    with pytest.raises(module.ReportError):
        module.build_digest(tmp_path / "absent", T0, threshold="gate:50")


def test_summary_output_unchanged_by_digest_flag_existence(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)

    proc = run_script("--summary", "--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert SUMMARY_RE.match(proc.stdout)
    assert len(proc.stdout.splitlines()) == 1


# --------------------------------------------------------------------------
# Fix-wave FX-2: digest gate/error text obeys the fixed class vocabulary
# --------------------------------------------------------------------------

HOSTILE_CLASS = 'bad\x1b[31mRED\x1b[0m"class'


def _hostile_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Isolated repo (script copy + sidecar cases) with one hostile class."""
    root = tmp_path / "hostile-repo"
    (root / "scripts").mkdir(parents=True)
    (root / "sidecar" / "cases").mkdir(parents=True)
    history = root / "history"
    history.mkdir()
    script = root / "scripts" / "engagement-report.py"
    script.write_bytes(SCRIPT.read_bytes())
    (root / "sidecar" / "cases" / "evil.yaml").write_text(
        "\n".join(
            [
                "case_id: evil-01",
                f"class: {HOSTILE_CLASS}",
                "expected:",
                "  detection: detected",
                "",
            ]
        ),
        encoding="utf-8",
    )
    seed_transitions(
        history,
        [{"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T1}],
    )
    (history / "last-run.json").write_text(
        json.dumps({"observations": {"evil-01": {"observed_detection": "missed"}}}),
        encoding="utf-8",
    )
    return script, history


def _run_hostile(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(script), *args],
        capture_output=True,
        text=True,
        env=_child_env(),
    )


def _only_printable(text: str) -> bool:
    return all(ch.isprintable() or ch == "\n" for ch in text)


def test_digest_gate_failure_never_echoes_raw_on_disk_class(tmp_path):
    script, history = _hostile_repo(tmp_path)

    proc = _run_hostile(
        script,
        "--digest",
        "--reports-dir",
        str(history),
        "--baseline",
        f"{HOSTILE_CLASS}=90",
    )
    assert proc.returncode == 3
    assert "\x1b" not in proc.stdout and "\x1b" not in proc.stderr
    assert _only_printable(proc.stdout) and _only_printable(proc.stderr)
    assert HOSTILE_CLASS not in proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[-1] == "Gate: FAIL (floors: other 0.0% < 90%)"
    assert proc.stderr.strip() == "gate: FAIL (floors: other 0.0% < 90%)"


def test_digest_unknown_baseline_error_has_no_raw_text(tmp_path):
    script, history = _hostile_repo(tmp_path)

    proc = _run_hostile(
        script,
        "--digest",
        "--reports-dir",
        str(history),
        "--baseline",
        "nope\x1b[31m\nFORGED=50",
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert _only_printable(proc.stderr)
    assert len(proc.stderr.splitlines()) == 1
    assert "\x1b" not in proc.stderr and "RED" not in proc.stderr
    assert "valid: abuse, auth-bypass" in proc.stderr
    assert proc.stderr.rstrip().endswith("prompt-injection)")
    assert "other" not in proc.stderr


def test_digest_baseline_other_rejected_without_claiming_it_valid(tmp_path):
    """FX-7b P3-4: ``other`` is a display label, never offered as valid."""
    script, history = _hostile_repo(tmp_path)

    proc = _run_hostile(
        script, "--digest", "--reports-dir", str(history), "--baseline", "other=90"
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "unknown --baseline class 'other'" in proc.stderr
    assert "valid:" in proc.stderr
    assert not proc.stderr.rstrip().endswith("other)")
    assert proc.stderr.count("other") == 1


def test_digest_export_jsonl_failure_message_is_printable_only(tmp_path):
    """FX-7b P3-5: the operator-supplied path never reaches stderr raw."""
    script, history = _hostile_repo(tmp_path)
    bad = tmp_path / "no-such-dir\x1b[31mRED" / "out.jsonl"

    proc = _run_hostile(
        script, "--digest", "--reports-dir", str(history), "--export-jsonl", str(bad)
    )
    assert proc.returncode == 1
    assert "\x1b" not in proc.stderr
    assert _only_printable(proc.stderr)
    assert "error: cannot write --export-jsonl export" in proc.stderr


def test_digest_floor_failure_names_schema_class_unchanged(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture, wrong={"abuse-01": "x"})

    proc = _digest(fixture, "--baseline", "abuse=90")
    assert proc.returncode == 3
    assert proc.stdout.splitlines()[-1] == "Gate: FAIL (floors: abuse 0.0% < 90%)"
    assert proc.stderr.strip() == "gate: FAIL (floors: abuse 0.0% < 90%)"


def test_digest_report_error_text_is_escaped(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)

    proc = _digest(fixture, "--baseline", "abuse\x1b[1m")
    assert proc.returncode == 1
    assert "\x1b" not in proc.stderr
    assert "invalid --baseline" in proc.stderr


def test_legacy_summary_gate_rendering_keeps_raw_class(tmp_path):
    """--summary and evaluate_threshold defaults are byte-identical (raw)."""
    module = _digest_module()
    script, history = _hostile_repo(tmp_path)

    proc = _run_hostile(
        script,
        "--summary",
        "--reports-dir",
        str(history),
        "--baseline",
        f"{HOSTILE_CLASS}=90",
    )
    assert proc.returncode == 3
    assert f"floors: {HOSTILE_CLASS} 0.0% < 90%" in proc.stderr + proc.stdout

    coverage = [(HOSTILE_CLASS, 1, 1, 0, 0.0, 0, "gappy")]
    legacy = module.evaluate_threshold([], coverage, None, [(HOSTILE_CLASS, 90.0)])
    assert legacy["line"] == f"Gate: FAIL (floors: {HOSTILE_CLASS} 0.0% < 90%)"
    assert legacy["failures"][0]["class"] == HOSTILE_CLASS
    mapped = module.evaluate_threshold(
        [], coverage, None, [(HOSTILE_CLASS, 90.0)], fixed_vocab=True
    )
    assert mapped["line"] == "Gate: FAIL (floors: other 0.0% < 90%)"
    assert mapped["failures"][0]["class"] == "other"


def test_digest_jsonl_symlink_leaf_is_refused(tmp_path):
    fixture = tmp_path / "reports"
    _seed_digest_history(fixture)
    real = tmp_path / "real.txt"
    real.write_text("KEEP", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)

    proc = _digest(fixture, "--export-jsonl", str(link))
    assert proc.returncode == 1
    assert "cannot write --export-jsonl export" in proc.stderr
    assert real.read_text(encoding="utf-8") == "KEEP"

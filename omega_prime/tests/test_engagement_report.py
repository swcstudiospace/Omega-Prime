"""Scheduled engagement reports (PLAN-F7): script + config key tests.

Hermetic ``tmp_path`` fixture histories with fixed RFC3339 timestamps —
no wall-clock leakage. The script is exercised as a subprocess; the
config node through :mod:`omega_prime.config`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from sidecar.findings import dedupe_hash
from sidecar.simulation import load_suite, validate_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "engagement-report.py"

SIDECAR_REPORTS_ENV = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
REDTEAM_REPORTS_ENV = "OMEGA_PRIME_REDTEAM_REPORTS_DIR"

T0 = "2026-10-01T00:00:00+00:00"
T1 = "2026-10-05T12:00:00+00:00"
T2 = "2026-10-08T12:00:00+00:00"

SECTIONS = [
    "## Header",
    "## Runs summary",
    "## Per-technique pass rates",
    "## Detection gaps",
    "## Findings deltas",
    "## State transitions",
]


def _child_env() -> dict[str, str]:
    """Child env scrubbed of reports-dir overrides for hermetic runs."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in (SIDECAR_REPORTS_ENV, REDTEAM_REPORTS_ENV)
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


def cycle(ts: str) -> list[dict[str, str]]:
    return [
        {"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": ts},
        {"cause": "run-completed", "from": "RUNNING", "to": "IDLE", "ts": ts},
    ]


def expected_detections() -> dict[str, str]:
    cases, errors = load_suite()
    assert not errors
    out: dict[str, str] = {}
    for case in cases:
        expected = case.get("expected")
        detection = expected.get("detection") if isinstance(expected, dict) else ""
        out[str(case["case_id"])] = str(detection)
    return out


def seed_last_run(
    directory: Path, wrong: dict[str, str] | None = None
) -> dict[str, dict[str, str]]:
    """All passing observations except ``wrong`` case ids."""
    observations = {
        case_id: {
            "observed_detection": detection,
            "response": f"loopback-seam:{case_id}:{detection}",
        }
        for case_id, detection in expected_detections().items()
    }
    for case_id, got in (wrong or {}).items():
        observations[case_id]["observed_detection"] = got
        observations[case_id]["response"] = f"loopback-seam:{case_id}:{got}"
    (directory / "last-run.json").write_text(
        json.dumps(
            {"suite": "smoke", "observations": observations}, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    return observations


def finding_row(case_id: str, response: str, ts: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "class": "test",
        "severity": "low",
        "observed_detection": "x",
        "verdict": "fail",
        "response": response,
        "dedupe_hash": dedupe_hash(case_id, response),
        "occurrence_count": 1,
        "ts": ts,
        "last_seen": ts,
    }


def seed_findings(directory: Path, rows: list[dict[str, object]]) -> None:
    path = directory / "findings.ndjson"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def section_lines(stdout: str, heading: str) -> list[str]:
    """Body lines of one ``## `` section (up to the next heading)."""
    lines = stdout.splitlines()
    start = lines.index(heading) + 1
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") or line.startswith("# "):
            break
        body.append(line)
    return body


def test_happy_six_sections_and_values(tmp_path, monkeypatch):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})

    hash_abuse = dedupe_hash("abuse-01", "resp-a")
    hash_jailbreak = dedupe_hash("jailbreak-01", "resp-j")
    hash_old = hashlib.sha256(b"old").hexdigest()
    seed_findings(
        fixture,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T2),
        ],
    )
    out_dir = fixture / "reports"
    out_dir.mkdir()
    (out_dir / "engagement-2026-09-24_2026-10-01.md").write_text(
        "# Engagement report\n\n"
        f"<!-- findings-snapshot: {json.dumps({hash_abuse: 'abuse-01', hash_old: 'old-01'}, sort_keys=True)} -->\n",
        encoding="utf-8",
    )

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""

    positions = [proc.stdout.index(section) for section in SECTIONS]
    assert positions == sorted(positions)

    assert "- runs in window: 2" in proc.stdout
    assert "| abuse-01 | 1/1 (100.0%) |" in proc.stdout
    assert "| jailbreak-01 | 0/1 (0.0%) |" in proc.stdout

    # Detection gaps equal validate_suite() failures verbatim. The live
    # call writes side-effect findings, so point it at a copy.
    twin = tmp_path / "twin"
    twin.mkdir()
    (twin / "last-run.json").write_text(
        (fixture / "last-run.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv(SIDECAR_REPORTS_ENV, str(tmp_path / "stray"))
    _, failures = validate_suite(reports=twin)
    assert failures, "fixture must produce at least one gap"
    gap_lines = [
        line[2:]
        for line in section_lines(proc.stdout, "## Detection gaps")
        if line.startswith("- ")
    ]
    assert gap_lines == failures
    assert all("'" in failure for failure in gap_lines)  # each names a case_id

    assert "- new: 1, rescinded: 1" in proc.stdout
    assert f"| {hash_jailbreak} | new | jailbreak-01 |" in proc.stdout
    assert f"| {hash_old} | rescinded | old-01 |" in proc.stdout

    assert "| IDLE | RUNNING | 2 |" in proc.stdout
    assert "| RUNNING | IDLE | 2 |" in proc.stdout

    canonical = out_dir / "engagement-2026-10-01_2026-10-08.md"
    assert canonical.is_file()
    assert canonical.read_text(encoding="utf-8") == proc.stdout


def test_determinism_byte_identical(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])

    first = run_script("--reports-dir", str(fixture), "--since", T0)
    second = run_script("--reports-dir", str(fixture), "--since", T0)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (
        hashlib.sha256(first.stdout.encode()).hexdigest()
        == hashlib.sha256(second.stdout.encode()).hexdigest()
    )


def test_empty_window_after_newest_record(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", "2027-01-01T00:00:00+00:00"
    )
    assert proc.returncode == 0, proc.stderr
    assert "- runs in window: 0" in proc.stdout
    assert "no runs in window" in proc.stdout
    pass_body = section_lines(proc.stdout, "## Per-technique pass rates")
    assert "| case_id | result |" in pass_body
    assert not [line for line in pass_body if line.startswith("| abuse")]
    trans_body = section_lines(proc.stdout, "## State transitions")
    assert "| from | to | count |" in trans_body
    assert len([line for line in trans_body if line.startswith("| ")]) == 2


def test_empty_reports_dir_is_valid_zero_report(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "no runs in window" in proc.stdout
    for section in SECTIONS:
        assert section in proc.stdout


def test_malformed_ndjson_line_skipped_with_warning(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    with (fixture / "transitions.ndjson").open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
    seed_last_run(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "warning:" in proc.stderr
    assert "transitions" in proc.stderr
    assert "- runs in window: 1" in proc.stdout


def test_no_baseline_treats_all_findings_as_new(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    seed_findings(
        fixture,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T1),
        ],
    )

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "no baseline" in proc.stdout
    assert "- new: 2, rescinded: 0" in proc.stdout


def test_failure_unreadable_reports_dir(tmp_path):
    marker = tmp_path / "not-a-dir"
    marker.write_text("x", encoding="utf-8")
    proc = run_script("--reports-dir", str(marker), "--since", T0)
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_failure_unparseable_last_run(tmp_path):
    fixture = tmp_path / "reports"
    fixture.mkdir()
    (fixture / "last-run.json").write_text("{oops", encoding="utf-8")
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 1
    assert "last-run.json" in proc.stderr


def test_script_is_stdlib_only():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in stdlib, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            assert node.module.split(".")[0] in stdlib, node.module


def test_config_reports_dir_default_and_env(tmp_path, monkeypatch):
    from omega_prime.config import load_config

    config = load_config(tmp_path)
    assert config["redteam"]["reports_dir"] == ""

    monkeypatch.setenv(REDTEAM_REPORTS_ENV, "/tmp/eng-reports")
    assert load_config(tmp_path)["redteam"]["reports_dir"] == "/tmp/eng-reports"

    monkeypatch.delenv(REDTEAM_REPORTS_ENV)
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"redteam": {"reports_dir": "rel/reports"}}), encoding="utf-8"
    )
    assert load_config(tmp_path)["redteam"]["reports_dir"] == "rel/reports"

"""Scheduled engagement reports (PLAN-F7, batch-02 G3/G5/G6): script + config tests.

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
from typing import Any

import pytest

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
    "## Coverage matrix",
    "## Detection gaps",
    "## Findings deltas",
    "## State transitions",
    "## State timeline",
    "## Severity timeline",
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


def seed_findings(directory: Path, rows: list[dict[str, Any]]) -> None:
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


def test_timeline_orders_chronologically_with_footer(tmp_path):
    """G5 happy: out-of-order rows render ascending with cause counts."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(
        fixture,
        [
            {
                "cause": "kill-switch-set",
                "from": "RUNNING",
                "to": "BLOCKED",
                "ts": "2026-10-06T00:00:00+00:00",
            },
            {"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T1},
            {"cause": "run-completed", "from": "RUNNING", "to": "IDLE", "ts": T1},
            {"cause": "run-started", "from": "IDLE", "to": "RUNNING", "ts": T2},
        ],
    )
    seed_last_run(fixture)
    # --since excludes the T1 rows: neither bullets nor footer may show them.
    proc = run_script(
        "--reports-dir", str(fixture), "--since", "2026-10-06T00:00:00+00:00"
    )
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## State timeline")
    bullets = [line for line in body if line.startswith("- ")]
    assert bullets == [
        "- 2026-10-06T00:00:00+00:00 RUNNING -> BLOCKED (kill-switch-set)",
        "- 2026-10-08T12:00:00+00:00 IDLE -> RUNNING (run-started)",
        "- causes: kill-switch-set=1, run-started=1",
    ]
    assert "2026-10-05" not in "\n".join(bullets)
    positions = [proc.stdout.index(section) for section in SECTIONS]
    assert positions == sorted(positions)


def test_timeline_empty_window_and_unknown_cause(tmp_path):
    """G5 edge: empty window text; legacy rows without cause count as unknown."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(
        fixture,
        [
            {"from": "IDLE", "to": "RUNNING", "ts": T1},
            {"cause": "", "from": "RUNNING", "to": "IDLE", "ts": T1},
        ],
    )
    seed_last_run(fixture)
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "- 2026-10-05T12:00:00+00:00 IDLE -> RUNNING (unknown)" in proc.stdout
    assert "- causes: unknown=2" in proc.stdout
    empty = run_script(
        "--reports-dir", str(fixture), "--since", "2027-01-01T00:00:00+00:00"
    )
    assert empty.returncode == 0, empty.stderr
    assert "- no transitions in window" in empty.stdout
    assert "- causes: none" in empty.stdout


def test_timeline_determinism_equal_timestamps(tmp_path):
    """G5 determinism: duplicate-timestamp ties order identically across runs."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(
        fixture,
        [
            {"cause": "b-cause", "from": "RUNNING", "to": "IDLE", "ts": T1},
            {"cause": "a-cause", "from": "IDLE", "to": "RUNNING", "ts": T1},
        ],
    )
    seed_last_run(fixture)
    first = run_script("--reports-dir", str(fixture), "--since", T0)
    second = run_script("--reports-dir", str(fixture), "--since", T0)
    assert first.returncode == 0 and second.returncode == 0
    assert (
        hashlib.sha256(first.stdout.encode()).hexdigest()
        == hashlib.sha256(second.stdout.encode()).hexdigest()
    )
    body = section_lines(first.stdout, "## State timeline")
    assert body[1].endswith("(a-cause)")
    assert body[2].endswith("(b-cause)")


def test_coverage_matrix_all_covered(tmp_path):
    """G6 happy: default suite fixture renders six covered rows."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Coverage matrix")
    rows = [line for line in body if line.startswith("| ")][2:]
    assert len(rows) == 6
    assert rows == sorted(rows)
    assert rows[0] == "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered | █ |"
    assert all(line.endswith("| covered | █ |") for line in rows)


def test_coverage_matrix_failing_class_is_gappy(tmp_path):
    """G6: one failing observation flips only its class row to gappy."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Coverage matrix")
    text = "\n".join(body)
    assert "| jailbreak | 1 | 1 | 0/1 (0.0%) | 1 | gappy | ▁ |" in text
    assert "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered | █ |" in text


def test_coverage_matrix_zero_runs_renders_na(tmp_path):
    """G6 edge: far-future --since still renders six n/a rows (cases exist)."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    proc = run_script(
        "--reports-dir", str(fixture), "--since", "2027-01-01T00:00:00+00:00"
    )
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Coverage matrix")
    rows = [line for line in body if line.startswith("| ")][2:]
    assert len(rows) == 6
    assert all("0/0 (n/a)" in line for line in rows)
    assert all(line.endswith("| — |") for line in rows)


def _coverage_fn():
    """The real ``coverage_rows()`` loaded from the report script."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "engagement_report_coverage_under_test", str(SCRIPT)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.coverage_rows


def test_coverage_rows_unknown_class_unexercised_and_attribution():
    """G6 unit edges: unknown classes sort last; load errors attribute by file."""
    coverage_rows = _coverage_fn()
    cases = [
        ("abuse-01", "abuse-detected", "", "abuse"),
        ("zzz-01", "detected", "", "weird-class"),
    ]
    observations = {"abuse-01": {"observed_detection": "abuse-detected"}}
    gaps = [
        "/tmp/cases/cost-abuse-99.yaml: cannot read ([Errno 13] denied)",
        "version drift implies DEGRADED: contract changed",
    ]
    rows = coverage_rows(cases, observations, gaps)
    by_class = {row[0]: row for row in rows}
    assert [row[0] for row in rows][-1] == "weird-class"
    assert len(rows) == 7
    assert by_class["abuse"] == ("abuse", 1, 1, 1, 100.0, 0, "covered")
    assert by_class["cost-abuse"] == ("cost-abuse", 0, 0, 0, 0.0, 1, "unexercised")
    assert by_class["weird-class"] == ("weird-class", 1, 0, 0, 0.0, 0, "gappy")
    assert sum(row[5] for row in rows) == 1


def test_coverage_rows_quoted_case_id_attributes_mismatch():
    """G6 unit: observation-mismatch strings count toward their case's class."""
    coverage_rows = _coverage_fn()
    cases = [
        ("abuse-01", "abuse-detected", "", "abuse"),
        ("jailbreak-01", "jailbreak-detected", "", "jailbreak"),
    ]
    observations = {
        "abuse-01": {"observed_detection": "abuse-detected"},
        "jailbreak-01": {"observed_detection": "not-detected"},
    }
    gaps = ["case 'jailbreak-01': FAIL — observed detection 'not-detected'"]
    rows = {row[0]: row for row in coverage_rows(cases, observations, gaps)}
    assert rows["abuse"] == ("abuse", 1, 1, 1, 100.0, 0, "covered")
    assert rows["jailbreak"] == ("jailbreak", 1, 1, 0, 0.0, 1, "gappy")


def test_severity_timeline_groups_critical_first(tmp_path):
    """Feature D happy: critical-first grouping with hash12/class/ts rows."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(
        fixture,
        [
            {**finding_row("abuse-01", "resp-a", T2), "severity": "low"},
            {
                **finding_row("jailbreak-01", "resp-j", T1),
                "severity": "critical",
                "class": "jailbreak",
            },
            {
                **finding_row("exfil-01", "resp-e", "2026-09-01T00:00:00+00:00"),
                "severity": "high",
                "class": "exfil",
            },
        ],
    )

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    body = [
        line
        for line in section_lines(proc.stdout, "## Severity timeline")
        if line.startswith("- ")
    ]
    assert body == [
        f"- {dedupe_hash('jailbreak-01', 'resp-j')[:12]} critical jailbreak {T1}",
        f"- {dedupe_hash('abuse-01', 'resp-a')[:12]} low test {T2}",
    ]


def test_severity_timeline_empty_window(tmp_path):
    """Feature D edge: no in-window findings renders the empty line."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "- no findings in window" in section_lines(
        proc.stdout, "## Severity timeline"
    )


def test_severity_timeline_input_order_independent(tmp_path):
    """Feature D determinism: shuffled file order renders byte-identical."""
    rows = [
        {**finding_row("abuse-01", "resp-a", T2), "severity": "low"},
        {
            **finding_row("jailbreak-01", "resp-j", T1),
            "severity": "critical",
            "class": "jailbreak",
        },
        {**finding_row("abuse-02", "resp-b", T1), "severity": "medium"},
    ]
    first_dir = tmp_path / "first" / "reports"
    first_dir.mkdir(parents=True)
    seed_transitions(first_dir, cycle(T1))
    seed_last_run(first_dir)
    seed_findings(first_dir, rows)
    second_dir = tmp_path / "second" / "reports"
    second_dir.mkdir(parents=True)
    seed_transitions(second_dir, cycle(T1))
    seed_last_run(second_dir)
    seed_findings(second_dir, list(reversed(rows)))

    first = run_script("--reports-dir", str(first_dir), "--since", T0)
    second = run_script("--reports-dir", str(second_dir), "--since", T0)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def _report_module():
    """The real report script loaded as a module (compare helpers)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "engagement_report_compare_under_test", str(SCRIPT)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compare_renders_week_over_week_section(tmp_path):
    """H6 happy: --compare appends current-vs-previous deltas with arrows."""
    previous_dir = tmp_path / "previous"
    previous_dir.mkdir()
    seed_transitions(previous_dir, cycle(T1))
    seed_last_run(previous_dir)
    seed_findings(previous_dir, [finding_row("abuse-01", "resp-a", T1)])
    first = run_script("--reports-dir", str(previous_dir), "--since", T0)
    assert first.returncode == 0, first.stderr
    assert "## Week-over-week comparison" not in first.stdout
    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text(first.stdout, encoding="utf-8")

    current_dir = tmp_path / "current"
    current_dir.mkdir()
    seed_transitions(current_dir, cycle(T1) + cycle(T2))
    seed_last_run(current_dir, wrong={"jailbreak-01": "not-detected"})
    seed_findings(
        current_dir,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T2),
        ],
    )
    second = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare",
        str(baseline_path),
    )
    assert second.returncode == 0, second.stderr
    body = section_lines(second.stdout, "## Week-over-week comparison")
    assert body, "comparison section must render"
    text = "\n".join(body)
    assert "- baseline: baseline.md" in text
    assert "| runs | 2 | 1 | +1 ↑ |" in text
    # One class now fails: the pass-rate delta points down.
    assert "| pass rate | " in text and " ↓ |" in text
    assert "| findings new | " in text
    assert "| findings rescinded | " in text


def test_compare_is_deterministic(tmp_path):
    """H6 determinism: same inputs render byte-identical comparison runs."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text(
        run_script("--reports-dir", str(fixture), "--since", T0).stdout,
        encoding="utf-8",
    )
    first = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--compare",
        str(baseline_path),
    )
    second = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--compare",
        str(baseline_path),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_compare_missing_file_exits_1(tmp_path):
    """H6 edge: an unreadable --compare path fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--compare",
        str(tmp_path / "absent.md"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_parse_previous_metrics_and_render_comparison_units():
    """H6 units: metric parsing (missing ⇒ 0) and delta/arrow rendering."""
    module = _report_module()
    assert module.parse_previous_metrics("# Engagement report\n") == {
        "runs": 0,
        "pass_pct": 0,
        "gaps": 0,
        "new": 0,
        "rescinded": 0,
    }
    text = (
        "# Engagement report\n"
        "\n"
        "## Runs summary\n"
        "\n"
        "- runs in window: 3\n"
        "\n"
        "## Per-technique pass rates\n"
        "\n"
        "| case_id | result |\n"
        "| --- | --- |\n"
        "| a-01 | 1/1 (100.0%) |\n"
        "| b-01 | 0/1 (0.0%) |\n"
        "\n"
        "## Detection gaps\n"
        "\n"
        "- 'a-01' never detected\n"
        "- 'b-01' mapping invalid\n"
        "\n"
        "## Findings deltas\n"
        "\n"
        "- new: 2, rescinded: 1\n"
    )
    assert module.parse_previous_metrics(text) == {
        "runs": 3,
        "pass_pct": 50,
        "gaps": 2,
        "new": 2,
        "rescinded": 1,
    }
    lines = module.render_comparison(
        current={"runs": 3, "pass_pct": 50, "gaps": 2, "new": 2, "rescinded": 1},
        previous={"runs": 3, "pass_pct": 100, "gaps": 0, "new": 1, "rescinded": 1},
        baseline_label="prev.md",
    )
    text = "\n".join(lines)
    assert lines[0] == "## Week-over-week comparison"
    assert "- baseline: prev.md" in text
    assert "| runs | 3 | 3 | +0 → |" in text
    assert "| pass rate | 50% | 100% | -50pp ↓ |" in text
    assert "| gaps | 2 | 0 | +2 ↑ |" in text
    assert "| findings new | 2 | 1 | +1 ↑ |" in text
    assert "| findings rescinded | 1 | 1 | +0 → |" in text


# Coverage drill-down (batch-04 Feature H4) ------------------------------------


def _drill_fixture(directory: Path) -> None:
    """Two classes of findings plus one run of history."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory)
    seed_findings(
        directory,
        [
            {
                **finding_row("abuse-01", "resp-a", T2),
                "class": "abuse",
                "severity": "high",
                "observed_detection": "no-detection",
                "verdict": "miss",
            },
            {
                **finding_row("abuse-02", "resp-b", T1),
                "class": "abuse",
                "severity": "low",
                "observed_detection": "x",
                "verdict": "fail",
            },
            {
                **finding_row("jailbreak-01", "resp-j", T1),
                "class": "jailbreak",
                "severity": "critical",
                "observed_detection": "y",
                "verdict": "fail",
            },
        ],
    )


def test_drill_down_renders_class_table_after_matrix(tmp_path):
    """H4 happy: detail rows for the class only, between matrix and gaps."""
    fixture = tmp_path / "reports"
    _drill_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--drill-down", "abuse"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    lines = proc.stdout.splitlines()
    matrix_at = lines.index("## Coverage matrix")
    drill_at = lines.index("## Coverage drill-down: abuse")
    gaps_at = lines.index("## Detection gaps")
    assert matrix_at < drill_at < gaps_at
    body = section_lines(proc.stdout, "## Coverage drill-down: abuse")
    assert body[1] == (
        "| dedupe_hash | severity | observed_detection | timestamp | verdict |"
    )
    rows = [line for line in body if line.startswith("| ")]
    assert len(rows) == 4  # header + separator + 2 findings
    assert any("high" in line and "miss" in line for line in rows)
    assert any("low" in line and "fail" in line for line in rows)
    assert not any("jailbreak-01" in line for line in rows)
    assert not any("critical" in line for line in rows)
    digests = [line.split("|")[1].strip() for line in rows[2:]]
    assert digests == sorted(digests)


def test_drill_down_empty_class_reports_no_findings(tmp_path):
    """H4 edge: a class with no findings renders the empty line."""
    fixture = tmp_path / "reports"
    _drill_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--drill-down", "cost-abuse"
    )
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Coverage drill-down: cost-abuse")
    assert "- no findings in class cost-abuse" in body


def test_drill_down_input_order_independent(tmp_path):
    """H4 determinism: shuffled file order renders byte-identical."""
    rows = [
        {**finding_row("abuse-01", "resp-a", T2), "class": "abuse"},
        {**finding_row("abuse-02", "resp-b", T1), "class": "abuse"},
        {**finding_row("jailbreak-01", "resp-j", T1), "class": "jailbreak"},
    ]
    first_dir = tmp_path / "first" / "reports"
    first_dir.mkdir(parents=True)
    seed_transitions(first_dir, cycle(T1))
    seed_last_run(first_dir)
    seed_findings(first_dir, rows)
    second_dir = tmp_path / "second" / "reports"
    second_dir.mkdir(parents=True)
    seed_transitions(second_dir, cycle(T1))
    seed_last_run(second_dir)
    seed_findings(second_dir, list(reversed(rows)))

    first = run_script(
        "--reports-dir", str(first_dir), "--since", T0, "--drill-down", "abuse"
    )
    second = run_script(
        "--reports-dir", str(second_dir), "--since", T0, "--drill-down", "abuse"
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_no_drill_down_flag_renders_no_section(tmp_path):
    """H4 regression: without --drill-down the report has no new section."""
    fixture = tmp_path / "reports"
    _drill_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Coverage drill-down" not in proc.stdout


# Coverage delta vs previous report (batch-05 Feature 3) -----------------------


def test_diff_coverage_renders_delta_section(tmp_path):
    """Feature 3 happy: --diff-coverage renders current|previous|Δ per class."""
    previous_dir = tmp_path / "previous"
    previous_dir.mkdir()
    seed_transitions(previous_dir, cycle(T1))
    seed_last_run(previous_dir)
    seed_findings(previous_dir, [finding_row("abuse-01", "resp-a", T1)])
    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text(
        run_script("--reports-dir", str(previous_dir), "--since", T0).stdout,
        encoding="utf-8",
    )

    current_dir = tmp_path / "current"
    current_dir.mkdir()
    seed_transitions(current_dir, cycle(T1) + cycle(T2))
    seed_last_run(current_dir, wrong={"jailbreak-01": "not-detected"})
    seed_findings(
        current_dir,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T2),
        ],
    )
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--diff-coverage",
        str(baseline_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    lines = proc.stdout.splitlines()
    matrix_at = lines.index("## Coverage matrix")
    delta_at = lines.index("## Coverage delta")
    gaps_at = lines.index("## Detection gaps")
    assert matrix_at < delta_at < gaps_at
    body = section_lines(proc.stdout, "## Coverage delta")
    assert body[0] == ""
    assert body[1] == "- baseline: baseline.md"
    assert body[3] == ("| class | metric | current | previous | delta | trend |")
    rows = [line for line in body if line.startswith("| ")]
    assert len(rows) == 2 + 6 * 5  # header + separator + 5 metrics x 6 classes
    text = "\n".join(body)
    # The failing class regresses on pass rate; untouched classes are flat.
    assert (
        "| jailbreak | pass rate | 0/1 (0.0%) | 1/1 (100.0%) | -100.0pp ↓ | regressed |"
        in text
    )
    assert (
        "| jailbreak | verdict | gappy | covered | covered → gappy | regressed |"
        in text
    )
    assert (
        "| abuse | pass rate | 1/1 (100.0%) | 1/1 (100.0%) | +0.0pp → | unchanged |"
        in text
    )
    assert all(
        line.split("|")[6].strip() in ("improved", "regressed", "unchanged")
        for line in rows[2:]
    )


def test_diff_coverage_is_deterministic(tmp_path):
    """Feature 3 determinism: same inputs render byte-identical delta runs."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text(
        run_script("--reports-dir", str(fixture), "--since", T0).stdout,
        encoding="utf-8",
    )
    first = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--diff-coverage",
        str(baseline_path),
    )
    second = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--diff-coverage",
        str(baseline_path),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_diff_coverage_missing_file_exits_1(tmp_path):
    """Feature 3 edge: an unreadable --diff-coverage path fails closed."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--diff-coverage",
        str(tmp_path / "absent.md"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_parse_previous_coverage_and_render_delta_units():
    """Feature 3 units: matrix parsing (n/a ⇒ 0.0) and trend rendering."""
    module = _report_module()
    assert module.parse_previous_coverage("# Engagement report\n") == {}
    text = (
        "# Engagement report\n"
        "\n"
        "## Coverage matrix\n"
        "\n"
        "| class | cases | runs | pass rate | gaps | verdict |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered |\n"
        "| jailbreak | 2 | 0 | 0/0 (n/a) | 1 | gappy |\n"
        "\n"
        "## Detection gaps\n"
    )
    assert module.parse_previous_coverage(text) == {
        "abuse": (1, 1, 1, 100.0, 0, "covered"),
        "jailbreak": (2, 0, 0, 0.0, 1, "gappy"),
    }
    lines = module.render_coverage_delta(
        current=[
            ("abuse", 1, 1, 1, 100.0, 0, "covered"),
            ("jailbreak", 2, 1, 0, 0.0, 2, "gappy"),
        ],
        previous={"abuse": (1, 1, 1, 100.0, 0, "covered")},
        baseline_label="prev.md",
    )
    text = "\n".join(lines)
    assert lines[0] == "## Coverage delta"
    assert "- baseline: prev.md" in text
    assert "| abuse | gaps | 0 | 0 | +0 → | unchanged |" in text
    assert "| jailbreak | pass rate | 0/1 (0.0%) | n/a | n/a → | unchanged |" in text
    assert "| jailbreak | gaps | 2 | n/a | n/a → | unchanged |" in text
    improved = module.render_coverage_delta(
        current=[("abuse", 1, 1, 1, 100.0, 0, "covered")],
        previous={"abuse": (1, 1, 0, 0.0, 1, "gappy")},
        baseline_label="prev.md",
    )
    improved_text = "\n".join(improved)
    assert (
        "| abuse | pass rate | 1/1 (100.0%) | 0/1 (0.0%) | +100.0pp ↑ | improved |"
        in improved_text
    )
    assert "| abuse | gaps | 0 | 1 | -1 ↓ | improved |" in improved_text
    assert (
        "| abuse | verdict | covered | gappy | gappy → covered | improved |"
        in improved_text
    )


def test_no_diff_coverage_flag_renders_no_section(tmp_path):
    """Feature 3 regression: without --diff-coverage there is no delta."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Coverage delta" not in proc.stdout


# JSON output mode (batch-05 Feature 4) ----------------------------------------

JSON_KEYS = [
    "title",
    "header",
    "runs_summary",
    "pass_rates",
    "coverage",
    "coverage_delta",
    "coverage_drill_down",
    "detection_gaps",
    "findings_deltas",
    "state_transitions",
    "state_timeline",
    "severity_timeline",
    "comparison",
    "snapshot",
]


def _json_fixture(directory: Path) -> None:
    """One run of history plus two findings for JSON tests."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory)
    seed_findings(
        directory,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T2),
        ],
    )


def test_json_outputs_single_object_with_all_sections(tmp_path):
    """Feature 4 happy: --json emits one ordered JSON object, no markdown."""
    fixture = tmp_path / "reports"
    _json_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert "## " not in proc.stdout
    data = json.loads(proc.stdout)
    assert list(data.keys()) == JSON_KEYS
    assert data["title"] == "Engagement report"
    assert data["runs_summary"] == {"runs": 2, "empty": False}
    assert len(data["coverage"]) == 6
    assert data["coverage"][0]["class"] == "abuse"
    assert set(data["coverage"][0].keys()) == {
        "class",
        "cases",
        "runs",
        "wins",
        "pass_pct",
        "gaps",
        "verdict",
    }
    assert data["coverage_delta"] is None
    assert data["coverage_drill_down"] is None
    assert data["comparison"] is None
    assert data["findings_deltas"]["new_count"] == 2
    assert data["state_timeline"]["cause_counts"] == {
        "run-completed": 2,
        "run-started": 2,
    }
    assert len(data["severity_timeline"]) == 2
    assert len(data["snapshot"]) == 2
    # Canonical rendering: re-dumping the parsed object is a fixed point.
    assert json.dumps(data, indent=2, sort_keys=False) + "\n" == proc.stdout


def test_json_matches_markdown_values(tmp_path):
    """Feature 4 consistency: JSON sections agree with the markdown run."""
    fixture = tmp_path / "reports"
    _json_fixture(fixture)

    markdown = run_script("--reports-dir", str(fixture), "--since", T0)
    payload = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert markdown.returncode == 0, markdown.stderr
    assert payload.returncode == 0, payload.stderr
    data = json.loads(payload.stdout)
    matrix_body = section_lines(markdown.stdout, "## Coverage matrix")
    matrix_rows = [line for line in matrix_body if line.startswith("| ")][2:]
    assert len(matrix_rows) == len(data["coverage"])
    for line, entry in zip(matrix_rows, data["coverage"], strict=True):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert cells[0] == entry["class"]
        assert int(cells[1]) == entry["cases"]
        assert int(cells[2]) == entry["runs"]
        assert int(cells[4]) == entry["gaps"]
        assert cells[5] == entry["verdict"]
    gap_body = [
        line
        for line in section_lines(markdown.stdout, "## Detection gaps")
        if line.strip().startswith("- ") and line.strip() != "- none"
    ]
    assert [f"- {gap}" for gap in data["detection_gaps"]] == gap_body


def test_json_is_deterministic(tmp_path):
    """Feature 4 determinism: same inputs emit byte-identical JSON."""
    fixture = tmp_path / "reports"
    _json_fixture(fixture)

    first = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    second = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_json_composes_with_compare_delta_and_drill_down(tmp_path):
    """Feature 4 composition: optional sections appear as JSON objects."""
    fixture = tmp_path / "reports"
    _json_fixture(fixture)
    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text(
        run_script("--reports-dir", str(fixture), "--since", T0).stdout,
        encoding="utf-8",
    )

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--compare",
        str(baseline_path),
        "--diff-coverage",
        str(baseline_path),
        "--drill-down",
        "abuse",
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["comparison"]["baseline"] == "baseline.md"
    assert [entry["metric"] for entry in data["comparison"]["metrics"]] == [
        "runs",
        "pass_pct",
        "gaps",
        "new",
        "rescinded",
    ]
    assert data["coverage_delta"]["baseline"] == "baseline.md"
    assert len(data["coverage_delta"]["rows"]) == 6 * 5
    assert set(data["coverage_delta"]["rows"][0].keys()) == {
        "class",
        "metric",
        "current",
        "previous",
        "delta",
        "trend",
    }
    assert data["coverage_drill_down"]["class"] == "abuse"
    # The canonical per-window copy stays markdown for future baselines.
    canonical = next((fixture / "reports").glob("engagement-*.md"))
    assert canonical.read_text(encoding="utf-8").startswith("# Engagement report")


def test_json_empty_window(tmp_path):
    """Feature 4 edge: far-future --since still emits valid all-zero JSON."""
    fixture = tmp_path / "reports"
    _json_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", "2030-01-01T00:00:00+00:00", "--json"
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert list(data.keys()) == JSON_KEYS
    assert data["runs_summary"] == {"runs": 0, "empty": True}
    assert all(entry["runs"] == 0 for entry in data["coverage"])
    assert data["state_timeline"] == {"events": [], "cause_counts": {}}


def test_json_out_writes_json_file(tmp_path):
    """Feature 4 --out: the JSON payload (not markdown) lands in the file."""
    fixture = tmp_path / "reports"
    _json_fixture(fixture)
    out_path = tmp_path / "report.json"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--json", "--out", str(out_path)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert list(data.keys()) == JSON_KEYS


def test_json_missing_compare_fails_closed(tmp_path):
    """Feature 4 edge: --json with an unreadable --compare exits 1."""
    fixture = tmp_path / "reports"
    _json_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--compare",
        str(tmp_path / "absent.md"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


# Historical trend graph (batch-06 Feature A) ----------------------------------

TREND_ROWS = [
    {"pass_rate": 80.0, "findings": 5},
    {"pass_rate": 90.0, "findings": 3},
    {"pass_rate": 100.0, "findings": 1},
]


def _trend_fixture(directory: Path) -> None:
    """One run of history for trend tests."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1))
    seed_last_run(directory)
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def seed_trend(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def test_trend_renders_sparkline_section(tmp_path):
    """Feature A happy: --trend renders per-run sparklines in file order."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    history = tmp_path / "history.ndjson"
    seed_trend(history, TREND_ROWS)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--trend", str(history)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    body = section_lines(proc.stdout, "## Trend")
    assert body, "trend section must render"
    text = "\n".join(body)
    assert "- source: history.ndjson" in text
    assert "- runs: 3" in text
    assert "- pass rate: 80.0%, 90.0%, 100.0%" in text
    assert "- pass-rate trend: \u2581\u2585\u2588" in text
    assert "- findings: 5, 3, 1" in text
    assert "- findings trend: \u2588\u2585\u2581" in text


def test_trend_section_after_coverage_delta(tmp_path):
    """Feature A order: Trend sits after Coverage delta, before drill-down."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text(
        run_script("--reports-dir", str(fixture), "--since", T0).stdout,
        encoding="utf-8",
    )
    history = tmp_path / "history.ndjson"
    seed_trend(history, TREND_ROWS)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--diff-coverage",
        str(baseline_path),
        "--trend",
        str(history),
        "--drill-down",
        "abuse",
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    matrix_at = lines.index("## Coverage matrix")
    delta_at = lines.index("## Coverage delta")
    trend_at = lines.index("## Trend")
    drill_at = lines.index("## Coverage drill-down: abuse")
    gaps_at = lines.index("## Detection gaps")
    assert matrix_at < delta_at < trend_at < drill_at < gaps_at


def test_trend_is_deterministic(tmp_path):
    """Feature A determinism: same history renders byte-identical runs."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    history = tmp_path / "history.ndjson"
    seed_trend(history, TREND_ROWS)
    args = ["--reports-dir", str(fixture), "--since", T0, "--trend", str(history)]

    first = run_script(*args)
    second = run_script(*args)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_trend_missing_file_exits_1(tmp_path):
    """Feature A edge: an unreadable --trend path fails closed with error:."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--trend",
        str(tmp_path / "absent.ndjson"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_trend_corrupt_row_exits_1(tmp_path):
    """Feature A edge: a corrupt trend row fails closed, never silent."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    history = tmp_path / "history.ndjson"
    history.write_text(
        '{"pass_rate": 80.0, "findings": 5}\nnot-json\n', encoding="utf-8"
    )

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--trend", str(history)
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_trend_empty_history_renders_empty_line(tmp_path):
    """Feature A edge: an empty history file renders the empty line."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    history = tmp_path / "history.ndjson"
    history.write_text("", encoding="utf-8")

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--trend", str(history)
    )
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Trend")
    assert "- runs: 0" in "\n".join(body)
    assert "- no trend history" in "\n".join(body)


def test_trend_sparkline_and_loader_units(tmp_path):
    """Feature A units: sparkline mapping, key aliases, flat/empty series."""
    module = _report_module()
    assert module.sparkline([]) == ""
    assert module.sparkline([80.0, 90.0, 100.0]) == "\u2581\u2585\u2588"
    assert module.sparkline([5.0, 3.0, 1.0]) == "\u2588\u2585\u2581"
    assert module.sparkline([42.0, 42.0]) == "\u2585\u2585"

    history = tmp_path / "history.ndjson"
    seed_trend(
        history,
        [
            {"pass_pct": "80%", "findings_count": 5},
            {"pass": 90, "count": 3},
        ],
    )
    rates, counts = module.load_trend_history(history)
    assert rates == [80.0, 90.0]
    assert counts == [5, 3]
    lines = module.render_trend(
        pass_rates=rates, findings_counts=counts, source_label="history.ndjson"
    )
    assert lines[0] == "## Trend"
    assert "- source: history.ndjson" in lines


def test_no_trend_flag_renders_no_section(tmp_path):
    """Feature A regression: without --trend the report has no new section."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Trend" not in proc.stdout


def test_json_trend_composition(tmp_path):
    """Feature A composition: --json --trend adds a trend object; default omits it."""
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    history = tmp_path / "history.ndjson"
    seed_trend(history, TREND_ROWS)

    plain = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert plain.returncode == 0, plain.stderr
    assert "trend" not in json.loads(plain.stdout)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--trend",
        str(history),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["trend"]["source"] == "history.ndjson"
    assert data["trend"]["pass_rates"] == [80.0, 90.0, 100.0]
    assert data["trend"]["findings_counts"] == [5, 3, 1]
    assert data["trend"]["pass_rate_sparkline"] == "\u2581\u2585\u2588"
    assert data["trend"]["findings_sparkline"] == "\u2588\u2585\u2581"


# PDF export hint (batch-06 Feature B) ------------------------------------------


def _hide_pandoc(monkeypatch, tmp_path: Path) -> Path:
    """Empty PATH dir so shutil.which("pandoc") finds nothing."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def test_pdf_fallback_without_pandoc(tmp_path, monkeypatch):
    """Feature B fallback: no pandoc errors to stderr, writes the .md sibling."""
    _hide_pandoc(monkeypatch, tmp_path)
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    pdf_path = tmp_path / "report.pdf"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--pdf", str(pdf_path)
    )
    assert proc.returncode == 0, proc.stderr
    assert "pandoc" in proc.stderr
    assert "error:" in proc.stderr
    assert not pdf_path.exists()
    fallback = tmp_path / "report.md"
    assert fallback.exists()
    # Fallback carries the rendered markdown report unchanged.
    assert fallback.read_text(encoding="utf-8") == proc.stdout
    assert proc.stdout.startswith("# Engagement report")


def test_pdf_fallback_write_failure_exits_1(tmp_path, monkeypatch):
    """Feature B edge: an unwritable fallback path fails closed."""
    _hide_pandoc(monkeypatch, tmp_path)
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--pdf",
        str(tmp_path / "no-such-dir" / "report.pdf"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_pdf_converts_with_pandoc(tmp_path, monkeypatch):
    """Feature B happy: a present pandoc converts the markdown to PDF."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "pandoc"
    fake.write_text(
        "#!/bin/sh\n"
        'out=""\n'
        'prev=""\n'
        'for a in "$@"; do\n'
        '  if [ "$prev" = "-o" ]; then out="$a"; fi\n'
        '  prev="$a"\n'
        "done\n"
        'for a in "$@"; do last="$a"; done\n'
        'printf "PDF:" > "$out"\n'
        'cat "$last" >> "$out"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    pdf_path = tmp_path / "report.pdf"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--pdf", str(pdf_path)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert pdf_path.exists()
    content = pdf_path.read_bytes()
    assert content.startswith(b"PDF:")
    assert b"# Engagement report" in content
    # No markdown fallback sibling on the success path.
    assert not (tmp_path / "report.md").exists()


def test_pdf_composes_with_trend(tmp_path, monkeypatch):
    """Feature B composition: --pdf converts the trend-bearing markdown."""
    _hide_pandoc(monkeypatch, tmp_path)
    fixture = tmp_path / "reports"
    _trend_fixture(fixture)
    history = tmp_path / "history.ndjson"
    seed_trend(history, TREND_ROWS)
    pdf_path = tmp_path / "report.pdf"

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--trend",
        str(history),
        "--pdf",
        str(pdf_path),
    )
    assert proc.returncode == 0, proc.stderr
    fallback = tmp_path / "report.md"
    assert "## Trend" in fallback.read_text(encoding="utf-8")


# Drill-down diff vs previous report (H10) -------------------------------------


def _diff_drill_seed(previous_dir: Path, current_dir: Path) -> Path:
    """Previous has A+OLD, current has A+NEW; returns the baseline path."""
    previous_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(previous_dir, cycle(T1))
    seed_last_run(previous_dir)
    seed_findings(
        previous_dir,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-old", T1),
        ],
    )
    baseline = previous_dir.parent / "baseline-h10.md"
    baseline.write_text(
        run_script("--reports-dir", str(previous_dir), "--since", T0).stdout,
        encoding="utf-8",
    )
    current_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(current_dir, cycle(T1) + cycle(T2))
    seed_last_run(current_dir)
    seed_findings(
        current_dir,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-new", T2),
        ],
    )
    return baseline


def test_diff_drill_down_renders_new_and_resolved(tmp_path):
    """H10 happy: --diff-drill-down keys new/resolved rows by dedupe_hash."""
    baseline = _diff_drill_seed(tmp_path / "previous", tmp_path / "current")
    proc = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--diff-drill-down",
        str(baseline),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    body = section_lines(proc.stdout, "## Drill-down diff")
    assert body, "drill-down diff section must render"
    assert body[1] == f"- baseline: {baseline.name}"
    assert "- new: 1, resolved: 1" in "\n".join(body)
    assert "| dedupe_hash | change | case_id |" in body
    new_hash = dedupe_hash("jailbreak-01", "resp-new")
    old_hash = dedupe_hash("jailbreak-01", "resp-old")
    text = "\n".join(body)
    assert f"| {new_hash} | new | jailbreak-01 |" in text
    assert f"| {old_hash} | resolved | jailbreak-01 |" in text
    # New rows sort before resolved rows, each group sorted by hash.
    rows = [line for line in body if line.startswith("| ")][2:]
    assert rows[0].startswith(f"| {new_hash} | new |")
    assert rows[1].startswith(f"| {old_hash} | resolved |")


def test_diff_drill_down_after_drill_down_section(tmp_path):
    """H10 order: diff sits after the drill-down, before detection gaps."""
    _drill_fixture(tmp_path / "reports")
    baseline = tmp_path / "baseline.md"
    baseline.write_text(
        run_script("--reports-dir", str(tmp_path / "reports"), "--since", T0).stdout,
        encoding="utf-8",
    )
    proc = run_script(
        "--reports-dir",
        str(tmp_path / "reports"),
        "--since",
        T0,
        "--drill-down",
        "abuse",
        "--diff-drill-down",
        str(baseline),
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    drill_at = lines.index("## Coverage drill-down: abuse")
    diff_at = lines.index("## Drill-down diff")
    gaps_at = lines.index("## Detection gaps")
    assert drill_at < diff_at < gaps_at


def test_diff_drill_down_is_deterministic(tmp_path):
    """H10 determinism: same inputs render byte-identical diff runs."""
    baseline = _diff_drill_seed(tmp_path / "previous", tmp_path / "current")
    first = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--diff-drill-down",
        str(baseline),
    )
    second = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--diff-drill-down",
        str(baseline),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_diff_drill_down_missing_file_exits_1(tmp_path):
    """H10 edge: an unreadable --diff-drill-down path fails closed."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--diff-drill-down",
        str(tmp_path / "no-such.md"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_parse_previous_snapshot_and_render_diff_units():
    """H10 units: snapshot parsing (missing ⇒ {}) and row ordering."""
    module = _report_module()
    assert module.parse_previous_snapshot("# no marker\n") == {}
    assert module.parse_previous_snapshot("<!-- findings-snapshot: nope -->") == {}
    text = (
        "# Engagement report\n"
        '<!-- findings-snapshot: {"b-hash": "abuse-01", "a-hash": "x"} -->\n'
    )
    assert module.parse_previous_snapshot(text) == {
        "b-hash": "abuse-01",
        "a-hash": "x",
    }
    rows = module.drill_down_diff_rows(
        current={"b-hash": "abuse-01", "c-hash": "new"},
        previous={"b-hash": "abuse-01", "a-hash": "old"},
    )
    assert rows == [
        ("c-hash", "new", "new"),
        ("a-hash", "resolved", "old"),
    ]
    lines = module.render_drill_down_diff(
        current={"c-hash": "new"},
        previous={"a-hash": "old"},
        baseline_label="baseline.md",
    )
    assert lines[0] == "## Drill-down diff"
    assert "- baseline: baseline.md" in lines
    assert "- new: 1, resolved: 1" in lines
    assert lines[-2] == "| c-hash | new | new |"
    assert lines[-1] == "| a-hash | resolved | old |"


def test_no_diff_drill_down_flag_renders_no_section(tmp_path):
    """H10 regression: without --diff-drill-down there is no diff."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Drill-down diff" not in proc.stdout


# Side-by-side comparison vs previous report (H12) ------------------------------


def _side_by_side_seed(previous_dir: Path, current_dir: Path) -> Path:
    """One-run all-pass baseline; two-run current with one failing class."""
    previous_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(previous_dir, cycle(T1))
    seed_last_run(previous_dir)
    seed_findings(previous_dir, [finding_row("abuse-01", "resp-a", T1)])
    baseline = previous_dir.parent / "baseline-h12.md"
    baseline.write_text(
        run_script("--reports-dir", str(previous_dir), "--since", T0).stdout,
        encoding="utf-8",
    )
    current_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(current_dir, cycle(T1) + cycle(T2))
    seed_last_run(current_dir, wrong={"jailbreak-01": "not-detected"})
    seed_findings(
        current_dir,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T2),
        ],
    )
    return baseline


def test_side_by_side_renders_two_column_table(tmp_path):
    """H12 happy: --side-by-side renders current vs previous metrics."""
    baseline = _side_by_side_seed(tmp_path / "previous", tmp_path / "current")
    proc = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--side-by-side",
        str(baseline),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    body = section_lines(proc.stdout, "## Side-by-side comparison")
    assert body, "side-by-side section must render"
    assert body[1] == f"- baseline: {baseline.name}"
    assert body[3] == "| metric | current | previous |"
    text = "\n".join(body)
    assert "| runs | 2 | 1 |" in text
    assert "| findings | 2 | 1 |" in text
    assert "| pass rate | " in text and "% | " in text
    assert "| coverage | " in text and " covered | " in text
    # All-pass baseline covers every class; one failing class regresses.
    assert "| coverage | 5/6 covered | 6/6 covered |" in text


def test_side_by_side_is_deterministic(tmp_path):
    """H12 determinism: same inputs render byte-identical comparison runs."""
    baseline = _side_by_side_seed(tmp_path / "previous", tmp_path / "current")
    first = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--side-by-side",
        str(baseline),
    )
    second = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--side-by-side",
        str(baseline),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_side_by_side_missing_file_exits_1(tmp_path):
    """H12 edge: an unreadable --side-by-side path fails closed."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--side-by-side",
        str(tmp_path / "no-such.md"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_render_side_by_side_units():
    """H12 units: two-column rendering with fixed metric order."""
    module = _report_module()
    lines = module.render_side_by_side(
        current_runs=2,
        previous_runs=1,
        current_pass=50,
        previous_pass=100,
        current_coverage="5/6 covered",
        previous_coverage="6/6 covered",
        current_findings=2,
        previous_findings=1,
        baseline_label="baseline.md",
    )
    assert lines[0] == "## Side-by-side comparison"
    assert lines[2] == "- baseline: baseline.md"
    assert lines[4] == "| metric | current | previous |"
    assert "| runs | 2 | 1 |" in lines
    assert "| pass rate | 50% | 100% |" in lines
    assert "| coverage | 5/6 covered | 6/6 covered |" in lines
    assert "| findings | 2 | 1 |" in lines
    assert module._coverage_summary(covered=5, total=6) == "5/6 covered"


def test_no_side_by_side_flag_renders_no_section(tmp_path):
    """H12 regression: without --side-by-side there is no comparison."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Side-by-side comparison" not in proc.stdout


def test_json_composes_with_diff_drill_down_and_side_by_side(tmp_path):
    """H10/H12 composition: --json adds drill_down_diff and side_by_side."""
    baseline = _side_by_side_seed(tmp_path / "previous", tmp_path / "current")
    proc = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--json",
        "--diff-drill-down",
        str(baseline),
        "--side-by-side",
        str(baseline),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["drill_down_diff"]["baseline"] == baseline.name
    assert data["drill_down_diff"]["new_count"] == 1
    assert data["drill_down_diff"]["resolved_count"] == 0
    assert data["side_by_side"]["baseline"] == baseline.name
    metrics = {row["metric"]: row for row in data["side_by_side"]["metrics"]}
    assert set(metrics) == {"runs", "pass rate", "coverage", "findings"}
    assert (metrics["runs"]["current"], metrics["runs"]["previous"]) == (2, 1)
    assert data["snapshot"] and data["comparison"] is None


# Risk-weighted coverage scoring (G12) -----------------------------------------


def test_risk_weighted_score_section_after_matrix(tmp_path):
    """G12 happy: all-pass run renders a 100% score section after the matrix."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert "## Risk-weighted score" in text
    matrix_at = text.index("## Coverage matrix")
    risk_at = text.index("## Risk-weighted score")
    gaps_at = text.index("## Detection gaps")
    assert matrix_at < risk_at < gaps_at
    assert "- risk-weighted score: 100.0%" in text
    assert "- weights: critical=10, high=5, moderate=2, low=1" in text
    body = section_lines(text, "## Risk-weighted score")
    # Header + separator + one breakdown row per schema class (six).
    assert len([line for line in body if line.startswith("| ")]) == 2 + 6


def test_risk_weighted_score_failing_class_drops_score(tmp_path):
    """G12: one failing observation drops the weighted score below 100%."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    first_id = sorted(expected_detections())[0]
    seed_last_run(fixture, wrong={first_id: "WRONG-DETECTION"})

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Risk-weighted score" in proc.stdout
    assert "- risk-weighted score: 100.0%" not in proc.stdout


def test_risk_weighted_score_is_deterministic(tmp_path):
    """G12 determinism: same inputs render a byte-identical risk section."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])

    first = run_script("--reports-dir", str(fixture), "--since", T0)
    second = run_script("--reports-dir", str(fixture), "--since", T0)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert section_lines(first.stdout, "## Risk-weighted score") == section_lines(
        second.stdout, "## Risk-weighted score"
    )


def test_risk_weights_table_values():
    """G12 units: deterministic weights table and class tiers."""
    module = _report_module()
    assert module.RISK_SEVERITY_WEIGHTS == {
        "critical": 10,
        "high": 5,
        "moderate": 2,
        "low": 1,
    }
    assert module.CLASS_RISK_TIER["auth-bypass"] == "critical"
    assert module.CLASS_RISK_TIER["prompt-injection"] == "critical"
    assert module.CLASS_RISK_TIER["abuse"] == "high"
    assert module.CLASS_RISK_TIER["jailbreak"] == "high"
    assert module.DEFAULT_CLASS_RISK_TIER == "low"


def test_risk_weighted_score_critical_failure_weighs_more():
    """G12 units: a critical-class FAIL drags the score more than a low one."""
    module = _report_module()
    crit_fail = [
        ("auth-bypass", 1, 1, 0, 0.0, 1, "gappy"),
        ("cost-abuse", 1, 1, 1, 100.0, 0, "covered"),
    ]
    low_fail = [
        ("auth-bypass", 1, 1, 1, 100.0, 0, "covered"),
        ("cost-abuse", 1, 1, 0, 0.0, 1, "gappy"),
    ]
    crit_score, crit_earned, crit_possible, _ = module.risk_weighted_score(crit_fail)
    low_score, low_earned, low_possible, _ = module.risk_weighted_score(low_fail)
    assert (crit_earned, crit_possible) == (2, 12)
    assert (low_earned, low_possible) == (10, 12)
    assert crit_score == round(100.0 * 2 / 12, 1)
    assert low_score == round(100.0 * 10 / 12, 1)
    assert crit_score < low_score


def test_risk_weighted_score_unknown_class_defaults_low_and_empty():
    """G12 units: unknown classes score at low weight; empty coverage is 0."""
    module = _report_module()
    score, earned, possible, breakdown = module.risk_weighted_score(
        [("zzz-new", 2, 0, 0, 0.0, 0, "unexercised")]
    )
    assert breakdown == [("zzz-new", "low", 1, 2, 0, 0, 2)]
    assert (score, earned, possible) == (0.0, 0, 2)
    assert module.risk_weighted_score([]) == (0.0, 0, 0, [])
    lines = module.render_risk_weighted_score([])
    assert lines[0] == "## Risk-weighted score"
    assert "- risk-weighted score: 0.0% (0/0 weighted points)" in lines


# HTML export (G10) ------------------------------------------------------------


def test_export_html_writes_self_contained_file(tmp_path):
    """G10 happy: --export html writes self-contained HTML, stdout stays markdown."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    out = tmp_path / "report.html"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("# Engagement report")
    assert "## Risk-weighted score" in proc.stdout
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert html.endswith("</html>\n")
    assert "<style>" in html
    assert "#c0392b" in html  # critical severity color in the inline style
    assert "<h1>" in html and "<h2>" in html
    assert "<table>" in html and "<thead>" in html
    assert 'class="sev-low"' in html
    assert "<!-- findings-snapshot: " in html
    # No external assets: the only hrefs are internal #coverage-* anchors.
    assert "src=" not in html and 'href="http' not in html
    assert 'href="#coverage-abuse"' in html


def test_export_html_is_deterministic(tmp_path):
    """G10 determinism: same inputs export byte-identical HTML files."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])

    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    proc1 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(first)
    )
    proc2 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(second)
    )
    assert proc1.returncode == 0, proc1.stderr
    assert proc2.returncode == 0, proc2.stderr
    assert proc1.stdout == proc2.stdout
    assert first.read_bytes() == second.read_bytes()


def test_export_html_unsupported_format_exits_1(tmp_path):
    """G10 edge: an unknown --export format fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--export",
        "pdf",
        str(tmp_path / "report.pdf"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr
    assert not (tmp_path / "report.pdf").exists()


def test_export_html_unwritable_output_exits_1(tmp_path):
    """G10 edge: an unwritable --export path fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(tmp_path)
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_render_html_report_converter_units():
    """G10 units: headings/tables/code-fences/lists convert deterministically."""
    module = _report_module()
    markdown = (
        "# Title\n"
        "\n"
        "## Section\n"
        "\n"
        "| a | b |\n"
        "| --- | --- |\n"
        "| critical | `x<y` |\n"
        "\n"
        "```\n"
        "raw <b> & critical\n"
        "```\n"
        "\n"
        "- low item\n"
        "- plain\n"
        "\n"
        "<!-- findings-snapshot: {} -->\n"
    )
    first = module.render_html_report(markdown)
    assert module.render_html_report(markdown) == first  # deterministic
    assert "<h1>Title</h1>" in first
    assert "<h2>Section</h2>" in first
    assert "<thead>" in first and "<th>a</th>" in first
    assert 'class="sev-critical"' in first  # table cell severity colored
    assert "<code>x&lt;y</code>" in first  # code span escaped, not colored
    assert "raw &lt;b&gt; &amp; critical" in first  # fence raw, uncolored
    assert "<ul>" in first and 'class="sev-low"' in first
    assert "<!-- findings-snapshot: {} -->" in first
    assert "src=" not in first and "href=" not in first


def test_render_html_report_escapes_markup():
    """G10 units: raw markup in prose is escaped, never emitted as tags."""
    module = _report_module()
    html = module.render_html_report('# T\n\n<a href="x">low</a>\n')
    assert "<a " not in html
    assert "&lt;a href=&quot;x&quot;&gt;" in html


# Combined --compare mode (I10) -----------------------------------------------


def _compare_seed(previous_dir: Path, current_dir: Path) -> Path:
    """One-run all-pass baseline; two-run current with one failing class."""
    previous_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(previous_dir, cycle(T1))
    seed_last_run(previous_dir)
    seed_findings(previous_dir, [finding_row("abuse-01", "resp-a", T1)])
    baseline = previous_dir.parent / "baseline-i10.md"
    baseline.write_text(
        run_script("--reports-dir", str(previous_dir), "--since", T0).stdout,
        encoding="utf-8",
    )
    current_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(current_dir, cycle(T1) + cycle(T2))
    seed_last_run(current_dir, wrong={"jailbreak-01": "not-detected"})
    seed_findings(
        current_dir,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T2),
        ],
    )
    return baseline


def test_compare_renders_all_comparison_sections(tmp_path):
    """I10 happy: --compare alone renders week-over-week, side-by-side,
    drill-down diff, and the skipped-trend note (no --trend given)."""
    baseline = _compare_seed(tmp_path / "previous", tmp_path / "current")
    proc = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--compare",
        str(baseline),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Week-over-week comparison" in proc.stdout
    assert "## Side-by-side comparison" in proc.stdout
    assert "## Drill-down diff" in proc.stdout
    assert "## Trend" not in proc.stdout
    compare_body = section_lines(proc.stdout, "## Week-over-week comparison")
    assert "- baseline: baseline-i10.md" in "\n".join(compare_body)
    assert (
        "- trend: skipped (no --trend history provided with --compare)" in compare_body
    )
    side_body = section_lines(proc.stdout, "## Side-by-side comparison")
    assert f"- baseline: {baseline.name}" in "\n".join(side_body)
    assert "| coverage | 5/6 covered | 6/6 covered |" in "\n".join(side_body)
    diff_body = section_lines(proc.stdout, "## Drill-down diff")
    assert f"- baseline: {baseline.name}" in "\n".join(diff_body)
    assert "- new: 1, resolved: 0" in "\n".join(diff_body)


def test_compare_matches_side_by_side_plus_diff_drill_down(tmp_path):
    """I10 equivalence: the --compare side-by-side/drill-down sections equal
    passing --side-by-side/--diff-drill-down with the same file."""
    baseline = _compare_seed(tmp_path / "previous", tmp_path / "current")
    combined = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--compare",
        str(baseline),
    )
    separate = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--side-by-side",
        str(baseline),
        "--diff-drill-down",
        str(baseline),
    )
    assert combined.returncode == 0, combined.stderr
    assert separate.returncode == 0, separate.stderr
    for heading in ("## Side-by-side comparison", "## Drill-down diff"):
        assert section_lines(combined.stdout, heading) == section_lines(
            separate.stdout, heading
        )
    # The separate run has neither the week-over-week section nor the note.
    assert "## Week-over-week comparison" not in separate.stdout
    assert "trend: skipped" not in separate.stdout


def test_compare_with_trend_renders_trend_without_skip_note(tmp_path):
    """I10 trend: --compare --trend renders ## Trend and drops the note."""
    baseline = _compare_seed(tmp_path / "previous", tmp_path / "current")
    history = tmp_path / "history.ndjson"
    seed_trend(history, TREND_ROWS)
    proc = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--compare",
        str(baseline),
        "--trend",
        str(history),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Trend" in proc.stdout
    assert "trend: skipped" not in proc.stdout
    trend_body = section_lines(proc.stdout, "## Trend")
    assert "- source: history.ndjson" in "\n".join(trend_body)
    assert "## Side-by-side comparison" in proc.stdout
    assert "## Drill-down diff" in proc.stdout


def test_compare_is_deterministic_with_all_sections(tmp_path):
    """I10 determinism: combined --compare runs are byte-identical."""
    baseline = _compare_seed(tmp_path / "previous", tmp_path / "current")
    first = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--compare",
        str(baseline),
    )
    second = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--compare",
        str(baseline),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_render_comparison_trend_skipped_unit():
    """I10 units: trend_skipped adds the note; default output unchanged."""
    module = _report_module()
    plain = module.render_comparison(
        current={"runs": 2, "pass_pct": 50, "gaps": 1, "new": 1, "rescinded": 0},
        previous={"runs": 1, "pass_pct": 100, "gaps": 0, "new": 1, "rescinded": 0},
        baseline_label="prev.md",
    )
    assert not any("trend: skipped" in line for line in plain)
    skipped = module.render_comparison(
        current={"runs": 2, "pass_pct": 50, "gaps": 1, "new": 1, "rescinded": 0},
        previous={"runs": 1, "pass_pct": 100, "gaps": 0, "new": 1, "rescinded": 0},
        baseline_label="prev.md",
        trend_skipped=True,
    )
    assert skipped[0] == "## Week-over-week comparison"
    assert "- trend: skipped (no --trend history provided with --compare)" in skipped
    # Same table rows either way; only the note line differs.
    assert [line for line in skipped if not line.startswith("- trend:")] == plain


def test_json_compare_includes_side_by_side_and_drill_down_diff(tmp_path):
    """I10 JSON: --json --compare gains side_by_side/drill_down_diff objects
    plus the trend note, without needing the individual flags."""
    baseline = _compare_seed(tmp_path / "previous", tmp_path / "current")
    proc = run_script(
        "--reports-dir",
        str(tmp_path / "current"),
        "--since",
        T0,
        "--json",
        "--compare",
        str(baseline),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["comparison"]["baseline"] == baseline.name
    assert (
        data["comparison"]["trend_note"]
        == "skipped (no --trend history provided with --compare)"
    )
    assert data["side_by_side"]["baseline"] == baseline.name
    metrics = {row["metric"]: row for row in data["side_by_side"]["metrics"]}
    assert (metrics["runs"]["current"], metrics["runs"]["previous"]) == (2, 1)
    assert data["drill_down_diff"]["baseline"] == baseline.name
    assert data["drill_down_diff"]["new_count"] == 1
    assert data["drill_down_diff"]["resolved_count"] == 0
    assert "trend" not in data


# HTML coverage drill-down (I12) ------------------------------------------------


def test_export_html_coverage_drill_down(tmp_path):
    """I12 happy: matrix class cells anchor to per-class drill-down tables."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    out = tmp_path / "report.html"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    html = out.read_text(encoding="utf-8")
    # Every matrix row links to its drill-down anchor.
    for klass in (
        "abuse",
        "auth-bypass",
        "cost-abuse",
        "jailbreak",
        "malformed-input",
        "prompt-injection",
    ):
        assert f'href="#coverage-{klass}"' in html
        assert f'<h3 id="coverage-{klass}">{klass}</h3>' in html
    assert "<h2>Coverage drill-down</h2>" in html
    # Drill-down sits after the matrix table in document order.
    assert html.find("<h2>Coverage drill-down</h2>") > html.find(
        '<a href="#coverage-abuse"'
    )
    # Per-class values: one failing class has 0 wins, 1 gap, gappy verdict.
    jailbreak = html.split('<h3 id="coverage-jailbreak">')[1].split("<h3")[0]
    assert "<tr><td>cases</td><td>1</td></tr>" in jailbreak
    assert "<tr><td>runs</td><td>1</td></tr>" in jailbreak
    assert "<tr><td>wins</td><td>0</td></tr>" in jailbreak
    assert "<tr><td>gaps</td><td>1</td></tr>" in jailbreak
    assert 'class="verdict-gappy"' in jailbreak
    abuse = html.split('<h3 id="coverage-abuse">')[1].split("<h3")[0]
    assert "<tr><td>wins</td><td>1</td></tr>" in abuse
    assert 'class="verdict-covered"' in abuse
    # Still self-contained: no external assets, only fragment links.
    assert "src=" not in html and 'href="http' not in html


def test_export_html_coverage_drill_down_is_deterministic(tmp_path):
    """I12 determinism: same inputs export byte-identical drill-down HTML."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    proc1 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(first)
    )
    proc2 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(second)
    )
    assert proc1.returncode == 0, proc1.stderr
    assert proc2.returncode == 0, proc2.stderr
    assert first.read_bytes() == second.read_bytes()


def test_render_html_coverage_helpers_units():
    """I12 units: matrix parsing/slugs, anchored table, drill-down section."""
    module = _report_module()
    markdown = (
        "# Engagement report\n"
        "\n"
        "## Coverage matrix\n"
        "\n"
        "| class | cases | runs | pass rate | gaps | verdict |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered |\n"
        "| jailbreak | 1 | 1 | 0/1 (0.0%) | 1 | gappy |\n"
        "\n"
        "## Detection gaps\n"
    )
    assert module._parse_coverage_matrix(markdown) == [
        ("abuse", 1, 1, 1, 0, "covered"),
        ("jailbreak", 1, 1, 0, 1, "gappy"),
    ]
    assert module._coverage_slug("auth-bypass") == "auth-bypass"
    html = module.render_html_report(markdown)
    assert module.render_html_report(markdown) == html  # deterministic
    assert '<td><a href="#coverage-abuse">abuse</a></td>' in html
    assert '<tr id="coverage-row-jailbreak">' in html
    assert "<h2>Coverage drill-down</h2>" in html
    assert '<h3 id="coverage-jailbreak">jailbreak</h3>' in html
    assert "<tr><td>wins</td><td>0</td></tr>" in html
    # Markdown without a coverage matrix gains no anchors or section.
    plain = module.render_html_report("# T\n\n| a | b |\n| --- | --- |\n| x | y |\n")
    assert "Coverage drill-down" not in plain
    assert "href=" not in plain


# Operator notes via --annotate (J10) ------------------------------------------


def _annotate_fixture(directory: Path) -> None:
    """One run of history plus one finding for annotate tests."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory)
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def test_annotate_renders_operator_notes_section(tmp_path):
    """J10 happy: repeatable --annotate renders a case_id|note table."""
    fixture = tmp_path / "reports"
    _annotate_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--annotate",
        "abuse-01=follow up with owner",
        "--annotate",
        "ghost-99=stale reference",
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Operator notes" in proc.stdout
    body = section_lines(proc.stdout, "## Operator notes")
    assert "| case_id | note |" in body
    assert "| abuse-01 | follow up with owner |" in body
    ghost = [line for line in body if line.startswith("| ghost-99 |")]
    assert len(ghost) == 1
    assert "(no matching case in this report)" in ghost[0]


def test_annotate_section_after_drill_down(tmp_path):
    """J10 order: Operator notes sits after drill-down, before gaps."""
    fixture = tmp_path / "reports"
    _annotate_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--drill-down",
        "abuse",
        "--annotate",
        "abuse-01=noted",
    )
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    drill_at = text.find("## Coverage drill-down: abuse")
    notes_at = text.find("## Operator notes")
    gaps_at = text.find("## Detection gaps")
    assert drill_at != -1 and notes_at != -1 and gaps_at != -1
    assert drill_at < notes_at < gaps_at


def test_annotate_is_deterministic(tmp_path):
    """J10 determinism: same annotations render byte-identical runs."""
    fixture = tmp_path / "reports"
    _annotate_fixture(fixture)
    args = [
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--annotate",
        "abuse-01=first",
        "--annotate",
        "ghost-99=second",
    ]
    first = run_script(*args)
    second = run_script(*args)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_annotate_malformed_exits_1(tmp_path):
    """J10 edge: an --annotate value without = fails closed with error:."""
    fixture = tmp_path / "reports"
    _annotate_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--annotate", "no-equals-here"
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_no_annotate_flag_renders_no_section(tmp_path):
    """J10 regression: without --annotate the report has no new section."""
    fixture = tmp_path / "reports"
    _annotate_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Operator notes" not in proc.stdout


def test_annotate_trend_persistence_and_recall(tmp_path):
    """J10 trend: --annotate persists kind/note lines; later runs recall them."""
    fixture = tmp_path / "reports"
    _annotate_fixture(fixture)
    history = tmp_path / "history.ndjson"
    seed_trend(history, [{"pass_rate": 80.0, "findings": 5}])

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--trend",
        str(history),
        "--annotate",
        "abuse-01=check trend",
    )
    assert proc.returncode == 0, proc.stderr
    # Metric section unaffected by the note line; notes section rendered.
    assert "- pass rate: 80.0%" in proc.stdout
    assert "| abuse-01 | check trend |" in proc.stdout
    # The note was appended to the history file as a kind/note line.
    rows = [
        json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0] == {"findings": 5, "pass_rate": 80.0}
    assert rows[1] == {"case_id": "abuse-01", "kind": "note", "note": "check trend"}
    # A later run without --annotate still shows the persisted note, once.
    recall = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--trend", str(history)
    )
    assert recall.returncode == 0, recall.stderr
    assert "- pass rate: 80.0%" in recall.stdout
    body = section_lines(recall.stdout, "## Operator notes")
    assert body.count("| abuse-01 | check trend |") == 1


def test_annotate_history_and_parse_units(tmp_path):
    """J10 units: note-tolerant history loading plus parse/render helpers."""
    module = _report_module()
    assert module.parse_annotation("abuse-01=a=b") == ("abuse-01", "a=b")
    assert module.parse_annotation("  abuse-01 = trim  ") == ("abuse-01", "trim")
    with pytest.raises(module.ReportError):
        module.parse_annotation("no-equals-here")
    with pytest.raises(module.ReportError):
        module.parse_annotation("=empty-case")
    lines = module.render_operator_notes(
        [("abuse-01", "kept"), ("ghost-99", "dropped")], {"abuse-01"}
    )
    assert lines[0] == "## Operator notes"
    assert "| abuse-01 | kept |" in lines
    assert "| ghost-99 | dropped (no matching case in this report) |" in lines
    assert module.merge_notes([("a", "1")], [("a", "1"), ("b", "2")]) == [
        ("a", "1"),
        ("b", "2"),
    ]

    history = tmp_path / "history.ndjson"
    seed_trend(
        history,
        [
            {"pass_rate": 80.0, "findings": 5},
            {"case_id": "abuse-01", "kind": "note", "note": "persisted"},
            {"pass_rate": 90.0, "findings": 3},
        ],
    )
    # Metric loader skips the note line gracefully; notes loader skips metrics.
    rates, counts = module.load_trend_history(history)
    assert rates == [80.0, 90.0]
    assert counts == [5, 3]
    assert module.load_trend_notes(history) == [("abuse-01", "persisted")]
    empty = tmp_path / "empty.ndjson"
    empty.write_text("", encoding="utf-8")
    assert module.load_trend_notes(empty) == []


def test_json_composes_with_annotate(tmp_path):
    """J10 composition: --json --annotate adds an operator_notes array."""
    fixture = tmp_path / "reports"
    _annotate_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--annotate",
        "abuse-01=kept",
        "--annotate",
        "ghost-99=lost",
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["operator_notes"] == [
        {"case_id": "abuse-01", "note": "kept", "matched": True},
        {"case_id": "ghost-99", "note": "lost", "matched": False},
    ]


# HTML risk-score badge (J12) ---------------------------------------------------


def test_export_html_risk_score_badge(tmp_path):
    """J12 happy: --export html adds a risk badge next to the h1."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture)
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    out = tmp_path / "report.html"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    # All-pass run scores 100.0 at the low (green) tier.
    html = out.read_text(encoding="utf-8")
    badge = '<span class="risk-badge sev-low">Risk score: 100.0 / 100</span>'
    assert badge in html
    assert html.find("</h1>\n" + badge) != -1
    # Markdown output unchanged: no badge markup on stdout.
    assert "risk-badge" not in proc.stdout
    assert "<span" not in proc.stdout
    # Deterministic reruns export byte-identical badges.
    again = tmp_path / "again.html"
    rerun = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(again)
    )
    assert rerun.returncode == 0, rerun.stderr
    assert again.read_bytes() == out.read_bytes()


def test_export_html_badge_tier_follows_score(tmp_path):
    """J12 tiers: a failing class drops the score and recolors the badge."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    out = tmp_path / "report.html"
    module = _report_module()

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    html = out.read_text(encoding="utf-8")
    score, _earned, _possible, _breakdown = module.risk_weighted_score(
        [
            (
                class_name,
                cases,
                runs,
                wins,
                (100.0 * wins / runs if runs else 0.0),
                gaps,
                verdict,
            )
            for class_name, cases, runs, wins, gaps, verdict in (
                module._parse_coverage_matrix(proc.stdout)
            )
        ]
    )
    assert score < 100.0
    expected = (
        f'<span class="risk-badge sev-{module.risk_score_tier(score)}">'
        f"Risk score: {score:.1f} / 100</span>"
    )
    assert expected in html


def test_risk_score_badge_units():
    """J12 units: badge text/tier from the matrix; none without a matrix."""
    module = _report_module()
    assert module.risk_score_tier(100.0) == "low"
    assert module.risk_score_tier(90.0) == "low"
    assert module.risk_score_tier(89.9) == "moderate"
    assert module.risk_score_tier(70.0) == "moderate"
    assert module.risk_score_tier(69.9) == "high"
    assert module.risk_score_tier(40.0) == "high"
    assert module.risk_score_tier(39.9) == "critical"
    assert module.risk_score_tier(0.0) == "critical"
    markdown = (
        "# Engagement report\n"
        "\n"
        "## Coverage matrix\n"
        "\n"
        "| class | cases | runs | pass rate | gaps | verdict |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered |\n"
        "| jailbreak | 1 | 1 | 0/1 (0.0%) | 1 | gappy |\n"
    )
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 1, 0, 0.0, 1, "gappy"),
    ]
    score, _earned, _possible, _breakdown = module.risk_weighted_score(coverage)
    assert module.risk_score_badge(markdown) == (
        f'<span class="risk-badge sev-{module.risk_score_tier(score)}">'
        f"Risk score: {score:.1f} / 100</span>"
    )
    assert module.risk_score_badge(markdown) == module.risk_score_badge(markdown)
    assert module.risk_score_badge("# T\n\nNo matrix here.\n") == ""
    html = module.render_html_report(markdown)
    assert module.risk_score_badge(markdown) in html
    plain = module.render_html_report("# T\n\n| a | b |\n| --- | --- |\n| x | y |\n")
    assert '<span class="risk-badge' not in plain


# Executive summary + email block (K10) ----------------------------------------


def _exec_fixture(directory: Path) -> None:
    """One all-pass run of history plus one finding for exec-summary tests."""
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory)
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def test_exec_summary_renders_after_title_before_header(tmp_path):
    """K10 happy: --exec-summary renders 3-6 bullets after title, before Header."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0, "--exec-summary")
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert text.startswith("# Engagement report\n")
    summary_at = text.index("## Executive summary")
    header_at = text.index("## Header")
    assert summary_at < header_at
    body = section_lines(text, "## Executive summary")
    bullets = [line for line in body if line.startswith("- ")]
    assert 3 <= len(bullets) <= 6
    joined = "\n".join(bullets)
    assert "- runs in window: 2" in joined
    assert "- risk-weighted score: 100.0%" in joined
    assert "- findings: +1 new / -0 rescinded" in joined
    assert "(no baseline)" in joined


def test_exec_summary_is_deterministic(tmp_path):
    """K10 determinism: same inputs render byte-identical summary runs."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    first = run_script("--reports-dir", str(fixture), "--since", T0, "--exec-summary")
    second = run_script("--reports-dir", str(fixture), "--since", T0, "--exec-summary")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_exec_summary_previous_baseline_delta(tmp_path):
    """K10 vs previous: a seeded baseline report drives the pass-rate delta."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out_dir = fixture / "reports"
    out_dir.mkdir()
    (out_dir / "engagement-2000-01-01_2000-01-02.md").write_text(
        "# Engagement report\n"
        "\n"
        "## Runs summary\n"
        "\n"
        "- runs in window: 3\n"
        "\n"
        "## Per-technique pass rates\n"
        "\n"
        "| case_id | result |\n"
        "| --- | --- |\n"
        "| a-01 | 1/1 (100.0%) |\n"
        "| b-01 | 0/1 (0.0%) |\n"
        "\n"
        "## Detection gaps\n"
        "\n"
        "- none\n"
        "\n"
        "## Findings deltas\n"
        "\n"
        "- new: 1, rescinded: 0\n"
        "\n"
        "<!-- findings-snapshot: {} -->\n",
        encoding="utf-8",
    )

    proc = run_script("--reports-dir", str(fixture), "--since", T0, "--exec-summary")
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Executive summary")
    assert "- pass rate: 100% (previous 50%, +50pp ↑)" in body


def test_exec_summary_failing_class_names_top_risk(tmp_path):
    """K10 risk: a failing class is named in the top-risk bullet."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])

    proc = run_script("--reports-dir", str(fixture), "--since", T0, "--exec-summary")
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Executive summary")
    risk = [line for line in body if line.startswith("- top risk classes:")]
    assert len(risk) == 1
    assert "jailbreak" in risk[0]
    assert "all classes fully covered" not in risk[0]


def test_exec_summary_with_recipient_renders_email_block(tmp_path):
    """K10 email: --recipient adds a Subject-led block under 2000 chars at the end."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--exec-summary",
        "--recipient",
        "lead@example.com",
    )
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert "## Email summary" in text
    email_at = text.index("## Email summary")
    assert email_at > text.index("## Severity timeline")
    assert email_at < text.index("<!-- findings-snapshot:")
    body = section_lines(text, "## Email summary")
    subjects = [line for line in body if line.startswith("Subject:")]
    assert len(subjects) == 1
    assert "To: lead@example.com" in body
    block = text[email_at : text.index("<!-- findings-snapshot:")]
    assert len(block) < 2000
    # Deterministic reruns render a byte-identical email block.
    rerun = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--exec-summary",
        "--recipient",
        "lead@example.com",
    )
    assert rerun.returncode == 0, rerun.stderr
    assert rerun.stdout == proc.stdout


def test_recipient_without_exec_summary_renders_no_email(tmp_path):
    """K10 regression: --recipient alone adds no email or summary section."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--recipient", "lead@example.com"
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Executive summary" not in proc.stdout
    assert "## Email summary" not in proc.stdout


def test_no_exec_summary_flag_renders_no_section(tmp_path):
    """K10 regression: without --exec-summary the report has no new section."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Executive summary" not in proc.stdout
    assert "## Email summary" not in proc.stdout


def test_exec_summary_weighted_gaps_and_email_units():
    """K10 units: risk ranking order plus the sub-2000-char email block."""
    module = _report_module()
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 1, 0, 0.0, 1, "gappy"),
        ("auth-bypass", 1, 1, 0, 0.0, 0, "gappy"),
    ]
    ranked = module.weighted_gaps(coverage)
    assert ranked[0] == ("auth-bypass", 10)
    assert ranked[1] == ("jailbreak", 10)
    assert ranked[-1] == ("abuse", 0)
    lines = module.render_exec_summary(
        runs=2,
        pass_rows=[("a-01", "1/1 (100.0%)"), ("b-01", "0/1 (0.0%)")],
        coverage=coverage,
        new_count=1,
        rescinded_count=0,
        previous_pass=50,
    )
    assert lines[0] == "## Executive summary"
    bullets = [line for line in lines if line.startswith("- ")]
    assert 3 <= len(bullets) <= 6
    assert "- pass rate: 50% (previous 50%, +0pp →)" in bullets
    email = module.render_email_block(
        recipient="lead@example.com",
        window_start=module.parse_rfc3339(T0),
        window_end=module.parse_rfc3339(T2),
        runs=2,
        pass_pct=50,
        previous_pass=50,
        score=33.3,
        earned=10,
        possible=30,
        new_count=1,
        rescinded_count=0,
        file_name="engagement-2026-10-01_2026-10-08.md",
    )
    assert email[0] == "## Email summary"
    assert email[2].startswith("Subject:")
    assert len("\n".join(email)) < 2000
    assert (
        module.render_email_block(
            recipient="lead@example.com",
            window_start=module.parse_rfc3339(T0),
            window_end=module.parse_rfc3339(T2),
            runs=2,
            pass_pct=50,
            previous_pass=50,
            score=33.3,
            earned=10,
            possible=30,
            new_count=1,
            rescinded_count=0,
            file_name="engagement-2026-10-01_2026-10-08.md",
        )
        == email
    )


def test_json_composes_with_exec_summary_and_recipient(tmp_path):
    """K10 composition: --json --exec-summary adds exec/email objects only then."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    plain = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert plain.returncode == 0, plain.stderr
    assert "exec_summary" not in json.loads(plain.stdout)
    full = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--exec-summary",
        "--recipient",
        "lead@example.com",
    )
    assert full.returncode == 0, full.stderr
    data = json.loads(full.stdout)
    assert data["exec_summary"]["runs"] == 2
    assert data["exec_summary"]["trend"] == "↑"
    assert data["email_summary"]["recipient"] == "lead@example.com"
    assert data["email_summary"]["subject"].startswith("Subject:")
    assert len(data["email_summary"]["subject"] + data["email_summary"]["body"]) < 2000


# CSV coverage export (K12) ------------------------------------------------------


def test_export_csv_writes_matrix_with_score_row(tmp_path):
    """K12 happy: --export-csv writes the matrix plus the score row; stdout stays md."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out = tmp_path / "coverage.csv"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export-csv", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("# Engagement report")
    rows = out.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "class,cases,runs,wins,pct,gaps,verdict,tier,weight,weighted"
    # Six schema classes plus the final risk-weighted-score row.
    assert len(rows) == 1 + 6 + 1
    assert all(len(row.split(",")) == 10 for row in rows)
    assert rows[1].startswith("abuse,")
    assert "covered" in rows[1]
    final = rows[-1].split(",")
    assert final[0] == "risk-weighted-score"
    assert final[4] == "100.0"
    assert "/" in final[9]


def test_export_csv_is_deterministic(tmp_path):
    """K12 determinism: same inputs export byte-identical CSV files."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"

    assert (
        run_script(
            "--reports-dir", str(fixture), "--since", T0, "--export-csv", str(first)
        ).returncode
        == 0
    )
    assert (
        run_script(
            "--reports-dir", str(fixture), "--since", T0, "--export-csv", str(second)
        ).returncode
        == 0
    )
    assert first.read_bytes() == second.read_bytes()


def test_export_csv_unwritable_output_exits_1(tmp_path):
    """K12 edge: an unwritable --export-csv path fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export-csv", str(fixture)
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_coverage_csv_rows_units(tmp_path):
    """K12 units: column order, quoting round-trip, and the score row."""
    import csv as csv_module

    module = _report_module()
    assert module.CSV_COLUMNS == (
        "class",
        "cases",
        "runs",
        "wins",
        "pct",
        "gaps",
        "verdict",
        "tier",
        "weight",
        "weighted",
    )
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("weird,class", 2, 1, 0, 0.0, 1, "gappy"),
    ]
    rows = module.coverage_csv_rows(coverage)
    assert len(rows) == 3
    assert rows[0][:7] == ["abuse", "1", "1", "1", "100.0", "0", "covered"]
    assert rows[0][7:] == ["high", "5", "5/5"]
    # Unknown classes fall back to the low tier at weight 1.
    assert rows[1][7:9] == ["low", "1"]
    assert rows[-1][0] == "risk-weighted-score"
    score, earned, possible, _breakdown = module.risk_weighted_score(coverage)
    assert rows[-1][4] == f"{score:.1f}"
    assert rows[-1][9] == f"{earned}/{possible}"
    # Proper quoting: a comma in a class survives a csv round-trip.
    assert module.write_csv_report(coverage, tmp_path / "q.csv") == 0
    with (tmp_path / "q.csv").open(newline="", encoding="utf-8") as handle:
        parsed = list(csv_module.reader(handle))
    assert parsed[0] == list(module.CSV_COLUMNS)
    assert parsed[2][0] == "weird,class"


def test_export_csv_composes_with_exec_summary(tmp_path):
    """K12 composition: --export-csv and --exec-summary work in one run."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out = tmp_path / "coverage.csv"

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--exec-summary",
        "--export-csv",
        str(out),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Executive summary" in proc.stdout
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("class,cases")


# Threshold gate (L10) ---------------------------------------------------------


def _threshold_fixture(directory: Path, wrong=None) -> None:
    """Two runs of history; all-pass unless ``wrong`` flips observations."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory, wrong=wrong)
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def test_threshold_pass_appends_gate_line_and_exits_0(tmp_path):
    """L10 happy: an all-pass run meeting gate:80 exits 0 with a PASS line."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--threshold", "gate:80"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout.splitlines()[-1] == "Gate: PASS (pass-rate 100 >= 80)"


def test_threshold_fail_exits_3_with_stderr_and_fail_line(tmp_path):
    """L10 gate: one failing class (5/6 = 83%) below gate:100 exits 3."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--threshold", "gate:100"
    )
    assert proc.returncode == 3, proc.stdout
    assert proc.stdout.splitlines()[-1] == "Gate: FAIL"
    assert "gate: FAIL (pass-rate 83 < 100)" in proc.stderr


def test_threshold_boundary_equal_value_passes(tmp_path):
    """L10 boundary: value == gate (83 >= 83) still passes."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--threshold", "gate:83"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[-1] == "Gate: PASS (pass-rate 83 >= 83)"


def test_threshold_gate_risk_uses_weighted_score(tmp_path):
    """L10 risk: all-pass scores 100; one jailbreak FAIL drops to 85.3."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)
    passing = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--threshold", "gate-risk:90"
    )
    assert passing.returncode == 0, passing.stderr
    assert passing.stdout.splitlines()[-1] == "Gate: PASS (risk-score 100.0 >= 90)"

    failing_fixture = tmp_path / "failing"
    _threshold_fixture(failing_fixture, wrong={"jailbreak-01": "not-detected"})
    failing = run_script(
        "--reports-dir",
        str(failing_fixture),
        "--since",
        T0,
        "--threshold",
        "gate-risk:90",
    )
    assert failing.returncode == 3, failing.stdout
    assert failing.stdout.splitlines()[-1] == "Gate: FAIL"
    assert "gate: FAIL (risk-score 85.3 < 90)" in failing.stderr


def test_threshold_invalid_spec_exits_1(tmp_path):
    """L10 edge: malformed/out-of-range specs fail closed with error:."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)
    for bad in ("bogus", "gate:", "gate:abc", "gate:101", "gate:-1", "gate-risk:nan"):
        proc = run_script(
            "--reports-dir", str(fixture), "--since", T0, "--threshold", bad
        )
        assert proc.returncode == 1, bad
        assert "error:" in proc.stderr, bad
        assert "Gate:" not in proc.stdout, bad


def test_threshold_is_deterministic(tmp_path):
    """L10 determinism: same gate renders byte-identical runs."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})
    first = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--threshold", "gate:80"
    )
    second = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--threshold", "gate:80"
    )
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout


def test_threshold_json_gains_gate_object_only_when_set(tmp_path):
    """L10 composition: --json gains a gate object only with --threshold."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)

    plain = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert plain.returncode == 0, plain.stderr
    assert "gate" not in json.loads(plain.stdout)

    gated = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--json", "--threshold", "gate:80"
    )
    assert gated.returncode == 0, gated.stderr
    data = json.loads(gated.stdout)
    assert data["gate"] == {
        "metric": "pass-rate",
        "value": 100,
        "gate": 80,
        "passed": True,
    }
    assert list(data.keys())[-1] == "gate"

    failing_fixture = tmp_path / "failing"
    _threshold_fixture(failing_fixture, wrong={"jailbreak-01": "not-detected"})
    failing = run_script(
        "--reports-dir",
        str(failing_fixture),
        "--since",
        T0,
        "--json",
        "--threshold",
        "gate-risk:90",
    )
    assert failing.returncode == 3, failing.stdout
    assert failing.stderr != ""
    assert json.loads(failing.stdout)["gate"]["passed"] is False


def test_threshold_composes_with_out_and_export_html(tmp_path):
    """L10 composition: --out/--export html carry the gate; exit follows it."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)
    out = tmp_path / "report.md"
    html = tmp_path / "report.html"

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--out",
        str(out),
        "--export",
        "html",
        str(html),
    )
    assert proc.returncode == 0, proc.stderr
    assert out.read_text(encoding="utf-8").splitlines()[-1].startswith("Gate: PASS")
    assert "Gate: PASS" in html.read_text(encoding="utf-8")


def test_threshold_summary_mode(tmp_path):
    """L10 composition: --summary honors the gate (extra line / exit 3)."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    passing = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--summary",
        "--threshold",
        "gate:80",
    )
    assert passing.returncode == 0, passing.stderr
    assert passing.stdout.splitlines()[-1] == "Gate: PASS (pass-rate 83 >= 80)"

    failing = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--summary",
        "--threshold",
        "gate:100",
    )
    assert failing.returncode == 3, failing.stdout
    assert failing.stdout.splitlines()[-1] == "Gate: FAIL"
    assert "gate: FAIL" in failing.stderr


def test_threshold_parse_and_evaluate_units():
    """L10 units: spec parsing, normalization, and verdict lines."""
    module = _report_module()
    assert module.parse_threshold("gate:80") == ("pass-rate", 80.0)
    assert module.parse_threshold("gate-risk:70.5") == ("risk-score", 70.5)
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 1, 0, 0.0, 1, "gappy"),
    ]
    pass_rows = [("a", "1/1 (100.0%)"), ("b", "0/1 (0.0%)")]
    gate = module.evaluate_threshold(pass_rows, coverage, "gate:50")
    assert gate["passed"] is True
    assert gate["line"] == "Gate: PASS (pass-rate 50 >= 50)"
    assert gate["value"] == 50 and gate["gate"] == 50
    missed = module.evaluate_threshold(pass_rows, coverage, "gate:51")
    assert missed["passed"] is False and missed["line"] == "Gate: FAIL"
    assert missed["stderr"] == "gate: FAIL (pass-rate 50 < 51)"
    risk = module.evaluate_threshold(pass_rows, coverage, "gate-risk:100")
    assert risk["metric"] == "risk-score" and risk["passed"] is False
    for bad in ("gate", "other:80", "gate:1e999", "gate:NaN", "gate-risk:"):
        with pytest.raises(Exception, match="error:"):
            module.parse_threshold(bad)


# HTML gap evidence links (L12) ------------------------------------------------


def _evidence_fixture(directory: Path) -> None:
    """One failing jailbreak case plus a titled finding for its case id."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory, wrong={"jailbreak-01": "not-detected"})
    seed_findings(
        directory,
        [
            {
                **finding_row("jailbreak-01", "resp-j", T1),
                "class": "jailbreak",
                "title": "Jailbreak bypass observed",
            },
            {
                **finding_row("jailbreak-01", "resp-j2", T2),
                "class": "jailbreak",
                "title": "Second sighting",
            },
        ],
    )


def test_export_html_gap_evidence_links_and_details(tmp_path):
    """L12 happy: gap case ids link to <details> evidence with titles."""
    fixture = tmp_path / "reports"
    _evidence_fixture(fixture)
    out = tmp_path / "report.html"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    html = out.read_text(encoding="utf-8")
    jailbreak = html.split('<h3 id="coverage-jailbreak">')[1].split("<h3")[0]
    assert '<li><a href="#gap-jailbreak-01">jailbreak-01</a></li>' in jailbreak
    assert '<details id="gap-jailbreak-01">' in jailbreak
    assert "<summary>jailbreak-01</summary>" in jailbreak
    assert "<p>case id: jailbreak-01</p>" in jailbreak
    assert "Jailbreak bypass observed" in jailbreak
    assert "Second sighting" in jailbreak
    # Gap-free classes render no anchors or details.
    abuse = html.split('<h3 id="coverage-abuse">')[1].split("<h3")[0]
    assert "<p>no gaps</p>" in abuse
    assert "<details" not in abuse
    assert "src=" not in html and 'href="http' not in html


def test_export_html_gap_evidence_without_titles(tmp_path):
    """L12 edge: gaps with no titled findings note the absence."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})
    seed_findings(fixture, [finding_row("abuse-01", "resp-a", T1)])
    out = tmp_path / "report.html"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    html = out.read_text(encoding="utf-8")
    jailbreak = html.split('<h3 id="coverage-jailbreak">')[1].split("<h3")[0]
    assert '<a href="#gap-jailbreak-01">' in jailbreak
    assert "<p>no associated findings</p>" in jailbreak


def test_export_html_gap_evidence_is_deterministic(tmp_path):
    """L12 determinism: same inputs export byte-identical evidence HTML."""
    fixture = tmp_path / "reports"
    _evidence_fixture(fixture)
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    proc1 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(first)
    )
    proc2 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export", "html", str(second)
    )
    assert proc1.returncode == 0, proc1.stderr
    assert proc2.returncode == 0, proc2.stderr
    assert first.read_bytes() == second.read_bytes()


def test_gap_evidence_helpers_units(tmp_path):
    """L12 units: gap attribution, title mapping, and markdown fallback."""
    module = _report_module()
    cases = [
        ("abuse-01", "d", "", "abuse"),
        ("jailbreak-01", "d", "", "jailbreak"),
    ]
    gaps = ["case 'jailbreak-01': FAIL \u2014 observed detection 'x' != expected 'd'"]
    assert module.coverage_gap_case_ids(cases, gaps) == {"jailbreak": ["jailbreak-01"]}
    assert module.coverage_gap_case_ids(cases, []) == {}
    findings: list[dict[str, object]] = [
        {"case_id": "jailbreak-01", "title": "B title"},
        {"case_id": "jailbreak-01", "title": "A title"},
        {"case_id": "abuse-01"},
        {"case_id": "", "title": "ghost"},
    ]
    assert module.findings_titles_by_case(findings) == {
        "jailbreak-01": ["A title", "B title"]
    }
    html = module._html_coverage_drill_down(
        [("jailbreak", 1, 1, 0, 1, "gappy"), ("abuse", 1, 1, 1, 0, "covered")],
        {"jailbreak": ["jailbreak-01"]},
        {"jailbreak-01": ["A title"]},
    )
    assert '<a href="#gap-jailbreak-01">jailbreak-01</a>' in html
    assert "<details" in html and "A title" in html
    markdown = (
        "# Engagement report\n"
        "\n"
        "## Coverage matrix\n"
        "\n"
        "| class | cases | runs | pass rate | gaps | verdict |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| jailbreak | 1 | 1 | 0/1 (0.0%) | 1 | gappy |\n"
        "\n"
        "## Detection gaps\n"
        "\n"
        "- case 'jailbreak-01': FAIL\n"
    )
    # Markdown-only fallback mines the same gap ids (no titles available).
    assert module.render_html_report(markdown) == module.render_html_report(markdown)
    fallback = module.render_html_report(markdown)
    assert '<a href="#gap-jailbreak-01">jailbreak-01</a>' in fallback
    assert "<p>no associated findings</p>" in fallback


# Three-way trend vs older report (M10) --------------------------------------

T3 = "2026-10-10T12:00:00+00:00"


def _delta_seed(base: Path) -> tuple[Path, Path, Path]:
    """Older (1 run, two failing classes), previous (2 runs, one failing),
    current (3 runs, all-pass); returns ``(older_md, previous_md, current)``."""
    older_dir = base / "older"
    older_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(older_dir, cycle(T1))
    seed_last_run(older_dir, wrong={"jailbreak-01": "not-detected", "abuse-01": "nope"})
    seed_findings(
        older_dir,
        [
            finding_row("abuse-01", "resp-a", T1),
            finding_row("jailbreak-01", "resp-j", T1),
        ],
    )
    older_md = base / "older-m10.md"
    older_proc = run_script("--reports-dir", str(older_dir), "--since", T0)
    assert older_proc.returncode == 0, older_proc.stderr
    older_md.write_text(older_proc.stdout, encoding="utf-8")
    previous_dir = base / "previous"
    previous_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(previous_dir, cycle(T1) + cycle(T2))
    seed_last_run(previous_dir, wrong={"jailbreak-01": "not-detected"})
    seed_findings(previous_dir, [finding_row("abuse-01", "resp-a", T1)])
    previous_md = base / "previous-m10.md"
    previous_proc = run_script("--reports-dir", str(previous_dir), "--since", T0)
    assert previous_proc.returncode == 0, previous_proc.stderr
    previous_md.write_text(previous_proc.stdout, encoding="utf-8")
    current_dir = base / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    seed_transitions(current_dir, cycle(T1) + cycle(T2) + cycle(T3))
    seed_last_run(current_dir)
    seed_findings(current_dir, [])
    return older_md, previous_md, current_dir


def test_delta_renders_three_way_trend_after_side_by_side(tmp_path):
    """M10 happy: --delta with --compare renders older → previous → current."""
    older_md, previous_md, current_dir = _delta_seed(tmp_path)
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare",
        str(previous_md),
        "--delta",
        str(older_md),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Three-way trend" in proc.stdout
    assert proc.stdout.index("## Side-by-side comparison") < proc.stdout.index(
        "## Three-way trend"
    )
    body = "\n".join(section_lines(proc.stdout, "## Three-way trend"))
    assert "- older: older-m10.md" in body
    assert "- previous: previous-m10.md" in body
    assert "- runs: 1 → 2 → 3 (↑)" in body
    assert "- pass rate: 67% → 83% → 100% (↑)" in body
    assert "- coverage: 4/6 covered → 5/6 covered → 6/6 covered (↑)" in body
    assert "- findings: 2 → 1 → 0 (↑)" in body
    assert "- risk-weighted score: 70.6% → 85.3% → 100.0% (↑)" in body


def test_delta_is_deterministic(tmp_path):
    """M10 determinism: same inputs render byte-identical three-way runs."""
    older_md, previous_md, current_dir = _delta_seed(tmp_path)
    first = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare",
        str(previous_md),
        "--delta",
        str(older_md),
    )
    second = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare",
        str(previous_md),
        "--delta",
        str(older_md),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_delta_missing_file_exits_1(tmp_path):
    """M10 edge: an unreadable --delta path fails closed with error:."""
    _older_md, previous_md, current_dir = _delta_seed(tmp_path)
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare",
        str(previous_md),
        "--delta",
        str(tmp_path / "nope-m10.md"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr
    assert "## Three-way trend" not in proc.stdout


def test_no_delta_flag_renders_no_section(tmp_path):
    """M10 regression: without --delta the report has no three-way trend."""
    _older_md, previous_md, current_dir = _delta_seed(tmp_path)
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare",
        str(previous_md),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Three-way trend" not in proc.stdout


def test_delta_without_compare_falls_back_to_older(tmp_path):
    """M10 standalone: --delta alone reuses the older report as previous."""
    older_md, _previous_md, current_dir = _delta_seed(tmp_path)
    proc = run_script(
        "--reports-dir", str(current_dir), "--since", T0, "--delta", str(older_md)
    )
    assert proc.returncode == 0, proc.stderr
    body = "\n".join(section_lines(proc.stdout, "## Three-way trend"))
    assert "- older: older-m10.md" in body
    assert "- previous: older-m10.md" in body
    assert "- runs: 1 → 1 → 3 (↑)" in body
    assert "- risk-weighted score: 70.6% → 70.6% → 100.0% (↑)" in body


def test_trend_arrow_and_render_three_way_units():
    """M10 units: net arrows (flat within 0.5, findings invert) and rows."""
    module = _report_module()
    assert module._trend_arrow(89.0, 71.0) == "↑"
    assert module._trend_arrow(70.0, 71.0) == "↓"
    assert module._trend_arrow(71.4, 71.0) == "→"
    assert module._trend_arrow(70.5, 71.0) == "→"
    assert module._trend_arrow(1.0, 4.0, higher_is_better=False) == "↑"
    assert module._trend_arrow(5.0, 4.0, higher_is_better=False) == "↓"
    assert module._trend_arrow(4.0, 4.0, higher_is_better=False) == "→"
    lines = module.render_three_way_trend(
        older_label="older.md",
        previous_label="previous.md",
        older=(1, 71, 3, 6, 4, 80.0),
        previous=(2, 83, 5, 6, 2, 60.0),
        current=(2, 89, 5, 6, 2, 80.4),
    )
    assert lines[0] == "## Three-way trend"
    assert "- older: older.md" in lines
    assert "- previous: previous.md" in lines
    assert "- runs: 1 → 2 → 2 (↑)" in lines
    assert "- pass rate: 71% → 83% → 89% (↑)" in lines
    assert "- coverage: 3/6 covered → 5/6 covered → 5/6 covered (↑)" in lines
    assert "- findings: 4 → 2 → 2 (↑)" in lines
    # Risk net moves 0.4 (older → current), inside the flat band.
    assert "- risk-weighted score: 80.0% → 60.0% → 80.4% (→)" in lines
    regressed = module.render_three_way_trend(
        older_label="o",
        previous_label="p",
        older=(3, 100, 6, 6, 0, 100.0),
        previous=(2, 83, 5, 6, 1, 85.3),
        current=(1, 67, 4, 6, 2, 70.6),
    )
    assert "- runs: 3 → 2 → 1 (↓)" in regressed
    assert "- findings: 0 → 1 → 2 (↓)" in regressed


# Embeddable markdown fragment (M12) ------------------------------------------


def _export_md_fixture(directory: Path) -> None:
    """Two all-pass runs plus one finding for fragment tests."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory)
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def test_export_md_writes_fragment_stdout_unchanged(tmp_path):
    """M12 happy: --export-md writes matrix+risk (no h1/snapshot); outputs stay."""
    fixture = tmp_path / "reports"
    _export_md_fixture(fixture)
    out = tmp_path / "fragment.md"
    plain = run_script("--reports-dir", str(fixture), "--since", T0)
    assert plain.returncode == 0, plain.stderr
    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export-md", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == plain.stdout
    canonical = fixture / "reports" / "engagement-2026-10-01_2026-10-08.md"
    assert canonical.is_file()
    assert canonical.read_text(encoding="utf-8") == proc.stdout
    fragment = out.read_text(encoding="utf-8")
    assert "## Coverage matrix" in fragment
    assert "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered | █ |" in fragment
    assert "## Risk-weighted score" in fragment
    assert "- risk-weighted score: 100.0% (34/34 weighted points)" in fragment
    assert fragment.startswith("## Coverage matrix")
    assert "# Engagement report" not in fragment
    assert "findings-snapshot" not in fragment
    assert fragment.endswith("\n")


def test_export_md_is_deterministic(tmp_path):
    """M12 determinism: same inputs export byte-identical fragments."""
    fixture = tmp_path / "reports"
    _export_md_fixture(fixture)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    proc1 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export-md", str(first)
    )
    proc2 = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export-md", str(second)
    )
    assert proc1.returncode == 0, proc1.stderr
    assert proc2.returncode == 0, proc2.stderr
    assert first.read_bytes() == second.read_bytes()


def test_export_md_unwritable_output_exits_1(tmp_path):
    """M12 edge: an unwritable --export-md path fails closed with error:."""
    fixture = tmp_path / "reports"
    _export_md_fixture(fixture)
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--export-md",
        str(tmp_path / "missing" / "fragment.md"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_export_md_composes_with_threshold(tmp_path):
    """M12 composition: the gate line lands in the fragment; exit follows it."""
    fixture = tmp_path / "reports"
    _export_md_fixture(fixture)
    passing = tmp_path / "passing.md"
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--export-md",
        str(passing),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[-1] == "Gate: PASS (pass-rate 100 >= 80)"
    assert passing.read_text(encoding="utf-8").splitlines()[-1] == (
        "Gate: PASS (pass-rate 100 >= 80)"
    )
    failing = tmp_path / "failing.md"
    failing_fixture = tmp_path / "failing-reports"
    failing_fixture.mkdir()
    seed_transitions(failing_fixture, cycle(T1) + cycle(T2))
    seed_last_run(failing_fixture, wrong={"jailbreak-01": "not-detected"})
    seed_findings(failing_fixture, [finding_row("abuse-01", "resp-a", T1)])
    failed = run_script(
        "--reports-dir",
        str(failing_fixture),
        "--since",
        T0,
        "--threshold",
        "gate:100",
        "--export-md",
        str(failing),
    )
    assert failed.returncode == 3
    assert "gate: FAIL" in failed.stderr
    assert failed.stdout.splitlines()[-1] == "Gate: FAIL"
    assert failing.read_text(encoding="utf-8").splitlines()[-1] == "Gate: FAIL"


def test_render_md_fragment_units(tmp_path):
    """M12 units: matrix rows (n/a without runs), risk section, gate line."""
    module = _report_module()
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 0, 0, 0.0, 1, "gappy"),
    ]
    fragment = module.render_md_fragment(coverage=coverage)
    assert fragment.startswith("## Coverage matrix\n")
    assert "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered | █ |" in fragment
    assert "| jailbreak | 1 | 0 | 0/0 (n/a) | 1 | gappy | — |" in fragment
    assert "## Risk-weighted score" in fragment
    assert "# Engagement report" not in fragment
    assert "findings-snapshot" not in fragment
    assert "Gate:" not in fragment
    gated = module.render_md_fragment(
        coverage=coverage, gate_line="Gate: PASS (pass-rate 100 >= 80)"
    )
    assert gated.splitlines()[-1] == "Gate: PASS (pass-rate 100 >= 80)"
    assert gated.endswith("\n")


# Standalone recipient file (N10) ---------------------------------------------


def test_recipient_file_with_recipient_matches_email_block(tmp_path):
    """N10 happy: --recipient-file mirrors the in-report email block standalone."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out = tmp_path / "email.txt"

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--exec-summary",
        "--recipient",
        "lead@example.com",
        "--recipient-file",
        str(out),
    )
    assert proc.returncode == 0, proc.stderr
    # Stdout is unchanged by the sidecar file: same as without the flag.
    plain = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--exec-summary",
        "--recipient",
        "lead@example.com",
    )
    assert plain.returncode == 0, plain.stderr
    assert proc.stdout == plain.stdout
    text = out.read_text(encoding="utf-8")
    assert len(text) < 2000
    assert text.endswith("\n")
    file_lines = text.splitlines()
    assert file_lines[0].startswith("Subject:")
    assert file_lines[1] == ""
    assert "To: lead@example.com" in file_lines
    # Same subject and body as the in-report ## Email summary block
    # (section_lines also sweeps the trailing snapshot marker, so cut it).
    body = section_lines(proc.stdout, "## Email summary")
    subjects = [line for line in body if line.startswith("Subject:")]
    assert len(subjects) == 1
    assert file_lines[0] == subjects[0]
    block_body = body[body.index(subjects[0]) + 1 :]
    cut = next(
        (
            index
            for index, line in enumerate(block_body)
            if line.startswith("<!--") or line.startswith("Gate:")
        ),
        len(block_body),
    )
    expected = block_body[:cut]
    while expected and expected[-1] == "":
        expected.pop()
    assert file_lines[1:] == expected


def test_recipient_file_implies_exec_summary_derivation(tmp_path):
    """N10 implied: --recipient-file alone still writes the email file."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out = tmp_path / "email.txt"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--recipient-file", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Email summary" not in proc.stdout
    assert "## Executive summary" not in proc.stdout
    text = out.read_text(encoding="utf-8")
    assert len(text) < 2000
    file_lines = text.splitlines()
    assert file_lines[0].startswith("Subject:")
    assert file_lines[1] == ""
    # No address: the To: line is dropped, the greeting stays.
    assert not any(line.startswith("To:") for line in file_lines)
    assert "Hi," in file_lines
    assert any(line.startswith("- runs: 2") for line in file_lines)


def test_recipient_file_is_deterministic(tmp_path):
    """N10 determinism: same inputs write byte-identical email files."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"

    assert (
        run_script(
            "--reports-dir",
            str(fixture),
            "--since",
            T0,
            "--exec-summary",
            "--recipient",
            "lead@example.com",
            "--recipient-file",
            str(first),
        ).returncode
        == 0
    )
    assert (
        run_script(
            "--reports-dir",
            str(fixture),
            "--since",
            T0,
            "--exec-summary",
            "--recipient",
            "lead@example.com",
            "--recipient-file",
            str(second),
        ).returncode
        == 0
    )
    assert first.read_bytes() == second.read_bytes()


def test_recipient_file_unwritable_output_exits_1(tmp_path):
    """N10 edge: an unwritable --recipient-file path fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--recipient-file",
        str(fixture),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_render_recipient_file_text_units():
    """N10 units: heading stripped; To: kept with recipient, dropped without."""
    module = _report_module()
    email = module.render_email_block(
        recipient="lead@example.com",
        window_start=module.parse_rfc3339(T0),
        window_end=module.parse_rfc3339(T2),
        runs=2,
        pass_pct=50,
        previous_pass=50,
        score=33.3,
        earned=10,
        possible=30,
        new_count=1,
        rescinded_count=0,
        file_name="engagement-2026-10-01_2026-10-08.md",
    )
    with_recipient = module.render_recipient_file_text(
        email, recipient="lead@example.com"
    )
    assert with_recipient.startswith("Subject:")
    assert with_recipient.splitlines()[1] == ""
    assert "## Email summary" not in with_recipient
    assert "To: lead@example.com" in with_recipient
    assert with_recipient.endswith("\n")
    assert len(with_recipient) < 2000
    # Deterministic on the same block.
    assert (
        module.render_recipient_file_text(email, recipient="lead@example.com")
        == with_recipient
    )
    without_recipient = module.render_recipient_file_text(email, recipient=None)
    assert without_recipient.startswith("Subject:")
    assert without_recipient.splitlines()[1] == ""
    assert not any(line.startswith("To:") for line in without_recipient.splitlines())
    assert "Hi," in without_recipient.splitlines()
    assert without_recipient.splitlines()[0] == with_recipient.splitlines()[0]


# Machine-readable scorecard (N12) ----------------------------------------------


def test_export_scorecard_writes_lines_with_overall(tmp_path):
    """N12 happy: --export-scorecard writes per-class lines plus OVERALL."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out = tmp_path / "scorecard.txt"

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--export-scorecard", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("# Engagement report")
    lines = out.read_text(encoding="utf-8").splitlines()
    # Two # header comments document the verdict mapping.
    assert len(lines) == 2 + 6 + 1
    assert all(line.startswith("#") for line in lines[:2])
    assert "covered->PASS" in lines[1]
    assert "gappy->FAIL" in lines[1]
    data = [line.split("\t") for line in lines[2:]]
    assert all(len(fields) == 3 for fields in data[:-1])
    assert [row[0] for row in data[:-1]] == [
        "abuse",
        "auth-bypass",
        "cost-abuse",
        "jailbreak",
        "malformed-input",
        "prompt-injection",
    ]
    assert data[0] == ["abuse", "covered", "PASS"]
    overall = lines[-1].split("\t")
    assert overall == ["OVERALL", "100.0"]
    assert out.read_text(encoding="utf-8").endswith("\n")


def test_export_scorecard_is_deterministic(tmp_path):
    """N12 determinism: same inputs export byte-identical scorecards."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"

    assert (
        run_script(
            "--reports-dir",
            str(fixture),
            "--since",
            T0,
            "--export-scorecard",
            str(first),
        ).returncode
        == 0
    )
    assert (
        run_script(
            "--reports-dir",
            str(fixture),
            "--since",
            T0,
            "--export-scorecard",
            str(second),
        ).returncode
        == 0
    )
    assert first.read_bytes() == second.read_bytes()


def test_export_scorecard_unwritable_output_exits_1(tmp_path):
    """N12 edge: an unwritable --export-scorecard path fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--export-scorecard",
        str(fixture),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_export_scorecard_composes_with_all_modes(tmp_path):
    """N12 composition: scorecard works with summary, gate, and recipient files."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    scorecard = tmp_path / "scorecard.txt"
    email = tmp_path / "email.txt"

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--exec-summary",
        "--recipient",
        "lead@example.com",
        "--threshold",
        "gate:80",
        "--export-scorecard",
        str(scorecard),
        "--recipient-file",
        str(email),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Executive summary" in proc.stdout
    assert "## Email summary" in proc.stdout
    assert proc.stdout.splitlines()[-1] == "Gate: PASS (pass-rate 100 >= 80)"
    assert scorecard.is_file()
    assert scorecard.read_text(encoding="utf-8").splitlines()[-1] == ("OVERALL\t100.0")
    assert email.read_text(encoding="utf-8").splitlines()[0].startswith("Subject:")


def test_render_scorecard_units(tmp_path):
    """N12 units: covered->PASS; gappy/unexercised->FAIL; OVERALL carries score."""
    module = _report_module()
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 1, 0, 0.0, 1, "gappy"),
        ("auth-bypass", 0, 0, 0, 0.0, 0, "unexercised"),
    ]
    text = module.render_scorecard(coverage)
    assert text.endswith("\n")
    lines = text.splitlines()
    assert lines[0].startswith("#")
    assert lines[1].startswith("#")
    assert "covered->PASS" in lines[1]
    assert "gappy->FAIL" in lines[1]
    assert "unexercised->FAIL" in lines[1]
    data = {}
    for line in lines[2:]:
        fields = line.split("\t")
        if len(fields) == 3:
            data[fields[0]] = (fields[1], fields[2])
    assert data["abuse"] == ("covered", "PASS")
    assert data["jailbreak"] == ("gappy", "FAIL")
    assert data["auth-bypass"] == ("unexercised", "FAIL")
    score, _earned, _possible, _breakdown = module.risk_weighted_score(coverage)
    assert lines[-1] == f"OVERALL\t{score:.1f}"
    # Deterministic on the same coverage.
    assert module.render_scorecard(coverage) == text
    assert module.write_scorecard_report(coverage, tmp_path) == 1


# Unicode trend glyphs in the coverage matrix (O12) ---------------------------


def test_coverage_matrix_trend_column_and_glyphs(tmp_path):
    """O12 happy: the matrix gains a trend column with win-rate glyphs."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Coverage matrix")
    table = [line for line in body if line.startswith("| ")]
    assert table[0] == "| class | cases | runs | pass rate | gaps | verdict | trend |"
    assert table[1] == "| --- | --- | --- | --- | --- | --- | --- |"
    assert len(table) == 2 + 6
    assert all(line.endswith("| █ |") for line in table[2:])


def test_coverage_matrix_trend_glyph_mid_and_empty(tmp_path):
    """O12 cells: a failing class maps 0% to ▁; classes without runs get —."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture, wrong={"jailbreak-01": "not-detected"})
    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    text = "\n".join(section_lines(proc.stdout, "## Coverage matrix"))
    assert "| jailbreak | 1 | 1 | 0/1 (0.0%) | 1 | gappy | ▁ |" in text
    empty = run_script(
        "--reports-dir", str(fixture), "--since", "2027-01-01T00:00:00+00:00"
    )
    assert empty.returncode == 0, empty.stderr
    empty_body = section_lines(empty.stdout, "## Coverage matrix")
    empty_rows = [line for line in empty_body if line.startswith("| ")][2:]
    assert all(line.endswith("| — |") for line in empty_rows)


def test_coverage_trend_glyph_units():
    """O12 units: ramp mapping across SPARKLINE_BLOCKS, clamping, em dash."""
    module = _report_module()
    assert module.coverage_trend_glyph(0, 0.0) == "—"
    assert module.coverage_trend_glyph(0, 100.0) == "—"
    assert module.coverage_trend_glyph(1, 0.0) == "▁"
    assert module.coverage_trend_glyph(1, 100.0) == "█"
    assert module.coverage_trend_glyph(2, 50.0) == "▅"
    assert module.coverage_trend_glyph(1, 150.0) == "█"
    assert module.coverage_trend_glyph(1, -5.0) == "▁"
    assert module.coverage_trend_glyph(3, 75.0) == "▆"
    # Trended rows parse like plain ones; the HTML export still anchors them.
    trended = (
        "## Coverage matrix\n"
        "\n"
        "| class | cases | runs | pass rate | gaps | verdict | trend |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered | █ |\n"
        "| jailbreak | 1 | 0 | 0/0 (n/a) | 1 | gappy | — |\n"
    )
    assert module._parse_coverage_matrix(trended) == [
        ("abuse", 1, 1, 1, 0, "covered"),
        ("jailbreak", 1, 0, 0, 1, "gappy"),
    ]
    assert module._is_coverage_matrix_table(trended.splitlines()[2:3])
    html = module.render_html_report("# T\n\n" + trended + "\n## Detection gaps\n")
    assert '<td><a href="#coverage-abuse">abuse</a></td>' in html
    assert "█" in html
    ramp = [
        module.coverage_trend_glyph(4, pct) for pct in (0.0, 25.0, 50.0, 75.0, 100.0)
    ]
    blocks = list(module.SPARKLINE_BLOCKS)
    assert [blocks.index(glyph) for glyph in ramp] == sorted(
        blocks.index(glyph) for glyph in ramp
    )


# Multi-report trend across a directory (O10) ----------------------------------


def _multi_seed(base: Path) -> tuple[Path, Path, Path]:
    """Two seeded reports in a scan dir plus a current dir.

    Returns ``(multi_dir, current_dir, previous_md)``; the scan dir
    holds the older and previous M10 reports under names that invert
    chronological order, so the trend must sort by timestamp, not name.
    """
    older_md, previous_md, current_dir = _delta_seed(base)
    multi_dir = base / "multi"
    multi_dir.mkdir(parents=True, exist_ok=True)
    (multi_dir / "z-older.md").write_text(
        older_md.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (multi_dir / "a-previous.md").write_text(
        previous_md.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return multi_dir, current_dir, previous_md


def test_compare_multi_renders_trend_table(tmp_path):
    """O10 happy: --compare-multi renders one row per metric, columns oldest→newest."""
    multi_dir, current_dir, _previous_md = _multi_seed(tmp_path)
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare-multi",
        str(multi_dir),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Multi-report trend" in proc.stdout
    body = "\n".join(section_lines(proc.stdout, "## Multi-report trend"))
    assert "- source: multi (2 reports)" in body
    assert "| metric | z-older.md | a-previous.md | trend |" in body
    assert "| runs | 1 | 2 | 1 → 2 (↑) |" in body
    assert "| pass rate | 67% | 83% | 67% → 83% (↑) |" in body
    assert (
        "| coverage | 4/6 covered | 5/6 covered | 4/6 covered → 5/6 covered (↑) |"
        in body
    )
    assert "| findings | 2 | 1 | 2 → 1 (↑) |" in body
    assert "| risk-weighted score | 70.6% | 85.3% | 70.6% → 85.3% (↑) |" in body


def test_compare_multi_orders_by_timestamp_and_skips_unreadable(tmp_path):
    """O10 order: timestamp sort (missing last, name-tiebroken); bad files skipped."""
    module = _report_module()
    multi_dir, _current_dir, _previous_md = _multi_seed(tmp_path)
    (multi_dir / "no-stamp.md").write_text("# bare\n", encoding="utf-8")
    (multi_dir / "bad.md").write_bytes(b"\xff\xfe\x00not-utf8")
    legs, skipped = module.collect_multi_report_entries(multi_dir)
    assert [leg[0] for leg, _stamp in legs] == [
        "z-older.md",
        "a-previous.md",
        "no-stamp.md",
    ]
    assert skipped == ["bad.md"]
    lines = module.render_multi_report_trend(
        source_label=multi_dir.name, legs=legs, skipped=skipped
    )
    assert lines[0] == "## Multi-report trend"
    assert "- skipped: bad.md (unreadable)" in lines
    assert lines[3] == "- skipped: bad.md (unreadable)"
    header = next(line for line in lines if line.startswith("| metric |"))
    assert header == "| metric | z-older.md | a-previous.md | no-stamp.md | trend |"


def test_compare_multi_empty_dir_omits_table_with_note(tmp_path):
    """O10 edge: a directory with no readable reports omits the table, keeps a note."""
    module = _report_module()
    empty_dir = tmp_path / "empty-multi"
    empty_dir.mkdir()
    legs, skipped = module.collect_multi_report_entries(empty_dir)
    assert legs == [] and skipped == []
    lines = module.render_multi_report_trend(
        source_label=empty_dir.name, legs=legs, skipped=skipped
    )
    assert "- no parseable reports in empty-multi" in lines
    assert not any(line.startswith("| metric |") for line in lines)
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--compare-multi",
        str(empty_dir),
    )
    assert proc.returncode == 0, proc.stderr
    assert "- no parseable reports in empty-multi" in proc.stdout


def test_compare_multi_missing_dir_exits_1(tmp_path):
    """O10 edge: a missing --compare-multi directory fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1))
    seed_last_run(fixture)
    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--compare-multi",
        str(tmp_path / "missing-dir"),
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr
    assert "--compare-multi" in proc.stderr


def test_compare_multi_is_deterministic(tmp_path):
    """O10 determinism: same directory renders byte-identical trend runs."""
    multi_dir, current_dir, _previous_md = _multi_seed(tmp_path)
    first = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare-multi",
        str(multi_dir),
    )
    second = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare-multi",
        str(multi_dir),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_compare_multi_composes_with_compare_and_delta(tmp_path):
    """O10 composition: --compare-multi joins --compare/--delta sections in order."""
    multi_dir, current_dir, previous_md = _multi_seed(tmp_path)
    older_text = (multi_dir / "z-older.md").read_text(encoding="utf-8")
    older_md = tmp_path / "older-o10.md"
    older_md.write_text(older_text, encoding="utf-8")
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--compare",
        str(previous_md),
        "--delta",
        str(older_md),
        "--compare-multi",
        str(multi_dir),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Week-over-week comparison" in proc.stdout
    assert "## Three-way trend" in proc.stdout
    assert "## Multi-report trend" in proc.stdout
    assert proc.stdout.index("## Three-way trend") < proc.stdout.index(
        "## Multi-report trend"
    )


def test_parse_report_timestamp_and_multi_render_units():
    """O10 units: generated-at parsing plus net arrows and trend cells."""
    module = _report_module()
    assert module.parse_report_timestamp("# Engagement report\n") is None
    assert module.parse_report_timestamp(
        "## Header\n\n- generated-at: 2026-10-08T12:00:00+00:00\n"
    ) == module.parse_rfc3339(T2)
    assert module.parse_report_timestamp("## Header\n\n- generated-at: bogus\n") is None
    first = ("a.md", 1, 67, 4, 6, 2, 70.6)
    second = ("b.md", 2, 83, 5, 6, 1, 85.3)
    lines = module.render_multi_report_trend(
        source_label="multi", legs=[(first, None), (second, None)], skipped=[]
    )
    assert lines[2] == "- source: multi (2 reports)"
    assert "| findings | 2 | 1 | 2 → 1 (↑) |" in lines
    assert "| risk-weighted score | 70.6% | 85.3% | 70.6% → 85.3% (↑) |" in lines
    regressed = module.render_multi_report_trend(
        source_label="multi", legs=[(second, None), (first, None)], skipped=[]
    )
    assert "| runs | 2 | 1 | 2 → 1 (↓) |" in regressed
    assert "| findings | 1 | 2 | 1 → 2 (↓) |" in regressed


def test_json_composes_with_compare_multi(tmp_path):
    """O10 JSON: --json --compare-multi gains a multi_trend object; default omits it."""
    multi_dir, current_dir, _previous_md = _multi_seed(tmp_path)
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--json",
        "--compare-multi",
        str(multi_dir),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["multi_trend"]["source"] == "multi"
    assert data["multi_trend"]["reports"] == ["z-older.md", "a-previous.md"]
    assert data["multi_trend"]["skipped"] == []
    assert [entry["metric"] for entry in data["multi_trend"]["metrics"]] == [
        "runs",
        "pass rate",
        "coverage",
        "findings",
        "risk-weighted score",
    ]
    plain = run_script("--reports-dir", str(current_dir), "--since", T0, "--json")
    assert plain.returncode == 0, plain.stderr
    assert "multi_trend" not in json.loads(plain.stdout)


# KPI sidebar strip (P10) ------------------------------------------------------


def _sidebar_fixture(directory: Path, wrong=None) -> None:
    """Two runs of history; all-pass unless ``wrong`` flips observations."""
    directory.mkdir(parents=True, exist_ok=True)
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory, wrong=wrong)
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def test_sidebar_renders_kpi_strip_after_title(tmp_path):
    """P10 happy: --sidebar renders the KPI table right after the title."""
    fixture = tmp_path / "reports"
    _sidebar_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0, "--sidebar")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == "# Engagement report"
    assert lines[1] == ""
    assert lines[2] == "| Runs | Pass | Coverage | Risk | Gate |"
    assert lines[3] == "| --- | --- | --- | --- | --- |"
    assert lines[4] == "| 2 | 100% | 6/6 covered | 100.0% | n/a |"
    assert lines[5] == ""
    assert lines[6] == "## Header"


def test_sidebar_gate_cell_follows_threshold(tmp_path):
    """P10 gate: the Gate cell is PASS/FAIL with --threshold, n/a without."""
    fixture = tmp_path / "reports"
    _sidebar_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    passing = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--sidebar",
        "--threshold",
        "gate:80",
    )
    assert passing.returncode == 0, passing.stderr
    assert "| 2 | 83% | 5/6 covered | 85.3% | PASS |" in passing.stdout

    failing = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--sidebar",
        "--threshold",
        "gate:100",
    )
    assert failing.returncode == 3, failing.stdout
    assert "| 2 | 83% | 5/6 covered | 85.3% | FAIL |" in failing.stdout


def test_sidebar_is_deterministic(tmp_path):
    """P10 determinism: same inputs render a byte-identical strip."""
    fixture = tmp_path / "reports"
    _sidebar_fixture(fixture, wrong={"jailbreak-01": "not-detected"})
    first = run_script("--reports-dir", str(fixture), "--since", T0, "--sidebar")
    second = run_script("--reports-dir", str(fixture), "--since", T0, "--sidebar")
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout


def test_no_sidebar_flag_renders_no_strip(tmp_path):
    """P10 regression: without --sidebar the report has no KPI table."""
    fixture = tmp_path / "reports"
    _sidebar_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "| Runs | Pass | Coverage | Risk | Gate |" not in proc.stdout
    lines = proc.stdout.splitlines()
    assert lines[0] == "# Engagement report"
    assert lines[2] == "## Header"


def test_sidebar_delta_vs_compare_baseline(tmp_path):
    """P10 deltas: --sidebar --compare gains signed deltas with arrows."""
    previous_dir = tmp_path / "previous"
    _sidebar_fixture(previous_dir, wrong={"jailbreak-01": "not-detected"})
    previous_md = tmp_path / "previous.md"
    seeded = run_script(
        "--reports-dir",
        str(previous_dir),
        "--since",
        T0,
        "--out",
        str(previous_md),
    )
    assert seeded.returncode == 0, seeded.stderr

    current_dir = tmp_path / "current"
    _sidebar_fixture(current_dir)
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--sidebar",
        "--compare",
        str(previous_md),
    )
    assert proc.returncode == 0, proc.stderr
    assert "| 2 (+0 →) | 100% (+17pp ↑) | 6/6 covered (+1 ↑) | " in proc.stdout
    assert "100.0% (+14.7pp ↑)" in proc.stdout


def test_sidebar_json_object_only_when_flagged(tmp_path):
    """P10 composition: --json gains a sidebar object only with --sidebar."""
    fixture = tmp_path / "reports"
    _sidebar_fixture(fixture)

    plain = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert plain.returncode == 0, plain.stderr
    assert "sidebar" not in json.loads(plain.stdout)

    flagged = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--sidebar",
    )
    assert flagged.returncode == 0, flagged.stderr
    data = json.loads(flagged.stdout)
    assert data["sidebar"] == {
        "runs": 2,
        "pass_pct": 100,
        "coverage": "6/6 covered",
        "covered": 6,
        "total": 6,
        "risk_score": 100.0,
        "gate": None,
    }

    gated = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--sidebar",
        "--threshold",
        "gate:80",
    )
    assert gated.returncode == 0, gated.stderr
    gated_data = json.loads(gated.stdout)
    assert gated_data["sidebar"]["gate"] == "PASS"
    assert list(gated_data.keys())[-1] == "gate"


def test_sidebar_composes_with_export_html(tmp_path):
    """P10 composition: --export html carries the KPI strip; stdout stays md."""
    fixture = tmp_path / "reports"
    _sidebar_fixture(fixture)
    html = tmp_path / "report.html"

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--sidebar",
        "--export",
        "html",
        str(html),
    )
    assert proc.returncode == 0, proc.stderr
    assert "| Runs | Pass | Coverage | Risk | Gate |" in proc.stdout
    html_text = html.read_text(encoding="utf-8")
    assert "<th>Runs</th>" in html_text
    assert "6/6" in html_text and "verdict-covered" in html_text


def test_sidebar_render_and_data_units():
    """P10 units: table rows, delta cells, gate default, JSON deltas."""
    module = _report_module()
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 1, 0, 0.0, 1, "gappy"),
    ]
    pass_rows = [("a", "1/1 (100.0%)"), ("b", "0/1 (0.0%)")]
    lines = module.render_sidebar(runs=2, pass_rows=pass_rows, coverage=coverage)
    assert lines[0] == "| Runs | Pass | Coverage | Risk | Gate |"
    assert lines[2] == "| 2 | 50% | 1/2 covered | 50.0% | n/a |"
    gated = module.render_sidebar(
        runs=2,
        pass_rows=pass_rows,
        coverage=coverage,
        gate="FAIL",
        previous=(1, 100, 2, 2, 100.0),
    )
    assert "(+1 ↑)" in gated[2] and "(-50pp ↓)" in gated[2]
    assert gated[2].endswith("| FAIL |")
    data = module.sidebar_data(
        runs=2,
        pass_rows=pass_rows,
        coverage=coverage,
        gate="FAIL",
        previous=(1, 100, 2, 2, 100.0),
    )
    assert data["previous"]["runs"] == 1
    assert data["deltas"] == {
        "runs": 1,
        "pass_pct": -50,
        "covered": -1,
        "risk_score": -50.0,
    }
    assert (
        module.sidebar_data(runs=0, pass_rows=[], coverage=[])["coverage"]
        == "0/0 covered"
    )


# Per-class baseline floor gates (P12) -----------------------------------------


def test_baseline_floor_fails_gate_despite_threshold_pass(tmp_path):
    """P12 happy: a class below its floor fails the gate (exit 3)."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    assert proc.returncode == 3, proc.stdout
    assert proc.stdout.splitlines()[-1] == ("Gate: FAIL (floors: jailbreak 0.0% < 50%)")
    assert "gate: FAIL (floors: jailbreak 0.0% < 50%)" in proc.stderr


def test_baseline_floor_pass_keeps_classic_gate_line(tmp_path):
    """P12 pass: met floors keep the exact L10 PASS line and exit 0."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout.splitlines()[-1] == "Gate: PASS (pass-rate 100 >= 80)"


def test_baseline_repeatable_lists_only_failing_classes(tmp_path):
    """P12 repeatable: two floors fail only on the unmet class."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--baseline",
        "abuse=100",
        "--baseline",
        "jailbreak=50",
    )
    assert proc.returncode == 3, proc.stdout
    assert proc.stdout.splitlines()[-1] == ("Gate: FAIL (floors: jailbreak 0.0% < 50%)")
    assert "abuse" not in proc.stdout.splitlines()[-1]


def test_baseline_unknown_class_exits_2(tmp_path):
    """P12 usage: an unknown class exits 2 listing the valid classes."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--baseline",
        "nope=50",
    )
    assert proc.returncode == 2, proc.stdout
    assert "error: unknown --baseline class 'nope'" in proc.stderr
    for valid in ("abuse", "jailbreak", "prompt-injection"):
        assert valid in proc.stderr
    assert "Gate:" not in proc.stdout


def test_baseline_invalid_spec_exits_1(tmp_path):
    """P12 edge: malformed/out-of-range specs fail closed with error:."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture)
    for bad in (
        "bogus",
        "jailbreak:80",
        "jailbreak=",
        "=50",
        "jailbreak=abc",
        "jailbreak=101",
        "jailbreak=-1",
        "jailbreak=nan",
    ):
        proc = run_script(
            "--reports-dir", str(fixture), "--since", T0, "--baseline", bad
        )
        assert proc.returncode == 1, bad
        assert "error:" in proc.stderr, bad
        assert "Gate:" not in proc.stdout, bad


def test_baseline_is_deterministic(tmp_path):
    """P12 determinism: same floors render byte-identical runs."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})
    first = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    second = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    assert first.returncode == 3 and second.returncode == 3
    assert first.stdout == second.stdout


def test_baseline_json_gate_gains_failures(tmp_path):
    """P12 composition: --json gate gains failures only with --baseline."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    plain = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--threshold",
        "gate:80",
    )
    assert plain.returncode == 0, plain.stderr
    assert "failures" not in json.loads(plain.stdout)["gate"]

    failing = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    assert failing.returncode == 3, failing.stdout
    assert json.loads(failing.stdout)["gate"] == {
        "metric": "pass-rate",
        "value": 83,
        "gate": 80,
        "passed": False,
        "failures": [{"class": "jailbreak", "pass_pct": 0.0, "floor": 50}],
    }

    passing_dir = tmp_path / "passing"
    _threshold_fixture(passing_dir)
    passing = run_script(
        "--reports-dir",
        str(passing_dir),
        "--since",
        T0,
        "--json",
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    assert passing.returncode == 0, passing.stderr
    assert json.loads(passing.stdout)["gate"]["failures"] == []


def test_baseline_composes_with_summary_and_sidebar(tmp_path):
    """P12 composition: --summary honors floors; --sidebar shows the verdict."""
    fixture = tmp_path / "reports"
    _threshold_fixture(fixture, wrong={"jailbreak-01": "not-detected"})

    failing = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--summary",
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    assert failing.returncode == 3, failing.stdout
    assert failing.stdout.splitlines()[-1].startswith("Gate: FAIL (floors:")
    assert "jailbreak" in failing.stderr

    passing = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--summary",
        "--threshold",
        "gate:80",
        "--baseline",
        "abuse=100",
    )
    assert passing.returncode == 0, passing.stderr
    assert passing.stdout.splitlines()[-1] == "Gate: PASS (pass-rate 83 >= 80)"

    strip = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--sidebar",
        "--threshold",
        "gate:80",
        "--baseline",
        "jailbreak=50",
    )
    assert strip.returncode == 3, strip.stdout
    assert "| FAIL |" in strip.stdout


def test_baseline_parse_validate_and_evaluate_units():
    """P12 units: spec parsing, class validation, and floor verdicts."""
    module = _report_module()
    assert module.parse_baseline("jailbreak=80") == ("jailbreak", 80.0)
    assert module.parse_baseline(" abuse = 70.5 ") == ("abuse", 70.5)
    for bad in (
        "bogus",
        "jailbreak:80",
        "jailbreak=",
        "=50",
        "jailbreak=abc",
        "jailbreak=101",
        "jailbreak=-1",
    ):
        with pytest.raises(Exception, match="error:"):
            module.parse_baseline(bad)
    cases = [("a-01", "x", "", "abuse"), ("j-01", "y", "", "jailbreak")]
    module.validate_baseline_classes([("abuse", 50.0)], cases)
    with pytest.raises(module.BaselineUsageError, match="valid:"):
        module.validate_baseline_classes([("nope", 50.0)], cases)
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 1, 0, 0.0, 1, "gappy"),
    ]
    pass_rows = [("a", "1/1 (100.0%)"), ("b", "0/1 (0.0%)")]
    floored = module.evaluate_threshold(
        pass_rows, coverage, "gate:50", [("jailbreak", 50.0)]
    )
    assert floored["passed"] is False
    assert floored["line"] == "Gate: FAIL (floors: jailbreak 0.0% < 50%)"
    assert floored["failures"] == [{"class": "jailbreak", "pass_pct": 0.0, "floor": 50}]
    met = module.evaluate_threshold(pass_rows, coverage, "gate:50", [("abuse", 100.0)])
    assert met["passed"] is True
    assert met["line"] == "Gate: PASS (pass-rate 50 >= 50)"
    assert met["failures"] == []


# Inline run history across a directory (Q10) ----------------------------------


def test_spark_history_renders_table_with_current_last(tmp_path):
    """Q10 happy: --spark-history renders history rows plus the current row last."""
    multi_dir, current_dir, _previous_md = _multi_seed(tmp_path)
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--spark-history",
        str(multi_dir),
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Run history" in proc.stdout
    body = section_lines(proc.stdout, "## Run history")
    assert "- source: multi (2 reports)" in body
    assert "| when | runs | pass % | risk score |" in body
    rows = [line for line in body if line.startswith("| ") and "---" not in line]
    assert len(rows) == 1 + 3  # header + 2 history rows + current row
    assert rows[1] == "| z-older.md | 1 | 67% (→) | 70.6% (→) |"
    assert rows[2] == "| a-previous.md | 2 | 83% (↑) | 85.3% (↑) |"
    assert (
        rows[3] == "| engagement-2026-10-01_2026-10-10.md | 3 | 100% (↑) | 100.0% (↑) |"
    )


def test_spark_history_is_deterministic(tmp_path):
    """Q10 determinism: same directory renders byte-identical history runs."""
    multi_dir, current_dir, _previous_md = _multi_seed(tmp_path)
    first = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--spark-history",
        str(multi_dir),
    )
    second = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--spark-history",
        str(multi_dir),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_spark_history_missing_dir_exits_1(tmp_path):
    """Q10 edge: a missing --spark-history directory fails closed with error:."""
    _fixture_dir = tmp_path / "reports"
    _fixture_dir.mkdir()
    _exec_fixture(_fixture_dir)
    proc = run_script(
        "--reports-dir",
        str(_fixture_dir),
        "--since",
        T0,
        "--spark-history",
        str(tmp_path / "no-such-dir"),
    )
    assert proc.returncode == 1, proc.stdout
    assert "error:" in proc.stderr
    assert "--spark-history" in proc.stderr
    assert "## Run history" not in proc.stdout


def test_spark_history_skips_unreadable_with_note(tmp_path):
    """Q10 order: timestamp sort with unreadable files noted, not fatal."""
    multi_dir, current_dir, _previous_md = _multi_seed(tmp_path)
    (multi_dir / "bad.md").write_bytes(b"\xff\xfe\x00not-utf8")
    proc = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--spark-history",
        str(multi_dir),
    )
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Run history")
    assert "- skipped: bad.md (unreadable)" in body
    rows = [line for line in body if line.startswith("| ") and "---" not in line]
    assert len(rows) == 1 + 3


def test_no_spark_history_flag_renders_no_section(tmp_path):
    """Q10 regression: without --spark-history the report has no new section."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Run history" not in proc.stdout


def test_spark_history_rows_and_render_units(tmp_path):
    """Q10 units: row assembly (history + current last) plus glyph rendering."""
    module = _report_module()
    legs = [
        (("old.md", 1, 67, 4, 6, 2, 70.6), module.parse_rfc3339(T1)),
        (("new.md", 2, 83, 5, 6, 1, 85.3), module.parse_rfc3339(T2)),
    ]
    rows = module.spark_history_rows(legs, "current.md", (3, 100, 6, 6, 0, 100.0))
    assert rows == [
        ("old.md", 1, 67, 70.6),
        ("new.md", 2, 83, 85.3),
        ("current.md", 3, 100, 100.0),
    ]
    lines = module.render_spark_history(
        source_label="hist",
        legs=legs,
        skipped=["bad.md"],
        current_label="current.md",
        current_leg=(3, 100, 6, 6, 0, 100.0),
    )
    assert lines[0] == "## Run history"
    assert "- source: hist (2 reports)" in lines
    assert "- skipped: bad.md (unreadable)" in lines
    assert "| when | runs | pass % | risk score |" in lines
    assert "| old.md | 1 | 67% (→) | 70.6% (→) |" in lines
    assert "| new.md | 2 | 83% (↑) | 85.3% (↑) |" in lines
    assert "| current.md | 3 | 100% (↑) | 100.0% (↑) |" in lines


def test_json_composes_with_spark_history(tmp_path):
    """Q10 JSON: --json --spark-history gains a run_history object; default omits it."""
    multi_dir, current_dir, _previous_md = _multi_seed(tmp_path)
    plain = run_script("--reports-dir", str(current_dir), "--since", T0, "--json")
    assert plain.returncode == 0, plain.stderr
    assert "run_history" not in json.loads(plain.stdout)
    full = run_script(
        "--reports-dir",
        str(current_dir),
        "--since",
        T0,
        "--json",
        "--spark-history",
        str(multi_dir),
    )
    assert full.returncode == 0, full.stderr
    data = json.loads(full.stdout)
    history = data["run_history"]
    assert history["source"] == "multi"
    assert history["reports"][-1] == "engagement-2026-10-01_2026-10-10.md"
    assert [row["report"] for row in history["rows"]] == history["reports"]
    assert history["rows"][0] == {
        "report": "z-older.md",
        "runs": 1,
        "pass_pct": 67,
        "risk_score": 70.6,
    }


# Grant coverage matrix from a RoE artifact (Q11) -------------------------------


def _grant_fixture(directory: Path) -> None:
    """One all-pass run of history for grant-matrix tests."""
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory)
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def test_grant_matrix_renders_covered_table(tmp_path):
    """Q11 happy: --grant-matrix renders yes/no rows with the artifact id header."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _grant_fixture(fixture)
    artifact = tmp_path / "roe.json"
    artifact.write_text(
        json.dumps({"artifact_id": "roe-1", "covers": ["github", "Slack"]}),
        encoding="utf-8",
    )
    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--grant-matrix", str(artifact)
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Grant coverage: roe-1" in proc.stdout
    body = section_lines(proc.stdout, "## Grant coverage: roe-1")
    assert "- source: roe.json" in body
    assert "| tracker | Covered |" in body
    rows = [line for line in body if line.startswith("| ") and "---" not in line]
    assert len(rows) == 1 + 9  # header + one row per known tracker
    assert "| github | yes |" in body
    assert "| slack | yes |" in body
    assert "| linear | no |" in body
    assert "| webhook | no |" in body


def test_grant_matrix_missing_covers_covers_all(tmp_path):
    """Q11 edge: a missing/empty covers key marks every tracker covered."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _grant_fixture(fixture)
    for name, document, heading in (
        (
            "missing.json",
            {"artifact_id": "roe-missing"},
            "## Grant coverage: roe-missing",
        ),
        (
            "empty.json",
            {"id": "roe-empty", "covers": []},
            "## Grant coverage: roe-empty",
        ),
    ):
        artifact = tmp_path / name
        artifact.write_text(json.dumps(document), encoding="utf-8")
        proc = run_script(
            "--reports-dir",
            str(fixture),
            "--since",
            T0,
            "--grant-matrix",
            str(artifact),
        )
        assert proc.returncode == 0, proc.stderr
        assert heading in proc.stdout
        body = section_lines(proc.stdout, heading)
        assert all(
            f"| {tracker} | yes |" in "\n".join(body)
            for tracker in (
                "github",
                "linear",
                "slack",
                "teams",
                "pagerduty",
                "opsgenie",
                "jira",
                "graph",
                "webhook",
            )
        )


def test_grant_matrix_invalid_artifact_is_fail_soft(tmp_path):
    """Q11 edge: unreadable/invalid artifacts omit the table with a note, exit 0."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _grant_fixture(fixture)
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    array_json = tmp_path / "array.json"
    array_json.write_text("[1, 2]", encoding="utf-8")
    for artifact in (tmp_path / "missing.json", bad_json, array_json):
        proc = run_script(
            "--reports-dir",
            str(fixture),
            "--since",
            T0,
            "--grant-matrix",
            str(artifact),
        )
        assert proc.returncode == 0, proc.stderr
        assert "## Grant coverage" not in proc.stdout
        assert "grant matrix skipped" in proc.stdout


def test_grant_matrix_is_deterministic(tmp_path):
    """Q11 determinism: same artifact renders byte-identical matrix runs."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _grant_fixture(fixture)
    artifact = tmp_path / "roe.json"
    artifact.write_text(
        json.dumps({"artifact_id": "roe-1", "covers": ["github"]}),
        encoding="utf-8",
    )
    first = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--grant-matrix", str(artifact)
    )
    second = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--grant-matrix", str(artifact)
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_no_grant_matrix_flag_renders_no_section(tmp_path):
    """Q11 regression: without --grant-matrix the report has no new section."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _grant_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0)
    assert proc.returncode == 0, proc.stderr
    assert "## Grant coverage" not in proc.stdout
    assert "grant matrix skipped" not in proc.stdout


def test_grant_matrix_load_and_render_units(tmp_path):
    """Q11 units: covers parsing (case-insensitive, all-on-empty) plus table rows."""
    module = _report_module()
    artifact = tmp_path / "roe.json"
    artifact.write_text(
        json.dumps({"id": "roe-x", "covers": [" GitHub ", 7, "", "SLACK"]}),
        encoding="utf-8",
    )
    artifact_id, covered, error = module.load_grant_matrix(artifact)
    assert (artifact_id, covered, error) == ("roe-x", {"github", "slack"}, None)
    _, _, error = module.load_grant_matrix(tmp_path / "nope.json")
    assert error is not None and "cannot read" in error
    lines = module.render_grant_matrix(
        artifact_id="roe-x", artifact_label="roe.json", covered={"github"}
    )
    assert lines[0] == "## Grant coverage: roe-x"
    assert "| github | yes |" in lines
    assert "| linear | no |" in lines
    rows = [line for line in lines if line.startswith("| ") and "---" not in line]
    assert len(rows) == 1 + 9
    all_covered = module.render_grant_matrix(
        artifact_id="roe-x", artifact_label="roe.json", covered=None
    )
    assert all(
        f"| {tracker} | yes |" in all_covered for tracker in module.GRANT_TRACKERS
    )


def test_json_composes_with_grant_matrix(tmp_path):
    """Q11 JSON: --json --grant-matrix gains a grant_matrix object; default omits it."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _grant_fixture(fixture)
    artifact = tmp_path / "roe.json"
    artifact.write_text(
        json.dumps({"artifact_id": "roe-1", "covers": ["github"]}),
        encoding="utf-8",
    )
    plain = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    assert plain.returncode == 0, plain.stderr
    assert "grant_matrix" not in json.loads(plain.stdout)
    full = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--grant-matrix",
        str(artifact),
    )
    assert full.returncode == 0, full.stderr
    matrix = json.loads(full.stdout)["grant_matrix"]
    assert matrix["artifact_id"] == "roe-1"
    by_tracker = {row["tracker"]: row["covered"] for row in matrix["rows"]}
    assert by_tracker["github"] is True
    assert by_tracker["linear"] is False
    assert len(matrix["rows"]) == 9


# Worst classes by weighted gaps in the executive summary (Q12) -----------------


def _worst_fixture(directory: Path) -> None:
    """Two runs of history with two failing classes (abuse + jailbreak)."""
    seed_transitions(directory, cycle(T1) + cycle(T2))
    seed_last_run(directory, wrong={"jailbreak-01": "not-detected", "abuse-01": "nope"})
    seed_findings(directory, [finding_row("abuse-01", "resp-a", T1)])


def test_worst_appends_top_n_bullet(tmp_path):
    """Q12 happy: --exec-summary --worst 1 names only the top class."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _worst_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--exec-summary", "--worst", "1"
    )
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Executive summary")
    worst = [line for line in body if line.startswith("- Worst classes by weighted")]
    assert len(worst) == 1
    assert worst[0] == "- Worst classes by weighted gaps: abuse (10 weighted points)"
    assert "jailbreak" not in worst[0]


def test_worst_bare_flag_defaults_to_three(tmp_path):
    """Q12 default: a bare --worst flag lists up to three classes."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _worst_fixture(fixture)

    proc = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--exec-summary", "--worst"
    )
    assert proc.returncode == 0, proc.stderr
    body = section_lines(proc.stdout, "## Executive summary")
    worst = [line for line in body if line.startswith("- Worst classes by weighted")]
    assert len(worst) == 1
    assert "abuse (10 weighted points)" in worst[0]
    assert "jailbreak (10 weighted points)" in worst[0]


def test_worst_zero_omits_bullet(tmp_path):
    """Q12 edge: --worst 0 omits the bullet (same section as without the flag)."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _worst_fixture(fixture)

    omitted = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--exec-summary", "--worst", "0"
    )
    plain = run_script("--reports-dir", str(fixture), "--since", T0, "--exec-summary")
    assert omitted.returncode == 0, omitted.stderr
    assert plain.returncode == 0, plain.stderr
    assert "Worst classes by weighted gaps" not in omitted.stdout
    assert "Worst classes by weighted gaps" not in plain.stdout
    assert omitted.stdout == plain.stdout


def test_worst_without_exec_summary_renders_no_section(tmp_path):
    """Q12 regression: --worst alone adds no executive summary section."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _worst_fixture(fixture)

    proc = run_script("--reports-dir", str(fixture), "--since", T0, "--worst", "2")
    assert proc.returncode == 0, proc.stderr
    assert "## Executive summary" not in proc.stdout
    assert "Worst classes by weighted gaps" not in proc.stdout


def test_worst_negative_exits_1(tmp_path):
    """Q12 edge: a negative --worst value fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _worst_fixture(fixture)

    proc = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--exec-summary",
        "--worst",
        "-1",
    )
    assert proc.returncode == 1, proc.stdout
    assert "error:" in proc.stderr


def test_worst_is_deterministic(tmp_path):
    """Q12 determinism: same N renders byte-identical summary runs."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _worst_fixture(fixture)

    first = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--exec-summary", "--worst", "2"
    )
    second = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--exec-summary", "--worst", "2"
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_worst_classes_and_bullet_units():
    """Q12 units: ranking reuse, N truncation, zero/empty handling, bullet text."""
    module = _report_module()
    coverage = [
        ("abuse", 1, 1, 1, 100.0, 0, "covered"),
        ("jailbreak", 1, 1, 0, 0.0, 1, "gappy"),
        ("auth-bypass", 1, 1, 0, 0.0, 0, "gappy"),
    ]
    assert module.worst_classes(coverage, 2) == [("auth-bypass", 10), ("jailbreak", 10)]
    assert module.worst_classes(coverage, 1) == [("auth-bypass", 10)]
    assert module.worst_classes(coverage, 0) == []
    assert module.worst_classes(coverage, -3) == []
    assert (
        module.render_worst_bullet(coverage, 2) == "- Worst classes by weighted gaps: "
        "auth-bypass (10 weighted points), jailbreak (10 weighted points)"
    )
    assert (
        module.render_worst_bullet([("abuse", 1, 1, 1, 100.0, 0, "covered")], 3)
        == "- Worst classes by weighted gaps: none (all classes fully covered)"
    )
    lines = module.render_exec_summary(
        runs=2,
        pass_rows=[("a-01", "1/1 (100.0%)"), ("b-01", "0/1 (0.0%)")],
        coverage=coverage,
        new_count=1,
        rescinded_count=0,
        previous_pass=50,
        worst_n=1,
    )
    assert (
        lines[-1]
        == "- Worst classes by weighted gaps: auth-bypass (10 weighted points)"
    )
    default = module.render_exec_summary(
        runs=2,
        pass_rows=[("a-01", "1/1 (100.0%)"), ("b-01", "0/1 (0.0%)")],
        coverage=coverage,
        new_count=1,
        rescinded_count=0,
        previous_pass=50,
    )
    assert all("Worst classes" not in line for line in default)


def test_json_worst_classes_only_with_exec_summary(tmp_path):
    """Q12 JSON: worst_classes appears only for --json --exec-summary --worst."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _worst_fixture(fixture)

    plain = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--json", "--worst", "2"
    )
    assert plain.returncode == 0, plain.stderr
    assert "exec_summary" not in json.loads(plain.stdout)
    no_worst = run_script(
        "--reports-dir", str(fixture), "--since", T0, "--json", "--exec-summary"
    )
    assert no_worst.returncode == 0, no_worst.stderr
    assert "worst_classes" not in json.loads(no_worst.stdout)["exec_summary"]
    full = run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--json",
        "--exec-summary",
        "--worst",
        "2",
    )
    assert full.returncode == 0, full.stderr
    worst = json.loads(full.stdout)["exec_summary"]["worst_classes"]
    assert worst == [
        {"class": "abuse", "weighted_points": 10},
        {"class": "jailbreak", "weighted_points": 10},
    ]


# --------------------------------------------------------------------------
# --export-jsonl (batch-18 R4): coverage matrix as JSON Lines
# --------------------------------------------------------------------------

JSONL_KEYS = [
    "schema",
    "class",
    "cases",
    "runs",
    "wins",
    "pass_pct",
    "gaps",
    "verdict",
    "tier",
    "weight",
    "earned",
    "possible",
]


def _jsonl_run(fixture: Path, out: Path, *extra: str):
    return run_script(
        "--reports-dir",
        str(fixture),
        "--since",
        T0,
        "--export-jsonl",
        str(out),
        *extra,
    )


def test_export_jsonl_schema_key_order_and_one_line_per_class(tmp_path):
    """R4 happy: one compact object per class, fixed keys, single trailing LF."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out = tmp_path / "coverage.jsonl"

    proc = _jsonl_run(fixture, out)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("# Engagement report")
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n") and not text.endswith("\n\n")
    lines = text.split("\n")[:-1]
    assert len(lines) == 6
    rows = [json.loads(line) for line in lines]
    assert [row["class"] for row in rows] == [
        "abuse",
        "auth-bypass",
        "cost-abuse",
        "jailbreak",
        "malformed-input",
        "prompt-injection",
    ]
    for line, row in zip(lines, rows, strict=True):
        assert list(row) == JSONL_KEYS
        assert row["schema"] == "coverage/1"
        assert ", " not in line and ": " not in line  # compact separators
        assert line.isascii()
    abuse = rows[0]
    assert abuse["verdict"] == "covered"
    assert abuse["pass_pct"] == 100.0 and isinstance(abuse["pass_pct"], float)
    assert (abuse["tier"], abuse["weight"]) == ("high", 5)
    assert (abuse["earned"], abuse["possible"]) == (5, 5)
    assert rows[1]["tier"] == "critical" and rows[1]["weight"] == 10


def test_export_jsonl_is_deterministic(tmp_path):
    """R4 determinism: two runs export byte-identical files."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"

    assert _jsonl_run(fixture, first).returncode == 0
    assert _jsonl_run(fixture, second).returncode == 0
    assert first.read_bytes() == second.read_bytes()


def test_export_jsonl_matches_csv_export(tmp_path):
    """R4: shared numbers agree with the CSV export of the same window."""
    import csv as csv_module

    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"abuse-01": "x"})
    out = tmp_path / "coverage.jsonl"
    csv_out = tmp_path / "coverage.csv"

    proc = _jsonl_run(fixture, out, "--export-csv", str(csv_out))
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    with csv_out.open(newline="", encoding="utf-8") as handle:
        table = list(csv_module.DictReader(handle))[:-1]
    assert len(rows) == len(table)
    for row, record in zip(rows, table, strict=True):
        assert row["class"] == record["class"]
        assert f"{row['pass_pct']:.1f}" == record["pct"]
        assert row["verdict"] == record["verdict"]
        assert f"{row['earned']}/{row['possible']}" == record["weighted"]
    assert rows[0]["verdict"] == "gappy"
    assert rows[0]["wins"] == 0 and rows[0]["pass_pct"] == 0.0


def test_export_jsonl_unwritable_output_exits_1(tmp_path):
    """R4 edge: an unwritable --export-jsonl path fails closed with error:."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)

    proc = _jsonl_run(fixture, fixture)
    assert proc.returncode == 1
    assert "error: cannot write --export-jsonl export" in proc.stderr
    missing = _jsonl_run(fixture, tmp_path / "no-such-dir" / "out.jsonl")
    assert missing.returncode == 1
    assert "error: cannot write --export-jsonl export" in missing.stderr


def test_export_jsonl_unset_leaves_report_and_json_unchanged(tmp_path):
    """R4: stdout markdown and --json payload do not depend on the new flag."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    _exec_fixture(fixture)
    out = tmp_path / "coverage.jsonl"

    plain = run_script("--reports-dir", str(fixture), "--since", T0)
    flagged = _jsonl_run(fixture, out)
    assert plain.returncode == 0 and flagged.returncode == 0
    assert plain.stdout == flagged.stdout
    plain_json = run_script("--reports-dir", str(fixture), "--since", T0, "--json")
    flagged_json = _jsonl_run(fixture, out, "--json")
    assert plain_json.returncode == 0 and flagged_json.returncode == 0
    assert plain_json.stdout == flagged_json.stdout
    assert "coverage/1" not in plain.stdout and "coverage/1" not in plain_json.stdout


def test_export_jsonl_composes_with_csv_and_gate(tmp_path):
    """R4 composition: file written even when the gate then fails with exit 3."""
    fixture = tmp_path / "reports"
    fixture.mkdir()
    seed_transitions(fixture, cycle(T1) + cycle(T2))
    seed_last_run(fixture, wrong={"abuse-01": "x"})
    out = tmp_path / "coverage.jsonl"

    proc = _jsonl_run(fixture, out, "--threshold", "gate:100")
    assert proc.returncode == 3
    assert len(out.read_text(encoding="utf-8").splitlines()) == 6


def test_coverage_jsonl_pure_functions(tmp_path, monkeypatch):
    """R4 contract: coverage_jsonl_rows/render_jsonl/write_jsonl_report units."""
    from datetime import UTC, datetime

    module = _report_module()
    fixture = tmp_path / "reports"
    fixture.mkdir()
    stamp = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    evil = "we\nird\u00e9\x1b[0m"
    window = {
        "runs": 1,
        "cases": [
            ("abuse-01", "detected", "", "abuse"),
            ("abuse-02", "detected", "", "abuse"),
            ("abuse-03", "detected", "", "abuse"),
            ("z-01", "detected", "", evil),
        ],
        "observations": {
            "abuse-01": {"observed_detection": "detected"},
            "abuse-02": {"observed_detection": "missed"},
            "abuse-03": {"observed_detection": "missed"},
        },
        "gaps": [],
        "pass_rows": [],
        "window_start": stamp,
        "window_end": stamp,
    }
    monkeypatch.setattr(module, "_collect_window", lambda *_args: window)

    rows = module.coverage_jsonl_rows(fixture, T0)
    assert [row["class"] for row in rows] == [
        "abuse",
        "auth-bypass",
        "cost-abuse",
        "jailbreak",
        "malformed-input",
        "prompt-injection",
        evil,
    ]
    assert all(list(row) == JSONL_KEYS for row in rows)
    abuse = rows[0]
    assert abuse["pass_pct"] == 33.3  # rounded to one decimal
    assert abuse["verdict"] == "gappy"
    assert rows[1]["verdict"] == "unexercised"
    assert (rows[-1]["tier"], rows[-1]["weight"]) == ("low", 1)

    text = module.render_jsonl(rows)
    lines = text.split("\n")
    assert lines[-1] == "" and len(lines) == len(rows) + 1
    assert text.isascii()  # control/non-ascii class names are escaped
    assert [json.loads(line) for line in lines[:-1]] == rows
    assert module.render_jsonl([]) == ""

    target = tmp_path / "out.jsonl"
    assert module.write_jsonl_report(rows, target) == 0
    assert target.read_text(encoding="utf-8") == text
    assert module.write_jsonl_report(rows, tmp_path) == 1


def test_export_jsonl_write_is_atomic_mode_0644_and_leaves_no_temp(tmp_path):
    """FX-2: temp file + replace; replaces an existing file; mode 0644."""
    module = _report_module()
    out = tmp_path / "coverage.jsonl"
    out.write_text("OLD\n", encoding="utf-8")
    out.chmod(0o600)
    rows = [{"schema": "coverage/1", "class": "abuse"}]

    assert module.write_jsonl_report(rows, out) == 0
    assert out.read_text(encoding="utf-8") == module.render_jsonl(rows)
    assert (out.stat().st_mode & 0o777) == 0o644
    assert [p.name for p in tmp_path.iterdir()] == ["coverage.jsonl"]


def test_export_jsonl_failed_replace_keeps_old_file_and_cleans_temp(
    tmp_path, monkeypatch, capsys
):
    module = _report_module()
    out = tmp_path / "coverage.jsonl"
    out.write_text("OLD\n", encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(module.os, "replace", boom)
    assert module.write_jsonl_report([{"class": "x"}], out) == 1
    assert "error: cannot write --export-jsonl export" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "OLD\n"
    assert [p.name for p in tmp_path.iterdir()] == ["coverage.jsonl"]


def test_export_jsonl_refuses_symlink_leaf(tmp_path, capsys):
    module = _report_module()
    real = tmp_path / "real.txt"
    real.write_text("KEEP", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)

    assert module.write_jsonl_report([{"class": "x"}], link) == 1
    assert "error: cannot write --export-jsonl export" in capsys.readouterr().err
    assert real.read_text(encoding="utf-8") == "KEEP"
    assert link.is_symlink()


def test_export_jsonl_hostile_class_round_trips_as_one_line():
    """FX-2: unknown class names stay JSON-escaped data on a single line."""
    module = _report_module()
    evil = 'evil\n\x1b[31mclass\u2028\u202e"}{'
    text = module.render_jsonl(
        [{"schema": "coverage/1", "class": evil}, {"class": "abuse"}]
    )
    lines = text.split("\n")
    assert lines[-1] == "" and len(lines) == 3
    assert text.isascii() and "\x1b" not in text
    assert json.loads(lines[0])["class"] == evil

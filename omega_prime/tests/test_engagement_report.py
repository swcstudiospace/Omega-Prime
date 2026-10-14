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
    assert rows[0] == "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered |"
    assert all(line.endswith("| covered |") for line in rows)


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
    assert "| jailbreak | 1 | 1 | 0/1 (0.0%) | 1 | gappy |" in text
    assert "| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered |" in text


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
    assert all(line.endswith("| gappy |") for line in rows)


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
    assert len(rows) == 2 + 6 * 5  # header + separator + 5 metrics × 6 classes
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
    for line, entry in zip(matrix_rows, data["coverage"]):
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

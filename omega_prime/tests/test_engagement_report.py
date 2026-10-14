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

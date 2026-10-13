"""Simulation tests: smoke run, detection validation, drift to DEGRADED."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sidecar import simulation
from sidecar.simulation import (
    CASE_CLASSES,
    FakeGateway,
    last_run_path,
    load_suite,
    main,
    run_suite,
    validate_suite,
)

SHIPPED_DIR = Path(__file__).resolve().parents[2] / "sidecar" / "cases"
SIDECAR_DIR = Path(__file__).resolve().parents[2] / "sidecar"

REPORTS_ENV = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"


def _isolated_reports(monkeypatch, tmp_path: Path) -> Path:
    reports = tmp_path / "reports"
    monkeypatch.setenv(REPORTS_ENV, str(reports))
    return reports


def test_run_smoke_exits_zero_with_per_class_counts(
    monkeypatch, tmp_path, capsys
):
    reports = _isolated_reports(monkeypatch, tmp_path)
    code = main(
        ["run", "--suite", "smoke", "--reports-dir", str(reports)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "6 cases" in out
    for klass in CASE_CLASSES:
        assert klass in out
    record = json.loads(last_run_path(reports).read_text(encoding="utf-8"))
    assert len(record["observations"]) == 6


def test_validate_passes_after_run(monkeypatch, tmp_path, capsys):
    reports = _isolated_reports(monkeypatch, tmp_path)
    assert main(["run", "--suite", "smoke", "--reports-dir", str(reports)]) == 0
    capsys.readouterr()
    code = main(
        ["validate", "--suite", "smoke", "--reports-dir", str(reports)]
    )
    assert code == 0
    assert "all 6 cases" in capsys.readouterr().out


def test_mismatched_detection_fails_naming_case_id(monkeypatch, tmp_path):
    reports = _isolated_reports(monkeypatch, tmp_path)
    cases, errors = load_suite(SHIPPED_DIR)
    assert errors == []
    run_suite(
        cases,
        gateway=FakeGateway(overrides={"abuse-01": "no-detection"}),
        reports=reports,
    )
    ok, failures = validate_suite(SHIPPED_DIR, reports=reports)
    assert not ok
    assert any("abuse-01" in failure for failure in failures)
    assert any("no-detection" in failure for failure in failures)


def test_deleted_expectation_fails_validation_by_name(monkeypatch, tmp_path):
    reports = _isolated_reports(monkeypatch, tmp_path)
    target = tmp_path / "cases"
    shutil.copytree(SHIPPED_DIR, target)
    victim = target / "abuse-01.yaml"
    lines = [
        line
        for line in victim.read_text(encoding="utf-8").splitlines(keepends=True)
        if "detection:" not in line
    ]
    victim.write_text("".join(lines), encoding="utf-8")
    ok, failures = validate_suite(target, reports=reports)
    assert not ok
    assert any("abuse-01" in failure for failure in failures)


def test_missing_run_record_fails_naming_remedy(monkeypatch, tmp_path):
    _isolated_reports(monkeypatch, tmp_path)
    ok, failures = validate_suite(SHIPPED_DIR, reports=tmp_path / "empty")
    assert not ok
    assert any("run first" in failure for failure in failures)


def test_unknown_suite_exits_two(monkeypatch, tmp_path, capsys):
    _isolated_reports(monkeypatch, tmp_path)
    assert main(["run", "--suite", "nope"]) == 2
    assert main(["validate", "--suite", "nope"]) == 2


def test_residual_rationale_excuses_exactly_its_own_case(
    monkeypatch, tmp_path
):
    reports = _isolated_reports(monkeypatch, tmp_path)
    target = tmp_path / "cases"
    shutil.copytree(SHIPPED_DIR, target)
    victim = target / "abuse-01.yaml"
    text = victim.read_text(encoding="utf-8")
    text += '  residual_rationale: "accepted residual: detector gap tracked in T-79-12"\n'
    victim.write_text(text, encoding="utf-8")
    cases, errors = load_suite(target)
    assert errors == []
    run_suite(
        cases,
        gateway=FakeGateway(overrides={"abuse-01": "no-detection"}),
        reports=reports,
    )
    ok, failures = validate_suite(target, reports=reports)
    assert ok, failures
    # A rationale on one case does not excuse another.
    run_suite(
        cases,
        gateway=FakeGateway(overrides={"cost-abuse-01": "no-detection"}),
        reports=reports,
    )
    ok, failures = validate_suite(target, reports=reports)
    assert not ok
    assert any("cost-abuse-01" in failure for failure in failures)


def test_state_machine_driven_idle_running_idle(monkeypatch, tmp_path):
    reports = _isolated_reports(monkeypatch, tmp_path)
    cases, _ = load_suite(SHIPPED_DIR)
    run_suite(cases, reports=reports)
    rows = [
        json.loads(line)
        for line in (reports / "transitions.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    edges = [(row["from"], row["to"]) for row in rows]
    assert ("IDLE", "RUNNING") in edges
    assert ("RUNNING", "IDLE") in edges


def test_simulation_opens_no_sockets_by_construction():
    for module in ("simulation.py", "findings.py"):
        source = (SIDECAR_DIR / module).read_text(encoding="utf-8")
        assert "import socket" not in source, module
        assert "urllib" not in source, module
        assert "http.client" not in source, module
        assert "requests" not in source, module


def test_simulation_module_surface():
    assert set(simulation.CASE_CLASSES) == set(CASE_CLASSES)
    assert len(simulation.CASE_CLASSES) == 6

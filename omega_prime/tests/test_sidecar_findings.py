"""Findings tests: sha256 dedupe, work-item export, drift to DEGRADED."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sidecar import findings
from sidecar.findings import (
    check_drift,
    dedupe_hash,
    load_findings,
    main,
    normalize_response,
    record_finding,
)
from sidecar.simulation import load_suite, run_suite, validate_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_DIR = REPO_ROOT / "sidecar" / "cases"
SIDECAR_DIR = REPO_ROOT / "sidecar"

REPORTS_ENV = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"


def _finding(**overrides):
    params = {
        "case_id": "abuse-01",
        "klass": "abuse",
        "severity": "medium",
        "observed_detection": "abuse-detected",
        "verdict": "pass",
        "response": "loopback-seam:abuse-01:abuse-detected",
    }
    params.update(overrides)
    return params


def test_replay_dedupes_to_one_row_with_count_two(tmp_path):
    first = record_finding(directory=tmp_path, **_finding())
    second = record_finding(directory=tmp_path, **_finding())
    rows = load_findings(tmp_path)
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 2
    assert rows[0]["ts"] == first["ts"]  # history preserved, not rewritten
    assert second["dedupe_hash"] == first["dedupe_hash"]
    assert len(second["dedupe_hash"]) == 64  # sha256 hex


def test_distinct_response_creates_second_row(tmp_path):
    record_finding(directory=tmp_path, **_finding())
    record_finding(
        directory=tmp_path, **_finding(response="loopback-seam:abuse-01:other")
    )
    rows = load_findings(tmp_path)
    assert len(rows) == 2
    assert rows[0]["dedupe_hash"] != rows[1]["dedupe_hash"]


def test_whitespace_only_difference_dedupes(tmp_path):
    record_finding(directory=tmp_path, **_finding(response="foo  bar"))
    record_finding(directory=tmp_path, **_finding(response="  foo bar\n"))
    assert len(load_findings(tmp_path)) == 1
    assert normalize_response("  a\tb\nc ") == "a b c"


def test_export_writes_complete_file_based_work_items(tmp_path, capsys):
    row = record_finding(directory=tmp_path, **_finding())
    record_finding(directory=tmp_path, **_finding(case_id="cost-abuse-01"))
    assert main(["export", "--reports-dir", str(tmp_path)]) == 0
    assert "exported 2 work items" in capsys.readouterr().out
    exported = json.loads((tmp_path / "work-items.json").read_text(encoding="utf-8"))
    assert len(exported) == 2
    first = exported[0]
    assert first["case_id"] == "abuse-01"
    assert first["severity"] == "medium"
    assert first["dedupe_hash"] == row["dedupe_hash"]
    assert first["occurrence_count"] == 1
    assert "abuse-01" in first["title"]
    for item in exported:
        assert set(item) >= {
            "title",
            "severity",
            "case_id",
            "dedupe_hash",
            "occurrence_count",
            "observed_detection",
            "verdict",
        }


def test_export_touches_no_network_by_construction():
    source = (SIDECAR_DIR / "findings.py").read_text(encoding="utf-8")
    assert "import socket" not in source
    assert "urllib" not in source
    assert "Linear" not in source and "linear" not in source


def test_drift_clean_tree_reports_ok():
    ok, detail = check_drift()
    assert ok, detail
    assert "no drift" in detail


def _mini_root(tmp_path: Path) -> Path:
    """Minimal fake repo root carrying VERSION, contract, and adapter."""
    root = tmp_path / "root"
    (root / "sidecar" / "contract").mkdir(parents=True)
    (root / "omega_prime" / "tools").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "sidecar" / "VERSION", root / "sidecar" / "VERSION")
    shutil.copy(
        REPO_ROOT / "sidecar" / "contract" / "bot-interface.json",
        root / "sidecar" / "contract" / "bot-interface.json",
    )
    shutil.copy(
        REPO_ROOT / "omega_prime" / "tools" / "redteam.py",
        root / "omega_prime" / "tools" / "redteam.py",
    )
    return root


def test_drift_version_mismatch_names_the_pair(tmp_path):
    root = _mini_root(tmp_path)
    (root / "sidecar" / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    ok, detail = check_drift(root)
    assert not ok
    assert "sidecar/VERSION" in detail
    assert "bot-interface.json" in detail
    assert "9.9.9" in detail


def test_drift_tool_surface_mismatch_names_missing_and_extra(tmp_path):
    root = _mini_root(tmp_path)
    adapter = root / "omega_prime" / "tools" / "redteam.py"
    text = adapter.read_text(encoding="utf-8")
    text = text.replace('"tempest_hat_dispatch",', '"tempest_rogue_tool",')
    adapter.write_text(text, encoding="utf-8")
    ok, detail = check_drift(root)
    assert not ok
    assert "tempest_hat_dispatch" in detail  # missing
    assert "tempest_rogue_tool" in detail  # extra


def test_validate_reports_degraded_never_silent_pass(monkeypatch, tmp_path):
    monkeypatch.setenv(REPORTS_ENV, str(tmp_path / "reports"))
    root = _mini_root(tmp_path)
    (root / "sidecar" / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    cases, errors = load_suite(SHIPPED_DIR)
    assert errors == []
    run_suite(cases, reports=tmp_path / "reports")
    ok, failures = validate_suite(
        SHIPPED_DIR, reports=tmp_path / "reports", repo_root=root
    )
    assert not ok
    assert any("DEGRADED" in failure for failure in failures)
    assert any("9.9.9" in failure for failure in failures)
    rows = [
        json.loads(line)
        for line in (tmp_path / "reports" / "transitions.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert ("IDLE", "DEGRADED") in [(r["from"], r["to"]) for r in rows]


def test_findings_module_surface():
    assert callable(findings.record_finding)
    assert callable(findings.export_work_items)
    assert callable(findings.check_drift)
    assert len(dedupe_hash("abuse-01", "resp")) == 64

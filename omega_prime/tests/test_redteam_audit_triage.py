# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic tests for the redteam npm-audit triage script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "redteam-audit-triage.py"


def _load():
    spec = importlib.util.spec_from_file_location("redteam_audit_triage", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TRIAGE = _load()


def _write_audit(tmp_path: Path, doc: object) -> Path:
    target = tmp_path / "audit.json"
    target.write_text(json.dumps(doc), encoding="utf-8")
    return target


def test_clean_audit_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(
        "AUDIT_JSON", str(_write_audit(tmp_path, {"vulnerabilities": {}}))
    )
    assert _TRIAGE.main() == 0
    assert "redteam-node-audit OK" in capsys.readouterr().out


def test_error_payload_fails_naming_error(tmp_path, monkeypatch, capsys):
    doc = {"error": {"code": "ENOAUDIT", "summary": "no audit available"}}
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 1
    err = capsys.readouterr().err
    assert "ENOAUDIT" in err


def test_missing_keys_fail_closed(tmp_path, monkeypatch, capsys):
    """Error JSON without vulnerabilities/advisories keys never passes."""
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, {"metadata": {}})))
    assert _TRIAGE.main() == 1
    assert "neither 'vulnerabilities' nor 'advisories'" in capsys.readouterr().err


def test_non_object_output_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, ["oops"])))
    assert _TRIAGE.main() == 1
    assert "not a JSON object" in capsys.readouterr().err


def test_unparseable_json_fails(tmp_path, monkeypatch, capsys):
    target = tmp_path / "audit.json"
    target.write_text("{nope", encoding="utf-8")
    monkeypatch.setenv("AUDIT_JSON", str(target))
    assert _TRIAGE.main() == 1
    assert "cannot parse" in capsys.readouterr().err


def test_missing_env_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AUDIT_JSON", raising=False)
    assert _TRIAGE.main() == 1
    assert "AUDIT_JSON is not set" in capsys.readouterr().err


def test_untriaged_high_fails_and_low_passes(tmp_path, monkeypatch, capsys):
    doc = {
        "vulnerabilities": {
            "left-pad-ish": {"severity": "high", "via": ["GHSA-aaaa-bbbb-cccc"]},
            "quiet-low": {"severity": "low", "via": ["GHSA-low"]},
        }
    }
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 1
    assert "untriaged" in capsys.readouterr().out


def test_triaged_findings_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        _TRIAGE, "TRIAGED", {"GHSA-aaaa-bbbb-cccc": ("2026-10-13", "test")}
    )
    doc = {
        "vulnerabilities": {
            "left-pad-ish": {"severity": "high", "via": ["GHSA-aaaa-bbbb-cccc"]},
        }
    }
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 0


def test_legacy_advisories_shape_supported(tmp_path, monkeypatch, capsys):
    doc = {
        "advisories": {
            "123": {
                "severity": "critical",
                "module_name": "old-dep",
                "url": "http://127.0.0.1/advisory",
            }
        }
    }
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 1
    assert "old-dep" in capsys.readouterr().out

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
        _TRIAGE,
        "TRIAGED",
        {"GHSA-aaaa-bbbb-cccc::node_modules/left-pad-ish": ("2026-10-13", "test")},
    )
    doc = {
        "vulnerabilities": {
            "left-pad-ish": {
                "severity": "high",
                "via": ["GHSA-aaaa-bbbb-cccc"],
                "nodes": ["node_modules/left-pad-ish"],
            },
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


# --- Scoped triage: advisory AND dependency path (real d1a2209 shape) ---

_ZOD_GHSA = "http://127.0.0.1/advisories/GHSA-mrhx-c7xc-m3w5"
_NESTED_ZOD = "node_modules/@ag-ui/core/node_modules/zod"


def _zod_advisory(url: str = _ZOD_GHSA, severity: str = "high") -> dict:
    return {
        "source": 1243066,
        "name": "zod",
        "dependency": "zod",
        "title": "Zod can exhaust memory while validating oversized constrained collections",
        "url": url,
        "severity": severity,
        "range": "<4.6.6",
    }


def _reviewed_doc() -> dict:
    """The high+ rows of the real pinned-redteam audit (pin d1a2209)."""
    return {
        "vulnerabilities": {
            "@ag-ui/core": {
                "severity": "high",
                "via": ["zod"],
                "nodes": ["node_modules/@ag-ui/core"],
            },
            "@openuidev/react-headless": {
                "severity": "high",
                "via": ["@ag-ui/core"],
                "nodes": ["node_modules/@openuidev/react-headless"],
            },
            "@openuidev/react-ui": {
                "severity": "high",
                "via": ["@openuidev/react-headless", "rehype-katex"],
                "nodes": ["node_modules/@openuidev/react-ui"],
            },
            "rehype-katex": {
                "severity": "low",
                "via": ["katex"],
                "nodes": ["node_modules/rehype-katex"],
            },
            "katex": {
                "severity": "low",
                "via": [
                    {
                        "source": 1241206,
                        "url": "http://127.0.0.1/advisories/GHSA-238p-pmpm-9mq7",
                        "severity": "low",
                    }
                ],
                "nodes": ["node_modules/katex"],
            },
            "zod": {
                "severity": "high",
                "via": [_zod_advisory()],
                "nodes": [_NESTED_ZOD],
            },
        }
    }


def test_reviewed_advisory_on_reviewed_path_is_accepted(tmp_path, monkeypatch, capsys):
    """The real TRIAGED block accepts exactly the reviewed nested-zod chain."""
    findings, error = _TRIAGE.triage(_reviewed_doc())
    assert error is None
    assert sorted(key for key, _, _ in findings) == [
        "GHSA-mrhx-c7xc-m3w5::node_modules/@ag-ui/core",
        f"GHSA-mrhx-c7xc-m3w5::{_NESTED_ZOD}",
        "GHSA-mrhx-c7xc-m3w5::node_modules/@openuidev/react-headless",
        "GHSA-mrhx-c7xc-m3w5::node_modules/@openuidev/react-ui",
    ]
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, _reviewed_doc())))
    assert _TRIAGE.main() == 0
    assert "0 untriaged" in capsys.readouterr().out


def test_different_critical_advisory_on_top_level_zod_is_rejected(
    tmp_path, monkeypatch, capsys
):
    """An unrelated critical on top-level zod fails even though zod is triaged."""
    doc = _reviewed_doc()
    zod = doc["vulnerabilities"]["zod"]
    zod["severity"] = "critical"
    zod["via"].append(
        _zod_advisory("http://127.0.0.1/advisories/GHSA-zzzz-yyyy-xxxx", "critical")
    )
    zod["nodes"].append("node_modules/zod")
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 1
    out = capsys.readouterr().out
    assert "GHSA-zzzz-yyyy-xxxx::node_modules/zod" in out
    assert f"GHSA-zzzz-yyyy-xxxx::{_NESTED_ZOD}" in out


def test_different_advisory_on_reviewed_path_is_rejected(tmp_path, monkeypatch, capsys):
    """A new advisory on the reviewed nested copy is not covered by the entry."""
    doc = _reviewed_doc()
    doc["vulnerabilities"]["zod"]["via"] = [
        _zod_advisory("http://127.0.0.1/advisories/GHSA-zzzz-yyyy-xxxx", "critical")
    ]
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 1
    assert f"GHSA-zzzz-yyyy-xxxx::{_NESTED_ZOD}" in capsys.readouterr().out


def test_same_advisory_on_different_path_is_rejected(tmp_path, monkeypatch, capsys):
    """The reviewed advisory on top-level zod (not the nested copy) fails."""
    doc = _reviewed_doc()
    doc["vulnerabilities"]["zod"]["nodes"] = ["node_modules/zod"]
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 1
    out = capsys.readouterr().out
    assert "GHSA-mrhx-c7xc-m3w5::node_modules/zod" in out


def test_package_name_alone_never_matches(tmp_path, monkeypatch, capsys):
    """A name-keyed TRIAGED entry does not cover a resolved advisory."""
    monkeypatch.setattr(_TRIAGE, "TRIAGED", {"zod": ("2026-10-29", "test")})
    doc = {"vulnerabilities": {"zod": _reviewed_doc()["vulnerabilities"]["zod"]}}
    monkeypatch.setenv("AUDIT_JSON", str(_write_audit(tmp_path, doc)))
    assert _TRIAGE.main() == 1


def test_unresolved_advisory_keys_on_package_and_path():
    """A high row with no resolvable advisory is keyed package:<name>::<path>."""
    doc = {
        "vulnerabilities": {
            "mystery": {
                "severity": "high",
                "via": ["not-in-map"],
                "nodes": ["node_modules/mystery"],
            }
        }
    }
    findings, error = _TRIAGE.triage(doc)
    assert error is None
    assert findings == [("package:mystery::node_modules/mystery", "high", "mystery")]


def test_legacy_advisory_binds_paths():
    doc = {
        "advisories": {
            "1": {
                "severity": "high",
                "module_name": "old-dep",
                "url": "http://127.0.0.1/advisories/GHSA-aaaa-bbbb-cccc",
                "findings": [{"paths": ["app>old-dep"]}],
            }
        }
    }
    findings, error = _TRIAGE.triage(doc)
    assert error is None
    assert findings == [("GHSA-aaaa-bbbb-cccc::app>old-dep", "high", "old-dep")]


def test_real_triage_block_has_no_name_only_high_entries():
    """Every TRIAGED key is scoped ``<advisory>::<path>``."""
    for key in _TRIAGE.TRIAGED:
        advisory, sep, path = key.partition("::")
        assert sep and advisory and path, key
        assert path != _TRIAGE.UNKNOWN_PATH, key

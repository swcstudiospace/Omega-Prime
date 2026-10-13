# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic tests for the redteam audit delta scanner.

Recorded run (hand-computed expectation): diffing the committed
``redteam-audit-831a6b0.json`` old-pin audit against the committed
``redteam-lock-v1.3.json`` upstream lock yields exactly one row per
report class and verdict ``regressed``:

- new: ``https://github.com/advisories/GHSA-new-0004`` (critical,
  package ``shiny-new-dep`` — absent from the old pin);
- fixed: ``https://github.com/advisories/GHSA-fixed-0001`` (high,
  package ``brace-expansion`` — advisory gone and the package no
  longer resolves at the old range);
- rescinded: ``https://github.com/advisories/GHSA-rescinded-0002``
  (moderate, package ``old-stable-pkg`` — advisory record gone while
  the lock still resolves the same ``>=2.0.0 <2.0.5`` range);
- severity drift: ``https://github.com/advisories/GHSA-drift-0003``
  ``moderate -> high`` (package ``drift-pkg``);
- verdict ``regressed`` (new non-empty, plus an upward drift).

The fixtures are synthetic but shape-faithful: the audit side uses the
real ``npm audit --json`` ``vulnerabilities`` schema handled by the
triage script, and the lock side uses the real ``package-lock.json``
``packages``/``lockfileVersion`` envelope with per-package
``advisories`` blocks standing in for already-resolved advisory
metadata (a live upstream lock carries none, and no network or
submodule checkout is used here).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "redteam-audit-delta.py"
_DATA = Path(__file__).resolve().parent / "data"
_OLD_AUDIT = _DATA / "redteam-audit-831a6b0.json"
_NEW_LOCK = _DATA / "redteam-lock-v1.3.json"

FIXED_KEY = "https://github.com/advisories/GHSA-fixed-0001"
RESCINDED_KEY = "https://github.com/advisories/GHSA-rescinded-0002"
DRIFT_KEY = "https://github.com/advisories/GHSA-drift-0003"
NEW_KEY = "https://github.com/advisories/GHSA-new-0004"


def _load():
    spec = importlib.util.spec_from_file_location("redteam_audit_delta", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DELTA = _load()


def _write(tmp_path: Path, name: str, doc: object) -> str:
    target = tmp_path / name
    target.write_text(json.dumps(doc), encoding="utf-8")
    return str(target)


def _audit_vuln(severity: str, via: list, range_: str = "") -> dict:
    return {"severity": severity, "via": via, "range": range_, "fixAvailable": False}


def _run(old: str, new: str, tmp_path: Path, capsys) -> tuple[int, str]:
    out = str(tmp_path / "report.md")
    code = _DELTA.main(["--old", old, "--new", new, "--out", out])
    return code, capsys.readouterr().out.strip()


# Happy paths.


def test_recorded_pin_vs_lock_run(tmp_path, capsys):
    """Done-criteria 3: the committed fixture pair hits every section."""
    code, verdict = _run(str(_OLD_AUDIT), str(_NEW_LOCK), tmp_path, capsys)
    assert code == 0
    assert verdict == "regressed"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    for header in (
        "## Advisories new",
        "## Fixed",
        "## Rescinded",
        "## Severity drift",
    ):
        assert header in report
    assert "Verdict: regressed" in report
    assert NEW_KEY in report and "shiny-new-dep" in report and "critical" in report
    assert FIXED_KEY in report and "brace-expansion" in report
    assert RESCINDED_KEY in report and "old-stable-pkg" in report
    assert (
        DRIFT_KEY in report and "moderate -> high" in report and "drift-pkg" in report
    )


def test_disjoint_audit_pair_yields_new_and_fixed(tmp_path, capsys):
    old = _write(
        tmp_path,
        "old.json",
        {
            "vulnerabilities": {
                "gone-dep": _audit_vuln("high", ["gone-dep", "GHSA-gone-1"], "<2.0.0")
            }
        },
    )
    new = _write(
        tmp_path,
        "new.json",
        {
            "vulnerabilities": {
                "fresh-dep": _audit_vuln("critical", ["GHSA-fresh-2"], "^1.0.0")
            }
        },
    )
    code, verdict = _run(old, new, tmp_path, capsys)
    assert code == 0
    assert verdict == "regressed"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "GHSA-fresh-2" in report and "GHSA-gone-1" in report
    assert "Verdict: regressed" in report


def test_mixed_lock_old_and_audit_new(tmp_path, capsys):
    old = _write(
        tmp_path,
        "old-lock.json",
        {
            "name": "x",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "x", "version": "1.0.0"},
                "node_modules/mixed-dep": {
                    "version": "1.0.0",
                    "range": "^1.0.0",
                    "advisories": [{"url": "GHSA-mixed-1", "severity": "low"}],
                },
            },
        },
    )
    new = _write(
        tmp_path,
        "new.json",
        {
            "vulnerabilities": {
                "mixed-dep": _audit_vuln("low", ["GHSA-mixed-1"], "^1.0.0")
            }
        },
    )
    code, verdict = _run(old, new, tmp_path, capsys)
    assert code == 0
    assert verdict == "unchanged"


def test_legacy_advisories_shape_parses(tmp_path, capsys):
    old = _write(
        tmp_path,
        "old.json",
        {
            "advisories": {
                "42": {
                    "id": 42,
                    "module_name": "old-dep",
                    "severity": "high",
                    "url": "https://npmjs.com/advisories/42",
                }
            }
        },
    )
    new = _write(tmp_path, "new.json", {"vulnerabilities": {}})
    code, verdict = _run(old, new, tmp_path, capsys)
    assert code == 0
    assert verdict == "improved"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "https://npmjs.com/advisories/42" in report and "old-dep" in report


# Edge cases.


def test_identical_inputs_verdict_unchanged(tmp_path, capsys):
    code, verdict = _run(str(_OLD_AUDIT), str(_OLD_AUDIT), tmp_path, capsys)
    assert code == 0
    assert verdict == "unchanged"


def test_upward_drift_regresses(tmp_path, capsys):
    old = _write(
        tmp_path,
        "old.json",
        {"vulnerabilities": {"d": _audit_vuln("high", ["GHSA-drift-9"], "^1.0.0")}},
    )
    new = _write(
        tmp_path,
        "new.json",
        {"vulnerabilities": {"d": _audit_vuln("critical", ["GHSA-drift-9"], "^1.0.0")}},
    )
    code, verdict = _run(old, new, tmp_path, capsys)
    assert code == 0
    assert verdict == "regressed"
    assert "high -> critical" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_downward_drift_only_is_unchanged(tmp_path, capsys):
    old = _write(
        tmp_path,
        "old.json",
        {"vulnerabilities": {"d": _audit_vuln("high", ["GHSA-drift-9"], "^1.0.0")}},
    )
    new = _write(
        tmp_path,
        "new.json",
        {"vulnerabilities": {"d": _audit_vuln("low", ["GHSA-drift-9"], "^1.0.0")}},
    )
    code, verdict = _run(old, new, tmp_path, capsys)
    assert code == 0
    assert verdict == "unchanged"


def test_rescinded_only_is_unchanged(tmp_path, capsys):
    old = _write(
        tmp_path,
        "old.json",
        {
            "vulnerabilities": {
                "s": _audit_vuln("moderate", ["GHSA-quiet-7"], ">=1.0.0 <2.0.0")
            }
        },
    )
    new = _write(
        tmp_path,
        "new.json",
        {
            "name": "x",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "x", "version": "1.0.0"},
                "node_modules/s": {"version": "1.5.0", "range": ">=1.0.0 <2.0.0"},
            },
        },
    )
    code, verdict = _run(old, new, tmp_path, capsys)
    assert code == 0
    assert verdict == "unchanged"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert (
        "GHSA-quiet-7" in report.split("## Rescinded")[1].split("## Severity drift")[0]
    )


# Failures.


def test_error_member_payload_fails_naming_file(tmp_path, capsys):
    bad = _write(
        tmp_path, "bad.json", {"error": {"code": "ENOAUDIT", "summary": "no audit"}}
    )
    out = str(tmp_path / "report.md")
    assert _DELTA.main(["--old", bad, "--new", str(_NEW_LOCK), "--out", out]) == 1
    err = capsys.readouterr().err
    assert "bad.json" in err and "ENOAUDIT" in err


def test_unknown_shape_fails_closed(tmp_path, capsys):
    strange = _write(tmp_path, "strange.json", {"something": "else"})
    out = str(tmp_path / "report.md")
    assert _DELTA.main(["--old", strange, "--new", str(_NEW_LOCK), "--out", out]) == 1
    err = capsys.readouterr().err
    assert "strange.json" in err


def test_malformed_json_fails(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    out = str(tmp_path / "report.md")
    assert (
        _DELTA.main(["--old", str(broken), "--new", str(_NEW_LOCK), "--out", out]) == 1
    )
    assert "broken.json" in capsys.readouterr().err


def test_missing_input_fails(tmp_path, capsys):
    missing = str(tmp_path / "does-not-exist.json")
    out = str(tmp_path / "report.md")
    assert _DELTA.main(["--old", missing, "--new", str(_NEW_LOCK), "--out", out]) == 1
    assert "does-not-exist.json" in capsys.readouterr().err


def test_missing_out_parent_exits_2(tmp_path, capsys):
    out = str(tmp_path / "no-such-dir" / "report.md")
    code = _DELTA.main(
        ["--old", str(_OLD_AUDIT), "--new", str(_NEW_LOCK), "--out", out]
    )
    assert code == 2
    assert "no-such-dir" in capsys.readouterr().err


def test_missing_flag_exits_2():
    with pytest.raises(SystemExit) as exc:
        _DELTA.main(["--old", str(_OLD_AUDIT), "--new", str(_NEW_LOCK)])
    assert exc.value.code == 2

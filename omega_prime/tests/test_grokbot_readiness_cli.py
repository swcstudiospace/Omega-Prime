# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Actual CLI regression for an unexplained canonical absence.

Appends one synthetic unknown tool name to ONLY the disposable vetted
``isolated_layout(ROOT)`` copy of the canonical roster, then invokes the
real readiness CLI against that owned root. Expectations derive from the
roster text and report shape/status semantics, never hardcoded normal
counts or source wording.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

from omega_prime.grokbot import local_validation as lv
from omega_prime.tooling.roster import load_roster_names

ROOT = Path(__file__).resolve().parents[2]

_SYNTHETIC = "zz-synthetic-unexplained-absence-canary"


def test_cli_unexplained_canonical_absence_fails(tmp_path: Path) -> None:
    """An owned-roster name absent from offered tools fails the real CLI."""
    baseline = load_roster_names(ROOT / lv.ROSTER_REL)
    assert baseline, "canonical roster must list tools"
    assert _SYNTHETIC not in baseline

    with lv.isolated_layout(ROOT) as layout:
        owned_roster = layout.install / lv.ROSTER_REL
        inventory_path = layout.install / ".omega-readiness-inventory.json"
        assert inventory_path.is_file()
        inventory_before = inventory_path.read_text(encoding="utf-8")

        text = owned_roster.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        owned_roster.write_text(text + f"  - {_SYNTHETIC}\n", encoding="utf-8")

        # Only the copied roster changed; vetted inventory/path membership kept.
        assert inventory_path.read_text(encoding="utf-8") == inventory_before
        assert load_roster_names(owned_roster) == [*baseline, _SYNTHETIC]

        report_path = tmp_path / "absence-report.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "omega_prime.grokbot.readiness",
                "--root",
                str(layout.install),
                "--json",
                "--output",
                str(report_path),
            ],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=600,
        )
        assert completed.returncode == 1, completed.stderr.decode()[-500:]

        report = json.loads(completed.stdout.decode("utf-8"))
        assert report["schema_version"] == 1
        assert report["scope"] == "isolated-local"
        assert report["ok"] is False
        assert (completed.returncode == 0) == (report["ok"] is True)

        tools = report["tools"]
        by_name = {row["name"]: row for row in tools}
        assert set(by_name) == set(baseline) | {_SYNTHETIC}
        assert len(by_name) == len(baseline) + 1

        row = by_name[_SYNTHETIC]
        for key in ("name", "family", "status", "evidence", "prerequisites"):
            assert key in row
        assert row["status"] == "failed"
        assert row["status"] not in ("blocked", "feature_disabled", "exercised")
        assert row["family"] == "unmapped"
        assert row["evidence"] != []
        assert row["prerequisites"] == []

        problems = report["summary"]["problems"]
        assert any(
            _SYNTHETIC in problem
            and ("absent-from-offered" in problem or "unmapped" in problem)
            for problem in problems
        )

        failed = [item for item in tools if item["status"] == "failed"]
        assert any(item["name"] == _SYNTHETIC for item in failed)
        summary = report["summary"]
        assert summary["total"] == len(baseline) + 1
        assert summary["roster"] == len(baseline) + 1
        assert summary["failed"] >= 1

        assert report_path.is_file()
        assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


_USAGE_CANARY = "omk_testcanary123"
"""Synthetic recognized-shaped unknown argument (matches the omk gate)."""

_SECRET_SHAPED_ROOT = "/tmp/omk_shortcanary1"
"""Nonexistent secret-shaped root: no roster lives under it."""

_BENIGN_MISSING_ROOT = "/tmp/omega-benign-missing-seat"
"""Nonexistent benign root: lowercase-only, one short token-alphabet run."""

_USAGE_WITHHELD_LABEL = "usage error withheld: secret gate tripped"


def _run_actual_cli(*argv: str) -> subprocess.CompletedProcess[bytes]:
    """Invoke the real readiness CLI without starting a product child."""
    return subprocess.run(
        [sys.executable, "-m", "omega_prime.grokbot.readiness", *argv],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )


def test_cli_unknown_secret_shaped_argument_withheld() -> None:
    """An actual unknown secret-shaped arg refuses 2 with no raw echo."""
    completed = _run_actual_cli(_USAGE_CANARY)
    assert completed.returncode == 2, completed.stderr.decode()[-500:]
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    assert stdout == ""
    assert _USAGE_CANARY not in stdout
    assert _USAGE_CANARY not in stderr
    assert _USAGE_WITHHELD_LABEL in stderr
    assert "usage:" in stderr


def test_cli_secret_shaped_missing_root_withheld(tmp_path: Path) -> None:
    """A secret-shaped missing root refuses 2, publishes only the label."""
    report_path = tmp_path / "withheld-report.json"
    completed = _run_actual_cli(
        "--root", _SECRET_SHAPED_ROOT, "--json", "--output", str(report_path)
    )
    assert completed.returncode == 2, completed.stderr.decode()[-500:]
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    assert stdout == ""
    assert _SECRET_SHAPED_ROOT not in stdout
    assert _SECRET_SHAPED_ROOT not in stderr
    assert _USAGE_CANARY not in stderr
    assert _USAGE_WITHHELD_LABEL in stderr
    assert not report_path.exists()


def test_cli_benign_missing_root_stays_helpful() -> None:
    """A benign missing root still names its path with the usage message."""
    completed = _run_actual_cli("--root", _BENIGN_MISSING_ROOT)
    assert completed.returncode == 2, completed.stderr.decode()[-500:]
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    assert stdout == ""
    assert "no canonical roster" in stderr
    assert _BENIGN_MISSING_ROOT in stderr
    assert _USAGE_WITHHELD_LABEL not in stderr

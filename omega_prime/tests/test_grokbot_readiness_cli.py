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
import os
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


_USAGE_CANARY = "omk_" + "testcanary123"
"""Synthetic recognized-shaped unknown argument (matches the omk gate)."""

_SECRET_SHAPED_ROOT = "/tmp/" + "omk_" + "shortcanary1"
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


_RETENTION_STAGE = "readiness"
_RETENTION_INTERPRETER = "requested 3.12, setup 3.12.7, path /opt/hostedtoolcache/Python/3.12.7/x64/bin/python3"
_RETENTION_SOURCE_COMMIT = "retention-test-source-commit"
_RETENTION_CANARY = "omk_" + "retentioncanary9"
"""Distinct recognized-shaped token: trips only the retention secret gate."""

_RETENTION_MISSING_REASON = "candidate report missing"
_RETENTION_INCOMPLETE_REASON = "candidate report incomplete"
_RETENTION_WITHHELD_REASON = "candidate report withheld: secret gate tripped"

_RETENTION_METADATA_KEYS = {
    "stage",
    "exit_code",
    "reason",
    "interpreter",
    "source_commit",
}


_RETENTION_STAGE_CANARY = "omk_" + "retentionstage7"
"""Recognized-shaped token poisoning the --stage identity label."""

_RETENTION_INTERPRETER_CANARY = "omk_" + "retentioninterp8"
"""Recognized-shaped token poisoning the --interpreter identity label."""

_RETENTION_SAFE_STAGE = "withheld"
"""Fixed safe stage label published when an identity label trips the gate."""

_RETENTION_SAFE_FIELD = "withheld: secret gate tripped"
"""Fixed safe identity value published when a label trips the gate."""


def _canonical_retention_report(ok: bool = True) -> dict:
    """Admission-valid report fixture covering the current canonical roster.

    Names come from the real checkout roster (the same source the retention
    producer validates against), each carried exactly once with a valid
    status -- never an empty tool list or a caller-invented roster the
    producer would rightly refuse.
    """
    names = load_roster_names(ROOT / lv.ROSTER_REL)
    assert names, "canonical roster must list tools"
    return {
        "schema_version": 1,
        "scope": "isolated-local",
        "profile": "default",
        "ok": ok,
        "checks": [],
        "tools": [
            {
                "name": name,
                "family": "retention-fixture",
                "status": "exercised",
                "evidence": ["retention fixture evidence"],
                "prerequisites": [],
            }
            for name in names
        ],
        "summary": {"total": len(names), "problems": []},
        "limitations": [],
    }


def _run_retention_producer(
    seat: Path,
    *,
    observed_exit: str,
    candidate_text: str | None = None,
    candidate_name: str = "candidate.json",
    output_name: str = "retained.json",
    output_log: str = "github-output",
    stage: str = _RETENTION_STAGE,
    interpreter: str = _RETENTION_INTERPRETER,
    source_commit: str = _RETENTION_SOURCE_COMMIT,
) -> tuple[subprocess.CompletedProcess[bytes], Path, Path]:
    """Invoke the real retention producer with an owned RUNNER_TEMP seat."""
    seat.mkdir(parents=True, exist_ok=True)
    github_output = seat / output_log
    if not github_output.exists():
        github_output.write_text("", encoding="utf-8")
    candidate = seat / candidate_name
    if candidate_text is not None:
        candidate.write_text(candidate_text, encoding="utf-8")
    output = seat / output_name
    env = dict(os.environ)
    env["RUNNER_TEMP"] = str(seat)
    env["GITHUB_OUTPUT"] = str(github_output)
    # The producer imports omega_prime.tooling.fs; script-dir sys.path does not
    # include the repo root, so hand it over explicitly (no-ops where the
    # package is pip-installed and keeps an existing PYTHONPATH working).
    env["PYTHONPATH"] = (
        str(ROOT)
        if not env.get("PYTHONPATH")
        else str(ROOT) + os.pathsep + env["PYTHONPATH"]
    )
    completed = subprocess.run(
        [
            sys.executable,
            "omega_prime/scripts/ci_receipt.py",
            "retention",
            "--stage",
            stage,
            "--observed-exit",
            observed_exit,
            "--interpreter",
            interpreter,
            "--source-commit",
            source_commit,
            "--candidate",
            str(candidate),
            "--output",
            str(output),
        ],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        env=env,
    )
    return completed, output, github_output


def _github_exit_codes(github_output: Path) -> list[str]:
    """Exit codes the producer recorded through the GITHUB_OUTPUT receipt."""
    codes: list[str] = []
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if line.startswith("exit_code="):
            codes.append(line.split("=", 1)[1])
    return codes


def test_retention_partial_candidate_yields_failure_metadata(tmp_path: Path) -> None:
    """A failing outcome with a partial candidate keeps exit 1, never a pass."""
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run",
        observed_exit="1",
        candidate_text=json.dumps({"schema_version": 1}),
    )
    assert completed.returncode == 1, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert metadata == {
        "stage": _RETENTION_STAGE,
        "exit_code": 1,
        "reason": _RETENTION_INCOMPLETE_REASON,
        "interpreter": _RETENTION_INTERPRETER,
        "source_commit": _RETENTION_SOURCE_COMMIT,
    }
    assert _github_exit_codes(github_output) == ["1"]
    assert "observed_exit_code=1" in completed.stdout.decode("utf-8")


def test_retention_missing_candidate_is_truthful_failure(tmp_path: Path) -> None:
    """An absent candidate never becomes a pass; the nonzero exit is preserved."""
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run", observed_exit="2"
    )
    assert completed.returncode == 2, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert set(metadata) == _RETENTION_METADATA_KEYS
    assert metadata["stage"] == _RETENTION_STAGE
    assert metadata["exit_code"] == 2
    assert metadata["reason"] == _RETENTION_MISSING_REASON
    assert metadata["interpreter"] == _RETENTION_INTERPRETER
    assert metadata["source_commit"] == _RETENTION_SOURCE_COMMIT
    assert "ok" not in metadata
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert _github_exit_codes(github_output) == ["2"]


def test_retention_secret_candidate_withheld(tmp_path: Path) -> None:
    """A secret-bearing candidate is refused even when the observed exit was 0."""
    report = _canonical_retention_report(ok=True)
    report["summary"]["problems"].append(_RETENTION_CANARY)
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run",
        observed_exit="0",
        candidate_text=json.dumps(report),
    )
    assert completed.returncode == 1, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert set(metadata) == _RETENTION_METADATA_KEYS
    assert metadata["exit_code"] == 0
    assert metadata["reason"] == _RETENTION_WITHHELD_REASON
    assert _github_exit_codes(github_output) == ["1"]
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    assert _RETENTION_CANARY not in stdout
    assert _RETENTION_CANARY not in stderr
    assert _RETENTION_CANARY not in output.read_text(encoding="utf-8")
    assert "observed_exit_code=0" in stdout
    assert "exit_code=1" in stdout


def test_retention_later_success_keeps_earlier_failure(tmp_path: Path) -> None:
    """Each run's own exit and document stand; later green never rewrites failure."""
    seat = tmp_path / "seat"
    failed, failed_output, github_output = _run_retention_producer(
        seat, observed_exit="1", output_name="failed.json"
    )
    assert failed.returncode == 1, failed.stderr.decode()[-500:]
    failed_bytes = failed_output.read_bytes()
    assert json.loads(failed_bytes.decode("utf-8"))["exit_code"] == 1

    candidate_text = json.dumps(_canonical_retention_report(ok=True))
    passed, passed_output, _ = _run_retention_producer(
        seat,
        observed_exit="0",
        candidate_text=candidate_text,
        candidate_name="ok-candidate.json",
        output_name="ok.json",
    )
    assert passed.returncode == 0, passed.stderr.decode()[-500:]
    assert passed_output.read_text(encoding="utf-8") == candidate_text
    assert failed_output.read_bytes() == failed_bytes
    assert _github_exit_codes(github_output) == ["1", "0"]


def test_retention_observed_zero_missing_candidate_refused(tmp_path: Path) -> None:
    """Observed 0 with no candidate publishes nothing and forces nonzero."""
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run", observed_exit="0"
    )
    assert completed.returncode == 1, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert set(metadata) == _RETENTION_METADATA_KEYS
    assert metadata["exit_code"] == 0
    assert metadata["exit_code"] is not None
    assert metadata["reason"] == _RETENTION_MISSING_REASON
    assert _github_exit_codes(github_output) == ["1"]
    assert "observed_exit_code=0" in completed.stdout.decode("utf-8")


def test_retention_unobserved_stage_records_null(tmp_path: Path) -> None:
    """A skipped stage records exit_code null, never null-as-zero, and fails."""
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run", observed_exit="unobserved"
    )
    assert completed.returncode == 1, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert set(metadata) == _RETENTION_METADATA_KEYS
    assert metadata["exit_code"] is None
    assert "unobserved" in metadata["reason"]
    assert _github_exit_codes(github_output) == ["1"]
    assert "observed_exit_code=null" in completed.stdout.decode("utf-8")


def test_retention_poisoned_stage_withheld(tmp_path: Path) -> None:
    """A recognized token in --stage refuses publication with fixed labels."""
    candidate_text = json.dumps(_canonical_retention_report(ok=True))
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run",
        observed_exit="0",
        candidate_text=candidate_text,
        stage=_RETENTION_STAGE_CANARY,
    )
    assert completed.returncode == 1, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert set(metadata) == _RETENTION_METADATA_KEYS
    assert metadata["exit_code"] == 0
    assert metadata["reason"] == _RETENTION_WITHHELD_REASON
    assert metadata["stage"] == _RETENTION_SAFE_STAGE
    assert metadata["interpreter"] == _RETENTION_INTERPRETER
    assert metadata["source_commit"] == _RETENTION_SOURCE_COMMIT
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert _github_exit_codes(github_output) == ["1"]
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    assert _RETENTION_STAGE_CANARY not in stdout
    assert _RETENTION_STAGE_CANARY not in stderr
    assert _RETENTION_STAGE_CANARY not in output.read_text(encoding="utf-8")
    assert "observed_exit_code=0" in stdout
    assert "exit_code=1" in stdout


def test_retention_poisoned_interpreter_withheld(tmp_path: Path) -> None:
    """A recognized token in --interpreter refuses publication, no raw echo."""
    candidate_text = json.dumps(_canonical_retention_report(ok=True))
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run",
        observed_exit="0",
        candidate_text=candidate_text,
        interpreter=_RETENTION_INTERPRETER_CANARY,
    )
    assert completed.returncode == 1, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert set(metadata) == _RETENTION_METADATA_KEYS
    assert metadata["exit_code"] == 0
    assert metadata["reason"] == _RETENTION_WITHHELD_REASON
    assert metadata["stage"] == _RETENTION_STAGE
    assert metadata["interpreter"] == _RETENTION_SAFE_FIELD
    assert metadata["source_commit"] == _RETENTION_SOURCE_COMMIT
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert _github_exit_codes(github_output) == ["1"]
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    assert _RETENTION_INTERPRETER_CANARY not in stdout
    assert _RETENTION_INTERPRETER_CANARY not in stderr
    assert _RETENTION_INTERPRETER_CANARY not in output.read_text(encoding="utf-8")
    assert "observed_exit_code=0" in stdout
    assert "exit_code=1" in stdout


def test_retention_clean_valid_report_publishes_verbatim(tmp_path: Path) -> None:
    """A canonically valid report with coherent exit 0 publishes byte-identical."""
    candidate_text = json.dumps(_canonical_retention_report(ok=True))
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run", observed_exit="0", candidate_text=candidate_text
    )
    assert completed.returncode == 0, completed.stderr.decode()[-500:]
    assert output.read_text(encoding="utf-8") == candidate_text
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert _github_exit_codes(github_output) == ["0"]
    stdout = completed.stdout.decode("utf-8")
    assert f"observed_stage={_RETENTION_STAGE}" in stdout
    assert "observed_exit_code=0" in stdout


def test_retention_partial_roster_refused(tmp_path: Path) -> None:
    """A candidate missing canonical names publishes nothing, exit preserved."""
    report = _canonical_retention_report(ok=False)
    del report["tools"][1:]
    report["summary"]["total"] = len(report["tools"])
    completed, output, github_output = _run_retention_producer(
        tmp_path / "run", observed_exit="1", candidate_text=json.dumps(report)
    )
    assert completed.returncode == 1, completed.stderr.decode()[-500:]
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert set(metadata) == _RETENTION_METADATA_KEYS
    assert metadata["exit_code"] == 1
    assert metadata["reason"] == _RETENTION_INCOMPLETE_REASON
    assert metadata["interpreter"] == _RETENTION_INTERPRETER
    assert _github_exit_codes(github_output) == ["1"]


def test_retention_duplicate_members_refuse_without_losing_observed_exit(
    tmp_path: Path,
) -> None:
    report = _canonical_retention_report(ok=True)
    candidate = json.dumps(report)
    summary = json.dumps(report["summary"])
    ambiguous = {
        "top": '{"ok": false, ' + candidate[1:],
        "nested": candidate.replace(summary, '{"total": -1, ' + summary[1:], 1),
    }
    for scope, text in ambiguous.items():
        for observed in ("0", "7"):
            completed, output, github_output = _run_retention_producer(
                tmp_path / f"{scope}-{observed}",
                observed_exit=observed,
                candidate_text=text,
            )
            effective = "1" if observed == "0" else observed
            assert completed.returncode == int(effective)
            metadata = json.loads(output.read_text(encoding="utf-8"))
            assert metadata["exit_code"] == int(observed)
            assert "ok" not in metadata
            assert "schema_version" not in metadata
            assert output.read_text(encoding="utf-8") != text
            assert _github_exit_codes(github_output) == [effective]
            assert stat.S_IMODE(output.stat().st_mode) == 0o600

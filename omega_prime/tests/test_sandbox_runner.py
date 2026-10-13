# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the fail-closed sandbox runner (Phase 83, SAND-02).

Hermetic: the subprocess call goes through an injected stub transport.
Live-binary probes skip (never fail) when openshell is absent, so this
module passes both with /usr/bin/openshell present and with PATH
scrubbed of openshell.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from omega_prime.sandbox.runner import (
    SandboxRunner,
    SandboxUnavailable,
    build_create_argv,
    probe_openshell,
)


def _policy(tmp_path: Path) -> Path:
    policy = tmp_path / "sandbox-policy.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")
    return policy


def _success_transport(seen: list):
    def transport(argv, timeout=120, cwd=None):
        seen.append((list(argv), timeout, cwd))
        if argv[:2] == ["openshell", "--version"] or (
            len(argv) >= 2 and argv[1] == "--version"
        ):
            return {"exit_code": 0, "stdout": "openshell 0.1.2", "stderr": ""}
        return {"exit_code": 0, "stdout": "gate-ok", "stderr": ""}
    return transport


def test_stub_present_run_captures_exit_output_and_binding(tmp_path):
    seen: list = []
    runner = SandboxRunner(
        _policy(tmp_path),
        transport=_success_transport(seen),
        probe=lambda: (True, None),
    )
    result = runner.run(["python3", "-m", "pytest", "-q"], timeout=60, cwd="/work")
    assert result.exit_code == 0
    assert "gate-ok" in result.output
    assert result.argv == ["python3", "-m", "pytest", "-q"]
    expected = hashlib.sha256(
        json.dumps(["python3", "-m", "pytest", "-q"], ensure_ascii=False,
                   sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert result.argv_sha256 == expected
    assert result.policy_sha256 == hashlib.sha256(
        (tmp_path / "sandbox-policy.yaml").read_bytes()
    ).hexdigest()
    create_argv = seen[0][0]
    assert create_argv[:3] == ["openshell", "sandbox", "create"]
    assert "--no-auto-providers" in create_argv
    assert "--policy" in create_argv
    assert create_argv[create_argv.index("--") + 1:] == [
        "python3", "-m", "pytest", "-q",
    ]
    assert seen[0][1] == 60
    assert seen[0][2] == "/work"

def test_forced_create_failure_never_executes_argv(tmp_path):
    marker = tmp_path / "would-have-run.txt"
    calls: list = []

    def failing_transport(argv, timeout=120, cwd=None):
        calls.append(list(argv))
        raise RuntimeError("sandbox create: denied")

    runner = SandboxRunner(
        _policy(tmp_path),
        transport=failing_transport,
        probe=lambda: (True, None),
    )
    argv = ["python3", "-c", f"open({str(marker)!r}, 'w').write('x')"]
    with pytest.raises(SandboxUnavailable, match="sandbox_unavailable"):
        runner.run(argv)
    assert not marker.exists()
    # The only transport call was the wrapped create; the raw argv never ran.
    assert len(calls) == 1
    assert calls[0][:3] == ["openshell", "sandbox", "create"]


def test_create_failure_cause_names_create_failed(tmp_path):
    def failing_transport(argv, timeout=120, cwd=None):
        raise RuntimeError("sandbox create: denied")

    runner = SandboxRunner(
        _policy(tmp_path),
        transport=failing_transport,
        probe=lambda: (True, None),
    )
    try:
        runner.run(["echo", "hi"])
    except SandboxUnavailable as exc:
        assert exc.cause.startswith("create-failed")
        assert "sandbox_unavailable" in str(exc)
    else:
        raise AssertionError("expected SandboxUnavailable")


def _missing_binary_transport(calls: list):
    """Emulate an OS without the binary: --version raises FileNotFoundError."""

    def transport(argv, timeout=120, cwd=None):
        calls.append(list(argv))
        raise FileNotFoundError("openshell")

    return transport


def test_binary_absent_probe_degrades_without_invoking(tmp_path):
    calls: list = []
    runner = SandboxRunner(
        _policy(tmp_path), transport=_missing_binary_transport(calls)
    )
    ok, cause = runner.available()
    assert ok is False
    assert cause is not None and "binary-missing" in cause
    with pytest.raises(SandboxUnavailable, match="binary-missing"):
        runner.run(["echo", "hi"])
    # Only the --version probe ran (once per available() call, including the
    # one inside run()); the gate argv never executed.
    assert calls == [["openshell", "--version"]] * len(calls) and calls


def test_real_probe_reports_missing_with_scrubbed_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    runner = SandboxRunner(_policy(tmp_path))
    ok, cause = runner.available()
    assert ok is False
    assert cause is not None and "binary-missing" in cause


def test_unreadable_policy_fails_closed(tmp_path):
    runner = SandboxRunner(
        tmp_path / "missing-policy.yaml",
        transport=_success_transport([]),
        probe=lambda: (True, None),
    )
    with pytest.raises(SandboxUnavailable, match="policy-unreadable"):
        runner.run(["echo", "hi"])


def test_argv_with_shell_metacharacters_passes_as_argv(tmp_path):
    seen: list = []
    runner = SandboxRunner(
        _policy(tmp_path),
        transport=_success_transport(seen),
        probe=lambda: (True, None),
    )
    tricky = ["printf", "%s", "; rm -rf / && $(evil) `backtick` | true"]
    result = runner.run(tricky)
    assert result.argv == tricky
    wrapped = seen[0][0]
    assert wrapped[wrapped.index("--") + 1:] == tricky
    assert all(isinstance(part, str) for part in wrapped)


def test_no_shell_true_in_runner_source():
    source = Path(__file__).resolve().parents[1] / "sandbox" / "runner.py"
    assert "shell=True" not in source.read_text(encoding="utf-8")


def test_build_create_argv_shape():
    argv = build_create_argv("p.yaml", "gate-abc", ["a", "b"])
    assert argv == [
        "openshell", "sandbox", "create", "--name", "gate-abc",
        "--policy", "p.yaml", "--no-auto-providers", "--", "a", "b",
    ]


def test_live_probe_reports_runnable_or_skips():
    if shutil.which("openshell") is None:
        pytest.skip("openshell binary absent")
    ok, cause = probe_openshell()
    assert ok is True
    assert cause is None

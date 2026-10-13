# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for gates-in-sandbox routing and receipt binding (Phase 83, SAND-02).

Hermetic: sandboxed runs go through a stub transport emulating openshell;
binary-absent cases scrub PATH. Green with and without /usr/bin/openshell.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

from omega_prime.mcp_server import default_registry
from omega_prime.receipts import (
    ReceiptError,
    command_digest,
    load_executions,
    validate_receipt,
)
from omega_prime.sandbox.policy_gen import digest_file
from omega_prime.tools.quality import QualityClient, QualityContext


class FakeRunner:
    """Unsandboxed runner double (today's path)."""

    def __init__(self, exits=None):
        self.exits = exits if exits is not None else {}
        self.calls: list = []

    def __call__(self, argv, timeout=120, cwd=None):
        self.calls.append(list(argv))
        return {"exit_code": 0, "stdout": "out", "stderr": ""}


class StubSandboxTransport:
    """Emulated openshell: answers --version, wraps create per behavior."""

    def __init__(self, behavior: str = "success"):
        self.behavior = behavior
        self.calls: list = []
        self.wrapped: list = []

    def __call__(self, argv, timeout=120, cwd=None):
        self.calls.append(list(argv))
        if len(argv) == 2 and argv[1] == "--version":
            return {"exit_code": 0, "stdout": "openshell 0.1.2", "stderr": ""}
        assert argv[:3] == ["openshell", "sandbox", "create"], argv[:3]
        assert "--no-auto-providers" in argv
        self.wrapped.append(argv[argv.index("--") + 1 :])
        if self.behavior == "create-failure":
            raise RuntimeError("sandbox create: policy rejected")
        if self.behavior == "setup-timeout":
            return {
                "exit_code": 127,
                "stdout": "",
                "stderr": "timeout before exec",
                "setup_error": "transport startup failed: timed out",
            }
        if self.behavior == "egress-denied":
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "sandbox: egress denied to api.evil.test:443 "
                "(default-deny policy)",
            }
        if self.behavior == "gate-fails":
            return {"exit_code": 3, "stdout": "FAIL", "stderr": ""}
        return {"exit_code": 0, "stdout": "gate-ok", "stderr": ""}


def _policy(tmp_path, monkeypatch) -> Any:
    policy = tmp_path / "sandbox-policy.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setenv("OMEGA_PRIME_COMMAND_LOG", str(tmp_path / "cmdlog.jsonl"))
    return policy


def _ctx(tmp_path, **overrides: Any) -> QualityContext:
    base: dict[str, Any] = {"root": str(tmp_path), "run": FakeRunner()}
    base.update(overrides)
    return QualityContext(**base)


def _suites(argv: list[str]) -> list[dict]:
    return [{"name": "unit", "argv": argv, "timeout": 30}]


def _receipt_for(entry: dict, bot: str = "bot-01-systems") -> dict:
    return {
        "bot": bot,
        "commands": [
            {
                "cmd": entry["argv"],
                "exit_code": entry["exit_code"],
                "argv_sha256": entry["argv_sha256"],
                "sandbox_policy_sha256": entry["sandbox"]["policy_sha256"],
            }
        ],
        "claims": [{"claim": "unit gate passed", "evidence_command_index": 0}],
        "unverified": [],
    }


# --- flag-off parity: today's exact shape, additive fields absent ---


def test_flag_off_keeps_today_shape(tmp_path):
    client = QualityClient(_ctx(tmp_path))
    result = client.gates_run(suites=_suites(["python3", "-m", "pytest", "-q"]))
    assert result["ok"] is True
    assert set(result["gates"]["unit"]) == {"exit_code", "output"}
    assert result["gates"]["unit"]["exit_code"] == 0


def test_flag_off_variants_keep_today_shape(tmp_path):
    for sandbox in ({}, {"enabled": False}, {"enabled": True, "scope": "everything"}):
        client = QualityClient(_ctx(tmp_path, sandbox=sandbox))
        result = client.gates_run(suites=_suites(["echo", "hi"]))
        assert set(result["gates"]["unit"]) == {"exit_code", "output"}, sandbox


def test_flag_off_non_dict_sandbox_keeps_today_shape(tmp_path):
    client = QualityClient(_ctx(tmp_path, sandbox="yes"))
    result = client.gates_run(suites=_suites(["echo", "hi"]))
    assert set(result["gates"]["unit"]) == {"exit_code", "output"}


# --- flag-on routing with stub transport ---


def test_on_with_stub_routes_under_policy(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    transport = StubSandboxTransport()
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=transport,
        )
    )
    result = client.gates_run(suites=_suites(["python3", "-m", "pytest", "-q"]))
    assert result["ok"] is True
    entry = result["gates"]["unit"]
    assert entry["argv"] == ["python3", "-m", "pytest", "-q"]
    assert entry["argv_sha256"] == command_digest(entry["argv"])
    assert entry["sandbox"]["enabled"] is True
    assert entry["sandbox"]["policy_sha256"] == digest_file(policy)
    assert entry["sandbox"]["degraded"] is None
    assert transport.wrapped == [["python3", "-m", "pytest", "-q"]]
    create = next(
        c for c in transport.calls if c[:3] == ["openshell", "sandbox", "create"]
    )
    assert create[create.index("--policy") + 1] == str(policy)
    records = load_executions()
    assert len(records) == 1
    assert records[0]["cmd"] == ["python3", "-m", "pytest", "-q"]
    assert records[0]["policy_sha256"] == digest_file(policy)


def test_gates_plus_execute_scope_sandboxes_gates(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    transport = StubSandboxTransport()
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={
                "enabled": True,
                "policy": str(policy),
                "scope": "gates+execute",
            },
            sandbox_transport=transport,
        )
    )
    result = client.gates_run(suites=_suites(["echo", "hi"]))
    assert result["ok"] is True
    assert result["gates"]["unit"]["sandbox"]["degraded"] is None
    assert transport.wrapped == [["echo", "hi"]]


def test_gate_failure_binds_exit_and_output(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=StubSandboxTransport("gate-fails"),
        )
    )
    result = client.gates_run(suites=_suites(["make", "check"]))
    assert result["ok"] is False
    entry = result["gates"]["unit"]
    assert entry["exit_code"] == 3
    assert "FAIL" in entry["output"]
    assert entry["sandbox"]["degraded"] is None


def test_network_denial_observable_in_gate_output(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=StubSandboxTransport("egress-denied"),
        )
    )
    result = client.gates_run(suites=_suites(["curl", "https://api.evil.test"]))
    assert result["ok"] is False
    entry = result["gates"]["unit"]
    assert "egress denied" in entry["output"]
    assert entry["sandbox"]["degraded"] is None
    assert entry["sandbox"]["policy_sha256"] == digest_file(policy)


def test_shell_metacharacters_stay_argv(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    transport = StubSandboxTransport()
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=transport,
        )
    )
    tricky = ["printf", "%s", "; rm -rf / && $(evil)"]
    result = client.gates_run(suites=_suites(tricky))
    assert result["gates"]["unit"]["argv"] == tricky
    assert transport.wrapped == [tricky]


# --- enabled-but-absent: explicit degraded receipts, nothing executes ---


def test_absent_binary_degrades_with_cause(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    policy = _policy(tmp_path, monkeypatch)
    calls: list = []

    def spy(argv, timeout=120, cwd=None):
        calls.append(list(argv))
        if list(argv) == ["openshell", "--version"]:
            raise FileNotFoundError("openshell")
        raise AssertionError("gate argv must not execute when the binary is absent")

    marker = tmp_path / "never-created.txt"
    argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"]
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=spy,
        )
    )
    result = client.gates_run(suites=_suites(argv))
    assert result["ok"] is False
    entry = result["gates"]["unit"]
    assert entry["exit_code"] == 127
    assert "sandbox_unavailable" in entry["sandbox"]["degraded"]
    assert "binary-missing" in entry["sandbox"]["degraded"]
    assert entry["argv"] == argv
    assert calls == [["openshell", "--version"]]
    assert not marker.exists()
    assert load_executions() == []


def test_create_failure_degrades_with_cause(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    transport = StubSandboxTransport("create-failure")
    marker = tmp_path / "never-created.txt"
    argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"]
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=transport,
        )
    )
    result = client.gates_run(suites=_suites(argv))
    assert result["ok"] is False
    assert "sandbox_unavailable" in result["gates"]["unit"]["sandbox"]["degraded"]
    assert "create-failed" in result["gates"]["unit"]["sandbox"]["degraded"]
    assert not marker.exists()


def test_setup_timeout_degrades_without_executed_evidence(tmp_path, monkeypatch):
    """Exit 127 from setup is a degraded receipt, never executed evidence."""
    policy = _policy(tmp_path, monkeypatch)
    transport = StubSandboxTransport("setup-timeout")
    argv = ["echo", "hi"]
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=transport,
        )
    )
    result = client.gates_run(suites=_suites(argv))
    assert result["ok"] is False
    entry = result["gates"]["unit"]
    assert entry["exit_code"] == 127
    assert "sandbox_unavailable" in entry["sandbox"]["degraded"]
    assert "create-failed" in entry["sandbox"]["degraded"]
    assert load_executions() == []


# --- receipt binding: tamper-evidence + approve cross-check ---


def test_sandboxed_receipt_validates_and_tamper_rejects(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=StubSandboxTransport(),
        )
    )
    result = client.gates_run(suites=_suites(["echo", "hi"]))
    entry = result["gates"]["unit"]
    receipt = _receipt_for(entry)
    validate_receipt(
        receipt,
        executions=load_executions(),
        expected_policy_sha256=digest_file(policy),
    )

    forged_digest = json.loads(json.dumps(receipt))
    forged_digest["commands"][0]["argv_sha256"] = "0" * 64
    with pytest.raises(ReceiptError, match=r"commands\[0\].*argv_sha256"):
        validate_receipt(
            forged_digest,
            executions=load_executions(),
            expected_policy_sha256=digest_file(policy),
        )

    tampered_argv = json.loads(json.dumps(receipt))
    tampered_argv["commands"][0]["cmd"] = ["echo", "bye"]
    with pytest.raises(ReceiptError, match=r"commands\[0\]"):
        validate_receipt(
            tampered_argv,
            executions=load_executions(),
            expected_policy_sha256=digest_file(policy),
        )

    swapped_policy = json.loads(json.dumps(receipt))
    swapped_policy["commands"][0]["sandbox_policy_sha256"] = "0" * 64
    with pytest.raises(ReceiptError, match=r"commands\[0\].*sandbox_policy_sha256"):
        validate_receipt(
            swapped_policy,
            executions=load_executions(),
            expected_policy_sha256=digest_file(policy),
        )


def test_approve_cross_checks_policy_digest(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=StubSandboxTransport(),
        )
    )
    result = client.gates_run(suites=_suites(["echo", "hi"]))
    good = tmp_path / "good-receipt.json"
    good.write_text(json.dumps(_receipt_for(result["gates"]["unit"])), encoding="utf-8")
    stamped = client.receipt_approve("good-receipt.json", approver="ada")
    assert stamped["ok"] is True

    bad = tmp_path / "swapped-receipt.json"
    receipt = _receipt_for(result["gates"]["unit"])
    receipt["commands"][0]["sandbox_policy_sha256"] = "0" * 64
    bad.write_text(json.dumps(receipt), encoding="utf-8")
    refused = client.receipt_approve("swapped-receipt.json", approver="ada")
    assert "gate_failed" in refused["error"]
    assert any("sandbox_policy_sha256" in line for line in refused["problems"])

    tampered = tmp_path / "tampered-receipt.json"
    receipt = _receipt_for(result["gates"]["unit"])
    receipt["commands"][0]["cmd"] = ["echo", "bye"]
    tampered.write_text(json.dumps(receipt), encoding="utf-8")
    refused_argv = client.receipt_approve("tampered-receipt.json", approver="ada")
    assert "gate_failed" in refused_argv["error"]


def test_unrecorded_commands_still_rejected_with_sandbox_on(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    client = QualityClient(
        _ctx(
            tmp_path,
            sandbox={"enabled": True, "policy": str(policy), "scope": "gates"},
            sandbox_transport=StubSandboxTransport(),
        )
    )
    ghost = tmp_path / "ghost-receipt.json"
    ghost.write_text(
        json.dumps(
            {
                "bot": "bot-01-systems",
                "commands": [{"cmd": ["never", "ran"], "exit_code": 0}],
                "claims": [{"claim": "did it", "evidence_command_index": 0}],
                "unverified": [],
            }
        ),
        encoding="utf-8",
    )
    refused = client.receipt_approve("ghost-receipt.json", approver="ada")
    assert "gate_failed" in refused["error"]


# --- served registration honors OMEGA_PRIME_SANDBOX_ENABLED (P1) ---

ROOT = Path(__file__).resolve().parents[2]


class _RecordingSandboxRunner:
    """Stub SandboxRunner seam: records construction, never executes argv."""

    seen: ClassVar[list] = []

    def __init__(self, policy_path, *, transport=None, **kwargs) -> None:
        self.policy_path = str(policy_path)
        type(self).seen.append(self)

    def available(self):
        return (True, None)

    def run(self, argv, timeout, cwd):
        from omega_prime.sandbox.runner import SandboxResult

        record = list(argv)
        try:
            policy_sha = digest_file(self.policy_path)
        except OSError:
            policy_sha = "unreadable"
        return SandboxResult(
            argv=record,
            argv_sha256=command_digest(record),
            exit_code=0,
            output="sandboxed-ok",
            policy_sha256=policy_sha,
        )


def _served_dispatch(tmp_path, env):
    registry = default_registry(ROOT, tmp_path / "home", env=env)
    return json.loads(
        registry.dispatch(
            "qua_gates_run",
            {
                "suites": [
                    {"name": "unit", "argv": ["no-such-binary-xyz"], "timeout": 30}
                ]
            },
        )
    )


def test_served_registry_routes_gates_through_runner_when_flag_on(
    tmp_path, monkeypatch
):
    _RecordingSandboxRunner.seen.clear()
    monkeypatch.setattr(
        "omega_prime.tools.quality.SandboxRunner", _RecordingSandboxRunner
    )
    monkeypatch.setenv("OMEGA_PRIME_COMMAND_LOG", str(tmp_path / "cmdlog.jsonl"))
    policy = tmp_path / "sandbox-policy.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")
    payload = _served_dispatch(
        tmp_path,
        {
            "OMEGA_PRIME_SANDBOX_ENABLED": "1",
            "OMEGA_PRIME_SANDBOX_POLICY": str(policy),
            "OMEGA_PRIME_SANDBOX_SCOPE": "gates",
        },
    )
    assert _RecordingSandboxRunner.seen, "flag on must route through SandboxRunner"
    assert _RecordingSandboxRunner.seen[0].policy_path == str(policy)
    assert payload["gates"]["unit"]["output"] == "sandboxed-ok"
    assert payload["gates"]["unit"]["sandbox"]["enabled"] is True


def test_served_registry_skips_runner_when_flag_off(tmp_path, monkeypatch):
    _RecordingSandboxRunner.seen.clear()
    monkeypatch.setattr(
        "omega_prime.tools.quality.SandboxRunner", _RecordingSandboxRunner
    )
    payload = _served_dispatch(tmp_path, {})
    assert _RecordingSandboxRunner.seen == []
    assert set(payload["gates"]["unit"]) == {"exit_code", "output"}

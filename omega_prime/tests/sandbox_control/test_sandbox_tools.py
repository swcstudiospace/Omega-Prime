from __future__ import annotations

import json
from pathlib import Path

import pytest
from sbx_fakes import FakeOpenShell, make_config, make_provider

from omega_prime.sandbox_control.provider import OpenShellSandboxProvider
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.sandbox_control import (
    SANDBOX_TOOL_NAMES,
    register_sandbox_control_tools,
)


@pytest.fixture
def fake() -> FakeOpenShell:
    return FakeOpenShell()


@pytest.fixture
def provider(tmp_path: Path, fake: FakeOpenShell) -> OpenShellSandboxProvider:
    return make_provider(make_config(tmp_path), fake)


def test_names_and_approval_flags(provider: OpenShellSandboxProvider) -> None:
    registry = ToolRegistry()
    names = register_sandbox_control_tools(registry, lambda: provider)
    assert tuple(names) == SANDBOX_TOOL_NAMES
    gated = {n for n in names if registry.approval_required(n)}
    assert gated == {"sandbox_create", "sandbox_exec", "sandbox_down"}


def test_mutating_tools_denied_without_approval(
    provider: OpenShellSandboxProvider,
) -> None:
    registry = ToolRegistry()
    register_sandbox_control_tools(registry, lambda: provider)
    for name, args in (
        ("sandbox_create", {"slug": "x"}),
        ("sandbox_exec", {"name": "ompsbx-a-1", "argv": ["true"]}),
        ("sandbox_down", {"name": "ompsbx-a-1"}),
    ):
        assert json.loads(registry.dispatch(name, args))["error"] == "approval required"


def test_read_only_tools_run_and_errors_are_structured(
    provider: OpenShellSandboxProvider,
) -> None:
    registry = ToolRegistry()
    register_sandbox_control_tools(registry, lambda: provider)
    assert json.loads(registry.dispatch("sandbox_list", {})) == {"sandboxes": []}
    status = json.loads(
        registry.dispatch("sandbox_status", {"name": "ompsbx-zz-000000"})
    )
    assert status == {"name": "ompsbx-zz-000000", "exists": False}


def test_approved_create_exec_down_roundtrip(
    provider: OpenShellSandboxProvider,
) -> None:
    log = ApprovalLog()
    for name in ("sandbox_create", "sandbox_exec", "sandbox_down"):
        log.approve(name, "ove")
    registry = ToolRegistry(approval_log=log)
    register_sandbox_control_tools(registry, lambda: provider)
    created = json.loads(registry.dispatch("sandbox_create", {"slug": "demo"}))
    name = created["name"]
    assert created["ready"] is False  # L14 fails closed until #61 supplies a probe
    ran = json.loads(
        registry.dispatch("sandbox_exec", {"name": name, "argv": ["echo", "hi"]})
    )
    assert ran["exit_code"] == 0
    refused = json.loads(
        registry.dispatch(
            "sandbox_exec", {"name": name, "argv": ["true"], "env": {"API_KEY": "x"}}
        )
    )
    assert refused["error"] == "secret_env_refused"
    down = json.loads(registry.dispatch("sandbox_down", {"name": name}))
    assert down["removed"] is True

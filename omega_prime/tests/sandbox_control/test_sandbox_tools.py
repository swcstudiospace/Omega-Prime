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


def test_dashboard_store_creates_valid_rows_with_the_real_helpers(
    tmp_path: Path,
) -> None:
    from omega_prime.dashboard.status_file import read_tasks, write_tasks
    from omega_prime.sandbox_control.events import OWNER_KIND, dashboard_task_store

    path = tmp_path / "tasks.json"
    write_tasks(
        {
            "version": 1,
            "updated_at": "2026-10-31T09:00:00+11:00",
            "tasks": [],
            "waiting_on_ove": [],
            "finished": [],
        },
        path=path,
    )
    store = dashboard_task_store(path)
    assert store is not None
    store.upsert_note("gb:sbx-ompsbx-demo-abc123", "working", "created")
    store.upsert_note("gb:00045", "blocked", "exit=2")
    result = read_tasks(path)
    rows = {row["id"]: row for row in json.loads(path.read_text())["tasks"]}
    assert result.status == "ok", result.error  # the dashboard validator accepts it
    assert rows["gb:00045"]["state"] == "blocked"
    assert rows["gb:00045"]["owner"] == {"kind": OWNER_KIND, "ref": "gb:00045"}


def test_missing_task_store_is_reported_on_every_result(
    tmp_path: Path, fake: FakeOpenShell, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omega_prime.tools.sandbox_control as tools

    monkeypatch.setattr(tools, "dashboard_task_store", lambda _path: None)
    built = tools.default_provider({"HOME": str(tmp_path), "PATH": "/usr/bin"})
    assert built.task_store_available is False
    provider = make_provider(make_config(tmp_path), fake)
    provider.task_store_available = False
    registry = ToolRegistry()
    register_sandbox_control_tools(registry, lambda: provider)
    out = json.loads(registry.dispatch("sandbox_list", {}))
    assert out["task_store"] == "unavailable"


def test_tools_use_the_resolved_env_not_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omega_prime.tools.sandbox_control as tools

    seen: list[dict[str, str] | None] = []
    real = tools.default_provider

    def spy(env: dict[str, str] | None = None) -> OpenShellSandboxProvider:
        seen.append(env)
        return real(env)

    monkeypatch.setattr(tools, "default_provider", spy)
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path / "wrong"))
    state = tmp_path / "configured"
    registry = ToolRegistry()
    register_sandbox_control_tools(
        registry, env={"HOME": str(tmp_path), "OMEGA_PRIME_STATE_DIR": str(state)}
    )
    registry.dispatch("sandbox_list", {})
    assert seen and seen[0] is not None
    assert real(seen[0]).config.state_dir == state

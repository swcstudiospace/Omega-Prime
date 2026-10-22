from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from sbx_fakes import FakeOpenShell, make_config, make_provider

from omega_prime.sandbox_control.events import FanoutSink, OutboxSink
from omega_prime.sandbox_control.provider import (
    OpenShellSandboxProvider,
    SandboxControlError,
    exec_argv,
    host_env,
    owned_name,
    safe_cwd,
)
from omega_prime.sdlc_events.sandbox import ReadinessInput, SandboxLifecycle


def _lifecycles(provider: OpenShellSandboxProvider) -> list[SandboxLifecycle]:
    return [e.lifecycle for e in provider.sink.events]  # type: ignore[attr-defined]


@pytest.fixture
def fake() -> FakeOpenShell:
    return FakeOpenShell()


@pytest.fixture
def provider(tmp_path: Path, fake: FakeOpenShell) -> OpenShellSandboxProvider:
    return make_provider(make_config(tmp_path), fake)


def test_create_without_tools_probe_is_not_ready_and_fails_closed(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    out = provider.create("Fix Login!!", task_id="gb:00045")
    assert out["name"].startswith("ompsbx-fix-login-")
    assert out["phase"] == "Ready"
    assert out["ready"] is False
    assert "tools_probe_missing(#61)" in out["reasons"]
    assert _lifecycles(provider) == [
        SandboxLifecycle.CREATED,
        SandboxLifecycle.NOT_READY,
    ]
    create = next(c for c, _ in fake.calls if c[1:3] == ["sandbox", "create"])
    assert create[create.index("--from") + 1] == "localhost/omp-sandbox:0.1"
    assert {"--detach", "--no-tty", "--no-auto-providers"} <= set(create)


def test_create_ready_when_probe_says_connected(
    provider: OpenShellSandboxProvider,
) -> None:
    provider.readiness_probe = lambda _name: ReadinessInput(
        container_ready=True, tools_connected=True, skills_connected=True
    )
    out = provider.create("demo", task_id="gb:00045")
    assert out["ready"] is True and out["reasons"] == []
    assert _lifecycles(provider)[-1] is SandboxLifecycle.READY


def test_create_not_ready_when_phase_never_ready(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    fake.phase_on_create = "Provisioning"
    ticks = iter(range(100))
    provider.clock = lambda: float(next(ticks))
    provider.readiness_probe = lambda _n: ReadinessInput(
        container_ready=True, tools_connected=True, skills_connected=True
    )
    out = provider.create("demo")
    assert out["ready"] is False and "container_not_ready" in out["reasons"]


@pytest.mark.parametrize(
    ("connected", "policy", "reason"),
    [(False, True, "gateway_not_connected"), (True, False, "policy_missing")],
)
def test_preflight_fails_closed(
    provider: OpenShellSandboxProvider,
    fake: FakeOpenShell,
    connected: bool,
    policy: bool,
    reason: str,
) -> None:
    fake.connected = connected
    if not policy:
        provider.config.policy.unlink()
    with pytest.raises(SandboxControlError) as err:
        provider.create("demo")
    assert err.value.code == "preflight_failed" and reason in err.value.reason
    assert not any(c[1:3] == ["sandbox", "create"] for c, _ in fake.calls)


def test_create_failure_is_redacted_and_reported(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    fake.create_rc = 1
    with pytest.raises(SandboxControlError) as err:
        provider.create("demo", task_id="gb:00045")
    assert "ghp_" not in err.value.reason and "[REDACTED]" in err.value.reason
    assert _lifecycles(provider) == [SandboxLifecycle.NOT_READY]


def test_exec_refuses_unowned_secret_env_and_escaping_cwd(
    provider: OpenShellSandboxProvider,
) -> None:
    with pytest.raises(SandboxControlError, match="not_owned"):
        provider.exec("osp-abc", ["true"])
    with pytest.raises(SandboxControlError, match="secret_env_refused"):
        provider.exec("ompsbx-demo-abc123", ["true"], env={"GITHUB_TOKEN": "x"})
    with pytest.raises(SandboxControlError, match="bad_cwd"):
        provider.exec("ompsbx-demo-abc123", ["true"], cwd="/sandbox/../etc")
    with pytest.raises(SandboxControlError, match="bad_argv"):
        provider.exec("ompsbx-demo-abc123", [])


def test_exec_bounds_and_redacts_output(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    from omega_prime.sandbox_control.provider import RunResult

    fake.exec_result = RunResult(3, "x" * 20000 + " sk-abcdefghijklmnop", "boom")
    out = provider.exec("ompsbx-demo-abc123", ["make", "test"], cwd="/sandbox/work")
    assert out["exit_code"] == 3 and out["truncated"] is True
    assert len(out["stdout"]) == 8000 and "sk-abcdef" not in out["stdout"]
    argv, _ = fake.calls[-1]
    assert argv[-3:] == ["--", "make", "test"]
    assert "--workdir" in argv and argv[argv.index("--workdir") + 1] == "/sandbox/work"
    assert _lifecycles(provider) == []  # ad-hoc exec does not touch lifecycle


def test_exec_report_exit_nonzero_emits_blocking_exit(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    from omega_prime.sandbox_control.provider import RunResult

    assert provider.ownership is not None
    provider.ownership.record("ompsbx-demo-abc123", "gb:00045")
    fake.exec_result = RunResult(2, "", "")
    provider.exec("ompsbx-demo-abc123", ["omp", "--version"], report_exit=True)
    event = provider.sink.events[-1]  # type: ignore[attr-defined]
    assert event.lifecycle is SandboxLifecycle.EXITED and event.exit_code == 2
    assert event.task_id == "gb:00045" and event.blocking


def test_down_deletes_marks_teardown_and_verifies_leftovers(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    name = provider.create("demo", task_id="gb:00045")["name"]
    assert provider.ownership is not None
    out = provider.down(name)
    assert out == {"name": name, "removed": True, "existed": True}
    assert _lifecycles(provider)[-1] is SandboxLifecycle.REMOVED
    assert provider.ownership.get(name) is None


def test_down_reports_leftovers(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    name = provider.create("demo")["name"]
    fake.leftover_containers = [f"openshell-default--{name}-d8eca4fc"]
    with pytest.raises(SandboxControlError, match="leftovers"):
        provider.down(name)
    assert provider.ownership is not None and provider.ownership.teardown(name)


def test_down_refuses_unowned(provider: OpenShellSandboxProvider) -> None:
    with pytest.raises(SandboxControlError, match="not_owned"):
        provider.down("osp-mvgrni40-3a9e4b")


def test_status_and_list(
    provider: OpenShellSandboxProvider, fake: FakeOpenShell
) -> None:
    assert provider.status("ompsbx-missing-000000") == {
        "name": "ompsbx-missing-000000",
        "exists": False,
    }
    name = provider.create("demo", task_id="gb:00045")["name"]
    status = provider.status(name)
    assert status["phase"] == "Ready" and status["owned"] is True
    assert status["task_id"] == "gb:00045"
    assert status["ready_condition"]["reason"] == "DependenciesReady"
    rows = provider.list_sandboxes()
    assert [r["name"] for r in rows] == [name]


def test_outbox_sink_is_private_cloudevents(
    provider: OpenShellSandboxProvider,
) -> None:
    outbox = provider.config.outbox
    provider.sink = FanoutSink([OutboxSink(outbox)])
    provider.create("demo")
    lines = [json.loads(line) for line in outbox.read_text().splitlines()]
    assert [x["type"] for x in lines] == ["op.sbx.created", "op.sbx.not_ready"]
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600


def test_exec_argv_drops_secret_base_env_and_sets_tty() -> None:
    argv = exec_argv("/os", "ompsbx-a-1", ["omp"], tty=True)
    assert "--tty" in argv and "--env" in argv
    assert "TERM=xterm-256color" in argv
    assert not any("TOKEN" in a or "KEY=" in a for a in argv)


def test_owned_name_and_cwd_helpers() -> None:
    assert owned_name("../../Weird Name", "abc123") == "ompsbx-weird-name-abc123"
    assert owned_name("", "abc123") == "ompsbx-task-abc123"
    assert safe_cwd("/sandbox/work/../x") == "/sandbox/x"
    with pytest.raises(SandboxControlError):
        safe_cwd("relative")


def test_host_env_keeps_no_secrets() -> None:
    env = host_env(
        {"HOME": "/h", "PATH": "/bin", "GITHUB_PAT": "x", "ANTHROPIC_API_KEY": "y"}
    )
    assert env == {"HOME": "/h", "PATH": "/bin", "OPENSHELL_TELEMETRY_ENABLED": "false"}


def test_config_from_env_never_falls_back_to_the_operator_home() -> None:
    from omega_prime.sandbox_control.provider import SandboxConfig

    bare = SandboxConfig.from_env({})
    assert bare.openshell == "openshell"
    assert str(bare.state_dir) == ".omega-prime"
    homed = SandboxConfig.from_env({"HOME": "/h", "OMEGA_PRIME_STATE_DIR": "/s"})
    assert homed.openshell == "/h/.local/opt/openshell/0.1.4/bin/openshell"
    assert homed.outbox == Path("/s/events/outbox.jsonl")
    assert homed.policy == Path("/workspace/omp-sandbox/sandbox/policy-podman.yaml")

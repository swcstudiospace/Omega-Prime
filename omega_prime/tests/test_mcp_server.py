"""Phase 36: MCP tool host over the registry."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from omega_prime.mcp_server import (
    SERVER_NAME,
    build_server,
    call_tool_handler,
    default_registry,
    list_tools_handler,
    roster_names,
)
from omega_prime.tools.agent_message import MESSAGING_TOOL_NAMES
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.autonomous import AUTONOMOUS_TOOL_NAMES
from omega_prime.tools.coding import register_coding_tools
from omega_prime.tools.goals import GOAL_TOOL_NAMES
from omega_prime.tools.harness import HARNESS_TOOL_NAMES
from omega_prime.tools.heartbeat import HEARTBEAT_TOOL_NAMES
from omega_prime.tools.infra import InfraClient, InfraContext, register_infra_tools
from omega_prime.tools.prime_runtime import KERNEL_TOOL_NAMES
from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.rlm import RLM_TOOL_NAMES
from omega_prime.tools.ultrathink import (
    UltrathinkClient,
    UltrathinkContext,
    register_ultrathink_tools,
)

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


def _small_registry(tmp_path: Path) -> ToolRegistry:
    registry = ToolRegistry(approval_log=ApprovalLog())
    register_coding_tools(registry, tmp_path)
    register_infra_tools(registry, InfraClient(InfraContext()))
    register_ultrathink_tools(registry, UltrathinkClient(UltrathinkContext()))
    return registry


def test_list_tools_matches_registry_and_roster(tmp_path: Path):
    registry = _small_registry(tmp_path)
    assert build_server(registry).name == SERVER_NAME
    listed = asyncio.run(list_tools_handler(registry)(None, None))
    names = [tool.name for tool in listed.tools]
    assert "read_file" in names and "infra_db_health" in names
    assert "ult_status" in names
    schemas = {tool.name: tool.input_schema for tool in listed.tools}
    assert schemas["read_file"]["type"] == "object"

    gated = asyncio.run(list_tools_handler(registry, ["read_file"])(None, None))
    assert [tool.name for tool in gated.tools] == ["read_file"]


def test_call_tool_dispatches_and_marks_errors(tmp_path: Path):
    registry = _small_registry(tmp_path)
    call = call_tool_handler(registry)

    class Params:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")
    ok = asyncio.run(call(None, Params("read_file", {"path": "note.txt"})))
    assert ok.is_error in (False, None)
    assert json.loads(ok.content[0].text)["content"] == "hi"

    unknown = asyncio.run(call(None, Params("nope", {})))
    assert unknown.is_error is True
    assert "Unknown tool" in unknown.content[0].text

    gated = asyncio.run(
        call(
            None,
            Params(
                "infra_railway_redeploy",
                {"project": "a", "service": "b", "prior_deployment_id": "d"},
            ),
        )
    )
    assert gated.is_error is True
    assert "approval required" in gated.content[0].text


def test_call_tool_null_error_field_is_a_result_not_an_error():
    registry = ToolRegistry()
    registry.register(
        "status_loaded",
        "status payload whose error field is null",
        {"type": "object", "properties": {}},
        lambda: {"loaded": True, "error": None},
    )
    registry.register(
        "status_failed",
        "status payload whose error field is a message",
        {"type": "object", "properties": {}},
        lambda: {"loaded": False, "error": "extension not built"},
    )
    call = call_tool_handler(registry)

    class Params:
        def __init__(self, name):
            self.name = name
            self.arguments = {}

    loaded = asyncio.run(call(None, Params("status_loaded")))
    assert loaded.is_error in (False, None)
    assert json.loads(loaded.content[0].text)["loaded"] is True

    failed = asyncio.run(call(None, Params("status_failed")))
    assert failed.is_error is True


def test_default_registry_serves_the_roster(tmp_path: Path):
    roster = roster_names(
        (OMEGA_PRIME / "contracts" / "tool-rosters" / "omega-prime.yaml").read_text(
            encoding="utf-8"
        )
    )
    registry = default_registry(ROOT, tmp_path)
    listed = asyncio.run(list_tools_handler(registry, roster)(None, None))
    served = [tool.name for tool in listed.tools]
    gated = (
        set(RLM_TOOL_NAMES)
        | set(HARNESS_TOOL_NAMES)
        | set(GOAL_TOOL_NAMES)
        | set(HEARTBEAT_TOOL_NAMES)
        | set(AUTONOMOUS_TOOL_NAMES)
        | set(MESSAGING_TOOL_NAMES)
        | set(KERNEL_TOOL_NAMES)
        | set(REDTEAM_TOOL_NAMES)
    )
    assert served == [name for name in roster if name not in gated]
    # DESK-03: delegate_task is served on the in-process desk parent, even
    # with no provider env (the tool itself answers not_configured then).
    assert "delegate_task" in served


def test_roster_call_refusal_policy_and_env(tmp_path: Path):
    import json as _json

    from omega_prime.policy.policy import SeatPolicy

    registry = _small_registry(tmp_path)
    call = call_tool_handler(registry, ["read_file"])

    class Params:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    refused = asyncio.run(
        call(None, Params("write_file", {"path": "x", "content": "y"}))
    )
    assert refused.is_error is True
    assert "policy forbids write_file" in refused.content[0].text

    policy = SeatPolicy.load(
        OMEGA_PRIME / "contracts" / "policies" / "omega-prime.json"
    )
    guarded = default_registry(ROOT, tmp_path, policy=policy)
    denied = _json.loads(
        guarded.dispatch(
            "write_file",
            {"path": "contracts/tool-rosters/omega-prime.yaml", "content": "x"},
        )
    )
    assert "error" in denied

    log = ApprovalLog()
    assert log.approve("infra_railway_redeploy", "ada").get("approved") is True
    approved = default_registry(ROOT, tmp_path, approval_log=log)
    ran = _json.loads(
        approved.dispatch("infra_railway_redeploy", {"project": "a", "service": "b"})
    )
    assert "approval required" not in ran.get("error", "")

    env = {
        "OMEGA_PRIME_PACKS_JSON": _json.dumps(
            {"demo": {"tools": ["t"], "seats": ["web"]}}
        ),
        "ULTRATHINK_ROOT": str(tmp_path / "ut"),
    }
    pack_log = ApprovalLog()
    pack_log.approve("app_tools_load", "ada")
    wired = default_registry(ROOT, tmp_path, env=env, approval_log=pack_log)
    loaded = _json.loads(
        wired.dispatch("app_tools_load", {"app": "nope", "task_id": "t"})
    )
    assert loaded.get("available") == ["demo"]
    status = _json.loads(wired.dispatch("ult_status", {}))
    assert "not_configured" not in status.get("error", "")


def test_ultrathink_registry_uses_explicit_ship_env(tmp_path: Path, monkeypatch):
    from omega_prime.tools.ultrathink import ULT_TOOL_NAMES

    root = tmp_path / "ultrathink"
    runner_calls = []

    def fake_run(argv, timeout=120):
        runner_calls.append((list(argv), timeout))
        return {
            "exit_code": 0,
            "stdout": '{"ok": true}',
            "stderr": "",
        }

    monkeypatch.setattr("omega_prime.tools.ultrathink._default_run", fake_run)
    monkeypatch.setenv("ULTRATHINK_SHIP", "1")
    log = ApprovalLog()
    assert log.approve("ult_ship_pr", "ada").get("approved") is True
    disabled = default_registry(
        ROOT,
        tmp_path / "home",
        env={"ULTRATHINK_ROOT": str(root)},
        approval_log=log,
    )

    class Params:
        def __init__(self):
            self.name = "ult_ship_pr"
            self.arguments = {"state": "plan.json"}

    denied = asyncio.run(call_tool_handler(disabled)(None, Params()))
    assert denied.is_error is True
    assert "ship_disabled" in denied.content[0].text
    assert runner_calls == []

    listed = asyncio.run(list_tools_handler(disabled, list(ULT_TOOL_NAMES))(None, None))
    assert [tool.name for tool in listed.tools] == list(ULT_TOOL_NAMES)
    disabled_schemas = {
        schema["function"]["name"]: schema["function"]
        for schema in disabled.schemas()
        if schema["function"]["name"] in ULT_TOOL_NAMES
    }

    monkeypatch.delenv("ULTRATHINK_SHIP")
    enabled_log = ApprovalLog()
    assert enabled_log.approve("ult_ship_pr", "ada").get("approved") is True
    enabled = default_registry(
        ROOT,
        tmp_path / "home",
        env={"ULTRATHINK_ROOT": str(root), "ULTRATHINK_SHIP": "true"},
        approval_log=enabled_log,
    )
    ran = asyncio.run(call_tool_handler(enabled)(None, Params()))
    assert ran.is_error in (False, None)
    assert json.loads(ran.content[0].text)["ok"] is True
    assert runner_calls == [
        ([str(root / "bin" / "ultrathink-ship"), "pr", "--state", "plan.json"], 120)
    ]
    enabled_schemas = {
        schema["function"]["name"]: schema["function"]
        for schema in enabled.schemas()
        if schema["function"]["name"] in ULT_TOOL_NAMES
    }
    assert enabled_schemas == disabled_schemas


def test_stdio_roundtrip_through_own_client(tmp_path: Path):
    from omega_prime.tools.mcp_session import mcp_session_call

    command = [
        sys.executable,
        "-m",
        "omega_prime.mcp_server",
        "--root",
        str(ROOT),
        "--home",
        str(tmp_path),
    ]
    called = mcp_session_call(command, "infra_db_health", {}, cwd=str(ROOT), timeout=30)
    assert "error" not in called, called
    text = called["result"]["content"][0]["text"]
    assert json.loads(text)["summary"] == {}


def test_desk_substrate_adapts_graph_tool_results():
    from omega_prime.mcp_server import _DeskSubstrate
    from omega_prime.substrate.client import SubstrateError

    class _Client:
        def __init__(self):
            self.calls = []
            self.fail = False

        def _call_tool(self, name, payload):
            self.calls.append(("_call_tool", name, payload))
            if self.fail:
                raise SubstrateError("parsed tool failed")
            return {"lease_id": "lease-1"}

        def _call_tool_text(self, name, payload):
            self.calls.append(("_call_tool_text", name, payload))
            if self.fail:
                raise SubstrateError("text tool failed")
            return '{"ok":true}'

    client = _Client()
    substrate = _DeskSubstrate(client)
    payload = {"graph_id": "g-1", "node_id": "n1", "lease_id": "lease-1"}
    assert substrate.call_tool("graph_claim", payload) == {
        "content": {"lease_id": "lease-1"}
    }
    assert substrate.call_tool("graph_heartbeat", payload) == {
        "content": {"lease_id": "lease-1"}
    }
    assert substrate.call_tool("graph_release", payload) == {"content": '{"ok":true}'}
    assert substrate.call_tool("graph_complete", payload) == {"content": '{"ok":true}'}
    assert client.calls == [
        ("_call_tool", "graph_claim", payload),
        ("_call_tool", "graph_heartbeat", payload),
        ("_call_tool_text", "graph_release", payload),
        ("_call_tool_text", "graph_complete", payload),
    ]
    client.fail = True
    assert substrate.call_tool("graph_release", payload) == {
        "error": "upstream_error",
        "reason": "text tool failed",
    }
    assert substrate.call_tool("graph_claim", payload) == {
        "error": "upstream_error",
        "reason": "parsed tool failed",
    }


def test_desk_substrate_reports_not_configured_without_transport_request():
    from omega_prime.mcp_server import _DeskSubstrate
    from omega_prime.substrate.client import SubstrateClient

    class _NeverTransport:
        calls = 0

        def post(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("unconfigured client attempted a request")

        def post_mcp(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("unconfigured client attempted a request")

    transport = _NeverTransport()
    client = SubstrateClient(transport=transport)
    substrate = _DeskSubstrate(client)

    assert substrate.call_tool("graph_claim", {"graph_id": "g", "node_id": "n"}) == {
        "error": "not_configured",
        "reason": "substrate gateway is not configured",
    }
    assert substrate.emit({"kind": "note"}) == {
        "error": "not_configured",
        "reason": "substrate gateway is not configured",
    }
    assert transport.calls == 0


def test_call_tool_substrate_docs_search_exports_no_raw_secret():
    from omega_prime.tools.substrate_tools import register_substrate_tools

    token = "ghp_" + "x" * 36

    class _DocsClient:
        def docs_search(self, query: str) -> dict:
            return {
                "ok": True,
                "status": 200,
                "body": {
                    "code": 0,
                    "data": {
                        "chunks": [
                            {
                                "content": f"excerpt {token} " + "y" * 2000,
                                "document_keyword": "guide.md",
                                "dataset_id": "ds-1",
                                "similarity": 0.5,
                            }
                        ]
                    },
                },
            }

    registry = ToolRegistry()
    register_substrate_tools(registry, _DocsClient())
    call = call_tool_handler(registry)

    class Params:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    ok = asyncio.run(call(None, Params("substrate_docs_search", {"query": "q"})))
    assert ok.is_error in (False, None)
    text = ok.content[0].text
    assert token not in text
    assert "retrieval" not in json.loads(text)

    class _BrokenClient:
        def docs_search(self, query: str) -> dict:
            return {"ok": True, "status": 200, "body": {"code": 0}}

    broken = ToolRegistry()
    register_substrate_tools(broken, _BrokenClient())
    bad = asyncio.run(
        call_tool_handler(broken)(None, Params("substrate_docs_search", {"query": "q"}))
    )
    assert bad.is_error is True
    assert "malformed" in bad.content[0].text


def test_P46_R01_registry_preview_uses_actual_policy(tmp_path: Path):
    """AC-OMEGA-V9-01: None policy, no-roster and approval never widen fetch."""
    from omega_prime.policy.policy import SeatPolicy

    doc = {"version": 1, "tools": {}, "paths": {}, "network": {"hosts": ["acme.test"]}}
    registry = default_registry(ROOT, tmp_path, policy=SeatPolicy(doc))
    out = json.loads(
        registry.dispatch("web_preview_check", {"url": "https://other.test/"})
    )
    assert "error" in out
    assert out["error"].split(":")[0] in ("forbidden_host", "upstream_error")
    bare = default_registry(ROOT, tmp_path, policy=None)
    denied = json.loads(
        bare.dispatch("web_preview_check", {"url": "https://acme.test/"})
    )
    assert "not_configured" in denied.get(
        "error", ""
    ) or "forbidden_host" in denied.get("error", "")


def test_P46_R07_missing_binding_returns_not_configured(tmp_path: Path):
    """AC-OMEGA-V9-01: unbound web tools deny before dispatch."""
    from omega_prime.tools.webpack import WebClient, WebContext, register_web_tools

    registry = ToolRegistry(approval_log=ApprovalLog())
    register_web_tools(registry, WebClient(WebContext(root=tmp_path)))
    for tool, args in (
        ("web_preview_check", {"url": "https://acme.test/"}),
        ("web_review_page", {"url": "https://acme.test/", "question": "q"}),
    ):
        out = json.loads(registry.dispatch(tool, args))
        assert "not_configured" in out.get(
            "error", ""
        ) or "connectivity failed" in out.get("error", ""), tool


def test_P46_R07_rebinding_different_transport_refuses(tmp_path: Path):
    """AC-OMEGA-V9-01: a registry binds exactly one dispatch transport."""
    import pytest as _pytest

    from omega_prime.mcp_server import bind_dispatch_transport
    from omega_prime.providers.destination import DestinationTransport

    registry = ToolRegistry(approval_log=ApprovalLog())
    first = DestinationTransport(None)
    bind_dispatch_transport(registry, first)
    bind_dispatch_transport(registry, first)
    with _pytest.raises(ValueError, match="different transport"):
        bind_dispatch_transport(registry, DestinationTransport(None))


def test_P46_R07_bound_dispatch_creates_worker_root(tmp_path: Path):
    """AC-OMEGA-V9-01: dispatch enters a bound root and leaves none behind."""
    from omega_prime.mcp_server import _coordinator_for
    from omega_prime.policy.policy import SeatPolicy

    doc = {"version": 1, "tools": {}, "paths": {}, "network": {"hosts": ["acme.test"]}}
    registry = default_registry(ROOT, tmp_path, policy=SeatPolicy(doc))
    coordinator = _coordinator_for(registry)
    assert coordinator is not None
    out = json.loads(
        registry.dispatch("web_preview_check", {"url": "https://acme.test/"})
    )
    assert "error" in out  # hermetic: no real DNS in unit tier
    assert coordinator.pending == 0
    assert coordinator.transport.current_operation() is None


def test_P46_R07_queue_bound_sixteen(tmp_path: Path):
    """AC-OMEGA-V9-01: dispatch queue limits to 16 pending items."""
    from omega_prime.mcp_server import _coordinator_for
    from omega_prime.policy.policy import SeatPolicy

    doc = {"version": 1, "tools": {}, "paths": {}, "network": {"hosts": ["acme.test"]}}
    registry = default_registry(ROOT, tmp_path, policy=SeatPolicy(doc))
    coordinator = _coordinator_for(registry)
    assert coordinator is not None
    with coordinator.lock:
        coordinator.pending = 16
    try:
        out = json.loads(
            registry.dispatch("web_preview_check", {"url": "https://acme.test/"})
        )
        assert "dispatch queue is full" in out.get("error", "")
    finally:
        with coordinator.lock:
            coordinator.pending = 0


def test_P46_R07_cancelled_operation_aborts_before_dispatch(tmp_path: Path):
    """AC-OMEGA-V9-01: pre-aborted root refuses execution immediately."""
    from omega_prime.mcp_server import bind_dispatch_transport
    from omega_prime.providers.destination import DestinationTransport
    from omega_prime.tools.webpack import WebClient, WebContext, register_web_tools

    transport = DestinationTransport(None)
    registry = ToolRegistry(approval_log=ApprovalLog())
    register_web_tools(
        registry, WebClient(WebContext(root=tmp_path, transport=transport))
    )
    bind_dispatch_transport(registry, transport)

    orig_begin = transport.begin_operation

    def _begin_cancelled(*args, **kwargs):
        root = orig_begin(*args, **kwargs)
        root.abort_handle.abort("cancelled")
        return root

    transport.begin_operation = _begin_cancelled  # type: ignore[method-assign]
    out = json.loads(
        registry.dispatch("web_preview_check", {"url": "https://acme.test/"})
    )
    assert "operation was cancelled" in out.get("error", "")


def test_quality_sandbox_default_policy_survives_work_root_change(
    tmp_path: Path, monkeypatch
) -> None:
    """The default policy resolves to the packaged file, not the cwd."""
    from omega_prime.mcp_server import (
        _packaged_sandbox_policy,
        _quality_sandbox_from_env,
    )

    packaged = Path(_packaged_sandbox_policy())
    assert packaged.is_absolute()
    assert packaged.is_file()
    assert packaged == OMEGA_PRIME / "hosting" / "openshell" / "sandbox-policy.yaml"
    monkeypatch.chdir(tmp_path)
    node = _quality_sandbox_from_env({"OMEGA_PRIME_SANDBOX_ENABLED": "1"})
    assert node is not None
    assert node["policy"] == str(packaged)
    assert node["scope"] == "gates"
    explicit = _quality_sandbox_from_env(
        {
            "OMEGA_PRIME_SANDBOX_ENABLED": "1",
            "OMEGA_PRIME_SANDBOX_POLICY": "/tmp/custom-policy.yaml",
        }
    )
    assert explicit is not None
    assert explicit["policy"] == "/tmp/custom-policy.yaml"
    assert _quality_sandbox_from_env({}) is None


def test_default_registry_exact_plugin_gate_and_passive_driver(
    tmp_path: Path, monkeypatch
):
    from omega_prime.tools import plugins

    loaded: list[tuple[Path, ToolRegistry]] = []
    gates: list[dict] = []
    predicate = plugins.plugins_enabled

    def enabled(env):
        gates.append(dict(env))
        return predicate(env)

    monkeypatch.setattr(plugins, "plugins_enabled", enabled)
    monkeypatch.setattr(
        plugins,
        "load_plugins",
        lambda home, registry: loaded.append((Path(home), registry)),
    )
    log = ApprovalLog()
    registries = [
        default_registry(
            ROOT,
            tmp_path / f"home-{value}",
            env={"OMEGA_PRIME_PLUGINS": value},
            approval_log=log,
        )
        for value in ("", "true", "1")
    ]
    assert [env["OMEGA_PRIME_PLUGINS"] for env in gates] == ["", "true", "1"]
    assert loaded == [(tmp_path / "home-1", registries[2])]
    for registry in registries:
        driver = registry.runtime_bindings["desk_driver"]
        assert driver._thread is None
        assert driver.approval_log is log
        assert driver.parent is None
        assert not driver.can_apply_pin_bump()
    assert log.approve("unrelated", "human")["approved"] is True
    assert not registries[0].runtime_bindings["desk_driver"].can_apply_pin_bump()
    assert log.approve("pin_bump_apply", "human")["approved"] is True
    assert registries[0].runtime_bindings["desk_driver"].can_apply_pin_bump()
    assert log.revoke("pin_bump_apply")
    assert not registries[0].runtime_bindings["desk_driver"].can_apply_pin_bump()


def test_exec_jobs_are_registry_local_and_denials_have_no_start_side_effect(
    tmp_path: Path, monkeypatch
):
    from omega_prime.agent.modes import apply_plan_mode
    from omega_prime.policy.policy import SeatPolicy
    from omega_prime.tools import exec_jobs

    starts: list[tuple[exec_jobs.JobControl, list[str]]] = []

    def fake_start(control, argv):
        starts.append((control, argv))
        return "job-1"

    monkeypatch.setattr(exec_jobs.JobControl, "start", fake_start)
    log = ApprovalLog()
    work = tmp_path / "workspace"
    work.mkdir()
    first = default_registry(ROOT, tmp_path, approval_log=log, work_root=work)
    second = default_registry(ROOT, tmp_path, approval_log=log, work_root=work)
    assert {
        schema["function"]["name"]
        for schema in first.schemas()
        if schema["function"]["name"].startswith("exec_job_")
    } == {"exec_job_start", "exec_job_poll", "exec_job_wait", "exec_job_kill"}

    for name, arguments in (
        ("exec_job_start", {"argv": ["echo", "x"]}),
        ("exec_job_poll", {"job_id": "job-1"}),
        ("exec_job_wait", {"job_id": "job-1"}),
        ("exec_job_kill", {"job_id": "job-1"}),
    ):
        denied = json.loads(first.dispatch(name, arguments))
        assert denied["error"] == "approval required"
    policy = SeatPolicy(
        {"version": 1, "tools": {"allow": []}, "paths": {}, "network": {}}
    )
    refused = default_registry(ROOT, tmp_path, policy=policy, approval_log=log)
    assert (
        "policy forbids"
        in json.loads(refused.dispatch("exec_job_start", {"argv": ["echo", "x"]}))[
            "error"
        ]
    )
    call = call_tool_handler(first, [])

    class Params:
        def __init__(self) -> None:
            self.name = "exec_job_start"
            self.arguments = {"argv": ["echo", "roster-refused"]}

    roster_denied = asyncio.run(call(None, Params()))
    assert "policy forbids" in roster_denied.content[0].text
    assert starts == []

    assert log.approve("exec_job_start", "human")["approved"] is True
    assert json.loads(first.dispatch("exec_job_start", {"argv": ["echo", "x"]})) == {
        "id": "job-1"
    }
    assert json.loads(second.dispatch("exec_job_start", {"argv": ["echo", "y"]})) == {
        "id": "job-1"
    }
    assert len(starts) == 2 and starts[0][0] is not starts[1][0]
    assert all(control.cwd == str(work) for control, _argv in starts)
    plan_calls: list[str] = []
    plan_tools = apply_plan_mode(
        {
            name: (lambda **_kwargs: plan_calls.append("called"))
            for name in ("exec_job_start", "exec_job_kill")
        }
    )
    plan_tools["exec_job_start"](argv=["echo", "z"])
    plan_tools["exec_job_kill"](job_id="job-1")
    assert plan_calls == []
    assert len(starts) == 2


def test_stdio_desk_driver_stops_once_after_normal_or_error(
    tmp_path: Path, monkeypatch
):
    from types import SimpleNamespace

    import omega_prime.mcp_server as mcp_server

    calls: list[str] = []

    class Driver:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        def start(self):
            self.started += 1

        def stop(self):
            self.stopped += 1

    driver = Driver()
    job_control = SimpleNamespace(shutdown=lambda: calls.append("shutdown"))
    registry = SimpleNamespace(
        runtime_bindings={
            "desk_driver": driver,
            "exec_job_control": job_control,
        }
    )
    runtime = SimpleNamespace(registry=registry, roster=None)
    monkeypatch.setattr(mcp_server, "load_runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(mcp_server, "_desk_driver_of", lambda _runtime: driver)
    monkeypatch.setattr(mcp_server, "build_server", lambda *args, **kwargs: object())
    monkeypatch.setattr(mcp_server, "_serve", lambda *_args: None)
    monkeypatch.setattr(mcp_server.asyncio, "run", lambda *_args: None)
    assert (
        mcp_server.main(["--root", str(ROOT), "--home", str(tmp_path), "--no-roster"])
        == 0
    )
    assert driver.started == driver.stopped == 1
    assert calls == ["shutdown"]


def test_stdio_desk_driver_stops_once_when_serve_raises(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    import omega_prime.mcp_server as mcp_server

    calls: list[str] = []

    class Driver:
        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    driver = Driver()
    job_control = SimpleNamespace(shutdown=lambda: calls.append("shutdown"))
    runtime = SimpleNamespace(
        registry=SimpleNamespace(
            runtime_bindings={
                "desk_driver": driver,
                "exec_job_control": job_control,
            }
        ),
        roster=None,
    )
    monkeypatch.setattr(mcp_server, "load_runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(mcp_server, "_desk_driver_of", lambda _runtime: driver)
    monkeypatch.setattr(mcp_server, "build_server", lambda *args, **kwargs: object())

    def fail(*_args, **_kwargs):
        raise RuntimeError("stdio failed")

    monkeypatch.setattr(mcp_server, "_serve", fail)
    try:
        mcp_server.main(["--root", str(ROOT), "--home", str(tmp_path), "--no-roster"])
    except RuntimeError as exc:
        assert str(exc) == "stdio failed"
    else:
        raise AssertionError("serve failure should propagate")
    assert calls == ["start", "stop", "shutdown"]


def test_load_runtime_preserves_settings_status_and_explicit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omega_prime.mcp_server import load_runtime
    from omega_prime.settings import load_settings

    status = load_settings({}, path=tmp_path / "absent-settings.json")
    monkeypatch.setenv("SUBSTRATE_URL", "https://ambient.example")
    runtime = load_runtime(
        ROOT,
        tmp_path,
        no_roster=True,
        env={},
        work_root=tmp_path,
        settings_status=status,
    )
    assert runtime.settings_status is status
    assert runtime.registry.runtime_bindings["settings_status"] is status
    doctor = json.loads(runtime.registry.dispatch("lead_doctor", {"action": "check"}))
    assert doctor["checks"]["settings"]["status"] == "missing"


def test_main_approval_validation_uses_effective_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import hashlib

    from omega_prime.mcp_server import main

    settings_home = tmp_path / "home"
    settings_dir = settings_home / ".omega-prime"
    settings_dir.mkdir(parents=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text(
        json.dumps({"OMEGA_PRIME_STATE_DIR": str(tmp_path / "saved-state")}),
        encoding="utf-8",
    )
    settings_file.chmod(0o600)
    process_state = tmp_path / "process-state"
    process_state.mkdir()
    monkeypatch.setenv("HOME", str(settings_home))
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(process_state))
    approvals = process_state / "approvals.txt"
    content = "read_file:admin\n"
    approvals.write_text(content, encoding="utf-8")
    approvals.chmod(0o600)

    result = main(
        [
            "--root",
            str(OMEGA_PRIME),
            "--approvals-file",
            str(approvals),
            "--approvals-sha256",
            hashlib.sha256(content.encode()).hexdigest(),
        ]
    )

    assert result == 2
    assert "outside OMEGA_PRIME_STATE_DIR" in capsys.readouterr().err

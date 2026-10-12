"""Phase 36: MCP tool host over the registry."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

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

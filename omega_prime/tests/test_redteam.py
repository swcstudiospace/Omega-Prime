"""Red team family: T3MP3ST loopback tools behind a fake transport."""

from __future__ import annotations

import functools
import json
from typing import Any

from omega_prime.config import load_config, redteam_enabled
from omega_prime.grokbot.interceptors import ToolCall, run_tool_call
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.redteam import (
    REDTEAM_TOOL_NAMES,
    RedteamClient,
    RedteamContext,
    register_redteam_tools,
    resolve_bearer_token,
)
from omega_prime.tools.registry import ToolRegistry

APPROVALS = (
    "tempest_mission_start",
    "tempest_mission_stop",
    "tempest_operator_task",
    "tempest_hat_dispatch",
)


class FakeTransport:
    """Records every request; answers from canned (method, path) routes."""

    def __init__(self, routes: dict | None = None, error: Exception | None = None):
        self.routes = routes if routes is not None else {}
        self.error = error
        self.bases: list[str] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.header_lines: list[dict[str, str]] = []

    def __call__(
        self,
        base_url: str,
        method: str,
        path: str,
        payload: Any,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, Any]:
        self.bases.append(base_url)
        self.calls.append((method, path, payload))
        self.header_lines.append(dict(headers))
        if self.error is not None:
            raise self.error
        route = self.routes.get((method, path))
        if route is None and "?" in path:
            route = self.routes.get((method, path.split("?", 1)[0]))
        if route is None:
            return 404, {"ok": False, "error": f"no route for {method} {path}"}
        return route


def _routes(**overrides) -> dict:
    routes: dict[Any, Any] = {
        ("GET", "/api/mission/status"): (200, {"mission": "m-1", "active": True}),
        ("GET", "/api/llm/status"): (200, {"model": "child", "live": True}),
        ("GET", "/api/oauth/status"): (200, {"sessions": 1}),
        ("GET", "/api/mission/report"): (200, {"summary": "clean"}),
        ("GET", "/api/operators/list"): (200, {"operators": [{"id": "op-1"}]}),
        ("GET", "/api/findings"): (200, {"findings": []}),
        ("GET", "/api/teams/tool-grants"): (
            200,
            {"grants": {"black": ["gate"], "blue": ["detect"], "recon": ["scan"]}},
        ),
        ("GET", "/api/operators/prompts"): (
            200,
            {"operators": [{"archetype": "recon"}]},
        ),
        ("POST", "/api/mission/start"): (200, {"missionId": "m-1"}),
        ("POST", "/api/mission/stop"): (200, {}),
        ("POST", "/api/teams/purple/synthesize"): (200, {"posture": "mixed"}),
        ("POST", "/api/operators/spawn"): (200, {"operator": {"id": "op-1"}}),
        ("POST", "/api/operators/op-1/task"): (200, {"accepted": True}),
    }
    routes.update(overrides)
    return routes


def _client(transport=None, **overrides) -> tuple[RedteamClient, FakeTransport]:
    transport = transport if transport is not None else FakeTransport(_routes())
    overrides.setdefault("gateway_url", "http://127.0.0.1:3333")
    ctx = RedteamContext(request=transport, **overrides)
    return RedteamClient(ctx), transport


def _registry(client: RedteamClient) -> ToolRegistry:
    registry = ToolRegistry(approval_log=ApprovalLog())
    register_redteam_tools(registry, client)
    return registry


def test_registration_returns_exactly_the_eleven_names_with_approval_flags():
    client, _ = _client()
    registry = ToolRegistry(approval_log=ApprovalLog())
    names = register_redteam_tools(registry, client)
    assert names == list(REDTEAM_TOOL_NAMES)
    assert len(names) == 11
    for name in REDTEAM_TOOL_NAMES:
        assert registry.approval_required(name) == (name in APPROVALS)


def test_dispatch_routes_gets_per_the_contract_table():
    client, transport = _client()
    registry = _registry(client)
    status = json.loads(registry.dispatch("tempest_status", {}))
    assert status["tool"] == "tempest_status"
    assert status["mission"]["data"]["mission"] == "m-1"
    assert status["llm"]["data"]["live"] is True
    assert transport.calls == [
        ("GET", "/api/mission/status", None),
        ("GET", "/api/llm/status", None),
    ]
    transport.calls.clear()
    listed = json.loads(registry.dispatch("tempest_operators_list", {}))
    assert listed["data"]["operators"] == [{"id": "op-1"}]
    assert transport.calls == [("GET", "/api/operators/list", None)]
    transport.calls.clear()
    report = json.loads(registry.dispatch("tempest_report", {}))
    assert report["data"]["summary"] == "clean"
    assert transport.calls == [("GET", "/api/mission/report", None)]
    transport.calls.clear()
    quoted = json.loads(registry.dispatch("tempest_report", {"missionId": "m/9"}))
    assert quoted["status"] == 404  # route not stubbed; the call shape is the point
    assert transport.calls == [("GET", "/api/mission/m%2F9/report", None)]
    transport.calls.clear()
    filtered = json.loads(registry.dispatch("tempest_findings", {"missionId": "m-1"}))
    assert filtered["ok"] is True
    assert transport.calls == [("GET", "/api/findings?missionId=m-1", None)]
    transport.calls.clear()
    plain = json.loads(registry.dispatch("tempest_findings", {}))
    assert plain["ok"] is True
    assert transport.calls == [("GET", "/api/findings", None)]


def test_dispatch_routes_posts_per_the_contract_table():
    log = ApprovalLog()
    for name in (
        "tempest_mission_start",
        "tempest_mission_stop",
        "tempest_operator_task",
    ):
        log.approve(name, "tester")
    client, transport = _client()
    registry = ToolRegistry(approval_log=log)
    register_redteam_tools(registry, client)
    started = json.loads(
        registry.dispatch(
            "tempest_mission_start",
            {"name": "n", "targets": [{"host": "example.com"}], "operators": ["recon"]},
        )
    )
    assert started["data"]["missionId"] == "m-1"
    assert transport.calls == [
        (
            "POST",
            "/api/mission/start",
            {"name": "n", "targets": [{"host": "example.com"}], "operators": ["recon"]},
        )
    ]
    transport.calls.clear()
    bare = json.loads(registry.dispatch("tempest_mission_start", {}))
    assert bare["ok"] is True
    assert transport.calls == [("POST", "/api/mission/start", {})]
    transport.calls.clear()
    stopped = json.loads(registry.dispatch("tempest_mission_stop", {}))
    assert stopped["ok"] is True
    assert transport.calls == [("POST", "/api/mission/stop", {})]
    transport.calls.clear()
    tasked = json.loads(
        registry.dispatch(
            "tempest_operator_task",
            {"operatorId": "op-1", "taskName": "t", "taskDescription": "d"},
        )
    )
    assert tasked["data"]["accepted"] is True
    assert transport.calls == [
        ("POST", "/api/operators/op-1/task", {"taskName": "t", "taskDescription": "d"})
    ]
    transport.calls.clear()
    missing = json.loads(registry.dispatch("tempest_operator_task", {"taskName": "t"}))
    assert missing == {"ok": False, "error": "operatorId required"}
    assert transport.calls == []
    transport.calls.clear()
    purple = json.loads(
        registry.dispatch(
            "tempest_purple_synthesize",
            {"findings": [{"id": "f-1"}], "detections": [{"gap": "d-1"}]},
        )
    )
    assert purple["data"]["posture"] == "mixed"
    assert transport.calls == [
        (
            "POST",
            "/api/teams/purple/synthesize",
            {"findings": [{"id": "f-1"}], "detections": [{"gap": "d-1"}]},
        )
    ]
    transport.calls.clear()
    empty = json.loads(registry.dispatch("tempest_purple_synthesize", {}))
    assert empty["ok"] is True
    assert transport.calls == [
        ("POST", "/api/teams/purple/synthesize", {"findings": [], "detections": []})
    ]


def test_providers_composes_llm_and_oauth_gets():
    client, transport = _client()
    registry = _registry(client)
    body = json.loads(registry.dispatch("tempest_providers", {}))
    assert body == {
        "tool": "tempest_providers",
        "llm": {"ok": True, "status": 200, "data": {"model": "child", "live": True}},
        "oauth": {"ok": True, "status": 200, "data": {"sessions": 1}},
    }
    assert transport.calls == [
        ("GET", "/api/llm/status", None),
        ("GET", "/api/oauth/status", None),
    ]


def test_hats_list_projects_teams_and_hats_from_the_responses():
    client, transport = _client()
    registry = _registry(client)
    body = json.loads(registry.dispatch("tempest_hats_list", {}))
    assert body == {
        "tool": "tempest_hats_list",
        "ok": True,
        "teams": [
            {"team": "black", "grants": ["gate"]},
            {"team": "blue", "grants": ["detect"]},
        ],
        "hats": [{"hat": "recon", "grants": ["scan"], "hasPrompt": True}],
    }
    assert transport.calls == [
        ("GET", "/api/teams/tool-grants", None),
        ("GET", "/api/operators/prompts", None),
    ]


def test_hats_list_reports_a_partial_failure_without_projecting():
    routes = _routes()
    routes[("GET", "/api/operators/prompts")] = (503, {"error": "down"})
    client, _ = _client(FakeTransport(routes))
    body = client.hats_list()
    assert body["ok"] is False
    assert body["prompts"] == {"ok": False, "status": 503, "data": {"error": "down"}}
    assert body["grants"]["ok"] is True
    assert "teams" not in body
    assert "hats" not in body


def test_hat_dispatch_spawns_then_tasks_the_spawned_operator():
    client, transport = _client()
    log = ApprovalLog()
    log.approve("tempest_hat_dispatch", "tester")
    registry = ToolRegistry(approval_log=log)
    register_redteam_tools(registry, client)
    body = json.loads(
        registry.dispatch(
            "tempest_hat_dispatch",
            {"hat": "recon", "task": "scan the edge", "missionId": "m-1"},
        )
    )
    assert body["ok"] is True
    assert body["hat"] == "recon"
    assert body["operatorId"] == "op-1"
    assert body["result"]["data"]["accepted"] is True
    assert transport.calls == [
        ("POST", "/api/operators/spawn", {"archetype": "recon"}),
        (
            "POST",
            "/api/operators/op-1/task",
            {
                "taskName": "scan the edge",
                "taskDescription": "scan the edge (mission m-1)",
            },
        ),
    ]


def test_hat_dispatch_truncates_task_name_and_skips_the_mission_note():
    client, transport = _client()
    log = ApprovalLog()
    log.approve("tempest_hat_dispatch", "tester")
    registry = ToolRegistry(approval_log=log)
    register_redteam_tools(registry, client)
    body = json.loads(
        registry.dispatch("tempest_hat_dispatch", {"hat": "recon", "task": "x" * 200})
    )
    assert body["ok"] is True
    assert transport.calls[1][2] == {
        "taskName": "x" * 120,
        "taskDescription": "x" * 200,
    }


def test_hat_dispatch_reports_a_spawn_failure_without_tasking():
    routes = _routes()
    routes[("POST", "/api/operators/spawn")] = (500, {"error": "unknown hat"})
    transports = (
        FakeTransport(routes),
        FakeTransport(_routes(), error=TimeoutError("slow")),
        FakeTransport(_routes(), error=ConnectionRefusedError("refused")),
    )
    for transport in transports:
        client, _ = _client(transport)
        log = ApprovalLog()
        log.approve("tempest_hat_dispatch", "tester")
        registry = ToolRegistry(approval_log=log)
        register_redteam_tools(registry, client)
        arguments = {"hat": "ghost", "task": "go"}
        call = ToolCall("tempest_hat_dispatch", arguments, None)
        payload, outcome = run_tool_call(
            (),
            call,
            functools.partial(registry.dispatch, "tempest_hat_dispatch", arguments),
        )
        body = json.loads(payload)
        assert body["ok"] is False
        assert body["stage"] == "spawn"
        assert body["error"].startswith("spawn_failed:")
        assert body["result"]["ok"] is False
        assert outcome.is_error is True
        assert transport.calls == [
            ("POST", "/api/operators/spawn", {"archetype": "ghost"})
        ]
    bare = client.hat_dispatch(task="go")
    assert bare == {"ok": False, "error": "hat required"}
    assert client.hat_dispatch(hat="recon") == {"ok": False, "error": "task required"}


def test_hat_dispatch_reports_a_spawn_without_an_operator_id():
    routes = _routes()
    routes[("POST", "/api/operators/spawn")] = (200, {})
    client, transport = _client(FakeTransport(routes))
    body = client.hat_dispatch(hat="recon", task="go")
    assert body == {
        "tool": "tempest_hat_dispatch",
        "ok": False,
        "stage": "spawn",
        "error": "spawn answered without an operator id",
        "result": {"ok": True, "status": 200, "data": {}},
    }
    assert len(transport.calls) == 1


class _TaskStageTimeout(FakeTransport):
    """Answers the spawn, then times out on the operator task request."""

    def __call__(self, base_url, method, path, payload, headers, timeout):
        if path.endswith("/task"):
            self.calls.append((method, path, payload))
            raise TimeoutError("slow")
        return super().__call__(base_url, method, path, payload, headers, timeout)


def test_hat_dispatch_surfaces_a_task_failure_after_a_successful_spawn():
    http_routes = _routes()
    http_routes[("POST", "/api/operators/op-1/task")] = (502, {"error": "busy"})
    for transport in (FakeTransport(http_routes), _TaskStageTimeout(_routes())):
        client, _ = _client(transport)
        log = ApprovalLog()
        log.approve("tempest_hat_dispatch", "tester")
        registry = ToolRegistry(approval_log=log)
        register_redteam_tools(registry, client)
        arguments = {"hat": "recon", "task": "scan the edge"}
        call = ToolCall("tempest_hat_dispatch", arguments, None)
        payload, outcome = run_tool_call(
            (),
            call,
            functools.partial(registry.dispatch, "tempest_hat_dispatch", arguments),
        )
        body = json.loads(payload)
        assert body["ok"] is False
        assert body["stage"] == "task"
        assert body["error"].startswith("task_failed:")
        assert body["operatorId"] == "op-1"
        assert body["result"]["ok"] is False
        assert outcome.is_error is True
        assert [path for _, path, _ in transport.calls] == [
            "/api/operators/spawn",
            "/api/operators/op-1/task",
        ]


def test_non_loopback_gateway_is_refused_without_a_bearer():
    transport = FakeTransport(_routes())
    client = RedteamClient(
        RedteamContext(gateway_url="http://10.9.8.7:3333", request=transport)
    )
    registry = _registry(client)
    row = json.loads(registry.dispatch("tempest_status", {}))
    assert row["error"].startswith("non_loopback_refused:")
    assert "bearer" in row["error"]
    assert transport.calls == []
    bare = client.mission_stop()
    assert bare["error"].startswith("non_loopback_refused:")
    assert "T3MP3ST_BEARER_TOKEN" in bare["error"]


def test_non_loopback_gateway_with_a_bearer_is_allowed():
    client, transport = _client(
        FakeTransport(_routes()),
        gateway_url="http://10.9.8.7:3333",
        bearer_token="tok",
    )
    body = client.operators_list()
    assert body["ok"] is True
    assert transport.bases == ["http://10.9.8.7:3333"]


def test_bearer_is_sent_as_an_authorization_header_when_set():
    client, transport = _client(bearer_token="sekrit")
    registry = _registry(client)
    json.loads(registry.dispatch("tempest_operators_list", {}))
    assert transport.header_lines == [{"Authorization": "Bearer sekrit"}]
    plain_client, plain_transport = _client()
    json.loads(_registry(plain_client).dispatch("tempest_operators_list", {}))
    assert plain_transport.header_lines == [{}]


def test_unreachable_gateway_degrades_to_an_error_row():
    client, transport = _client(
        FakeTransport(_routes(), error=ConnectionRefusedError("refused"))
    )
    registry = _registry(client)
    row = json.loads(registry.dispatch("tempest_operators_list", {}))
    assert row["error"].startswith("gateway_unreachable:")
    assert "127.0.0.1:3333" in row["error"]
    transport.calls.clear()
    composed = json.loads(registry.dispatch("tempest_status", {}))
    assert composed["mission"]["error"].startswith("gateway_unreachable:")
    assert composed["llm"]["error"].startswith("gateway_unreachable:")
    slow_client, _ = _client(FakeTransport(_routes(), error=TimeoutError("slow")))
    slow = slow_client.providers()
    assert slow["llm"]["error"].startswith("gateway_unreachable:")


def test_resolve_bearer_token_env_wins_over_the_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n", encoding="utf-8")

    def read_text(path: str) -> str:
        if path != str(token_file):
            raise OSError(f"no such file: {path}")
        return token_file.read_text(encoding="utf-8")

    assert (
        resolve_bearer_token(
            {
                "T3MP3ST_BEARER_TOKEN": " from-env ",
                "T3MP3ST_BEARER_TOKEN_FILE": "/nope",
            },
            read_text,
        )
        == "from-env"
    )
    assert (
        resolve_bearer_token({"T3MP3ST_BEARER_TOKEN_FILE": str(token_file)}, read_text)
        == "file-token"
    )
    assert resolve_bearer_token({"T3MP3ST_BEARER_TOKEN_FILE": "/nope"}, read_text) == ""
    assert resolve_bearer_token({}, read_text, str(token_file)) == "file-token"
    assert resolve_bearer_token({}, read_text) == ""


def test_redteam_disabled_by_default_and_enabled_by_env(monkeypatch):
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_ENABLED", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_GATEWAY_URL", raising=False)
    config = load_config()
    assert redteam_enabled(config) is False
    assert config["redteam"] == {
        "enabled": False,
        "gateway_url": "http://127.0.0.1:3333",
        "bearer_token_file": "",
        "kill": False,
        "roe_required": True,
        "roe_file": "",
        "roe_key": "",
    }
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ENABLED", "1")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_GATEWAY_URL", "http://127.0.0.1:4444")
    config = load_config()
    assert redteam_enabled(config) is True
    assert config["redteam"]["gateway_url"] == "http://127.0.0.1:4444"

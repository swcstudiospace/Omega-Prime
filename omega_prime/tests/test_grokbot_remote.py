# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the remote SSE MCP host: fail-closed startup, auth, CORS, probes."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import signal
import socket
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

from omega_prime.grokbot.audit import GrokBotAuditTracer
from omega_prime.grokbot.interceptors import ToolCall, ToolOutcome
from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.grokbot.remote import (
    ACCESS_LOGGER_NAME,
    GracefulServer,
    StripQueryFromAccessLog,
    create_mcp_sse_app,
    create_sse_app,
    install_access_log_filter,
    serve_sse,
)
from omega_prime.grokbot.security import AuthMiddleware, SecurityConfigError
from omega_prime.mcp_server import RuntimeConfigError

ROOT = find_repo_root()
TOKEN = "secure-key-12345"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
JSON_POST = {**BEARER, "Content-Type": "application/json"}


def _app(tmp_path: Path, **kwargs: Any) -> Starlette:
    """An app with every side effect (audit file, home) inside `tmp_path`."""
    kwargs.setdefault("allowed_hosts", ["testserver"])
    kwargs.setdefault("env", {})
    kwargs.setdefault("audit", GrokBotAuditTracer(tmp_path / "audit" / "audit.jsonl"))
    kwargs.setdefault("home", tmp_path / "home")
    return create_mcp_sse_app(root=ROOT, **kwargs)


def _middleware_types(app: Starlette) -> list[Any]:
    return [item.cls for item in app.user_middleware]


def _copy_contracts(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(
        ROOT / "omega_prime" / "contracts", repo / "omega_prime" / "contracts"
    )
    return repo


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_mcp_sse_health_endpoints(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)

    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["service"] == "omega-prime-mcp-server"
    assert data["version"] == "6.0.0"
    assert data["package_version"]
    assert data["auth"] == "disabled"
    assert data["rostered_tools"] > 100
    assert data["rostered_tools"] == len(app.state.tool_names)

    res_ready = client.get("/readyz")
    assert res_ready.status_code == 200
    ready = res_ready.json()
    assert ready["ready"] is True
    assert ready["checks"] == {
        "registry": "ok",
        "roster": "ok",
        "policy": "ok",
        "audit": "ok",
        "shutdown": "ok",
        "token_store": "ok",
        "streamable_http": "ok",
    }


def test_mcp_sse_no_roster_serves_every_registered_tool(tmp_path: Path) -> None:
    rostered = _app(tmp_path)
    everything = _app(tmp_path, no_roster=True)
    assert len(everything.state.tool_names) >= len(rostered.state.tool_names)
    ready = TestClient(everything).get("/readyz").json()
    assert ready["checks"]["roster"] == "disabled"


def test_mcp_sse_state_exposes_runtime_objects(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert app.state.tool_names == app.state.runtime.tool_names
    assert app.state.shutting_down is False
    assert app.state.in_flight.count == 0
    assert app.state.gate.limit == 1
    assert app.state.audit.log_path == tmp_path / "audit" / "audit.jsonl"


def test_mcp_sse_auth_rejected(tmp_path: Path) -> None:
    app = _app(tmp_path, token=TOKEN)
    client = TestClient(app)

    # Health check works without auth
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["auth"] == "required"

    # SSE endpoint requires auth
    res_sse = client.get("/sse")
    assert res_sse.status_code == 401
    assert res_sse.json()["error"] == "unauthorized"
    assert res_sse.headers["www-authenticate"].startswith("Bearer")

    # Invalid Bearer token
    res_bad = client.get("/sse", headers={"Authorization": "Bearer wrong-token"})
    assert res_bad.status_code == 401


def test_mcp_sse_query_string_token_is_not_a_credential(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, token=TOKEN))

    assert client.get(f"/sse?token={TOKEN}").status_code == 401
    assert client.post(f"/messages/?session_id=x&token={TOKEN}").status_code == 401


def test_mcp_sse_auth_accepted(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, token=TOKEN))

    # Messages endpoint with invalid token returns 401
    res_msg_bad = client.post(
        "/messages/?session_id=dummy", headers={"Authorization": "Bearer bad"}
    )
    assert res_msg_bad.status_code == 401

    # A valid header passes auth; an unknown session is not a 401.
    res_msg_ok = client.post("/messages/?session_id=dummy", headers=BEARER)
    assert res_msg_ok.status_code != 401
    res_json = client.post("/messages/?session_id=dummy", headers=JSON_POST, json={})
    assert res_json.status_code in (400, 404)


def test_auth_failures_are_audited_without_the_token(tmp_path: Path) -> None:
    app = _app(tmp_path, token=TOKEN)
    client = TestClient(app)
    leaked = "presented-secret-value-9876"

    client.get("/sse")
    client.get("/sse", headers={"Authorization": f"Bearer {leaked}"})
    client.get(f"/sse?token={leaked}")

    audit: GrokBotAuditTracer = app.state.audit
    events = [r for r in audit.read_recent(10) if r["event"] == "auth_failure"]
    assert [e["details"]["reason"] for e in events] == [
        "missing",
        "invalid",
        "missing",
    ]
    assert all(e["details"]["path"] == "/sse" for e in events)
    assert all(e["status"] == "denied" and e["is_error"] for e in events)
    text = audit.log_path.read_text(encoding="utf-8")
    assert leaked not in text
    assert TOKEN not in text
    assert audit.verify_integrity()[0] is True


def test_readyz_is_503_while_shutting_down(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    assert client.get("/readyz").status_code == 200

    app.state.shutting_down = True
    res = client.get("/readyz")
    assert res.status_code == 503
    body = res.json()
    assert body["ready"] is False
    assert body["checks"]["shutdown"] == "draining"
    # Liveness is unaffected by draining.
    assert client.get("/healthz").status_code == 200


def test_readyz_is_503_when_audit_directory_is_not_writable(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    app = _app(tmp_path, audit=GrokBotAuditTracer(blocker / "audit.jsonl"))

    res = TestClient(app).get("/readyz")

    assert res.status_code == 503
    body = res.json()
    assert body["ready"] is False
    assert body["checks"]["audit"] == "fail"


def test_wildcard_bind_without_token_is_refused(tmp_path: Path) -> None:
    for host in ("0.0.0.0", "::", "192.0.2.10"):
        with pytest.raises(SecurityConfigError, match="without authentication"):
            _app(tmp_path, host=host)

    allowed = _app(tmp_path, host="0.0.0.0", allow_insecure_no_auth=True)
    assert TestClient(allowed).get("/healthz").json()["auth"] == "disabled"
    secured = _app(tmp_path, host="0.0.0.0", token=TOKEN)
    assert TestClient(secured).get("/healthz").json()["auth"] == "required"


def test_short_token_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SecurityConfigError, match="too short"):
        _app(tmp_path, token="short")


def test_invalid_policy_is_refused(tmp_path: Path) -> None:
    repo = _copy_contracts(tmp_path)
    policy = repo / "omega_prime" / "contracts" / "policies" / "omega-prime.json"
    policy.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(RuntimeConfigError, match="seat policy"):
        create_sse_app(repo, env={}, audit=GrokBotAuditTracer(tmp_path / "a.jsonl"))

    policy.unlink()
    with pytest.raises(RuntimeConfigError, match="seat policy"):
        create_sse_app(repo, env={}, audit=GrokBotAuditTracer(tmp_path / "a.jsonl"))


def test_missing_roster_is_refused(tmp_path: Path) -> None:
    repo = _copy_contracts(tmp_path)
    roster = repo / "omega_prime" / "contracts" / "tool-rosters" / "omega-prime.yaml"
    roster.unlink()

    with pytest.raises(RuntimeConfigError, match="roster"):
        create_sse_app(repo, env={}, audit=GrokBotAuditTracer(tmp_path / "a.jsonl"))


def test_omega_audit_reads_the_host_log(tmp_path: Path) -> None:
    log = tmp_path / "audit" / "audit.jsonl"
    tracer = GrokBotAuditTracer(log)
    tracer.log_event("tool_call", tool_name="read_file", status="ok")
    app = _app(
        tmp_path,
        audit=tracer,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "OMEGA_PRIME_AUDIT_LOG": str(tmp_path / "other.jsonl"),
        },
    )
    raw = app.state.runtime.registry.dispatch(
        "omega_command", {"command": "/omega-audit"}
    )
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["result"]["status"] == "verified"
    assert body["result"]["verified"] >= 1
    assert not (tmp_path / "other.jsonl").exists()


def test_approve_is_honoured_by_the_remote_runtime(tmp_path: Path) -> None:
    call = ("discord_send", {"channel_id": 1, "text": "hello"})

    plain = _app(tmp_path)
    assert "discord_send" in plain.state.runtime.gated_tools
    refused = json.loads(plain.state.runtime.registry.dispatch(*call))
    assert refused["error"] == "approval required"

    approved = _app(tmp_path, approvals=[("discord_send", "tester")])
    assert approved.state.runtime.approval_log.is_approved("discord_send")
    result = json.loads(approved.state.runtime.registry.dispatch(*call))
    assert result.get("error") != "approval required"


def test_bad_approval_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigError, match="pre-approve"):
        _app(tmp_path, approvals=[("discord_send", "bot-00-omega-prime")])


def test_cors_headers_only_for_configured_origins(tmp_path: Path) -> None:
    good = "https://app.example"
    app = _app(tmp_path, allowed_origins=[good])
    client = TestClient(app)

    allowed = client.get("/healthz", headers={"Origin": good})
    assert allowed.headers["access-control-allow-origin"] == good
    assert "access-control-allow-credentials" not in allowed.headers

    other = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers

    preflight = client.options(
        "/sse",
        headers={
            "Origin": good,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == good
    assert "DELETE" in preflight.headers["access-control-allow-methods"]

    assert _middleware_types(app).count(CORSMiddleware) == 1


def test_no_cors_without_configured_origins(tmp_path: Path) -> None:
    app = _app(tmp_path, token=TOKEN)
    client = TestClient(app)

    res = client.get("/healthz", headers={"Origin": "https://app.example"})

    assert "access-control-allow-origin" not in res.headers
    assert CORSMiddleware not in _middleware_types(app)
    assert AuthMiddleware in _middleware_types(app)


def test_wildcard_origin_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SecurityConfigError, match="wildcards"):
        _app(tmp_path, allowed_origins=["*"])
    with pytest.raises(SecurityConfigError, match="wildcards"):
        _app(tmp_path, allowed_hosts=["*"])


def test_hostile_origin_to_sse_is_rejected(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, token=TOKEN))

    res = client.get("/sse", headers={**BEARER, "Origin": "https://evil.example"})

    assert res.status_code == 403
    assert "Origin" in res.text


def test_hostile_host_is_rejected_on_a_loopback_bind(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, token=TOKEN))

    sse = client.get("/sse", headers={**BEARER, "Host": "evil.example"})
    post = client.post(
        "/messages/?session_id=dummy",
        headers={**JSON_POST, "Host": "evil.example"},
        json={},
    )

    assert sse.status_code == 421
    assert post.status_code == 421


def test_origin_stays_validated_when_host_validation_is_off(tmp_path: Path) -> None:
    app = _app(tmp_path, host="0.0.0.0", token=TOKEN, allowed_hosts=())
    client = TestClient(app)

    hostile = client.get("/sse", headers={**BEARER, "Origin": "https://evil.example"})
    any_host = client.post(
        "/messages/?session_id=dummy",
        headers={**JSON_POST, "Host": "anything.example"},
        json={},
    )

    assert hostile.status_code == 403
    assert any_host.status_code in (400, 404)


def test_public_url_enables_host_and_origin_validation(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        host="0.0.0.0",
        token=TOKEN,
        allowed_hosts=(),
        public_url="https://bot.example.com/mounted/path",
    )
    client = TestClient(app)
    post = "/messages/?session_id=dummy"

    ok = client.post(post, headers={**JSON_POST, "Host": "bot.example.com"}, json={})
    other = client.post(post, headers={**JSON_POST, "Host": "other.example"}, json={})
    cors = client.get("/healthz", headers={"Origin": "https://bot.example.com"})

    assert ok.status_code in (400, 404)
    assert other.status_code == 421
    assert cors.headers["access-control-allow-origin"] == "https://bot.example.com"


@pytest.mark.parametrize(
    "public_url",
    ["ftp://x.example", "bot.example.com", "https://user:pw@x.example", "https://"],
)
def test_malformed_public_url_is_refused(tmp_path: Path, public_url: str) -> None:
    with pytest.raises(SecurityConfigError, match="public URL"):
        _app(tmp_path, public_url=public_url)


def test_request_body_is_limited(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, token=TOKEN, max_body_bytes=100))

    res = client.post(
        "/messages/?session_id=dummy", headers=JSON_POST, content=b"x" * 500
    )

    assert res.status_code == 413


@pytest.mark.parametrize("kwargs", [{"max_body_bytes": 0}, {"shutdown_grace": -1.0}])
def test_invalid_limits_are_refused(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must"):
        _app(tmp_path, **kwargs)


def test_serve_sse_returns_2_on_a_refused_bind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = serve_sse(
        ROOT,
        host="0.0.0.0",
        port=_free_port(),
        home=tmp_path / "home",
        audit=GrokBotAuditTracer(tmp_path / "audit.jsonl"),
        env={},
    )

    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("omega-prime-mcp-server: refusing to serve on 0.0.0.0")


def test_serve_sse_returns_2_on_invalid_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _copy_contracts(tmp_path)
    policy = repo / "omega_prime" / "contracts" / "policies" / "omega-prime.json"
    policy.write_text("{ not json", encoding="utf-8")

    code = serve_sse(
        repo,
        port=_free_port(),
        audit=GrokBotAuditTracer(tmp_path / "audit.jsonl"),
        env={},
    )

    assert code == 2
    assert "omega-prime-mcp-server: cannot load seat policy" in (
        capsys.readouterr().err
    )


def test_serve_sse_returns_2_on_bad_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    audit = GrokBotAuditTracer(tmp_path / "audit.jsonl")
    short = serve_sse(ROOT, port=_free_port(), token="short", audit=audit, env={})
    port = serve_sse(ROOT, port=70000, audit=audit, env={})
    url = serve_sse(ROOT, port=_free_port(), public_url="nope", audit=audit, env={})

    assert (short, port, url) == (2, 2, 2)
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 3
    assert all(line.startswith("omega-prime-mcp-server: ") for line in lines)


def test_graceful_server_drains_then_exits(tmp_path: Path) -> None:
    app = _app(tmp_path)
    clock = _Clock()
    server = GracefulServer(
        uvicorn.Config(app), state=app.state, grace=5.0, clock=clock
    )
    call = ToolCall("probe", {}, None)
    app.state.in_flight.before(call)

    assert server.drain_complete() is False  # no drain requested yet
    server.request_shutdown()
    assert app.state.shutting_down is True
    assert server.draining is True
    assert server.should_exit is False
    assert server.drain_complete() is False  # a call is still in flight

    clock.now += 4.9
    assert server.drain_complete() is False
    clock.now += 0.2
    assert server.drain_complete() is True  # grace elapsed
    app.state.in_flight.after(call, ToolOutcome("ok", False, 0.0))
    assert app.state.in_flight.count == 0


def test_graceful_server_exits_as_soon_as_idle(tmp_path: Path) -> None:
    app = _app(tmp_path)
    server = GracefulServer(uvicorn.Config(app), state=app.state, grace=30.0)
    call = ToolCall("probe", {}, None)
    app.state.in_flight.before(call)
    server.request_shutdown()

    assert asyncio.run(server.on_tick(1)) is False
    assert server.should_exit is False
    app.state.in_flight.after(call, ToolOutcome("ok", False, 0.0))
    assert asyncio.run(server.on_tick(1)) is True
    assert server.should_exit is True
    assert server.force_exit is False


def test_second_shutdown_request_forces_exit(tmp_path: Path) -> None:
    app = _app(tmp_path)
    server = GracefulServer(uvicorn.Config(app), state=app.state, grace=30.0)
    app.state.in_flight.before(ToolCall("probe", {}, None))

    server.handle_exit(signal.SIGTERM, None)
    assert server.force_exit is False
    assert server.should_exit is False
    server.handle_exit(signal.SIGINT, None)

    assert server.force_exit is True
    assert server.should_exit is True


# -- access log: the query string (token, session id) never reaches the log --


@pytest.fixture
def clean_access_filters() -> Any:
    access_logger = logging.getLogger(ACCESS_LOGGER_NAME)

    def strip() -> None:
        for existing in list(access_logger.filters):
            if isinstance(existing, StripQueryFromAccessLog):
                access_logger.removeFilter(existing)

    strip()
    yield access_logger
    strip()


def _access_record(*args: Any) -> logging.LogRecord:
    return logging.LogRecord(
        ACCESS_LOGGER_NAME,
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        args,
        None,
    )


def test_access_filter_strips_the_query_string() -> None:
    record = _access_record(
        "127.0.0.1:5", "GET", f"/sse?token={TOKEN}&x=1?y", "1.1", 401
    )

    assert StripQueryFromAccessLog().filter(record) is True

    assert record.args == ("127.0.0.1:5", "GET", "/sse", "1.1", 401)
    assert record.getMessage() == '127.0.0.1:5 - "GET /sse HTTP/1.1" 401'
    assert TOKEN not in record.getMessage()


def test_access_filter_strips_the_session_id() -> None:
    record = _access_record("c", "POST", "/messages/?session_id=abc123", "1.1", 202)

    StripQueryFromAccessLog().filter(record)

    assert record.args == ("c", "POST", "/messages/", "1.1", 202)


def test_access_filter_leaves_a_path_without_query_unchanged() -> None:
    record = _access_record("c", "GET", "/healthz", "1.1", 200)
    before = record.args

    assert StripQueryFromAccessLog().filter(record) is True

    assert record.args is before


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("c", "GET"),
        ("c", "GET", "/p?q=1", "1.1"),
        ("c", "GET", "/p?q=1", "1.1", 200, "extra"),
        ("c", "GET", 7, "1.1", 200),
        ("c", "GET", None, "1.1", 200),
    ],
)
def test_access_filter_ignores_other_record_shapes(args: tuple[Any, ...]) -> None:
    record = _access_record(*args)

    assert StripQueryFromAccessLog().filter(record) is True

    assert record.args == args


def test_access_filter_ignores_non_tuple_args_and_never_raises() -> None:
    mapping = logging.LogRecord(
        ACCESS_LOGGER_NAME, logging.INFO, __file__, 1, "%(a)s", ({"a": "/p?q=1"},), None
    )
    plain = logging.LogRecord(
        ACCESS_LOGGER_NAME, logging.INFO, __file__, 1, "no args ?q=1", None, None
    )

    class Exploding:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(name)

    broken: Any = Exploding()

    filt = StripQueryFromAccessLog()
    assert filt.filter(mapping) is True
    assert mapping.args == {"a": "/p?q=1"}
    assert filt.filter(plain) is True
    assert plain.args is None
    assert filt.filter(broken) is True


def test_install_access_log_filter_is_idempotent(clean_access_filters: Any) -> None:
    access_logger: logging.Logger = clean_access_filters

    first = install_access_log_filter()
    second = install_access_log_filter()
    third = install_access_log_filter()

    assert first is second is third
    installed = [
        f for f in access_logger.filters if isinstance(f, StripQueryFromAccessLog)
    ]
    assert installed == [first]


def test_installed_filter_rewrites_what_handlers_see(
    clean_access_filters: Any,
) -> None:
    access_logger: logging.Logger = clean_access_filters
    seen: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    handler = Capture()
    access_logger.addHandler(handler)
    previous = access_logger.level
    access_logger.setLevel(logging.INFO)
    try:
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d', "c", "GET", "/sse?token=zz", "1.1", 401
        )
        install_access_log_filter()
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d', "c", "GET", "/sse?token=zz", "1.1", 401
        )
    finally:
        access_logger.removeHandler(handler)
        access_logger.setLevel(previous)

    assert seen == [
        'c - "GET /sse?token=zz HTTP/1.1" 401',
        'c - "GET /sse HTTP/1.1" 401',
    ]


def test_serve_sse_installs_the_access_filter_once(
    tmp_path: Path, clean_access_filters: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    access_logger: logging.Logger = clean_access_filters
    runs: list[int] = []
    monkeypatch.setattr(
        GracefulServer, "run", lambda self, sockets=None: runs.append(1)
    )
    kwargs: dict[str, Any] = {
        "home": tmp_path / "home",
        "audit": GrokBotAuditTracer(tmp_path / "audit.jsonl"),
        "env": {},
    }

    assert serve_sse(ROOT, port=_free_port(), **kwargs) == 0
    assert serve_sse(ROOT, port=_free_port(), **kwargs) == 0

    assert runs == [1, 1]
    installed = [
        f for f in access_logger.filters if isinstance(f, StripQueryFromAccessLog)
    ]
    assert len(installed) == 1

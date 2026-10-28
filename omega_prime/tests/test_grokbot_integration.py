# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 63 host integration over a real 127.0.0.1 socket.

Proves both MCP transports, live token revoke/expiry, the approval gateway,
traffic protection, breaker recovery, metrics, and correlated request ids.
Nothing leaves the loopback interface, and no test sleeps on a wall clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import logging.config
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from uvicorn.config import LOGGING_CONFIG

from omega_prime.grokbot.audit import GrokBotAuditTracer
from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.grokbot.remote import GracefulServer, create_sse_app, serve_sse
from omega_prime.grokbot.security import (
    SCOPE_CALL,
    SCOPE_READ,
    Principal,
    TokenStore,
    hash_token,
)
from omega_prime.grokbot.telemetry import JsonLogFormatter, configure_logging
from omega_prime.grokbot.tokens import (
    DEFAULT_RELOAD_INTERVAL,
    create_token,
    revoke_token,
)

ROOT = find_repo_root()
TOKEN = "integration-token-0123456789abcdef"
WRONG = "wrong-token-sentinel-zzzzzzzzzzzz"
READER = "reader-token-0123456789abcdef"
CALLER = "caller-token-0123456789abcdef"
BOT_ID = "bot-00-omega-prime"
_TRACEPARENT = re.compile(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}")
_ACCEPT = {"Accept": "application/json, text/event-stream"}
# Bound for a subprocess MCP host to answer /readyz (cold import under CI load).
_SUBPROCESS_STARTUP_TIMEOUT = 60.0


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Host:
    def __init__(
        self, app: Starlette, server: GracefulServer, thread: threading.Thread
    ) -> None:
        self.app = app
        self.server = server
        self.thread = thread
        port = server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"

    @property
    def audit(self) -> GrokBotAuditTracer:
        audit: GrokBotAuditTracer = self.app.state.audit
        return audit

    def records(self, event: str | None = None) -> list[dict[str, Any]]:
        rows = self.audit.read_recent(400)
        if event is None:
            return rows
        return [row for row in rows if row["event"] == event]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str | None]] = []
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        path = getattr(record, "path", "")
        request_id = getattr(record, "request_id", None)
        self.rows.append(
            (str(path), request_id if isinstance(request_id, str) else None)
        )
        self.lines.append(self.format(record))


def _app(tmp_path: Path, **kwargs: Any) -> Starlette:
    kwargs.setdefault("env", {})
    kwargs.setdefault("home", tmp_path / "home")
    kwargs.setdefault("audit", GrokBotAuditTracer(tmp_path / "audit" / "audit.jsonl"))
    return create_sse_app(ROOT, **kwargs)


@contextlib.contextmanager
def _serve(app: Starlette) -> Iterator[_Host]:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=2,
    )
    server = GracefulServer(config, state=app.state, grace=20.0)
    thread = threading.Thread(target=server.run, name="integration-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.02)
    try:
        yield _Host(app, server, thread)
    finally:
        server.should_exit = True
        thread.join(10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(5)


@contextlib.contextmanager
def _access_log() -> Iterator[_Capture]:
    """Capture `omega_prime.access` as NDJSON for the duration of the block."""
    configure_logging("json")
    target = logging.getLogger("omega_prime.access")
    previous = target.level
    handler = _Capture()
    handler.setFormatter(JsonLogFormatter())
    target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)
        configure_logging("text")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_trace(response: httpx2.Response) -> None:
    request_id = response.headers["x-request-id"]
    traceparent = response.headers["traceparent"]
    assert re.fullmatch(r"[0-9a-f]{16}", request_id)
    assert traceparent.startswith(f"00-{request_id}")
    assert _TRACEPARENT.fullmatch(traceparent)


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> httpx2.Response:
    with httpx2.Client(trust_env=False, timeout=10.0) as http:
        response = http.request(method, url, headers=headers, json=payload)
    _assert_trace(response)
    return response


def _run(coro: Awaitable[Any]) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(coro, 30)

    return asyncio.run(bounded())


@contextlib.asynccontextmanager
async def _mcp(host: _Host, token: str, transport: str) -> AsyncIterator[Client]:
    headers = _bearer(token)
    if transport == "sse":
        client_transport = sse_client(f"{host.base}/sse", headers=headers)
        async with Client(
            client_transport, mode="legacy", read_timeout_seconds=10.0
        ) as client:
            yield client
        return
    async with httpx2.AsyncClient(
        headers=headers, trust_env=False, timeout=10.0
    ) as http:
        client_transport = streamable_http_client(f"{host.base}/mcp", http_client=http)
        async with Client(
            client_transport, mode="legacy", read_timeout_seconds=10.0
        ) as client:
            yield client


def _text(result: Any) -> str:
    return "".join(getattr(part, "text", "") for part in result.content)


async def _listed(host: _Host, token: str, transport: str) -> list[str]:
    async with _mcp(host, token, transport) as client:
        listing = await client.list_tools()
    return [tool.name for tool in listing.tools]


async def _list_and_call(host: _Host, token: str, transport: str) -> list[str]:
    async with _mcp(host, token, transport) as client:
        listing = await client.list_tools()
        result = await client.call_tool("todo_read", {})
    assert result.is_error is False
    return [tool.name for tool in listing.tools]


async def _call(
    host: _Host,
    token: str,
    name: str,
    arguments: dict[str, Any],
    *,
    transport: str = "sse",
) -> tuple[bool, str]:
    async with _mcp(host, token, transport) as client:
        result = await client.call_tool(name, arguments)
    return bool(result.is_error), _text(result)


def _denial_codes(host: _Host) -> list[str | None]:
    return [row["details"]["denial"] for row in host.records("tool_call")]


def test_transports_manifest_metrics_and_request_ids(tmp_path: Path) -> None:
    app = _app(tmp_path, token=TOKEN)
    assert app.state.token_store.enabled is True
    assert app.state.approval_log is app.state.runtime.approval_log
    assert app.state.metrics is not None
    assert app.state.rate_limiter is not None
    assert app.state.breakers is not None
    with _serve(app) as host, _access_log() as captured:
        missing = _request("GET", f"{host.base}/sse")
        leaked = _request(
            "GET", f"{host.base}/sse", headers={"Authorization": f"Bearer {WRONG}"}
        )
        query = _request("GET", f"{host.base}/sse?token={TOKEN}")
        hostile_sse = _request(
            "GET",
            f"{host.base}/sse",
            headers={**_bearer(TOKEN), "Origin": "https://evil.example"},
        )
        hostile_mcp = _request(
            "POST",
            f"{host.base}/mcp",
            headers={
                **_bearer(TOKEN),
                **_ACCEPT,
                "Content-Type": "application/json",
                "Origin": "https://evil.example",
            },
            payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        anonymous_mcp = _request(
            "POST",
            f"{host.base}/mcp",
            headers={**_ACCEPT, "Content-Type": "application/json"},
            payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        sse_names = _run(_list_and_call(host, TOKEN, "sse"))
        http_names = _run(_list_and_call(host, TOKEN, "http"))
        health = _request("GET", f"{host.base}/healthz")
        ready = _request("GET", f"{host.base}/readyz")
        manifest_denied = _request("GET", f"{host.base}/manifest.json")
        manifest = _request("GET", f"{host.base}/manifest.json", headers=_bearer(TOKEN))
        metrics_denied = _request("GET", f"{host.base}/metrics")
        metrics = _request("GET", f"{host.base}/metrics", headers=_bearer(TOKEN))
        audit_text = host.audit.log_path.read_text(encoding="utf-8")
        log_text = "\n".join(captured.lines)

    assert missing.status_code == 401
    assert leaked.status_code == 401
    assert query.status_code == 401
    assert hostile_sse.status_code == 403
    assert hostile_mcp.status_code == 403
    assert anonymous_mcp.status_code == 401
    assert sse_names == http_names
    assert len(sse_names) == health.json()["rostered_tools"] > 100

    body = manifest.json()
    assert manifest_denied.status_code == 401
    assert manifest.status_code == 200
    assert body["mcp_server"]["auth"]["type"] == "bearer"
    assert body["mcp_server"]["rostered_tool_count"] == len(sse_names)
    assert set(body["mcp_server"]["tools"]) == set(sse_names)
    assert TOKEN not in json.dumps(body)

    assert ready.status_code == 200
    assert ready.json()["checks"]["token_store"] == "ok"
    assert ready.json()["checks"]["streamable_http"] == "ok"

    assert metrics_denied.status_code == 401
    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    for series in (
        "omega_http_requests_total",
        "omega_http_request_duration_seconds",
        "omega_tool_calls_total",
        "omega_tool_call_duration_seconds",
        "omega_auth_failures_total",
    ):
        assert series in metrics.text

    transports = {row["details"]["transport"] for row in host.records("tool_call")}
    assert transports == {"sse", "http"}
    health_id = health.headers["x-request-id"]
    assert ("/healthz", health_id) in captured.rows
    for transport, path in (("http", "/mcp"), ("sse", "/messages")):
        matched = [
            row
            for row in host.records("tool_call")
            if row["details"]["transport"] == transport
        ]
        assert matched
        request_id = matched[-1]["details"]["request_id"]
        assert isinstance(request_id, str) and re.fullmatch(r"[0-9a-f]{16}", request_id)
        assert any(
            logged.startswith(path) and rid == request_id
            for logged, rid in captured.rows
        )

    assert captured.lines
    for line in captured.lines:
        json.loads(line)
    assert all("?" not in path for path, _rid in captured.rows)
    assert TOKEN not in log_text
    assert WRONG not in log_text
    assert TOKEN not in audit_text
    assert WRONG not in audit_text
    assert host.audit.verify_integrity()[0] is True


def test_file_token_revokes_and_expires_without_restart(tmp_path: Path) -> None:
    clock = _Clock(1_700_000_000.0)
    path = tmp_path / "tokens.json"
    expiring, _expired = create_token(
        path,
        scopes=["read", "call"],
        label="short",
        ttl_days=1,
        clock=clock,
    )
    permanent, record = create_token(
        path, scopes=["read", "call"], label="ops", clock=clock
    )
    app = _app(tmp_path, token_store_path=path, clock=clock)
    with _serve(app) as host:
        assert (
            _request(
                "GET", f"{host.base}/manifest.json", headers=_bearer(expiring)
            ).status_code
            == 200
        )
        names = _run(_list_and_call(host, permanent, "http"))
        assert names
        assert host.records("tool_call")[-1]["caller"] == record.id

        clock.now += 86_400
        assert (
            _request(
                "GET", f"{host.base}/manifest.json", headers=_bearer(expiring)
            ).status_code
            == 401
        )
        assert (
            _request(
                "GET", f"{host.base}/manifest.json", headers=_bearer(permanent)
            ).status_code
            == 200
        )

        assert revoke_token(path, record.id, clock=clock) is True
        clock.now += DEFAULT_RELOAD_INTERVAL
        revoked = _request(
            "GET", f"{host.base}/manifest.json", headers=_bearer(permanent)
        )
        audit_text = host.audit.log_path.read_text(encoding="utf-8")

    assert revoked.status_code == 401
    assert permanent not in audit_text
    assert expiring not in audit_text


def test_unreadable_token_store_degrades_readyz_and_denies_auth(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not-a-file"
    path.mkdir()
    app = _app(tmp_path, token_store_path=path)
    assert app.state.token_store.enabled is True
    assert app.state.token_store.degraded is True
    with _serve(app) as host:
        ready = _request("GET", f"{host.base}/readyz")
        health = _request("GET", f"{host.base}/healthz")
        denied = _request(
            "GET", f"{host.base}/sse", headers=_bearer("any-bearer-token-0123456789")
        )

    assert health.status_code == 200
    assert health.json()["auth"] == "required"
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert ready.json()["checks"]["token_store"] == "degraded"
    assert ready.json()["checks"]["streamable_http"] == "ok"
    assert denied.status_code == 401


def test_admin_approval_is_scoped_audited_and_unblocks_the_tool(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tokens.json"
    caller_token, caller = create_token(path, scopes=["read", "call"], label="caller")
    admin_token, admin = create_token(path, scopes=["admin"], label="ops")
    app = _app(tmp_path, token_store_path=path)
    arguments = {"channel_id": 1, "text": "hello"}
    with _serve(app) as host:
        blocked = _run(_call(host, caller_token, "discord_send", arguments))
        forbidden = _request(
            "GET", f"{host.base}/admin/approvals", headers=_bearer(caller_token)
        )
        granted = _request(
            "POST",
            f"{host.base}/admin/approvals",
            headers=_bearer(admin_token),
            payload={"tool": "discord_send", "ttl_seconds": 120},
        )
        opened = _run(_call(host, admin_token, "discord_send", arguments))
        approvals = host.records("approval_granted")
        calls = [
            row
            for row in host.records("tool_call")
            if row["tool_name"] == "discord_send"
        ]

    assert forbidden.status_code == 403
    assert granted.status_code == 201
    assert granted.json()["approved"] is True
    assert granted.json()["approved_by"] == "ops"
    assert isinstance(granted.json()["expires_at"], (int, float))
    assert blocked[0] is True
    assert "approval required" in blocked[1]
    assert "approval required" not in opened[1]
    assert len(approvals) == 1
    assert approvals[0]["caller"] == admin.id
    assert approvals[0]["details"]["approver"] == "ops"
    assert approvals[0]["details"]["ttl_seconds"] == 120
    assert [row["caller"] for row in calls] == [caller.id, admin.id]
    assert BOT_ID not in {approvals[0]["caller"], approvals[0]["details"]["approver"]}
    assert BOT_ID not in {row["caller"] for row in calls}
    text = host.audit.log_path.read_text(encoding="utf-8")
    assert caller_token not in text
    assert admin_token not in text


def test_request_burst_returns_retry_after(tmp_path: Path) -> None:
    app = _app(tmp_path, token=TOKEN, rate_limit=2)
    with _serve(app) as host:
        first = _request("GET", f"{host.base}/manifest.json", headers=_bearer(TOKEN))
        second = _request("GET", f"{host.base}/manifest.json", headers=_bearer(TOKEN))
        third = _request("GET", f"{host.base}/manifest.json", headers=_bearer(TOKEN))
        probe = _request("GET", f"{host.base}/healthz")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers["retry-after"].isdigit()
    assert int(third.headers["retry-after"]) >= 1
    assert third.json() == {
        "error": "rate_limited",
        "retry_after": int(third.headers["retry-after"]),
    }
    assert probe.status_code == 200


def test_auth_failures_are_throttled_before_the_tool(tmp_path: Path) -> None:
    app = _app(tmp_path, token=TOKEN, auth_failure_limit=3)
    with _serve(app) as host:
        failures = [_request("GET", f"{host.base}/manifest.json") for _ in range(3)]
        blocked = _request("GET", f"{host.base}/manifest.json", headers=_bearer(TOKEN))
        calls = host.records("tool_call")
        reasons = [row["details"]["reason"] for row in host.records("auth_failure")]

    assert [response.status_code for response in failures] == [401, 401, 401]
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "too_many_auth_failures"
    assert blocked.headers["retry-after"].isdigit()
    assert calls == []
    assert reasons == ["missing", "missing", "missing"]


def test_scope_and_tool_rate_denials_are_audited_and_counted(
    tmp_path: Path,
) -> None:
    store = TokenStore(
        [
            (
                Principal("reader", frozenset({SCOPE_READ}), "read only"),
                hash_token(READER),
            ),
            (
                Principal("caller", frozenset({SCOPE_READ, SCOPE_CALL}), "caller"),
                hash_token(CALLER),
            ),
        ]
    )
    app = _app(tmp_path, token_store=store, tool_rate_limit=1)
    ran: list[str] = []

    def clarify(question: str = "") -> dict[str, str]:
        ran.append(question)
        return {"question": question}

    app.state.runtime.registry.register(
        "clarify",
        "records that it ran",
        {"type": "object", "properties": {"question": {"type": "string"}}},
        clarify,
    )
    with _serve(app) as host:
        denied = _run(_call(host, READER, "clarify", {"question": "no"}))
        allowed = _run(_call(host, CALLER, "clarify", {"question": "yes"}))
        limited = _run(_call(host, CALLER, "clarify", {"question": "again"}))
        metrics = _request("GET", f"{host.base}/metrics", headers=_bearer(CALLER))

    assert denied[0] is True and "forbidden" in denied[1]
    assert allowed[0] is False
    assert json.loads(allowed[1]) == {"question": "yes"}
    assert limited[0] is True and "rate_limited" in limited[1]
    assert ran == ["yes"]
    assert _denial_codes(host) == ["forbidden", None, "rate_limited"]
    assert 'omega_tool_denials_total{code="forbidden"}' in metrics.text
    assert 'omega_tool_denials_total{code="rate_limited"}' in metrics.text
    assert READER not in host.audit.log_path.read_text(encoding="utf-8")
    assert CALLER not in host.audit.log_path.read_text(encoding="utf-8")


def test_breaker_opens_without_running_and_recovers(tmp_path: Path) -> None:
    clock = _Clock(1_000.0)
    app = _app(
        tmp_path,
        token=TOKEN,
        no_roster=True,
        breaker_threshold=2,
        breaker_cooldown=5.0,
        tool_rate_limit=0,
        clock=clock,
    )
    calls = {"n": 0}

    def boom() -> dict[str, str]:
        calls["n"] += 1
        return {"error": "upstream_error: boom"}

    app.state.runtime.registry.register(
        "todo_read",
        "fails upstream",
        {"type": "object", "properties": {}},
        boom,
    )
    with _serve(app) as host:
        first = _run(_call(host, TOKEN, "todo_read", {}))
        second = _run(_call(host, TOKEN, "todo_read", {}))
        opened = _run(_call(host, TOKEN, "todo_read", {}))
        stalled = calls["n"]
        clock.now += 12
        recovered = _run(_call(host, TOKEN, "todo_read", {}))
        metrics = _request("GET", f"{host.base}/metrics", headers=_bearer(TOKEN))
        health = _request("GET", f"{host.base}/healthz").json()
        manifest = _request(
            "GET", f"{host.base}/manifest.json", headers=_bearer(TOKEN)
        ).json()
        listed = _run(_listed(host, TOKEN, "http"))

    assert first[0] is True and "upstream_error: boom" in first[1]
    assert second[0] is True and "upstream_error: boom" in second[1]
    assert stalled == 2
    assert opened[0] is True and "circuit_open" in opened[1]
    assert recovered[0] is True and "upstream_error: boom" in recovered[1]
    assert calls["n"] == 3
    assert "circuit_open" in _denial_codes(host)
    assert 'omega_tool_denials_total{code="circuit_open"}' in metrics.text
    assert manifest["mcp_server"]["tools"] == app.state.tool_names
    assert manifest["mcp_server"]["rostered_tool_count"] == health["rostered_tools"]
    assert health["rostered_tools"] == len(app.state.tool_names)
    assert set(app.state.tool_names) <= set(listed)


def test_admin_routes_and_metrics_follow_the_switches(tmp_path: Path) -> None:
    app = _app(tmp_path, metrics_enabled=False)
    assert app.state.metrics is not None
    with _serve(app) as host:
        admin = _request("GET", f"{host.base}/admin/approvals")
        metrics = _request("GET", f"{host.base}/metrics")
        manifest = _request("GET", f"{host.base}/manifest.json")

    assert admin.status_code == 404
    assert metrics.status_code == 404
    assert manifest.status_code == 200
    assert manifest.json()["mcp_server"]["auth"]["type"] == "none"


def test_serve_sse_configures_json_logs_and_disables_uvicorn_access_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs: list[uvicorn.Config] = []
    real_config = uvicorn.Config

    def capturing(*args: Any, **kwargs: Any) -> uvicorn.Config:
        config = real_config(*args, **kwargs)
        configs.append(config)
        return config

    monkeypatch.setattr(uvicorn, "Config", capturing)
    monkeypatch.setattr(GracefulServer, "run", lambda self: None)
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            code = serve_sse(
                ROOT,
                host="127.0.0.1",
                port=9,
                token=TOKEN,
                home=tmp_path / "home",
                audit=GrokBotAuditTracer(tmp_path / "audit.jsonl"),
                env={},
                log_format="json",
            )
        formatter = logging.getLogger("omega_prime").handlers[0].formatter
        log_config = configs[0].log_config
        error_logger = logging.getLogger("uvicorn.error")
        assert code == 0
        assert configs[0].access_log is False
        assert isinstance(formatter, JsonLogFormatter)
        assert stdout.getvalue() == ""
        assert isinstance(log_config, dict)
        assert log_config["disable_existing_loggers"] is False
        assert (
            log_config["formatters"]["json"]["()"]
            == "omega_prime.grokbot.telemetry.JsonLogFormatter"
        )
        assert set(log_config["handlers"]) == {"stderr"}
        handler = log_config["handlers"]["stderr"]
        assert handler["formatter"] == "json"
        assert handler["class"] == "logging.StreamHandler"
        assert handler["stream"] == "ext://sys.stderr"
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            assert log_config["loggers"][name]["handlers"] == ["stderr"]
            assert log_config["loggers"][name]["propagate"] is False
        assert error_logger.propagate is False
        assert len(error_logger.handlers) == 1
        assert isinstance(error_logger.handlers[0].formatter, JsonLogFormatter)
        assert logging.getLogger("uvicorn").handlers == error_logger.handlers
    finally:
        configure_logging("text")
        logging.config.dictConfig(LOGGING_CONFIG)


def test_serve_sse_text_logs_keep_the_banner_and_uvicorn_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs: list[uvicorn.Config] = []
    seen: list[dict[str, Any]] = []
    real_config = uvicorn.Config

    def capturing(*args: Any, **kwargs: Any) -> uvicorn.Config:
        seen.append(kwargs)
        config = real_config(*args, **kwargs)
        configs.append(config)
        return config

    monkeypatch.setattr(uvicorn, "Config", capturing)
    monkeypatch.setattr(GracefulServer, "run", lambda self: None)
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = serve_sse(
            ROOT,
            host="127.0.0.1",
            port=9,
            home=tmp_path / "home",
            audit=GrokBotAuditTracer(tmp_path / "audit.jsonl"),
            env={},
            log_format="text",
        )

    assert code == 0
    assert "log_config" not in seen[0]
    assert configs[0].log_config is LOGGING_CONFIG
    assert configs[0].access_log is False
    assert re.fullmatch(
        r"Serving omega-prime MCP on http://127\.0\.0\.1:9/sse "
        r"\(\d+ tools, authentication off\)\n"
        r"Healthcheck: http://127\.0\.0\.1:9/healthz\n",
        stdout.getvalue(),
    )


def test_json_log_subprocess_emits_only_ndjson(tmp_path: Path) -> None:
    """A real SSE process with --log-format json writes only NDJSON to stderr."""
    token = "integration-json-log-token-0123456789"
    token_file = tmp_path / "token"
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, f"{token}\n".encode())
    finally:
        os.close(fd)
    home = tmp_path / "home"
    home.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])

    env = os.environ.copy()
    env.pop("MCP_AUTH_TOKEN", None)
    env.pop("PYTHONWARNINGS", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "omega_prime.mcp_server",
            "--transport",
            "sse",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--token-file",
            str(token_file),
            "--audit-log",
            str(tmp_path / "audit.jsonl"),
            "--home",
            str(home),
            "--log-format",
            "json",
            "--shutdown-grace",
            "3",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []

    def _read(pipe: Any, parts: list[bytes]) -> None:
        parts.append(pipe.read())

    out_thread = threading.Thread(target=_read, args=(proc.stdout, stdout_parts))
    err_thread = threading.Thread(target=_read, args=(proc.stderr, stderr_parts))
    out_thread.start()
    err_thread.start()
    ready = False
    anonymous_status = 0
    authed_status = 0
    try:
        # A cold interpreter importing the 125-tool host can take well over 8 s
        # on a loaded CI runner; the loop still exits at once if the process dies.
        deadline = time.monotonic() + _SUBPROCESS_STARTUP_TIMEOUT
        with httpx2.Client(trust_env=False, timeout=0.5) as client:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    response = client.get(f"http://127.0.0.1:{port}/readyz")
                except (httpx2.HTTPError, OSError):
                    time.sleep(0.05)
                    continue
                if response.status_code == 200:
                    ready = True
                    break
                time.sleep(0.05)
            if ready:
                anonymous_status = client.get(
                    f"http://127.0.0.1:{port}/manifest.json"
                ).status_code
                authed_status = client.get(
                    f"http://127.0.0.1:{port}/manifest.json",
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        out_thread.join(timeout=2)
        err_thread.join(timeout=2)

    stdout = b"".join(stdout_parts)
    stderr = b"".join(stderr_parts).decode(errors="replace")
    assert ready, stderr
    assert anonymous_status == 401, stderr
    assert authed_status == 200, stderr
    assert proc.returncode == 0, stderr
    assert stdout == b""
    lines = [line for line in stderr.splitlines() if line.strip()]
    assert lines, stderr
    for line in lines:
        json.loads(line)
        assert token not in line

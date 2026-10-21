# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""End-to-end tests: the SSE host on a real socket, driven by the MCP SDK client.

Each test serves the app with `GracefulServer` on 127.0.0.1 (port 0) in a
background thread and talks to it with `mcp.client.Client` over `sse_client`, or
with plain HTTP for the probes. Nothing leaves the loopback interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.sse import sse_client
from starlette.applications import Starlette

from omega_prime.grokbot.audit import GrokBotAuditTracer
from omega_prime.grokbot.interceptors import ToolCall, ToolOutcome
from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.grokbot.remote import (
    ACCESS_LOGGER_NAME,
    GracefulServer,
    StripQueryFromAccessLog,
    create_sse_app,
    install_access_log_filter,
)
from omega_prime.grokbot.security import (
    SCOPE_CALL,
    SCOPE_READ,
    Principal,
    TokenStore,
    hash_token,
)

ROOT = find_repo_root()
TOKEN = "e2e-secret-token-0123456789"
QUESTION = "sentinel-question-value-4711"
WRONG_TOKEN = "wrong-token-sentinel-zzzzzzzz"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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

    def records(self, event: str = "tool_call") -> list[dict[str, Any]]:
        return [r for r in self.audit.read_recent(200) if r["event"] == event]


def _app(tmp_path: Path, **kwargs: Any) -> Starlette:
    kwargs.setdefault("token", TOKEN)
    kwargs.setdefault("env", {})
    kwargs.setdefault("home", tmp_path / "home")
    kwargs.setdefault("audit", GrokBotAuditTracer(tmp_path / "audit" / "audit.jsonl"))
    return create_sse_app(ROOT, **kwargs)


@contextlib.contextmanager
def _serve(
    app: Starlette,
    *,
    grace: float = 20.0,
    access_log: logging.Handler | None = None,
    strip_query: bool = False,
) -> Iterator[_Host]:
    """Serve `app` on 127.0.0.1:0 in a thread.

    With `access_log`, uvicorn's access logging is on and that handler receives
    the `uvicorn.access` records (uvicorn's logger does not propagate to the root
    logger, so `caplog` has to be attached to it directly). `strip_query` installs
    the filter the way `serve_sse` does: after `Config`, before serving.
    """
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="info" if access_log is not None else "warning",
        access_log=access_log is not None,
        timeout_graceful_shutdown=2,
    )
    access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
    if access_log is not None:
        access_logger.addHandler(access_log)
    if strip_query:
        install_access_log_filter()
    server = GracefulServer(config, state=app.state, grace=grace)
    thread = threading.Thread(target=server.run, name="e2e-server", daemon=True)
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
        if access_log is not None:
            access_logger.removeHandler(access_log)


def _http(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with _NO_PROXY.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _run(coro: Awaitable[Any]) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(coro, 30)

    return asyncio.run(bounded())


@contextlib.asynccontextmanager
async def _client(
    host: _Host,
    *,
    token: str | None = TOKEN,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[Client]:
    merged = dict(headers or {})
    if token is not None:
        merged["Authorization"] = f"Bearer {token}"
    transport = sse_client(f"{host.base}/sse", headers=merged)
    async with Client(transport, mode="legacy", read_timeout_seconds=10.0) as client:
        yield client


def _leaves(exc: BaseException) -> Iterator[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


def _rejected_statuses(
    host: _Host, *, token: str | None = TOKEN, headers: dict[str, str] | None = None
) -> list[int]:
    """HTTP statuses the SSE handshake failed with (empty when it connected)."""

    async def attempt() -> list[int]:
        try:
            async with _client(host, token=token, headers=headers):
                return []
        except Exception as exc:
            return [
                leaf.response.status_code
                for leaf in _leaves(exc)
                if isinstance(leaf, httpx2.HTTPStatusError)
            ]

    result: list[int] = _run(attempt())
    return result


def _call(host: _Host, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    async def go() -> tuple[bool, str]:
        async with _client(host) as client:
            result = await client.call_tool(name, arguments)
        text = "".join(getattr(part, "text", "") for part in result.content)
        return bool(result.is_error), text

    outcome: tuple[bool, str] = _run(go())
    return outcome


def test_e2e_connection_requires_a_bearer_header(tmp_path: Path) -> None:
    with _serve(_app(tmp_path)) as host:
        assert _rejected_statuses(host, token=None) == [401]
        assert _rejected_statuses(host, token=WRONG_TOKEN) == [401]
        assert _http(f"{host.base}/sse?token={TOKEN}")[0] == 401
        assert _http(f"{host.base}/sse", headers={"Authorization": TOKEN})[0] == 401
        assert _rejected_statuses(host) == []

        reasons = [r["details"]["reason"] for r in host.records("auth_failure")]
        assert reasons == ["missing", "invalid", "missing", "malformed"]


def test_e2e_listed_tools_match_health_and_state(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with _serve(app) as host:

        async def list_names() -> list[str]:
            async with _client(host) as client:
                listing = await client.list_tools()
            return [tool.name for tool in listing.tools]

        names: list[str] = _run(list_names())
        status, body = _http(f"{host.base}/healthz")

    health = json.loads(body)
    assert status == 200
    assert health["auth"] == "required"
    assert len(names) == health["rostered_tools"] == len(app.state.tool_names)
    assert sorted(names) == sorted(app.state.tool_names)
    assert len(names) > 100


def test_e2e_clarify_reports_undeliverable_without_live_operator(
    tmp_path: Path,
) -> None:
    with _serve(_app(tmp_path)) as host:

        async def go() -> tuple[Any, Any]:
            async with _client(host) as client:
                plain = await client.call_tool("todo_read", {})
                undeliverable = await client.call_tool(
                    "clarify", {"question": QUESTION}
                )
            return plain, undeliverable

        plain, undeliverable = _run(go())
        records = host.records()
        verified, count, _ = host.audit.verify_integrity()
        text = host.audit.log_path.read_text(encoding="utf-8")

    assert not plain.is_error
    assert undeliverable.is_error
    assert json.loads(undeliverable.content[0].text) == {
        "error": "undeliverable: no clarify interaction is configured"
    }
    assert [r["tool_name"] for r in records] == ["todo_read", "clarify"]
    first, second = records
    assert first["caller"] == "env-token"
    assert first["status"] == "ok"
    assert first["is_error"] is False
    assert first["details"]["arg_keys"] == []
    assert first["details"]["transport"] == "sse"
    assert second["details"]["arg_keys"] == ["question"]
    assert len(second["details"]["args_sha256"]) == 64
    assert second["status"] == "error"
    assert second["is_error"] is True
    assert QUESTION not in text
    assert verified is True
    assert count == len(records)


def test_e2e_audit_file_never_holds_token_text(tmp_path: Path) -> None:
    with _serve(_app(tmp_path)) as host:
        assert _rejected_statuses(host, token=WRONG_TOKEN) == [401]
        assert _http(f"{host.base}/sse?token={TOKEN}")[0] == 401
        assert not _call(host, "todo_read", {})[0]
        text = host.audit.log_path.read_text(encoding="utf-8")
        verified = host.audit.verify_integrity()[0]

    assert '"auth_failure"' in text
    assert '"tool_call"' in text
    assert TOKEN not in text
    assert WRONG_TOKEN not in text
    assert hash_token(TOKEN) not in text
    assert verified is True


def test_e2e_tool_outside_the_roster_is_an_error(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.state.runtime.registry.register(
        "unrostered_probe",
        "not in the roster",
        {"type": "object", "properties": {}},
        lambda: {"ran": True},
    )
    assert "unrostered_probe" not in app.state.tool_names
    with _serve(app) as host:
        is_error, text = _call(host, "unrostered_probe", {})
        records = host.records()

    assert is_error is True
    assert "policy forbids unrostered_probe" in text
    assert [(r["tool_name"], r["status"], r["is_error"]) for r in records] == [
        ("unrostered_probe", "error", True)
    ]


READER_TOKEN = "reader-token-0123456789abcdef"
CALLER_TOKEN = "caller-token-0123456789abcdef"


def test_e2e_scope_denial_is_audited_and_the_tool_does_not_run(
    tmp_path: Path,
) -> None:
    reader = Principal("reader", frozenset({SCOPE_READ}), "read only")
    caller = Principal("caller", frozenset({SCOPE_READ, SCOPE_CALL}), "may call")
    store = TokenStore(
        [(reader, hash_token(READER_TOKEN)), (caller, hash_token(CALLER_TOKEN))]
    )
    app = _app(tmp_path, token=None, token_store=store)
    ran: list[str] = []

    def probe() -> dict[str, Any]:
        ran.append("ran")
        return {"todos": []}

    # Re-registering an allowed (rostered, policy-permitted) name keeps the call
    # valid for everything except the scope check.
    app.state.runtime.registry.register(
        "todo_read", "records that it ran", {"type": "object"}, probe
    )
    with _serve(app) as host:

        async def call_as(token: str) -> tuple[int, bool, str]:
            async with _client(host, token=token) as client:
                listing = await client.list_tools()
                result = await client.call_tool("todo_read", {})
            text = "".join(getattr(part, "text", "") for part in result.content)
            return len(listing.tools), bool(result.is_error), text

        listed, denied, denied_text = _run(call_as(READER_TOKEN))
        ran_after_denial = list(ran)
        records_after_denial = host.records()
        _, allowed_error, allowed_text = _run(call_as(CALLER_TOKEN))
        records = host.records()
        verified, count, _ = host.audit.verify_integrity()
        text = host.audit.log_path.read_text(encoding="utf-8")

    assert listed == len(app.state.tool_names)  # `read` still lists tools
    assert denied is True
    assert "forbidden" in denied_text
    assert ran_after_denial == []  # the tool body never ran for the reader
    assert app.state.in_flight.count == 0

    assert len(records_after_denial) == 1
    record = records_after_denial[0]
    assert record["tool_name"] == "todo_read"
    assert record["caller"] == "reader"
    assert record["status"] == "denied"
    assert record["is_error"] is True
    assert record["details"]["denial"] == "forbidden"
    assert record["details"]["transport"] == "sse"

    # Control: a principal holding `call` runs the tool and is recorded as ok.
    assert allowed_error is False
    assert json.loads(allowed_text) == {"todos": []}
    assert ran == ["ran"]
    assert [(r["caller"], r["status"]) for r in records] == [
        ("reader", "denied"),
        ("caller", "ok"),
    ]
    assert records[1]["details"]["denial"] is None
    assert verified is True
    assert count == len(records)
    assert READER_TOKEN not in text
    assert CALLER_TOKEN not in text


def test_e2e_approval_gated_tool_needs_a_pre_approval(tmp_path: Path) -> None:
    arguments = {"channel_id": 1, "text": "hello"}
    with _serve(_app(tmp_path)) as host:
        is_error, text = _call(host, "discord_send", arguments)
    assert is_error is True
    assert "approval required" in text

    approved = _app(tmp_path / "approved", approvals=[("discord_send", "tester")])
    with _serve(approved) as host:
        _, text = _call(host, "discord_send", arguments)
    assert "approval required" not in text


def test_e2e_hostile_origin_and_host_are_rejected(tmp_path: Path) -> None:
    with _serve(_app(tmp_path)) as host:
        hostile = {"Origin": "https://evil.example"}
        assert _rejected_statuses(host, headers=hostile) == [403]
        status, _ = _http(
            f"{host.base}/sse",
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.example"},
        )
        assert status == 421
        assert _rejected_statuses(host) == []


def test_e2e_slow_tool_does_not_freeze_probes(tmp_path: Path) -> None:
    app = _app(tmp_path, no_roster=True)
    entered = threading.Event()
    release = threading.Event()

    def blocked() -> dict[str, Any]:
        entered.set()
        return {"released": release.wait(5)}

    # Re-registering an allowed name keeps the seat policy satisfied.
    app.state.runtime.registry.register(
        "todo_read", "blocks until released", {"type": "object"}, blocked
    )
    with _serve(app) as host:

        async def go() -> tuple[tuple[int, str], tuple[int, str], int, bool]:
            async with _client(host) as client:
                call = asyncio.create_task(client.call_tool("todo_read", {}))
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    started = time.monotonic()
                    health = await asyncio.to_thread(
                        _http, f"{host.base}/healthz", timeout=1.0
                    )
                    ready = await asyncio.to_thread(
                        _http, f"{host.base}/readyz", timeout=1.0
                    )
                    assert time.monotonic() - started < 1.0
                    in_flight = app.state.in_flight.count
                finally:
                    release.set()
                result = await asyncio.wait_for(call, 10)
            return health, ready, in_flight, bool(result.is_error)

        health, ready, in_flight, is_error = _run(go())
        deadline = time.monotonic() + 2
        while app.state.in_flight.count and time.monotonic() < deadline:
            time.sleep(0.02)
        remaining = app.state.in_flight.count

    assert health[0] == 200
    assert json.loads(health[1])["rostered_tools"] == len(app.state.tool_names)
    assert ready[0] == 200
    assert in_flight == 1
    assert is_error is False
    assert remaining == 0


def _force_in_flight(app: Starlette) -> Callable[[], None]:
    """Count one call as in flight; the returned function ends it."""
    call = ToolCall("probe", {}, None)
    app.state.in_flight.before(call)

    def finish() -> None:
        app.state.in_flight.after(call, ToolOutcome("ok", False, 0.0))

    return finish


def test_e2e_drain_waits_for_in_flight_calls(tmp_path: Path) -> None:
    app = _app(tmp_path)
    finish = _force_in_flight(app)
    with _serve(app, grace=30.0) as host:
        host.server.request_shutdown()
        assert app.state.shutting_down is True
        status, body = _http(f"{host.base}/readyz")
        assert status == 503
        assert json.loads(body)["checks"]["shutdown"] == "draining"

        time.sleep(0.5)  # several server ticks
        assert host.thread.is_alive()
        assert host.server.should_exit is False
        assert _http(f"{host.base}/healthz")[0] == 200

        finish()
        host.thread.join(5)
        assert not host.thread.is_alive()


def test_e2e_drain_gives_up_after_the_grace_period(tmp_path: Path) -> None:
    app = _app(tmp_path)
    finish = _force_in_flight(app)
    with _serve(app, grace=0.3) as host:
        host.server.request_shutdown()
        host.thread.join(5)
        stopped = not host.thread.is_alive()
        still_in_flight = app.state.in_flight.count
    finish()

    assert stopped
    assert still_in_flight == 1


def test_e2e_second_shutdown_request_forces_exit(tmp_path: Path) -> None:
    app = _app(tmp_path)
    finish = _force_in_flight(app)
    with _serve(app, grace=60.0) as host:
        host.server.request_shutdown()
        time.sleep(0.3)
        assert host.thread.is_alive()

        host.server.request_shutdown()
        host.thread.join(5)
        assert not host.thread.is_alive()
        assert host.server.force_exit is True
    finish()


def _access_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == ACCESS_LOGGER_NAME
    ]


def _wait_for_access(caplog: pytest.LogCaptureFixture, needle: str) -> None:
    deadline = time.monotonic() + 3
    while not any(needle in m for m in _access_messages(caplog)):
        assert time.monotonic() < deadline, f"no access record for {needle!r}"
        time.sleep(0.02)


def _strip_installed_filters() -> None:
    access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
    for existing in list(access_logger.filters):
        if isinstance(existing, StripQueryFromAccessLog):
            access_logger.removeFilter(existing)


def test_e2e_access_log_never_holds_a_query_string_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = "0123456789abcdef0123456789abcdef"
    _strip_installed_filters()
    try:
        with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER_NAME):
            # Control: without the filter the harness does see the token, so the
            # assertion below is not vacuous.
            with _serve(_app(tmp_path / "plain"), access_log=caplog.handler) as host:
                assert _http(f"{host.base}/sse?token={TOKEN}")[0] == 401
                _wait_for_access(caplog, "/sse")
            assert any(TOKEN in m for m in _access_messages(caplog))
            caplog.clear()

            with _serve(
                _app(tmp_path / "filtered"),
                access_log=caplog.handler,
                strip_query=True,
            ) as host:
                status, _ = _http(f"{host.base}/sse?token={TOKEN}")
                assert status == 401  # the request itself is refused
                post = urllib.request.Request(
                    f"{host.base}/messages/?session_id={session}&token={TOKEN}",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with contextlib.suppress(urllib.error.HTTPError):
                    _NO_PROXY.open(post, timeout=5)
                _wait_for_access(caplog, "/messages/")
                assert _http(f"{host.base}/healthz?probe=1")[0] == 200
                _wait_for_access(caplog, "/healthz")

        messages = _access_messages(caplog)
        everything = "\n".join(
            [caplog.text, *messages, *(str(r.args) for r in caplog.records)]
        )
    finally:
        _strip_installed_filters()

    assert TOKEN not in everything
    assert session not in everything
    assert "token=" not in everything
    assert "probe=1" not in everything
    assert any('"GET /sse HTTP/1.1" 401' in m for m in messages)
    assert any('"POST /messages/ HTTP/1.1" 401' in m for m in messages)
    assert any('"GET /healthz HTTP/1.1" 200' in m for m in messages)

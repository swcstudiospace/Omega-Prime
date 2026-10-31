# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Trace context, request ids, redacted NDJSON logs and the access log."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omega_prime.grokbot.security import Principal, generate_token
from omega_prime.grokbot.telemetry import (
    AccessLogMiddleware,
    JsonLogFormatter,
    RequestContextMiddleware,
    TraceContext,
    configure_logging,
    format_traceparent,
    new_trace_context,
    parse_traceparent,
    request_id_var,
)

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"
VALID = f"00-{TRACE}-{SPAN}-01"


# ------------------------------------------------------------------------ traceparent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (VALID, TraceContext(TRACE, SPAN, "01")),
        (f"00-{TRACE}-{SPAN}-00", TraceContext(TRACE, SPAN, "00")),
        (VALID.upper(), TraceContext(TRACE, SPAN, "01")),
        (f"01-{TRACE}-{SPAN}-01", None),
        (f"ff-{TRACE}-{SPAN}-01", None),
        (f"00-{'0' * 32}-{SPAN}-01", None),
        (f"00-{TRACE}-{'0' * 16}-01", None),
        (f"00-{TRACE[:-1]}-{SPAN}-01", None),
        (f"00-{TRACE}0-{SPAN}-01", None),
        (f"00-{TRACE}-{SPAN[:-1]}-01", None),
        (f"00-{TRACE}-{SPAN}-1", None),
        (f"00-{TRACE}-{SPAN}", None),
        (f"00-{TRACE}-{SPAN}-01-extra", None),
        (f"00-{TRACE}-{SPAN}-01\n", None),
        (f" {VALID}", None),
        (f"00-{'g' * 32}-{SPAN}-01", None),
        (f"00_{TRACE}_{SPAN}_01", None),
        ("", None),
        (None, None),
        (12345, None),
    ],
)
def test_parse_traceparent_table(value: Any, expected: TraceContext | None) -> None:
    assert parse_traceparent(value) == expected


def test_new_trace_context_is_random_and_valid() -> None:
    first = new_trace_context()
    second = new_trace_context()
    assert first.trace_id != second.trace_id
    assert first.parent_span_id is None
    assert parse_traceparent(format_traceparent(first)) == TraceContext(
        first.trace_id, first.span_id, first.flags
    )


def test_child_context_keeps_trace_id_and_flags() -> None:
    parent = parse_traceparent(f"00-{TRACE}-{SPAN}-00")
    assert parent is not None
    child = new_trace_context(parent)
    assert child.trace_id == TRACE
    assert child.flags == "00"
    assert child.span_id != SPAN
    assert child.parent_span_id == SPAN
    assert format_traceparent(child) == f"00-{TRACE}-{child.span_id}-00"


# ---------------------------------------------------------------------- request context


class _InjectPrincipal:
    def __init__(self, app: ASGIApp, principal: Principal) -> None:
        self.app = app
        self.principal = principal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["principal"] = self.principal
        await self.app(scope, receive, send)


async def _echo_state(request: Request) -> JSONResponse:
    trace = request.state.trace
    return JSONResponse(
        {
            "request_id": request.state.request_id,
            "trace_id": trace.trace_id,
            "span_id": trace.span_id,
            "parent_span_id": trace.parent_span_id,
            "contextvar": request_id_var.get(),
        },
        headers={"X-Request-Id": "handler-set", "traceparent": "handler-set"},
    )


def _app() -> Starlette:
    return Starlette(routes=[Route("/healthz", _echo_state)])


def test_middleware_mints_state_and_headers_without_inbound_context() -> None:
    response = TestClient(RequestContextMiddleware(_app())).get("/healthz")
    body = response.json()
    ctx = parse_traceparent(response.headers["traceparent"])
    assert ctx is not None
    assert response.headers["x-request-id"] == ctx.trace_id[:16] == body["request_id"]
    assert body["trace_id"] == ctx.trace_id
    assert body["span_id"] == ctx.span_id
    assert body["parent_span_id"] is None
    assert body["contextvar"] == body["request_id"]
    # Headers set by the handler are replaced, not duplicated.
    assert response.headers.get_list("x-request-id") == [body["request_id"]]
    assert len(response.headers.get_list("traceparent")) == 1
    assert request_id_var.get() is None


def test_middleware_continues_a_valid_inbound_trace() -> None:
    response = TestClient(RequestContextMiddleware(_app())).get(
        "/healthz", headers={"traceparent": VALID.upper()}
    )
    body = response.json()
    assert body["trace_id"] == TRACE
    assert body["parent_span_id"] == SPAN
    assert body["span_id"] != SPAN
    assert body["request_id"] == TRACE[:16]
    echoed = parse_traceparent(response.headers["traceparent"])
    assert echoed is not None
    assert echoed.trace_id == TRACE
    assert echoed.span_id == body["span_id"]


@pytest.mark.parametrize(
    "inbound",
    [
        f"00-{'0' * 32}-{SPAN}-01",
        "totally-malformed",
        f"ff-{TRACE}-{SPAN}-01",
        "00-<script>alert(1)</script>-x-01",
    ],
)
def test_malformed_inbound_traceparent_is_replaced_not_echoed(inbound: str) -> None:
    response = TestClient(RequestContextMiddleware(_app())).get(
        "/healthz", headers={"traceparent": inbound}
    )
    body = response.json()
    assert response.headers["traceparent"] != inbound
    assert inbound not in response.text
    assert body["parent_span_id"] is None
    assert body["trace_id"] != TRACE
    assert parse_traceparent(response.headers["traceparent"]) is not None


def test_inbound_request_id_is_ignored() -> None:
    forged = "forged-id\nINJECTED"
    response = TestClient(RequestContextMiddleware(_app())).get(
        "/healthz", headers={"X-Request-Id": "forged-id"}
    )
    assert response.headers["x-request-id"] != "forged-id"
    assert "forged-id" not in response.text
    assert forged not in response.headers["x-request-id"]
    assert len(response.headers["x-request-id"]) == 16


def test_request_id_is_stable_for_a_trace_and_unique_without_one() -> None:
    client = TestClient(RequestContextMiddleware(_app()))
    ids = {client.get("/healthz").headers["x-request-id"] for _ in range(5)}
    assert len(ids) == 5


# ---------------------------------------------------------------------- JSON formatter


def _json_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_json_formatter_emits_one_valid_line_and_redacts_secrets() -> None:
    logger, stream = _json_logger("test.telemetry.json")
    omk = generate_token()
    logger.info(
        "auth header was Authorization: Bearer abcdef1234567890xyz token %s", omk
    )
    output = stream.getvalue()
    assert output.count("\n") == 1
    record = json.loads(output)
    assert set(record) >= {"ts", "level", "logger", "msg"}
    assert record["level"] == "INFO"
    assert record["logger"] == "test.telemetry.json"
    assert record["ts"].endswith("Z")
    assert "abcdef1234567890xyz" not in output
    assert omk not in output
    assert "request_id" not in record


def test_json_formatter_redacts_bearer_and_omk_in_isolation() -> None:
    logger, stream = _json_logger("test.telemetry.json2")
    omk = generate_token()
    logger.warning("Bearer abcdef1234567890xyz")
    logger.warning("leaked %s here", omk)
    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert "abcdef1234567890xyz" not in first["msg"]
    assert omk not in second["msg"]
    assert "[REDACTED]" in first["msg"]
    assert "[REDACTED]" in second["msg"]


def test_json_formatter_extras_context_and_exceptions() -> None:
    logger, stream = _json_logger("test.telemetry.json3")
    omk = generate_token()
    token = request_id_var.set("req-from-context")
    try:
        try:
            raise ValueError(f"failed with Bearer abcdef1234567890xyz and {omk}")
        except ValueError:
            logger.exception(
                "boom\nsecond line",
                extra={
                    "tool": "echo",
                    "count": 3,
                    "ratio": float("nan"),
                    "nested": {"a": [1, 2, {"b": None}]},
                    "api_key": "abcdefgh12345678",
                    "note": f"carries {omk}",
                    "blob": object(),
                    "level": "spoofed",
                },
            )
    finally:
        request_id_var.reset(token)
    output = stream.getvalue()
    assert output.count("\n") == 1  # newline in msg and traceback are escaped
    record = json.loads(output)
    assert record["level"] == "ERROR"
    assert record["msg"] == "boom\nsecond line"
    assert record["request_id"] == "req-from-context"
    assert record["tool"] == "echo"
    assert record["count"] == 3
    assert record["ratio"] == "nan"
    assert record["nested"] == {"a": [1, 2, {"b": None}]}
    assert record["api_key"] == "[REDACTED]"
    assert "blob" not in record
    assert "ValueError" in record["exc"]
    for leaked in ("abcdef1234567890xyz", "abcdefgh12345678", omk):
        assert leaked not in output


def test_explicit_request_id_extra_wins_over_context() -> None:
    logger, stream = _json_logger("test.telemetry.json4")
    token = request_id_var.set("ctx")
    try:
        logger.info("x", extra={"request_id": "explicit"})
    finally:
        request_id_var.reset(token)
    assert json.loads(stream.getvalue())["request_id"] == "explicit"


def test_json_formatter_drops_uvicorns_ansi_color_message() -> None:
    logger, stream = _json_logger("test.telemetry.json.color")
    logger.info(
        "Finished server process [%d]",
        1,
        extra={"color_message": "Finished server process [\x1b[36m%d\x1b[0m]"},
    )
    line = stream.getvalue().strip()
    entry = json.loads(line)
    assert entry["msg"] == "Finished server process [1]"
    assert "color_message" not in entry
    assert "\\u001b" not in line


# ---------------------------------------------------------------------- configure_logging


@pytest.fixture
def clean_omega_logger() -> Iterator[io.StringIO]:
    target = logging.getLogger("omega_prime")
    saved = (list(target.handlers), target.level, target.propagate)
    root_before = list(logging.getLogger().handlers)
    stream = io.StringIO()
    target.handlers = []
    yield stream
    for handler in target.handlers:
        handler.close()
    target.handlers, target.level, target.propagate = saved
    assert logging.getLogger().handlers == root_before


def test_configure_logging_is_idempotent(
    clean_omega_logger: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pytest re-installs its own capture per phase, so redirect inside the test.
    monkeypatch.setattr(sys, "stderr", clean_omega_logger)
    target = logging.getLogger("omega_prime")
    root_handlers = list(logging.getLogger().handlers)
    configure_logging("json", "DEBUG")
    configure_logging("json", "DEBUG")
    configure_logging("json", "DEBUG")
    assert len(target.handlers) == 1
    assert isinstance(target.handlers[0].formatter, JsonLogFormatter)
    assert target.level == logging.DEBUG
    assert logging.getLogger().handlers == root_handlers

    logging.getLogger("omega_prime.sub").debug("hello %s", "there")
    lines = clean_omega_logger.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["msg"] == "hello there"

    configure_logging("text", "WARNING")
    assert len(target.handlers) == 1
    assert not isinstance(target.handlers[0].formatter, JsonLogFormatter)
    assert target.level == logging.WARNING
    logging.getLogger("omega_prime.sub").info("dropped")
    logging.getLogger("omega_prime.sub").warning("kept Bearer abcdef1234567890xyz")
    text = clean_omega_logger.getvalue()
    assert "dropped" not in text
    assert "kept" in text
    assert "abcdef1234567890xyz" not in text


def test_configure_logging_follows_a_replaced_stderr(
    clean_omega_logger: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stderr", clean_omega_logger)
    configure_logging("json")
    replacement = io.StringIO()
    monkeypatch.setattr(sys, "stderr", replacement)
    configure_logging("json")
    logging.getLogger("omega_prime.x").info("moved")
    assert "moved" in replacement.getvalue()
    assert "moved" not in clean_omega_logger.getvalue()


def test_configure_logging_rejects_bad_arguments(
    clean_omega_logger: io.StringIO,
) -> None:
    with pytest.raises(ValueError):
        configure_logging("xml")
    with pytest.raises(ValueError):
        configure_logging("json", "LOUD")
    assert logging.getLogger("omega_prime").handlers == []


# ---------------------------------------------------------------------- access log


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _access_logger(name: str) -> tuple[logging.Logger, _ListHandler]:
    logger = logging.getLogger(name)
    handler = _ListHandler()
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, handler


def test_access_log_records_one_line_without_query_or_header_values() -> None:
    logger, handler = _access_logger("test.access.one")
    stream = io.StringIO()
    json_handler = logging.StreamHandler(stream)
    json_handler.setFormatter(JsonLogFormatter())
    logger.addHandler(json_handler)

    principal = Principal("tok_ab12cd34", frozenset({"read"}), "ci")
    app = RequestContextMiddleware(
        AccessLogMiddleware(_InjectPrincipal(_app(), principal), logger=logger)
    )
    response = TestClient(app).get(
        "/healthz?token=supersecretvalue&x=1",
        headers={
            "Authorization": "Bearer hunter2hunter2hunter2",
            "Cookie": "sessionid=cookievalue123",
            "X-Custom": "headervalue-xyz",
        },
    )

    assert len(handler.records) == 1
    record = handler.records[0]
    assert record.name == "test.access.one"
    assert record.__dict__["method"] == "GET"
    assert record.__dict__["path"] == "/healthz"
    assert record.__dict__["status"] == 200
    assert record.__dict__["duration_ms"] >= 0
    assert record.__dict__["principal"] == "tok_ab12cd34"
    assert record.__dict__["request_id"] == response.headers["x-request-id"]

    rendered = record.getMessage() + stream.getvalue()
    for leaked in (
        "supersecretvalue",
        "token=",
        "x=1",
        "hunter2",
        "Bearer",
        "cookievalue123",
        "sessionid",
        "headervalue-xyz",
        "testserver",
    ):
        assert leaked not in rendered
    payload = json.loads(stream.getvalue())
    assert payload["path"] == "/healthz"
    assert payload["principal"] == "tok_ab12cd34"
    assert payload["request_id"] == response.headers["x-request-id"]
    assert payload["status"] == 200


def test_access_log_without_principal_and_default_logger_name() -> None:
    async def ping(request: Request) -> JSONResponse:
        return JSONResponse({})

    plain = Starlette(routes=[Route("/healthz", ping)])
    assert AccessLogMiddleware(plain).logger.name == "omega_prime.access"
    logger, handler = _access_logger("test.access.two")
    TestClient(AccessLogMiddleware(plain, logger=logger)).get("/healthz")
    record = handler.records[0]
    assert record.__dict__["principal"] is None
    assert record.__dict__["request_id"] is None
    assert "principal=-" in record.getMessage()


async def _drive(app: ASGIApp, path: str = "/healthz") -> None:
    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        return None

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"a=secret",
        "headers": [(b"authorization", b"Bearer abcdefghijklmnop")],
    }
    await app(scope, receive, send)


def test_access_log_records_status_500_when_the_app_raises() -> None:
    logger, handler = _access_logger("test.access.three")

    async def boom(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(_drive(AccessLogMiddleware(boom, logger=logger)))
    assert len(handler.records) == 1
    assert handler.records[0].__dict__["status"] == 500


def test_access_log_waits_for_a_streaming_response_to_end() -> None:
    logger, handler = _access_logger("test.access.four")
    seen: dict[str, int] = {}

    async def run() -> None:
        gate = asyncio.Event()

        async def stream_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"x", "more_body": True})
            await gate.wait()
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        task = asyncio.create_task(
            _drive(AccessLogMiddleware(stream_app, logger=logger))
        )
        await asyncio.sleep(0.05)
        seen["during"] = len(handler.records)
        gate.set()
        await asyncio.wait_for(task, timeout=2)
        seen["after"] = len(handler.records)

    asyncio.run(run())
    assert seen == {"during": 0, "after": 1}


def test_access_log_neutralizes_control_characters_in_the_path() -> None:
    logger, handler = _access_logger("test.access.five")

    async def ok(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    asyncio.run(_drive(AccessLogMiddleware(ok, logger=logger), path="/a\nFAKE 200\r/b"))
    message = handler.records[0].getMessage()
    assert "\n" not in message
    assert "\r" not in message
    assert "\\x0a" in message
    assert handler.records[0].__dict__["status"] == 404


def test_non_http_scopes_bypass_telemetry_middleware() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def run() -> None:
        async def receive() -> Message:
            return {"type": "lifespan.startup"}

        async def send(message: Message) -> None:
            return None

        await RequestContextMiddleware(app)({"type": "lifespan"}, receive, send)
        await AccessLogMiddleware(app)({"type": "lifespan"}, receive, send)

    asyncio.run(run())
    assert seen == ["lifespan", "lifespan"]

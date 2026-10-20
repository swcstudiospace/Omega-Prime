# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Read-only Starlette API and static frontend for the dashboard."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, MutableMapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from omega_prime.dashboard import __version__
from omega_prime.dashboard.poller import DashboardPoller

_SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


class _SSEResponse(Response):
    """Stream server-sent events directly over ASGI.

    starlette's StreamingResponse races a disconnect listener against the
    body stream; under the TestClient portal that listener never observes
    a disconnect and the response never starts. Streaming start/chunks/
    final-frame by hand keeps SSE testable and keeps cancellation honest.
    """

    def __init__(
        self, events: AsyncIterator[str], *, headers: Mapping[str, str] | None = None
    ) -> None:
        self.events = events
        super().__init__(
            status_code=200, headers=headers, media_type="text/event-stream"
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        # Response.__init__ adds "content-length: 0" for an empty body; a
        # streaming response must not send it or clients read zero bytes.
        headers = [
            (key, value)
            for key, value in self.raw_headers
            if key.lower() != b"content-length"
        ]
        start = {
            "type": "http.response.start",
            "status": self.status_code,
            "headers": headers,
        }
        await send(start)
        try:
            async for chunk in self.events:
                body = (
                    chunk
                    if isinstance(chunk, bytes | memoryview)
                    else chunk.encode(self.charset)
                )
                await send(
                    {"type": "http.response.body", "body": body, "more_body": True}
                )
        except BaseException:
            with suppress(Exception):
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
            raise
        await send({"type": "http.response.body", "body": b"", "more_body": False})


class _DashboardGuards:
    """Enforce GET-only, exact loopback Host and response security headers."""

    def __init__(self, app: Any, port: int) -> None:
        self.app = app
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        host_values = [
            value for key, value in scope.get("headers", []) if key.lower() == b"host"
        ]
        host = host_values[0].decode("latin-1").lower() if len(host_values) == 1 else ""
        if method != "GET":
            response = Response(
                "method not allowed: dashboard is read-only",
                status_code=405,
                media_type="text/plain",
            )
        elif host not in self.allowed_hosts:
            response = Response(
                "host not allowed", status_code=403, media_type="text/plain"
            )
        else:

            async def send_with_security(message: MutableMapping[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    response_headers = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() not in _SECURITY_HEADERS
                    ]
                    response_headers.extend(
                        (key.encode("latin-1"), value.encode("latin-1"))
                        for key, value in _SECURITY_HEADERS.items()
                    )
                    message["headers"] = response_headers
                await send(message)

            await self.app(scope, receive, send_with_security)
            return

        async def send_guarded(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message["headers"].extend(
                    (key.encode("latin-1"), value.encode("latin-1"))
                    for key, value in _SECURITY_HEADERS.items()
                )
            await send(message)

        await response(scope, receive, send_guarded)


def _empty_snapshot() -> dict[str, Any]:
    return {
        "generated_at": None,
        "sources": {},
        "active": [],
        "pr_gate": [],
        "waiting_on_ove": [],
        "finished": [],
    }


def create_app(
    poller: DashboardPoller, *, port: int = 7421, heartbeat_s: float = 15.0
) -> Starlette:
    """Build the local dashboard application around a shared poller."""
    static_dir = Path(__file__).resolve().parent / "static"
    index_path = static_dir / "index.html"

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await poller.start()
        try:
            yield
        finally:
            await poller.stop()

    async def index(_: Any) -> Response:
        if not index_path.is_file():
            return Response("not found", status_code=404)
        return FileResponse(index_path)

    async def snapshot(_: Any) -> Response:
        value = poller.snapshot() or _empty_snapshot()
        content = json.dumps(value, separators=(",", ":"), default=str)
        return Response(content, media_type="application/json")

    async def health(_: Any) -> Response:
        return JSONResponse({"ok": True, "version": __version__})

    async def stream(_: Any) -> Response:
        async def sse_generator() -> AsyncIterator[str]:
            previous_hash: str | None = None
            yield "retry: 3000\n\n"
            current_hash = poller.snapshot_hash()
            current = poller.snapshot()
            if current is not None and current_hash is not None:
                yield f"event: snapshot\ndata: {json.dumps(current, separators=(',', ':'), default=str)}\n\n"
                previous_hash = current_hash
            while True:
                changed = await poller.wait_for_update(heartbeat_s)
                current_hash = poller.snapshot_hash()
                if current_hash is not None and current_hash != previous_hash:
                    current = poller.snapshot()
                    if current is not None:
                        yield f"event: snapshot\ndata: {json.dumps(current, separators=(',', ':'), default=str)}\n\n"
                        previous_hash = current_hash
                elif not changed:
                    yield ": heartbeat\n\n"

        return _SSEResponse(
            sse_generator(),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", index, methods=["GET"]),
            Mount(
                "/static",
                app=StaticFiles(directory=static_dir, check_dir=False),
                name="static",
            ),
            Route("/api/snapshot", snapshot, methods=["GET"]),
            Route("/api/stream", stream, methods=["GET"]),
            Route("/healthz", health, methods=["GET"]),
        ],
    )
    app.add_middleware(_DashboardGuards, port=port)
    return app

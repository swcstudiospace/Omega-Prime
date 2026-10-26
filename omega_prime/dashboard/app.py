# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Read-only Starlette API and static frontend for the dashboard."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping, MutableMapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute

from omega_prime.dashboard import __version__
from omega_prime.dashboard.poller import DashboardPoller
from omega_prime.dashboard.terminal import TerminalService, resolve_task_pane
from omega_prime.dashboard.terminal_ws import HerdrBridge, make_terminal_ws_endpoint

if TYPE_CHECKING:
    from omega_prime.dashboard.events import TasksFileWatcher

_SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


class _SSEResponse(Response):
    """Stream server-sent events directly over ASGI.

    The disconnect listener is raced against each next event, so a quiet
    heartbeat gap still ends promptly when the client disconnects. Streaming
    start/chunks/final-frame by hand keeps SSE testable and keeps cancellation
    honest.
    """

    def __init__(
        self,
        events: AsyncIterator[str],
        *,
        headers: Mapping[str, str] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.events = events
        self.on_close = on_close
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
        try:
            await send(start)
        except BaseException:
            if self.on_close is not None:
                self.on_close()
            raise

        disconnected = asyncio.Event()

        async def listen_for_disconnect() -> None:
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    disconnected.set()
                    return

        listener = asyncio.create_task(listen_for_disconnect())
        disconnect_waiter = asyncio.create_task(disconnected.wait())
        next_event: asyncio.Task[str] | None = None
        try:
            while True:
                next_event = asyncio.create_task(
                    cast("Coroutine[Any, Any, str]", anext(self.events))
                )
                done, _ = await asyncio.wait(
                    {next_event, disconnect_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect_waiter in done:
                    next_event.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_event
                    next_event = None
                    await send(
                        {"type": "http.response.body", "body": b"", "more_body": False}
                    )
                    return

                completed_event = next_event
                next_event = None
                try:
                    chunk = completed_event.result()
                except StopAsyncIteration:
                    break
                body = (
                    chunk
                    if isinstance(chunk, bytes | memoryview)
                    else chunk.encode(self.charset)
                )
                await send(
                    {"type": "http.response.body", "body": body, "more_body": True}
                )
                await asyncio.sleep(0)
                if disconnected.is_set():
                    await send(
                        {"type": "http.response.body", "body": b"", "more_body": False}
                    )
                    return
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except BaseException:
            with suppress(Exception):
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
            raise
        finally:
            if next_event is not None:
                next_event.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_event
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
            disconnect_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await disconnect_waiter
            close = getattr(self.events, "aclose", None)
            if close is not None:
                with suppress(Exception):
                    await close()
            if self.on_close is not None:
                self.on_close()


class _DashboardGuards:
    """Enforce GET-only, exact loopback Host and response security headers.

    Websocket scopes are admitted only for /ws/ routes with a loopback Host
    and an explicit same-server Origin; everything else closes 4403 before any
    application logic (browsers do not apply CORS to websockets, so Origin is
    checked here). HTTP behavior is unchanged.
    """

    def __init__(self, app: Any, port: int, host: str) -> None:
        self.app = app
        self.port = port
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if isinstance(address, ipaddress.IPv6Address):
            self.allowed_hosts.add(f"[{host.lower()}]:{port}")
        else:
            selected_host = f"{host}:{port}".lower()
            if selected_host not in self.allowed_hosts:
                self.allowed_hosts.add(selected_host)
        # Origins and websocket CSP sources are derived from every supported
        # Host (including a bracketed IPv6 loopback such as [::1]), so a page
        # served on any accepted address can open its own websockets.
        hosts = sorted(self.allowed_hosts)
        ws_sources = " ".join(f"ws://{value}" for value in hosts)
        self.security_headers = dict(_SECURITY_HEADERS)
        self.security_headers["content-security-policy"] = _SECURITY_HEADERS[
            "content-security-policy"
        ].replace(
            "connect-src 'self'",
            f"connect-src 'self' {ws_sources}",
        )
        self.allowed_origins = {f"http://{value}".lower() for value in hosts}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "websocket":
            host_values = [
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"host"
            ]
            host = (
                host_values[0].decode("latin-1").lower()
                if len(host_values) == 1
                else ""
            )
            origin_values = [
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"origin"
            ]
            origin = (
                origin_values[0].decode("latin-1").lower()
                if len(origin_values) == 1
                else ""
            )
            if (
                host not in self.allowed_hosts
                or origin not in self.allowed_origins
                or not str(scope.get("path", "")).startswith("/ws/")
            ):
                await send({"type": "websocket.close", "code": 4403})
                return
            await self.app(scope, receive, send)
            return
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
                        if key.lower() not in self.security_headers
                    ]
                    response_headers.extend(
                        (key.encode("latin-1"), value.encode("latin-1"))
                        for key, value in self.security_headers.items()
                    )
                    message["headers"] = response_headers
                await send(message)

            await self.app(scope, receive, send_with_security)
            return

        async def send_guarded(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message["headers"].extend(
                    (key.encode("latin-1"), value.encode("latin-1"))
                    for key, value in self.security_headers.items()
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
        "idle_sessions": [],
    }


def create_app(
    poller: DashboardPoller,
    *,
    port: int = 7421,
    host: str = "127.0.0.1",
    heartbeat_s: float = 15.0,
    terminal_service: TerminalService | None = None,
    attach_token: str | None = None,
    terminal_readonly: bool = False,
    terminal_bridge: HerdrBridge | None = None,
    terminal_auth_timeout_s: float = 5.0,
    terminal_input_per_s: int = 32,
    tasks_path: Path | None = None,
    tasks_watcher: TasksFileWatcher | None = None,
) -> Starlette:
    """Build the local dashboard application around a shared poller.

    The attach token is minted once per app (or supplied for tests), lives
    only in this call frame, and is released solely through GET /api/session
    to same-origin loopback requests; websockets must present it in their
    first message. The value is never logged, stored, or put in a cookie.
    """
    static_dir = Path(__file__).resolve().parent / "static"
    legacy_index = static_dir / "index.html"
    cc_dir = static_dir / "cc"
    cc_index = cc_dir / "index.html"
    terminal_backend = terminal_service or TerminalService()
    token = attach_token if attach_token is not None else secrets.token_urlsafe(32)
    from omega_prime.dashboard import events

    events_endpoint = events.make_events_ws_endpoint(poller, heartbeat_s=heartbeat_s)
    watcher = tasks_watcher
    if watcher is None and tasks_path is not None:
        watcher = events.TasksFileWatcher(
            tasks_path, on_change=lambda: poller.nudge("tasks_json")
        )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await poller.start()
        watcher_task: asyncio.Task[None] | None = None
        if app.state.tasks_watcher is not None:
            watcher_task = asyncio.create_task(app.state.tasks_watcher.run())
        try:
            yield
        finally:
            if watcher_task is not None:
                watcher_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher_task
            await poller.stop()

    async def unavailable() -> Response:
        return Response(
            "<!doctype html><title>Command Center unavailable</title>"
            "<p>The built Command Center is unavailable. "
            '<a href="/legacy">Open the legacy dashboard</a>.</p>',
            status_code=503,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def index(_: Any) -> Response:
        if not cc_index.is_file():
            return await unavailable()
        return FileResponse(
            cc_index, media_type="text/html", headers={"Cache-Control": "no-cache"}
        )

    async def legacy(_: Any) -> Response:
        if not legacy_index.is_file():
            return Response("not found", status_code=404)
        return FileResponse(
            legacy_index, media_type="text/html", headers={"Cache-Control": "no-cache"}
        )

    async def cc_redirect(_: Any) -> Response:
        return Response(
            status_code=308,
            headers={"Location": "/cc/"},
        )

    async def cc_asset(request: Any) -> Response:
        relative = request.path_params["asset_path"]
        parts = relative.split("/")
        if (
            not relative
            or any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
            or relative.endswith((".map",))
            or "node_modules" in parts
        ):
            return Response("not found", status_code=404)
        if len(parts) == 2 and parts[0] == "assets":
            suffix = Path(parts[1]).suffix.lower()
            if suffix not in {".js", ".css", ".woff2"}:
                return Response("not found", status_code=404)
        elif len(parts) == 2 and parts[0] == "licenses" and parts[1].endswith(".txt"):
            suffix = ".txt"
        else:
            return Response("not found", status_code=404)
        target = (cc_dir / relative).resolve()
        if not target.is_relative_to(cc_dir.resolve()) or not target.is_file():
            return Response("not found", status_code=404)
        media_type = {
            ".js": "text/javascript",
            ".css": "text/css",
            ".woff2": "font/woff2",
            ".txt": "text/plain",
        }[suffix]
        headers = {}
        if suffix != ".txt" and any(len(part) >= 8 and "-" in part for part in parts):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            headers["Cache-Control"] = "no-cache"
        return FileResponse(target, media_type=media_type, headers=headers)

    async def legacy_asset(request: Any) -> Response:
        name = request.path_params["asset_name"]
        if name not in {"app.js", "app.css"}:
            return Response("not found", status_code=404)
        target = (static_dir / name).resolve()
        if not target.is_relative_to(static_dir.resolve()) or not target.is_file():
            return Response("not found", status_code=404)
        media_type = "text/javascript" if name.endswith(".js") else "text/css"
        return FileResponse(
            target, media_type=media_type, headers={"Cache-Control": "no-cache"}
        )

    async def snapshot(_: Any) -> Response:
        value = poller.snapshot() or _empty_snapshot()
        content = json.dumps(value, separators=(",", ":"), default=str)
        return Response(content, media_type="application/json")

    async def health(_: Any) -> Response:
        return JSONResponse({"ok": True, "version": __version__})

    async def session(request: Any) -> Response:
        sec_fetch_site = request.headers.get("sec-fetch-site")
        if sec_fetch_site is not None and sec_fetch_site not in ("same-origin", "none"):
            return Response("forbidden", status_code=403, media_type="text/plain")
        return JSONResponse(
            {"token": token, "readonly": terminal_readonly},
            headers={"Cache-Control": "no-store"},
        )

    async def terminal(request: Any) -> Response:
        task_id = request.path_params["task_id"]
        if (
            request.scope.get("query_string")
            or len(task_id.encode("utf-8")) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in task_id)
        ):
            return Response("not found", status_code=404)
        snapshot = poller.snapshot() or _empty_snapshot()
        task = next(
            (
                item
                for group in ("active", "idle_sessions", "finished")
                for item in snapshot.get(group, [])
                if isinstance(item, dict) and item.get("id") == task_id
            ),
            None,
        )
        if task is None:
            return Response("not found", status_code=404)
        pane = resolve_task_pane(task)
        if pane is None:

            async def no_session() -> AsyncIterator[str]:
                yield "retry: 3000\n\n"
                yield terminal_backend._event("no_session", "", None)

            return _SSEResponse(
                no_session(),
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        if not await terminal_backend.reserve():
            return Response(
                "terminal stream limit reached",
                status_code=429,
                headers={"Retry-After": "15"},
            )
        started = [False]

        def current_pane() -> str | None:
            # Re-resolved from the latest snapshot every cycle: a relaunch
            # replaces herdr_pane, and the open panel must follow it rather
            # than keep showing the previous run's pane as live.
            latest = poller.snapshot() or _empty_snapshot()
            row = next(
                (
                    item
                    for group in ("active", "idle_sessions", "finished")
                    for item in latest.get(group, [])
                    if isinstance(item, dict) and item.get("id") == task_id
                ),
                None,
            )
            return resolve_task_pane(row) if row is not None else None

        async def events() -> AsyncIterator[str]:
            started[0] = True
            async for event in terminal_backend.stream(
                pane, reserved=True, resolve=current_pane
            ):
                yield event

        return _SSEResponse(
            events(),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            on_close=lambda: (
                terminal_backend._slots.release() if not started[0] else None
            ),
        )

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
            Route("/cc", cc_redirect, methods=["GET"]),
            Route("/cc/", index, methods=["GET"]),
            Route("/cc/{asset_path:path}", cc_asset, methods=["GET"]),
            Route("/legacy", legacy, methods=["GET"]),
            Route("/static/{asset_name}", legacy_asset, methods=["GET"]),
            Route("/api/tasks/{task_id:path}/terminal", terminal, methods=["GET"]),
            Route("/api/snapshot", snapshot, methods=["GET"]),
            Route("/api/stream", stream, methods=["GET"]),
            Route("/api/session", session, methods=["GET"]),
            WebSocketRoute(
                "/ws/tasks/{task_id:path}/terminal",
                make_terminal_ws_endpoint(
                    poller,
                    terminal_bridge or HerdrBridge(),
                    token=token,
                    readonly=terminal_readonly,
                    auth_timeout_s=terminal_auth_timeout_s,
                    input_per_s=terminal_input_per_s,
                ),
            ),
            WebSocketRoute("/ws/events", events_endpoint),
            Route("/healthz", health, methods=["GET"]),
        ],
    )
    app.add_middleware(_DashboardGuards, port=port, host=host)
    app.state.tasks_watcher = watcher
    return app

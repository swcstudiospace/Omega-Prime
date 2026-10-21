# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Interactive per-task Herdr terminal over a token-gated loopback websocket.

The bridge drives Herdr through an injectable async runner (no Herdr binary is
required — tests use fakes), redacts pane frames with the existing terminal
rules adapted for ANSI (visible text sanitized, escape sequences preserved),
and caps concurrent terminal websockets. The endpoint authenticates with a
per-server-session token in the first message, enforces rw/ro modes, and
forwards size-capped, rate-limited input to the task's own pane. Nothing here
is ever logged: herdr argv, client input payloads, frame text and the attach
token stay out of logs, errors and stdout.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import unicodedata
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from omega_prime.dashboard import terminal
from omega_prime.dashboard.redact import redact_value
from omega_prime.dashboard.terminal import (
    _ANSI,
    _ASSIGN,
    _BEARER,
    _JWT,
    _TOKEN,
    TERMINAL_MAX_BYTES,
    TERMINAL_MAX_CAPTURE_BYTES,
    TERMINAL_MAX_LINES,
    TERMINAL_READ_TIMEOUT_S,
    PaneClosedError,
    _read_pane,
    resolve_task_pane,
)

TERMINAL_WS_LIMIT = 4
TERMINAL_INPUT_MAX_BYTES = 4096
TERMINAL_WS_POLL_S = 1.0

_UNSET = object()


def _redact_visible_run(text: str) -> str:
    """Sanitize one visible-text run exactly like sanitize_terminal_text."""
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc")
    text = str(redact_value(text))
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _TOKEN.sub("[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    text = _ASSIGN.sub(r"\1=[REDACTED]", text)
    return text


def redact_ansi_frame(value: str) -> str:
    """Redact visible text of an ANSI frame while keeping escape runs verbatim."""
    parts: list[str] = []
    cursor = 0
    for match in _ANSI.finditer(value):
        parts.append(_redact_visible_run(value[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_redact_visible_run(value[cursor:]))
    text = "".join(parts)
    text = "\n".join(text.splitlines()[-TERMINAL_MAX_LINES:])
    encoded = text.encode("utf-8")
    if len(encoded) > TERMINAL_MAX_BYTES:
        encoded = encoded[-TERMINAL_MAX_BYTES:]
        while encoded and (encoded[0] & 0xC0) == 0x80:
            encoded = encoded[1:]
        text = encoded.decode("utf-8", errors="ignore")
    return text


class HerdrBridge:
    """Pane I/O for terminal websockets behind an injectable async runner."""

    def __init__(
        self,
        *,
        runner: Callable[[list[str]], Awaitable[str]] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_s: float = TERMINAL_WS_POLL_S,
    ) -> None:
        self.runner = runner or _read_pane
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep = sleep
        self.poll_s = poll_s
        self._slots = asyncio.Semaphore(TERMINAL_WS_LIMIT)

    async def reserve(self) -> bool:
        if self._slots.locked() and getattr(self._slots, "_value", 0) <= 0:
            return False
        await self._slots.acquire()
        return True

    def release(self) -> None:
        self._slots.release()

    async def read_frame(self, pane: str) -> str:
        """Read the pane's visible screen as ANSI and return it redacted."""
        argv = ["herdr", "pane", "read", pane, "--source", "visible", "--format", "ansi"]
        raw = await asyncio.wait_for(self.runner(argv), TERMINAL_READ_TIMEOUT_S)
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > TERMINAL_MAX_CAPTURE_BYTES:
            raise OSError("terminal capture unavailable")
        return redact_ansi_frame(raw)

    async def send_text(self, pane: str, text: str) -> None:
        await self.runner(["herdr", "pane", "send-text", pane, text])

    async def send_keys(self, pane: str, keys: list[str]) -> None:
        await self.runner(["herdr", "pane", "send-keys", pane, *keys])

    async def poll_events(
        self, pane_provider: Callable[[], str | None]
    ) -> AsyncIterator[dict[str, str]]:
        """Yield frame/status events for the pane the provider resolves.

        Frames are sent only when the redacted visible content changes; a pane
        change always produces a first frame. Statuses are deduplicated. The
        generator returns on pane_closed and propagates CancelledError.
        """
        last_status: str | None = None
        last_text: str | None = None
        current_pane: str | None | object = _UNSET
        while True:
            pane = pane_provider()
            if pane is None:
                if last_status != "no_session":
                    yield {"type": "status", "status": "no_session"}
                    last_status = "no_session"
                await self.sleep(self.poll_s)
                continue
            if current_pane is _UNSET or pane != current_pane:
                current_pane = pane
                last_text = None
            try:
                text = await self.read_frame(pane)
            except asyncio.CancelledError:
                raise
            except PaneClosedError:
                yield {"type": "status", "status": "pane_closed"}
                return
            except Exception:
                if last_status != "unavailable":
                    yield {"type": "status", "status": "unavailable"}
                    last_status = "unavailable"
                await self.sleep(self.poll_s)
                continue
            if last_text is None or text != last_text:
                yield {
                    "type": "frame",
                    "text": text,
                    "updated_at": self.clock().astimezone(UTC).isoformat(),
                }
                last_text = text
            last_status = None
            await self.sleep(self.poll_s)


def make_terminal_ws_endpoint(
    poller: Any,
    bridge: HerdrBridge,
    *,
    token: str,
    readonly: bool = False,
    auth_timeout_s: float = 5.0,
    input_per_s: int = 32,
    mono: Callable[[], float] = time.monotonic,
) -> Callable[[WebSocket], Awaitable[None]]:
    """Build the /ws/tasks/{task_id}/terminal endpoint (protocol default 5).

    Auth happens in the first message within auth_timeout_s; failures close
    4401. Unknown or invalid task ids close 4404, an exhausted connection cap
    closes 4429. The pane is resolved only from the task row via
    terminal.resolve_task_pane and re-resolved every cycle, so pane changes
    are followed live; client frames never carry or influence pane ids.
    Read-only connections (server flag or client mode) drop input silently.
    """

    def _task_row(snapshot: dict[str, Any], task_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for group in ("active", "idle_sessions", "finished")
                for item in snapshot.get(group, [])
                if isinstance(item, dict) and item.get("id") == task_id
            ),
            None,
        )

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            message = await asyncio.wait_for(websocket.receive_json(), auth_timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            await websocket.close(code=4401)
            return
        if (
            not isinstance(message, dict)
            or message.get("type") != "auth"
            or not isinstance(message.get("token"), str)
            or not secrets.compare_digest(message["token"], token)
            or message.get("mode") not in ("rw", "ro")
        ):
            await websocket.close(code=4401)
            return
        force_readonly = readonly or message["mode"] == "ro"

        task_id = websocket.path_params["task_id"]
        if (
            websocket.scope.get("query_string")
            or len(task_id.encode("utf-8")) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in task_id)
        ):
            await websocket.close(code=4404)
            return
        snapshot = poller.snapshot() or {"active": [], "idle_sessions": [], "finished": []}
        if _task_row(snapshot, task_id) is None:
            await websocket.close(code=4404)
            return
        if not await bridge.reserve():
            await websocket.close(code=4429)
            return

        last_pane: list[str | None] = [None]

        def pane_provider() -> str | None:
            current = poller.snapshot() or {"active": [], "idle_sessions": [], "finished": []}
            row = _task_row(current, task_id)
            pane = resolve_task_pane(row) if row is not None else None
            if pane is not None:
                last_pane[0] = pane
            return pane

        async def writer() -> None:
            async for event in bridge.poll_events(pane_provider):
                await websocket.send_json(event)

        async def _allowed(window: deque[float], now: float) -> bool:
            while window and now - window[0] >= 1.0:
                window.popleft()
            if len(window) >= input_per_s:
                return False
            window.append(now)
            return True

        async def reader() -> None:
            window: deque[float] = deque()
            while True:
                try:
                    frame = await websocket.receive_json()
                except WebSocketDisconnect:
                    return
                if force_readonly or not isinstance(frame, dict):
                    continue
                kind = frame.get("type")
                if kind == "input":
                    text = frame.get("text")
                    if (
                        not isinstance(text, str)
                        or len(text.encode("utf-8")) > TERMINAL_INPUT_MAX_BYTES
                        or not await _allowed(window, mono())
                    ):
                        continue
                    pane = last_pane[0]
                    if pane is None:
                        continue
                    try:
                        await bridge.send_text(pane, text)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                elif kind == "keys":
                    keys = frame.get("keys")
                    if (
                        not isinstance(keys, list)
                        or not all(isinstance(key, str) for key in keys)
                        or len(json.dumps(keys, separators=(",", ":")).encode("utf-8"))
                        > TERMINAL_INPUT_MAX_BYTES
                        or not await _allowed(window, mono())
                    ):
                        continue
                    pane = last_pane[0]
                    if pane is None:
                        continue
                    try:
                        await bridge.send_keys(pane, keys)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue

        writer_task = asyncio.create_task(writer())
        reader_task = asyncio.create_task(reader())
        try:
            # First completion wins; the sibling is cancelled so a returning
            # reader (disconnect) or writer (pane_closed) ends the session.
            await asyncio.wait(
                {writer_task, reader_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            writer_task.cancel()
            reader_task.cancel()
            await asyncio.gather(writer_task, reader_task, return_exceptions=True)
            bridge.release()
        # The session must always end with an explicit close frame: a bare
        # return completes the ASGI app without one, and the peer would wait
        # forever (uvicorn tolerates a close after peer disconnect).
        try:
            await websocket.close()
        except Exception:
            pass

    return endpoint

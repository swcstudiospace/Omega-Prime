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
import contextlib
import difflib
import json
import re
import secrets
import time
import unicodedata
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

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


# Text-bearing escape strings: OSC (hyperlinks, window titles, clipboard) and
# DCS/SOS/PM/APC. Their payloads are text the browser terminal may surface
# (link targets, titles), so they are removed outright rather than preserved.
_STRING_SEQ = re.compile(r"\x1b(?:\]|[PX^_])[^\x07\x1b]*(?:\x07|\x1b\\)")
# Introducers of the above that never terminate are dropped too, so the bytes
# that follow are treated (and redacted) as ordinary visible text.
_STRING_INTRO = frozenset("]PX^_")


def _split_frame(value: str) -> tuple[str, list[tuple[int, str]]]:
    """Split a frame into its visible text and positioned styling escapes.

    Each escape is paired with the visible-text index it precedes. Only
    styling/cursor escapes (CSI and two-byte ESC sequences) are kept.
    """
    value = _STRING_SEQ.sub("", value)
    visible: list[str] = []
    escapes: list[tuple[int, str]] = []
    count = 0
    cursor = 0
    for match in _ANSI.finditer(value):
        run = value[cursor : match.start()]
        visible.append(run)
        count += len(run)
        sequence = match.group(0)
        if not (len(sequence) >= 2 and sequence[1] in _STRING_INTRO):
            escapes.append((count, sequence))
        cursor = match.end()
    visible.append(value[cursor:])
    return "".join(visible), escapes


# Upper bound on len(old) * len(new) for a character-level diff of one changed
# region. Larger regions are replaced as a single span (still exact visible
# text; only styling inside that span moves ahead of it), so redaction cost
# stays linear in the frame size instead of quadratic.
_DIFF_MAX_CELLS = 200_000


def _diff_region(
    offset: int, old_text: str, new_text: str
) -> list[tuple[int, int, str]]:
    """Edits for one changed region, trimmed to its differing middle."""
    limit = min(len(old_text), len(new_text))
    prefix = 0
    while prefix < limit and old_text[prefix] == new_text[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and old_text[len(old_text) - 1 - suffix] == new_text[len(new_text) - 1 - suffix]
    ):
        suffix += 1
    old_mid = old_text[prefix : len(old_text) - suffix]
    new_mid = new_text[prefix : len(new_text) - suffix]
    start = offset + prefix
    if not old_mid and not new_mid:
        return []
    if len(old_mid) * len(new_mid) > _DIFF_MAX_CELLS:
        return [(start, start + len(old_mid), new_mid)]
    matcher = difflib.SequenceMatcher(None, old_mid, new_mid, autojunk=False)
    return [
        (start + i1, start + i2, new_mid[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _map_redaction(visible: str, redacted: str) -> list[tuple[int, int, str]]:
    """Edits turning ``visible`` into ``redacted`` as (start, end, text).

    Lines are aligned one-to-one when the line count is unchanged (the usual
    case, so the diff stays small); otherwise the differing middle of the
    whole text is diffed, bounded by ``_DIFF_MAX_CELLS``.
    """
    edits: list[tuple[int, int, str]] = []
    old_lines = visible.split("\n")
    new_lines = redacted.split("\n")
    if len(old_lines) != len(new_lines):
        return _diff_region(0, visible, redacted)
    offset = 0
    for old_line, new_line in zip(old_lines, new_lines, strict=True):
        if old_line != new_line:
            edits.extend(_diff_region(offset, old_line, new_line))
        offset += len(old_line) + 1
    return edits


def redact_ansi_frame(value: str) -> str:
    """Redact an ANSI frame across its complete visible text.

    Secrets are matched on the visible text with every escape removed (the
    same view sanitize_terminal_text redacts), so styling between an
    assignment and its value cannot split a match. The replacements are then
    mapped back onto the styled frame: styling escapes keep their positions,
    and the frame's visible text is exactly the redacted visible text.
    Text-bearing OSC/DCS strings are dropped entirely.
    """
    visible, escapes = _split_frame(value)
    redacted = _redact_visible_run(visible)
    edits = _map_redaction(visible, redacted) if redacted != visible else []
    parts: list[str] = []
    escape_index = 0
    edit_index = 0
    position = 0
    length = len(visible)
    while position <= length:
        while escape_index < len(escapes) and escapes[escape_index][0] <= position:
            parts.append(escapes[escape_index][1])
            escape_index += 1
        if edit_index < len(edits) and edits[edit_index][0] == position:
            start, end, text = edits[edit_index]
            edit_index += 1
            # Styling inside the replaced span stays, ahead of the replacement.
            while escape_index < len(escapes) and escapes[escape_index][0] < end:
                parts.append(escapes[escape_index][1])
                escape_index += 1
            parts.append(text)
            if end > start:
                position = end
                continue
        if position < length:
            parts.append(visible[position])
        position += 1
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
        argv = [
            "herdr",
            "pane",
            "read",
            pane,
            "--source",
            "visible",
            "--format",
            "ansi",
        ]
        raw = await asyncio.wait_for(self.runner(argv), TERMINAL_READ_TIMEOUT_S)
        if (
            not isinstance(raw, str)
            or len(raw.encode("utf-8")) > TERMINAL_MAX_CAPTURE_BYTES
        ):
            raise OSError("terminal capture unavailable")
        # Redaction is CPU work on up to a full capture; keep it off the
        # event loop that serves HTTP, terminal input and live events.
        return await asyncio.to_thread(redact_ansi_frame, raw)

    async def send_text(self, pane: str, text: str) -> None:
        await self.runner(["herdr", "pane", "send-text", pane, text])

    async def send_keys(self, pane: str, keys: list[str]) -> None:
        await self.runner(["herdr", "pane", "send-keys", pane, *keys])

    async def poll_events(
        self, pane_provider: Callable[[], str | None]
    ) -> AsyncIterator[dict[str, str]]:
        """Yield frame/status events for the pane the provider resolves.

        Frames are sent only when the redacted visible content changes; a pane
        change always produces a first frame. Statuses are deduplicated, and
        recovery from an error status yields a ``live`` status followed by a
        fresh frame. The generator returns on pane_closed and propagates
        CancelledError.
        """
        last_status: str | None = None
        last_text: str | None = None
        current_pane: str | object | None = _UNSET
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
            if last_status is not None:
                # Recovered from no_session/unavailable: tell the client so it
                # clears the error state, and resend the screen even if it
                # did not change while the pane was unreadable.
                yield {"type": "status", "status": "live"}
                last_text = None
            if last_text is None or text != last_text:
                yield {
                    "type": "frame",
                    "text": text,
                    "updated_at": self.clock().astimezone(UTC).isoformat(),
                }
                last_text = text
            last_status = None
            await self.sleep(self.poll_s)


def _parse_auth(raw: Any) -> dict[str, Any] | None:
    """Validate the first-frame auth shape; None for anything malformed."""
    if not isinstance(raw, str):
        return None
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    if (
        not isinstance(message, dict)
        or message.get("type") != "auth"
        or not isinstance(message.get("token"), str)
        or message.get("mode") not in ("rw", "ro")
    ):
        return None
    return message


def _token_matches(candidate: str, token: str) -> bool:
    """Constant-time token check that never raises on unsupported text.

    secrets.compare_digest raises TypeError for non-ASCII str input, so the
    comparison runs on UTF-8 bytes (a non-ASCII candidate simply mismatches).
    """
    try:
        encoded = candidate.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(encoded, token.encode("utf-8"))


async def _close_quietly(websocket: WebSocket, code: int) -> None:
    with contextlib.suppress(Exception):
        await websocket.close(code=code)


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
            raw = await asyncio.wait_for(websocket.receive_text(), auth_timeout_s)
        except TimeoutError:
            await websocket.close(code=4401)
            return
        except WebSocketDisconnect:
            return
        except (KeyError, RuntimeError, TypeError):
            # A binary (non-text) first frame, or a receive the socket state
            # cannot serve, is a rejected auth attempt, not a server error.
            await _close_quietly(websocket, 4401)
            return
        message = _parse_auth(raw)
        if message is None or not _token_matches(message["token"], token):
            await _close_quietly(websocket, 4401)
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
        snapshot = poller.snapshot() or {
            "active": [],
            "idle_sessions": [],
            "finished": [],
        }
        if _task_row(snapshot, task_id) is None:
            await websocket.close(code=4404)
            return
        if not await bridge.reserve():
            await websocket.close(code=4429)
            return

        def pane_provider() -> str | None:
            # Resolved from the current snapshot on every call: the writer
            # follows pane changes live, and input is never sent to a pane
            # the task no longer maps (or to any pane once the task is gone).
            current = poller.snapshot() or {
                "active": [],
                "idle_sessions": [],
                "finished": [],
            }
            row = _task_row(current, task_id)
            return resolve_task_pane(row) if row is not None else None

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
                    raw_frame = await websocket.receive_text()
                except WebSocketDisconnect:
                    return
                except KeyError:
                    continue  # binary frame: not part of the protocol
                try:
                    frame = json.loads(raw_frame)
                except ValueError:
                    continue
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
                    pane = pane_provider()
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
                    pane = pane_provider()
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
        cancelled: asyncio.CancelledError | None = None
        try:
            # First completion wins; the sibling is cancelled so a returning
            # reader (disconnect) or writer (pane_closed) ends the session.
            await asyncio.wait(
                {writer_task, reader_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError as exc:
            # Session torn down by the server; remember the marked exception
            # so it can be re-raised after cleanup (unmarked re-deliveries
            # below must not replace it).
            cancelled = exc
        finally:
            writer_task.cancel()
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(writer_task, reader_task, return_exceptions=True)
            bridge.release()
            # The session must always end with an explicit close frame: a bare
            # return completes the ASGI app without one, and the peer would wait
            # forever (uvicorn tolerates a close after peer disconnect).
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await websocket.close()
        if cancelled is not None:
            raise cancelled

    return endpoint

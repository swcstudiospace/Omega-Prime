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
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

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

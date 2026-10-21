# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Bounded, read-only terminal capture for task-owned Herdr panes."""
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from omega_prime.dashboard.redact import redact_value

TERMINAL_STREAM_LIMIT = 4
TERMINAL_READ_TIMEOUT_S = 2.0
TERMINAL_POLL_S = 1.0
TERMINAL_HEARTBEAT_S = 15.0
TERMINAL_MAX_LINES = 200
TERMINAL_MAX_BYTES = 65536
TERMINAL_MAX_CAPTURE_BYTES = 1_000_000
_PANE = re.compile(r"^w\d+:p\d+$")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"']+")
_TOKEN = re.compile(r"\b(?:ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9_-]{8,})\b")
_JWT = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_ASSIGN = re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PAT)[A-Z0-9_]*)\s*=\s*([^\s,;]+)")


def resolve_task_pane(task: Any) -> str | None:
    mapping = getattr(task, "herdr_pane", None)
    if mapping is None and isinstance(task, dict):
        mapping = task.get("herdr_pane")
    if isinstance(mapping, dict):
        pane = mapping.get("pane_id")
        return pane if isinstance(pane, str) and _PANE.fullmatch(pane) else None
    owner = getattr(task, "owner", None)
    if isinstance(task, dict):
        owner = task.get("owner")
    kind = owner.get("kind") if isinstance(owner, dict) else getattr(owner, "kind", None)
    ref = owner.get("ref") if isinstance(owner, dict) else getattr(owner, "ref", None)
    return ref if kind == "herdr-pane" and isinstance(ref, str) and _PANE.fullmatch(ref) else None


def sanitize_terminal_text(value: str) -> str:
    text = _ANSI.sub("", value)
    text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)
    text = str(redact_value(text))
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _TOKEN.sub("[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    text = _ASSIGN.sub(r"\1=[REDACTED]", text)
    lines = text.splitlines()
    text = "\n".join(lines[-TERMINAL_MAX_LINES:])
    encoded = text.encode("utf-8")
    if len(encoded) > TERMINAL_MAX_BYTES:
        encoded = encoded[-TERMINAL_MAX_BYTES:]
        while encoded and (encoded[0] & 0xC0) == 0x80:
            encoded = encoded[1:]
        text = encoded.decode("utf-8", errors="ignore")
    return text


async def _read_pane(pane: str) -> str:
    argv = ["herdr", "pane", "read", pane, "--source", "recent", "--lines", "200", "--format", "text"]
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), TERMINAL_READ_TIMEOUT_S)
        if len(stdout) > TERMINAL_MAX_CAPTURE_BYTES or process.returncode != 0:
            raise OSError("Terminal capture unavailable")
        return stdout.decode("utf-8", errors="replace")
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise


class TerminalService:
    def __init__(self, *, runner: Callable[[str], Awaitable[str]] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.runner = runner or _read_pane
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep = sleep
        self._slots = asyncio.Semaphore(TERMINAL_STREAM_LIMIT)

    async def stream(self, pane: str) -> AsyncIterator[str]:
        if not _PANE.fullmatch(pane):
            yield self._event("no_session", "", None)
            return
        if self._slots.locked() and getattr(self._slots, "_value", 0) <= 0:
            yield self._event("unavailable", "", None)
            return
        await self._slots.acquire()
        previous: tuple[str, str] | None = None
        updated: str | None = None
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    raw = await asyncio.wait_for(self.runner(pane), TERMINAL_READ_TIMEOUT_S)
                    text = sanitize_terminal_text(raw)
                    state = "live"
                except asyncio.CancelledError:
                    raise
                except (TimeoutError, OSError, Exception):
                    text, state = "", "unavailable"
                if (state, text) != previous:
                    if text or state != "live":
                        updated = self.clock().astimezone(UTC).isoformat()
                    yield self._event(state, text, updated)
                    previous = (state, text)
                else:
                    await self.sleep(TERMINAL_HEARTBEAT_S)
                    yield ": heartbeat\n\n"
                await self.sleep(TERMINAL_POLL_S)
        finally:
            self._slots.release()

    @staticmethod
    def _event(status: str, text: str, updated_at: str | None) -> str:
        import json
        return "event: terminal\ndata: " + json.dumps({"status": status, "text": text, "updated_at": updated_at}, separators=(",", ":")) + "\n\n"

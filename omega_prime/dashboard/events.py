# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Live typed-delta event stream for the dashboard websocket.

This module computes the five locked delta message types between successive
poller snapshots (protocol default 9) and hosts the /ws/events endpoint with
its connection limiter and the tasks.json stat watcher (protocol default 10).
Nothing here logs, and no function performs I/O in the delta computation.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from starlette.websockets import WebSocket

from omega_prime.dashboard.poller import DashboardPoller

EVENTS_WS_LIMIT = 16
EVENTS_WATCH_INTERVAL_S = 0.5

# Deliberate mirror of app._empty_snapshot: events.py must not import app.py
# (app.py imports this module for the route factory, so the import would be a
# cycle). The shape is pinned by the snapshot-contract tests on both sides.
_EMPTY_SNAPSHOT: dict[str, Any] = {
    "generated_at": None,
    "sources": {},
    "active": [],
    "pr_gate": [],
    "waiting_on_ove": [],
    "finished": [],
    "idle_sessions": [],
}

def _row_map(bucket: Any) -> dict[str, dict[str, Any]]:
    """Index one snapshot bucket by row id, keeping the last row per id."""
    rows: dict[str, dict[str, Any]] = {}
    if not isinstance(bucket, list):
        return rows
    for row in bucket:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str):
            continue
        rows[row_id] = row
    return rows


def _without_stale(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "stale"}


def compute_deltas(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> list[dict[str, Any]]:
    """Diff two served snapshots into the locked protocol-9 message list.

    Pure: no I/O, no logging, no state. ``previous`` may be ``None``, which
    behaves like an empty snapshot. Message order is fixed: task_finished,
    task_changed, waiting_added, waiting_cleared, stale_changed; rows inside
    each group keep the current snapshot's order (the previous snapshot's
    order for waiting_cleared).
    """
    prev = previous if isinstance(previous, dict) else {}
    prev_active = _row_map(prev.get("active"))
    prev_waiting = _row_map(prev.get("waiting_on_ove"))
    prev_finished = _row_map(prev.get("finished"))
    now_active = _row_map(current.get("active"))
    now_waiting = _row_map(current.get("waiting_on_ove"))
    now_finished = _row_map(current.get("finished"))

    messages: list[dict[str, Any]] = []
    for row_id, row in now_finished.items():
        if row_id not in prev_finished:
            messages.append({"type": "task_finished", "task": row})
    for row_id, row in now_active.items():
        before = prev_active.get(row_id)
        if before is None:
            messages.append({"type": "task_changed", "task": row})
        elif _without_stale(before) != _without_stale(row):
            messages.append({"type": "task_changed", "task": row})
    for row_id, item in now_waiting.items():
        if row_id not in prev_waiting:
            messages.append({"type": "waiting_added", "item": item})
    for row_id in prev_waiting:
        if row_id not in now_waiting:
            messages.append({"type": "waiting_cleared", "id": row_id})
    for row_id, row in now_active.items():
        before = prev_active.get(row_id)
        if before is not None and before.get("stale") != row.get("stale"):
            messages.append(
                {"type": "stale_changed", "id": row_id, "stale": row.get("stale")}
            )
    return messages


class ConnectionLimiter:
    """Synchronous, loop-atomic connection counter (no semaphore semantics)."""

    def __init__(self, limit: int = EVENTS_WS_LIMIT) -> None:
        self._limit = limit
        self._active = 0

    def try_acquire(self) -> bool:
        if self._active >= self._limit:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active -= 1


def make_events_ws_endpoint(
    poller: DashboardPoller,
    *,
    heartbeat_s: float = 15.0,
    limit: int = EVENTS_WS_LIMIT,
    mono: Any = time.monotonic,
) -> Any:
    """Build the /ws/events endpoint (protocol default 9).

    The first message is always the full snapshot (or the empty-snapshot
    literal before the first build); afterwards the connection receives only
    the five locked delta types plus heartbeats, computed by re-diffing
    ``poller.snapshot()`` on every wake — including heartbeat timeouts, which
    is the missed-wake recovery. No token exists on this route; admission is
    the guard's Host plus required-Origin check. A full limiter closes 4429
    before accept.
    """

    limiter = ConnectionLimiter(limit)

    def _frame(message: dict[str, Any]) -> str:
        return json.dumps(message, separators=(",", ":"), default=str)

    async def endpoint(websocket: WebSocket) -> None:
        if not limiter.try_acquire():
            await websocket.close(code=4429)
            return
        try:
            await websocket.accept()
            first = poller.snapshot()
            await websocket.send_text(
                _frame(
                    {
                        "type": "snapshot",
                        "snapshot": first if first is not None else _EMPTY_SNAPSHOT,
                    }
                )
            )

            async def reader() -> None:
                # Server-to-client only: every client frame is discarded and
                # no token is ever read on this route.
                while True:
                    await websocket.receive()

            async def writer() -> None:
                previous = first
                last_sent = mono()
                while True:
                    await poller.wait_for_update(heartbeat_s)
                    current = poller.snapshot()
                    if current is not None and current is not previous:
                        for message in compute_deltas(previous, current):
                            await websocket.send_text(_frame(message))
                        previous = current
                        last_sent = mono()
                    if mono() - last_sent >= heartbeat_s:
                        await websocket.send_text(_frame({"type": "heartbeat"}))
                        last_sent = mono()

            writer_task = asyncio.create_task(writer())
            reader_task = asyncio.create_task(reader())
            cancelled: asyncio.CancelledError | None = None
            try:
                await asyncio.wait(
                    {writer_task, reader_task}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError as exc:
                cancelled = exc
            finally:
                writer_task.cancel()
                reader_task.cancel()
                try:
                    await asyncio.gather(
                        writer_task, reader_task, return_exceptions=True
                    )
                except asyncio.CancelledError:
                    pass
                # The session must always end with an explicit close frame: a
                # bare return completes the ASGI app without one, and the peer
                # would wait forever (uvicorn tolerates a close after peer
                # disconnect).
                try:
                    await websocket.close()
                except (Exception, asyncio.CancelledError):
                    pass
            if cancelled is not None:
                raise cancelled
        finally:
            limiter.release()

    return endpoint

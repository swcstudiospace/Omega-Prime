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
import contextlib
import json
import time
from pathlib import Path
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


def _row_map(bucket: Any, *, first_wins: bool = False) -> dict[str, dict[str, Any]]:
    """Index one snapshot bucket by row id.

    The last row per id wins, except for ``finished`` (``first_wins``): it is
    newest first and the Finished view keeps the first row per id, so a delta
    must never prefer an older duplicate the view would hide.
    """
    rows: dict[str, dict[str, Any]] = {}
    if not isinstance(bucket, list):
        return rows
    for row in bucket:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str):
            continue
        if first_wins:
            rows.setdefault(row_id, row)
        else:
            rows[row_id] = row
    return rows


def _has_duplicate_ids(bucket: Any) -> bool:
    """True when a bucket lists the same row id more than once."""
    if not isinstance(bucket, list):
        return False
    ids = [
        row.get("id")
        for row in bucket
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    return len(ids) != len(set(ids))


def _without_stale(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "stale"}


_DELTA_BUCKETS = ("active", "waiting_on_ove", "finished")


def _canonical_sources(value: Any) -> Any:
    """Sources without per-build timing noise (mirrors the poller hash)."""
    if not isinstance(value, dict):
        return value
    return {
        name: (
            {key: item for key, item in state.items() if key != "took_ms"}
            if isinstance(state, dict)
            else state
        )
        for name, state in value.items()
    }


def needs_full_snapshot(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> bool:
    """True when the change cannot be expressed by the locked delta types.

    The delta messages only add/replace active rows, move rows into
    ``finished``, add/clear waiting items and patch active-row staleness.
    Anything else — source status (CI/CD, GitHub checks), ``pr_gate``,
    ``idle_sessions``, active rows that disappear without finishing, edits
    or removals of existing waiting/finished rows, or any other top-level
    key — needs a full snapshot so live clients never keep old data.
    ``generated_at`` and per-source ``took_ms`` are ignored as build noise.
    """
    if not isinstance(previous, dict):
        return True
    keys = (set(previous) | set(current)) - {"generated_at", *_DELTA_BUCKETS}
    for key in keys:
        before, after = previous.get(key), current.get(key)
        if key == "sources":
            before, after = _canonical_sources(before), _canonical_sources(after)
        if before != after:
            return True
    # Delta messages are keyed by row id; a bucket that repeats an id (the
    # bridge can hold a new and an older finished record for one task)
    # cannot be patched safely, so the client gets the whole snapshot.
    for bucket in _DELTA_BUCKETS:
        if _has_duplicate_ids(previous.get(bucket)) or _has_duplicate_ids(
            current.get(bucket)
        ):
            return True
    prev_active = _row_map(previous.get("active"))
    prev_waiting = _row_map(previous.get("waiting_on_ove"))
    prev_finished = _row_map(previous.get("finished"), first_wins=True)
    now_active = _row_map(current.get("active"))
    now_waiting = _row_map(current.get("waiting_on_ove"))
    now_finished = _row_map(current.get("finished"), first_wins=True)
    for row_id in prev_active:
        if row_id in now_active:
            continue
        # task_finished removes the active row client-side, but it is only
        # emitted for rows that are new to the finished bucket.
        if row_id not in now_finished or row_id in prev_finished:
            return True
    for row_id, row in prev_finished.items():
        if now_finished.get(row_id) != row:
            return True
    for row_id in now_finished:
        # task_finished drops the active row client-side; a row that newly
        # finishes while an active row with the same id is still served
        # (the bridge lets both coexist) would vanish from the live view.
        if row_id not in prev_finished and row_id in now_active:
            return True
    for row_id, item in prev_waiting.items():
        if row_id in now_waiting and now_waiting[row_id] != item:
            return True
    return False


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
    prev_finished = _row_map(prev.get("finished"), first_wins=True)
    now_active = _row_map(current.get("active"))
    now_waiting = _row_map(current.get("waiting_on_ove"))
    now_finished = _row_map(current.get("finished"), first_wins=True)

    messages: list[dict[str, Any]] = []
    for row_id, row in now_finished.items():
        if row_id not in prev_finished:
            messages.append({"type": "task_finished", "task": row})
    for row_id, row in now_active.items():
        before = prev_active.get(row_id)
        if before is None or _without_stale(before) != _without_stale(row):
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


class TasksFileWatcher:
    """Stat-poll the tasks.json path; fire on_change once per signature change.

    The signature is ``(st_mtime_ns, st_size)`` with ``None`` meaning the file
    is missing (a normal state before the first write). Never fires on the
    baseline, never on unchanged stats, never twice for one change; stat
    errors degrade to the missing-file signature instead of raising, and
    nothing is logged. Cancellation propagates normally.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        on_change: Any,
        interval_s: float = EVENTS_WATCH_INTERVAL_S,
        sleep: Any = asyncio.sleep,
        stat: Any = None,
    ) -> None:
        self.path = Path(path)
        self._on_change = on_change
        self._interval_s = interval_s
        self._sleep = sleep
        self._stat = stat if stat is not None else (lambda: self.path.stat())

    def _signature(self) -> tuple[int, int] | None:
        try:
            info = self._stat()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    async def run(self) -> None:
        signature = self._signature()
        while True:
            await self._sleep(self._interval_s)
            current = self._signature()
            if current != signature:
                signature = current
                self._on_change()


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
    ``poller.snapshot()`` on every wake (a full snapshot frame is re-sent
    when the change is outside what the deltas can express) — including heartbeat timeouts, which
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
                        if needs_full_snapshot(previous, current):
                            messages = [{"type": "snapshot", "snapshot": current}]
                        else:
                            messages = compute_deltas(previous, current)
                        for message in messages:
                            await websocket.send_text(_frame(message))
                        previous = current
                        # Only a frame actually sent resets the heartbeat
                        # timer; unchanged rebuilds still yield a new
                        # snapshot object and must not starve heartbeats.
                        if messages:
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
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.gather(
                        writer_task, reader_task, return_exceptions=True
                    )
                # The session must always end with an explicit close frame: a
                # bare return completes the ASGI app without one, and the peer
                # would wait forever (uvicorn tolerates a close after peer
                # disconnect).
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await websocket.close()
            if cancelled is not None:
                raise cancelled
        finally:
            limiter.release()

    return endpoint

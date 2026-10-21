# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Live typed-delta event stream for the dashboard websocket.

This module computes the five locked delta message types between successive
poller snapshots (protocol default 9) and hosts the /ws/events endpoint with
its connection limiter and the tasks.json stat watcher (protocol default 10).
Nothing here logs, and no function performs I/O in the delta computation.
"""

from __future__ import annotations

import time
from typing import Any

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

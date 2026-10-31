# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Where sandbox lifecycle events go.

* ``OutboxSink``: append-only CloudEvents JSONL (0600, ``flock``), the hand-off
  point for the future NATS bridge (see event-mesh decision).
* ``HandlerSink``: ``on_sandbox_event`` into a task store, once per delivery.
* ``dashboard_task_store``: the live Command Center ``tasks.json`` through the
  dashboard's own locked helpers when that package is importable. The
  fallback is ``None`` (outbox only). This module never writes ``tasks.json``
  itself.

A sink failure never hides the sandbox operation's result: ``FanoutSink``
records the error on the event result and keeps going.
"""

from __future__ import annotations

import fcntl
import importlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from omega_prime.sdlc_events.handlers import TaskStorePort
from omega_prime.sdlc_events.idempotency import IdempotencyStore
from omega_prime.sdlc_events.sandbox import SandboxEvent, on_sandbox_event


class EventSink(Protocol):
    def emit(self, event: SandboxEvent) -> None: ...


@dataclass
class OutboxSink:
    path: Path

    def emit(self, event: SandboxEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(event.to_cloudevent(), sort_keys=True) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class HandlerSink:
    store: TaskStorePort
    idem: IdempotencyStore

    def emit(self, event: SandboxEvent) -> None:
        on_sandbox_event(event, store=self.store, idem=self.idem)


@dataclass
class MemorySink:
    events: list[SandboxEvent] = field(default_factory=list)

    def emit(self, event: SandboxEvent) -> None:
        self.events.append(event)


@dataclass
class FanoutSink:
    sinks: Sequence[EventSink]
    errors: list[str] = field(default_factory=list)

    def emit(self, event: SandboxEvent) -> None:
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception as exc:  # a sink must not undo the sandbox op
                self.errors.append(f"{type(sink).__name__}: {type(exc).__name__}")


OWNER_KIND = "sandbox-control"


class _DashboardTaskStore:
    """``TaskStorePort`` over the dashboard's locked tasks.json helpers."""

    def __init__(self, path: Path, status_file: Any, task_ids: Any) -> None:
        self._path = path
        self._status = status_file
        self._ids = task_ids

    def upsert_note(self, task_id: str, state: str, note: str) -> None:
        now = datetime.now().astimezone().replace(microsecond=0)
        stamp = now.isoformat()
        with self._status.locked_tasks(self._path, write=True) as data:
            self._ids._backfill(data)
            row = next((t for t in data["tasks"] if t.get("id") == task_id), None)
            if row is None:
                title = f"Sandbox {task_id.removeprefix('gb:sbx-')}"
                # The dashboard rejects task rows without owner.kind/ref.
                row = {
                    "id": task_id,
                    "title": title,
                    "owner": {"kind": OWNER_KIND, "ref": task_id},
                    "state": state,
                    "started_at": stamp,
                    "links": [],
                    "pid": None,
                    "progress": None,
                    "stale": None,
                    "last_heartbeat_at": None,
                }
                data["tasks"].append(row)
            record = self._ids._record(data, task_id, row["title"])
            self._ids._observe(record, state, now)
            row.update(
                state=state, note=note, last_activity_at=stamp, seq=record["seq"]
            )
            data["updated_at"] = stamp
            self._status._write_locked(data, self._path)


def dashboard_task_store(path: Path) -> TaskStorePort | None:
    """The live tasks.json store, or ``None`` when the dashboard is not installed."""
    try:
        status_file = importlib.import_module("omega_prime.dashboard.status_file")
        task_ids = importlib.import_module("omega_prime.dashboard.task_ids")
    except ImportError:
        return None
    needed = (
        (status_file, "locked_tasks"),
        (status_file, "_write_locked"),
        (task_ids, "_backfill"),
        (task_ids, "_record"),
        (task_ids, "_observe"),
    )
    if not all(hasattr(module, name) for module, name in needed):
        return None
    return _DashboardTaskStore(path, status_file, task_ids)

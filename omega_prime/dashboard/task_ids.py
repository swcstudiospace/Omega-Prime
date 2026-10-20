# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Durable, process-safe dashboard task numbering."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any
from omega_prime.dashboard.redact import redact_value
from omega_prime.dashboard.status_file import _canonical_identity, _write_locked, locked_tasks


def format_display_id(seq: int, title: str) -> str:
    if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
        raise ValueError("seq must be a positive integer")
    return f"{seq:05d} - {redact_value(title)}"


def _key(identity: str) -> str:
    if not isinstance(identity, str) or not identity:
        raise ValueError("identity must be a non-empty string")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _next(data: dict[str, Any]) -> int:
    row_max = max(
        (row["seq"] for name in ("tasks", "waiting_on_ove", "finished") for row in data[name] if "seq" in row),
        default=0,
    )
    registry_max = max((record["seq"] for record in data["identity_registry"].values()), default=0)
    return max(data["next_seq"], row_max + 1, registry_max + 1)


def _record(
    data: dict[str, Any],
    identity: str,
    title: str,
    preferred_seq: int | None = None,
) -> dict[str, Any]:
    key = _key(identity)
    registry = data["identity_registry"]
    record = registry.get(key)
    if record is None:
        seq = _next(data) if preferred_seq is None else preferred_seq
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
            raise ValueError("seq must be a positive integer")
        if any(existing["seq"] == seq for existing in registry.values()):
            raise ValueError("duplicate sequence ownership")
        record = registry[key] = {
            "seq": seq,
            "title": redact_value(title),
            "timeline": [],
            "last_state": None,
        }
        data["next_seq"] = max(data["next_seq"], seq + 1)
    else:
        if preferred_seq is not None and record["seq"] != preferred_seq:
            raise ValueError("row sequence conflicts with identity registry")
        record["title"] = redact_value(title)
    return record


def _pending_backfill_count(data: dict[str, Any]) -> int:
    pending: set[str] = set()
    for category in ("tasks", "waiting_on_ove", "finished"):
        for row in data[category]:
            key = _key(_canonical_identity(row["id"]))
            if key not in data["identity_registry"] and "seq" not in row:
                pending.add(key)
    return len(pending)
def _backfill(data: dict[str, Any]) -> None:
    for category in ("tasks", "waiting_on_ove", "finished"):
        for row in data[category]:
            identity = _canonical_identity(row["id"])
            record = _record(
                data,
                identity,
                row.get("title", row.get("ask", "")),
                row.get("seq"),
            )
            row["seq"] = record["seq"]



def _observe(record: dict[str, Any], state: str, now: datetime) -> None:
    if record["last_state"] != state:
        stamp = now.isoformat()
        record["timeline"].append({"at": stamp, "state": redact_value(state)})
        record["last_state"] = redact_value(state)


def next_task_id(*, path: Path) -> int:
    with locked_tasks(path, write=False) as data:
        return _next(data) + _pending_backfill_count(data)


def allocate_task_id(*, path: Path, identity: str, title: str, now: datetime) -> dict[str, Any]:
    with locked_tasks(path, write=True) as data:
        _backfill(data)
        record = _record(data, identity, title)
        _write_locked(data, path)
        return {"seq": record["seq"], "display_id": format_display_id(record["seq"], title)}


def assign_task_ids(*, path: Path, now: datetime) -> dict[str, Any]:
    with locked_tasks(path, write=True) as data:
        _backfill(data)
        _write_locked(data, path)
        return data


def number_snapshot(snapshot: Any, *, path: Path, now: datetime) -> Any:
    """Allocate bridge identities first, then collector-only identities deterministically."""
    with locked_tasks(path, write=True) as data:
        _backfill(data)
        rows = [*snapshot.active, *snapshot.waiting_on_ove, *snapshot.finished]
        unique: dict[str, Any] = {}
        for row in rows:
            unique.setdefault(row.id, row)
        for identity in sorted(unique):
            row = unique[identity]
            title = getattr(row, "title", getattr(row, "ask", ""))
            record = _record(data, identity, title)
            state = getattr(row, "state", "waiting" if hasattr(row, "ask") else getattr(row, "result", "finished"))
            _observe(record, str(state), now)
        _write_locked(data, path)
        for row in rows:
            record = data["identity_registry"][_key(row.id)]
            row.seq = record["seq"]
            row.display_id = format_display_id(record["seq"], getattr(row, "title", getattr(row, "ask", "")))
            row.timeline = list(record["timeline"])
        return snapshot

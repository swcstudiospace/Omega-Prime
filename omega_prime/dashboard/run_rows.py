# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""tasks.json row lifecycle for visible sandbox runs (W113-02).

``start``/``touch``/``finish`` maintain one additive-field row per run in
the dashboard tasks.json. Every mutation is a single locked
read-modify-write — ``locked_tasks(path, write=True)`` plus
``_write_locked`` inside the block — so no second writer, no partial JSON,
no key reordering; unknown fields on other rows survive byte-for-byte
(the store's documented additive-field design).

Rows are one run's record: ``start`` pins ``started_at`` at first creation
and never resets it; ``touch`` is a no-op for missing or terminal rows, so
late events never resurrect or churn the store; ``finish`` upserts
(a run whose start was suppressed still deserves its terminal record) and
stores a recursively redacted receipt under the single ``receipt`` key.

Functions raise naturally on a corrupt store (``ValueError`` from the
loader): the "visibility never kills runs" suppress discipline belongs to
the engine call sites, exactly as ``omp_driver.run`` wraps ``raise_block``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from omega_prime.credentials.redact import redact_text
from omega_prime.dashboard.status_file import _write_locked, locked_tasks

_TERMINAL_STATES = ("done", "failed")

__all__ = ["finish", "start", "touch"]


def _now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _row_by_id(data: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    tasks = data.get("tasks", [])
    return next((row for row in tasks if row.get("id") == task_id), None)


def _redact(value: Any) -> Any:
    """Recursive redact_text pass: strings through, containers rebuilt."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def start(
    path: Any,
    task_id: str,
    *,
    job_id: str,
    stage: str | None,
    pane: str | None,
) -> None:
    """Create or refresh the run's row in the ``working`` state.

    A missing row is created with ``started_at``/``last_activity_at``
    stamped now and ``herdr_pane`` only when a pane actually opened. A
    non-terminal row moves back to ``working`` with ``started_at``
    untouched (a crash-resume must not look brand new). A terminal
    (``done``/``failed``) row is never resurrected: no write at all.
    """
    with locked_tasks(path, write=True) as data:
        row = _row_by_id(data, task_id)
        if row is None:
            row = {
                "id": task_id,
                "state": "working",
                "job_id": job_id,
                "stage": stage,
                "started_at": _now(),
                "last_activity_at": _now(),
            }
            if pane is not None:
                row["herdr_pane"] = pane
            data.setdefault("tasks", []).append(row)
            _write_locked(data, path)
            return
        if row.get("state") in _TERMINAL_STATES:
            return
        row["state"] = "working"
        row["job_id"] = job_id
        row["stage"] = stage
        row["last_activity_at"] = _now()
        if pane is not None:
            row["herdr_pane"] = pane
        _write_locked(data, path)


def touch(path: Any, task_id: str, event: Mapping[str, Any] | str) -> None:
    """Stamp ``last_activity_at`` on the owning row (one locked RMW).

    ``last_event`` gets a short label for question/stage_done-shaped
    events (``"question"``, ``"stage_done <status>"`` collapsed to
    ``"stage_done"`` when the status is empty) or the event verbatim when
    it is already a plain string; any other shape leaves ``last_event``
    unchanged. A missing or terminal row is a no-op — no write at all.
    Only this row's two keys are ever assigned.
    """
    with locked_tasks(path, write=True) as data:
        row = _row_by_id(data, task_id)
        if row is None or row.get("state") in _TERMINAL_STATES:
            return
        row["last_activity_at"] = _now()
        label = _event_label(event)
        if label is not None:
            row["last_event"] = label
        _write_locked(data, path)


def _event_label(event: Mapping[str, Any] | str) -> str | None:
    """Short ``last_event`` label, or ``None`` to leave the field alone."""
    if isinstance(event, Mapping):
        kind = event.get("event")
        if kind == "question":
            return "question"
        if kind == "stage_done":
            status = str(event.get("status") or "").strip()
            return f"stage_done {status}" if status else "stage_done"
        return None
    if isinstance(event, str):
        return event
    return None


def finish(path: Any, task_id: str, ok: bool, receipt: Mapping[str, Any]) -> None:
    """Set the row terminal (``done``/``failed``) with a redacted receipt.

    Upsert: a missing row is created terminal (``started_at`` now) so a
    run whose start was suppressed still gets its record. The receipt is
    stored under the single key ``receipt`` with every string redacted
    recursively — the secrets-excluded backstop. ``last_activity_at`` is
    stamped on both paths.
    """
    with locked_tasks(path, write=True) as data:
        row = _row_by_id(data, task_id)
        state = "done" if ok else "failed"
        redacted = _redact(dict(receipt))
        stamp = _now()
        if row is None:
            data.setdefault("tasks", []).append(
                {
                    "id": task_id,
                    "state": state,
                    "started_at": stamp,
                    "last_activity_at": stamp,
                    "receipt": redacted,
                }
            )
        else:
            row["state"] = state
            row["last_activity_at"] = stamp
            row["receipt"] = redacted
        _write_locked(data, path)

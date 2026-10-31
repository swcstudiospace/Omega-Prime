# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""tasks.json row lifecycle for visible sandbox runs (W113-02).

``start``/``touch``/``finish`` maintain one additive-field row per run in
the dashboard tasks.json. Every mutation is a single locked
read-modify-write — ``locked_tasks(path, write=True)`` plus
``_write_locked`` inside the block — so no second writer and no partial
JSON. Rows follow the validated tasks.json v1 schema: a created row carries
``title`` and an ``engine`` owner; the run's own fields (``job_id``,
``stage``, ``last_event``, ``pane``, ``receipt``) are the store's optional
engine-run fields. ``pane`` is whatever the launcher returned (a Herdr pane
id or ``tmux:<session>``); a Herdr id also fills ``herdr_pane`` so the
Command Center can attach to it.

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
from datetime import UTC, datetime
from typing import Any

from omega_prime.credentials.redact import redact_text
from omega_prime.dashboard.status_file import _PANE_ID, _write_locked, locked_tasks

_TERMINAL_STATES = ("done", "failed")

__all__ = ["finish", "start", "touch"]


def _now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def _row_by_id(data: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    tasks = data.get("tasks", [])
    return next((row for row in tasks if row.get("id") == task_id), None)


def _run_of(row: Mapping[str, Any]) -> str | None:
    """The job id of the run a row records (row field, else its receipt)."""
    job = row.get("job_id")
    if isinstance(job, str) and job:
        return job
    receipt = row.get("receipt")
    if isinstance(receipt, Mapping):
        job = receipt.get("job_id")
        if isinstance(job, str) and job:
            return job
    return None


def _new_row(
    task_id: str, state: str, job_id: str | None, stamp: str
) -> dict[str, Any]:
    """A v1 tasks row for a run the store did not know yet."""
    return {
        "id": task_id,
        "title": task_id,
        "owner": {"kind": "engine", "ref": job_id or task_id},
        "state": state,
        "started_at": stamp,
        "last_activity_at": stamp,
    }


def _set_pane(row: dict[str, Any], pane: str) -> None:
    row["pane"] = pane
    row["herdr_pane"] = {"pane_id": pane} if _PANE_ID.fullmatch(pane) else None


def _drop_pane(row: dict[str, Any]) -> None:
    row.pop("pane", None)
    row["herdr_pane"] = None


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
) -> bool:
    """Create or refresh the run's row in the ``working`` state.

    Returns ``True`` when the row is now this run's record, ``False`` when
    it was left untouched (see below); a caller that gets ``False`` owns no
    row and must not touch or finish it.

    A missing row is created with ``started_at``/``last_activity_at``
    stamped now and ``herdr_pane`` only when a pane actually opened. A
    non-terminal row moves back to ``working`` with ``started_at``
    untouched (a crash-resume must not look brand new), stamped only when
    the row never had one. A terminal (``done``/``failed``) row of this
    same ``job_id``, or of no known run, is never resurrected: no write at
    all. A terminal row left by an earlier run is this run's record now: it starts fresh
    (``started_at`` now, the old receipt and pane dropped), so the
    dashboard never shows the old run while the new one works.
    """
    with locked_tasks(path, write=True) as data:
        row = _row_by_id(data, task_id)
        if row is None:
            row = _new_row(task_id, "working", job_id, _now())
            row["job_id"] = job_id
            row["stage"] = stage
            if pane is not None:
                _set_pane(row, pane)
            data.setdefault("tasks", []).append(row)
            _write_locked(data, path)
            return True
        if row.get("state") in _TERMINAL_STATES:
            if _run_of(row) in (None, job_id):
                return False
            row.pop("receipt", None)
            _drop_pane(row)
            row["started_at"] = _now()
        row["state"] = "working"
        row["job_id"] = job_id
        row["stage"] = stage
        if not row.get("started_at"):
            row["started_at"] = _now()
        row["last_activity_at"] = _now()
        if pane is not None:
            _set_pane(row, pane)
        _write_locked(data, path)
        return True


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


def finish(
    path: Any,
    task_id: str,
    ok: bool,
    receipt: Mapping[str, Any],
    *,
    job_id: str | None = None,
) -> None:
    """Set the row terminal (``done``/``failed``) with a redacted receipt.

    With ``job_id``, only the record that run started is finished: a row
    a later run has taken over (another ``job_id``) is left alone.

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
            created = _new_row(task_id, state, job_id, stamp)
            created["receipt"] = redacted
            if job_id is not None:
                created["job_id"] = job_id
            data.setdefault("tasks", []).append(created)
        elif job_id is not None and _run_of(row) not in (None, job_id):
            return
        else:
            row["state"] = state
            row["last_activity_at"] = stamp
            row["receipt"] = redacted
        _write_locked(data, path)

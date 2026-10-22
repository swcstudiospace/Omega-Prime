# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Per-task staleness for working/blocked tasks.json rows (protocol default 1).

Evidence is injectable and lazy: pid liveness via /proc, Herdr pane state via a
subprocess adapter. A pane listing that cannot be produced is ``None``
("unavailable") and suppresses both pane reasons — a missing herdr binary must
never fabricate pane_gone or pane_exited. Every verdict's ``since`` is the row's
newest liveness reference timestamp (never the current time) so unchanged
evidence yields byte-identical snapshots across poller rebuilds.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.terminal import resolve_task_pane

DEFAULT_STALE_MINUTES = 20.0
REASONS = ("pid_dead", "pane_gone", "pane_exited", "no_heartbeat")

PidAlive = Callable[[int], bool]
PaneLister = Callable[[], dict[str, dict[str, Any]] | None]
Clock = Callable[[], datetime]

_STALE_STATES = ("working", "blocked")
_PANE_LIST_KEYS = ("panes", "result", "items")
_EXITED_STATUSES = ("exited", "dead", "closed")
# Sentinel for stale_map's ``panes``: "ask the pane lister" (the default),
# as opposed to an explicit listing or an explicit ``None`` ("unavailable").
LOAD_PANES: Any = object()


def _canonical_identity(identity: str) -> str:
    """Mirror model.py's tasks_json identity rule (model.py:663)."""
    return identity if ":" in identity else f"tasks:{identity}"


def last_signal(row: dict[str, Any]) -> datetime | None:
    """The row's newest parseable liveness reference (heartbeat or activity)."""
    best: datetime | None = None
    for field in ("last_heartbeat_at", "last_activity_at"):
        value = row.get(field)
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if best is None or parsed > best:
            best = parsed
    return best


def no_heartbeat(
    row: dict[str, Any], *, now: datetime, stale_minutes: float = DEFAULT_STALE_MINUTES
) -> bool:
    """True when the row's newest reference is at least stale_minutes old.

    A row carrying no parseable reference never qualifies: there is no
    heartbeat evidence to age, and ``since`` would have nothing stable to
    anchor to.
    """
    reference = last_signal(row)
    if reference is None:
        return False
    return (now - reference).total_seconds() >= stale_minutes * 60


def normalize_pane_list(payload: Any) -> dict[str, dict[str, Any]] | None:
    """Defensively normalize a `herdr pane list` payload; None when unusable.

    A genuinely empty listing is ``{}`` (Herdr is up, no panes). A response
    whose shape is unrecognized, or whose entries carry no usable pane id,
    is ``None`` so callers never mistake a bad response for "every pane is
    gone". Envelopes such as ``{"result": {"panes": [...]}}`` are unwrapped.
    """
    entries: Any = None
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        for key in _PANE_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                entries = value
                break
            if isinstance(value, dict):
                if any(name in value for name in _PANE_LIST_KEYS):
                    return normalize_pane_list(value)
                entries = [
                    {"pane_id": pane_id, **info} if isinstance(info, dict) else info
                    for pane_id, info in value.items()
                    if isinstance(pane_id, str)
                ]
                break
    if not isinstance(entries, list):
        return None
    panes: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pane_id = entry.get("pane_id")
        if not isinstance(pane_id, str) or not pane_id:
            pane_id = entry.get("id")
        if not isinstance(pane_id, str) or not pane_id:
            continue
        panes[pane_id] = entry
    if entries and not panes:
        return None
    return panes


def _default_pane_lister() -> dict[str, dict[str, Any]] | None:
    """Subprocess adapter shaped like herdr_launch._run; never raises."""
    try:
        result = subprocess.run(
            ["herdr", "pane", "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return normalize_pane_list(json.loads(result.stdout))
    except (json.JSONDecodeError, ValueError):
        return None


def _default_pid_alive(pid: int) -> bool:
    """/proc existence check — the same root the procs collector uses."""
    return (Path("/proc") / str(pid)).exists()


def pane_exited(info: dict[str, Any]) -> bool:
    """True when a pane-list entry reports an exited flag or exit status."""
    if info.get("exited"):
        return True
    status = info.get("status")
    return isinstance(status, str) and status.lower() in _EXITED_STATUSES


class StaleChecker:
    """Evaluate {reason, since} staleness for working/blocked task rows."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        pid_alive: PidAlive | None = None,
        pane_lister: PaneLister | None = None,
        stale_minutes: float = DEFAULT_STALE_MINUTES,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        self.pid_alive = pid_alive or _default_pid_alive
        self.pane_lister = pane_lister or _default_pane_lister
        self.stale_minutes = stale_minutes

    def _now(self, now: datetime | None) -> datetime:
        current = now if now is not None else self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return current

    def stale_reason(
        self, row: dict[str, Any], *, now: datetime | None = None
    ) -> dict[str, str | None] | None:
        """{"reason", "since"} for the first matching evidence, else None."""
        if not isinstance(row, dict) or row.get("state") not in _STALE_STATES:
            return None
        panes: dict[str, dict[str, Any]] | None = None
        panes_loaded = False

        def pane_map() -> dict[str, dict[str, Any]] | None:
            nonlocal panes, panes_loaded
            if not panes_loaded:
                panes_loaded = True
                panes = self.pane_lister()
            return panes

        return self._evaluate(row, self._now(now), pane_map)

    def stale_map(
        self,
        rows: list[Any],
        *,
        now: datetime | None = None,
        panes: Any = LOAD_PANES,
    ) -> dict[str, dict[str, str | None] | None]:
        """Batch form: pane lister at most once, keyed by row identity.

        ``panes`` lets a caller supply pane evidence it already holds (a
        listing, or ``None`` for "unavailable") so the batch never waits on
        Herdr itself; by default the lister is asked lazily.
        """
        current = self._now(now)
        panes_loaded = panes is not LOAD_PANES
        if not panes_loaded:
            panes = None

        def pane_map() -> dict[str, dict[str, Any]] | None:
            nonlocal panes, panes_loaded
            if not panes_loaded:
                panes_loaded = True
                panes = self.pane_lister()
            return panes

        out: dict[str, dict[str, str | None] | None] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            verdict: dict[str, str | None] | None = None
            if row.get("state") in _STALE_STATES:
                verdict = self._evaluate(row, current, pane_map)
            out[_canonical_identity(row["id"])] = verdict
        return out

    def _evaluate(
        self,
        row: dict[str, Any],
        current: datetime,
        pane_map: Callable[[], dict[str, dict[str, Any]] | None],
    ) -> dict[str, str | None] | None:
        anchor: str | None = None
        reference = last_signal(row)
        if reference is not None:
            anchor = reference.isoformat()

        pid = row.get("pid")
        if (
            isinstance(pid, int)
            and not isinstance(pid, bool)
            and pid > 0
            and not self.pid_alive(pid)
        ):
            return {"reason": "pid_dead", "since": anchor}

        # The same mapping the terminal and reconciler use: herdr_pane, or an
        # owner of kind herdr-pane whose ref is the pane id.
        pane_id = resolve_task_pane(row)
        if pane_id:
            panes = pane_map()
            if panes is not None:
                info = panes.get(pane_id)
                if info is None:
                    return {"reason": "pane_gone", "since": anchor}
                if pane_exited(info):
                    return {"reason": "pane_exited", "since": anchor}

        if no_heartbeat(row, now=current, stale_minutes=self.stale_minutes):
            return {"reason": "no_heartbeat", "since": anchor}
        return None

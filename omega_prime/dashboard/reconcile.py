# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Rebuild tasks.json liveness from Herdr pane/process evidence and /proc.

Dry-run by default: prints a redacted JSON report and never touches the file.
--apply writes only through locked_tasks + _write_locked: it attaches orphan
panes whose workspace label matches a pane-less task row and stamps stale
markers onto rows verified dead or heartbeatless. It never deletes rows and
never changes a row's state, title or owner. A root updated_at younger than 15
minutes blocks all row mutations unless --force.

Usage:
    python3 -m omega_prime.dashboard.reconcile [--tasks PATH] [--apply]
        [--force] [--stale-minutes N]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard import stale
from omega_prime.dashboard.herdr_launch import _run, workspace_label
from omega_prime.dashboard.redact import redact_value
from omega_prime.dashboard.stale import normalize_pane_list
from omega_prime.dashboard.status_file import _PANE_ID, _write_locked, locked_tasks

Runner = Any  # Callable[[list[str]], str]
PidAlive = Any  # Callable[[int], bool]

_PROTECTION_MINUTES = 15.0


def _pane_map(runner: Runner) -> dict[str, dict[str, Any]] | None:
    """`herdr pane list` through the injectable runner; None when unusable."""
    try:
        payload = json.loads(runner(["herdr", "pane", "list"]))
    except (OSError, ValueError):
        return None
    return normalize_pane_list(payload)


def _pane_pid(runner: Runner, pane_id: str) -> int | None:
    """Per-pane pid via `herdr pane process-info`; None without usable evidence.

    The pane id reaches argv only after matching the _PANE_ID pattern.
    """
    if not _PANE_ID.fullmatch(pane_id):
        return None
    try:
        payload = json.loads(
            runner(["herdr", "pane", "process-info", "--pane", pane_id])
        )
    except (OSError, ValueError):
        return None
    source: Any = payload
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        source = payload["result"]
    pid = source.get("pid") if isinstance(source, dict) else None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return pid


def _positive_pid(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _row_verdict(
    row: dict[str, Any],
    panes: dict[str, dict[str, Any]] | None,
    runner: Runner,
    pid_alive: PidAlive,
    *,
    now: datetime,
    stale_minutes: float,
) -> str | None:
    """One verdict per working/blocked row; first match wins.

    Mirrors StaleChecker's evidence order: the row's own recorded pid is
    checked against /proc regardless of pane availability, then pane absence,
    the pane's exited flag/status, the pane process pid, and finally
    heartbeat age.
    """
    row_pid = _positive_pid(row.get("pid"))
    if row_pid is not None and not pid_alive(row_pid):
        return "pid_dead"
    pane = row.get("herdr_pane")
    pane_id = pane.get("pane_id") if isinstance(pane, dict) else None
    if panes is not None and isinstance(pane_id, str) and pane_id:
        info = panes.get(pane_id)
        if info is None:
            return "pane_gone"
        if stale.pane_exited(info):
            return "pane_exited"
        evidence_pid = _pane_pid(runner, pane_id)
        if evidence_pid is not None and not pid_alive(evidence_pid):
            return "pid_dead"
    if stale.no_heartbeat(row, now=now, stale_minutes=stale_minutes):
        return "no_heartbeat"
    return None


def _orphan_attaches(
    data: dict[str, Any], panes: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """Attach candidates: unreferenced panes whose workspace labels a pane-less row.

    Several panes matching one row pick the lowest pane id; one pane matching
    several rows prefers the first row in file order.
    """
    referenced: set[str] = set()
    for row in data["tasks"]:
        pane = row.get("herdr_pane") if isinstance(row, dict) else None
        if isinstance(pane, dict) and isinstance(pane.get("pane_id"), str):
            referenced.add(pane["pane_id"])
    attaches: list[dict[str, str]] = []
    claimed: set[str] = set()
    for row in data["tasks"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        pane = row.get("herdr_pane")
        if isinstance(pane, dict) and pane.get("pane_id"):
            continue
        label = workspace_label(row["id"])
        matches = sorted(
            pane_id
            for pane_id, info in panes.items()
            if pane_id not in referenced
            and pane_id not in claimed
            and isinstance(info.get("workspace"), str)
            and info["workspace"] == label
        )
        if matches:
            claimed.add(matches[0])
            attaches.append(
                {"id": row["id"], "pane_id": matches[0], "workspace": label}
            )
    return attaches


def _updated_age_minutes(updated_at: Any, now: datetime) -> float | None:
    if not isinstance(updated_at, str):
        return None
    try:
        stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (now - stamp).total_seconds() / 60.0


def main(
    argv: list[str] | None = None,
    *,
    runner: Runner | None = None,
    pid_alive: PidAlive | None = None,
    clock: Any = None,
) -> int:
    parser = argparse.ArgumentParser(prog="reconcile")
    parser.add_argument(
        "--tasks",
        default=str(Path.home() / ".omega-prime/dashboard/tasks.json"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--stale-minutes", type=float, default=stale.DEFAULT_STALE_MINUTES
    )
    args = parser.parse_args(argv)
    path = Path(args.tasks)
    run = runner or _run
    alive = pid_alive or stale._default_pid_alive
    now = clock() if clock is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    try:
        with locked_tasks(path, write=args.apply) as data:
            return _reconcile(args, data, path, run, alive, now)
    except ValueError as exc:
        error = redact_value({"error": "invalid tasks file", "detail": str(exc)})
        sys.stderr.write(json.dumps(error, sort_keys=True) + "\n")
        return 1


def _reconcile(
    args: argparse.Namespace,
    data: dict[str, Any],
    path: Path,
    run: Runner,
    alive: PidAlive,
    now: datetime,
) -> int:
    age = _updated_age_minutes(data.get("updated_at"), now)
    protected = age is not None and age < _PROTECTION_MINUTES and not args.force

    panes = _pane_map(run)
    dead: list[dict[str, Any]] = []
    heartbeatless: list[dict[str, Any]] = []
    marked: list[tuple[dict[str, Any], str]] = []
    for row in data["tasks"]:
        if not isinstance(row, dict) or row.get("state") not in ("working", "blocked"):
            continue
        verdict = _row_verdict(
            row, panes, run, alive, now=now, stale_minutes=args.stale_minutes
        )
        if verdict in ("pid_dead", "pane_gone", "pane_exited"):
            pane = row.get("herdr_pane")
            dead.append(
                {
                    "id": row.get("id"),
                    "reason": verdict,
                    "pane_id": pane.get("pane_id") if isinstance(pane, dict) else None,
                    "pid": row.get("pid"),
                }
            )
            marked.append((row, verdict))
        elif verdict == "no_heartbeat":
            reference = stale.last_signal(row)
            heartbeatless.append(
                {
                    "id": row.get("id"),
                    "since": reference.isoformat() if reference else None,
                }
            )
            marked.append((row, verdict))

    attaches = _orphan_attaches(data, panes) if panes is not None else []

    if args.apply and not protected:
        for entry in attaches:
            for row in data["tasks"]:
                if isinstance(row, dict) and row.get("id") == entry["id"]:
                    row["herdr_pane"] = {
                        "pane_id": entry["pane_id"],
                        "workspace": entry["workspace"],
                    }
                    break
        for row, verdict in marked:
            reference = stale.last_signal(row)
            row["stale"] = {
                "reason": verdict,
                "since": (reference or now).isoformat(),
            }
        data["updated_at"] = now.isoformat()
        _write_locked(data, path)

    report = {
        "herdr": "unavailable" if panes is None else "ok",
        "stale_minutes": args.stale_minutes,
        "protected": protected,
        "dead": dead,
        "no_heartbeat": heartbeatless,
        "attach": attaches,
    }
    print(json.dumps(redact_value(report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import copy
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard import stale
from omega_prime.dashboard.herdr_launch import _run, workspace_label
from omega_prime.dashboard.redact import redact_value
from omega_prime.dashboard.stale import normalize_pane_list
from omega_prime.dashboard.status_file import _PANE_ID, _write_locked, locked_tasks
from omega_prime.dashboard.terminal import resolve_task_pane

Runner = Any  # Callable[[list[str]], str]
PidAlive = Any  # Callable[[int], bool]

_PROTECTION_MINUTES = 15.0


def _pane_map(runner: Runner) -> dict[str, dict[str, Any]] | None:
    """`herdr pane list` through the injectable runner; None when unusable."""
    try:
        payload = json.loads(runner(["herdr", "pane", "list"]))
    except (OSError, ValueError, subprocess.SubprocessError):
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
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    source: Any = payload
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        source = payload["result"]
    pid = source.get("pid") if isinstance(source, dict) else None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return pid


def _mapped_pane(row: Any) -> str | None:
    """The pane a row already owns: herdr_pane first, else a herdr-pane owner.

    A recorded herdr_pane id is returned as-is (even if malformed) so a bad
    mapping is still judged instead of being treated as missing.
    """
    if not isinstance(row, dict):
        return None
    pane = row.get("herdr_pane")
    if isinstance(pane, dict):
        pane_id = pane.get("pane_id")
        if isinstance(pane_id, str) and pane_id:
            return pane_id
    return resolve_task_pane(row)


def _positive_pid(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _row_verdict(
    row: dict[str, Any],
    panes: dict[str, dict[str, Any]] | None,
    pane_pids: dict[str, int | None],
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
    pane_id = _mapped_pane(row)
    if panes is not None and pane_id is not None:
        info = panes.get(pane_id)
        if info is None:
            return "pane_gone"
        if stale.pane_exited(info):
            return "pane_exited"
        evidence_pid = pane_pids.get(pane_id)
        if evidence_pid is not None and not pid_alive(evidence_pid):
            return "pid_dead"
    if stale.no_heartbeat(row, now=now, stale_minutes=stale_minutes):
        return "no_heartbeat"
    return None


def _orphan_attaches(
    data: dict[str, Any], panes: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """Attach candidates: unreferenced live panes whose workspace labels a pane-less row.

    A pane counts as referenced when any task or finished row owns it, through
    herdr_pane or a herdr-pane owner; exited panes are never chosen.
    Several panes matching one row pick the lowest pane id; one pane matching
    several rows prefers the first row in file order.
    """
    referenced: set[str] = set()
    for bucket in ("tasks", "finished"):
        rows = data.get(bucket)
        for row in rows if isinstance(rows, list) else []:
            owned = _mapped_pane(row)
            if owned is not None:
                referenced.add(owned)
    attaches: list[dict[str, str]] = []
    claimed: set[str] = set()
    for row in data["tasks"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        if _mapped_pane(row) is not None:
            continue
        label = workspace_label(row["id"])
        matches = sorted(
            pane_id
            for pane_id, info in panes.items()
            if pane_id not in referenced
            and pane_id not in claimed
            and isinstance(info.get("workspace"), str)
            and info["workspace"] == label
            and not stale.pane_exited(info)
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
        # Herdr can take up to 15s per call, and the dashboard takes the same
        # lock on its event loop, so external evidence is gathered with the
        # lock released and the rows are re-read before any write.
        with locked_tasks(path, write=False) as current:
            snapshot = copy.deepcopy(current)
        panes, pane_pids = _gather_evidence(snapshot, run)
        if not args.apply:
            return _reconcile(args, snapshot, path, panes, pane_pids, alive, now)
        gathered = {_row_key(row) for row in snapshot.get("tasks", [])}
        with locked_tasks(path, write=True) as data:
            # A row that changed while Herdr was checked (e.g. record_pane
            # mapped it to a new pane) was never judged by this evidence:
            # leave it for the next pass rather than mark it from stale data.
            return _reconcile(
                args,
                data,
                path,
                panes,
                pane_pids,
                alive,
                now,
                judge=lambda row: _row_key(row) in gathered,
            )
    except ValueError as exc:
        error = redact_value({"error": "invalid tasks file", "detail": str(exc)})
        sys.stderr.write(json.dumps(error, sort_keys=True) + "\n")
        return 1


def _row_key(row: Any) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def _gather_evidence(
    data: dict[str, Any], run: Runner
) -> tuple[dict[str, dict[str, Any]] | None, dict[str, int | None]]:
    """Pane map plus per-pane pids for working/blocked rows, taken unlocked."""
    panes = _pane_map(run)
    pane_pids: dict[str, int | None] = {}
    if panes is None:
        return None, pane_pids
    for row in data["tasks"]:
        if not isinstance(row, dict) or row.get("state") not in ("working", "blocked"):
            continue
        pane_id = _mapped_pane(row)
        if pane_id is None or pane_id in pane_pids:
            continue
        info = panes.get(pane_id)
        if info is None or stale.pane_exited(info):
            continue
        pane_pids[pane_id] = _pane_pid(run, pane_id)
    return panes, pane_pids


def _reconcile(
    args: argparse.Namespace,
    data: dict[str, Any],
    path: Path,
    panes: dict[str, dict[str, Any]] | None,
    pane_pids: dict[str, int | None],
    alive: PidAlive,
    now: datetime,
    judge: Callable[[dict[str, Any]], bool] | None = None,
) -> int:
    age = _updated_age_minutes(data.get("updated_at"), now)
    protected = age is not None and age < _PROTECTION_MINUTES and not args.force

    dead: list[dict[str, Any]] = []
    heartbeatless: list[dict[str, Any]] = []
    marked: list[tuple[dict[str, Any], str]] = []
    for row in data["tasks"]:
        if not isinstance(row, dict) or row.get("state") not in ("working", "blocked"):
            continue
        if judge is not None and not judge(row):
            continue
        verdict = _row_verdict(
            row, panes, pane_pids, alive, now=now, stale_minutes=args.stale_minutes
        )
        if verdict in ("pid_dead", "pane_gone", "pane_exited"):
            dead.append(
                {
                    "id": row.get("id"),
                    "reason": verdict,
                    "pane_id": _mapped_pane(row),
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
                    if judge is not None and not judge(row):
                        break
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

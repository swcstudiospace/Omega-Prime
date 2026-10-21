# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Launch a task's work inside a Herdr pane and record the pane on the task row.

Every long-running task (GSD drive, omp session, sandbox run) should start here so the
Command Center can show its live terminal. Usage:

    python -m omega_prime.dashboard.herdr_launch --id gb:00031 --title "00031 - ..." \
        --cwd /workspace/x -- bash drive.sh
    python -m omega_prime.dashboard.herdr_launch --id gb:00031 --title "..." --attach w6:p1
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.status_file import (
    _canonical_identity,
    _write_locked,
    locked_tasks,
)

Runner = Callable[[list[str]], str]
_PANE = re.compile(r"^w[0-9A-Za-z]+:p[0-9A-Za-z]+$")
_LABEL = re.compile(r"[^a-z0-9_-]+")


def _run(argv: list[str]) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=15, check=False
    )
    if result.returncode != 0:
        raise OSError(f"herdr {argv[1:3]} failed")
    return result.stdout


class PaneNotRecordedError(RuntimeError):
    """Work started in a pane, but the pane could not be saved on the task."""

    def __init__(self, pane_id: str) -> None:
        super().__init__(
            f"work is running in Herdr pane {pane_id} but the task row was not"
            f" updated; record it with --attach {pane_id} instead of relaunching"
        )
        self.pane_id = pane_id


def workspace_label(identity: str) -> str:
    return _LABEL.sub("-", identity.split(":")[-1].lower()).strip("-")[:32] or "task"


def record_pane(
    *,
    path: Path,
    identity: str,
    title: str,
    pane_id: str,
    workspace: str | None,
    owner_ref: str,
    now: datetime | None = None,
    restart: bool = False,
) -> dict[str, Any]:
    """Link ``pane_id`` to the task row, creating the row if needed.

    Rows match by canonical identity, so ``tasks:a`` and a stored ``a`` are the
    same task. ``restart`` marks new work launched for the task: a finished
    row goes back to ``working``. Attaching an existing pane keeps the state.
    """
    if not _PANE.fullmatch(pane_id):
        raise ValueError("pane_id is invalid")
    stamp = (now or datetime.now().astimezone()).isoformat()
    pane = {"pane_id": pane_id, **({"workspace": workspace} if workspace else {})}
    with locked_tasks(path, write=True) as data:
        wanted = _canonical_identity(identity)
        row = next(
            (t for t in data["tasks"] if _canonical_identity(t["id"]) == wanted),
            None,
        )
        if row is None:
            row = {
                "id": identity,
                "title": title,
                "owner": {"kind": "herdr-pane", "ref": owner_ref},
                "state": "working",
                "started_at": stamp,
                "progress": None,
                "note": None,
                "links": [],
            }
            data["tasks"].append(row)
        row["herdr_pane"] = pane
        row["last_activity_at"] = stamp
        state = row.get("state")
        if restart and state in {"done", "failed"}:
            # New work for a finished task is a fresh run, not the old result.
            row["started_at"] = stamp
        if (restart and state != "working") or state in {"queued", "blocked"}:
            row["state"] = "working"
        data["updated_at"] = stamp
        _write_locked(data, path)
        return dict(row)


def launch(
    *,
    path: Path,
    identity: str,
    title: str,
    cwd: str,
    command: Sequence[str],
    herdr: str = "herdr",
    runner: Runner = _run,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not command:
        raise ValueError("command is required")
    # Check the task file before any work starts, so a bad file cannot leave
    # a running pane with no task link (and a retry cannot run it twice).
    with locked_tasks(path, write=False):
        pass
    label = workspace_label(identity)
    created = json.loads(
        runner(
            [herdr, "workspace", "create", "--cwd", cwd, "--label", label, "--no-focus"]
        )
    )
    pane_id = created["result"]["root_pane"]["pane_id"]
    runner([herdr, "pane", "run", pane_id, shlex.join(command)])
    try:
        return record_pane(
            path=path,
            identity=identity,
            title=title,
            pane_id=pane_id,
            workspace=label,
            owner_ref=cwd,
            now=now,
            restart=True,
        )
    except Exception as exc:
        raise PaneNotRecordedError(pane_id) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="herdr_launch")
    parser.add_argument("--id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--cwd", default=str(Path.cwd()))
    parser.add_argument(
        "--tasks", default=str(Path.home() / ".omega-prime/dashboard/tasks.json")
    )
    parser.add_argument(
        "--attach", help="record an existing pane (wN:pN) instead of creating one"
    )
    parser.add_argument("--workspace")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    path = Path(args.tasks)
    if args.attach:
        row = record_pane(
            path=path,
            identity=args.id,
            title=args.title,
            pane_id=args.attach,
            workspace=args.workspace,
            owner_ref=args.cwd,
        )
    else:
        try:
            row = launch(
                path=path,
                identity=args.id,
                title=args.title,
                cwd=args.cwd,
                command=command,
            )
        except PaneNotRecordedError as exc:
            print(f"herdr_launch: {exc}", file=sys.stderr)
            return 1
    print(f"{row['id']} -> {row['herdr_pane']['pane_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

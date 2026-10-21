# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``omega`` console for job watch and local RPC UI control."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from omega_prime.engine.jobs import (
    JobPathError,
    agent_data_dir,
    job_dir,
    omp_home_override,
    state_dir,
)
from omega_prime.engine.ui_control import UIControlError, UIControlStore
from omega_prime.engine.watch import format_event, iter_events
from omega_prime.engine.watch import main as watch_main


def _identity(args: argparse.Namespace) -> dict[str, str]:
    return {
        "job_id": args.job,
        "session_generation": args.session,
        "turn_generation": args.turn,
        "request_id": args.request,
    }


def _control_store(
    env: dict[str, str], job: str | None, extra_binds: list[str]
) -> UIControlStore:
    state = state_dir(env)
    # Match the engine's writable bind set conservatively. The per-job directory
    # and current working directory cover the default engine invocation;
    # additional work roots must be supplied with --writable-bind.
    binds: list[Path | str] = [Path.cwd(), agent_data_dir(env)]
    if job is not None:
        binds.append(state / "jobs" / job)
    work = env.get("OMEGA_PRIME_WORK_ROOT", "").strip()
    if work:
        binds.append(work)
    home = omp_home_override(env) or (Path.home() / ".omp")
    if home.exists():
        binds.append(home)
    binds.extend(extra_binds)
    return UIControlStore(state, writable_binds=binds)


def _read_status_events(env: dict[str, str], job: str, limit: int = 20) -> list[str]:
    try:
        directory = job_dir(env, job)
    except JobPathError:
        return []
    path = directory / "events.jsonl"
    relevant = {
        "ui_pending",
        "ui_answered",
        "ui_rejected_stale",
        "attached",
        "attach_failed",
        "ui_control_attachment",
        "mcp_attachment",
        "cleanup_conflict",
        "cleanup_failed",
    }
    events = [
        event for event in iter_events(path) or [] if event.get("type") in relevant
    ]
    return [format_event(event) for event in events[-limit:]]


def _emit(payload: Any) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omega")
    sub = parser.add_subparsers(dest="command", required=True)
    watch = sub.add_parser("watch", help="Tail one job's omp event feed")
    watch.add_argument("job")
    watch.add_argument("--follow", action="store_true")
    watch.add_argument("--state-dir", default=None)
    watch.add_argument("--timeout", type=float, default=2.0)

    ui = sub.add_parser("ui", help="Inspect and control local RPC UI requests")
    ui_sub = ui.add_subparsers(dest="ui_command", required=True)
    for command in ("list", "status"):
        item = ui_sub.add_parser(command, help=f"Show {command} local UI state")
        item.add_argument("--job", required=(command == "status"))
        item.add_argument("--state-dir", default=None)
        item.add_argument("--writable-bind", action="append", default=[])
    answer = ui_sub.add_parser("answer", help="Answer one exact pending UI request")
    answer.add_argument("--job", required=True)
    answer.add_argument("--session", required=True)
    answer.add_argument("--turn", required=True)
    answer.add_argument("--request", required=True)
    values = answer.add_mutually_exclusive_group(required=True)
    values.add_argument(
        "--text",
        action="append",
        help="Text answer; repeat in question order for multi-question asks",
    )
    values.add_argument("--choice")
    values.add_argument("--confirm", choices=("true", "false"))
    answer.add_argument("--state-dir", default=None)
    answer.add_argument("--writable-bind", action="append", default=[])
    abort = ui_sub.add_parser("abort", help="Abort an observed pending UI session")
    abort.add_argument("--job", required=True)
    abort.add_argument("--session", required=True)
    abort.add_argument("--state-dir", default=None)
    abort.add_argument("--writable-bind", action="append", default=[])

    args = parser.parse_args(argv)
    if args.command == "watch":
        forwarded = [args.job]
        if args.follow:
            forwarded.append("--follow")
        if args.state_dir:
            forwarded.extend(["--state-dir", args.state_dir])
        forwarded.extend(["--timeout", str(args.timeout)])
        return watch_main(forwarded)

    env = dict(os.environ)
    if getattr(args, "state_dir", None):
        env["OMEGA_PRIME_STATE_DIR"] = args.state_dir
    try:
        with _control_store(env, args.job, args.writable_bind) as store:
            if args.ui_command == "list":
                _emit(store.list_pending(job=args.job))
            elif args.ui_command == "status":
                result = store.status(job=args.job)
                result["events"] = _read_status_events(env, args.job)
                _emit(result)
            elif args.ui_command == "answer":
                if args.text is not None:
                    kind, value = "text", args.text
                elif args.choice is not None:
                    kind, value = "select", args.choice
                else:
                    kind, value = "confirm", args.confirm == "true"
                answer_command = store.answer(_identity(args), kind, value)
                _emit(
                    {
                        "queued": True,
                        "action": answer_command["action"],
                        "identity": answer_command["identity"],
                    }
                )
            elif args.ui_command == "abort":
                abort_command = store.abort(args.job, args.session)
                _emit(
                    {
                        "queued": True,
                        "action": abort_command["action"],
                        "identity": abort_command["identity"],
                    }
                )
        return 0
    except (UIControlError, JobPathError, OSError, ValueError) as exc:
        sys.stderr.write(f"omega ui: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

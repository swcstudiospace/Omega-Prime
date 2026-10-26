# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Command-line entry point for the local task dashboard."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import signal
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from omega_prime.dashboard.config import DashboardConfig, load_dashboard_config
from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.poller import (
    FLEET,
    GITHUB,
    HERDR_PODMAN,
    LOCAL_SOURCES,
    CollectorSpec,
    DashboardPoller,
)
from omega_prime.dashboard.task_ids import (
    allocate_task_id,
    assign_task_ids,
    next_task_id,
)


def build_default_collectors(
    *, home: Path, config: DashboardConfig
) -> list[CollectorSpec]:
    """Lazily load and configure the dashboard's real source collectors."""
    from omega_prime.dashboard.collectors.agentos import make_agentos_collector
    from omega_prime.dashboard.collectors.command_log import make_command_log_collector
    from omega_prime.dashboard.collectors.fleet import make_fleet_collector
    from omega_prime.dashboard.collectors.github import make_github_collector
    from omega_prime.dashboard.collectors.herdr import make_herdr_collector
    from omega_prime.dashboard.collectors.omega_jobs import make_omega_jobs_collector
    from omega_prime.dashboard.collectors.omp_sessions import (
        make_omp_sessions_collector,
    )
    from omega_prime.dashboard.collectors.podman import make_podman_collector
    from omega_prime.dashboard.collectors.procs import make_procs_collector
    from omega_prime.dashboard.collectors.sandbox_files import (
        make_sandbox_files_collector,
    )
    from omega_prime.dashboard.collectors.tasks_json import make_tasks_json_collector
    from omega_prime.dashboard.collectors.wiring import make_wiring_collector

    def now_fn() -> datetime:
        return datetime.now(UTC)

    local: list[tuple[str, Callable[[], SourceResult]]] = [
        (
            "omp_sessions",
            make_omp_sessions_collector(
                root=home / ".omp" / "agent" / "sessions",
                now_fn=now_fn,
                window_hours=config.window_hours,
            ),
        ),
        (
            "wiring",
            make_wiring_collector(
                repo_root=Path("/workspace/omes-bot"),
                runs_root=Path("/workspace/omes-bot-wiring-audit/runs"),
                now_fn=now_fn,
                window_hours=config.window_hours,
            ),
        ),
        (
            "sandbox",
            make_sandbox_files_collector(
                sandbox_root=Path("/workspace/omp-sandbox"),
                now_fn=now_fn,
                window_hours=config.window_hours,
            ),
        ),
        (
            "omega_jobs",
            make_omega_jobs_collector(
                jobs_root=home / ".omega-prime" / "jobs",
                now_fn=now_fn,
                window_hours=config.window_hours,
            ),
        ),
        (
            "command_log",
            make_command_log_collector(
                log_path=home / ".omega-prime" / "command-log.jsonl", now_fn=now_fn
            ),
        ),
        (
            "tasks_json",
            make_tasks_json_collector(
                tasks_path=home / ".omega-prime" / "dashboard" / "tasks.json",
                now_fn=now_fn,
            ),
        ),
        (
            "agentos",
            make_agentos_collector(
                agentos_root=Path("/home/box/agentos"), now_fn=now_fn
            ),
        ),
        ("procs", make_procs_collector(proc_root=Path("/proc"), now_fn=now_fn)),
    ]
    specs = [CollectorSpec(source, fn, LOCAL_SOURCES, 5.0) for source, fn in local]
    specs.extend(
        [
            CollectorSpec("herdr", make_herdr_collector(), HERDR_PODMAN, 5.0),
            CollectorSpec("podman", make_podman_collector(), HERDR_PODMAN, 5.0),
            CollectorSpec("github", make_github_collector(now_fn=now_fn), GITHUB, 15.0),
        ]
    )
    specs.append(
        CollectorSpec(
            "fleet",
            make_fleet_collector(enabled=config.fleet_enabled),
            FLEET,
            15.0,
        )
    )
    return specs


def _loopback(host: str) -> bool:
    if host.lower() in {"127.0.0.1", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _process_start_time(pid: int) -> int | None:
    """Return a process' Linux start time (stat field 22), if readable."""
    try:
        fields = (
            (Path("/proc") / str(pid) / "stat")
            .read_text(encoding="ascii")
            .rsplit(")", 1)[1]
            .split()
        )
        return int(fields[19])
    except (OSError, UnicodeError, IndexError, ValueError):
        return None


def _parse_pidfile(contents: str) -> tuple[int, int] | None:
    parts = contents.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _serve(app: Any, host: str, port: int) -> None:
    import uvicorn

    # Open SSE streams never end on their own; cap the drain on shutdown.
    uvicorn.run(app, host=host, port=port, timeout_graceful_shutdown=3)


def main(
    argv: Sequence[str] | None = None,
    *,
    home: Path | None = None,
    serve: Callable[[object, str, int], None] | None = None,
    signal_fn: Callable[[int, int], None] | None = None,
    stdout: TextIO | None = None,
    poller_factory: Callable[[], DashboardPoller] | None = None,
) -> int:
    """Run, stop, or collect a one-shot dashboard snapshot."""
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    base_home = home if home is not None else Path.home()
    tasks_path = base_home / ".omega-prime" / "dashboard" / "tasks.json"
    if raw_argv and raw_argv[0] in {"next-id", "assign-id", "assign-ids"}:
        command = raw_argv[0]
        if command == "next-id":
            argparse.ArgumentParser(prog="next-id").parse_args(raw_argv[1:])
            print(f"{next_task_id(path=tasks_path):05d}", file=stdout or sys.stdout)
            return 0
        if command == "assign-id":
            assign_parser = argparse.ArgumentParser(prog="assign-id")
            assign_parser.add_argument("--id", required=True)
            assign_parser.add_argument("--title", required=True)
            parsed = assign_parser.parse_args(raw_argv[1:])
            result = allocate_task_id(
                path=tasks_path,
                identity=parsed.id,
                title=parsed.title,
                now=datetime.now(UTC),
            )
            print(result["display_id"], file=stdout or sys.stdout)
            return 0
        argparse.ArgumentParser(prog="assign-ids").parse_args(raw_argv[1:])
        assign_task_ids(path=tasks_path, now=datetime.now(UTC))
        return 0
    parser = argparse.ArgumentParser(
        description="Local loopback task dashboard (see --terminal-readonly)"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("OMEGA_PRIME_DASHBOARD_PORT", "7421")),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument(
        "--terminal-readonly",
        action="store_true",
        help="force every task terminal websocket read-only",
    )
    args = parser.parse_args(raw_argv)
    stderr = sys.stderr
    output = stdout if stdout is not None else sys.stdout
    if not _loopback(args.host):
        print(
            f"refusing to bind {args.host!r}: the dashboard is loopback-only; use 127.0.0.1",
            file=stderr,
        )
        return 2
    base_home = home if home is not None else Path.home()
    pidfile = base_home / ".omega-prime" / "dashboard" / "dashboard.pid"
    if args.stop:
        try:
            identity = _parse_pidfile(pidfile.read_text(encoding="ascii"))
        except FileNotFoundError:
            print("no dashboard pidfile", file=stderr)
            return 1
        except (OSError, UnicodeError):
            print("invalid dashboard pidfile", file=stderr)
            return 1
        if identity is None:
            print("invalid dashboard pidfile", file=stderr)
            return 1
        pid, recorded_start = identity
        current_start = _process_start_time(pid)
        if current_start is None:
            print("dashboard process already stopped", file=stderr)
            with suppress(FileNotFoundError):
                pidfile.unlink()
            return 0
        if current_start != recorded_start:
            print(
                "pidfile refers to a different process; refusing",
                file=stderr,
            )
            return 1
        try:
            (signal_fn or os.kill)(pid, signal.SIGTERM)
        except ProcessLookupError:
            print("dashboard process already stopped", file=stderr)
            with suppress(FileNotFoundError):
                pidfile.unlink()
        except OSError as exc:
            print(f"could not stop dashboard: {type(exc).__name__}", file=stderr)
            return 1
        return 0
    if not args.once:
        try:
            previous = _parse_pidfile(pidfile.read_text(encoding="ascii"))
        except FileNotFoundError:
            previous = None
        except (OSError, UnicodeError):
            print("invalid dashboard pidfile", file=stderr)
            return 1
        if (
            previous is not None
            and previous[0] != os.getpid()
            and _process_start_time(previous[0]) == previous[1]
        ):
            print(f"dashboard already running (pid {previous[0]})", file=stderr)
            return 1
    config = load_dashboard_config(base_home / ".omega-prime" / "settings.json")

    def build_poller() -> DashboardPoller:
        return DashboardPoller(
            build_default_collectors(home=base_home, config=config),
            quiet_minutes=config.quiet_minutes,
            window_hours=config.window_hours,
            task_ids_path=tasks_path,
        )

    factory = poller_factory if poller_factory is not None else build_poller
    if args.once:
        poller = factory()
        asyncio.run(poller.refresh_now())
        print(
            json.dumps(poller.snapshot(), separators=(",", ":"), default=str),
            file=output,
        )
        return 0
    poller = factory()
    from omega_prime.dashboard.app import create_app

    app = create_app(
        poller,
        port=args.port,
        host=args.host,
        terminal_readonly=args.terminal_readonly,
        tasks_path=tasks_path,
    )
    pid = os.getpid()
    start = _process_start_time(pid)
    if start is None:
        print("could not determine dashboard process start time", file=stderr)
        return 1
    pidfile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(pidfile.parent, 0o700)
        descriptor = os.open(pidfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as pid_stream:
            pid_stream.write(f"{pid} {start}\n")
        os.chmod(pidfile, 0o600)
        (serve or _serve)(app, args.host, args.port)
    finally:
        try:
            current_identity = _parse_pidfile(pidfile.read_text(encoding="ascii"))
        except (OSError, UnicodeError):
            current_identity = None
        if current_identity == (pid, start):
            with suppress(FileNotFoundError):
                pidfile.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    if config.fleet_enabled:
        specs.append(
            CollectorSpec("fleet", make_fleet_collector(enabled=True), FLEET, 15.0)
        )
    return specs


def _loopback(host: str) -> bool:
    if host.lower() in {"127.0.0.1", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _serve(app: Any, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


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
    parser = argparse.ArgumentParser(description="Local read-only task dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("OMEGA_PRIME_DASHBOARD_PORT", "7421")),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)
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
            pid = int(pidfile.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            print("no dashboard pidfile", file=stderr)
            return 1
        except (OSError, ValueError):
            print("invalid dashboard pidfile", file=stderr)
            return 1
        try:
            (signal_fn or os.kill)(pid, signal.SIGTERM)
        except ProcessLookupError:
            print("dashboard process already stopped", file=stderr)
        except OSError as exc:
            print(f"could not stop dashboard: {type(exc).__name__}", file=stderr)
            return 1
        return 0
    config = load_dashboard_config(base_home / ".omega-prime" / "settings.json")

    def build_poller() -> DashboardPoller:
        return DashboardPoller(
            build_default_collectors(home=base_home, config=config),
            quiet_minutes=config.quiet_minutes,
            window_hours=config.window_hours,
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

    app = create_app(poller, port=args.port)
    pidfile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(pidfile.parent, 0o700)
        descriptor = os.open(pidfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as pid_stream:
            pid_stream.write(f"{os.getpid()}\n")
        os.chmod(pidfile, 0o600)
        (serve or _serve)(app, args.host, args.port)
    finally:
        with suppress(FileNotFoundError):
            pidfile.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

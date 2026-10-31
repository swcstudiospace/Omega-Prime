# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Read safe omp process identity fields from procfs.

Agent rows carry a bounded ``label`` (task number/title, role, phase, plan,
stage, run id, model) parsed by ``omega_prime.dashboard.labels`` so the model
can title them like any other task. omp helper workers are marked
``role: helper`` and long-lived daemons ``role: service``; neither is work.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.labels import parse_omp_argv
from omega_prime.dashboard.model import SourceResult

_PID = re.compile(r"[0-9]+\Z")
# procfs truncates comm to 15 bytes, so "openshell-gateway" reads back short.
_AGENT_COMM = "omp"
_SERVICE_COMMS = {"omp", "herdr", "openshell-gatew", "openshell-gateway"}
_DEAD_STATES = {"Z", "X", "x"}
# omp subcommands that run as long-lived servers rather than agent sessions.
_OMP_DAEMONS = {"auth-broker", "daemon", "mcp", "serve", "server"}


def _service(comm: str, cmdline: bytes) -> str | None:
    """Name a long-lived daemon, or None for an agent run.

    Daemons share an agent's binary or worktree, so counting them as agents
    would show a permanent "omp process N" task with no work behind it.
    """
    args = _args(cmdline)
    if comm.startswith("openshell-gatew") or (
        _subcommand(args, "openshell-gateway") is not None
    ):
        return "openshell-gateway"
    if comm == "herdr" and _subcommand(args, "herdr") == "server":
        return "herdr server"
    if comm == _AGENT_COMM:
        sub = _subcommand(args, _AGENT_COMM)
        if sub in _OMP_DAEMONS:
            return f"omp {sub}"
    return None


def _args(cmdline: bytes) -> list[str]:
    return [
        part.decode("utf-8", errors="replace") for part in cmdline.split(b"\0") if part
    ]


def _subcommand(args: list[str], program: str) -> str | None:
    """The first positional argument after `program`, when `program` is the
    executable itself or the script an interpreter (bun, node) runs.

    Option names and their values are skipped, so
    ``omp --cwd server -p "Do the work"`` never treats ``server`` as a
    daemon name. Returns "" when the program runs with no subcommand, None
    when it is not the program.
    """
    for index, arg in enumerate(args[:2]):
        if Path(arg).name != program:
            continue
        expect_value = False
        for part in args[index + 1 :]:
            if expect_value:
                expect_value = False
                if not part.startswith("-"):
                    continue
                # Previous flag was boolean; this token is another flag.
            if part == "--":
                # Everything after end-of-options is positional.
                continue
            if part.startswith("--"):
                if "=" not in part:
                    expect_value = True
                continue
            if part.startswith("-") and part != "-":
                continue
            return part
        return ""
    return None


def _stat_fields(entry: Path) -> list[str]:
    stat = (entry / "stat").read_text(encoding="utf-8").strip()
    closing = stat.rfind(")")
    if closing < 0:
        raise ValueError("invalid stat")
    return stat[closing + 1 :].split()


def _boot_time() -> float:
    with Path("/proc/stat").open(encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("btime "):
                return float(line.split()[1])
    raise OSError("boot time unavailable")


def _mode(cmdline: bytes) -> str | None:
    tokens = cmdline.split(b"\0")
    for index, token in enumerate(tokens[:-1]):
        if token == b"--mode":
            return tokens[index + 1].decode("utf-8", errors="replace")
    return None


def make_procs_collector(
    *,
    proc_root: Path = Path("/proc"),
    now_fn: Callable[[], datetime] | None = None,
    clock_hz: int | None = None,
    boot_time: float | None = None,
) -> Callable[[], SourceResult]:
    """Create a procfs collector that never reads process environments."""
    clock_ticks = clock_hz
    boot = boot_time

    def collect() -> SourceResult:
        nonlocal clock_ticks, boot
        if not proc_root.is_dir():
            return SourceResult(
                "omp_procs", "unavailable", error="processes unavailable"
            )
        try:
            if clock_ticks is None:
                clock_ticks = int(os.sysconf("SC_CLK_TCK"))
            if boot is None:
                boot = _boot_time()
        except (OSError, ValueError, TypeError):
            return SourceResult(
                "omp_procs", "unavailable", error="process timing unavailable"
            )
        now = (now_fn or (lambda: datetime.now(UTC)))().timestamp()
        items: list[dict[str, Any]] = []
        services: list[dict[str, Any]] = []
        skipped = 0
        exited = 0
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            return SourceResult(
                "omp_procs", "unavailable", error="processes unavailable"
            )
        for entry in entries:
            if not _PID.fullmatch(entry.name) or not entry.is_dir():
                continue
            try:
                comm = (entry / "comm").read_text(encoding="utf-8").strip()
                if comm not in _SERVICE_COMMS:
                    continue
                fields = _stat_fields(entry)
                if fields[0] in _DEAD_STATES:
                    exited += 1
                    continue
                starttime = int(fields[19])
                cmdline = (entry / "cmdline").read_bytes()
                if not cmdline:
                    # Only zombies and kernel threads have an empty cmdline.
                    exited += 1
                    continue
                service = _service(comm, cmdline)
                if service is None and comm != _AGENT_COMM:
                    continue
                cwd: str | None
                try:
                    cwd = os.readlink(entry / "cwd")
                except OSError:
                    cwd = None
                # Re-read stat last: a pid that exited (or was reused) during
                # the scan is dropped instead of reported as live work.
                try:
                    again = _stat_fields(entry)
                except OSError:
                    exited += 1
                    continue
                if again[0] in _DEAD_STATES or int(again[19]) != starttime:
                    exited += 1
                    continue
                elapsed = max(0.0, now - (boot + starttime / clock_ticks))
                row = {
                    "kind": "omp_proc",
                    "pid": int(entry.name),
                    "elapsed_s": elapsed,
                    "elapsed_measured_at": datetime.fromtimestamp(
                        now, tz=UTC
                    ).isoformat(),
                    "cwd": cwd,
                }
                if service is not None:
                    services.append(
                        {
                            **row,
                            "kind": "service_proc",
                            "role": "service",
                            "name": service,
                        }
                    )
                    continue
                label = parse_omp_argv(_args(cmdline))
                items.append(
                    {
                        **row,
                        "role": "helper" if label["worker"] else "agent",
                        "mode": _mode(cmdline),
                        "label": label,
                    }
                )
            except (OSError, ValueError, IndexError, UnicodeError):
                skipped += 1
        items.sort(key=lambda item: item["pid"])
        services.sort(key=lambda item: item["pid"])
        return SourceResult(
            "omp_procs",
            "ok" if items else "empty",
            items,
            extra={"skipped": skipped, "exited": exited, "services": services},
        )

    return collect

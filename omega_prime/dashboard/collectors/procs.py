# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Read safe omp process identity fields from procfs."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult

_PID = re.compile(r"[0-9]+\Z")


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
        skipped = 0
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
                if (entry / "comm").read_text(encoding="utf-8").strip() != "omp":
                    continue
                stat = (entry / "stat").read_text(encoding="utf-8").strip()
                closing = stat.rfind(")")
                if closing < 0:
                    raise ValueError("invalid stat")
                fields = stat[closing + 1 :].split()
                starttime = int(fields[19])
                cwd: str | None
                try:
                    cwd = os.readlink(entry / "cwd")
                except OSError:
                    cwd = None
                mode = _mode((entry / "cmdline").read_bytes())
                elapsed = max(0.0, now - (boot + starttime / clock_ticks))
                items.append(
                    {
                        "kind": "omp_proc",
                        "pid": int(entry.name),
                        "elapsed_s": elapsed,
                        "elapsed_measured_at": datetime.fromtimestamp(
                            now, tz=UTC
                        ).isoformat(),
                        "cwd": cwd,
                        "mode": mode,
                    }
                )
            except (OSError, ValueError, IndexError, UnicodeError):
                skipped += 1
        items.sort(key=lambda item: item["pid"])
        return SourceResult(
            "omp_procs", "ok" if items else "empty", items, extra={"skipped": skipped}
        )

    return collect

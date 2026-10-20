# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect validated user task bridge data."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value
from omega_prime.dashboard.status_file import read_tasks


def make_tasks_json_collector(
    *, tasks_path: Path, now_fn: Callable[[], Any]
) -> Callable[[], SourceResult]:
    """Build a cached collector for the validated tasks bridge."""
    cache: tuple[int, int, SourceResult] | None = None

    def collect() -> SourceResult:
        nonlocal cache
        if not tasks_path.exists():
            return SourceResult(
                "tasks_json",
                "missing",
                error="tasks.json not written yet (filled in by Omega Prime on chat updates)",
            )
        try:
            stat = tasks_path.stat()
            if cache is not None and cache[:2] == (stat.st_mtime_ns, stat.st_size):
                return cache[2]
            result = read_tasks(tasks_path)
            if result.status != "ok" or result.data is None:
                output = SourceResult(
                    "tasks_json",
                    "unreadable",
                    error=redact_value(result.error or "tasks file unreadable"),
                )
            else:
                data = result.data
                output = SourceResult(
                    "tasks_json",
                    "ok",
                    extra={
                        "tasks": data["tasks"],
                        "waiting_on_ove": data["waiting_on_ove"],
                        "finished": data["finished"],
                        "age_s": result.age_s,
                    },
                )
            cache = (stat.st_mtime_ns, stat.st_size, output)
            return output
        except OSError as exc:
            return SourceResult(
                "tasks_json", "unreadable", error=redact_value(str(exc))
            )

    return collect

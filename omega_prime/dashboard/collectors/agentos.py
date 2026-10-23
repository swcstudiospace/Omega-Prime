# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect AgentOS routing and state directory summaries."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value


def make_agentos_collector(
    *, agentos_root: Path, now_fn: Callable[[], Any]
) -> Callable[[], SourceResult]:
    """Build a cached summary collector for AgentOS routing state."""
    cache: tuple[int, int, int, SourceResult] | None = None

    def collect() -> SourceResult:
        nonlocal cache
        if not agentos_root.exists():
            return SourceResult(
                "agentos",
                "unavailable",
                error=redact_value(f"{agentos_root}: path not found"),
            )
        routing_path = agentos_root / "gsd-routing.json"
        state_path = agentos_root / "state"
        try:
            stat = routing_path.stat() if routing_path.exists() else None
            state_count = (
                sum(1 for _ in state_path.iterdir()) if state_path.is_dir() else 0
            )
            key = (
                (stat.st_mtime_ns, stat.st_size, state_count)
                if stat
                else (0, 0, state_count)
            )
            if cache is not None and cache[:3] == key:
                return cache[3]
            summary = "0 routes"
            if stat:
                with routing_path.open("rb") as stream:
                    stream.seek(max(0, stat.st_size - 262144))
                    raw = stream.read(262144)
                data = json.loads(raw.decode("utf-8", errors="replace"))
                if not isinstance(data, dict):
                    raise ValueError("routing data must be an object")
                routes = data.get("routes", [])
                count = len(routes) if isinstance(routes, (list, dict)) else 0
                summary = f"{count} routes"
            output = SourceResult(
                "agentos",
                "ok",
                extra=redact_value(
                    {"routing_summary": summary, "state_entries": state_count}
                ),
            )
            cache = (*key, output)
            return output
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return SourceResult("agentos", "error", error=redact_value(str(exc)))

    return collect

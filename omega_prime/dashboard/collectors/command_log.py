# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect redacted summaries from the Omega command log."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_line, redact_value

_LIMIT = 262144


def make_command_log_collector(
    *, log_path: Path, now_fn: Callable[[], Any]
) -> Callable[[], SourceResult]:
    """Build a cached collector for a bounded command-log tail."""
    cache: tuple[int, int, dict[str, Any]] | None = None

    def collect() -> SourceResult:
        nonlocal cache
        if not log_path.is_file():
            return SourceResult(
                "command_log",
                "unavailable",
                error=redact_value(f"{log_path}: path not found"),
            )
        try:
            stat = log_path.stat()
            if cache is not None and cache[:2] == (stat.st_mtime_ns, stat.st_size):
                extra = cache[2]
            else:
                with log_path.open("rb") as stream:
                    stream.seek(max(0, stat.st_size - _LIMIT))
                    raw = stream.read(_LIMIT)
                if stat.st_size > _LIMIT and raw and not raw.startswith(b"\n"):
                    pos = raw.find(b"\n")
                    raw = b"" if pos < 0 else raw[pos + 1 :]
                records: list[dict[str, Any]] = []
                for line in raw.decode("utf-8", errors="replace").splitlines():
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            records.append(value)
                    except json.JSONDecodeError:
                        continue
                codes = [
                    row.get("exit_code")
                    for row in records[-5:]
                    if isinstance(row.get("exit_code"), int)
                ]
                last = records[-1] if records else {}
                extra = redact_value(
                    {
                        "count": len(records),
                        "last_exit_codes": codes,
                        "last_cwd": last.get("cwd"),
                        "last_cmd": redact_line(last.get("cmd"), cap=120),
                    }
                )
                cache = (stat.st_mtime_ns, stat.st_size, extra)
            count = extra["count"]
            code = extra["last_exit_codes"][-1] if extra["last_exit_codes"] else None
            return SourceResult(
                "command_log",
                "ok" if count else "empty",
                extra=extra,
                detail=f"{count} commands, last exit {code}",
            )
        except OSError:
            return SourceResult(
                "command_log", "error", error="unable to read command log"
            )

    return collect

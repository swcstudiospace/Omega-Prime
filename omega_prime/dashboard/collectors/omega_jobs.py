# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect recent Omega job session metadata."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_line, redact_value

_LIMIT = 262144


def make_omega_jobs_collector(
    *, jobs_root: Path, now_fn: Callable[[], datetime], window_hours: float = 24.0
) -> Callable[[], SourceResult]:
    cache: dict[Path, tuple[int, int, int, int, dict[str, Any] | None]] = {}

    def collect() -> SourceResult:
        if not jobs_root.exists():
            return SourceResult(
                "omega_jobs", "unavailable", error=f"{jobs_root}: path not found"
            )
        cutoff = now_fn() - timedelta(hours=window_hours)
        items: list[dict[str, Any]] = []
        total = 0
        for directory in sorted(jobs_root.glob("job-*")):
            session_path = directory / "session.json"
            if not session_path.is_file():
                continue
            total += 1
            try:
                event_path = directory / "events.jsonl"
                activity = (
                    event_path.stat() if event_path.exists() else session_path.stat()
                )
                last = datetime.fromtimestamp(activity.st_mtime, tz=cutoff.tzinfo)
                if last < cutoff:
                    continue
                stat = session_path.stat()
                event_path = directory / "events.jsonl"
                event_stat = event_path.stat() if event_path.is_file() else None
                event_key = (
                    (event_stat.st_mtime_ns, event_stat.st_size)
                    if event_stat
                    else (0, 0)
                )
                cached = cache.get(session_path)
                if cached and cached[:4] == (
                    stat.st_mtime_ns,
                    stat.st_size,
                    *event_key,
                ):
                    row = cached[4]
                else:
                    with session_path.open("rb") as stream:
                        stream.seek(max(0, stat.st_size - _LIMIT))
                        raw = stream.read(_LIMIT)
                    if stat.st_size > _LIMIT and raw and not raw.startswith(b"\n"):
                        pos = raw.find(b"\n")
                        raw = b"" if pos < 0 else raw[pos + 1 :]
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    if not isinstance(data, dict):
                        continue
                    ok = data.get("ok") if isinstance(data.get("ok"), bool) else None
                    if event_stat:
                        with event_path.open("rb") as stream:
                            stream.seek(max(0, event_stat.st_size - _LIMIT))
                            event_raw = stream.read(_LIMIT)
                        if (
                            event_stat.st_size > _LIMIT
                            and event_raw
                            and not event_raw.startswith(b"\n")
                        ):
                            pos = event_raw.find(b"\n")
                            event_raw = b"" if pos < 0 else event_raw[pos + 1 :]
                        terminal: dict[str, Any] | None = None
                        for line in event_raw.splitlines():
                            try:
                                event = json.loads(
                                    line.decode("utf-8", errors="replace")
                                )
                            except json.JSONDecodeError:
                                continue
                            if isinstance(event, dict) and event.get("type") in {
                                "job_end",
                                "agent_end",
                            }:
                                terminal = event
                        if terminal:
                            ok = (
                                terminal.get("ok")
                                if terminal.get("type") == "job_end"
                                and isinstance(terminal.get("ok"), bool)
                                else terminal.get("type") == "agent_end"
                            )
                    final = data.get("final_text")
                    row = redact_value(
                        {
                            "kind": "omega_job",
                            "job_id": data.get(
                                "job_id", directory.name.removeprefix("job-")
                            ),
                            "model": data.get("model"),
                            "plan": data.get("plan"),
                            "provider": data.get("provider"),
                            "sandbox": data.get("sandbox"),
                            "stage": data.get("stage"),
                            "ok": ok,
                            "final_text_line": redact_line(final, cap=120),
                            "started_at": data.get("started_at"),
                            "last_activity_at": last.isoformat(),
                        }
                    )
                    cache[session_path] = (
                        stat.st_mtime_ns,
                        stat.st_size,
                        *event_key,
                        row,
                    )
                if row is not None:
                    row["last_activity_at"] = last.isoformat()
                    items.append(row)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
        return SourceResult(
            "omega_jobs",
            "ok" if items else "empty",
            items=items,
            extra={"total_jobs": total},
        )

    return collect

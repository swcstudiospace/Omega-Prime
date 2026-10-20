# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect recent OMP sessions from bounded JSONL tails."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value

_LIMIT = 262144


def _tail(path: Path) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - _LIMIT))
        data = stream.read(_LIMIT)
    if size > _LIMIT and data and not data.startswith(b"\n"):
        first = data.find(b"\n")
        data = b"" if first < 0 else data[first + 1 :]
    return data.decode("utf-8", errors="replace").splitlines()


def _todo(value: Any) -> dict[str, int] | None:
    if isinstance(value, dict):
        name = value.get("tool") or value.get("name")
        if isinstance(name, str) and "todo" in name.lower():
            done = value.get("done")
            total = value.get("total")
            if isinstance(done, int) and isinstance(total, int):
                return {"done": done, "total": total}
        for child in value.values():
            found = _todo(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _todo(child)
            if found is not None:
                return found
    return None


def make_omp_sessions_collector(
    *, root: Path, now_fn: Callable[[], datetime], window_hours: float = 24.0
) -> Callable[[], SourceResult]:
    """Build a cached collector for session JSONL files under ``root``."""
    cache: dict[Path, tuple[int, int, dict[str, Any] | None, int]] = {}

    def collect() -> SourceResult:
        if not root.exists():
            return SourceResult(
                "omp_sessions", "unavailable", error=f"{root}: path not found"
            )
        items: list[dict[str, Any]] = []
        malformed = 0
        cutoff = now_fn() - timedelta(hours=window_hours)
        try:
            paths = sorted(root.rglob("*.jsonl"))
            for path in paths:
                try:
                    stat = path.stat()
                    if datetime.fromtimestamp(stat.st_mtime, tz=cutoff.tzinfo) < cutoff:
                        continue
                    cached = cache.get(path)
                    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                        row = cached[2]
                        malformed += cached[3]
                        if row is not None:
                            items.append(row)
                        continue
                    malformed_before = malformed
                    tail_lines = _tail(path)
                    records: list[dict[str, Any]] = []
                    for line in tail_lines:
                        try:
                            record = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            malformed += 1
                            continue
                        if isinstance(record, dict):
                            records.append(record)
                    session: dict[str, Any] = {
                        "kind": "omp_session",
                        "session_path": str(path),
                        "usage": {},
                        "progress": None,
                    }
                    header_in_tail = any(
                        record.get("type") == "session" for record in records
                    )
                    for record in records:
                        typ = record.get("type")
                        data_value = record.get("data")
                        data = data_value if isinstance(data_value, dict) else record
                        stamp = record.get("timestamp") or record.get("ts")
                        if isinstance(stamp, str):
                            try:
                                datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                                session["last_ts"] = stamp
                            except ValueError:
                                pass
                        if typ == "session":
                            if "session_id" not in session:
                                session["session_id"] = data.get("id") or data.get(
                                    "session_id"
                                )
                                session["started_at"] = data.get("timestamp") or stamp
                            if isinstance(data.get("cwd"), str):
                                session["cwd"] = data["cwd"]
                        elif typ in {"title", "title_change"}:
                            session["title"] = data.get("title")
                        elif typ == "model_change":
                            session["model"] = data.get("model")
                        elif typ == "model_usage":
                            session["model"] = data.get("model", session.get("model"))
                            usage = data.get("usage")
                            if isinstance(usage, dict):
                                session["usage"] = {
                                    "input": usage.get(
                                        "input", usage.get("input_tokens", 0)
                                    ),
                                    "output": usage.get(
                                        "output", usage.get("output_tokens", 0)
                                    ),
                                }
                        # Only terminal custom records end sessions; model_usage
                        # stopReason values describe individual model turns.
                        elif typ == "custom" and data.get("kind") in {
                            "signal",
                            "normal",
                        }:
                            kind = data["kind"]
                            session["stop_reason"] = (
                                "stopped"
                                if kind == "signal"
                                else str(data.get("reason") or "normal")
                            )
                        elif typ == "thinking_level_change":
                            session["thinking_level"] = data.get(
                                "thinkingLevel", data.get("thinking_level")
                            )
                    if not header_in_tail:
                        with path.open("rb") as stream:
                            head = stream.read(_LIMIT)
                        if len(head) == _LIMIT:
                            head = head[: head.rfind(b"\n") + 1]
                            for line in head.decode(
                                "utf-8", errors="replace"
                            ).splitlines():
                                try:
                                    record = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                if (
                                    not isinstance(record, dict)
                                    or record.get("type") != "session"
                                ):
                                    continue
                                data_value = record.get("data")
                                data = (
                                    data_value
                                    if isinstance(data_value, dict)
                                    else record
                                )
                                stamp = record.get("timestamp")
                                if "session_id" not in session:
                                    session_id = data.get("id") or data.get(
                                        "session_id"
                                    )
                                    if session_id is not None:
                                        session["session_id"] = session_id
                                if "started_at" not in session:
                                    started_at = data.get("timestamp") or (
                                        stamp if isinstance(stamp, str) else None
                                    )
                                    if started_at is not None:
                                        session["started_at"] = started_at
                                if isinstance(data.get("cwd"), str):
                                    session.setdefault("cwd", data["cwd"])
                                break
                    for record in reversed(records):
                        progress = _todo(record)
                        if progress is not None:
                            session["progress"] = progress
                            break
                    session.setdefault(
                        "last_ts",
                        datetime.fromtimestamp(
                            stat.st_mtime, tz=cutoff.tzinfo
                        ).isoformat(),
                    )
                    row = redact_value(session)
                    cache[path] = (
                        stat.st_mtime_ns,
                        stat.st_size,
                        row,
                        malformed - malformed_before,
                    )
                    items.append(row)
                except OSError:
                    continue
        except OSError:
            return SourceResult(
                "omp_sessions", "error", error="unable to read sessions"
            )
        return SourceResult(
            "omp_sessions",
            "ok" if items else "empty",
            items=items,
            extra={"malformed": malformed},
        )

    return collect

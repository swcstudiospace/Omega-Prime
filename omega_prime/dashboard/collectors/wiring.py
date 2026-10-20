# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect local wiring worktrees and bounded phase-log state."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value

_LIMIT = 262144
_PHASE = re.compile(r"^p[0-9]+$")


def _default_runner(args: list[str]) -> str:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=5, check=False
    ).stdout


def _log_lines(path: Path) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - _LIMIT))
        data = stream.read(_LIMIT)
    if size > _LIMIT and data and not data.startswith(b"\n"):
        pos = data.find(b"\n")
        data = b"" if pos < 0 else data[pos + 1 :]
    return data.decode("utf-8", errors="replace").splitlines()


def make_wiring_collector(
    *,
    repo_root: Path,
    runs_root: Path,
    now_fn: Callable[[], datetime],
    window_hours: float = 24.0,
    runner: Callable[[list[str]], str] | None = None,
) -> Callable[[], SourceResult]:
    """Build a cached collector for wiring worktrees and run markers."""
    run = runner or _default_runner
    cache: dict[Path, tuple[int, int, list[str]]] = {}

    def collect() -> SourceResult:
        if not repo_root.exists():
            return SourceResult(
                "wiring", "unavailable", error=f"{repo_root}: path not found"
            )
        try:
            output = run(
                ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"]
            )
            worktrees: dict[str, tuple[str, str | None]] = {}
            current_path: str | None = None
            for line in output.splitlines():
                if line.startswith("worktree "):
                    current_path = line[9:]
                    if _PHASE.fullmatch(Path(current_path).name):
                        worktrees[Path(current_path).name] = (current_path, None)
                elif (
                    line.startswith("branch ")
                    and current_path
                    and Path(current_path).name in worktrees
                ):
                    phase = Path(current_path).name
                    worktrees[phase] = (
                        current_path,
                        line[7:].removeprefix("refs/heads/"),
                    )
            phases = set(worktrees)
            if runs_root.exists():
                phases.update(
                    path.name
                    for path in runs_root.iterdir()
                    if path.is_dir() and _PHASE.fullmatch(path.name)
                )
            items: list[dict[str, Any]] = []
            for phase in sorted(phases):
                worktree, branch = worktrees.get(phase, (None, None))
                directory = runs_root / phase
                stage = "pending"
                progress = None
                stamp = None
                log = directory / "phase.log"
                if log.is_file():
                    stat = log.stat()
                    cached = cache.get(log)
                    all_lines = (
                        cached[2]
                        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size)
                        else _log_lines(log)
                    )
                    cache[log] = (stat.st_mtime_ns, stat.st_size, all_lines)
                    lines = all_lines[-64:]
                    # phase.log stamps are box-local wall-clock times.
                    now = now_fn().astimezone()
                    starts = [
                        line
                        for line in all_lines
                        if re.search(r"\b(sol|luna)\s+start\b", line)
                    ]
                    if starts:
                        match = re.search(r"\b(sol|luna)\s+start\b", starts[-1])
                        if match:
                            stage = match.group(1)
                    if (directory / "luna.done").exists():
                        stage = "done"
                    elif (directory / "sol.done").exists():
                        stage = "luna"
                    todos = [line for line in lines if "tool todo " in line]
                    if todos:
                        try:
                            data = json.loads(todos[-1].split("tool todo ", 1)[1])
                            if isinstance(data.get("done"), int) and isinstance(
                                data.get("total"), int
                            ):
                                progress = {
                                    "done": data["done"],
                                    "total": data["total"],
                                }
                        except (json.JSONDecodeError, AttributeError):
                            pass
                    times = (
                        re.findall(r"\b([0-9]{2}):([0-9]{2}):([0-9]{2})\b", lines[-1])
                        if lines
                        else []
                    )
                    stamp_dt = datetime.fromtimestamp(stat.st_mtime).astimezone()
                    if times:
                        hour, minute, second = (int(x) for x in times[-1])
                        stamp_dt = stamp_dt.replace(
                            hour=hour, minute=minute, second=second, microsecond=0
                        )
                    if stamp_dt > now + timedelta(minutes=5):
                        stamp_dt -= timedelta(days=1)
                    stamp = stamp_dt.isoformat()
                    if stage != "done" and stamp_dt < now - timedelta(
                        hours=window_hours
                    ):
                        continue
                head = None
                if worktree:
                    try:
                        head = (
                            run(
                                ["git", "-C", worktree, "rev-parse", "--short", "HEAD"]
                            ).strip()
                            or None
                        )
                    except Exception:
                        head = None
                items.append(
                    redact_value(
                        {
                            "phase": phase,
                            "branch": branch,
                            "head_sha": head,
                            "stage": stage,
                            "last_log_ts": stamp,
                            "progress": progress,
                            "worktree": worktree,
                        }
                    )
                )
            return SourceResult("wiring", "ok" if items else "empty", items=items)
        except Exception as exc:
            return SourceResult("wiring", "error", error=redact_value(str(exc)))

    return collect

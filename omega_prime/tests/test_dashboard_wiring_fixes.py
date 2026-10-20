# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regressions for wiring collector command and phase-log handling."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from omega_prime.dashboard.collectors.wiring import make_wiring_collector


def test_wiring_command_and_stale_old_stage_log(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    phase = runs / "p1"
    phase.mkdir(parents=True)
    now = datetime.now().astimezone()
    old = now - timedelta(days=3)
    log = phase / "phase.log"
    log.write_text(
        "".join(f"ordinary line {i}\n" for i in range(80))
        + "12:00:00 sol start\n12:01:00 luna start\n12:02:00 activity\n",
        encoding="utf-8",
    )
    os.utime(log, (old.timestamp(), old.timestamp()))
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return ""

    result = make_wiring_collector(
        repo_root=repo,
        runs_root=runs,
        now_fn=lambda: now,
        window_hours=24,
        runner=runner,
    )()
    assert calls == [["git", "-C", str(repo), "worktree", "list", "--porcelain"]]
    assert result.items == []

    # Widen the window to inspect the stale log's retained stage and mtime date.
    result = make_wiring_collector(
        repo_root=repo,
        runs_root=runs,
        now_fn=lambda: now,
        window_hours=100,
        runner=lambda _args: "",
    )()
    item: dict[str, Any] = result.items[0]
    assert item["stage"] == "luna"
    assert datetime.fromisoformat(item["last_log_ts"]).date() == old.date()


def test_fresh_wiring_log_is_emitted_with_recent_stamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    phase = runs / "p2"
    phase.mkdir(parents=True)
    now = datetime.now().astimezone()
    text_time = (now - timedelta(minutes=3)).strftime("%H:%M:%S")
    log = phase / "phase.log"
    log.write_text(f"{text_time} sol start\n{text_time} activity\n", encoding="utf-8")
    os.utime(log, (now.timestamp(), now.timestamp()))

    result = make_wiring_collector(
        repo_root=repo,
        runs_root=runs,
        now_fn=lambda: now,
        runner=lambda _args: "",
    )()
    assert len(result.items) == 1
    item = result.items[0]
    stamp = datetime.fromisoformat(item["last_log_ts"])
    assert item["stage"] == "sol"
    assert abs((now - stamp).total_seconds()) < 5 * 60

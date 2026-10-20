# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regression tests for dashboard polling and task-file validation fixes."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import pytest

from omega_prime.dashboard import poller as poller_module
from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.poller import CollectorSpec, DashboardPoller
from omega_prime.dashboard.status_file import read_tasks


def test_read_tasks_rejects_invalid_task_shapes(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tasks": None,
                "waiting_on_ove": [],
                "finished": [],
            }
        ),
        encoding="utf-8",
    )

    result = read_tasks(path)

    assert result.status == "unreadable"
    assert result.error == "tasks must be a list"


def test_slow_source_does_not_block_fast_source() -> None:
    async def run() -> None:
        slow_calls = 0
        fast_calls = 0
        slow_started = asyncio.Event()

        def slow() -> SourceResult:
            nonlocal slow_calls
            slow_calls += 1
            slow_started.set()
            time.sleep(0.4)
            return SourceResult("slow", "ok")

        def fast() -> SourceResult:
            nonlocal fast_calls
            fast_calls += 1
            return SourceResult("fast", "ok")

        poller = DashboardPoller(
            [
                CollectorSpec("slow", slow, 0.05, 1.0),
                CollectorSpec("fast", fast, 0.05, 1.0),
            ],
            tick_s=0.01,
        )
        await poller.start()
        try:
            await asyncio.wait_for(slow_started.wait(), timeout=1.0)
            await asyncio.sleep(0.28)
            assert slow_calls >= 1
            assert fast_calls >= 4
        finally:
            await poller.stop()

    asyncio.run(run())


def test_normalization_failure_keeps_last_snapshot_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        calls = 0

        def collect() -> SourceResult:
            return SourceResult("local", "ok")

        poller = DashboardPoller(
            [CollectorSpec("local", collect, 0.05, 1.0)], tick_s=0.01
        )
        await poller.refresh_now()
        previous = poller.snapshot()
        original_normalize = poller_module.normalize_tasks

        def fail_once(
            sources: Mapping[str, SourceResult],
            *,
            now: datetime,
            quiet_minutes: float = 15.0,
            window_hours: float = 24.0,
        ) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("broken normalization")
            return original_normalize(
                sources,
                now=now,
                quiet_minutes=quiet_minutes,
                window_hours=window_hours,
            )

        monkeypatch.setattr(poller_module, "normalize_tasks", fail_once)
        await poller.start()
        try:
            for _ in range(100):
                if (
                    poller.sources_status().get("normalization", {}).get("status")
                    == "error"
                ):
                    break
                await asyncio.sleep(0.01)
            assert poller.snapshot() == previous
            assert poller.sources_status()["normalization"] == {
                "status": "error",
                "error": "normalize failed: RuntimeError",
            }

            for _ in range(100):
                if "normalization" not in poller.sources_status():
                    break
                await asyncio.sleep(0.01)
            assert "normalization" not in poller.sources_status()
            assert poller.snapshot() is not None
            assert calls >= 2
        finally:
            await poller.stop()

    asyncio.run(run())


def test_allowlisted_source_extras_are_published() -> None:
    async def run() -> None:
        extras = {"routing_summary": {"route": "local"}, "state_entries": 3}
        poller = DashboardPoller(
            [
                CollectorSpec(
                    "agentos",
                    lambda: SourceResult("agentos", "ok", extra=extras),
                    1.0,
                    1.0,
                )
            ]
        )

        await poller.refresh_now()

        assert poller.sources_status()["agentos"]["routing_summary"] == {
            "route": "local"
        }
        assert poller.sources_status()["agentos"]["state_entries"] == 3
        snap = poller.snapshot()
        assert snap is not None
        assert snap["sources"]["agentos"]["routing_summary"] == {"route": "local"}
        assert snap["sources"]["agentos"]["state_entries"] == 3

    asyncio.run(run())

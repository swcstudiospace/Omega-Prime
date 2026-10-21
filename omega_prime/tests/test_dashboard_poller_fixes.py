# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regression tests for dashboard polling and task-file validation fixes."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

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


def test_one_in_flight_call_per_source() -> None:
    async def run() -> None:
        inside = False
        calls = 0
        reentered: list[int] = []
        fast_calls = 0

        def slow() -> SourceResult:
            nonlocal inside, calls
            calls += 1
            if inside:
                reentered.append(calls)
            inside = True
            time.sleep(0.25)
            inside = False
            return SourceResult("slow", "ok")

        def fast() -> SourceResult:
            nonlocal fast_calls
            fast_calls += 1
            return SourceResult("fast", "ok")

        poller = DashboardPoller(
            [
                CollectorSpec("slow", slow, 0.05, 5.0),
                CollectorSpec("fast", fast, 0.05, 5.0),
            ],
            tick_s=0.01,
        )
        await poller.start()
        try:
            await asyncio.sleep(0.18)
            assert reentered == []
            assert calls == 1
            assert fast_calls >= 2

            async def wait_for_second_call() -> None:
                while calls < 2:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_second_call(), timeout=1.5)
            assert calls >= 2
            assert reentered == []
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


def test_github_command_center_is_published_with_only_bounded_fields() -> None:
    # Built at runtime so no credential-shaped literal lives in the source,
    # while the value the poller sees still looks like a real GitHub PAT.
    fake_pat = "ghp_" + token_hex(10)

    async def run() -> None:
        extra = {
            "command_center": {
                "repositories": [{"name": "repo", "token": fake_pat}],
                "ci_cd": [],
                "recent_merges": [],
                "meta": {"password": "secret_value"},
                "other": "discard",
            },
            "other": "discard",
        }
        poller = DashboardPoller(
            [
                CollectorSpec(
                    "github",
                    lambda: SourceResult("github", "ok", extra=extra),
                    1.0,
                    1.0,
                )
            ]
        )
        await poller.refresh_now()
        snapshot = poller.snapshot()
        assert snapshot is not None
        status = snapshot["sources"]["github"]
        assert set(status["command_center"]) == {
            "repositories",
            "ci_cd",
            "recent_merges",
            "meta",
        }
        rendered = str(status)
        assert fake_pat not in rendered
        assert "secret_value" not in rendered
        assert "other" not in status

    asyncio.run(run())


def test_github_timeout_exposes_error_without_mutating_cached_rows() -> None:
    async def run() -> None:
        calls = 0

        def collect() -> SourceResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                return SourceResult(
                    "github",
                    "ok",
                    extra={"command_center": {"repositories": [{"name": "cached"}]}},
                )
            time.sleep(0.1)
            return SourceResult("github", "ok")

        poller = DashboardPoller(
            [CollectorSpec("github", collect, 1.0, 0.05)],
            clock=lambda: datetime(2026, 10, 21, tzinfo=UTC),
        )
        await poller._collect(poller.specs[0])
        poller._build_snapshot()
        previous = poller.snapshot()
        assert previous is not None
        await poller._collect(poller.specs[0])
        poller._build_snapshot()
        current = poller.snapshot()
        assert current is not None
        assert current["sources"]["github"]["status"] == "error"
        assert (
            current["sources"]["github"]["command_center"]
            == previous["sources"]["github"]["command_center"]
        )

    asyncio.run(run())


def test_default_cli_poller_factory_receives_numbering_path(
    tmp_path, monkeypatch, capsys
) -> None:
    from omega_prime.dashboard import __main__ as dashboard_main

    observed = {}

    class FakePoller:
        def __init__(self, specs, **kwargs):
            observed["path"] = kwargs.get("task_ids_path")

        async def refresh_now(self):
            pass

        def snapshot(self):
            return {
                "generated_at": "now",
                "sources": {},
                "active": [],
                "pr_gate": [],
                "waiting_on_ove": [],
                "finished": [],
            }

    monkeypatch.setattr(dashboard_main, "DashboardPoller", FakePoller)
    assert dashboard_main.main(["--once"], home=tmp_path) == 0
    capsys.readouterr()
    assert observed["path"] == tmp_path / ".omega-prime" / "dashboard" / "tasks.json"
    assert DashboardPoller([]).task_ids_path is None

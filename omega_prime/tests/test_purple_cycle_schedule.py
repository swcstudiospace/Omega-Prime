# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Purple-cycle cron jobs: schedule, clear, and tick."""

from __future__ import annotations

from pathlib import Path

import pytest

from omega_prime.cron.scheduler import (
    PURPLE_CYCLE_KIND,
    JobStore,
    clear_purple_cycle,
    list_purple_cycles,
    schedule_purple_cycle,
    tick_purple_cycles,
)


def _store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.json")


def test_invalid_schedule_writes_no_job(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        schedule_purple_cycle(store, due_at=10, interval_seconds=0)
    with pytest.raises(ValueError):
        schedule_purple_cycle(store, due_at=10, interval_seconds=-5)
    with pytest.raises(ValueError):
        schedule_purple_cycle(store, due_at=10, interval_seconds=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        schedule_purple_cycle(store, due_at=10, interval_seconds=30, mode="apply")
    with pytest.raises(ValueError):
        schedule_purple_cycle(store, due_at=10, interval_seconds=30, mode="")
    with pytest.raises(ValueError):
        schedule_purple_cycle(
            store, due_at=10, interval_seconds=30, steps=("not-a-step",)
        )
    assert store.jobs == []
    assert not store.path.exists()


def test_clear_unknown_id_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    kept = store.schedule("plain", 10, interval_seconds=30)
    before = store.path.read_text(encoding="utf-8")
    result = clear_purple_cycle(store, "missing")
    assert result == {"error": "no purple-cycle job: 'missing'"}
    assert store.path.read_text(encoding="utf-8") == before
    assert [job["id"] for job in store.jobs] == [kept]


def test_schedule_stamps_kind_mode_and_steps(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job_id = schedule_purple_cycle(
        store,
        due_at=10,
        interval_seconds=30,
        mode="run",
        steps=["engagement", "preflight"],
    )
    listed = list_purple_cycles(store)
    assert len(listed) == 1
    job = listed[0]
    assert job["id"] == job_id
    assert job["kind"] == PURPLE_CYCLE_KIND
    assert job["mode"] == "run"
    assert job["steps"] == ("preflight", "engagement")
    cleared = clear_purple_cycle(store, job_id)
    assert cleared == {"cleared": job_id}
    assert list_purple_cycles(store) == []


def test_tick_runs_due_once_and_records_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    calls: list[str] = []

    def runner(job: dict) -> dict:
        calls.append(job["mode"])
        return {"ok": False, "exit": 1, "state": "failed"}

    schedule_purple_cycle(store, due_at=10, interval_seconds=30, mode="plan")
    other = store.schedule("plain", 10)
    ran = tick_purple_cycles(store, 10, runner=runner)
    assert ran == []
    assert calls == ["plan"]
    cycle = list_purple_cycles(store)[0]
    assert cycle["history"][-1]["status"] == "failed"
    assert cycle["history"][-1]["result"]["state"] == "failed"
    assert cycle["due_at"] == 40
    plain = next(job for job in store.jobs if job["id"] == other)
    assert plain["last_ran_at"] is None
    tick_purple_cycles(store, 10, runner=runner)
    assert calls == ["plan"]


def test_second_tick_before_next_due_does_not_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    calls: list[int] = []

    def runner(job: dict) -> dict:
        calls.append(1)
        return {"ok": True, "exit": 0, "state": "completed"}

    schedule_purple_cycle(store, due_at=5, interval_seconds=100, mode="plan")
    ran = tick_purple_cycles(store, 5, runner=runner)
    assert len(ran) == 1
    assert ran[0]["history"][-1]["status"] == "ok"
    tick_purple_cycles(store, 50, runner=runner)
    assert calls == [1]

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Purple-cycle cron jobs: schedule, clear, and tick."""

from __future__ import annotations

from pathlib import Path

import pytest

from omega_prime.cron.apscheduler_backend import SchedulerService
from omega_prime.cron.desk_driver import DeskDriver
from omega_prime.cron.scheduler import (
    PURPLE_CYCLE_KIND,
    JobStore,
    _default_purple_cycle_runner,
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


def test_each_due_time_keeps_its_own_cycle_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_run_cycle(**kwargs: object) -> dict[str, object]:
        cycle_id = kwargs.get("cycle_id")
        assert isinstance(cycle_id, str)
        seen.append(cycle_id)
        assert kwargs.get("force_new", False) is False
        return {"ok": True, "exit": 0, "state": "completed"}

    monkeypatch.setattr("omega_prime.grokbot.purple_cycle.run_cycle", fake_run_cycle)
    job = {"id": "abc123", "mode": "run", "steps": None, "due_at": 1000}
    _default_purple_cycle_runner(job)
    _default_purple_cycle_runner(job)
    job["due_at"] = 2000
    _default_purple_cycle_runner(job)
    assert seen[0] == seen[1]
    assert seen[0] != seen[2]
    assert "1000" in seen[0]
    assert "2000" in seen[2]


def test_desk_driver_fires_a_due_purple_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def runner(job: dict, root: Path | None = None) -> dict[str, object]:
        calls.append(str(job["mode"]))
        return {"ok": True, "exit": 0, "state": "completed"}

    monkeypatch.setattr(
        "omega_prime.cron.scheduler._default_purple_cycle_runner", runner
    )
    root = tmp_path / "install"
    store = JobStore(root / "cron" / "jobs.json")
    schedule_purple_cycle(store, due_at=1000, interval_seconds=60, mode="plan")
    DeskDriver(root, clock=lambda: 1000.0).tick_once()
    assert calls == ["plan"]
    saved = list_purple_cycles(JobStore(root / "cron" / "jobs.json"))
    assert saved[0]["last_ran_at"] == 1000.0
    assert saved[0]["due_at"] == 1060.0


def test_scheduler_service_runs_purple_cycles_not_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def runner(job: dict, root: Path | None = None) -> dict[str, object]:
        calls.append(str(job["mode"]))
        return {"ok": True, "exit": 0, "state": "completed"}

    monkeypatch.setattr(
        "omega_prime.cron.scheduler._default_purple_cycle_runner", runner
    )
    store = JobStore(tmp_path / "jobs.json")
    schedule_purple_cycle(store, due_at=1, interval_seconds=3600, mode="run")
    seen: list[str] = []

    def _model(prompt: str) -> str:
        seen.append(prompt)
        return "nope"

    service = SchedulerService(store, _model)
    try:
        service.start()
    finally:
        service.stop()
    assert calls == ["run"]
    assert seen == []
    ran_at = store.jobs[0]["last_ran_at"]
    assert isinstance(ran_at, float)
    assert store.jobs[0]["due_at"] > ran_at


def test_desk_driver_passes_the_install_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []

    def fake_run_cycle(**kwargs: object) -> dict[str, object]:
        seen.append(kwargs["root"])
        return {"ok": True, "exit": 0, "state": "completed"}

    monkeypatch.setattr("omega_prime.grokbot.purple_cycle.run_cycle", fake_run_cycle)
    install = tmp_path / "install"
    store = JobStore(install / "cron" / "jobs.json")
    schedule_purple_cycle(store, due_at=1000, interval_seconds=60, mode="plan")
    DeskDriver(install, clock=lambda: 1000.0).tick_once()
    assert seen == [install.resolve()]


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

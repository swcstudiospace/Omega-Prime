# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Purple-cycle cron jobs: schedule, clear, and tick."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_desk_driver_dispatches_due_prompt_with_delegate_parent(
    tmp_path: Path,
) -> None:
    from omega_prime.agent.conversation_loop import Agent
    from omega_prime.agent.model import ScriptedModel

    root = tmp_path / "install"
    store = JobStore(root / "cron" / "jobs.json")
    store.schedule("summarize the scheduled task", due_at=1000)
    parent = Agent(model=ScriptedModel([]), tools={})
    parent.child_model = ScriptedModel(
        [{"role": "assistant", "content": "scheduled task completed"}]
    )

    DeskDriver(root, parent=parent, clock=lambda: 1000.0).tick_once()

    saved = JobStore(root / "cron" / "jobs.json").jobs[0]
    assert saved["last_result"] == {
        "state": "succeeded",
        "summary": "scheduled task completed",
    }
    assert saved["history"][-1]["disposition"] == "succeeded"


def test_desk_driver_writes_blocked_receipt_for_prompt_without_parent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "install"
    store = JobStore(root / "cron" / "jobs.json")
    store.schedule("summarize the scheduled task", due_at=1000)

    DeskDriver(root, clock=lambda: 1000.0).tick_once()

    saved = JobStore(root / "cron" / "jobs.json").jobs[0]
    assert saved["last_result"] == {
        "state": "blocked",
        "error": "ValueError: delegate returned error or pending output",
    }
    assert saved["history"][-1]["disposition"] == "blocked"


def test_desk_driver_hides_tick_exception_text_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel = "credential-shaped-tick-sentinel"
    later_ticks: list[str] = []

    def fail_watchdog(store: JobStore, now: float) -> None:
        raise RuntimeError(sentinel)

    monkeypatch.setattr("omega_prime.cron.desk_driver.tick_roe_watchdog", fail_watchdog)
    monkeypatch.setattr(
        "omega_prime.cron.desk_driver.tick_pin_bump_schedules",
        lambda *args, **kwargs: later_ticks.append("pin-bump"),
    )
    monkeypatch.setattr(
        "omega_prime.cron.desk_driver.tick_purple_cycles",
        lambda *args, **kwargs: later_ticks.append("purple-cycle"),
    )

    DeskDriver(tmp_path, clock=lambda: 1000.0).tick_once()

    stderr = capsys.readouterr().err
    assert "RuntimeError" in stderr
    assert "roe-watchdog tick failed" in stderr
    assert sentinel not in stderr
    assert later_ticks == ["pin-bump", "purple-cycle"]


def test_desk_driver_runs_purple_cycles_not_the_model(
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
    schedule_purple_cycle(store, due_at=1, interval_seconds=3600, mode="run")

    DeskDriver(root, clock=lambda: 2.0).tick_once()

    assert calls == ["run"]
    saved = JobStore(root / "cron" / "jobs.json").jobs[0]
    assert saved["last_ran_at"] == 2.0
    assert saved["due_at"] > saved["last_ran_at"]


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


def test_purple_cycle_claim_preserves_concurrent_create_and_removal(
    tmp_path: Path,
) -> None:
    from threading import Event, Thread

    store = _store(tmp_path)
    other_store = JobStore(store.path)
    job_id = schedule_purple_cycle(store, due_at=10, interval_seconds=30)
    entered = Event()
    release = Event()
    result = []

    def runner(job: dict) -> dict:
        entered.set()
        assert release.wait(timeout=5)
        return {"ok": True, "exit": 0, "state": "completed"}

    worker = Thread(
        target=lambda: result.extend(tick_purple_cycles(store, 10, runner=runner))
    )
    worker.start()
    assert entered.wait(timeout=5)
    created_id = other_store.schedule("concurrent prompt", 100)
    assert other_store.remove(job_id, kind=PURPLE_CYCLE_KIND)
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert result == []
    persisted = JobStore(store.path).jobs
    assert [job["id"] for job in persisted] == [created_id]


def test_stale_purple_occurrence_is_missed_and_coalesced_forward(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    schedule_purple_cycle(store, due_at=10, interval_seconds=30)
    calls = []

    def runner(job: dict) -> dict:
        calls.append(job)
        return {"ok": True, "exit": 0}

    ran = tick_purple_cycles(
        store,
        1000,
        runner=runner,
    )
    assert calls == []
    assert len(ran) == 1
    assert ran[0]["history"][-1]["status"] == "missed"
    assert ran[0]["last_result"]["state"] == "missed"
    assert ran[0]["due_at"] == 1030


def test_purple_runner_failure_is_settled_before_next_job(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = schedule_purple_cycle(store, due_at=10, interval_seconds=30)
    second = schedule_purple_cycle(store, due_at=10, interval_seconds=30)
    calls = []

    def runner(job: dict) -> dict:
        calls.append(job["id"])
        if job["id"] == first:
            raise RuntimeError("sensitive runner detail")
        return {"ok": True, "exit": 0, "state": "completed"}

    ran = tick_purple_cycles(store, 10, runner=runner)
    assert calls == [first, second]
    assert [job["id"] for job in ran] == [second]
    jobs = {job["id"]: job for job in JobStore(store.path).jobs}
    assert jobs[first]["history"][-1]["status"] == "failed"
    assert jobs[first]["last_result"]["error"] == "RuntimeError"
    assert "sensitive runner detail" not in str(jobs[first]["last_result"])
    assert jobs[first]["due_at"] == jobs[second]["due_at"] == 40

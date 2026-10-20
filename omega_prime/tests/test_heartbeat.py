"""Tests for the session heartbeat cron kind (Phase 57). Hermetic."""

from __future__ import annotations

import pytest

from omega_prime.cron.heartbeat import (
    HEARTBEAT_KIND,
    clear_heartbeat,
    list_heartbeats,
    schedule_heartbeat,
    tick_heartbeats,
)
from omega_prime.cron.scheduler import JobStore


def _store(tmp_path):
    return JobStore(tmp_path / "cron" / "jobs.json")


def test_schedule_and_list_heartbeat(tmp_path):
    store = _store(tmp_path)
    job_id = schedule_heartbeat(
        store, "sess-1", "check in", interval_seconds=60, due_at=1000
    )
    beats = list_heartbeats(store)
    assert len(beats) == 1
    assert beats[0]["id"] == job_id
    assert beats[0]["kind"] == HEARTBEAT_KIND
    assert beats[0]["session"] == "sess-1"
    # Persisted.
    assert len(list_heartbeats(_store(tmp_path))) == 1


def test_heartbeat_kind_and_session_are_persisted_together(tmp_path, monkeypatch):
    from threading import Event, Thread

    path = tmp_path / "cron" / "jobs.json"
    scheduler_store = JobStore(path)
    generic_store = JobStore(path)
    original_save = scheduler_store._save
    save_entered = Event()
    release_save = Event()
    generic_started = Event()
    job_ids = []
    generic_ran = []

    def paused_save():
        save_entered.set()
        assert release_save.wait(timeout=5)
        original_save()

    monkeypatch.setattr(scheduler_store, "_save", paused_save)
    scheduler = Thread(
        target=lambda: job_ids.append(
            schedule_heartbeat(
                scheduler_store,
                "sess-atomic",
                "check in",
                interval_seconds=60,
                due_at=1000,
            )
        )
    )
    scheduler.start()
    assert save_entered.wait(timeout=5)

    def generic_tick():
        generic_started.set()
        generic_ran.extend(generic_store.tick(1000, lambda _prompt: "generic"))

    generic = Thread(target=generic_tick)
    generic.start()
    assert generic_started.wait(timeout=5)
    release_save.set()
    scheduler.join(timeout=5)
    generic.join(timeout=5)
    assert not scheduler.is_alive()
    assert not generic.is_alive()

    assert generic_ran == []
    persisted = next(job for job in JobStore(path).jobs if job["id"] == job_ids[0])
    assert persisted["kind"] == HEARTBEAT_KIND
    assert persisted["session"] == "sess-atomic"


def test_heartbeat_requires_interval_and_session(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="interval"):
        schedule_heartbeat(store, "s", "p", interval_seconds=0, due_at=0)
    with pytest.raises(ValueError, match="session"):
        schedule_heartbeat(store, "  ", "p", interval_seconds=10, due_at=0)


def test_tick_reenters_named_session(tmp_path):
    store = _store(tmp_path)
    schedule_heartbeat(store, "sess-A", "ping", interval_seconds=60, due_at=1000)
    calls = []

    def session_runner(session, prompt):
        calls.append((session, prompt))
        return f"ran {session}"

    ran = tick_heartbeats(store, 1000, session_runner)
    assert calls == [("sess-A", "ping")]
    assert ran[0]["last_result"] == "ran sess-A"
    # Interval job reschedules.
    assert ran[0]["due_at"] == 1060
    # Not due again at the same instant.
    assert tick_heartbeats(store, 1000, session_runner) == []
    assert len(calls) == 1


def test_tick_ignores_non_heartbeat_jobs(tmp_path):
    store = _store(tmp_path)
    store.schedule("plain job", 1000)  # one-shot, no kind
    calls = []
    ran = tick_heartbeats(store, 1000, lambda s, p: calls.append((s, p)))
    assert ran == [] and calls == []


def test_clear_heartbeat(tmp_path):
    store = _store(tmp_path)
    job_id = schedule_heartbeat(store, "s", "p", interval_seconds=60, due_at=1000)
    assert clear_heartbeat(store, job_id) == {"cleared": job_id}
    assert list_heartbeats(store) == []
    assert "error" in clear_heartbeat(store, job_id)


def test_heartbeat_tools_roundtrip(tmp_path):
    import json as _json

    from omega_prime.tools.heartbeat import (
        HEARTBEAT_TOOL_NAMES,
        register_heartbeat_tools,
    )
    from omega_prime.tools.registry import ToolRegistry

    class _Approvals:
        def is_approved(self, name):
            return True

    registry = ToolRegistry(approval_log=_Approvals())
    names = register_heartbeat_tools(registry, tmp_path)
    assert names == list(HEARTBEAT_TOOL_NAMES)
    out = _json.loads(
        registry.dispatch(
            "heartbeat_set",
            {"session": "sess", "prompt": "p", "interval_seconds": 30},
        )
    )
    assert out["session"] == "sess"
    listed = _json.loads(registry.dispatch("heartbeat_list", {}))
    assert len(listed) == 1
    cleared = _json.loads(
        registry.dispatch("heartbeat_clear", {"job_id": out["scheduled"]})
    )
    assert cleared == {"cleared": out["scheduled"]}


def test_heartbeat_tools_disabled(tmp_path):
    from omega_prime.tools.heartbeat import register_heartbeat_tools
    from omega_prime.tools.registry import ToolRegistry

    registry = ToolRegistry()
    assert register_heartbeat_tools(registry, tmp_path, enabled=False) == []
    assert registry.schemas() == []

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co

"""Deterministic HeartbeatRuntime behavior regressions (Phase 61-02).

Overdue beats run synchronously through ``start()``, concurrency uses
thread events with bounded joins as hang detectors, and time is an injected
mutable clock. Scheduler-calendar tests use a paused real backend or its real
executor, without wall-clock delivery or polling. Direct ``_fire_job`` drives
exercise the real claim/callback/finish path and assert consumed outcomes
(disk history, runner calls, owned sink events, live scheduler state),
never scheduler call kwargs.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager

import pytest
from apscheduler.schedulers.base import STATE_RUNNING, STATE_STOPPED

from omega_prime.agent.session_lease import SessionLease
from omega_prime.cron.heartbeat_runtime import HeartbeatRuntime
from omega_prime.cron.scheduler import HISTORY_LIMIT, JobStore


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@contextmanager
def _live(tmp_path, now: float = 1000.0, **kwargs):
    clock = Clock(now)
    with HeartbeatRuntime(tmp_path / "cron" / "jobs.json", clock=clock, **kwargs) as rt:
        yield rt, clock


def _fresh(rt: HeartbeatRuntime) -> list[dict]:
    return JobStore(rt.path).jobs


def _row(rt: HeartbeatRuntime, job_id: str) -> dict:
    for job in _fresh(rt):
        if isinstance(job, dict) and job.get("id") == job_id:
            return job
    raise AssertionError(f"job {job_id} missing from {rt.path}")


# ---------------------------------------------------------- SessionLease ---


def test_lease_wait_wakes_on_release():
    lease = SessionLease()
    lease.acquire()
    started = threading.Event()
    done = threading.Event()

    def waiter() -> None:
        started.set()
        lease.acquire(wait=True)
        done.set()
        lease.release()

    thread = threading.Thread(target=waiter)
    thread.start()
    try:
        assert started.wait(timeout=10)
        assert not done.is_set()
        lease.release()
        assert done.wait(timeout=10)
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert lease.held is False


def test_lease_wait_default_fast_fail():
    lease = SessionLease()
    lease.acquire()
    try:
        with pytest.raises(RuntimeError):
            lease.acquire()
        with pytest.raises(RuntimeError):
            lease.acquire(wait=False)
    finally:
        lease.release()
    lease.acquire(wait=True)
    lease.release()
    assert lease.held is False
    with lease:
        assert lease.held is True
    assert lease.held is False


# ------------------------------------------------------------------ tracer ---


def test_tracer_named_delivery(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            return "done"

        rt.bind("sess-A", runner)
        job_id = rt.schedule("sess-A", "ping", interval_seconds=60, due_at=1000.0)
        ran = rt.start()
        assert [job["id"] for job in ran] == [job_id]
        assert calls == ["ping"]
        row = _row(rt, job_id)
        assert row["last_result"] == "done"
        assert row["last_ran_at"] == 1000.0
        assert row["history"] == [{"ran_at": 1000.0, "result": "done"}]
        assert row["claimed_at"] is None
        assert row["due_at"] == 1060.0


def test_unknown_target_session_and_job(tmp_path):
    service: list[dict] = []
    with _live(tmp_path, event_sink=service.append) as (rt, _clock):
        calls: list[str] = []
        rt.bind("other", lambda prompt: calls.append(prompt))
        job_id = rt.schedule("ghost", "ping", interval_seconds=60, due_at=1000.0)
        ran = rt.start()
        assert ran[0]["last_result"] == {"error": "unknown heartbeat session: 'ghost'"}
        assert calls == []
        row = _row(rt, job_id)
        assert row["due_at"] == 1060.0
        assert len(service) == 1
        assert service[0]["type"] == "prime_degraded"
        assert service[0]["session"] == "ghost"
        assert rt.clear("no-such-id") == {"error": "no heartbeat job: 'no-such-id'"}


def test_nonserializable_structured_error(tmp_path):
    with _live(tmp_path) as (rt, clock):
        seen: list[dict] = []

        def produce(prompt: str) -> object:
            return object()

        rt.bind("s", produce, event_sink=seen.append)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        ran = rt.start()
        assert ran[0]["last_result"] == {
            "error": "heartbeat result is not JSON-serializable"
        }
        assert len(seen) == 1
        assert seen[0]["type"] == "prime_degraded"
        assert seen[0]["error"] == "heartbeat result is not JSON-serializable"
        text = rt.path.read_text(encoding="utf-8")
        assert "object at 0x" not in text
        assert "repr(" not in text
        # The schedule keeps firing after a bad payload.
        rt.bind("s", lambda prompt: "recovered", event_sink=seen.append)
        clock.now += 61.0
        again = rt._fire_job(job_id)
        assert again is not None
        assert again["last_result"] == "recovered"
        assert len(seen) == 1


def test_kind_filter_foreign_rows_untouched(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        foreign = JobStore(rt.path)
        foreign_id = foreign.schedule("plain job", 1000.0)
        rt.bind("s", lambda prompt: "hb")
        beat_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        ran = rt.start()
        assert [job["id"] for job in ran] == [beat_id]
        rows = {job["id"]: job for job in _fresh(rt)}
        assert rows[foreign_id]["last_result"] is None
        assert rows[foreign_id]["history"] == []
        assert rows[foreign_id].get("claimed_at") is None
        assert rows[foreign_id]["due_at"] == 1000.0
        assert rt.job_ids == [beat_id]


# ---------------------------------------------------------------- lifecycle ---


def test_lifecycle_double_start_and_stop(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        rt.bind("s", lambda prompt: "x")
        job_id = rt.schedule("s", "p", interval_seconds=60, due_at=2000.0)
        assert rt.start() == []
        assert rt.start() == []
        assert rt.job_ids == [job_id]
        rt.stop()
        rt.stop()
        assert rt.job_ids == []


def test_stop_before_start_is_safe(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        rt.stop()
        assert rt.job_ids == []


def test_shutdown_close_settles(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        rt.bind("s", lambda prompt: "x")
        job_id = rt.schedule("s", "p", interval_seconds=60, due_at=2000.0)
        rt.start()
        assert rt.job_ids == [job_id]
        rt.close()
        rt.close()
        assert rt.job_ids == []
        assert rt.bindings == {}


def test_resync_prune_stale_on_restart(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        rt.bind("s", lambda prompt: "x")
        stale_id = rt.schedule("s", "old", interval_seconds=60, due_at=900.0)
        live_id = rt.schedule("s", "new", interval_seconds=60, due_at=950.0)
        rt.start()
        rt.stop()
        assert rt.clear(stale_id) == {"cleared": stale_id}
    with _live(tmp_path, now=1061.0) as (rt2, _clock2):
        calls: list[str] = []

        def run(prompt):
            calls.append(prompt)
            return "ran"

        rt2.bind("s", run)
        ran = rt2.start()
        assert [job["id"] for job in ran] == [live_id]
        assert calls == ["new"]
        assert rt2.job_ids == [live_id]


def test_start_returns_overdue_in_due_order(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        order: list[str] = []

        def run(prompt):
            order.append(prompt)
            return "ok"

        rt.bind("s", run)
        late = rt.schedule("s", "late", interval_seconds=60, due_at=900.0)
        early = rt.schedule("s", "early", interval_seconds=60, due_at=800.0)
        ran = rt.start()
        assert [job["id"] for job in ran] == [early, late]
        assert order == ["early", "late"]


def test_completed_rows_ignored(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        calls: list[str] = []
        rt.bind("s", lambda prompt: calls.append(prompt))
        job_id = rt.schedule("s", "p", interval_seconds=60, due_at=900.0)
        rows = _fresh(rt)
        for job in rows:
            if job["id"] == job_id:
                job["completed"] = True
        rt.path.write_text(json.dumps({"jobs": rows}), encoding="utf-8")
        assert rt.start() == []
        assert calls == []
        assert rt.job_ids == []
        assert len(rt.list_jobs()) == 1


# --------------------------------------------------------------- concurrency ---


def test_overlap_same_job_runs_once(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            entered.set()
            assert release.wait(timeout=10)
            return "once"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        first = threading.Thread(target=rt._fire_job, args=(job_id,))
        first.start()
        try:
            assert entered.wait(timeout=10)
            assert rt._fire_job(job_id) is None
            release.set()
            first.join(timeout=10)
        finally:
            release.set()
            first.join(timeout=10)
        assert not first.is_alive()
        assert calls == ["ping"]
        assert _row(rt, job_id)["history"] == [{"ran_at": 1000.0, "result": "once"}]


def test_lost_update_clear_during_beat_no_resurrection(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()

        def runner(prompt: str) -> str:
            entered.set()
            assert release.wait(timeout=10)
            return "late"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        beat = threading.Thread(target=rt._fire_job, args=(job_id,))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            assert rt.list_jobs()[0]["id"] == job_id
            assert rt.clear(job_id) == {"cleared": job_id}
            release.set()
            beat.join(timeout=10)
        finally:
            release.set()
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert _fresh(rt) == []


def test_lost_update_schedule_during_beat_no_clobber(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()

        def runner(prompt: str) -> str:
            entered.set()
            assert release.wait(timeout=10)
            return "first"

        rt.bind("s", runner)
        first_id = rt.schedule("s", "one", interval_seconds=60, due_at=1000.0)
        beat = threading.Thread(target=rt._fire_job, args=(first_id,))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            second_id = rt.schedule("s", "two", interval_seconds=60, due_at=5000.0)
            release.set()
            beat.join(timeout=10)
        finally:
            release.set()
            beat.join(timeout=10)
        assert not beat.is_alive()
        rows = {job["id"]: job for job in _fresh(rt)}
        assert rows[first_id]["last_result"] == "first"
        assert rows[second_id]["prompt"] == "two"
        assert rows[second_id]["last_result"] is None


def test_no_deadlock_lease_wait_with_live_bookkeeping(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        lease = SessionLease()
        lease.acquire()
        entered = threading.Event()
        calls: list[str] = []

        def runner(prompt: str) -> str:
            entered.set()
            lease.acquire(wait=True)
            try:
                calls.append(prompt)
            finally:
                lease.release()
            return "beat"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        other_id = rt.schedule("s", "other", interval_seconds=60, due_at=5000.0)
        beat = threading.Thread(target=rt._fire_job, args=(job_id,))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(RuntimeError):
                lease.acquire()
            assert rt.clear(other_id) == {"cleared": other_id}
            assert [job["id"] for job in rt.list_jobs()] == [job_id]
            lease.release()
            beat.join(timeout=10)
        finally:
            if lease.held:
                lease.release()
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert calls == ["ping"]
        assert _row(rt, job_id)["last_result"] == "beat"


# ------------------------------------------------------------ ownership/sink ---


def test_rebind_snapshot_in_flight(tmp_path):
    with _live(tmp_path) as (rt, clock):
        entered = threading.Event()
        release = threading.Event()
        first_calls: list[str] = []
        second_calls: list[str] = []
        first_sink: list[dict] = []
        second_sink: list[dict] = []

        def first(prompt: str) -> str:
            first_calls.append(prompt)
            entered.set()
            assert release.wait(timeout=10)
            return "first"

        def second(prompt: str) -> str:
            second_calls.append(prompt)
            return "second"

        rt.bind("s", first, event_sink=first_sink.append)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        beat = threading.Thread(target=rt._fire_job, args=(job_id,))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            rt.bind("s", second, event_sink=second_sink.append)
            release.set()
            beat.join(timeout=10)
        finally:
            release.set()
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert first_calls == ["ping"]
        assert second_calls == []
        assert first_sink == [] and second_sink == []
        clock.now += 61.0
        again = rt._fire_job(job_id)
        assert again is not None
        assert again["last_result"] == "second"
        assert second_calls == ["ping"]


def test_conditional_unbind(tmp_path):
    with _live(tmp_path) as (rt, clock):
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            return "ok"

        def other(prompt: str) -> str:
            return "other"

        rt.bind("s", runner)
        assert rt.unbind("s", runner=other) is False
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        fired = rt._fire_job(job_id)
        assert fired is not None
        assert fired["last_result"] == "ok"
        assert rt.unbind("missing") is False
        assert rt.unbind("s", runner=runner) is True
        assert rt.bindings == {}
        clock.now += 61.0
        again = rt._fire_job(job_id)
        assert again is not None
        assert again["last_result"] == {"error": "unknown heartbeat session: 's'"}
        assert calls == ["ping"]


def test_sink_ownership_per_binding(tmp_path):
    service: list[dict] = []
    with _live(tmp_path, event_sink=service.append) as (rt, _clock):
        sink_a: list[dict] = []
        sink_b: list[dict] = []

        def fail(prompt: str) -> str:
            raise ValueError("bad beat")

        rt.bind("a", fail, event_sink=sink_a.append)
        rt.bind("b", lambda prompt: "fine", event_sink=sink_b.append)
        rt.schedule("a", "pa", interval_seconds=60, due_at=900.0)
        rt.schedule("b", "pb", interval_seconds=60, due_at=950.0)
        rt.start()
        assert len(sink_a) == 1
        assert sink_a[0]["type"] == "prime_degraded"
        assert sink_a[0]["session"] == "a"
        assert sink_b == []
        assert service == []


def test_service_sink_undirected_only(tmp_path):
    service: list[dict] = []
    owned: list[dict] = []
    with _live(tmp_path, event_sink=service.append) as (rt, _clock):
        rt.bind("owned", lambda prompt: "ok", event_sink=owned.append)
        rt.schedule("ghost", "p", interval_seconds=60, due_at=1000.0)
        rt.schedule("owned", "p", interval_seconds=60, due_at=1000.0)
        rt.start()
        assert owned == []
        assert len(service) == 1
        assert service[0]["session"] == "ghost"


def test_sink_failure_guarded(tmp_path):
    with _live(tmp_path) as (rt, _clock):

        def bad_sink(event: dict) -> None:
            raise RuntimeError("sink down")

        def fail(prompt: str) -> str:
            raise ValueError("runner down")

        rt.bind("s", fail, event_sink=bad_sink)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        finished = rt._fire_job(job_id)
        assert finished is not None
        assert finished["last_result"] == {"error": "ValueError: runner down"}


# ------------------------------------------------------------------ failures ---


def test_runner_exception_degrades_and_continues(tmp_path):
    secret = "sk-abcdefgh12345678"
    with _live(tmp_path) as (rt, clock):
        seen: list[dict] = []

        def boom(prompt: str) -> str:
            raise ValueError(f"provider blew up token={secret}")

        rt.bind("s", boom, event_sink=seen.append)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        ran = rt.start()
        assert ran[0]["last_result"] == {
            "error": "ValueError: provider blew up token=[REDACTED]"
        }
        assert len(seen) == 1
        assert seen[0]["type"] == "prime_degraded"
        text = rt.path.read_text(encoding="utf-8")
        assert secret not in text
        assert "[REDACTED]" in text
        assert seen[0]["error"] == "ValueError: provider blew up token=[REDACTED]"
        rt.bind("s", lambda prompt: "steady", event_sink=seen.append)
        clock.now += 61.0
        again = rt._fire_job(job_id)
        assert again is not None
        assert again["last_result"] == "steady"
        assert len(seen) == 1


def test_base_exception_propagates_guard_released(tmp_path):
    with _live(tmp_path) as (rt, clock):
        calls: list[str] = []

        def stop(prompt: str) -> str:
            calls.append(prompt)
            raise KeyboardInterrupt("stop")

        rt.bind("s", stop)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        with pytest.raises(KeyboardInterrupt):
            rt._fire_job(job_id)
        row = _row(rt, job_id)
        assert row["claimed_at"] is None
        assert row["history"] == []
        assert row["last_result"] is None

        def recover(prompt):
            calls.append(prompt)
            return "recovered"

        rt.bind("s", recover)
        clock.now += 61.0
        again = rt._fire_job(job_id)
        assert again is not None
        assert again["last_result"] == "recovered"
        assert calls == ["ping", "ping"]


def test_history_bounded(tmp_path):
    with _live(tmp_path) as (rt, clock):
        rt.bind("s", lambda prompt: "ok")
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        for _ in range(HISTORY_LIMIT + 5):
            finished = rt._fire_job(job_id)
            assert finished is not None
            clock.now += 61.0
        row = _row(rt, job_id)
        assert len(row["history"]) == HISTORY_LIMIT


def test_schedule_validation_and_defaults(tmp_path):
    with _live(tmp_path) as (rt, clock):
        rt.bind("s", lambda prompt: "ok")
        with pytest.raises(ValueError):
            rt.schedule("  ", "p", interval_seconds=60, due_at=1000.0)
        with pytest.raises(ValueError):
            rt.schedule("s", "p", interval_seconds=0, due_at=1000.0)
        with pytest.raises(ValueError):
            rt.schedule("s", "p", interval_seconds=True, due_at=1000.0)
        job_id = rt.schedule("s", "p", interval_seconds=60)
        assert _row(rt, job_id)["due_at"] == clock.now


def test_missing_fields_skip_with_error(tmp_path):
    service: list[dict] = []
    with _live(tmp_path, event_sink=service.append) as (rt, _clock):
        calls: list[str] = []
        rt.bind("s", lambda prompt: calls.append(prompt))
        path = rt.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": "m1",
                            "kind": "session_heartbeat",
                            "prompt": "p",
                            "due_at": 1000.0,
                            "interval_seconds": 60,
                            "completed": False,
                            "last_result": None,
                            "last_ran_at": None,
                            "history": [],
                            "claimed_at": None,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        ran = rt.start()
        assert calls == []
        assert "error" in ran[0]["last_result"]
        assert rt.job_ids == []
        assert [event["job_id"] for event in service] == ["m1"]
        assert rt.start() == []
        assert [event["job_id"] for event in service] == ["m1"]


def test_malformed_store_raises(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        rt.bind("s", lambda prompt: "ok")
        rt.path.parent.mkdir(parents=True, exist_ok=True)
        rt.path.write_text(json.dumps({"jobs": "nope"}), encoding="utf-8")
        with pytest.raises(ValueError):
            rt.start()


# ------------------------------------------------------------------ tool glue ---


def test_same_path_reuse_and_tool_sync(tmp_path):
    import json as _json

    from omega_prime.tools.heartbeat import register_heartbeat_tools
    from omega_prime.tools.registry import ToolRegistry

    class _Approvals:
        def is_approved(self, name):
            return True

    registry = ToolRegistry(approval_log=_Approvals())
    try:
        register_heartbeat_tools(registry, tmp_path)
        first = registry.runtime_bindings["prime_heartbeat"]
        first.start()
        register_heartbeat_tools(registry, tmp_path)
        out = _json.loads(
            registry.dispatch(
                "heartbeat_set",
                {"session": "sess", "prompt": "p", "interval_seconds": 30},
            )
        )
        assert out["session"] == "sess"
        assert first.job_ids == [out["scheduled"]]
        listed = _json.loads(registry.dispatch("heartbeat_list", {}))
        assert [job["id"] for job in listed] == [out["scheduled"]]
        cleared = _json.loads(
            registry.dispatch("heartbeat_clear", {"job_id": out["scheduled"]})
        )
        assert cleared == {"cleared": out["scheduled"]}
        assert first.job_ids == []
    finally:
        binding = registry.runtime_bindings.get("prime_heartbeat")
        if isinstance(binding, HeartbeatRuntime):
            binding.close()


def test_reentrant_service_sink_no_deadlock(tmp_path):
    seen: list[dict] = []
    observed: dict[str, object] = {}
    box: dict[str, object] = {}

    def reentrant(event: dict) -> None:
        seen.append(event)
        runtime = box["rt"]
        assert isinstance(runtime, HeartbeatRuntime)
        observed["listed"] = runtime.list_jobs()
        observed["bindings"] = runtime.bindings
        observed["cleared"] = runtime.clear(str(box["other"]))

    with _live(tmp_path, event_sink=reentrant) as (rt, _clock):
        box["rt"] = rt
        other = rt.schedule("s", "other", interval_seconds=60, due_at=5000.0)
        box["other"] = other
        ghost = rt.schedule("ghost", "ping", interval_seconds=60, due_at=1000.0)
        done: list[list[dict]] = []
        beat = threading.Thread(target=lambda: done.append(rt.start()))
        beat.start()
        beat.join(timeout=10)
        assert not beat.is_alive()
        assert [job["id"] for job in done[0]] == [ghost]
        assert len(seen) == 1
        assert seen[0]["session"] == "ghost"
        listed = observed["listed"]
        assert isinstance(listed, list)
        assert [job["id"] for job in listed] == [other, ghost]
        assert observed["cleared"] == {"cleared": other}
        assert _row(rt, ghost)["last_result"] == {
            "error": "unknown heartbeat session: 'ghost'"
        }


def test_clear_vs_resync_no_stale_live_entry(tmp_path):
    with _live(tmp_path) as (rt, clock):
        entered = threading.Event()
        release = threading.Event()

        def runner(prompt: str) -> str:
            entered.set()
            assert release.wait(timeout=10)
            return "late"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=5000.0)
        rt.start()
        assert rt.job_ids == [job_id]
        rt._scheduler.pause()
        clock.now = 5000.0
        results: list[dict | None] = []
        beat = threading.Thread(target=lambda: results.append(rt._fire_job(job_id)))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            assert rt.clear(job_id) == {"cleared": job_id}
            assert rt.job_ids == []
            release.set()
            beat.join(timeout=10)
        finally:
            release.set()
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert results == [None]
        assert _fresh(rt) == []
        assert rt.job_ids == []


def test_concurrent_churn_keeps_scheduler_reconciled(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        rt.bind("s", lambda prompt: "ok")
        rt.start()
        errors: list[Exception] = []

        def churn(worker: int) -> None:
            try:
                for index in range(20):
                    job_id = rt.schedule(
                        "s",
                        f"p-{worker}-{index}",
                        interval_seconds=60,
                        due_at=5000.0,
                    )
                    rt.list_jobs()
                    rt.clear(job_id)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=churn, args=(worker,)) for worker in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert _fresh(rt) == []
        assert rt.job_ids == []


def test_finish_preserves_external_due_and_interval(tmp_path):
    with _live(tmp_path) as (rt, clock):
        entered = threading.Event()
        release = threading.Event()

        def runner(prompt: str) -> str:
            entered.set()
            assert release.wait(timeout=10)
            return "ran"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        results: list[dict | None] = []
        beat = threading.Thread(target=lambda: results.append(rt._fire_job(job_id)))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            rows = _fresh(rt)
            for job in rows:
                if job["id"] == job_id:
                    job["due_at"] = 9000.0
                    job["interval_seconds"] = 600
            rt.path.write_text(json.dumps({"jobs": rows}), encoding="utf-8")
            clock.now += 1.0
            release.set()
            beat.join(timeout=10)
        finally:
            release.set()
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert len(results) == 1
        assert results[0] is not None
        assert results[0]["last_result"] == "ran"
        row = _row(rt, job_id)
        assert row["last_result"] == "ran"
        assert len(row["history"]) == 1
        assert row["interval_seconds"] == 600
        assert row["due_at"] == 9000.0
        assert row["claimed_at"] is None
        assert rt.job_ids == []
        assert rt.start() == []
        assert rt.job_ids == [job_id]


def test_finish_drops_when_claim_superseded(tmp_path):
    with _live(tmp_path) as (rt, clock):
        entered = threading.Event()
        release = threading.Event()

        def runner(prompt: str) -> str:
            entered.set()
            assert release.wait(timeout=10)
            return "ran"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        results: list[dict | None] = []
        beat = threading.Thread(target=lambda: results.append(rt._fire_job(job_id)))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            rows = _fresh(rt)
            for job in rows:
                if job["id"] == job_id:
                    job["claimed_at"] = None
                    job["due_at"] = 9000.0
            rt.path.write_text(json.dumps({"jobs": rows}), encoding="utf-8")
            clock.now += 1.0
            release.set()
            beat.join(timeout=10)
        finally:
            release.set()
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert results == [None]
        row = _row(rt, job_id)
        assert row["last_result"] is None
        assert row["history"] == []
        assert row["due_at"] == 9000.0
        assert row["claimed_at"] is None


# --------------------------------------------- lifecycle races ---


def test_close_settles_blocked_overdue_start_no_late_restart(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            entered.set()
            assert release.wait(timeout=10)
            return "settled"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        ran: list[list[dict]] = []
        starter = threading.Thread(target=lambda: ran.append(rt.start()))
        starter.start()
        try:
            assert entered.wait(timeout=10)
            closer = threading.Thread(target=rt.close)
            closer.start()
            try:
                closer.join(timeout=0.5)
                assert closer.is_alive()
            finally:
                release.set()
                closer.join(timeout=10)
            starter.join(timeout=10)
        finally:
            release.set()
            starter.join(timeout=10)
        assert not starter.is_alive()
        assert not closer.is_alive()
        assert calls == ["ping"]
        assert [job["id"] for job in ran[0]] == [job_id]
        assert ran[0][0]["last_result"] == "settled"
        assert _row(rt, job_id)["last_result"] == "settled"
        assert _row(rt, job_id)["claimed_at"] is None
        assert rt.bindings == {}
        assert rt.job_ids == []
        assert rt._scheduler.state == STATE_STOPPED


def test_stop_before_start_settles_direct_callback(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()

        def runner(prompt: str) -> str:
            entered.set()
            assert release.wait(timeout=10)
            return "late-settled"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        finished: list[dict | None] = []
        beat = threading.Thread(target=lambda: finished.append(rt._fire_job(job_id)))
        beat.start()
        try:
            assert entered.wait(timeout=10)
            stopper = threading.Thread(target=rt.stop)
            stopper.start()
            try:
                stopper.join(timeout=0.5)
                assert stopper.is_alive()
            finally:
                release.set()
                stopper.join(timeout=10)
            beat.join(timeout=10)
        finally:
            release.set()
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert not stopper.is_alive()
        assert finished[0] is not None
        assert finished[0]["last_result"] == "late-settled"
        assert _row(rt, job_id)["last_result"] == "late-settled"
        assert rt._scheduler.state == STATE_STOPPED


def test_stale_unbind_keeps_replacement_and_other_owner(tmp_path):
    with _live(tmp_path) as (rt, clock):

        def runner_a(prompt: str) -> str:
            return "a-ok"

        def runner_b(prompt: str) -> str:
            return "b-ok"

        def intruder(prompt: str) -> str:
            return "intruder"

        rt.bind("a", runner_a)
        rt.bind("b", runner_b)
        job_a = rt.schedule("a", "pa", interval_seconds=60, due_at=5000.0)
        job_b = rt.schedule("b", "pb", interval_seconds=60, due_at=5000.0)
        assert rt.start() == []
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.unbind("a", runner=intruder, stop_if_empty=True) is False
        assert set(rt.bindings) == {"a", "b"}
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.unbind("a", runner=runner_a, stop_if_empty=True) is True
        assert set(rt.bindings) == {"b"}
        assert rt._scheduler.state == STATE_RUNNING
        assert job_b in rt.job_ids
        rt.bind("b", intruder)
        assert rt.unbind("b", runner=runner_b, stop_if_empty=True) is False
        assert set(rt.bindings) == {"b"}
        assert rt._scheduler.state == STATE_RUNNING
        clock.now = 5000.0
        fired = rt._fire_job(job_b)
        assert fired is not None
        assert fired["last_result"] == "intruder"
        assert rt.unbind("b", runner=intruder, stop_if_empty=True) is True
        assert rt.bindings == {}
        assert rt.job_ids == []
        assert rt._scheduler.state == STATE_STOPPED
        # Detaching a binding never deletes its persisted rows.
        assert _row(rt, job_a)["prompt"] == "pa"
        assert _row(rt, job_b)["last_result"] == "intruder"


def test_rebind_during_last_owner_shutdown_stays_live(tmp_path, monkeypatch):
    clock = Clock()
    rt = HeartbeatRuntime(tmp_path / "cron" / "jobs.json", clock=clock)
    entered = threading.Event()
    draining = threading.Event()
    release = threading.Event()
    old_calls: list[str] = []
    new_calls: list[str] = []
    real_wait = rt._wait_for_in_flight

    def observed_wait():
        draining.set()
        real_wait()

    monkeypatch.setattr(rt, "_wait_for_in_flight", observed_wait)

    def old(prompt: str) -> str:
        old_calls.append(prompt)
        entered.set()
        assert release.wait(timeout=10)
        return "old-done"

    def new(prompt: str) -> str:
        new_calls.append(prompt)
        return "new-done"

    worker = None
    stopper = None
    try:
        rt.bind("s", old)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1060)
        assert rt.start() == []
        clock.now = 1061
        worker = threading.Thread(target=lambda: rt._fire_job(job_id))
        worker.start()
        assert entered.wait(timeout=10)
        detached: list[bool] = []
        stopper = threading.Thread(
            target=lambda: detached.append(
                rt.unbind("s", runner=old, stop_if_empty=True)
            )
        )
        stopper.start()
        assert draining.wait(timeout=10)
        rt.bind("s", new)
        release.set()
        worker.join(timeout=10)
        stopper.join(timeout=10)
        assert not worker.is_alive()
        assert not stopper.is_alive()
        assert detached == [True]
        assert old_calls == ["ping"]
        clock.now = 1122
        rt._fire_job(job_id)
        assert new_calls == ["ping"]
        assert _row(rt, job_id)["last_result"] == "new-done"
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=10)
        if stopper is not None:
            stopper.join(timeout=10)
        rt.close()


def test_restart_delivers_future_beat_after_stop(tmp_path):
    with _live(tmp_path) as (rt, clock):
        calls: list[str] = []

        def run(prompt):
            calls.append(prompt)
            return "beat-ok"

        rt.bind("s", run)
        job_id = rt.schedule("s", "ping", interval_seconds=3600, due_at=1061.0)
        assert rt.start() == []
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.job_ids == [job_id]
        rt.stop()
        assert rt.job_ids == []
        assert rt._scheduler.state == STATE_STOPPED
        assert calls == []
        clock.now = 1061.0
        ran = rt.start()
        assert [job["id"] for job in ran] == [job_id]
        assert ran[0]["last_result"] == "beat-ok"
        assert calls == ["ping"]
        assert _row(rt, job_id)["last_result"] == "beat-ok"
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.job_ids == [job_id]


# --------------------------------- last-owner settlement/retry/self-races ---


def test_last_owner_unbind_settles_blocked_start_before_backend_start(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            entered.set()
            assert release.wait(timeout=10)
            return "settled"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        ran: list[list[dict]] = []
        starter = threading.Thread(target=lambda: ran.append(rt.start()))
        starter.start()
        detached: list[bool] = []
        unbinder = threading.Thread(
            target=lambda: detached.append(
                rt.unbind("s", runner=runner, stop_if_empty=True)
            )
        )
        try:
            assert entered.wait(timeout=10)
            unbinder.start()
            unbinder.join(timeout=0.5)
            assert unbinder.is_alive()
            assert starter.is_alive()
        finally:
            release.set()
            unbinder.join(timeout=10)
            starter.join(timeout=10)
        assert not unbinder.is_alive()
        assert not starter.is_alive()
        assert detached == [True]
        assert calls == ["ping"]
        assert [job["id"] for job in ran[0]] == [job_id]
        assert ran[0][0]["last_result"] == "settled"
        assert _row(rt, job_id)["last_result"] == "settled"
        assert _row(rt, job_id)["claimed_at"] is None
        assert rt.bindings == {}
        assert rt.job_ids == []
        assert rt._scheduler.state == STATE_STOPPED


def test_last_owner_unbind_shutdown_error_then_retry(tmp_path):
    with _live(tmp_path) as (rt, _clock):

        def runner(prompt: str) -> str:
            return "ok"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=5000.0)
        assert rt.start() == []
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.job_ids == [job_id]
        orig_shutdown = rt._scheduler.shutdown
        calls: list[int] = []

        def flaky(*args: object, **kwargs: object) -> None:
            if not calls:
                calls.append(1)
                raise OSError("boom")
            return orig_shutdown(*args, **kwargs)

        rt._scheduler.shutdown = flaky  # type: ignore[method-assign]
        try:
            with pytest.raises(OSError):
                rt.unbind("s", runner=runner, stop_if_empty=True)
        finally:
            rt._scheduler.shutdown = orig_shutdown  # type: ignore[method-assign]
        assert calls == [1]
        assert rt.bindings == {}
        assert rt._scheduler.state == STATE_RUNNING
        assert job_id in rt.job_ids
        assert _row(rt, job_id)["prompt"] == "ping"
        rt.stop()
        assert rt.bindings == {}
        assert rt.job_ids == []
        assert rt._scheduler.state == STATE_STOPPED
        assert rt.unbind("s", runner=runner, stop_if_empty=True) is False
        assert _row(rt, job_id)["prompt"] == "ping"


def test_last_owner_unbind_retry_preserves_replacement(tmp_path):
    with _live(tmp_path) as (rt, clock):

        def old(prompt: str) -> str:
            return "old"

        def new(prompt: str) -> str:
            return "new"

        rt.bind("s", old)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=5000.0)
        assert rt.start() == []
        assert rt._scheduler.state == STATE_RUNNING
        orig_shutdown = rt._scheduler.shutdown
        calls: list[int] = []

        def flaky(*args: object, **kwargs: object) -> None:
            if not calls:
                calls.append(1)
                raise OSError("boom")
            return orig_shutdown(*args, **kwargs)

        rt._scheduler.shutdown = flaky  # type: ignore[method-assign]
        try:
            with pytest.raises(OSError):
                rt.unbind("s", runner=old, stop_if_empty=True)
        finally:
            rt._scheduler.shutdown = orig_shutdown  # type: ignore[method-assign]
        assert rt.bindings == {}
        assert rt._scheduler.state == STATE_RUNNING
        rt.bind("s", new)
        assert rt.unbind("s", runner=old, stop_if_empty=True) is False
        assert set(rt.bindings) == {"s"}
        assert rt._scheduler.state == STATE_RUNNING
        assert job_id in rt.job_ids
        clock.now = 5000.0
        fired = rt._fire_job(job_id)
        assert fired is not None
        assert fired["last_result"] == "new"
        assert _row(rt, job_id)["last_result"] == "new"


def test_self_shutdown_from_callback_rejected(tmp_path):
    with _live(tmp_path) as (rt, clock):
        stop_errors: list[BaseException] = []

        def runner_stop(prompt: str) -> str:
            try:
                rt.stop()
            except RuntimeError as exc:
                stop_errors.append(exc)
                return "saw-stop"
            raise AssertionError("stop from callback should raise")

        rt.bind("s", runner_stop)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        ran = rt.start()
        assert len(stop_errors) == 1
        assert ran[0]["last_result"] == "saw-stop"
        assert set(rt.bindings) == {"s"}
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.job_ids == [job_id]

        close_errors: list[BaseException] = []

        def runner_close(prompt: str) -> str:
            try:
                rt.close()
            except RuntimeError as exc:
                close_errors.append(exc)
                return "saw-close"
            raise AssertionError("close from callback should raise")

        rt.bind("s", runner_close)
        clock.now = 1061.0
        finished = rt._fire_job(job_id)
        assert finished is not None
        assert finished["last_result"] == "saw-close"
        assert len(close_errors) == 1
        assert set(rt.bindings) == {"s"}
        assert rt._scheduler.state == STATE_RUNNING

        unbind_errors: list[BaseException] = []
        me: dict[str, object] = {}

        def runner_unbind(prompt: str) -> str:
            try:
                rt.unbind("s", runner=me["me"], stop_if_empty=True)
            except RuntimeError as exc:
                unbind_errors.append(exc)
                return "saw-unbind"
            raise AssertionError("unbind from callback should raise")

        me["me"] = runner_unbind
        rt.bind("s", runner_unbind)
        clock.now = 1122.0
        again = rt._fire_job(job_id)
        assert again is not None
        assert again["last_result"] == "saw-unbind"
        assert len(unbind_errors) == 1
        assert set(rt.bindings) == {"s"}
        assert rt._scheduler.state == STATE_RUNNING
        assert _row(rt, job_id)["claimed_at"] is None


def test_self_shutdown_from_sink_rejected(tmp_path):
    with _live(tmp_path) as (rt, clock):
        stop_seen: list[BaseException] = []
        close_seen: list[BaseException] = []
        unbind_seen: list[BaseException] = []
        phase: dict[str, str] = {"step": "stop"}

        def fail(prompt: str) -> str:
            raise ValueError("bad")

        def sink(event: dict) -> None:
            try:
                if phase["step"] == "stop":
                    rt.stop()
                elif phase["step"] == "close":
                    rt.close()
                else:
                    rt.unbind("s", runner=fail, stop_if_empty=True)
            except RuntimeError as exc:
                if phase["step"] == "stop":
                    stop_seen.append(exc)
                elif phase["step"] == "close":
                    close_seen.append(exc)
                else:
                    unbind_seen.append(exc)

        rt.bind("s", fail, event_sink=sink)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        assert rt.start()[0]["last_result"] == {"error": "ValueError: bad"}
        assert len(stop_seen) == 1
        assert set(rt.bindings) == {"s"}
        assert rt._scheduler.state == STATE_RUNNING
        phase["step"] = "close"
        clock.now = 1061.0
        again = rt._fire_job(job_id)
        assert again is not None
        assert again["last_result"] == {"error": "ValueError: bad"}
        assert len(close_seen) == 1
        assert set(rt.bindings) == {"s"}
        assert rt._scheduler.state == STATE_RUNNING
        phase["step"] = "unbind"
        clock.now = 1122.0
        third = rt._fire_job(job_id)
        assert third is not None
        assert third["last_result"] == {"error": "ValueError: bad"}
        assert len(unbind_seen) == 1
        assert set(rt.bindings) == {"s"}
        assert rt._scheduler.state == STATE_RUNNING
        assert _row(rt, job_id)["claimed_at"] is None


def test_stop_settles_blocked_start_no_restart(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            entered.set()
            assert release.wait(timeout=10)
            return "settled"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        ran: list[list[dict]] = []
        starter = threading.Thread(target=lambda: ran.append(rt.start()))
        starter.start()
        stopper = threading.Thread(target=lambda: rt.stop())
        try:
            assert entered.wait(timeout=10)
            stopper.start()
            stopper.join(timeout=0.5)
            assert stopper.is_alive()
        finally:
            release.set()
            stopper.join(timeout=10)
            starter.join(timeout=10)
        assert not starter.is_alive()
        assert not stopper.is_alive()
        assert calls == ["ping"]
        assert [job["id"] for job in ran[0]] == [job_id]
        assert ran[0][0]["last_result"] == "settled"
        assert _row(rt, job_id)["last_result"] == "settled"
        assert set(rt.bindings) == {"s"}
        assert rt.job_ids == []
        assert rt._scheduler.state == STATE_STOPPED


def test_close_clears_all_distinct_from_unbind(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        rt.bind("a", lambda prompt: "a-ok")
        rt.bind("b", lambda prompt: "b-ok")
        job_a = rt.schedule("a", "pa", interval_seconds=60, due_at=5000.0)
        job_b = rt.schedule("b", "pb", interval_seconds=60, due_at=5000.0)
        assert rt.start() == []
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.unbind("a", stop_if_empty=True) is True
        assert set(rt.bindings) == {"b"}
        assert rt._scheduler.state == STATE_RUNNING
        assert rt.job_ids == [job_a, job_b]
        rt.close()
        assert rt.bindings == {}
        assert rt.job_ids == []
        assert rt._scheduler.state == STATE_STOPPED
        assert _row(rt, job_a)["prompt"] == "pa"
        assert _row(rt, job_b)["prompt"] == "pb"


def test_drain_blocks_new_claims_during_shutdown(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            entered.set()
            assert release.wait(timeout=10)
            return "first-done"

        rt.bind("s", runner)
        first_id = rt.schedule("s", "one", interval_seconds=60, due_at=1000.0)
        second_id = rt.schedule("s", "two", interval_seconds=60, due_at=1000.0)
        first_done: list[dict | None] = []
        beat = threading.Thread(
            target=lambda: first_done.append(rt._fire_job(first_id))
        )
        beat.start()
        stopper = threading.Thread(target=rt.stop)
        try:
            assert entered.wait(timeout=10)
            stopper.start()
            stopper.join(timeout=0.5)
            assert stopper.is_alive()
            assert rt._fire_job(second_id) is None
            assert calls == ["one"]
        finally:
            release.set()
            stopper.join(timeout=10)
            beat.join(timeout=10)
        assert not beat.is_alive()
        assert not stopper.is_alive()
        assert first_done[0] is not None
        assert first_done[0]["last_result"] == "first-done"
        assert _row(rt, first_id)["last_result"] == "first-done"
        assert _row(rt, second_id)["last_result"] is None
        assert rt._scheduler.state == STATE_STOPPED


def test_later_explicit_stop_cancels_last_owner_restart(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    executed = threading.Event()
    failures = []

    with HeartbeatRuntime(tmp_path / "cron" / "jobs.json") as runtime:

        def owner(prompt):
            return "old owner"

        runtime.bind("alpha", owner)
        runtime.start()
        backend = runtime._scheduler
        shutdown = backend.shutdown
        calls = [0]

        def blocking_shutdown(**kwargs):
            calls[0] += 1
            if calls[0] == 1:
                entered.set()
                assert release.wait(timeout=10)
            shutdown(**kwargs)

        monkeypatch.setattr(backend, "shutdown", blocking_shutdown)

        def detach():
            try:
                runtime.unbind("alpha", runner=owner, stop_if_empty=True)
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=detach)
        worker.start()
        try:
            assert entered.wait(timeout=10)

            def run(prompt):
                executed.set()
                return "new owner"

            runtime.bind("beta", run)
            job_id = runtime.schedule("beta", "next work", interval_seconds=60)
            with pytest.raises(RuntimeError):
                runtime.start()
            runtime.stop()
        finally:
            release.set()
            worker.join(timeout=10)
        assert not worker.is_alive()
        assert not failures
        assert runtime._scheduler.state == STATE_STOPPED
        assert not executed.is_set()
        runtime.start()
        assert executed.is_set()
        assert _row(runtime, job_id)["last_result"] == "new owner"


@pytest.mark.parametrize(
    ("due_at", "interval"),
    [
        (float("inf"), 60),
        (1_700_000_000_000, 60),
        (1060, float("nan")),
        (1060, 10**400),
        (1060, 1e-300),
    ],
)
def test_unrepresentable_schedule_does_not_change_disk(tmp_path, due_at, interval):
    with _live(tmp_path) as (rt, _clock):
        store = JobStore(rt.path)
        store.jobs = [{"id": "foreign", "kind": "external", "payload": "preserve"}]
        store._save()
        before = rt.path.read_bytes()
        with pytest.raises(ValueError):
            rt.schedule("s", "ping", interval_seconds=interval, due_at=due_at)
        assert rt.path.read_bytes() == before


def test_poison_calendar_and_foreign_rows_do_not_block_valid_neighbor(tmp_path):
    service: list[dict] = []
    with _live(tmp_path, event_sink=service.append) as (rt, clock):
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            return "valid reply"

        rt.bind("s", runner)
        valid_id = rt.schedule("s", "valid prompt", interval_seconds=60, due_at=1060)
        store = JobStore(rt.path)
        foreign = ["foreign JSON row", {"foreign_payload": "keep"}]
        poison = dict(store.jobs[0], id="poison", due_at=1_700_000_000_000)
        rt.path.write_text(json.dumps({"jobs": [poison, *foreign, *store.jobs]}))
        foreign_before = JobStore(rt.path).jobs[1:3]
        ran = rt.start()
        assert [row["id"] for row in ran] == ["poison"]
        assert "error" in ran[0]["last_result"]
        assert rt.job_ids == [valid_id]
        assert [event["job_id"] for event in service] == ["poison"]
        clock.now = 1061
        rt._fire_job(valid_id)
        assert calls == ["valid prompt"]
        assert _row(rt, valid_id)["last_result"] == "valid reply"
        rt.clear(valid_id)
        assert JobStore(rt.path).jobs[1:] == foreign_before
        assert rt.job_ids == []


def test_owned_heartbeat_without_id_fails_explicitly(tmp_path):
    with _live(tmp_path) as (rt, _clock):
        store = JobStore(rt.path)
        store.jobs = [{"kind": "session_heartbeat", "session": "s", "prompt": "bad"}]
        store._save()
        with pytest.raises(ValueError):
            rt.start()
        assert rt.job_ids == []


def test_injected_scheduler_keeps_foreign_entries_and_refuses_dead_restart(tmp_path):
    from apscheduler.schedulers.background import BackgroundScheduler

    backend = BackgroundScheduler()
    backend.start(paused=True)
    backend.add_job(lambda: None, "interval", seconds=3600, id="foreign-service")
    with _live(tmp_path, scheduler=backend) as (rt, _clock):
        rt.bind("s", lambda prompt: "own reply")
        own = rt.schedule("s", "ping", interval_seconds=60, due_at=1060)
        rt.start()
        assert backend.get_job("foreign-service") is not None
        assert rt.job_ids == [own]
        rt.clear(own)
        assert backend.get_job("foreign-service") is not None
        rt.stop()
        with pytest.raises(RuntimeError):
            rt.start()
        assert rt.job_ids == []


@pytest.mark.parametrize("due_at", [1000, 1045])
def test_scheduler_projects_actual_calendar_deadline(tmp_path, monkeypatch, due_at):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from apscheduler.schedulers.background import BackgroundScheduler

    from omega_prime.cron import heartbeat_runtime

    wall_time = 2_000_000_000.0
    monkeypatch.setattr(
        heartbeat_runtime, "time", SimpleNamespace(time=lambda: wall_time)
    )
    backend = BackgroundScheduler()
    backend.start(paused=True)
    with _live(tmp_path, scheduler=backend) as (rt, _clock):
        rt.bind("s", lambda prompt: "delivered")
        rt.start()
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=due_at)
        job = backend.get_job(job_id)
        assert job.next_run_time == datetime.fromtimestamp(
            wall_time + due_at - 1000, tz=UTC
        )
        assert _row(rt, job_id)["last_result"] is None


def test_overdue_executor_delivery_catches_up_once(tmp_path):
    from datetime import UTC, datetime, timedelta

    from apscheduler.executors.base import run_job
    from apscheduler.schedulers.background import BackgroundScheduler

    backend = BackgroundScheduler()
    backend.start(paused=True)
    with _live(tmp_path, scheduler=backend) as (rt, clock):
        calls: list[str] = []

        def runner(prompt: str) -> str:
            calls.append(prompt)
            return "caught up"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "overdue prompt", interval_seconds=60, due_at=1060)
        rt.start()
        clock.now = 1061
        job = backend.get_job(job_id)
        run_job(job, "default", [datetime.now(UTC) - timedelta(hours=1)], __name__)
        assert calls == ["overdue prompt"]
        row = _row(rt, job_id)
        assert row["last_result"] == "caught up"
        assert row["history"] == [{"ran_at": 1061, "result": "caught up"}]
        rt._fire_job(job_id)
        assert calls == ["overdue prompt"]


# ------------------------------------------ shared jobs.json transactions ---


def test_clear_keeps_a_job_another_writer_saved_after_the_load(tmp_path, monkeypatch):
    """``clear`` must not write back its older copy of the shared job file."""
    from omega_prime.cron import heartbeat_runtime

    with _live(tmp_path) as (rt, _clock):
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=5000.0)
        original = heartbeat_runtime.clear_heartbeat
        added: list[str] = []

        def clear_after_another_write(store, target):
            # Another process (the prompt tick) saves after clear loaded.
            added.append(JobStore(rt.path).schedule("prompt", 9000.0))
            return original(store, target)

        monkeypatch.setattr(
            heartbeat_runtime, "clear_heartbeat", clear_after_another_write
        )
        assert rt.clear(job_id) == {"cleared": job_id}
        ids = [job["id"] for job in _fresh(rt)]
        assert ids == added


def test_finish_waits_for_the_store_lock_and_keeps_other_writes(tmp_path, monkeypatch):
    """A beat's settle reloads under the store lock, so a concurrent write stays."""
    from omega_prime.cron import heartbeat_runtime

    with _live(tmp_path) as (rt, _clock):
        armed = threading.Event()
        writer_done = threading.Event()
        added: list[str] = []

        def other_writer() -> None:
            added.append(JobStore(rt.path).schedule("prompt", 9000.0))
            writer_done.set()

        writer = threading.Thread(target=other_writer)
        original_find = heartbeat_runtime._find

        def find_then_race(jobs, target):
            found = original_find(jobs, target)
            if armed.is_set():
                armed.clear()
                # The settle has read the row; another writer saves now. It
                # must wait for the settle's lock rather than be overwritten.
                writer.start()
                writer_done.wait(timeout=0.5)
            return found

        def runner(prompt: str) -> str:
            armed.set()
            return "beat"

        rt.bind("s", runner)
        job_id = rt.schedule("s", "ping", interval_seconds=60, due_at=1000.0)
        monkeypatch.setattr(heartbeat_runtime, "_find", find_then_race)
        rt._fire_job(job_id)
        writer.join(timeout=10)
        assert not writer.is_alive()
        rows = {job["id"]: job for job in _fresh(rt)}
        assert rows[job_id]["last_result"] == "beat"
        assert added and added[0] in rows

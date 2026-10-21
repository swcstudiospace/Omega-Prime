"""Phase 24: JobStore jobs firing through a real APScheduler."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

from omega_prime.cron.apscheduler_backend import SchedulerService
from omega_prime.cron.scheduler import JobStore


def _wait_until(predicate, *, deadline=10.0):
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _store_for(path: Path) -> JobStore:
    return JobStore(path / "jobs.json")


def _recorder(seen: list[str], reply: str):
    class RecorderModel:
        def complete(self, messages, tools=None):
            del tools
            seen.append(messages[-1]["content"])
            return {"role": "assistant", "content": reply}

    return SimpleNamespace(child_model=RecorderModel(), tools={})


def test_one_shot_fires_once_and_persists(tmp_path: Path):
    store = _store_for(tmp_path)
    seen: list[str] = []
    job_id = store.schedule("hello", time.time() + 0.2)
    service = SchedulerService(store, _recorder(seen, "done"))
    try:
        # A busy runner can spend the 0.2s lead before start() reads the
        # clock. The job is then overdue and start() runs it; otherwise the
        # date trigger does. Both are one execution of the same one-shot.
        ran_at_start = service.start()
        assert [entry["id"] for entry in ran_at_start] in ([], [job_id])
        assert _wait_until(lambda: seen == ["hello"])
        # The store reloads and replaces its row list while settling, so fetch
        # the current row rather than retaining a stale reference.
        assert _wait_until(
            lambda: next(entry for entry in store.jobs if entry["id"] == job_id)[
                "completed"
            ]
        )
        job = next(entry for entry in store.jobs if entry["id"] == job_id)
        assert job["last_result"] == {"state": "succeeded", "summary": "done"}
        assert len(job["history"]) == 1
        jobs_file = tmp_path / "jobs.json"

        def persisted_result() -> object:
            # The store replaces the file atomically: every read parses.
            return json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"][0][
                "last_result"
            ]

        assert _wait_until(
            lambda: persisted_result() == {"state": "succeeded", "summary": "done"}
        )
    finally:
        service.stop()


def test_interval_job_fires_repeatedly(tmp_path: Path):
    store = _store_for(tmp_path)
    seen: list[str] = []
    first_due = time.time() + 0.2
    store.schedule("beat", first_due, interval_seconds=0.2)
    service = SchedulerService(store, _recorder(seen, "beat-done"))
    try:
        service.start()
        # Read the current row after each settlement; store transactions reload
        # and replace the in-memory job list.
        assert _wait_until(
            lambda: len(seen) >= 2 and len(store.jobs[0]["history"]) >= 2
        )
        job = store.jobs[0]
        assert job["completed"] is False
        assert job["due_at"] > first_due
    finally:
        service.stop()


def test_shared_due_date_runs_each_job_once(tmp_path: Path):
    store = _store_for(tmp_path)
    seen: list[str] = []
    due = time.time() + 0.2
    store.schedule("one", due)
    store.schedule("two", due)
    service = SchedulerService(store, _recorder(seen, "ok"))
    try:
        service.start()
        assert _wait_until(lambda: sorted(seen) == ["one", "two"])
        time.sleep(0.3)
        assert sorted(seen) == ["one", "two"]
        assert all(entry["completed"] for entry in store.jobs)
    finally:
        service.stop()


def test_restart_resumes_without_duplicates(tmp_path: Path):
    store = _store_for(tmp_path)
    seen: list[str] = []
    store.schedule("beat", time.time() + 0.2, interval_seconds=0.3)
    first = SchedulerService(store, _recorder(seen, "ok"))
    try:
        first.start()
        assert _wait_until(lambda: len(seen) >= 1)
    finally:
        first.stop()
    reopened = JobStore(tmp_path / "jobs.json")
    assert len(reopened.jobs[0]["history"]) >= 1
    second = SchedulerService(reopened, _recorder(seen, "ok"))
    try:
        second.start()
        count_at_restart = len(seen)
        assert _wait_until(lambda: len(seen) > count_at_restart)
        assert len(reopened.jobs[0]["history"]) == len(seen)
    finally:
        second.stop()


def test_overdue_jobs_run_on_start(tmp_path: Path):
    store = _store_for(tmp_path)
    store.schedule("late", time.time() - 5)
    service = SchedulerService(store, _recorder([], "late-done"))
    try:
        ran = service.start()
        assert [entry["prompt"] for entry in ran] == ["late"]
        assert store.jobs[0]["completed"] is True
        assert service.job_ids == []
    finally:
        service.stop()


def test_double_start_registers_once_and_stop_is_safe(tmp_path: Path):
    store = _store_for(tmp_path)
    store.schedule("hello", time.time() + 30)
    service = SchedulerService(store, _recorder([], "ok"))
    try:
        service.stop()
        service.start()
        service.start()
        assert service.job_ids == [store.jobs[0]["id"]]
    finally:
        service.stop()


def test_due_interval_job_is_registered_again_after_initial_run(tmp_path: Path):
    store = _store_for(tmp_path)
    seen: list[str] = []
    job_id = store.schedule("beat", time.time() - 0.1, interval_seconds=0.2)
    service = SchedulerService(store, _recorder(seen, "beat-done"))
    try:
        ran = service.start()

        assert [entry["id"] for entry in ran] == [job_id]
        assert service.job_ids == [job_id]
        assert _wait_until(
            lambda: len(seen) >= 2 and len(store.jobs[0]["history"]) >= 2
        )
    finally:
        service.stop()


def test_specialized_kinds_are_not_registered_with_apscheduler(tmp_path: Path):
    store = _store_for(tmp_path)
    now = time.time()
    kinds = (
        "desk_lead_pass",
        "roe_watchdog",
        "pin_bump_schedule",
        "purple_cycle",
        "session_heartbeat",
    )
    for kind in kinds:
        store.schedule(kind, now + 60)
    # Each schedule call reloads from disk. Apply kind metadata only after the
    # rows have all been created so every specialized row is actually stored.
    for job in store.jobs:
        job["kind"] = job["prompt"]
    store._save()
    service = SchedulerService(store, _recorder([], "unused"))
    try:
        service.start()
        assert service.job_ids == []
    finally:
        service.stop()


def test_missing_parent_blocks_due_job_without_model_execution(tmp_path: Path):
    store = _store_for(tmp_path)
    store.schedule("do not invoke a model", time.time() - 1)
    service = SchedulerService(store)
    try:
        ran = service.start()
        assert len(ran) == 1
        job = JobStore(store.path).jobs[0]

        assert job["last_result"] == {
            "state": "blocked",
            "error": "ValueError: delegate returned error or pending output",
        }
        assert job["history"][-1]["result"] == job["last_result"]
        assert JobStore(store.path).jobs[0]["last_result"] == job["last_result"]
        assert job["history"][-1]["disposition"] == "blocked"
    finally:
        service.stop()

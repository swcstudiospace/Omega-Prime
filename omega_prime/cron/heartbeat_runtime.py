# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Named-session heartbeat delivery over the persisted job store (LOOP-02).

A :class:`HeartbeatRuntime` owns one ``root/cron/jobs.json`` path plus its
live APScheduler entries. Beats re-enter the exact named live session through
the runner captured by :meth:`bind`; unknown names are explicit errors, never
a fresh conversation.

Lock discipline: the state lock guards bindings, the in-flight set, the
started flag, the lifecycle epoch, scheduler identity/freshness, the
callback-thread table, and the shutdown-drain depth, plus
every persisted read-modify-write cycle (fresh :class:`JobStore` reload per
boundary, so external/tool updates are immediately authoritative and no
stale snapshot can clobber a concurrent update). The lock is never held
across session-lease waiting, the bound runner callback, diagnostic sinks,
in-flight settlement, or ``scheduler.shutdown(wait=True)``, so a foreground
turn holding the lease cannot deadlock a background beat and a settling
beat (which needs the lock to finish) cannot deadlock a concurrent stop.
``stop``/``close``/last-owner ``unbind`` bump the epoch and settle
already-executing beats even when the scheduler never started, so a stale
``start`` can never restart scheduling after a later stop/close; a shut-down
owned scheduler is recreated on the next ``start`` instead of submitting to
a dead executor. While a shutdown drain is active no new beat starts, so a
wait cannot race recurring execution; shutdown from inside a callback or
sink is rejected before any lifecycle mutation.
"""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypeGuard

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import (
    STATE_STOPPED,
    SchedulerAlreadyRunningError,
    SchedulerNotRunningError,
)

from omega_prime.credentials.redact import redact_text
from omega_prime.cron.heartbeat import (
    HEARTBEAT_KIND,
    clear_heartbeat,
    schedule_heartbeat,
)
from omega_prime.cron.scheduler import HISTORY_LIMIT, JobStore


def _at(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _find(jobs: list[dict], job_id: str) -> dict | None:
    for job in jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            return job
    return None


def _valid_interval(value: Any) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 < value < math.inf
    )


def _valid_due(value: Any) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and -math.inf < value < math.inf
    )


def _calendar_fire_at(due: Any, interval: Any, now: float) -> datetime:
    """Validate persisted and live calendars before any schedule is written."""
    if not _valid_due(due):
        raise ValueError("due_at must be a finite unix timestamp in seconds")
    if not _valid_interval(interval):
        raise ValueError("interval_seconds must be finite and positive")
    try:
        _at(due)
        fire_at = _at(time.time() + max(0.0, due - now))
        # A positive float can round to a zero timedelta or fail to advance
        # the persisted clock. Reject it rather than letting APScheduler
        # silently substitute a one-second interval and repeatedly re-arm.
        delta = timedelta(seconds=interval)
        next_due = now + interval
        if delta.total_seconds() <= 0 or next_due <= now:
            raise ValueError("heartbeat interval cannot advance scheduler clock")
        fire_at + delta
        _at(next_due)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError(
            "heartbeat deadline or interval exceeds scheduler calendar"
        ) from exc
    return fire_at


def _job_fire_at(job: dict, now: float) -> datetime | None:
    """Only valid heartbeat rows receive live entries; malformed rows disarm."""
    session = job.get("session")
    if (
        not isinstance(session, str)
        or not session.strip()
        or not isinstance(job.get("prompt"), str)
    ):
        return None
    try:
        return _calendar_fire_at(job.get("due_at"), job.get("interval_seconds"), now)
    except ValueError:
        return None


class HeartbeatRuntime:
    """Drive persisted heartbeat jobs into named bound sessions.

    ``runner(prompt)`` returns the actual run result/response. Each binding
    also captures an optional ``event_sink(event)`` that receives that
    session's ``prime_degraded`` events. The constructor ``event_sink`` is the
    service sink for undirected diagnostics only (beats with no binding).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        event_sink: Callable[[dict], Any] | None = None,
        clock: Callable[[], float] | None = None,
        scheduler: BackgroundScheduler | None = None,
    ) -> None:
        self._path = Path(path).expanduser().resolve()
        self._service_sink = event_sink
        self._clock = clock or time.time
        self._owns_scheduler = scheduler is None
        self._scheduler = scheduler or BackgroundScheduler()
        self._lock = threading.Lock()
        # Settlement signal for stop(wait=True)/close/last-owner unbind.
        # Waiting releases the state lock, so a settling beat can still
        # finish (it needs the lock) and wake the waiter. Never waited on
        # from inside a callback or sink: that would be a self-join and is
        # rejected explicitly before any lifecycle mutation.
        self._settled = threading.Condition(self._lock)
        self._bindings: dict[str, tuple[Any, Any]] = {}
        self._in_flight: set[str] = set()
        self._entry_ids: set[str] = set()
        self._started = False
        # Lifecycle epoch: bumped by every stop/close/last-owner shutdown.
        # A start that observes a newer epoch before (re)starting the
        # scheduler aborts instead, so an older start never restarts
        # scheduling after a later stop/close.
        self._epoch = 0
        # An owned scheduler whose pool was shut down cannot deliver again
        # (APScheduler does not revive its executor on restart), so the next
        # start replaces it with a fresh instance.
        self._fresh_pool = True
        # Threads currently executing a bound runner or diagnostic sink,
        # by ident with nesting depth. stop/close/last-owner unbind from
        # such a thread would wait on/join itself, so it is rejected with
        # RuntimeError before any binding/epoch/scheduler mutation.
        self._callback_counts: dict[int, int] = {}
        # Active shutdown drains (stop/close/last-owner unbind, including
        # the empty-service retry path). While >0, _fire_job starts no new
        # beat so a wait cannot race recurring scheduler execution;
        # already-executing beats still settle to completion.
        self._drain_depth = 0
        # Only the latest conditional last-owner shutdown may resume a new
        # binding after every concurrent drain settles. A later explicit
        # stop/close advances the epoch and cancels that implicit restart.
        self._resume_epoch: int | None = None

    @property
    def path(self) -> Path:
        """Resolved persisted job file this runtime owns."""
        return self._path

    @property
    def bindings(self) -> dict[str, dict[str, Any]]:
        """Snapshot of named bindings for glue introspection."""
        with self._lock:
            return {
                name: {"runner": runner, "event_sink": sink}
                for name, (runner, sink) in self._bindings.items()
            }

    @property
    def job_ids(self) -> list[str]:
        """Registered entries owned by this runtime, not foreign injected jobs."""
        with self._lock:
            if self._scheduler.state == STATE_STOPPED:
                return []
            return [
                job.id
                for job in self._scheduler.get_jobs()
                if job.id in self._entry_ids
            ]

    def bind(
        self,
        name: str,
        runner: Callable[[str], Any],
        *,
        event_sink: Callable[[dict], Any] | None = None,
    ) -> None:
        """Attach a live ``runner`` (plus its sink) to a named session."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("heartbeat binding name must be a non-empty string")
        if not callable(runner):
            raise ValueError("heartbeat binding runner must be callable")
        with self._lock:
            self._bindings[name] = (runner, event_sink)

    def unbind(
        self, name: str, *, runner: Any = None, stop_if_empty: bool = False
    ) -> bool:
        """Detach a named binding; conditional on the expected runner.

        With ``runner=None`` the detachment is unconditional. Otherwise the
        stored runner must be ``runner``, so closing a replaced agent never
        detaches its replacement. A mismatch detaches nothing and returns
        ``False``; other names are never touched.

        With ``stop_if_empty=False`` (default) this is a pure detach and
        never touches scheduling. With ``stop_if_empty=True`` a successful
        detach that empties the last binding also stops owned scheduling:
        the epoch is bumped (superseding stale starts), in-flight beats
        settle even when the scheduler never started, the scheduler is
        shut down outside the state lock, and a binding installed
        concurrently with that shutdown restarts scheduling instead of
        being stranded. A shutdown failure propagates (the detach stays
        detached) so it is observable and the caller can retry cleanup via
        ``stop``/``close`` or the empty-service ``unbind`` retry path below;
        the retry never resurrects the detached binding and never detaches
        a replacement. Calling the last-owner shutdown from inside its own
        callback or sink raises ``RuntimeError`` before any mutation.
        """
        with self._lock:
            pair = self._bindings.get(name)
            if pair is None:
                if not stop_if_empty or self._bindings:
                    return False
                if threading.get_ident() in self._callback_counts:
                    raise RuntimeError(
                        "heartbeat last-owner unbind from inside its own "
                        "callback or sink would deadlock"
                    )
                scheduler = self._scheduler
                need_shutdown = scheduler.state != STATE_STOPPED
                if not need_shutdown and not self._in_flight:
                    return False
                self._epoch += 1
                self._started = False
                self._drain_depth += 1
                is_retry = True
            else:
                if runner is not None and pair[0] is not runner:
                    return False
                if not stop_if_empty or len(self._bindings) > 1:
                    del self._bindings[name]
                    return True
                if threading.get_ident() in self._callback_counts:
                    raise RuntimeError(
                        "heartbeat last-owner unbind from inside its own "
                        "callback or sink would deadlock"
                    )
                del self._bindings[name]
                self._epoch += 1
                self._started = False
                scheduler = self._scheduler
                need_shutdown = scheduler.state != STATE_STOPPED
                self._drain_depth += 1
                is_retry = False
            self._resume_epoch = self._epoch
        try:
            self._wait_for_in_flight()
            if need_shutdown:
                self._shutdown_instance(scheduler, wait=True)
        finally:
            with self._lock:
                if (
                    need_shutdown
                    and self._scheduler is scheduler
                    and scheduler.state == STATE_STOPPED
                ):
                    self._fresh_pool = False
            self._finish_drain()
        return not is_retry

    def schedule(
        self,
        session: str,
        prompt: str,
        *,
        interval_seconds: int | float,
        due_at: int | float | None = None,
    ) -> str:
        """Persist a heartbeat and sync its live entry. Returns the job id."""
        now = self._clock()
        due = now if due_at is None else due_at
        _calendar_fire_at(due, interval_seconds, now)
        if not isinstance(prompt, str):
            raise ValueError("heartbeat prompt must be a string")
        with self._lock:
            store = JobStore(self._path)
            job_id = schedule_heartbeat(
                store,
                session,
                prompt,
                interval_seconds=interval_seconds,
                due_at=due,
            )
            self._sync_entry_locked(store, job_id)
        return job_id

    def list_jobs(self) -> list[dict]:
        """All heartbeat jobs (complete or not) from a fresh disk read."""
        with self._lock:
            store = JobStore(self._path)
            return copy.deepcopy(
                [
                    job
                    for job in store.jobs
                    if isinstance(job, dict) and job.get("kind") == HEARTBEAT_KIND
                ]
            )

    @contextmanager
    def _locked_store(self) -> Iterator[JobStore]:
        """The job file, reloaded under the store's cross-process lock.

        Every heartbeat read-modify-save runs inside this, the same flock the
        scheduler's claim/settle path takes, so a heartbeat save can never
        write back a stale copy over a prompt job another tick just settled.
        Never nest it: the lock is not re-entrant across file descriptors.
        """
        store = JobStore(self._path)
        with store._store_transaction():
            yield store

    def clear(self, job_id: str) -> dict:
        """Remove one heartbeat; unknown ids (or foreign rows) are errors."""
        with self._lock:
            store = JobStore(self._path)
            # clear_heartbeat removes the row in the store's own locked
            # reload-and-save transaction, so it never writes a stale copy.
            result = clear_heartbeat(store, job_id)
            self._sync_entry_locked(store, job_id)
        return result

    def start(self) -> list[dict]:
        """Reconcile live entries, run overdue beats once, start firing.

        The lifecycle epoch is captured on entry: overdue beats already
        executing still settle, but if a stop/close/last-owner shutdown
        lands mid-start the remaining overdue beats are skipped and the
        scheduler is not (re)started, so an older start never restarts
        scheduling after a later stop/close. A start that arrives while a
        shutdown drain is active raises ``RuntimeError`` without starting;
        it never reports a successful empty start for an unavailable service.
        """
        now = self._clock()
        with self._lock:
            if self._drain_depth > 0:
                raise RuntimeError("heartbeat shutdown is in progress")
            epoch = self._epoch
            self._prepare_scheduler_locked()
            scheduler = self._scheduler
            store = JobStore(self._path)
            if any(
                isinstance(job, dict)
                and job.get("kind") == HEARTBEAT_KIND
                and not job.get("completed")
                and not isinstance(job.get("id"), str)
                for job in store.jobs
            ):
                raise ValueError("persisted heartbeat job is missing a string id")
            wanted = {
                job["id"]
                for job in store.jobs
                if isinstance(job, dict)
                and job.get("kind") == HEARTBEAT_KIND
                and not job.get("completed")
                and isinstance(job.get("id"), str)
            }
            for entry in scheduler.get_jobs():
                if entry.id in self._entry_ids and entry.id not in wanted:
                    with suppress(JobLookupError):
                        scheduler.remove_job(entry.id)
                    self._entry_ids.discard(entry.id)
            pending: list[tuple[Any, str]] = []
            for job in store.jobs:
                if not isinstance(job, dict) or job.get("id") not in wanted:
                    continue
                due = job.get("due_at")
                if _job_fire_at(job, now) is not None and _valid_due(due) and due > now:
                    continue
                pending.append((min(due, now) if _valid_due(due) else now, job["id"]))
            pending.sort(key=lambda item: item[0])
            overdue = [job_id for _, job_id in pending]
        ran: list[dict] = []
        for job_id in overdue:
            with self._lock:
                if self._epoch != epoch or self._drain_depth > 0:
                    break
            finished = self._fire_job(job_id)
            if finished is not None:
                ran.append(finished)
        with self._lock:
            if (
                self._epoch != epoch
                or self._scheduler is not scheduler
                or self._drain_depth > 0
            ):
                return ran
            with suppress(SchedulerAlreadyRunningError):
                scheduler.start()
            self._started = True
            store = JobStore(self._path)
            for job in store.jobs:
                if isinstance(job, dict) and isinstance(job.get("id"), str):
                    self._sync_entry_locked(store, job["id"])
        return ran

    def stop(self, *, wait: bool = True) -> None:
        """Shut the scheduler down. Safe before ``start()``, idempotent.

        Always bumps the lifecycle epoch (superseding an in-flight
        ``start``) and, with ``wait=True``, settles already-executing
        beats first — even when the scheduler never started, since
        overdue beats may be running on the caller thread. While the drain
        is active no new beat starts, so the wait cannot race recurring
        execution. The wait releases the state lock, so settling beats can
        finish; the scheduler shutdown itself also runs outside the lock
        because a settling beat needs the lock to record its outcome.
        Shutdown failures other than "not running" propagate and leave
        retryable state behind. Calling from inside a callback or sink
        raises ``RuntimeError`` before any mutation instead of deadlocking.
        """
        with self._lock:
            if threading.get_ident() in self._callback_counts:
                raise RuntimeError(
                    "heartbeat stop from inside its own callback or sink would deadlock"
                )
            self._epoch += 1
            self._started = False
            scheduler = self._scheduler
            need_shutdown = scheduler.state != STATE_STOPPED
            need_drain = wait or need_shutdown
            if need_drain:
                self._drain_depth += 1
        if not need_drain:
            return
        try:
            if wait:
                self._wait_for_in_flight()
            if need_shutdown:
                self._shutdown_instance(scheduler, wait=wait)
                with self._lock:
                    if self._scheduler is scheduler:
                        self._fresh_pool = False
        finally:
            self._finish_drain()

    def close(self) -> None:
        """Stop owned scheduling and drop every binding. Idempotent.

        This is the explicit whole-service shutdown: unlike the
        conditional last-owner stop in :meth:`unbind`, it always settles
        in-flight beats, stops the scheduler, and clears all bindings.
        Scheduling is marked stopped before the wait so no new beat starts
        while settling. Bindings are dropped only after scheduling stops,
        so a shutdown failure propagates with cleanup still retryable
        instead of being masked as success. Calling from inside a callback
        or sink raises ``RuntimeError`` before any mutation.
        """
        with self._lock:
            if threading.get_ident() in self._callback_counts:
                raise RuntimeError(
                    "heartbeat close from inside its own callback or sink "
                    "would deadlock"
                )
            self._epoch += 1
            self._started = False
            scheduler = self._scheduler
            need_shutdown = scheduler.state != STATE_STOPPED
            self._drain_depth += 1
        try:
            self._wait_for_in_flight()
            if need_shutdown:
                self._shutdown_instance(scheduler, wait=True)
                with self._lock:
                    if self._scheduler is scheduler:
                        self._fresh_pool = False
            with self._lock:
                self._bindings.clear()
                self._started = False
        finally:
            self._finish_drain()

    def _finish_drain(self) -> None:
        """Resume a replacement only after all drains settle, unless superseded."""
        with self._lock:
            self._drain_depth -= 1
            if (
                self._drain_depth == 0
                and self._resume_epoch == self._epoch
                and self._bindings
                and not self._started
            ):
                self._restart_locked()

    def __enter__(self) -> HeartbeatRuntime:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        self.close()
        return False

    def _fire_job(self, job_id: str) -> dict | None:
        """Run one beat through claim, unlocked callback, fresh finish."""
        with self._lock:
            if self._drain_depth > 0:
                return None
            if job_id in self._in_flight:
                return None
            self._in_flight.add(job_id)
        try:
            return self._run_claimed(job_id)
        finally:
            with self._lock:
                self._in_flight.discard(job_id)
                self._settled.notify_all()

    def _wait_for_in_flight(self) -> None:
        """Block until every executing beat settles, lock released while waiting.

        The state lock is released during the wait so a settling beat can
        acquire it to record its outcome and wake this waiter. Calling from
        inside a callback or sink raises ``RuntimeError`` instead of
        self-joining; shutdown entry points check before mutating so this
        is only a defensive second gate.
        """
        with self._lock:
            if threading.get_ident() in self._callback_counts:
                raise RuntimeError(
                    "heartbeat shutdown wait from inside its own callback "
                    "or sink would deadlock"
                )
            while self._in_flight:
                self._settled.wait()

    def _enter_callback(self) -> None:
        ident = threading.get_ident()
        with self._lock:
            self._callback_counts[ident] = self._callback_counts.get(ident, 0) + 1

    def _exit_callback(self) -> None:
        ident = threading.get_ident()
        with self._lock:
            depth = self._callback_counts.get(ident, 0)
            if depth <= 1:
                self._callback_counts.pop(ident, None)
            else:
                self._callback_counts[ident] = depth - 1

    @staticmethod
    def _shutdown_instance(scheduler: BackgroundScheduler, *, wait: bool) -> None:
        """Shut one scheduler instance down; "not running" is a clean no-op.

        Any other failure propagates so a failed close/stop stays
        observable and retryable instead of reading as success.
        """
        with suppress(SchedulerNotRunningError):
            scheduler.shutdown(wait=wait)

    def _prepare_scheduler_locked(self) -> None:
        """A dead injected executor is an explicit error, never a fake restart."""
        if self._fresh_pool:
            return
        if not self._owns_scheduler:
            raise RuntimeError(
                "injected heartbeat scheduler was shut down; supply a fresh runtime"
            )
        self._scheduler = BackgroundScheduler()
        self._entry_ids.clear()
        self._fresh_pool = True

    def _restart_locked(self) -> None:
        """Restart live scheduling when a shutdown met a new binding.

        The state lock is held and only non-blocking scheduler operations
        run here: replace a shut-down owned scheduler, (re)start it, and
        re-project every persisted heartbeat entry.
        """
        self._prepare_scheduler_locked()
        with suppress(SchedulerAlreadyRunningError):
            self._scheduler.start()
        self._started = True
        store = JobStore(self._path)
        for job in store.jobs:
            if isinstance(job, dict) and isinstance(job.get("id"), str):
                self._sync_entry_locked(store, job["id"])

    def _run_claimed(self, job_id: str) -> dict | None:
        now = self._clock()
        runner: Any = None
        sink: Any = None
        deferred: tuple[Any, dict[str, Any]] | None = None
        finished: dict | None = None
        with self._lock:
            if self._drain_depth > 0:
                return None
            with self._locked_store() as store:
                job = _find(store.jobs, job_id)
                if (
                    job is None
                    or job.get("kind") != HEARTBEAT_KIND
                    or job.get("completed")
                ):
                    return None
                session = job.get("session")
                prompt = job.get("prompt")
                interval = job.get("interval_seconds")
                claim_due = job.get("due_at")
                history = job.get("history")
                if isinstance(history, list) and any(
                    isinstance(entry, dict) and entry.get("ran_at") == now
                    for entry in history
                ):
                    return None
                if _job_fire_at(job, now) is None:
                    outcome = {
                        "error": "heartbeat job has invalid session, prompt, "
                        "interval_seconds, due_at, or scheduler calendar"
                    }
                    finished = self._finish_locked(store, job, now, None, outcome)
                    self._sync_entry_locked(store, job_id)
                    # A diagnostic sink may re-enter this runtime (list/clear),
                    # so capture the event under the lock and emit after release.
                    deferred = (
                        self._service_sink,
                        {
                            "type": "prime_degraded",
                            "family": "heartbeat",
                            "error": outcome["error"],
                            "job_id": job_id,
                        },
                    )
                else:
                    assert isinstance(session, str)
                    if job["due_at"] > now:
                        return None
                    history = job.get("history")
                    if not isinstance(history, list):
                        history = []
                        job["history"] = history
                    if job.get("claimed_at") == now or any(
                        isinstance(entry, dict) and entry.get("ran_at") == now
                        for entry in history
                    ):
                        return None
                    job["claimed_at"] = now
                    store._save()
                    pair = self._bindings.get(session)
                    if pair is None:
                        outcome = {"error": f"unknown heartbeat session: {session!r}"}
                        finished = self._finish_locked(
                            store, job, now, interval, outcome
                        )
                        self._sync_entry_locked(store, job_id)
                        deferred = (
                            self._service_sink,
                            {
                                "type": "prime_degraded",
                                "family": "heartbeat",
                                "error": outcome["error"],
                                "session": session,
                                "job_id": job_id,
                            },
                        )
                    else:
                        runner, sink = pair
        if deferred is not None:
            emit_sink, event = deferred
            self._emit_guarded(emit_sink, event)
            return finished
        self._enter_callback()
        try:
            try:
                raw = runner(prompt)
            except Exception as exc:
                text = redact_text(f"{type(exc).__name__}: {exc}")
                outcome = {"error": text}
                self._emit_guarded(
                    sink,
                    {
                        "type": "prime_degraded",
                        "family": "heartbeat",
                        "error": text,
                        "session": session,
                        "job_id": job_id,
                    },
                )
                return self._finish(job_id, now, outcome, interval, claim_due)
            except BaseException:
                self._release_claim(job_id)
                raise
            try:
                json.dumps(raw, ensure_ascii=False)
            except Exception:
                error = "heartbeat result is not JSON-serializable"
                outcome = {"error": error}
                self._emit_guarded(
                    sink,
                    {
                        "type": "prime_degraded",
                        "family": "heartbeat",
                        "error": error,
                        "session": session,
                        "job_id": job_id,
                    },
                )
                return self._finish(job_id, now, outcome, interval, claim_due)
            return self._finish(job_id, now, raw, interval, claim_due)
        finally:
            self._exit_callback()

    def _finish(
        self,
        job_id: str,
        now: float,
        outcome: Any,
        claim_interval: Any,
        claim_due: Any,
    ) -> dict | None:
        """Reconcile one beat outcome into freshly read state.

        A job cleared (or rewritten foreign/completed) mid-callback is gone
        on reload, so the outcome is dropped instead of resurrecting the
        row. A claim superseded mid-callback (``claimed_at`` no longer this
        beat's) is also dropped: another writer owns the row. When only the
        schedule moved (due/interval), this run is still recorded but the
        current due/interval values stand, so finish never overwrites an
        external update.
        """
        with self._lock, self._locked_store() as store:
            job = _find(store.jobs, job_id)
            if (
                job is None
                or job.get("kind") != HEARTBEAT_KIND
                or job.get("completed")
                or job.get("claimed_at") != now
            ):
                return None
            if (
                job.get("interval_seconds") != claim_interval
                or job.get("due_at") != claim_due
            ):
                finished = self._finish_locked(store, job, now, None, outcome)
            else:
                finished = self._finish_locked(store, job, now, claim_interval, outcome)
            self._sync_entry_locked(store, job_id)
        return finished

    def _finish_locked(
        self,
        store: JobStore,
        job: dict,
        now: float,
        interval: Any,
        outcome: Any,
    ) -> dict:
        job["last_result"] = outcome
        job["last_ran_at"] = now
        history = job.get("history")
        if not isinstance(history, list):
            history = []
            job["history"] = history
        history.append({"ran_at": now, "result": outcome})
        del history[:-HISTORY_LIMIT]
        job["claimed_at"] = None
        if _valid_interval(interval):
            job["due_at"] = now + interval
        store._save()
        return copy.deepcopy(job)

    def _release_claim(self, job_id: str) -> None:
        """Clear a claim after a ``BaseException`` so future beats can fire."""
        with self._lock, self._locked_store() as store:
            job = _find(store.jobs, job_id)
            if job is None or job.get("kind") != HEARTBEAT_KIND:
                return
            job["claimed_at"] = None
            store._save()

    def _sync_entry_locked(self, store: JobStore, job_id: str) -> None:
        """Reconcile one live entry from the given current store.

        The state lock is held: scheduler add/remove runs here (never a
        model callback or an external sink), so no writer can slip a
        clear or reschedule between the persisted row read and its live
        projection. A missing, foreign, completed, or due-less row ends
        with no live entry; otherwise exactly one fresh entry mirrors the
        row's current due/interval.
        """
        if not self._started:
            return
        job = _find(store.jobs, job_id)
        if job_id in self._entry_ids:
            with suppress(JobLookupError):
                self._scheduler.remove_job(job_id)
            self._entry_ids.discard(job_id)
        if job is None or job.get("kind") != HEARTBEAT_KIND or job.get("completed"):
            return
        fire_at = _job_fire_at(job, self._clock())
        if fire_at is None:
            return
        # Only interval heartbeats are valid. No date fallback can re-arm an
        # invalid row at its unchanged, already-overdue persisted deadline.
        self._scheduler.add_job(
            self._fire_job,
            "interval",
            seconds=job["interval_seconds"],
            start_date=fire_at,
            next_run_time=fire_at,
            id=job_id,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
            args=[job_id],
        )
        self._entry_ids.add(job_id)

    def _emit_guarded(
        self, sink: Callable[[dict], Any] | None, event: dict[str, Any]
    ) -> None:
        if sink is None:
            return
        self._enter_callback()
        try:
            with suppress(Exception):
                sink(dict(event))
        finally:
            self._exit_callback()


__all__ = ["HeartbeatRuntime"]

"""APScheduler backend: JobStore jobs as real scheduled entries.

One-shots become date triggers, intervals become interval triggers, and each
fire runs `store.tick` — so claim, bounded history, completion, and resume
are exactly the `JobStore` semantics. The JSON store is the source of truth;
the scheduler keeps entries in memory only. `start()` first ticks once for
overdue jobs, then registers future entries keyed by job id.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from omega_prime.cron.scheduler import (
    DESK_LEAD_KIND,
    PIN_BUMP_SCHEDULE_KIND,
    JobStore,
)


class SchedulerService:
    """Drive one `JobStore` with a background APScheduler."""

    def __init__(
        self,
        store: JobStore,
        runner: Callable[[str], str],
        *,
        scheduler: BackgroundScheduler | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.scheduler = scheduler or BackgroundScheduler()
        self._clock = clock or time.time
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> list[dict]:
        """Tick overdue jobs now, register future entries, start firing."""
        now = self._clock()
        # Desk lead passes are another driver's jobs (DeskDriver); ticking
        # them here would burn a model call per pass. Pin-bump schedules
        # are owned by tick_pin_bump_schedules for the same reason.
        ran = self.store.tick(
            now, self.runner, skip_kinds=(DESK_LEAD_KIND, PIN_BUMP_SCHEDULE_KIND)
        )
        for job in self.store.jobs:
            if job.get("completed"):
                continue
            if job["due_at"] <= now:
                continue
            if self.scheduler.get_job(job["id"]) is not None:
                continue
            if job.get("interval_seconds") is not None:
                interval = job["interval_seconds"]
                self.scheduler.add_job(
                    self._fire,
                    "interval",
                    seconds=interval,
                    start_date=_at(job["due_at"]),
                    id=job["id"],
                    max_instances=1,
                    coalesce=True,
                )
            else:
                self.scheduler.add_job(
                    self._fire, "date", run_date=_at(job["due_at"]), id=job["id"]
                )
        if not self._started:
            self.scheduler.start()
            self._started = True
        return ran

    def stop(self, *, wait: bool = True) -> None:
        """Shut the scheduler down. Safe before `start()`."""
        if not self._started:
            return
        self.scheduler.shutdown(wait=wait)
        self._started = False

    @property
    def job_ids(self) -> list[str]:
        """Registered entry ids (one per scheduled JobStore job)."""
        return [job.id for job in self.scheduler.get_jobs()]

    def _fire(self) -> None:
        # `tick` is single-threaded (claim by exact `now`); concurrent fires
        # must not interleave inside it.
        with self._lock:
            self.store.tick(
                self._clock(),
                self.runner,
                skip_kinds=(DESK_LEAD_KIND, PIN_BUMP_SCHEDULE_KIND),
            )


def _at(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


__all__ = ["SchedulerService"]

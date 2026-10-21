"""APScheduler backend: JobStore jobs as real scheduled entries.

One-shots become date triggers, intervals become interval triggers, and each
fire dispatches generic prompt jobs through the supplied delegate parent.
Specialized schedule kinds belong to their designated owners and are never
registered as generic APScheduler callbacks. The JSON store is the source of
truth; `start()` first ticks overdue generic jobs, then registers future ones.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from omega_prime.cron.scheduler import GENERIC_PROMPT_KIND, JobStore, run_due_jobs


class SchedulerService:
    """Drive one `JobStore` with a background APScheduler."""

    def __init__(
        self,
        store: JobStore,
        parent: Any = None,
        *,
        scheduler: BackgroundScheduler | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.parent = parent
        self.scheduler = scheduler or BackgroundScheduler()
        self._clock = clock or time.time
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> list[dict]:
        """Tick overdue jobs now, register future entries, start firing."""
        now = self._clock()
        # DeskDriver owns all specialized cron kinds; heartbeat has its own
        # runtime. APScheduler handles only generic prompt jobs.
        ran = run_due_jobs(self.store, now, self.parent)
        for job in self.store.jobs:
            if job.get("kind") not in (None, GENERIC_PROMPT_KIND) or job.get(
                "completed"
            ):
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
        with self._lock:
            run_due_jobs(self.store, self._clock(), self.parent)


def _at(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


__all__ = ["SchedulerService"]

"""Production cron driver for the scheduler kinds owned by DeskDriver.

Phase 64 DESK-04. The desk pass is a cron job kind
(``omega_prime.cron.scheduler.DESK_LEAD_KIND``) riding the same
:class:`~omega_prime.cron.scheduler.JobStore` file everything else uses; this
driver is the process-side owner that ticks those jobs without a manual call.
It follows the :class:`~omega_prime.cron.apscheduler_backend.SchedulerService`
shape — one background thread plus a lock, fire-serialized — and the heartbeat
store-path convention (``root/cron/jobs.json``, the file
``omega_prime.tools.heartbeat`` and :class:`HeartbeatRuntime` resolve). A fresh
``JobStore`` is built per tick so a store written by another process (or by
``SchedulerService``'s model-driven tick, which skips desk jobs) is always
reloaded, never clobbered.

Serialization: one worker thread runs one pass at a time, and the disk claim
in :func:`~omega_prime.cron.scheduler.tick_desk_lead_passes` makes a
concurrent tick (another thread or another process) a no-op. A ``parent`` of
``None`` never crashes the server: every ticket comes back explicitly blocked
with a failure receipt, never a fabricated success.
"""

from __future__ import annotations

import atexit
import math
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.cron.scheduler import (
    JobStore,
    run_desk_lead_passes,
    run_due_jobs,
    tick_pin_bump_schedules,
    tick_purple_cycles,
    tick_roe_watchdog,
)

DEFAULT_INTERVAL_SECONDS = 60.0
"""Seconds between desk-pass ticks. Passes themselves run only when due."""


class DeskDriver:
    """Run due desk lead passes in the background, one at a time.

    ``root`` locates the shared job file (heartbeat convention:
    ``root/cron/jobs.json``); ``parent`` is the runtime's in-process agent
    shim and ``work_root`` the directory the desk works on. ``start()`` first
    ticks once — a restarted server resumes overdue passes immediately — then
    keeps the cadence on a daemon thread. ``stop()`` ends the thread and is
    safe before ``start()`` and from the worker thread itself.
    """

    def __init__(
        self,
        root: str | Path,
        parent: Any = None,
        work_root: str | Path | None = None,
        *,
        interval_seconds: int | float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] | None = None,
        approval_log: Any = None,
        can_apply_pin_bump: Callable[[], bool] | None = None,
        intake_path: str | Path | None = None,
    ) -> None:
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be a positive finite number")
        self.path = Path(root, "cron", "jobs.json").expanduser().resolve()
        self.parent = parent
        self.work_root = work_root
        self.approval_log = approval_log
        self.can_apply_pin_bump = can_apply_pin_bump
        # The intake file the host's lead tools opened, so a scheduled pass
        # reads the same queue even when the agent folder comes from
        # settings.json rather than the process env.
        self.intake_path = intake_path
        self.interval_seconds = interval_seconds
        self._clock = clock or time.time
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """Start ticking. Idempotent while the worker is alive."""
        with self._lock:
            if self._running:
                return
            self._stop.clear()
            self._running = True
            thread = threading.Thread(
                target=self._loop, name="desk-pass-driver", daemon=True
            )
            self._thread = thread
        # Overdue passes resume right away, then the cadence continues on the
        # thread. The tick owns no registry state, so this is safe mid-build.
        self.tick_once()
        thread.start()
        atexit.register(self._atexit_stop)

    def stop(self, *, wait: bool = True) -> None:
        """Stop the background thread. Safe before ``start()``."""
        with self._lock:
            self._running = False
            thread = self._thread
        self._stop.set()
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.tick_once()

    def tick_once(self) -> None:
        """One serialized tick over the shared job file.

        Reloads the store from disk, then evaluates the desk, generic prompt,
        watchdog, pin-bump, and purple-cycle kinds. Each kind is isolated so
        one failure does not prevent the remaining owners from being evaluated.
        """
        ticks = (
            (
                "desk-pass",
                lambda store: run_desk_lead_passes(
                    store,
                    self._clock(),
                    self.parent,
                    self.work_root,
                    intake_path=self.intake_path,
                ),
            ),
            (
                "prompt-cron",
                lambda store: run_due_jobs(store, self._clock(), self.parent),
            ),
            ("roe-watchdog", lambda store: tick_roe_watchdog(store, self._clock())),
            (
                "pin-bump",
                lambda store: tick_pin_bump_schedules(
                    store,
                    self._clock(),
                    root=self.path.parent.parent,
                    approval_log=self.approval_log,
                    can_apply_pin_bump=self.can_apply_pin_bump,
                ),
            ),
            (
                "purple-cycle",
                lambda store: tick_purple_cycles(
                    store, self._clock(), root=self.path.parent.parent
                ),
            ),
        )
        with self._tick_lock:
            store: JobStore | None = None
            for kind, tick in ticks:
                try:
                    if store is None:
                        store = JobStore(self.path)
                    tick(store)
                except Exception as exc:
                    print(
                        f"desk-pass driver: {kind} tick failed: {type(exc).__name__}",
                        file=sys.stderr,
                    )

    def _atexit_stop(self) -> None:
        # Interpreter shutdown must not wait on an in-flight model call; the
        # disk claim makes the pass resumable by the next process.
        self.stop(wait=False)


__all__ = ["DEFAULT_INTERVAL_SECONDS", "DeskDriver"]

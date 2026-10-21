"""Due-job tick. Jobs live in a JSON file.

Adapted from Hermes ``cron/scheduler_tick.py``. ``tick`` runs each job whose
``due_at`` is at or before ``now``. A one-shot job completes. An interval job
is due again at ``now + interval_seconds``.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _process_start(pid: int) -> str | None:
    """Return a start identity for ``pid`` that changes when the pid is reused.

    Linux reports the process start time in clock ticks since boot (field 22
    of ``/proc/<pid>/stat``); combined with the boot id it tells a restarted
    worker that happens to get the same pid (common in containers) apart
    from the worker that saved the claim. Other platforms return ``None``.
    """
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = stat_text[stat_text.rindex(")") + 2 :].split()
        start = fields[19]
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (OSError, ValueError, IndexError):
        return None
    return f"{boot.replace(':', '')}-{start}" if start.isdigit() else None


def _new_claim_token() -> str:
    pid = os.getpid()
    start = _process_start(pid) or "-"
    return f"{pid}:{start}:{threading.get_ident()}:{uuid.uuid4().hex}"


def _claim_is_stale(token: Any) -> bool:
    """Return whether a persisted claim belongs to an exited worker."""
    if not isinstance(token, str):
        return token is not None
    parts = token.split(":", 3)
    try:
        if len(parts) == 4:
            pid_text, start, worker_text, _nonce = parts
        elif len(parts) == 3:
            # Tokens saved before the start identity was recorded.
            pid_text, worker_text, _nonce = parts
            start = "-"
        else:
            return True
        pid = int(pid_text)
        worker_id = int(worker_text)
    except (TypeError, ValueError):
        return True
    if pid <= 0 or worker_id <= 0:
        return True
    if start != "-":
        current = _process_start(pid)
        if current is not None and current != start:
            # The pid now belongs to a different process: the worker that
            # saved this claim exited (for example across a restart).
            return True
    if pid == os.getpid():
        if start == "-" and _process_start(pid) is not None:
            # A legacy token naming our own pid cannot have been saved by
            # this process, which always records its start identity.
            return True
        return worker_id not in {
            thread.ident
            for thread in threading.enumerate()
            if thread.is_alive() and thread.ident is not None
        }
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


HISTORY_LIMIT = 20

# Only untyped legacy prompt rows and explicitly typed prompt rows are generic.
GENERIC_PROMPT_KIND = "prompt"
SPECIALIZED_KINDS = frozenset(
    {
        "desk_lead_pass",
        "roe_watchdog",
        "pin_bump_schedule",
        "purple_cycle",
        "session_heartbeat",
    }
)


PIN_BUMP_APPLY_APPROVAL = "pin_bump_apply"
CRON_LATENESS_GRACE_ENV = "OMEGA_PRIME_CRON_LATENESS_GRACE_SECONDS"
DEFAULT_CRON_LATENESS_GRACE_SECONDS = 300.0


def cron_lateness_grace_seconds() -> float:
    """Read the configured positive finite action lateness grace."""
    raw = os.environ.get(CRON_LATENESS_GRACE_ENV)
    try:
        value = DEFAULT_CRON_LATENESS_GRACE_SECONDS if raw is None else float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{CRON_LATENESS_GRACE_ENV} must be positive and finite"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{CRON_LATENESS_GRACE_ENV} must be positive and finite")
    return value


def _monotonic() -> float:
    """Monotonic clock seam for :class:`_TickClock` (patched in tests)."""
    return time.monotonic()


class _TickClock:
    """Fresh per-job clock readings for one tick.

    A tick selects due jobs at the ``now`` it was given, then runs them one
    after another. A slow earlier job (an audit, a model call) moves the real
    clock on, so a later queued job must judge its lateness, and stamp its
    run, against a fresh reading rather than the tick-start value. ``read``
    returns ``now`` plus the whole seconds elapsed on the monotonic clock
    since the tick began: the injected ``now`` stays authoritative (hermetic
    callers keep exact stamps) and wall-clock jumps cannot move it backwards.
    Sub-second drift is dropped; lateness grace is configured in seconds.
    """

    def __init__(self, now: int | float) -> None:
        self._base = now
        self._start = _monotonic()

    def elapsed(self) -> int:
        return math.floor(max(_monotonic() - self._start, 0.0))

    def read(self, elapsed: int | None = None) -> int | float:
        if elapsed is None:
            elapsed = self.elapsed()
        return self._base if elapsed == 0 else self._base + elapsed


class JobStore:
    """One JSON file of jobs. A new store reloads what an earlier store wrote."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.jobs: list[dict] = []
        self._load()

    def schedule(
        self,
        prompt: str,
        due_at: int | float,
        *,
        interval_seconds: int | float | None = None,
    ) -> str:
        """Store one job and return its id. ``due_at`` is a unix timestamp in seconds."""
        job_id = uuid.uuid4().hex
        with self._store_transaction():
            self.jobs.append(
                {
                    "id": job_id,
                    "prompt": prompt,
                    "due_at": due_at,
                    "interval_seconds": interval_seconds,
                    "completed": False,
                    "last_result": None,
                    "last_ran_at": None,
                    "history": [],
                    "claimed_at": None,
                    "claim_token": None,
                }
            )
            self._save()
        return job_id

    def _schedule_typed(
        self,
        prompt: str,
        due_at: int | float,
        *,
        interval_seconds: int | float | None = None,
        kind: str,
        **metadata: Any,
    ) -> str:
        """Create a typed job atomically with its kind-specific metadata."""
        job_id = uuid.uuid4().hex
        with self._store_transaction():
            self.jobs.append(
                {
                    "id": job_id,
                    "prompt": prompt,
                    "due_at": due_at,
                    "interval_seconds": interval_seconds,
                    "completed": False,
                    "last_result": None,
                    "last_ran_at": None,
                    "history": [],
                    "claimed_at": None,
                    "claim_token": None,
                    "kind": kind,
                    **metadata,
                }
            )
            self._save()
        return job_id

    def _claim_typed(self, job_id: str, kind: str, now: int | float) -> dict | None:
        """Claim one typed occurrence in a short transaction."""
        with self._store_transaction():
            job = next(
                (
                    item
                    for item in self.jobs
                    if isinstance(item, dict) and item.get("id") == job_id
                ),
                None,
            )
            if job is None:
                return None
            history = job.get("history")
            already_run = isinstance(history, list) and any(
                isinstance(entry, dict) and entry.get("ran_at") == now
                for entry in history
            )
            due_at = job.get("due_at")
            token = job.get("claim_token")
            if token is not None:
                if not _claim_is_stale(token):
                    return None
                job["claim_token"] = None
                job["claimed_at"] = None
            if (
                job.get("kind") != kind
                or job.get("completed")
                or due_at is None
                or due_at > now
                or already_run
            ):
                return None
            token = _new_claim_token()
            scheduled_for = job["due_at"]
            job["claim_token"] = token
            job["claimed_at"] = now
            self._save()
            return {
                "job": json.loads(json.dumps(job)),
                "token": token,
                "scheduled_for": scheduled_for,
            }

    def _settle_typed_claim(
        self, claim: dict, update: Callable[[dict], None]
    ) -> dict | None:
        """Apply kind-specific state only while the claim is still owned."""
        with self._store_transaction():
            job = next(
                (
                    item
                    for item in self.jobs
                    if isinstance(item, dict) and item.get("id") == claim["job"]["id"]
                ),
                None,
            )
            if job is None or job.get("claim_token") != claim["token"]:
                return None
            update(job)
            job["claimed_at"] = None
            job["claim_token"] = None
            self._save()
            return json.loads(json.dumps(job))

    def remove(self, job_id: str, *, kind: str | None = None) -> bool:
        """Remove one job under the same short transaction as claims and writes."""
        with self._store_transaction():
            for index, job in enumerate(self.jobs):
                if (
                    isinstance(job, dict)
                    and job.get("id") == job_id
                    and (kind is None or job.get("kind") == kind)
                ):
                    self.jobs.pop(index)
                    self._save()
                    return True
        return False

    @contextlib.contextmanager
    def _store_transaction(self):
        """Serialize short store mutations across processes and reload first."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._load()
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _claim(self, job_id: str, now: int | float) -> dict | None:
        with self._store_transaction():
            job = next(
                (
                    item
                    for item in self.jobs
                    if isinstance(item, dict) and item.get("id") == job_id
                ),
                None,
            )
            if job is None:
                return None
            token = job.get("claim_token")
            if token is not None:
                if not _claim_is_stale(token):
                    return None
                job["claim_token"] = None
                job["claimed_at"] = None
            due_at = job.get("due_at")
            if job.get("completed") or due_at is None or due_at > now:
                return None
            history = job.get("history")
            if isinstance(history, list) and any(
                isinstance(entry, dict)
                and entry.get("scheduled_for") == job.get("due_at")
                and entry.get("ran_at") == now
                for entry in history
            ):
                return None
            due_at = job["due_at"]
            token = _new_claim_token()
            job["claim_token"] = token
            job["claimed_at"] = now
            self._save()
            return {
                "job": json.loads(json.dumps(job)),
                "token": token,
                "scheduled_for": due_at,
            }

    def _settle_claim(
        self,
        claim: dict,
        now: int | float,
        outcome: Any,
        disposition: str,
        *,
        write_receipt: bool = False,
    ) -> dict | None:
        with self._store_transaction():
            job = next(
                (
                    item
                    for item in self.jobs
                    if isinstance(item, dict) and item.get("id") == claim["job"]["id"]
                ),
                None,
            )
            if job is None or job.get("claim_token") != claim["token"]:
                return None
            history = job.get("history")
            if not isinstance(history, list):
                history = []
            entry = {"ran_at": now, "result": outcome}
            if write_receipt:
                entry = {
                    "scheduled_for": claim["scheduled_for"],
                    "ran_at": now,
                    "disposition": disposition,
                    "result": outcome,
                }
            history.append(entry)
            job["history"] = history[-HISTORY_LIMIT:]
            job["last_result"] = outcome
            job["last_ran_at"] = now
            job["claimed_at"] = None
            job["claim_token"] = None
            interval = job.get("interval_seconds")
            if interval is None:
                job["completed"] = True
            else:
                job["due_at"] = now + interval
            self._save()
            return json.loads(json.dumps(job))

    def tick(
        self,
        now: int | float,
        runner: Callable[[str], Any],
        *,
        skip_kind: str | None = None,
        skip_kinds: tuple[str, ...] | list[str] | None = None,
        write_receipts: bool = False,
    ) -> list[dict]:
        """Run due prompts; opt-in receipts are reserved for delegate dispatch."""
        skipped = set(SPECIALIZED_KINDS)
        if skip_kinds:
            skipped.update(skip_kinds)
        if skip_kind is not None:
            skipped.add(skip_kind)
        grace = cron_lateness_grace_seconds()
        tick_start = now
        with self._store_transaction():
            job_ids = [
                job["id"]
                for job in self.jobs
                if isinstance(job, dict)
                and not job.get("completed")
                and job.get("kind") in (None, GENERIC_PROMPT_KIND)
                and job.get("kind") not in skipped
                and job.get("due_at") is not None
                and job["due_at"] <= now
            ]
        ran: list[dict] = []
        clock = _TickClock(tick_start)
        for job_id in job_ids:
            # A fresh reading per queued job: an earlier slow runner must not
            # let this one pass the lateness check on the tick-start time.
            now = clock.read()
            claim = self._claim(job_id, now)
            if claim is None:
                continue
            if now - float(claim["scheduled_for"]) > grace:
                missed = {
                    "state": "missed",
                    "scheduled_for": claim["scheduled_for"],
                    "evaluated_at": now,
                    "lateness": now - float(claim["scheduled_for"]),
                }
                settled = self._settle_claim(
                    claim, now, missed, "missed", write_receipt=write_receipts
                )
                if settled is not None:
                    ran.append(settled)
                continue
            try:
                result = runner(claim["job"].get("prompt", ""))
            except Exception as exc:
                outcome = {
                    "state": "failed",
                    "error": f"{type(exc).__name__}: scheduler runner failed",
                }
                disposition = "failed"
            else:
                if not write_receipts:
                    outcome = result
                    disposition = "succeeded"
                else:
                    context = "delegate returned invalid output"
                    try:
                        if not isinstance(result, str) or not result.strip():
                            context = "generic prompt runner returned empty output"
                            raise ValueError
                        response = json.loads(result)
                        if not isinstance(response, dict):
                            context = "delegate returned malformed output"
                            raise ValueError
                        if response.get("error") or response.get("pending"):
                            context = "delegate returned error or pending output"
                            raise ValueError
                        summary = response.get("summary")
                        if not isinstance(summary, str) or not summary.strip():
                            context = "delegate returned empty final output"
                            raise ValueError
                        outcome = {"state": "succeeded", "summary": summary}
                        disposition = "succeeded"
                    except (
                        TypeError,
                        json.JSONDecodeError,
                        ValueError,
                    ) as exc:
                        if isinstance(exc, json.JSONDecodeError):
                            context = "delegate returned malformed output"
                        elif isinstance(exc, TypeError):
                            context = "delegate returned invalid output"
                        outcome = {
                            "state": "blocked",
                            "error": f"{type(exc).__name__}: {context}",
                        }
                        disposition = "blocked"
            settled = self._settle_claim(
                claim, now, outcome, disposition, write_receipt=write_receipts
            )
            if settled is not None:
                ran.append(settled)
        return ran

    def _load(self) -> None:
        if not self.path.exists():
            self.jobs = []
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise ValueError(f"job store {self.path} is not a jobs list")
        for job in jobs:
            if isinstance(job, dict):
                job.setdefault("history", [])
                job.setdefault("claimed_at", None)
                job.setdefault("claim_token", None)
        self.jobs = jobs

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"jobs": self.jobs}, ensure_ascii=False, indent=2) + "\n"
        # Write a sibling temp file and rename it over the store. `write_text`
        # truncates first, so a concurrent reader or a crash mid-write saw an
        # empty or partial file and the whole schedule was lost.
        try:
            mode: int | None = stat.S_IMODE(self.path.stat().st_mode)
        except FileNotFoundError:
            mode = None
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                os.chmod(tmp_name, mode)
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


DESK_LEAD_KIND = "desk_lead_pass"
"""Job kind marker stored on desk lead pass jobs (Phase 64 DESK-04).

A desk pass re-runs the lead routine (:func:`run_lead_pass`) over the shared
intake store on schedule, dispatching tickets through the runtime's parent
shim. It rides the same :class:`JobStore` scheduler as everything else and
follows the v10 heartbeat precedent (61-02): jobs fire serialized — one tick
runs each due pass once, in order — and the claim is persisted to disk before
the pass starts, so no store lock is held across the model calls inside it.
"""


def schedule_desk_lead_pass(
    store: JobStore,
    *,
    due_at: int | float,
    interval_seconds: int | float,
    intake_path: str | Path | None = None,
    limit: int = 5,
    prompt: str = "run one desk lead pass",
) -> str:
    """Schedule a recurring desk lead pass and return its id.

    A pass with no interval would be a single reminder, not a desk loop, so
    like the heartbeat kind it requires a positive ``interval_seconds``.
    ``intake_path`` pins the shared ``IntakeStore`` file; ``None`` means the
    runner resolves it (state dir, then the work root). ``limit`` bounds each
    pass's ticket batch.
    """
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a desk lead pass needs a positive interval_seconds")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return store._schedule_typed(
        prompt,
        due_at,
        interval_seconds=interval_seconds,
        kind=DESK_LEAD_KIND,
        intake_path=None if intake_path is None else str(intake_path),
        limit=limit,
    )


def list_desk_lead_passes(store: JobStore) -> list[dict]:
    """All desk lead pass jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == DESK_LEAD_KIND
    ]


def desk_lead_runner(
    parent: Any = None,
    work_root: str | Path | None = None,
    *,
    bot: str = "bot-00-omega-prime",
    intake_path: str | Path | None = None,
) -> Callable[[dict], Any]:
    """Build the production runner for ``desk_lead_pass`` jobs.

    Each pass resolves the same ``IntakeStore`` the lead tools use (the
    shared state dir via :func:`desk_intake_path`) and dispatches tickets
    through the parent shim, so the routine and ``lead_intake_next`` /
    ``lead_intake_ack`` see the same records. ``intake_path`` is the file the
    host's lead tools opened (from its effective settings env); a job's own
    ``intake_path`` still wins. The lead stack is imported inside the
    runner, so plain cron ticks never load it.
    """

    def runner(job: dict) -> Any:
        from omega_prime.routines.desk_lead import (
            desk_intake_path,
            make_dispatch,
            run_lead_pass,
        )
        from omega_prime.tools.lead import IntakeStore

        path = job.get("intake_path") or intake_path or desk_intake_path(work_root)
        intake = IntakeStore(path)
        limit = job.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            limit = 5
        return run_lead_pass(
            intake, make_dispatch(parent, work_root, bot=bot), limit=limit, by=bot
        )

    return runner


def tick_desk_lead_passes(
    store: JobStore, now: int | float, lead_runner: Callable[[dict], Any]
) -> list[dict]:
    """Run each due ``desk_lead_pass`` job once, in order.

    Mirrors the v10 heartbeat tick: only desk jobs are considered, a job
    claimed or already run for ``now`` is skipped, and the claim is saved to
    disk before ``lead_runner(job)`` starts — so a concurrent tick (or
    process) sees ``claimed_at`` and nothing holds a store lock across the
    pass's model calls.
    """
    with store._store_transaction():
        job_ids = [
            job["id"]
            for job in store.jobs
            if isinstance(job, dict)
            and job.get("kind") == DESK_LEAD_KIND
            and not job.get("completed")
            and job.get("due_at") is not None
            and job["due_at"] <= now
        ]
    # Read settings before any claim is saved: an invalid value must not
    # leave a claimed occurrence behind.
    grace = cron_lateness_grace_seconds()
    ran: list[dict] = []
    clock = _TickClock(now)
    for job_id in job_ids:
        # Fresh reading per queued pass (see JobStore.tick).
        current = clock.read()
        claim = store._claim(job_id, current)
        if claim is None:
            continue
        lateness = current - float(claim["scheduled_for"])
        if lateness > grace:
            missed = {
                "state": "missed",
                "scheduled_for": claim["scheduled_for"],
                "evaluated_at": current,
                "lateness": lateness,
            }
            settled = store._settle_claim(
                claim, current, missed, "missed", write_receipt=True
            )
            if settled is not None:
                ran.append(settled)
            continue
        try:
            result = lead_runner(claim["job"])
            if result is None:
                outcome = {
                    "state": "blocked",
                    "error": "desk lead runner returned no outcome",
                }
                disposition = "blocked"
            elif not isinstance(result, dict):
                outcome = {
                    "state": "blocked",
                    "error": "desk lead runner returned invalid outcome",
                }
                disposition = "blocked"
            else:
                outcome = dict(result)
                outcome.setdefault("state", "succeeded")
                disposition = "succeeded"
        except Exception as exc:
            outcome = {
                "state": "failed",
                "error": f"{type(exc).__name__}: desk lead runner failed",
            }
            disposition = "failed"
        settled = store._settle_claim(
            claim, current, outcome, disposition, write_receipt=True
        )
        if settled is not None:
            ran.append(settled)
    return ran


def run_desk_lead_passes(
    store: JobStore,
    now: int | float,
    parent: Any = None,
    work_root: str | Path | None = None,
    *,
    bot: str = "bot-00-omega-prime",
    intake_path: str | Path | None = None,
) -> list[dict]:
    """Production entry point: tick due desk passes with real dispatch.

    ``parent`` is the runtime's in-process agent shim (``runtime.parent``)
    and ``work_root`` the runtime work root. Passes always run through a real
    dispatch closure — never ``dispatch=None``; without a parent every ticket
    comes back explicitly blocked with a failure receipt.
    """
    return tick_desk_lead_passes(
        store,
        now,
        desk_lead_runner(parent, work_root, bot=bot, intake_path=intake_path),
    )


def run_due_jobs(store: JobStore, now: int | float, parent: Any = None) -> list[dict]:
    """Dispatch due generic prompt jobs through the supplied delegate parent."""

    def runner(prompt: str) -> str:
        if parent is None:
            return json.dumps({"error": "delegate parent unavailable"})
        from omega_prime.agent.delegate import delegate_task

        return delegate_task(parent, prompt)

    return store.tick(now, runner, write_receipts=True)


#: Job kind marker stored on RoE watchdog jobs (K11). An independent
#: ticker that flags expired grants even when the heartbeat system is
#: down: each tick runs the lifecycle ``watchdog_check`` on the job's
#: configured RoE artifact path and persists state transitions to the
#: job digest. Never stored on any other kind — pre-existing kinds are
#: unchanged.
ROE_WATCHDOG_KIND = "roe_watchdog"

#: Default watchdog cadence: once a day (seconds).
ROE_WATCHDOG_INTERVAL_SECONDS = 86400

#: Bounded per-job run history, mirroring :func:`tick_desk_lead_passes`.
ROE_WATCHDOG_HISTORY_LIMIT = 20


def schedule_roe_watchdog(
    store: JobStore,
    artifact_path: str,
    *,
    due_at: int | float,
    interval_seconds: int | float = ROE_WATCHDOG_INTERVAL_SECONDS,
) -> str:
    """Schedule a recurring RoE watchdog for ``artifact_path`` and return its id.

    Mirrors :func:`schedule_desk_lead_pass`: validates a non-empty
    artifact path and a positive interval, then stamps ``kind`` +
    ``artifact_path`` on the stored job. The default cadence is daily.
    """
    if not isinstance(artifact_path, str) or not artifact_path.strip():
        raise ValueError("artifact_path must be a non-empty string")
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a roe watchdog needs a positive interval_seconds")
    return store._schedule_typed(
        f"roe-watchdog {artifact_path.strip()}",
        due_at,
        interval_seconds=interval_seconds,
        kind=ROE_WATCHDOG_KIND,
        artifact_path=artifact_path.strip(),
    )


def list_roe_watchdogs(store: JobStore) -> list[dict]:
    """All RoE watchdog jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == ROE_WATCHDOG_KIND
    ]


def clear_roe_watchdog(store: JobStore, job_id: str) -> dict:
    """Remove one RoE watchdog by id. Unknown id is a structured result."""
    if store.remove(job_id, kind=ROE_WATCHDOG_KIND):
        return {"cleared": job_id}
    return {"error": f"no roe-watchdog job: {job_id!r}"}


def _roe_watchdog_now(now: Any) -> tuple[Any, float]:
    """Normalize ``now`` to ``(checker_now, stamp)``.

    Accepts a UTC datetime (hermetic clock) or a unix timestamp; the job
    record only ever stores numeric stamps (JSON-safe), while the
    checker always receives a datetime.
    """
    if isinstance(now, datetime):
        current = now
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        else:
            current = current.astimezone(UTC)
    else:
        current = datetime.fromtimestamp(now, tz=UTC)
    return current, current.timestamp()


def tick_roe_watchdog(store: JobStore, now: Any, checker: Any = None) -> list[dict]:
    """Run each due RoE watchdog through ``watchdog_check``.

    Only ``roe_watchdog`` non-completed jobs due at ``now`` run, one
    tick each in order; a job claimed or already run for ``now`` is
    skipped, and the claim is saved before the check starts. ``checker``
    defaults to :func:`omega_prime.grokbot.roe_lifecycle.watchdog_check`
    (imported lazily so plain cron ticks never load the lifecycle
    stack); pass an explicit ``checker(artifact_path, now=...)`` seam
    for hermetic tests. Each tick records ``last_result`` /
    ``last_ran_at``, appends bounded history (cap 20), and advances
    ``due_at = stamp + interval``. State transitions persist to
    ``job["roe_watchdog_digest"]`` (``{"from", "to", "at",
    "artifact_id", "effective_until", "action"}``) and update
    ``job["roe_watchdog_state"]``, so a DOWN heartbeat cannot silence an
    expiring grant — the digest keeps the valid → expiring → expired trail.
    The check is read-only, so an occurrence older than the configured
    lateness grace still reads the grant now (its history entry carries
    ``late``) and the next due time is set from that fresh check. Never raises:
    a checker failure records a fail-closed ``missing`` result instead. Pass
    """
    if checker is None:
        from omega_prime.grokbot.roe_lifecycle import watchdog_check

        checker = watchdog_check

    checker_now, stamp = _roe_watchdog_now(now)
    # Read settings before any claim is saved (see tick_desk_lead_passes).
    grace = cron_lateness_grace_seconds()
    ran: list[dict] = []
    with store._store_transaction():
        job_ids = [
            job["id"]
            for job in store.jobs
            if isinstance(job, dict)
            and job.get("kind") == ROE_WATCHDOG_KIND
            and not job.get("completed")
            and job.get("due_at") is not None
            and job["due_at"] <= stamp
        ]
    clock = _TickClock(stamp)
    for job_id in job_ids:
        # Fresh reading per queued watchdog (see JobStore.tick).
        elapsed = clock.elapsed()
        stamp = clock.read(elapsed)
        job_now = checker_now + timedelta(seconds=elapsed)
        claim = store._claim_typed(job_id, ROE_WATCHDOG_KIND, stamp)
        if claim is None:
            continue
        claimed_job = claim["job"]
        lateness = stamp - float(claim["scheduled_for"])
        # The watchdog only reads the grant, so a late occurrence is still
        # checked now: skipping it would leave ``roe_watchdog_state`` stale
        # (say ``valid`` past expiry) for a whole interval. Lateness beyond
        # the grace is recorded on the history entry instead.
        late = (
            {"scheduled_for": claim["scheduled_for"], "lateness": lateness}
            if lateness > grace
            else None
        )
        artifact = claimed_job.get("artifact_path", "")
        try:
            result = checker(artifact, now=job_now)
        except Exception:
            result = {
                "state": "missing",
                "artifact_id": artifact
                if isinstance(artifact, str) and artifact.strip()
                else "unknown-artifact",
                "effective_until": None,
                "action": "provide artifact",
            }
        if not isinstance(result, dict):
            result = {
                "state": "missing",
                "artifact_id": "unknown-artifact",
                "effective_until": None,
                "action": "provide artifact",
            }
        if result.get("state") not in (
            "valid",
            "expiring-7d",
            "expiring-1d",
            "expired",
            "missing",
            "invalid",
        ):
            result = {
                "state": "invalid",
                "artifact_id": result.get("artifact_id", "unknown-artifact"),
                "effective_until": result.get("effective_until"),
                "action": "fix artifact",
            }

        def settle(
            job: dict,
            *,
            stamp: float = stamp,
            result: dict = result,
            late: dict | None = late,
        ) -> None:
            history = job.get("history")
            if not isinstance(history, list):
                history = []
            job["last_result"] = result
            job["last_ran_at"] = stamp
            entry: dict[str, Any] = {"ran_at": stamp, "result": result}
            if late is not None:
                entry["late"] = late
            history.append(entry)
            job["history"] = history[-ROE_WATCHDOG_HISTORY_LIMIT:]
            interval = job.get("interval_seconds")
            try:
                job["due_at"] = stamp + (
                    interval if interval is not None else ROE_WATCHDOG_INTERVAL_SECONDS
                )
            except TypeError:
                job["due_at"] = stamp + ROE_WATCHDOG_INTERVAL_SECONDS
            previous = job.get("roe_watchdog_state")
            current_state = result.get("state")
            if previous != current_state:
                job["roe_watchdog_state"] = current_state
                digest = job.get("roe_watchdog_digest")
                if not isinstance(digest, list):
                    digest = []
                digest.append(
                    {
                        "from": previous,
                        "to": current_state,
                        "at": stamp,
                        "artifact_id": result.get("artifact_id"),
                        "effective_until": result.get("effective_until"),
                        "action": result.get("action"),
                    }
                )
                job["roe_watchdog_digest"] = digest

        settled = store._settle_typed_claim(claim, settle)
        if settled is not None:
            ran.append(settled)
    return ran


#: Job kind marker stored on pin-bump schedule jobs (L9). A scheduled
#: pin-bump re-runs the audit chain on a cron cadence with audit-only
#: semantics — no mutations — and only opens the normal bump PR path
#: for runs whose verdict is improved (and, when the job's stored
#: config enables ``--semver-check``, whose fixes are all
#: semver-compatible). Any other verdict is recorded in the job digest
#: and nothing else happens. Never stored on any other kind —
#: pre-existing kinds are unchanged.
PIN_BUMP_SCHEDULE_KIND = "pin_bump_schedule"

#: Bounded per-job run history, mirroring :func:`tick_desk_lead_passes`.
PIN_BUMP_SCHEDULE_HISTORY_LIMIT = 20

#: Accepted minute/hour/day-of-month/month/day-of-week ranges for one
#: five-field cron expression (day-of-week accepts 0-7; 7 is Sunday).
_PIN_BUMP_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _pin_bump_cron_atom(atom: str, lo: int, hi: int) -> bool:
    """True when one comma-separated cron atom is in range for ``lo``-``hi``."""
    if not atom:
        return False
    if atom == "*":
        return True
    if "/" in atom:
        base, _, step_text = atom.partition("/")
        if not step_text.isdigit() or int(step_text) < 1:
            return False
        if not base:
            return False
        if base == "*":
            base = f"{lo}-{hi}"
        atom = base
        if atom == "*":
            return True
    if "-" in atom:
        start_text, _, end_text = atom.partition("-")
        if not start_text.isdigit() or not end_text.isdigit():
            return False
        start, end = int(start_text), int(end_text)
        return lo <= start <= end <= hi
    return atom.isdigit() and lo <= int(atom) <= hi


def validate_pin_bump_cron(expr: str) -> str:
    """Normalized cron expression, or raise ``ValueError`` (fail-closed).

    Accepts a five-field ``minute hour dom month dow`` expression using
    ``*``, ``*/step``, ranges, steps over ranges, and comma lists of
    those atoms. Anything else — wrong arity, out-of-range values,
    names, ``@`` shorthands — is a usage error.
    """
    if not isinstance(expr, str):
        raise ValueError("cron expression must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            "cron expression needs 5 fields "
            f"(minute hour dom month dow), got {len(fields)}"
        )
    for field, (lo, hi) in zip(fields, _PIN_BUMP_CRON_RANGES, strict=True):
        atoms = field.split(",")
        if not atoms or any(not _pin_bump_cron_atom(atom, lo, hi) for atom in atoms):
            raise ValueError(f"invalid cron field: {field!r}")
    return " ".join(fields)


def _pin_bump_field_matches(field: str, lo: int, hi: int, value: int) -> bool:
    """True when a validated cron field fires for ``value``."""
    for atom in field.split(","):
        step = 1
        if "/" in atom:
            atom, _, step_text = atom.partition("/")
            step = int(step_text)
            if atom == "*":
                atom = f"{lo}-{hi}"
            elif atom.isdigit():
                # Vixie ``N/step`` means ``N-hi/step``.
                atom = f"{atom}-{hi}"
        if atom == "*":
            span_lo, span_hi = lo, hi
        elif "-" in atom:
            start_text, _, end_text = atom.partition("-")
            span_lo, span_hi = int(start_text), int(end_text)
        else:
            span_lo = span_hi = int(atom)
        candidates = set(range(span_lo, span_hi + 1, step))
        # Day-of-week 7 is Sunday (0); normalize both sides.
        normalized = {7 if hi == 7 and v == 0 else v for v in candidates}
        probe = 7 if hi == 7 and value == 0 and 7 in normalized else value
        if probe in normalized or value in candidates:
            return True
    return False


def _pin_bump_matches(expr: str, moment: datetime) -> bool:
    """True when validated ``expr`` fires at ``moment`` (minute precision)."""
    fields = expr.split()
    values = (
        moment.minute,
        moment.hour,
        moment.day,
        moment.month,
        moment.isoweekday() % 7,
    )
    return all(
        _pin_bump_field_matches(field, lo, hi, value)
        for field, (lo, hi), value in zip(
            fields, _PIN_BUMP_CRON_RANGES, values, strict=True
        )
    )


def pin_bump_next_due(cron_expr: str, after: int | float) -> float:
    """Next UTC fire time strictly after ``after`` for ``cron_expr``.

    Raises ``ValueError`` for an invalid expression or when no fire
    time exists within two years (fail-closed — a never-firing shape
    such as Feb 30 is never silently scheduled).
    """
    expr = validate_pin_bump_cron(cron_expr)
    cursor = int(after) - (int(after) % 60) + 60
    for _ in range(2 * 366 * 24 * 60):
        moment = datetime.fromtimestamp(cursor, tz=UTC)
        if _pin_bump_matches(expr, moment):
            return float(cursor)
        cursor += 60
    raise ValueError(f"cron expression never fires: {expr!r}")


def schedule_pin_bump(
    store: JobStore,
    *,
    cron_expr: str,
    name: str = "pin-bump",
    config: dict | None = None,
    now: int | float | None = None,
) -> str:
    """Schedule a recurring pin-bump audit and return its id.

    Mirrors :func:`schedule_desk_lead_pass`: validates the cron
    expression fail-closed (``ValueError`` — nothing is registered),
    then stamps ``kind`` + ``schedule_name`` + ``cron_expr`` + the
    caller's argv-echo ``config`` on the stored job. A duplicate
    ``name`` replaces the previous job with that name. ``last_verdict``
    and ``last_run`` start unset and are filled by each tick.
    """
    expr = validate_pin_bump_cron(cron_expr)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("schedule name must be a non-empty string")
    label = name.strip()
    stamp = time.time() if now is None else float(now)
    next_due = pin_bump_next_due(expr, stamp)
    job_id = uuid.uuid4().hex
    with store._store_transaction():
        store.jobs = [
            job
            for job in store.jobs
            if not (
                isinstance(job, dict)
                and job.get("kind") == PIN_BUMP_SCHEDULE_KIND
                and job.get("schedule_name") == label
            )
        ]
        store.jobs.append(
            {
                "id": job_id,
                "prompt": f"pin-bump-schedule {label} {expr}",
                "due_at": next_due,
                "interval_seconds": None,
                "completed": False,
                "last_result": None,
                "last_ran_at": None,
                "history": [],
                "claimed_at": None,
                "claim_token": None,
                "kind": PIN_BUMP_SCHEDULE_KIND,
                "schedule_name": label,
                "cron_expr": expr,
                "pin_bump_config": dict(config) if config else {},
                "last_verdict": None,
                "last_run": None,
            }
        )
        store._save()
    return job_id


def list_pin_bump_schedules(store: JobStore) -> list[dict]:
    """All pin-bump schedule jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == PIN_BUMP_SCHEDULE_KIND
    ]


def _default_pin_bump_audit(job: dict, root: Any) -> dict:
    """Run one scheduled audit with audit-only semantics (no mutations)."""
    from omega_prime.grokbot import pin_bump as pin_bump_mod

    return pin_bump_mod.audit_scheduled_job(
        dict(job.get("pin_bump_config") or {}), root=root
    )


def _default_pin_bump_apply(job: dict, root: Any) -> Any:
    """Open the normal bump PR path for one scheduled run."""
    from omega_prime.grokbot import pin_bump as pin_bump_mod

    return pin_bump_mod.apply_scheduled_bump(
        dict(job.get("pin_bump_config") or {}), root=root
    )


def tick_pin_bump_schedules(
    store: JobStore,
    now: int | float,
    *,
    audit: Callable[[dict], Any] | None = None,
    bump: Callable[[dict], Any] | None = None,
    root: str | Path | None = None,
    approval_log: Any = None,
    can_apply_pin_bump: Callable[[], bool] | None = None,
) -> list[dict]:
    """Run each due pin-bump schedule once, in order.

    Each tick runs the audit chain with audit-only semantics (no
    mutations) and opens the normal bump PR path only when the
    verdict is ``improved`` — and, when the job's stored config
    enables ``--semver-check``, only when every fix is
    semver-compatible. Any other outcome records the verdict in the
    job digest and does nothing. ``audit(job)`` returns a mapping
    with ``verdict`` (and ``semver_compatible`` when relevant);
    ``bump(job)`` performs the PR path. Both default to the real
    ``pin_bump`` seams (imported lazily so plain cron ticks never
    load the runbook stack); pass explicit seams for hermetic tests.
    Never raises: a failing seam records a fail-closed digest entry.
    """
    stamp = float(now)
    if root is None:
        root = store.path.parent.parent
    ran: list[dict] = []
    with store._store_transaction():
        job_ids = [
            job["id"]
            for job in store.jobs
            if isinstance(job, dict)
            and job.get("kind") == PIN_BUMP_SCHEDULE_KIND
            and not job.get("completed")
            and job.get("due_at") is not None
            and job["due_at"] <= stamp
        ]
    grace = cron_lateness_grace_seconds()
    clock = _TickClock(stamp)
    for job_id in job_ids:
        # A fresh reading per queued job: a slow earlier job must not let
        # this one pass the lateness check on the tick-start stamp.
        stamp = clock.read()
        claim = store._claim_typed(job_id, PIN_BUMP_SCHEDULE_KIND, stamp)
        if claim is None:
            continue
        job = claim["job"]
        due_at = claim["scheduled_for"]
        lateness = stamp - float(due_at)
        if lateness > grace:
            outcome = {
                "scheduled_for": due_at,
                "evaluated_at": stamp,
                "lateness": lateness,
                "disposition": "missed",
            }

            def settle_missed(
                current: dict, *, outcome: dict = outcome, stamp: float = stamp
            ) -> None:
                current["last_result"] = outcome
                current["last_ran_at"] = stamp
                history = current.get("history")
                if not isinstance(history, list):
                    history = []
                history.append({"ran_at": stamp, **outcome})
                current["history"] = history[-PIN_BUMP_SCHEDULE_HISTORY_LIMIT:]
                try:
                    current["due_at"] = pin_bump_next_due(
                        current.get("cron_expr", "* * * * *"), stamp
                    )
                except ValueError:
                    current["completed"] = True

            settled = store._settle_typed_claim(claim, settle_missed)
            if settled is not None:
                ran.append(settled)
            continue
        config = job.get("pin_bump_config")
        if not isinstance(config, dict):
            config = {}
        semver_on = bool(config.get("semver_check"))
        try:
            audit_seam = audit or (lambda j: _default_pin_bump_audit(j, root))
            result = audit_seam(job)
        except Exception as exc:
            result = {"verdict": "error", "error": type(exc).__name__}
        if not isinstance(result, dict):
            result = {"verdict": "error", "error": "audit seam misbehaved"}
        verdict = result.get("verdict")
        semver_ok = result.get("semver_compatible") is True
        action = "none"
        bump_result: Any = None
        if verdict == "improved" and (not semver_on or semver_ok):
            try:
                approved = (
                    approval_log is not None
                    and callable(getattr(approval_log, "is_approved", None))
                    and approval_log.is_approved(PIN_BUMP_APPLY_APPROVAL)
                    and can_apply_pin_bump is not None
                    and can_apply_pin_bump()
                )
                if not approved or not approval_log.is_approved(
                    PIN_BUMP_APPLY_APPROVAL
                ):
                    action = "blocked-approval-required"
                else:
                    bump_seam = bump or (lambda j: _default_pin_bump_apply(j, root))
                    with contextlib.redirect_stdout(io.StringIO()):
                        bump_result = bump_seam(job)
                    action = "bump-pr" if bump_result == 0 else "bump-pr-failed"
            except Exception as exc:
                bump_result = type(exc).__name__
                action = "bump-pr-failed"
        outcome = {"verdict": verdict, "action": action, "detail": bump_result}

        def settle_result(
            current: dict,
            *,
            outcome: dict = outcome,
            verdict: Any = verdict,
            stamp: float = stamp,
            semver_ok: bool = semver_ok,
            action: str = action,
            bump_result: Any = bump_result,
        ) -> None:
            current["last_result"] = outcome
            current["last_verdict"] = verdict
            current["last_run"] = stamp
            history = current.get("history")
            if not isinstance(history, list):
                history = []
            history.append({"ran_at": stamp, "result": outcome})
            current["history"] = history[-PIN_BUMP_SCHEDULE_HISTORY_LIMIT:]
            digest = current.get("pin_bump_digest")
            if not isinstance(digest, list):
                digest = []
            digest.append(
                {
                    "at": stamp,
                    "verdict": verdict,
                    "action": action,
                    "semver_compatible": semver_ok,
                    "detail": bump_result,
                }
            )
            current["pin_bump_digest"] = digest[-PIN_BUMP_SCHEDULE_HISTORY_LIMIT:]
            try:
                current["due_at"] = pin_bump_next_due(
                    current.get("cron_expr", "* * * * *"), stamp
                )
            except ValueError:
                current["completed"] = True

        settled = store._settle_typed_claim(claim, settle_result)
        if settled is not None:
            ran.append(settled)
    return ran


#: Job kind marker stored on purple-cycle jobs. A scheduled cycle runs
#: ``omega_prime.grokbot.purple_cycle`` in ``plan`` or ``run`` only.
#: ``apply`` is refused at schedule time so findings-dispatch never
#: executes from this ticker. The generic due-job tick skips this kind.
PURPLE_CYCLE_KIND = "purple_cycle"

#: Bounded per-job run history, mirroring :func:`tick_roe_watchdog`.
PURPLE_CYCLE_HISTORY_LIMIT = 20

#: Modes a scheduled cycle may store. ``apply`` is never one of them.
_PURPLE_CYCLE_MODES = ("plan", "run")

#: Canonical step names. Order matches ``purple_cycle.STEP_NAMES``.
PURPLE_CYCLE_STEPS = (
    "preflight",
    "release-watch",
    "pin-bump-audit",
    "engagement",
    "findings-plan",
    "findings-dispatch",
)


def schedule_purple_cycle(
    store: JobStore,
    *,
    due_at: int | float,
    interval_seconds: int | float,
    mode: str = "plan",
    steps: Any = None,
) -> str:
    """Schedule a recurring purple cycle and return its id.

    ``mode`` is ``plan`` or ``run``. ``apply``, an empty mode, a
    non-positive interval, a bool interval, and an unknown step name
    raise ``ValueError`` before ``store.schedule`` is called. ``steps``
    is ``None`` (every known step) or a tuple of known step names stored
    on the job. The scheduled mode never runs findings-dispatch through
    apply.
    """
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, (int, float))
        or interval_seconds <= 0
    ):
        raise ValueError("a purple cycle needs a positive interval_seconds")
    if not isinstance(mode, str) or mode not in _PURPLE_CYCLE_MODES:
        raise ValueError("purple cycle mode must be 'plan' or 'run'")
    stored_steps: tuple[str, ...] | None
    if steps is None:
        stored_steps = None
    else:
        if isinstance(steps, str) or not isinstance(steps, (tuple, list)):
            raise ValueError("steps must be a sequence of known step names")
        if not steps:
            raise ValueError("steps must name at least one step")
        unknown = [name for name in steps if name not in PURPLE_CYCLE_STEPS]
        if unknown:
            raise ValueError(
                f"unknown step {unknown[0]!r}; expected {PURPLE_CYCLE_STEPS}"
            )
        stored_steps = tuple(name for name in PURPLE_CYCLE_STEPS if name in steps)
    return store._schedule_typed(
        f"purple-cycle {mode}",
        due_at,
        interval_seconds=interval_seconds,
        kind=PURPLE_CYCLE_KIND,
        mode=mode,
        steps=stored_steps,
    )


def list_purple_cycles(store: JobStore) -> list[dict]:
    """All purple-cycle jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == PURPLE_CYCLE_KIND
    ]


def clear_purple_cycle(store: JobStore, job_id: str) -> dict:
    """Remove one purple cycle by id. Unknown id is a structured result."""
    if store.remove(job_id, kind=PURPLE_CYCLE_KIND):
        return {"cleared": job_id}
    return {"error": f"no purple-cycle job: {job_id!r}"}


def _purple_cycle_failed(result: Any) -> bool:
    """True when a cycle result is not a successful completed run."""
    if not isinstance(result, dict):
        return True
    if result.get("ok") is False:
        return True
    state = result.get("state")
    if state in ("failed", "blocked", "error"):
        return True
    exit_code = result.get("exit")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return result.get("ok") is not True
    return exit_code != 0


def _due_token(due: Any) -> str:
    """Stable checkpoint token for one scheduled due time."""
    if isinstance(due, bool) or not isinstance(due, (int, float)):
        return "na"
    if due != due or due in (float("inf"), float("-inf")):
        return "na"
    whole = int(due)
    if whole == due:
        return str(whole)
    return format(float(due), "f").replace("-", "m").replace(".", "p")


def _occurrence_token(value: Any, fallback: str) -> str:
    """Keep a job id inside the checkpoint-id alphabet."""
    text = value.strip() if isinstance(value, str) else ""
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)
    cleaned = cleaned.strip(".-")
    if not cleaned or not cleaned[0].isalnum():
        return fallback
    return cleaned[:80]


def _purple_occurrence_cycle_id(job: dict) -> str:
    """Durable id for this due time. A retry of the same due time matches it.

    The default purple-cycle id is the UTC date, so every ``run`` tick that
    day resumes the first completed checkpoint and records success without
    doing the work. The due time stays put until the runner returns, so a
    crash retry of this occurrence still resumes, and the next interval does not.
    """
    from omega_prime.grokbot.purple_cycle import STEP_NAMES

    mode = job.get("mode")
    if mode not in _PURPLE_CYCLE_MODES:
        raise ValueError("purple cycle mode must be 'plan' or 'run'")
    steps = job.get("steps")
    if isinstance(steps, (list, tuple)) and not isinstance(steps, str):
        selected = [name for name in STEP_NAMES if name in steps]
    else:
        selected = list(STEP_NAMES)
    cycle_id = (
        "purple-cycle-"
        f"{_occurrence_token(job.get('id'), 'job')}-{_due_token(job.get('due_at'))}-{mode}"
    )
    if selected != list(STEP_NAMES):
        digest = hashlib.sha256(",".join(selected).encode("ascii")).hexdigest()
        cycle_id += f"-s{digest[:8]}"
    return cycle_id


def _default_purple_cycle_runner(job: dict, root: Path | None = None) -> Any:
    """Run one cycle in the job's plan or run mode. Never apply."""
    from omega_prime.grokbot import purple_cycle

    mode = job.get("mode")
    if mode not in _PURPLE_CYCLE_MODES:
        raise ValueError("purple cycle mode must be 'plan' or 'run'")
    steps = job.get("steps")
    return purple_cycle.run_cycle(
        mode=mode,
        steps=steps,
        cycle_id=_purple_occurrence_cycle_id(job),
        root=root,
    )


def tick_purple_cycles(
    store: JobStore,
    now: int | float,
    runner: Callable[[dict], Any] | None = None,
    *,
    root: Path | None = None,
) -> list[dict]:
    """Run each due purple cycle once, in order.

    Only non-completed ``purple_cycle`` jobs due at ``now`` run. A job
    already claimed or recorded for ``now`` is skipped, and the claim is
    saved before the runner starts. ``runner(job)`` defaults to
    :func:`_default_purple_cycle_runner`, which calls
    ``omega_prime.grokbot.purple_cycle.run_cycle`` in the job's ``plan``
    or ``run`` mode and never ``apply``. ``root`` is the install directory
    whose ``omega-prime.json`` the cycle must load; it is passed only to
    that default runner. A failed cycle result is stored
    on the job history as ``failed`` and is omitted from the returned
    list (that list is the successful runs). After a run, ``due_at``
    advances by ``interval_seconds``, so a second tick at the same
    ``now`` does not run the job again.
    """
    stamp = float(now)
    ran: list[dict] = []
    with store._store_transaction():
        job_ids = [
            job["id"]
            for job in store.jobs
            if isinstance(job, dict)
            and job.get("kind") == PURPLE_CYCLE_KIND
            and not job.get("completed")
            and job.get("due_at") is not None
            and job["due_at"] <= stamp
        ]
    grace = cron_lateness_grace_seconds()
    clock = _TickClock(stamp)
    for job_id in job_ids:
        # A fresh reading per queued job: a slow earlier job must not let
        # this one pass the lateness check on the tick-start stamp.
        stamp = clock.read()
        claim = store._claim_typed(job_id, PURPLE_CYCLE_KIND, stamp)
        if claim is None:
            continue
        job = claim["job"]
        due_at = claim["scheduled_for"]
        lateness = stamp - float(due_at)
        if lateness > grace:
            outcome = {
                "state": "missed",
                "scheduled_for": due_at,
                "evaluated_at": stamp,
                "lateness": lateness,
            }

            def settle_missed(
                current: dict, *, outcome: dict = outcome, stamp: float = stamp
            ) -> None:
                current["last_result"] = outcome
                current["last_ran_at"] = stamp
                history = current.get("history")
                if not isinstance(history, list):
                    history = []
                history.append({"ran_at": stamp, "result": outcome, "status": "missed"})
                current["history"] = history[-PURPLE_CYCLE_HISTORY_LIMIT:]
                interval = current.get("interval_seconds")
                if (
                    isinstance(interval, bool)
                    or not isinstance(interval, (int, float))
                    or interval <= 0
                ):
                    current["completed"] = True
                else:
                    current["due_at"] = stamp + interval

            settled = store._settle_typed_claim(claim, settle_missed)
            if settled is not None:
                ran.append(settled)
            continue
        if job.get("mode") not in _PURPLE_CYCLE_MODES:
            result = {
                "ok": False,
                "exit": 1,
                "state": "failed",
                "error": "invalid scheduled mode",
            }
        else:
            try:
                result = (
                    _default_purple_cycle_runner(job, root=root)
                    if runner is None
                    else runner(job)
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "exit": 1,
                    "state": "failed",
                    "error": type(exc).__name__,
                }
        failed = _purple_cycle_failed(result)

        def settle_result(
            current: dict,
            *,
            result: Any = result,
            stamp: float = stamp,
            failed: bool = failed,
        ) -> None:
            current["last_result"] = result
            current["last_ran_at"] = stamp
            history = current.get("history")
            if not isinstance(history, list):
                history = []
            history.append(
                {
                    "ran_at": stamp,
                    "result": result,
                    "status": "failed" if failed else "ok",
                }
            )
            current["history"] = history[-PURPLE_CYCLE_HISTORY_LIMIT:]
            interval = current.get("interval_seconds")
            if isinstance(interval, bool) or not isinstance(interval, (int, float)):
                current["completed"] = True
            elif interval > 0:
                current["due_at"] = stamp + interval
            else:
                current["completed"] = True

        settled = store._settle_typed_claim(claim, settle_result)
        if settled is not None and not failed:
            ran.append(settled)
    return ran


__all__ = [
    "DESK_LEAD_KIND",
    "HISTORY_LIMIT",
    "PIN_BUMP_SCHEDULE_HISTORY_LIMIT",
    "PIN_BUMP_SCHEDULE_KIND",
    "PURPLE_CYCLE_HISTORY_LIMIT",
    "PURPLE_CYCLE_KIND",
    "PURPLE_CYCLE_STEPS",
    "ROE_WATCHDOG_HISTORY_LIMIT",
    "ROE_WATCHDOG_INTERVAL_SECONDS",
    "ROE_WATCHDOG_KIND",
    "JobStore",
    "clear_purple_cycle",
    "clear_roe_watchdog",
    "desk_lead_runner",
    "list_desk_lead_passes",
    "list_pin_bump_schedules",
    "list_purple_cycles",
    "list_roe_watchdogs",
    "pin_bump_next_due",
    "run_desk_lead_passes",
    "run_due_jobs",
    "schedule_desk_lead_pass",
    "schedule_pin_bump",
    "schedule_purple_cycle",
    "schedule_roe_watchdog",
    "tick_desk_lead_passes",
    "tick_pin_bump_schedules",
    "tick_purple_cycles",
    "tick_roe_watchdog",
    "validate_pin_bump_cron",
]

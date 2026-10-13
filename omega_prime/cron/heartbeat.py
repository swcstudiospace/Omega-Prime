# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Session heartbeat — a cron job kind that re-enters a named session.

Behavior port of Prime Agent's heartbeat (``rlm-heartbeat`` skill +
``pa-core/src/cron/``) @ ``967eb13f`` (MIT, PrimeIntellect). A heartbeat is a
recurring job whose prompt re-enters a named session on schedule, riding the
existing :class:`JobStore` scheduler (LOOP-02). Jobs fire serialized (the v4
rule): one tick runs each due heartbeat once, in order.

The heartbeat prompt carries the target session name so the runner re-enters
that session rather than starting a fresh conversation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from omega_prime.cron.scheduler import JobStore

#: Job kind marker stored on heartbeat jobs.
HEARTBEAT_KIND = "session_heartbeat"


def schedule_heartbeat(
    store: JobStore,
    session: str,
    prompt: str,
    *,
    interval_seconds: int | float,
    due_at: int | float,
) -> str:
    """Schedule a recurring heartbeat that re-enters ``session`` with ``prompt``.

    Heartbeats are interval jobs (a heartbeat with no interval is a single
    reminder, not a heartbeat). Returns the job id.
    """
    if not isinstance(session, str) or not session.strip():
        raise ValueError("session must be a non-empty string")
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a heartbeat needs a positive interval_seconds")
    job_id = store.schedule(prompt, due_at, interval_seconds=interval_seconds)
    for job in store.jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            job["kind"] = HEARTBEAT_KIND
            job["session"] = session
            break
    store._save()
    return job_id


def list_heartbeats(store: JobStore) -> list[dict]:
    """All heartbeat jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == HEARTBEAT_KIND
    ]


def clear_heartbeat(store: JobStore, job_id: str) -> dict:
    """Remove one heartbeat by id. Unknown id is a structured result."""
    for index, job in enumerate(store.jobs):
        if (
            isinstance(job, dict)
            and job.get("id") == job_id
            and job.get("kind") == HEARTBEAT_KIND
        ):
            store.jobs.pop(index)
            store._save()
            return {"cleared": job_id}
    return {"error": f"no heartbeat job: {job_id!r}"}


def heartbeat_runner(session_runner: Any) -> Any:
    """Build a ``runner(prompt)`` that re-enters the job's named session.

    ``session_runner(session, prompt)`` runs one turn in the named session
    and returns its final response. The JobStore tick passes only the prompt,
    so the session is bound through the job dict at tick time by
    :func:`tick_heartbeats`.
    """

    def runner(session: str, prompt: str) -> Any:
        return session_runner(session, prompt)

    return runner


def tick_heartbeats(
    store: JobStore, now: int | float, session_runner: Any
) -> list[dict]:
    """Run each due heartbeat, re-entering its named session.

    Like :meth:`JobStore.tick` but the runner receives the job's ``session``
    so the turn re-enters the named session. Only heartbeat jobs are considered.
    """
    ran: list[dict] = []
    for job in store.jobs:
        if job.get("kind") != HEARTBEAT_KIND or job.get("completed"):
            continue
        if job["due_at"] > now:
            continue
        history = job.setdefault("history", [])
        if job.get("claimed_at") == now:
            continue
        if any(isinstance(e, dict) and e.get("ran_at") == now for e in history):
            continue
        job["claimed_at"] = now
        store._save()
        job["last_result"] = session_runner(job.get("session", ""), job["prompt"])
        job["last_ran_at"] = now
        history.append({"ran_at": now, "result": job["last_result"]})
        del history[:-20]
        job["claimed_at"] = None
        job["due_at"] = now + job["interval_seconds"]
        ran.append(job)
    if ran:
        store._save()
    return ran


#: Job kind marker stored on RoE expiry watcher jobs.
ROE_EXPIRY_KIND = "roe_expiry"

#: Default watcher cadence: once a day (seconds).
ROE_EXPIRY_INTERVAL_SECONDS = 86400

#: Bounded per-job run history, mirroring :func:`tick_heartbeats`.
ROE_EXPIRY_HISTORY_LIMIT = 20


def schedule_roe_expiry(
    store: JobStore,
    artifact_path: str,
    *,
    interval_seconds: int | float = ROE_EXPIRY_INTERVAL_SECONDS,
    due_at: int | float,
) -> str:
    """Schedule a recurring RoE expiry watcher for ``artifact_path``.

    Mirrors :func:`schedule_heartbeat`: validates a non-empty artifact
    path and a positive interval, then stamps ``kind`` + ``artifact_path``
    on the stored job. Returns the job id. The default cadence is daily.
    """
    if not isinstance(artifact_path, str) or not artifact_path.strip():
        raise ValueError("artifact_path must be a non-empty string")
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("a roe-expiry watcher needs a positive interval_seconds")
    prompt = f"roe-expiry-watch {artifact_path.strip()}"
    job_id = store.schedule(prompt, due_at, interval_seconds=interval_seconds)
    for job in store.jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            job["kind"] = ROE_EXPIRY_KIND
            job["artifact_path"] = artifact_path.strip()
            break
    store._save()
    return job_id


def list_roe_expiries(store: JobStore) -> list[dict]:
    """All RoE expiry watcher jobs, complete or not."""
    return [
        job
        for job in store.jobs
        if isinstance(job, dict) and job.get("kind") == ROE_EXPIRY_KIND
    ]


def clear_roe_expiry(store: JobStore, job_id: str) -> dict:
    """Remove one RoE expiry watcher by id. Unknown id is a structured result."""
    for index, job in enumerate(store.jobs):
        if (
            isinstance(job, dict)
            and job.get("id") == job_id
            and job.get("kind") == ROE_EXPIRY_KIND
        ):
            store.jobs.pop(index)
            store._save()
            return {"cleared": job_id}
    return {"error": f"no roe-expiry job: {job_id!r}"}


def _roe_now(now: Any) -> tuple[Any, float, str]:
    """Normalize ``now`` to ``(checker_now, stamp, day)``.

    Accepts a UTC datetime (hermetic clock) or a unix timestamp; the job
    record only ever stores numeric stamps (JSON-safe), while the checker
    always receives a datetime.
    """
    if isinstance(now, datetime):
        current = now
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        else:
            current = current.astimezone(UTC)
    else:
        current = datetime.fromtimestamp(now, tz=UTC)
    return current, current.timestamp(), current.date().isoformat()


def tick_roe_expiry(store: JobStore, now: Any, checker: Any) -> list[dict]:
    """Run each due RoE expiry watcher through ``checker``.

    Mirrors :func:`tick_heartbeats`: iterates only ``ROE_EXPIRY_KIND``
    non-completed jobs due at ``now``, claims via ``claimed_at``/``history``
    (``ran_at == now`` ⇒ skip), calls ``checker(artifact_path, now)``,
    records ``last_result``/``last_ran_at``, appends bounded history (cap
    20, ``del history[:-20]``), advances ``due_at = now + interval``,
    ``_save()``. Returns only the jobs that *fired* — ``valid`` ticks are
    silent bookkeeping, and each threshold (``expiring-7d`` / ``expiring-1d``
    / ``expired``) fires at most once per UTC calendar day per job, tracked
    as ``job["last_fired"] = {threshold: "YYYY-MM-DD"}``. Pass
    ``skip_kind=ROE_EXPIRY_KIND`` to the generic ``JobStore.tick`` driver
    so it leaves these jobs to this tick. Never calls ``transition()``.
    """
    checker_now, stamp, today = _roe_now(now)
    fired: list[dict] = []
    ran_any = False
    for job in store.jobs:
        if not isinstance(job, dict):
            continue
        if job.get("kind") != ROE_EXPIRY_KIND or job.get("completed"):
            continue
        due_at = job.get("due_at")
        if due_at is None or due_at > stamp:
            continue
        history = job.setdefault("history", [])
        if job.get("claimed_at") == stamp:
            continue
        if any(isinstance(e, dict) and e.get("ran_at") == stamp for e in history):
            continue
        job["claimed_at"] = stamp
        store._save()
        job["last_result"] = checker(job.get("artifact_path", ""), checker_now)
        job["last_ran_at"] = stamp
        history.append({"ran_at": stamp, "result": job["last_result"]})
        del history[:-ROE_EXPIRY_HISTORY_LIMIT]
        job["claimed_at"] = None
        job["due_at"] = stamp + job["interval_seconds"]
        ran_any = True
        result = job["last_result"]
        state = result.get("state") if isinstance(result, dict) else None
        if state in ("expiring-7d", "expiring-1d", "expired"):
            last_fired = job.setdefault("last_fired", {})
            if last_fired.get(state) != today:
                last_fired[state] = today
                fired.append(job)
    if ran_any:
        store._save()
    return fired


__all__ = [
    "HEARTBEAT_KIND",
    "ROE_EXPIRY_HISTORY_LIMIT",
    "ROE_EXPIRY_INTERVAL_SECONDS",
    "ROE_EXPIRY_KIND",
    "clear_heartbeat",
    "clear_roe_expiry",
    "heartbeat_runner",
    "list_heartbeats",
    "list_roe_expiries",
    "schedule_heartbeat",
    "schedule_roe_expiry",
    "tick_heartbeats",
    "tick_roe_expiry",
]

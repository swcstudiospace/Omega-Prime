# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Register the cron admin tool family on one registry.

An admin surface over the scheduler's own job store — the
``root/cron/jobs.json`` file :class:`~omega_prime.cron.scheduler.JobStore`
owns and the heartbeat family, the heartbeat runtime and the desk driver all
resolve. Every handler opens a fresh store (a new ``JobStore`` reloads what
another process wrote) and persists only through the store's own atomic
save, so a concurrent tick is reloaded, never clobbered: there is no second
store and no second writer path. Creation for a kind goes through the same
scheduler functions that own that kind (``schedule_desk_lead_pass``,
``schedule_heartbeat``, ``JobStore.schedule`` for plain prompt jobs).
``omega_prime/contracts/tool-rosters/omega-prime.yaml`` lists this family.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.cron.heartbeat import HEARTBEAT_KIND, schedule_heartbeat
from omega_prime.cron.scheduler import DESK_LEAD_KIND, JobStore, schedule_desk_lead_pass
from omega_prime.prime.types import reject_extra
from omega_prime.tools.registry import ToolRegistry

CRON_ADMIN_TOOL_NAMES = (
    "cron_jobs_list",
    "cron_job_create",
    "cron_job_remove",
)

PROMPT_KIND = "prompt"
"""Kind label for plain prompt jobs dispatched by ``run_due_jobs``.

The store itself leaves these with no ``kind`` key (``JobStore.schedule``
never sets one); ``cron_jobs_list`` reports them as ``"prompt"`` and
``cron_job_create`` stores them the same way, because that is the
representation the production cron tick reads.
"""

_KNOWN_KINDS = (PROMPT_KIND, DESK_LEAD_KIND, HEARTBEAT_KIND)

_WRITE_TOOLS = frozenset({"cron_job_create", "cron_job_remove"})


def register_cron_admin_tools(registry: ToolRegistry, root: Any) -> list[str]:
    """Register the cron admin family over ``root/cron/jobs.json``."""
    path = Path(root, "cron", "jobs.json").expanduser().resolve()

    def cron_jobs_list(**extra: Any) -> dict:
        reject_extra(extra, what="cron_jobs_list")
        store, failure = _open(path)
        if failure is not None or store is None:
            return failure or {"error": "cron store unreadable"}
        jobs = [_row(job) for job in store.jobs if isinstance(job, dict)]
        return {"jobs": jobs, "count": len(jobs)}

    def cron_job_create(
        kind: Any = PROMPT_KIND,
        prompt: Any = None,
        due_at: Any = None,
        interval_seconds: Any = None,
        session: Any = None,
        intake_path: Any = None,
        limit: Any = None,
        **extra: Any,
    ) -> dict:
        reject_extra(extra, what="cron_job_create")
        if kind not in _KNOWN_KINDS:
            return {
                "error": f"unsupported cron kind {kind!r}",
                "reason": f"supported kinds: {', '.join(_KNOWN_KINDS)}",
            }
        if not isinstance(prompt, str) or not prompt.strip():
            return {"error": "prompt must be a non-empty string"}
        if due_at is None:
            fire_at: int | float = time.time()
        elif isinstance(due_at, bool) or not isinstance(due_at, (int, float)):
            return {"error": "due_at must be a number (unix seconds)"}
        else:
            fire_at = due_at
        if interval_seconds is None:
            interval: int | float | None = None
        elif (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or interval_seconds <= 0
        ):
            return {"error": "interval_seconds must be a positive number or omitted"}
        else:
            interval = interval_seconds
        if kind in (DESK_LEAD_KIND, HEARTBEAT_KIND) and interval is None:
            return {"error": f"a {kind} job needs a positive interval_seconds"}
        if kind == HEARTBEAT_KIND:
            if not isinstance(session, str) or not session.strip():
                return {"error": "a session_heartbeat job needs a non-empty session"}
        elif session is not None:
            return {"error": f"a {kind} job does not take session"}
        if kind == DESK_LEAD_KIND:
            if intake_path is not None and not isinstance(intake_path, str):
                return {"error": "intake_path must be a path string or omitted"}
            if limit is None:
                batch_limit = 5
            elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                return {"error": "limit must be a positive integer or omitted"}
            else:
                batch_limit = limit
        else:
            if intake_path is not None:
                return {"error": f"a {kind} job does not take intake_path"}
            if limit is not None:
                return {"error": f"a {kind} job does not take limit"}
        store, failure = _open(path)
        if failure is not None or store is None:
            return failure or {"error": "cron store unreadable"}
        try:
            if kind == DESK_LEAD_KIND:
                if interval is None:
                    return {
                        "error": "a desk_lead_pass job needs a positive interval_seconds"
                    }
                job_id = schedule_desk_lead_pass(
                    store,
                    due_at=fire_at,
                    interval_seconds=interval,
                    intake_path=intake_path,
                    limit=batch_limit,
                    prompt=prompt,
                )
            elif kind == HEARTBEAT_KIND:
                if interval is None:
                    return {
                        "error": "a session_heartbeat job needs a positive interval_seconds"
                    }
                job_id = schedule_heartbeat(
                    store,
                    session,
                    prompt,
                    interval_seconds=interval,
                    due_at=fire_at,
                )
            else:
                job_id = store.schedule(prompt, fire_at, interval_seconds=interval)
        except (OSError, TypeError, ValueError) as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {
            "created": job_id,
            "kind": kind,
            "due_at": fire_at,
            "interval_seconds": interval,
        }

    def cron_job_remove(job_id: Any = None, **extra: Any) -> dict:
        reject_extra(extra, what="cron_job_remove")
        if not isinstance(job_id, str) or not job_id:
            return {"error": "job_id must be a non-empty string"}
        store, failure = _open(path)
        if failure is not None or store is None:
            return failure or {"error": "cron store unreadable"}
        kind = next(
            (
                job.get("kind") or PROMPT_KIND
                for job in store.jobs
                if isinstance(job, dict) and job.get("id") == job_id
            ),
            PROMPT_KIND,
        )
        if not store.remove(job_id):
            return {"error": f"no cron job: {job_id!r}"}
        return {"removed": job_id, "kind": kind}

    handlers: dict[str, Callable[..., Any]] = {
        "cron_jobs_list": cron_jobs_list,
        "cron_job_create": cron_job_create,
        "cron_job_remove": cron_job_remove,
    }
    if tuple(handlers) != CRON_ADMIN_TOOL_NAMES:
        raise RuntimeError(
            "cron admin tool handlers drifted from CRON_ADMIN_TOOL_NAMES"
        )
    for name in CRON_ADMIN_TOOL_NAMES:
        description, parameters = _SCHEMAS[name]
        registry.register(
            name,
            description,
            parameters,
            handlers[name],
            requires_approval=name in _WRITE_TOOLS,
        )
    return list(CRON_ADMIN_TOOL_NAMES)


def _open(path: Path) -> tuple[JobStore | None, dict | None]:
    """Open a fresh store, or an explicit failure dict. Never raises."""
    try:
        return JobStore(path), None
    except (OSError, ValueError) as exc:
        return None, {"error": f"cron store unreadable at {path}: {exc}"}


def _row(job: dict) -> dict:
    """One list row from the store's own fields."""
    completed = bool(job.get("completed"))
    return {
        "id": job.get("id"),
        "kind": job.get("kind") or PROMPT_KIND,
        "prompt": job.get("prompt"),
        "due_at": job.get("due_at"),
        "interval_seconds": job.get("interval_seconds"),
        "completed": completed,
        # The tick fires a job whose due_at is at or before now and skips
        # completed ones, so next due is the stored due_at until completion.
        "next_due_at": None if completed else job.get("due_at"),
        "last_ran_at": job.get("last_ran_at"),
    }


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "cron_jobs_list": (
        "List every job in the scheduler's store (root/cron/jobs.json): id, "
        "kind, prompt, schedule and interval fields, next due, completion.",
        _object({}, []),
    ),
    "cron_job_create": (
        "Create one scheduled job in the scheduler's own store. Kinds are "
        "what the scheduler's drivers actually run: 'prompt' (run by the "
        "model-driven tick; one-shot without interval_seconds), "
        "'desk_lead_pass' (recurring desk pass; needs positive "
        "interval_seconds, optional intake_path and limit) and "
        "'session_heartbeat' (recurring; needs session and positive "
        "interval_seconds).",
        _object(
            {
                "kind": {
                    "type": "string",
                    "enum": list(_KNOWN_KINDS),
                    "description": "Job kind; defaults to 'prompt'.",
                },
                "prompt": _string("The prompt the job carries and runs when due."),
                "due_at": {
                    "type": "number",
                    "description": "First fire time (unix seconds); defaults to now.",
                },
                "interval_seconds": {
                    "type": "number",
                    "description": "Seconds between fires; omit for a one-shot "
                    "prompt job. Required positive for desk_lead_pass and "
                    "session_heartbeat.",
                },
                "session": _string(
                    "Named session to re-enter (session_heartbeat only)."
                ),
                "intake_path": _string(
                    "Shared IntakeStore path (desk_lead_pass only; default resolved)."
                ),
                "limit": {
                    "type": "integer",
                    "description": "Ticket batch limit per pass "
                    "(desk_lead_pass only; default 5).",
                },
            },
            ["prompt"],
        ),
    ),
    "cron_job_remove": (
        "Remove one scheduled job by id from the scheduler's store.",
        _object({"job_id": _string("The job id to remove.")}, ["job_id"]),
    ),
}

__all__ = ["CRON_ADMIN_TOOL_NAMES", "PROMPT_KIND", "register_cron_admin_tools"]

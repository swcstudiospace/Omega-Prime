"""Typed workflow jobs, matching policy, and the finite scaler vocabulary.

Inputs (what happened) and decisions (what to do) are frozen dataclasses so
the pure scaler can be driven and asserted without any I/O.
"""

from __future__ import annotations

import enum
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from omega_prime.ci_runners.settings import RUNNER_PREFIX


class PayloadError(ValueError):
    """A verified webhook payload does not have the documented shape."""


class JobStatus(enum.IntEnum):
    """Monotonic job lifecycle; a lower status never overwrites a higher one."""

    QUEUED = 1
    IN_PROGRESS = 2
    COMPLETED = 3


_ACTIONS = {
    "queued": JobStatus.QUEUED,
    "in_progress": JobStatus.IN_PROGRESS,
    "completed": JobStatus.COMPLETED,
}


@dataclass(frozen=True)
class WorkflowJob:
    job_id: int
    run_id: int
    repo: str
    private: bool
    labels: tuple[str, ...]
    status: JobStatus
    runner_name: str | None = None
    runner_id: int | None = None


def _int_field(obj: Mapping[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PayloadError(f"workflow_job.{key} must be a positive integer")
    return value


def parse_workflow_job(payload: Mapping[str, Any]) -> WorkflowJob | None:
    """Parse a verified ``workflow_job`` payload. ``waiting`` and unknown → None."""
    action = payload.get("action")
    if not isinstance(action, str):
        raise PayloadError("action must be a string")
    status = _ACTIONS.get(action)
    if status is None:
        return None
    job = payload.get("workflow_job")
    repo = payload.get("repository")
    if not isinstance(job, Mapping) or not isinstance(repo, Mapping):
        raise PayloadError("workflow_job and repository objects are required")
    full_name = repo.get("full_name")
    if not isinstance(full_name, str) or "/" not in full_name:
        raise PayloadError("repository.full_name must be owner/repo")
    labels_raw = job.get("labels")
    if not isinstance(labels_raw, list) or not all(
        isinstance(label, str) for label in labels_raw
    ):
        raise PayloadError("workflow_job.labels must be a list of strings")
    runner_name = job.get("runner_name")
    if runner_name is not None and not isinstance(runner_name, str):
        raise PayloadError("workflow_job.runner_name must be a string or null")
    runner_id = job.get("runner_id")
    if runner_id is not None and (
        isinstance(runner_id, bool) or not isinstance(runner_id, int)
    ):
        raise PayloadError("workflow_job.runner_id must be an integer or null")
    # Fail closed: only an explicit private=true repository is private.
    private = repo.get("private") is True and repo.get("visibility", "private") in (
        "private",
        "internal",
    )
    return WorkflowJob(
        job_id=_int_field(job, "id"),
        run_id=_int_field(job, "run_id"),
        repo=full_name.lower(),
        private=private,
        labels=tuple(label.strip().lower() for label in labels_raw),
        status=status,
        runner_name=runner_name or None,
        runner_id=runner_id or None,
    )


def matches(
    job: WorkflowJob, pool_labels: Collection[str], repos: Collection[str]
) -> bool:
    """A job is ours iff it runs in a private allowed repo and asks only for
    labels the pool carries (labels ⊆ pool). An empty allowlist means every
    repo the runner group admits; public repos are always refused."""
    if not job.private or not job.labels:
        return False
    if repos and job.repo not in repos:
        return False
    return set(job.labels) <= set(pool_labels)


def is_pool_runner(name: str | None) -> bool:
    return bool(name) and str(name).startswith(RUNNER_PREFIX)


class RunnerState(enum.Enum):
    STARTING = "starting"  # reserved; JIT minted / launch in flight
    IDLE = "idle"  # agent accepted; waiting for a job
    BUSY = "busy"  # GitHub assigned a job


@dataclass
class Runner:
    name: str
    generation: str
    state: RunnerState
    runner_id: int | None = None
    job_id: int | None = None


# --- input events -----------------------------------------------------------


@dataclass(frozen=True)
class JobEvent:
    job: WorkflowJob


@dataclass(frozen=True)
class LaunchSucceeded:
    name: str
    generation: str
    runner_id: int | None = None


@dataclass(frozen=True)
class LaunchFailed:
    """``rejected``: the agent definitively did not accept (safe to roll back).
    ``capacity``: the agent answered 429. ``rejected=False`` is ambiguous."""

    name: str
    generation: str
    reason: str
    rejected: bool = True
    capacity: bool = False
    runner_id: int | None = None


@dataclass(frozen=True)
class RunnerExited:
    name: str
    generation: str
    exit_code: int


@dataclass(frozen=True)
class IdleDeadline:
    """One-shot delayed event. ``generation`` is the opaque arm nonce."""

    name: str
    generation: str


@dataclass(frozen=True)
class ObservedRunner:
    """A pool runner as GitHub lists it (startup reconcile / idle guard)."""

    name: str
    runner_id: int
    busy: bool
    online: bool


@dataclass(frozen=True)
class AgentRunner:
    """A sanitized agent inventory row."""

    name: str
    generation: str
    status: str
    pid: int | None


@dataclass(frozen=True)
class ControllerStarted:
    github: tuple[ObservedRunner, ...] = ()
    agent: tuple[AgentRunner, ...] = ()


InputEvent = (
    JobEvent
    | LaunchSucceeded
    | LaunchFailed
    | RunnerExited
    | IdleDeadline
    | ControllerStarted
)


# --- decisions ----------------------------------------------------------------


@dataclass(frozen=True)
class Launch:
    name: str
    generation: str
    job_id: int


@dataclass(frozen=True)
class Reap:
    name: str
    generation: str
    runner_id: int | None
    delete_github: bool = True
    stop_agent: bool = True


@dataclass(frozen=True)
class ScheduleIdle:
    name: str
    generation: str
    delay_s: float


@dataclass(frozen=True)
class CancelIdle:
    name: str
    generation: str


Decision = Launch | Reap | ScheduleIdle | CancelIdle


@dataclass(frozen=True)
class DomainEvent:
    """A fact for the event feed. ``kind`` is the subject suffix."""

    kind: str
    data: Mapping[str, Any]


__all__ = [
    "AgentRunner",
    "CancelIdle",
    "ControllerStarted",
    "Decision",
    "DomainEvent",
    "IdleDeadline",
    "InputEvent",
    "JobEvent",
    "JobStatus",
    "Launch",
    "LaunchFailed",
    "LaunchSucceeded",
    "ObservedRunner",
    "PayloadError",
    "Reap",
    "Runner",
    "RunnerExited",
    "RunnerState",
    "ScheduleIdle",
    "WorkflowJob",
    "is_pool_runner",
    "matches",
    "parse_workflow_job",
]

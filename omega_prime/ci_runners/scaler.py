"""Pure, event-sourced runner scaler. No I/O, no clock, no scheduling.

``Scaler.handle(event)`` folds one input event into the ledger and returns the
decisions (Launch / Reap / ScheduleIdle / CancelIdle) plus domain events for
the feed. The controller interprets both; this module never publishes, never
sleeps, and never touches the network.

Capacity (DESIGN §4):
    demand = min(max_runners, queued + busy) - (starting + idle + busy)
    launch = max(0, min(demand, max_runners - tracked))
where ``tracked`` counts every ledger entry, including FINISHING (job done,
agent slot not yet confirmed free) and REAPING (cleanup in flight), so live
runners can never exceed ``max_runners``. A reservation is released exactly
once: on a rejected launch, the agent's exit callback, or a reap whose
outcome confirms the child is gone. A completed job only moves its runner to
FINISHING; a one-shot deadline then stops it if the exit callback never comes.

Launch failures (a rejected launch, or a runner exiting before it took a job,
whatever its exit code: actions/runner exits 0 on fatal startup errors such as
an unreadable work directory) back off: ``hold="failure"`` plus one ScheduleRetry of
``RETRY_BASE_S * 2**(n-1)`` (capped at ``RETRY_CAP_S``). New demand does not
clear that hold; the retry deadline or a runner taking a job does. After
``MAX_CONSECUTIVE_FAILURES`` the circuit opens (``hold="circuit"``, no more
automatic retries) until a pool runner takes a job or the controller restarts.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Literal

from omega_prime.ci_runners.agent.jobhook import parse_repo_patterns, patterns_cover
from omega_prime.ci_runners.jobs import (
    CancelIdle,
    ControllerStarted,
    Decision,
    DomainEvent,
    IdleDeadline,
    InputEvent,
    JobEvent,
    JobStatus,
    Launch,
    LaunchFailed,
    LaunchSucceeded,
    Reap,
    ReapFinished,
    ReapOutcome,
    RetryDeadline,
    Runner,
    RunnerExited,
    RunnerState,
    ScheduleIdle,
    ScheduleRetry,
    WorkflowJob,
    is_pool_runner,
    matches,
)
from omega_prime.ci_runners.settings import HARD_MAX_RUNNERS, RUNNER_PREFIX

JOB_MEMORY = 4096
RETRY_BASE_S = 15.0
RETRY_CAP_S = 240.0
MAX_CONSECUTIVE_FAILURES = 5
RETRY_KEY = "omega-ci-launch-retry"  # DelayedEvents key for the backoff arm
_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


@dataclass(frozen=True)
class Step:
    decisions: tuple[Decision, ...] = ()
    events: tuple[DomainEvent, ...] = ()


def _new_generation() -> str:
    return uuid.uuid4().hex


def _new_suffix() -> str:
    raw = uuid.uuid4().int
    return "".join(_SUFFIX_ALPHABET[(raw >> (6 * i)) % 36] for i in range(4))


@dataclass
class _Out:
    decisions: list[Decision] = field(default_factory=list)
    events: list[DomainEvent] = field(default_factory=list)

    def event(self, kind: str, **data: object) -> None:
        self.events.append(DomainEvent(kind, data))

    def step(self) -> Step:
        return Step(tuple(self.decisions), tuple(self.events))


class Scaler:
    """Deterministic given its injected id sources."""

    def __init__(
        self,
        *,
        labels: Collection[str],
        repos: Collection[str] = (),
        public_repos: Collection[str] = (),
        max_runners: int = 8,
        idle_grace_s: float = 180.0,
        new_generation: Callable[[], str] = _new_generation,
        new_suffix: Callable[[], str] = _new_suffix,
    ) -> None:
        if not 1 <= max_runners <= HARD_MAX_RUNNERS:
            raise ValueError(f"max_runners must be 1..{HARD_MAX_RUNNERS}")
        self.labels = frozenset(labels)
        self.repos = parse_repo_patterns(repos)
        self.public_repos = parse_repo_patterns(public_repos)
        if self.public_repos and not (
            self.repos and patterns_cover(self.public_repos, self.repos)
        ):
            raise ValueError("public_repos must be covered by a non-empty repos")
        self.max_runners = max_runners
        self.idle_grace_s = idle_grace_s
        self._new_generation = new_generation
        self._new_suffix = new_suffix
        self.runners: dict[str, Runner] = {}
        self.queued: OrderedDict[int, WorkflowJob] = OrderedDict()
        self._jobs: OrderedDict[int, JobStatus] = OrderedDict()
        self._covered: set[int] = set()
        self._armed: dict[str, str] = {}
        self.hold: Literal["failure", "capacity", "circuit"] | None = None
        self.failures = 0  # consecutive launch failures (reset by a busy runner)
        self._retry_arm: str | None = None
        self.started = False

    # -- queries -------------------------------------------------------------

    def count(self, state: RunnerState) -> int:
        return sum(1 for r in self.runners.values() if r.state is state)

    def registered(self, name: str, generation: str) -> bool:
        """Is (name, generation) a live runner this controller launched?"""
        runner = self.runners.get(name)
        return (
            bool(generation)
            and runner is not None
            and (runner.generation == generation)
        )

    def armed(self, name: str) -> str | None:
        return self._armed.get(name)

    def wanted(self) -> int:
        starting = self.count(RunnerState.STARTING)
        idle = self.count(RunnerState.IDLE)
        busy = self.count(RunnerState.BUSY)
        target = min(self.max_runners, len(self.queued) + busy)
        free_slots = self.max_runners - len(self.runners)
        return max(0, min(target - (starting + idle + busy), free_slots))

    @staticmethod
    def backoff(failures: int) -> float:
        return min(RETRY_CAP_S, RETRY_BASE_S * 2 ** max(0, failures - 1))

    # -- the fold ------------------------------------------------------------

    def handle(self, event: InputEvent) -> Step:
        out = _Out()
        if isinstance(event, JobEvent):
            self._on_job(event.job, out)
        elif isinstance(event, LaunchSucceeded):
            self._on_launched(event, out)
        elif isinstance(event, LaunchFailed):
            self._on_launch_failed(event, out)
        elif isinstance(event, RunnerExited):
            self._on_exited(event, out)
        elif isinstance(event, IdleDeadline):
            self._on_deadline(event, out)
        elif isinstance(event, RetryDeadline):
            self._on_retry(event, out)
        elif isinstance(event, ReapFinished):
            self._on_reap_finished(event, out)
        elif isinstance(event, ControllerStarted):
            self._on_started(event, out)
        else:
            raise TypeError(f"unknown input event {type(event).__name__}")
        self._settle(out)
        return out.step()

    # -- handlers --------------------------------------------------------------

    def _remember(self, job_id: int, status: JobStatus) -> None:
        self._jobs[job_id] = status
        self._jobs.move_to_end(job_id)
        while len(self._jobs) > JOB_MEMORY:
            self._jobs.popitem(last=False)

    def _dequeue(self, job_id: int) -> None:
        self.queued.pop(job_id, None)
        self._covered.discard(job_id)

    def _on_job(self, job: WorkflowJob, out: _Out) -> None:
        previous = self._jobs.get(job.job_id)
        if previous is not None and previous >= job.status:
            return  # duplicate or late (monotonic)
        ours = job.runner_name in self.runners if job.runner_name else False
        relevant = (
            matches(job, self.labels, self.repos, self.public_repos)
            or ours
            or is_pool_runner(job.runner_name)
            or job.job_id in self.queued
        )
        if not relevant:
            return
        self._remember(job.job_id, job.status)
        if job.status is JobStatus.QUEUED:
            # New demand never clears a failure hold: that is what turned a
            # broken launch path into an unbounded relaunch loop.
            if len(self.queued) >= JOB_MEMORY:
                out.event("error", reason="queue_full", job_id=job.job_id)
                return
            self.queued[job.job_id] = job
            out.event(
                "demand", job_id=job.job_id, repo=job.repo, queued=len(self.queued)
            )
            return
        self._dequeue(job.job_id)
        if job.status is JobStatus.IN_PROGRESS:
            self._on_assigned(job, out)
            return
        # COMPLETED: the ephemeral runner is done with its one job, but its
        # agent slot is free only once the process exits. Keep it tracked
        # (it still counts against capacity) until the exit callback, with a
        # one-shot deadline in case that callback is lost.
        name = job.runner_name
        if name and (name in self.runners or is_pool_runner(name)):
            self._healthy(out)  # a pool runner ran a job: launches work
        if name and name in self.runners:
            runner = self.runners[name]
            if runner.state is not RunnerState.REAPING:
                self._disarm(name, out)
                runner.state = RunnerState.FINISHING
                self._arm(runner, out)
            out.event("finished", name=name, job_id=job.job_id)
        out.event("demand", job_id=job.job_id, repo=job.repo, queued=len(self.queued))

    def _on_assigned(self, job: WorkflowJob, out: _Out) -> None:
        name = job.runner_name
        if name and (name in self.runners or is_pool_runner(name)):
            self._healthy(out)  # a pool runner took a job: launches work
        if name and name in self.runners:
            runner = self.runners[name]
            self._disarm(name, out)
            runner.state = RunnerState.BUSY
            runner.job_id = job.job_id
            runner.runner_id = job.runner_id or runner.runner_id
            out.event("busy", name=name, job_id=job.job_id, repo=job.repo)
        elif name and is_pool_runner(name):
            # A pool runner we lost track of (restart, ambiguous launch): count it.
            self.runners[name] = Runner(
                name, "", RunnerState.BUSY, job.runner_id, job.job_id
            )
            out.event("busy", name=name, job_id=job.job_id, adopted=True)
        else:
            out.event(
                "demand",
                job_id=job.job_id,
                picked_by="other",
                queued=len(self.queued),
            )

    def _on_launched(self, event: LaunchSucceeded, out: _Out) -> None:
        runner = self.runners.get(event.name)
        if (
            runner is None
            or runner.generation != event.generation
            or runner.state is not RunnerState.STARTING
        ):
            return
        runner.state = RunnerState.IDLE
        runner.runner_id = event.runner_id or runner.runner_id
        out.event("launched", name=event.name, runner_id=runner.runner_id)

    def _on_launch_failed(self, event: LaunchFailed, out: _Out) -> None:
        runner = self.runners.get(event.name)
        if (
            runner is None
            or runner.generation != event.generation
            or runner.state is not RunnerState.STARTING
        ):
            return
        runner_id = event.runner_id or runner.runner_id
        if event.rejected:
            del self.runners[event.name]
            self._failed(out, capacity=event.capacity)
            out.event(
                "error",
                name=event.name,
                reason=event.reason,
                capacity=event.capacity,
                released=True,
            )
            if runner_id is not None:
                # Roll back the unused registration; the controller deletes it
                # only after GitHub confirms the runner is idle.
                out.decisions.append(
                    Reap(
                        event.name,
                        event.generation,
                        runner_id,
                        delete_github=True,
                        stop_agent=False,
                    )
                )
            return
        # Ambiguous: the child may be running. Keep it (it holds capacity) and
        # let the normal idle path reap it once GitHub says it is idle.
        runner.state = RunnerState.IDLE
        runner.runner_id = runner_id
        self._failed(out)
        out.event("error", name=event.name, reason=event.reason, ambiguous=True)

    def _on_exited(self, event: RunnerExited, out: _Out) -> None:
        if not self.registered(event.name, event.generation):
            return
        runner = self.runners[event.name]
        # Any exit before a job is a failed launch, exit 0 included: the
        # runner's run-helper exits 0 even on terminal errors (an unreadable
        # work directory, a rejected JIT config), which is how a broken image
        # once relaunched in a hot loop (00055). A runner that did take a job
        # but whose in_progress webhook is late is corrected by that webhook
        # (or the completed one), which resets the failure count.
        early = runner.state in (RunnerState.STARTING, RunnerState.IDLE)
        self._release(event.name, out)
        out.event("exited", name=event.name, exit_code=event.exit_code)
        if early:
            out.event(
                "error",
                name=event.name,
                reason="exited_before_job",
                exit_code=event.exit_code,
            )
            self._failed(out)

    def _on_deadline(self, event: IdleDeadline, out: _Out) -> None:
        if self._armed.get(event.name) != event.generation:
            return  # stale or cancelled arm
        del self._armed[event.name]
        runner = self.runners.get(event.name)
        if runner is None:
            return
        if runner.state is RunnerState.FINISHING:
            # The exit callback never came: stop the child, keep the slot
            # until the agent confirms.
            runner.state = RunnerState.REAPING
            out.decisions.append(
                Reap(runner.name, runner.generation, None, delete_github=False)
            )
            out.event("reaped", name=runner.name, reason="finish_deadline")
            return
        if runner.state is not RunnerState.IDLE or self.queued:
            return
        runner.state = RunnerState.REAPING  # tracked until ReapFinished
        out.decisions.append(Reap(runner.name, runner.generation, runner.runner_id))
        out.event("reaped", name=runner.name, reason="idle_deadline")

    def _on_reap_finished(self, event: ReapFinished, out: _Out) -> None:
        runner = self.runners.get(event.name)
        if (
            runner is None
            or runner.generation != event.generation
            or runner.state is not RunnerState.REAPING
        ):
            return  # rollback / orphan reaps are untracked by design
        if event.outcome is ReapOutcome.DONE:
            self._release(event.name, out)
            out.event("exited", name=event.name, reason="reaped")
        elif event.outcome is ReapOutcome.GITHUB_FAILED:
            runner.state = RunnerState.IDLE  # nothing changed; settle re-arms
        elif event.outcome is ReapOutcome.BUSY:
            runner.state = RunnerState.BUSY  # it took a job after all
        else:  # AGENT_FAILED: deregistered, child may still run; retry later
            runner.state = RunnerState.FINISHING
            self._arm(runner, out)
        if event.outcome is not ReapOutcome.DONE:
            out.event("error", name=event.name, reason=f"reap_{event.outcome.value}")

    def _on_retry(self, event: RetryDeadline, out: _Out) -> None:
        if event.name != RETRY_KEY or event.generation != self._retry_arm:
            return
        self._retry_arm = None
        if self.hold in ("failure", "capacity"):
            self.hold = None
            out.event("launch_retry", failures=self.failures)

    def _on_started(self, event: ControllerStarted, out: _Out) -> None:
        if self.started:
            return
        self.started = True
        github = {r.name: r for r in event.github if is_pool_runner(r.name)}
        agent_names = set()
        for row in event.agent:
            if not is_pool_runner(row.name):
                continue
            agent_names.add(row.name)
            observed = github.get(row.name)
            if observed is None:
                out.decisions.append(
                    Reap(row.name, row.generation, None, delete_github=False)
                )
                out.event("reaped", name=row.name, reason="orphan_process")
                continue
            state = RunnerState.BUSY if observed.busy else RunnerState.IDLE
            self.runners[row.name] = Runner(
                row.name, row.generation, state, observed.runner_id
            )
        for name, observed in github.items():
            if name in agent_names:
                continue
            if observed.busy:
                self.runners[name] = Runner(
                    name, "", RunnerState.BUSY, observed.runner_id
                )
                continue
            out.decisions.append(
                Reap(name, "", observed.runner_id, delete_github=True, stop_agent=False)
            )
            out.event("reaped", name=name, reason="orphan_registration")
        out.event(
            "idle",
            reconciled=True,
            runners=len(self.runners),
            busy=self.count(RunnerState.BUSY),
        )
        for job in event.queued:  # demand accepted before the restart
            if job.status is JobStatus.QUEUED:
                self._on_job(job, out)

    # -- settle ------------------------------------------------------------------

    def _arm(self, runner: Runner, out: _Out) -> None:
        arm = f"{runner.generation or 'adopted'}.{self._new_generation()}"
        self._armed[runner.name] = arm
        out.decisions.append(ScheduleIdle(runner.name, arm, self.idle_grace_s))

    def _release(self, name: str, out: _Out) -> None:
        """Drop a reservation whose agent slot is confirmed free."""
        del self.runners[name]
        self._disarm(name, out)
        if self.hold == "capacity":
            self.hold = None

    def _failed(self, out: _Out, *, capacity: bool = False) -> None:
        if self.hold == "circuit":
            return
        if capacity:
            # The agent is full: wait for a slot to free (an exit) or, if
            # none is ours to free, one bounded retry. Not a health failure.
            self.hold = "capacity"
            self._schedule_retry(RETRY_CAP_S, out)
            return
        self.failures += 1
        if self.failures >= MAX_CONSECUTIVE_FAILURES:
            self.hold = "circuit"
            self._retry_arm = None
            out.event(
                "launch_circuit_open",
                failures=self.failures,
                queued=len(self.queued),
                action="launches stopped until a pool runner takes a job or the"
                " controller restarts",
            )
            return
        self.hold = "failure"
        delay = self.backoff(self.failures)
        self._schedule_retry(delay, out)
        out.event("launch_backoff", failures=self.failures, delay_s=delay)

    def _schedule_retry(self, delay_s: float, out: _Out) -> None:
        arm = self._new_generation()  # a newer arm makes older ones stale
        self._retry_arm = arm
        out.decisions.append(ScheduleRetry(RETRY_KEY, arm, delay_s))

    def _healthy(self, out: _Out) -> None:
        self.failures = 0
        if self.hold in ("failure", "circuit"):
            if self.hold == "circuit":
                out.event("launch_circuit_closed")
            self.hold = None
            self._retry_arm = None

    def _disarm(self, name: str, out: _Out) -> None:
        generation = self._armed.pop(name, None)
        if generation is not None:
            out.decisions.append(CancelIdle(name, generation))

    def _settle(self, out: _Out) -> None:
        if not self.started:
            return
        if self.queued:
            if self.hold is None:
                self._scale_up(out)
            return
        for runner in self.runners.values():
            if runner.state is RunnerState.IDLE and runner.name not in self._armed:
                self._arm(runner, out)
                out.event("idle", name=runner.name, grace_s=self.idle_grace_s)

    def _scale_up(self, out: _Out) -> None:
        for _ in range(self.wanted()):
            uncovered = [j for j in self.queued if j not in self._covered]
            job_id = uncovered[0] if uncovered else next(reversed(self.queued))
            self._covered.add(job_id)
            name = self._name_for(job_id)
            generation = self._new_generation()
            self.runners[name] = Runner(name, generation, RunnerState.STARTING)
            out.decisions.append(Launch(name, generation, job_id))
            out.event("launch_requested", name=name, job_id=job_id)

    def _name_for(self, job_id: int) -> str:
        stem = f"{RUNNER_PREFIX}{job_id}"[: len(RUNNER_PREFIX) + 40]
        for _ in range(64):
            name = f"{stem}-{self._new_suffix()}"
            if name not in self.runners:
                return name
        raise RuntimeError("could not mint a unique runner name")


__all__ = [
    "JOB_MEMORY",
    "MAX_CONSECUTIVE_FAILURES",
    "RETRY_BASE_S",
    "RETRY_CAP_S",
    "RETRY_KEY",
    "Scaler",
    "Step",
]

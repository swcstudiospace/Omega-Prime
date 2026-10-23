"""Serialized controller: one worker folds events through the pure Scaler.

Webhooks and agent callbacks only enqueue. A single worker task applies each
input to the scaler and interprets the returned decisions; slow effects
(JIT + wake/launch, reaps) run as tracked tasks whose outcomes come back as
new input events, so scaler transitions never interleave. Startup performs
exactly one reconcile (one paginated GitHub listing + one agent inventory)
before any queued event is processed. No timers beyond the one-shot idle
deadlines in :mod:`delayed`, no polling.

Every Reap reports its outcome back (``ReapFinished``) so the scaler keeps a
runner tracked until cleanup really happened, and the queue is mirrored to a
:class:`~omega_prime.ci_runners.demand.DemandStore` so a restart replays it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from omega_prime.ci_runners.delayed import AsyncioDelayedEvents, Deadline, DelayedEvents
from omega_prime.ci_runners.demand import DemandStore
from omega_prime.ci_runners.desktop import DesktopClient, DesktopError, LaunchOutcome
from omega_prime.ci_runners.github import GitHubClient, GitHubError
from omega_prime.ci_runners.jobs import (
    AgentRunner,
    CancelIdle,
    ControllerStarted,
    IdleDeadline,
    InputEvent,
    JobEvent,
    Launch,
    LaunchFailed,
    LaunchSucceeded,
    ObservedRunner,
    Reap,
    ReapFinished,
    ReapOutcome,
    RetryDeadline,
    RunnerExited,
    ScheduleIdle,
    ScheduleRetry,
    WorkflowJob,
)
from omega_prime.ci_runners.publisher import (
    CloudEvent,
    EventPublisher,
    UnsafeEventError,
)
from omega_prime.ci_runners.scaler import Scaler, Step

log = logging.getLogger(__name__)

MAX_PENDING_WEBHOOKS = 256


class Controller:
    def __init__(
        self,
        *,
        scaler: Scaler,
        github: GitHubClient,
        desktop: DesktopClient,
        publisher: EventPublisher,
        delayed: DelayedEvents | None = None,
        extra_secrets: Callable[[], tuple[str, ...]] = tuple,
        max_pending: int = MAX_PENDING_WEBHOOKS,
        demand: DemandStore | None = None,
    ) -> None:
        self.scaler = scaler
        self.github = github
        self.desktop = desktop
        self.publisher = publisher
        self._delayed = delayed
        self._extra_secrets = extra_secrets
        self.max_pending = max_pending
        self._queue: asyncio.Queue[tuple[bool, InputEvent]] = asyncio.Queue()
        self._external = 0
        self._worker: asyncio.Task[None] | None = None
        self._effects: set[asyncio.Task[None]] = set()
        self._jits: dict[tuple[str, str], str] = {}
        self._demand = demand
        self._saved_demand: tuple[int, ...] | None = None
        self.ready = asyncio.Event()

    # -- admission (called from HTTP handlers; never blocks) -------------------

    def offer_job(self, job: WorkflowJob) -> bool:
        """Admit a verified webhook job. False (→ 503) when saturated."""
        if self._external >= self.max_pending:
            return False
        self._external += 1
        self._queue.put_nowait((True, JobEvent(job)))
        return True

    def submit(self, event: InputEvent) -> None:
        """Internal events (effect outcomes, deadlines) are always admitted."""
        self._queue.put_nowait((False, event))

    def runner_exited(self, name: str, generation: str, exit_code: int) -> bool:
        """Agent callback. Only a registered (name, generation) is accepted."""
        if not self.scaler.registered(name, generation):
            return False
        self.submit(RunnerExited(name, generation, exit_code))
        return True

    @property
    def pending(self) -> int:
        return self._external

    # -- lifecycle ---------------------------------------------------------------

    async def start(self) -> None:
        if self._worker is not None:
            return
        if self._delayed is None:
            self._delayed = AsyncioDelayedEvents(self._deadline)
        self._worker = asyncio.create_task(self._run(), name="omega-ci-controller")

    async def stop(self) -> None:
        if self._delayed is not None:
            self._delayed.cancel_all()
        tasks = [t for t in (self._worker, *self._effects) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._effects.clear()
        self._jits.clear()
        self._worker = None

    async def settle(self) -> None:
        """Wait until the queue is empty and no effect is in flight (tests/ops)."""
        await self.ready.wait()
        while True:
            await self._queue.join()
            if not self._effects:
                if self._queue.empty():
                    return
                continue
            await asyncio.gather(*list(self._effects), return_exceptions=True)

    def _deadline(self, event: Deadline) -> None:
        self.submit(event)

    async def _run(self) -> None:
        started = await self._snapshot()
        if self._demand is not None:
            started = dataclasses.replace(started, queued=self._demand.load())
        self._apply(self.scaler.handle(started))
        self._persist_demand()
        self.ready.set()
        while True:
            external, event = await self._queue.get()
            try:
                self._apply(self.scaler.handle(event))
                self._persist_demand()
            except Exception:  # keep the worker alive; never log payloads
                log.exception("ci-runners: scaler step failed")
                self._publish("error", {"reason": "scaler_step_failed"})
            finally:
                if external:
                    self._external -= 1
                self._queue.task_done()

    def _persist_demand(self) -> None:
        if self._demand is None:
            return
        ids = tuple(self.scaler.queued)
        if ids == self._saved_demand:
            return
        try:
            self._demand.save(self.scaler.queued.values())
        except OSError as exc:
            # Degrades to the pre-persistence behaviour; never stops the worker.
            self._publish(
                "error", {"reason": "demand_persist", "errno": exc.errno or 0}
            )
            return
        self._saved_demand = ids

    async def _snapshot(self) -> ControllerStarted:
        """The one startup reconcile. A failed side never causes a reap."""
        github: tuple[ObservedRunner, ...] | None
        agent: tuple[AgentRunner, ...] | None
        try:
            github = tuple(
                ObservedRunner(r.name, r.runner_id, r.busy, r.online)
                for r in await self.github.list_pool_runners()
            )
        except GitHubError as exc:
            self._publish("error", {"reason": "reconcile_github", "status": exc.status})
            github = None
        try:
            agent = tuple(await self.desktop.inventory())
        except DesktopError as exc:
            self._publish("error", {"reason": "reconcile_agent", "status": exc.status})
            agent = None
        if github is None and agent is None:
            return ControllerStarted()
        if github is None:
            # Unknown registrations: keep every agent child as busy (never reap).
            assert agent is not None
            github = tuple(ObservedRunner(r.name, 0, True, True) for r in agent)
        if agent is None:
            # Unknown children: mirror registrations so none counts as an orphan
            # process; idle ones then age out through the normal idle path.
            agent = tuple(AgentRunner(o.name, "", "unknown", None) for o in github)
        return ControllerStarted(github=github, agent=agent)

    # -- interpreting decisions --------------------------------------------------

    def _apply(self, step: Step) -> None:
        for event in step.events:
            self._publish(event.kind, event.data)
        for decision in step.decisions:
            if isinstance(decision, Launch):
                self._spawn(self._launch(decision))
            elif isinstance(decision, Reap):
                self._jits.pop((decision.name, decision.generation), None)
                self._spawn(self._reap(decision))
            elif isinstance(decision, ScheduleIdle):
                assert self._delayed is not None
                self._delayed.schedule(
                    decision.name,
                    decision.generation,
                    decision.delay_s,
                    IdleDeadline(decision.name, decision.generation),
                )
            elif isinstance(decision, ScheduleRetry):
                assert self._delayed is not None
                self._delayed.schedule(
                    decision.name,
                    decision.generation,
                    decision.delay_s,
                    RetryDeadline(decision.name, decision.generation),
                )
            elif isinstance(decision, CancelIdle):
                assert self._delayed is not None
                self._delayed.cancel(decision.name, decision.generation)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._effects.add(task)
        task.add_done_callback(self._effects.discard)

    def _secrets(self) -> tuple[str, ...]:
        return (*self._jits.values(), *self.github.secrets(), *self._extra_secrets())

    def _publish(self, kind: str, data: Any) -> None:
        subject = data.get("name") if isinstance(data, dict) else None
        try:
            self.publisher.publish(
                CloudEvent.of(kind, dict(data), subject=subject),
                known_secrets=self._secrets(),
            )
        except (UnsafeEventError, OSError, ValueError) as exc:
            log.warning("ci-runners: event %s dropped (%s)", kind, type(exc).__name__)

    async def _launch(self, d: Launch) -> None:
        try:
            jit = await self.github.generate_jit(d.name)
        except GitHubError as exc:
            self.submit(
                LaunchFailed(d.name, d.generation, f"jit_{exc.status or exc.code}")
            )
            return
        key = (d.name, d.generation)
        self._jits[key] = jit.encoded  # memory-only, dropped below
        try:
            self._publish("wake_requested", {"name": d.name})
            result = await self.desktop.launch(d.name, d.generation, jit.encoded)
            if result.accepted:
                self._publish(
                    "woke",
                    {
                        "name": d.name,
                        "attempts": result.attempts,
                        "elapsed_s": round(result.elapsed_s, 3),
                    },
                )
                self.submit(LaunchSucceeded(d.name, d.generation, jit.runner_id))
                return
            if result.outcome is LaunchOutcome.WAKE_FAILED:
                present = await self.desktop.find(d.name, d.generation)
                if present:
                    self.submit(LaunchSucceeded(d.name, d.generation, jit.runner_id))
                else:
                    self.submit(
                        LaunchFailed(
                            d.name,
                            d.generation,
                            "wake_failed" if present is False else "wake_ambiguous",
                            rejected=present is False,
                            runner_id=jit.runner_id,
                        )
                    )
                return
            self.submit(
                LaunchFailed(
                    d.name,
                    d.generation,
                    f"agent_{result.status}",
                    rejected=True,
                    capacity=result.outcome is LaunchOutcome.CAPACITY,
                    runner_id=jit.runner_id,
                )
            )
        finally:
            self._jits.pop(key, None)

    async def _reap(self, d: Reap) -> None:
        self.submit(ReapFinished(d.name, d.generation, await self._reap_outcome(d)))

    async def _reap_outcome(self, d: Reap) -> ReapOutcome:
        runner_id = d.runner_id or None
        if d.delete_github and runner_id is not None:
            try:
                gone = await self.github.delete_runner_if_idle(runner_id)
            except GitHubError as exc:
                self._publish(
                    "error",
                    {"name": d.name, "reason": "reap_github", "status": exc.status},
                )
                return ReapOutcome.GITHUB_FAILED
            if not gone:
                # Busy after all: keep the child; the assignment webhook re-adopts it.
                self._publish("error", {"name": d.name, "reason": "reap_runner_busy"})
                return ReapOutcome.BUSY
        if d.stop_agent:
            try:
                await self.desktop.stop(d.name)
            except DesktopError as exc:
                self._publish(
                    "error",
                    {"name": d.name, "reason": "reap_agent", "status": exc.status},
                )
                return ReapOutcome.AGENT_FAILED
        return ReapOutcome.DONE


__all__ = ["MAX_PENDING_WEBHOOKS", "Controller"]

"""Serialized controller: one worker folds events through the pure Scaler.

Webhooks and agent callbacks only enqueue. A single worker task applies each
input to the scaler and interprets the returned decisions; slow effects
(JIT + wake/launch, reaps) run as tracked tasks whose outcomes come back as
new input events, so scaler transitions never interleave. Startup performs
exactly one reconcile (one paginated GitHub listing + one agent inventory)
before any queued event is processed. After that the agent inventory is only
read when the scaler asks (``ReconcileAgent``: jobs wait behind a full pool).
No timers beyond the one-shot deadlines in :mod:`delayed`, no polling.

Every Reap reports its outcome back (``ReapFinished``) so the scaler keeps a
runner tracked until cleanup really happened, and the queue is mirrored to a
:class:`~omega_prime.ci_runners.demand.DemandStore` so a restart replays it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from typing import Any

from omega_prime.ci_runners.agent.jobhook import repo_allowed
from omega_prime.ci_runners.delayed import AsyncioDelayedEvents, Deadline, DelayedEvents
from omega_prime.ci_runners.demand import DemandStore
from omega_prime.ci_runners.desktop import DesktopClient, DesktopError, LaunchOutcome
from omega_prime.ci_runners.forkguard import (
    LOOKUP_FAILED,
    NOT_OPTED_IN,
    run_refusal,
)
from omega_prime.ci_runners.github import GitHubClient, GitHubError
from omega_prime.ci_runners.jobs import (
    AgentInventory,
    AgentRunner,
    CancelIdle,
    ControllerStarted,
    IdleDeadline,
    InputEvent,
    JobEvent,
    JobStatus,
    Launch,
    LaunchFailed,
    LaunchSucceeded,
    ObservedRunner,
    Reap,
    ReapFinished,
    ReapOutcome,
    ReconcileAgent,
    RetryDeadline,
    RunnerExited,
    ScheduleIdle,
    ScheduleRetry,
    WorkflowJob,
    wants_pool,
)
from omega_prime.ci_runners.pool import DesktopPool, PartialInventory
from omega_prime.ci_runners.publisher import (
    CloudEvent,
    EventPublisher,
    UnsafeEventError,
)
from omega_prime.ci_runners.scaler import Scaler, Step

log = logging.getLogger(__name__)

MAX_PENDING_WEBHOOKS = 256
DEMAND_CHECK_CONCURRENCY = 4
# Exit callbacks held while startup is still reconciling (bounded; the pool
# itself is capped far below this).
STARTUP_EXIT_MEMORY = 64
# Fork-guard verdicts cached per workflow run (a run has many jobs, and its
# repository and trigger never change), so a public run costs one GitHub call.
RUN_VERDICT_MEMORY = 512
# Job ids already seen past ``queued`` (in_progress / completed). A ``queued``
# delivery that arrives later is stale: state only moves forward.
JOB_STATE_MEMORY = 4096


class Controller:
    def __init__(
        self,
        *,
        scaler: Scaler,
        github: GitHubClient,
        desktop: DesktopClient | DesktopPool,
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
        # Every agent call goes through the pool (a pass-through for one agent).
        self.agents = (
            desktop
            if isinstance(desktop, DesktopPool)
            else DesktopPool.single(desktop, scaler.max_runners)
        )
        if self.agents.notify is None:
            self.agents.notify = self._publish
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
        self._startup_exits: dict[tuple[str, str], int] = {}
        self._verdicts: OrderedDict[tuple[str, int], str | None] = OrderedDict()
        self._verifying: dict[tuple[str, int], asyncio.Future[str | None]] = {}
        # Acknowledged ``queued`` pool jobs the scaler has not taken yet: a
        # public one awaiting its run lookup, or any one still waiting in the
        # worker queue. They are written to the demand file *before* the 202
        # (see ``offer_job``), so a restart at any point replays them; public
        # ones are re-verified on replay because trust is never persisted.
        self._accepted: dict[int, WorkflowJob] = {}
        # Saved demand read from disk but not yet reconciled at startup. Until
        # ``ControllerStarted`` is applied every save keeps these entries, so
        # a job acknowledged during startup never overwrites older demand.
        self._replay: tuple[WorkflowJob, ...] | None = None
        self._replay_loaded = False
        self._advanced: OrderedDict[int, None] = OrderedDict()

    # -- admission (called from HTTP handlers; never blocks) -------------------

    def offer_job(self, job: WorkflowJob) -> bool:
        """Admit a verified webhook job. False (→ 503) when saturated.

        True means the caller may commit the delivery and answer 202, so a
        ``queued`` pool job is durable before this returns: it is written to
        the demand file first (also during startup, merged with the saved
        demand not yet replayed). If that write fails the job is not admitted
        (→ 503, delivery not committed) rather than acknowledged and then lost
        on the next restart.
        """
        if job.status is JobStatus.QUEUED and job.job_id in self._advanced:
            return True  # stale redelivery: the job already started or finished
        if self._external >= self.max_pending:
            return False
        if job.status is JobStatus.QUEUED and wants_pool(
            job, self.scaler.labels, self.scaler.repos
        ):
            fresh = job.job_id not in self._accepted
            if fresh:
                self._accepted[job.job_id] = job
            if not self._persist_demand():
                if fresh:
                    self._accepted.pop(job.job_id, None)
                return False
        if job.status is not JobStatus.QUEUED:
            self._advance(job.job_id)
        self._external += 1
        if self._guarded(job):
            # Public repo asking for the pool: verify before the scaler sees it.
            # Already on disk (above), so a restart during the lookup replays
            # and re-verifies it.
            self._spawn(self._guard(job))
        else:
            self._queue.put_nowait((True, JobEvent(job)))
        return True

    def _advance(self, job_id: int) -> None:
        self._advanced[job_id] = None
        self._advanced.move_to_end(job_id)
        while len(self._advanced) > JOB_STATE_MEMORY:
            self._advanced.popitem(last=False)
        if self._accepted.pop(job_id, None) is not None:
            self._persist_demand()

    # -- public-repository fork guard ---------------------------------------------

    def _guarded(self, job: WorkflowJob) -> bool:
        """A new public job that asks for the pool must pass the fork guard.

        Later statuses (in_progress / completed) pass straight through
        untrusted: the scaler only follows them for jobs it already queued or
        runners it owns, so they can never cause a launch.
        """
        return (
            job.public
            and not job.private
            and job.status is JobStatus.QUEUED
            and wants_pool(job, self.scaler.labels, self.scaler.repos)
        )

    async def _guard(self, job: WorkflowJob) -> None:
        try:
            reason = await self._refusal(job)
        except Exception as exc:  # fail closed, keep the admission count honest
            log.warning("ci-runners: fork guard failed (%s)", type(exc).__name__)
            reason = LOOKUP_FAILED
        if job.job_id in self._advanced:
            # It started or finished elsewhere while we were looking: admitting
            # the old ``queued`` event now would leave demand nothing clears.
            self._external -= 1
            return
        if reason is None:
            # Stays in ``_accepted`` until the worker has queued it, so the
            # persisted demand never has a gap.
            self._queue.put_nowait(
                (True, JobEvent(dataclasses.replace(job, trusted=True)))
            )
            return
        self._external -= 1
        if self._accepted.pop(job.job_id, None) is not None:
            self._persist_demand()
        self._refused(job, reason)

    def _refused(self, job: WorkflowJob, reason: str) -> None:
        self._publish(
            "refused",
            {
                "job_id": job.job_id,
                "run_id": job.run_id,
                "repo": job.repo,
                "reason": reason,
            },
        )

    async def _refusal(self, job: WorkflowJob) -> str | None:
        """``None`` when the public job may run, else why not (fails closed)."""
        if not repo_allowed(job.repo, self.scaler.public_repos, fork=job.fork):
            return NOT_OPTED_IN
        key = (job.repo, job.run_id)
        if key in self._verdicts:
            self._verdicts.move_to_end(key)
            return self._verdicts[key]
        pending = self._verifying.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._verifying[key] = future
        verdict: str | None
        try:
            try:
                run = await self.github.workflow_run(job.repo, job.run_id)
            except GitHubError:
                verdict = LOOKUP_FAILED  # transient: not cached, a redelivery retries
            else:
                verdict = run_refusal(run, job.repo)
                self._verdicts[key] = verdict
                while len(self._verdicts) > RUN_VERDICT_MEMORY:
                    self._verdicts.popitem(last=False)
            future.set_result(verdict)
            return verdict
        except BaseException:
            if not future.done():
                future.set_result(LOOKUP_FAILED)
            raise
        finally:
            self._verifying.pop(key, None)

    def submit(self, event: InputEvent) -> None:
        """Internal events (effect outcomes, deadlines) are always admitted."""
        self._queue.put_nowait((False, event))

    def runner_exited(self, name: str, generation: str, exit_code: int) -> bool:
        """Agent callback. Only a registered (name, generation) is accepted.

        Before startup has applied ``ControllerStarted`` no runner is
        registered yet, but the agent sends each exit exactly once. Those
        callbacks are held and replayed right after startup for any runner
        the snapshot restored, so a runner that exits while startup awaits
        GitHub or the agent is never restored as a live IDLE runner.
        """
        if not self.ready.is_set():
            key = (name, generation)
            if (
                key not in self._startup_exits
                and len(self._startup_exits) >= STARTUP_EXIT_MEMORY
            ):
                return False
            self._startup_exits[key] = exit_code
            return True
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
        # Saved demand is checked first so the runner snapshot is the last
        # await before ControllerStarted is applied (freshest inventory).
        queued: tuple[WorkflowJob, ...] | None = None
        saved = self._saved_demand_jobs()
        if saved is not None:
            queued = await self._verified_demand(saved)
        started = await self._snapshot()
        if queued is not None:
            started = dataclasses.replace(started, queued=queued)
        self._apply(self.scaler.handle(started))
        self._replay = None  # reconciled: the scaler's queue supersedes it
        self._persist_demand()
        held, self._startup_exits = self._startup_exits, {}
        for (name, generation), code in held.items():
            if self.scaler.registered(name, generation):
                self.submit(RunnerExited(name, generation, code))
        self.ready.set()
        while True:
            external, event = await self._queue.get()
            try:
                if (
                    isinstance(event, JobEvent)
                    and event.job.status is JobStatus.QUEUED
                    and (event.job.trusted or not self._guarded(event.job))
                ):
                    # The scaler takes it now; the persist below records that.
                    self._accepted.pop(event.job.job_id, None)
                stale = (
                    isinstance(event, JobEvent)
                    and event.job.status is JobStatus.QUEUED
                    and event.job.job_id in self._advanced
                )
                if not stale:  # else superseded while it waited in the queue
                    self._apply(self.scaler.handle(event))
                self._persist_demand()
            except Exception:  # keep the worker alive; never log payloads
                log.exception("ci-runners: scaler step failed")
                self._publish("error", {"reason": "scaler_step_failed"})
            finally:
                if external:
                    self._external -= 1
                self._queue.task_done()

    def _saved_demand_jobs(self) -> tuple[WorkflowJob, ...] | None:
        """The demand file as found at startup, read exactly once."""
        if self._demand is None:
            return None
        if not self._replay_loaded:
            self._replay_loaded = True
            self._replay = self._demand.load()
        return self._replay

    def _persist_demand(self) -> bool:
        """Mirror the demand to disk. False only when a needed write failed."""
        if self._demand is None:
            return True
        jobs = list(self.scaler.queued.values())
        if not self.ready.is_set():
            # Startup: the saved demand is not reconciled yet; keep it.
            jobs += self._saved_demand_jobs() or ()
        seen = {j.job_id for j in jobs}
        jobs += [j for i, j in self._accepted.items() if i not in seen]
        ids = tuple(j.job_id for j in jobs)
        if ids == self._saved_demand:
            return True
        try:
            self._demand.save(jobs)
        except OSError as exc:
            # The worker keeps going (callers decide what a failure means);
            # the last good file stays in place.
            self._publish(
                "error", {"reason": "demand_persist", "errno": exc.errno or 0}
            )
            self._saved_demand = None
            return False
        self._saved_demand = ids
        return True

    async def _verified_demand(
        self, saved: tuple[WorkflowJob, ...]
    ) -> tuple[WorkflowJob, ...]:
        """Persisted jobs GitHub still reports as queued, oldest first.

        While the controller was down another runner may have taken or
        finished a saved job, and no webhook will ever say so. Replaying such
        an entry would keep the queue non-empty, so idle runners would never
        be reaped and the Desktop would never sleep. Each entry is therefore
        checked once, here, against GitHub's current job state. An entry that
        cannot be checked is dropped (and reported): an unverifiable job must
        not hold capacity forever; at worst it waits for a re-run.

        Rows saved before fork status was persisted (``fork`` is ``None``)
        would fail an ``owner/*`` entry and be silently forgotten when an
        operator switches from exact names to a wildcard. For those the
        repository's ``fork`` flag is refreshed from the job's workflow run
        first (see :meth:`_refresh_fork`). If it stays unknown the row is
        *retained*: kept in the demand file (so neither this restart nor the
        next one loses it) but never handed to the scaler, so unknown fork
        status can never launch a runner.
        """
        if not saved:
            return ()
        gate = asyncio.Semaphore(DEMAND_CHECK_CONCURRENCY)

        async def check(job: WorkflowJob) -> tuple[WorkflowJob, str]:
            async with gate:
                try:
                    status = await self.github.job_status(job.repo, job.job_id)
                except GitHubError:
                    return job, "unverified"
                if status is JobStatus.QUEUED and self._fork_blocks(job):
                    fork = await self._refresh_fork(job)
                    if fork is None:
                        return job, "retained"
                    job = dataclasses.replace(job, fork=fork)
                    if not wants_pool(job, self.scaler.labels, self.scaler.repos):
                        return job, "fork_repo"  # a verified fork: not listed
            if status is not JobStatus.QUEUED:
                return job, "gone" if status is None else status.name.lower()
            if job.public and not job.private:
                # Trust is never persisted: re-run the fork guard on replay.
                try:
                    reason = await self._refusal(job)
                except Exception:
                    reason = LOOKUP_FAILED
                if reason is not None:
                    self._refused(job, reason)
                    return job, "refused"
                return dataclasses.replace(job, trusted=True), "queued"
            return job, "queued"

        results = await asyncio.gather(*(check(job) for job in saved))
        kept = tuple(job for job, verdict in results if verdict == "queued")
        retained = [job for job, verdict in results if verdict == "retained"]
        for job in retained:
            # Persisted with the rest of the accepted demand, never scaled: a
            # webhook redelivery (which carries ``repository.fork``) or the
            # next restart can still serve it; in_progress / completed clear it.
            # One that already moved on during the lookup is not re-added:
            # nothing would clear it again before the next restart.
            if job.job_id not in self._advanced:
                self._accepted.setdefault(job.job_id, job)
        dropped: dict[str, int] = {}
        for _, verdict in results:
            if verdict not in ("queued", "retained"):
                dropped[verdict] = dropped.get(verdict, 0) + 1
        if dropped or retained:
            self._publish(
                "demand",
                {
                    "reconciled": True,
                    "saved": len(saved),
                    "queued": len(kept),
                    **{f"dropped_{k}": v for k, v in sorted(dropped.items())},
                    **({"retained_fork_unknown": len(retained)} if retained else {}),
                },
            )
        if retained:
            self._publish(
                "error", {"reason": "demand_fork_unknown", "count": len(retained)}
            )
        if "unverified" in dropped:
            self._publish(
                "error",
                {"reason": "demand_unverified", "count": dropped["unverified"]},
            )
        return kept

    def _fork_blocks(self, job: WorkflowJob) -> bool:
        """Only the unknown fork status keeps this saved job out of the pool:
        it would pass an allowlist (``OMEGA_CI_REPOS``, or for a public job
        ``OMEGA_CI_PUBLIC_REPOS``) if the repository were known not to be a
        fork, i.e. it is admitted only through an ``owner/*`` entry."""
        if job.fork is not None:
            return False
        labels, repos = self.scaler.labels, self.scaler.repos
        as_source = dataclasses.replace(job, fork=False)
        if not wants_pool(as_source, labels, repos):
            return False  # not ours whatever the fork flag says
        if not wants_pool(job, labels, repos):
            return True
        public = self.scaler.public_repos
        return (
            job.public
            and not job.private
            and not repo_allowed(job.repo, public, fork=None)
            and repo_allowed(job.repo, public, fork=False)
        )

    async def _refresh_fork(self, job: WorkflowJob) -> bool | None:
        """``repository.fork`` of the saved job's workflow run, or ``None``.

        ``None`` (lookup error, missing run, another repository, a missing or
        non-boolean flag) means unknown, and unknown never matches an
        ``owner/*`` entry: the caller keeps the row but does not serve it.
        """
        try:
            run = await self.github.workflow_run(job.repo, job.run_id)
        except GitHubError:
            return None
        if not isinstance(run, dict):
            return None
        repo = run.get("repository")
        if not isinstance(repo, dict):
            return None
        name = repo.get("full_name")
        if not isinstance(name, str) or name.lower() != job.repo.lower():
            return None
        fork = repo.get("fork")
        return fork if isinstance(fork, bool) else None

    async def _snapshot(self) -> ControllerStarted:
        """The one startup reconcile. A failed side never causes a reap."""
        github: tuple[ObservedRunner, ...] | None
        agent: tuple[AgentRunner, ...] | None
        known: tuple[AgentRunner, ...] = ()  # answering agents when some are silent
        try:
            github = tuple(
                ObservedRunner(r.name, r.runner_id, r.busy, r.online)
                for r in await self.github.list_pool_runners()
            )
        except GitHubError as exc:
            self._publish("error", {"reason": "reconcile_github", "status": exc.status})
            github = None
        try:
            agent = tuple(await self.agents.inventory())
        except PartialInventory as exc:
            self._publish(
                "error",
                {"reason": "reconcile_agent", "status": exc.status, "partial": True},
            )
            agent = None
            known = exc.rows
        except DesktopError as exc:
            self._publish("error", {"reason": "reconcile_agent", "status": exc.status})
            agent = None
        if agent is None and known:
            if github is None:
                agent = known  # the answering agents' children, kept busy below
            else:
                # Some agents silent: keep the answering agents' rows (their
                # real generations, and children GitHub's listing missed) and
                # mirror every other registration as an unknown child.
                listed = {r.name for r in known}
                agent = known + tuple(
                    AgentRunner(o.name, "", "unknown", None)
                    for o in github
                    if o.name not in listed
                )
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
        self.agents.retain(self.scaler.runners)
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
            elif isinstance(decision, ReconcileAgent):
                self._spawn(self._reconcile_agent(decision))

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
        except Exception as exc:  # never strand the STARTING reservation
            log.warning("ci-runners: jit failed (%s)", type(exc).__name__)
            self.submit(LaunchFailed(d.name, d.generation, "jit_unexpected"))
            return
        key = (d.name, d.generation)
        self._jits[key] = jit.encoded  # memory-only, dropped below
        try:
            self._publish("wake_requested", {"name": d.name})
            result = await self.agents.launch(d.name, d.generation, jit.encoded)
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
            if result.outcome is LaunchOutcome.FINISHED:
                # A retried launch whose child already ran and exited (its one
                # exit callback was lost): settle it as that exit, so the
                # reservation is released instead of held by a dead runner.
                assert result.exit_code is not None
                self.submit(RunnerExited(d.name, d.generation, result.exit_code))
                return
            if result.outcome is LaunchOutcome.WAKE_FAILED:
                present = await self.agents.find(d.name, d.generation)
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

    async def _reconcile_agent(self, d: ReconcileAgent) -> None:
        """One agent inventory for the scaler's ghost-slot check (00063)."""
        names: frozenset[str] | None
        try:
            names = await self.agents.present_names()
        except Exception as exc:  # never strand the in-flight check
            log.warning("ci-runners: agent reconcile failed (%s)", type(exc).__name__)
            names = None
        self.submit(AgentInventory(d.token, names))

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
                await self.agents.stop(d.name)
            except DesktopError as exc:
                self._publish(
                    "error",
                    {"name": d.name, "reason": "reap_agent", "status": exc.status},
                )
                return ReapOutcome.AGENT_FAILED
        return ReapOutcome.DONE


__all__ = [
    "DEMAND_CHECK_CONCURRENCY",
    "MAX_PENDING_WEBHOOKS",
    "STARTUP_EXIT_MEMORY",
    "Controller",
]

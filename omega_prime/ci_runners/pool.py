"""Serverless runner-agent pool: one controller, N sleeping Desktop agents.

Each agent is one Railway "Ubuntu Desktop" container with
``sleepApplication`` on, so it scales to zero after ~10 minutes without
outbound traffic and wakes on the next inbound request. The pool places each
new runner on the least-loaded healthy agent (load / capacity, ties to the
lowest index) and wakes an agent that may be asleep (no runner of ours on it)
with one ``GET /healthz`` before handing it a JIT config. There is no health
loop: agents are only contacted when there is work for them (a launch, a
reconcile while runners are live, a reap), so idle agents stay asleep.

Failover (00064):

* ``429`` marks the agent full until one of our runners on it is released
  (or ``FULL_HOLD_S`` passes) and the next agent is tried.
* A definitive failure (wake probe not ready, ``409``, other ``4xx``/``5xx``)
  marks the agent unhealthy for ``DOWN_BASE_S`` doubling to ``DOWN_MAX_S``;
  unhealthy agents are still tried, last. No timer runs: health is compared
  to the clock.
* An ambiguous launch (``WAKE_FAILED``) stays on its agent: the timed-out
  request may still arrive there, so the same name, generation and
  single-use JIT are never sent to a second agent. The controller's normal
  ``find`` path settles it, and the agent is marked unhealthy so the retry
  (a fresh JIT) prefers another agent.
* Capacity is re-checked right before each reservation, because other
  launches may fill an agent while this one waits on a wake probe.

Unknown is never treated as gone: a startup inventory fails as a whole if
any agent does not answer (the controller's conservative fallback then
runs), and the ghost-slot check keeps every runner on a silent agent, plus
every tracked runner whose agent is unknown. Only agents holding our
runners are asked during the ghost-slot check, so unused agents stay
asleep.

With one agent the pool is a pass-through, so single-agent behaviour and call
counts are exactly the pre-swarm ones.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field

from .desktop import (
    AgentRunner,
    LaunchOutcome,
    RunnerAgent,
    WakeProbe,
    WakeResult,
)

DOWN_BASE_S = 60.0
DOWN_MAX_S = 600.0
FULL_HOLD_S = 60.0
# Status reported when a wake probe never got a usable answer.
WAKE_UNREADY_STATUS = 503

_SUCCESS = frozenset({LaunchOutcome.ACCEPTED, LaunchOutcome.REPLAYED})


@dataclass
class PoolMember:
    label: str
    client: RunnerAgent
    capacity: int
    failures: int = 0
    down_until: float = 0.0
    full_until: float = 0.0
    full_load: int = field(default=-1)


Notify = Callable[[str, dict[str, object]], None]


class DesktopPool:
    """Schedules runners across agents; same surface as :class:`DesktopClient`."""

    def __init__(
        self,
        members: Sequence[PoolMember],
        *,
        clock: Callable[[], float] = time.monotonic,
        notify: Notify | None = None,
    ) -> None:
        if not members:
            raise ValueError("DesktopPool needs at least one agent")
        self.members = tuple(members)
        self._clock = clock
        self.notify = notify
        # runner name -> member index (set when placed, i.e. a reservation).
        self._owners: dict[str, int] = {}
        # Runners the scaler tracks (last ``retain``); owner may be unknown.
        self._tracked: frozenset[str] = frozenset()

    @classmethod
    def single(cls, client: RunnerAgent, capacity: int) -> DesktopPool:
        return cls([PoolMember("agent1", client, capacity)])

    @classmethod
    def of(
        cls,
        agents: Sequence[tuple[str, RunnerAgent, int]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> DesktopPool:
        """``(label, client, capacity)`` per agent, agent 1 first."""
        return cls([PoolMember(*a) for a in agents], clock=clock)

    # -- bookkeeping -----------------------------------------------------------

    @property
    def capacity(self) -> int:
        return sum(m.capacity for m in self.members)

    def owner(self, name: str) -> str | None:
        index = self._owners.get(name)
        return None if index is None else self.members[index].label

    def load(self, index: int) -> int:
        return sum(1 for i in self._owners.values() if i == index)

    def retain(self, live: Collection[str]) -> None:
        """Forget placements of runners the scaler no longer tracks."""
        self._tracked = frozenset(live)
        for name in [n for n in self._owners if n not in live]:
            del self._owners[name]
        for i, member in enumerate(self.members):
            if member.full_load >= 0 and self.load(i) < member.full_load:
                member.full_until = 0.0  # a slot we held there was released
                member.full_load = -1

    def healthy(self, index: int) -> bool:
        return self._clock() >= self.members[index].down_until

    def _has_room(self, index: int) -> bool:
        member = self.members[index]
        return self.load(index) < member.capacity and self._clock() >= member.full_until

    def _order(self) -> list[int]:
        room = [i for i in range(len(self.members)) if self._has_room(i)]
        return sorted(
            room,
            key=lambda i: (
                not self.healthy(i),
                self.load(i) / self.members[i].capacity,
                i,
            ),
        )

    def _mark_down(self, index: int, reason: str, status: int | None) -> None:
        member = self.members[index]
        member.failures += 1
        hold = min(DOWN_MAX_S, DOWN_BASE_S * 2 ** (member.failures - 1))
        member.down_until = self._clock() + hold
        self._emit(
            "agent_unhealthy",
            {"agent": member.label, "reason": reason, "status": status, "hold_s": hold},
        )

    def _mark_up(self, index: int) -> None:
        member = self.members[index]
        if member.failures:
            self._emit("agent_healthy", {"agent": member.label})
        member.failures = 0
        member.down_until = 0.0

    def _mark_full(self, index: int) -> None:
        member = self.members[index]
        member.full_until = self._clock() + FULL_HOLD_S
        member.full_load = self.load(index)

    def _emit(self, kind: str, data: dict[str, object]) -> None:
        if self.notify is not None:
            self.notify(kind, data)

    # -- RunnerAgent surface ---------------------------------------------------

    async def launch(self, name: str, generation: str, jitconfig: str) -> WakeResult:
        if len(self.members) == 1:
            self._owners[name] = 0
            result = await self.members[0].client.launch(name, generation, jitconfig)
            if (
                result.outcome not in _SUCCESS
                and result.outcome is not LaunchOutcome.WAKE_FAILED
            ):
                self._owners.pop(name, None)
            return result
        last: WakeResult | None = None
        saw_capacity = False
        attempts = 0
        elapsed = 0.0
        for index in self._order():
            member = self.members[index]
            if not self._has_room(index):
                saw_capacity = True  # filled by other launches while we waited
                continue
            # Reserve before any await so concurrent launches spread out.
            self._owners[name] = index
            if self.load(index) == 1:  # nothing else of ours there: may be asleep
                probe = await self._wake(member)
                if probe is not None:
                    attempts += probe.attempts
                    elapsed += probe.elapsed_s
                    if not probe.ready:
                        del self._owners[name]
                        reason = "draining" if probe.draining else "wake_failed"
                        status = probe.status or WAKE_UNREADY_STATUS
                        self._mark_down(index, reason, status)
                        last = WakeResult(
                            LaunchOutcome.REJECTED, status, attempts, elapsed
                        )
                        continue
                    if probe.attempts > 1:
                        self._emit(
                            "agent_woke",
                            {
                                "agent": member.label,
                                "elapsed_s": round(probe.elapsed_s, 1),
                            },
                        )
            result = await member.client.launch(name, generation, jitconfig)
            attempts += result.attempts
            elapsed += result.elapsed_s
            outcome = result.outcome
            if outcome in _SUCCESS or outcome is LaunchOutcome.FINISHED:
                self._mark_up(index)
                return self._total(result, attempts, elapsed)
            if outcome is LaunchOutcome.CAPACITY:
                del self._owners[name]
                self._mark_full(index)
                saw_capacity = True
                last = result
                continue
            if outcome is LaunchOutcome.WAKE_FAILED:
                # The request may still land on this agent: keep the owner and
                # never send this JIT elsewhere; the controller's find settles it.
                self._mark_down(index, "launch_ambiguous", result.status)
                return self._total(result, attempts, elapsed)
            # CONFLICT / REJECTED: this agent refused; try the next one.
            del self._owners[name]
            self._mark_down(index, "rejected", result.status)
            last = result
        if saw_capacity or last is None:
            return WakeResult(LaunchOutcome.CAPACITY, 429, attempts, elapsed)
        return self._total(last, attempts, elapsed)

    @staticmethod
    def _total(result: WakeResult, attempts: int, elapsed: float) -> WakeResult:
        return WakeResult(
            result.outcome, result.status, attempts, elapsed, result.exit_code
        )

    async def _wake(self, member: PoolMember) -> WakeProbe | None:
        wake = getattr(member.client, "wake", None)
        if wake is None:
            return None
        probe: WakeProbe = await wake()
        return probe

    async def _inventories(
        self, indexes: Sequence[int]
    ) -> list[list[AgentRunner] | BaseException]:
        return list(
            await asyncio.gather(
                *(self.members[i].client.inventory() for i in indexes),
                return_exceptions=True,
            )
        )

    async def inventory(self) -> list[AgentRunner]:
        """Every agent's children (startup reconcile).

        Raises if any agent does not answer: a partial list would make the
        silent agent's runners look like orphans, so the controller's
        conservative "unknown children" fallback runs instead.
        """
        if len(self.members) == 1:
            only = await self.members[0].client.inventory()
            for row in only:
                self._owners.setdefault(row.name, 0)
            return only
        results = await self._inventories(range(len(self.members)))
        for got in results:
            if isinstance(got, BaseException):
                raise got
        rows: list[AgentRunner] = []
        for index, got in enumerate(results):
            assert not isinstance(got, BaseException)
            for row in got:
                self._owners[row.name] = index
                rows.append(row)
        return rows

    async def present_names(self) -> frozenset[str]:
        """Names that may still be running, for the ghost-slot check.

        Asks only the agents holding our tracked runners, so unused agents
        stay asleep, or every agent when a tracked runner's owner is unknown
        (e.g. after a startup inventory fallback). An agent that does not
        answer keeps every runner placed on it, and every tracked runner
        with an unknown owner (unknown is never treated as gone). Raises
        only if no asked agent answered.
        """
        if len(self.members) == 1:
            return frozenset(r.name for r in await self.members[0].client.inventory())
        unowned = {n for n in self._tracked if n not in self._owners}
        if unowned:
            indexes = list(range(len(self.members)))
        else:
            indexes = sorted({self._owners[n] for n in self._tracked})
        if not indexes:
            return frozenset()
        results = await self._inventories(indexes)
        names: set[str] = set()
        failure: BaseException | None = None
        answered = False
        for index, got in zip(indexes, results, strict=True):
            if isinstance(got, BaseException):
                failure = got
                names.update(n for n, i in self._owners.items() if i == index)
                names.update(unowned)
                continue
            answered = True
            for row in got:
                names.add(row.name)
                if row.name in self._tracked:
                    self._owners[row.name] = index
        if not answered:
            assert failure is not None
            raise failure
        return frozenset(names)

    async def find(self, name: str, generation: str) -> bool | None:
        index = self._owners.get(name)
        if index is not None or len(self.members) == 1:
            return await self.members[index or 0].client.find(name, generation)
        answers = await asyncio.gather(
            *(m.client.find(name, generation) for m in self.members),
            return_exceptions=True,
        )
        unknown = False
        for i, answer in enumerate(answers):
            if answer is True:
                self._owners[name] = i
                return True
            if answer is not False:
                unknown = True
        return None if unknown else False

    async def stop(self, name: str) -> bool:
        index = self._owners.get(name)
        if index is not None or len(self.members) == 1:
            return await self.members[index or 0].client.stop(name)
        results = await asyncio.gather(
            *(m.client.stop(name) for m in self.members), return_exceptions=True
        )
        for got in results:
            if isinstance(got, BaseException):
                raise got
        return True


__all__ = [
    "DOWN_BASE_S",
    "DOWN_MAX_S",
    "FULL_HOLD_S",
    "DesktopPool",
    "PoolMember",
]

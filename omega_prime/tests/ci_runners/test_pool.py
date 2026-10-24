"""Serverless multi-agent pool (00064): placement, wake, failover, sleep.

Every agent is a real :class:`DesktopClient` over its own scripted
:class:`FakeHttp`; no sockets, no sleeping (fake clock).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import pytest

from omega_prime.ci_runners.desktop import (
    DesktopClient,
    DesktopError,
    LaunchOutcome,
)
from omega_prime.ci_runners.http import HttpRequest, HttpResponse, TransportError
from omega_prime.ci_runners.jobs import IdleDeadline
from omega_prime.ci_runners.pool import DOWN_BASE_S, DOWN_MAX_S, DesktopPool
from omega_prime.ci_runners.scaler import RunnerState
from omega_prime.tests.ci_runners._secrets import placeholder

from ._fakes import FakeHttp, resp
from ._rig import Rig, job, routes
from ._support import FakeClock

JIT = placeholder("jit-config")
TOKENS = [placeholder(f"agent-{i}-token") for i in (1, 2, 3)]
HEALTHY = {"status": "ok", "draining": False, "runners": 0, "capacity": 8}


def agent_http() -> FakeHttp:
    http = FakeHttp()
    http.on("GET", "/healthz", resp(200, HEALTHY))
    http.on("POST", "/v1/runners", resp(201))
    http.on("GET", "/v1/runners", resp(200, []))
    return http


def constant(token: str) -> Callable[[], str]:
    return lambda: token


class Swarm:
    def __init__(self, n: int = 3, capacity: int = 8, budget_s: float = 30.0) -> None:
        self.clock = FakeClock()
        self.http = [agent_http() for _ in range(n)]
        self.events: list[tuple[str, dict[str, object]]] = []
        self.pool = DesktopPool.of(
            [
                (
                    f"agent{i + 1}",
                    DesktopClient(
                        f"https://runner{i + 1}.example.test",
                        constant(TOKENS[i]),
                        self.http[i],
                        budget_s=budget_s,
                        clock=self.clock,
                        wait=self.clock.wait,
                        jitter=lambda: 1.0,
                    ),
                    capacity,
                )
                for i in range(n)
            ],
            clock=self.clock,
        )
        self.pool.notify = lambda kind, data: self.events.append((kind, data))

    def calls(self, i: int) -> list[tuple[str, str]]:
        return [(c.method, urlsplit(c.url).path) for c in self.http[i].calls]

    def launch(self, name: str) -> Any:
        return asyncio.run(self.pool.launch(name, "gen-1", JIT))


def run(coro: Awaitable[Any]) -> Any:
    async def main() -> Any:
        return await coro

    return asyncio.run(main())


# -- placement -----------------------------------------------------------------


def test_least_loaded_spread_round_robins_across_agents() -> None:
    s = Swarm()
    for k in range(6):
        assert s.launch(f"omega-ci-{k}-aaaa").outcome is LaunchOutcome.ACCEPTED
    owners = [s.pool.owner(f"omega-ci-{k}-aaaa") for k in range(6)]
    assert owners == ["agent1", "agent2", "agent3"] * 2
    assert [s.pool.load(i) for i in range(3)] == [2, 2, 2]


def test_concurrent_launches_spread_instead_of_piling_on_agent1() -> None:
    s = Swarm()

    async def burst() -> list[Any]:
        return list(
            await asyncio.gather(
                *(s.pool.launch(f"omega-ci-{k}-bbbb", "g", JIT) for k in range(9))
            )
        )

    results = asyncio.run(burst())
    assert all(r.outcome is LaunchOutcome.ACCEPTED for r in results)
    assert [s.pool.load(i) for i in range(3)] == [3, 3, 3]


def test_uneven_capacity_balances_by_fraction_full() -> None:
    s = Swarm(n=2)
    s.pool.members[1].capacity = 2
    names = [f"omega-ci-{k}-cccc" for k in range(5)]
    for n in names:
        s.launch(n)
    # 0/8 vs 0/2 -> agent1; 1/8 vs 0/2 -> agent2; then 3 more fit agent1
    # before it is fuller (4/8) than agent2 (1/2).
    assert [s.pool.load(0), s.pool.load(1)] == [4, 1]
    # Pool full: no agent is contacted, capacity goes back to the scaler.
    s.pool.members[0].capacity = 4
    s.pool.members[1].capacity = 1
    before = sum(len(h.calls) for h in s.http)
    assert s.launch("omega-ci-9-cccc").outcome is LaunchOutcome.CAPACITY
    assert sum(len(h.calls) for h in s.http) == before


# -- serverless: wake before place, no idle traffic ------------------------------


def test_sleeping_agent_is_woken_with_healthz_before_the_jit_is_sent() -> None:
    s = Swarm()
    # agent1 is asleep: Railway answers 503 while the container boots.
    s.http[0].on("GET", "/healthz", resp(503), resp(503), resp(200, HEALTHY))
    assert s.launch("omega-ci-1-dddd").outcome is LaunchOutcome.ACCEPTED
    assert s.calls(0) == [("GET", "/healthz")] * 3 + [("POST", "/v1/runners")]
    woke = [d for k, d in s.events if k == "agent_woke"]
    assert [d["agent"] for d in woke] == ["agent1"]


def test_awake_agent_with_our_runners_is_not_probed_again() -> None:
    s = Swarm(n=1 + 1)
    s.launch("omega-ci-1-eeee")  # agent1 (probe + POST)
    s.launch("omega-ci-2-eeee")  # agent2 (probe + POST)
    s.launch("omega-ci-3-eeee")  # agent1 again: already awake, no probe
    assert s.calls(0) == [("GET", "/healthz"), ("POST", "/v1/runners")] * 1 + [
        ("POST", "/v1/runners")
    ]


def test_wake_timeout_falls_back_to_the_next_agent() -> None:
    s = Swarm(budget_s=20.0)
    s.http[0].on("GET", "/healthz", TransportError("connect"))
    result = s.launch("omega-ci-1-ffff")
    assert result.outcome is LaunchOutcome.ACCEPTED
    assert s.pool.owner("omega-ci-1-ffff") == "agent2"
    assert ("POST", "/v1/runners") not in s.calls(0)  # JIT never sent to agent1
    assert s.clock.waits and sum(s.clock.waits) <= 20.0 + 1
    kinds = [(k, d.get("agent"), d.get("reason")) for k, d in s.events]
    assert ("agent_unhealthy", "agent1", "wake_failed") in kinds


def test_draining_agent_is_skipped() -> None:
    s = Swarm()
    s.http[0].on("GET", "/healthz", resp(200, {**HEALTHY, "draining": True}))
    assert s.launch("omega-ci-1-gggg").outcome is LaunchOutcome.ACCEPTED
    assert s.pool.owner("omega-ci-1-gggg") == "agent2"
    assert s.calls(0) == [("GET", "/healthz")]


def test_idle_pool_makes_no_requests_so_every_agent_can_sleep() -> None:
    """No timer, no health loop: time passing alone never touches an agent."""
    s = Swarm()
    s.launch("omega-ci-1-hhhh")
    s.pool.retain(set())  # runner exited and was reaped
    before = [len(h.calls) for h in s.http]
    s.clock.advance(24 * 3600)
    s.pool.retain(set())
    assert [len(h.calls) for h in s.http] == before
    assert s.pool.load(0) == 0


def test_agent_with_no_runners_left_is_probed_again_after_it_may_have_slept() -> None:
    s = Swarm(n=1 + 1)
    s.launch("omega-ci-1-iiii")
    s.pool.retain(set())
    s.clock.advance(15 * 60)  # Railway slept it after ~10 idle minutes
    s.launch("omega-ci-2-iiii")
    assert s.calls(0).count(("GET", "/healthz")) == 2


# -- failover ------------------------------------------------------------------------


def test_429_marks_agent_full_and_places_on_the_next() -> None:
    s = Swarm()
    s.http[0].on("POST", "/v1/runners", resp(429))
    assert s.launch("omega-ci-1-jjjj").outcome is LaunchOutcome.ACCEPTED
    assert s.pool.owner("omega-ci-1-jjjj") == "agent2"
    s.launch("omega-ci-2-jjjj")
    s.launch("omega-ci-3-jjjj")
    # agent1 is held full: the next ones go to agents 2 and 3 only.
    assert s.calls(0).count(("POST", "/v1/runners")) == 1


def test_full_hold_clears_when_one_of_our_runners_there_is_released() -> None:
    s = Swarm(n=2)
    s.launch("omega-ci-1-kkkk")  # agent1
    s.http[0].on("POST", "/v1/runners", resp(429))
    s.launch("omega-ci-2-kkkk")  # agent2 (empty)
    s.launch("omega-ci-3-kkkk")  # agent1 429 -> agent2
    assert s.pool.owner("omega-ci-3-kkkk") == "agent2"
    s.http[0].on("POST", "/v1/runners", resp(201))
    s.pool.retain({"omega-ci-2-kkkk", "omega-ci-3-kkkk"})  # agent1 runner gone
    s.launch("omega-ci-4-kkkk")
    assert s.pool.owner("omega-ci-4-kkkk") == "agent1"


def test_every_agent_full_returns_capacity_to_the_scaler() -> None:
    s = Swarm()
    for h in s.http:
        h.on("POST", "/v1/runners", resp(429))
    result = s.launch("omega-ci-1-llll")
    assert result.outcome is LaunchOutcome.CAPACITY
    assert s.pool.owner("omega-ci-1-llll") is None


def test_rejecting_agent_goes_unhealthy_with_doubling_cooldown() -> None:
    s = Swarm(n=2)
    s.http[0].on("POST", "/v1/runners", resp(400))
    s.launch("omega-ci-1-mmmm")
    member = s.pool.members[0]
    assert not s.pool.healthy(0)
    assert member.down_until - s.clock.now == pytest.approx(DOWN_BASE_S, abs=1)
    # Unhealthy agents are tried last, not never.
    s.http[1].on("POST", "/v1/runners", resp(429))
    s.launch("omega-ci-2-mmmm")
    assert member.failures == 2
    assert member.down_until - s.clock.now == pytest.approx(2 * DOWN_BASE_S, abs=1)
    for k in range(10):
        s.http[1].on("POST", "/v1/runners", resp(201))
        s.pool.members[1].full_until = 0
        s.clock.advance(DOWN_MAX_S + 1)
        s.launch(f"omega-ci-{k + 3}-mmmm")
    assert member.down_until - s.clock.now <= DOWN_MAX_S
    # Recovered: one success clears the health record.
    s.http[0].on("POST", "/v1/runners", resp(201))
    s.clock.advance(DOWN_MAX_S + 1)
    s.pool.retain(set())
    s.launch("omega-ci-99-mmmm")
    assert member.failures == 0 and s.pool.healthy(0)
    assert any(k == "agent_healthy" for k, _ in s.events)


def test_ambiguous_launch_that_may_have_started_is_never_placed_twice() -> None:
    s = Swarm(budget_s=10.0)
    s.http[0].on("POST", "/v1/runners", TransportError("reset"))
    s.http[0].on(
        "GET",
        "/v1/runners",
        TransportError("reset"),  # find: unknown
    )
    result = s.launch("omega-ci-1-nnnn")
    assert result.outcome is LaunchOutcome.WAKE_FAILED
    assert s.pool.owner("omega-ci-1-nnnn") == "agent1"
    assert s.calls(1) == [] and s.calls(2) == []


def test_ambiguous_launch_proven_absent_fails_over() -> None:
    s = Swarm(budget_s=10.0)
    s.http[0].on("POST", "/v1/runners", TransportError("reset"))
    s.http[0].on("GET", "/v1/runners", resp(200, []))  # find: not there
    assert s.launch("omega-ci-1-oooo").outcome is LaunchOutcome.ACCEPTED
    assert s.pool.owner("omega-ci-1-oooo") == "agent2"


# -- reconcile / find / stop ------------------------------------------------------------


def rows(*names: str) -> list[dict[str, Any]]:
    return [{"name": n, "generation": "gen-1", "status": "running"} for n in names]


def test_inventory_is_the_union_and_records_owners() -> None:
    s = Swarm()
    s.http[1].on("GET", "/v1/runners", resp(200, rows("omega-ci-7-pppp")))
    s.http[2].on("GET", "/v1/runners", resp(200, rows("omega-ci-8-pppp")))
    got = run(s.pool.inventory())
    assert sorted(r.name for r in got) == ["omega-ci-7-pppp", "omega-ci-8-pppp"]
    assert s.pool.owner("omega-ci-8-pppp") == "agent3"


def test_inventory_raises_only_when_every_agent_fails() -> None:
    s = Swarm(budget_s=5.0)
    s.http[0].on("GET", "/v1/runners", resp(500))
    assert run(s.pool.inventory()) == []
    for h in s.http:
        h.on("GET", "/v1/runners", resp(500))
    with pytest.raises(DesktopError):
        run(s.pool.inventory())


def test_present_names_keeps_runners_of_an_unreachable_agent() -> None:
    s = Swarm(budget_s=5.0)
    s.launch("omega-ci-1-qqqq")  # agent1
    s.launch("omega-ci-2-qqqq")  # agent2
    s.http[0].on("GET", "/v1/runners", resp(500))
    s.http[1].on("GET", "/v1/runners", resp(200, []))  # agent2 lost its child
    names = run(s.pool.present_names())
    assert names == frozenset({"omega-ci-1-qqqq"})  # ghost on agent2 only
    for h in s.http:
        h.on("GET", "/v1/runners", resp(500))
    with pytest.raises(DesktopError):
        run(s.pool.present_names())


def test_stop_goes_to_the_owner_only() -> None:
    s = Swarm()
    s.launch("omega-ci-1-rrrr")
    s.launch("omega-ci-2-rrrr")  # agent2
    for h in s.http:
        h.on("DELETE", "/v1/runners/omega-ci-2-rrrr", resp(202))
    assert run(s.pool.stop("omega-ci-2-rrrr")) is True
    assert [c for c in s.calls(1) if c[0] == "DELETE"] == [
        ("DELETE", "/v1/runners/omega-ci-2-rrrr")
    ]
    assert not [c for c in s.calls(0) + s.calls(2) if c[0] == "DELETE"]


def test_stop_unknown_owner_asks_every_agent_and_fails_closed() -> None:
    s = Swarm(budget_s=5.0)
    for h in s.http:
        h.on("DELETE", "/v1/runners/omega-ci-5-ssss", resp(404))
    assert run(s.pool.stop("omega-ci-5-ssss")) is True
    s.http[2].on("DELETE", "/v1/runners/omega-ci-5-ssss", resp(500))
    with pytest.raises(DesktopError):
        run(s.pool.stop("omega-ci-5-ssss"))


def test_find_unknown_owner_checks_every_agent() -> None:
    s = Swarm(budget_s=5.0)
    s.http[2].on("GET", "/v1/runners", resp(200, rows("omega-ci-6-tttt")))
    assert run(s.pool.find("omega-ci-6-tttt", "gen-1")) is True
    assert s.pool.owner("omega-ci-6-tttt") == "agent3"
    assert run(s.pool.find("omega-ci-0-tttt", "gen-1")) is False
    s.http[0].on("GET", "/v1/runners", resp(500))
    assert run(s.pool.find("omega-ci-0-tttt", "gen-1")) is None


# -- single agent stays exactly the old behaviour ------------------------------------------


def test_single_agent_pool_is_a_pass_through_without_wake_probe() -> None:
    s = Swarm(n=1)
    assert s.launch("omega-ci-1-uuuu").outcome is LaunchOutcome.ACCEPTED
    assert s.calls(0) == [("POST", "/v1/runners")]
    s.http[0].on("POST", "/v1/runners", resp(429))
    assert s.launch("omega-ci-2-uuuu").outcome is LaunchOutcome.CAPACITY
    assert s.pool.owner("omega-ci-2-uuuu") is None


def test_pool_needs_an_agent() -> None:
    with pytest.raises(ValueError):
        DesktopPool([])


# -- controller wiring ----------------------------------------------------------------------


def scenario(rig: Rig, body: Callable[[Rig], Awaitable[None]]) -> None:
    async def main() -> None:
        await rig.ctrl.start()
        await rig.ctrl.settle()
        try:
            await body(rig)
        finally:
            await rig.ctrl.stop()

    asyncio.run(main())


def swarm_rig(n: int = 3, capacity: int = 2) -> Rig:
    rig = Rig(routes(), max_runners=n * capacity)
    rig.http.on("GET", "/healthz", resp(200, HEALTHY))
    children: dict[str, list[dict[str, Any]]] = {}

    def started(request: HttpRequest) -> HttpResponse:
        body = json.loads(request.body or b"{}")
        row = {"name": body["name"], "generation": body["generation"]}
        children.setdefault(urlsplit(request.url).hostname or "", []).append(
            {**row, "status": "running"}
        )
        return resp(201)

    def listing(request: HttpRequest) -> HttpResponse:
        return resp(200, children.get(urlsplit(request.url).hostname or "", []))

    rig.http.on("POST", "/v1/runners", started)
    rig.http.on("GET", "/v1/runners", listing)
    pool = DesktopPool.of(
        [
            (
                f"agent{i + 1}",
                DesktopClient(
                    f"https://runner{i + 1}.example.test",
                    constant(TOKENS[i]),
                    rig.http,
                    clock=rig.clock,
                    wait=rig.clock.wait,
                    jitter=lambda: 1.0,
                ),
                capacity,
            )
            for i in range(n)
        ],
        clock=rig.clock,
    )
    rig.ctrl.agents = pool
    pool.notify = rig.ctrl._publish
    return rig


def hosts(rig: Rig, method: str, path: str) -> list[str]:
    return [
        urlsplit(c.url).hostname or ""
        for c in rig.http.calls
        if c.method == method and urlsplit(c.url).path == path
    ]


def test_controller_spreads_a_queue_of_jobs_over_three_agents() -> None:
    rig = swarm_rig()

    async def body(r: Rig) -> None:
        for k in range(6):
            assert r.ctrl.offer_job(job(job_id=1000 + k))
        await r.ctrl.settle()

    scenario(rig, body)
    assert len(rig.scaler.runners) == 6
    assert all(r.state is RunnerState.IDLE for r in rig.scaler.runners.values())
    placed = hosts(rig, "POST", "/v1/runners")
    assert sorted(placed) == sorted([f"runner{i}.example.test" for i in (1, 2, 3)] * 2)
    # One wake probe per agent (each was empty, so possibly asleep).
    assert sorted(hosts(rig, "GET", "/healthz")) == [
        f"runner{i}.example.test" for i in (1, 2, 3)
    ]
    # Startup reconcile asked every agent once.
    assert sorted(set(hosts(rig, "GET", "/v1/runners"))) == [
        f"runner{i}.example.test" for i in (1, 2, 3)
    ]
    assert all(t not in rig.publisher.text() for t in TOKENS)


def test_controller_reaps_on_the_agent_that_owns_the_runner() -> None:
    rig = swarm_rig(n=2, capacity=1)
    target: list[str] = []

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job(job_id=2001))
        r.ctrl.offer_job(job(job_id=2002))
        await r.ctrl.settle()
        for job_id in (2001, 2002):  # both picked up elsewhere: queue drains
            r.ctrl.offer_job(
                job("in_progress", job_id=job_id, runner_name="vps-runner")
            )
        await r.ctrl.settle()
        pool = r.ctrl.agents
        owner = {n: pool.owner(n) for n in r.scaler.runners}
        assert sorted(o or "" for o in owner.values()) == ["agent1", "agent2"]
        name = next(n for n, o in owner.items() if o == "agent2")
        target.append(name)
        r.http.on("DELETE", f"/v1/runners/{name}", resp(202))
        [arm] = [g for (n, g) in r.delayed.armed if n == name]
        r.ctrl.submit(IdleDeadline(name, arm))
        await r.ctrl.settle()

    scenario(rig, body)
    assert hosts(rig, "DELETE", f"/v1/runners/{target[0]}") == ["runner2.example.test"]
    assert target[0] not in rig.scaler.runners

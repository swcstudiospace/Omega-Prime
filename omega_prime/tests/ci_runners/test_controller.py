"""Controller orchestration over real adapters with a fake HTTP transport."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners.github import AppTokenSource
from omega_prime.ci_runners.http import TransportError
from omega_prime.ci_runners.jobs import (
    IdleDeadline,
    RetryDeadline,
    RunnerState,
)
from omega_prime.ci_runners.publisher import UnsafeEventError
from omega_prime.ci_runners.scaler import RETRY_BASE_S, RETRY_KEY
from omega_prime.ci_runners.settings import GITHUB_AUDIENCE, AppAuth, SecretFile

from ._fakes import ListPublisher, resp
from ._rig import JIT, ORG, TOKEN, Rig, job, routes

JOBS = "/repos/acme/widgets/actions/jobs"


def scenario(rig: Rig, body: Callable[[Rig], Awaitable[None]]) -> None:
    async def main() -> None:
        await rig.ctrl.start()
        await rig.ctrl.start()  # idempotent
        await rig.ctrl.settle()
        try:
            await body(rig)
        finally:
            await rig.ctrl.stop()

    asyncio.run(main())


def test_launch_happy_path_publishes_redacted_events() -> None:
    rig = Rig(routes())

    async def body(r: Rig) -> None:
        assert r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    runner = rig.only()
    assert runner.state is RunnerState.IDLE and runner.runner_id == 42
    assert rig.http.paths()[:2] == [ORG, "/v1/runners"]  # one-shot reconcile
    assert rig.http.paths()[2:] == [f"{ORG}/generate-jitconfig", "/v1/runners"]
    assert rig.publisher.types()[-5:] == [
        "demand",
        "launch_requested",
        "wake_requested",
        "woke",
        "launched",
    ]
    assert JIT not in rig.publisher.text() and TOKEN not in rig.publisher.text()
    assert rig.ctrl._jits == {}


def test_capacity_429_rolls_back_registration_and_keeps_demand() -> None:
    http = routes()
    http.on("POST", "/v1/runners", resp(429))
    rig = Rig(http)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    assert rig.scaler.runners == {} and 101 in rig.scaler.queued
    assert rig.scaler.hold == "capacity"
    assert http.paths()[-2:] == [f"{ORG}/42", f"{ORG}/42"]  # GET idle, then DELETE
    assert [c.method for c in http.calls[-2:]] == ["GET", "DELETE"]


def test_jit_failure_never_calls_desktop() -> None:
    http = routes()
    http.on("POST", f"{ORG}/generate-jitconfig", resp(500))
    rig = Rig(http)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    assert http.paths("POST") == [f"{ORG}/generate-jitconfig"]
    assert rig.scaler.runners == {} and "error" in rig.publisher.types()


@pytest.mark.parametrize(
    ("inventory", "state"),
    [
        (resp(200, []), None),  # confirmed unaccepted → rolled back
        (resp(500), RunnerState.IDLE),  # ambiguous → kept, never deleted
    ],
)
def test_terminal_wake_failure_reconciles_identity(
    inventory: Any, state: RunnerState | None
) -> None:
    http = routes()
    http.on("POST", "/v1/runners", resp(503))
    rig = Rig(http, desktop={"budget_s": 1})

    async def body(r: Rig) -> None:
        r.http.on("GET", "/v1/runners", inventory)
        r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    if state is None:
        assert rig.scaler.runners == {}
        assert "DELETE" in [c.method for c in http.calls]
    else:
        assert rig.only().state is state
        assert "DELETE" not in [c.method for c in http.calls]


def test_wake_failure_but_child_present_counts_as_launched() -> None:
    http = routes()
    http.on("POST", "/v1/runners", resp(503))
    rig = Rig(http, desktop={"budget_s": 1})

    async def body(r: Rig) -> None:
        def inventory(_req: Any) -> Any:
            [runner] = r.scaler.runners.values()
            row = {"name": runner.name, "generation": runner.generation}
            return resp(200, [row])

        r.http.on("GET", "/v1/runners", inventory)
        r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    assert rig.only().state is RunnerState.IDLE and rig.only().runner_id == 42


def test_idle_deadline_reaps_github_then_agent() -> None:
    rig = Rig(routes())

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()
        r.ctrl.offer_job(job("in_progress", runner_name="vps-runner"))
        await r.ctrl.settle()
        [(name, generation)] = list(r.delayed.armed)
        r.http.on("DELETE", f"/v1/runners/{name}", resp(202))
        r.ctrl._deadline(IdleDeadline(name, generation))
        await r.ctrl.settle()

    scenario(rig, body)
    assert rig.scaler.runners == {}
    tail = [(c.method, c.url.split("test")[-1]) for c in rig.http.calls[-3:]]
    assert [m for m, _ in tail] == ["GET", "DELETE", "DELETE"]
    assert "reaped" in rig.publisher.types()


def test_failed_agent_stop_keeps_the_runner_tracked() -> None:
    """GitHub deregistered it but the child may live: the slot stays held."""
    rig = Rig(routes())

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()
        r.ctrl.offer_job(job("in_progress", runner_name="vps-runner"))
        await r.ctrl.settle()
        [(name, generation)] = list(r.delayed.armed)
        r.http.on("DELETE", f"/v1/runners/{name}", resp(500))
        r.ctrl._deadline(IdleDeadline(name, generation))
        await r.ctrl.settle()

    scenario(rig, body)
    runner = rig.only()
    assert runner.state is RunnerState.FINISHING
    assert rig.scaler.registered(runner.name, runner.generation)
    assert "reap_agent" in {e["data"].get("reason") for e in rig.publisher.events}


def test_failed_github_reap_returns_runner_to_idle() -> None:
    http = routes()
    http.on("GET", f"{ORG}/42", resp(500))
    rig = Rig(http)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()
        r.ctrl.offer_job(job("in_progress", runner_name="vps-runner"))
        await r.ctrl.settle()
        [(name, generation)] = list(r.delayed.armed)
        r.ctrl._deadline(IdleDeadline(name, generation))
        await r.ctrl.settle()

    scenario(rig, body)
    runner = rig.only()
    assert runner.state is RunnerState.IDLE
    assert rig.scaler.armed(runner.name) is not None  # re-armed for another try


def test_reap_keeps_runner_that_turned_busy() -> None:
    http = routes()
    http.on(
        "GET", f"{ORG}/42", resp(200, {"id": 42, "name": "omega-ci-x", "busy": True})
    )
    rig = Rig(http)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()
        r.ctrl.offer_job(job("in_progress", runner_name="vps-runner"))
        await r.ctrl.settle()
        [(name, generation)] = list(r.delayed.armed)
        r.ctrl.submit(IdleDeadline(name, generation))
        await r.ctrl.settle()

    scenario(rig, body)
    assert "/v1/runners/" not in " ".join(
        c.url for c in http.calls if c.method == "DELETE"
    )
    reasons = [e["data"].get("reason") for e in rig.publisher.events]
    assert "reap_runner_busy" in reasons
    assert rig.only().state is RunnerState.BUSY  # still tracked, not lost


def test_exit_callback_requires_registered_generation() -> None:
    rig = Rig(routes())

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()
        runner = r.only()
        names.append(runner.name)
        assert not r.ctrl.runner_exited(runner.name, "forged", 0)
        assert r.ctrl.runner_exited(runner.name, runner.generation, 0)
        await r.ctrl.settle()

    names: list[str] = []
    scenario(rig, body)
    assert names[0] not in rig.scaler.runners and "exited" in rig.publisher.types()
    # Job 101 is still queued, so the scaler (correctly) launched a replacement.
    assert len(rig.scaler.runners) == 1


def test_startup_reconcile_reaps_orphans_and_keeps_busy() -> None:
    http = routes()
    http.on(
        "GET",
        ORG,
        resp(
            200,
            {
                "runners": [
                    {"id": 1, "name": "omega-ci-1-a", "busy": True, "status": "online"},
                    {"id": 42, "name": "omega-ci-2-b", "busy": False},
                ]
            },
        ),
    )
    http.on(
        "GET",
        "/v1/runners",
        resp(
            200,
            [
                {"name": "omega-ci-1-a", "generation": "g1", "status": "running"},
                {"name": "omega-ci-9-z", "generation": "g9", "status": "running"},
            ],
        ),
    )
    http.on("DELETE", "/v1/runners/omega-ci-9-z", resp(202))
    rig = Rig(http)

    async def body(r: Rig) -> None:
        await r.ctrl.settle()

    scenario(rig, body)
    assert rig.scaler.runners["omega-ci-1-a"].state is RunnerState.BUSY
    deletes = [c.url for c in http.calls if c.method == "DELETE"]
    assert any(u.endswith("/v1/runners/omega-ci-9-z") for u in deletes)
    assert any(u.endswith(f"{ORG}/42") for u in deletes)
    assert http.paths("GET").count(ORG) == 1


@pytest.mark.parametrize("side", ["github", "agent", "both"])
def test_failed_snapshot_side_never_reaps(side: str) -> None:
    http = routes()
    http.on(
        "GET",
        ORG,
        resp(200, {"runners": [{"id": 5, "name": "omega-ci-5-e", "busy": False}]}),
    )
    http.on(
        "GET", "/v1/runners", resp(200, [{"name": "omega-ci-6-f", "generation": "g6"}])
    )
    if side in ("github", "both"):
        http.on("GET", ORG, resp(401))
    if side in ("agent", "both"):
        http.on("GET", "/v1/runners", resp(401))
    rig = Rig(http)

    async def body(r: Rig) -> None:
        await r.ctrl.settle()

    scenario(rig, body)
    assert [c for c in http.calls if c.method == "DELETE"] == []
    assert "error" in rig.publisher.types()
    if side == "github":
        assert rig.scaler.runners["omega-ci-6-f"].state is RunnerState.BUSY
    elif side == "agent":
        assert rig.scaler.runners["omega-ci-5-e"].state is RunnerState.IDLE
        # Idle ones age out through the normal one-shot idle path.
        assert rig.scaler.armed("omega-ci-5-e") is not None
    else:
        assert rig.scaler.runners == {}


def test_reap_errors_are_published_not_raised() -> None:
    http = routes()
    http.on("GET", ORG, resp(200, {"runners": [{"id": 42, "name": "omega-ci-2-b"}]}))
    http.on("GET", f"{ORG}/42", resp(500))
    http.on(
        "GET", "/v1/runners", resp(200, [{"name": "omega-ci-9-z", "generation": "g"}])
    )
    http.on("DELETE", "/v1/runners/omega-ci-9-z", resp(500))
    rig = Rig(http)

    async def body(r: Rig) -> None:
        await r.ctrl.settle()

    scenario(rig, body)
    reasons = {e["data"].get("reason") for e in rig.publisher.events}
    assert {"reap_github", "reap_agent"} <= reasons


def test_admission_is_bounded() -> None:
    async def main() -> None:
        rig = Rig(routes(), max_pending=1)
        assert rig.ctrl.offer_job(job(job_id=1))
        assert not rig.ctrl.offer_job(job(job_id=2))
        assert rig.ctrl.pending == 1
        await rig.ctrl.start()
        await rig.ctrl.settle()
        assert rig.ctrl.pending == 0 and rig.ctrl.offer_job(job(job_id=3))
        await rig.ctrl.settle()
        await rig.ctrl.stop()

    asyncio.run(main())


def test_unsafe_publish_and_step_failures_do_not_kill_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Exploding(ListPublisher):
        def publish(self, event: Any, *, known_secrets: Any = ()) -> None:
            if event.type.endswith("demand"):
                raise UnsafeEventError("x")
            super().publish(event, known_secrets=known_secrets)

    rig = Rig(routes(), publisher=Exploding())

    async def body(r: Rig) -> None:
        original = r.scaler.handle
        calls = {"n": 0}

        def flaky(event: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return original(event)

        monkeypatch.setattr(r.scaler, "handle", flaky)
        r.ctrl.offer_job(job(job_id=1))
        r.ctrl.offer_job(job(job_id=2))
        await r.ctrl.settle()

    scenario(rig, body)
    types = rig.publisher.types()
    assert "error" in types and "launched" in types and "demand" not in types


def test_default_delayed_adapter_is_created_on_start() -> None:
    async def main() -> None:
        rig = Rig(routes())
        rig.ctrl._delayed = None
        await rig.ctrl.start()
        assert rig.ctrl._delayed is not None
        await rig.ctrl.stop()

    asyncio.run(main())


def test_token_connection_failure_keeps_worker_and_releases_launch(
    tmp_path: Path,
) -> None:
    """App token POST fails to connect: reconcile and launch both degrade to
    GitHubError, the worker survives, and the reservation is released."""
    key = tmp_path / "key.pem"
    key.write_text("-----BEGIN FAKE KEY-----\n")
    key.chmod(0o600)
    http = routes()
    http.on("POST", "/app/installations/2/access_tokens", TransportError("URLError"))
    auth = AppTokenSource(
        AppAuth(1, 2, SecretFile("OMEGA_CI_GITHUB_APP_KEY_FILE", key, GITHUB_AUDIENCE)),
        http,
        signer=lambda k, d: b"sig",
    )
    rig = Rig(http, auth=auth)

    async def main() -> None:
        await rig.ctrl.start()
        try:  # bounded: the pre-fix bug killed the worker, so settle never returned
            await asyncio.wait_for(rig.ctrl.settle(), 5)
            assert rig.ctrl.ready.is_set()
            assert rig.ctrl.offer_job(job())
            await asyncio.wait_for(rig.ctrl.settle(), 5)
            assert rig.ctrl._worker is not None and not rig.ctrl._worker.done()
        finally:
            await rig.ctrl.stop()

    asyncio.run(main())
    assert rig.scaler.runners == {} and rig.scaler.hold == "failure"
    assert "error" in rig.publisher.types()


def test_restart_replays_persisted_demand(tmp_path: Path) -> None:
    from omega_prime.ci_runners.demand import DemandStore

    path = tmp_path / "demand.json"
    first = Rig(routes(), max_runners=1, demand=DemandStore(path))

    async def body(r: Rig) -> None:
        for n in (1, 2, 3):
            r.ctrl.offer_job(job(job_id=n))
        await r.ctrl.settle()

    scenario(first, body)
    assert list(first.scaler.queued) == [1, 2, 3]
    # A fresh controller (restart) with an empty scaler picks the backlog up,
    # after GitHub confirms every saved job is still queued.
    http = routes()
    for n in (1, 2, 3):
        http.on("GET", f"{JOBS}/{n}", resp(200, {"status": "queued"}))
    second = Rig(http, max_runners=2, demand=DemandStore(path))
    scenario(second, lambda r: r.ctrl.settle())
    assert list(second.scaler.queued) == [1, 2, 3]
    assert sorted(p for p in http.paths("GET") if p.startswith(JOBS)) == [
        f"{JOBS}/1",
        f"{JOBS}/2",
        f"{JOBS}/3",
    ]
    assert second.publisher.types().count("launch_requested") == 2
    # Serving the jobs empties the persisted backlog.
    third = Rig(routes(), demand=DemandStore(path))

    async def serve(r: Rig) -> None:
        for n in (1, 2, 3):
            r.ctrl.offer_job(job("in_progress", job_id=n, runner_name="vps-runner"))
        await r.ctrl.settle()

    scenario(third, serve)
    assert DemandStore(path).load() == ()


def test_restart_drops_saved_jobs_github_no_longer_queues(tmp_path: Path) -> None:
    """Jobs taken, finished or deleted while the controller was down (or that
    cannot be checked) are not replayed, so they cannot pin idle runners."""
    from omega_prime.ci_runners.demand import DemandStore

    path = tmp_path / "demand.json"
    DemandStore(path).save([job(job_id=n) for n in (1, 2, 3, 4, 5, 6)])
    http = routes()
    http.on("GET", f"{JOBS}/1", resp(200, {"status": "completed"}))
    http.on("GET", f"{JOBS}/2", resp(200, {"status": "in_progress"}))
    http.on("GET", f"{JOBS}/3", resp(404))
    http.on("GET", f"{JOBS}/4", resp(500))
    http.on("GET", f"{JOBS}/5", resp(200, {"status": "waiting"}))
    http.on("GET", f"{JOBS}/6", resp(200, {"no": "status"}))
    rig = Rig(http, demand=DemandStore(path))
    scenario(rig, lambda r: r.ctrl.settle())
    assert list(rig.scaler.queued) == [5]
    [summary] = [
        e["data"]
        for e in rig.publisher.events
        if e["type"].endswith(".demand") and e["data"].get("reconciled")
    ]
    assert summary == {
        "reconciled": True,
        "saved": 6,
        "queued": 1,
        "dropped_completed": 1,
        "dropped_gone": 1,
        "dropped_in_progress": 1,
        "dropped_unverified": 2,
    }
    errors = {e["data"].get("reason"): e["data"] for e in rig.publisher.events}
    assert errors["demand_unverified"]["count"] == 2
    assert [j.job_id for j in DemandStore(path).load()] == [5]  # file rewritten


def test_restart_with_no_live_job_lets_idle_runners_be_reaped(tmp_path: Path) -> None:
    from omega_prime.ci_runners.demand import DemandStore

    path = tmp_path / "demand.json"
    DemandStore(path).save([job(job_id=9)])
    http = routes()
    http.on("GET", f"{JOBS}/9", resp(200, {"status": "completed"}))
    http.on(
        "GET",
        ORG,
        resp(200, {"runners": [{"id": 42, "name": "omega-ci-x", "busy": False}]}),
    )
    http.on(
        "GET", "/v1/runners", resp(200, [{"name": "omega-ci-x", "generation": "g"}])
    )
    rig = Rig(http, demand=DemandStore(path))
    scenario(rig, lambda r: r.ctrl.settle())
    assert rig.scaler.queued == {}
    runner = rig.only()
    assert runner.state is RunnerState.IDLE and rig.scaler.armed(runner.name)


def test_finished_replay_releases_the_reservation() -> None:
    """The first launch reply and the one exit callback were both lost: the
    retry learns the child already exited and frees its slot."""
    http = routes()
    http.on(
        "POST",
        "/v1/runners",
        resp(200, {"status": "finished", "exit_code": 0}),
        resp(201),
    )
    rig = Rig(http, max_runners=1)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    types = rig.publisher.types()
    # The dead runner's slot was freed, so the still-queued job got a fresh
    # runner instead of waiting behind a reservation that never resolves.
    assert types.count("launch_requested") == 2 and "exited" in types
    first, second = (
        e["data"]["name"]
        for e in rig.publisher.events
        if e["type"].endswith("launch_requested")
    )
    assert first != second and list(rig.scaler.runners) == [second]


def test_finished_replay_with_failure_backs_off() -> None:
    http = routes()
    http.on("POST", "/v1/runners", resp(200, {"status": "finished", "exit_code": 1}))
    rig = Rig(http, max_runners=1)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    assert rig.scaler.runners == {} and rig.scaler.hold == "failure"
    assert 101 in rig.scaler.queued


class _BrokenToken:
    """A token source that fails with something other than GitHubError."""

    async def token(self) -> str:
        raise OSError("disk gone")

    def secrets(self) -> tuple[str, ...]:
        return ()


def test_unexpected_token_failure_never_strands_a_launch() -> None:
    rig = Rig(routes(), auth=_BrokenToken())

    async def main() -> None:
        await rig.ctrl.start()
        try:
            await asyncio.wait_for(rig.ctrl.settle(), 5)
            assert rig.ctrl.offer_job(job())
            await asyncio.wait_for(rig.ctrl.settle(), 5)
            assert rig.ctrl._worker is not None and not rig.ctrl._worker.done()
        finally:
            await rig.ctrl.stop()

    asyncio.run(main())
    assert rig.scaler.runners == {} and rig.scaler.hold == "failure"
    reasons = {e["data"].get("reason") for e in rig.publisher.events}
    assert "reconcile_github" in reasons and "jit_None" not in reasons


def test_unexpected_jit_error_releases_the_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = Rig(routes())

    async def boom(name: str) -> Any:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(rig.ctrl.github, "generate_jit", boom)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()

    scenario(rig, body)
    assert rig.scaler.runners == {} and rig.scaler.hold == "failure"
    assert "jit_unexpected" in {e["data"].get("reason") for e in rig.publisher.events}


def test_demand_persist_failure_is_published_not_raised(tmp_path: Path) -> None:
    from omega_prime.ci_runners.demand import DemandStore

    blocker = tmp_path / "file"
    blocker.write_text("")
    rig = Rig(routes(), demand=DemandStore(blocker / "demand.json"))

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()
        assert r.ctrl._worker is not None and not r.ctrl._worker.done()

    scenario(rig, body)
    assert "demand_persist" in {e["data"].get("reason") for e in rig.publisher.events}


def test_launch_failures_schedule_a_backoff_retry() -> None:
    http = routes()
    http.on("POST", f"{ORG}/generate-jitconfig", resp(500))
    rig = Rig(http)

    async def body(r: Rig) -> None:
        r.ctrl.offer_job(job())
        await r.ctrl.settle()
        [(key, arm)] = [k for k in r.delayed.armed if k[0] == RETRY_KEY]
        assert r.delayed.armed[(key, arm)] == RETRY_BASE_S
        r.ctrl.offer_job(job(job_id=2))  # new demand does not relaunch
        await r.ctrl.settle()
        assert r.http.paths("POST").count(f"{ORG}/generate-jitconfig") == 1
        r.ctrl._deadline(RetryDeadline(key, arm))
        await r.ctrl.settle()
        assert r.http.paths("POST").count(f"{ORG}/generate-jitconfig") == 3

    scenario(rig, body)
    assert "launch_backoff" in rig.publisher.types()

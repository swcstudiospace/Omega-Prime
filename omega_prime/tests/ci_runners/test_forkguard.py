"""Public-repository fork guard: opt-in, run verification, fail closed.

Behaviour tests over the real controller, GitHub client and scaler with the
scripted fake transport (no sockets). Private-repository behaviour must stay
exactly as before: no run lookup, straight to launch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners import forkguard
from omega_prime.ci_runners.demand import DemandStore
from omega_prime.ci_runners.http import TransportError
from omega_prime.ci_runners.jobs import matches
from omega_prime.ci_runners.scaler import Scaler
from omega_prime.ci_runners.settings import (
    DEFAULT_LABELS,
    ControllerSettings,
    SettingsError,
)

from ._fakes import resp
from ._rig import ORG, Rig, job, routes
from .test_settings import base_env

REPO = "acme/widgets"
RUN = "/repos/acme/widgets/actions/runs/9001"
JIT_PATH = f"{ORG}/generate-jitconfig"


def public_job(action: str = "queued", **kw: Any) -> Any:
    return job(action, private=False, visibility="public", **kw)


def run_body(
    event: str = "push",
    head: str | None = REPO,
    repo: str = REPO,
    fork: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": 9001,
        "event": event,
        "repository": {"full_name": repo},
    }
    body["head_repository"] = (
        None if head is None else {"full_name": head, "fork": fork}
    )
    return body


def scenario(rig: Rig, body: Callable[[Rig], Awaitable[None]]) -> None:
    async def main() -> None:
        await rig.ctrl.start()
        await rig.ctrl.settle()
        try:
            await body(rig)
        finally:
            await rig.ctrl.stop()

    asyncio.run(main())


def offer(*jobs: Any) -> Callable[[Rig], Awaitable[None]]:
    async def body(r: Rig) -> None:
        for j in jobs:
            assert r.ctrl.offer_job(j)
        await r.ctrl.settle()

    return body


def refusals(rig: Rig) -> list[str]:
    return [
        e["data"]["reason"]
        for e in rig.publisher.events
        if e["type"] == "omega.ci.runner.refused"
    ]


def opted_in(http: Any) -> Rig:
    return Rig(http, repos=(REPO,), public_repos=(REPO,))


# -- parsing and matching (pure) ---------------------------------------------------


def test_payload_visibility_is_parsed_fail_closed() -> None:
    assert public_job().public and not public_job().private
    assert not job().public and job().private
    ambiguous = job(private=False, visibility="internal-ish")
    assert not ambiguous.public and not ambiguous.private
    assert not public_job().trusted  # trust is never parsed from a webhook


def test_matches_refuses_public_unless_opted_in_and_trusted() -> None:
    labels, repos = DEFAULT_LABELS, frozenset({REPO})
    pub = public_job()
    trusted = pub.__class__(**{**pub.__dict__, "trusted": True})
    assert not matches(pub, labels, repos)
    assert not matches(trusted, labels, repos)  # not opted in
    assert not matches(pub, labels, repos, {REPO})  # opted in, unverified
    assert matches(trusted, labels, repos, {REPO})
    assert matches(job(), labels, repos)  # private unchanged


def test_scaler_requires_public_subset_of_repos() -> None:
    with pytest.raises(ValueError):
        Scaler(labels=DEFAULT_LABELS, public_repos=(REPO,))
    with pytest.raises(ValueError):
        Scaler(labels=DEFAULT_LABELS, repos=("acme/other",), public_repos=(REPO,))


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        (run_body(), None),
        (run_body("workflow_dispatch"), None),
        (run_body("pull_request"), None),
        (run_body("pull_request", head="mallory/widgets"), forkguard.FORK),
        (run_body("pull_request", fork=True), forkguard.FORK),
        (run_body("pull_request_target"), forkguard.PULL_REQUEST_TARGET),
        (run_body("issue_comment"), forkguard.EVENT_NOT_ALLOWED),
        (run_body("workflow_run"), forkguard.EVENT_NOT_ALLOWED),
        (run_body(head=None), forkguard.HEAD_UNKNOWN),
        (
            {**run_body(), "head_repository": {"full_name": REPO}},
            forkguard.HEAD_UNKNOWN,
        ),
        (
            {**run_body(), "head_repository": {"full_name": REPO, "fork": None}},
            forkguard.HEAD_UNKNOWN,
        ),
        (
            {**run_body(), "head_repository": {"full_name": REPO, "fork": "false"}},
            forkguard.HEAD_UNKNOWN,
        ),
        (run_body(repo="acme/other"), forkguard.REPO_MISMATCH),
        ({"event": "push"}, forkguard.REPO_MISMATCH),
        (None, forkguard.RUN_NOT_FOUND),
    ],
)
def test_run_refusal(run: dict[str, Any] | None, reason: str | None) -> None:
    assert forkguard.run_refusal(run, REPO) == reason


# -- controller behaviour -----------------------------------------------------------


def test_public_repo_not_opted_in_is_refused_without_a_lookup() -> None:
    rig = Rig(routes(), repos=(REPO,))
    scenario(rig, offer(public_job()))
    assert refusals(rig) == [forkguard.NOT_OPTED_IN]
    assert RUN not in rig.http.paths() and JIT_PATH not in rig.http.paths()
    assert rig.scaler.queued == {} and rig.scaler.runners == {}
    assert rig.ctrl.pending == 0
    [event] = [e for e in rig.publisher.events if e["type"].endswith(".refused")]
    assert event["data"] == {
        "job_id": 101,
        "run_id": 9001,
        "repo": REPO,
        "reason": forkguard.NOT_OPTED_IN,
    }


def test_opted_in_same_repo_push_launches() -> None:
    http = routes()
    http.on("GET", RUN, resp(200, run_body("push")))
    rig = opted_in(http)
    scenario(rig, offer(public_job()))
    assert refusals(rig) == []
    paths = http.paths()
    assert paths.index(RUN) < paths.index(JIT_PATH)  # verified before any mint
    assert "launched" in rig.publisher.types()
    assert len(rig.scaler.runners) == 1


def test_fork_pull_request_is_refused() -> None:
    http = routes()
    http.on("GET", RUN, resp(200, run_body("pull_request", head="mallory/widgets")))
    rig = opted_in(http)
    scenario(rig, offer(public_job()))
    assert refusals(rig) == [forkguard.FORK]
    assert JIT_PATH not in http.paths() and rig.scaler.runners == {}
    assert rig.ctrl.pending == 0


def test_pull_request_target_is_refused() -> None:
    http = routes()
    http.on("GET", RUN, resp(200, run_body("pull_request_target")))
    rig = opted_in(http)
    scenario(rig, offer(public_job()))
    assert refusals(rig) == [forkguard.PULL_REQUEST_TARGET]
    assert JIT_PATH not in http.paths()


@pytest.mark.parametrize(
    "reply",
    [resp(500, {"message": "boom"}), resp(401), TransportError("reset"), resp(200, [])],
)
def test_lookup_failure_is_refused_and_not_cached(reply: Any) -> None:
    http = routes()
    http.on("GET", RUN, reply)
    rig = opted_in(http)

    async def body(r: Rig) -> None:
        for n in (1, 2):  # one after the other: no shared in-flight lookup
            assert r.ctrl.offer_job(public_job(job_id=n))
            await r.ctrl.settle()

    scenario(rig, body)
    assert refusals(rig) == [forkguard.LOOKUP_FAILED] * 2
    assert http.paths().count(RUN) == 2  # transient: not cached, asked again
    assert JIT_PATH not in http.paths() and rig.ctrl.pending == 0


def test_missing_run_is_refused() -> None:
    http = routes()
    http.on("GET", RUN, resp(404, {"message": "Not Found"}))
    rig = opted_in(http)
    scenario(rig, offer(public_job()))
    assert refusals(rig) == [forkguard.RUN_NOT_FOUND]


def test_one_lookup_per_workflow_run() -> None:
    http = routes()
    http.on("GET", RUN, resp(200, run_body("push")))
    rig = opted_in(http)
    scenario(rig, offer(public_job(job_id=1), public_job(job_id=2)))
    assert http.paths().count(RUN) == 1
    assert rig.publisher.types().count("launch_requested") == 2


def test_verdict_is_cached_across_later_jobs_of_the_run() -> None:
    http = routes()
    http.on("GET", RUN, resp(200, run_body("push")))
    rig = opted_in(http)

    async def body(r: Rig) -> None:
        for n in (1, 2):
            assert r.ctrl.offer_job(public_job(job_id=n))
            await r.ctrl.settle()

    scenario(rig, body)
    assert http.paths().count(RUN) == 1


def test_later_statuses_of_refused_jobs_never_launch() -> None:
    http = routes()
    http.on("GET", RUN, resp(200, run_body("pull_request", head="mallory/widgets")))
    rig = opted_in(http)
    scenario(
        rig,
        offer(
            public_job(),
            public_job("in_progress", runner_name="omega-ci-evil"),
            public_job("completed", runner_name="omega-ci-evil"),
        ),
    )
    assert JIT_PATH not in http.paths()
    assert http.paths().count(RUN) == 1  # only the queued job is screened


def test_private_behaviour_unchanged_even_with_public_opt_in() -> None:
    rig = opted_in(routes())
    scenario(rig, offer(job()))
    assert refusals(rig) == []
    assert not any("/actions/runs/" in p for p in rig.http.paths())
    assert "launched" in rig.publisher.types()


def test_restart_reverifies_saved_public_demand(tmp_path: Path) -> None:
    path = tmp_path / "demand.json"
    http = routes()
    http.on("GET", RUN, resp(200, run_body("push")))
    first = Rig(
        http,
        repos=(REPO,),
        public_repos=(REPO,),
        max_runners=1,
        demand=DemandStore(path),
    )
    scenario(first, offer(public_job(job_id=1), public_job(job_id=2)))
    assert list(first.scaler.queued) == [1, 2]
    # Restart: the run now looks like a fork (trust is never persisted), so
    # the saved public jobs are dropped instead of replayed.
    http = routes()
    for n in (1, 2):
        http.on(
            "GET",
            f"/repos/acme/widgets/actions/jobs/{n}",
            resp(200, {"status": "queued"}),
        )
    http.on("GET", RUN, resp(200, run_body("pull_request", head="mallory/widgets")))
    second = Rig(http, repos=(REPO,), public_repos=(REPO,), demand=DemandStore(path))
    scenario(second, lambda r: r.ctrl.settle())
    assert second.scaler.queued == {}
    assert refusals(second) == [forkguard.FORK] * 2
    assert JIT_PATH not in http.paths()


# -- settings ---------------------------------------------------------------------


def test_public_repos_default_empty(tmp_path: Path) -> None:
    settings = ControllerSettings.from_env(base_env(tmp_path))
    assert settings.public_repos == frozenset()


def test_public_repos_must_be_subset_of_repos(tmp_path: Path) -> None:
    ok = ControllerSettings.from_env(
        base_env(
            tmp_path,
            OMEGA_CI_REPOS="Acme/Widgets,acme/gadgets",
            OMEGA_CI_PUBLIC_REPOS="acme/widgets",
        )
    )
    assert ok.public_repos == frozenset({REPO})
    for extra in (
        {"OMEGA_CI_PUBLIC_REPOS": REPO},  # empty allowlist cannot opt in
        {"OMEGA_CI_REPOS": "acme/gadgets", "OMEGA_CI_PUBLIC_REPOS": REPO},
        {"OMEGA_CI_REPOS": REPO, "OMEGA_CI_PUBLIC_REPOS": "not a repo"},
    ):
        with pytest.raises(SettingsError, match="OMEGA_CI_PUBLIC_REPOS"):
            ControllerSettings.from_env(base_env(tmp_path, **extra))

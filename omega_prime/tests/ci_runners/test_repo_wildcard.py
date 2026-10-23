"""Org-wide ``owner/*`` allowlist entries (task 00054).

One shared matcher (``omega_prime.ci_runners.agent.jobhook``) serves
``OMEGA_CI_REPOS``, ``OMEGA_CI_PUBLIC_REPOS`` and ``OMEGA_CI_AGENT_REPOS``.
These are behaviour tests over the real parser, settings, scaler, controller
(scripted fake transport, no sockets), demand store and the runner-side hook
run as a plain file. The rules: case-insensitive, fail closed, no bare ``*``
or partial globs, the public subset check understands wildcards, and an
``owner/*`` entry never admits a repository that is itself a GitHub fork.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners import forkguard
from omega_prime.ci_runners.agent import jobhook as hook
from omega_prime.ci_runners.demand import DemandStore
from omega_prime.ci_runners.jobs import matches, wants_pool
from omega_prime.ci_runners.scaler import Scaler
from omega_prime.ci_runners.settings import (
    DEFAULT_LABELS,
    AgentSettings,
    ControllerSettings,
    SettingsError,
)

from ._fakes import resp
from ._rig import ORG, Rig, job, routes
from .test_forkguard import gated, offer, refusals, run_body, scenario, until
from .test_settings import agent_env, base_env

ORG_ALL = "swcstudiospace/*"
REPO = "swcstudiospace/omega-prime"
OTHER = "swcstudiospace/brand-new-repo"
ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "omega_prime" / "ci_runners" / "agent" / "jobhook.py"
JIT_PATH = f"{ORG}/generate-jitconfig"


# -- parse ------------------------------------------------------------------------


def test_parse_accepts_exact_and_owner_wildcard_case_insensitively() -> None:
    got = hook.parse_repo_patterns([" SWCStudioSpace/* ", "Acme/Widgets"])
    assert got == frozenset({ORG_ALL, "acme/widgets"})


@pytest.mark.parametrize(
    "bad",
    [
        "*",
        "*/*",
        "*/widgets",
        "swcstudiospace/foo*",
        "swcstudiospace/*foo",
        "swcstudiospace/**",
        "swcstudiospace/?",
        "swcstudiospace",
        "swcstudiospace/",
        "/widgets",
        "swcstudiospace/a/b",
        "-bad/*",
        "swcstudiospace/.",
        "swcstudiospace/..",
        "",
    ],
)
def test_bad_patterns_are_rejected(bad: str) -> None:
    assert not hook.is_repo_pattern(bad)
    with pytest.raises(hook.RepoPatternError):
        hook.parse_repo_patterns([bad])


@pytest.mark.parametrize(
    "name", ["OMEGA_CI_REPOS", "OMEGA_CI_PUBLIC_REPOS", "OMEGA_CI_AGENT_REPOS"]
)
@pytest.mark.parametrize("bad", ["*", "swcstudiospace/foo*", "*/omega-prime"])
def test_settings_reject_bad_patterns_naming_the_variable(
    tmp_path: Path, name: str, bad: str
) -> None:
    with pytest.raises(SettingsError, match=name) as err:
        if name == "OMEGA_CI_AGENT_REPOS":
            AgentSettings.from_env(agent_env(**{name: bad}))
        else:
            extra = {"OMEGA_CI_REPOS": ORG_ALL, name: bad}
            ControllerSettings.from_env(base_env(tmp_path, **extra))
    assert bad not in str(err.value) or bad == "*"  # names variables, not values


def test_settings_accept_the_org_wide_setup(tmp_path: Path) -> None:
    ctrl = ControllerSettings.from_env(
        base_env(
            tmp_path,
            OMEGA_CI_REPOS="SWCStudioSpace/*",
            OMEGA_CI_PUBLIC_REPOS="swcstudiospace/*",
        )
    )
    assert ctrl.repos == ctrl.public_repos == frozenset({ORG_ALL})
    agent = AgentSettings.from_env(agent_env(OMEGA_CI_AGENT_REPOS="swcstudiospace/*"))
    assert agent.job_repos == frozenset({ORG_ALL})


# -- match ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("repo", "patterns", "fork", "allowed"),
    [
        (REPO, {ORG_ALL}, False, True),
        ("SWCStudioSpace/Omega-Prime", {ORG_ALL}, False, True),
        (OTHER, {ORG_ALL}, False, True),  # new repos are covered automatically
        ("acme/widgets", {ORG_ALL}, False, False),  # other owners are not
        ("swcstudiospace-evil/x", {ORG_ALL}, False, False),  # no prefix match
        ("evil/swcstudiospace", {ORG_ALL}, False, False),
        (REPO, set(), False, False),  # empty means nothing
        (REPO, {"*"}, False, False),  # a stray bare * never matches
        (REPO, {"swcstudiospace/omega*"}, False, False),  # nor a partial glob
        # repositories that are GitHub forks need their exact entry
        (REPO, {ORG_ALL}, True, False),
        (REPO, {ORG_ALL}, None, False),  # unknown fork status fails closed
        (REPO, {ORG_ALL, REPO}, True, True),
        (REPO, {REPO}, None, True),  # exact entries behave as before
        ("swcstudiospace/*", {ORG_ALL}, False, False),  # not a repo name
        ("swcstudiospace/..", {ORG_ALL}, False, False),
        (None, {ORG_ALL}, False, False),
    ],
)
def test_repo_allowed(
    repo: str | None, patterns: set[str], fork: bool | None, allowed: bool
) -> None:
    assert hook.repo_allowed(repo, patterns, fork=fork) is allowed


@pytest.mark.parametrize(
    ("subset", "superset", "covered"),
    [
        ({ORG_ALL}, {ORG_ALL}, True),
        ({REPO, OTHER}, {ORG_ALL}, True),
        ({REPO}, {REPO, "acme/x"}, True),
        (set(), set(), True),
        ({ORG_ALL}, {REPO, OTHER}, False),  # a wildcard needs the same wildcard
        ({ORG_ALL}, {"acme/*"}, False),
        ({"acme/widgets"}, {ORG_ALL}, False),
        ({REPO}, set(), False),
    ],
)
def test_patterns_cover(subset: set[str], superset: set[str], covered: bool) -> None:
    assert hook.patterns_cover(subset, superset) is covered


def test_public_wildcard_requires_repos_to_cover_it(tmp_path: Path) -> None:
    for repos in ("swcstudiospace/omega-prime", "acme/*", ""):
        extra = {"OMEGA_CI_PUBLIC_REPOS": ORG_ALL}
        if repos:
            extra["OMEGA_CI_REPOS"] = repos
        with pytest.raises(SettingsError, match="OMEGA_CI_PUBLIC_REPOS"):
            ControllerSettings.from_env(base_env(tmp_path, **extra))
    narrow = ControllerSettings.from_env(
        base_env(tmp_path, OMEGA_CI_REPOS=ORG_ALL, OMEGA_CI_PUBLIC_REPOS=REPO)
    )
    assert narrow.public_repos == frozenset({REPO})
    with pytest.raises(ValueError, match="public_repos"):
        Scaler(labels=DEFAULT_LABELS, repos=(REPO,), public_repos=(ORG_ALL,))


def test_job_matching_through_the_shared_matcher() -> None:
    private = job(repo=OTHER, fork=False)
    assert wants_pool(private, DEFAULT_LABELS, {ORG_ALL})
    assert matches(private, DEFAULT_LABELS, {ORG_ALL})
    assert not wants_pool(
        job(repo="acme/widgets", fork=False), DEFAULT_LABELS, {ORG_ALL}
    )
    assert not wants_pool(job(repo=OTHER, fork=True), DEFAULT_LABELS, {ORG_ALL})
    assert not wants_pool(job(repo=OTHER), DEFAULT_LABELS, {ORG_ALL})  # fork absent
    public = job(repo=OTHER, private=False, visibility="public", fork=False)
    assert not matches(public, DEFAULT_LABELS, {ORG_ALL}, {ORG_ALL})  # untrusted
    trusted = dataclasses.replace(public, trusted=True)
    assert matches(trusted, DEFAULT_LABELS, {ORG_ALL}, {ORG_ALL})
    assert not matches(trusted, DEFAULT_LABELS, {ORG_ALL}, ())  # not opted in


def test_demand_keeps_fork_status_across_a_restart(tmp_path: Path) -> None:
    store = DemandStore(tmp_path / "demand.json")
    store.save([job(repo=OTHER, fork=False, job_id=1), job(repo=REPO, job_id=2)])
    loaded = {j.job_id: j.fork for j in DemandStore(tmp_path / "demand.json").load()}
    assert loaded == {1: False, 2: None}


# -- controller (fake transport, no sockets) ---------------------------------------


def _run_path(repo: str) -> str:
    return f"/repos/{repo}/actions/runs/9001"


def _org_rig(http: Any) -> Rig:
    return Rig(http, repos=(ORG_ALL,), public_repos=(ORG_ALL,))


def test_org_wildcard_private_repo_launches_without_lookup() -> None:
    http = routes()
    rig = _org_rig(http)
    scenario(rig, offer(job(repo=OTHER, fork=False)))
    assert _run_path(OTHER) not in http.paths()
    assert len(rig.scaler.runners) == 1


def test_org_wildcard_public_push_is_verified_then_launches() -> None:
    http = routes()
    http.on("GET", _run_path(OTHER), resp(200, run_body("push", OTHER, OTHER)))
    rig = _org_rig(http)
    public = job(repo=OTHER, private=False, visibility="public", fork=False)
    scenario(rig, offer(public))
    assert refusals(rig) == []
    paths = http.paths()
    assert paths.index(_run_path(OTHER)) < paths.index(JIT_PATH)
    assert len(rig.scaler.runners) == 1


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (run_body("pull_request", "mallory/brand-new-repo", OTHER), forkguard.FORK),
        (run_body("pull_request_target", OTHER, OTHER), forkguard.PULL_REQUEST_TARGET),
        (run_body("push", None, OTHER), forkguard.HEAD_UNKNOWN),
        (run_body("issue_comment", OTHER, OTHER), forkguard.EVENT_NOT_ALLOWED),
    ],
)
def test_org_wildcard_keeps_every_fork_guard_rule(
    body: dict[str, Any], reason: str
) -> None:
    http = routes()
    http.on("GET", _run_path(OTHER), resp(200, body))
    rig = _org_rig(http)
    scenario(
        rig, offer(job(repo=OTHER, private=False, visibility="public", fork=False))
    )
    assert refusals(rig) == [reason]
    assert JIT_PATH not in http.paths() and rig.scaler.runners == {}


def test_public_org_repo_not_opted_in_is_refused_under_wildcard_repos() -> None:
    http = routes()
    rig = Rig(http, repos=(ORG_ALL,))
    scenario(
        rig, offer(job(repo=OTHER, private=False, visibility="public", fork=False))
    )
    assert refusals(rig) == [forkguard.NOT_OPTED_IN]
    assert _run_path(OTHER) not in http.paths() and JIT_PATH not in http.paths()


@pytest.mark.parametrize("private", [True, False])
def test_org_repo_that_is_a_github_fork_is_never_served_by_the_wildcard(
    private: bool,
) -> None:
    http = routes()
    rig = _org_rig(http)
    vis = "private" if private else "public"
    scenario(rig, offer(job(repo=OTHER, private=private, visibility=vis, fork=True)))
    assert JIT_PATH not in http.paths() and _run_path(OTHER) not in http.paths()
    assert rig.scaler.queued == {} and rig.scaler.runners == {}


def test_org_fork_listed_exactly_is_served() -> None:
    http = routes()
    rig = Rig(http, repos=(ORG_ALL, OTHER))
    scenario(rig, offer(job(repo=OTHER, fork=True)))
    assert len(rig.scaler.runners) == 1


# -- runner-side hook --------------------------------------------------------------


def _event(repo: str, *, fork: Any = False, private: bool = False) -> dict[str, Any]:
    source: dict[str, Any] = {"full_name": repo, "private": private}
    if fork is not None:
        source["fork"] = fork
    return {"repository": source}


def _pr(repo: str, head: str, head_fork: Any = False) -> dict[str, Any]:
    body = _event(repo)
    body["pull_request"] = {"head": {"repo": {"full_name": head, "fork": head_fork}}}
    return body


@pytest.mark.parametrize(
    ("name", "repo", "payload", "reason"),
    [
        ("push", OTHER, _event(OTHER), None),
        ("push", "SWCStudioSpace/Brand-New-Repo", _event(OTHER), None),
        ("workflow_dispatch", REPO, _event(REPO), None),
        ("pull_request", OTHER, _pr(OTHER, OTHER), None),
        ("schedule", OTHER, _event(OTHER, private=True), None),
        ("push", "acme/widgets", _event("acme/widgets"), hook.REPO_NOT_ALLOWED),
        ("pull_request", OTHER, _pr(OTHER, "mallory/x"), hook.FORK),
        ("pull_request", OTHER, _pr(OTHER, OTHER, head_fork=True), hook.FORK),
        ("pull_request_target", OTHER, _event(OTHER), hook.PULL_REQUEST_TARGET),
        ("schedule", OTHER, _event(OTHER), hook.EVENT_NOT_ALLOWED),
        ("push", OTHER, _event(OTHER, fork=True), hook.FORK_NOT_LISTED),
        ("push", OTHER, _event(OTHER, fork=None), hook.FORK_NOT_LISTED),
        ("push", OTHER, _event(OTHER, fork="no"), hook.FORK_NOT_LISTED),
        ("push", OTHER, _event(REPO), hook.REPO_MISMATCH),
    ],
)
def test_hook_with_org_wildcard(
    name: str, repo: str, payload: dict[str, Any], reason: str | None
) -> None:
    assert hook.refusal(name, repo, payload, {ORG_ALL}) == reason


def test_hook_org_wildcard_admits_default_branch_schedule() -> None:
    payload = _event(OTHER)
    payload["repository"]["default_branch"] = "main"
    assert (
        hook.refusal("schedule", OTHER, payload, {ORG_ALL}, ref="refs/heads/main")
        is None
    )
    assert (
        hook.refusal("schedule", OTHER, payload, {ORG_ALL}, ref="refs/heads/feature")
        == hook.EVENT_NOT_ALLOWED
    )


def test_hook_exact_entry_admits_an_org_fork_but_not_its_fork_prs() -> None:
    allowed = {ORG_ALL, OTHER}
    assert hook.refusal("push", OTHER, _event(OTHER, fork=True), allowed) is None
    # Inside an org fork every PR head is a fork repository: still refused.
    pr = _pr(OTHER, OTHER, head_fork=True)
    assert hook.refusal("pull_request", OTHER, pr, allowed) == hook.FORK


def test_hook_ignores_bad_entries_and_empty_means_refuse_all() -> None:
    assert hook.refusal("push", OTHER, _event(OTHER), set()) == hook.NO_ALLOWLIST
    assert hook.refusal("push", OTHER, _event(OTHER), {"*"}) == hook.NO_ALLOWLIST
    assert (
        hook.refusal("push", OTHER, _event(OTHER), {"swcstudiospace/brand*"})
        == hook.NO_ALLOWLIST
    )


def test_policy_round_trips_wildcards_and_a_bad_entry_voids_it(tmp_path: Path) -> None:
    path = tmp_path / "job-policy.json"
    path.write_text(json.dumps(hook.policy_document(["SWCStudioSpace/*"])))
    assert hook.load_policy(path) == frozenset({ORG_ALL})
    path.write_text(json.dumps({"version": 1, "repos": [ORG_ALL, "*"]}))
    assert hook.load_policy(path) == frozenset()
    with pytest.raises(hook.RepoPatternError):
        hook.policy_document(["*"])


def _run_hook(
    tmp_path: Path, policy: list[str], payload: dict[str, Any], repo: str
) -> int:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"version": 1, "repos": policy}))
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload))
    env = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": repo,
        "GITHUB_EVENT_PATH": str(event_path),
        "PATH": "/usr/bin:/bin",
    }
    proc = subprocess.run(
        [sys.executable, "-I", str(MODULE), str(policy_path)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return proc.returncode


def test_hook_file_runs_standalone_with_the_wildcard(tmp_path: Path) -> None:
    assert _run_hook(tmp_path, [ORG_ALL], _event(OTHER), OTHER) == 0
    assert _run_hook(tmp_path, [ORG_ALL], _event(OTHER, fork=True), OTHER) == 1
    assert _run_hook(tmp_path, ["acme/*"], _event(OTHER), OTHER) == 1
    assert _run_hook(tmp_path, [], _event(OTHER), OTHER) == 1


# -- legacy demand rows (saved before fork status was persisted) ------------------


def _legacy_demand(path: Path, *, public: bool = False, job_id: int = 1) -> None:
    """A demand file as an older controller wrote it: no ``fork`` key."""
    row = {
        "job_id": job_id,
        "run_id": 9001,
        "repo": OTHER,
        "private": not public,
        "public": public,
        "labels": list(DEFAULT_LABELS),
        "seen_at": 1.0,
    }
    path.write_text(json.dumps({"version": 1, "jobs": [row]}), encoding="utf-8")


def _job_path(job_id: int = 1) -> str:
    return f"/repos/{OTHER}/actions/jobs/{job_id}"


def _wall_rig(http: Any, path: Path, **kw: Any) -> Rig:
    return Rig(http, demand=DemandStore(path, wall=lambda: 2.0), **kw)


def _saved(path: Path) -> list[tuple[int, Any]]:
    return [(r["job_id"], r["fork"]) for r in json.loads(path.read_text())["jobs"]]


def test_legacy_row_has_its_fork_flag_refreshed_and_is_replayed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    http.on(
        "GET",
        _run_path(OTHER),
        resp(200, {"repository": {"full_name": OTHER, "fork": False}}),
    )
    rig = _wall_rig(http, path, repos=(ORG_ALL,))
    scenario(rig, lambda r: r.ctrl.settle())
    assert list(rig.scaler.queued) == [1]
    assert rig.scaler.queued[1].fork is False
    assert len(rig.scaler.runners) == 1 and JIT_PATH in http.paths()
    assert _saved(path) == [(1, False)]  # retained, now with a known flag


@pytest.mark.parametrize(
    "reply",
    [
        resp(500),
        resp(404),
        resp(200, {"repository": {"full_name": OTHER}}),  # flag missing
        resp(200, {"repository": {"full_name": OTHER, "fork": "no"}}),
        resp(200, {"repository": {"full_name": REPO, "fork": False}}),  # other repo
    ],
)
def test_legacy_row_with_unknown_fork_is_kept_but_never_launched(
    tmp_path: Path, reply: Any
) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    http.on("GET", _run_path(OTHER), reply)
    rig = _wall_rig(http, path, repos=(ORG_ALL,))
    scenario(rig, lambda r: r.ctrl.settle())
    assert rig.scaler.queued == {} and rig.scaler.runners == {}
    assert JIT_PATH not in http.paths()
    assert _saved(path) == [(1, None)]  # not forgotten
    errors = [
        e["data"]
        for e in rig.publisher.events
        if e["type"] == "omega.ci.runner.error"
        and e["data"].get("reason") == "demand_fork_unknown"
    ]
    assert errors == [{"reason": "demand_fork_unknown", "count": 1}]
    # A later restart that can see the flag serves it.
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    http.on(
        "GET",
        _run_path(OTHER),
        resp(200, {"repository": {"full_name": OTHER, "fork": False}}),
    )
    again = _wall_rig(http, path, repos=(ORG_ALL,))
    scenario(again, lambda r: r.ctrl.settle())
    assert list(again.scaler.queued) == [1] and len(again.scaler.runners) == 1


def test_retained_legacy_row_is_cleared_once_the_job_moves_on(tmp_path: Path) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    http.on("GET", _run_path(OTHER), resp(500))
    rig = _wall_rig(http, path, repos=(ORG_ALL,))
    scenario(
        rig, offer(job("completed", repo=OTHER, job_id=1, runner_name="elsewhere"))
    )
    assert json.loads(path.read_text())["jobs"] == []
    assert JIT_PATH not in http.paths()


@pytest.mark.parametrize("later", ["in_progress", "completed"])
def test_legacy_row_that_moves_on_during_a_failed_lookup_is_not_retained(
    tmp_path: Path, later: str
) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    gate, answer = gated(resp(500))
    http.on("GET", _run_path(OTHER), answer)
    rig = _wall_rig(http, path, repos=(ORG_ALL,))

    async def main() -> None:
        await rig.ctrl.start()
        try:
            await until(lambda: _run_path(OTHER) in http.paths())
            # The job starts or finishes while the fork lookup is in flight.
            assert rig.ctrl.offer_job(
                job(later, repo=OTHER, job_id=1, runner_name="elsewhere")
            )
            gate.set()
            await rig.ctrl.settle()
        finally:
            gate.set()
            await rig.ctrl.stop()

    asyncio.run(main())
    assert json.loads(path.read_text())["jobs"] == []
    assert rig.scaler.queued == {} and rig.scaler.runners == {}
    assert JIT_PATH not in http.paths()


def test_legacy_row_verified_as_a_fork_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    http.on(
        "GET",
        _run_path(OTHER),
        resp(200, {"repository": {"full_name": OTHER, "fork": True}}),
    )
    rig = _wall_rig(http, path, repos=(ORG_ALL,))
    scenario(rig, lambda r: r.ctrl.settle())
    assert rig.scaler.queued == {} and rig.scaler.runners == {}
    assert JIT_PATH not in http.paths()
    assert _saved(path) == []
    [summary] = [
        e["data"]
        for e in rig.publisher.events
        if e["type"].endswith(".demand") and e["data"].get("reconciled")
    ]
    assert summary["dropped_fork_repo"] == 1


def test_legacy_public_row_is_refreshed_then_fork_guarded(tmp_path: Path) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path, public=True)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    body = run_body("push", OTHER, OTHER)
    body["repository"]["fork"] = False
    http.on("GET", _run_path(OTHER), resp(200, body))
    rig = _wall_rig(http, path, repos=(ORG_ALL,), public_repos=(ORG_ALL,))
    scenario(rig, lambda r: r.ctrl.settle())
    assert refusals(rig) == []
    assert list(rig.scaler.queued) == [1] and len(rig.scaler.runners) == 1


def test_legacy_public_row_with_unknown_fork_never_launches(tmp_path: Path) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path, public=True)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    http.on("GET", _run_path(OTHER), resp(200, run_body("push", OTHER, OTHER)))
    rig = _wall_rig(http, path, repos=(ORG_ALL,), public_repos=(ORG_ALL,))
    scenario(rig, lambda r: r.ctrl.settle())
    assert rig.scaler.queued == {} and JIT_PATH not in http.paths()
    assert _saved(path) == [(1, None)]


def test_legacy_row_listed_exactly_needs_no_refresh(tmp_path: Path) -> None:
    path = tmp_path / "demand.json"
    _legacy_demand(path)
    http = routes()
    http.on("GET", _job_path(), resp(200, {"status": "queued"}))
    rig = _wall_rig(http, path, repos=(OTHER,))
    scenario(rig, lambda r: r.ctrl.settle())
    assert _run_path(OTHER) not in http.paths()
    assert list(rig.scaler.queued) == [1]

"""Runner-side pre-job guard: pure rules, the script, and the agent wiring.

The hook is what actually stops a refused job: a JIT runner sits in a shared
runner group with shared labels, so GitHub may assign it any matching job.
These tests drive the pure rules, ``main`` over real files, the shipped shell
script (paths rewritten to this checkout), and the supervisor that installs
the hook and its policy. No sockets.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners.agent import jobhook as hook
from omega_prime.ci_runners.agent import supervisor as sup
from omega_prime.ci_runners.agent.supervisor import UnsafeJobHook, child_env
from omega_prime.ci_runners.settings import (
    DEFAULT_AGENT_JOB_POLICY,
    AgentSettings,
    SettingsError,
)

from ._runner import JIT, Exits, runner_root, supervisor

REPO = "acme/widgets"
ALLOWED = frozenset({REPO})
ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "deploy" / "ci-runner-desktop"
SCRIPT = DESKTOP / "job-started.sh"
MODULE = ROOT / "omega_prime" / "ci_runners" / "agent" / "jobhook.py"


def event(
    *,
    repo: str = REPO,
    private: bool | None = False,
    head: str | None = None,
    fork: Any = False,
) -> dict[str, Any]:
    source: dict[str, Any] = {"full_name": repo}
    if private is not None:
        source["private"] = private
    body: dict[str, Any] = {"repository": source}
    if head is not None:
        body["pull_request"] = {"head": {"repo": {"full_name": head, "fork": fork}}}
    return body


def pr(head: str = REPO, fork: Any = False, **kw: Any) -> dict[str, Any]:
    return event(head=head, fork=fork, **kw)


# -- pure rules -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "repository", "payload", "allowed", "reason"),
    [
        ("push", REPO, event(), ALLOWED, None),
        ("workflow_dispatch", REPO, event(), ALLOWED, None),
        ("pull_request", REPO, pr(), ALLOWED, None),
        ("push", "ACME/Widgets", event(repo="Acme/widgets"), ALLOWED, None),
        ("schedule", REPO, event(private=True), ALLOWED, None),  # private: any event
        ("pull_request", REPO, pr(private=True), ALLOWED, None),
        # forks, in public and private repositories alike
        ("pull_request", REPO, pr(head="mallory/widgets"), ALLOWED, hook.FORK),
        ("pull_request", REPO, pr(fork=True), ALLOWED, hook.FORK),
        (
            "pull_request",
            REPO,
            pr(head="mallory/widgets", private=True),
            ALLOWED,
            hook.FORK,
        ),
        ("pull_request", REPO, pr(fork=None), ALLOWED, hook.HEAD_UNKNOWN),
        ("pull_request", REPO, pr(fork="false"), ALLOWED, hook.HEAD_UNKNOWN),
        ("pull_request", REPO, event(), ALLOWED, hook.HEAD_UNKNOWN),
        (
            "pull_request",
            REPO,
            {**event(), "pull_request": {"head": {"repo": None}}},
            ALLOWED,
            hook.HEAD_UNKNOWN,
        ),
        (
            "pull_request_review",
            REPO,
            pr(head="mallory/widgets", private=True),
            ALLOWED,
            hook.FORK,
        ),
        # pull_request_target never, even private and same-repo
        (
            "pull_request_target",
            REPO,
            pr(private=True),
            ALLOWED,
            hook.PULL_REQUEST_TARGET,
        ),
        # public (or unknown visibility): only push / workflow_dispatch / pull_request
        ("issue_comment", REPO, event(), ALLOWED, hook.EVENT_NOT_ALLOWED),
        ("workflow_run", REPO, event(private=None), ALLOWED, hook.EVENT_NOT_ALLOWED),
        # allowlist and context
        ("push", REPO, event(), frozenset(), hook.NO_ALLOWLIST),
        (
            "push",
            "acme/other",
            event(repo="acme/other"),
            ALLOWED,
            hook.REPO_NOT_ALLOWED,
        ),
        ("push", REPO, event(repo="acme/other"), ALLOWED, hook.REPO_MISMATCH),
        ("push", REPO, {"repository": None}, ALLOWED, hook.REPO_MISMATCH),
        ("push", REPO, None, ALLOWED, hook.EVENT_UNREADABLE),
        ("push", REPO, [], ALLOWED, hook.EVENT_UNREADABLE),
        (None, REPO, event(), ALLOWED, hook.CONTEXT_MISSING),
        ("push", "", event(), ALLOWED, hook.CONTEXT_MISSING),
    ],
)
def test_refusal(
    name: str | None,
    repository: str,
    payload: Any,
    allowed: frozenset[str],
    reason: str | None,
) -> None:
    assert hook.refusal(name, repository, payload, allowed) == reason


def test_policy_round_trip_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(hook.policy_document({"Acme/Widgets"})))
    assert hook.load_policy(path) == ALLOWED
    for bad in ("", "{", "[]", '{"version": 2, "repos": ["a/b"]}', '{"repos": "a/b"}'):
        path.write_text(bad)
        assert hook.load_policy(path) == frozenset()
    assert hook.load_policy(tmp_path / "missing.json") == frozenset()


# -- main over real files -----------------------------------------------------------


def files(tmp_path: Path, payload: Any, repos: Any = (REPO,)) -> tuple[Path, Path]:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(hook.policy_document(repos)))
    path = tmp_path / "event.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return policy, path


def env_for(path: Path, name: str = "push", repo: str = REPO) -> dict[str, str]:
    return {
        "GITHUB_EVENT_NAME": name,
        "GITHUB_REPOSITORY": repo,
        "GITHUB_EVENT_PATH": str(path),
    }


def test_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy, path = files(tmp_path, event())
    assert hook.main([str(policy)], env_for(path)) == 0
    fork_policy, fork_path = files(tmp_path, pr(head="mallory/widgets"))
    assert hook.main([str(fork_policy)], env_for(fork_path, "pull_request")) == 1
    assert "refused this job (fork)" in capsys.readouterr().err
    _, garbage = files(tmp_path, "{not json")
    assert hook.main([str(policy)], env_for(garbage)) == 1
    assert hook.main([str(policy)], {}) == 1
    assert hook.main([], env_for(path)) == 1  # no policy argument
    assert hook.main([str(tmp_path / "absent.json")], env_for(path)) == 1


# -- the shipped script ---------------------------------------------------------------


def script_words() -> list[str]:
    [line] = [x for x in SCRIPT.read_text().splitlines() if x.startswith("exec ")]
    return shlex.split(line)


def test_script_is_pinned_to_image_paths() -> None:
    words = script_words()
    assert words[:3] == ["exec", "/usr/bin/python3", "-I"]
    assert words[3] == "/opt/omega-ci/site/omega_prime/ci_runners/agent/jobhook.py"
    assert words[4] == str(DEFAULT_AGENT_JOB_POLICY) and len(words) == 5
    assert os.access(SCRIPT, os.X_OK)
    dockerfile = (DESKTOP / "Dockerfile").read_text()
    assert (
        "COPY deploy/ci-runner-desktop/job-started.sh /opt/omega-ci/job-started.sh"
        in (dockerfile)
    )
    assert "OMEGA_CI_AGENT_JOB_HOOK=/opt/omega-ci/job-started.sh" in dockerfile
    assert f"OMEGA_CI_AGENT_JOB_POLICY={DEFAULT_AGENT_JOB_POLICY}" in dockerfile
    # The supply-chain pins stay immutable digests / checksums.
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    for arg in ("UPSTREAM_ENTRYPOINT_SHA256", "RUNNER_SHA256", "UVICORN_SHA256"):
        assert re.search(rf"^ARG {arg}=[0-9a-f]{{64}}$", dockerfile, re.M), arg


def run_script(tmp_path: Path, policy: Path, env: dict[str, str]) -> int:
    text = SCRIPT.read_text()
    text = text.replace("/usr/bin/python3", sys.executable)
    text = text.replace(
        "/opt/omega-ci/site/omega_prime/ci_runners/agent/jobhook.py", str(MODULE)
    )
    text = text.replace(str(DEFAULT_AGENT_JOB_POLICY), str(policy))
    local = tmp_path / "job-started.sh"
    local.write_text(text)
    local.chmod(0o755)
    hostile = {"PYTHONPATH": str(tmp_path / "evil"), "PYTHONSTARTUP": "/dev/null"}
    done = subprocess.run(
        ["/bin/sh", str(local)],
        env={"PATH": "/usr/bin:/bin", **hostile, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return done.returncode


def test_script_allows_and_refuses(tmp_path: Path) -> None:
    # A planted module on PYTHONPATH must not shadow the stdlib (-I).
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "json.py").write_text("raise SystemExit(0)\n")
    policy, path = files(tmp_path, event())
    assert run_script(tmp_path, policy, env_for(path)) == 0
    _, fork = files(tmp_path, pr(head="mallory/widgets"))
    assert run_script(tmp_path, policy, env_for(fork, "pull_request")) == 1
    _, target = files(tmp_path, pr())
    assert run_script(tmp_path, policy, env_for(target, "pull_request_target")) == 1
    assert run_script(tmp_path, tmp_path / "absent.json", env_for(path)) == 1
    assert run_script(tmp_path, policy, {}) == 1


# -- agent wiring ---------------------------------------------------------------------


def make_hook(tmp_path: Path) -> Path:
    path = tmp_path / "job-started.sh"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_child_env_carries_the_hook_set_by_the_agent(tmp_path: Path) -> None:
    hooked = make_hook(tmp_path)
    inherited = {"PATH": "/x", sup.HOOK_ENV: "/tmp/attacker.sh"}
    assert sup.HOOK_ENV not in child_env(inherited, JIT, tmp_path)  # never inherited
    assert child_env(inherited, JIT, tmp_path, hooked)[sup.HOOK_ENV] == str(hooked)


def test_prepare_writes_policy_and_checks_hook(tmp_path: Path) -> None:
    policy = tmp_path / "state" / "job-policy.json"
    hooked = make_hook(tmp_path)
    s = supervisor(
        tmp_path,
        tmp_path,
        Exits(),
        job_hook=hooked,
        job_policy=policy,
        job_repos=frozenset({"Acme/Widgets"}),
    )
    s.prepare()
    assert hook.load_policy(policy) == ALLOWED
    assert stat.S_IMODE(policy.stat().st_mode) == 0o644
    hooked.chmod(0o777)
    with pytest.raises(UnsafeJobHook, match="world-writable"):
        s.prepare()
    hooked.chmod(0o755)
    with pytest.raises(UnsafeJobHook, match="owned by the job identity"):
        sup.check_hook(hooked, os.getuid(), None)
    with pytest.raises(UnsafeJobHook, match="not readable"):
        sup.check_hook(tmp_path / "absent.sh", None, None)
    with pytest.raises(ValueError, match="job_policy"):
        supervisor(tmp_path, tmp_path, Exits(), job_hook=hooked)


def test_real_child_gets_the_hook(tmp_path: Path) -> None:
    root = runner_root(tmp_path)
    hooked = make_hook(tmp_path)
    exits = Exits()
    s = supervisor(
        tmp_path,
        root,
        exits,
        job_hook=hooked,
        job_policy=tmp_path / "policy.json",
        job_repos=ALLOWED,
    )

    async def main() -> None:
        s.prepare()
        assert s.admit("omega-ci-101-abcd", "g1", JIT) is sup.Admission.ACCEPTED
        await exits.wait()

    asyncio.run(main())
    keys = (tmp_path / "report" / "keys").read_text().split()
    assert sup.HOOK_ENV in keys


def test_agent_settings(tmp_path: Path) -> None:
    base = {
        "OMEGA_CI_AGENT_TOKEN": "t" * 40,
        "OMEGA_CI_CONTROLLER_URL": "https://ci.example.test",
    }
    s = AgentSettings.from_env(base)
    assert s.job_repos == frozenset() and s.job_hook is None
    assert s.job_policy == DEFAULT_AGENT_JOB_POLICY
    s = AgentSettings.from_env(
        {
            **base,
            "OMEGA_CI_AGENT_REPOS": "Acme/Widgets, acme/gadgets",
            "OMEGA_CI_AGENT_JOB_HOOK": "/opt/omega-ci/job-started.sh",
            "OMEGA_CI_AGENT_JOB_POLICY": str(tmp_path / "p.json"),
        }
    )
    assert s.job_repos == frozenset({REPO, "acme/gadgets"})
    assert s.job_hook == Path("/opt/omega-ci/job-started.sh")
    for bad in (
        {"OMEGA_CI_AGENT_REPOS": "not a repo"},
        {"OMEGA_CI_AGENT_JOB_HOOK": "job-started.sh"},
        {"OMEGA_CI_AGENT_JOB_POLICY": "relative.json"},
    ):
        with pytest.raises(SettingsError, match=next(iter(bad))):
            AgentSettings.from_env({**base, **bad})

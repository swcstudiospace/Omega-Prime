"""Supervisor lifecycle with real harmless child processes (no network)."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from omega_prime.ci_runners.agent import supervisor as sup
from omega_prime.ci_runners.agent.supervisor import (
    SPAWN_FAILED,
    Admission,
    Supervisor,
    UnsafeCredentialFile,
    build_workdir,
    check_private,
    child_env,
)

from ._runner import AGENT_TOKEN, JIT, Exits, runner_root, supervisor

NAME = "omega-ci-101-abcd"


def test_tracer_real_child_exit_cleanup_then_one_callback(tmp_path: Path) -> None:
    root = runner_root(tmp_path)
    report = tmp_path / "report"
    exits = Exits()

    async def main() -> Supervisor:
        s = supervisor(tmp_path, root, exits)
        s.prepare()
        assert s.admit(NAME, "gen-a", JIT) is Admission.ACCEPTED
        assert s.admit(NAME, "gen-a", JIT) is Admission.REPLAYED
        assert s.admit(NAME, "gen-b", JIT) is Admission.CONFLICT
        await exits.wait()
        await asyncio.sleep(0)
        return s

    s = asyncio.run(main())
    assert exits.payloads == [
        {
            "type": "agent.runner_exited",
            "name": NAME,
            "generation": "gen-a",
            "exit_code": 3,
        }
    ]
    assert not (tmp_path / "work" / NAME).exists() and s.children == {}
    # The JIT reached the child only through its environment …
    assert (report / "jit").read_text() == JIT
    argv = (report / "argv").read_text().split()
    assert argv == [str(tmp_path / "work" / NAME / "run.sh")]
    keys = set((report / "keys").read_text().split())
    assert sup.JIT_ENV in keys
    assert not keys & {"PASSWORD", "OMEGA_CI_AGENT_TOKEN", "GITHUB_TOKEN"}
    # … ran in its own tree with scratch dirs, at lower priority and capped fds.
    assert (report / "cwd").read_text().strip() == str(tmp_path / "work" / NAME)
    assert {"_work", "_diag", "bin", "run.sh"} <= set(
        (report / "ls").read_text().split()
    )
    assert int((report / "nice").read_text()) >= sup.NICE
    assert int((report / "nofile").read_text()) <= sup.RLIMIT_NOFILE
    # … and nowhere else on disk.
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file() and p.parent != report]
    assert all(JIT not in p.read_text(errors="ignore") for p in leftovers)
    assert JIT not in repr(s.children) and s.notified == 1


def _sleeping(tmp_path: Path, tail: str = "exec sleep 30") -> Path:
    return runner_root(tmp_path, tail=tail)


async def _until_running(s: Supervisor, name: str) -> None:
    async def check() -> None:
        while s.children[name].status != "running":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(check(), 10)


def test_stop_terminates_process_group(tmp_path: Path) -> None:
    exits = Exits()

    async def main() -> None:
        s = supervisor(tmp_path, _sleeping(tmp_path), exits)
        s.admit(NAME, "g", JIT)
        await _until_running(s, NAME)
        assert s.inventory() == [
            {
                "name": NAME,
                "generation": "g",
                "status": "running",
                "pid": s.children[NAME].pid,
            }
        ]
        assert s.stop(NAME) and s.stop(NAME)  # idempotent
        assert s.admit(NAME, "g", JIT) is Admission.CONFLICT  # stopping ≠ replay
        await exits.wait()
        assert not s.stop(NAME)

    asyncio.run(main())
    assert exits.payloads[0]["exit_code"] == -15


def test_term_ignored_escalates_to_kill(tmp_path: Path) -> None:
    exits = Exits()

    async def main() -> None:
        root = _sleeping(tmp_path, tail="trap '' TERM\nexec sleep 30")
        s = supervisor(tmp_path, root, exits)
        s.admit(NAME, "g", JIT)
        await _until_running(s, NAME)
        s.stop(NAME)
        await exits.wait()

    asyncio.run(main())
    assert exits.payloads[0]["exit_code"] == -9


def test_capacity_and_drain(tmp_path: Path) -> None:
    exits = Exits()

    async def main() -> None:
        s = supervisor(tmp_path, _sleeping(tmp_path), exits, max_runners=1)
        assert s.admit(NAME, "g", JIT) is Admission.ACCEPTED
        assert s.admit("omega-ci-102-b", "g", JIT) is Admission.CAPACITY
        s.drain()
        assert s.admit("omega-ci-103-c", "g", JIT) is Admission.DRAINING
        await _until_running(s, NAME)
        await s.shutdown()
        assert exits.payloads[0]["name"] == NAME and s.children == {}

    asyncio.run(main())


def test_shutdown_stops_every_child_once(tmp_path: Path) -> None:
    exits = Exits()

    async def main() -> None:
        s = supervisor(tmp_path, _sleeping(tmp_path), exits)
        s.admit("omega-ci-1-a", "g1", JIT)
        s.admit("omega-ci-2-b", "g2", JIT)
        await _until_running(s, "omega-ci-1-a")
        await s.shutdown()
        await s.shutdown()

    asyncio.run(main())
    assert sorted(p["name"] for p in exits.payloads) == ["omega-ci-1-a", "omega-ci-2-b"]


def test_cancellation_terminates_and_still_notifies(tmp_path: Path) -> None:
    exits = Exits()

    async def main() -> None:
        s = supervisor(tmp_path, _sleeping(tmp_path), exits)
        s.admit(NAME, "g", JIT)
        await _until_running(s, NAME)
        task = s.children[NAME].task
        assert task is not None
        task.cancel()
        await exits.wait()

    asyncio.run(main())
    assert exits.payloads[0]["exit_code"] == -15
    assert not (tmp_path / "work" / NAME).exists()


def test_spawn_failure_reports_and_releases(tmp_path: Path) -> None:
    root = runner_root(tmp_path)
    (root / "run.sh").chmod(0o644)
    exits = Exits()

    async def main() -> Supervisor:
        s = supervisor(tmp_path, root, exits)
        s.admit(NAME, "g", JIT)
        await exits.wait()
        return s

    s = asyncio.run(main())
    assert exits.payloads[0]["exit_code"] == SPAWN_FAILED and s.children == {}
    assert not (tmp_path / "work" / NAME).exists()


def test_stop_before_spawn_never_spawns(tmp_path: Path) -> None:
    exits = Exits()
    spawn = AsyncMock(side_effect=AssertionError("must not spawn"))

    async def main() -> None:
        s = supervisor(tmp_path, runner_root(tmp_path), exits, spawn=spawn)
        s.admit(NAME, "g", JIT)
        assert s.inventory()[0]["status"] == "starting"
        s.stop(NAME)
        await exits.wait()

    asyncio.run(main())
    spawn.assert_not_awaited()
    assert exits.payloads[0]["exit_code"] == SPAWN_FAILED


def test_stop_during_spawn_terminates_after_pid_known(tmp_path: Path) -> None:
    exits = Exits()

    async def main() -> None:
        s = supervisor(tmp_path, _sleeping(tmp_path), exits)
        real = asyncio.create_subprocess_exec

        async def spawn(*a: Any, **kw: Any) -> Any:
            process = await real(*a, **kw)
            s.stop(NAME)
            return process

        s._spawn = spawn
        s.admit(NAME, "g", JIT)
        await exits.wait()

    asyncio.run(main())
    assert exits.payloads[0]["exit_code"] == -15


def test_job_identity_spawn_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chowned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        sup.os, "chown", lambda p, u, g: chowned.append((Path(p).name, u, g))
    )
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    exits = Exits()

    async def spawn(*a: Any, **kw: Any) -> Any:
        calls.append((a, kw))
        raise PermissionError("not root in tests")

    async def main() -> None:
        s = supervisor(
            tmp_path,
            runner_root(tmp_path),
            exits,
            spawn=spawn,
            job_uid=4242,
            job_gid=4343,
        )
        s.admit(NAME, "g", JIT)
        await exits.wait()

    asyncio.run(main())
    [(args, kwargs)] = calls
    assert args == (str(tmp_path / "work" / NAME / "run.sh"),)
    assert kwargs["user"] == 4242 and kwargs["group"] == 4343
    assert kwargs["extra_groups"] == [] and kwargs["start_new_session"] is True
    assert kwargs["preexec_fn"] is sup.limit_child
    assert JIT not in repr(args) and kwargs["env"][sup.JIT_ENV] == JIT
    assert sorted(c[0] for c in chowned) == ["_diag", "_work", NAME]
    assert {(u, g) for _, u, g in chowned} == {(4242, 4343)}


@pytest.mark.parametrize(
    "kw",
    [
        {"max_runners": 0},
        {"job_uid": 1000},
        {"job_uid": 0, "job_gid": 0},
        {"job_uid": os.geteuid(), "job_gid": 5000},
    ],
)
def test_constructor_rejects_unsafe_identity(
    tmp_path: Path, kw: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        supervisor(tmp_path, tmp_path, Exits(), **kw)


@pytest.mark.parametrize(
    ("name", "generation", "jit"),
    [
        ("bad", "g", JIT),
        (NAME, "", JIT),
        (NAME, "g g", JIT),
        (NAME, "g", ""),
        (NAME, "g", "x\nINJECT=1"),
    ],
)
def test_admit_validates(tmp_path: Path, name: str, generation: str, jit: str) -> None:
    async def main() -> None:
        s = supervisor(tmp_path, tmp_path, Exits())
        with pytest.raises(ValueError):
            s.admit(name, generation, jit)

    asyncio.run(main())


def test_notify_failure_is_contained(tmp_path: Path) -> None:
    async def boom(_payload: dict[str, Any]) -> None:
        raise OSError("down")

    async def main() -> Supervisor:
        s = Supervisor(
            runner_root=runner_root(tmp_path),
            work_root=tmp_path / "work",
            max_runners=1,
            notify=boom,
            base_env={},
        )
        s.admit(NAME, "g", JIT)
        task = s.children[NAME].task
        assert task is not None
        await asyncio.wait_for(task, 15)
        return s

    s = asyncio.run(main())
    assert s.notified == 1 and s.children == {}


def test_prepare_removes_only_stale_runner_trees(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "omega-ci-9-stale" / "_work").mkdir(parents=True)
    (work / "keep-me").mkdir()
    s = supervisor(tmp_path, tmp_path, Exits())
    s.prepare()
    assert sorted(p.name for p in work.iterdir()) == ["keep-me"]
    assert stat.S_IMODE(work.stat().st_mode) == 0o711


def test_build_workdir_links_shared_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = runner_root(tmp_path)
    dest = tmp_path / "w" / NAME
    (dest / "stale").mkdir(parents=True)
    build_workdir(root, dest, None, None)
    assert not (dest / "stale").exists()
    assert (dest / "run.sh").stat().st_ino == (root / "run.sh").stat().st_ino
    assert os.readlink(dest / "bin" / "current") == "Runner.Listener"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    assert stat.S_IMODE((dest / "_work").stat().st_mode) == 0o700

    def no_links(*_a: Any, **_k: Any) -> None:
        raise OSError("EXDEV")

    monkeypatch.setattr(sup.os, "link", no_links)
    shutil.rmtree(dest)
    build_workdir(root, dest, None, None)
    assert (dest / "run.sh").stat().st_ino != (root / "run.sh").stat().st_ino
    assert (dest / "run.sh").read_text() == (root / "run.sh").read_text()


def test_child_env_is_an_allowlist(tmp_path: Path) -> None:
    env = child_env(
        {
            "PATH": "/x",
            "TZ": "UTC",
            "PASSWORD": "p",
            "OMEGA_CI_AGENT_TOKEN": AGENT_TOKEN,
        },
        JIT,
        tmp_path,
    )
    assert env == {"PATH": "/x", "TZ": "UTC", "HOME": str(tmp_path), sup.JIT_ENV: JIT}
    assert child_env({}, JIT, tmp_path)["PATH"] == sup.DEFAULT_PATH


def test_check_private(tmp_path: Path) -> None:
    secret = tmp_path / "token"
    secret.write_text("x")
    secret.chmod(0o600)
    check_private(secret, None, None)
    check_private(secret, os.getuid() + 1, os.getgid() + 1)
    with pytest.raises(UnsafeCredentialFile, match="owned"):
        check_private(secret, os.getuid(), None)
    secret.chmod(0o640)
    check_private(secret, None, os.getgid() + 1)
    with pytest.raises(UnsafeCredentialFile, match="group"):
        check_private(secret, None, os.getgid())
    secret.chmod(0o604)
    with pytest.raises(UnsafeCredentialFile, match="other"):
        check_private(secret, None, None)


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to switch to a job UID")
def test_job_uid_cannot_read_agent_token_or_environ(
    tmp_path: Path,
) -> None:
    """Linux-only, root-only: the real identity boundary (runs in the image)."""
    token = tmp_path / "agent.token"
    token.write_text(AGENT_TOKEN)
    token.chmod(0o600)
    tmp_path.chmod(0o755)
    report = tmp_path / "denial"
    report.mkdir(mode=0o777)
    report.chmod(0o777)
    tail = (
        f"cat '{token}' > '{report}/token' 2>/dev/null; echo $? > '{report}/token.rc'\n"
        f"cat /proc/{os.getpid()}/environ > '{report}/env' 2>/dev/null;"
        f" echo $? > '{report}/env.rc'\nexit 0"
    )
    root = runner_root(tmp_path, tail=tail, report=report)
    exits = Exits()

    async def main() -> None:
        s = supervisor(tmp_path, root, exits, job_uid=65534, job_gid=65534)
        s.admit(NAME, "g", JIT)
        await exits.wait()

    asyncio.run(main())
    assert (report / "token.rc").read_text().strip() != "0"
    assert (report / "env.rc").read_text().strip() != "0"
    assert AGENT_TOKEN not in (report / "token").read_text(errors="ignore")


@pytest.mark.parametrize(
    ("hard", "expected"),
    [
        (sup.resource.RLIM_INFINITY, (sup.RLIMIT_NPROC, sup.RLIMIT_NOFILE)),
        (1024, (1024, 1024)),
    ],
)
def test_limit_child_lowers_priority_and_caps_resources(
    hard: int, expected: tuple[int, int]
) -> None:
    niced: list[int] = []
    set_calls: dict[int, tuple[int, int]] = {}

    def nice(inc: int) -> int:
        niced.append(inc)
        return inc

    def getrlimit(limit: int) -> tuple[int, int]:
        return (0, hard)

    def setrlimit(limit: int, value: tuple[int, int]) -> None:
        set_calls[limit] = value

    sup.limit_child(nice, getrlimit, setrlimit)
    assert niced == [sup.NICE]
    nproc, nofile = expected
    assert set_calls == {
        sup.resource.RLIMIT_NPROC: (nproc, nproc),
        sup.resource.RLIMIT_NOFILE: (nofile, nofile),
    }

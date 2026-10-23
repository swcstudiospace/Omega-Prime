"""Ephemeral runner child lifecycle: reserve → isolate → spawn → wait → clean → notify.

One reservation is one ``run.sh`` child. The JIT config crosses into the child
only as ``ACTIONS_RUNNER_INPUT_JITCONFIG`` in its scrubbed environment; it is
never written to disk, put on argv, logged, returned, or kept after spawn.

Completion is pushed by the child's own ``process.wait()``; there are no
timers, polling or sleep loops. Termination escalates SIGTERM → SIGKILL once,
bounded by one ``wait_for``.

Isolation (documented, not overstated):

* With ``job_uid``/``job_gid`` set (the image does), children run as that
  dedicated non-root identity. Credential files must not be readable by it and
  ``/proc/<agent>/environ`` is unreadable across UIDs.
* Children share that UID, so per-runner workdirs (0700) are hygiene, not a
  confidentiality boundary between concurrent jobs. ``nice`` and rlimits are
  resource guards only. No Docker / privileged access is provided.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import logging
import os
import re
import resource
import shutil
import signal
import stat
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omega_prime.ci_runners.agent.jobhook import policy_document
from omega_prime.ci_runners.settings import RUNNER_NAME_RE

log = logging.getLogger(__name__)

JIT_ENV = "ACTIONS_RUNNER_INPUT_JITCONFIG"
HOOK_ENV = "ACTIONS_RUNNER_HOOK_JOB_STARTED"
ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM")
DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TERM_GRACE_S = 10.0
NICE = 10
RLIMIT_NPROC = 4096
RLIMIT_NOFILE = 65536
SPAWN_FAILED = -1  # exit_code reported when the child never ran
FINISHED_MEMORY = 1024  # (name, generation) identities kept after cleanup
GENERATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
JIT_RE = re.compile(r"^[A-Za-z0-9+/=_-]{1,65536}$")

Notify = Callable[[dict[str, Any]], Awaitable[None]]
Spawn = Callable[..., Awaitable[Any]]


class Admission(enum.Enum):
    ACCEPTED = "accepted"
    REPLAYED = "replayed"
    FINISHED = "finished"
    CONFLICT = "conflict"
    DRAINING = "draining"
    CAPACITY = "capacity"


class UnsafeCredentialFile(RuntimeError):
    """The agent token file is readable by the job identity (or world)."""


@dataclass(eq=False)
class Child:
    name: str
    generation: str
    status: str = "starting"  # starting → running → stopping
    pid: int | None = None
    stop_requested: bool = False
    process: Any = field(default=None, repr=False)
    exited: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "generation": self.generation,
            "status": self.status,
            "pid": self.pid,
        }


def child_env(
    base: Mapping[str, str], jit: str, home: Path, hook: Path | None = None
) -> dict[str, str]:
    """Allowlisted environment: nothing from the agent's secrets survives.

    ``hook`` (the image's pre-job guard) is set by the agent itself, never
    inherited, so the runner always runs it before a job's first step.
    """
    env = {k: base[k] for k in ENV_ALLOWLIST if k in base}
    env.setdefault("PATH", DEFAULT_PATH)
    env["HOME"] = str(home)
    if hook is not None:
        env[HOOK_ENV] = str(hook)
    env[JIT_ENV] = jit
    return env


class UnsafeJobHook(RuntimeError):
    """The pre-job hook is missing or could be altered by the job identity."""


def check_hook(path: Path, job_uid: int | None, job_gid: int | None) -> None:
    """Fail closed unless the hook is a root-owned file the job cannot change."""
    try:
        st = path.stat()
    except OSError as exc:
        raise UnsafeJobHook(f"{path}: not readable ({type(exc).__name__})") from exc
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeJobHook(f"{path}: not a regular file")
    if st.st_mode & stat.S_IWOTH:
        raise UnsafeJobHook(f"{path}: world-writable")
    if job_uid is not None and st.st_uid == job_uid:
        raise UnsafeJobHook(f"{path}: owned by the job identity")
    if job_gid is not None and st.st_gid == job_gid and st.st_mode & stat.S_IWGRP:
        raise UnsafeJobHook(f"{path}: group-writable by the job identity")


def write_job_policy(path: Path, repos: frozenset[str]) -> None:
    """The hook's allowlist: repository names only, 0644, replaced atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(policy_document(repos)), encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, path)


def check_private(path: Path, job_uid: int | None, job_gid: int | None) -> None:
    """Fail closed if the job identity (or anyone) could read a credential file."""
    st = path.stat()
    if st.st_mode & stat.S_IRWXO:
        raise UnsafeCredentialFile(f"{path}: accessible to other users (chmod 600)")
    if job_uid is not None and st.st_uid == job_uid:
        raise UnsafeCredentialFile(f"{path}: owned by the job identity")
    if job_gid is not None and st.st_gid == job_gid and st.st_mode & stat.S_IRWXG:
        raise UnsafeCredentialFile(f"{path}: group-accessible to the job identity")


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst, follow_symlinks=False)
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=False)


class UnsafeWorkdir(RuntimeError):
    """A scratch path in the per-runner tree is not a plain directory."""


SCRATCH_DIRS = ("_work", "_diag")


def build_workdir(
    runner_root: Path, workdir: Path, job_uid: int | None, job_gid: int | None
) -> None:
    """Private per-runner tree; the shared runner files stay read-only.

    Files are hard-linked (copy fallback) and keep their root ownership, so a
    job cannot modify the shared installation. Only the top directory and the
    runner's own scratch dirs belong to the job identity.

    Ordering is the security property: the whole tree is built and checked
    while it is still root-owned (0700), the scratch dirs are handed over
    first with ``follow_symlinks=False``, and the top directory is chowned
    last. Until that final call no job process can create, rename or replace
    anything inside it, so no later ``chown`` can be redirected by a symlink.
    """
    if workdir.is_symlink():
        workdir.unlink()
    elif workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(runner_root, workdir, symlinks=True, copy_function=_link_or_copy)
    workdir.chmod(0o700)
    scratch_paths = [workdir / name for name in SCRATCH_DIRS]
    for scratch in scratch_paths:
        with contextlib.suppress(FileExistsError):
            scratch.mkdir(mode=0o700)
        info = os.lstat(scratch)
        if not stat.S_ISDIR(info.st_mode):  # a symlink (or file) from the root
            raise UnsafeWorkdir(f"{scratch.name}: not a plain directory")
        os.chmod(scratch, 0o700, follow_symlinks=False)
    if job_uid is not None and job_gid is not None:
        for scratch in scratch_paths:
            os.chown(scratch, job_uid, job_gid, follow_symlinks=False)
        os.chown(workdir, job_uid, job_gid, follow_symlinks=False)


def limit_child(
    nice: Callable[[int], int] = os.nice,
    getrlimit: Callable[[int], tuple[int, int]] = resource.getrlimit,
    setrlimit: Callable[[int, tuple[int, int]], None] = resource.setrlimit,
) -> None:
    """``preexec_fn`` for each runner: lower priority and cap processes/files.

    Called with no arguments in the forked child; the parameters exist so the
    exact limits can be checked in-process with fakes.
    """
    nice(NICE)
    for limit, value in (
        (resource.RLIMIT_NPROC, RLIMIT_NPROC),
        (resource.RLIMIT_NOFILE, RLIMIT_NOFILE),
    ):
        _soft, hard = getrlimit(limit)
        cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
        setrlimit(limit, (cap, cap))


class Supervisor:
    def __init__(
        self,
        *,
        runner_root: Path,
        work_root: Path,
        max_runners: int,
        notify: Notify,
        job_uid: int | None = None,
        job_gid: int | None = None,
        base_env: Mapping[str, str] | None = None,
        spawn: Spawn | None = None,
        term_grace_s: float = TERM_GRACE_S,
        job_hook: Path | None = None,
        job_policy: Path | None = None,
        job_repos: frozenset[str] = frozenset(),
    ) -> None:
        if max_runners < 1:
            raise ValueError("max_runners must be ≥ 1")
        if (job_uid is None) != (job_gid is None):
            raise ValueError("job_uid and job_gid go together")
        if job_uid == 0 or job_gid == 0:
            raise ValueError("the job identity must not be root")
        if job_uid is not None and job_uid == os.geteuid():
            raise ValueError("the job identity must differ from the agent's")
        self.runner_root = runner_root
        self.work_root = work_root
        self.max_runners = max_runners
        self.job_uid = job_uid
        self.job_gid = job_gid
        self._notify = notify
        self._base_env = dict(os.environ if base_env is None else base_env)
        self._spawn: Spawn = spawn or asyncio.create_subprocess_exec
        self._grace = term_grace_s
        if job_hook is not None and job_policy is None:
            raise ValueError("job_hook needs job_policy")
        self.job_hook = job_hook
        self.job_policy = job_policy
        self.job_repos = job_repos
        self.children: dict[str, Child] = {}
        # name → (generation, exit code) of children that already ran
        self._finished: OrderedDict[str, tuple[str, int]] = OrderedDict()
        self.draining = False
        self.notified = 0

    # -- admission -------------------------------------------------------------

    def admit(self, name: str, generation: str, jit: str) -> Admission:
        """Atomically reserve a slot (no await) and start the child task."""
        if not RUNNER_NAME_RE.match(name):
            raise ValueError("invalid runner name")
        if not GENERATION_RE.match(generation):
            raise ValueError("invalid generation")
        if not JIT_RE.match(jit):
            raise ValueError("invalid jitconfig")
        existing = self.children.get(name)
        if existing is not None:
            if existing.generation == generation and not existing.stop_requested:
                return Admission.REPLAYED
            return Admission.CONFLICT
        if name in self._finished:
            # A retried launch whose child already ran and was cleaned up:
            # the JIT config is single-use, so never start it again. Report
            # FINISHED (with the exit code, see finished_exit) rather than a
            # live replay, so the controller releases the slot even if the
            # one exit callback was lost.
            if self._finished[name][0] == generation:
                return Admission.FINISHED
            return Admission.CONFLICT
        if self.draining:
            return Admission.DRAINING
        if len(self.children) >= self.max_runners:
            return Admission.CAPACITY
        child = Child(name, generation)
        self.children[name] = child
        child.task = asyncio.get_running_loop().create_task(self._run(child, jit))
        return Admission.ACCEPTED

    def finished_exit(self, name: str, generation: str) -> int | None:
        """Exit code of (name, generation) if that child already finished."""
        entry = self._finished.get(name)
        if entry is None or entry[0] != generation:
            return None
        return entry[1]

    def inventory(self) -> list[dict[str, Any]]:
        return [c.row() for c in self.children.values()]

    def stop(self, name: str) -> bool:
        child = self.children.get(name)
        if child is None:
            return False
        if not child.stop_requested:
            child.stop_requested = True
            child.status = "stopping"
            if child.process is not None:
                asyncio.get_running_loop().create_task(self._terminate(child))
        return True

    def drain(self) -> None:
        self.draining = True

    # -- lifecycle -------------------------------------------------------------

    def prepare(self) -> None:
        """One-shot at startup: private work root, stale trees removed."""
        if self.job_hook is not None:
            assert self.job_policy is not None
            check_hook(self.job_hook, self.job_uid, self.job_gid)
            write_job_policy(self.job_policy, self.job_repos)
            if not self.job_repos:
                log.warning(
                    "OMEGA_CI_AGENT_REPOS unset: the job guard refuses every job"
                )
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.work_root.chmod(0o711)
        for entry in self.work_root.iterdir():
            if RUNNER_NAME_RE.match(entry.name) and entry.name not in self.children:
                shutil.rmtree(entry, ignore_errors=True)

    async def shutdown(self) -> None:
        """Drain, terminate every child, await their cleanup + callbacks."""
        self.draining = True
        tasks = [c.task for c in self.children.values() if c.task is not None]
        for name in list(self.children):
            self.stop(name)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, child: Child, jit: str) -> None:
        workdir = self.work_root / child.name
        exit_code: int | None = SPAWN_FAILED
        try:
            await asyncio.to_thread(
                build_workdir, self.runner_root, workdir, self.job_uid, self.job_gid
            )
            if child.stop_requested:
                return
            child.process = await self._spawn(
                str(workdir / "run.sh"),
                cwd=str(workdir),
                env=child_env(self._base_env, jit, workdir, self.job_hook),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                user=self.job_uid,
                group=self.job_gid,
                extra_groups=[] if self.job_gid is not None else None,
                preexec_fn=limit_child,
            )
            del jit
            child.pid = child.process.pid
            if child.stop_requested:
                asyncio.get_running_loop().create_task(self._terminate(child))
            else:
                child.status = "running"
            exit_code = await child.process.wait()
        except asyncio.CancelledError:
            if child.process is not None and child.process.returncode is None:
                await self._terminate(child)
                exit_code = child.process.returncode
        except Exception as exc:
            log.warning("runner %s failed to start: %s", child.name, type(exc).__name__)
        finally:
            code = SPAWN_FAILED if exit_code is None else exit_code
            child.exited.set()
            await asyncio.to_thread(shutil.rmtree, workdir, True)
            self._remember_finished(child, code)  # before the live record goes
            self.children.pop(child.name, None)
            await self._send_exit(child, code)

    def _remember_finished(self, child: Child, exit_code: int) -> None:
        self._finished[child.name] = (child.generation, exit_code)
        self._finished.move_to_end(child.name)
        while len(self._finished) > FINISHED_MEMORY:
            self._finished.popitem(last=False)

    async def _terminate(self, child: Child) -> None:
        process = child.process
        if process is None or process.returncode is not None:
            return
        self._signal(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), self._grace)
        except TimeoutError:
            self._signal(process.pid, signal.SIGKILL)
            await process.wait()

    @staticmethod
    def _signal(pid: int, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, sig)

    async def _send_exit(self, child: Child, exit_code: int) -> None:
        payload = {
            "type": "agent.runner_exited",
            "name": child.name,
            "generation": child.generation,
            "exit_code": exit_code,
        }
        self.notified += 1
        try:
            await self._notify(payload)
        except Exception as exc:
            log.warning(
                "exit callback for %s failed: %s", child.name, type(exc).__name__
            )


__all__ = [
    "ENV_ALLOWLIST",
    "FINISHED_MEMORY",
    "HOOK_ENV",
    "JIT_ENV",
    "SPAWN_FAILED",
    "Admission",
    "Child",
    "Supervisor",
    "UnsafeCredentialFile",
    "UnsafeJobHook",
    "UnsafeWorkdir",
    "build_workdir",
    "check_hook",
    "check_private",
    "child_env",
    "limit_child",
    "write_job_policy",
]

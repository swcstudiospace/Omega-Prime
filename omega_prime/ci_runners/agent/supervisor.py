"""Ephemeral runner child lifecycle: reserve → isolate → spawn → wait → clean → notify.

One reservation is one ``run.sh`` child. The JIT config crosses into the child
only as ``ACTIONS_RUNNER_INPUT_JITCONFIG`` in its scrubbed environment; it is
never written to disk, put on argv, logged, returned, or kept after spawn.

Completion is pushed by the child's own ``process.wait()``, which tracks the
process exit only (output goes through a pipe the agent owns, so a leftover
helper holding it cannot delay the exit); the output drain after exit is
bounded by the grace period. There are no polling or sleep loops. Termination escalates SIGTERM → SIGKILL once,
bounded by one ``wait_for``.

Isolation (documented, not overstated):

* With ``job_uid``/``job_gid`` set (the image does), children run as that
  dedicated non-root identity. Credential files must not be readable by it and
  ``/proc/<agent>/environ`` is unreadable across UIDs.
* Children share that UID, so per-runner workdirs (0700) are hygiene, not a
  confidentiality boundary between concurrent jobs. ``nice`` and rlimits are
  resource guards only. No Docker / privileged access is provided.
* The work root and every directory above it must be listable (r-x) by the
  job identity: actions/runner refuses to start otherwise ("Permission to read
  the directory contents is required for ... and each directory up the
  hierarchy") and exits 0 within a second. ``prepare()`` makes the work root
  0755 and checks the chain, so a bad image fails at agent startup instead of
  at every launch. Nothing secret lives in those directories.

Each child's stdout/stderr, and the tail of its ``_diag`` logs when it exits
without having taken a job, go to the agent log: bounded and redacted (secret
patterns plus any long opaque blob, so a JIT config can never be echoed).
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
from omega_prime.credentials.redact import REDACTED, redact_text

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
WORK_ROOT_MODE = 0o755  # listable by the job identity (actions/runner requires it)
LISTABLE = stat.S_IROTH | stat.S_IXOTH
# Child output relayed to the agent log: bounded per child, redacted per line.
LOG_MAX_LINES = 200
LOG_MAX_BYTES = 64 * 1024
LOG_LINE_CAP = 500
DIAG_TAIL_BYTES = 8 * 1024
DIAG_TAIL_LINES = 60
JOB_STARTED_MARKER = "Running job:"
_BLOB_RE = re.compile(r"[A-Za-z0-9+/=_.-]{64,}")
_PATH_SEGMENT_MAX = 32


def _blob(match: re.Match[str]) -> str:
    """Redact a long opaque run (JIT config, JWT, encoded key); keep paths.

    A path is kept only when it starts with ``/`` and every segment is short
    and free of base64 padding/plus signs, which a long base64 value almost
    never satisfies, so the runner's own directory errors stay readable.
    """
    text = match.group(0)
    if text.startswith("/") and all(
        len(part) <= _PATH_SEGMENT_MAX and "+" not in part and "=" not in part
        for part in text.split("/")
    ):
        return text
    return REDACTED


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
    output: asyncio.Task[None] | None = field(default=None, repr=False)
    took_job: bool = False  # the runner printed "Running job:"

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


def redact_runner_line(line: str, cap: int = LOG_LINE_CAP) -> str:
    """One runner output line, safe for the agent log.

    Known secret shapes (GitHub tokens, Bearer values, ``token=...``) plus any
    long opaque blob (a JIT config, JWT or encoded credential is one) become
    ``[REDACTED]``; control characters are dropped and the line is capped.
    """
    text = "".join(ch for ch in line if ch.isprintable() or ch == "\t")
    text = redact_text(text)
    text = _BLOB_RE.sub(_blob, text)
    if len(text) > cap:
        text = text[:cap] + "…"
    return text


def diag_tail(
    workdir: Path, max_bytes: int = DIAG_TAIL_BYTES, max_lines: int = DIAG_TAIL_LINES
) -> list[str]:
    """Redacted tail of the newest ``_diag`` log (empty when there is none).

    Reads only regular files directly in ``_diag`` (no symlinks followed, and
    the opened file itself must be regular: a FIFO or device swapped in after
    the listing is refused without blocking), and at most ``max_bytes`` from
    the end, so a job cannot make the agent read elsewhere, read a lot, or
    stall.
    """
    diag = workdir / "_diag"
    try:
        if not stat.S_ISDIR(os.lstat(diag).st_mode):
            return []
        logs = []
        for entry in os.scandir(diag):
            if entry.name.endswith(".log") and entry.is_file(follow_symlinks=False):
                logs.append((entry.stat(follow_symlinks=False).st_mtime, entry.path))
        if not logs:
            return []
        newest = max(logs)[1]
        # O_NONBLOCK: a job owns ``_diag`` and can swap the checked file for a
        # FIFO before the open; a blocking open of a writer-less FIFO would
        # never return and hold the slot. The opened file is then re-checked.
        fd = os.open(newest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode):
                return []
            size = info.st_size
            fh.seek(max(0, size - max_bytes))
            raw = fh.read(max_bytes)
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if len(raw) == max_bytes and lines:
        lines = lines[1:]  # first line is probably cut mid-way
    return [redact_runner_line(line) for line in lines[-max_lines:] if line.strip()]


def ensure_listable_chain(path: Path, top: Path | None = None) -> None:
    """Fail closed unless ``path`` and every ancestor are r-x for others.

    actions/runner checks read permission on its directory and each one up the
    hierarchy; without it the runner exits within a second (exit code 0).
    ``top`` (tests only) stops the walk at that ancestor, inclusive.
    """
    chain = [path, *path.parents]
    if top is not None:
        chain = chain[: chain.index(top) + 1]
    for directory in chain:
        try:
            mode = directory.stat().st_mode
        except OSError as exc:
            raise UnsafeWorkdir(
                f"{directory}: not readable ({type(exc).__name__})"
            ) from exc
        if mode & LISTABLE != LISTABLE:
            raise UnsafeWorkdir(
                f"{directory}: mode {stat.S_IMODE(mode):04o} is not listable by"
                " the job identity (needs o+rx; actions/runner refuses to start)"
            )


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
        self.work_root.chmod(WORK_ROOT_MODE)
        if self.job_uid is not None:
            ensure_listable_chain(self.work_root)
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
        output: asyncio.ReadTransport | None = None
        try:
            await asyncio.to_thread(
                build_workdir, self.runner_root, workdir, self.job_uid, self.job_gid
            )
            if child.stop_requested:
                return
            # Our own pipe, not asyncio's PIPE: with PIPE, process.wait() does
            # not return until every holder of the write end closes it, so a
            # helper left alive by run.sh would keep the slot forever. Here
            # wait() tracks the process exit alone and the relay is drained
            # separately, bounded by the grace period.
            read_fd, write_fd = os.pipe()
            stream: asyncio.StreamReader | None = None
            try:
                stream, output = await self._open_output(read_fd)
                child.process = await self._spawn(
                    str(workdir / "run.sh"),
                    cwd=str(workdir),
                    env=child_env(self._base_env, jit, workdir, self.job_hook),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=write_fd,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                    user=self.job_uid,
                    group=self.job_gid,
                    extra_groups=[] if self.job_gid is not None else None,
                    preexec_fn=limit_child,
                )
            finally:
                os.close(write_fd)  # the child holds its own copy
            del jit
            child.pid = child.process.pid
            if child.stop_requested:
                asyncio.get_running_loop().create_task(self._terminate(child))
            else:
                child.status = "running"
            assert stream is not None
            child.output = asyncio.get_running_loop().create_task(
                self._relay(child, stream)
            )
            exit_code = await child.process.wait()
            await self._drain_output(child)
        except asyncio.CancelledError:
            if child.process is not None and child.process.returncode is None:
                await self._terminate(child)
                exit_code = child.process.returncode
        except Exception as exc:
            log.warning("runner %s failed to start: %s", child.name, type(exc).__name__)
        finally:
            code = SPAWN_FAILED if exit_code is None else exit_code
            child.exited.set()
            if child.output is not None and not child.output.done():
                child.output.cancel()
            if output is not None:
                output.close()
            if (
                child.process is not None
                and not child.took_job
                and not child.stop_requested
            ):
                await self._log_diag(child, workdir, code)
            await asyncio.to_thread(shutil.rmtree, workdir, True)
            self._remember_finished(child, code)  # before the live record goes
            self.children.pop(child.name, None)
            await self._send_exit(child, code)

    @staticmethod
    async def _open_output(
        read_fd: int,
    ) -> tuple[asyncio.StreamReader, asyncio.ReadTransport]:
        """Async reader over the read end of the child's output pipe.

        Takes ownership of ``read_fd``: it is closed here on failure and by
        closing the returned transport otherwise.
        """
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        pipe = os.fdopen(read_fd, "rb", buffering=0, closefd=True)
        try:
            transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), pipe
            )
        except BaseException:
            pipe.close()
            raise
        return reader, transport

    async def _drain_output(self, child: Child) -> None:
        """After run.sh exits, give its output ``grace`` seconds to finish.

        If the pipe is still open then, a leftover process in the runner's
        session holds it: stop that session and give up on the rest, so the
        slot is released and the exit reported either way.
        """
        if child.output is None:
            return
        done, _ = await asyncio.wait({child.output}, timeout=self._grace)
        if done:
            return
        log.warning(
            "runner %s exited but its output stayed open for %.0fs; stopping"
            " leftover processes in its session",
            child.name,
            self._grace,
        )
        if child.pid is not None:
            self._signal(child.pid, signal.SIGKILL)
        child.output.cancel()

    async def _relay(self, child: Child, stream: asyncio.StreamReader) -> None:
        """Copy child output to the agent log, bounded; always drain the pipe."""
        lines = 0
        size = 0
        truncated = False
        while True:
            try:
                raw = await stream.readline()
            except ValueError:  # one line over the reader's limit: skip it
                raw = b"\n"
            except Exception:
                return
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if JOB_STARTED_MARKER in text:
                child.took_job = True
            if lines >= LOG_MAX_LINES or size >= LOG_MAX_BYTES:
                if not truncated:
                    truncated = True
                    log.info("runner %s: further output not logged", child.name)
                continue  # keep draining so the child never blocks on the pipe
            safe = redact_runner_line(text)
            if safe:
                lines += 1
                size += len(safe)
                log.info("runner %s: %s", child.name, safe)

    async def _log_diag(self, child: Child, workdir: Path, code: int) -> None:
        try:  # optional logging never holds up cleanup
            tail = await asyncio.wait_for(
                asyncio.to_thread(diag_tail, workdir), self._grace
            )
        except TimeoutError:
            tail = []
        log.warning(
            "runner %s exited (code %s) before taking a job; _diag tail follows"
            " (%d lines)",
            child.name,
            code,
            len(tail),
        )
        for line in tail:
            log.warning("runner %s diag: %s", child.name, line)

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
    "diag_tail",
    "ensure_listable_chain",
    "limit_child",
    "redact_runner_line",
    "write_job_policy",
]

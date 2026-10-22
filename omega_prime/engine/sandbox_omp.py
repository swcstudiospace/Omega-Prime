# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Anda stdio client for the omp-sandbox engine backend.

The backend is selected only by ``OMEGA_PRIME_OMP_BACKEND=omp-sandbox``
(``selected``). One ``omp-sandbox-anda client`` wrapper process per run
speaks newline JSON on stdio: requests are ``{"id","tool","args"}`` lines,
replies are ``{"id","ok",...}`` (``ok:false`` carries ``error`` + ``code``),
unsolicited ``{"event", ...}`` lines interleave with replies, the first
line is ``{"event":"wrapper_ready", ...}``, and the ``down`` reply lands
before the wrapper exits (client.rs drains in-flight replies first).

Every wrapper failure — missing binary, non-zero exit, protocol garbage,
EOF mid-run, or any ``ok:false`` reply including ``path_refused`` and tier
errors — raises ``SandboxUnavailable``. There is no bwrap fallback
(GRACEFUL_DEGRADATION): an omp-sandbox outage is a sandbox failure, never
a silent switch of sandbox.

``poll_events`` is wrapper-internal (its event pump) and is never sent by
this client. Bounded waits cover only the ``wrapper_ready`` handshake and
individual tool replies; the stage wait itself has no deadline — liveness
is the wrapper's event stream, and a set interrupt aborts the stage
explicitly. This module is stdlib-only and never imports from
``omp-sandbox``.

Result contract (the frozen EngineResult, no field changes): ``ok`` comes
from the terminal ``stage_done`` status being ``completed``; ``final_text``
from the stage record's full ``finalText`` (``lastText`` is only a status
preview); ``plan``/``provider``/``model``/``fallback_from`` name what
actually ran — the router Choice, or the approved fallback plan when the
stage record shows the sandbox fell back; ``stage`` mirrors the Choice;
``sandbox`` is ``"omp-sandbox"``; ``usage`` is empty and ``cost`` None (the
wrapper surfaces no usage); ``commands`` lists the wrapper tools called in
order. Exported commits are fast-forwarded onto the host branch and ride
the free-form stats dict under documented keys:
``stats["omp_sandbox"] = {"stage_id": <stageId>, "tier": <up result tier
or None>, "status": <final stage_done status>, "model_requested": <the
model the stage ended on>, "export": <export_commits result dict or None>,
"applied": <{"applied", "head"} after the host fast-forward, or None>}``.
Nothing else is stored there, and no env value is ever printed or logged
(presence/truthiness only).
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.dashboard import herdr_launch, run_rows
from omega_prime.dashboard.blocks import clear_block, raise_block
from omega_prime.dashboard.status_file import locked_tasks
from omega_prime.engine.jobs import append_event, child_env, state_dir, write_json
from omega_prime.engine.omp_driver import EngineError, EngineResult, _system
from omega_prime.engine.plan_router import (
    PlanNotAllowed,
    evaluate_all,
    read_usage,
    select,
    stage_fallback,
)
from omega_prime.engine.sandbox import SandboxUnavailable

BACKEND_ENV = "OMEGA_PRIME_OMP_BACKEND"
BACKEND_NAME = "omp-sandbox"
ANDA_BIN_ENV = "OMEGA_PRIME_OMP_SANDBOX_ANDA"
# The omp-sandbox package root (holds src/core/server.ts). The wrapper starts
# the TS core relative to it, and falls back to its own cwd when unset.
ROOT_ENV = "OMP_SANDBOX_ROOT"
# Stage a stageless call runs as: run_gsd_stage has no stageless form, and a
# plain engine turn (route_turn, delegate, ultrathink) is execution work.
DEFAULT_STAGE = "execute"
WORKSPACE_ROOTS_ENV = "OMP_SANDBOX_WORKSPACE_ROOTS"
# Same default as omp-sandbox/src/core/paths.ts WORKSPACE_ROOTS.
DEFAULT_WORKSPACE_ROOTS = "/workspace/omes-bot-wt"

# Bounded waits, sized like omp_driver's _READY_TIMEOUT/_COMMAND_TIMEOUT
# with head-room for `up` (auth broker, sandbox tier, agentOS VM, GSD-Core
# — the core's own internal bounds run to 300s). Only the handshake and
# per-tool replies are bounded; the stage wait never times out.
_WRAPPER_READY_TIMEOUT = 30.0
_TOOL_TIMEOUT = 300.0
# `up` and `export_commits` run the core's own long bounded steps, so their
# replies get deadlines that cover those steps instead of the per-tool one:
# a cold `up` can build the Podman image (1800s), create the sandbox (600s),
# try the GSD install twice (600s each) and upload the seed (600s); an
# export commits leftovers and reads HEAD (120s each), bundles (300s) and
# downloads (600s).
_UP_TIMEOUT = 1800.0 + 600.0 + 2 * 600.0 + 600.0 + 600.0
_EXPORT_TIMEOUT = 120.0 + 120.0 + 120.0 + 300.0 + 600.0 + 300.0
# The wrapper exits shortly after replying to `down`; grace before terminate.
_EXIT_GRACE = 10.0
# Wake-up tick for waits that must also notice an interrupt.
_POLL_TICK = 0.2
# Answer store-read cadence for a raised GSD question: one shared-lock
# snapshot per second at most, riding these same event-loop ticks. The
# question/stage wait itself has NO deadline (phase-112 lock).
_ANSWER_POLL = 1.0

_EOF = object()


def anda_binary(env: Mapping[str, str]) -> str | None:
    """The wrapper binary: an explicit ``OMEGA_PRIME_OMP_SANDBOX_ANDA`` file
    or ``omp-sandbox-anda`` from PATH. ``None`` when neither resolves;
    never raises."""
    explicit = str(env.get(ANDA_BIN_ENV, "")).strip()
    if explicit:
        path = Path(explicit)
        try:
            if path.is_file() and path.stat().st_mode & 0o111 != 0:
                return str(path)
        except OSError:
            return None
        return None
    return shutil.which("omp-sandbox-anda", path=env.get("PATH") or None)


def sandbox_root(env: Mapping[str, str]) -> Path | None:
    """The omp-sandbox package root the wrapper must start from.

    ``OMP_SANDBOX_ROOT`` when set, otherwise the ``omp-sandbox`` directory
    shipped beside this package. Either one must hold
    ``src/core/server.ts``; ``None`` when neither does, so readiness and
    the run refuse instead of starting a wrapper that would look for the
    core under the caller's directory.
    """
    explicit = str(env.get(ROOT_ENV, "")).strip()
    candidate = (
        Path(explicit)
        if explicit
        else Path(__file__).resolve().parents[2] / "omp-sandbox"
    )
    try:
        if candidate.is_absolute() and (candidate / "src/core/server.ts").is_file():
            return candidate.resolve()
    except OSError:
        return None
    return None


#: Plans the omp-sandbox core can route (its ``PLAN_MODELS``). The router may
#: pick another plan (Anthropic with only its key set), which the core would
#: reject only after a sandbox was started, so readiness and selection refuse
#: it up front.
SANDBOX_PLANS = ("chatgpt", "zai")


def check_backend_plan(plan: str) -> None:
    """Refuse a selected plan the omp-sandbox core cannot run."""
    if plan not in SANDBOX_PLANS:
        raise PlanNotAllowed(
            f"plan {plan!r} is not supported by the omp-sandbox backend; "
            f"expected one of {SANDBOX_PLANS}"
        )


def fallback_model(env: Mapping[str, str], stage: str, plan: str | None) -> str | None:
    """``provider/model`` the approved fallback ``plan`` resolves to for ``stage``.

    The same resolution the router uses for a primary choice, so operator
    model overrides (for example ``OMEGA_PRIME_OMP_MODEL_ZAI_EXECUTE``) apply
    to a sandbox-side fallback too instead of the core's built-in default.
    """
    if plan is None:
        return None
    match = next((item for item in evaluate_all(env, stage) if item.plan == plan), None)
    return None if match is None else f"{match.provider}/{match.model_id}"


def backend_stage(stage: str | None) -> str:
    """The stage this backend runs: the requested one, else ``execute``."""
    return DEFAULT_STAGE if stage is None else stage


def selected(env: Mapping[str, str]) -> bool:
    """Whether ``OMEGA_PRIME_OMP_BACKEND`` selects this backend.

    One explicit knob: the value must be exactly ``omp-sandbox``; unset or
    any other value keeps the bwrap path byte-identical. Selection also
    wins over the unsandboxed override — an explicit choice of a different
    sandbox is not an escape hatch.
    """
    return str(env.get(BACKEND_ENV, "")).strip() == BACKEND_NAME


def workspace_roots(env: Mapping[str, str]) -> list[str]:
    """Colon-split ``OMP_SANDBOX_WORKSPACE_ROOTS`` (paths.ts semantics)."""
    raw = env.get(WORKSPACE_ROOTS_ENV)
    text = DEFAULT_WORKSPACE_ROOTS if raw is None else str(raw)
    return [root for root in text.split(":") if root]


def allowed_worktree(work: Path, env: Mapping[str, str]) -> Path:
    """Engine-side admission for the job worktree, before any spawn.

    Mirrors paths.ts ``allowedDir``: absolute, no ``..`` component, and
    after ``resolve()`` strictly inside one allowlisted root. A refused
    worktree raises SandboxUnavailable with ``path_refused`` in the detail;
    the TS core re-validates the same knob in its own ``allowedDir``
    (defense in depth, T-112-04).
    """
    roots = workspace_roots(env)
    what = "the job worktree"
    if not str(work):
        raise SandboxUnavailable(
            f"path_refused: {what} must be a non-empty path ({WORKSPACE_ROOTS_ENV})"
        )
    if not work.is_absolute():
        raise SandboxUnavailable(
            f"path_refused: {what} must be an absolute path ({WORKSPACE_ROOTS_ENV})"
        )
    if ".." in work.parts:
        raise SandboxUnavailable(
            f"path_refused: {what} must not contain '..' ({WORKSPACE_ROOTS_ENV})"
        )
    try:
        real = work.resolve()
    except OSError as exc:
        raise SandboxUnavailable(
            f"path_refused: {what} does not resolve ({WORKSPACE_ROOTS_ENV})"
        ) from exc
    if not real.is_dir():
        raise SandboxUnavailable(
            f"path_refused: {what} is not a directory ({WORKSPACE_ROOTS_ENV})"
        )
    for root in roots:
        try:
            root_real = Path(root).resolve()
        except OSError:
            continue
        rel = os.path.relpath(real, root_real)
        if (
            rel not in (".", "..")
            and not rel.startswith(".." + os.sep)
            and not os.path.isabs(rel)
        ):
            return real
    raise SandboxUnavailable(
        f"path_refused: {what} is not under an allowlisted "
        f"{WORKSPACE_ROOTS_ENV} root ({', '.join(roots)})"
    )


def wrapper_env(env: Mapping[str, str]) -> dict[str, str]:
    """Child env for the wrapper: ``child_env`` plus every OMP_SANDBOX_* key.

    ``child_env`` copies only named keys, so the TS core's own knobs —
    ``OMP_SANDBOX_WORKSPACE_ROOTS`` among them — would otherwise be
    dropped; forwarding them is how the engine-side allowlist value reaches
    the core's independent re-validation. Values are never logged.
    """
    child = child_env(env)
    for name, value in env.items():
        if name.startswith("OMP_SANDBOX_") and isinstance(value, str) and value:
            child[name] = value
    return child


def _seed_ref(work: Path) -> str:
    """The worktree's branch name for ``up``'s ``seed_repo`` argument.

    One ``git symbolic-ref`` subprocess, no shell. Job worktrees are always
    git checkouts on a branch; anything else refuses before the wrapper
    spawns (export_commits needs the seeded repo).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(work), "symbolic-ref", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable(
            f"seed ref derivation failed: {type(exc).__name__}"
        ) from exc
    if proc.returncode != 0:
        raise SandboxUnavailable(
            "seed ref derivation failed: git symbolic-ref --short HEAD was refused"
        )
    ref = proc.stdout.strip()
    if not ref:
        raise SandboxUnavailable("seed ref derivation failed: git named no branch")
    return ref


class AndaClient:
    """One ``omp-sandbox-anda client`` process and its stdio session.

    A daemon reader thread parses each stdout line as JSON onto a queue
    (EOF, a decode failure, or a non-object line puts a sentinel). Replies
    are matched strictly by the echoed ``id``; unsolicited ``{"event",...}``
    lines are buffered for ``drain_events()`` and are never mistaken for
    replies. Every failure mode — bad first line, garbage, EOF, non-zero
    exit, ``ok:false`` — raises ``SandboxUnavailable`` with the error and
    code in the detail. The client tolerates the ``attestation`` stamp on
    every line, never interprets it, and never writes received text
    anywhere.
    """

    def __init__(self, binary: str, env: Mapping[str, str]) -> None:
        self.binary = binary
        self.env = dict(env)
        self.proc: subprocess.Popen[str] | None = None
        self.events: list[dict[str, Any]] = []
        # Every stage_done seen on any path (a tool reply wait, an event
        # wait), keyed by stageId, so a terminal event that lands while a
        # call is in flight is never lost to the stage waiter.
        self.terminal: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[Any] = queue.Queue()
        self._reader_error: BaseException | None = None
        self._stderr_parts: list[str] = []
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._next_id = 0

    def start(self, cwd: Path | None = None) -> None:
        """Spawn ``<binary> client`` in ``cwd`` and start the reader threads."""
        self.proc = subprocess.Popen(
            [self.binary, "client"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self.env,
            cwd=str(cwd) if cwd is not None else None,
        )
        self._stdout_thread = threading.Thread(target=self._pump_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def wait_ready(self, timeout: float | None = None) -> dict[str, Any]:
        """Consume the first protocol line; it must announce ``wrapper_ready``.

        Any other first line, a decode failure, EOF, or a wrapper that
        already exited non-zero is a SandboxUnavailable.
        """
        bound = _WRAPPER_READY_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + bound
        first = self._take(deadline, "the wrapper_ready handshake")
        if first.get("event") != "wrapper_ready":
            raise SandboxUnavailable(
                "anda wrapper's first protocol line was not wrapper_ready"
            )
        return first

    def call(
        self,
        tool: str,
        args: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send one tool request and return its ``result`` (``ok:true``).

        Interleaved event lines are buffered; the first reply whose echoed
        ``id`` matches wins. ``ok:false`` raises SandboxUnavailable with
        ``"<tool> failed: <error> (code <code>)"``; a bounded-wait timeout,
        EOF, or a dead wrapper raises SandboxUnavailable naming the tool.
        """
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise SandboxUnavailable(
                f"{tool} was not sent: the anda wrapper is not running"
            )
        self._next_id += 1
        request_id = str(self._next_id)
        payload = json.dumps(
            {"id": request_id, "tool": tool, "args": dict(args or {})},
            ensure_ascii=False,
        )
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise SandboxUnavailable(
                f"{tool} was not sent: {type(exc).__name__}"
            ) from exc
        bound = _TOOL_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + bound
        while True:
            message = self._take(deadline, f"the {tool} reply")
            if "event" in message and "id" not in message:
                self._buffer(message)
                continue
            if str(message.get("id") or "") == request_id:
                if message.get("ok") is not True:
                    raise SandboxUnavailable(
                        f"{tool} failed: {message.get('error')} "
                        f"(code {message.get('code')})"
                    )
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            # A reply for an id we never sent is ignored rather than guessed
            # at (replies are only meaningful while their call is in flight).
            continue

    def next_event(self, tick: float) -> dict[str, Any] | None:
        """Next unsolicited event dict, blocking up to ``tick`` seconds.

        Returns ``None`` when the tick elapsed without an event — the
        caller decides what a quiet tick means (interrupt checks live
        there). EOF, protocol garbage, or a dead wrapper raises
        SandboxUnavailable, so a stage wait can never hang on a dead
        wrapper; it only ever stops on events, EOF, or an explicit abort.
        """
        deadline = time.monotonic() + max(tick, 0.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self._queue.get(timeout=min(_POLL_TICK, remaining))
            except queue.Empty:
                continue
            if message is _EOF:
                self._raise_stream_failure()
            if "event" in message and "id" not in message:
                self._buffer(message)
                return message
            # Stray non-event lines (a reply outside any in-flight call) are
            # ignored rather than guessed at.
            continue

    def _buffer(self, message: dict[str, Any]) -> None:
        self.events.append(message)
        if message.get("event") == "stage_done":
            self.terminal[str(message.get("stageId") or "")] = message

    def drain_events(self) -> list[dict[str, Any]]:
        """Hand over the buffered event dicts and empty the buffer."""
        drained, self.events = self.events, []
        return drained

    def down(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Send ``down``, then close stdin and wait for the wrapper to exit.

        The wrapper replies to ``down`` and exits afterwards, so the reply
        is the contract; a missing reply or a dead process is tolerated
        (errors are swallowed once the attempt is recorded) and the
        bounded exit wait plus terminate fallback finish the job. Returns
        the down result dict, or None when no reply arrived.
        """
        reply: dict[str, Any] | None = None
        try:
            reply = self.call("down", {}, timeout)
        except SandboxUnavailable:
            reply = None
        self._shutdown()
        return reply

    def close(self) -> None:
        """Kill a still-live wrapper without raising."""
        proc = self.proc
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

    def stderr_tail(self) -> str:
        return "".join(self._stderr_parts)[-2000:]

    def _shutdown(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                proc.stdin.close()
        try:
            proc.wait(timeout=_EXIT_GRACE)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    proc.kill()

    def _take(self, deadline: float, what: str) -> dict[str, Any]:
        """Next protocol dict, bounded by ``deadline``; sentinel → failure."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxUnavailable(f"timed out waiting for {what}")
            try:
                message = self._queue.get(timeout=min(_POLL_TICK, remaining))
            except queue.Empty:
                continue
            if message is _EOF:
                self._raise_stream_failure()
            return message

    def _raise_stream_failure(self) -> None:
        if self._reader_error is not None:
            raise SandboxUnavailable(
                "anda wrapper stdout reader failed: "
                f"{type(self._reader_error).__name__}"
            ) from self._reader_error
        status = self.proc.poll() if self.proc is not None else None
        if status is not None and status != 0:
            raise SandboxUnavailable(f"anda wrapper exited with status {status}")
        raise SandboxUnavailable(
            "anda wrapper stdout closed before the protocol completed"
        )

    def _pump_stdout(self) -> None:
        stream = self.proc.stdout if self.proc is not None else None
        if stream is None:
            self._queue.put(_EOF)
            return
        try:
            for line in stream:
                text = line.strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except ValueError:
                    self._queue.put(_EOF)
                    return
                if not isinstance(parsed, dict):
                    self._queue.put(_EOF)
                    return
                self._queue.put(parsed)
        except BaseException as exc:
            self._reader_error = exc
        finally:
            self._queue.put(_EOF)

    def _pump_stderr(self) -> None:
        stream = self.proc.stderr if self.proc is not None else None
        if stream is None:
            return
        try:
            for line in stream:
                if sum(len(part) for part in self._stderr_parts) < 8000:
                    self._stderr_parts.append(line)
        except (OSError, ValueError):
            pass


def run_backend(
    prompt: str,
    *,
    job_id: str,
    directory: Path,
    cwd: str | Path | None,
    system_prompt: str | None,
    thinking: str,
    plan: str | None,
    mode: str,
    env: Mapping[str, str],
    binary: str,
    omp_major: int,
    stage: str | None,
    interrupt: Any | None,
    tasks_path: str | Path | None = None,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EngineResult:
    """One engine stage run through the omp-sandbox wrapper.

    Admission order before any wrapper spawn: the worktree is allowlist-
    checked and must be clean (the sandbox receives the committed branch,
    so uncommitted edits would be silently left out), the seed ref is
    derived from it, ``read_usage``/``select`` run exactly as on the bwrap
    path (so ``PlanNotAllowed`` raises pre-spawn), and the wrapper's package
    root must resolve. A stageless call runs as ``execute``
    (``backend_stage``). The wrapper then seeds the worktree (``up`` with
    ``seed_repo``), runs the stage (``run_gsd_stage`` returns immediately,
    with the only fallback plan Python approved), waits for the matching
    ``stage_done`` event with no deadline (a set interrupt aborts the stage
    explicitly and raises ``cancelled``), reads the stage record from
    ``sandbox_status``, exports commits after a completed stage (edits it
    left uncommitted are committed first, ``include_dirty``; any that still
    remain make the run ``export_dirty`` rather than a success) and
    fast-forwards the host branch onto them. ``down`` always runs in the
    finally, followed by the down-summary job event. Every wrapper failure
    raises ``SandboxUnavailable``; there is no bwrap fallback. Once the
    stage was submitted, every failure keeps ``started=True`` (through
    ``metadata``) so callers never re-run the same task elsewhere. A stage
    that ends ``failed``/``aborted`` returns ``ok=False`` with reason
    ``engine`` instead of raising.
    ``tasks_path``/``task_id`` are optional dashboard context (keyword-only,
    default ``None``): both set switches on the visibility wiring (Herdr
    pane, tasks.json row, question round-trip); with either absent the run
    is unchanged. See the module docstring for the stats keys and
    session.json/events parity.
    """
    run_metadata = metadata if metadata is not None else {}
    tracked = tasks_path is not None and task_id is not None
    # The receipt in the finally must be buildable on every path, and only a
    # row that run_rows.start actually created (after the up reply) is ever
    # touched or finished — never the existing `started`, which is set only
    # after run_gsd_stage returns and would strand a started row.
    stage_id: str | None = None
    row_started = False
    work = allowed_worktree(Path(cwd).resolve() if cwd is not None else Path.cwd(), env)
    seed_ref = _seed_ref(work)
    _refuse_dirty(work)
    stage = backend_stage(stage)
    usage = read_usage(binary, env)
    choice = select(env, usage, requested=plan, stage=stage)
    check_backend_plan(choice.decision.plan)
    fallback_plan = stage_fallback(env, usage, choice)
    fallback_model_id = fallback_model(env, stage, fallback_plan)
    anda = anda_binary(env)
    if anda is None:
        raise SandboxUnavailable(
            f"{ANDA_BIN_ENV} is not set and omp-sandbox-anda is not on PATH"
        )
    root = sandbox_root(env)
    if root is None:
        raise SandboxUnavailable(
            f"{ROOT_ENV} does not name an omp-sandbox package (src/core/server.ts)"
        )
    events_path = directory / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.touch(exist_ok=True)
    gsd_command = "gsd-plan-phase" if stage == "plan" else "gsd-execute-phase"
    # system_prompt cannot ride an omp flag inside the sandbox, so it opens
    # the task text (same _system helper as the bwrap path).
    task_text = _system(system_prompt) + "\n\n" + prompt if system_prompt else prompt
    # Pre-spawn session.json with the bwrap key set (no task text, no env).
    write_json(
        directory / "session.json",
        {
            "job_id": job_id,
            "plan": choice.decision.plan,
            "provider": choice.decision.provider,
            "model": choice.decision.model_id,
            "stage": choice.stage,
            "fallback_from": choice.fallback_from,
            "sandbox": BACKEND_NAME,
            "mcp_json": "",
            "skills": "",
            "omp_major": omp_major,
            "mode": mode,
        },
    )
    child = wrapper_env(env)
    child[ROOT_ENV] = str(root)
    client = AndaClient(anda, child)
    commands: list[str] = []
    started = False
    outcome: dict[str, Any] | None = None
    # Events that arrive before the stage-start reply (the real wrapper
    # sends events and replies from separate tasks): the wait loop still
    # acts on them, a question among them included.
    early_events: list[dict[str, Any]] = []
    try:
        client.start(cwd=root)
        client.wait_ready()
        up_result = _tool(
            client,
            "up",
            {"seed_repo": {"path": str(work), "ref": seed_ref}},
            commands,
            events_path,
            timeout=_UP_TIMEOUT,
            tasks_path=tasks_path,
            task_id=task_id,
            row_started=row_started,
        )
        if tracked and tasks_path is not None and task_id is not None:
            # Visibility wiring (W113-01/W113-02), only once the stage is
            # actually launching: a run that fails at or before the up reply
            # starts no row — pre-stage visibility stays run()'s raise_block.
            # Every write is best-effort (omp_driver.py:385 discipline): a
            # missing herdr or an unwritable/corrupt store can never fail a
            # healthy stage.
            pane: str | None = None
            with contextlib.suppress(Exception):
                pane = herdr_launch.open_pane(
                    env,
                    cwd=work,
                    label=f"{task_id} {stage}",
                    command=[
                        sys.executable,
                        "-m",
                        "omega_prime.engine.watch",
                        job_id,
                        "--follow",
                        "--state-dir",
                        # Absolute: the pane runs in the worktree, so a
                        # relative state dir would name another feed.
                        str(state_dir(env).resolve()),
                    ],
                    session=f"omega-{job_id}",
                )
            with contextlib.suppress(Exception):
                # After start, inside the same suppress: a failed start leaves
                # the row un-started and the run un-finished. A start that
                # declined to take the row over (a finished record of this
                # run or of no known run) is not this run's row either, so
                # nothing later touches, finishes or re-receipts it.
                row_started = run_rows.start(
                    tasks_path, task_id, job_id=job_id, stage=stage, pane=pane
                )
        stage_result = _tool(
            client,
            "run_gsd_stage",
            {
                "stage": stage,
                "plan": choice.decision.plan,
                "model": f"{choice.decision.provider}/{choice.decision.model_id}",
                "fallback_plan": fallback_plan,
                "fallback_model": fallback_model_id,
                "gsd_command": gsd_command,
                "task": task_text,
                "thinking": thinking,
            },
            commands,
            events_path,
            tasks_path=tasks_path,
            task_id=task_id,
            row_started=row_started,
            drained=early_events,
        )
        started = True
        run_metadata["started"] = True
        stage_id = str(stage_result.get("stageId") or "")
        if not stage_id:
            raise SandboxUnavailable("run_gsd_stage returned no stageId")
        done_event = _wait_stage_done(
            client,
            stage_id,
            interrupt,
            events_path,
            tasks_path=tasks_path,
            task_id=task_id,
            row_started=row_started,
            stage=stage,
            early_events=early_events,
        )
        done_status = str(done_event.get("status") or "")
        status_record = _tool(
            client,
            "sandbox_status",
            {"stage_id": stage_id},
            commands,
            events_path,
            tasks_path=tasks_path,
            task_id=task_id,
            row_started=row_started,
        )
        export: dict[str, Any] | None = None
        applied: dict[str, Any] | None = None
        if done_status == "completed":
            # include_dirty: edits the stage left uncommitted ride one extra
            # commit, so finished work is never torn down with the sandbox.
            export = _tool(
                client,
                "export_commits",
                {"include_dirty": True},
                commands,
                events_path,
                timeout=_EXPORT_TIMEOUT,
                tasks_path=tasks_path,
                task_id=task_id,
                row_started=row_started,
            )
            applied = _apply_export(work, seed_ref, export)
            if export.get("dirty"):
                raise EngineError(
                    "export_dirty",
                    "the sandbox still had uncommitted edits after the export "
                    "commit; the stage is not reported as done",
                    started=True,
                )
        tier = up_result.get("tier")
        final_text = status_record.get("finalText")
        if not isinstance(final_text, str):
            raise SandboxUnavailable(
                "sandbox_status returned no finalText for the stage"
            )
        outcome = {
            "ok": done_status == "completed",
            "status": done_status,
            "error": status_record.get("error"),
            "final_text": final_text,
            "stage_id": stage_id,
            "tier": tier if isinstance(tier, str) else None,
            "model_requested": status_record.get("modelRequested")
            or stage_result.get("model"),
            "fallback_used": bool(status_record.get("fallbackFrom")),
            "export": export,
            "applied": applied,
        }
    except (EngineError, SandboxUnavailable) as exc:
        if started:
            # Submitted means started, on every later failure path too, so
            # no caller re-runs the same task elsewhere.
            setattr(exc, "started", True)  # noqa: B010 - SandboxUnavailable has no field
        raise
    finally:
        # Terminal row before the shutdown: it lands even if `down` is slow.
        # Gated on row_started, not tracked — a run that never started its
        # row (tier error at the up reply, pre-spawn plan-gate refusal)
        # writes nothing; that visibility is run()'s raise_block.
        run_ok = bool(outcome["ok"]) if outcome is not None else False
        if row_started and tasks_path is not None and task_id is not None:
            with contextlib.suppress(Exception):
                run_rows.finish(
                    tasks_path,
                    task_id,
                    job_id=job_id,
                    ok=run_ok,
                    receipt={
                        "job_id": job_id,
                        "stage_id": stage_id,
                        "ok": run_ok,
                        "status": (
                            str(outcome["status"]) if outcome is not None else "failed"
                        ),
                        "export": (
                            outcome.get("export") if outcome is not None else None
                        ),
                    },
                )
        down_summary = client.down()
        commands.append("down")
        _append_down_event(events_path, outcome, down_summary)
        client.close()
    if outcome is None:
        raise SandboxUnavailable("the omp-sandbox stage ended without a terminal event")
    ok = bool(outcome["ok"])
    ran_plan = choice.decision.plan
    provider = choice.decision.provider
    model = choice.decision.model_id
    fallback_from = choice.fallback_from
    if outcome["fallback_used"]:
        # The sandbox retried on the approved fallback plan: report what ran.
        ran_plan = fallback_plan or ran_plan
        provider, _, model = str(outcome["model_requested"]).partition("/")
        fallback_from = choice.decision.plan
    result = EngineResult(
        ok=ok,
        job_id=job_id,
        final_text=str(outcome["final_text"]),
        usage={},
        cost=None,
        plan=ran_plan,
        provider=provider,
        model=model,
        stage=choice.stage,
        fallback_from=fallback_from,
        events_path=str(events_path),
        reason=None if ok else "engine",
        detail=""
        if ok
        else str(outcome["error"] or outcome["status"] or "stage did not complete"),
        stats={
            "omp_sandbox": {
                "stage_id": outcome["stage_id"],
                "tier": outcome["tier"],
                "status": outcome["status"],
                "model_requested": outcome["model_requested"],
                "export": outcome["export"],
                "applied": outcome["applied"],
            }
        },
        sandbox=BACKEND_NAME,
        commands=commands,
        started=started,
    )
    # Post-run session.json: the bwrap success key set with this run's truth.
    write_json(
        directory / "session.json",
        {
            "job_id": job_id,
            "ok": ok,
            "plan": result.plan,
            "provider": result.provider,
            "model": result.model,
            "stage": result.stage,
            "fallback_from": result.fallback_from,
            "sandbox": BACKEND_NAME,
            "mcp_json": "",
            "skills": "",
            "commands": commands,
            "cost": None,
            "final_text": result.final_text[:2000],
        },
    )
    return result


def _touch_row(
    tasks_path: str | Path | None,
    task_id: str | None,
    row_started: bool,
    event: dict[str, Any],
) -> None:
    """Best-effort ``run_rows.touch`` for one wrapper event.

    Only a row that ``run_rows.start`` actually created is touched; a
    missing/unwritable/corrupt store is suppressed — visibility never
    kills a run.
    """
    if not row_started or tasks_path is None or task_id is None:
        return
    with contextlib.suppress(Exception):
        run_rows.touch(tasks_path, task_id, event)


def _git(work: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """One git command in the job worktree; no shell, bounded."""
    try:
        return subprocess.run(
            ["git", "-C", str(work), *argv],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable(f"git {argv[0]} failed: {type(exc).__name__}") from exc


def _refuse_dirty(work: Path) -> None:
    """Refuse a worktree with uncommitted or untracked changes.

    The sandbox is seeded from the committed branch, so local edits would be
    silently missing from the run (and the fast-forward afterwards could
    clobber them).
    """
    proc = _git(work, "status", "--porcelain", "--untracked-files=normal")
    if proc.returncode != 0:
        raise SandboxUnavailable("git status failed in the job worktree")
    if proc.stdout.strip():
        raise EngineError(
            "dirty_worktree",
            "the job worktree has uncommitted or untracked changes; the "
            "omp-sandbox backend runs the committed branch, so commit or "
            "stash them first",
        )


def _apply_export(work: Path, ref: str, export: Mapping[str, Any]) -> dict[str, Any]:
    """Fast-forward the host branch onto the sandbox's exported commits.

    The export bundle carries ``base..head`` of ``ref``. The host branch must
    still be checked out and sit at ``base`` (nothing else moved it during
    the run), the fetched tip must equal the reported ``head``, and the
    update is ``merge --ff-only`` so no host commit or edit is overwritten.
    A refused application is ``export_conflict`` with ``started=True``; the
    bundle stays on disk for a manual merge.
    """
    bundle = export.get("bundle")
    head = str(export.get("head") or "")
    base = str(export.get("base") or "")
    if not bundle:
        return {"applied": False, "head": head, "note": "no new commits"}

    def conflict(detail: str) -> EngineError:
        return EngineError(
            "export_conflict",
            f"exported commits were not applied to {ref}: {detail} "
            f"(bundle kept at {bundle})",
            started=True,
        )

    branch = _git(work, "symbolic-ref", "--short", "HEAD")
    if branch.returncode != 0 or branch.stdout.strip() != ref:
        raise conflict("the worktree is no longer on that branch")
    host = _git(work, "rev-parse", "HEAD")
    if host.returncode != 0 or host.stdout.strip() != base:
        raise conflict("the host branch moved since the sandbox was seeded")
    fetched = _git(work, "fetch", "-q", str(bundle), ref)
    if fetched.returncode != 0:
        raise conflict("git fetch from the bundle failed")
    tip = _git(work, "rev-parse", "FETCH_HEAD")
    if tip.returncode != 0 or tip.stdout.strip() != head:
        raise conflict("the bundle tip does not match the reported head")
    merged = _git(work, "merge", "--ff-only", "-q", head)
    if merged.returncode != 0:
        raise conflict("the fast-forward was refused")
    return {"applied": True, "head": head}


def _tool(
    client: AndaClient,
    tool: str,
    args: dict[str, Any],
    commands: list[str],
    events_path: Path,
    *,
    timeout: float | None = None,
    tasks_path: str | Path | None = None,
    task_id: str | None = None,
    row_started: bool = False,
    drained: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One wrapper tool call; events land in events.jsonl via append_event,
    the tool name in commands. ``timeout`` overrides the per-tool bound. Each
    drained event also touches the run's tasks.json row when one was started
    (best-effort, never fatal), and is appended to ``drained`` when given, so
    a caller can still act on it."""
    try:
        reply = client.call(tool, args, timeout)
    finally:
        for event in client.drain_events():
            append_event(events_path, event)
            _touch_row(tasks_path, task_id, row_started, event)
            if drained is not None:
                drained.append(event)
    commands.append(tool)
    return reply


def _question_item_id(task_id: str, request_id: str) -> str:
    """The stable waiting_on_ove item id for one GSD question."""
    return f"{task_id}:question:{request_id}"


def _question_text(event: Mapping[str, Any]) -> str:
    """The operator-facing ask for one wrapper ``question`` event.

    The wrapper (core.ts) sends omp's shape: ``questions`` (each with its
    ``question`` and option ``label``s), or a ``title`` with plain
    ``options``, and the ``method``. Each question keeps its choices.
    """
    parts: list[str] = []
    questions = event.get("questions")
    if isinstance(questions, list):
        for item in questions:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("question") or "").strip()
            raw = item.get("options")
            labels = [
                str(option.get("label") or "").strip()
                for option in (raw if isinstance(raw, list) else [])
                if isinstance(option, Mapping)
            ]
            labels = [label for label in labels if label]
            if labels:
                text = f"{text} (options: {' / '.join(labels)})".strip()
            if text:
                parts.append(text)
    if not parts:
        title = str(event.get("title") or "").strip()
        raw = event.get("options")
        options = [
            str(option).strip()
            for option in (raw if isinstance(raw, list) else [])
            if str(option).strip()
        ]
        if title and options:
            parts.append(f"{title} (options: {' / '.join(options)})")
        elif title:
            parts.append(title)
        elif options:
            parts.append(f"options: {' / '.join(options)}")
    if not parts:
        legacy = str(event.get("question") or "").strip()
        if legacy:
            parts.append(legacy)
    if not parts:
        method = str(event.get("method") or "").strip()
        parts.append(f"the sandbox stage asks for input ({method or 'question'})")
    return " | ".join(parts)


def _raise_question(
    tasks_path: str | Path | None,
    task_id: str | None,
    stage: str | None,
    request_id: str,
    question_text: Any,
    question_ids: list[str] | None = None,
) -> str:
    """Raise exactly one question item through the only block writer.

    The id embeds the wrapper's requestId, so a re-ask after a stale
    answer raises under a new id instead of duplicating; ask/blocks pass
    redact_text inside raise_block (the owning row goes ``blocked``).
    Returns the item id.
    """
    item_id = _question_item_id(str(task_id), request_id)
    raise_block(
        tasks_path,
        item_id,
        ask=str(question_text or ""),
        blocks=f"sandbox GSD question (stage {stage})",
        task_id=str(task_id),
        question_ids=question_ids,
    )
    return item_id


def _question_ids(event: Mapping[str, Any]) -> list[str]:
    """The ids of an ``ask`` event's questions, in order (empty for the
    single-prompt methods); an answer's ``per_question`` map keys by them."""
    questions = event.get("questions")
    if not isinstance(questions, list):
        return []
    return [
        str(item["id"])
        for item in questions
        if isinstance(item, Mapping) and str(item.get("id") or "")
    ]


def _answer_from_store(
    tasks_path: str | Path | None, item_id: str
) -> str | Mapping[str, Any] | None:
    """The item's additive ``answer`` field, or ``None`` when not answered.

    One shared-lock snapshot of the store. An answer is a text string, or a
    mapping carrying ``selected`` (a list), ``text`` (a string) or
    ``per_question`` (question id -> text string or ``selected``/``text``
    mapping, one separate answer per question of a multi-question ask).
    Anything else is not an answer yet.
    """
    if tasks_path is None:
        return None
    with locked_tasks(tasks_path) as data:
        item = next(
            (row for row in data.get("waiting_on_ove", []) if row.get("id") == item_id),
            None,
        )
        answer = item.get("answer") if item is not None else None
    if isinstance(answer, str):
        return answer
    if isinstance(answer, Mapping) and _answer_args(answer):
        return answer
    return None


def _one_answer(value: Any) -> dict[str, Any]:
    """One question's answer as wrapper args (``selected`` or ``text``), or
    ``{}`` when the value is not an answer."""
    if isinstance(value, str):
        return {"text": value}
    if isinstance(value, Mapping):
        selected = value.get("selected")
        if isinstance(selected, list):
            return {"selected": [str(choice) for choice in selected]}
        text = value.get("text")
        if isinstance(text, str):
            return {"text": text}
    return {}


def _answer_args(answer: str | Mapping[str, Any]) -> dict[str, Any]:
    """``answer_question`` args for one polled answer (server.ts:44); ``{}``
    when the answer has no usable shape. A ``per_question`` map passes each
    question its own answer (the wrapper applies a shared ``text`` or
    ``selected`` to every question it has no per-question answer for)."""
    if isinstance(answer, str):
        return {"text": answer}
    per = answer.get("per_question")
    if isinstance(per, Mapping):
        separate = {str(qid): _one_answer(value) for qid, value in per.items()}
        separate = {qid: value for qid, value in separate.items() if value}
        if separate:
            return {"per_question": separate, **_one_answer(answer)}
    return _one_answer(answer)


def _clear_question(
    tasks_path: str | Path | None, task_id: str | None, item_id: str
) -> None:
    """Clear the question item (idempotent; the blocked row returns to
    ``working`` through clear_block's own semantics)."""
    clear_block(tasks_path, item_id, task_id=str(task_id))


def _wait_stage_done(
    client: AndaClient,
    stage_id: str,
    interrupt: Any | None,
    events_path: Path,
    *,
    tasks_path: str | Path | None = None,
    task_id: str | None = None,
    row_started: bool = False,
    stage: str | None = None,
    early_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Consume events until the stage's ``stage_done`` arrives.

    No deadline: the wrapper's event stream is the liveness signal, and
    EOF or a dead wrapper raises SandboxUnavailable instead of hanging.
    The terminal event is taken from ``client.terminal``, which every read
    path fills, so a ``stage_done`` that arrived during a tool call (the
    stage-start reply, the abort reply) is never lost. Every event is
    appended through the redacting ``append_event``. A set
    interrupt sends the ``abort`` tool once and keeps consuming until the
    terminal event, then raises ``cancelled`` with ``started=True`` so
    ``run()`` maps it exactly like the bwrap path.
    Every event also touches the run's tasks.json row when one was started
    (best-effort).

    A ``question`` event with a non-empty requestId (and a started row)
    raises exactly one waiting_on_ove item and the loop polls the store
    for its additive ``answer`` at most once per _ANSWER_POLL seconds —
    no wall-clock deadline — piping it back through ``answer_question``
    into the same live session; a stale answer clears the dead item and
    keeps waiting (a re-ask arrives as a new question event), any other
    answer_question failure propagates as SandboxUnavailable. A set
    interrupt is honoured while an answer is pending.

    ``early_events`` are events a tool call already logged (the stage-start
    reply can trail them); they get the same question and completion
    handling exactly once. A question still open when the wait ends for
    any reason (wrapper closed, stage failed, cancelled) is cleared.
    """
    # The open questions: wrapper requestId -> waiting_on_ove item id. The
    # wrapper keeps every live request pending under its own id (a new
    # question never cancels an earlier one), so each item lives until its
    # own request is answered, found dead, or the wait ends.
    pending: dict[str, str] = {}
    try:
        return _wait_loop(
            client,
            stage_id,
            interrupt,
            events_path,
            tasks_path=tasks_path,
            task_id=task_id,
            row_started=row_started,
            stage=stage,
            early_events=early_events,
            pending=pending,
        )
    finally:
        # Questions still open when the wait ends (wrapper closed, stage
        # failed, cancelled) belong to a dead session: clear their items.
        for item_id in list(pending.values()):
            with contextlib.suppress(Exception):
                _clear_question(tasks_path, task_id, item_id)


def _wait_loop(
    client: AndaClient,
    stage_id: str,
    interrupt: Any | None,
    events_path: Path,
    *,
    tasks_path: str | Path | None,
    task_id: str | None,
    row_started: bool,
    stage: str | None,
    early_events: list[dict[str, Any]] | None,
    pending: dict[str, str],
) -> dict[str, Any]:
    """The body of ``_wait_stage_done``; ``pending`` maps each open
    question's requestId to its item id, kept current so the caller can
    clear what is left on any exit."""
    aborted = False
    last_answer_check = 0.0
    # Events a tool call already logged and touched, still to be acted on.
    unhandled: list[dict[str, Any]] = list(early_events or [])
    while True:
        done = client.terminal.pop(stage_id, None)
        if done is None and not unhandled:
            client.next_event(_POLL_TICK)
            done = client.terminal.pop(stage_id, None)
        fresh = client.drain_events()
        for buffered in fresh:
            append_event(events_path, buffered)
            _touch_row(tasks_path, task_id, row_started, buffered)
        batch, unhandled = unhandled + fresh, []
        for buffered in batch:
            if (
                buffered.get("event") == "question"
                and row_started
                and str(buffered.get("requestId") or "")
            ):
                # A GSD follow-up question: raise it under its own stable id
                # beside any other open one. A requestId-less question is
                # informational only — append/touch already happened above.
                rid = str(buffered.get("requestId"))
                with contextlib.suppress(Exception):
                    pending[rid] = _raise_question(
                        tasks_path,
                        task_id,
                        stage,
                        rid,
                        _question_text(buffered),
                        _question_ids(buffered),
                    )
                    last_answer_check = time.monotonic()
        if (
            done is None
            and pending
            and time.monotonic() - last_answer_check >= _ANSWER_POLL
        ):
            last_answer_check = time.monotonic()
            for rid, item_id in list(pending.items()):
                answer: str | Mapping[str, Any] | None = None
                with contextlib.suppress(Exception):
                    answer = _answer_from_store(tasks_path, item_id)
                if answer is None:
                    continue
                try:
                    client.call(
                        "answer_question",
                        {
                            "stage_id": stage_id,
                            "request_id": rid,
                            **_answer_args(answer),
                        },
                    )
                except SandboxUnavailable as exc:
                    if "stale_answer" not in str(exc):
                        # Wrapper-level failure: propagate per the
                        # no-silent-fallback rule.
                        raise
                    # The request died (answered/cancelled elsewhere): the
                    # item is dead too — clear it and keep waiting; a fresh
                    # ask arrives as a new question event with a new id.
                with contextlib.suppress(Exception):
                    _clear_question(tasks_path, task_id, item_id)
                pending.pop(rid, None)
        if done is not None:
            if aborted and done.get("status") == "aborted":
                # Our own abort landed (an externally aborted stage returns
                # as ok False instead). run() maps this to reason
                # "cancelled" exactly like the bwrap path.
                raise EngineError(
                    "cancelled", "stage aborted by interrupt", started=True
                )
            return done
        if interrupt is not None and interrupt.is_set() and not aborted:
            # Explicit abort. Core.abort waits for the stage to settle, so
            # its stage_done can arrive before this reply; the client keeps
            # it in ``terminal`` and the next loop returns it.
            client.call("abort", {"stage_id": stage_id})
            aborted = True


def _append_down_event(
    events_path: Path,
    outcome: dict[str, Any] | None,
    down_summary: dict[str, Any] | None,
) -> None:
    """Final job-shaped event so the down result (leftovers, ownership) is
    visible in events.jsonl. Redaction rides ``append_event``."""
    body: dict[str, Any] = {"type": "job_end", "down": down_summary or {}}
    if outcome is not None:
        body["ok"] = bool(outcome["ok"])
        if not outcome["ok"]:
            body["reason"] = "engine"
    with contextlib.suppress(OSError):
        append_event(events_path, body)

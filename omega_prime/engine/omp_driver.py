# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Spawn ``omp`` and speak the Phase A1a RPC and RPC-UI NDJSON subsets."""

from __future__ import annotations

import contextlib
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from omega_prime.agent_state import private_state_paths
from omega_prime.credentials.redact import redact_text
from omega_prime.engine.clarify_ipc import ClarifyRequest, ClarifySocketBroker
from omega_prime.engine.hitl import (
    DeliveryStatus,
    OperatorRequest,
    OperatorTransport,
    PendingRequestRegistry,
    RequestIdentity,
    RequestStatus,
    new_generation,
)
from omega_prime.engine.jobs import (
    agent_data_dir,
    append_event,
    child_env,
    job_dir,
    maybe_tmux,
    omp_home_override,
    sandboxed_child_env,
    state_dir,
    write_json,
)
from omega_prime.engine.mcp_bridge import (
    ENGINE_SYSTEM_PROMPT,
    AcquiredAttachment,
    recover_attachments,
    try_acquire_attachment,
)
from omega_prime.engine.plan_router import PlanNotAllowed, read_usage, select
from omega_prime.engine.protocol import (
    MODES,
    OMP_SUPPORTED_MAJOR,
    THINKING_LEVELS,
    AskAnswer,
    AskQuestion,
    AskRequest,
    ConfirmRequest,
    SelectRequest,
    UiFrameError,
    UiRequest,
    assistant_text,
    dumps,
    event_filter,
    loads,
    parse_ui_request,
    parse_version,
    usage_of,
)
from omega_prime.engine.sandbox import SandboxUnavailable, sandbox_ready, wrap
from omega_prime.engine.skills_attach import attach_skills
from omega_prime.engine.ui_control import UIControlError, UIControlStore

#: Env var naming the delegate depth of a child started through the engine.
DELEGATE_DEPTH_ENV = "OMEGA_PRIME_DELEGATE_DEPTH"
_READY_TIMEOUT = 15.0
_COMMAND_TIMEOUT = 15.0


# Static ask/blocks texts for the waiting_on_ove item raised when a gated
# stage cannot run. The box item shape has no detail field, so the exception
# detail stays in EngineResult.detail and is not copied into the item.
_ENGINE_ASK = {
    "plan_not_allowed": (
        "The stage's coding plan is not allowlisted, so the run was refused. "
        "Allow the plan for this stage (its opt-in flag such as "
        "OMEGA_PRIME_OMP_ALLOW_ZAI, or an API key on the allowlist) and "
        "re-run the task."
    ),
    "sandbox_unavailable": (
        "The engine sandbox tier is unavailable, so the run was refused. "
        "Restore the sandbox binary (bwrap or the configured tier), or set "
        "OMEGA_PRIME_ENGINE_UNSANDBOXED=1 explicitly, then re-run the task."
    ),
}
_ENGINE_BLOCKS = {
    "plan_not_allowed": "engine plan gate (plan_not_allowed)",
    "sandbox_unavailable": "engine sandbox gate (sandbox_unavailable)",
}


_EOF = object()


class _CancelEvent(Protocol):
    def is_set(self) -> bool: ...


class EngineError(Exception):
    """The engine refused or the omp process failed. ``reason`` is stable."""

    def __init__(self, reason: str, detail: str = "", *, started: bool = False) -> None:
        self.reason = reason
        self.detail = detail or reason
        self.started = started
        super().__init__(self.detail)


@dataclass
class EngineResult:
    ok: bool
    job_id: str
    final_text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    cost: float | None = None
    plan: str | None = None
    provider: str | None = None
    model: str | None = None
    stage: str | None = None
    fallback_from: str | None = None
    events_path: str = ""
    reason: str | None = None
    detail: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    sandbox: str = ""
    commands: list[str] = field(default_factory=list)
    started: bool = False
    mcp_status: str = "attach_failed"
    cleanup_conflicts: list[str] = field(default_factory=list)
    mode: str = "rpc"
    omp_major: int | None = None
    ui_summary: dict[str, Any] = field(default_factory=dict)


def resolve_omp(env: Mapping[str, str]) -> str | None:
    explicit = str(env.get("OMEGA_PRIME_OMP_BIN", "")).strip()
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return str(path)
        return None
    return shutil.which("omp", path=env.get("PATH") or None)


def omp_home(env: Mapping[str, str]) -> Path:
    """omp's default config root, which holds the one shared login store.

    ``OMEGA_PRIME_OMP_HOME`` overrides it (``child_env`` then hands omp
    ``PI_CODING_AGENT_DIR=<override>/agent``); otherwise ``$HOME/.omp``. Jobs run on
    omp's default profile so every job reads the same ``agent/agent.db`` login.
    A per-job ``--profile`` would point omp at
    ``~/.omp/profiles/<name>/agent/agent.db``, which has no login, and omp
    rejects a path as a profile name. The store is never copied: OAuth refresh
    tokens are single-use, so a copy would break the next refresh.
    """
    override = omp_home_override(env)
    if override is not None:
        return override
    home = str(env.get("HOME", "")).strip()
    return (Path(home) if home else Path.home()) / ".omp"


def omp_version(binary: str, env: Mapping[str, str]) -> tuple[int, int, int]:
    try:
        proc = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=child_env(env),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            "not_configured", f"omp --version failed: {type(exc).__name__}"
        ) from exc
    if proc.returncode != 0:
        raise EngineError("not_configured", "omp --version failed")
    parsed = parse_version(proc.stdout + "\n" + proc.stderr)
    if parsed is None:
        raise EngineError("version_unsupported", "omp version was not readable")
    if parsed[0] != OMP_SUPPORTED_MAJOR:
        raise EngineError(
            "version_unsupported",
            f"omp {parsed[0]}.x is not supported; this driver pins major "
            f"{OMP_SUPPORTED_MAJOR}",
        )
    return parsed


class OmpSession:
    """One omp RPC process and the events it has emitted."""

    def __init__(self, proc: subprocess.Popen[str], events_path: Path) -> None:
        self.proc = proc
        self.events_path = events_path
        self.events: list[dict[str, Any]] = []
        self.agent_ended = False
        self._queue: queue.Queue[Any] = queue.Queue()
        self._send_lock = threading.Lock()
        self._reader_error: BaseException | None = None
        self._abort_sent = False
        self._stdout = threading.Thread(
            target=_pump, args=(proc.stdout, self._queue, self), daemon=True
        )
        self._stderr_parts: list[str] = []
        self._stderr = threading.Thread(
            target=_collect, args=(proc.stderr, self._stderr_parts), daemon=True
        )
        self._stdout.start()
        self._stderr.start()

    def wait_type(self, kind: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self._next(min(0.1, deadline - time.monotonic()))
            if event is None:
                continue
            if event.get("type") == kind:
                return event
        raise EngineError("timeout", f"timed out waiting for {kind}")

    def send(self, kind: str, **fields: Any) -> str:
        command_id = uuid.uuid4().hex[:12]
        payload: dict[str, Any] = {"id": command_id, "type": kind}
        payload.update(fields)
        self.send_frame(payload)
        return command_id

    def send_frame(self, payload: dict[str, Any]) -> None:
        """Write one complete host frame, serializing concurrent callers."""
        if self.proc.stdin is None:
            raise EngineError("protocol", "omp stdin is closed")
        with self._send_lock:
            self.proc.stdin.write(dumps(payload) + "\n")
            self.proc.stdin.flush()

    def wait_response(self, command_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self._next(min(0.1, deadline - time.monotonic()))
            if event is None:
                continue
            if str(event.get("id") or "") == command_id and event.get("type") in {
                "response",
                "error",
            }:
                return event
        raise EngineError("timeout", "timed out waiting for a command response")

    def close(self) -> int:
        if self.proc.stdin is not None:
            with contextlib.suppress(OSError):
                self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout=5)

    def stderr_tail(self) -> str:
        return "".join(self._stderr_parts)[-2000:]

    def _next(self, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            try:
                wait = min(0.1, max(0.0, deadline - time.monotonic()))
                line = self._queue.get(timeout=wait)
            except queue.Empty:
                line = None
            if line is _EOF:
                self._raise_stream_failure()
            if line is not None:
                event = loads(line)
                if event is None:
                    continue
                self.events.append(event)
                append_event(self.events_path, event)
                if event.get("type") == "agent_end":
                    self.agent_ended = True
                return event
            if self.proc.poll() is not None:
                raise EngineError(
                    "process", f"omp exited with status {self.proc.returncode}"
                )
            if self._reader_error is not None:
                self._raise_stream_failure()
            if self._stdout.is_alive() is False:
                self._raise_stream_failure()
            if time.monotonic() >= deadline:
                return None

    def _raise_stream_failure(self) -> None:
        if self._reader_error is not None:
            raise EngineError(
                "process",
                f"omp stdout reader failed: {type(self._reader_error).__name__}",
            ) from self._reader_error
        raise EngineError("process", "omp stdout closed before agent_end")

    def abort_once(self) -> None:
        if self._abort_sent:
            return
        self._abort_sent = True
        with contextlib.suppress(EngineError, OSError):
            session_abort(self)


def _pump(stream: Any, sink: queue.Queue[Any], session: OmpSession) -> None:
    if stream is None:
        sink.put(_EOF)
        return
    try:
        for line in stream:
            sink.put(line)
    except BaseException as exc:
        session._reader_error = exc
    finally:
        sink.put(_EOF)


def _collect(stream: Any, parts: list[str]) -> None:
    if stream is None:
        return
    for line in stream:
        if sum(len(part) for part in parts) < 8000:
            parts.append(line)


def run(
    prompt: str,
    *,
    job_id: str | None = None,
    cwd: str | Path | None = None,
    system_prompt: str | None = None,
    thinking: str | None = None,
    plan: str | None = None,
    mode: str = "rpc",
    env: Mapping[str, str] | None = None,
    unsandboxed: bool | None = None,
    tmux: bool | None = None,
    tasks_path: str | Path | None = None,
    task_id: str | None = None,
    stage: str | None = None,
    ui_control: UIControlStore | None = None,
    cancel_event: _CancelEvent | None = None,
    interrupt: _CancelEvent | None = None,
    delegate_depth: int | None = None,
) -> EngineResult:
    """Run one prompt on omp and return the assistant text, usage, and cost.

    ``stage`` (``plan`` or ``execute``) picks that stage's model and lets the
    router fall back from ChatGPT to Z.ai; see ``plan_router.select``.

    ``tasks_path`` and ``task_id`` are optional dashboard context: when both
    are set and the run fails the plan gate (``plan_not_allowed``) or the
    sandbox gate (``sandbox_unavailable``), the same call raises one
    ``waiting_on_ove`` item and blocks the task row before the failed
    ``EngineResult`` returns. Without them, behaviour is unchanged.

    ``delegate_depth`` is the delegate depth of a child started by
    ``delegate_task``; omp's MCP server starts its delegate parent there, so
    nested delegates keep the parent's ``max_depth``.
    """
    mapping = dict(env if env is not None else {})
    if env is None:
        import os

        mapping = dict(os.environ)
    token = job_id or "job-" + uuid.uuid4().hex[:8]
    try:
        directory = job_dir(mapping, token)
    except (OSError, ValueError) as exc:
        return EngineResult(
            False, token or "job", reason="invalid_job", detail=str(exc), mode=mode
        )
    metadata: dict[str, Any] = {
        "job_id": token,
        "mode": mode,
        "omp_major": None,
        "mcp_status": "attach_failed",
        "ui_summary": {"status": "not_used" if mode == "rpc" else "attach_failed"},
    }
    try:
        return _run(
            prompt,
            job_id=token,
            directory=directory,
            cwd=cwd,
            system_prompt=system_prompt,
            thinking=thinking or "low",
            plan=plan,
            mode=mode,
            env=mapping,
            unsandboxed=unsandboxed,
            tmux=tmux,
            stage=stage,
            ui_control=ui_control,
            cancel_event=cancel_event if cancel_event is not None else interrupt,
            metadata=metadata,
            delegate_depth=delegate_depth,
        )
    except (EngineError, PlanNotAllowed, SandboxUnavailable, OSError) as exc:
        if isinstance(exc, OSError):
            reason = "process"
            detail = f"omp closed the pipe: {type(exc).__name__}"
            started = bool(metadata.get("started", False))
        else:
            candidate_reason = getattr(exc, "reason", None)
            if not isinstance(candidate_reason, str):
                candidate_reason = (
                    "plan_not_allowed"
                    if isinstance(exc, PlanNotAllowed)
                    else "sandbox_unavailable"
                )
            reason = candidate_reason
            detail = str(getattr(exc, "detail", None) or exc)
            started = bool(getattr(exc, "started", False) or metadata.get("started"))
        events_path = directory / "events.jsonl"
        if mode == "rpc-ui" and not metadata.get("ui_attachment_event"):
            metadata["ui_summary"] = {"status": "attach_failed"}
            metadata["ui_attachment_event"] = "attach_failed"
            with contextlib.suppress(OSError):
                events_path.parent.mkdir(parents=True, exist_ok=True)
                events_path.touch(exist_ok=True)
                append_event(
                    events_path,
                    {
                        "type": "ui_control_attachment",
                        "status": "attach_failed",
                        "reason": str(reason or "engine"),
                    },
                )
        metadata.update({"ok": False, "reason": str(reason or "engine")})
        with contextlib.suppress(OSError):
            if events_path.is_file():
                append_event(
                    events_path,
                    {"type": "job_end", "ok": False, "reason": str(reason or "engine")},
                )
            write_json(directory / "session.json", metadata)
        if (
            tasks_path is not None
            and task_id is not None
            and reason in ("plan_not_allowed", "sandbox_unavailable")
        ):
            # Best-effort visibility: one waiting_on_ove item per stable id,
            # raised before the failed result returns. Suppressed so a
            # visibility failure can never mask or replace the engine
            # failure below (W111-04).
            with contextlib.suppress(Exception):
                from omega_prime.dashboard.blocks import raise_block

                raise_block(
                    tasks_path,
                    f"{task_id}:{reason}",
                    ask=_ENGINE_ASK[str(reason)],
                    blocks=_ENGINE_BLOCKS[str(reason)],
                    task_id=task_id,
                )
        return EngineResult(
            False,
            token,
            plan=metadata.get("plan"),
            provider=metadata.get("provider"),
            model=metadata.get("model"),
            stage=metadata.get("stage"),
            fallback_from=metadata.get("fallback_from"),
            events_path=str(events_path),
            reason=str(reason),
            detail=detail,
            stats=dict(metadata.get("stats", {})),
            sandbox=str(metadata.get("sandbox", "")),
            commands=list(metadata.get("commands", [])),
            started=started,
            mcp_status=str(metadata.get("mcp_status", "attach_failed")),
            cleanup_conflicts=list(metadata.get("cleanup_conflicts", [])),
            mode=mode,
            omp_major=metadata.get("omp_major"),
            ui_summary=dict(metadata.get("ui_summary", {})),
        )


def _run(
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
    unsandboxed: bool | None,
    tmux: bool | None,
    stage: str | None = None,
    ui_control: UIControlStore | None = None,
    cancel_event: _CancelEvent | None = None,
    metadata: dict[str, Any] | None = None,
    delegate_depth: int | None = None,
) -> EngineResult:
    run_metadata = metadata if metadata is not None else {}
    if mode not in MODES:
        raise EngineError("invalid_mode", "mode must be rpc or rpc-ui")
    if thinking not in THINKING_LEVELS:
        raise EngineError("invalid_thinking", "thinking level is not recognised")
    if mode == "rpc-ui" and not isinstance(ui_control, UIControlStore):
        raise EngineError(
            "ui_control_unavailable",
            "rpc-ui requires a safely constructed UIControlStore",
        )
    if (
        mode == "rpc-ui"
        and cancel_event is not None
        and not callable(getattr(cancel_event, "is_set", None))
    ):
        raise EngineError("invalid_cancel_event", "cancel_event must provide is_set()")
    binary = resolve_omp(env)
    if binary is None:
        raise EngineError("not_configured", "omp is not on PATH")
    version = omp_version(binary, env)
    run_metadata.update({"job_id": job_id, "mode": mode, "omp_major": version[0]})
    # The omp-sandbox backend branches before the bwrap gates. Selecting it
    # is one explicit knob (OMEGA_PRIME_OMP_BACKEND=omp-sandbox) and never
    # falls back to bwrap on failure; the lazy import keeps the bwrap path
    # independent of the module.
    from omega_prime.engine import sandbox_omp

    if sandbox_omp.selected(env):
        return sandbox_omp.run_backend(
            prompt,
            job_id=job_id,
            directory=directory,
            cwd=cwd,
            system_prompt=system_prompt,
            thinking=thinking,
            plan=plan,
            mode=mode,
            env=env,
            binary=binary,
            omp_major=version[0],
            stage=stage,
            interrupt=cancel_event,
            metadata=run_metadata,
        )
    if not sandbox_ready(env, unsandboxed_override=unsandboxed):
        raise SandboxUnavailable(
            "bubblewrap is not available; set OMEGA_PRIME_ENGINE_UNSANDBOXED=1 "
            "to run omp without a sandbox"
        )
    usage = read_usage(binary, env)
    choice = select(env, usage, requested=plan, stage=stage)
    work = Path(os.path.abspath(cwd)) if cwd is not None else Path.cwd()
    skills_view = work / ".omp" / "skills"
    attachment: AcquiredAttachment | None = None
    mcp_path = str(work / ".omp" / "mcp.json")
    mcp_status = "attach_failed"
    prompt_text = _system(system_prompt)
    session_generation = new_generation() if mode == "rpc-ui" else None
    turn_generation = new_generation() if mode == "rpc-ui" else None
    ui_summary: dict[str, Any] = (
        {
            "status": "attaching",
            "pending": 0,
            "answered": 0,
            "rejected_stale": 0,
            "aborted": 0,
        }
        if mode == "rpc-ui"
        else {"status": "not_used"}
    )
    run_metadata.update(
        {
            "plan": choice.decision.plan,
            "provider": choice.decision.provider,
            "model": choice.decision.model_id,
            "stage": choice.stage,
            "fallback_from": choice.fallback_from,
            "sandbox": "",
            "mcp_json": mcp_path,
            "mcp_status": mcp_status,
            "skills": str(skills_view),
            "omp_major": version[0],
            "mode": mode,
            "session_generation": session_generation,
            "turn_generation": turn_generation,
            "ui_summary": ui_summary,
            "commands": [],
            "stats": {},
            "cleanup_conflicts": [],
        }
    )
    argv = [
        binary,
        "--mode",
        mode,
        "--session-dir",
        str(directory / "sessions"),
        "--model",
        f"{choice.decision.provider}/{choice.decision.model_id}",
        "--thinking",
        thinking,
        "--cwd",
        str(work),
        "--no-title",
        "--append-system-prompt",
        prompt_text,
    ]
    argv.extend(["--skills", "*"])
    binds = [directory, work] if work != directory else [directory]
    # The Omega MCP child writes memory, sessions and desk stores into the
    # agent data folder the host server uses too, so both see one memory.
    # Only that folder is writable; the rest of the state root (ui-control,
    # the attachment journal) stays read-only, and settings.json, secrets/
    # and dashboard/ are hidden inside the sandbox.
    agent_dir = agent_data_dir(env)
    agent_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if agent_dir not in binds:
        binds.append(agent_dir)
    store = omp_home(env)
    if store.is_dir() and store not in binds:
        # Writable so omp can save a refreshed login in place.
        binds.append(store)
    hidden = private_state_paths([state_dir(env)])
    wrapped, sandbox = wrap(
        argv,
        env=env,
        binds=binds,
        hidden=hidden,
        unsandboxed_override=unsandboxed,
    )
    run_metadata["sandbox"] = sandbox
    events_path = directory / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.touch(exist_ok=True)
    write_json(directory / "session.json", run_metadata)
    maybe_tmux(job_id, env, enabled=tmux)
    skills_status = "attach_failed"
    clarify_broker: ClarifySocketBroker | None = None
    clarify_socket_dir = (
        tempfile.TemporaryDirectory(prefix="omp-clarify-") if mode == "rpc-ui" else None
    )
    candidate_broker = (
        ClarifySocketBroker(Path(clarify_socket_dir.name) / "c.sock")
        if clarify_socket_dir is not None
        else None
    )
    recovery_conflicts: list[str] = []
    try:
        recovery_conflicts = list(recover_attachments(state_dir(env), work) or [])
        if recovery_conflicts:
            run_metadata["cleanup_conflicts"] = list(recovery_conflicts)
            append_event(
                events_path,
                {
                    "type": "mcp_attachment",
                    "status": "recovery_conflicts",
                    "cleanup_conflicts": list(recovery_conflicts),
                },
            )
            write_json(directory / "session.json", run_metadata)
        attachment = try_acquire_attachment(
            work,
            state_dir(env),
            job_id,
            agent_dir=agent_dir,
            clarify_socket_path=(
                candidate_broker.socket_path if candidate_broker is not None else None
            ),
        )
    except (OSError, ValueError):
        attachment = None
    if attachment is not None:
        try:
            skills_status = str(
                attach_skills(work, attachment).get("status", "attach_failed")
            )
            if candidate_broker is not None:
                try:
                    candidate_broker.start()
                    clarify_broker = candidate_broker
                except (OSError, ValueError):
                    attachment.release()
                    attachment = None
                    skills_status = "attach_failed"
        except BaseException:
            if clarify_broker is not None:
                clarify_broker.close()
            if attachment is not None:
                attachment.release()
            if clarify_socket_dir is not None:
                clarify_socket_dir.cleanup()
            raise
    run_metadata["skills_status"] = skills_status
    run_metadata["clarify_status"] = (
        ("attached" if clarify_broker is not None else "attach_failed")
        if mode == "rpc-ui"
        else "not_used"
    )
    process_env = sandboxed_child_env(env, hidden)
    if choice.stage is not None:
        # omp hands its env to the omega-prime MCP server it starts, so a
        # delegate_task from a staged turn knows to build on execute.
        process_env["OMEGA_PRIME_ENGINE_STAGE"] = choice.stage
    if delegate_depth is not None:
        # The MCP server's delegate parent starts at this depth, so a child
        # run through the engine cannot reset the delegate depth limit.
        process_env[DELEGATE_DEPTH_ENV] = str(max(0, int(delegate_depth)))
    gate_read = gate_write = -1
    try:
        launch = wrapped
        pass_fds: tuple[int, ...] = ()
        if attachment is not None:
            gate_read, gate_write = os.pipe()
            gate_script = (
                "import os,sys; gate=int(sys.argv[1]); "
                "ready=os.read(gate,1); os.close(gate); "
                "sys.exit(127) if not ready else os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
            )
            launch = [sys.executable, "-c", gate_script, str(gate_read), *wrapped]
            pass_fds = (gate_read,)
        proc = subprocess.Popen(
            launch,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(work),
            # Credential files under the hidden secrets/ folder are read on
            # the host and passed by env, so the MCP child can still start.
            env=process_env,
            bufsize=1,
            pass_fds=pass_fds,
        )
    except BaseException:
        if gate_write >= 0:
            os.close(gate_write)
        if clarify_broker is not None:
            clarify_broker.close()
        if attachment is not None:
            attachment.release()
        if clarify_socket_dir is not None:
            clarify_socket_dir.cleanup()
        raise
    finally:
        if gate_read >= 0:
            os.close(gate_read)
    if attachment is not None:
        try:
            attachment.record_child(proc.pid)
            os.write(gate_write, b"x")
        except BaseException:
            proc.kill()
            proc.wait()
            if clarify_broker is not None:
                clarify_broker.close()
            attachment.release()
            if clarify_socket_dir is not None:
                clarify_socket_dir.cleanup()
            raise
        finally:
            os.close(gate_write)
    session = OmpSession(proc, events_path)
    commands: list[str] = run_metadata["commands"]
    started = False
    stats: dict[str, Any] = run_metadata["stats"]
    try:
        session.wait_type("ready", _READY_TIMEOUT)
        if mode == "rpc-ui":
            ui_summary["status"] = "attached"
            append_event(
                events_path,
                {"type": "ui_control_attachment", "status": "attached"},
            )
            run_metadata["ui_attachment_event"] = "attached"
        commands.append(
            _roundtrip(
                session,
                "set_event_filter",
                events=event_filter(mode),
                messageUpdates="delta",
            )
        )
        commands.append(
            _roundtrip(
                session,
                "set_model",
                provider=choice.decision.provider,
                modelId=choice.decision.model_id,
            )
        )
        commands.append(_roundtrip(session, "set_thinking_level", level=thinking))
        commands.append("get_state")
        state_id = session.send("get_state")
        state_response = session.wait_response(state_id, _COMMAND_TIMEOUT)
        state_data = state_response.get("data")
        dump_tools = (
            state_data.get("dumpTools") if isinstance(state_data, dict) else None
        )
        if (
            attachment is not None
            and isinstance(dump_tools, list)
            and "mcp__omega-prime_omega_command" in dump_tools
        ):
            mcp_status = "attached"
        run_metadata["mcp_status"] = mcp_status
        prompt_id = session.send("prompt", message=prompt, streamingBehavior="steer")
        started = True
        run_metadata["started"] = True
        commands.append("prompt")
        if mode == "rpc-ui":
            # Turns have no wall-clock limit; they end at agent_end, on
            # cancel_event, or on an operator abort (main #49 removed the
            # 120 s turn timeout).
            _accept_prompt_ui(
                session,
                prompt_id,
                math.inf,
                job_id=job_id,
                session_generation=session_generation,
                turn_generation=turn_generation,
                ui_control=ui_control,
                clarify_broker=clarify_broker,
                cancel_event=cancel_event,
                events_path=events_path,
                commands=commands,
                ui_summary=ui_summary,
            )
        else:
            _accept_prompt(session, prompt_id, cancel_event)
        try:
            stats_id = session.send("get_session_stats")
            commands.append("get_session_stats")
            response = session.wait_response(stats_id, _COMMAND_TIMEOUT)
            data = response.get("data")
            if isinstance(data, dict):
                stats.update(data)
        except EngineError:
            stats.clear()
    except OSError as exc:
        raise EngineError(
            "process",
            f"omp closed the pipe: {type(exc).__name__}",
            started=started,
        ) from exc
    except EngineError as exc:
        if started:
            exc.started = True
        if exc.reason == "cancelled":
            session.abort_once()
        raise
    finally:
        if clarify_broker is not None:
            clarify_broker.close()
        if clarify_socket_dir is not None:
            clarify_socket_dir.cleanup()
        session.close()
        conflicts = [
            *recovery_conflicts,
            *(attachment.release() if attachment is not None else []),
        ]
        run_metadata["mcp_status"] = mcp_status
        run_metadata["cleanup_conflicts"] = conflicts
        run_metadata["commands"] = commands
        run_metadata["stats"] = stats
        append_event(
            events_path,
            {
                "type": "mcp_attachment",
                "status": mcp_status,
                "cleanup_conflicts": conflicts,
            },
        )
    text, usage_map, cost = _collect_output(session.events)
    result = EngineResult(
        ok=True,
        job_id=job_id,
        final_text=text,
        usage=usage_map,
        cost=cost,
        plan=choice.decision.plan,
        provider=choice.decision.provider,
        model=choice.decision.model_id,
        stage=choice.stage,
        fallback_from=choice.fallback_from,
        events_path=str(events_path),
        started=True,
        stats=stats,
        sandbox=sandbox,
        commands=commands,
        mcp_status=mcp_status,
        cleanup_conflicts=conflicts,
        mode=mode,
        omp_major=version[0],
        ui_summary=dict(ui_summary),
    )
    run_metadata.update(
        {
            "ok": True,
            "plan": result.plan,
            "provider": result.provider,
            "model": result.model,
            "stage": result.stage,
            "fallback_from": result.fallback_from,
            "sandbox": result.sandbox,
            "mode": result.mode,
            "omp_major": result.omp_major,
            "mcp_json": mcp_path,
            "mcp_status": result.mcp_status,
            "cleanup_conflicts": result.cleanup_conflicts,
            "commands": commands,
            "stats": stats,
            "ui_summary": result.ui_summary,
            "cost": cost,
            "final_text": text[:2000],
        }
    )
    write_json(directory / "session.json", run_metadata)
    return result


def _system(extra: str | None) -> str:
    if extra and extra.strip():
        return ENGINE_SYSTEM_PROMPT + "\n\n" + extra.strip()[:32000]
    return ENGINE_SYSTEM_PROMPT


def _roundtrip(session: OmpSession, kind: str, **fields: Any) -> str:
    command_id = session.send(kind, **fields)
    response = session.wait_response(command_id, _COMMAND_TIMEOUT)
    if response.get("success") is False:
        raise EngineError("protocol", f"{kind} was rejected")
    return kind


def _drain_queued_for_end(session: OmpSession) -> bool:
    """Read only already-queued lines; True when one of them is agent_end.

    Bounded by the queue size at entry so a cancel never waits on omp.
    """
    pending_count = session._queue.qsize()
    for _ in range(pending_count):
        try:
            pending = session._queue.get_nowait()
        except queue.Empty:
            break
        if pending is _EOF:
            session._raise_stream_failure()
        raced = loads(pending)
        if raced is None:
            continue
        session.events.append(raced)
        append_event(session.events_path, raced)
        if raced.get("type") == "agent_end":
            session.agent_ended = True
            return True
    return False


def _accept_prompt(session: OmpSession, command_id: str, interrupt: Any | None) -> None:
    while True:
        event = session._next(0.1)
        if (
            event is not None
            and str(event.get("id") or "") == command_id
            and event.get("type") in {"response", "error"}
            and event.get("success") is False
        ):
            raise EngineError("protocol", "prompt was rejected")
        if session.agent_ended:
            return
        if interrupt is not None and interrupt.is_set():
            if _drain_queued_for_end(session):
                return
            raise EngineError("cancelled", "prompt cancelled", started=True)


def _request_identity_map(identity: RequestIdentity) -> dict[str, str]:
    return {
        "job_id": identity.job_id,
        "session_generation": identity.session_generation,
        "turn_generation": identity.turn_generation,
        "request_id": identity.request_id,
    }


def _ui_json_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _ui_json_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_ui_json_data(item) for item in value]
    return value


def _ui_event_summary(request: UiRequest) -> dict[str, Any]:
    if isinstance(request, AskRequest):
        # Each question keeps its own identity and options, in order; the
        # answer command needs one value per question.
        return {
            "method": request.method,
            "question": redact_text(
                request.questions[0].question if request.questions else ""
            )[:512],
            "questions": [
                {
                    "id": question.id,
                    "question": redact_text(question.question)[:512],
                    "options": [
                        redact_text(option.label)[:128] for option in question.options
                    ],
                }
                for question in request.questions
            ],
        }
    elif isinstance(request, SelectRequest):
        prompt = request.title
        options = [redact_text(option)[:128] for option in request.options]
    elif isinstance(request, ConfirmRequest):
        prompt = request.message
        options = []
    else:
        prompt = ""
        options = []
    return {
        "method": request.method,
        "question": redact_text(prompt)[:512],
        "options": options,
    }


class _UIControlTransport(OperatorTransport):
    """Publish parsed RPC UI requests to the existing operator control store."""

    def __init__(
        self,
        store: UIControlStore,
        events_path: Path,
        *,
        job_id: str,
        session_generation: str,
        turn_generation: str,
        summary: dict[str, Any],
    ) -> None:
        self.store = store
        self.events_path = events_path
        self.job_id = job_id
        self.session_generation = session_generation
        self.turn_generation = turn_generation
        self.summary = summary

    def deliver(self, request: OperatorRequest) -> DeliveryStatus:
        identity = request.identity
        if (
            identity.job_id != self.job_id
            or identity.session_generation != self.session_generation
            or identity.turn_generation != self.turn_generation
        ):
            return DeliveryStatus.UNDELIVERABLE
        identity_mapping = _request_identity_map(identity)
        summary = _ui_event_summary(request.request)
        serialized = _ui_json_data(asdict(request.request))
        try:
            self.store.register_request(identity_mapping, serialized)
            append_event(
                self.events_path,
                {
                    "type": "ui_pending",
                    "identity": identity_mapping,
                    "request": summary,
                },
            )
        except (OSError, UIControlError):
            return DeliveryStatus.UNDELIVERABLE
        self.summary["pending"] += 1
        return DeliveryStatus.DELIVERED


def _answer_from_command(
    request: UiRequest, command: Mapping[str, Any]
) -> str | bool | tuple[AskAnswer, ...] | None:
    raw = command.get("answer")
    if not isinstance(raw, Mapping):
        return None
    if isinstance(request, SelectRequest):
        value = raw.get("value")
        return value if raw.get("type") == "select" and isinstance(value, str) else None
    if isinstance(request, ConfirmRequest):
        confirmed = raw.get("confirmed")
        return (
            confirmed
            if raw.get("type") == "confirm" and type(confirmed) is bool
            else None
        )
    if isinstance(request, AskRequest) and raw.get("type") == "ask":
        answers = raw.get("answers")
        if not isinstance(answers, Sequence) or isinstance(answers, (str, bytes)):
            return None
        mapped: list[AskAnswer] = []
        for item in answers:
            if not isinstance(item, Mapping):
                return None
            selected = item.get("selectedOptions")
            custom_input = item.get("customInput")
            if (
                not isinstance(item.get("id"), str)
                or not isinstance(selected, Sequence)
                or isinstance(selected, (str, bytes))
                or any(not isinstance(option, str) for option in selected)
                or (custom_input is not None and not isinstance(custom_input, str))
            ):
                return None
            mapped.append(AskAnswer(item["id"], tuple(selected), custom_input))
        return tuple(mapped)
    return None


def _accept_prompt_ui(
    session: OmpSession,
    command_id: str,
    timeout: float,
    *,
    job_id: str,
    session_generation: str | None,
    turn_generation: str | None,
    ui_control: UIControlStore | None,
    clarify_broker: ClarifySocketBroker | None,
    cancel_event: _CancelEvent | None,
    events_path: Path,
    commands: list[str],
    ui_summary: dict[str, Any],
) -> None:
    if (
        not isinstance(ui_control, UIControlStore)
        or session_generation is None
        or turn_generation is None
    ):
        raise EngineError(
            "ui_control_unavailable", "rpc-ui control context is unavailable"
        )

    registry = PendingRequestRegistry()
    pending: dict[RequestIdentity, UiRequest] = {}
    clarify_pending: dict[RequestIdentity, tuple[ClarifyRequest, AskRequest]] = {}
    transport = _UIControlTransport(
        ui_control,
        events_path,
        job_id=job_id,
        session_generation=session_generation,
        turn_generation=turn_generation,
        summary=ui_summary,
    )
    deadline = time.monotonic() + timeout
    abort_sent = False

    def record_rejected(identity: Mapping[str, Any], reason: str) -> None:
        append_event(
            events_path,
            {
                "type": "ui_rejected_stale",
                "identity": dict(identity),
                "reason": reason,
            },
        )
        ui_summary["rejected_stale"] += 1

    def resolve_store(identity: RequestIdentity, resolution: str) -> bool:
        try:
            ui_control.resolve_request(_request_identity_map(identity), resolution)
        except (OSError, UIControlError):
            return False
        return True

    def register_clarify_requests() -> None:
        if clarify_broker is None or abort_sent:
            return
        while True:
            request = clarify_broker.next_request()
            if request is None:
                return
            identity = RequestIdentity(
                job_id=job_id,
                session_generation=session_generation,
                turn_generation=turn_generation,
                request_id=request.request_id,
            )
            ui_request = AskRequest(
                id=request.request_id,
                questions=(
                    AskQuestion(
                        id=request.request_id,
                        question=request.question,
                        options=(),
                    ),
                ),
            )
            identity_mapping = _request_identity_map(identity)
            registered = False
            try:
                ui_control.register_request(
                    identity_mapping, _ui_json_data(asdict(ui_request))
                )
                registered = True
                append_event(
                    events_path,
                    {
                        "type": "ui_pending",
                        "identity": identity_mapping,
                        "request": _ui_event_summary(ui_request),
                    },
                )
            except (OSError, UIControlError):
                if registered:
                    resolve_store(identity, "rejected_stale")
                request.reject()
                raise EngineError(
                    "ui_control_unavailable",
                    "clarification request could not be delivered safely",
                    started=True,
                ) from None
            clarify_pending[identity] = (request, ui_request)
            ui_summary["pending"] += 1

    def mark_pending(resolution: str) -> bool:
        if pending:
            registry.invalidate_session(job_id, session_generation)
        resolved = True
        for identity in tuple(pending):
            resolved = resolve_store(identity, resolution) and resolved
            pending.pop(identity, None)
            ui_summary["pending"] -= 1
            try:
                if resolution == "aborted":
                    ui_summary["aborted"] += 1
                    record_rejected(_request_identity_map(identity), "aborted")
                else:
                    record_rejected(_request_identity_map(identity), "stale")
            except OSError:
                resolved = False
        for identity, (request, _ui_request) in tuple(clarify_pending.items()):
            resolved = resolve_store(identity, resolution) and resolved
            try:
                request.reject()
            except RuntimeError:
                resolved = False
            clarify_pending.pop(identity, None)
            ui_summary["pending"] -= 1
            try:
                if resolution == "aborted":
                    ui_summary["aborted"] += 1
                    record_rejected(_request_identity_map(identity), "aborted")
                else:
                    record_rejected(_request_identity_map(identity), "stale")
            except OSError:
                resolved = False
        return resolved

    def send_abort() -> None:
        nonlocal abort_sent, deadline
        if abort_sent:
            return
        abort_sent = True
        # Turns have no wall-clock limit, but after an abort omp gets one
        # command timeout to reach agent_end before the turn is given up.
        deadline = min(deadline, time.monotonic() + _COMMAND_TIMEOUT)
        resolved = mark_pending("aborted")
        commands.append("abort")
        session.send("abort")
        # The abort is on the wire; _run's cancel cleanup must not send another.
        session._abort_sent = True
        if not resolved:
            raise EngineError(
                "ui_control", "unable to mark pending request aborted", started=True
            )

    def acknowledge(command: Mapping[str, Any]) -> str:
        queue_id = command.get("queue_id")
        if not isinstance(queue_id, str):
            raise EngineError(
                "ui_control", "control queue item has no queue_id", started=True
            )
        try:
            ui_control.acknowledge_command(
                queue_id,
                job_id=job_id,
                session_generation=session_generation,
            )
        except (OSError, UIControlError) as exc:
            raise EngineError(
                "ui_control", "unable to acknowledge operator command", started=True
            ) from exc
        return queue_id

    def process_command(command: dict[str, Any]) -> None:
        action = command.get("action")
        raw_identity = command.get("identity")
        if not isinstance(raw_identity, Mapping):
            record_rejected({}, "invalid_identity")
            return
        if (
            raw_identity.get("job_id") != job_id
            or raw_identity.get("session_generation") != session_generation
        ):
            record_rejected(raw_identity, "wrong_session")
            return
        if action == "abort":
            if set(raw_identity) != {"job_id", "session_generation"}:
                record_rejected(raw_identity, "invalid_identity")
                acknowledge(command)
                return
            acknowledge(command)
            send_abort()
            return
        if action != "answer":
            record_rejected(raw_identity, "unsupported_action")
            acknowledge(command)
            return
        request_job = raw_identity.get("job_id")
        request_session = raw_identity.get("session_generation")
        request_turn = raw_identity.get("turn_generation")
        request_id = raw_identity.get("request_id")
        if (
            not isinstance(request_job, str)
            or not isinstance(request_session, str)
            or not isinstance(request_turn, str)
            or not isinstance(request_id, str)
        ):
            record_rejected(raw_identity, "invalid_identity")
            acknowledge(command)
            return
        try:
            identity = RequestIdentity(
                job_id=request_job,
                session_generation=request_session,
                turn_generation=request_turn,
                request_id=request_id,
            )
        except (TypeError, ValueError):
            record_rejected(raw_identity, "invalid_identity")
            acknowledge(command)
            return
        if identity.turn_generation != turn_generation:
            record_rejected(raw_identity, "wrong_session")
            acknowledge(command)
            return
        clarify_entry = clarify_pending.get(identity)
        if clarify_entry is not None:
            request, ask_request = clarify_entry
            answer = _answer_from_command(ask_request, command)
            text = (
                answer[0].custom_input
                if isinstance(answer, tuple)
                and len(answer) == 1
                and answer[0].selected_options == ()
                else None
            )
            if not isinstance(text, str):
                acknowledge(command)
                record_rejected(raw_identity, "invalid")
                return
            acknowledge(command)
            try:
                request.answer(text)
            except RuntimeError:
                resolve_store(identity, "rejected_stale")
                clarify_pending.pop(identity, None)
                ui_summary["pending"] -= 1
                record_rejected(raw_identity, "duplicate")
                return
            resolved = resolve_store(identity, "answered")
            append_event(
                events_path,
                {"type": "ui_answered", "identity": _request_identity_map(identity)},
            )
            clarify_pending.pop(identity, None)
            ui_summary["pending"] -= 1
            ui_summary["answered"] += 1
            if not resolved:
                raise EngineError(
                    "ui_control", "unable to mark clarification answered", started=True
                )
            return
        snapshot = registry.get(identity)
        if snapshot is None:
            record_rejected(raw_identity, "unknown_request")
            acknowledge(command)
            return
        answer = _answer_from_command(snapshot.request, command)
        queue_id = command.get("queue_id")
        if not isinstance(queue_id, str):
            raise EngineError(
                "ui_control", "control queue item has no queue_id", started=True
            )
        acknowledged = False
        write_attempted = False

        def send_response(response: dict[str, Any]) -> None:
            nonlocal acknowledged, write_attempted
            ui_control.acknowledge_command(
                queue_id,
                job_id=job_id,
                session_generation=session_generation,
            )
            acknowledged = True
            write_attempted = True
            session.send_frame(response)

        status = (
            RequestStatus.INVALID
            if answer is None
            else registry.submit(identity, answer, send_response)
        )
        if status is RequestStatus.ANSWERED:
            resolved = resolve_store(identity, "answered")
            append_event(
                events_path,
                {"type": "ui_answered", "identity": _request_identity_map(identity)},
            )
            ui_summary["pending"] -= 1
            ui_summary["answered"] += 1
            pending.pop(identity, None)
            if write_attempted:
                commands.append("extension_ui_response")
            if not resolved:
                raise EngineError(
                    "ui_control", "unable to mark UI request answered", started=True
                )
            return
        if not acknowledged and status is not RequestStatus.SEND_FAILED:
            acknowledge(command)
        if status is RequestStatus.STALE:
            resolve_store(identity, "rejected_stale")
            if pending.pop(identity, None) is not None:
                ui_summary["pending"] -= 1
        if status is RequestStatus.SEND_FAILED:
            resolve_store(identity, "send_failed")
            if pending.pop(identity, None) is not None:
                ui_summary["pending"] -= 1
        record_rejected(raw_identity, status.value)
        if status is RequestStatus.SEND_FAILED:
            raise EngineError(
                "ui_send_failed",
                "the reserved UI response could not be written",
                started=True,
            )

    def process_request(event: dict[str, Any]) -> None:
        request = parse_ui_request(event)
        raw_id = event.get("id")
        request_id = raw_id if isinstance(raw_id, str) else ""
        identity_mapping = {
            "job_id": job_id,
            "session_generation": session_generation,
            "turn_generation": turn_generation,
            "request_id": request_id,
        }
        if isinstance(request, UiFrameError):
            record_rejected(identity_mapping, request.code)
            return
        if abort_sent:
            record_rejected(identity_mapping, "session_aborted")
            return
        identity = RequestIdentity(
            job_id=job_id,
            session_generation=session_generation,
            turn_generation=turn_generation,
            request_id=request.id,
        )
        status = registry.open(identity, request, transport)
        if status is RequestStatus.PENDING:
            pending[identity] = request
            return
        record_rejected(identity_mapping, status.value)
        if status is RequestStatus.UNDELIVERABLE:
            resolve_store(identity, "send_failed")
            raise EngineError(
                "ui_control_unavailable",
                "operator request could not be delivered safely",
                started=True,
            )

    try:
        while True:
            if session.agent_ended:
                break
            register_clarify_requests()
            if cancel_event is not None and cancel_event.is_set():
                # A finished turn wins over a late interrupt: read what omp
                # already queued before deciding to abort.
                if _drain_queued_for_end(session):
                    break
                send_abort()
                raise EngineError("cancelled", "prompt cancelled", started=True)
            try:
                queued = ui_control.next_command(
                    job_id=job_id, session_generation=session_generation
                )
            except (OSError, UIControlError) as exc:
                raise EngineError(
                    "ui_control", "unable to read operator command queue", started=True
                ) from exc
            if queued is not None:
                process_command(queued)
            if session.agent_ended:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if abort_sent:
                    break
                raise EngineError(
                    "timeout", "timed out waiting for agent_end", started=True
                )
            # Poll cadence only; turn completion still uses the caller's deadline.
            event = session._next(min(remaining, 0.05))
            if event is None:
                continue
            if event.get("type") == "extension_ui_request":
                process_request(event)
            if (
                str(event.get("id") or "") == command_id
                and event.get("type") in {"response", "error"}
                and event.get("success") is False
            ):
                raise EngineError("protocol", "prompt was rejected", started=True)
            if session.agent_ended:
                break
    except BaseException:
        with contextlib.suppress(OSError):
            mark_pending("aborted" if abort_sent else "rejected_stale")
        raise

    if not mark_pending("aborted" if abort_sent else "rejected_stale"):
        raise EngineError(
            "ui_control", "unable to resolve pending operator requests", started=True
        )
    if abort_sent:
        raise EngineError("aborted", "omp session was aborted", started=True)
    if not session.agent_ended:
        raise EngineError("timeout", "timed out waiting for agent_end", started=True)


def _sum_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        current = merged.get(key)
        if isinstance(value, bool) or isinstance(current, bool):
            continue
        if isinstance(value, (int, float)) and isinstance(current, (int, float)):
            merged[key] = current + value
        elif key not in merged:
            merged[key] = value
    return merged


def _collect_output(
    events: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], float | None]:
    deltas: list[str] = []
    ended = ""
    usage: dict[str, Any] = {}
    cost = 0.0
    saw_cost = False
    for event in events:
        kind = event.get("type")
        if kind == "message_update":
            piece = assistant_text(event)
            if piece:
                deltas.append(piece)
        elif kind == "message_end":
            piece = assistant_text(event)
            if piece:
                ended = piece
            found, found_cost = usage_of(event)
            if found:
                usage = _sum_usage(usage, found)
            if found_cost is not None:
                cost += found_cost
                saw_cost = True
    text = ended or "".join(deltas)
    return text, usage, cost if saw_cost else None


def session_steer(session: OmpSession, message: str) -> dict[str, Any]:
    command_id = session.send("steer", message=message)
    return session.wait_response(command_id, _COMMAND_TIMEOUT)


def session_abort(session: OmpSession) -> dict[str, Any]:
    command_id = session.send("abort")
    return session.wait_response(command_id, _COMMAND_TIMEOUT)


def probe(
    env: Mapping[str, str],
    *,
    unsandboxed: bool | None = None,
    stage: str | None = None,
    plan: str | None = None,
) -> tuple[bool, str]:
    """Whether a turn can run on omp. Does not call a model API.

    With ``stage``, the stage's plan order (``plan`` first, else ChatGPT,
    then Z.ai) must have an allowlisted plan. ``plan`` is the caller's
    requested plan and ``stage`` the GSD stage, both exactly as
    ``run(plan=..., stage=...)`` will receive them, so readiness accepts what
    the router will select and refuses what it will refuse. With the
    omp-sandbox backend selected a stageless turn is checked as the
    ``execute`` stage it will run on.
    """
    from omega_prime.engine import sandbox_omp

    binary = resolve_omp(env)
    if binary is None:
        return False, "not_configured"
    backend = sandbox_omp.selected(env)
    if backend:
        stage = sandbox_omp.backend_stage(stage)
    try:
        select(env, requested=plan, stage=stage)
    except PlanNotAllowed:
        return False, "plan_not_allowed"
    if backend:
        # The backend's honest readiness, in its own order: the host omp
        # binary still backs read_usage, so the same version check applies;
        # bwrap readiness never gates the backend.
        try:
            omp_version(binary, env)
        except EngineError as exc:
            return False, exc.reason
        if sandbox_omp.anda_binary(env) is None:
            return False, "sandbox_unavailable"
        if sandbox_omp.sandbox_root(env) is None:
            return False, "sandbox_unavailable"
        return True, "ok"
    if not sandbox_ready(env, unsandboxed_override=unsandboxed):
        return False, "sandbox_unavailable"
    try:
        omp_version(binary, env)
    except EngineError as exc:
        return False, exc.reason
    return True, "ok"


def state_root_for(env: Mapping[str, str] | None) -> Path:
    import os

    mapping = env if env is not None else os.environ
    return state_dir(mapping)

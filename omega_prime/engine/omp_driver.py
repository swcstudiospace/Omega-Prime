# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Spawn ``omp --mode rpc`` and speak the NDJSON subset Phase A1a needs."""

from __future__ import annotations

import contextlib
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omega_prime.engine.jobs import (
    append_event,
    child_env,
    job_dir,
    maybe_tmux,
    omp_home_override,
    state_dir,
    write_json,
)
from omega_prime.engine.mcp_bridge import (
    ENGINE_SYSTEM_PROMPT,
    skills_dir,
    write_mcp_json,
)
from omega_prime.engine.plan_router import PlanNotAllowed, read_usage, select
from omega_prime.engine.protocol import (
    EVENT_FILTER,
    MODES,
    OMP_SUPPORTED_MAJOR,
    THINKING_LEVELS,
    assistant_text,
    dumps,
    loads,
    parse_version,
    usage_of,
)
from omega_prime.engine.sandbox import SandboxUnavailable, sandbox_ready, wrap

_READY_TIMEOUT = 15.0
_COMMAND_TIMEOUT = 15.0


_EOF = object()


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
        if self.proc.stdin is None:
            raise EngineError("protocol", "omp stdin is closed")
        self.proc.stdin.write(dumps(payload) + "\n")
        self.proc.stdin.flush()
        return command_id

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
    stage: str | None = None,
    interrupt: Any | None = None,
) -> EngineResult:
    """Run one prompt on omp and return the assistant text, usage, and cost.

    ``stage`` (``plan`` or ``execute``) picks that stage's model and lets the
    router fall back from ChatGPT to Z.ai; see ``plan_router.select``.
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
            False, token or "job", reason="invalid_job", detail=str(exc)
        )
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
            interrupt=interrupt,
        )
    except (EngineError, PlanNotAllowed, SandboxUnavailable, OSError) as exc:
        reason: str | None
        if isinstance(exc, OSError):
            reason = "process"
            detail = f"omp closed the pipe: {type(exc).__name__}"
            started = False
        else:
            reason = getattr(exc, "reason", None)
            if reason is None:
                reason = (
                    "plan_not_allowed"
                    if isinstance(exc, PlanNotAllowed)
                    else "sandbox_unavailable"
                )
            detail = str(getattr(exc, "detail", None) or exc)
            started = bool(getattr(exc, "started", False))
        events_path = directory / "events.jsonl"
        with contextlib.suppress(OSError):
            if events_path.is_file():
                append_event(
                    events_path,
                    {"type": "job_end", "ok": False, "reason": str(reason or "engine")},
                )
        return EngineResult(
            False,
            token,
            reason=str(reason),
            detail=detail,
            events_path=str(directory / "events.jsonl"),
            started=started,
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
    interrupt: Any | None = None,
) -> EngineResult:
    if mode not in MODES:
        raise EngineError("invalid_mode", "mode must be rpc or rpc-ui")
    if thinking not in THINKING_LEVELS:
        raise EngineError("invalid_thinking", "thinking level is not recognised")
    binary = resolve_omp(env)
    if binary is None:
        raise EngineError("not_configured", "omp is not on PATH")
    version = omp_version(binary, env)
    if not sandbox_ready(env, unsandboxed_override=unsandboxed):
        raise SandboxUnavailable(
            "bubblewrap is not available; set OMEGA_PRIME_ENGINE_UNSANDBOXED=1 "
            "to run omp without a sandbox"
        )
    usage = read_usage(binary, env)
    choice = select(env, usage, requested=plan, stage=stage)
    # Per-job config dir (mcp.json, process cwd). It is not an omp --profile:
    # jobs share omp's default profile and its single login store.
    profile = directory / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    skills = skills_dir()
    work = Path(cwd).resolve() if cwd is not None else Path.cwd()
    mcp_path = write_mcp_json(profile, work_root=work)
    prompt_text = _system(system_prompt)
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
    if skills.is_dir():
        argv.extend(["--skills", str(skills)])
    binds = [directory, work] if work != directory else [directory]
    store = omp_home(env)
    if store.is_dir() and store not in binds:
        # Writable so omp can save a refreshed login in place.
        binds.append(store)
    wrapped, sandbox = wrap(
        argv,
        env=env,
        binds=binds,
        unsandboxed_override=unsandboxed,
    )
    events_path = directory / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.touch(exist_ok=True)
    write_json(
        directory / "session.json",
        {
            "job_id": job_id,
            "plan": choice.decision.plan,
            "provider": choice.decision.provider,
            "model": choice.decision.model_id,
            "stage": choice.stage,
            "fallback_from": choice.fallback_from,
            "sandbox": sandbox,
            "mcp_json": str(mcp_path),
            "skills": str(skills) if skills.is_dir() else "",
            "omp_major": version[0],
            "mode": mode,
        },
    )
    maybe_tmux(job_id, env, enabled=tmux)
    process_env = child_env(env)
    if choice.stage is not None:
        # omp hands its env to the omega-prime MCP server it starts, so a
        # delegate_task from a staged turn knows to build on execute.
        process_env["OMEGA_PRIME_ENGINE_STAGE"] = choice.stage
    proc = subprocess.Popen(
        wrapped,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(profile),
        env=process_env,
        bufsize=1,
    )
    session = OmpSession(proc, events_path)
    commands: list[str] = []
    started = False
    stats: dict[str, Any] = {}
    try:
        session.wait_type("ready", _READY_TIMEOUT)
        commands.append(
            _roundtrip(
                session,
                "set_event_filter",
                events=EVENT_FILTER,
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
        prompt_id = session.send("prompt", message=prompt, streamingBehavior="steer")
        started = True
        commands.append("prompt")
        _accept_prompt(session, prompt_id, interrupt)
        try:
            stats_id = session.send("get_session_stats")
            commands.append("get_session_stats")
            response = session.wait_response(stats_id, _COMMAND_TIMEOUT)
            data = response.get("data")
            if isinstance(data, dict):
                stats = data
        except EngineError:
            stats = {}
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
        session.close()
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
        stats=stats,
        sandbox=sandbox,
        commands=commands,
        started=True,
    )
    write_json(
        directory / "session.json",
        {
            "job_id": job_id,
            "ok": True,
            "plan": result.plan,
            "provider": result.provider,
            "model": result.model,
            "stage": result.stage,
            "fallback_from": result.fallback_from,
            "sandbox": sandbox,
            "mcp_json": str(mcp_path),
            "skills": str(skills) if skills.is_dir() else "",
            "commands": commands,
            "cost": cost,
            "final_text": text[:2000],
        },
    )
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
                    return
            raise EngineError("cancelled", "prompt cancelled", started=True)


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
) -> tuple[bool, str]:
    """Whether a turn can run on omp. Does not call a model API.

    With ``stage``, the stage's plan order (ChatGPT, then Z.ai) must have an
    allowlisted plan, matching what ``run(stage=...)`` will select.
    """
    binary = resolve_omp(env)
    if binary is None:
        return False, "not_configured"
    try:
        if stage is None:
            select(env, requested=None)
        else:
            select(env, stage=stage)
    except PlanNotAllowed:
        return False, "plan_not_allowed"
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

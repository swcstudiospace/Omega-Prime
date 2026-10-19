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
_TURN_TIMEOUT = 120.0


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
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stdout = threading.Thread(
            target=_pump, args=(proc.stdout, self._queue), daemon=True
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
            event = self._next(deadline - time.monotonic())
            if event is None:
                break
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
            event = self._next(deadline - time.monotonic())
            if event is None:
                break
            if str(event.get("id") or "") == command_id and event.get("type") in {
                "response",
                "error",
            }:
                return event
        raise EngineError("timeout", "timed out waiting for a command response")

    def read_until_agent_end(self, timeout: float) -> None:
        if self.agent_ended:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self._next(deadline - time.monotonic())
            if event is None or self.agent_ended:
                return
        raise EngineError("timeout", "timed out waiting for agent_end")

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
        if timeout < 0:
            return None
        try:
            line = self._queue.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            return None
        if line is None:
            return None
        event = loads(line)
        if event is None:
            return self._next(timeout)
        self.events.append(event)
        append_event(self.events_path, event)
        if event.get("type") == "agent_end":
            self.agent_ended = True
        return event


def _pump(stream: Any, sink: queue.Queue[str | None]) -> None:
    if stream is None:
        sink.put(None)
        return
    for line in stream:
        sink.put(line)
    sink.put(None)


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
    timeout: float | None = None,
    tmux: bool | None = None,
) -> EngineResult:
    """Run one prompt on omp and return the assistant text, usage, and cost."""
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
            timeout=timeout if timeout is not None else _TURN_TIMEOUT,
            tmux=tmux,
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
    timeout: float,
    tmux: bool | None,
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
    choice = select(env, usage, requested=plan)
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
        "--profile",
        str(profile),
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
    wrapped, sandbox = wrap(
        argv,
        env=env,
        binds=[directory, work] if work != directory else [directory],
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
            "sandbox": sandbox,
            "mcp_json": str(mcp_path),
            "skills": str(skills) if skills.is_dir() else "",
            "omp_major": version[0],
            "mode": mode,
        },
    )
    maybe_tmux(job_id, env, enabled=tmux)
    proc = subprocess.Popen(
        wrapped,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(profile),
        env=child_env(env),
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
        _accept_prompt(session, prompt_id, timeout)
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


def _accept_prompt(session: OmpSession, command_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    saw_response = False
    while time.monotonic() < deadline:
        event = session._next(deadline - time.monotonic())
        if event is None:
            break
        if str(event.get("id") or "") == command_id and event.get("type") in {
            "response",
            "error",
        }:
            saw_response = True
            if event.get("success") is False:
                raise EngineError("protocol", "prompt was rejected")
        if session.agent_ended and saw_response:
            return
        if session.agent_ended and not saw_response:
            # Some fakes emit agent_end and the response in either order.
            return
    if not session.agent_ended:
        raise EngineError("timeout", "timed out waiting for agent_end")


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
    env: Mapping[str, str], *, unsandboxed: bool | None = None
) -> tuple[bool, str]:
    """Whether a turn can run on omp. Does not call a model API."""
    binary = resolve_omp(env)
    if binary is None:
        return False, "not_configured"
    try:
        select(env, requested=None)
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

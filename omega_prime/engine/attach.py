# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Prefer the omp engine from the existing loops, and fall back when it is not ready."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.engine.jobs import job_dir, mcp_state_dir
from omega_prime.engine.omp_driver import EngineResult, omp_home, probe, run
from omega_prime.engine.ui_control import UIControlStore

_OFF = frozenset({"0", "off", "python", "false", "no"})
_TRANSCRIPT_CHARS = 4_000
_PROMPT_LIMIT = 100_000
_REQUIRED = frozenset({"omp", "on", "1", "required", "true", "yes"})
_NON_LIVE = frozenset({"ScriptedModel", "FakeModel", "FakeProvider"})


def engine_mode(env: Mapping[str, str]) -> str:
    raw = str(env.get("OMEGA_PRIME_ENGINE", "auto")).strip().lower()
    if raw in _OFF:
        return "off"
    if raw in _REQUIRED:
        return "required"
    return "auto"


def _live_model(model: Any) -> bool:
    current = model
    for _ in range(5):
        if current is None:
            return False
        name = type(current).__name__
        module = type(current).__module__
        if name in _NON_LIVE:
            return False
        if module.startswith("omega_prime.providers."):
            return True
        nxt = getattr(current, "provider", None)
        if nxt is None or nxt is current:
            return False
        current = nxt
    return False


def _prompt_text(user_message: Any) -> str:
    if isinstance(user_message, str):
        return user_message
    try:
        return json.dumps(user_message, ensure_ascii=False, default=str)[:100000]
    except TypeError:
        return str(user_message)[:100000]


def _restricted(agent: Any) -> bool:
    """Plan mode and narrowed tool maps stay on the Python loop.

    The MCP bridge serves the installation roster. It does not apply
    ``plan_mode`` refusals or a ``child_tool_hook``. A hand-built tool map,
    or an Omega Prime agent constructed with a roster, is a restriction.
    ``engine_full_tools`` marks a registry-wide agent that the roster already
    covers.
    """
    if getattr(agent, "plan_mode", False):
        return True
    if getattr(agent, "child_tool_hook", None) is not None:
        return True
    if getattr(agent, "tool_roster", None) is not None:
        return True
    if getattr(agent, "engine_full_tools", False):
        return False
    tools = getattr(agent, "tools", None)
    return isinstance(tools, dict) and len(tools) > 0


def _with_history(user_message: Any, conversation_history: list | None) -> str:
    """Send the prior transcript with the latest request."""
    latest = _prompt_text(user_message)
    if not conversation_history:
        return latest
    lines: list[str] = []
    for row in conversation_history[-40:]:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        if role not in {"system", "user", "assistant"}:
            continue
        content = row.get("content")
        text = content if isinstance(content, str) else _prompt_text(content)
        if not text:
            continue
        lines.append(f"{role}: {text[:_TRANSCRIPT_CHARS]}")
    if not lines:
        return latest[:_PROMPT_LIMIT]
    footer = f"\n\nCurrent request:\n{latest}"
    if len(footer) >= _PROMPT_LIMIT:
        return latest[:_PROMPT_LIMIT]
    header = "Prior transcript:\n"
    room = _PROMPT_LIMIT - len(header) - len(footer)
    # ``transcript[-0:]`` is the whole string, so a full request must drop history.
    if room <= 0:
        return latest[:_PROMPT_LIMIT]
    transcript = "\n".join(lines)
    if len(transcript) > room:
        transcript = transcript[-room:]
    return f"{header}{transcript}{footer}"


def _mapping(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is None:
        return os.environ
    return env


def route_turn(
    agent: Any,
    user_message: Any,
    *,
    system_message: str | None,
    conversation_history: list | None,
    env: Mapping[str, str] | None = None,
    task_id: str | None = None,
    interrupt: Any | None = None,
) -> dict[str, Any] | None:
    """Run one turn on omp, or return None so the caller keeps the Python loop.

    Auto mode uses omp only when a binary, an allowlisted plan, and a sandbox
    are all present, and only for a live provider model. Required mode refuses
    instead of calling that provider. Scripted and fake models stay on the
    Python loop unless ``agent.force_engine`` is set. Plan mode and a narrowed
    tool map always stay on the Python loop. Once the prompt has been sent,
    a later failure is returned instead of falling back to Python.
    """
    mapping = _mapping(env)
    mode = engine_mode(mapping)
    forced = bool(getattr(agent, "force_engine", False))
    if _restricted(agent):
        return None
    if mode == "off" and not forced:
        return None
    if not forced and not _live_model(getattr(agent, "model", None)):
        return None
    if mode == "auto" and not forced:
        ready, _reason = probe(mapping)
        if not ready:
            return None
    job_id = getattr(agent, "engine_job_id", None) or "job-" + uuid.uuid4().hex[:8]
    engine_cwd = getattr(agent, "engine_cwd", None)
    work_root = (
        Path(os.path.abspath(engine_cwd)) if engine_cwd is not None else Path.cwd()
    )
    try:
        binds: list[Path | str] = [
            job_dir(mapping, job_id),
            work_root,
            mcp_state_dir(mapping),
        ]
        omp_store = omp_home(mapping)
        if omp_store.is_dir() and omp_store not in binds:
            binds.append(omp_store)
        ui_control = UIControlStore.from_env(mapping, writable_binds=binds)
    except (OSError, ValueError):
        result = EngineResult(
            False,
            job_id,
            reason="ui_control_unavailable",
            detail="rpc-ui requires a safe operator control store",
            mode="rpc-ui",
        )
        return _refused(
            agent, user_message, conversation_history, result, task_id=task_id
        )
    with ui_control:
        result = run(
            _with_history(user_message, conversation_history),
            cwd=work_root,
            system_prompt=system_message,
            thinking=getattr(agent, "engine_thinking", None),
            plan=getattr(agent, "engine_plan", None),
            env=mapping,
            job_id=job_id,
            mode="rpc-ui",
            ui_control=ui_control,
            cancel_event=(
                interrupt
                if interrupt is not None
                else getattr(agent, "interrupt", None)
            ),
        )
    if not result.ok:
        if (
            mode == "required"
            or forced
            or result.started
            or result.reason == "ui_control_unavailable"
        ):
            return _refused(
                agent, user_message, conversation_history, result, task_id=task_id
            )
        return None
    return _accepted(user_message, conversation_history, result, task_id=task_id)


def _history(user_message: Any, conversation_history: list | None, text: str) -> list:
    messages = conversation_history if conversation_history is not None else []
    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": text})
    return messages


def _engine_body(result: EngineResult) -> dict[str, Any]:
    return {
        "job_id": result.job_id,
        "plan": result.plan,
        "provider": result.provider,
        "model": result.model,
        "events": result.events_path,
        "usage": result.usage,
        "cost": result.cost,
        "sandbox": result.sandbox,
        "mcp_status": result.mcp_status,
        "mcp_cleanup_conflicts": result.cleanup_conflicts,
        "started": result.started,
    }


def _stamp(result: dict[str, Any], task_id: str | None) -> dict[str, Any]:
    if task_id is not None:
        result["task_id"] = task_id
    return result


def _accepted(
    user_message: Any,
    conversation_history: list | None,
    result: EngineResult,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    messages = _history(user_message, conversation_history, result.final_text)
    return _stamp(
        {
            "final_response": result.final_text,
            "messages": messages,
            "api_calls": 1,
            "completed": True,
            "interrupted": False,
            "failed": False,
            "turn_exit_reason": "text_response(finish_reason=engine)",
            "engine": _engine_body(result),
        },
        task_id,
    )


def _refused(
    agent: Any,
    user_message: Any,
    conversation_history: list | None,
    result: EngineResult,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    del agent
    text = f"engine refused: {result.reason}"
    messages = _history(user_message, conversation_history, text)
    return _stamp(
        {
            "final_response": text,
            "messages": messages,
            "api_calls": 0,
            "completed": False,
            "interrupted": result.reason == "cancelled",
            "failed": result.reason != "cancelled",
            "turn_exit_reason": result.reason or "engine",
            "engine": {
                "reason": result.reason,
                "detail": result.detail,
                "started": result.started,
            },
        },
        task_id,
    )


def delegate_via_engine(
    goal: str,
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    interrupt: Any | None = None,
) -> str | None:
    """JSON summary from omp, or None when the Python child path should run."""
    mapping = _mapping(env)
    mode = engine_mode(mapping)
    if mode == "off":
        return None
    ready, reason = probe(mapping)
    if not ready and mode != "required":
        return None
    if not ready and mode == "required":
        return json.dumps({"error": reason}, ensure_ascii=False)
    result = run(goal, env=mapping, cwd=cwd, interrupt=interrupt)
    if not result.ok:
        if mode == "required" or result.started:
            return json.dumps(
                {"error": result.reason or "engine", "detail": result.detail},
                ensure_ascii=False,
            )
        return None
    return json.dumps(
        {"summary": result.final_text, "engine": "omp", "job_id": result.job_id},
        ensure_ascii=False,
    )


def desk_engine_dispatch(
    goal: str,
    env: Mapping[str, str],
    *,
    cwd: str | None = None,
) -> dict[str, Any] | None:
    """One desk ticket on omp. None means keep the existing blocked path."""
    raw = delegate_via_engine(goal, env=env, cwd=cwd)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, str) and error:
        return {
            "status": "blocked",
            "reason": error,
            "engine": "omp",
        }
    summary = payload.get("summary")
    if not isinstance(summary, str):
        return None
    return {
        "status": "done",
        "summary": summary,
        "engine": "omp",
        "job_id": payload.get("job_id"),
        "receipt": {
            "commands": [
                {
                    "cmd": "omp --mode rpc",
                    "exit_code": 0,
                    "output_tail": summary[-2000:],
                }
            ],
            "claims": [
                {
                    "claim": "desk ticket ran on the omp engine",
                    "evidence_command_index": 0,
                }
            ],
            "unverified": ["the omp final text is not independently re-executed"],
            "destructive": False,
            "approved_by": None,
        },
    }


def _required_miss(env: Mapping[str, str], reason: str) -> dict[str, Any]:
    body: dict[str, Any] = {"used": False, "reason": reason}
    if engine_mode(env) == "required":
        body["failed"] = True
        body["error"] = reason
    return body


def ultrathink_engine(
    plan: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, Any]:
    """Run a resolved ultrathink spec on omp when the engine is ready."""
    mode = engine_mode(env)
    if mode == "off":
        return {"used": False, "reason": "off"}
    ready, reason = probe(env)
    if not ready:
        return _required_miss(env, reason)
    if not plan.get("found"):
        return {"used": False, "reason": "no_plan"}
    spec = plan.get("spec_path")
    if not isinstance(spec, str) or not spec:
        return {"used": False, "reason": "no_spec"}
    path = _spec_path(spec)
    if path is None or not path.is_file():
        return {"used": False, "reason": "no_spec"}
    try:
        text = path.read_text(encoding="utf-8")[:100000]
    except OSError:
        return {"used": False, "reason": "no_spec"}
    result = run(
        "Carry out this ultrathink spec.\n\n" + text,
        env=env,
        system_prompt="Ultrathink turn. Stay inside the spec.",
    )
    if not result.ok:
        body = {
            "used": False,
            "reason": result.reason or "engine",
            "detail": result.detail,
        }
        if engine_mode(env) == "required" or result.started:
            body["failed"] = True
            body["error"] = result.reason or "engine"
        return body
    return {
        "used": True,
        "job_id": result.job_id,
        "plan": result.plan,
        "final_text": result.final_text[:2000],
    }


def _spec_path(spec: str):
    from pathlib import Path

    try:
        return Path(spec)
    except (TypeError, ValueError):
        return None

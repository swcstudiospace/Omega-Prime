"""One tool round: persist the assistant call, append one tool row per call, then steer.

Adapted from Hermes ``agent/turn_tool_round.py`` and ``apply_pending_steer_to_tool_results``.
The steer is its own user message after the tool row. The tool row is not edited.
No guardrail halt, session-db flush, or post-tool compression runs here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from omega_prime.agent.prompt_builder import steer_user_row


@dataclass
class ToolRoundVerdict:
    """``action`` is ``continue`` (another iteration) or ``break`` (turn ends)."""

    action: str
    final_response: str | None = None
    turn_exit_reason: str | None = None


def run_tool_round(
    agent: Any,
    *,
    assistant_message: dict,
    messages: list,
    task_id: str | None = None,
) -> ToolRoundVerdict:
    """Append the assistant tool-call message and one tool message per call.

    A call whose arguments are not a JSON object never reaches its tool: its
    row is an ``error:`` row and the other calls of the round still run.
    If ``agent.pending_steer`` is set, append a new user row after those tool
    messages and clear the pending text. Do not write the steer into a tool row.
    """
    tool_calls = _tool_calls(assistant_message)
    messages.append(_assistant_row(assistant_message, tool_calls))
    for call in tool_calls:
        name, arguments, call_id, problem = _split_call(call)
        content = (
            f"error: {problem}"
            if problem is not None
            else _execute(agent, name, arguments)
        )
        messages.append(
            {
                "role": "tool",
                "name": name,
                "tool_call_id": call_id,
                "content": content,
            }
        )
        _report_tool_call(agent, name, arguments, content)
    steer = _drain_pending_steer(agent)
    if steer and tool_calls:
        messages.append(steer_user_row(steer))
    return ToolRoundVerdict(action="continue")


def _tool_calls(message: dict) -> list:
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, dict)]


def _assistant_row(message: dict, tool_calls: list) -> dict:
    content = message.get("content") if isinstance(message, dict) else None
    row = {"role": "assistant", "content": content, "tool_calls": tool_calls}
    if "prime_message" in message:
        row["prime_message"] = message["prime_message"]
    return row


def _split_call(call: dict) -> tuple[str, dict, str | None, str | None]:
    """``(name, arguments, call_id, problem)``; ``problem`` set means do not run."""
    raw = call.get("function")
    function: dict[str, Any] = raw if isinstance(raw, dict) else {}
    name = str(function.get("name") or "")
    call_id = call.get("id")
    arguments, problem = _arguments(name, function.get("arguments"))
    return (
        name,
        arguments,
        call_id if isinstance(call_id, str) else None,
        problem,
    )


def _arguments(name: str, raw: Any) -> tuple[dict, str | None]:
    """Decode one call's arguments; a malformed payload is a problem, never ``{}``."""
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    problem = f"tool {name} received arguments that are not a valid JSON object"
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            return {}, f"{problem} ({getattr(exc, 'msg', type(exc).__name__)})"
        if isinstance(parsed, dict):
            return parsed, None
    return {}, problem


def _execute(agent: Any, name: str, arguments: dict) -> str:
    tools = getattr(agent, "tools", None) or {}
    fn = tools.get(name)
    if fn is None:
        return f"error: unknown tool {name}"
    try:
        result = fn(**arguments) if arguments else _call_without_args(fn)
    except Exception as exc:
        return f"error: {exc}"
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    return str(result)


def _report_tool_call(agent: Any, name: str, arguments: dict, content: str) -> None:
    """Report one executed call to the substrate session. Never raises."""
    substrate = getattr(agent, "substrate", None)
    if substrate is None:
        return
    try:
        ok = not (isinstance(content, str) and content.startswith("error:"))
        substrate.on_tool_call(name, arguments, ok=ok)
    except Exception:
        pass


def _call_without_args(fn: Any) -> Any:
    try:
        return fn()
    except TypeError:
        return fn()


def _drain_pending_steer(agent: Any) -> str | None:
    text = getattr(agent, "pending_steer", None)
    if text is None:
        return None
    agent.pending_steer = None
    cleaned = str(text).strip()
    return cleaned or None


__all__ = ["ToolRoundVerdict", "run_tool_round"]

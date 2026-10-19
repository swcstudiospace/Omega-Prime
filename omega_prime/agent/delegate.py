"""Isolated child agent. The parent sees only the child's final response.

Adapted from Hermes ``tools/delegate_tool.py``. The child is an ``Agent`` running
``run_conversation``. Credentials, ACP, gateway delivery, and threads are not
ported. ``background=True`` returns a handle; ``join_delegate`` runs the child.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from omega_prime.agent.conversation_loop import Agent, run_conversation

_PENDING: dict[str, dict[str, Any]] = {}


def delegate_task(
    parent: Any,
    goal: Any,
    *,
    tasks: Any = None,
    max_depth: int = 2,
    max_children: int = 1,
    background: bool = False,
) -> str:
    """Run one child per goal, or return a handle when ``background`` is set.

    Returns JSON. A finished child puts its ``final_response`` in ``summary``.
    The child tool map is a new dict. ``parent.tools`` is not edited.
    """
    goals, error = _goals(goal, tasks)
    if error is not None:
        return _error(error)
    assert goals is not None
    depth = getattr(parent, "delegate_depth", 0)
    if depth >= max_depth:
        return _error(f"delegate depth {depth} reached max_depth {max_depth}")
    if len(goals) > max_children:
        return _error(
            f"batch of {len(goals)} tasks exceeds max_children {max_children}"
        )
    if getattr(parent, "child_model", None) is None:
        if len(goals) == 1 and not background:
            from omega_prime.engine.attach import _restricted, delegate_via_engine

            # A plan-mode parent, a child_tool_hook, or a narrowed tool map
            # must not receive the full MCP registry.
            if not _restricted(parent):
                routed = delegate_via_engine(goals[0])
                if routed is not None:
                    return routed
        return _error("parent.child_model is required")
    if background:
        handle = uuid.uuid4().hex
        _PENDING[handle] = {
            "parent": parent,
            "goals": list(goals),
            "max_depth": max_depth,
            "max_children": max_children,
            "ran": False,
            "summary": None,
        }
        return json.dumps({"pending": True, "handle": handle}, ensure_ascii=False)
    summaries = _run_goals(parent, goals, max_depth, max_children)
    return _foreground(summaries)


def join_delegate(handle: str) -> str:
    """Run a background child if it has not run, and return its summary.

    A second join returns the same summary and does not run the child again.
    One task returns that child's ``final_response``. A batch returns a JSON
    array of those strings.
    """
    job = _PENDING.get(handle)
    if job is None:
        return _error(f"unknown delegate handle {handle}")
    if job["ran"]:
        return job["summary"]
    summaries = _run_goals(
        job["parent"], job["goals"], job["max_depth"], job["max_children"]
    )
    summary = (
        summaries[0]
        if len(summaries) == 1
        else json.dumps(summaries, ensure_ascii=False)
    )
    job["summary"] = summary
    job["ran"] = True
    return summary


def _run_goals(
    parent: Any, goals: list[str], max_depth: int, max_children: int
) -> list[str]:
    return [_run_child(parent, goal, max_depth, max_children) for goal in goals]


def _run_child(parent: Any, goal: str, max_depth: int, max_children: int) -> str:
    child_depth = getattr(parent, "delegate_depth", 0) + 1
    child_tools = dict(parent.tools)
    holder: dict[str, Any] = {}
    if child_depth < max_depth:
        child_tools["delegate_task"] = _bind_delegate(holder, max_depth, max_children)
    else:
        child_tools.pop("delegate_task", None)
    child = Agent(model=parent.child_model, tools=child_tools)
    from omega_prime.durable.journal import TurnJournal, attach_journal

    parent_journal = getattr(parent, "journal", None)
    if isinstance(parent_journal, TurnJournal):
        attach_journal(child, parent_journal)
    child.delegate_depth = child_depth
    child.max_depth = max_depth
    child.max_children = max_children
    child.child_model = parent.child_model
    holder["child"] = child
    # The hook may pop keys from this dict. It is the child's map, not the parent's.
    hook = getattr(parent, "child_tool_hook", None)
    if hook is not None:
        hook(child_tools)
    result = run_conversation(child, goal)
    response = result.get("final_response") if isinstance(result, dict) else ""
    return response if isinstance(response, str) else ""


def _bind_delegate(holder: dict[str, Any], max_depth: int, max_children: int):
    """A ``delegate_task`` callable bound to the child, not the parent."""

    def delegate_task_tool(
        goal: Any = None, tasks: Any = None, background: bool = False
    ) -> str:
        agent = holder["child"]
        return delegate_task(
            agent,
            goal,
            tasks=tasks,
            max_depth=getattr(agent, "max_depth", max_depth),
            max_children=getattr(agent, "max_children", max_children),
            background=background,
        )

    return delegate_task_tool


def _goals(goal: Any, tasks: Any) -> tuple[list[str] | None, str | None]:
    if tasks is not None:
        if not isinstance(tasks, list):
            return None, "tasks must be a list"
        goals: list[str] = []
        for item in tasks:
            if not isinstance(item, dict) or not isinstance(item.get("goal"), str):
                return None, "each task must be an object with a goal string"
            goals.append(item["goal"])
        if not goals:
            return None, "tasks must not be empty"
        return goals, None
    if goal is None:
        return None, "goal or tasks is required"
    if not isinstance(goal, str):
        return None, "goal must be a string"
    return [goal], None


def _foreground(summaries: list[str]) -> str:
    if len(summaries) == 1:
        return json.dumps({"summary": summaries[0]}, ensure_ascii=False)
    return json.dumps({"summaries": summaries}, ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


__all__ = ["delegate_task", "join_delegate"]

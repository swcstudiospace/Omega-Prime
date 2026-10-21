"""Register skill, memory, and session-search tools on one registry.

The closures bind ``skills_root``, ``memory_dir``, and ``session_db``. The model
does not send those paths. ``dispatch`` returns the JSON strings the functions return.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omega_prime.memory.manager import MemoryManager
from omega_prime.memory.provider import BuiltinMemoryProvider
from omega_prime.memory.store import MemoryStore
from omega_prime.session.search import SessionStore
from omega_prime.session.search import session_search as search_sessions
from omega_prime.skills_runtime.manager import skill_manage as manage_skill
from omega_prime.skills_runtime.manager import skill_view as view_skill
from omega_prime.tools.registry import ToolRegistry

# Names offered after the coding tools. omega_prime/contracts/tool-rosters/omega-prime.yaml lists these.
GROWTH_TOOL_NAMES = (
    "skill_manage",
    "skill_view",
    "memory",
    "session_search",
)


def register_growth_tools(
    registry: ToolRegistry,
    *,
    skills_root: str | Path,
    memory_dir: str | Path,
    session_db: str | Path,
    memory_store: MemoryStore | None = None,
    skills_fallback: str | Path | None = None,
    retain_memory: Callable[[str], dict[str, Any]] | None = None,
) -> list[str]:
    """Register the four growth tools. Handlers close over the three paths.

    ``memory_store`` injects an existing ``MemoryStore`` (DESK-01: the host
    builds one shared store for the growth tools and the lead pack, so
    recalls see retains from both); by default one is built from
    ``memory_dir``.
    """
    store = memory_store if memory_store is not None else MemoryStore(memory_dir)
    provider = BuiltinMemoryProvider(store, retain=retain_memory)
    manager = MemoryManager()
    manager.add_provider(provider)
    sessions = SessionStore(session_db)
    root = skills_root
    shipped = None if skills_fallback is None else Path(skills_fallback)

    def skill_manage(
        action: str,
        name: str,
        content: str | None = None,
        category: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
    ) -> str:
        if action == "create" and shipped is not None and _skill_present(shipped, name):
            return json.dumps(
                {
                    "success": False,
                    "error": f"A skill named '{name}' already exists.",
                },
                ensure_ascii=False,
            )
        return manage_skill(
            action,
            name,
            content=content,
            category=category,
            old_string=old_string,
            new_string=new_string,
            skills_root=root,
        )

    def skill_view(name: str) -> str:
        primary = view_skill(name, skills_root=root)
        if shipped is None or not _skill_missing(primary):
            return primary
        return view_skill(name, skills_root=shipped)

    def memory(
        action: str | None = None,
        target: str = "memory",
        content: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "action": action,
            "target": target,
            "content": content if content is not None else new_text,
            "old_text": old_text,
        }
        return manager.handle_tool_call("memory", payload)

    def session_search(query: str = "") -> str:
        return search_sessions(query, store=sessions)

    handlers: dict[str, Callable[..., Any]] = {
        "skill_manage": skill_manage,
        "skill_view": skill_view,
        "memory": memory,
        "session_search": session_search,
    }
    if tuple(handlers) != GROWTH_TOOL_NAMES:
        raise RuntimeError("growth tool handlers drifted from GROWTH_TOOL_NAMES")
    for name in GROWTH_TOOL_NAMES:
        description, parameters = _SCHEMAS[name]
        registry.register(name, description, parameters, handlers[name])
    return list(GROWTH_TOOL_NAMES)


def _skill_present(root: Path, name: str) -> bool:
    """True when ``skill_view`` reads ``name`` from ``root``."""
    try:
        parsed = json.loads(view_skill(name, skills_root=root))
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and parsed.get("success") is True


def _skill_missing(payload: str) -> bool:
    """True when ``skill_view`` did not find the name in that root.

    A missing skills directory is the same as a missing name: the shipped
    root is still the copy to read.
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict) or parsed.get("success") is True:
        return False
    error = parsed.get("error")
    if not isinstance(error, str):
        return False
    lowered = error.lower()
    return "not found" in lowered or "not a directory" in lowered


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "skill_manage": (
        "Create, edit, or patch a skill's SKILL.md. "
        "create refuses an existing name. patch replaces one exact string and leaves the file "
        "unchanged when the match is not unique.",
        _object(
            {
                "action": _string("create, edit, or patch."),
                "name": _string("Skill directory name. One safe path segment."),
                "content": _string("Full SKILL.md text for create or edit."),
                "category": _string("Optional single directory under the skills root."),
                "old_string": _string(
                    "Exact text to replace for patch. Must match once."
                ),
                "new_string": _string("Replacement text for patch."),
            },
            ["action", "name"],
        ),
    ),
    "skill_view": (
        "Read a skill's SKILL.md from disk and return its text.",
        _object(
            {"name": _string("Skill directory name.")},
            ["name"],
        ),
    ),
    "memory": (
        "Add, replace, or remove one memory or user-profile entry. "
        "A duplicate add does not append. An over-limit add leaves the file unchanged.",
        _object(
            {
                "action": _string("add, replace, or remove."),
                "target": _string("memory or user. Defaults to memory."),
                "content": _string("New entry text for add or replace."),
                "old_text": _string("Entry text to find for replace or remove."),
                "new_text": _string("Alias of content for replace."),
            },
            ["action"],
        ),
    ),
    "session_search": (
        "Find stored session messages whose content contains the query as a literal substring. "
        "An empty query is an error.",
        _object(
            {"query": _string("Literal substring. Not a regular expression.")},
            ["query"],
        ),
    ),
}


__all__ = ["GROWTH_TOOL_NAMES", "register_growth_tools"]

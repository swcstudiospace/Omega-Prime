"""Earn a skill only when the turn actually asked to save one.

``review_turn`` does not invent a procedure. The tool output is XML-escaped
and wrapped in a ``<skill name="{slug}" version="1">`` envelope (behind the
skill frontmatter), written through ``skill_manage``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from omega_prime.memory.provider import is_trivial_prompt
from omega_prime.skills_runtime.guards import validate_name
from omega_prime.skills_runtime.manager import skill_manage

EARNED_DESCRIPTION = "Use when the user asks to save this procedure."
_SAVE_PHRASE = "save as a skill"
_SLUG = re.compile(r"name:([a-z0-9][a-z0-9._-]*)")


def review_turn(
    *, skills_root: str | Path, user_text: str, tool_results: Any
) -> dict[str, Any]:
    """Return ``created`` and ``path``. A miss does not call ``skill_manage``."""
    earned = _earned(user_text, tool_results)
    if earned is None:
        return {"created": False, "path": None}
    slug, output = earned
    raw = skill_manage(
        "create",
        slug,
        content=_skill_text(slug, output),
        skills_root=skills_root,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"created": False, "path": None, "error": "skill_write_failed"}
    if not isinstance(parsed, dict) or parsed.get("success") is not True:
        return {"created": False, "path": None, "error": "skill_write_failed"}
    path = parsed.get("path")
    return {"created": True, "path": path if isinstance(path, str) else None}


def _earned(user_text: Any, tool_results: Any) -> tuple[str, str] | None:
    text = user_text if isinstance(user_text, str) else ""
    if is_trivial_prompt(text):
        return None
    if _SAVE_PHRASE not in text.lower():
        return None
    slug = _slug(text)
    if slug is None:
        return None
    output = _output(tool_results)
    if output is None:
        return None
    return slug, output


def _slug(text: str) -> str | None:
    for match in _SLUG.finditer(text):
        slug = match.group(1)
        if validate_name(slug) is None:
            return slug
    return None


def _output(tool_results: Any) -> str | None:
    if not isinstance(tool_results, list):
        return None
    for item in tool_results:
        if not isinstance(item, dict) or item.get("success") is not True:
            continue
        output = item.get("output")
        if isinstance(output, str) and output != "":
            return output
    return None


def _skill_text(slug: str, output: str) -> str:
    # Escape the tool output so it cannot break out of the envelope.
    body = escape(output)
    return (
        f"---\nname: {slug}\ndescription: {EARNED_DESCRIPTION}\n---\n"
        f'<skill name="{slug}" version="1">\n{body}\n</skill>\n'
    )


__all__ = ["EARNED_DESCRIPTION", "review_turn"]

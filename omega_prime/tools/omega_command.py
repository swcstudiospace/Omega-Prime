# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Register ``omega_command``, the one tool Grok Bot uses for slash commands.

Offered after ``delegate_task``. The roster lists this name in that slot.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from omega_prime.commands import execute_command
from omega_prime.tools.registry import ToolRegistry

OMEGA_COMMAND_TOOL_NAMES = ("omega_command",)

_DESCRIPTION = (
    "Run one Omega Prime slash command. Pass the user's /omega-... text exactly. "
    "Use this instead of the underlying tools when the user typed a slash "
    "command or asked for onboard, connectors, a Python check, desk status, "
    "doctor, roster, recall, retain, gates, or delegate. "
    "Generated commands run here too: /omega-<skill> loads that skill's "
    "instructions, and /omega-<routine> runs that routine's workflow or loads "
    "its instructions. A generated name that matches a curated command or "
    "another generated name takes a -skill or -routine suffix "
    "(/omega-python-skill loads the python skill; /omega-python runs the check). "
    "/omega-help lists every command."
)
_PARAMETERS = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "The slash command, including the leading /omega-.",
        },
        "doctor_context": {
            "type": "object",
            "description": (
                "Optional caller context for the doctor check: "
                "prompt_sha256 and installed_skills only."
            ),
            "properties": {
                "prompt_sha256": {"type": "string"},
                "installed_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    },
    "required": ["command"],
}


def register_omega_command_tools(
    registry: ToolRegistry, env: Mapping[str, str] | None = None
) -> list[str]:
    """Register ``omega_command``. ``env`` is read for connector requests, not logged."""
    mapping: Mapping[str, str] = {} if env is None else env

    def omega_command(command: str, doctor_context: Any = None) -> str:
        text = command if isinstance(command, str) else ""
        payload = execute_command(
            text, registry, mapping, doctor_context=doctor_context
        )
        return json.dumps(payload, ensure_ascii=False)

    registry.register("omega_command", _DESCRIPTION, _PARAMETERS, omega_command)
    return list(OMEGA_COMMAND_TOOL_NAMES)


__all__ = ["OMEGA_COMMAND_TOOL_NAMES", "register_omega_command_tools"]

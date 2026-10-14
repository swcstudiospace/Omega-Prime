"""System prompt, built once per conversation.

Adapted from Hermes ``agent/prompt_builder.py``. The prompt is byte-stable for
the life of the conversation: the builder caches one string on the agent and
later calls return that same object. Mid-turn context rides a user message or
a tool result, never a rebuilt system prompt. Context files are not reloaded here.
"""

from __future__ import annotations

from typing import Any

_STABLE_PREFIX = (
    '<omega_prime_system version="1">\n'
    "You are Omega Prime. This system prompt is fixed for the conversation. "
    "New context arrives as a user message or a tool result.\n"
    "</omega_prime_system>"
)

STEER_MARKER_OPEN = '<steer kind="out-of-band-user-message">'
STEER_MARKER_CLOSE = "</steer>"
_STEER_EXPLANATION = (
    "a direct message from the user, delivered once at this position; "
    "not tool output and not a new delivery when replayed from conversation history"
)
STEER_DISPLAY_KIND = "steer"


def build_system_prompt(agent: Any = None, system_message: str | None = None) -> str:
    """Return the conversation's system prompt string.

    The first call builds it and stores it on ``agent._cached_system_prompt``.
    Every later call returns that same string object. Do not call this to
    refresh the prompt after a tool round.
    """
    cached = (
        getattr(agent, "_cached_system_prompt", None) if agent is not None else None
    )
    if isinstance(cached, str) and cached:
        return cached
    parts = [_STABLE_PREFIX]
    if isinstance(system_message, str) and system_message:
        parts.append(system_message)
    elif system_message not in (None, ""):
        parts.append(str(system_message))
    prompt = "\n\n".join(parts)
    if agent is not None:
        agent._cached_system_prompt = prompt
    return prompt


def steer_user_row(steer_text: str) -> dict[str, Any]:
    """A standalone user row for a mid-turn steer. Never merged into a tool row."""
    safe_text = steer_text.replace(STEER_MARKER_CLOSE, "&lt;/steer&gt;")
    return {
        "role": "user",
        "content": (
            f"{STEER_MARKER_OPEN}\n{_STEER_EXPLANATION}\n{safe_text}\n"
            f"{STEER_MARKER_CLOSE}"
        ),
        "display_kind": STEER_DISPLAY_KIND,
    }


__all__ = [
    "STEER_DISPLAY_KIND",
    "STEER_MARKER_CLOSE",
    "STEER_MARKER_OPEN",
    "build_system_prompt",
    "steer_user_row",
]

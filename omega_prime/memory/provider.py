"""Memory provider surface. Prefetch skips greetings and slash commands.

The trivial-prompt list matches Hermes: empty text, slash commands, and the
greeting and acknowledgement words, with trailing punctuation allowed.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace as replace_dataclass
from typing import Any

from omega_prime.credentials.redact import redact_text
from omega_prime.memory.backends import (
    LegacyRecord,
    LocalRecord,
    decode_local_record,
    encode_local_record,
)
from omega_prime.memory.store import MemoryStore

INDICATOR_GLYPH = "🧠"

# Anchored, then only whitespace or punctuation, so "k8s" and "note" do not match.
TRIVIAL_PROMPT_RE = re.compile(
    r"^(yes|no|ok|okay|sure|thanks|thank you|y|n|yep|nope|yeah|nah|"
    r"hi|hey|hello|yo|sup|"
    r"continue|go ahead|do it|proceed|got it|cool|nice|great|done|next|lgtm|k)"
    r"""[\s!?.:;,"'~\u2018\u2019\u201c\u201d\u2014\u2013\u2026()\[\]{}<>*&^%$#@!+=`\u00a0]*$""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RecallStatus:
    """What the last prefetch injected. ``count == 0`` means content without a count."""

    provider_label: str
    count: int
    glyph: str = INDICATOR_GLYPH


def is_trivial_prompt(text: str | None) -> bool:
    """True for empty input, slash commands, and bare greetings or acknowledgements."""
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("/"):
        return True
    return bool(TRIVIAL_PROMPT_RE.match(stripped))


class MemoryProvider(ABC):
    """Pluggable recall. ``prefetch`` returns ``""`` unless a subclass overrides it."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, for example ``builtin``."""

    @abstractmethod
    def is_available(self) -> bool:
        """True when the provider can serve a turn."""

    @abstractmethod
    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Bind session state once at startup."""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Formatted recall for the upcoming turn. The default is no recall."""
        return ""

    @abstractmethod
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """OpenAI-style tool schemas this provider handles."""

    @abstractmethod
    def handle_tool_call(
        self, tool_name: str, args: dict[str, Any], **kwargs: Any
    ) -> str:
        """Handle one tool call. Must return a JSON string."""


class BuiltinMemoryProvider(MemoryProvider):
    """Recall and writes go through one ``MemoryStore``."""

    def __init__(
        self,
        store: MemoryStore,
        retain: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self._retain = retain
        self._session_id = ""

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self.store.load_from_disk()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if is_trivial_prompt(
            query if isinstance(query, str) or query is None else None
        ):
            return ""
        self.store.load_from_disk()
        return redact_text(self.store.render())

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "memory",
                "description": (
                    "Add, replace, or remove one entry in MEMORY.md or USER.md. "
                    "Entries are joined by a section delimiter."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "replace", "remove"],
                        },
                        "target": {"type": "string", "enum": ["memory", "user"]},
                        "content": {"type": "string"},
                        "old_text": {"type": "string"},
                    },
                    "required": ["action"],
                },
            }
        ]

    def handle_tool_call(
        self, tool_name: str, args: dict[str, Any], **kwargs: Any
    ) -> str:
        if tool_name != "memory":
            return json.dumps(
                {
                    "success": False,
                    "error": f"Provider builtin does not handle tool '{tool_name}'.",
                },
                ensure_ascii=False,
            )
        payload = args or {}
        action = payload.get("action")
        target = payload.get("target") or "memory"
        content = payload.get("content")
        if content is None:
            content = payload.get("new_text")
        old_text = payload.get("old_text")
        if action == "add":
            if target == "memory" and self._retain is not None:
                return json.dumps(
                    self._retain("" if content is None else content), ensure_ascii=False
                )
            result = self.store.add(target, "" if content is None else content)
        elif action == "replace":
            old_value = "" if old_text is None else old_text
            new_value = "" if content is None else content
            record_match = self._local_record_match(target, old_value)
            if isinstance(record_match, dict):
                result = record_match
            elif record_match is not None:
                stored, record = record_match
                if record is None:
                    result = self.store.replace(target, stored, new_value)
                elif not isinstance(new_value, str) or not new_value.strip():
                    result = {"success": False, "error": "Content cannot be empty."}
                elif "§" in new_value:
                    result = {
                        "success": False,
                        "error": "Entry contains the delimiter character §.",
                    }
                elif isinstance(record, LegacyRecord):
                    result = self.store.replace(
                        target,
                        stored,
                        f"[hindsight:{record.bank}] {new_value.strip()}",
                    )
                else:
                    updated_record = replace_dataclass(
                        record, content=new_value.strip()
                    )
                    result = self.store.replace(
                        target, stored, encode_local_record(updated_record)
                    )
            elif target == "memory":
                result = {
                    "success": False,
                    "error": (
                        f"No entry matched '{old_value.strip()}'."
                        if isinstance(old_value, str)
                        else "old_text must be a string."
                    ),
                }
            else:
                result = self.store.replace(target, old_value, new_value)
        elif action == "remove":
            old_value = "" if old_text is None else old_text
            record_match = self._local_record_match(target, old_value)
            if isinstance(record_match, dict):
                result = record_match
            elif record_match is not None:
                result = self.store.remove(target, record_match[0])
            elif target == "memory":
                result = {
                    "success": False,
                    "error": (
                        f"No entry matched '{old_value.strip()}'."
                        if isinstance(old_value, str)
                        else "old_text must be a string."
                    ),
                }
            else:
                result = self.store.remove(target, old_value)
        else:
            result = {
                "success": False,
                "error": f"Unknown action '{action}'. Use add, replace, or remove.",
            }
        return json.dumps(result, ensure_ascii=False)

    def _local_record_match(
        self, target: str, old_text: str
    ) -> tuple[str, LocalRecord | LegacyRecord | None] | dict[str, Any] | None:
        """Find one logical memory entry by its plain or decoded content."""
        if target != "memory" or not isinstance(old_text, str) or not old_text.strip():
            return None
        self.store.load_from_disk()
        needle = old_text.strip()
        candidates: list[tuple[str, str, LocalRecord | LegacyRecord | None]] = []
        for stored in self.store.memory_entries:
            bank_envelope = re.match(r"^\[hindsight:([^\[\]\r\n]+)\] ", stored)
            if bank_envelope is None:
                candidates.append((stored, stored, None))
                continue
            try:
                record = decode_local_record(stored, bank_envelope.group(1))
            except (TypeError, ValueError):
                # Never allow raw matching to reach inside an encoded record.
                continue
            if isinstance(record, (LocalRecord, LegacyRecord)):
                candidates.append((stored, record.content, record))
        exact = [item for item in candidates if item[1] == needle]
        matches = exact or [item for item in candidates if needle in item[1]]
        if len(matches) > 1:
            return {
                "success": False,
                "error": f"Multiple entries matched '{needle}'.",
            }
        if not matches:
            return None
        stored, _content, record = matches[0]
        return stored, record


__all__ = [
    "BuiltinMemoryProvider",
    "MemoryProvider",
    "RecallStatus",
    "is_trivial_prompt",
]

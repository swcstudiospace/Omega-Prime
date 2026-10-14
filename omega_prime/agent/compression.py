"""The only rewrite of messages already appended to a turn.

Adapted from the Hermes compression entry the turn calls (``compress_after_tool_results``
and the conversation compressor). Older turns may be replaced with one summary.
The original system message, and the bytes of its content, stay as they are.
No auxiliary model call.

Omp extra rules (``compaction/pruning.ts``) run here too: tool results their
tool flagged contextually useless (zero matches, elapsed waits) are blanked
with a notice before summarization, bypassing any recency protection. Results
flagged as errors are never elided, and an error is never marked useless.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

SUMMARY_KIND = "summary"
_SUMMARY_HEADING = '<context_summary version="1">'
_SUMMARY_CLOSE = "</context_summary>"

# Exact placeholder written over an elided useless tool result (Omp pruning).
USELESS_NOTICE = '<elided reason="useless-tool-result"/>'


def prune_useless_results(messages: list) -> int:
    """Blank useless non-error tool results with a notice. Return how many.

    Omp ``pruning.ts`` rule: results flagged contextually useless bypass the
    recency window — even the kept tail is blanked — because the model already
    consumed them. Error rows are never elided. Idempotent: an already
    blanked row is left alone. Callers inside the loop reach this through
    :func:`compress_messages` so append-only transcripts stay valid.
    """
    pruned = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "tool":
            continue
        if message.get("useless") is not True or message.get("is_error") is True:
            continue
        if message.get("content") == USELESS_NOTICE:
            continue
        message["content"] = USELESS_NOTICE
        pruned += 1
    return pruned


def compress_messages(messages: list) -> list:
    """Replace older turns with a summary. Leave the system prompt bytes unchanged.

    Short transcripts (nothing older than the kept tail) are returned as-is.
    The rewrite is in place so the caller's list object stays the transcript.
    Useless non-error tool results are blanked first, wherever they sit.
    """
    prune_useless_results(messages)
    if not isinstance(messages, list) or len(messages) < 4:
        return messages
    system = None
    start = 0
    if isinstance(messages[0], dict) and messages[0].get("role") == "system":
        system = messages[0]
        start = 1
    body = list(messages[start:])
    if len(body) <= 2:
        return messages
    older = body[:-2]
    tail = body[-2:]
    summary = {
        "role": "user",
        "content": _summary_text(older),
        "display_kind": SUMMARY_KIND,
    }
    rewritten: list[Any] = []
    if system is not None:
        rewritten.append(system)
    rewritten.append(summary)
    rewritten.extend(tail)
    messages[:] = rewritten
    return messages


def _summary_text(older: list) -> str:
    lines = [_SUMMARY_HEADING]
    for message in older:
        if not isinstance(message, dict):
            lines.append(escape(str(message)))
            continue
        role = message.get("role", "?")
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        calls = message.get("tool_calls") or []
        escaped_text = escape(text[:200])
        if calls:
            names = []
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                name = function.get("name") if isinstance(function, dict) else None
                if name:
                    names.append(escape(str(name)))
            suffix = "tool calls: " + ", ".join(names)
            escaped_text = (escaped_text + " " if escaped_text else "") + suffix
        lines.append(f"{role}: {escaped_text}")
    lines.append(_SUMMARY_CLOSE)
    return "\n".join(lines)


__all__ = [
    "SUMMARY_KIND",
    "USELESS_NOTICE",
    "compress_messages",
    "prune_useless_results",
]

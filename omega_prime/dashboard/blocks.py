# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Idempotent waiting_on_ove blocks: raise, clear, and the push-step hook.

Every block that needs Ove — a blocked push, gh not logged in, an
unanswered approval — becomes exactly one ``waiting_on_ove`` item keyed by
a stable id, through one locked read-modify-write of the dashboard
tasks.json. Repeats update rather than duplicate; resolution clears.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from omega_prime.credentials.redact import redact_text
from omega_prime.dashboard.status_file import _write_locked, locked_tasks

# Static per-reason texts for the push step: ask names the fix, blocks names
# the gate. Reason codes plus short text only — never the argv of a
# credentialed command (W111-03; redaction is the backstop, not permission).
PUSH_REASONS: dict[str, tuple[str, str]] = {
    "auto_review_blocked": (
        "Approve the blocked push (Auto-review) so the task can ship its commits.",
        "push gate: Auto-review is holding the push (auto_review_blocked)",
    ),
    "gh_not_logged_in": (
        "Run `gh auth login` on the host so pushes can go out.",
        "push gate: gh is not logged in on the host (gh_not_logged_in)",
    ),
    "approval_unanswered": (
        "Answer the pending approval card so the run can continue.",
        "push gate: an approval card is unanswered (approval_unanswered)",
    ),
}


def raise_block(
    path: Any,
    item_id: str,
    ask: str,
    blocks: str,
    task_id: str,
) -> None:
    """Upsert one waiting_on_ove item by stable id and block the owning task.

    The first raise allocates ``seq`` once (max existing seq, at least 0,
    plus 1) and pins ``since`` (UTC ISO-8601); any later call with the same
    ``item_id`` updates ``ask``/``blocks`` in place and leaves id, seq and
    since unchanged, so a retrying caller can neither duplicate the item nor
    reset the block's age. ``ask`` and ``blocks`` pass the secret redaction
    before storage. The tasks[] row whose id == ``task_id`` becomes
    ``blocked``; a missing row still writes the item — the item, not the
    row, is the source of truth for the block.
    """
    since = datetime.now(UTC).isoformat()
    with locked_tasks(path, write=True) as data:
        items = data.setdefault("waiting_on_ove", [])
        item = next((row for row in items if row.get("id") == item_id), None)
        if item is None:
            item = {
                "id": item_id,
                "seq": max([int(row.get("seq") or 0) for row in items] + [0]) + 1,
                "ask": "",
                "blocks": "",
                "since": since,
            }
            items.append(item)
        item["ask"] = redact_text(ask)
        item["blocks"] = redact_text(blocks)
        item["task_id"] = task_id
        for row in data.get("tasks", []):
            if row.get("id") == task_id:
                row["state"] = "blocked"
        _write_locked(data, path)


def _owned_by(item: dict[str, Any], task_id: str) -> bool:
    """Whether a waiting_on_ove item blocks ``task_id``.

    Items record their owner in ``task_id``; an item written before that
    field existed is owned through its stable ``"<task_id>:"`` id prefix.
    """
    owner = item.get("task_id")
    if owner is not None:
        return owner == task_id
    return str(item.get("id") or "").startswith(f"{task_id}:")


def clear_block(path: Any, item_id: str, task_id: str) -> None:
    """Remove the item; return the owning task to ``working`` once unblocked.

    The task stays ``blocked`` while any other item for it remains (a plan
    gate and a push gate can coexist), and flips back only when its last
    block is cleared. Idempotent: a missing item or row is a no-op that
    still persists cleanly. Only a currently ``blocked`` row flips —
    ``done``/``failed`` rows are terminal and never resurrected by a late
    clear.
    """
    with locked_tasks(path, write=True) as data:
        items = data.get("waiting_on_ove", [])
        kept = [row for row in items if row.get("id") != item_id]
        if len(kept) != len(items):
            data["waiting_on_ove"] = kept
        still_blocked = any(_owned_by(row, task_id) for row in kept)
        for row in data.get("tasks", []):
            if (
                row.get("id") == task_id
                and row.get("state") == "blocked"
                and not still_blocked
            ):
                row["state"] = "working"
        _write_locked(data, path)


def push_block(path: Any, task_id: str, reason: str, detail: str = "") -> None:
    """Raise the push-step block for ``task_id`` as item ``f"{task_id}:push"``.

    ``reason`` must be a PUSH_REASONS code (ValueError otherwise, and
    nothing is written); ``detail`` is optional short text — never argv —
    which is redacted and appended to ask as " — {detail}". The successful
    push clears via ``clear_block(path, f"{task_id}:push", task_id)``.
    """
    if reason not in PUSH_REASONS:
        raise ValueError(f"unknown push reason {reason!r}")
    ask, blocks = PUSH_REASONS[reason]
    if detail:
        ask = f"{ask} — {redact_text(detail)}"
    raise_block(path, f"{task_id}:push", ask=ask, blocks=blocks, task_id=task_id)


__all__ = ["PUSH_REASONS", "clear_block", "push_block", "raise_block"]

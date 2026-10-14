# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-sync-plan``: what the findings sync would send. Read-only."""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from omega_prime.commands.redteam_common import (
    Error,
    parse_flags,
    resolve_reports_dir,
)

NAME = "omega-redteam-sync-plan"
USAGE = "/omega-redteam-sync-plan"
SUMMARY = (
    "Plan the next findings sync without dispatching anything: what would sync "
    "and what is held."
)
WHEN = "The user asks what the findings sync would send to the tracker next."

DEFAULT_TRACKER = "github"

#: Longest list returned per held/would-sync category and for conflict rows.
MAX_ROWS = 20
_MAX_TEXT = 64
_MAX_LABEL = 64
_MAX_DETAIL = 200
_TRUNCATED_KIND = "conflicts-truncated"
_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]", re.ASCII)
_MORE_RE = re.compile(r"(\d{1,9}) more conflicts not listed", re.ASCII)


def _ascii(value: Any, limit: int = _MAX_TEXT) -> str:
    """``value`` as printable ASCII of at most ``limit`` characters.

    Anything outside 0x20-0x7e (ESC, newlines, bidi marks, non-ASCII) becomes a
    ``\\xNN``/``\\uNNNN`` escape; the output is cut at a whole escape.
    """
    out: list[str] = []
    size = 0
    for char in str(value):
        code = ord(char)
        if 0x20 <= code < 0x7F:
            piece = char
        elif code < 0x100:
            piece = f"\\x{code:02x}"
        elif code < 0x10000:
            piece = f"\\u{code:04x}"
        else:
            piece = f"\\U{code:08x}"
        if size + len(piece) > limit:
            break
        out.append(piece)
        size += len(piece)
    return "".join(out)


def _held_rows(value: Any) -> tuple[list[dict[str, Any]], int]:
    """First ``MAX_ROWS`` hold rows with every string value clipped and escaped."""
    rows = [row for row in _list(value) if isinstance(row, dict)]
    clipped: list[dict[str, Any]] = []
    for row in rows[:MAX_ROWS]:
        clipped.append(
            {
                _ascii(key): (
                    item if isinstance(item, (int, float, bool)) else _ascii(item)
                )
                for key, item in row.items()
            }
        )
    return clipped, len(rows)


def _looks_like_path(label: str) -> bool:
    return "/" in label or "\\" in label or _DRIVE_RE.match(label) is not None


def _label_aliases(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Label -> safe name: basenames for path labels, ``grant-N`` on a collision."""
    labels = sorted(
        {
            label
            for row in rows
            for label in _list(row.get("grants"))
            if isinstance(label, str)
        }
    )
    proposed: dict[str, str] = {}
    for label in labels:
        proposed[label] = (
            os.path.basename(label.replace("\\", "/"))
            if _looks_like_path(label)
            else label
        )
    counts: dict[str, int] = {}
    for name in proposed.values():
        counts[name] = counts.get(name, 0) + 1
    needs_alias = {
        label
        for label in labels
        if _looks_like_path(label)
        and (not proposed[label] or counts[proposed[label]] > 1)
    }
    taken = {proposed[label] for label in labels if label not in needs_alias}
    aliases: dict[str, str] = {}
    for index, label in enumerate(labels, start=1):
        if label not in needs_alias:
            aliases[label] = proposed[label]
            continue
        number = index
        while f"grant-{number}" in taken:
            number += 1
        name = f"grant-{number}"
        taken.add(name)
        aliases[label] = name
    return aliases


def _unlisted(sentinel: Mapping[str, Any]) -> int:
    """``N`` from the detector's "<N> more conflicts not listed" text, else 0."""
    found = _MORE_RE.match(str(sentinel.get("detail", "")))
    return int(found.group(1)) if found else 0


def _conflict_rows(value: Any) -> tuple[list[dict[str, Any]], int]:
    """Conflict rows, capped and path-free; a truncation sentinel passes through."""
    rows = [row for row in _list(value) if isinstance(row, dict)]
    sentinels = [row for row in rows if row.get("kind") == _TRUNCATED_KIND]
    real = [row for row in rows if row.get("kind") != _TRUNCATED_KIND]
    kept = real[:MAX_ROWS]
    aliases = _label_aliases(kept)
    ordered = sorted(aliases, key=lambda label: (-len(label), label))
    out: list[dict[str, Any]] = []
    for row in kept:
        detail = str(row.get("detail", ""))
        for label in ordered:
            if label and label != aliases[label]:
                detail = detail.replace(label, aliases[label])
        out.append(
            {
                "kind": _ascii(row.get("kind", "")),
                "grants": [
                    _ascii(aliases.get(label, label), _MAX_LABEL)
                    for label in _list(row.get("grants"))
                    if isinstance(label, str)
                ],
                "detail": _ascii(detail, _MAX_DETAIL),
            }
        )
    return out + [dict(row) for row in sentinels], len(real) + sum(
        _unlisted(row) for row in sentinels
    )


def _forbidden_runner(argv: Any, data: Any) -> Any:
    raise AssertionError("sync-plan must never reach the dispatch runner")


def _plan_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy of ``config`` with both findings-sync ledgers forced off."""
    plan = copy.deepcopy(dict(config))
    redteam = plan.get("redteam")
    node = redteam.get("findings_sync") if isinstance(redteam, dict) else None
    if isinstance(node, dict):
        node["dispatch_ledger"] = ""
        node["grant_usage_ledger"] = ""
    return plan


def _tracker(config: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    """Effective tracker: the plan rows' tracker, else the configured one."""
    skipped = result.get("skipped_tracker")
    if isinstance(skipped, str) and skipped:
        return skipped
    plan = result.get("dispatch_plan")
    if isinstance(plan, list):
        for row in plan:
            if isinstance(row, dict) and isinstance(row.get("tracker"), str):
                return str(row["tracker"])
    redteam = config.get("redteam")
    node = redteam.get("findings_sync") if isinstance(redteam, dict) else None
    configured = node.get("tracker") if isinstance(node, dict) else None
    if isinstance(configured, str) and configured.strip():
        return configured.strip().lower()
    return DEFAULT_TRACKER


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """The would-sync plan from a dry tick with every ledger off. Writes nothing."""
    parse_flags(args, USAGE)
    reports = resolve_reports_dir(env, USAGE)
    try:
        from omega_prime.config import load_config
        from omega_prime.grokbot import findings_sync

        config = load_config()
        result = findings_sync.tick(
            _plan_config(config),
            reports_directory=reports,
            runner=_forbidden_runner,
            dispatch_dry_run=True,
            now=datetime.now(UTC).replace(microsecond=0),
        )
    except Exception as exc:
        raise Error(
            "plan_failed", {"usage": USAGE, "reason": type(exc).__name__}
        ) from None
    if result.get("error"):
        raise Error(
            "plan_failed",
            {
                "usage": USAGE,
                "reason": "tick-error",
                "detail": _ascii(result["error"], _MAX_DETAIL),
            },
        )
    pushed = [row for row in _list(result.get("pushed")) if isinstance(row, dict)]
    quiet, quiet_total = _held_rows(result.get("quiet_skipped"))
    budget, budget_total = _held_rows(result.get("budget_held"))
    conflicts, conflicts_total = _conflict_rows(result.get("grant_conflicts"))
    return {
        "tracker": _ascii(_tracker(config, result)),
        "disabled": bool(result.get("disabled")),
        "would_sync": [_ascii(row.get("dedupe_hash", "")) for row in pushed[:MAX_ROWS]],
        "would_sync_total": len(pushed),
        "skipped": len(_list(result.get("skipped"))),
        "quiet_held": quiet,
        "quiet_held_total": quiet_total,
        "budget_held": budget,
        "budget_held_total": budget_total,
        "grant_conflicts": conflicts,
        "grant_conflicts_total": conflicts_total,
    }


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]

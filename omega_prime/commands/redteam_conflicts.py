# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-conflicts``: RoE grant contradictions. Read-only."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.commands.redteam_common import Error

NAME = "omega-redteam-conflicts"
USAGE = "/omega-redteam-conflicts"
SUMMARY = "Show Rules-of-Engagement grant conflicts, basenames only."
WHEN = "The user asks whether concurrent red-team grants contradict each other."

ROE_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILE"
ROE_FILES_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILES"

#: Longest conflict list returned to chat.
MAX_ROWS = 20
_TRUNCATED_KIND = "conflicts-truncated"
_MORE_RE = re.compile(r"(\d{1,9}) more conflicts not listed", re.ASCII)


def _grant_paths(env: Mapping[str, str]) -> list[str]:
    """Effective grant paths. A non-empty list replaces the single file.

    Same replacement rule as ``redteam_roe_files``: when
    ``OMEGA_PRIME_REDTEAM_ROE_FILES`` names anything, the legacy single
    file is not an active grant and must not produce conflicts.
    """
    listed: list[str] = []
    for entry in (env.get(ROE_FILES_ENV_VAR) or "").split(","):
        text = entry.strip()
        if text and text not in listed:
            listed.append(text)
    if listed:
        return listed
    single = (env.get(ROE_FILE_ENV_VAR) or "").strip()
    return [single] if single else []


def _basename(name: Any) -> str:
    """Last path component only. A trailing separator yields nothing, not a directory."""
    text = str(name).strip()
    if not text:
        return ""
    return Path(text).name


def _unlisted(row: Mapping[str, Any]) -> int:
    found = _MORE_RE.search(str(row.get("detail", "")))
    return int(found.group(1)) if found else 0


def _row(row: Mapping[str, Any]) -> dict[str, Any]:
    grants = row.get("grants")
    names: list[str] = []
    if isinstance(grants, (list, tuple)):
        for name in grants:
            base = _basename(name)
            if base and base not in names:
                names.append(base)
    elif grants is not None:
        base = _basename(grants)
        if base:
            names.append(base)
    return {"kind": str(row.get("kind", "")), "label": ", ".join(names)}


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Grant conflicts from the host RoE env. Writes nothing."""
    if not isinstance(args, str) or args.strip():
        raise Error("bad_args", {"usage": USAGE})
    paths = _grant_paths(env)
    if not paths:
        return {"conflicts": [], "total": 0, "truncated": 0}
    try:
        from omega_prime.grokbot import roe_lifecycle

        found = roe_lifecycle.grant_conflicts(paths)
    except Exception:
        raise Error("detector_failed", {"usage": USAGE}) from None
    if not isinstance(found, list):
        raise Error("detector_failed", {"usage": USAGE})
    real: list[dict[str, Any]] = []
    truncated = 0
    for row in found:
        if not isinstance(row, dict):
            continue
        if row.get("kind") == _TRUNCATED_KIND:
            truncated += _unlisted(row)
            continue
        real.append(_row(row))
    extra = len(real) - MAX_ROWS
    if extra > 0:
        truncated += extra
        real = real[:MAX_ROWS]
    return {"conflicts": real, "total": len(real) + truncated, "truncated": truncated}


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]

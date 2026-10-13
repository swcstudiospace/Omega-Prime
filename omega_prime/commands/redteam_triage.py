# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-triage``: append one triage PROPOSAL row to the review queue."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NAME = "omega-redteam-triage"
USAGE = "/omega-redteam-triage <dedupe-hash> --rationale <text>"
SUMMARY = "Propose triage for one red-team finding."
WHEN = "The user asks to triage a red-team finding with a rationale."

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
REVIEW_QUEUE_FILENAME = "review-queue.ndjson"
RATIONALE_FLAG = "--rationale"
MAX_RATIONALE = 500
PROPOSER = "operator"

_HASH_RE = re.compile(r"[0-9a-f]{64}")


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def _queue_path(env: Mapping[str, str]) -> Path:
    """Review-queue file: env override wins, else the sidecar reports dir."""
    override = (env.get(REPORTS_ENV_VAR) or "").strip()
    if not override:
        override = (os.environ.get(REPORTS_ENV_VAR) or "").strip()
    if override:
        return Path(override) / REVIEW_QUEUE_FILENAME
    try:
        from sidecar.state import reports_dir
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    return reports_dir() / REVIEW_QUEUE_FILENAME


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Validate the hash and rationale, append one PROPOSAL row, nothing else."""
    flag_at = args.find(RATIONALE_FLAG)
    if flag_at < 0:
        raise Error("missing_rationale", {"usage": USAGE})
    dedupe_hash = args[:flag_at].strip()
    rationale = args[flag_at + len(RATIONALE_FLAG) :].strip()
    if _HASH_RE.fullmatch(dedupe_hash) is None:
        raise Error("bad_hash", {"usage": USAGE, "dedupe_hash": dedupe_hash[:64]})
    if not rationale or len(rationale) > MAX_RATIONALE:
        raise Error(
            "bad_rationale",
            {"usage": USAGE, "rationale_chars": len(rationale)},
        )
    try:
        from sidecar.state import utc_now_rfc3339
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    row = {
        "dedupe_hash": dedupe_hash,
        "rationale": rationale,
        "ts": utc_now_rfc3339(),
        "proposer": PROPOSER,
    }
    path = _queue_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return {"proposal": row}


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]

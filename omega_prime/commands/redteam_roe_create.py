# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-roe-create``: build an unsigned RoE artifact. Never signs."""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

NAME = "omega-redteam-roe-create"
USAGE = "/omega-redteam-roe-create --operator <name> --days <N> --key-file <path>"
SUMMARY = "Build an unsigned red-team Rules-of-Engagement artifact."
WHEN = "The user asks to create or renew a red-team Rules-of-Engagement artifact."

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
ARTIFACT_FILENAME = "roe.json"

_KNOWN_FLAGS = ("--operator", "--days", "--key-file")


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def _reports_dir(env: Mapping[str, str]) -> Path:
    """Reports dir: caller-env override wins, else the sidecar default."""
    override = (env.get(REPORTS_ENV_VAR) or "").strip()
    if not override:
        override = (os.environ.get(REPORTS_ENV_VAR) or "").strip()
    if override:
        return Path(override)
    try:
        from sidecar.state import reports_dir
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": USAGE, "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    return reports_dir()


def _flag_name(token: str) -> str:
    return token[2:].replace("-", "_")


def _parse_flags(args: str) -> dict[str, str]:
    """Scan ``--operator/--days/--key-file``; missing flags name themselves."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        raise Error("bad_args", {"usage": USAGE}) from None
    values: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token not in _KNOWN_FLAGS or token in values:
            raise Error("bad_args", {"usage": USAGE})
        if index + 1 >= len(tokens) or not tokens[index + 1].strip():
            raise Error(f"missing_{_flag_name(token)}", {"usage": USAGE})
        values[token] = tokens[index + 1].strip()
        index += 2
    for flag in _KNOWN_FLAGS:
        if flag not in values:
            raise Error(f"missing_{_flag_name(flag)}", {"usage": USAGE})
    return values


def _safe_stem(operator: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", operator.strip()).strip("-")
    return cleaned or "operator"


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Build the unsigned artifact JSON in the reports dir. No signing."""
    from omega_prime.redteam.roe import (
        CASE_CLASSES,
        SCHEMA_VERSION,
        validate_schema,
        validate_targets,
    )

    values = _parse_flags(args)
    operator = values["--operator"]
    if not operator:
        raise Error("missing_operator", {"usage": USAGE})
    try:
        days = int(values["--days"], 10)
    except ValueError:
        raise Error("invalid_days", {"usage": USAGE}) from None
    if days <= 0:
        raise Error("invalid_days", {"usage": USAGE})
    # --key-file is accepted for symmetric UX but never read here; signing
    # happens in omega-redteam-roe-sign.
    now = datetime.now(UTC)
    document = {
        "schema_version": SCHEMA_VERSION,
        "operator": operator,
        "authorized_targets": [{"host": "127.0.0.1"}],
        "effective_from": now.isoformat(timespec="seconds"),
        "effective_until": (now + timedelta(days=days)).isoformat(timespec="seconds"),
        "case_classes": list(CASE_CLASSES),
    }
    schema_errors = validate_schema(document)
    target_errors = validate_targets(document)
    if schema_errors or target_errors:
        raise Error(
            "artifact_invalid",
            {"usage": USAGE, "reasons": schema_errors + target_errors},
        )
    directory = _reports_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%S")
    path = directory / f"roe-{_safe_stem(operator)}-{stamp}.json"
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "artifact": str(path),
        "operator": operator,
        "effective_until": document["effective_until"],
    }


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]

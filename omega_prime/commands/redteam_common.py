# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Shared plumbing for the chat-native red-team reporting commands.

Strict flag parsing, the reports-dir resolver and the loader for the
hyphenated ``scripts/engagement-report.py``. No command takes a path-like
argument: the reports directory comes only from the host environment.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import sys
from collections.abc import Collection, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
STATE_DIR_ENV_VAR = "OMEGA_PRIME_STATE_DIR"

#: Longest accepted argument string; anything longer is a ``bad_args``.
MAX_ARGS_LENGTH = 512

_SINCE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"(?:[Tt]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:[Zz]|[+-]\d{2}:\d{2})?)?",
    re.ASCII,
)
_THRESHOLD_RE = re.compile(r"(gate|gate-risk):(\d{1,3}(?:\.\d{1,2})?)", re.ASCII)
_MAX_THRESHOLD = 100.0

_ENGAGEMENT_MODULE: Any = None


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def parse_flags(
    args: str,
    usage: str,
    *,
    switches: Collection[str] = (),
    options: Collection[str] = (),
) -> dict[str, Any]:
    """Strict allowlist parse: ``switches`` are booleans, ``options`` take a value.

    Unknown or duplicate flags, positional words, an option without a value
    and unbalanced quoting are all ``bad_args``. Returns the flags that were
    given (switch -> ``True``, option -> its raw string).
    """
    bad = Error("bad_args", {"usage": usage})
    if len(args) > MAX_ARGS_LENGTH or any(
        ord(ch) < 32 and not ch.isspace() for ch in args
    ):
        raise bad
    try:
        tokens = shlex.split(args)
    except ValueError:
        raise bad from None
    found: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in found:
            raise bad
        if token in switches:
            found[token] = True
        elif token in options:
            if index >= len(tokens) or tokens[index].startswith("--"):
                raise bad
            found[token] = tokens[index]
            index += 1
        else:
            raise bad
    return found


def validate_since(value: str, usage: str) -> str:
    """The ``--since`` text when it is an ISO-8601 instant, else ``bad_since``."""
    text = value.strip()
    if _SINCE_RE.fullmatch(text) is not None:
        parsable = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            datetime.fromisoformat(parsable)
        except ValueError:
            raise Error("bad_since", {"usage": usage}) from None
        return text
    raise Error("bad_since", {"usage": usage})


def validate_threshold(value: str, usage: str) -> str:
    """The ``--threshold`` spec when well formed and 0-100, else ``bad_threshold``."""
    match = _THRESHOLD_RE.fullmatch(value)
    if match is None or float(match.group(2)) > _MAX_THRESHOLD:
        raise Error("bad_threshold", {"usage": usage})
    return value


def resolve_reports_dir(env: Mapping[str, str], usage: str) -> Path:
    """Reports dir: env mapping first, then process env, then the sidecar default."""
    override = (env.get(REPORTS_ENV_VAR) or "").strip()
    if not override:
        override = (os.environ.get(REPORTS_ENV_VAR) or "").strip()
    if override:
        return Path(override)
    try:
        from sidecar import state
    except ImportError as exc:
        raise Error(
            "sidecar_unavailable",
            {"usage": usage, "reason": type(exc).__name__},
        ) from exc
    return Path(state.reports_dir())


def load_engagement_module(usage: str) -> Any:
    """Load ``scripts/engagement-report.py`` by path (hyphenated name, cached)."""
    global _ENGAGEMENT_MODULE
    if _ENGAGEMENT_MODULE is None:
        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "engagement-report.py"
        )
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location(
                "omega_prime_commands_engagement_report", script
            )
            if spec is None or spec.loader is None:
                raise ImportError("engagement-report script cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            raise Error(
                "report_unavailable",
                {"usage": usage, "reason": type(exc).__name__},
            ) from exc
        finally:
            sys.dont_write_bytecode = previous
        _ENGAGEMENT_MODULE = module
    return _ENGAGEMENT_MODULE


__all__ = [
    "MAX_ARGS_LENGTH",
    "REPORTS_ENV_VAR",
    "STATE_DIR_ENV_VAR",
    "Error",
    "load_engagement_module",
    "parse_flags",
    "resolve_reports_dir",
    "validate_since",
    "validate_threshold",
]

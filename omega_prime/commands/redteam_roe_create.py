# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-roe-create``: build a RoE artifact, signing it when asked."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
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

#: Valueless convenience flag: print the exact env-var export command after signing.
_OPTIONAL_FLAGS = ("--print-install",)


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


def _parse_flags(args: str) -> tuple[dict[str, str], bool]:
    """Scan ``--operator/--days/--key-file`` plus the ``--print-install`` flag.

    Required flags may be absent (the caller decides between the guided
    TTY flow and the flag-required error); unknown, duplicated, or
    value-less required flags still fail as ``bad_args``/``missing_*``.
    Returns ``(values, print_install)``.
    """
    try:
        tokens = shlex.split(args)
    except ValueError:
        raise Error("bad_args", {"usage": USAGE}) from None
    values: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _OPTIONAL_FLAGS:
            if token in values:
                raise Error("bad_args", {"usage": USAGE})
            values[token] = "1"
            index += 1
            continue
        if token not in _KNOWN_FLAGS or token in values:
            raise Error("bad_args", {"usage": USAGE})
        if index + 1 >= len(tokens) or not tokens[index + 1].strip():
            raise Error(f"missing_{_flag_name(token)}", {"usage": USAGE})
        values[token] = tokens[index + 1].strip()
        index += 2
    return values, "--print-install" in values


def _stdin_is_tty() -> bool:
    """Whether stdin is an interactive terminal (seam for tests)."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _prompt_operator() -> str:
    """Interactively read a non-empty operator name."""
    while True:
        answer = input("Operator name: ").strip()
        if answer:
            return answer
        print("Operator name must not be empty.")


def _prompt_days() -> int:
    """Interactively read a positive effective-days integer."""
    while True:
        answer = input("Effective days: ").strip()
        try:
            days = int(answer, 10)
        except ValueError:
            days = 0
        if days > 0:
            return days
        print("Effective days must be a positive integer.")


def _prompt_case_classes(known: list[str]) -> list[str]:
    """Interactively read a case-class subset; empty keeps them all."""
    print(f"Known case classes: {', '.join(known)}")
    while True:
        raw = input("Case classes (comma-separated, empty for all): ").strip()
        if not raw:
            return list(known)
        chosen = [entry.strip() for entry in raw.split(",")]
        chosen = [entry for entry in chosen if entry]
        if not chosen:
            return list(known)
        unknown = [entry for entry in chosen if entry not in known]
        if unknown:
            print(
                "Unknown case classes: "
                + ", ".join(sorted(set(unknown)))
                + f". Choose from: {', '.join(known)}"
            )
            continue
        picked = set(chosen)
        return [entry for entry in known if entry in picked]


def _prompt_key_file() -> str:
    """Interactively read a key-file path; empty skips signing."""
    return input("Key file path (empty to skip signing): ").strip()


def _safe_stem(operator: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", operator.strip()).strip("-")
    return cleaned or "operator"


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Build the artifact JSON in the reports dir; sign it when a key resolves.

    Flag mode without ``--print-install`` is unchanged: all three required
    flags must be present and the artifact stays unsigned. When required
    flags are missing and stdin is a TTY, the missing operator name,
    effective days, and case classes are prompted for interactively
    (non-TTY callers keep the flag-required ``missing_*`` error). A
    provided key file then auto-chains into ``redteam_roe_sign`` logic;
    ``--print-install`` additionally prints the export command after
    signing. Key material never enters the result or stdout.
    """
    from omega_prime.redteam.roe import (
        CASE_CLASSES,
        SCHEMA_VERSION,
        validate_schema,
        validate_targets,
    )

    values, print_install = _parse_flags(args)
    missing = [flag for flag in _KNOWN_FLAGS if flag not in values]
    guided = False
    case_classes = list(CASE_CLASSES)
    if missing:
        if not _stdin_is_tty():
            raise Error(f"missing_{_flag_name(missing[0])}", {"usage": USAGE})
        guided = True
        if "--operator" in missing:
            values["--operator"] = _prompt_operator()
        if "--days" in missing:
            values["--days"] = str(_prompt_days())
        case_classes = _prompt_case_classes(list(CASE_CLASSES))
        if "--key-file" in missing:
            key_answer = _prompt_key_file()
            if key_answer:
                values["--key-file"] = key_answer
    operator = values.get("--operator", "")
    if not operator:
        raise Error("missing_operator", {"usage": USAGE})
    try:
        days = int(values.get("--days", ""), 10)
    except ValueError:
        raise Error("invalid_days", {"usage": USAGE}) from None
    if days <= 0:
        raise Error("invalid_days", {"usage": USAGE})
    now = datetime.now(UTC)
    document = {
        "schema_version": SCHEMA_VERSION,
        "operator": operator,
        "authorized_targets": [{"host": "127.0.0.1"}],
        "effective_from": now.isoformat(timespec="seconds"),
        "effective_until": (now + timedelta(days=days)).isoformat(timespec="seconds"),
        "case_classes": case_classes,
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
    result: dict[str, Any] = {
        "artifact": str(path),
        "operator": operator,
        "effective_until": document["effective_until"],
    }
    if guided:
        result["case_classes"] = case_classes
    key_file = (values.get("--key-file") or "").strip()
    if key_file and (guided or print_install):
        from omega_prime.commands.redteam_roe_sign import Error as _SignError
        from omega_prime.commands.redteam_roe_sign import run as _sign_run

        try:
            signed = _sign_run(
                f"--artifact {shlex.quote(str(path))} "
                f"--key-file {shlex.quote(key_file)}",
                env,
            )
        except _SignError as exc:
            raise Error(exc.error, exc.result) from None
        result["signature"] = signed["signature"]
        result["install"] = signed["install"]
        if print_install:
            print(signed["install"])
    elif print_install:
        raise Error("missing_key_file", {"usage": USAGE})
    return result


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]

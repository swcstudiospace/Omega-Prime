# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-roe-sign``: HMAC-sign an RoE artifact. Prints install, installs nothing."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NAME = "omega-redteam-roe-sign"
USAGE = "/omega-redteam-roe-sign --artifact <path> --key-file <path>"
SUMMARY = "Sign a red-team Rules-of-Engagement artifact with a key file."
WHEN = "The user asks to sign a red-team Rules-of-Engagement artifact."

ROE_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_FILE"
ROE_KEY_FILE_ENV_VAR = "OMEGA_PRIME_REDTEAM_ROE_KEY_FILE"

_KNOWN_FLAGS = ("--artifact", "--key-file")


class Error(Exception):
    """Structured command failure: ``error`` names it, ``result`` carries it."""

    def __init__(self, error: str, result: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.result = result


def _flag_name(token: str) -> str:
    return token[2:].replace("-", "_")


def _parse_flags(args: str) -> dict[str, str]:
    """Scan ``--artifact/--key-file``; missing flags name themselves."""
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


def _read_key(key_file: str) -> bytes:
    """Key bytes from the operator's key file. Never echoed by callers."""
    try:
        text = Path(key_file).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        raise Error("key_unreadable", {"usage": USAGE}) from None
    if not text:
        raise Error("key_unreadable", {"usage": USAGE})
    return text.encode("utf-8")


def run(args: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Sign the artifact; report the install command without running it."""
    from omega_prime.redteam.roe import sign_artifact

    del env
    values = _parse_flags(args)
    artifact = values["--artifact"]
    key = _read_key(values["--key-file"])
    try:
        sig_path = sign_artifact(artifact, key)
    except OSError as exc:
        raise Error(
            "artifact_unreadable",
            {"usage": USAGE, "artifact": artifact, "reason": f"{type(exc).__name__}"},
        ) from exc
    except ValueError as exc:
        raise Error(
            "artifact_invalid",
            {"usage": USAGE, "artifact": artifact, "reason": f"{type(exc).__name__}"},
        ) from exc
    install = (
        f"export {ROE_FILE_ENV_VAR}={artifact}\n"
        f"# place the signing key where the harness reads it:\n"
        f"# export {ROE_KEY_FILE_ENV_VAR}={values['--key-file']}"
    )
    return {"artifact": artifact, "signature": str(sig_path), "install": install}


__all__ = ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]

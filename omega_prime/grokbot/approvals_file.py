# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Human grants for gated tools on the stdio route.

SSE has ``/admin/approvals``. stdio does not, and the launcher must not put
``--approve`` on the command the model can see and edit. A person writes a
mode 0600 file outside the repository. The process reads it once at startup.

This module never writes that file. An approver equal to the seat, or to a
name the model could claim for itself, is refused. The grant lasts until the
process exits; editing the file does nothing until a person restarts the host.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

BOT_ID = "bot-00-omega-prime"
_SELF_APPROVERS = frozenset(
    {
        BOT_ID,
        "omega-prime",
        "omega_prime",
        "assistant",
        "model",
        "self",
        "bot",
        "system",
    }
)
_TOOL_RE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
_APPROVER_RE_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def resolve_approvals_path(
    explicit: Path | None = None, env: Mapping[str, str] | None = None
) -> Path | None:
    """The file to load, or None when the operator has not provided one.

    ``explicit`` and ``OMEGA_PRIME_APPROVALS_FILE`` are required to exist
    (the caller fails closed). The state-dir default is returned only when
    that file is already there. This function does not create it.
    """
    source = os.environ if env is None else env
    if explicit is not None:
        return Path(explicit).expanduser()
    named = source.get("OMEGA_PRIME_APPROVALS_FILE", "")
    if isinstance(named, str) and named.strip():
        return Path(named.strip()).expanduser()
    state = source.get("OMEGA_PRIME_STATE_DIR", "")
    if not isinstance(state, str) or not state.strip():
        return None
    candidate = Path(state.strip()).expanduser() / "approvals"
    if candidate.is_file():
        return candidate
    return None


def load_approvals_file(
    path: Path | str, *, repo_root: Path | str
) -> list[tuple[str, str]]:
    """Read ``TOOL:APPROVER`` lines. Raise ``ValueError`` when the file is unsafe.

    The path, before and after resolving symlinks, must sit outside
    ``repo_root``. The file must be a regular file the owner can read and
    nobody else can read or write.
    """
    repo = Path(repo_root).resolve()
    raw = Path(path).expanduser()
    lexical = Path(os.path.abspath(raw))
    if _inside(lexical, repo):
        raise ValueError("approvals file must live outside the repository")
    try:
        info = raw.stat()
    except OSError as exc:
        raise ValueError(f"cannot read approvals file: {exc.strerror or exc}") from exc
    resolved = raw.resolve()
    if _inside(resolved, repo):
        raise ValueError("approvals file must live outside the repository")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("approvals file is not a regular file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError(
            "approvals file is accessible by group/other; run: chmod 600 <path>"
        )
    if not info.st_mode & stat.S_IRUSR:
        raise ValueError("approvals file is not readable by its owner")
    try:
        text = raw.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read approvals file: {exc.strerror or exc}") from exc
    grants: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tool, separator, approver = stripped.partition(":")
        tool = tool.strip()
        approver = approver.strip()
        if not separator or not _tool_name(tool) or not _approver_name(approver):
            raise ValueError(
                f"approvals file line {line_no} must be TOOL:APPROVER "
                "with a human approver"
            )
        if approver.casefold() in _SELF_APPROVERS or approver.casefold() == tool:
            raise ValueError(
                f"approvals file line {line_no} approver must be a person, not the bot"
            )
        if tool in seen:
            grants = [(name, who) for name, who in grants if name != tool]
        seen.add(tool)
        grants.append((tool, approver))
    return grants


def _tool_name(value: str) -> bool:
    return (
        1 <= len(value) <= 80
        and value[0].isalpha()
        and all(char in _TOOL_RE_CHARS for char in value)
    )


def _approver_name(value: str) -> bool:
    return (
        1 <= len(value) <= 64
        and value[0].isalpha()
        and all(char in _APPROVER_RE_CHARS for char in value)
    )


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = ["BOT_ID", "load_approvals_file", "resolve_approvals_path"]

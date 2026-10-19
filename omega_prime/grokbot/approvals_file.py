# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Human grants for gated tools on the stdio route.

SSE has ``/admin/approvals``. stdio does not, and the launcher must not put
``--approve`` on the command the model can see and edit. A person writes a
mode 0600 file outside the repository and outside ``OMEGA_PRIME_STATE_DIR``,
then pins its sha256 on the command that starts the server.

The state directory is not a grant source. The server can write there
(secret files, and ``execute_code`` runs as the host user), so a file that
appears at ``$OMEGA_PRIME_STATE_DIR/approvals`` is ignored. A matching digest
is required because a mode-0600 file and a human-looking name do not prove
who wrote it: the bot can create both. The bot can overwrite the file, but
it cannot change the digest on the next launch, and a mismatch refuses to
start rather than granting.

This module never writes that file. An approver equal to the seat, or to a
name the model could claim for itself, is refused. The grant lasts until the
process exits; editing the file does nothing until a person updates the
digest and restarts the host.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
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
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_TOOL_RE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
_APPROVER_RE_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def resolve_approvals_path(
    explicit: Path | None = None, env: Mapping[str, str] | None = None
) -> Path | None:
    """The file a person named, or None when they have not named one.

    Only ``explicit`` and ``OMEGA_PRIME_APPROVALS_FILE`` count. A file at
    ``$OMEGA_PRIME_STATE_DIR/approvals`` is not loaded: the server can write
    that directory, so finding a file there is not a person's approval.
    This function does not create a file.
    """
    source = os.environ if env is None else env
    if explicit is not None:
        return Path(explicit).expanduser()
    named = source.get("OMEGA_PRIME_APPROVALS_FILE", "")
    if isinstance(named, str) and named.strip():
        return Path(named.strip()).expanduser()
    return None


def resolve_approvals_digest(
    explicit: str | None = None, env: Mapping[str, str] | None = None
) -> str:
    """Hex sha256 a person pinned, or ``""`` when they have not pinned one.

    ``explicit`` wins over ``OMEGA_PRIME_APPROVALS_SHA256``. The value is
    not checked here; ``load_approvals_file`` compares it to the file bytes.
    """
    source = os.environ if env is None else env
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip().lower()
    named = source.get("OMEGA_PRIME_APPROVALS_SHA256", "")
    if isinstance(named, str) and named.strip():
        return named.strip().lower()
    return ""


def load_approvals_file(
    path: Path | str,
    *,
    repo_root: Path | str,
    digest: str,
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Read ``TOOL:APPROVER`` lines. Raise ``ValueError`` when the file is unsafe.

    The path, before and after resolving symlinks, must sit outside
    ``repo_root`` and outside ``OMEGA_PRIME_STATE_DIR``. The file must be a
    regular file the owner can read and nobody else can read or write, and
    ``digest`` must be the sha256 of its bytes.
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
    source = os.environ if env is None else env
    state = _state_dir(source)
    if state is not None and (
        _inside(lexical, Path(os.path.abspath(state)))
        or _inside(resolved, state.resolve())
    ):
        raise ValueError(
            "approvals file must live outside OMEGA_PRIME_STATE_DIR; "
            "the server can write that directory"
        )
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("approvals file is not a regular file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError(
            "approvals file is accessible by group/other; run: chmod 600 <path>"
        )
    if not info.st_mode & stat.S_IRUSR:
        raise ValueError("approvals file is not readable by its owner")
    try:
        blob = raw.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read approvals file: {exc.strerror or exc}") from exc
    expected = digest.strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise ValueError(
            "approvals file needs a sha256 of its bytes "
            "(--approvals-sha256 or OMEGA_PRIME_APPROVALS_SHA256)"
        )
    actual = hashlib.sha256(blob).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError(
            "approvals file does not match the supplied sha256; "
            "a person must update the digest after editing it"
        )
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("approvals file is not UTF-8") from exc
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


def _state_dir(env: Mapping[str, str]) -> Path | None:
    raw = env.get("OMEGA_PRIME_STATE_DIR", "")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw.strip()).expanduser()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "BOT_ID",
    "load_approvals_file",
    "resolve_approvals_digest",
    "resolve_approvals_path",
]

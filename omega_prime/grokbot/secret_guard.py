# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Enforced secret-redaction gate for Grok Bot diagnostic bundles.

This module is the fail-closed checkpoint scanned content must pass before it
is written into a shareable artifact (diag bundle, pasted report, manifest
snapshot). Detection reuses :func:`omega_prime.grokbot.audit.redact_sensitive`
so the token/auth-header patterns live in exactly one place; only the gaps
audit scrubbing does not cover (PEM private-key blocks and bare high-entropy
token-shaped strings) are matched here.

Public API (consumed by Batch B ``diag_bundle``):

- :class:`SecretLeakError` -- raised on any trip. Its message carries only the
  caller-supplied ``name`` label and the match kind (``token-pattern`` or
  ``0600-violation``); it never echoes the secret value.
- :func:`assert_no_secrets` -- scan literal text (``str``) or a file
  (``os.PathLike``). File inputs additionally enforce the 0600 file mode.
- :func:`enforce_token_file_mode` -- raise unless ``path`` is a regular file
  whose permission bits are exactly ``0o600``.
"""

from __future__ import annotations

import math
import os
import re
import stat
from pathlib import Path

from omega_prime.grokbot.audit import redact_sensitive
from omega_prime.tooling.fs import PRIVATE_FILE_MODE

__all__ = [
    "SecretLeakError",
    "assert_no_secrets",
    "enforce_token_file_mode",
]

#: Match kinds surfaced in :class:`SecretLeakError` messages. Nothing else --
#: in particular no excerpt of the offending value -- may appear there.
TOKEN_PATTERN_KIND = "token-pattern"
MODE_VIOLATION_KIND = "0600-violation"

#: PEM private-key armour, e.g. ``-----BEGIN RSA PRIVATE KEY-----``.
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9 ]*)?PRIVATE KEY-----")
#: IIbRo_NeX bearer tokens (``omk_…``); hyphen accepted too so a
#: mistyped separator still trips the fail-closed gate. Suffix length is
#: deliberately short (>=8) -- entropy/total-length checks do not apply.
_OMK_RE = re.compile(r"omk[-_][A-Za-z0-9_\-]{8,}")

#: Runs that could be a bare token; survivors are filtered by entropy/shape.
_CANDIDATE_RE = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")

#: Hex digests / UUIDs are integrity metadata, not secrets.
_HEX_RE = re.compile(r"\A[0-9a-fA-F]+\Z")
_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
#: Hierarchical Git refs. ``/`` is in the token alphabet, so ``refs/heads/``
#: plus an ordinary branch name is one 32+ character candidate even when no
#: component is a token. ``refs/pull/38/merge`` and ``refs/heads/main`` stay
#: under that length; a long branch name does not.
_GIT_REF_RE = re.compile(
    r"\Arefs/(?:heads|tags)(?:/[A-Za-z0-9._-]+)+\Z"
    r"|\Arefs/pull/[0-9]+/(?:merge|head)\Z"
)

_MIN_CANDIDATE_LEN = 32
_MIN_ENTROPY_BITS = 4.2


class SecretLeakError(ValueError):
    """A secret (or a wrongly-permissioned secret file) tripped the gate.

    The message carries only the caller-supplied ``name`` label and the match
    kind (``token-pattern`` or ``0600-violation``); it never contains the
    offending secret value, so propagating it to logs is safe.
    """


def enforce_token_file_mode(path: str | os.PathLike[str]) -> Path:
    """Require ``path`` to be a regular file with mode exactly ``0o600``.

    This is the strict form of :func:`omega_prime.tooling.fs.read_secret_file`
    semantics: any file accepted here (regular, no group/other bits) is also
    accepted there, while lax-but-group-closed modes such as ``0o400`` are
    rejected so checked-in fixtures cannot drift from the deploy convention.

    Raises :class:`SecretLeakError` with kind ``0600-violation``; the message
    names only the path, never file contents.
    """
    target = Path(path)
    try:
        info = target.stat()
    except OSError as exc:
        raise SecretLeakError(
            f"secret gate tripped: {MODE_VIOLATION_KIND} ({target}: unreadable)"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise SecretLeakError(
            f"secret gate tripped: {MODE_VIOLATION_KIND} ({target}: not a file)"
        )
    if stat.S_IMODE(info.st_mode) != PRIVATE_FILE_MODE:
        raise SecretLeakError(
            f"secret gate tripped: {MODE_VIOLATION_KIND} "
            f"({target}: run: chmod 600 {target})"
        )
    return target


def _shannon_entropy(candidate: str) -> float:
    """Per-character Shannon entropy of ``candidate`` in bits."""
    counts: dict[str, int] = {}
    for char in candidate:
        counts[char] = counts.get(char, 0) + 1
    length = len(candidate)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


def _looks_like_generic_token(candidate: str) -> bool:
    """Conservative bare-token heuristic for what audit patterns miss.

    Flags runs of 32+ token-alphabet characters with mixed character classes
    and high per-character entropy. Pure-hex digests, UUIDs, and
    low-diversity runs (padding, repeats) are excluded so integrity metadata
    in manifests and logs does not trip the gate.

    A hierarchical Git ref is judged per component. ``/`` stays inside the
    token alphabet for every other string, so a base64 blob or a cache path
    that contains a slash is unchanged. A ref component that itself meets
    this test still trips.
    """
    text = candidate.strip("=").strip()
    if len(text) < _MIN_CANDIDATE_LEN:
        return False
    if _HEX_RE.match(text) or _UUID_RE.match(text):
        return False
    if _GIT_REF_RE.fullmatch(text):
        return any(_looks_like_generic_token(part) for part in text.split("/") if part)
    if len(set(text)) <= 4:
        return False
    classes = sum(
        (
            any(char.islower() for char in text),
            any(char.isupper() for char in text),
            any(char.isdigit() for char in text),
        )
    )
    if classes < 2:
        return False
    return _shannon_entropy(text) >= _MIN_ENTROPY_BITS


def _assert_text_clean(text: str, name: str) -> None:
    """Raise :class:`SecretLeakError` (kind ``token-pattern``) on any hit."""
    if redact_sensitive(text) != text:
        raise SecretLeakError(
            f"secret gate tripped for {name}: {TOKEN_PATTERN_KIND} detected"
        )
    if _PRIVATE_KEY_RE.search(text):
        raise SecretLeakError(
            f"secret gate tripped for {name}: {TOKEN_PATTERN_KIND} detected"
        )
    if _OMK_RE.search(text):
        raise SecretLeakError(
            f"secret gate tripped for {name}: {TOKEN_PATTERN_KIND} detected"
        )
    for match in _CANDIDATE_RE.finditer(text):
        if _looks_like_generic_token(match.group(0)):
            raise SecretLeakError(
                f"secret gate tripped for {name}: {TOKEN_PATTERN_KIND} detected"
            )


def assert_no_secrets(payload: str | os.PathLike[str], name: str = "bundle") -> None:
    """Fail closed if ``payload`` carries secret material.

    Pass literal text as ``str`` to scan it inline, or a :class:`Path`
    (any ``os.PathLike``) to scan a file -- file inputs first enforce the
    0600 mode via :func:`enforce_token_file_mode`, so a ``str`` that happens
    to name a file is still treated as literal text and never touches disk.

    Detection reuses :func:`omega_prime.grokbot.audit.redact_sensitive` (any
    text it would scrub trips the gate) plus PEM private-key blocks and a
    conservative bare high-entropy token heuristic.

    Raises :class:`SecretLeakError` whose message holds only ``name`` and the
    match kind (``token-pattern`` or ``0600-violation``), never secret bytes.
    Returns :data:`None` when the payload is clean.
    """
    if isinstance(payload, os.PathLike):
        target = enforce_token_file_mode(payload)
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise SecretLeakError(
                f"secret gate tripped for {name}: "
                f"{MODE_VIOLATION_KIND} ({target}: unreadable)"
            ) from exc
        _assert_text_clean(text, name)
        return None
    _assert_text_clean(payload, name)
    return None

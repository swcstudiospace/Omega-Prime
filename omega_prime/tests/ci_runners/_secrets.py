"""Per-run stand-ins for credentials, so no credential literal lives in tests.

Each value is minted at import time from :func:`secrets.token_hex`, carries an
obviously fake ``fake-`` prefix and no provider token shape (``ghp_``,
``ghs_`` ...), and is unique per test run.
"""

from __future__ import annotations

import secrets
from pathlib import Path


def placeholder(label: str) -> str:
    """``fake-<label>-<32 hex>``: long enough for every minimum-length check."""
    return f"fake-{label}-{secrets.token_hex(16)}"


def provider_shaped(prefix: str) -> str:
    """A value in a provider token *shape* (e.g. ``ghp_`` + 36 chars), minted
    per run from :func:`secrets.token_hex`, for tests that prove the
    pattern redactor catches that shape. Never a literal in source."""
    return prefix + secrets.token_hex(18)


def write_secret(path: Path, value: str) -> Path:
    """A secret file the credential broker accepts: regular, 0600, non-empty."""
    path.write_text(value + "\n")
    path.chmod(0o600)
    return path


__all__ = ["placeholder", "provider_shaped", "write_secret"]

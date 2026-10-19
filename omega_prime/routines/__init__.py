"""Scheduled routines: sweeps the agent runs on a cadence, never ad hoc."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

STATE_DIR_ENV = "OMEGA_PRIME_STATE_DIR"


def state_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """The install state directory, or None when the caller gave no home.

    An empty mapping (unit tests, a command with no host env) does not fall
    back to the process home or the working tree. A live host passes ``HOME``
    or ``OMEGA_PRIME_STATE_DIR``.
    """
    mapping = os.environ if env is None else env
    raw = mapping.get(STATE_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    xdg = mapping.get("XDG_STATE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "omega-prime"
    home = mapping.get("HOME", "").strip()
    if home:
        return Path(home) / ".local" / "state" / "omega-prime"
    return None


__all__ = ["STATE_DIR_ENV", "state_dir"]

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Required submodule checkout. An empty directory is a setup failure."""

from __future__ import annotations

from pathlib import Path

INIT_COMMAND = "git submodule update --init --recursive"
EMPTY_PRIME = (
    "prime-agent submodule is empty. Run: git submodule update --init --recursive"
)


def check_submodules(root: Path) -> tuple[bool, str]:
    """Fail when the required Prime checkout has no ``Cargo.toml``.

    ``redteam/`` is optional. An empty optional checkout is reported and
    does not fail setup.
    """
    prime = root / "prime-agent" / "Cargo.toml"
    if not prime.is_file():
        return False, EMPTY_PRIME
    redteam = root / "redteam" / "package.json"
    if redteam.is_file():
        return True, "prime-agent submodule populated; redteam submodule populated"
    return True, (
        "prime-agent submodule populated; redteam submodule is optional and empty. "
        f"Populate it with: {INIT_COMMAND}"
    )

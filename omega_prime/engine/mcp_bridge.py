# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Per-session omp mcp.json that starts Omega Prime's stdio server."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SKILLS = Path(__file__).resolve().parents[1] / "skills"
_INSTALL = Path(__file__).resolve().parents[2]


def skills_dir() -> Path:
    """Checked-in skills omp should load with ``--skills``."""
    return _SKILLS


def python_executable() -> str:
    return sys.executable


def install_root() -> Path:
    """Repository root that holds the roster and policy."""
    return _INSTALL


def write_mcp_json(
    profile: Path,
    *,
    python: str | None = None,
    work_root: Path | None = None,
) -> Path:
    """Write ``mcp.json`` and ``.mcp.json`` inside the session profile.

    ``--root`` is the installation that owns the roster and policy.
    ``--work-root`` is the checkout the tools may change. The server command
    is ``python -m omega_prime.mcp_server``. Approvals and audit stay in that
    process. No credentials are written into the file.
    """
    profile.mkdir(parents=True, exist_ok=True)
    args = ["-m", "omega_prime.mcp_server", "--root", str(_INSTALL)]
    if work_root is not None:
        args.extend(["--work-root", str(work_root)])
    payload = {
        "mcpServers": {
            "omega-prime": {
                "type": "stdio",
                "command": python or python_executable(),
                "args": args,
            }
        }
    }
    text = json.dumps(payload, indent=2) + "\n"
    path = profile / "mcp.json"
    path.write_text(text, encoding="utf-8")
    (profile / ".mcp.json").write_text(text, encoding="utf-8")
    return path


ENGINE_SYSTEM_PROMPT = (
    "You are Omega Prime's coding loop running on omp. "
    "Omega Prime supplies tools through the omega-prime MCP server, plus "
    "skills, prompts, and loop policy. Do not call model provider HTTP APIs. "
    "Do not print secrets, tokens, or environment values."
)

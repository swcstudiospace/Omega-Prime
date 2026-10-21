# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Per-job files under ``$OMEGA_PRIME_STATE_DIR/jobs/<id>/``."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omega_prime.engine.protocol import redact

_JOB_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


class JobPathError(ValueError):
    """A job id is not a single path segment."""


def flag(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in _TRUTHY


def falsy(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in _FALSY


def state_dir(env: Mapping[str, str]) -> Path:
    """State root. An explicit directory wins, otherwise ``~/.omega-prime``."""
    raw = str(env.get("OMEGA_PRIME_STATE_DIR", "")).strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".omega-prime"


MCP_STATE_DIR = "mcp-state"


def mcp_state_dir(env: Mapping[str, str]) -> Path:
    """Writable state root for the sandboxed Omega MCP child.

    It is a sibling of ``ui-control`` and ``mcp-attachments`` so the child can
    create its memory, session and desk stores without a writable bind over
    the operator control queue or the attachment journal.
    """
    return state_dir(env) / MCP_STATE_DIR


def check_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or _JOB_RE.fullmatch(job_id) is None:
        raise JobPathError("job id must be a short token")
    return job_id


def job_dir(env: Mapping[str, str], job_id: str) -> Path:
    """Directory for one job. The id cannot escape ``jobs/``."""
    token = check_job_id(job_id)
    root = (state_dir(env) / "jobs").resolve()
    path = (root / token).resolve()
    if path.parent != root:
        raise JobPathError("job id escapes the jobs directory")
    return path


def append_event(path: Path, event: dict[str, Any]) -> None:
    """Append one redacted event as a single JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(redact(event), ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def maybe_tmux(
    job_id: str,
    env: Mapping[str, str],
    *,
    enabled: bool | None = None,
) -> str | None:
    """Start ``omega-<job>`` when tmux is available.

    ``OMEGA_PRIME_ENGINE_TMUX=0`` skips the session. A missing tmux binary
    skips it too. The session runs ``python -m omega_prime.engine.watch``.
    """
    if enabled is False or falsy(env, "OMEGA_PRIME_ENGINE_TMUX"):
        return None
    if enabled is not True and not flag(env, "OMEGA_PRIME_ENGINE_TMUX"):
        # Default: attach a session only when tmux is already on PATH.
        pass
    tmux = shutil.which("tmux", path=env.get("PATH") or None)
    if not tmux:
        return None
    token = check_job_id(job_id)
    session = f"omega-{token}"
    probe = subprocess.run(
        [tmux, "has-session", "-t", session],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return session
    state = str(state_dir(env))
    watch = [
        sys.executable,
        "-m",
        "omega_prime.engine.watch",
        token,
        "--follow",
        "--state-dir",
        state,
    ]
    spawned = subprocess.run(
        [tmux, "new-session", "-d", "-s", session, "--", *watch],
        check=False,
        capture_output=True,
        text=True,
        env=child_env(env),
    )
    if spawned.returncode != 0:
        return None
    return session


_PASS_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "XDG_RUNTIME_DIR",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "ZAI_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "META_API_KEY",
    "MUSE_API_KEY",
    "CURSOR_API_KEY",
    "OMEGA_PRIME_STATE_DIR",
    "OMEGA_PRIME_WORK_ROOT",
)

# Connector settings the stdio MCP server reads. omp inherits this narrow
# set and the MCP child inherits it from omp. Values are not written to
# mcp.json. Model HTTP credentials beyond the provider keys above are not
# added here.
_CONNECTOR_ENV = (
    "X_API_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "ULTRATHINK_ROOT",
    "OMEGA_PRIME_PACKS_JSON",
    "OMEGA_PRIME_PACK_API_BASES_JSON",
    "SUBSTRATE_URL",
    "SUBSTRATE_TOKEN",
    "SUBSTRATE_TOKEN_GROK_BOT",
    "HINDSIGHT_URL",
    "HINDSIGHT_API_KEY",
    "HINDSIGHT_API_TOKEN",
    "RAILWAY_TOKEN",
    "OMEGA_PRIME_RAILWAY_PROJECTS",
    "GREPTILE_API_KEY",
    "GREPTILE_GITHUB_TOKEN",
    "VERCEL_TOKEN",
    "PLAY_CONSOLE_TOKEN",
    "ASC_KEY_ID",
    "ASC_ISSUER_ID",
    "ASC_PRIVATE_KEY",
    "OMEGA_PRIME_DESK_BUS_URL",
    "OMEGA_PRIME_DESK_DOCS_INDEX",
    "OMEGA_PRIME_DESK_NOTIFY_URL",
    "OMEGA_PRIME_DESK_CHILD_MODEL",
    "OMEGA_PRIME_DESK_CHILD_PROVIDER",
)


def omp_home_override(env: Mapping[str, str]) -> Path | None:
    """The ``OMEGA_PRIME_OMP_HOME`` config root, or ``None`` for omp's default."""
    explicit = str(env.get("OMEGA_PRIME_OMP_HOME", "")).strip()
    return Path(explicit) if explicit else None


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    """Environment handed to omp. Credential values stay out of argv.

    Only named keys are copied. ``OMEGA_FAKE_OMP_*`` is copied so the hermetic
    fake binary can be configured without opening the rest of the process env.
    """
    child: dict[str, str] = {}
    for name in (*_PASS_ENV, *_CONNECTOR_ENV):
        value = env.get(name)
        if isinstance(value, str) and value:
            child[name] = value
        file_name = f"{name}_FILE"
        file_value = env.get(file_name)
        if isinstance(file_value, str) and file_value:
            child[file_name] = file_value
    for name, value in env.items():
        if name.startswith("OMEGA_FAKE_OMP_") and isinstance(value, str):
            child[name] = value
    override = omp_home_override(env)
    if override is not None:
        # omp reads its login store (agent.db) from PI_CODING_AGENT_DIR, which
        # defaults to $HOME/.omp/agent. Point it at the override so usage
        # checks and RPC runs use the same store the sandbox binds.
        child["PI_CODING_AGENT_DIR"] = str(override / "agent")
    child.setdefault("PATH", os.defpath)
    return child

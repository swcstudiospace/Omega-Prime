# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Never-raising pane launcher for visible sandbox runs (W113-01).

Kept apart from ``herdr_launch`` (the CLI that records a pane on a task row):
this module only opens a pane for an engine run and never touches tasks.json.

One Herdr pane per sandbox run: ``herdr workspace create --cwd <cwd>
--label <label> --no-focus`` yields a pane id, then ``herdr pane run
<pane> <shell command>`` points the pane at the run. Both follow the
contract ``herdr_launch.launch`` already uses: the create reply is JSON
and the pane id is ``result.root_pane.pane_id``; the run command is ONE
``shlex.join(command)`` string, not separate arguments.

Any failure — missing binary, non-zero exit, timeout, unparseable output,
OSError — falls back to the engine's tmux session-spawn shape, re-implemented
locally (``omega_prime/engine/jobs.py`` ``maybe_tmux``; no engine import —
the layer direction engine→dashboard is locked and the pane command and
session name arrive from the caller). ``open_pane`` never raises: every
path ends in a pane id, ``"tmux:<session>"`` or ``None``.

Children run with a minimal env (``PATH``/``HOME`` only) so no
credential-bearing value from the engine env can reach pane argv or
receipts.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

_FALSY = frozenset({"0", "false", "no", "off"})

_HERDR_TIMEOUT = 10.0

__all__ = ["open_pane"]


def _child_env(env: Mapping[str, str]) -> dict[str, str]:
    """Two keys, nothing else — never the full engine env."""
    return {
        "PATH": env.get("PATH") or os.environ.get("PATH", os.defpath),
        "HOME": env.get("HOME") or os.environ.get("HOME", ""),
    }


def _parse_pane_id(stdout: str) -> str | None:
    """``result.root_pane.pane_id`` from the JSON create reply, else ``None``."""
    try:
        pane = json.loads(stdout)["result"]["root_pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(pane, str) or not pane.strip():
        return None
    return pane


def _herdr_pane(
    env: Mapping[str, str], *, cwd: str, label: str, command: list[str]
) -> str | None:
    """Open the herdr pane; ``None`` on any failure (the fallback takes over)."""
    herdr = shutil.which("herdr", path=env.get("PATH") or None)
    if not herdr:
        return None
    created = subprocess.run(
        [
            herdr,
            "workspace",
            "create",
            "--cwd",
            cwd,
            "--label",
            label,
            "--no-focus",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=_HERDR_TIMEOUT,
        env=_child_env(env),
    )
    if created.returncode != 0:
        return None
    pane = _parse_pane_id(created.stdout)
    if pane is None:
        return None
    run = subprocess.run(
        [herdr, "pane", "run", pane, shlex.join(command)],
        check=False,
        capture_output=True,
        text=True,
        timeout=_HERDR_TIMEOUT,
        env=_child_env(env),
    )
    if run.returncode != 0:
        return None
    return pane


def _tmux_pane(
    env: Mapping[str, str], *, command: list[str], session: str | None
) -> str | None:
    """Session-spawn fallback, the local shape of engine ``maybe_tmux``.

    Skips when ``OMEGA_PRIME_ENGINE_TMUX`` is falsy, tmux is missing, or no
    session name was passed; an existing session is reused.
    """
    if session is None:
        return None
    if str(env.get("OMEGA_PRIME_ENGINE_TMUX", "")).strip().lower() in _FALSY:
        return None
    tmux = shutil.which("tmux", path=env.get("PATH") or None)
    if not tmux:
        return None
    try:
        probe = subprocess.run(
            [tmux, "has-session", "-t", session],
            check=False,
            capture_output=True,
            text=True,
            timeout=_HERDR_TIMEOUT,
            env=_child_env(env),
        )
        if probe.returncode == 0:
            return f"tmux:{session}"
        spawned = subprocess.run(
            [tmux, "new-session", "-d", "-s", session, "--", *command],
            check=False,
            capture_output=True,
            text=True,
            timeout=_HERDR_TIMEOUT,
            env=_child_env(env),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if spawned.returncode != 0:
        return None
    return f"tmux:{session}"


def open_pane(
    env: Mapping[str, str],
    *,
    cwd: Any,
    label: str,
    command: Sequence[str],
    session: str | None = None,
) -> str | None:
    """Open one visible pane for a run and return its id, or ``None``.

    Returns the herdr pane id, ``"tmux:<session>"`` from the fallback, or
    ``None`` — never raises, so a visibility failure cannot kill a run.
    ``session=None`` disables the tmux fallback. The keyword-only
    ``session`` is a documented deviation from the literal W113-01
    signature: the session name is an engine convention the dashboard
    layer must not derive, so the caller passes it in.
    """
    argv = [str(part) for part in command]
    pane: str | None = None
    try:
        pane = _herdr_pane(env, cwd=str(cwd), label=label, command=argv)
    except Exception:  # a hung or missing herdr still gets the fallback
        pane = None
    if pane is not None:
        return pane
    try:
        return _tmux_pane(env, command=argv, session=session)
    except Exception:  # visibility never kills runs
        return None

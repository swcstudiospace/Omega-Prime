# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Outer sandbox for an omp process.

Bubblewrap is the interim layer. When it is missing the engine refuses to
start unless it is explicitly configured unsandboxed.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from omega_prime.engine.jobs import flag

UNSANDBOXED_ENV = "OMEGA_PRIME_ENGINE_UNSANDBOXED"


class SandboxUnavailable(Exception):
    """Bubblewrap is missing and unsandboxed mode is not configured."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def unsandboxed(env: Mapping[str, str], override: bool | None = None) -> bool:
    if override is not None:
        return override
    return flag(env, UNSANDBOXED_ENV)


def bwrap_path(env: Mapping[str, str]) -> str | None:
    explicit = str(env.get("OMEGA_PRIME_BWRAP_BIN", "")).strip()
    if explicit:
        path = Path(explicit)
        if path.is_file() and os_access(path):
            return str(path)
        return None
    return shutil.which("bwrap", path=env.get("PATH") or None)


def os_access(path: Path) -> bool:
    return path.stat().st_mode & 0o111 != 0


def sandbox_ready(
    env: Mapping[str, str], *, unsandboxed_override: bool | None = None
) -> bool:
    if unsandboxed(env, unsandboxed_override):
        return True
    return bwrap_path(env) is not None


def wrap(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    binds: Sequence[Path],
    hidden: Sequence[Path] = (),
    unsandboxed_override: bool | None = None,
) -> tuple[list[str], str]:
    """Return the argv to spawn and a short sandbox label.

    ``bwrap`` gets a read-only root, a read-write bind for each job path,
    and an unshared pid namespace. Each ``hidden`` path (operator files such
    as ``settings.json``, ``secrets/`` and ``dashboard/``) is masked: a
    directory by an empty read-only tmpfs, a file by ``/dev/null``. A hidden
    path that would cover a writable bind is refused. The omp argv follows
    ``--``.
    """
    if unsandboxed(env, unsandboxed_override):
        return list(argv), "unsandboxed"
    binary = bwrap_path(env)
    if binary is None:
        raise SandboxUnavailable(
            "bubblewrap is not available; set "
            f"{UNSANDBOXED_ENV}=1 to run omp without a sandbox"
        )
    command = [
        binary,
        "--ro-bind",
        "/",
        "/",
        "--unshare-pid",
        "--die-with-parent",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]
    bind_paths = [Path(str(bind)) for bind in binds]
    for bind in bind_paths:
        text = str(bind)
        command.extend(["--bind", text, text])
    for item in hidden:
        path = Path(str(item))
        for bind in bind_paths:
            if bind == path or path in bind.parents:
                raise SandboxUnavailable(
                    f"writable bind {bind} sits inside private path {path}"
                )
        text = str(path)
        if path.is_dir() and not path.is_symlink():
            command.extend(["--tmpfs", text, "--remount-ro", text])
        elif path.exists() or path.is_symlink():
            command.extend(["--ro-bind", "/dev/null", text])
    command.append("--")
    command.extend(argv)
    return command, "bwrap"

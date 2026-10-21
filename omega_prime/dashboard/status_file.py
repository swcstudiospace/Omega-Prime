# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Locked read-modify-write store for the dashboard ``tasks.json``.

Box contract (README-REFS): every writer holds an exclusive ``fcntl.flock``
on a sidecar ``<path>.lock`` for the whole read-modify-write and persists
through ``_write_locked`` inside the ``with`` block, so concurrent writers
(the engine, the hourly digest routine, a push hook) never observe partial
or interleaved JSON.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Documented schema, not enforced against stored data: the file belongs to
# the dashboard. Unknown fields on rows and items pass through untouched.
TASK_STATES = ("queued", "working", "blocked", "waiting_on_ove", "done", "failed")
ITEM_FIELDS = ("id", "seq", "ask", "blocks", "since")


def _seed() -> dict[str, Any]:
    return {"tasks": [], "waiting_on_ove": []}


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _seed()
    if not raw.strip():
        return _seed()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"tasks store is not a JSON object: {path}")
    return data


@contextlib.contextmanager
def locked_tasks(
    path: str | os.PathLike[str], write: bool = False
) -> Iterator[dict[str, Any]]:
    """Yield the parsed tasks.json under a sidecar flock.

    ``write=True`` holds ``LOCK_EX`` on ``<path>.lock`` for the whole
    ``with`` block; otherwise the lock is shared. The manager itself never
    writes: persistence is the caller's explicit ``_write_locked(data,
    path)`` inside the block, which is what serializes every
    read-modify-write against concurrent writers.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        try:
            yield _load(target)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_locked(data: dict[str, Any], path: str | os.PathLike[str]) -> None:
    """Persist ``data`` atomically: same-directory temp file + ``os.replace``.

    Called inside the ``locked_tasks(write=True)`` block. On any error the
    temp file is unlinked and the error re-raised; ``path`` keeps its prior
    content. No key reordering, no field filtering.
    """
    target = Path(path)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=target.parent,
        prefix=target.name + ".",
        suffix=".tmp",
    )
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(json.dumps(data, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


__all__ = ["ITEM_FIELDS", "TASK_STATES", "_write_locked", "locked_tasks"]

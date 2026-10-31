# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Ownership records for sandboxes this package created.

One small JSON file per sandbox under ``<state>/sandbox/owned/<name>.json``
(mode 0600, atomic replace). The records map a sandbox to its owning task and
mark deliberate teardown, so the Podman events source can tell a delete's
SIGKILL (137) from a real crash.

Every update holds a per-sandbox ``flock`` (``<name>.lock``) and writes
through its own temporary file: a background RLM child and a host call can
both reach ``sandbox_down`` (child dispatch bypasses ``ToolGate``), and their
writes must not share a file or lose each other's fields.

A teardown mark is cleared when deletion fails and expires after
``TEARDOWN_TTL_S`` anyway, so a stuck mark can never hide later real deaths.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from omega_prime.sdlc_events.sandbox import SANDBOX_NAME_RE

# Long enough for ``sandbox delete`` (180 s timeout) plus the leftover checks.
TEARDOWN_TTL_S = 600.0


class Ownership:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._root = root
        self._clock = clock

    def _path(self, name: str) -> Path:
        if not SANDBOX_NAME_RE.match(name):
            raise ValueError(f"invalid sandbox name: {name!r}")
        return self._root / f"{name}.json"

    @contextlib.contextmanager
    def _locked(self, name: str) -> Iterator[Path]:
        path = self._path(name)
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) else None

    def _update(self, name: str, change: Callable[[dict[str, Any]], None]) -> None:
        with self._locked(name) as path:
            record = self._read(path) or {"name": name, "task_id": None}
            change(record)
            self._write(path, record)

    def get(self, name: str) -> dict[str, Any] | None:
        return self._read(self._path(name))

    def record(self, name: str, task_id: str | None) -> None:
        with self._locked(name) as path:
            self._write(
                path, {"name": name, "task_id": task_id, "created_at": self._clock()}
            )

    def mark_teardown(self, name: str, *, uuid: str | None = None) -> None:
        def change(record: dict[str, Any]) -> None:
            record["teardown_at"] = self._clock()
            if uuid:
                record["uuid"] = uuid

        self._update(name, change)

    def clear_teardown(self, name: str) -> None:
        self._update(name, lambda record: record.pop("teardown_at", None))

    def forget(self, name: str) -> None:
        with self._locked(name) as path, contextlib.suppress(FileNotFoundError):
            path.unlink()

    def task_for(self, name: str) -> str | None:
        record = self.get(name)
        task = record.get("task_id") if record else None
        return task if isinstance(task, str) and task else None

    def uuid_for(self, name: str) -> str | None:
        record = self.get(name)
        uuid = record.get("uuid") if record else None
        return uuid if isinstance(uuid, str) and uuid else None

    def teardown(self, name: str) -> bool:
        record = self.get(name)
        marked = record.get("teardown_at") if record else None
        if not isinstance(marked, int | float):
            return False
        return self._clock() - marked < TEARDOWN_TTL_S

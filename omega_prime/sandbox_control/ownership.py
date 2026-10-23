# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Ownership records for sandboxes this package created.

One small JSON file per sandbox under ``<state>/sandbox/owned/<name>.json``
(mode 0600, atomic replace). The records map a sandbox to its owning task and
mark deliberate teardown, so the Podman events source can tell a delete's
SIGKILL (137) from a real crash.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from omega_prime.sdlc_events.sandbox import SANDBOX_NAME_RE


class Ownership:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, name: str) -> Path:
        if not SANDBOX_NAME_RE.match(name):
            raise ValueError(f"invalid sandbox name: {name!r}")
        return self._root / f"{name}.json"

    def _write(self, name: str, record: dict[str, Any]) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(name)
        tmp = path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
        os.replace(tmp, path)

    def get(self, name: str) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) else None

    def record(self, name: str, task_id: str | None) -> None:
        self._write(name, {"name": name, "task_id": task_id, "created_at": time.time()})

    def mark_teardown(self, name: str) -> None:
        record = self.get(name) or {"name": name, "task_id": None}
        record["teardown_at"] = time.time()
        self._write(name, record)

    def forget(self, name: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(name).unlink()

    def task_for(self, name: str) -> str | None:
        record = self.get(name)
        task = record.get("task_id") if record else None
        return task if isinstance(task, str) and task else None

    def teardown(self, name: str) -> bool:
        record = self.get(name)
        return bool(record and record.get("teardown_at"))

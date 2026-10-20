# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Validated, redacted and atomic tasks.json v1 bridge."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.redact import redact_value

_STATES = {"queued", "working", "blocked", "waiting_on_ove", "done", "failed"}
_RESULTS = {"ok", "failed", "stopped"}


@dataclass
class TasksFileResult:
    status: str
    data: dict[str, Any] | None = None
    error: str | None = None
    age_s: float | None = None


def _string(obj: Any, field: str, prefix: str) -> str:
    value = obj.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{prefix}.{field} must be a string")
    return value


def _optional_string(obj: dict[str, Any], field: str, prefix: str) -> str | None:
    value = obj.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{prefix}.{field} must be a string or null")
    return value


def _progress(value: Any, prefix: str) -> dict[str, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("done"), int)
        or isinstance(value.get("done"), bool)
        or not isinstance(value.get("total"), int)
        or isinstance(value.get("total"), bool)
    ):
        raise ValueError(
            f"{prefix}.progress must be null or contain integer done and total"
        )
    return {"done": value["done"], "total": value["total"]}


def _links(value: Any, prefix: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{prefix}.links must be a list")
    result: list[dict[str, str]] = []
    for i, link in enumerate(value):
        if not isinstance(link, dict):
            raise ValueError(f"{prefix}.links[{i}] must be a dict")
        result.append(
            {
                "label": _string(link, "label", f"{prefix}.links[{i}]"),
                "path_or_url": _string(link, "path_or_url", f"{prefix}.links[{i}]"),
            }
        )
    return result


def _validated(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("version") != 1:
        raise ValueError("version must equal 1")
    out: dict[str, Any] = {
        "version": 1,
        "updated_at": _string(data, "updated_at", "root"),
    }
    for category, required in (
        ("tasks", ("id", "title")),
        ("waiting_on_ove", ("id", "ask")),
        ("finished", ("id", "title")),
    ):
        rows = data.get(category)
        if not isinstance(rows, list):
            raise ValueError(f"{category} must be a list")
        clean: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            prefix = f"{category}[{i}]"
            if not isinstance(row, dict):
                raise ValueError(f"{prefix} must be a dict")
            item: dict[str, Any] = {key: _string(row, key, prefix) for key in required}
            if category == "tasks":
                owner = row.get("owner")
                if not isinstance(owner, dict):
                    raise ValueError(f"{prefix}.owner must be a dict")
                item["owner"] = {
                    "kind": _string(owner, "kind", f"{prefix}.owner"),
                    "ref": _string(owner, "ref", f"{prefix}.owner"),
                }
                state = _string(row, "state", prefix)
                if state not in _STATES:
                    raise ValueError(f"{prefix}.state is invalid")
                item["state"] = state
                item["started_at"] = _optional_string(row, "started_at", prefix)
                item["last_activity_at"] = _optional_string(
                    row, "last_activity_at", prefix
                )
                item["progress"] = _progress(row.get("progress"), prefix)
                item["note"] = _optional_string(row, "note", prefix)
                item["links"] = _links(row.get("links", []), prefix)
            elif category == "waiting_on_ove":
                item["since"] = _optional_string(row, "since", prefix)
                item["blocks"] = _string(row, "blocks", prefix)
            else:
                item["finished_at"] = _string(row, "finished_at", prefix)
                result = _string(row, "result", prefix)
                if result not in _RESULTS:
                    raise ValueError(f"{prefix}.result is invalid")
                item["result"] = result
                item["receipt"] = _optional_string(row, "receipt", prefix)
            clean.append(item)
        out[category] = clean
    return out


def write_tasks(data: dict[str, Any], *, path: Path) -> Path:
    """Validate and atomically persist a redacted tasks.json document."""
    safe = redact_value(_validated(data))
    payload = (json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    return path


def read_tasks(path: Path) -> TasksFileResult:
    """Read the bridge without allowing malformed input to destabilize callers."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return TasksFileResult("missing")
    except OSError as exc:
        return TasksFileResult("unreadable", error=redact_value(str(exc)))
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("tasks file must be a version 1 object")
        data = _validated(data)
    except (json.JSONDecodeError, ValueError) as exc:
        return TasksFileResult("unreadable", error=redact_value(str(exc)))
    try:
        age = max(0.0, datetime.now().timestamp() - path.stat().st_mtime)
    except OSError:
        age = None
    return TasksFileResult("ok", redact_value(data), None, age)

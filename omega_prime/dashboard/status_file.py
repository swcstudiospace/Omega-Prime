# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Validated, redacted and atomic tasks.json v1 bridge."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.redact import redact_value

_STATES = {"queued", "working", "blocked", "waiting_on_ove", "done", "failed"}
_RESULTS = {"ok", "failed", "stopped"}
_PANE_ID = re.compile(r"^w[0-9A-Za-z]+:p[0-9A-Za-z]+$")


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


def _optional_iso(obj: dict[str, Any], field: str, prefix: str) -> str | None:
    value = obj.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{prefix}.{field} must be an ISO-8601 string or null")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{prefix}.{field} must be an ISO-8601 string or null"
        ) from exc
    return value


def _optional_pid(obj: dict[str, Any], field: str, prefix: str) -> int | None:
    value = obj.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{prefix}.{field} must be a positive integer or null")
    return value


def _optional_stale(value: Any, prefix: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"reason", "since"}:
        raise ValueError(f"{prefix}.stale must be a stale mapping")
    reason = value.get("reason")
    since = value.get("since")
    if not isinstance(reason, str):
        raise ValueError(f"{prefix}.stale.reason must be a string or null")
    if not isinstance(since, str):
        raise ValueError(f"{prefix}.stale.since must be an ISO-8601 string or null")
    try:
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{prefix}.stale.since must be an ISO-8601 string or null"
        ) from exc
    return {"reason": reason, "since": since}


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


def _pane(value: Any, prefix: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"pane_id", "workspace"}:
        raise ValueError(f"{prefix}.herdr_pane must be a pane mapping")
    pane_id = value.get("pane_id")
    if not isinstance(pane_id, str) or not _PANE_ID.fullmatch(pane_id):
        raise ValueError(f"{prefix}.herdr_pane.pane_id is invalid")
    workspace = value.get("workspace")
    result = {"pane_id": pane_id}
    if workspace is not None:
        if (
            not isinstance(workspace, str)
            or len(workspace.encode("utf-8")) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in workspace)
        ):
            raise ValueError(f"{prefix}.herdr_pane.workspace is invalid")
        safe_workspace = redact_value(workspace)
        result["workspace"] = safe_workspace
    return result


def _canonical_identity(identity: str) -> str:
    return identity if ":" in identity else f"tasks:{identity}"


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
                item["herdr_pane"] = _pane(row.get("herdr_pane"), prefix)
                item["last_heartbeat_at"] = _optional_iso(
                    row, "last_heartbeat_at", prefix
                )
                item["pid"] = _optional_pid(row, "pid", prefix)
                item["stale"] = _optional_stale(row.get("stale"), prefix)
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
                item["herdr_pane"] = _pane(row.get("herdr_pane"), prefix)
            if "seq" in row:
                seq = row["seq"]
                if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
                    raise ValueError(f"{prefix}.seq must be a positive integer")
                item["seq"] = seq
            clean.append(item)
        out[category] = clean

    next_seq = data.get("next_seq", 1)
    if isinstance(next_seq, bool) or not isinstance(next_seq, int) or next_seq <= 0:
        raise ValueError("next_seq must be a positive integer")
    registry = data.get("identity_registry", {})
    if not isinstance(registry, dict):
        raise ValueError("identity_registry must be an object")
    clean_registry: dict[str, Any] = {}
    seq_owner: dict[int, str] = {}
    for key, record in registry.items():
        if (
            not isinstance(key, str)
            or len(key) != 64
            or any(c not in "0123456789abcdef" for c in key)
        ):
            raise ValueError("identity_registry key invalid")
        if not isinstance(record, dict):
            raise ValueError("identity_registry record invalid")
        seq = record.get("seq")
        title = record.get("title")
        timeline = record.get("timeline")
        last_state = record.get("last_state")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
            raise ValueError("registry seq must be a positive integer")
        if not isinstance(title, str) or not isinstance(timeline, list):
            raise ValueError("registry history invalid")
        if last_state is not None and not isinstance(last_state, str):
            raise ValueError("registry last_state invalid")
        clean_timeline: list[dict[str, str]] = []
        for event in timeline:
            if not isinstance(event, dict) or set(event) != {"at", "state"}:
                raise ValueError("registry timeline event invalid")
            at, state = event["at"], event["state"]
            if not isinstance(at, str) or not isinstance(state, str):
                raise ValueError("registry timeline event fields invalid")
            try:
                datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("registry timeline timestamp invalid") from exc
            clean_timeline.append({"at": at, "state": state})
        if seq in seq_owner:
            raise ValueError("duplicate sequence ownership")
        seq_owner[seq] = key
        clean_registry[key] = {
            "seq": seq,
            "title": title,
            "timeline": clean_timeline,
            "last_state": last_state,
        }
    row_owner: dict[int, str] = {}
    for category in ("tasks", "waiting_on_ove", "finished"):
        for row in out[category]:
            seq = row.get("seq")
            if seq is None:
                continue
            key = hashlib.sha256(
                _canonical_identity(row["id"]).encode("utf-8")
            ).hexdigest()
            if seq in row_owner and row_owner[seq] != key:
                raise ValueError("duplicate sequence ownership")
            row_owner[seq] = key
            if key in clean_registry and clean_registry[key]["seq"] != seq:
                raise ValueError("row sequence conflicts with identity registry")
            if seq in seq_owner and seq_owner[seq] != key:
                raise ValueError("row sequence conflicts with identity registry")
    out["next_seq"] = max(
        next_seq, max(seq_owner, default=0) + 1, max(row_owner, default=0) + 1
    )
    out["identity_registry"] = clean_registry
    return out


@contextmanager
def locked_tasks(path: Path, *, write: bool):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("tasks document must be an object")
            data = _validated(data)
        else:
            data = _validated(
                {
                    "version": 1,
                    "updated_at": datetime.now().isoformat(),
                    "tasks": [],
                    "waiting_on_ove": [],
                    "finished": [],
                    "next_seq": 1,
                    "identity_registry": {},
                }
            )
        yield data
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_locked(data: dict[str, Any], path: Path) -> Path:
    safe = redact_value(_validated(data))
    payload = (json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        if path.read_bytes() == payload:
            return path
    except FileNotFoundError:
        pass
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


def write_tasks(data: dict[str, Any], *, path: Path) -> Path:
    with locked_tasks(path, write=True) as current:
        candidate = _validated(data)
        registry = {
            key: dict(record) for key, record in current["identity_registry"].items()
        }
        for key, incoming in candidate["identity_registry"].items():
            existing = registry.get(key)
            if existing is None:
                registry[key] = incoming
                continue
            if existing["seq"] != incoming["seq"]:
                raise ValueError("identity sequence cannot be reassigned")
            known = {(event["at"], event["state"]) for event in existing["timeline"]}
            for event in incoming["timeline"]:
                if (event["at"], event["state"]) not in known:
                    existing["timeline"].append(event)
                    known.add((event["at"], event["state"]))
            existing["title"] = incoming["title"]
            if incoming["last_state"] is not None:
                existing["last_state"] = incoming["last_state"]
        candidate["identity_registry"] = registry
        candidate["next_seq"] = max(
            candidate["next_seq"],
            current["next_seq"],
            max((record["seq"] for record in registry.values()), default=0) + 1,
        )
        for category in ("tasks", "waiting_on_ove", "finished"):
            for row in candidate[category]:
                key = hashlib.sha256(
                    _canonical_identity(row["id"]).encode("utf-8")
                ).hexdigest()
                record = registry.get(key)
                if record is not None:
                    if "seq" in row and row["seq"] != record["seq"]:
                        raise ValueError(
                            "row sequence conflicts with identity registry"
                        )
                    row["seq"] = record["seq"]
        return _write_locked(candidate, path)


def heartbeat(
    path: Path,
    task_id: str,
    *,
    state: str | None = None,
    note: str | None = None,
    pid: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record liveness for one task row through the locked write path.

    Sets last_heartbeat_at (plus any optional state/note/pid), refreshes the root
    updated_at, drops any persisted stale marker from the row, and returns a copy
    of the row. Unknown task ids raise ValueError without writing anything.
    """
    stamp = (now or datetime.now(UTC)).isoformat()
    with locked_tasks(path, write=True) as data:
        row = next((t for t in data["tasks"] if t.get("id") == task_id), None)
        if row is None:
            raise ValueError(f"no tasks row with id {task_id}")
        row["last_heartbeat_at"] = stamp
        if state is not None:
            row["state"] = state
        if note is not None:
            row["note"] = note
        if pid is not None:
            row["pid"] = pid
        row.pop("stale", None)
        data["updated_at"] = stamp
        _write_locked(data, path)
        return dict(row)


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

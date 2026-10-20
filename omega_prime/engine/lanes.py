# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Small/big router. Both lanes take the same ``JobSpec``.

Local work stays on the box only when every gate holds:

- no cloud-only marker (Rust, Go, Docker, mobile)
- declared languages are JS, TS, Python, or docs
- ``max_files`` is at most 25
- ``max_minutes`` is unlimited unless an operator configures a limit
- fewer than 2 running local jobs
- at least 3 GiB of free memory
- at most one running local ``xai-oauth`` job

A full box does not wait. The default is to spill that job to the fleet.
``overflow="queue"`` records it as a queued local job instead.

The local executor in this unit is a stub. U2 supplies the agentOS runner
behind ``LocalExecutor``. A local job that hits a VM limit or an unsupported
toolchain is re-posted to the fleet with its partial branch. The job receipt
records that lane change.

Nothing here calls a model HTTP API.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

from omega_prime.engine.fleet_client import (
    FleetClient,
    FleetError,
    JobSpec,
    validate_spec,
)
from omega_prime.engine.jobs import append_event, job_dir, state_dir
from omega_prime.engine.protocol import redact
from omega_prime.tooling.fs import atomic_write_json

_GIB = 1024 * 1024 * 1024
_LOCAL_LANGUAGES = frozenset(
    {
        "js",
        "javascript",
        "mjs",
        "cjs",
        "jsx",
        "ts",
        "typescript",
        "tsx",
        "py",
        "python",
        "docs",
        "doc",
        "markdown",
        "md",
        "mdx",
        "txt",
        "text",
        "rst",
    }
)
_ESCALATION_REASONS = frozenset({"vm_limit", "unsupported_toolchain", "time_cap"})
_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rust", re.compile(r"(?i)(?:\brust\b|\.rs\b|cargo\.toml)")),
    (
        "go",
        re.compile(
            r"(?i)(?:\bgolang\b|go\.mod|go\.sum|(?:^|[\s/])[^/\s]+\.go\b|"
            r"\bgo\s+(?:service|module|code|lang|language|package|file|test|"
            r"program|app|toolchain|compiler|library|source|backend)s?\b|"
            r"\b(?:in|using|with|via)\s+go\b)"
        ),
    ),
    ("docker", re.compile(r"(?i)(?:\bdocker\b|dockerfile|docker-compose)")),
    (
        "mobile",
        re.compile(
            r"(?i)(?:\bandroid\b|\bkotlin\b|\bswift(?:ui)?\b|\bflutter\b|"
            r"react[- ]native|\bmobile\b|\biphone\b|\bipad\b|"
            r"(?<![A-Za-z])ios(?![A-Za-z])|"
            r"(?:^|/)(?:android|ios)(?:/|$))"
        ),
    ),
)


class LocalExecutor(Protocol):
    """Starts one local job. U2's sandbox runner replaces the stub."""

    def start(self, job_id: str, spec: JobSpec) -> None:
        """Accept ``spec``. Raising fails the job before any fleet spill."""


class StubLocalExecutor:
    """Records that the local lane accepted the job. It does not spawn omp."""

    def start(self, job_id: str, spec: JobSpec) -> None:
        del job_id, spec


@dataclass(frozen=True)
class LaneLimits:
    """Operator-configurable classification limits; unset minutes are unlimited."""

    max_minutes: int | None = None
    max_files: int = 25
    max_parallel: int = 2
    min_free_bytes: int = 3 * _GIB
    xai_oauth_concurrency: int = 1


@dataclass(frozen=True)
class BoxSnapshot:
    """Capacity and classification inputs. Memory ``None`` means unknown."""

    running_local: int = 0
    running_xai_oauth: int = 0
    free_bytes: int | None = None
    languages: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    extra_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    """Lane choice. ``reasons`` say why the job did not stay running locally."""

    lane: str
    status: str
    reasons: tuple[str, ...]


def choose_lane(
    spec: JobSpec,
    box: BoxSnapshot,
    *,
    limits: LaneLimits | None = None,
    overflow: str = "spill",
) -> Decision:
    """Pick ``local`` or ``fleet`` without doing I/O."""
    caps = limits if limits is not None else LaneLimits()
    if overflow not in {"spill", "queue"}:
        raise FleetError("invalid_overflow")
    validate_spec(spec)
    blocked = _class_reasons(spec, box, caps)
    if blocked:
        return Decision("fleet", "submitted", blocked)
    full = _capacity_reasons(spec, box, caps)
    if not full:
        return Decision("local", "running", ())
    if overflow == "queue":
        return Decision("local", "queued", full)
    return Decision("fleet", "submitted", ("capacity", *full))


def _class_reasons(
    spec: JobSpec, box: BoxSnapshot, limits: LaneLimits
) -> tuple[str, ...]:
    reasons: list[str] = []
    markers = _markers(spec, box)
    if markers:
        reasons.append("cloud_only")
    if not _languages_ok(box.languages):
        reasons.append("language")
    if spec.max_files is not None and spec.max_files > limits.max_files:
        reasons.append("max_files")
    if (
        limits.max_minutes is not None
        and spec.max_minutes is not None
        and spec.max_minutes > limits.max_minutes
    ):
        reasons.append("max_minutes")
    return tuple(reasons)


def _capacity_reasons(
    spec: JobSpec, box: BoxSnapshot, limits: LaneLimits
) -> tuple[str, ...]:
    reasons: list[str] = []
    if box.running_local >= limits.max_parallel:
        reasons.append("max_parallel")
    if box.free_bytes is None or box.free_bytes < limits.min_free_bytes:
        reasons.append("memory")
    if _uses_xai_oauth(spec) and box.running_xai_oauth >= limits.xai_oauth_concurrency:
        reasons.append("xai_oauth")
    return tuple(reasons)


def _languages_ok(languages: tuple[str, ...]) -> bool:
    for raw in languages:
        if not isinstance(raw, str):
            return False
        name = raw.strip().lower().lstrip(".")
        if name not in _LOCAL_LANGUAGES:
            return False
    return True


def _markers(spec: JobSpec, box: BoxSnapshot) -> tuple[str, ...]:
    found: list[str] = []
    haystack = "\n".join(
        (
            spec.prompt,
            spec.repo,
            spec.branch or "",
            "\n".join(box.paths),
            "\n".join(box.extra_markers),
            "\n".join(box.languages),
        )
    )
    for name, pattern in _MARKER_PATTERNS:
        if pattern.search(haystack):
            found.append(name)
    for raw in box.extra_markers:
        label = raw.strip().lower()
        if label in {"rust", "go", "docker", "mobile"} and label not in found:
            found.append(label)
    return tuple(found)


def _uses_xai_oauth(spec: JobSpec) -> bool:
    model = spec.model or ""
    return model == "xai-oauth" or model.startswith("xai-oauth/")


def free_memory_bytes() -> int | None:
    """``MemAvailable`` from ``/proc/meminfo``, or ``None`` when it is absent."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


class LaneRouter:
    """Applies ``choose_lane`` and writes the job record for the chosen lane."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        client: FleetClient | None = None,
        executor: LocalExecutor | None = None,
        memory: Callable[[], int | None] | None = None,
        limits: LaneLimits | None = None,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._client = client if client is not None else FleetClient(self._env)
        self._executor = executor if executor is not None else StubLocalExecutor()
        self._memory = memory if memory is not None else free_memory_bytes
        self._limits = limits if limits is not None else LaneLimits()

    def dispatch(
        self,
        spec: JobSpec,
        *,
        languages: tuple[str, ...] = (),
        paths: tuple[str, ...] = (),
        extra_markers: tuple[str, ...] = (),
        overflow: str = "spill",
    ) -> dict[str, Any]:
        """Choose a lane and start or queue the job. Returns a redacted record."""
        with self._reservation():
            box = self.snapshot(
                languages=languages, paths=paths, extra_markers=extra_markers
            )
            decision = choose_lane(spec, box, limits=self._limits, overflow=overflow)
            job_id = _local_id()
            if decision.lane == "local":
                spec = _with_local_caps(spec, self._limits)
            record = _base_record(job_id, spec, decision)
            if decision.lane == "local":
                try:
                    self._write_spec(job_id, spec)
                    self._write_lane(job_id, record)
                except Exception:
                    self._drop_reservation(job_id)
                    raise
        if decision.lane == "local":
            return self._start_local(job_id, spec, record)
        return self._submit_fleet(job_id, spec, record)

    def escalate(
        self,
        job_id: str,
        *,
        reason: str,
        partial_branch: str,
    ) -> dict[str, Any]:
        """Re-queue a local job onto the fleet and record the lane change."""
        if reason not in _ESCALATION_REASONS:
            raise FleetError("invalid_escalation")
        current = self._read_lane(job_id)
        if current.get("status") in {"escalated", "closed"}:
            raise FleetError("already_escalated")
        if current.get("lane") != "local":
            raise FleetError("not_local")
        spec = self._read_spec(job_id)
        posted = replace(spec, branch=partial_branch)
        validate_spec(posted)
        fleet_job = self._client.submit(posted)
        fleet_id = fleet_job.get("id")
        change = {
            "from": "local",
            "to": "fleet",
            "reason": reason,
            "partial_branch": partial_branch,
            "fleet_job_id": fleet_id,
        }
        current["status"] = "escalated"
        current["lane"] = "fleet"
        current["fleet_job_id"] = fleet_id
        changes = list(current.get("changes") or [])
        changes.append(change)
        current["changes"] = changes
        self._write_lane(job_id, current)
        receipt = {
            "job_id": job_id,
            "lane_change": change,
        }
        self._write_json(job_id, "receipt.json", receipt)
        append_event(
            self._events_path(job_id),
            {"type": "lane_change", "job_id": job_id, **change},
        )
        return redact(receipt)

    def snapshot(
        self,
        *,
        languages: tuple[str, ...] = (),
        paths: tuple[str, ...] = (),
        extra_markers: tuple[str, ...] = (),
    ) -> BoxSnapshot:
        running = self._running()
        xai = sum(1 for item in running if _record_uses_xai(item))
        return BoxSnapshot(
            running_local=len(running),
            running_xai_oauth=xai,
            free_bytes=self._memory(),
            languages=languages,
            paths=paths,
            extra_markers=extra_markers,
        )

    def _start_local(
        self, job_id: str, spec: JobSpec, record: dict[str, Any]
    ) -> dict[str, Any]:
        append_event(
            self._events_path(job_id),
            {
                "type": "lane_chosen",
                "job_id": job_id,
                "lane": "local",
                "status": record["status"],
                "reasons": list(record["reasons"]),
            },
        )
        if record["status"] == "running":
            try:
                self._executor.start(job_id, spec)
            except Exception:
                record["status"] = "failed"
                record["error"] = "local_start_failed"
                self._write_lane(job_id, record)
                raise
        return redact(record)

    def _submit_fleet(
        self, job_id: str, spec: JobSpec, record: dict[str, Any]
    ) -> dict[str, Any]:
        fleet_job = self._client.submit(spec)
        record["fleet_job_id"] = fleet_job.get("id")
        record["fleet_status"] = fleet_job.get("status")
        self._write_lane(job_id, record)
        append_event(
            self._events_path(job_id),
            {
                "type": "lane_chosen",
                "job_id": job_id,
                "lane": "fleet",
                "fleet_job_id": record["fleet_job_id"],
                "reasons": list(record["reasons"]),
            },
        )
        return redact(record)

    def _running(self) -> list[dict[str, Any]]:
        root = state_dir(self._env) / "jobs"
        if not root.is_dir():
            return []
        running: list[dict[str, Any]] = []
        for entry in root.iterdir():
            path = entry / "lane.json"
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
                payload = json.loads(text)
            except (OSError, ValueError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("lane") == "local"
                and payload.get("status") == "running"
            ):
                running.append(payload)
        return running

    def _read_lane(self, job_id: str) -> dict[str, Any]:
        path = job_dir(self._env, job_id) / "lane.json"
        try:
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)
        except (OSError, ValueError):
            raise FleetError("unknown_job") from None
        if not isinstance(payload, dict):
            raise FleetError("unknown_job")
        return payload

    @contextmanager
    def _reservation(self) -> Iterator[None]:
        """Exclusive lock from the capacity read through the local reservation.

        The lock file lives in the state directory, so two MCP processes that
        share that directory cannot both accept a local slot.
        """
        root = state_dir(self._env)
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / "lanes.lock").open("a+")
        try:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            handle.close()

    def _write_lane(self, job_id: str, record: dict[str, Any]) -> None:
        self._write_json(job_id, "lane.json", record)

    def _drop_reservation(self, job_id: str) -> None:
        """Free a slot when the reservation did not finish.

        A running ``lane.json`` counts against the local cap, so a failed
        follow-up write must not leave that status behind.
        """
        root = job_dir(self._env, job_id)
        lane = root / "lane.json"
        spec_path = root / "spec.json"
        if lane.is_file():
            try:
                payload = json.loads(lane.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {"id": job_id, "lane": "local"}
            if isinstance(payload, dict):
                payload["status"] = "failed"
                payload["error"] = "reservation_failed"
                try:
                    self._write_lane(job_id, payload)
                except OSError:
                    return
            return
        try:
            spec_path.unlink()
        except OSError:
            return

    def _write_spec(self, job_id: str, spec: JobSpec) -> None:
        """Persist the local spec, recording an unset minute limit as JSON null."""
        path = job_dir(self._env, job_id) / "spec.json"
        payload = spec.to_json()
        payload["max_minutes"] = spec.max_minutes
        if spec.stage is not None:
            # Local-only field; the fleet post uses ``to_json`` without it.
            payload["stage"] = spec.stage
        atomic_write_json(path, payload)

    def _read_spec(self, job_id: str) -> JobSpec:
        path = job_dir(self._env, job_id) / "spec.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise FleetError("invalid_spec") from None
        return _spec_from(payload)

    def _write_json(self, job_id: str, name: str, payload: dict[str, Any]) -> None:
        path = job_dir(self._env, job_id) / name
        atomic_write_json(path, redact(payload))

    def _events_path(self, job_id: str) -> Path:
        return job_dir(self._env, job_id) / "events.jsonl"


def _with_local_caps(spec: JobSpec, limits: LaneLimits) -> JobSpec:
    """Fill missing local bounds; minutes stay unset unless explicitly capped."""
    minutes = (
        spec.max_minutes
        if spec.max_minutes is not None or limits.max_minutes is None
        else limits.max_minutes
    )
    files = limits.max_files if spec.max_files is None else spec.max_files
    if minutes == spec.max_minutes and files == spec.max_files:
        return spec
    return replace(spec, max_minutes=minutes, max_files=files)


def _local_id() -> str:
    return "local-" + secrets.token_hex(8)


def _base_record(job_id: str, spec: JobSpec, decision: Decision) -> dict[str, Any]:
    return {
        "id": job_id,
        "lane": decision.lane,
        "status": decision.status,
        "reasons": list(decision.reasons),
        "model": spec.model,
        "stage": spec.stage,
        "spec": spec.to_json(),
        "changes": [],
    }


def _spec_from(payload: object) -> JobSpec:
    if not isinstance(payload, dict):
        raise FleetError("invalid_spec")
    raw_links = payload.get("links", [])
    if raw_links is None:
        raw_links = []
    if not isinstance(raw_links, list):
        raise FleetError("invalid_spec")
    links = raw_links
    try:
        spec = JobSpec(
            repo=str(payload["repo"]),
            prompt=str(payload["prompt"]),
            base=_optional_str(payload.get("base")),
            branch=_optional_str(payload.get("branch")),
            phase=_optional_str(payload.get("phase")),
            plan=_optional_str(payload.get("plan")),
            agent=_optional_str(payload.get("agent")),
            approval_mode=_optional_str(payload.get("approval_mode")),
            push=_optional_bool(payload.get("push")),
            pr=_optional_bool(payload.get("pr")),
            notify=_optional_bool(payload.get("notify")),
            max_minutes=_optional_int(payload.get("max_minutes")),
            max_files=_optional_int(payload.get("max_files")),
            max_attempts=_optional_int(payload.get("max_attempts")),
            links=tuple(str(item) for item in links),
            slash_command=_optional_str(payload.get("slash_command")),
            slash_args=_optional_str(payload.get("slash_args")),
            raw_prompt=_optional_bool(payload.get("raw_prompt")),
            hitl_test=_optional_bool(payload.get("hitl_test")),
            events=_optional_bool(payload.get("events")),
            model=_optional_str(payload.get("model")),
            stage=_optional_str(payload.get("stage")),
        )
    except (KeyError, TypeError):
        raise FleetError("invalid_spec") from None
    validate_spec(spec)
    return spec


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError
    return value


def _record_uses_xai(record: Mapping[str, Any]) -> bool:
    model = record.get("model")
    return isinstance(model, str) and (
        model == "xai-oauth" or model.startswith("xai-oauth/")
    )


__all__ = [
    "BoxSnapshot",
    "Decision",
    "LaneLimits",
    "LaneRouter",
    "LocalExecutor",
    "StubLocalExecutor",
    "choose_lane",
    "free_memory_bytes",
]

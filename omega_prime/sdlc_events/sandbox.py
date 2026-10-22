"""Sandbox lifecycle events, the L14 readiness gate and the L15 exit rule.

Sandboxes are OpenShell/Podman sandboxes run for Omega Prime work (00045).
Events come from the sandbox-control tools and from the Podman events source
(L13). They become CloudEvents for the outbox and task-row updates through
``on_sandbox_event``.

Rows:

* every sandbox gets its own row ``gb:sbx-<name>`` (``working`` until
  ``removed`` marks it ``done``; blocking lifecycles mark it ``blocked``);
* a blocking lifecycle (not_ready, unhealthy, oom, non-zero or unknown exit)
  also marks the *owning* task ``blocked``. A sandbox event never marks an
  owning task ``done``, because only the work itself can decide that.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from omega_prime.sdlc_events.gate import GateResult
from omega_prime.sdlc_events.handlers import HandlerResult, TaskStorePort, _run_claimed
from omega_prime.sdlc_events.idempotency import IdempotencyStore
from omega_prime.sdlc_events.types import DeliveryId

SANDBOX_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
CLOUDEVENT_SOURCE_PREFIX = "omega-prime/sandbox"


class SandboxLifecycle(StrEnum):
    CREATED = "created"
    STARTED = "started"
    READY = "ready"
    NOT_READY = "not_ready"
    UNHEALTHY = "unhealthy"
    OOM = "oom"
    EXITED = "exited"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class SandboxEvent:
    delivery: DeliveryId
    sandbox: str
    lifecycle: SandboxLifecycle
    source: str
    task_id: str | None = None
    exit_code: int | None = None
    reasons: tuple[str, ...] = ()
    at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not SANDBOX_NAME_RE.match(self.sandbox):
            raise ValueError(f"invalid sandbox name: {self.sandbox!r}")
        if not self.source or not self.source.strip():
            raise ValueError("source required")

    @property
    def subject(self) -> str:
        return f"op.sbx.{self.sandbox}.{self.lifecycle.value}"

    @property
    def blocking(self) -> bool:
        """True when this event must block the owning task (L14/L15)."""
        if self.lifecycle in {
            SandboxLifecycle.NOT_READY,
            SandboxLifecycle.UNHEALTHY,
            SandboxLifecycle.OOM,
        }:
            return True
        if self.lifecycle is SandboxLifecycle.EXITED:
            return self.exit_code != 0
        return False

    def to_cloudevent(self) -> dict[str, Any]:
        """CloudEvents 1.0 structured-mode envelope (JSON)."""
        stamp = datetime.fromtimestamp(self.at, tz=UTC).isoformat()
        data: dict[str, Any] = {
            "sandbox": self.sandbox,
            "lifecycle": self.lifecycle.value,
            "task_id": self.task_id,
            "exit_code": self.exit_code,
            "reasons": list(self.reasons),
        }
        return {
            "specversion": "1.0",
            "id": self.delivery.value,
            "source": f"{CLOUDEVENT_SOURCE_PREFIX}/{self.source}",
            "type": f"op.sbx.{self.lifecycle.value}",
            "subject": self.subject,
            "time": stamp,
            "datacontenttype": "application/json",
            "data": data,
        }


@dataclass(frozen=True, slots=True)
class ReadinessInput:
    """Inputs to the L14 gate. ``None`` for a probe means it is not configured."""

    container_ready: bool
    broker_required: bool = False
    broker_relay_ready: bool = False
    tools_connected: bool | None = None
    skills_connected: bool | None = None


def evaluate_sandbox_ready(inp: ReadinessInput) -> GateResult:
    """L14: a sandbox is ready only when every dependency is connected.

    A missing tools/skills probe fails closed: what "connected" means is the
    open #61 decision, and a sandbox that cannot reach tools or skills must
    not be handed work as if it were ready.
    """
    reasons: list[str] = []
    if not inp.container_ready:
        reasons.append("container_not_ready")
    if inp.broker_required and not inp.broker_relay_ready:
        reasons.append("broker_relay_not_ready")
    for label, value in (
        ("tools", inp.tools_connected),
        ("skills", inp.skills_connected),
    ):
        if value is None:
            reasons.append(f"{label}_probe_missing(#61)")
        elif not value:
            reasons.append(f"{label}_not_connected")
    return GateResult(ok=not reasons, reasons=tuple(reasons))


def readiness_event(
    sandbox: str,
    inp: ReadinessInput,
    *,
    source: str,
    task_id: str | None = None,
    attempt: str = "1",
) -> SandboxEvent:
    """Turn a readiness evaluation into a ready/not_ready event."""
    gate = evaluate_sandbox_ready(inp)
    lifecycle = SandboxLifecycle.READY if gate.ok else SandboxLifecycle.NOT_READY
    return SandboxEvent(
        delivery=DeliveryId(f"{source}:{sandbox}:{lifecycle.value}:{attempt}"),
        sandbox=sandbox,
        lifecycle=lifecycle,
        source=source,
        task_id=task_id,
        reasons=gate.reasons,
    )


def sandbox_row_id(sandbox: str) -> str:
    return f"gb:sbx-{sandbox}"


def _note(event: SandboxEvent) -> str:
    parts = [f"sandbox {event.sandbox} {event.lifecycle.value}"]
    if event.exit_code is not None:
        parts.append(f"exit={event.exit_code}")
    elif event.lifecycle is SandboxLifecycle.EXITED:
        parts.append("exit=unknown")
    if event.reasons:
        parts.append("reasons=" + ",".join(event.reasons))
    parts.append(f"via {event.source}")
    return " ".join(parts)


def on_sandbox_event(
    event: SandboxEvent,
    *,
    store: TaskStorePort,
    idem: IdempotencyStore,
) -> HandlerResult:
    """Apply one sandbox lifecycle event to the task rows, once per delivery."""

    def work() -> HandlerResult:
        note = _note(event)
        row = sandbox_row_id(event.sandbox)
        if event.blocking:
            store.upsert_note(row, "blocked", note)
            if event.task_id:
                store.upsert_note(event.task_id, "blocked", note)
            return HandlerResult(action="blocked", detail=note)
        if event.lifecycle is SandboxLifecycle.REMOVED:
            store.upsert_note(row, "done", note)
            return HandlerResult(action="removed", detail=note)
        store.upsert_note(row, "working", note)
        return HandlerResult(action="noted", detail=note)

    return _run_claimed(event, idem, work)


def event_from_mapping(raw: Mapping[str, Any]) -> SandboxEvent:
    """Rebuild an event from its CloudEvents form (outbox replay)."""
    data = raw.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("cloudevent data must be an object")
    source = str(raw.get("source", ""))
    prefix = CLOUDEVENT_SOURCE_PREFIX + "/"
    if not source.startswith(prefix):
        raise ValueError(f"unexpected cloudevent source: {source!r}")
    stamp = datetime.fromisoformat(str(raw["time"])).timestamp()
    exit_code = data.get("exit_code")
    return SandboxEvent(
        delivery=DeliveryId(str(raw["id"])),
        sandbox=str(data["sandbox"]),
        lifecycle=SandboxLifecycle(str(data["lifecycle"])),
        source=source[len(prefix) :],
        task_id=data.get("task_id") or None,
        exit_code=None if exit_code is None else int(exit_code),
        reasons=tuple(str(r) for r in data.get("reasons") or ()),
        at=stamp,
    )

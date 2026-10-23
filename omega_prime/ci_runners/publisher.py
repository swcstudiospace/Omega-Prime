"""Redacting CloudEvent publisher port for ``omega.ci.*`` events.

Every adapter redacts with the known transient secrets (JIT configs, tokens)
plus the shared secret patterns, then refuses to emit anything that still
contains a known secret (for example as a dict key) or is not plain JSON.
"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from omega_prime.credentials.redact import redact_value

SOURCE = "omega-prime/ci-runners"
RUNNER_KINDS = frozenset(
    {
        "demand",
        "launch_requested",
        "launched",
        "busy",
        "idle",
        "reaped",
        "exited",
        "error",
        "finished",  # job completed; slot held until the process exits
        "launch_backoff",
        "launch_retry",
        "launch_circuit_open",
        "launch_circuit_closed",
        "refused",  # public-repo fork guard said no (reason only, no payload)
    }
)
DESKTOP_KINDS = frozenset({"wake_requested", "woke"})


class UnsafeEventError(RuntimeError):
    """The event could not be made safe to emit; nothing was written."""


class PublisherUnavailable(RuntimeError):
    """The selected publisher cannot run in this install."""


def event_type(kind: str) -> str:
    if kind in RUNNER_KINDS:
        return f"omega.ci.runner.{kind}"
    if kind in DESKTOP_KINDS:
        return f"omega.ci.desktop.{kind}"
    raise ValueError(f"unknown ci event kind {kind!r}")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CloudEvent:
    type: str
    data: Mapping[str, Any]
    subject: str | None = None
    source: str = SOURCE
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    time: str = field(default_factory=_now)
    specversion: str = "1.0"

    @classmethod
    def of(
        cls, kind: str, data: Mapping[str, Any], *, subject: str | None = None
    ) -> CloudEvent:
        return cls(type=event_type(kind), data=dict(data), subject=subject)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "specversion": self.specversion,
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "time": self.time,
            "datacontenttype": "application/json",
            "data": dict(self.data),
        }
        if self.subject is not None:
            out["subject"] = self.subject
        return out


def redact_event(
    event: CloudEvent, known_secrets: Collection[str] = ()
) -> dict[str, Any]:
    """Return the safe dict form or raise :class:`UnsafeEventError`."""
    secrets = tuple(s for s in known_secrets if isinstance(s, str) and s)
    safe = redact_value(event.to_dict(), extra=secrets)
    try:
        line = json.dumps(safe, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise UnsafeEventError(f"{event.type}: payload is not plain JSON") from None
    if any(secret in line for secret in secrets):
        raise UnsafeEventError(f"{event.type}: a secret survived redaction")
    return safe


class EventPublisher(Protocol):
    def publish(
        self, event: CloudEvent, *, known_secrets: Collection[str] = ()
    ) -> None: ...


class NullPublisher:
    """Checks safety, then drops the event."""

    def __init__(self) -> None:
        self.dropped = 0

    def publish(
        self, event: CloudEvent, *, known_secrets: Collection[str] = ()
    ) -> None:
        redact_event(event, known_secrets)
        self.dropped += 1


class JsonlPublisher:
    """Append one redacted CloudEvent per line (owner-only file)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()

    def publish(
        self, event: CloudEvent, *, known_secrets: Collection[str] = ()
    ) -> None:
        line = json.dumps(redact_event(event, known_secrets), sort_keys=True) + "\n"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)


Sink = Callable[[Mapping[str, Any]], Any]


class EventStreamPublisher:
    """Hands the redacted CloudEvent dict to an injected mesh producer sink."""

    def __init__(self, sink: Sink) -> None:
        self._sink = sink

    def publish(
        self, event: CloudEvent, *, known_secrets: Collection[str] = ()
    ) -> None:
        self._sink(redact_event(event, known_secrets))


def make_publisher(
    kind: str, events_path: Path, *, sink: Sink | None = None
) -> EventPublisher:
    if kind == "null":
        return NullPublisher()
    if kind == "jsonl":
        return JsonlPublisher(events_path)
    if kind == "eventstream":
        try:
            importlib.import_module("omega_prime.eventstream")
        except ImportError:
            raise PublisherUnavailable(
                "OMEGA_CI_PUBLISHER=eventstream needs omega_prime.eventstream "
                "(task 00046); use jsonl until it lands"
            ) from None
        if sink is None:
            raise PublisherUnavailable(
                "OMEGA_CI_PUBLISHER=eventstream needs a mesh producer sink; "
                "use jsonl until the 00046 producer is wired"
            )
        return EventStreamPublisher(sink)
    raise PublisherUnavailable(f"unknown publisher {kind!r}")


__all__ = [
    "DESKTOP_KINDS",
    "RUNNER_KINDS",
    "SOURCE",
    "CloudEvent",
    "EventPublisher",
    "EventStreamPublisher",
    "JsonlPublisher",
    "NullPublisher",
    "PublisherUnavailable",
    "UnsafeEventError",
    "event_type",
    "make_publisher",
    "redact_event",
]

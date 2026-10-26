"""Injected event-stream telemetry API.

Public methods: ``publish(count=1, latency=None, traceparent=None, attributes=None)``,
``outbox(depth, bytes, oldest_age)``, ``consume(outcome, latency, count=1,
traceparent=None, attributes=None)``, ``retry(count=1)``, ``dead_letter(count=1)``,
``handler_in_flight(value)``, ``connection_state(state)``, ``readiness(connected,
outbox_bytes, max_outbox_bytes, circuit_state)`` (returns a bool), ``liveness()``
(always true while the process is executing), and ``export()`` (one explicit
export request). Metrics, tracer and export callable are supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.taxonomy import EVENT_TYPES

_CONSUME_OUTCOMES = frozenset({"ack", "handled", "retry", "dlq", "authentication"})
_CONNECTION_STATES = frozenset({"connected", "disconnected", "unhealthy"})
_SPAN_ATTRIBUTE_VALUES = {
    "ce_type": frozenset(EVENT_TYPES),
    "subscription_type": frozenset({"Exclusive", "Failover", "Shared", "Key_Shared"}),
}


class MetricSink(Protocol):
    """Small metric sink compatible with injected telemetry adapters."""

    def increment(
        self, name: str, value: int, attributes: Mapping[str, str]
    ) -> None: ...

    def observe(
        self, name: str, value: float, attributes: Mapping[str, str]
    ) -> None: ...

    def set(self, name: str, value: float, attributes: Mapping[str, str]) -> None: ...


class Tracer(Protocol):
    """Tracer starts a span and receives its remote parent trace context."""

    def start_span(
        self, name: str, traceparent: str | None, attributes: Mapping[str, Any]
    ) -> Any: ...


class ExportOnce(Protocol):
    def __call__(self) -> Any: ...


class EventStreamObserver:
    """Record event-driven telemetry; no exporter lifecycle is started here."""

    def __init__(
        self,
        metrics: MetricSink,
        tracer: Tracer,
        export_once: ExportOnce,
        *,
        known_secrets: tuple[str, ...] = (),
    ) -> None:
        self._metrics = metrics
        self._tracer = tracer
        self._export_once = export_once
        self._known_secrets = known_secrets

    def _span(
        self,
        operation: str,
        traceparent: str | None,
        attributes: Mapping[str, Any] | None,
    ) -> None:
        supplied = dict(attributes or {})
        safe: dict[str, str] = {}
        for key, allowed_values in _SPAN_ATTRIBUTE_VALUES.items():
            value = supplied.get(key)
            if not isinstance(value, str):
                continue
            redacted = redact_value(value, self._known_secrets)
            if redacted in allowed_values:
                safe[key] = redacted
        self._tracer.start_span(operation, traceparent, safe)

    def publish(
        self,
        count: int = 1,
        latency: float | None = None,
        traceparent: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        self._metrics.increment("eventstream.publish.count", count, {})
        if latency is not None:
            self._metrics.observe("eventstream.publish.latency", latency, {})
        self._span("eventstream.publish", traceparent, attributes)

    def outbox(self, depth: int, bytes: int, oldest_age: float) -> None:
        self._metrics.set("outbox.depth", depth, {})
        self._metrics.set("outbox.bytes", bytes, {})
        self._metrics.set("outbox.oldest_age", oldest_age, {})

    def consume(
        self,
        outcome: str,
        latency: float,
        count: int = 1,
        traceparent: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        labels = {"outcome": self._safe_outcome(outcome)}
        self._metrics.increment("consume.count", count, labels)
        self._metrics.observe("consume.latency", latency, labels)
        self._span("eventstream.consume", traceparent, attributes)

    def _safe_outcome(self, outcome: str) -> str:
        safe = str(redact_value(outcome, self._known_secrets))
        return safe if safe in _CONSUME_OUTCOMES else "unknown"

    def retry(self, count: int = 1) -> None:
        self._metrics.increment("retry.count", count, {})

    def dead_letter(self, count: int = 1) -> None:
        self._metrics.increment("dlq.count", count, {})

    def handler_in_flight(self, value: int) -> None:
        self._metrics.set("handler.in_flight", value, {})

    def connection_state(self, state: str) -> None:
        candidate = str(redact_value(state, self._known_secrets))
        safe_state = candidate if candidate in _CONNECTION_STATES else "unknown"
        self._metrics.set(
            "connection.state", float(safe_state == "connected"), {"state": safe_state}
        )

    @staticmethod
    def readiness(
        connected: bool, outbox_bytes: int, max_outbox_bytes: int, circuit_state: str
    ) -> bool:
        """Return readiness on demand; the 80% threshold is strict."""
        return (
            connected
            and max_outbox_bytes > 0
            and outbox_bytes * 5 < max_outbox_bytes * 4
            and circuit_state == "closed"
        )

    @staticmethod
    def liveness() -> bool:
        """Process liveness is independent of broker/readiness state."""
        return True

    def export(self) -> Any:
        """Request exactly one export; never called implicitly."""
        return self._export_once()


__all__ = ["EventStreamObserver", "ExportOnce", "MetricSink", "Tracer"]

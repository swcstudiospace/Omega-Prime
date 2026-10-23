"""Portable delayed retry, singleton handoff and dead-letter routing."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.backoff import Rng, decorrelated_jitter
from omega_prime.eventstream.envelope import (
    CloudEvent,
    Delivery,
    PublishReceipt,
    PublishRequest,
)
from omega_prime.eventstream.errors import (
    AuthenticationError,
    PermanentError,
    SchemaError,
)
from omega_prime.eventstream.observability import EventStreamObserver
from omega_prime.eventstream.ports import (
    Ack,
    DeadLetter,
    HandlerOutcome,
    Publisher,
    Retry,
)
from omega_prime.eventstream.taxonomy import derive_key, get_event_type

_LOG = logging.getLogger(__name__)


def _safe_reason(reason: object, secrets: tuple[str, ...]) -> str:
    return (str(redact_value(str(reason), secrets)).strip() or "unspecified failure")[
        :256
    ]


def _matches(receipt: PublishReceipt, request: PublishRequest) -> bool:
    return receipt.event_id == request.event.id and receipt.topic == request.topic


class RetryRouter:
    """Apply handler outcomes; durable destination receipt precedes source ack."""

    def __init__(
        self,
        publisher: Publisher,
        subscription: str,
        *,
        observer: EventStreamObserver | None = None,
        rng: Rng = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        known_secrets: tuple[str, ...] = (),
        subscription_type: str = "Key_Shared",
        on_authentication_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.publisher, self.subscription = publisher, subscription
        self.observer, self.rng, self.clock = observer, rng, clock
        self.known_secrets, self.subscription_type = known_secrets, subscription_type
        self.on_authentication_failure = on_authentication_failure

    def _history_for(self, delivery: Delivery) -> list[dict[str, Any]]:
        rows = delivery.properties.get("omega_failure_history", ())
        if isinstance(rows, str):
            # Pulsar message properties are strings; history is JSON-encoded.
            try:
                rows = json.loads(rows)
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
        if not isinstance(rows, (list, tuple)):
            return []
        clean: list[dict[str, Any]] = []
        for row in rows[-4:]:
            if isinstance(row, Mapping):
                try:
                    attempt = int(row.get("attempt", 0))
                except (TypeError, ValueError):
                    continue
                reason = _safe_reason(row.get("reason", ""), self.known_secrets)
                if 1 <= attempt <= 5 and reason:
                    clean.append({"attempt": attempt, "reason": reason})
        return clean

    async def handle(
        self,
        delivery: Delivery,
        outcome: HandlerOutcome | BaseException,
        *,
        attempt: int | None = None,
        aggregate_key: str | None = None,
        settle_before_ack: Callable[[str], Awaitable[bool]] | None = None,
        latency: float = 0.0,
    ) -> str:
        props, event = delivery.properties, delivery.event
        current = max(
            1, attempt if attempt is not None else int(props.get("omega_attempt", 1))
        )
        traceparent = (
            event.model_extra.get("traceparent") if event.model_extra else None
        )
        trace_id = str(traceparent).split("-")[1] if traceparent else ""
        safe_event_id = str(redact_value(event.id, extra=self.known_secrets))[:128]
        safe_subscription = str(
            redact_value(self.subscription, extra=self.known_secrets)
        )[:64]
        log_fields = {
            "ce_id": safe_event_id,
            "ce_type": event.type,
            "subscription": safe_subscription,
            "attempt": current,
            "trace_id": trace_id,
        }
        if isinstance(outcome, AuthenticationError):
            _LOG.warning("eventstream authentication failure", extra=log_fields)
            if self.observer:
                self.observer.connection_state("unhealthy")
                self.observer.consume(
                    "authentication",
                    latency,
                    traceparent=traceparent,
                    attributes={"subscription": self.subscription},
                )
            if self.on_authentication_failure is not None:
                await self.on_authentication_failure()
            return "unsettled"
        if isinstance(outcome, Ack):
            settled = settle_before_ack is None or await settle_before_ack("ack")
            if settled:
                await delivery.acknowledge()
                if self.observer:
                    self.observer.consume(
                        "ack",
                        latency,
                        traceparent=traceparent,
                        attributes={"subscription": self.subscription},
                    )
                _LOG.info("eventstream delivery acknowledged", extra=log_fields)
            return "ack"
        if isinstance(outcome, BaseException):
            if isinstance(outcome, (PermanentError, SchemaError)):
                await self._dead_letter(
                    delivery,
                    current,
                    _safe_reason(outcome, self.known_secrets),
                    aggregate_key,
                    settle_before_ack=settle_before_ack,
                )
                return "dlq"
            retry_outcome: Retry | DeadLetter = Retry(
                0,
                _safe_reason(outcome, self.known_secrets),
            )
        else:
            retry_outcome = outcome
        if isinstance(retry_outcome, DeadLetter) or current >= 5:
            await self._dead_letter(
                delivery,
                current,
                _safe_reason(retry_outcome.reason, self.known_secrets),
                aggregate_key,
                settle_before_ack=settle_before_ack,
            )
            return "dlq"
        reason = _safe_reason(retry_outcome.reason, self.known_secrets)
        history = self._history_for(delivery)
        history.append({"attempt": current, "reason": reason})
        previous = float(props.get("omega_retry_previous", 1.0))
        delay = min(
            600.0,
            max(float(retry_outcome.after), decorrelated_jitter(previous, self.rng)),
        )
        origin = str(props.get("omega_origin_topic", delivery.topic))
        request_props = {
            **dict(props),
            "omega_attempt": current + 1,
            "omega_origin_topic": origin,
            "omega_last_error": reason,
            "omega_retry_previous": delay,
            "omega_failure_history": tuple(history),
        }
        if self.subscription_type in {"Failover", "Exclusive"}:
            request_props["omega_retry_target"] = self.subscription
        request = PublishRequest(
            event=event,
            topic=f"persistent://omega/system/retry.{self.subscription}",
            key=delivery.key,
            sequence_id=current,
            producer_name=f"omega-eventstream-{event.id}",
            deliver_after=self.clock() + timedelta(seconds=delay),
            single_use=True,
            properties=request_props,
        )
        receipt = await self.publisher.publish(request)
        if not _matches(receipt, request):
            raise RuntimeError("retry destination receipt does not match request")
        settled = settle_before_ack is None or await settle_before_ack("retry")
        if settled:
            await delivery.acknowledge()
            if self.observer:
                self.observer.retry()
                self.observer.publish(
                    traceparent=traceparent, attributes={"topic": request.topic}
                )
                self.observer.consume(
                    "retry",
                    latency,
                    traceparent=traceparent,
                    attributes={"subscription": self.subscription},
                )
            _LOG.info("eventstream retry durably published", extra=log_fields)
        return "retry"

    async def _dead_letter(
        self,
        delivery: Delivery,
        attempt: int,
        reason: str,
        aggregate_key: str | None,
        *,
        settle_before_ack: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        event, data = delivery.event, dict(delivery.event.data)
        history = self._history_for(delivery)
        if not history or history[-1]["attempt"] != attempt:
            history.append({"attempt": attempt, "reason": reason})
        if {"repo", "number", "head_sha", "actor"}.issubset(data):
            payload = {
                "kind": "pr",
                "repo": data["repo"],
                "number": data["number"],
                "head_sha": data["head_sha"],
                "actor": data["actor"],
                "reason": reason,
            }
            key = f"{data['repo']}#{data['number']}"
        else:
            origin = str(delivery.properties.get("omega_origin_topic", delivery.topic))
            aggregate = aggregate_key or delivery.key
            payload = {
                "kind": "dlq",
                "origin_topic": origin,
                "aggregate_key": aggregate,
                "source_ce_id": event.id,
                "reason": reason,
                "history": history,
            }
            key = "dlq:" + json.dumps(
                [origin, aggregate],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        traceparent = (
            event.model_extra.get("traceparent") if event.model_extra else None
        )
        blocked = CloudEvent(
            specversion="1.0",
            id=f"dlq:{self.subscription}:{event.id}",
            source=event.source,
            type="omega.control.signal.blocked",
            datacontenttype="application/json",
            dataschema="urn:omega:schema:omega.control.signal.blocked:1",
            data=payload,
            **({"traceparent": traceparent} if traceparent else {}),
        )
        request = PublishRequest(
            event=blocked,
            topic=f"persistent://omega/system/dlq.{self.subscription}",
            key=key,
            sequence_id=attempt,
            # Per-event producer name + single_use so Pulsar dedup cannot drop a
            # later dead-letter that shares attempt=1 with an earlier one.
            producer_name=f"omega-eventstream-dlq-{self.subscription}-{event.id}",
            single_use=True,
            properties={
                **{
                    key: value
                    for key, value in dict(delivery.properties).items()
                    if key != "omega_retry_target"
                },
                "omega_attempt": attempt,
                "omega_origin_topic": str(
                    delivery.properties.get("omega_origin_topic", delivery.topic)
                ),
                "omega_source_ce_id": event.id,
                "omega_last_error": reason,
                "omega_failure_history": tuple(history),
            },
        )
        receipt = await self.publisher.publish(request)
        if not _matches(receipt, request):
            raise RuntimeError("dead-letter destination receipt does not match request")
        settled = settle_before_ack is None or await settle_before_ack("dlq")
        if not settled:
            return
        await delivery.acknowledge()
        if self.observer:
            self.observer.dead_letter()
            self.observer.publish(
                traceparent=traceparent,
                attributes={"topic": request.topic},
            )
        trace_id = str(traceparent).split("-")[1] if traceparent else ""
        _LOG.warning(
            "eventstream delivery dead-lettered",
            extra={
                "ce_id": str(redact_value(event.id, extra=self.known_secrets))[:128],
                "ce_type": event.type,
                "subscription": str(
                    redact_value(self.subscription, extra=self.known_secrets)
                )[:64],
                "attempt": attempt,
                "trace_id": trace_id,
            },
        )


class SingletonRetryHandoff:
    """Republish targeted delayed messages to a singleton's original input."""

    def __init__(
        self,
        publisher: Publisher,
        target: str,
        observer: EventStreamObserver | None = None,
    ) -> None:
        self.publisher, self.target, self.observer = publisher, target, observer

    async def handle(self, delivery: Delivery) -> str:
        props = delivery.properties
        if props.get("omega_retry_target") != self.target:
            await delivery.acknowledge()
            if self.observer:
                self.observer.consume(
                    "filtered", 0.0, attributes={"subscription": self.target}
                )
            return "filtered"
        origin = str(props.get("omega_origin_topic", ""))
        if not origin:
            raise ValueError("retry handoff lacks original topic")
        attempt = int(props.get("omega_attempt", 1))
        request = PublishRequest(
            event=delivery.event,
            topic=origin,
            key=delivery.key,
            sequence_id=attempt,
            producer_name=f"omega-eventstream-{delivery.event.id}",
            single_use=True,
            properties=dict(props),
        )
        receipt = await self.publisher.publish(request)
        if not _matches(receipt, request):
            raise RuntimeError("retry handoff receipt does not match request")
        await delivery.acknowledge()
        if self.observer:
            traceparent = (
                delivery.event.model_extra.get("traceparent")
                if delivery.event.model_extra
                else None
            )
            self.observer.retry()
            self.observer.publish(
                traceparent=traceparent, attributes={"topic": request.topic}
            )
        return "republished"


class DlqTriage:
    """Re-emit durable DLQ blocked signals before acknowledging triage input."""

    def __init__(
        self, publisher: Publisher, observer: EventStreamObserver | None = None
    ) -> None:
        self.publisher, self.observer = publisher, observer

    async def handle(self, delivery: Delivery) -> None:
        event = delivery.event
        attempt = int(delivery.properties.get("omega_attempt", 1))
        request = PublishRequest(
            event=event,
            topic=get_event_type(event.type).topic,
            key=derive_key(event.type, event.data),
            sequence_id=attempt,
            producer_name=f"omega-eventstream-{event.id}",
            properties=dict(delivery.properties),
        )
        receipt = await self.publisher.publish(request)
        if not _matches(receipt, request):
            raise RuntimeError("DLQ triage receipt does not match request")
        await delivery.acknowledge()
        if self.observer:
            traceparent = (
                event.model_extra.get("traceparent") if event.model_extra else None
            )
            self.observer.publish(
                traceparent=traceparent, attributes={"topic": request.topic}
            )

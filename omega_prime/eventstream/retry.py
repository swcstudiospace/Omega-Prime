"""Portable delayed retry, singleton handoff and dead-letter routing."""

from __future__ import annotations

import json
import logging
import math
import re
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


#: Highest business attempt carried in ``omega_attempt`` (dead-letter at five).
MAX_BUSINESS_ATTEMPTS = 5
_DECIMAL = re.compile(r"[0-9]{1,3}")


class InvalidRetryMetadata(SchemaError):
    """Transport retry properties are malformed and cannot drive retry policy."""


def parse_retry_previous(properties: Mapping[str, Any]) -> float:
    """Return the previous retry delay (default 1.0) after validating it."""
    previous = properties.get("omega_retry_previous")
    if previous is None:
        return 1.0
    if isinstance(previous, bool) or not isinstance(previous, (int, float, str)):
        raise InvalidRetryMetadata("omega_retry_previous must be a positive number")
    try:
        value = float(previous)
    except (TypeError, ValueError, OverflowError):
        raise InvalidRetryMetadata(
            "omega_retry_previous must be a positive number"
        ) from None
    if not math.isfinite(value) or value <= 0:
        raise InvalidRetryMetadata("omega_retry_previous must be a positive number")
    return value


def parse_retry_attempt(properties: Mapping[str, Any]) -> int:
    """Validate retry transport properties and return the business attempt.

    Pulsar message properties are arbitrary strings, so ``omega_attempt`` and
    ``omega_retry_previous`` are checked before they reach any arithmetic.
    Raises :class:`InvalidRetryMetadata` for values the router cannot honour.
    """
    raw = properties.get("omega_attempt", 1)
    if isinstance(raw, bool):
        raise InvalidRetryMetadata("omega_attempt must be an integer from 1 to 5")
    if isinstance(raw, int):
        attempt = raw
    elif isinstance(raw, str) and _DECIMAL.fullmatch(raw):
        attempt = int(raw)
    else:
        raise InvalidRetryMetadata("omega_attempt must be an integer from 1 to 5")
    if not 1 <= attempt <= MAX_BUSINESS_ATTEMPTS:
        raise InvalidRetryMetadata("omega_attempt must be an integer from 1 to 5")
    parse_retry_previous(properties)
    return attempt


#: Retry-routing transport properties that never ride on a dead letter. A
#: dead letter is terminal, so a delay hint or singleton target is meaningless
#: there, and a malformed ``omega_retry_previous`` copied onto it would make
#: :class:`DlqTriage` reject the blocked signal and strand it unsettled.
RETRY_ONLY_PROPERTIES = frozenset({"omega_retry_target", "omega_retry_previous"})


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
        current = max(1, attempt if attempt is not None else parse_retry_attempt(props))
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
                    latency=latency,
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
                latency=latency,
            )
            return "dlq"
        reason = _safe_reason(retry_outcome.reason, self.known_secrets)
        history = self._history_for(delivery)
        history.append({"attempt": current, "reason": reason})
        # Consumers dead-letter malformed metadata before claiming; direct
        # callers that pass ``attempt`` explicitly fall back to the base delay.
        try:
            previous = parse_retry_previous(props)
        except InvalidRetryMetadata:
            previous = 1.0
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
        latency: float = 0.0,
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
                    if key not in RETRY_ONLY_PROPERTIES
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
            # One consume count per delivery, with the measured handler time,
            # so dead-lettered work is visible in consume totals and latency.
            self.observer.consume(
                "dlq",
                latency,
                traceparent=traceparent,
                attributes={"subscription": self.subscription},
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
        attempt = parse_retry_attempt(props)
        request = PublishRequest(
            event=delivery.event,
            topic=origin,
            key=delivery.key,
            sequence_id=attempt,
            # Subscription-scoped producer: two singletons on one input topic
            # (merge-executor and audit-projector on control/action) can hand
            # off the same event at the same attempt, and Pulsar dedup keys on
            # (producer name, sequence id) per topic.
            producer_name=(
                f"omega-eventstream-handoff-{self.target}-{delivery.event.id}"
            ),
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
        # Dead letters are terminal: retry-routing metadata is dropped rather
        # than validated, so a record written before RetryRouter stripped it
        # (or by another SDK) still re-emits instead of sticking unsettled.
        properties = {
            key: value
            for key, value in dict(delivery.properties).items()
            if key not in RETRY_ONLY_PROPERTIES
        }
        attempt = parse_retry_attempt(properties)
        request = PublishRequest(
            event=event,
            topic=get_event_type(event.type).topic,
            key=derive_key(event.type, event.data),
            sequence_id=attempt,
            producer_name=f"omega-eventstream-{event.id}",
            # Per-event producer: release it after the send so triage never
            # accumulates one native producer per dead-lettered event.
            single_use=True,
            properties=properties,
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

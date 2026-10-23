from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from omega_prime.eventstream.backoff import create_with_backoff, decorrelated_jitter
from omega_prime.eventstream.envelope import (
    CloudEvent,
    Delivery,
    PublishReceipt,
    PublishRequest,
)
from omega_prime.eventstream.errors import AuthenticationError
from omega_prime.eventstream.observability import EventStreamObserver
from omega_prime.eventstream.ports import Ack, DeadLetter, Retry
from omega_prime.eventstream.retry import DlqTriage, RetryRouter, SingletonRetryHandoff
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type


class FixedRng:
    def __init__(self, sample: float) -> None:
        self.sample = sample

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.sample


class Publisher:
    def __init__(self, fail: bool = False) -> None:
        self.requests: list[PublishRequest] = []
        self.fail = fail

    async def publish(self, request: PublishRequest) -> PublishReceipt:
        self.requests.append(request)
        if self.fail:
            raise OSError("destination unavailable")
        return PublishReceipt(
            event_id=request.event.id,
            topic=request.topic,
            message_id="durable",
            acknowledged_at=datetime.now(UTC),
        )

    async def close(self) -> None:
        pass


def make_delivery(
    acknowledged: list[bool],
    *,
    properties: dict[str, Any] | None = None,
    event_id: str = "ce1",
) -> Delivery:
    event = CloudEvent.model_validate(
        {
            "specversion": "1.0",
            "id": event_id,
            "source": "urn:test",
            "type": "omega.task.state_changed",
            "datacontenttype": "application/json",
            "dataschema": schema_uri_for_type("omega.task.state_changed"),
            "data": {
                "task_id": "task-1",
                "previous_state": "queued",
                "state": "running",
                "sequence": 1,
                "actor": "test",
            },
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "omegakey": "task-1",
        }
    )

    async def ack() -> None:
        acknowledged.append(True)

    return Delivery(
        event=event,
        topic="persistent://omega/task/state",
        key="task-1",
        message_id="m1",
        properties=properties or {},
        ack=ack,
    )


def test_structured_logs_redact_secret_shaped_event_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        caplog.set_level("INFO")
        secret_shaped_id = "ghp" + "_" + "abcdefghijklmnop"
        router = RetryRouter(Publisher(), "gate")
        await router.handle(make_delivery([], event_id=secret_shaped_id), Ack())

        records = [
            record
            for record in caplog.records
            if record.name == "omega_prime.eventstream.retry"
        ]
        assert records
        assert getattr(records[-1], "ce_id", None) == "[REDACTED]"
        assert secret_shaped_id not in repr(records[-1].__dict__)

    asyncio.run(scenario())


def test_jitter_edges_and_cap_after_sampling() -> None:
    assert decorrelated_jitter(1, FixedRng(0)) == 1
    assert decorrelated_jitter(1, FixedRng(1)) == 3
    assert decorrelated_jitter(300, FixedRng(0.75)) == 600


def test_jitter_uses_uniform_method_on_injected_rng_object() -> None:
    class RecordingRng:
        def __init__(self) -> None:
            self.bounds: tuple[float, float] | None = None

        def uniform(self, low: float, high: float) -> float:
            self.bounds = (low, high)
            return 600.0

    rng = RecordingRng()
    assert decorrelated_jitter(300.0, rng) == 600.0
    assert rng.bounds == (1.0, 900.0)


def test_creation_backoff_retries_only_transient_io_with_injected_wait() -> None:
    async def scenario() -> None:
        calls = 0
        delays: list[float] = []

        async def create() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise OSError("temporary connection failure")
            return "resource"

        async def wait(delay: float) -> None:
            delays.append(delay)

        result = await create_with_backoff(
            create, attempts=3, wait=wait, rng=FixedRng(0)
        )
        assert result == "resource"
        assert calls == 3
        assert delays == [1.0, 1.0]

    asyncio.run(scenario())


def test_creation_backoff_does_not_retry_non_io_failure_or_exceed_five() -> None:
    async def scenario() -> None:
        calls = 0
        delays: list[float] = []

        async def invalid_configuration() -> str:
            nonlocal calls
            calls += 1
            raise ValueError("invalid resource configuration")

        async def wait(delay: float) -> None:
            delays.append(delay)

        with pytest.raises(ValueError, match="invalid resource configuration"):
            await create_with_backoff(invalid_configuration, wait=wait)
        assert calls == 1
        assert delays == []

        with pytest.raises(ValueError, match="between one and five"):
            await create_with_backoff(invalid_configuration, attempts=6, wait=wait)
        assert calls == 1

    asyncio.run(scenario())


def test_retry_publishes_durably_before_ack_and_preserves_identity() -> None:
    async def scenario() -> None:
        publisher = Publisher()
        acked: list[bool] = []
        router = RetryRouter(
            publisher,
            "gate",
            rng=FixedRng(0),
            clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )
        source = make_delivery(acked, properties={"custom_property": "preserved"})
        assert (
            await router.handle(source, Retry(after=9, reason="transient"), attempt=1)
            == "retry"
        )
        request = publisher.requests[0]
        assert request.topic == "persistent://omega/system/retry.gate"
        assert request.deliver_after == datetime(2026, 1, 1, 0, 0, 9, tzinfo=UTC)
        assert request.event.id == "ce1" and request.key == "task-1"
        assert request.event.model_extra == source.event.model_extra
        assert not {"omega_attempt", "omega_origin_topic", "omega_last_error"} & set(
            request.event.model_extra or {}
        )
        assert request.properties["custom_property"] == "preserved"
        assert request.properties["omega_attempt"] == 2
        assert request.properties["omega_origin_topic"] == source.topic
        assert request.properties["omega_last_error"] == "transient"
        assert request.properties["omega_failure_history"] == (
            {"attempt": 1, "reason": "transient"},
        )

    asyncio.run(scenario())


def test_retry_publish_failure_leaves_source_unacknowledged() -> None:
    async def scenario() -> None:
        acked: list[bool] = []
        router = RetryRouter(Publisher(fail=True), "gate", rng=FixedRng(0))
        with pytest.raises(OSError):
            await router.handle(
                make_delivery(acked), Retry(after=0, reason="down"), attempt=1
            )
        assert acked == []

    asyncio.run(scenario())


def test_fifth_failure_routes_non_pr_to_canonical_dlq_before_ack() -> None:
    async def scenario() -> None:

        publisher = Publisher()
        acked: list[bool] = []
        router = RetryRouter(publisher, "gate")
        source = make_delivery(acked, properties={"custom_property": "preserved"})
        assert (
            await router.handle(source, DeadLetter("bad payload"), attempt=5) == "dlq"
        )
        request = publisher.requests[0]
        assert request.properties["custom_property"] == "preserved"
        assert acked == [True]
        assert request.topic == "persistent://omega/system/dlq.gate"
        assert request.key == 'dlq:["persistent://omega/task/state","task-1"]'
        assert request.event.data["kind"] == "dlq"
        assert request.event.data["history"] == (
            {"attempt": 5, "reason": "bad payload"},
        )
        assert "repo" not in request.event.data and "number" not in request.event.data

    asyncio.run(scenario())


def test_singleton_retry_handoff_publishes_without_delay_and_ack_after_receipt() -> (
    None
):
    async def scenario() -> None:
        publisher = Publisher()
        acked: list[bool] = []
        source = make_delivery(
            acked,
            properties={
                "omega_retry_target": "merge",
                "omega_origin_topic": "persistent://omega/task/state",
                "omega_attempt": 3,
            },
        )
        assert (
            await SingletonRetryHandoff(publisher, "merge").handle(source)
            == "republished"
        )
        request = publisher.requests[0]
        assert request.topic == "persistent://omega/task/state"
        assert request.deliver_after is None
        assert request.properties["omega_attempt"] == 3
        assert acked == [True]

    asyncio.run(scenario())


def test_authentication_failure_is_unsettled_and_non_business_attempt() -> None:
    async def scenario() -> None:
        publisher = Publisher()
        acked: list[bool] = []
        router = RetryRouter(publisher, "gate")
        result = await router.handle(
            make_delivery(acked),
            AuthenticationError("auth failed"),
            attempt=1,
        )
        assert result == "unsettled"
        assert acked == [] and publisher.requests == []

    asyncio.run(scenario())


def test_dlq_publish_failure_leaves_source_unacknowledged() -> None:
    async def scenario() -> None:

        acked: list[bool] = []
        router = RetryRouter(Publisher(fail=True), "gate")
        with pytest.raises(OSError):
            await router.handle(make_delivery(acked), DeadLetter("poison"), attempt=1)
        assert acked == []

    asyncio.run(scenario())


def test_attempts_one_through_five_have_no_sixth_retry() -> None:
    async def scenario() -> None:
        publisher = Publisher()
        acked: list[bool] = []
        router = RetryRouter(publisher, "gate", rng=FixedRng(0))
        properties: dict[str, Any] = {}
        outcomes: list[str] = []
        for attempt in range(1, 6):
            result = await router.handle(
                make_delivery(acked, properties=properties),
                Retry(after=0, reason=f"failure {attempt}"),
            )
            outcomes.append(result)
            properties = dict(publisher.requests[-1].properties)
        assert outcomes == ["retry", "retry", "retry", "retry", "dlq"]
        assert len(publisher.requests) == 5
        assert publisher.requests[-1].event.model_dump()["data"]["history"] == [
            {"attempt": attempt, "reason": f"failure {attempt}"}
            for attempt in range(1, 6)
        ]

    asyncio.run(scenario())


def test_retry_redacts_known_secrets_and_emits_observer_transitions() -> None:
    class Observer:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def retry(self) -> None:
            self.calls.append("retry")

        def publish(self, **_kwargs: Any) -> None:
            self.calls.append("publish")

        def consume(self, outcome: str, *_args: Any, **_kwargs: Any) -> None:
            self.calls.append(outcome)

    async def scenario() -> None:
        observer_double = Observer()
        observer = cast(EventStreamObserver, observer_double)
        publisher = Publisher()
        acked: list[bool] = []
        router = RetryRouter(
            publisher,
            "gate",
            observer=observer,
            rng=FixedRng(0),
            known_secrets=("known-secret",),
        )
        await router.handle(
            make_delivery(acked),
            Retry(after=0, reason="failed with known-secret"),
            attempt=1,
        )
        assert (
            publisher.requests[0].properties["omega_last_error"]
            == "failed with [REDACTED]"
        )
        assert publisher.requests[0].properties["omega_failure_history"] == (
            {"attempt": 1, "reason": "failed with [REDACTED]"},
        )
        assert observer_double.calls == ["retry", "publish", "retry"]

    asyncio.run(scenario())


def test_pr_dlq_preserves_pr_kind_and_canonical_key() -> None:
    async def scenario() -> None:

        event = CloudEvent.model_validate(
            {
                "specversion": "1.0",
                "id": "pr-event",
                "source": "urn:test",
                "type": "omega.sdlc.pr.opened",
                "datacontenttype": "application/json",
                "dataschema": schema_uri_for_type("omega.sdlc.pr.opened"),
                "data": {
                    "repo": "Omega/prime",
                    "number": 75,
                    "head_sha": "abc",
                    "actor": "gate",
                    "title": "title",
                    "base_ref": "main",
                },
                "omegakey": "Omega/prime#75",
            }
        )
        acked: list[bool] = []

        async def ack() -> None:
            acked.append(True)

        source = Delivery(
            event=event,
            topic="persistent://omega/sdlc/pr",
            key="Omega/prime#75",
            message_id="pr-message",
            ack=ack,
        )
        publisher = Publisher()
        await RetryRouter(publisher, "gate").handle(
            source, DeadLetter("policy failure")
        )
        request = publisher.requests[0]
        assert request.key == "Omega/prime#75"
        assert request.event.data["kind"] == "pr"
        assert (
            request.event.data["repo"] == "Omega/prime"
            and request.event.data["number"] == 75
        )
        assert acked == [True]

    asyncio.run(scenario())


def test_retry_delay_floor_and_cap() -> None:
    async def scenario() -> None:
        publisher = Publisher()
        router = RetryRouter(publisher, "gate", rng=FixedRng(0))
        await router.handle(
            make_delivery([], properties={"omega_retry_previous": 1}),
            Retry(after=0, reason="floor"),
            attempt=1,
        )
        assert publisher.requests[-1].deliver_after is not None
        assert publisher.requests[-1].properties["omega_retry_previous"] == 1

        await router.handle(
            make_delivery([], properties={"omega_retry_previous": 1}),
            Retry(after=700, reason="cap"),
            attempt=1,
        )
        assert publisher.requests[-1].properties["omega_retry_previous"] == 600

    asyncio.run(scenario())


def test_singleton_retry_handoff_filters_non_target_and_leaves_failed_publish_unacked() -> (
    None
):
    async def scenario() -> None:
        filtered_publisher = Publisher()
        filtered_acked: list[bool] = []
        filtered = make_delivery(
            filtered_acked,
            properties={"omega_retry_target": "another-target"},
        )
        result = await SingletonRetryHandoff(filtered_publisher, "merge").handle(
            filtered
        )
        assert result == "filtered"
        assert filtered_acked == [True]
        assert filtered_publisher.requests == []

        failed_publisher = Publisher(fail=True)
        failed_acked: list[bool] = []
        targeted = make_delivery(
            failed_acked,
            properties={
                "omega_retry_target": "merge",
                "omega_origin_topic": "persistent://omega/task/state",
            },
        )
        with pytest.raises(OSError):
            await SingletonRetryHandoff(failed_publisher, "merge").handle(targeted)
        assert failed_acked == []

    asyncio.run(scenario())


def test_dlq_triage_publishes_blocked_signal_before_ack_and_keeps_failure_unacked() -> (
    None
):
    async def scenario() -> None:
        payload = {
            "kind": "dlq",
            "origin_topic": "persistent://omega/auth/session",
            "aggregate_key": 'host:"cli\\worker"',
            "source_ce_id": "source-event",
            "reason": "invalid event",
            "history": [{"attempt": 1, "reason": "schema rejected"}],
        }
        event = CloudEvent.model_validate(
            {
                "specversion": "1.0",
                "id": "blocked-event",
                "source": "urn:test",
                "type": "omega.control.signal.blocked",
                "datacontenttype": "application/json",
                "dataschema": schema_uri_for_type("omega.control.signal.blocked"),
                "data": payload,
                "omegakey": derive_key("omega.control.signal.blocked", payload),
            }
        )

        async def source(acked: list[bool]) -> Delivery:
            async def ack() -> None:
                acked.append(True)

            return Delivery(
                event=event,
                topic="persistent://omega/system/dlq.gate",
                key=derive_key("omega.control.signal.blocked", payload),
                message_id="dlq-message",
                properties={"omega_attempt": 1},
                ack=ack,
            )

        publisher = Publisher()
        acked: list[bool] = []
        await DlqTriage(publisher).handle(await source(acked))
        assert acked == [True]
        assert publisher.requests[0].event.type == "omega.control.signal.blocked"
        assert publisher.requests[0].topic == "persistent://omega/control/signal"
        assert publisher.requests[0].key == derive_key(
            "omega.control.signal.blocked", payload
        )

        failed_publisher = Publisher(fail=True)
        failed_acked: list[bool] = []
        with pytest.raises(OSError):
            await DlqTriage(failed_publisher).handle(await source(failed_acked))
        assert failed_acked == []

    asyncio.run(scenario())


def test_non_pr_dlq_key_uses_canonical_escaping() -> None:
    async def scenario() -> None:
        origin = 'persistent://omega/auth/"session"\\replayed'
        aggregate = 'host:"a"\\worker'
        publisher = Publisher()
        source = make_delivery(
            [],
            properties={"omega_origin_topic": origin},
        )
        source = Delivery(
            event=source.event,
            topic=origin,
            key=aggregate,
            message_id=source.message_id,
            properties={"omega_origin_topic": origin},
            ack=source.acknowledge,
        )

        await RetryRouter(publisher, "gate").handle(
            source, DeadLetter("poison"), attempt=5
        )
        expected = "dlq:" + json.dumps(
            [origin, aggregate], ensure_ascii=False, separators=(",", ":")
        )
        assert publisher.requests[0].key == expected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "delay",
    [True, float("nan"), float("inf"), -1, "1", None, 10**1000],
)
def test_retry_rejects_invalid_delay_values(delay: Any) -> None:
    with pytest.raises(ValueError):
        Retry(after=delay, reason="retry")


@pytest.mark.parametrize("reason", [None, 0, "", "   "])
def test_retry_and_dead_letter_require_nonempty_string_reasons(reason: Any) -> None:
    with pytest.raises(ValueError):
        Retry(after=0, reason=reason)
    with pytest.raises(ValueError):
        DeadLetter(reason=reason)


@pytest.mark.asyncio
async def test_history_parses_json_string_properties_from_broker() -> None:
    """Pulsar stores properties as strings; history must resume across attempts."""
    publisher = Publisher()
    router = RetryRouter(
        publisher, "gate", subscription_type="Shared", rng=FixedRng(0.0).uniform
    )
    acknowledged: list[bool] = []
    delivery = make_delivery(
        acknowledged,
        properties={
            "omega_attempt": "2",
            "omega_failure_history": json.dumps(
                [{"attempt": 1, "reason": "attempt 1"}]
            ),
        },
    )
    assert await router.handle(delivery, Retry(1.0, "attempt 2")) == "retry"
    history = publisher.requests[0].properties["omega_failure_history"]
    assert [item["attempt"] for item in history] == [1, 2]
    assert acknowledged == [True]


@pytest.mark.parametrize(
    ("outcome", "attempt"),
    [(DeadLetter("permanent"), 1), (Retry(after=0, reason="still failing"), 5)],
)
def test_dead_letter_counts_one_consume_with_handler_latency(
    outcome: Any, attempt: int
) -> None:
    class Observer:
        def __init__(self) -> None:
            self.consumed: list[tuple[str, float]] = []
            self.dead_letters = 0

        def dead_letter(self) -> None:
            self.dead_letters += 1

        def publish(self, **_kwargs: Any) -> None:
            pass

        def retry(self) -> None:
            pass

        def consume(self, outcome: str, latency: float, **_kwargs: Any) -> None:
            self.consumed.append((outcome, latency))

    async def scenario() -> None:
        observer = Observer()
        acked: list[bool] = []
        router = RetryRouter(
            Publisher(), "gate", observer=cast(EventStreamObserver, observer)
        )
        result = await router.handle(
            make_delivery(acked), outcome, attempt=attempt, latency=1.25
        )
        assert result == "dlq" and acked == [True]
        assert observer.dead_letters == 1
        # Exactly one consume record per dead-lettered delivery, with latency.
        assert observer.consumed == [("dlq", 1.25)]

    asyncio.run(scenario())


def test_dlq_triage_uses_a_single_use_per_event_producer() -> None:
    async def scenario() -> None:
        payload = {
            "kind": "dlq",
            "origin_topic": "persistent://omega/task/state",
            "aggregate_key": "task-1",
            "source_ce_id": "source-event",
            "reason": "invalid event",
            "history": [{"attempt": 1, "reason": "schema rejected"}],
        }
        event = CloudEvent.model_validate(
            {
                "specversion": "1.0",
                "id": "blocked-single-use",
                "source": "urn:test",
                "type": "omega.control.signal.blocked",
                "datacontenttype": "application/json",
                "dataschema": schema_uri_for_type("omega.control.signal.blocked"),
                "data": payload,
                "omegakey": derive_key("omega.control.signal.blocked", payload),
            }
        )

        async def ack() -> None:
            pass

        publisher = Publisher()
        await DlqTriage(publisher).handle(
            Delivery(
                event=event,
                topic="persistent://omega/system/dlq.gate",
                key=derive_key("omega.control.signal.blocked", payload),
                message_id="dlq-message",
                properties={"omega_attempt": 1},
                ack=ack,
            )
        )
        (request,) = publisher.requests
        assert request.producer_name == "omega-eventstream-blocked-single-use"
        assert request.single_use is True

    asyncio.run(scenario())

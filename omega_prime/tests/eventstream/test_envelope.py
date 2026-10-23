from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.envelope import (
    CloudEvent,
    Delivery,
    PublishReceipt,
    PublishRequest,
)
from omega_prime.eventstream.errors import ContractError
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type

TYPE = "omega.sdlc.pr.opened"
SCHEMA = schema_uri_for_type(TYPE)


def valid_event(**overrides: Any) -> CloudEvent:
    values: dict[str, Any] = {
        "specversion": "1.0",
        "id": "github-delivery/abC-123",
        "source": "github://example/project",
        "type": TYPE,
        "subject": "pr/7",
        "time": datetime(2026, 10, 23, 12, tzinfo=UTC),
        "datacontenttype": "application/json",
        "dataschema": SCHEMA,
        "data": {
            "repo": "example/project",
            "number": 7,
            "head_sha": "abc123",
            "actor": "alice",
            "title": "Example change",
            "base_ref": "main",
        },
        "omegakey": "example/project#7",
        "omegatask": "00046",
    }
    values.update(overrides)
    return CloudEvent(**values)


def dlq_event(**overrides: Any) -> CloudEvent:
    type_name = "omega.control.signal.blocked"
    values: dict[str, Any] = {
        "type": type_name,
        "dataschema": schema_uri_for_type(type_name),
        "data": {
            "kind": "dlq",
            "origin_topic": "persistent://omega/auth/session",
            "aggregate_key": "host:tool",
            "source_ce_id": "auth-delivery-1",
            "reason": "invalid source event",
            "history": [{"attempt": 1, "reason": "schema rejected"}],
        },
    }
    values.update(overrides)
    values.setdefault("omegakey", derive_key(type_name, values["data"]))
    return valid_event(**values)


def test_webhook_delivery_id_and_timezone_are_preserved() -> None:
    when = datetime.fromisoformat("2026-10-23T06:40:00+11:00")
    event = valid_event(id="delivery-uuid-from-webhook", time=when)
    assert event.id == "delivery-uuid-from-webhook"
    assert event.time == when
    with pytest.raises((ValidationError, ValueError, ContractError)):
        valid_event(time=datetime(2026, 10, 23, 6, 40))


@pytest.mark.parametrize(
    "changes",
    [
        {"specversion": "2.0"},
        {"id": ""},
        {"source": ""},
        {"type": ""},
        {"datacontenttype": "text/plain"},
        {"dataschema": "urn:omega:schema:omega.sdlc.pr.closed:1"},
        {"subject": ""},
    ],
)
def test_rejects_invalid_cloud_event_contract(changes: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, ValueError, ContractError)):
        valid_event(**changes)


@pytest.mark.parametrize(
    "attribute",
    ["specversion", "id", "source", "type", "subject", "datacontenttype", "dataschema"],
)
def test_envelope_attributes_require_json_strings(attribute: str) -> None:
    with pytest.raises(ValidationError):
        valid_event(**{attribute: 7})


def test_source_must_be_a_uri_reference() -> None:
    with pytest.raises(ValidationError):
        valid_event(source="github://example/not a uri")


def test_traceparent_rejects_non_string_values_without_type_errors() -> None:
    with pytest.raises(ValidationError):
        valid_event(traceparent=7)


def test_invalid_traceparent_error_does_not_expose_secret_input() -> None:
    secret = "sk-abcdefghijklmnop"
    with pytest.raises(ValidationError) as error:
        valid_event(traceparent=secret)
    assert secret not in str(error.value)


def test_json_codec_preserves_unicode_nested_data_and_extensions() -> None:
    event = dlq_event(
        data={
            "kind": "dlq",
            "origin_topic": "persistent://omega/auth/session",
            "aggregate_key": "host:tool",
            "source_ce_id": "auth-delivery-1",
            "reason": "échec",
            "history": [
                {"attempt": 1, "reason": "日本語"},
                {"attempt": 2, "reason": "雪"},
            ],
        },
        omegaheadsha="雪",
        custom="value",
    )
    assert event.model_extra is not None
    with pytest.raises((TypeError, AttributeError)):
        event.model_extra["custom"] = "changed"
    decoded = CloudEvent.from_bytes(event.to_bytes(extra=("omegaheadsha", "custom")))
    assert decoded.model_dump(mode="json") == event.model_dump(mode="json")
    assert getattr(decoded, "omegaheadsha") == "雪"  # noqa: B009 - CloudEvents extension attribute
    assert getattr(decoded, "custom") == "value"  # noqa: B009 - CloudEvents extension attribute


@pytest.mark.parametrize("wire_time", [123, None, "yesterday"])
def test_json_codec_rejects_invalid_wire_time(wire_time: Any) -> None:
    payload = json.loads(valid_event().to_bytes())
    payload["time"] = wire_time
    with pytest.raises((ValidationError, ValueError, ContractError)):
        CloudEvent.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize("attribute", ["specversion", "datacontenttype", "subject"])
def test_json_codec_rejects_missing_required_or_null_subject(attribute: str) -> None:
    payload = json.loads(valid_event().to_bytes())
    if attribute == "subject":
        payload[attribute] = None
    else:
        payload.pop(attribute)
    with pytest.raises((ValidationError, ValueError, ContractError)):
        CloudEvent.from_bytes(json.dumps(payload).encode())


def test_json_codec_accepts_omitted_optional_subject() -> None:
    payload = json.loads(valid_event().to_bytes())
    payload.pop("subject")
    decoded = CloudEvent.from_bytes(json.dumps(payload).encode())
    assert decoded.subject is None


def test_payload_integer_normalization_survives_json_codec() -> None:
    event = valid_event(
        data={
            "repo": "example/project",
            "number": 7.0,
            "head_sha": "abc123",
            "actor": "alice",
            "title": "Example change",
            "base_ref": "main",
        }
    )
    assert type(event.data["number"]) is int
    assert event.data["number"] == 7
    decoded = CloudEvent.from_bytes(event.to_bytes())
    assert type(decoded.data["number"]) is int
    assert decoded.data["number"] == 7


def test_properties_expose_attributes_without_payload() -> None:
    event = valid_event(
        traceparent="00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    )
    properties = event.to_properties()
    assert properties["ce_id"] == event.id
    assert properties["ce_type"] == event.type
    assert properties["ce_source"] == event.source
    assert properties["ce_subject"] == event.subject
    assert "ce_time" in properties and "traceparent" in properties
    assert "data" not in properties
    assert "repo" not in properties and "number" not in properties


def test_recursive_inputs_are_isolated_and_event_is_deeply_immutable() -> None:
    data = {
        "kind": "dlq",
        "origin_topic": "persistent://omega/auth/session",
        "aggregate_key": "host:tool",
        "source_ce_id": "auth-delivery-1",
        "reason": "invalid source event",
        "history": [{"attempt": 1, "reason": "before"}],
    }
    event = dlq_event(data=data)
    data["history"][0]["reason"] = "after"
    assert event.model_dump(mode="json")["data"]["history"][0]["reason"] == "before"
    with pytest.raises((TypeError, AttributeError)):
        event.data["history"][0]["reason"] = "mutated"
    with pytest.raises(TypeError):
        event.data._values["repo"] = "mutated"  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]


def test_known_secret_and_secret_shaped_values_do_not_escape_codec_or_repr() -> None:
    secret = "tok_live_very-secret-value"
    shaped_secret = "sk-abcdefghijklmnop"
    event = valid_event(
        data={
            "repo": "example/project",
            "number": 7,
            "head_sha": "abc",
            "actor": f"{secret} {shaped_secret}",
            "title": "Example change",
            "base_ref": "main",
        },
        custom=shaped_secret,
    )
    encoded = event.to_bytes(extra=(secret, shaped_secret))
    assert secret.encode() not in encoded
    assert shaped_secret.encode() not in encoded
    assert secret not in repr(event)
    assert shaped_secret not in repr(event)
    assert shaped_secret not in repr(event.model_extra)


def test_redaction_rejects_changes_to_payload_routing_identity() -> None:
    repo = "example/private-project"
    event = CloudEvent(
        specversion="1.0",
        id="delivery-identity-redaction",
        source="github://example/project",
        type=TYPE,
        subject="pr/7",
        time=datetime(2026, 10, 23, 12, tzinfo=UTC),
        datacontenttype="application/json",
        dataschema=SCHEMA,
        data={
            "repo": repo,
            "number": 7,
            "head_sha": "abc123",
            "actor": "alice",
            "title": "Example change",
            "base_ref": "main",
        },
    )
    with pytest.raises(ContractError):
        event.to_bytes(extra=(repo,))


def test_redaction_rejects_changes_to_event_id() -> None:
    secret = "webhook-delivery-secret"
    event = valid_event(id=secret)
    with pytest.raises(ContractError):
        event.to_bytes(extra=(secret,))


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b'{"specversion": "1.0"}', b"\xff"])
def test_malformed_or_non_object_json_rejects(raw: bytes) -> None:
    with pytest.raises((ValidationError, ValueError, ContractError)):
        CloudEvent.from_bytes(raw)


def test_publish_request_retains_durable_identity_and_freezes_inputs() -> None:
    event = valid_event()
    props = {"custom": {"values": ["before"]}}
    request = PublishRequest(
        event=event,
        topic="persistent://omega/sdlc/pr",
        key="example/project#7",
        sequence_id=12,
        producer_name="ingress",
        properties=props,
    )
    props["custom"]["values"].append("after")
    assert request.sequence_id == 12 and request.producer_name == "ingress"
    assert request.properties["custom"]["values"] == ("before",)
    assert request.model_dump(mode="json")["properties"] == {
        "custom": {"values": ["before"]}
    }
    with pytest.raises((TypeError, AttributeError)):
        request.properties["custom"]["values"].append("mutated")
    with pytest.raises((ValidationError, ValueError, ContractError)):
        PublishRequest(
            event=event,
            topic="persistent://omega/sdlc/pr",
            key="example/project#7",
            sequence_id=-1,
            producer_name="ingress",
        )
    with pytest.raises((ValidationError, ValueError, ContractError)):
        PublishRequest(
            event=event,
            topic="persistent://omega/sdlc/pr",
            key="example/project#7",
            sequence_id=0,
            producer_name="",
        )


@pytest.mark.parametrize("sequence_id", [True, 12.0])
def test_publish_request_rejects_non_integer_sequence_id(sequence_id: Any) -> None:
    with pytest.raises((ValidationError, ValueError, ContractError)):
        PublishRequest(
            event=valid_event(),
            topic="persistent://omega/sdlc/pr",
            key="example/project#7",
            sequence_id=sequence_id,
            producer_name="ingress",
        )


@pytest.mark.parametrize(
    "topic",
    ["persistent://omega/system/retry.gate", "persistent://omega/system/dlq.gate"],
)
def test_publish_request_accepts_gate_routing_topics(topic: str) -> None:
    request = PublishRequest(
        event=valid_event(),
        topic=topic,
        key="example/project#7",
        sequence_id=12,
        producer_name="ingress",
    )
    assert request.topic == topic
    assert request.key == "example/project#7"


def test_publish_request_rejects_unregistered_topic() -> None:
    with pytest.raises((ValidationError, ValueError, ContractError)):
        PublishRequest(
            event=valid_event(),
            topic="persistent://omega/unknown/topic",
            key="example/project#7",
            sequence_id=12,
            producer_name="ingress",
        )


def test_publish_receipt_represents_acknowledgement_metadata() -> None:
    receipt = PublishReceipt(
        event_id="delivery-uuid",
        topic="persistent://omega/sdlc/pr",
        message_id="ledger:4:8",
        acknowledged_at=datetime.now(UTC),
    )
    assert receipt.event_id == "delivery-uuid"
    assert receipt.message_id == "ledger:4:8"
    assert receipt.topic == "persistent://omega/sdlc/pr"


@pytest.mark.asyncio
async def test_delivery_ack_is_individual_and_only_explicit() -> None:
    calls: list[str] = []

    async def acknowledge() -> None:
        calls.append("ack")

    delivery = Delivery(
        event=valid_event(),
        topic="persistent://omega/sdlc/pr",
        key="example/project#7",
        message_id="ledger:4:8",
        ack=acknowledge,
    )
    assert delivery.event.type == TYPE
    assert calls == []
    await delivery.ack()
    assert calls == ["ack"]


def test_delivery_rejects_non_boolean_redelivered_metadata() -> None:
    async def acknowledge() -> None:
        return None

    with pytest.raises((ValidationError, ValueError, ContractError)):
        Delivery(
            event=valid_event(),
            topic="persistent://omega/sdlc/pr",
            key="example/project#7",
            ack=acknowledge,
            message_id="ledger:4:8",
            redelivered=1,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("broker ack failed"), asyncio.CancelledError()]
)
async def test_delivery_ack_failure_and_cancellation_propagate(
    failure: BaseException,
) -> None:
    async def acknowledge() -> None:
        raise failure

    delivery = Delivery(
        event=valid_event(),
        topic="persistent://omega/sdlc/pr",
        key="example/project#7",
        message_id="ledger:4:8",
        ack=acknowledge,
    )
    with pytest.raises(type(failure)):
        await delivery.ack()


@given(st.text(min_size=1, max_size=60).filter(lambda value: bool(value.strip())))
def test_webhook_ids_roundtrip_without_ulid_restriction(event_id: str) -> None:
    # Ids that the redactor would rewrite (secret-shaped, e.g. "Bearer 0") are
    # refused fail-closed; that contract is pinned by the test below.
    assume(redact_value(event_id) == event_id)
    event = valid_event(id=event_id)
    assert CloudEvent.from_bytes(event.to_bytes()).id == event_id


@pytest.mark.parametrize("event_id", ["Bearer 0", "Bearer abcdefghijklmnop"])
def test_secret_shaped_ids_are_refused_rather_than_silently_rewritten(
    event_id: str,
) -> None:
    assert redact_value(event_id) != event_id
    with pytest.raises(ContractError):
        valid_event(id=event_id).to_bytes()


def test_freeze_rejects_non_json_values() -> None:
    from omega_prime.eventstream.envelope import _freeze
    from omega_prime.eventstream.errors import ContractError

    with pytest.raises(ContractError):
        _freeze(object())
    with pytest.raises(ContractError):
        _freeze({1: "x"})  # type: ignore[dict-item]
    with pytest.raises(ContractError):
        _freeze(float("nan"))


def test_frozen_containers_reject_mutation_and_expose_repr() -> None:
    from omega_prime.eventstream.envelope import _freeze, _FrozenDict, _FrozenMap

    frozen = _freeze({"a": 1, "nested": {"b": 2}})
    assert isinstance(frozen, _FrozenMap)
    assert len(frozen) == 2
    assert repr(frozen) == "<immutable mapping>"
    assert list(frozen) == ["a", "nested"]
    assert frozen["a"] == 1

    extras = _FrozenDict({"omegaext": "keep"})
    with pytest.raises(TypeError, match="immutable"):
        extras["x"] = 1
    with pytest.raises(TypeError, match="immutable"):
        del extras["omegaext"]
    with pytest.raises(TypeError, match="immutable"):
        extras.clear()
    with pytest.raises(TypeError, match="immutable"):
        extras.pop("omegaext")
    with pytest.raises(TypeError, match="immutable"):
        extras.popitem()
    with pytest.raises(TypeError, match="immutable"):
        extras.setdefault("y", 1)
    with pytest.raises(TypeError, match="immutable"):
        extras.update({"z": 1})
    with pytest.raises(TypeError, match="immutable"):
        extras |= {"z": 1}

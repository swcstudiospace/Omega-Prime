"""Immutable CloudEvents and broker-neutral transport values."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.errors import ContractError, SchemaError


class _FrozenMap(Mapping[str, Any]):
    """Small recursively immutable mapping used at all public boundaries."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = MappingProxyType(
            {key: _freeze(value) for key, value in values.items()}
        )

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return "<immutable mapping>"


class _FrozenDict(dict[str, Any]):
    """Read-only dict-compatible copy for Pydantic's extra field storage."""

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("event extensions are immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("event extensions are immutable")

    def clear(self) -> None:
        raise TypeError("event extensions are immutable")

    def pop(self, key: str, default: Any = None) -> Any:
        raise TypeError("event extensions are immutable")

    def popitem(self) -> tuple[str, Any]:
        raise TypeError("event extensions are immutable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        raise TypeError("event extensions are immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("event extensions are immutable")

    def __ior__(self, other: Any) -> _FrozenDict:  # type: ignore[override, misc]
        raise TypeError("event extensions are immutable")

    def __repr__(self) -> str:
        return "<redacted extensions>"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ContractError("JSON object keys must be strings")
        return _FrozenMap(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ContractError("value is not JSON-compatible")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze(value)
    if not isinstance(frozen, Mapping):
        raise ContractError("value is not a JSON object")
    return cast(Mapping[str, Any], frozen)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamp must include a timezone")
    return value


def _taxonomy_validate(type_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        from omega_prime.eventstream.taxonomy import validate_payload
    except ImportError as exc:
        raise SchemaError("event taxonomy is unavailable") from exc
    try:
        return validate_payload(type_name, payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ContractError("event payload violates its registered schema") from exc


def _taxonomy_schema(type_name: str) -> str:
    try:
        from omega_prime.eventstream.taxonomy import schema_uri_for_type
    except ImportError as exc:
        raise SchemaError("event taxonomy is unavailable") from exc
    try:
        return schema_uri_for_type(type_name)
    except (ValueError, KeyError) as exc:
        raise ContractError("event type is not registered") from exc


def _taxonomy_topic(type_name: str) -> str:
    try:
        from omega_prime.eventstream.taxonomy import get_event_type
    except ImportError as exc:
        raise SchemaError("event taxonomy is unavailable") from exc
    try:
        return get_event_type(type_name).topic
    except (ValueError, KeyError) as exc:
        raise ContractError("event type is not registered") from exc


_CE_ID = re.compile(r"^[a-z0-9]{2}-[a-f0-9]{32}-[a-f0-9]{16}-[a-f0-9]{2}$")
_EXTENSION = re.compile(r"^[a-z][a-z0-9]{0,19}$")
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SYSTEM_REPUBLISH_TOPIC = re.compile(
    r"^persistent://omega/system/(?:retry|dlq)\.[^/\s]+$"
)


_URI_REFERENCE_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~!$&'()*+,;=:@/?#[]%"
)


def _is_uri_reference(value: str) -> bool:
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        return False
    if any(character not in _URI_REFERENCE_CHARS for character in value):
        return False
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    first_segment = parsed.path.split("/", 1)[0]
    return bool(parsed.scheme or ":" not in first_segment)


class CloudEvent(BaseModel):
    """CloudEvents 1.0 structured JSON with validated, immutable data."""

    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        arbitrary_types_allowed=True,
        hide_input_in_errors=True,
    )

    specversion: StrictStr
    id: StrictStr
    source: StrictStr
    type: StrictStr
    subject: StrictStr | None = None
    time: datetime | None = None
    datacontenttype: StrictStr
    dataschema: StrictStr
    data: Mapping[str, Any] = Field(repr=False)

    @field_validator("data")
    @classmethod
    def freeze_and_validate_data(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("event data must be a JSON object")
        return _freeze_mapping(value)

    @field_serializer("data")
    def serialize_data(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], _thaw(value))

    @field_validator("subject", mode="before")
    @classmethod
    def reject_explicit_null_subject(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("subject cannot be null when supplied")
        return value

    @field_validator("time", mode="before")
    @classmethod
    def validate_time_input(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return _aware(value)
        if isinstance(value, str) and _RFC3339.fullmatch(value):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("time must be RFC3339") from exc
            _aware(parsed)
            return value
        raise ValueError("time must be an aware datetime or RFC3339 string")

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if not _is_uri_reference(value):
            raise ValueError("source must be a URI-reference")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> CloudEvent:
        if self.specversion != "1.0":
            raise ValueError("specversion must be 1.0")
        for field_name in ("id", "source", "type", "dataschema"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be nonempty")
        if self.subject is not None and not self.subject.strip():
            raise ValueError("subject must be nonempty when present")
        if self.datacontenttype != "application/json":
            raise ValueError("datacontenttype must be application/json")
        if self.dataschema != _taxonomy_schema(self.type):
            raise ValueError("dataschema does not match the registered event type")
        # CloudEvents extensions use lower-case attribute names and scalar JSON values.
        extensions = self.model_extra or {}
        for name, value in extensions.items():
            if name in type(self).model_fields or not _EXTENSION.fullmatch(name):
                raise ValueError("invalid CloudEvents extension name")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("extension values must be finite JSON scalars")
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError("extension values must be JSON scalars")
        traceparent = extensions.get("traceparent")
        if traceparent is not None and (
            not isinstance(traceparent, str) or not _CE_ID.fullmatch(traceparent)
        ):
            raise ValueError("traceparent is invalid")
        validated_payload = _taxonomy_validate(self.type, self.data)
        object.__setattr__(self, "data", _freeze_mapping(validated_payload))
        if "omegakey" in extensions and extensions["omegakey"] != _expected_key(
            self.type, self.data
        ):
            raise ValueError("omegakey does not match event identity")
        if extensions and not isinstance(extensions, _FrozenDict):
            object.__setattr__(self, "__pydantic_extra__", _FrozenDict(extensions))
        return self

    def __repr__(self) -> str:
        return "CloudEvent(<redacted>)"

    def to_bytes(self, *, extra: tuple[str, ...] = ()) -> bytes:
        """Serialize safe structured JSON, redacting before final schema validation."""
        original_key = _expected_key(self.type, self.data)
        known_secrets = tuple(dict.fromkeys(extra))
        document: dict[str, Any] = {
            "specversion": self.specversion,
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "datacontenttype": self.datacontenttype,
            "dataschema": self.dataschema,
            "data": _thaw(self.data),
        }
        if self.subject is not None:
            document["subject"] = self.subject
        if self.time is not None:
            document["time"] = self.time.isoformat()
        document.update(
            {name: _thaw(value) for name, value in (self.model_extra or {}).items()}
        )
        sanitized = redact_value(document, extra=known_secrets)
        if not isinstance(sanitized, Mapping):
            raise ContractError("redacted event must remain a JSON object")
        sanitized_document = _thaw(sanitized)
        if not isinstance(sanitized_document, dict) or not isinstance(
            sanitized_document.get("data"), dict
        ):
            raise ContractError("redacted event must remain a JSON object")
        sanitized = cast(dict[str, Any], sanitized_document)
        for name in (
            "specversion",
            "id",
            "source",
            "type",
            "datacontenttype",
            "dataschema",
            "subject",
            "time",
        ):
            if (name in sanitized) != (name in document) or sanitized.get(
                name
            ) != document.get(name):
                raise ContractError("redaction changed envelope identity metadata")
        valid_payload = _taxonomy_validate(self.type, sanitized["data"])
        sanitized["data"] = _thaw(valid_payload)
        if _expected_key(self.type, sanitized["data"]) != original_key:
            raise ContractError("redaction changed event routing identity")
        if "omegakey" in sanitized:
            expected_key = _expected_key(self.type, sanitized["data"])
            if sanitized["omegakey"] != expected_key:
                raise ContractError(
                    "redacted event routing key does not match payload identity"
                )
        try:
            CloudEvent.model_validate(sanitized)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ContractError(
                "redacted event violates the CloudEvents contract"
            ) from exc
        try:
            return json.dumps(
                sanitized, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ContractError("event cannot be serialized as JSON") from exc

    @classmethod
    def from_bytes(cls, value: bytes) -> CloudEvent:
        if not isinstance(value, bytes):
            raise TypeError("structured event must be bytes")
        try:
            document = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("structured event is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise ContractError("structured event must be a JSON object")
        try:
            return cls.model_validate(document)
        except (ValidationError, ValueError, TypeError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError(
                "structured event violates the CloudEvents contract"
            ) from exc

    def to_properties(self) -> Mapping[str, str]:
        """Return filterable CloudEvents attributes without event data."""
        attrs: dict[str, str] = {
            "ce_id": self.id,
            "ce_type": self.type,
            "ce_source": self.source,
        }
        if self.subject is not None:
            attrs["ce_subject"] = self.subject
        if self.time is not None:
            attrs["ce_time"] = self.time.isoformat()
        for name, value in (self.model_extra or {}).items():
            if name in {
                "traceparent",
                "omegakey",
                "omegatask",
                "omegagraph",
                "omegaheadsha",
            }:
                attrs[name] = str(value)
        return _FrozenMap(attrs)


class PublishRequest(BaseModel):
    """Durable producer intent passed to a later publisher implementation."""

    model_config = ConfigDict(
        frozen=True, arbitrary_types_allowed=True, hide_input_in_errors=True
    )

    event: CloudEvent
    topic: StrictStr
    key: StrictStr
    sequence_id: StrictInt = Field(ge=0)
    producer_name: StrictStr
    deliver_after: datetime | None = None
    properties: Mapping[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("properties")
    @classmethod
    def freeze_request_properties(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("properties must be an object")
        return _freeze_mapping(value)

    @field_serializer("properties")
    def serialize_properties(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], _thaw(value))

    @field_validator("deliver_after")
    @classmethod
    def validate_delivery_time(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def validate_request(self) -> PublishRequest:
        if (
            not self.topic.strip()
            or not self.key.strip()
            or not self.producer_name.strip()
        ):
            raise ValueError("topic, key and producer_name must be nonempty")
        if self.sequence_id < 0:
            raise ValueError("sequence_id must be nonnegative")
        if self.topic != _taxonomy_topic(
            self.event.type
        ) and not _SYSTEM_REPUBLISH_TOPIC.fullmatch(self.topic):
            raise ValueError(
                "publish topic does not match event type or reserved redelivery topic"
            )
        expected = _expected_key(self.event.type, self.event.data)
        if self.key != expected:
            raise ValueError("publish key does not match event identity")
        if (
            self.event.model_extra
            and "omegakey" in self.event.model_extra
            and self.key != self.event.model_extra["omegakey"]
        ):
            raise ValueError("publish key does not match omegakey")
        return self


def _expected_key(type_name: str, payload: Mapping[str, Any]) -> str:
    try:
        from omega_prime.eventstream.taxonomy import derive_key
    except ImportError as exc:
        raise SchemaError("event taxonomy is unavailable") from exc
    try:
        return derive_key(type_name, payload)
    except (ValueError, KeyError, TypeError) as exc:
        raise ContractError("event identity cannot produce its routing key") from exc


class PublishReceipt(BaseModel):
    """Acknowledgement returned only after broker acceptance."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    event_id: StrictStr
    topic: StrictStr
    message_id: StrictStr
    acknowledged_at: datetime

    @field_validator("acknowledged_at")
    @classmethod
    def validate_ack_time(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def validate_receipt(self) -> PublishReceipt:
        if (
            not self.event_id.strip()
            or not self.topic.strip()
            or not self.message_id.strip()
        ):
            raise ValueError("receipt identity fields must be nonempty")
        return self


@runtime_checkable
class AckControl(Protocol):
    """Local capability for acknowledging one delivered message."""

    async def __call__(self) -> None: ...


class Delivery(BaseModel):
    """Immutable delivered message with explicit individual-ack capability."""

    model_config = ConfigDict(
        frozen=True, arbitrary_types_allowed=True, hide_input_in_errors=True
    )

    event: CloudEvent
    topic: StrictStr
    key: StrictStr
    message_id: StrictStr
    redelivered: StrictBool = False
    properties: Mapping[str, Any] = Field(default_factory=dict, repr=False)
    ack: AckControl = Field(repr=False, exclude=True)

    @field_validator("properties")
    @classmethod
    def freeze_delivery_properties(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("properties must be an object")
        return _freeze_mapping(value)

    @field_serializer("properties")
    def serialize_properties(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], _thaw(value))

    @model_validator(mode="after")
    def validate_delivery(self) -> Delivery:
        if (
            not self.topic.strip()
            or not self.key.strip()
            or not self.message_id.strip()
        ):
            raise ValueError("delivery identity fields must be nonempty")
        return self

    async def acknowledge(self) -> None:
        """Acknowledge only this delivery; errors and cancellation propagate."""
        await self.ack()


__all__ = ["AckControl", "CloudEvent", "Delivery", "PublishReceipt", "PublishRequest"]

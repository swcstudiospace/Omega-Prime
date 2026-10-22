from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from omega_prime.eventstream.envelope import CloudEvent
from omega_prime.eventstream.taxonomy import (
    EVENT_TYPES,
    resolve_schema_uri,
    schema_uri_for_type,
    validate_payload,
)

_RFC3339_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
_URI_REFERENCE_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~!$&'()*+,;=:@/?#[]%"
)
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str) or not _RFC3339_PATTERN.fullmatch(value):
        return not isinstance(value, str)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).utcoffset() is not None
    except ValueError:
        return False


@_FORMAT_CHECKER.checks("uri-reference")
def _is_uri_reference(value: object) -> bool:
    if not isinstance(value, str):
        return True
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


def _validator(definition: Mapping[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(definition, format_checker=_FORMAT_CHECKER)


def _model_accepts(type_name: str, payload: Any) -> bool:
    try:
        if type_name == "CloudEvent":
            CloudEvent.model_validate(payload)
        else:
            validate_payload(
                "omega.audit.example" if type_name == "omega.audit.*" else type_name,
                payload,
            )
    except (ValidationError, ValueError, TypeError):
        return False
    return True


def test_all_schema_bundles_are_draft_2020_12(schema_bundles: dict[str, Any]) -> None:
    assert set(schema_bundles) == {
        "envelope",
        "sdlc.pr",
        "sdlc.review",
        "ci",
        "auth",
        "job",
        "sandbox",
        "task",
        "mem",
        "control",
        "hitl",
        "audit",
    }
    for family, bundle in schema_bundles.items():
        Draft202012Validator.check_schema(bundle)
        assert bundle["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert bundle["$id"] == f"urn:omega:schema:{family}:1"


def test_schema_definitions_cover_catalog_and_resolve_all_type_uris(
    schema_bundles: dict[str, Any],
) -> None:
    definition_names = {
        type_name
        for bundle in schema_bundles.values()
        for type_name in bundle.get("$defs", {})
    }
    assert definition_names == set(EVENT_TYPES) | {"omega.audit.*", "CloudEvent"}
    for type_name in EVENT_TYPES:
        assert (
            resolve_schema_uri(schema_uri_for_type(type_name))
            == schema_bundles[
                next(
                    family
                    for family, bundle in schema_bundles.items()
                    if type_name in bundle.get("$defs", {})
                )
            ]["$defs"][type_name]
        )
    assert (
        resolve_schema_uri(schema_uri_for_type("omega.audit.example"))
        == schema_bundles["audit"]["$defs"]["omega.audit.*"]
    )


def test_envelope_type_schema_matches_catalog_and_open_audit_family(
    schema_bundles: dict[str, Any],
) -> None:
    type_schema = schema_bundles["envelope"]["$defs"]["CloudEvent"]["properties"][
        "type"
    ]
    alternatives = type_schema["anyOf"]
    enum_schema = next(option for option in alternatives if "enum" in option)
    audit_schema = next(option for option in alternatives if "pattern" in option)
    assert set(enum_schema["enum"]) == set(EVENT_TYPES)
    assert audit_schema["pattern"].startswith(r"^omega\.audit\.")

    dataschema = schema_bundles["envelope"]["$defs"]["CloudEvent"]["properties"][
        "dataschema"
    ]
    dataschema_alternatives = dataschema["anyOf"]
    schema_uri_enum = next(
        option for option in dataschema_alternatives if "enum" in option
    )
    audit_uri_pattern = next(
        option for option in dataschema_alternatives if "pattern" in option
    )
    assert set(schema_uri_enum["enum"]) == {
        schema_uri_for_type(name) for name in EVENT_TYPES
    }
    assert r"omega\.audit\." in audit_uri_pattern["pattern"]


def test_definition_examples_match_models_and_schemas(
    payload_definitions: list[tuple[str, str, dict[str, Any]]],
) -> None:
    for family, type_name, definition in payload_definitions:
        assert definition.get("examples"), (family, type_name, "missing valid examples")
        assert definition.get("x-omega-invalid-examples"), (
            family,
            type_name,
            "missing invalid examples",
        )
        validator = _validator(definition)
        for example in definition["examples"]:
            assert isinstance(example, dict)
            assert validator.is_valid(example), (type_name, example)
            assert _model_accepts(type_name, example), (type_name, example)
        for example in definition["x-omega-invalid-examples"]:
            assert isinstance(example, dict)
            assert not validator.is_valid(example), (type_name, example)
            assert not _model_accepts(type_name, example), (type_name, example)


def test_envelope_examples_match_cloud_event_model(
    schema_bundles: dict[str, Any],
) -> None:
    definition = schema_bundles["envelope"]["$defs"]["CloudEvent"]
    validator = _validator(definition)
    for example in definition["examples"]:
        assert validator.is_valid(example)
        assert _model_accepts("CloudEvent", example)
    for example in definition["x-omega-invalid-examples"]:
        assert not validator.is_valid(example)
        assert not _model_accepts("CloudEvent", example)


def test_envelope_rejects_unknown_non_audit_type(
    schema_bundles: dict[str, Any],
) -> None:
    definition = schema_bundles["envelope"]["$defs"]["CloudEvent"]
    example = deepcopy(definition["examples"][0])
    example["type"] = "omega.unknown.event"
    assert not _validator(definition).is_valid(example)
    assert not _model_accepts("CloudEvent", example)


def _invalid_boundary(
    definition: Mapping[str, Any], example: dict[str, Any], selector: int
) -> dict[str, Any]:
    mutated = deepcopy(example)
    required = definition.get("required", [])
    props = definition.get("properties", {})
    if selector % 2 == 0 and required:
        mutated.pop(required[selector % len(required)], None)
        return mutated
    candidates = [key for key in required if key in props]
    if candidates:
        key = candidates[selector % len(candidates)]
        constraint = props[key]
        if "const" in constraint or "enum" in constraint:
            mutated[key] = "__invalid__"
        elif constraint.get("type") == "integer":
            mutated[key] = constraint.get("minimum", 0) - 1
        elif constraint.get("type") == "string":
            mutated[key] = ""
        else:
            mutated[key] = None
    else:
        mutated["__unexpected__"] = True
    return mutated


@given(
    st.sampled_from((*tuple(EVENT_TYPES), "omega.audit.example")),
    st.integers(min_value=0, max_value=2**16),
    st.integers(min_value=0, max_value=2**16),
)
def test_hypothesis_required_and_constraint_boundaries(
    type_name: str, example_index: int, selector: int
) -> None:
    definition = resolve_schema_uri(schema_uri_for_type(type_name))
    examples = definition["examples"]
    example = examples[example_index % len(examples)]
    invalid = _invalid_boundary(definition, example, selector)
    assert not _validator(definition).is_valid(invalid), (type_name, invalid)
    assert not _model_accepts(type_name, invalid), (type_name, invalid)

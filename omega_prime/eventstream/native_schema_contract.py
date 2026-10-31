"""Shared native Pulsar schema contract for the Python and TypeScript SDKs.

The Autonomous Event-Driven ADLC Programming Bot registers one native Avro-format
JSON schema per topic. Python derives it from the taxonomy JSON Schemas
(:func:`omega_prime.eventstream.adapters.pulsar.native_json_schema`); the
TypeScript SDK must present byte-identical schema definitions and names or the
broker's compatibility check rejects mixed-language producers. This module
renders that single contract into ``packages/eventstream-ts/src/
native-schemas.generated.ts`` and verifies it has not drifted::

    python -m omega_prime.eventstream.native_schema_contract --write
    python -m omega_prime.eventstream.native_schema_contract --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from omega_prime.eventstream.adapters.pulsar import native_json_schema
from omega_prime.eventstream.taxonomy import EVENT_TYPES

REDELIVERY_TEMPLATE_TOPIC = "persistent://omega/system/retry.__template__"
SAMPLE_REDELIVERY_TOPICS = (
    "persistent://omega/system/retry.gate-1",
    "persistent://omega/system/dlq.dlq-triage",
)
GENERATED_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "eventstream-ts"
    / "src"
    / "native-schemas.generated.ts"
)


def _compact(definition: object) -> str:
    return json.dumps(definition, separators=(",", ":"), ensure_ascii=True)


def contract_topics() -> tuple[str, ...]:
    """Every non-redelivery topic with a registered native schema."""
    topics = {event.topic for event in EVENT_TYPES.values()}
    topics.update({"persistent://omega/audit/log", "persistent://omega/sdlc/pr-state"})
    return tuple(sorted(topics))


def render_typescript() -> str:
    """Render the generated TypeScript module deterministically."""
    topic_lines = [
        f"  {json.dumps(topic)}: {json.dumps(_compact(native_json_schema(topic)))},"
        for topic in contract_topics()
    ]
    template = dict(native_json_schema(REDELIVERY_TEMPLATE_TOPIC))
    template["name"] = ""
    sample_lines = [
        f"  {json.dumps(topic)}: {json.dumps(_compact(native_json_schema(topic)))},"
        for topic in SAMPLE_REDELIVERY_TOPICS
    ]
    return "\n".join(
        [
            "// GENERATED FILE - DO NOT EDIT.",
            "// Source: omega_prime/eventstream/native_schema_contract.py",
            "// Regenerate: python -m omega_prime.eventstream.native_schema_contract --write",
            "// Shared native Pulsar schema contract of the Autonomous Event-Driven ADLC",
            "// Programming Bot: Python and TypeScript producers register identical schemas.",
            "",
            "/** Compact native Avro-format JSON schema per registered topic. */",
            "export const NATIVE_TOPIC_SCHEMAS: Readonly<Record<string, string>> = Object.freeze({",
            *topic_lines,
            "});",
            "",
            "/** Retry/DLQ schema; `name` is OmegaCloudEvent_ + avroName(topic leaf). */",
            f"export const NATIVE_REDELIVERY_SCHEMA_TEMPLATE = {json.dumps(_compact(template))};",
            "",
            "/** Python-rendered redelivery schemas used by the cross-language contract test. */",
            "export const NATIVE_REDELIVERY_SCHEMA_SAMPLES: Readonly<Record<string, string>> = Object.freeze({",
            *sample_lines,
            "});",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = render_typescript()
    if args.write:
        GENERATED_PATH.write_text(rendered, encoding="utf-8")
        return 0
    current = (
        GENERATED_PATH.read_text(encoding="utf-8") if GENERATED_PATH.exists() else ""
    )
    if current != rendered:
        sys.stderr.write(
            f"{GENERATED_PATH.name} is stale; run "
            "python -m omega_prime.eventstream.native_schema_contract --write\n"
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

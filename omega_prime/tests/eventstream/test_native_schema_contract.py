"""The shared native schema contract consumed by the TypeScript SDK never drifts."""

from __future__ import annotations

import json

from omega_prime.eventstream import native_schema_contract as contract
from omega_prime.eventstream.adapters.pulsar import native_json_schema


def test_generated_typescript_contract_is_current() -> None:
    assert contract.GENERATED_PATH.read_text(encoding="utf-8") == (
        contract.render_typescript()
    ), "run python -m omega_prime.eventstream.native_schema_contract --write"
    assert contract.main(["--check"]) == 0


def test_contract_covers_every_registered_topic_with_python_schemas() -> None:
    rendered = contract.render_typescript()
    for topic in contract.contract_topics():
        schema = json.dumps(native_json_schema(topic), separators=(",", ":"))
        assert json.dumps(schema) in rendered
    assert "persistent://omega/sdlc/pr" in contract.contract_topics()
    assert native_json_schema("persistent://omega/sdlc/pr")["name"] == (
        "OmegaCloudEvent_pr"
    )

"""CI rollup state, source filtering, and Pulsar process contract tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from omega_prime.eventstream.envelope import CloudEvent
from omega_prime.eventstream.functions.ci_rollup import (
    is_source_message,
    output_event,
    process,
    reduce_ci_run,
)

REQUIRED = ("build", "tests")
BASE = {
    "repo": "acme/project",
    "number": 7,
    "head_sha": "abc123",
    "run_id": "run-7",
    "actor": "ci",
}


class _Context:
    def __init__(self, properties: Mapping[str, Any]) -> None:
        self.properties = properties
        self.state: dict[str, bytes] = {}
        self.published: list[tuple[str, bytes, str]] = []

    def get_message_properties(self) -> Mapping[str, Any]:
        return self.properties

    def get_user_config_value(self, key: str) -> object:
        return list(REQUIRED) if key == "required_checks" else None

    def get_state(self, key: str) -> bytes | None:
        return self.state.get(key)

    def put_state(self, key: str, value: bytes) -> None:
        self.state[key] = value

    def publish(self, topic: str, message: bytes, **options: Any) -> None:
        message_conf = options["message_conf"]
        self.published.append((topic, message, str(message_conf["partition_key"])))
        callback = options["callback"]
        callback(0, None)


def _check(name: str, status: str, conclusion: str | None = None) -> dict[str, Any]:
    return {
        **BASE,
        "check": name,
        "status": status,
        "conclusion": conclusion,
    }


def _event(event_type: str, conclusion: str | None = None) -> CloudEvent:
    if event_type == "omega.ci.run.started":
        data = {**BASE, "status": "in_progress"}
    else:
        data = {**BASE, "conclusion": conclusion or "success"}
    return CloudEvent(
        specversion="1.0",
        id=f"source-{event_type.rsplit('.', 1)[-1]}",
        source="urn:ci:github",
        type=event_type,
        datacontenttype="application/json",
        dataschema=f"urn:omega:schema:{event_type}:1",
        data=data,
    )


def test_pending_until_all_required_checks_terminal() -> None:
    state = reduce_ci_run(None, _check("build", "completed", "success"), REQUIRED)
    assert state["result"] is None
    state = reduce_ci_run(state, _check("tests", "in_progress"), REQUIRED)
    assert state["result"] is None
    state = reduce_ci_run(state, _check("tests", "completed", "success"), REQUIRED)
    assert state["result"] == "passed"
    event_key = output_event(state)
    assert event_key is not None
    assert event_key[0].type == "omega.ci.run.passed"
    assert event_key[1] == "acme/project#7"


def test_failure_and_cancellation_are_terminal_failure() -> None:
    state = reduce_ci_run(None, _check("build", "completed", "success"), REQUIRED)
    state = reduce_ci_run(state, _check("tests", "completed", "cancelled"), REQUIRED)
    event = output_event(state)
    assert state["result"] == "failed"
    assert event is not None and event[0].type == "omega.ci.run.failed"
    assert event[0].data["conclusion"] == "cancelled"


def test_ci_identity_scopes_do_not_mix_repo_head_or_run_checks() -> None:
    prior = reduce_ci_run(None, _check("build", "completed", "success"), REQUIRED)
    other_identities = (
        {"repo": "other/project"},
        {"head_sha": "different-head"},
        {"run_id": "different-run"},
    )
    for identity in other_identities:
        check = {**_check("tests", "completed", "success"), **identity}
        scoped = reduce_ci_run(prior, check, REQUIRED)
        assert scoped["result"] is None
        assert set(scoped["checks"]) == {"tests"}
        assert all(scoped[key] == value for key, value in identity.items())


def test_actor_and_failure_output_are_deterministic_after_reordering() -> None:
    events = [
        {**_check("build", "completed", "cancelled"), "actor": "zeta"},
        {**_check("tests", "completed", "failure"), "actor": "alpha"},
    ]
    left: dict[str, Any] | None = None
    for event in events:
        left = reduce_ci_run(left, event, REQUIRED)
    right: dict[str, Any] | None = None
    for event in reversed(events):
        right = reduce_ci_run(right, event, REQUIRED)
    assert left is not None and right is not None
    assert left == right
    assert left["actor"] == "alpha"
    emitted = output_event(left)
    assert emitted is not None
    assert emitted[0].data["conclusion"] == "cancelled"
    assert emitted == output_event(right)


def test_action_required_is_terminal_but_uses_contract_failure_conclusion() -> None:
    state = reduce_ci_run(
        None, _check("build", "completed", "action_required"), REQUIRED
    )
    state = reduce_ci_run(state, _check("tests", "completed", "success"), REQUIRED)
    emitted = output_event(state)
    assert state["result"] == "failed"
    assert emitted is not None
    assert emitted[0].type == "omega.ci.run.failed"
    assert emitted[0].data["conclusion"] == "failure"


def test_duplicates_and_reordered_events_are_deterministic() -> None:
    events = [
        _check("build", "completed", "success"),
        _check("tests", "completed", "failure"),
    ]
    left: dict[str, Any] | None = None
    for event in events:
        left = reduce_ci_run(left, event, REQUIRED)
    right: dict[str, Any] | None = None
    for event in reversed(events):
        right = reduce_ci_run(right, event, REQUIRED)
    assert left is not None and right is not None
    assert left == right
    repeated = reduce_ci_run(left, events[0], REQUIRED)
    assert repeated == left
    assert output_event(left) == output_event(right)


def test_stale_nonterminal_cannot_regress_terminal_or_mutate_prior_state() -> None:
    state = reduce_ci_run(None, _check("build", "completed", "success"), REQUIRED)
    snapshot = json.loads(json.dumps(state))
    updated = reduce_ci_run(state, _check("build", "in_progress"), REQUIRED)
    assert state == snapshot
    assert updated == state


@pytest.mark.parametrize(
    "check",
    [
        {
            "repo": "x",
            "number": 7,
            "head_sha": "y",
            "run_id": "z",
            "check": "build",
            "status": "completed",
        },
        {**BASE, "number": True, "check": "build", "status": "queued"},
        {**BASE, "check": "build", "status": "queued", "conclusion": "success"},
        {**BASE, "check": "build", "status": "unknown"},
        {**BASE, "check": "build", "status": "completed", "conclusion": "mystery"},
    ],
)
def test_malformed_inputs_rejected(check: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        reduce_ci_run(None, check, REQUIRED)


def test_empty_required_set_rejected() -> None:
    with pytest.raises(ValueError):
        reduce_ci_run(None, _check("build", "in_progress"), ())


def test_process_rolls_up_configured_check_properties_and_reemits_duplicates() -> None:
    context = _Context({"check_name": "build"})
    first = _event("omega.ci.run.passed", "success")
    assert process(first.to_bytes(), context) is None
    assert not context.published
    context.properties = {"check_name": "tests"}
    second = _event("omega.ci.run.failed", "failure")
    assert process(second.to_bytes(), context) is None
    assert len(context.published) == 1
    topic, payload, key = context.published[0]
    result = CloudEvent.from_bytes(payload)
    assert topic == "persistent://omega/sdlc/ci"
    assert key == "acme/project#7"
    assert result.type == "omega.ci.run.failed"
    assert result.data["number"] == BASE["number"]
    assert result.data["head_sha"] == BASE["head_sha"]
    assert result.data["run_id"] == BASE["run_id"]
    assert result.data["conclusion"] == "failure"
    assert process(second.to_bytes(), context) is None
    assert context.published[0][1] == context.published[1][1]
    state = next(iter(context.state.values()))
    assert json.loads(state)["result"] == "failed"


def test_process_requires_real_check_metadata_and_filters_own_output() -> None:
    event = _event("omega.ci.run.failed", "failure")
    context = _Context({})
    assert process(event.to_bytes(), context) is None
    assert not context.published
    assert process(b"not-json", context) is None
    output = CloudEvent(
        specversion="1.0",
        id="ci-output",
        source="urn:omega:function:ci-rollup",
        type="omega.ci.run.failed",
        datacontenttype="application/json",
        dataschema="urn:omega:schema:omega.ci.run.failed:1",
        data={**BASE, "conclusion": "failure"},
    )
    assert not is_source_message(output)

    loop_context = _Context({"check_name": "build"})
    assert process(output.to_bytes(), loop_context) is None
    assert not loop_context.state and not loop_context.published
    assert is_source_message(event)


def test_ci_rollup_process_adapter_ignores_malformed_input() -> None:
    from omega_prime.eventstream.functions.ci_rollup import process

    class Ctx:
        def get_current_message_topic_name(self) -> str:
            return "persistent://omega/sdlc/pr"

        def get_user_config_value(self, key: str, default: object = None) -> object:
            return default

        def publish(
            self, topic: str, payload: bytes, properties: dict | None = None
        ) -> None:
            self.last = (topic, payload, properties)

        def incr_counter(self, name: str, value: int = 1) -> None:
            return None

        def get_counter(self, name: str) -> int:
            return 0

        def put_state(self, key: str, value: bytes) -> None:
            return None

        def get_state(self, key: str) -> bytes | None:
            return None

        def delete_state(self, key: str) -> None:
            return None

        def ack(self, message: object = None) -> None:
            return None

    process(b"not-json", Ctx())
    process(b"{}", Ctx())

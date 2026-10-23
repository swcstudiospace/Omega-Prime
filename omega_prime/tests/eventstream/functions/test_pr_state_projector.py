"""Head-aware PR projector reducer and Pulsar state boundary tests."""

from __future__ import annotations

import json
from typing import Any

from omega_prime.eventstream.envelope import CloudEvent
from omega_prime.eventstream.functions.pr_state_projector import (
    process,
    projection,
    reduce_pr_state,
    serialize_projection,
)

BASE: dict[str, Any] = {
    "repo": "acme/project",
    "number": 7,
    "head_sha": "head-a",
    "actor": "bot",
}


def _event(event_type: str, **updates: Any) -> CloudEvent:
    data = {**BASE, **updates}
    if event_type == "omega.sdlc.pr.opened":
        data.update(title="Change", base_ref="main")
    elif event_type == "omega.sdlc.pr.pushed":
        data.setdefault("before_sha", data["head_sha"])
    elif event_type == "omega.sdlc.pr.ready":
        data["draft"] = False
    elif event_type == "omega.sdlc.pr.converted_to_draft":
        data["draft"] = True
    elif event_type == "omega.sdlc.pr.closed":
        data.update(merged=False, closed_reason="completed")
    elif event_type == "omega.sdlc.pr.merged":
        data.update(merged_by="reviewer", merge_sha="merge-a")
    elif event_type == "omega.sdlc.review.approved":
        data["reviewer"] = "reviewer"
    elif event_type == "omega.sdlc.review.changes_requested":
        data.update(reviewer="reviewer", summary="needs work")
    elif event_type == "omega.sdlc.review.commented":
        data.update(reviewer="reviewer", comment_id="comment-1")
    elif event_type == "omega.sdlc.review.greptile_scored":
        data["score"] = 87
    elif event_type == "omega.ci.run.started":
        data.update(run_id="run-1", status="in_progress")
    elif event_type == "omega.ci.run.passed":
        data.update(run_id="run-1", conclusion="success")
    elif event_type == "omega.ci.run.failed":
        data.update(run_id="run-1", conclusion="failure")
    return CloudEvent(
        specversion="1.0",
        id=f"event-{event_type.rsplit('.', 1)[-1]}",
        source="urn:test:github",
        type=event_type,
        datacontenttype="application/json",
        dataschema=f"urn:omega:schema:{event_type}:1",
        data=data,
    )


def test_reducer_tracks_head_review_ci_and_deterministic_identity() -> None:
    state = reduce_pr_state(None, _event("omega.sdlc.pr.opened"))
    state = reduce_pr_state(state, _event("omega.sdlc.review.approved"))
    state = reduce_pr_state(state, _event("omega.ci.run.passed"))
    result = projection(state)
    assert (result["repo"], result["number"], result["head_sha"]) == (
        "acme/project",
        7,
        "head-a",
    )
    assert result["review"]["approved"]["reviewer"] == "reviewer"
    assert result["ci"]["run-1"]["status"] == "passed"
    assert result["id"] == projection(state)["id"]
    assert serialize_projection(state) == serialize_projection(state)


def test_new_head_resets_head_scoped_state_and_stale_inputs_cannot_regress() -> None:
    state = reduce_pr_state(None, _event("omega.sdlc.pr.pushed"))
    state = reduce_pr_state(state, _event("omega.sdlc.review.approved"))
    state = reduce_pr_state(
        state,
        _event("omega.sdlc.pr.pushed", head_sha="head-b", before_sha="head-a"),
        source="sdlc/pr",
        source_position=(0, 2, 1),
    )
    stale_review = reduce_pr_state(
        state,
        _event("omega.sdlc.review.changes_requested", head_sha="head-a"),
        source="sdlc/review",
        source_position=(0, 8, 1),
    )
    stale_push = reduce_pr_state(
        state,
        _event("omega.sdlc.pr.pushed", head_sha="head-c", before_sha="head-b"),
        source="sdlc/pr",
        source_position=(0, 2, 0),
    )
    assert state["head_sha"] == "head-b"
    assert state["review"] == {}
    assert stale_review["review"] == {}
    assert stale_push["head_sha"] == "head-b"
    assert stale_push["source_positions"]["sdlc/pr#0"] == [0, 2, 1]


def test_position_order_is_partition_local_not_cross_topic() -> None:
    state = reduce_pr_state(
        None,
        _event("omega.sdlc.pr.pushed"),
        source="sdlc/pr",
        source_position=(0, 4, 9),
    )
    same_source_old = reduce_pr_state(
        state,
        _event("omega.sdlc.pr.pushed", head_sha="old", before_sha="head-a"),
        source="sdlc/pr",
        source_position=(0, 4, 8),
    )
    another_topic = reduce_pr_state(
        state,
        _event("omega.sdlc.review.approved"),
        source="sdlc/review",
        source_position=(0, 1, 0),
    )
    assert same_source_old["head_sha"] == "head-a"
    assert another_topic["review"]["approved"]["reviewer"] == "reviewer"


def test_conflicting_push_without_position_cannot_replace_known_head() -> None:
    state = reduce_pr_state(None, _event("omega.sdlc.pr.pushed"))
    state = reduce_pr_state(
        state,
        _event("omega.sdlc.pr.pushed", head_sha="head-b", before_sha="head-a"),
    )
    conflicting = reduce_pr_state(
        state,
        _event("omega.sdlc.pr.pushed", head_sha="head-c", before_sha="head-a"),
    )
    assert conflicting["head_sha"] == "head-b"


def test_closed_and_merged_transitions_are_terminal() -> None:
    state = reduce_pr_state(None, _event("omega.sdlc.pr.closed"))
    assert projection(state)["status"] == "closed"
    state = reduce_pr_state(None, _event("omega.sdlc.pr.merged"))
    state = reduce_pr_state(state, _event("omega.sdlc.pr.closed", merged=False))
    state = reduce_pr_state(state, _event("omega.sdlc.pr.opened"))
    state = reduce_pr_state(
        state,
        _event("omega.sdlc.pr.pushed", head_sha="head-b", before_sha="head-a"),
    )
    assert projection(state)["status"] == "merged"
    assert projection(state)["merged"] is True
    assert projection(state)["head_sha"] == "head-a"


def test_closed_state_cannot_be_reopened_or_updated_by_late_events() -> None:
    closed = reduce_pr_state(None, _event("omega.sdlc.pr.closed"))
    closed = reduce_pr_state(
        closed,
        _event("omega.sdlc.pr.pushed", head_sha="head-b", before_sha="head-a"),
    )
    reopened = reduce_pr_state(closed, _event("omega.sdlc.pr.opened"))
    reviewed = reduce_pr_state(reopened, _event("omega.sdlc.review.approved"))
    ci_updated = reduce_pr_state(reviewed, _event("omega.ci.run.passed"))
    result = projection(ci_updated)
    assert result["status"] == "closed"
    assert result["head_sha"] == "head-a"
    assert result["closed"] is True
    assert result["merged"] is False
    assert result["review"] == {}
    assert result["ci"] == {}


def test_reducer_does_not_mutate_explicit_prior_state() -> None:
    previous = reduce_pr_state(None, _event("omega.sdlc.pr.opened"))
    snapshot = json.loads(json.dumps(previous))
    updated = reduce_pr_state(previous, _event("omega.sdlc.review.approved"))
    assert previous == snapshot
    assert updated["review"]["approved"]["reviewer"] == "reviewer"


class _FunctionContext:
    def __init__(self) -> None:
        self.state: dict[str, bytes] = {}
        self.position: tuple[int, int, int] = (0, 4, 2)
        self.published: list[tuple[str, bytes, str]] = []

    def get_state(self, key: str) -> bytes | None:
        return self.state.get(key)

    def put_state(self, key: str, value: bytes) -> None:
        self.state[key] = value

    def get_current_message_topic_name(self) -> str:
        return "persistent://omega/sdlc/pr"

    def get_message_id(self) -> object:
        return _MessageId(self.position)

    def publish(self, topic: str, message: bytes, **options: Any) -> None:
        message_conf = options["message_conf"]
        self.published.append((topic, message, str(message_conf["partition_key"])))
        callback = options["callback"]
        callback(0, None)


class _MessageId:
    def __init__(self, position: tuple[int, int, int]) -> None:
        self.position = position

    def get_partition(self) -> int:
        return self.position[0]

    def get_ledger_id(self) -> int:
        return self.position[1]

    def get_entry_id(self) -> int:
        return self.position[2]


def test_process_persists_before_output_and_duplicate_reemits_crash_gap() -> None:
    context = _FunctionContext()
    source = _event("omega.sdlc.pr.opened").to_bytes()
    process(source, context)
    assert context.state
    persisted = json.loads(next(iter(context.state.values())))
    assert persisted["repo"] == "acme/project"
    assert len(context.published) == 1
    first_topic, first_payload, first_key = context.published[0]
    assert first_topic == "persistent://omega/sdlc/pr-state"
    assert first_key == "acme/project#7"
    # Simulate the crash after durable state commit but before output publication:
    # replay must output current projection rather than suppressing the duplicate.
    process(source, context)
    assert context.published[1] == (first_topic, first_payload, first_key)
    assert json.loads(first_payload)["key"] == "acme/project#7"


def test_process_uses_source_positions_and_reemits_current_projection() -> None:
    context = _FunctionContext()
    process(_event("omega.sdlc.pr.pushed").to_bytes(), context)
    context.position = (0, 4, 3)
    process(
        _event(
            "omega.sdlc.pr.pushed", head_sha="head-b", before_sha="head-a"
        ).to_bytes(),
        context,
    )
    context.position = (0, 4, 2)
    process(
        _event(
            "omega.sdlc.pr.pushed", head_sha="head-c", before_sha="head-b"
        ).to_bytes(),
        context,
    )
    _, newer, stale = context.published
    assert json.loads(newer[1])["head_sha"] == "head-b"
    assert stale == newer


def test_output_serialization_is_stable() -> None:
    state = reduce_pr_state(None, _event("omega.sdlc.pr.opened"))
    assert serialize_projection(state) == json.dumps(
        projection(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def test_process_rejects_malformed_cloud_event_without_state_write() -> None:
    context = _FunctionContext()
    process(b"not-json", context)
    process(b'{"specversion":"1.0","type":"omega.sdlc.pr.opened"}', context)
    assert context.state == {}


def test_pr_state_projector_process_adapter_ignores_malformed_input() -> None:
    from omega_prime.eventstream.functions.pr_state_projector import process

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

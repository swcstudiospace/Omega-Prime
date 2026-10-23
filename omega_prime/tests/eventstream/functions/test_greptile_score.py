"""Contract tests for Greptile score parsing and keyed Function output."""

from __future__ import annotations

from collections.abc import Mapping
from threading import Thread
from typing import Any

import pytest

from omega_prime.eventstream.envelope import CloudEvent
from omega_prime.eventstream.functions.greptile_score import (
    NoScore,
    ScoreResult,
    parse_greptile_score,
    process,
)
from omega_prime.eventstream.taxonomy import derive_key

IDENTITY = {
    "repo": "acme/widget",
    "pr": 17,
    "head_sha": "abc123",
    "key": "acme/widget#17",
}


class _Context:
    def __init__(
        self, properties: Mapping[str, str], publish_result: object = 0
    ) -> None:
        self.properties = properties
        self.publish_result = publish_result
        self.published: list[tuple[str, bytes, str]] = []

    def get_message_properties(self) -> Mapping[str, str]:
        return self.properties

    def publish(self, topic: str, message: bytes, **options: Any) -> None:
        message_conf = options["message_conf"]
        self.published.append((topic, message, str(message_conf["partition_key"])))
        callback = options["callback"]
        Thread(target=callback, args=(self.publish_result, None), daemon=True).start()


def _commented_event() -> CloudEvent:
    return CloudEvent(
        specversion="1.0",
        id="comment-1",
        source="urn:test:github",
        type="omega.sdlc.review.commented",
        datacontenttype="application/json",
        dataschema="urn:omega:schema:omega.sdlc.review.commented:1",
        data={
            "repo": "acme/widget",
            "number": 17,
            "head_sha": "abc123",
            "actor": "greptile-apps[bot]",
            "reviewer": "greptile-apps[bot]",
            "comment_id": "c-1",
        },
    )


@pytest.mark.parametrize(
    "text,score",
    [
        ("Score: 92", 92),
        ("Greptile score: 92%", 92),
        ("Confidence score: 92/100", 92),
        ("Confidence score: 4/5", 80),
        ("<!-- greptile_confidence_score: 5 -->", 100),
    ],
)
def test_explicit_score_grammars_preserve_identity(text: str, score: int) -> None:
    result = parse_greptile_score(text, IDENTITY)
    assert result == ScoreResult("acme/widget", 17, "abc123", "acme/widget#17", score)


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Review looks good", "score_absent_or_malformed"),
        ("Score: ninety-two", "score_absent_or_malformed"),
        ("Score: 101", "score_out_of_range"),
        ("Score: 92\nScore: 91", "score_ambiguous"),
    ],
)
def test_invalid_scores_are_explicit_no_result(text: str, reason: str) -> None:
    result = parse_greptile_score(text, IDENTITY)
    assert isinstance(result, NoScore)
    assert result.reason == reason


def test_invalid_or_incomplete_identity_has_no_result() -> None:
    for identity in (
        {**IDENTITY, "pr": True},
        {**IDENTITY, "head_sha": ""},
        {**IDENTITY, "key": "acme/other#17"},
    ):
        result = parse_greptile_score("Score: 92", identity)
        assert isinstance(result, NoScore)
        assert result.reason == "identity_missing_or_malformed"


def test_process_publishes_only_typed_score_and_preserves_pr_key() -> None:
    event = _commented_event()
    key = derive_key(event.type, event.data)
    context = _Context({"comment_body": "Review complete\nGreptile score: 92%"})

    process(event.to_bytes(), context)
    assert len(context.published) == 1
    topic, payload, published_key = context.published[0]
    output = CloudEvent.from_bytes(payload)
    assert topic == "persistent://omega/sdlc/review"
    assert published_key == key == "acme/widget#17"
    assert output.type == "omega.sdlc.review.greptile_scored"
    assert output.data == {
        "repo": "acme/widget",
        "number": 17,
        "head_sha": "abc123",
        "actor": "greptile",
        "score": 92,
    }
    process(event.to_bytes(), context)
    assert context.published[0][1] == context.published[1][1]


def test_process_has_no_output_for_absent_or_invalid_comment_body() -> None:
    event = _commented_event()
    no_body = _Context({})
    invalid = _Context({"comment_body": "Score: 101"})
    malformed = _Context({"comment_body": "Score: 92\nScore: 91"})

    process(event.to_bytes(), no_body)
    process(event.to_bytes(), invalid)
    process(event.to_bytes(), malformed)
    process(b"not-json", invalid)
    assert not no_body.published and not invalid.published and not malformed.published


def test_process_propagates_output_publish_failure() -> None:
    event = _commented_event()
    context = _Context({"comment_body": "Score: 92"}, publish_result="Result.Timeout")

    with pytest.raises(RuntimeError, match=r"Result\.Timeout"):
        process(event.to_bytes(), context)


def test_process_rejects_own_output_source() -> None:
    original = _commented_event()
    event = CloudEvent(
        specversion=original.specversion,
        id=original.id,
        source="urn:omega:function:greptile-score",
        type=original.type,
        datacontenttype=original.datacontenttype,
        dataschema=original.dataschema,
        data=original.data,
    )
    context = _Context({"comment_body": "Score: 92"})

    process(event.to_bytes(), context)
    assert not context.published


def test_process_rejects_non_greptile_actors() -> None:
    event = _commented_event()
    forged = CloudEvent(
        specversion=event.specversion,
        id=event.id,
        source=event.source,
        type=event.type,
        datacontenttype=event.datacontenttype,
        dataschema=event.dataschema,
        data={**dict(event.data), "actor": "alice", "reviewer": "alice"},
    )
    context = _Context({"comment_body": "Score: 100"})
    process(forged.to_bytes(), context)
    assert not context.published


def test_greptile_score_process_adapter_ignores_malformed_input() -> None:
    from omega_prime.eventstream.functions.greptile_score import process

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

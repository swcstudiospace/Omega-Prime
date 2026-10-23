"""Redacting CloudEvent publishers."""

from __future__ import annotations

import json
import stat
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners.publisher import (
    CloudEvent,
    EventStreamPublisher,
    JsonlPublisher,
    NullPublisher,
    PublisherUnavailable,
    UnsafeEventError,
    event_type,
    make_publisher,
    redact_event,
)

SECRET = "jit-config-blob-ABCDEFGHIJKLMNOP"


def test_event_types() -> None:
    assert event_type("launched") == "omega.ci.runner.launched"
    assert event_type("woke") == "omega.ci.desktop.woke"
    with pytest.raises(ValueError):
        event_type("nope")


def test_cloudevent_shape() -> None:
    event = CloudEvent.of("busy", {"name": "omega-ci-1-a"}, subject="omega-ci-1-a")
    body = event.to_dict()
    assert body["specversion"] == "1.0" and body["subject"] == "omega-ci-1-a"
    assert body["time"].endswith("Z") and body["source"] == "omega-prime/ci-runners"
    assert "subject" not in CloudEvent.of("busy", {}).to_dict()


def test_redaction_of_known_and_pattern_secrets() -> None:
    event = CloudEvent.of(
        "error", {"detail": f"cfg={SECRET}", "tok": "ghp_" + "a" * 36}
    )
    text = json.dumps(redact_event(event, [SECRET, ""]))
    assert SECRET not in text and "ghp_" + "a" * 36 not in text


def test_secret_as_key_is_unsafe() -> None:
    event = CloudEvent.of("error", {SECRET: 1})
    with pytest.raises(UnsafeEventError, match="survived"):
        redact_event(event, [SECRET])


def test_non_json_is_unsafe() -> None:
    with pytest.raises(UnsafeEventError, match="plain JSON"):
        redact_event(CloudEvent.of("error", {"x": object()}))
    with pytest.raises(UnsafeEventError):
        redact_event(CloudEvent.of("error", {"x": float("nan")}))


def test_null_publisher_still_checks() -> None:
    pub = NullPublisher()
    pub.publish(CloudEvent.of("idle", {}))
    assert pub.dropped == 1
    with pytest.raises(UnsafeEventError):
        pub.publish(CloudEvent.of("error", {SECRET: 1}), known_secrets=[SECRET])
    assert pub.dropped == 1


def test_jsonl_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "events.jsonl"
    pub = JsonlPublisher(path)
    pub.publish(CloudEvent.of("demand", {"jit": SECRET}), known_secrets=[SECRET])
    pub.publish(CloudEvent.of("idle", {}))
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and SECRET not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_eventstream_sink() -> None:
    got: list[Any] = []
    EventStreamPublisher(got.append).publish(
        CloudEvent.of("woke", {"t": SECRET}), known_secrets=[SECRET]
    )
    assert got[0]["type"] == "omega.ci.desktop.woke" and SECRET not in str(got)


def test_make_publisher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(make_publisher("null", tmp_path), NullPublisher)
    assert isinstance(make_publisher("jsonl", tmp_path / "e"), JsonlPublisher)
    with pytest.raises(PublisherUnavailable, match="unknown"):
        make_publisher("kafka", tmp_path)
    monkeypatch.setitem(sys.modules, "omega_prime.eventstream", None)
    with pytest.raises(PublisherUnavailable, match="00046"):
        make_publisher("eventstream", tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "omega_prime.eventstream",
        types.ModuleType("omega_prime.eventstream"),
    )
    with pytest.raises(PublisherUnavailable, match="sink"):
        make_publisher("eventstream", tmp_path)
    sink_pub = make_publisher("eventstream", tmp_path, sink=lambda _: None)
    assert isinstance(sink_pub, EventStreamPublisher)

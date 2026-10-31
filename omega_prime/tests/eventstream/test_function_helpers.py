"""Coverage for Pulsar Function helpers and projection edges."""

from __future__ import annotations

import pytest


def test_publish_keyed_rejects_missing_publish_and_failures() -> None:
    from omega_prime.eventstream.functions import publish_keyed

    class NoPublish:
        pass

    with pytest.raises(TypeError, match="keyed publish"):
        publish_keyed(NoPublish(), "t", b"{}", "k")

    class SlowPublish:
        def publish(self, *args, **kwargs):
            # never invoke callback -> timeout
            return None

    with pytest.raises(TimeoutError, match="timed out"):
        # monkeypatch wait via short timeout by patching Event.wait
        from omega_prime.eventstream import functions as fn

        original = fn.Event

        class FastEvent:
            def __init__(self):
                self._set = False

            def set(self):
                self._set = True

            def wait(self, timeout=None):
                return False

        fn.Event = FastEvent  # type: ignore[misc, assignment]
        try:
            publish_keyed(SlowPublish(), "t", b"{}", "k")
        finally:
            fn.Event = original  # type: ignore[misc]

    class FailPublish:
        def publish(self, *args, **kwargs):
            cb = kwargs["callback"]
            cb(RuntimeError("sink down"), None)

    with pytest.raises(RuntimeError, match="sink down"):
        publish_keyed(FailPublish(), "t", b"{}", "k")

    class BadCode:
        def publish(self, *args, **kwargs):
            kwargs["callback"](7, None)

    with pytest.raises(RuntimeError, match="publish failed"):
        publish_keyed(BadCode(), "t", b"{}", "k")


def test_publish_keyed_accepts_ok_result() -> None:
    from omega_prime.eventstream.functions import publish_keyed

    class OkPublish:
        def publish(self, *args, **kwargs):
            kwargs["callback"]("Result.Ok", None)

    publish_keyed(OkPublish(), "t", b"{}", "k")


def test_pr_state_projector_accepts_raw_mapping_and_rejects_bad_type() -> None:
    from omega_prime.eventstream.functions.pr_state_projector import reduce_pr_state

    with pytest.raises(ValueError, match="unsupported PR projection"):
        reduce_pr_state(None, {"type": 1, "data": {}})

    with pytest.raises(ValueError, match="unsupported PR projection"):
        reduce_pr_state(None, {"type": "omega.unknown", "data": {}})

    with pytest.raises(ValueError, match="must be an object"):
        reduce_pr_state(
            None,
            {"type": "omega.sdlc.pr.opened", "data": "nope"},
        )

    state = reduce_pr_state(
        None,
        {
            "type": "omega.sdlc.pr.opened",
            "data": {
                "repo": "owner/repo",
                "number": 1,
                "head_sha": "a" * 40,
                "actor": "tester",
                "title": "hello",
                "base_ref": "main",
            },
        },
    )
    assert state["repo"] == "owner/repo"
    assert state["number"] == 1

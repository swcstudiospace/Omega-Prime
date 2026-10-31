"""Keyed output support for pure Pulsar process-runtime transformations."""

from __future__ import annotations

from threading import Event
from typing import Any


def publish_keyed(context: Any, topic: str, payload: bytes, key: str) -> None:
    """Publish a keyed output and wait for its actual completion callback.

    Pulsar's stock ProcessRuntime output sink does not forward a source message
    key. The public Context.publish API does, so process adapters use it and
    do not return until the sink has acknowledged or rejected the output.
    """
    publish = getattr(context, "publish", None)
    if not callable(publish):
        raise TypeError("Pulsar Function context does not support keyed publish")

    completed = Event()
    outcomes: list[object] = []

    def on_complete(result: object, message: object) -> None:
        del message
        outcomes.append(result)
        completed.set()

    publish(
        topic,
        payload,
        properties={"content_type": "application/cloudevents+json"},
        message_conf={"partition_key": key},
        callback=on_complete,
    )
    if not completed.wait(timeout=30):
        raise TimeoutError("Pulsar Function output acknowledgement timed out")

    result = outcomes[0]
    if isinstance(result, BaseException):
        raise result
    if result != 0 and str(result).casefold() not in {"ok", "result.ok"}:
        raise RuntimeError(f"Pulsar Function output publish failed: {result!r}")


__all__ = ["publish_keyed"]

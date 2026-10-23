"""Webhook boundary: body cap, HMAC, allowlist, dedupe, ASGI helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from omega_prime.ci_runners.settings import MAX_BODY_BYTES
from omega_prime.ci_runners.webhook import (
    BodyTooLarge,
    ClientDisconnected,
    DeliveryLRU,
    WebhookRejected,
    bearer_ok,
    header_map,
    read_body,
    send_json,
    sign,
    verify_delivery,
    verify_signature,
)

from ._support import WEBHOOK_SECRET, encode, job_payload, signed_headers


def _hdrs(pairs: list[tuple[str, str]]) -> dict[str, str]:
    return {k.lower(): v for k, v in pairs}


def _receiver(messages: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    async def receive() -> dict[str, Any]:
        return messages.pop(0)

    return receive


def test_read_body_chunks_and_cap() -> None:
    msgs: list[dict[str, Any]] = [
        {"type": "lifespan.noise"},
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": None, "more_body": True},
        {"type": "http.request", "body": b"c"},
    ]
    assert asyncio.run(read_body(_receiver(msgs))) == b"abc"
    big = [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
    ]
    with pytest.raises(BodyTooLarge):
        asyncio.run(read_body(_receiver(big), limit=1000))
    with pytest.raises(ClientDisconnected):
        asyncio.run(read_body(_receiver([{"type": "http.disconnect"}])))


def test_header_map_rejects_oversized_content_length() -> None:
    scope = {"headers": [(b"Content-Length", str(MAX_BODY_BYTES + 1).encode())]}
    with pytest.raises(BodyTooLarge):
        header_map(scope)
    with pytest.raises(WebhookRejected, match="bad_content_length"):
        header_map({"headers": [(b"content-length", b"lots")]})
    assert header_map({"headers": [(b"X-A", b"1")]}) == {"x-a": "1"}


def test_signature_and_bearer() -> None:
    body = b"{}"
    good = sign(WEBHOOK_SECRET, body)
    assert verify_signature(WEBHOOK_SECRET, body, good)
    assert not verify_signature(WEBHOOK_SECRET, b"{ }", good)
    assert not verify_signature(WEBHOOK_SECRET, body, good.replace("sha256=", "sha1="))
    assert not verify_signature(b"", body, good)
    assert not verify_signature(WEBHOOK_SECRET, body, None)
    assert bearer_ok("Bearer tok", "tok")
    assert not bearer_ok("Bearer nope", "tok")
    assert not bearer_ok("Basic tok", "tok")
    assert not bearer_ok("Bearer tok", "")


def test_verify_delivery_happy_path() -> None:
    body = encode(job_payload())
    delivery = verify_delivery(_hdrs(signed_headers(body)), body, WEBHOOK_SECRET)
    assert delivery.event == "workflow_job" and delivery.delivery_id == "d-1"
    assert delivery.payload["action"] == "queued"


@pytest.mark.parametrize(
    ("body", "headers", "status", "reason"),
    [
        (b"{}", {"x-hub-signature-256": "sha256=00"}, 401, "bad_signature"),
        (b"{}", {"x-hub-signature-256": "sha256=\u00e9"}, 401, "bad_signature"),
        (
            b"{}",
            {"x-hub-signature-256": "sha256=" + "\u00e9" * 64},
            401,
            "bad_signature",
        ),
        (b"{}", {"x-hub-signature-256": "sha256=" + "G" * 64}, 401, "bad_signature"),
        (b"{}", {"x-github-delivery": ""}, 400, "bad_delivery_id"),
        (b"{}", {"x-github-delivery": "d" * 129}, 400, "bad_delivery_id"),
        (b"{}", {"x-github-event": "push"}, 202, "event_ignored"),
        (b"not json", {}, 400, "bad_json"),
        (b"[1]", {}, 400, "bad_json"),
        (b"\xff", {}, 400, "bad_json"),
    ],
)
def test_verify_delivery_rejections(
    body: bytes, headers: dict[str, str], status: int, reason: str
) -> None:
    merged = _hdrs(signed_headers(body)) | headers
    with pytest.raises(WebhookRejected) as info:
        verify_delivery(merged, body, WEBHOOK_SECRET)
    assert (info.value.status, info.value.reason) == (status, reason)


def test_verify_delivery_size_checked_before_signature() -> None:
    body = b"x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(BodyTooLarge):
        verify_delivery({}, body, WEBHOOK_SECRET)


def test_delivery_lru() -> None:
    lru = DeliveryLRU(size=2)
    assert not lru.seen("a")
    lru.commit("a")
    lru.commit("b")
    lru.commit("a")
    lru.commit("c")
    assert lru.seen("a") and lru.seen("c") and not lru.seen("b")
    assert len(lru) == 2
    with pytest.raises(ValueError):
        DeliveryLRU(size=0)


def test_send_json() -> None:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(send_json(send, 418, {"ok": True}))
    assert sent[0]["status"] == 418
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
    assert sent[1]["body"] == b'{"ok":true}'

"""Webhook boundary: bounded body, HMAC-SHA256, event allowlist, dedupe.

Nothing here parses a payload before its signature checks out, and nothing
buffers more than :data:`MAX_BODY_BYTES` of an untrusted body (chunked or
not). Also home to the tiny plain-ASGI helpers both HTTP apps share.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from omega_prime.ci_runners.settings import MAX_BODY_BYTES

ALLOWED_EVENTS = frozenset({"workflow_job", "ping"})
DEDUPE_SIZE = 4096
_SIG_PREFIX = "sha256="
_SIG_RE = re.compile(r"sha256=[0-9a-f]{64}")

Scope = Mapping[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class WebhookRejected(Exception):
    """Reject with an HTTP status. ``reason`` is a fixed, secret-free code."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class BodyTooLarge(WebhookRejected):
    def __init__(self) -> None:
        super().__init__(413, "body_too_large")


class ClientDisconnected(Exception):
    """The client went away before the body finished."""


async def read_body(receive: Receive, limit: int = MAX_BODY_BYTES) -> bytes:
    """Accumulate an ASGI request body, failing as soon as it passes ``limit``."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        kind = message.get("type")
        if kind == "http.disconnect":
            raise ClientDisconnected
        if kind != "http.request":
            continue
        chunk = message.get("body", b"") or b""
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def header_map(scope: Scope) -> dict[str, str]:
    """Lower-cased header names → latin-1 values (last one wins)."""
    out: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", ()):
        out[bytes(raw_name).decode("latin-1").lower()] = bytes(raw_value).decode(
            "latin-1"
        )
    if "content-length" in out:
        try:
            if int(out["content-length"]) > MAX_BODY_BYTES:
                raise BodyTooLarge
        except ValueError:
            raise WebhookRejected(400, "bad_content_length") from None
    return out


async def send_json(send: Send, status: int, payload: Any) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def sign(secret: bytes, body: bytes) -> str:
    return _SIG_PREFIX + hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, body: bytes, header: str | None) -> bool:
    """Constant-time check of ``X-Hub-Signature-256``."""
    if not secret or not header:
        return False
    candidate = header.strip()
    if not _SIG_RE.fullmatch(candidate):
        return False  # wrong prefix, length or a non-hex/non-ASCII digest
    return hmac.compare_digest(sign(secret, body).encode(), candidate.encode())


def bearer_ok(header: str | None, token: str) -> bool:
    """Constant-time ``Authorization: Bearer <token>`` check."""
    if not token or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), token.encode())


@dataclass(frozen=True)
class Delivery:
    event: str
    delivery_id: str
    payload: Mapping[str, Any]


def verify_delivery(headers: Mapping[str, str], body: bytes, secret: bytes) -> Delivery:
    """Authenticate then parse. Raises :class:`WebhookRejected`."""
    if len(body) > MAX_BODY_BYTES:
        raise BodyTooLarge
    if not verify_signature(secret, body, headers.get("x-hub-signature-256")):
        raise WebhookRejected(401, "bad_signature")
    event = headers.get("x-github-event", "")
    delivery_id = headers.get("x-github-delivery", "").strip()
    if not delivery_id or len(delivery_id) > 128:
        raise WebhookRejected(400, "bad_delivery_id")
    if event not in ALLOWED_EVENTS:
        raise WebhookRejected(202, "event_ignored")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        raise WebhookRejected(400, "bad_json") from None
    if not isinstance(payload, dict):
        raise WebhookRejected(400, "bad_json")
    return Delivery(event=event, delivery_id=delivery_id, payload=payload)


class DeliveryLRU:
    """Bounded memory of seen ``X-GitHub-Delivery`` ids.

    ``seen`` is a read; ``commit`` records the id. Callers commit only after
    the delivery was actually admitted, so a 503 lets GitHub redeliver.
    """

    def __init__(self, size: int = DEDUPE_SIZE) -> None:
        if size < 1:
            raise ValueError("size must be positive")
        self._size = size
        self._ids: OrderedDict[str, None] = OrderedDict()

    def __len__(self) -> int:
        return len(self._ids)

    def seen(self, delivery_id: str) -> bool:
        return delivery_id in self._ids

    def commit(self, delivery_id: str) -> None:
        self._ids[delivery_id] = None
        self._ids.move_to_end(delivery_id)
        while len(self._ids) > self._size:
            self._ids.popitem(last=False)


__all__ = [
    "ALLOWED_EVENTS",
    "DEDUPE_SIZE",
    "BodyTooLarge",
    "ClientDisconnected",
    "Delivery",
    "DeliveryLRU",
    "WebhookRejected",
    "bearer_ok",
    "header_map",
    "read_body",
    "send_json",
    "sign",
    "verify_delivery",
    "verify_signature",
]

"""Shared hermetic helpers: fake clock, tiny ASGI driver, payload builders."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from omega_prime.ci_runners.webhook import sign

WEBHOOK_SECRET = b"test-webhook-secret-not-real"
ASGIApp = Callable[..., Awaitable[None]]


class FakeClock:
    """Monotonic fake time; ``wait`` advances instead of sleeping."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self) -> Any:
        return json.loads(self.body or b"null")


async def asgi_call(
    app: ASGIApp,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: Iterable[tuple[str, str]] = (),
    chunks: Sequence[bytes] | None = None,
    disconnect: bool = False,
) -> Response:
    """Drive one plain-ASGI HTTP request in-process."""
    parts = list(chunks) if chunks is not None else [body]
    messages: list[dict[str, Any]] = [
        {"type": "http.request", "body": part, "more_body": i < len(parts) - 1}
        for i, part in enumerate(parts)
    ]
    if disconnect:
        messages = [*messages[:-1], {"type": "http.disconnect"}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        await asyncio.Event().wait()  # pragma: no cover - never reached
        return {}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    await app(scope, receive, send)
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    if start is None:
        return Response(0)
    payload = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    hdrs = {k.decode(): v.decode() for k, v in start.get("headers", [])}
    return Response(start["status"], hdrs, payload)


def job_payload(
    action: str = "queued",
    *,
    job_id: int = 101,
    run_id: int = 9001,
    labels: Sequence[str] = ("self-hosted", "linux", "x64", "omega-ci"),
    repo: str = "acme/widgets",
    private: bool = True,
    visibility: str = "private",
    runner_name: str | None = None,
    runner_id: int | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "workflow_job": {
            "id": job_id,
            "run_id": run_id,
            "labels": list(labels),
            "runner_name": runner_name,
            "runner_id": runner_id,
        },
        "repository": {"full_name": repo, "private": private, "visibility": visibility},
    }


def signed_headers(
    body: bytes,
    *,
    event: str = "workflow_job",
    delivery: str = "d-1",
    secret: bytes = WEBHOOK_SECRET,
) -> list[tuple[str, str]]:
    return [
        ("X-GitHub-Event", event),
        ("X-GitHub-Delivery", delivery),
        ("X-Hub-Signature-256", sign(secret, body)),
        ("Content-Type", "application/json"),
    ]


def encode(payload: Any) -> bytes:
    return json.dumps(payload).encode()


class Ids:
    """Deterministic generation/suffix sources."""

    def __init__(self) -> None:
        self.n = 0

    def generation(self) -> str:
        self.n += 1
        return f"gen{self.n:04d}"

    def suffix(self) -> str:
        self.n += 1
        return f"s{self.n:03d}"[-4:]

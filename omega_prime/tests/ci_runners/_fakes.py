"""Injected fakes for adapter/controller tests: HTTP router, delayed, sink."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Collection
from typing import Any
from urllib.parse import urlsplit

from omega_prime.ci_runners.delayed import Deadline
from omega_prime.ci_runners.http import HttpRequest, HttpResponse
from omega_prime.ci_runners.publisher import CloudEvent, redact_event
from omega_prime.tests.ci_runners._secrets import placeholder

Reply = HttpResponse | BaseException | Callable[[HttpRequest], HttpResponse]


def resp(status: int, body: Any = None, **headers: str) -> HttpResponse:
    raw = b"" if body is None else json.dumps(body).encode()
    hdrs = {k.replace("_", "-").lower(): v for k, v in headers.items()}
    return HttpResponse(status, hdrs, raw)


class FakeHttp:
    """Synchronous transport: routes on (method, path); last reply is sticky."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Reply]] = {}
        self.calls: list[HttpRequest] = []
        self._lock = threading.Lock()

    def on(self, method: str, path: str, *replies: Reply) -> FakeHttp:
        self.routes[(method, path)] = list(replies)
        return self

    def __call__(self, request: HttpRequest) -> HttpResponse:
        parts = urlsplit(request.url)
        with self._lock:
            self.calls.append(request)
            queue = self.routes.get((request.method, parts.path))
            if not queue:
                return resp(599, {"error": "unrouted"})
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return reply(request)
        return reply

    def paths(self, method: str | None = None) -> list[str]:
        return [
            urlsplit(c.url).path
            for c in self.calls
            if method is None or c.method == method
        ]

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(c.body or b"null")
            for c in self.calls
            if urlsplit(c.url).path == path and c.body
        ]


class FakeDelayed:
    def __init__(self) -> None:
        self.armed: dict[tuple[str, str], float] = {}
        self.cancelled: list[tuple[str, str]] = []

    def schedule(
        self, name: str, generation: str, delay_s: float, event: Deadline
    ) -> Any:
        self.armed[(name, generation)] = delay_s
        return None

    def cancel(self, name: str, generation: str) -> bool:
        self.cancelled.append((name, generation))
        return self.armed.pop((name, generation), None) is not None

    def cancel_all(self) -> None:
        self.armed.clear()


class ListPublisher:
    """Captures the redacted dicts exactly as a real adapter would emit them."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(
        self, event: CloudEvent, *, known_secrets: Collection[str] = ()
    ) -> None:
        self.events.append(redact_event(event, known_secrets))

    def types(self) -> list[str]:
        return [e["type"].rsplit(".", 1)[1] for e in self.events]

    def text(self) -> str:
        return json.dumps(self.events)


class StaticToken:
    def __init__(self, token: str | None = None) -> None:
        self.value = token or placeholder("installation-token")

    async def token(self) -> str:
        return self.value

    def secrets(self) -> tuple[str, ...]:
        return (self.value,)

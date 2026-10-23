"""Client for the Desktop runner agent; the launch POST is also the wake.

Railway Serverless sleeps the Desktop; the first launch request wakes it
(D-120-05). Only connect failures and 502/503 are retried, inside a
request-inclusive 150 s budget, at most 16 attempts, with jittered
exponential backoff capped at 15 s. Nothing waits or starts after the budget
is spent, and 429 (capacity) is never retried. The JIT config travels only in
the launch body, over HTTPS, and is never logged or returned.
"""

from __future__ import annotations

import asyncio
import enum
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from omega_prime.ci_runners.http import (
    HttpRequest,
    HttpResponse,
    Transport,
    TransportError,
    send,
)
from omega_prime.ci_runners.jobs import AgentRunner
from omega_prime.ci_runners.settings import DEFAULT_WAKE_BUDGET_S, RUNNER_NAME_RE

MAX_ATTEMPTS = 16
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 15.0
REQUEST_TIMEOUT_S = 30.0
RETRYABLE = frozenset({502, 503})

Wait = Callable[[float], Awaitable[None]]


class LaunchOutcome(enum.Enum):
    ACCEPTED = "accepted"  # 201
    REPLAYED = "replayed"  # 200, same name+generation already running
    CAPACITY = "capacity"  # 429
    CONFLICT = "conflict"  # 409, other generation or draining
    REJECTED = "rejected"  # any other definitive status
    WAKE_FAILED = "wake_failed"  # budget/attempts spent on connect/502/503


@dataclass(frozen=True)
class WakeResult:
    outcome: LaunchOutcome
    status: int | None
    attempts: int
    elapsed_s: float

    @property
    def accepted(self) -> bool:
        return self.outcome in (LaunchOutcome.ACCEPTED, LaunchOutcome.REPLAYED)


class DesktopError(Exception):
    """Sanitized agent failure (operation + status only)."""

    def __init__(self, op: str, status: int | None) -> None:
        self.op = op
        self.status = status
        super().__init__(
            f"desktop {op}: {status if status is not None else 'no response'}"
        )


_OUTCOMES = {
    201: LaunchOutcome.ACCEPTED,
    200: LaunchOutcome.REPLAYED,
    429: LaunchOutcome.CAPACITY,
    409: LaunchOutcome.CONFLICT,
}


class DesktopClient:
    def __init__(
        self,
        base_url: str,
        token: Callable[[], str],
        transport: Transport,
        *,
        budget_s: float = DEFAULT_WAKE_BUDGET_S,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if urlsplit(base_url).scheme != "https":
            raise ValueError("the Desktop agent URL must be https")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport
        self.budget_s = budget_s
        self.max_attempts = max_attempts
        self._clock = clock
        self._wait = wait
        self._jitter = jitter

    def backoff(self, attempt: int) -> float:
        """Full-range jitter in [50%, 100%] of min(cap, base * 2^(n-1))."""
        ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (attempt - 1)))
        return ceiling * (0.5 + 0.5 * self._jitter())

    def _request(
        self, method: str, path: str, body: bytes | None, timeout: float
    ) -> HttpRequest:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        return HttpRequest(method, self.base_url + path, headers, body, timeout)

    async def _with_wake(
        self, method: str, path: str, body: bytes | None = None
    ) -> tuple[HttpResponse | None, int, float]:
        start = self._clock()
        deadline = start + self.budget_s
        attempts = 0
        response: HttpResponse | None = None
        while attempts < self.max_attempts:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            attempts += 1
            request = self._request(
                method, path, body, min(REQUEST_TIMEOUT_S, remaining)
            )
            try:
                # The socket timeout alone is per-operation (connect, each
                # read), so a trickling peer could outlive the budget; the
                # wait_for makes ``deadline`` a hard overall bound. A request
                # still running in its worker thread is abandoned, not awaited.
                response = await asyncio.wait_for(
                    send(self._transport, request), timeout=remaining
                )
            except (TransportError, TimeoutError):
                response = None
            if response is not None and response.status not in RETRYABLE:
                return response, attempts, self._clock() - start
            if attempts >= self.max_attempts:
                break
            delay = self.backoff(attempts)
            if self._clock() + delay >= deadline:
                break
            await self._wait(delay)
        return None, attempts, self._clock() - start

    async def launch(self, name: str, generation: str, jitconfig: str) -> WakeResult:
        """POST /v1/runners; the same body is reused on every retry."""
        if not RUNNER_NAME_RE.match(name) or not generation or not jitconfig:
            raise ValueError("launch needs a pool name, generation and JIT config")
        body = json.dumps(
            {"name": name, "generation": generation, "jitconfig": jitconfig}
        ).encode()
        response, attempts, elapsed = await self._with_wake("POST", "/v1/runners", body)
        if response is None:
            return WakeResult(LaunchOutcome.WAKE_FAILED, None, attempts, elapsed)
        outcome = _OUTCOMES.get(response.status, LaunchOutcome.REJECTED)
        return WakeResult(outcome, response.status, attempts, elapsed)

    async def inventory(self) -> list[AgentRunner]:
        response, _, _ = await self._with_wake("GET", "/v1/runners")
        if response is None or response.status != 200:
            raise DesktopError("inventory", response.status if response else None)
        try:
            rows = json.loads(response.body)
        except (UnicodeDecodeError, ValueError):
            raise DesktopError("inventory", response.status) from None
        if not isinstance(rows, list):
            raise DesktopError("inventory", response.status)
        out: list[AgentRunner] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name, generation = row.get("name"), row.get("generation")
            status, pid = row.get("status"), row.get("pid")
            if not isinstance(name, str) or not RUNNER_NAME_RE.match(name):
                continue
            if not isinstance(generation, str):
                continue
            out.append(
                AgentRunner(
                    name,
                    generation,
                    status if isinstance(status, str) else "unknown",
                    pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
                )
            )
        return out

    async def find(self, name: str, generation: str) -> bool | None:
        """Is exactly (name, generation) running? ``None`` when unknown."""
        try:
            rows = await self.inventory()
        except DesktopError:
            return None
        return any(r.name == name and r.generation == generation for r in rows)

    async def stop(self, name: str) -> bool:
        """DELETE /v1/runners/{name}. 202 and 404 both mean "not running"."""
        if not RUNNER_NAME_RE.match(name):
            raise ValueError("not a pool runner name")
        response, _, _ = await self._with_wake(
            "DELETE", f"/v1/runners/{quote(name, safe='')}"
        )
        if response is None:
            raise DesktopError("stop", None)
        if response.status in (202, 404):
            return True
        raise DesktopError("stop", response.status)


__all__ = [
    "BACKOFF_CAP_S",
    "MAX_ATTEMPTS",
    "DesktopClient",
    "DesktopError",
    "LaunchOutcome",
    "WakeResult",
]

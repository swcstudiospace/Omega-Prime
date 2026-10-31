"""Deterministic retry delay calculation and finite resource-creation waits."""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

T = TypeVar("T")


class _UniformRng(Protocol):
    def uniform(self, low: float, high: float, /) -> float: ...


Rng = random.Random | _UniformRng | Callable[[float, float], float] | None


def decorrelated_jitter(previous: float, rng: Rng = None) -> float:
    """Sample decorrelated jitter, capped after sampling at ten minutes."""
    if not math.isfinite(previous) or previous <= 0:
        raise ValueError("previous delay must be positive and finite")
    if rng is None:
        sample = random.uniform(1.0, 3.0 * previous)
    elif callable(rng):
        sample = rng(1.0, 3.0 * previous)
    else:
        sample = rng.uniform(1.0, 3.0 * previous)
    return min(600.0, sample)


async def create_with_backoff(
    create: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Rng = None,
    retryable: Callable[[Exception], bool] | None = None,
) -> T:
    """Retry transient resource-creation I/O failures a finite number of times.

    Optional broker clients can supply a predicate for their own transient
    connection exceptions without making this module import that SDK.
    """
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 1 <= attempts <= 5
    ):
        raise ValueError("creation attempts must be between one and five")
    is_retryable = retryable or (lambda error: isinstance(error, OSError))
    delay = 1.0
    for attempt in range(attempts):
        try:
            return await create()
        except Exception as exc:
            if attempt + 1 == attempts or not is_retryable(exc):
                raise
            delay = decorrelated_jitter(delay, rng)
            await wait(delay)
    raise AssertionError("unreachable")


__all__ = ["create_with_backoff", "decorrelated_jitter"]

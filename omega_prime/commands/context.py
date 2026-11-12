# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Host-scoped nested dispatch.

Core commands dispatch tools through ``registry.dispatch`` when nothing has
set a nested dispatcher. The Grok Bot host sets one for the duration of a
``tools/call`` so inner calls re-enter that call's roster and interceptor
chain. The context variable is empty for in-process callers (the agent
intercept, cron), which keep registry-gate semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

NestedDispatch = Callable[[str, dict[str, Any]], str]

_dispatch: ContextVar[NestedDispatch | None] = ContextVar(
    "omega_nested_dispatch", default=None
)
_depth: ContextVar[int] = ContextVar("omega_nested_depth", default=0)
_roster: ContextVar[frozenset[str] | None] = ContextVar(
    "omega_served_roster", default=None
)

# Two nested re-entries are allowed (a command, then a tool that command
# called). The next one is refused instead of recursing.
NESTED_DISPATCH_DEPTH = 2


def get_nested_dispatch() -> NestedDispatch | None:
    """The host dispatcher for this context, or None when unset."""
    return _dispatch.get()


def get_served_roster() -> frozenset[str] | None:
    """Tool names the active host call may serve, or None when unset."""
    return _roster.get()


def nested_depth() -> int:
    """How many nested host dispatches are already open."""
    return _depth.get()


@contextmanager
def nested_dispatch_scope(fn: NestedDispatch) -> Iterator[None]:
    """Bind ``fn`` as the nested dispatcher until the block exits."""
    token = _dispatch.set(fn)
    try:
        yield
    finally:
        _dispatch.reset(token)


@contextmanager
def served_roster_scope(names: frozenset[str] | None) -> Iterator[None]:
    """Bind the host roster until the block exits. None leaves the default."""
    if names is None:
        yield
        return
    token = _roster.set(names)
    try:
        yield
    finally:
        _roster.reset(token)


@contextmanager
def nested_depth_frame() -> Iterator[None]:
    """Count one nested host dispatch. Pair with :func:`nested_depth`."""
    token = _depth.set(_depth.get() + 1)
    try:
        yield
    finally:
        _depth.reset(token)


__all__ = [
    "NESTED_DISPATCH_DEPTH",
    "NestedDispatch",
    "get_nested_dispatch",
    "get_served_roster",
    "nested_depth",
    "nested_depth_frame",
    "nested_dispatch_scope",
    "served_roster_scope",
]

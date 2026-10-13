# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Prometheus metrics for the Grok Bot host, with no third-party dependency.

`MetricsRegistry` holds counters, gauges and histograms and renders them in the
Prometheus text exposition format 0.0.4. Everything here is thread-safe because tool
calls (and therefore `MetricsInterceptor`) run in worker threads while the HTTP
middleware runs on the event loop; no structure holds loop-bound state.

Label cardinality is bounded on purpose. HTTP paths are folded into a fixed set of
groups, tool names are only used when the host serves them, and every metric caps its
number of label children (extra label sets fold into one `overflow` child), so a
hostile client cannot grow memory by inventing label values.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from bisect import bisect_left
from collections.abc import Callable, Collection, Iterable, Sequence
from itertools import pairwise
from typing import Any

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omega_prime.grokbot.interceptors import ToolCall, ToolDenial, ToolOutcome
from omega_prime.grokbot.security import SCOPE_READ, principal_from_scope

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)
DEFAULT_MAX_CHILDREN = 1000
OVERFLOW_LABEL = "overflow"
DEFAULT_PATH_GROUPS: tuple[str, ...] = (
    "/sse",
    "/messages",
    "/mcp",
    "/healthz",
    "/readyz",
    "/metrics",
    "/manifest.json",
    "/admin",
)
OTHER_PATH = "other"
UNKNOWN_TOOL = "unknown"
AUTH_FAILURE_REASONS = frozenset({"missing", "malformed", "invalid", "other"})

_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_TOOL_STATUSES = frozenset({"ok", "error", "denied"})
_MAX_CODE_CHARS = 64


# --------------------------------------------------------------------------- format


def _format_value(value: float) -> str:
    """A sample value / `le` bound: integral floats without `.0`, `+Inf`, `NaN`."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _escape_label_value(value: str) -> str:
    """Escape `\\`, `"` and newline; other control characters become `\\uXXXX` text.

    The exposition format only defines three escapes, so a control character is
    rendered as a literal (escaped-backslash) `\\u000d` sequence: visible, never able
    to break the line structure, and still a valid label value.
    """
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif code < 0x20 or 0x7F <= code <= 0x9F:
            out.append(f"\\\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _escape_help(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _label_block(names: Sequence[str], values: Sequence[str], *extra: str) -> str:
    pairs = [
        f'{name}="{_escape_label_value(value)}"'
        for name, value in zip(names, values, strict=True)
    ]
    pairs.extend(extra)
    return "{" + ",".join(pairs) + "}" if pairs else ""


# --------------------------------------------------------------------------- metrics


class _Child:
    """One label set's state; shares the owning metric's lock."""

    __slots__ = ("_lock",)

    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock


class _CounterChild(_Child):
    __slots__ = ("_value",)

    def __init__(self, lock: threading.Lock) -> None:
        super().__init__(lock)
        self._value = 0.0

    def inc(self, n: float = 1.0) -> None:
        """Add `n` (must be >= 0)."""
        if not n >= 0:
            raise ValueError("counters can only increase")
        with self._lock:
            self._value += n


class _GaugeChild(_Child):
    __slots__ = ("_value",)

    def __init__(self, lock: threading.Lock) -> None:
        super().__init__(lock)
        self._value = 0.0

    def inc(self, n: float = 1.0) -> None:
        with self._lock:
            self._value += n

    def dec(self, n: float = 1.0) -> None:
        with self._lock:
            self._value -= n

    def set(self, value: float) -> None:
        with self._lock:
            self._value = float(value)


class _HistogramChild(_Child):
    __slots__ = ("_bounds", "_counts", "_sum")

    def __init__(self, lock: threading.Lock, bounds: tuple[float, ...]) -> None:
        super().__init__(lock)
        self._bounds = bounds
        self._counts = [0] * (len(bounds) + 1)  # last slot is +Inf
        self._sum = 0.0

    def observe(self, value: float) -> None:
        if math.isnan(value):
            raise ValueError("cannot observe NaN")
        index = bisect_left(self._bounds, value)
        with self._lock:
            self._counts[index] += 1
            self._sum += value


class _Metric:
    """Shared label handling and child bookkeeping for the three metric types."""

    kind = ""

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        *,
        max_children: int = DEFAULT_MAX_CHILDREN,
    ) -> None:
        if not _METRIC_NAME.fullmatch(name):
            raise ValueError(f"invalid metric name: {name!r}")
        names = tuple(label_names)
        for label in names:
            if not _LABEL_NAME.fullmatch(label) or label.startswith("__"):
                raise ValueError(f"invalid label name: {label!r}")
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate label names on {name}")
        if max_children < 1:
            raise ValueError("max_children must be at least 1")
        self.name = name
        self.help = help_text
        self.label_names = names
        self.max_children = max_children
        self._lock = threading.Lock()
        self._children: dict[tuple[str, ...], Any] = {}
        if not names:
            self._child(())

    def _new_child(self) -> Any:
        raise NotImplementedError

    def _child(self, key: tuple[str, ...]) -> Any:
        with self._lock:
            child = self._children.get(key)
            if child is not None:
                return child
            if len(self._children) >= self.max_children:
                key = (OVERFLOW_LABEL,) * len(self.label_names)
                child = self._children.get(key)
                if child is not None:
                    return child
            child = self._new_child()
            self._children[key] = child
            return child

    def labels(self, **values: str) -> Any:
        """The child for exactly this label set (names must match registration)."""
        if set(values) != set(self.label_names):
            raise ValueError(
                f"{self.name} takes labels {list(self.label_names)}, "
                f"got {sorted(values)}"
            )
        for label, value in values.items():
            if not isinstance(value, str):
                raise ValueError(f"label {label!r} of {self.name} must be a string")
        return self._child(tuple(values[name] for name in self.label_names))

    def _sorted_children(self) -> list[tuple[tuple[str, ...], Any]]:
        with self._lock:
            return sorted(self._children.items(), key=lambda item: item[0])

    def _header(self) -> list[str]:
        return [
            f"# HELP {self.name} {_escape_help(self.help)}",
            f"# TYPE {self.name} {self.kind}",
        ]

    def _samples(self) -> list[str]:
        raise NotImplementedError

    def render(self) -> list[str]:
        """Exposition lines for this metric (consistent snapshot)."""
        return [*self._header(), *self._samples()]


class Counter(_Metric):
    """A monotonically increasing value; `.inc()` directly when unlabeled."""

    kind = "counter"

    def _new_child(self) -> _CounterChild:
        return _CounterChild(self._lock)

    def inc(self, n: float = 1.0) -> None:
        self.labels().inc(n)

    def _samples(self) -> list[str]:
        with self._lock:
            rows = sorted((key, c._value) for key, c in self._children.items())
        return [
            f"{self.name}{_label_block(self.label_names, key)} {_format_value(value)}"
            for key, value in rows
        ]


class Gauge(_Metric):
    """A value that can go up and down; `.set/.inc/.dec` directly when unlabeled."""

    kind = "gauge"

    def _new_child(self) -> _GaugeChild:
        return _GaugeChild(self._lock)

    def inc(self, n: float = 1.0) -> None:
        self.labels().inc(n)

    def dec(self, n: float = 1.0) -> None:
        self.labels().dec(n)

    def set(self, value: float) -> None:
        self.labels().set(value)

    def _samples(self) -> list[str]:
        with self._lock:
            rows = sorted((key, c._value) for key, c in self._children.items())
        return [
            f"{self.name}{_label_block(self.label_names, key)} {_format_value(value)}"
            for key, value in rows
        ]


class Histogram(_Metric):
    """Observations bucketed cumulatively, plus `_sum` and `_count`."""

    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        *,
        buckets: Iterable[float] = DEFAULT_BUCKETS,
        max_children: int = DEFAULT_MAX_CHILDREN,
    ) -> None:
        if "le" in label_names:
            raise ValueError("'le' is reserved for histogram buckets")
        bounds = [float(b) for b in buckets]
        if bounds and math.isinf(bounds[-1]) and bounds[-1] > 0:
            bounds.pop()  # +Inf is always appended implicitly
        if any(math.isnan(b) or math.isinf(b) for b in bounds):
            raise ValueError("histogram buckets must be finite")
        if any(a >= b for a, b in pairwise(bounds)):
            raise ValueError("histogram buckets must be strictly increasing")
        self.buckets = tuple(bounds)
        super().__init__(name, help_text, label_names, max_children=max_children)

    def _new_child(self) -> _HistogramChild:
        return _HistogramChild(self._lock, self.buckets)

    def observe(self, value: float) -> None:
        self.labels().observe(value)

    def _samples(self) -> list[str]:
        with self._lock:
            rows = sorted(
                (key, list(c._counts), c._sum) for key, c in self._children.items()
            )
        lines: list[str] = []
        bounds = [*(_format_value(b) for b in self.buckets), "+Inf"]
        for key, counts, total in rows:
            running = 0
            for bound, count in zip(bounds, counts, strict=True):
                running += count
                block = _label_block(self.label_names, key, f'le="{bound}"')
                lines.append(f"{self.name}_bucket{block} {running}")
            block = _label_block(self.label_names, key)
            lines.append(f"{self.name}_sum{block} {_format_value(total)}")
            lines.append(f"{self.name}_count{block} {running}")
        return lines


class MetricsRegistry:
    """Named metrics plus the text exposition of all of them."""

    def __init__(self, *, max_children: int = DEFAULT_MAX_CHILDREN) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _Metric] = {}
        self._max_children = max_children

    def _register(self, metric: _Metric) -> Any:
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is None:
                self._metrics[metric.name] = metric
                return metric
        if type(existing) is not type(metric):
            raise ValueError(
                f"metric {metric.name} is already registered as a {existing.kind}"
            )
        if existing.label_names != metric.label_names:
            raise ValueError(
                f"metric {metric.name} is already registered with labels "
                f"{list(existing.label_names)}"
            )
        if (
            isinstance(existing, Histogram)
            and isinstance(metric, Histogram)
            and existing.buckets != metric.buckets
        ):
            raise ValueError(
                f"metric {metric.name} is already registered with other buckets"
            )
        return existing

    def counter(self, name: str, help: str = "", labels: Sequence[str] = ()) -> Counter:
        """Register (or fetch) a counter."""
        metric = Counter(name, help, labels, max_children=self._max_children)
        registered: Counter = self._register(metric)
        return registered

    def gauge(self, name: str, help: str = "", labels: Sequence[str] = ()) -> Gauge:
        """Register (or fetch) a gauge."""
        metric = Gauge(name, help, labels, max_children=self._max_children)
        registered: Gauge = self._register(metric)
        return registered

    def histogram(
        self,
        name: str,
        help: str = "",
        labels: Sequence[str] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> Histogram:
        """Register (or fetch) a histogram with fixed upper bounds."""
        metric = Histogram(
            name, help, labels, buckets=buckets, max_children=self._max_children
        )
        registered: Histogram = self._register(metric)
        return registered

    def render(self) -> str:
        """The whole registry in Prometheus text format 0.0.4 ('' when empty)."""
        with self._lock:
            metrics = list(self._metrics.values())
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n" if lines else ""


# ------------------------------------------------------------------------ middleware


class MetricsMiddleware:
    """Pure ASGI request metrics (never `BaseHTTPMiddleware`: it breaks SSE).

    Paths are folded into `path_groups` (prefix match on a path segment boundary,
    else `other`) so raw paths and query strings never become label values. The
    request is counted and its duration observed when the response STARTS, because an
    SSE response may never end; the in-flight gauge is decremented in a `finally`.
    """

    def __init__(
        self,
        app: ASGIApp,
        registry: MetricsRegistry,
        *,
        path_groups: Sequence[str] = DEFAULT_PATH_GROUPS,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.app = app
        self.path_groups = tuple(path_groups)
        self._clock = clock
        self._requests = registry.counter(
            "omega_http_requests_total",
            "HTTP requests by method, path group and status class.",
            ("method", "path", "status"),
        )
        self._duration = registry.histogram(
            "omega_http_request_duration_seconds",
            "HTTP time to first response byte by method and path group.",
            ("method", "path"),
        )
        self._in_flight = registry.gauge(
            "omega_http_in_flight", "HTTP requests currently being served."
        )

    def group_path(self, path: str) -> str:
        """The bounded label for `path`."""
        for group in self.path_groups:
            if path == group or path.startswith(group + "/"):
                return group
        return OTHER_PATH

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_method = str(scope.get("method", "")).upper()
        method = raw_method if raw_method in _HTTP_METHODS else "OTHER"
        group = self.group_path(str(scope.get("path", "")))
        started = self._clock()
        recorded = False

        def record(status: str) -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            self._requests.labels(method=method, path=group, status=status).inc()
            self._duration.labels(method=method, path=group).observe(
                max(0.0, self._clock() - started)
            )

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                record(_status_class(message.get("status", 0)))
            await send(message)

        self._in_flight.inc()
        failed = False
        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            failed = True
            raise
        finally:
            record("5xx" if failed else "none")
            self._in_flight.dec()


def _status_class(status: Any) -> str:
    if isinstance(status, int) and 100 <= status < 600:
        return f"{status // 100}xx"
    return "other"


# ------------------------------------------------------------------------ interceptor


class MetricsInterceptor:
    """Tool-call metrics as a `ToolCallInterceptor` (thread-safe, never denies).

    The `tool` label is the tool name only while it is in `tool_names()`; any other
    name (a client probing arbitrary names) is `unknown`. Note that `after` only runs
    for interceptors whose `before` ran, so denials issued EARLIER in the chain are
    invisible here; place this interceptor before the ones whose denials matter.
    """

    def __init__(
        self,
        registry: MetricsRegistry,
        *,
        tool_names: Callable[[], Collection[str]],
    ) -> None:
        self._tool_names = tool_names
        self._calls = registry.counter(
            "omega_tool_calls_total",
            "Tool calls by tool and status (ok, error, denied).",
            ("tool", "status"),
        )
        self._duration = registry.histogram(
            "omega_tool_call_duration_seconds",
            "Tool call duration by tool.",
            ("tool",),
        )
        self._in_flight = registry.gauge(
            "omega_tool_calls_in_flight", "Tool calls currently running."
        )
        self._denials = registry.counter(
            "omega_tool_denials_total", "Denied tool calls by denial code.", ("code",)
        )

    def _tool_label(self, name: str) -> str:
        try:
            return name if name in self._tool_names() else UNKNOWN_TOOL
        except Exception:
            return UNKNOWN_TOOL

    def before(self, call: ToolCall) -> ToolDenial | None:
        del call
        self._in_flight.inc()
        return None

    def after(self, call: ToolCall, outcome: ToolOutcome) -> None:
        tool = self._tool_label(call.name)
        status = outcome.status if outcome.status in _TOOL_STATUSES else "error"
        self._in_flight.dec()
        self._calls.labels(tool=tool, status=status).inc()
        seconds = max(0.0, outcome.duration_ms) / 1000.0
        self._duration.labels(tool=tool).observe(seconds)
        if outcome.denial_code is not None:
            self._denials.labels(code=outcome.denial_code[:_MAX_CODE_CHARS]).inc()


# -------------------------------------------------------------------------- helpers


def record_auth_failure(registry: MetricsRegistry) -> Callable[[dict[str, Any]], None]:
    """A callback for `AuthMiddleware(on_failure=...)` counting failures by reason."""
    failures = registry.counter(
        "omega_auth_failures_total",
        "Rejected requests by reason (missing, malformed, invalid, other).",
        ("reason",),
    )

    def on_failure(event: dict[str, Any]) -> None:
        reason = event.get("reason")
        label = reason if reason in AUTH_FAILURE_REASONS else "other"
        failures.labels(reason=label).inc()

    return on_failure


def register_build_info(
    registry: MetricsRegistry, *, version: str, package_version: str
) -> None:
    """Expose `omega_build_info{version,package_version} 1`."""
    gauge = registry.gauge(
        "omega_build_info",
        "Build information (always 1).",
        ("version", "package_version"),
    )
    gauge.labels(version=version, package_version=package_version).set(1)


# ------------------------------------------------- token rotation series (Phase 82)

TOKEN_ROTATIONS_TOTAL = "omega_token_rotations_total"
TOKEN_EXPIRY_TOTAL = "omega_token_expiry_total"
TOKEN_ACTIVE = "omega_token_active"
TOKEN_LABEL_HASH_LABEL = "label_hash"


def token_label_hash(label: str) -> str:
    """SHA-256 hex of a token label — the only label form metrics may carry."""
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class TokenMetrics:
    """Handles for the three token series (registered once per registry).

    `omega_token_rotations_total` advances on every successful `rotate`;
    `omega_token_expiry_total` advances when `observe` first sees an entry
    expired (overlap-close or TTL); `omega_token_active{label_hash}` tracks
    the live-entry count per label hash. Raw labels and token material never
    become label values. Denial visibility stays on the existing
    `omega_tool_denials_total{code}` — no denial series here.
    """

    def __init__(self, registry: MetricsRegistry) -> None:
        self._rotations = registry.counter(
            TOKEN_ROTATIONS_TOTAL, "Completed host-token rotations."
        )
        self._expiry = registry.counter(
            TOKEN_EXPIRY_TOTAL,
            "Token entries observed expired (overlap-close or TTL).",
        )
        self._active = registry.gauge(
            TOKEN_ACTIVE,
            "Live token entries by label hash.",
            (TOKEN_LABEL_HASH_LABEL,),
        )
        self._known_hashes: set[str] = set()
        self._expired_ids: set[str] = set()

    def record_rotation(self, n: float = 1.0) -> None:
        """Count `n` completed rotations (called once per successful rotate)."""
        self._rotations.inc(n)

    def record_expiry(self, n: float = 1.0) -> None:
        """Count `n` entries that transitioned to expired."""
        self._expiry.inc(n)

    def set_active(self, label_hash_value: str, count: float) -> None:
        """Set the live-entry gauge for one label hash."""
        self._active.labels(label_hash=label_hash_value).set(count)
        self._known_hashes.add(label_hash_value)

    def observe(self, records: Sequence[Any], now: float) -> dict[str, Any]:
        """Sync the gauge from token records; count newly-expired entries.

        `records` are `TokenRecord`-shaped (duck-typed: `.id`, `.label`,
        `.status(now)`). Stale label hashes reset to 0 so a fully-rotated
        label does not pin a ghost series. Returns
        `{"live": ..., "expired": ..., "newly_expired": [...]}`.
        """
        live: dict[str, int] = {}
        expired_now: set[str] = set()
        for record in records:
            status = record.status(now)
            if status == "active":
                digest = token_label_hash(record.label)
                live[digest] = live.get(digest, 0) + 1
            elif status == "expired":
                expired_now.add(record.id)
        for known in self._known_hashes - live.keys():
            self._active.labels(label_hash=known).set(0)
        for digest, count in live.items():
            self._active.labels(label_hash=digest).set(count)
        self._known_hashes |= live.keys()
        newly = sorted(expired_now - self._expired_ids)
        if newly:
            self._expiry.inc(len(newly))
        self._expired_ids |= expired_now
        return {
            "live": sum(live.values()),
            "expired": len(expired_now),
            "newly_expired": newly,
        }


def register_token_metrics(registry: MetricsRegistry) -> TokenMetrics:
    """Register (or fetch) the three token series; return their handles."""
    return TokenMetrics(registry)


class MetricsEndpoint:
    """Raw ASGI endpoint serving the registry; requires a principal with a scope.

    A request with no principal gets 401 and one without `required_scope` gets 403.
    A deployment that runs without authentication simply does not mount this
    endpoint behind a principal-less app; that policy belongs to the integrator.
    """

    def __init__(self, registry: MetricsRegistry, required_scope: str) -> None:
        self.registry = registry
        self.required_scope = required_scope

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        principal = principal_from_scope(scope)
        response: Response
        if principal is None:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Bearer realm="omega-prime"',
                    "Cache-Control": "no-store",
                },
            )
        elif not principal.allows(self.required_scope):
            response = JSONResponse(
                {"error": "forbidden"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        else:
            response = Response(
                self.registry.render().encode("utf-8", "replace"),
                media_type=None,
                headers={
                    "Content-Type": CONTENT_TYPE,
                    "Cache-Control": "no-store",
                },
            )
        await response(scope, receive, send)


def metrics_endpoint(
    registry: MetricsRegistry, *, required_scope: str = SCOPE_READ
) -> MetricsEndpoint:
    """The `/metrics` ASGI endpoint for `registry`."""
    return MetricsEndpoint(registry, required_scope)


__all__ = [
    "AUTH_FAILURE_REASONS",
    "CONTENT_TYPE",
    "DEFAULT_BUCKETS",
    "DEFAULT_PATH_GROUPS",
    "TOKEN_ACTIVE",
    "TOKEN_EXPIRY_TOTAL",
    "TOKEN_LABEL_HASH_LABEL",
    "TOKEN_ROTATIONS_TOTAL",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsEndpoint",
    "MetricsInterceptor",
    "MetricsMiddleware",
    "MetricsRegistry",
    "TokenMetrics",
    "metrics_endpoint",
    "record_auth_failure",
    "register_build_info",
    "register_token_metrics",
    "token_label_hash",
]

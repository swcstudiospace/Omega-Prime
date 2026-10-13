# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Per-caller usage quotas with a fixed soft-then-hard rollout.

Caller identity is the token-store label of the authenticating bearer token
(never raw token material). Rotation preserves labels
(`omega_prime.grokbot.tokens.rotate_token` mints the replacement with the
old entry's label), so quota accounting keyed on the label is stable across
`rotated_from`/`rotated_to` lineage without any identity primitive of its
own; deployments that rename labels at rotation may pass `label_resolver`
to fold lineage to a canonical label.

Modes (one key, ordered): `report` counts and exposes only; `soft`
additionally warns at >=80% of any configured limit; `hard` additionally
denies over-limit requests pre-dispatch with the existing denial pipeline
(`ToolDenial(code="quota")`, visible in `omega_tool_denials_total`).
Report mode enforces nothing: no denial, no warning, no behavior change.

An empty or missing quota document is fully inert: the engine registers no
series and counts nothing. A caller missing from the document is unlimited.
Callers without token identity (unauthenticated/local transports) are
counted under a fixed safe bucket and are NEVER denied in any mode — hard
mode is token-identity-scoped.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
QUOTA_MODES = ("report", "soft", "hard")

#: UTC-day window kind keys, matching the quota-document field stems.
KIND_REQUESTS = "requests"
KIND_TOOL_CALLS = "tool_calls"

#: Fixed safe bucket for callers without token identity. Never denied.
UNKNOWN_CALLER = "_unknown_"
#: Fold target when distinct caller labels exceed the cardinality cap.
OVERFLOW_CALLER = "_overflow_"
#: Bound on distinct exported caller labels (T-82-07); excess folds over.
MAX_CALLER_LABELS = 64
#: Soft-mode warning threshold as a fraction of any configured limit.
WARN_RATIO = 0.8

CALLER_REQUESTS_TOTAL = "omega_caller_requests_total"
CALLER_TOOL_CALLS_TOTAL = "omega_caller_tool_calls_total"
CALLER_QUOTA_LIMIT = "omega_caller_quota_limit"
CALLER_QUOTA_USED_RATIO = "omega_caller_quota_used_ratio"
CALLER_QUOTA_WARN = "omega_caller_quota_warn"

_LIMIT_FIELDS = {
    KIND_REQUESTS: "requests_per_day",
    KIND_TOOL_CALLS: "tool_calls_per_day",
}


class QuotaDocumentError(ValueError):
    """A quota document is malformed; never half-applied (T-82-11)."""


@dataclass(frozen=True)
class QuotaLimits:
    """Per-caller daily caps. `None` for a kind means unlimited."""

    requests_per_day: int | None = None
    tool_calls_per_day: int | None = None

    def kinds(self) -> dict[str, int]:
        """Configured `{kind: limit}` pairs (unlimited kinds omitted)."""
        out: dict[str, int] = {}
        if self.requests_per_day is not None:
            out[KIND_REQUESTS] = self.requests_per_day
        if self.tool_calls_per_day is not None:
            out[KIND_TOOL_CALLS] = self.tool_calls_per_day
        return out


def _check_limit(caller: str, field: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuotaDocumentError(
            f"caller {caller!r}: {field!r} must be an integer, got {value!r}"
        )
    if value <= 0:
        raise QuotaDocumentError(
            f"caller {caller!r}: {field!r} must be positive, got {value!r}"
        )
    return value


def load_quota_document(path: str | Path | None) -> dict[str, QuotaLimits]:
    """Load `{caller label: QuotaLimits}` from a versioned quota document.

    An empty path or a missing file means unlimited for every caller (the
    engine stays inert). Anything present validates strictly and fails
    named — unknown fields, a wrong `schema_version`, non-object
    `callers`, or non-positive limits each raise `QuotaDocumentError`.
    """
    if path is None or str(path) == "":
        return {}
    target = Path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise QuotaDocumentError(f"cannot read quota document {target}: {exc}") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise QuotaDocumentError(
            f"quota document {target} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise QuotaDocumentError(f"quota document {target} must contain an object")
    unknown_top = set(document) - {"schema_version", "callers"}
    if unknown_top:
        raise QuotaDocumentError(
            f"quota document {target} has unknown fields: {sorted(unknown_top)}"
        )
    if document.get("schema_version") != SCHEMA_VERSION:
        raise QuotaDocumentError(
            f"quota document {target} has schema_version "
            f"{document.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
    callers = document.get("callers", {})
    if not isinstance(callers, dict):
        raise QuotaDocumentError(
            f"quota document {target}: 'callers' must be an object"
        )
    limits: dict[str, QuotaLimits] = {}
    for label, spec in callers.items():
        if not isinstance(label, str) or label == "":
            raise QuotaDocumentError(
                f"quota document {target}: caller names must be non-empty strings"
            )
        if label in (UNKNOWN_CALLER, OVERFLOW_CALLER):
            raise QuotaDocumentError(
                f"quota document {target}: caller {label!r} is a reserved bucket name"
            )
        if not isinstance(spec, dict):
            raise QuotaDocumentError(
                f"quota document {target}: caller {label!r} must map to an object"
            )
        unknown_fields = set(spec) - set(_LIMIT_FIELDS.values())
        if unknown_fields:
            raise QuotaDocumentError(
                f"quota document {target}: caller {label!r} has unknown fields: "
                f"{sorted(unknown_fields)}"
            )
        limits[label] = QuotaLimits(
            requests_per_day=(
                _check_limit(label, "requests_per_day", spec["requests_per_day"])
                if "requests_per_day" in spec
                else None
            ),
            tool_calls_per_day=(
                _check_limit(label, "tool_calls_per_day", spec["tool_calls_per_day"])
                if "tool_calls_per_day" in spec
                else None
            ),
        )
    return limits


def _utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).date().isoformat()


@dataclass
class _Buckets:
    requests: int = 0
    tool_calls: int = 0
    denied: int = 0


def _default_resolver(principal: Any) -> str:
    label = getattr(principal, "label", None) if principal is not None else None
    if isinstance(label, str) and label != "":
        return label
    return UNKNOWN_CALLER


class QuotaEngine:
    """Quota accounting plus the `before`/`after` interceptor pair.

    When constructed without limits the engine is inert: it registers no
    metric series and its hooks are pure pass-through. With limits it
    counts per-caller UTC-day usage; `soft` warns at >=80% without ever
    denying; `hard` denies over-limit requests pre-dispatch with
    `code="quota"`. Mode and document reload live (`set_mode`,
    `reload_document`, `refresh_from_config`) — no restart needed.
    """

    def __init__(
        self,
        registry: Any | None = None,
        *,
        mode: str = "report",
        limits: dict[str, QuotaLimits] | None = None,
        clock: Callable[[], float] | None = None,
        label_resolver: Callable[[Any], str] | None = None,
    ) -> None:
        if mode not in QUOTA_MODES:
            raise ValueError(f"mode must be one of {QUOTA_MODES}, got {mode!r}")
        self._mode = mode
        self._limits: dict[str, QuotaLimits] = dict(limits or {})
        self._clock = clock or time.time
        self._resolve = label_resolver or _default_resolver
        self._lock = threading.Lock()
        self._days: dict[str, dict[str, _Buckets]] = {}
        # Buckets are keyed by RAW caller label (never folded): enforcement is
        # per-caller even past the export cardinality cap. Only the exported
        # series label folds excess callers into `_overflow_` (T-82-07).
        self._warned: set[tuple[str, str, str]] = set()
        self._known_labels: list[str] = []
        self._registry = registry
        self._requests: Any = None
        self._tool_calls: Any = None
        self._limit_gauge: Any = None
        self._ratio_gauge: Any = None
        self._warn_gauge: Any = None
        if self.is_active:
            self._register_series()

    def _register_series(self) -> None:
        """Register quota series on the construction-time registry (once).

        Called at construction when limits are present, and lazily by
        `reload_document`/`refresh_from_config` when a live reload activates
        a previously inert engine — so a no-restart enable also exports
        series. Never registers twice; without a stored registry a private
        one is built, exactly as construction does.
        """
        if self._limit_gauge is not None:
            return
        registry = self._registry
        if registry is None:
            from omega_prime.grokbot.metrics import MetricsRegistry

            registry = MetricsRegistry()
            self._registry = registry
        self._requests = registry.counter(
            CALLER_REQUESTS_TOTAL,
            "Tool-call requests by caller label (quota accounting).",
            ("caller",),
        )
        self._tool_calls = registry.counter(
            CALLER_TOOL_CALLS_TOTAL,
            "Completed tool calls by caller label (quota accounting).",
            ("caller",),
        )
        self._limit_gauge = registry.gauge(
            CALLER_QUOTA_LIMIT,
            "Configured daily quota by caller label and kind.",
            ("caller", "kind"),
        )
        self._ratio_gauge = registry.gauge(
            CALLER_QUOTA_USED_RATIO,
            "Used fraction of the daily quota by caller label and kind.",
            ("caller", "kind"),
        )
        self._warn_gauge = registry.gauge(
            CALLER_QUOTA_WARN,
            "Whether the caller reached the soft-warning threshold today.",
            ("caller",),
        )
        self._publish_limits()

    @property
    def mode(self) -> str:
        """The live enforcement mode."""
        return self._mode

    @property
    def is_active(self) -> bool:
        """Whether any limits are configured (inactive ⇒ fully inert)."""
        return any(limit.kinds() for limit in self._limits.values())

    @property
    def limits(self) -> dict[str, QuotaLimits]:
        """A copy of the loaded limits (labels only, never token material)."""
        return dict(self._limits)

    def set_mode(self, mode: str) -> None:
        """Flip the enforcement mode live; unknown modes raise (no fallback)."""
        if mode not in QUOTA_MODES:
            raise ValueError(f"mode must be one of {QUOTA_MODES}, got {mode!r}")
        self._mode = mode

    def reload_document(self, path: str | Path | None) -> dict[str, QuotaLimits]:
        """Reload limits from a quota document (live, no restart)."""
        loaded = load_quota_document(path)
        with self._lock:
            self._limits = loaded
            if self.is_active:
                self._register_series()
            else:
                self._publish_limits()
        return dict(loaded)

    def refresh_from_config(self, config: dict[str, Any]) -> None:
        """Apply the `quotas` config node live (mode + document).

        Atomic: the document loads before anything is mutated, so a bad
        document leaves the previous mode and limits untouched (never
        half-applied). Unknown modes keep the current mode.
        """
        node = config.get("quotas", {})
        if not isinstance(node, dict):
            return
        mode = node.get("mode", "report")
        document = node.get("document", "")
        if not isinstance(document, str):
            document = ""
        loaded = load_quota_document(document or None)
        with self._lock:
            if mode in QUOTA_MODES:
                self._mode = mode
            self._limits = loaded
            if self.is_active:
                self._register_series()
            else:
                self._publish_limits()

    # -- identity ------------------------------------------------------

    def caller_label(self, principal: Any) -> str:
        """The folded series label for a principal (raw label, capped)."""
        return self._fold(self._resolve_raw(principal))

    def _resolve_raw(self, principal: Any) -> str:
        """The unfolded caller label (never folded, never registered)."""
        try:
            raw = self._resolve(principal)
        except Exception:
            raw = UNKNOWN_CALLER
        if not isinstance(raw, str) or raw == "":
            raw = UNKNOWN_CALLER
        return raw

    def _fold(self, label: str) -> str:
        if label in self._known_labels:
            return label
        if len(self._known_labels) < MAX_CALLER_LABELS:
            self._known_labels.append(label)
            return label
        if OVERFLOW_CALLER not in self._known_labels:
            self._known_labels.append(OVERFLOW_CALLER)
        return OVERFLOW_CALLER

    def _fold_known(self, label: str, known: list[str]) -> str:
        """Fold `label` against a snapshot of known labels (no mutation).

        Read paths (`daily_counts`) use this so exporting never perturbs
        first-seen registration order; every bucketed raw label is already
        registered by the write path, so this agrees with `_fold`.
        """
        if label in known:
            return label
        if len(known) < MAX_CALLER_LABELS:
            return label
        return OVERFLOW_CALLER

    # -- counting ------------------------------------------------------

    def _day_buckets(self, day: str) -> dict[str, _Buckets]:
        buckets = self._days.get(day)
        if buckets is None:
            buckets = {}
            self._days[day] = buckets
        return buckets

    def _roll_day(self, day: str) -> None:
        """Reset sticky warn state when the UTC day advances."""
        stale = {(d, c, k) for (d, c, k) in self._warned if d != day}
        if not stale:
            return
        warned_today = {caller for (d, caller, _) in self._warned if d == day}
        if self._warn_gauge is not None:
            for _, raw, _ in stale:
                if raw in warned_today:
                    continue
                with contextlib.suppress(Exception):
                    self._warn_gauge.labels(caller=self._fold(raw)).set(0)
        self._warned = {(d, c, k) for (d, c, k) in self._warned if d == day}

    def _update_ratio(self, caller: str, kind: str, used: int, limit: int) -> None:
        if self._ratio_gauge is None:
            return
        with contextlib.suppress(Exception):
            self._ratio_gauge.labels(caller=caller, kind=kind).set(used / limit)

    def _publish_limits(self) -> None:
        if self._limit_gauge is None:
            return
        for label, limit in self._limits.items():
            for kind, value in limit.kinds().items():
                with contextlib.suppress(Exception):
                    self._limit_gauge.labels(caller=self._fold(label), kind=kind).set(
                        float(value)
                    )

    def _maybe_warn(
        self, day: str, caller: str, raw: str, kind: str, used: int
    ) -> bool:
        """Emit one warning per caller per kind per UTC day (soft only)."""
        if self._mode != "soft":
            return False
        limit = self._limits.get(raw)
        if limit is None:
            return False
        configured = limit.kinds().get(kind)
        if configured is None:
            return False
        if used < WARN_RATIO * configured:
            return False
        key = (day, raw, kind)
        if key in self._warned:
            return True
        self._warned.add(key)
        if self._warn_gauge is not None:
            with contextlib.suppress(Exception):
                self._warn_gauge.labels(caller=caller).set(1)
        logger.warning(
            "quota warning: caller %r used %d of %d %s per UTC day (%.0f%%)",
            raw,
            used,
            configured,
            kind,
            100.0 * used / configured,
        )
        return True

    # -- interceptor ---------------------------------------------------

    def before(self, call: Any) -> Any | None:
        """Count one request, then warn (soft) or refuse (hard) pre-dispatch."""
        if not self.is_active:
            return None
        from omega_prime.grokbot.interceptors import ToolDenial

        with self._lock:
            day = _utc_day(self._clock())
            self._roll_day(day)
            raw = self._resolve_raw(call.principal)
            caller = self._fold(raw)
            bucket = self._day_buckets(day).setdefault(raw, _Buckets())
            bucket.requests += 1
            if self._requests is not None:
                with contextlib.suppress(Exception):
                    self._requests.labels(caller=caller).inc()
            limits = self._limits.get(raw)
            req_limit = limits.requests_per_day if limits else None
            if req_limit is not None:
                self._update_ratio(caller, KIND_REQUESTS, bucket.requests, req_limit)
            if self._mode == "soft":
                if req_limit is not None:
                    self._maybe_warn(day, caller, raw, KIND_REQUESTS, bucket.requests)
                tc_limit = limits.tool_calls_per_day if limits else None
                if tc_limit is not None:
                    self._maybe_warn(
                        day, caller, raw, KIND_TOOL_CALLS, bucket.tool_calls
                    )
                return None
            if self._mode != "hard" or raw == UNKNOWN_CALLER:
                return None
            if req_limit is not None and bucket.requests > req_limit:
                bucket.denied += 1
                return ToolDenial(
                    code="quota",
                    message=(
                        f"quota exceeded for caller {raw!r}: {bucket.requests} "
                        f"requests on {day} (limit {req_limit}/day)"
                    ),
                )
            tc_limit = limits.tool_calls_per_day if limits else None
            if tc_limit is not None and bucket.tool_calls >= tc_limit:
                bucket.denied += 1
                return ToolDenial(
                    code="quota",
                    message=(
                        f"quota exceeded for caller {raw!r}: {bucket.tool_calls} "
                        f"tool calls on {day} (limit {tc_limit}/day)"
                    ),
                )
            return None

    def after(self, call: Any, outcome: Any) -> None:
        """Count one completed tool call (denied calls never dispatched)."""
        if not self.is_active:
            return
        if getattr(outcome, "status", None) == "denied":
            return
        with self._lock:
            day = _utc_day(self._clock())
            self._roll_day(day)
            raw = self._resolve_raw(call.principal)
            caller = self._fold(raw)
            bucket = self._day_buckets(day).setdefault(raw, _Buckets())
            bucket.tool_calls += 1
            if self._tool_calls is not None:
                with contextlib.suppress(Exception):
                    self._tool_calls.labels(caller=caller).inc()
            limits = self._limits.get(raw)
            tc_limit = limits.tool_calls_per_day if limits else None
            if tc_limit is not None:
                self._update_ratio(caller, KIND_TOOL_CALLS, bucket.tool_calls, tc_limit)
                self._maybe_warn(day, caller, raw, KIND_TOOL_CALLS, bucket.tool_calls)

    # -- rollup support --------------------------------------------------

    def seed_day(self, day: str, counts: dict[str, dict[str, Any]]) -> None:
        """Feed recorded counters for `day` (rollup CLI/tests, no server).

        `counts` maps caller label to `requests`/`tool_calls`/`denied`
        ints (plus an optional `warn` bool restoring the warning flag).
        Labels seed raw buckets (no folding merge), so a dumped day
        restores per-caller fidelity.
        """
        datetime.strptime(day, "%Y-%m-%d")
        with self._lock:
            buckets = self._day_buckets(day)
            for label, entry in counts.items():
                if not isinstance(label, str) or label == "":
                    raise ValueError(f"seed_day: bad caller label {label!r}")
                self._fold(label)
                bucket = buckets.setdefault(label, _Buckets())
                bucket.requests = int(entry.get("requests", 0))
                bucket.tool_calls = int(entry.get("tool_calls", 0))
                bucket.denied = int(entry.get("denied", 0))
                if entry.get("warn"):
                    self._warned.add((day, label, KIND_REQUESTS))
                    if self._warn_gauge is not None:
                        with contextlib.suppress(Exception):
                            self._warn_gauge.labels(caller=self._fold(label)).set(1)

    def daily_counts(self, day: str) -> dict[str, dict[str, Any]]:
        """Per-caller `{requests, tool_calls, denied, limits, warn}` for `day`.

        Raw buckets folding past the export cap combine into one summed
        `_overflow_` entry (limits unset: members may differ), so the
        exported key set stays bounded while enforcement stayed per-caller.
        """
        datetime.strptime(day, "%Y-%m-%d")
        with self._lock:
            buckets = dict(self._days.get(day, {}))
            warned = set(self._warned)
            known = list(self._known_labels)
            limits = dict(self._limits)
        grouped: dict[str, list[str]] = {}
        for raw in sorted(buckets):
            grouped.setdefault(self._fold_known(raw, known), []).append(raw)
        out: dict[str, dict[str, Any]] = {}
        for caller in sorted(grouped):
            members = grouped[caller]
            if len(members) == 1 and members[0] == caller:
                raw = members[0]
                bucket = buckets[raw]
                limit = limits.get(raw)
                configured = limit.kinds() if limit else {}
                out[caller] = {
                    "requests": bucket.requests,
                    "tool_calls": bucket.tool_calls,
                    "denied": bucket.denied,
                    "limits": {
                        "requests_per_day": configured.get(KIND_REQUESTS),
                        "tool_calls_per_day": configured.get(KIND_TOOL_CALLS),
                    },
                    "warn": any(
                        (day, raw, kind) in warned
                        for kind in (KIND_REQUESTS, KIND_TOOL_CALLS)
                    ),
                }
            else:
                out[caller] = {
                    "requests": sum(buckets[raw].requests for raw in members),
                    "tool_calls": sum(buckets[raw].tool_calls for raw in members),
                    "denied": sum(buckets[raw].denied for raw in members),
                    "limits": {
                        "requests_per_day": None,
                        "tool_calls_per_day": None,
                    },
                    "warn": any(
                        (day, raw, kind) in warned
                        for raw in members
                        for kind in (KIND_REQUESTS, KIND_TOOL_CALLS)
                    ),
                }
        return out

    def build_rollup(self, day: str) -> dict[str, Any]:
        """The deterministic rollup payload for one UTC day."""
        return {"date": day, "callers": self.daily_counts(day)}

    # -- host-side export --------------------------------------------------

    def counters_snapshot(self, day: str) -> dict[str, dict[str, Any]]:
        """Raw per-caller counters for `day` (feeds `rollup --counters`).

        Keys are unfolded labels (plus `warn`) so `seed_day` restores the
        exact per-caller buckets the host counted — including overflow
        callers the exported series folded away.
        """
        datetime.strptime(day, "%Y-%m-%d")
        with self._lock:
            buckets = dict(self._days.get(day, {}))
            warned = set(self._warned)
        return {
            raw: {
                "requests": buckets[raw].requests,
                "tool_calls": buckets[raw].tool_calls,
                "denied": buckets[raw].denied,
                "warn": any(
                    (day, raw, kind) in warned
                    for kind in (KIND_REQUESTS, KIND_TOOL_CALLS)
                ),
            }
            for raw in sorted(buckets)
        }

    def known_days(self) -> list[str]:
        """UTC days with counted buckets, oldest first."""
        with self._lock:
            return sorted(self._days)

    def dump_counters_file(self, rollup_dir: str | Path, day: str) -> Path:
        """Write `rollup_dir/counters-<day>.json`; re-runs are byte-identical."""
        datetime.strptime(day, "%Y-%m-%d")
        directory = Path(rollup_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"counters-{day}.json"
        target.write_text(
            json.dumps(self.counters_snapshot(day), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    def export_counters(self, rollup_dir: str | Path) -> list[Path]:
        """Dump every known day into `rollup_dir` (the nightly rollup input)."""
        return [self.dump_counters_file(rollup_dir, day) for day in self.known_days()]


def render_rollup(payload: dict[str, Any]) -> str:
    """Canonical rollup text: sorted keys, fixed indent (idempotent)."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_rollup_file(
    rollup_dir: str | Path, day: str, payload: dict[str, Any]
) -> Path:
    """Write `rollup_dir/usage-<day>.json`; re-runs are byte-identical."""
    datetime.strptime(day, "%Y-%m-%d")
    directory = Path(rollup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"usage-{day}.json"
    target.write_text(render_rollup(payload), encoding="utf-8")
    return target


_DEFAULT_ROLLUP_DIR = ".omega-quotas/"
#: Seconds between serve-side quota ticks (config re-read + counters dump).
MAINTENANCE_INTERVAL_SECONDS = 60.0
_STOP_JOIN_TIMEOUT = 5.0


class QuotasMaintenance:
    """Serve-side quota tick: live config reload plus counters dump.

    One `tick_once` re-reads the `quotas` config node (mode + document take
    effect via `refresh_from_config` — no restart) and dumps every known
    day's raw counters into the configured `rollup_dir` as
    `counters-<UTC-day>.json`, the file the nightly rollup consumes via
    `--counters`. `start` runs the tick on a daemon thread (the same shape
    as the OTLP push thread); `tick_once` is the hermetic unit seam and
    never raises — failures are logged and reported in the result dict.
    """

    def __init__(
        self,
        engine: QuotaEngine,
        *,
        root: str | Path | None = None,
        interval_seconds: float = MAINTENANCE_INTERVAL_SECONDS,
        config_loader: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._engine = engine
        self._root = Path(root) if root is not None else None
        self._interval = (
            interval_seconds
            if isinstance(interval_seconds, (int, float))
            and not isinstance(interval_seconds, bool)
            and interval_seconds > 0
            else MAINTENANCE_INTERVAL_SECONDS
        )
        if config_loader is not None:
            self._config_loader = config_loader
        else:
            root_arg = self._root

            def _load() -> dict[str, Any]:
                from omega_prime.config import load_config

                return load_config(root_arg)

            self._config_loader = _load
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def interval_seconds(self) -> float:
        """The tick cadence in seconds."""
        return self._interval

    def _rollup_dir(self, config: dict[str, Any]) -> Path:
        node = config.get("quotas", {})
        name: Any = _DEFAULT_ROLLUP_DIR
        if isinstance(node, dict):
            candidate = node.get("rollup_dir", _DEFAULT_ROLLUP_DIR)
            if isinstance(candidate, (str, Path)) and str(candidate) != "":
                name = candidate
        path = Path(name)
        if not path.is_absolute() and self._root is not None:
            path = self._root / path
        return path

    def tick_once(self) -> dict[str, Any]:
        """One reload + dump pass; never raises (failures are reported)."""
        try:
            config = self._config_loader()
        except Exception as exc:
            logger.warning("quota maintenance config reload failed: %s", exc)
            return {
                "ok": False,
                "mode": self._engine.mode,
                "dumped": [],
                "errors": [f"config: {exc}"],
            }
        if not isinstance(config, dict):
            config = {}
        errors: list[str] = []
        try:
            self._engine.refresh_from_config(config)
        except Exception as exc:
            logger.warning("quota maintenance reload failed: %s", exc)
            errors.append(f"reload: {exc}")
        dumped: list[str] = []
        try:
            dumped = [
                str(target)
                for target in self._engine.export_counters(self._rollup_dir(config))
            ]
        except Exception as exc:
            logger.warning("quota maintenance dump failed: %s", exc)
            errors.append(f"dump: {exc}")
        return {
            "ok": not errors,
            "mode": self._engine.mode,
            "dumped": dumped,
            "errors": errors,
        }

    def start(self) -> QuotasMaintenance:
        """Start the daemon tick thread (idempotent)."""
        if self._thread is None:
            self._stopped.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="quota-maintenance",
                daemon=True,
            )
            self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self.tick_once()
            except Exception as exc:  # never raises into serving
                logger.warning("quota maintenance tick failed: %s", exc)
                logger.debug("quota maintenance tick failed", exc_info=True)

    def stop(self) -> None:
        """Stop the tick thread (best-effort, never raises into serving)."""
        self._stopped.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_STOP_JOIN_TIMEOUT)


__all__ = [
    "CALLER_QUOTA_LIMIT",
    "CALLER_QUOTA_USED_RATIO",
    "CALLER_QUOTA_WARN",
    "CALLER_REQUESTS_TOTAL",
    "CALLER_TOOL_CALLS_TOTAL",
    "KIND_REQUESTS",
    "KIND_TOOL_CALLS",
    "MAINTENANCE_INTERVAL_SECONDS",
    "MAX_CALLER_LABELS",
    "OVERFLOW_CALLER",
    "QUOTA_MODES",
    "SCHEMA_VERSION",
    "UNKNOWN_CALLER",
    "WARN_RATIO",
    "QuotaDocumentError",
    "QuotaEngine",
    "QuotaLimits",
    "QuotasMaintenance",
    "load_quota_document",
    "render_rollup",
    "write_rollup_file",
]

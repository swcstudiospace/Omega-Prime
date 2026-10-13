# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Optional push-based telemetry export (Phase 81, OBS-01 export half).

The default install stays dependency-free: this module performs zero
third-party imports at top level. The OpenTelemetry imports live inside
:func:`_default_sender`, so ``import opentelemetry`` never happens
unless the operator both enables ``telemetry.otlp`` and installs the
``otel`` extra (``opentelemetry-proto`` plus the documented PyPI package
``opentelemetry-exporter-otlp-proto-http``, which pulls in the SDK).

Behavior:

- :func:`maybe_start` returns ``None`` (no thread, registry untouched)
  unless the node is enabled *and* carries a non-empty push target.
- When enabled without the extra installed, it logs exactly one warning
  naming the extra and returns ``None`` — local metrics, logging and
  audit continue uninterrupted (documented degraded mode).
- The exporter reads ONLY the existing ``MetricsRegistry`` snapshot
  (same counters/gauges/histograms, no second source of truth),
  converts it to real OTLP metrics, and pushes them with the
  exporter's own ``export()`` (OTLP/HTTP protobuf) at the configured
  interval from a daemon thread. Per-tick failures are swallowed after
  a single warning so serving never blocks on export (fail-open for
  telemetry, never for serving).
- No per-user labels are introduced; only the existing R5 series shapes
  leave the process.

Test seam: pass ``sender=`` to :func:`maybe_start` to inject an
in-process recorder (it receives the Prometheus-text snapshot), or
monkeypatch :func:`_default_sender` to simulate a missing extra
(``ImportError``).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, never a runtime import.
    from omega_prime.grokbot.metrics import MetricsRegistry

logger = logging.getLogger(__name__)

#: Name of the optional dependency group gating this module.
OTEL_EXTRA = "otel"

_DEFAULT_INTERVAL_SECONDS = 60.0
_STOP_JOIN_TIMEOUT = 5.0

#: Scope name stamped on the exported OTLP metrics.
_SCOPE_NAME = "omega-prime"

_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?"
    r"\s+(?P<value>\S+)$"
)


def _unescape_label_value(raw: str) -> str:
    """Inverse of the registry's label escaping (``\\``, ``\"``, ``\\n``, ``\\uXXXX``)."""
    out: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char != "\\" or index + 1 >= len(raw):
            out.append(char)
            index += 1
            continue
        nxt = raw[index + 1]
        if nxt == "n":
            out.append("\n")
            index += 2
        elif nxt == "u" and raw[index + 2 : index + 6].isalnum():
            try:
                out.append(chr(int(raw[index + 2 : index + 6], 16)))
                index += 6
            except ValueError:
                out.append(nxt)
                index += 2
        else:
            out.append(nxt)
            index += 2
    return "".join(out)


def _split_labels(body: str) -> dict[str, str]:
    """Parse a ``{k="v",...}`` label block (quote-aware)."""
    labels: dict[str, str] = {}
    if not body.strip():
        return labels
    for part in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', body):
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = _unescape_label_value(value[1:-1])
        labels[name] = value
    return labels


def _parse_exposition(payload: str) -> list[dict[str, Any]]:
    """Parse our own Prometheus-text snapshot into metric families.

    Returns ``[{name, type, help, samples}]`` with ``samples`` as
    ``[(raw_name, labels, value)]``. Raises :class:`ValueError` on
    malformed lines.
    """
    families: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for lineno, line in enumerate(payload.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if line.startswith("# HELP "):
            _, _, rest = line.partition("# HELP ")
            name, _, help_text = rest.partition(" ")
            by_name.setdefault(name, {"samples": []})["help"] = help_text
            continue
        if line.startswith("# TYPE "):
            _, _, rest = line.partition("# TYPE ")
            name, _, kind = rest.partition(" ")
            by_name.setdefault(name, {"samples": []})["type"] = kind.strip()
            continue
        if line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if match is None:
            raise ValueError(f"unparseable exposition line {lineno}: {line!r}")
        raw_name = match.group("name")
        if raw_name in by_name:
            # A declared family (HELP/TYPE precede samples): a counter
            # literally named ``*_count`` stays its own family.
            base = raw_name
        else:
            base = raw_name
            for suffix in ("_bucket", "_sum", "_count"):
                if raw_name.endswith(suffix):
                    base = raw_name[: -len(suffix)]
                    break
        family = by_name.setdefault(base, {"samples": []})
        labels = _split_labels(match.group("labels") or "")
        try:
            value = float(match.group("value"))
        except ValueError:
            raise ValueError(
                f"unparseable sample value on line {lineno}: {line!r}"
            ) from None
        family["samples"].append((raw_name, labels, value))
    for name, family in by_name.items():
        family["name"] = name
        family.setdefault("type", "untyped")
        family.setdefault("help", "")
        families.append(family)
    return families


def _build_metrics_data(
    families: list[dict[str, Any]], sdk: Any, start_ns: int, now_ns: int
) -> Any:
    """Convert parsed families to an SDK ``MetricsData`` for ``export()``."""
    metrics = []
    for family in families:
        name = family["name"]
        kind = family["type"]
        help_text = family["help"]
        if kind == "histogram":
            metrics.append(
                sdk.Metric(
                    name,
                    help_text,
                    "",
                    sdk.Histogram(
                        data_points=_histogram_points(
                            sdk, family["samples"], start_ns, now_ns
                        ),
                        aggregation_temporality=sdk.AggregationTemporality.CUMULATIVE,
                    ),
                )
            )
        elif kind == "counter":
            metrics.append(
                sdk.Metric(
                    name,
                    help_text,
                    "",
                    sdk.Sum(
                        data_points=[
                            sdk.NumberDataPoint(
                                attributes=dict(labels),
                                start_time_unix_nano=start_ns,
                                time_unix_nano=now_ns,
                                value=value,
                            )
                            for _, labels, value in family["samples"]
                        ],
                        aggregation_temporality=sdk.AggregationTemporality.CUMULATIVE,
                        is_monotonic=True,
                    ),
                )
            )
        else:
            metrics.append(
                sdk.Metric(
                    name,
                    help_text,
                    "",
                    sdk.Gauge(
                        data_points=[
                            sdk.NumberDataPoint(
                                attributes=dict(labels),
                                start_time_unix_nano=start_ns,
                                time_unix_nano=now_ns,
                                value=value,
                            )
                            for _, labels, value in family["samples"]
                        ]
                    ),
                )
            )
    scope_metrics = sdk.ScopeMetrics(
        scope=sdk.InstrumentationScope(_SCOPE_NAME), metrics=metrics, schema_url=""
    )
    return sdk.MetricsData(
        resource_metrics=[
            sdk.ResourceMetrics(
                resource=sdk.Resource({}),
                scope_metrics=[scope_metrics],
                schema_url="",
            )
        ]
    )


def _histogram_points(
    sdk: Any,
    samples: list[tuple[str, dict[str, str], float]],
    start_ns: int,
    now_ns: int,
) -> list[Any]:
    """Fold ``_bucket``/``_sum``/``_count`` samples into OTLP points."""
    buckets: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
    for raw_name, labels, value in samples:
        key = tuple(sorted((k, v) for k, v in labels.items() if k != "le"))
        slot = buckets.setdefault(
            key, {"bounds": [], "counts": [], "sum": None, "count": None}
        )
        if raw_name.endswith("_bucket"):
            bound = labels.get("le", "+Inf")
            slot["bounds"].append(float("inf") if bound == "+Inf" else float(bound))
            slot["counts"].append(value)
        elif raw_name.endswith("_sum"):
            slot["sum"] = value
        elif raw_name.endswith("_count"):
            slot["count"] = value
    points = []
    for key, slot in buckets.items():
        order = sorted(range(len(slot["bounds"])), key=lambda i: slot["bounds"][i])
        bounds = [slot["bounds"][i] for i in order]
        cumulative = [slot["counts"][i] for i in order]
        counts = [
            cumulative[i] - (cumulative[i - 1] if i else 0.0)
            for i in range(len(cumulative))
        ]
        finite_bounds = [b for b in bounds if b != float("inf")]
        if slot["count"] is not None:
            count: float = slot["count"]
        elif cumulative:
            count = cumulative[-1]
        else:
            count = 0.0
        points.append(
            sdk.HistogramDataPoint(
                attributes=dict(key),
                start_time_unix_nano=start_ns,
                time_unix_nano=now_ns,
                count=int(count),
                sum=slot["sum"],
                bucket_counts=[int(c) for c in counts],
                explicit_bounds=finite_bounds,
            )
        )
    return points


def _default_sender(endpoint: str) -> Callable[[str], None]:
    """Build the push closure for ``endpoint`` (lazy capability check).

    The imports below are the ONLY OpenTelemetry imports in runtime code:
    they prove the extra is installed. ``ImportError`` propagates to
    :func:`maybe_start`, which degrades to the documented warning mode.
    Each text snapshot from the registry is converted to real OTLP
    metrics and pushed with the exporter's own ``export()``.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )

    try:
        from opentelemetry.sdk.util import instrumentation_scope as _scope_mod

        _scope_cls = _scope_mod.InstrumentationScope
    except ImportError:
        from opentelemetry.sdk.util import instrumentation as _scope_mod

        _scope_cls = _scope_mod.InstrumentationScope
    from opentelemetry.sdk.metrics import export as _sdk_export
    from opentelemetry.sdk.resources import Resource as _Resource

    class _Sdk:
        Metric = _sdk_export.Metric
        Sum = _sdk_export.Sum
        Gauge = _sdk_export.Gauge
        Histogram = _sdk_export.Histogram
        NumberDataPoint = _sdk_export.NumberDataPoint
        HistogramDataPoint = _sdk_export.HistogramDataPoint
        MetricsData = _sdk_export.MetricsData
        ResourceMetrics = _sdk_export.ResourceMetrics
        ScopeMetrics = _sdk_export.ScopeMetrics
        AggregationTemporality = _sdk_export.AggregationTemporality
        InstrumentationScope = _scope_cls
        Resource = _Resource

    exporter = OTLPMetricExporter(endpoint=endpoint)
    start_ns = time.time_ns()

    def _send(payload: str) -> None:
        families = _parse_exposition(payload)
        data = _build_metrics_data(families, _Sdk, start_ns, time.time_ns())
        exporter.export(data)

    return _send


def _coerce_interval(value: Any) -> float:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_SECONDS
    return interval if interval > 0 else _DEFAULT_INTERVAL_SECONDS


class OtlpExporter:
    """Fixed-interval snapshot pusher over a caller-supplied ``sender``."""

    def __init__(
        self,
        *,
        registry: MetricsRegistry,
        endpoint: str,
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
        sender: Callable[[str], None],
    ) -> None:
        self._registry = registry
        self._endpoint = endpoint
        self._interval = interval_seconds
        self._sender = sender
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        """The configured push target (operator-supplied, never committed)."""
        return self._endpoint

    @property
    def interval_seconds(self) -> float:
        """The push cadence in seconds."""
        return self._interval

    def start(self) -> OtlpExporter:
        """Start the daemon push thread (idempotent)."""
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run,
                name="otlp-export",
                daemon=True,
            )
            self._thread.start()
        return self

    def export_once(self) -> None:
        """Render the registry and push one snapshot (raises on failure)."""
        payload = self._registry.render()
        self._sender(payload)

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self.export_once()
            except Exception as exc:
                logger.warning("telemetry push tick failed: %s", exc)
                logger.debug("telemetry push tick failed", exc_info=True)

    def stop(self) -> None:
        """Stop the push thread (best-effort, never raises into serving)."""
        self._stopped.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_STOP_JOIN_TIMEOUT)


def maybe_start(
    otlp_config: Mapping[str, Any] | None,
    registry: MetricsRegistry,
    *,
    sender: Callable[[str], None] | None = None,
) -> OtlpExporter | None:
    """Construct and start the exporter when configured, else ``None``.

    Returns ``None`` (no thread, registry untouched) when the node is
    missing/disabled or carries an empty push target. When enabled with
    a target but the extra absent, logs exactly one warning naming the
    extra and returns ``None``.
    """
    node = dict(otlp_config or {})
    if not node.get("enabled"):
        return None
    endpoint = str(node.get("endpoint") or "").strip()
    if not endpoint:
        logger.warning("telemetry.otlp is enabled without a push target; export off")
        return None
    interval = _coerce_interval(node.get("interval_seconds"))
    if sender is None:
        try:
            sender = _default_sender(endpoint)
        except ImportError:
            logger.warning(
                "telemetry.otlp is enabled but the %r extra is not installed; "
                "export off (local metrics unaffected)",
                OTEL_EXTRA,
            )
            return None
    exporter = OtlpExporter(
        registry=registry,
        endpoint=endpoint,
        interval_seconds=interval,
        sender=sender,
    )
    exporter.start()
    logger.info("telemetry push started (every %ss)", interval)
    return exporter


__all__ = [
    "OTEL_EXTRA",
    "OtlpExporter",
    "maybe_start",
]

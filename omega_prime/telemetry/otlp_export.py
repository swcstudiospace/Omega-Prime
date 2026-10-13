# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Optional push-based telemetry export (Phase 81, OBS-01 export half).

The default install stays dependency-free: this module performs zero
third-party imports at top level. The only OpenTelemetry import lives
inside :func:`_default_sender`, so ``import opentelemetry`` never happens
unless the operator both enables ``telemetry.otlp`` and installs the
``otel`` extra (``opentelemetry-proto`` plus the documented PyPI package
``opentelemetry-exporter-otlp-proto-http``).

Behavior:

- :func:`maybe_start` returns ``None`` (no thread, registry untouched)
  unless the node is enabled *and* carries a non-empty push target.
- When enabled without the extra installed, it logs exactly one warning
  naming the extra and returns ``None`` — local metrics, logging and
  audit continue uninterrupted (documented degraded mode).
- The exporter reads ONLY the existing ``MetricsRegistry`` snapshot
  (same counters/gauges/histograms, no second source of truth) and
  pushes it at the configured interval from a daemon thread. Per-tick
  failures are swallowed after a single warning so serving never blocks
  on export (fail-open for telemetry, never for serving).
- No per-user labels are introduced; only the existing R5 series shapes
  leave the process.

Test seam: pass ``sender=`` to :func:`maybe_start` to inject an
in-process recorder, or monkeypatch :func:`_default_sender` to simulate
a missing extra (``ImportError``).
"""

from __future__ import annotations

import logging
import threading
import urllib.request
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, never a runtime import.
    from omega_prime.grokbot.metrics import MetricsRegistry

logger = logging.getLogger(__name__)

#: Name of the optional dependency group gating this module.
OTEL_EXTRA = "otel"

_DEFAULT_INTERVAL_SECONDS = 60.0
_STOP_JOIN_TIMEOUT = 5.0

#: Content type of the pushed snapshot (the registry's exposition format).
SNAPSHOT_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _default_sender(endpoint: str) -> Callable[[str], None]:
    """Build the push closure for ``endpoint`` (lazy capability check).

    The import below is the ONLY OpenTelemetry import in runtime code:
    it proves the extra is installed. ``ImportError`` propagates to
    :func:`maybe_start`, which degrades to the documented warning mode.
    The snapshot itself travels over stdlib ``urllib``.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )

    exporter = OTLPMetricExporter(endpoint=endpoint)

    def _send(payload: str) -> None:
        request = urllib.request.Request(
            exporter.endpoint,
            data=payload.encode("utf-8"),
            headers={"Content-Type": SNAPSHOT_CONTENT_TYPE},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):
            pass

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

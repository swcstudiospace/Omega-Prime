# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic tests for the optional push exporter (no network, no extra)."""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

from omega_prime.grokbot.metrics import MetricsRegistry
from omega_prime.telemetry import otlp_export

R5_SERIES = (
    "omega_http_requests_total",
    "omega_http_request_duration_seconds",
    "omega_tool_denials_total",
)


def _seeded_registry() -> MetricsRegistry:
    """A registry carrying every R5 series the exporter must forward."""
    registry = MetricsRegistry()
    registry.counter(
        "omega_http_requests_total",
        "HTTP requests by method, path group and status class.",
        ("method", "path", "status"),
    ).labels(method="GET", path="/healthz", status="2xx").inc()
    registry.histogram(
        "omega_http_request_duration_seconds",
        "HTTP time to first response byte by method and path group.",
        ("method", "path"),
    ).labels(method="GET", path="/healthz").observe(0.01)
    registry.counter(
        "omega_tool_denials_total",
        "Denied tool calls by denial code.",
        ("code",),
    ).labels(code="rate_limited").inc()
    return registry


def _enabled_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "enabled": True,
        "endpoint": "http://127.0.0.1:4318/v1/metrics",
        "interval_seconds": 60,
    }
    config.update(overrides)
    return config


def test_default_config_starts_no_exporter() -> None:
    threads_before = {thread.ident for thread in threading.enumerate()}
    registry = _seeded_registry()
    before = registry.render()
    assert before != ""

    exporter = otlp_export.maybe_start(
        {"enabled": False, "endpoint": "", "interval_seconds": 60},
        registry,
    )

    assert exporter is None
    assert registry.render() == before
    assert {thread.ident for thread in threading.enumerate()} == threads_before


def test_enabled_pushes_all_r5_series_to_stub() -> None:
    received: list[str] = []
    registry = _seeded_registry()

    exporter = otlp_export.maybe_start(
        _enabled_config(),
        registry,
        sender=received.append,
    )
    assert exporter is not None
    assert exporter.endpoint == "http://127.0.0.1:4318/v1/metrics"
    try:
        assert exporter._thread is not None and exporter._thread.daemon
        exporter.export_once()
    finally:
        exporter.stop()

    assert len(received) == 1
    for series in R5_SERIES:
        assert series in received[0]


def test_enabled_without_extra_warns_once_and_serves(
    caplog: Any, monkeypatch: Any
) -> None:
    def _missing_extra(endpoint: str) -> Any:
        raise ImportError("No module named 'opentelemetry'")

    monkeypatch.setattr(otlp_export, "_default_sender", _missing_extra)
    registry = _seeded_registry()
    # Snapshot first: third-party packages (e.g. opentelemetry.util logging on
    # their own records, or a transitively installed opentelemetry-api already
    # imported by PyRIT's chain) may share this process. Only OUR logger's
    # records and NEW otel imports count here.
    otel_before = {
        module
        for module in sys.modules
        if module == "opentelemetry" or module.startswith("opentelemetry.")
    }

    with caplog.at_level(logging.WARNING, logger=otlp_export.__name__):
        exporter = otlp_export.maybe_start(_enabled_config(), registry)

    assert exporter is None
    warnings = [
        record
        for record in caplog.records
        if record.name == otlp_export.__name__ and record.levelno >= logging.WARNING
    ]
    assert len(warnings) == 1
    assert "otel" in warnings[0].getMessage()
    # Serving is uninterrupted: the registry still renders every R5 series.
    text = registry.render()
    for series in R5_SERIES:
        assert series in text
    assert {
        module
        for module in sys.modules
        if module == "opentelemetry" or module.startswith("opentelemetry.")
    } == otel_before

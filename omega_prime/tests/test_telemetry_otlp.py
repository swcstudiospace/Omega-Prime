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


def test_parse_exposition_round_trips_seeded_registry() -> None:
    """The text snapshot parses back into typed families with values."""
    families = otlp_export._parse_exposition(_seeded_registry().render())
    by_name = {family["name"]: family for family in families}
    assert by_name["omega_http_requests_total"]["type"] == "counter"
    (raw, labels, value) = by_name["omega_http_requests_total"]["samples"][0]
    assert raw == "omega_http_requests_total"
    assert labels == {"method": "GET", "path": "/healthz", "status": "2xx"}
    assert value == 1.0
    assert by_name["omega_http_request_duration_seconds"]["type"] == "histogram"
    bucket_kinds = {
        "bucket" if name.endswith("_bucket") else name.rsplit("_", 1)[-1]
        for name, _, _ in by_name["omega_http_request_duration_seconds"]["samples"]
    }
    assert {"bucket", "sum", "count"} <= bucket_kinds


def test_parse_exposition_keeps_literal_count_counter() -> None:
    """A counter literally named ``*_count`` is not folded into a histogram."""
    payload = (
        "# HELP jobs_count done jobs\n# TYPE jobs_count counter\n"
        'jobs_count{queue="a"} 3\n'
    )
    (family,) = otlp_export._parse_exposition(payload)
    assert family["name"] == "jobs_count"
    assert family["type"] == "counter"
    assert family["samples"] == [("jobs_count", {"queue": "a"}, 3.0)]


def _install_fake_otel(monkeypatch: Any) -> dict[str, Any]:
    """Install fake ``opentelemetry`` modules; return captured exports."""
    import types

    captured: dict[str, Any] = {"exports": [], "instances": []}

    class _Fake:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    def _named(name: str) -> Any:
        return type(name, (_Fake,), {"__module__": __name__})

    class _FakeExporter(_Fake):
        def __init__(self, endpoint: str = "", **kwargs: Any) -> None:
            super().__init__(endpoint, **kwargs)
            self.endpoint = endpoint
            captured["instances"].append(self)

        def export(self, data: Any, **kwargs: Any) -> str:
            captured["exports"].append(data)
            return "SUCCESS"

    class _FakeTemporality:
        UNSPECIFIED = 0
        DELTA = 1
        CUMULATIVE = 2

    exporter_leaf = types.ModuleType(
        "opentelemetry.exporter.otlp.proto.http.metric_exporter"
    )
    exporter_leaf.OTLPMetricExporter = _FakeExporter  # type: ignore[attr-defined]
    sdk_export_leaf = types.ModuleType("opentelemetry.sdk.metrics.export")
    for attr in (
        "Metric",
        "Sum",
        "Gauge",
        "Histogram",
        "NumberDataPoint",
        "HistogramDataPoint",
        "MetricsData",
        "ResourceMetrics",
        "ScopeMetrics",
    ):
        setattr(sdk_export_leaf, attr, _named(attr))
    sdk_export_leaf.AggregationTemporality = _FakeTemporality  # type: ignore[attr-defined]
    resources_leaf = types.ModuleType("opentelemetry.sdk.resources")
    resources_leaf.Resource = _named("Resource")  # type: ignore[attr-defined]
    scope_leaf = types.ModuleType("opentelemetry.sdk.util.instrumentation_scope")
    scope_leaf.InstrumentationScope = _named("InstrumentationScope")  # type: ignore[attr-defined]

    fakes: dict[str, Any] = {
        "opentelemetry": types.ModuleType("opentelemetry"),
        "opentelemetry.exporter": types.ModuleType("opentelemetry.exporter"),
        "opentelemetry.exporter.otlp": types.ModuleType("opentelemetry.exporter.otlp"),
        "opentelemetry.exporter.otlp.proto": types.ModuleType(
            "opentelemetry.exporter.otlp.proto"
        ),
        "opentelemetry.exporter.otlp.proto.http": types.ModuleType(
            "opentelemetry.exporter.otlp.proto.http"
        ),
        "opentelemetry.exporter.otlp.proto.http.metric_exporter": exporter_leaf,
        "opentelemetry.sdk": types.ModuleType("opentelemetry.sdk"),
        "opentelemetry.sdk.metrics": types.ModuleType("opentelemetry.sdk.metrics"),
        "opentelemetry.sdk.metrics.export": sdk_export_leaf,
        "opentelemetry.sdk.resources": resources_leaf,
        "opentelemetry.sdk.util": types.ModuleType("opentelemetry.sdk.util"),
        "opentelemetry.sdk.util.instrumentation_scope": scope_leaf,
    }
    for _name, module in fakes.items():
        module.__path__ = []  # type: ignore[attr-defined]
    for name, module in fakes.items():
        if "." in name:
            parent, _, child = name.rpartition(".")
            setattr(fakes[parent], child, module)
    for name, module in fakes.items():
        monkeypatch.setitem(sys.modules, name, module)
    return captured


def test_default_sender_converts_snapshot_and_calls_export(monkeypatch: Any) -> None:
    """With the extra present the push is OTLP ``export()``, not text."""
    captured = _install_fake_otel(monkeypatch)
    registry = _seeded_registry()
    exporter = otlp_export.maybe_start(_enabled_config(), registry)
    assert exporter is not None
    try:
        exporter.export_once()
    finally:
        exporter.stop()
    assert len(captured["exports"]) == 1
    data = captured["exports"][0]
    (resource_metrics,) = data.kwargs["resource_metrics"]
    (scope_metrics,) = resource_metrics.kwargs["scope_metrics"]
    assert scope_metrics.kwargs["scope"].args == ("omega-prime",)
    metrics = {m.args[0]: m for m in scope_metrics.kwargs["metrics"]}
    for series in R5_SERIES:
        assert series in metrics, f"{series} missing from OTLP export"
    total = metrics["omega_http_requests_total"]
    assert total.args[1] == "HTTP requests by method, path group and status class."
    assert total.args[2] == ""
    total_data = total.args[3]
    assert total_data.__class__.__name__ == "Sum"
    assert total_data.kwargs["is_monotonic"] is True
    assert total_data.kwargs["aggregation_temporality"] == 2
    (point,) = total_data.kwargs["data_points"]
    assert point.kwargs["attributes"] == {
        "method": "GET",
        "path": "/healthz",
        "status": "2xx",
    }
    assert point.kwargs["value"] == 1.0
    assert point.kwargs["time_unix_nano"] >= point.kwargs["start_time_unix_nano"]
    denied = metrics["omega_tool_denials_total"]
    assert denied.args[3].__class__.__name__ == "Sum"
    (dpoint,) = denied.args[3].kwargs["data_points"]
    assert dpoint.kwargs["attributes"] == {"code": "rate_limited"}
    assert dpoint.kwargs["value"] == 1.0
    duration = metrics["omega_http_request_duration_seconds"]
    duration_data = duration.args[3]
    assert duration_data.__class__.__name__ == "Histogram"
    assert duration_data.kwargs["aggregation_temporality"] == 2
    (hpoint,) = duration_data.kwargs["data_points"]
    assert hpoint.kwargs["attributes"] == {"method": "GET", "path": "/healthz"}
    assert sum(hpoint.kwargs["bucket_counts"]) == 1
    assert hpoint.kwargs["count"] == 1
    assert hpoint.kwargs["sum"] == 0.01
    assert (
        len(hpoint.kwargs["explicit_bounds"]) == len(hpoint.kwargs["bucket_counts"]) - 1
    )

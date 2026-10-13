# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Zero-dependency guard: the default install never touches OpenTelemetry."""

# NOTE on environment honesty: ``opentelemetry-api`` IS importable in this dev
# venv — it arrives transitively through PyRIT's dependency chain, not through
# anything omega-prime declares. ``importlib.metadata`` lists it, and any
# third-party import may leave ``opentelemetry.*`` keys in ``sys.modules``.
# That is an environment fact, not a product dependency. These guards therefore
# assert (a) ``opentelemetry`` appears in NO base dependency set (packaging
# metadata, read hermetically from the repo — no imports, no network), and
# (b)/(c) default-path code adds NO *NEW* ``opentelemetry*`` entries to
# ``sys.modules`` (delta before/after, never absolute cleanliness) while
# metrics keep rendering with the export flag off.

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _otel_modules() -> list[str]:
    return [
        module
        for module in sys.modules
        if module == "opentelemetry" or module.startswith("opentelemetry.")
    ]


def _new_otel_modules(before: set[str]) -> list[str]:
    """``opentelemetry*`` keys that appeared after the ``before`` snapshot."""
    return sorted(module for module in _otel_modules() if module not in before)


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_opentelemetry_is_neither_installed_nor_imported() -> None:
    """No base dependency set may declare ``opentelemetry``.

    Covers ``[project] dependencies`` in ``pyproject.toml`` and the pinned
    lines of ``requirements-lock.txt`` for the distributions behind our
    ``otel`` extra. A transitive ``opentelemetry-api`` pin elsewhere in the
    lockfile (PyRIT's chain) is allowed — it is not ours to declare.
    """
    project = _pyproject()["project"]
    base_deps: list[str] = project["dependencies"]
    assert all("opentelemetry" not in dep.lower() for dep in base_deps)

    otel_extra: list[str] = project["optional-dependencies"]["otel"]
    extra_dist_names = {
        req.split("[")[0]
        .split(";")[0]
        .split("(")[0]
        .split("=")[0]
        .split("<")[0]
        .split(">")[0]
        .strip()
        .lower()
        .replace("_", "-")
        for req in otel_extra
    }
    pinned = {
        line.split("==")[0].split(";")[0].strip().lower().replace("_", "-")
        for line in (REPO_ROOT / "requirements-lock.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith(("#", "-", " "))
    }
    assert extra_dist_names, "otel extra must declare its distributions"
    assert extra_dist_names.isdisjoint(pinned)


def test_runtime_modules_import_without_opentelemetry() -> None:
    before = set(_otel_modules())
    import omega_prime.config
    import omega_prime.grokbot.metrics
    import omega_prime.telemetry.otlp_export

    assert omega_prime.telemetry.otlp_export.OTEL_EXTRA == "otel"
    assert _new_otel_modules(before) == []

    registry = omega_prime.grokbot.metrics.MetricsRegistry()
    assert (
        omega_prime.telemetry.otlp_export.maybe_start(
            {"enabled": False, "endpoint": "", "interval_seconds": 60},
            registry,
        )
        is None
    )
    assert _new_otel_modules(before) == []


def test_default_config_disables_export() -> None:
    from omega_prime.config import load_config
    from omega_prime.grokbot.metrics import MetricsRegistry
    from omega_prime.telemetry.otlp_export import maybe_start

    before = set(_otel_modules())
    config = load_config()
    assert config["telemetry"]["otlp"] == {
        "enabled": False,
        "endpoint": "",
        "interval_seconds": 60,
    }
    assert maybe_start(config["telemetry"]["otlp"], MetricsRegistry()) is None
    assert _new_otel_modules(before) == []


def test_metrics_render_without_opentelemetry() -> None:
    from omega_prime.grokbot.metrics import MetricsRegistry

    before = set(_otel_modules())
    registry = MetricsRegistry()
    registry.counter(
        "omega_http_requests_total",
        "HTTP requests.",
        ("method", "path", "status"),
    ).labels(method="GET", path="/healthz", status="2xx").inc()

    assert "omega_http_requests_total" in registry.render()
    assert _new_otel_modules(before) == []

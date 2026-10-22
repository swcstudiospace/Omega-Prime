"""Shared broker-free fixtures for eventstream contract tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources
from typing import Any

import pytest


@pytest.fixture(scope="session")
def schema_bundles() -> dict[str, Mapping[str, Any]]:
    """Load each packaged schema bundle, keyed by its family name."""
    schema_dir = resources.files("omega_prime.eventstream").joinpath("schemas")
    bundles: dict[str, Mapping[str, Any]] = {}
    for resource in schema_dir.iterdir():
        if resource.name.endswith(".v1.json"):
            family = resource.name.removesuffix(".v1.json")
            bundles[family] = json.loads(resource.read_text(encoding="utf-8"))
    return bundles


@pytest.fixture(scope="session")
def payload_definitions(
    schema_bundles: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, str, Mapping[str, Any]]]:
    """Flatten payload definitions for shared schema and taxonomy checks."""
    return [
        (family, type_name, definition)
        for family, bundle in schema_bundles.items()
        for type_name, definition in bundle.get("$defs", {}).items()
        if type_name != "CloudEvent"
    ]

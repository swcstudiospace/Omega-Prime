# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic tests for the versioned SLO document and its loader.

Offline: no network, no collector, no subprocess. The shipped slo.json is
read from the source tree; corruption cases run against in-memory copies.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from omega_prime.telemetry import slo

SHIPPED = slo.DEFAULT_SLO_PATH


def _shipped_doc() -> dict:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def test_shipped_document_validates_with_three_objectives() -> None:
    doc = slo.load(SHIPPED)
    assert doc["schema_version"] == 1
    assert [objective["id"] for objective in doc["objectives"]] == [
        "serve-availability",
        "tool-call-latency-p99",
        "denial-rate-budget",
    ]


def test_check_command_exits_zero_on_shipped_document() -> None:
    assert slo.main(["check"]) == 0


def test_check_command_fails_on_missing_file(tmp_path: Path) -> None:
    assert slo.main(["check", "--file", str(tmp_path / "absent.json")]) == 1


def test_rejects_unknown_series_naming_the_objective() -> None:
    doc = _shipped_doc()
    doc["objectives"][0]["series"] = "omega_no_such_series_total"
    with pytest.raises(slo.SloError, match="serve-availability"):
        slo.validate(doc)


def test_rejects_bad_schema_version() -> None:
    doc = _shipped_doc()
    doc["schema_version"] = 2
    with pytest.raises(slo.SloError, match="schema_version"):
        slo.validate(doc)


def test_rejects_unknown_kind_naming_the_objective() -> None:
    doc = _shipped_doc()
    doc["objectives"][1]["kind"] = "apdex"
    with pytest.raises(slo.SloError, match="tool-call-latency-p99"):
        slo.validate(doc)


def test_rejects_missing_threshold_naming_the_objective() -> None:
    doc = _shipped_doc()
    del doc["objectives"][2]["target"]
    with pytest.raises(slo.SloError, match="denial-rate-budget"):
        slo.validate(doc)


def test_rejects_duplicate_objective_id() -> None:
    doc = _shipped_doc()
    doc["objectives"].append(copy.deepcopy(doc["objectives"][0]))
    with pytest.raises(slo.SloError, match="serve-availability"):
        slo.validate(doc)


def test_every_objective_series_exists_in_the_metrics_registry() -> None:
    """Drift guard: an SLO series removed from metrics.py fails loudly here."""
    from omega_prime.grokbot import metrics

    registry_text = Path(metrics.__file__).read_text(encoding="utf-8")
    for objective in _shipped_doc()["objectives"]:
        assert objective["series"] in slo.KNOWN_SERIES
        assert f'"{objective["series"]}"' in registry_text

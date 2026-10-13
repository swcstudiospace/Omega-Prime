# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Versioned SLO definitions and their stdlib-only loader (Phase 81-02).

Thresholds live in exactly one artifact, ``slo.json`` next to this module
(``schema_version: 1``). The alert generator, the nightly workflow, and the
docs reference that file; no threshold literal is duplicated anywhere else.
``validate`` rejects a bad ``schema_version``, missing keys, an unknown
``kind``, and any ``series`` outside the R5 registry mirror below — always
naming the offending objective so corruption fails loudly instead of
silently weakening an alert.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

DEFAULT_SLO_PATH = Path(__file__).with_name("slo.json")

KNOWN_SERIES = frozenset(
    {
        # Mirror of the R5 series registered in omega_prime/grokbot/metrics.py.
        "omega_http_requests_total",
        "omega_http_request_duration_seconds",
        "omega_http_in_flight",
        "omega_tool_calls_total",
        "omega_tool_call_duration_seconds",
        "omega_tool_calls_in_flight",
        "omega_tool_denials_total",
        "omega_auth_failures_total",
        "omega_build_info",
    }
)

KINDS = frozenset({"ratio", "quantile", "budget"})

_BASE_KEYS = frozenset({"id", "series", "kind", "window_days"})
_KIND_KEYS: dict[str, frozenset[str]] = {
    "ratio": frozenset({"good", "target"}),
    "quantile": frozenset({"quantile", "threshold_seconds"}),
    "budget": frozenset({"target"}),
}


class SloError(ValueError):
    """A rejected SLO document; the message always names the objective."""


def _fail(objective_id: str, detail: str) -> SloError:
    return SloError(f"slo objective {objective_id!r}: {detail}")


def _check_number(
    objective_id: str, key: str, value: Any, *, low: float, high: float
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(objective_id, f"{key} must be a number, got {value!r}")
    if not low < float(value) < high:
        raise _fail(objective_id, f"{key} must be in ({low}, {high}), got {value!r}")


def validate(doc: Any) -> dict[str, Any]:
    """Shape-check a parsed slo.json document; return it unchanged.

    Raises :class:`SloError` naming the objective on the first defect:
    wrong ``schema_version``, a missing key, an unknown ``kind``, or a
    ``series`` outside :data:`KNOWN_SERIES`.
    """
    if not isinstance(doc, dict):
        raise SloError("slo document must be an object")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise SloError(
            f"slo schema_version must be {SCHEMA_VERSION}, "
            f"got {doc.get('schema_version')!r}"
        )
    objectives = doc.get("objectives")
    if not isinstance(objectives, list) or not objectives:
        raise SloError("slo document must carry a non-empty 'objectives' list")
    seen: set[str] = set()
    for index, objective in enumerate(objectives):
        if not isinstance(objective, dict):
            raise SloError(f"slo objective at index {index} must be an object")
        objective_id = objective.get("id")
        if not isinstance(objective_id, str) or not objective_id:
            raise SloError(f"slo objective at index {index} is missing a string 'id'")
        if objective_id in seen:
            raise _fail(objective_id, f"duplicate objective id {objective_id!r}")
        seen.add(objective_id)
        kind = objective.get("kind")
        if kind not in KINDS:
            raise _fail(objective_id, f"unknown kind {kind!r}")
        required = _BASE_KEYS | _KIND_KEYS[kind]
        missing = sorted(key for key in required if key not in objective)
        if missing:
            raise _fail(objective_id, f"missing keys: {', '.join(missing)}")
        series = objective.get("series")
        if series not in KNOWN_SERIES:
            raise _fail(objective_id, f"unknown series {series!r}")
        window = objective.get("window_days")
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            raise _fail(
                objective_id, f"window_days must be a positive int, got {window!r}"
            )
        if kind in ("ratio", "budget"):
            _check_number(
                objective_id, "target", objective["target"], low=0.0, high=1.0
            )
        if kind == "ratio" and (
            not isinstance(objective.get("good"), str) or not objective["good"]
        ):
            raise _fail(objective_id, "good must be a non-empty matcher string")
        if kind == "quantile":
            _check_number(
                objective_id, "quantile", objective["quantile"], low=0.0, high=1.0
            )
            threshold = objective["threshold_seconds"]
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or float(threshold) <= 0
            ):
                raise _fail(
                    objective_id,
                    f"threshold_seconds must be a positive number, got {threshold!r}",
                )
    return doc


def load(path: str | Path = DEFAULT_SLO_PATH) -> dict[str, Any]:
    """Read ``path`` as JSON and :func:`validate` it."""
    raw = Path(path).read_text(encoding="utf-8")
    return validate(json.loads(raw))


def _check_command(path: Path) -> int:
    try:
        doc = load(path)
    except (OSError, json.JSONDecodeError, SloError) as exc:
        print(f"slo check failed: {exc}", file=sys.stderr)
        return 1
    objectives = doc["objectives"]
    print(f"slo check ok: {len(objectives)} objectives from {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python -m omega_prime.telemetry.slo check``."""
    parser = argparse.ArgumentParser(prog="omega-prime-telemetry-slo")
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--file", type=Path, default=DEFAULT_SLO_PATH)
    args = parser.parse_args(argv)
    if args.command == "check":
        return _check_command(args.file)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

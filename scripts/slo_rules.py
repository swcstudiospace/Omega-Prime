#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Generate a Prometheus rules file from the versioned SLO document.

Reads ``omega_prime/telemetry/slo.json`` (``--slo``) and emits one alert
per objective plus its supporting recording rule to the path given with
``--output``. Every threshold, window, series name, and matcher comes from
the loaded document — the only literals below are Prometheus/R5
conventions (the ``_bucket`` histogram suffix, the ``le`` label, the
denials-are-a-subset-of-calls pairing, per-kind ``for``/``severity``
routing). Same input yields a byte-identical file: mappings dump with
sorted keys, the header names the source plus its schema_version, and no
timestamp is emitted.

Usage:
    python scripts/slo_rules.py --output /tmp/omega-slo-rules.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omega_prime.telemetry import slo

# Per-kind alert routing (cadence/severity), keyed by objective kind.
# These are routing choices, not thresholds — thresholds come from slo.json.
_FOR_BY_KIND = {"ratio": "1h", "quantile": "15m", "budget": "6h"}
_SEVERITY_BY_KIND = {"ratio": "critical", "quantile": "warning", "budget": "warning"}
_ALERT_SUFFIX_BY_KIND = {"ratio": "burn", "quantile": "breach", "budget": "exhaustion"}

# R5 registry convention: denied tool calls counted by
# omega_tool_denials_total are a subset of omega_tool_calls_total, so the
# denial-share denominator is that total-counterpart series.
_CALLS_TOTAL_SERIES = "omega_tool_calls_total"

_MATCHER = re.compile(
    r"^(?P<label>[a-zA-Z_][a-zA-Z0-9_]*)(?P<op>=~|!~|!=|=)(?P<value>.*)$"
)
_NEGATED_OP = {"=~": "!~", "!~": "=~", "=": "!=", "!=": "="}


def _fmt_num(value: int | float) -> str:
    """Render a threshold number deterministically (no float dust)."""
    if isinstance(value, bool):
        raise ValueError(f"not a number: {value!r}")
    if isinstance(value, int):
        return str(value)
    text = repr(round(float(value), 9))
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _parse_matcher(text: str) -> tuple[str, str, str]:
    match = _MATCHER.match(text)
    if match is None:
        raise ValueError(f"unparseable matcher {text!r}")
    return match.group("label"), match.group("op"), match.group("value")


def _prom_matcher(label: str, op: str, value: str) -> str:
    return f"{label}{op}{value}" if value.startswith('"') else f'{label}{op}"{value}"'


def _negate_matcher(text: str) -> str:
    label, op, value = _parse_matcher(text)
    return _prom_matcher(label, _NEGATED_OP[op], value)


def _window(objective: dict) -> str:
    return f"{objective['window_days']}d"


def _ratio_rules(objective: dict) -> tuple[dict, dict]:
    series = objective["series"]
    window = _window(objective)
    target = objective["target"]
    budget = _fmt_num(1.0 - float(target))
    snake = objective["id"].replace("-", "_")
    record = f"slo:{snake}:error_share_{window}"
    bad = _negate_matcher(objective["good"])
    record_rule = {
        "record": record,
        "expr": (
            f"sum(rate({series}{{{bad}}}[{window}])) / sum(rate({series}[{window}]))"
        ),
    }
    alert_rule = {
        "alert": f"slo_{snake}_{_ALERT_SUFFIX_BY_KIND['ratio']}",
        "expr": f"{record} > {budget}",
        "for": _FOR_BY_KIND["ratio"],
        "labels": {
            "severity": _SEVERITY_BY_KIND["ratio"],
            "slo_id": objective["id"],
            "window": window,
        },
        "annotations": {
            "summary": (
                f"SLO {objective['id']}: error share above budget "
                f"({budget}) over {window}"
            ),
            "description": (
                f"Bad-request share of {series} exceeds the "
                f"{_fmt_num(target)} availability target's error budget "
                f"({budget}) over the last {window}."
            ),
        },
    }
    return record_rule, alert_rule


def _quantile_rules(objective: dict) -> tuple[dict, dict]:
    series = objective["series"]
    window = _window(objective)
    quantile = objective["quantile"]
    threshold = _fmt_num(objective["threshold_seconds"])
    snake = objective["id"].replace("-", "_")
    band = f"p{round(float(quantile) * 100)}"
    record = f"slo:{snake}:latency_{band}_{window}"
    record_rule = {
        "record": record,
        "expr": (
            f"histogram_quantile({_fmt_num(quantile)}, "
            f"sum by (le) (rate({series}_bucket[{window}])))"
        ),
    }
    alert_rule = {
        "alert": f"slo_{snake}_{_ALERT_SUFFIX_BY_KIND['quantile']}",
        "expr": f"{record} > {threshold}",
        "for": _FOR_BY_KIND["quantile"],
        "labels": {
            "severity": _SEVERITY_BY_KIND["quantile"],
            "slo_id": objective["id"],
            "window": window,
        },
        "annotations": {
            "summary": (
                f"SLO {objective['id']}: {band} latency above "
                f"{threshold}s over {window}"
            ),
            "description": (
                f"{band} of {series} exceeds the "
                f"{threshold}s threshold over the last {window}."
            ),
        },
    }
    return record_rule, alert_rule


def _budget_rules(objective: dict) -> tuple[dict, dict]:
    series = objective["series"]
    window = _window(objective)
    target = objective["target"]
    budget = _fmt_num(1.0 - float(target))
    snake = objective["id"].replace("-", "_")
    record = f"slo:{snake}:denial_share_{window}"
    record_rule = {
        "record": record,
        "expr": (
            f"sum(rate({series}[{window}])) "
            f"/ sum(rate({_CALLS_TOTAL_SERIES}[{window}]))"
        ),
    }
    alert_rule = {
        "alert": f"slo_{snake}_{_ALERT_SUFFIX_BY_KIND['budget']}",
        "expr": f"{record} > {budget}",
        "for": _FOR_BY_KIND["budget"],
        "labels": {
            "severity": _SEVERITY_BY_KIND["budget"],
            "slo_id": objective["id"],
            "window": window,
        },
        "annotations": {
            "summary": (
                f"SLO {objective['id']}: denial share above budget "
                f"({budget}) over {window}"
            ),
            "description": (
                f"Denied-tool-call share of {series} exhausts the "
                f"{_fmt_num(target)} budget target (budget {budget}) "
                f"over the last {window}."
            ),
        },
    }
    return record_rule, alert_rule


_BUILDERS = {
    "ratio": _ratio_rules,
    "quantile": _quantile_rules,
    "budget": _budget_rules,
}


def build_rules(doc: dict) -> dict:
    """One group per objective (document order), each with record + alert."""
    groups = []
    for objective in doc["objectives"]:
        record_rule, alert_rule = _BUILDERS[objective["kind"]](objective)
        groups.append(
            {"name": f"slo-{objective['id']}", "rules": [record_rule, alert_rule]}
        )
    return {"groups": groups}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python scripts/slo_rules.py --output PATH``."""
    parser = argparse.ArgumentParser(prog="omega-prime-slo-rules")
    parser.add_argument(
        "--slo", type=Path, default=Path("omega_prime/telemetry/slo.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        doc = slo.load(args.slo)
    except (OSError, ValueError) as exc:
        print(f"slo_rules: cannot load {args.slo}: {exc}", file=sys.stderr)
        return 1
    body = yaml.safe_dump(build_rules(doc), sort_keys=True, width=1024)
    header = (
        f"# Generated from {args.slo} "
        f"(schema_version {doc['schema_version']}) — do not edit by hand.\n"
        f"# Regenerate with: python scripts/slo_rules.py "
        f"--slo {args.slo} --output <path>\n"
    )
    args.output.write_text(header + body, encoding="utf-8")
    print(f"slo_rules: wrote {len(doc['objectives'])} alerts to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

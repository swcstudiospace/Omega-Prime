# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Per-case eval trend and regression check (Phase 84). Stdlib only.

Two modes (mirroring ``scripts/nightly_readiness_check.py``)::

    python3 -m omega_prime.evals.trend --history H [--threshold X]
    python3 -m omega_prime.evals.trend check --baseline B --report R [--threshold X]
    python3 -m omega_prime.evals.trend bless --baseline B --report R
    python3 -m omega_prime.evals.trend --bless --report R --baseline B

History mode reads runs sequentially, prints each case's trend
(first/last/min/mean, delta vs the previous run for the same case), and a
regression line naming every case whose score dropped >= threshold between
the last two runs. Exit 1 on regression (naming case ids + deltas), exit 0
on flat/green history; malformed input exits nonzero fail-closed with the
offending line identified.

Baseline mode compares a fresh rubric report against the committed
``omega_prime/evals/history-baseline.json``: exit 1 names every case
scoring below its baseline by >= threshold (or below ``min_score``). The
baseline moves ONLY via an explicit ``bless`` run inside a reviewed PR —
``check`` and the nightly workflow never write it.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_USAGE = 2

DEFAULT_THRESHOLD = 0.1


class TrendError(ValueError):
    """A malformed history, baseline, or report document."""


def resolve_threshold(explicit: float | None = None) -> float:
    """Threshold from ``--threshold`` or the ``evals`` config node."""
    if explicit is not None:
        return explicit
    try:
        from omega_prime.config import load_config

        node = load_config().get("evals", {})
        value = node.get("regress_threshold", DEFAULT_THRESHOLD)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrendError("evals.regress_threshold is not numeric")
        return float(value)
    except TrendError:
        raise
    except Exception as exc:
        raise TrendError(f"cannot resolve threshold: {exc}") from exc


def _runs_in_order(records: list[dict]) -> list[str]:
    """Distinct run ids in first-seen (append) order."""
    seen: list[str] = []
    for record in records:
        rid = record.get("run_id")
        if not isinstance(rid, str):
            continue
        if rid not in seen:
            seen.append(rid)
    return seen


def per_case_trend(records: list[dict]) -> dict[str, dict]:
    """Per-case ``{runs, first, last, min, mean, delta}`` summary."""
    by_case: dict[str, list[float]] = {}
    for record in records:
        case_id = record.get("case_id")
        score = record.get("score")
        if not isinstance(case_id, str) or not case_id:
            raise TrendError("history record needs a non-empty string case_id")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise TrendError(f"history record for {case_id!r} has a non-numeric score")
        by_case.setdefault(case_id, []).append(float(score))
    trends: dict[str, dict] = {}
    for case_id, scores in by_case.items():
        delta = scores[-1] - scores[-2] if len(scores) >= 2 else 0.0
        trends[case_id] = {
            "runs": len(scores),
            "first": scores[0],
            "last": scores[-1],
            "min": min(scores),
            "mean": sum(scores) / len(scores),
            "delta": delta,
        }
    return trends


def find_regressions(
    records: list[dict], threshold: float = DEFAULT_THRESHOLD
) -> list[tuple[str, float]]:
    """Cases whose score dropped >= threshold between the last two runs."""
    runs = _runs_in_order(records)
    if len(runs) < 2:
        return []
    prev_id, last_id = runs[-2], runs[-1]
    prev = {
        r["case_id"]: float(r["score"]) for r in records if r.get("run_id") == prev_id
    }
    last = {
        r["case_id"]: float(r["score"]) for r in records if r.get("run_id") == last_id
    }
    regressed: list[tuple[str, float]] = []
    for case_id, score in last.items():
        if case_id in prev:
            delta = score - prev[case_id]
            if delta <= -abs(threshold):
                regressed.append((case_id, delta))
    return sorted(regressed)


def _history_command(history: Path, threshold: float) -> int:
    from omega_prime.evals.history import HistoryError, read_history

    try:
        records = read_history(history)
    except HistoryError as exc:
        print(f"trend failed: {exc}", file=sys.stderr)
        return EXIT_REGRESSION
    if not records:
        print(f"trend: no records in {history} — nothing to compare")
        return EXIT_OK
    try:
        trends = per_case_trend(records)
        regressed = find_regressions(records, threshold)
    except TrendError as exc:
        print(f"trend failed: {exc}", file=sys.stderr)
        return EXIT_REGRESSION
    for case_id in sorted(trends):
        trend = trends[case_id]
        print(
            f"trend {case_id}: runs={trend['runs']} "
            f"first={trend['first']:.3f} last={trend['last']:.3f} "
            f"min={trend['min']:.3f} mean={trend['mean']:.3f} "
            f"delta={trend['delta']:+.3f}"
        )
    if regressed:
        names = ", ".join(f"{case_id} ({delta:+.3f})" for case_id, delta in regressed)
        print(f"regression: {names} (threshold={threshold})")
        return EXIT_REGRESSION
    print(f"ok: no case dropped >= {threshold} between the last two runs")
    return EXIT_OK


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TrendError(f"cannot read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TrendError(f"{label} {path} is not valid JSON: {exc}") from exc


def _baseline_cases(doc: Any, label: str) -> dict[str, dict]:
    if isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict) and isinstance(doc.get("cases"), list):
        items = doc["cases"]
    elif isinstance(doc, dict) and isinstance(doc.get("cases"), dict):
        return doc["cases"]
    else:
        raise TrendError(f"{label}: expected a list or an object with cases")
    out: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("case_id"):
            raise TrendError(f"{label}: every case needs a case_id")
        out[item["case_id"]] = item
    return out


def compare_baseline(
    baseline_doc: Any, report_doc: Any, threshold: float = DEFAULT_THRESHOLD
) -> tuple[bool, str]:
    """Return ``(is_regression, human_line)`` for a baseline/report pair."""
    baseline = _baseline_cases(baseline_doc, "baseline")
    if not isinstance(report_doc, dict) or not isinstance(
        report_doc.get("rubric_cases"), list
    ):
        raise TrendError("report: expected an object with a rubric_cases list")
    report = _baseline_cases({"cases": report_doc["rubric_cases"]}, "report")
    missing = sorted(set(baseline) - set(report))
    if missing:
        raise TrendError(
            f"report is missing baselined cases: {', '.join(missing)} — "
            "re-bless explicitly if the set legitimately changed"
        )
    regressed: list[str] = []
    for case_id in sorted(baseline):
        base_score = baseline[case_id].get("score")
        rep_score = report[case_id].get("score")
        min_score = report[case_id].get(
            "min_score", baseline[case_id].get("min_score", 0.0)
        )
        if not isinstance(rep_score, (int, float)) or isinstance(rep_score, bool):
            raise TrendError(f"report: case {case_id!r} has a non-numeric score")
        if not isinstance(base_score, (int, float)) or isinstance(base_score, bool):
            raise TrendError(f"baseline: case {case_id!r} has a non-numeric score")
        drop = float(base_score) - float(rep_score)
        if drop >= threshold:
            regressed.append(
                f"{case_id} (baseline={base_score} report={rep_score} drop={drop:.3f})"
            )
        elif float(rep_score) < float(min_score):
            regressed.append(
                f"{case_id} (report={rep_score} below min_score={min_score})"
            )
    if regressed:
        return True, f"regression vs baseline: {'; '.join(regressed)}"
    return (
        False,
        f"ok: {len(baseline)} cases at or above baseline (threshold={threshold})",
    )


def _check_command(baseline: Path, report: Path, threshold: float) -> int:
    try:
        regressed, line = compare_baseline(
            _load_json(baseline, "baseline"), _load_json(report, "report"), threshold
        )
    except TrendError as exc:
        print(f"trend check failed: {exc}", file=sys.stderr)
        return EXIT_REGRESSION
    print(line)
    return EXIT_REGRESSION if regressed else EXIT_OK


def _bless_command(baseline: Path, report: Path) -> int:
    try:
        report_doc = _load_json(report, "report")
        cases = _baseline_cases({"cases": report_doc["rubric_cases"]}, "report")
    except (TrendError, KeyError, AttributeError) as exc:
        print(f"bless refused: {exc}", file=sys.stderr)
        return EXIT_REGRESSION
    blessed = {
        "generated_at": datetime.datetime.now(datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "note": "explicit --bless in a reviewed PR; the workflow never blesses",
        "cases": [
            {
                "case_id": case_id,
                "score": cases[case_id].get("score"),
                "min_score": cases[case_id].get("min_score", 1.0),
            }
            for case_id in sorted(cases)
        ],
    }
    text = json.dumps(blessed, indent=2, sort_keys=False) + "\n"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(baseline.parent), prefix=baseline.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, baseline)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        print(f"bless failed: cannot write {baseline}: {exc}", file=sys.stderr)
        return EXIT_REGRESSION
    print(f"blessed {baseline} from {len(cases)} rubric cases")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: history mode plus ``check`` / ``bless`` subcommands."""
    parser = argparse.ArgumentParser(prog="omega-prime-evals-trend")
    parser.add_argument("--history", type=Path, default=None)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--bless", action="store_true", default=False)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=False)
    check = sub.add_parser("check", help="exit non-zero on baseline regression")
    check.add_argument("--baseline", type=Path, required=True)
    check.add_argument("--report", type=Path, required=True)
    check.add_argument("--threshold", type=float, default=None)
    bless = sub.add_parser("bless", help="explicitly rewrite the baseline")
    bless.add_argument("--baseline", type=Path, required=True)
    bless.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        threshold = (
            args.threshold if args.threshold is not None else resolve_threshold()
        )
        return _check_command(args.baseline, args.report, threshold)
    if args.command == "bless" or args.bless:
        if args.baseline is None or args.report is None:
            print("trend: --bless needs --baseline and --report", file=sys.stderr)
            return EXIT_USAGE
        return _bless_command(args.baseline, args.report)
    if args.history is None:
        print("trend: need --history or a check/bless subcommand", file=sys.stderr)
        return EXIT_USAGE
    threshold = args.threshold if args.threshold is not None else resolve_threshold()
    return _history_command(args.history, threshold)


if __name__ == "__main__":
    raise SystemExit(main())

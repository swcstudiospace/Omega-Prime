#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Nightly readiness regression gate (Phase 81-02). Stdlib only.

Compares a fresh readiness report against the committed
``omega_prime/telemetry/readiness_baseline.json``. Regression (per
``.planning/updates/plan-03.md``) is a report with ``ok: false``, or an
``summary.exercised`` count lower than the baseline for the same
profile/scope. ``check`` exits 0 on green and 1 on regression, naming the
profile and the delta. A profile/scope mismatch or a malformed document
also exits non-zero (fail-closed: a human must look).

The baseline moves ONLY via an explicit ``bless`` run inside a reviewed
PR — ``check`` and the nightly workflow never write it::

    python scripts/nightly_readiness_check.py check \\
        --baseline omega_prime/telemetry/readiness_baseline.json \\
        --report /tmp/readiness-report.json

    python scripts/nightly_readiness_check.py bless \\
        --baseline omega_prime/telemetry/readiness_baseline.json \\
        --report /tmp/readiness-report.json
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


class BaselineError(ValueError):
    """A malformed baseline/report document or an incomparable pair."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BaselineError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{path} is not valid JSON: {exc}") from exc


def _shape(doc: Any, path: Path) -> tuple[bool, str, str, int]:
    """Project the (ok, profile, scope, exercised) comparison keys."""
    if not isinstance(doc, dict):
        raise BaselineError(f"{path}: report must be an object")
    ok = doc.get("ok")
    if not isinstance(ok, bool):
        raise BaselineError(f"{path}: 'ok' must be a bool")
    profile = doc.get("profile")
    if not isinstance(profile, str) or not profile:
        raise BaselineError(f"{path}: 'profile' must be a non-empty string")
    scope = doc.get("scope")
    if not isinstance(scope, str) or not scope:
        raise BaselineError(f"{path}: 'scope' must be a non-empty string")
    summary = doc.get("summary")
    if not isinstance(summary, dict):
        raise BaselineError(f"{path}: 'summary' must be an object")
    exercised = summary.get("exercised")
    if isinstance(exercised, bool) or not isinstance(exercised, int) or exercised < 0:
        raise BaselineError(f"{path}: 'summary.exercised' must be a non-negative int")
    return ok, profile, scope, exercised


def compare(baseline_doc: Any, report_doc: Any) -> tuple[bool, str]:
    """Return (is_regression, human_line) for a baseline/report pair."""
    _, base_profile, base_scope, base_exercised = _shape(baseline_doc, Path("baseline"))
    rep_ok, rep_profile, rep_scope, rep_exercised = _shape(report_doc, Path("report"))
    if (rep_profile, rep_scope) != (base_profile, base_scope):
        raise BaselineError(
            f"profile/scope mismatch: baseline {base_profile}/{base_scope} "
            f"vs report {rep_profile}/{rep_scope} — re-bless explicitly "
            "if the profile legitimately changed"
        )
    if not rep_ok:
        return True, (
            f"regression: profile={rep_profile} ok=false "
            f"(baseline exercised={base_exercised})"
        )
    if rep_exercised < base_exercised:
        delta = rep_exercised - base_exercised
        return True, (
            f"regression: profile={rep_profile} exercised={rep_exercised} "
            f"baseline={base_exercised} delta={delta:+d}"
        )
    return False, (
        f"ok: profile={rep_profile} exercised={rep_exercised} baseline={base_exercised}"
    )


def _check_command(baseline: Path, report: Path) -> int:
    try:
        regressed, line = compare(_load_json(baseline), _load_json(report))
    except BaselineError as exc:
        print(f"readiness check failed: {exc}", file=sys.stderr)
        return EXIT_REGRESSION
    print(line)
    return EXIT_REGRESSION if regressed else EXIT_OK


def _bless_command(baseline: Path, report: Path) -> int:
    try:
        report_doc = _load_json(report)
        ok, profile, scope, exercised = _shape(report_doc, report)
    except BaselineError as exc:
        print(f"bless refused: {exc}", file=sys.stderr)
        return EXIT_REGRESSION
    if not isinstance(report_doc, dict):
        print(f"bless refused: {report} is not an object", file=sys.stderr)
        return EXIT_REGRESSION
    if not ok:
        print("bless warning: blessing a report with ok=false", file=sys.stderr)
    blessed = dict(report_doc)
    blessed["blessed_from"] = {
        "blessed_at": datetime.datetime.now(datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "profile": profile,
        "scope": scope,
        "exercised": exercised,
        "note": "explicit --bless in a reviewed PR; the workflow never blesses",
    }
    text = json.dumps(blessed, indent=2, sort_keys=True) + "\n"
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
    print(f"blessed {baseline} from profile={profile} exercised={exercised}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entry point with ``check`` and ``bless`` subcommands."""
    parser = argparse.ArgumentParser(prog="omega-prime-nightly-readiness-check")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="exit non-zero on regression")
    check.add_argument("--baseline", type=Path, required=True)
    check.add_argument("--report", type=Path, required=True)
    bless = sub.add_parser("bless", help="explicitly rewrite the baseline")
    bless.add_argument("--baseline", type=Path, required=True)
    bless.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        return _check_command(args.baseline, args.report)
    if args.command == "bless":
        return _bless_command(args.baseline, args.report)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())

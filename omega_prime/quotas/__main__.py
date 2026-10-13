# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Usage rollup CLI: reduce recorded counters into a per-day summary file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m omega_prime.quotas",
        description="Per-caller usage quota rollups (Phase 82).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    rollup = sub.add_parser(
        "rollup", help="Write rollup_dir/usage-YYYY-MM-DD.json for one UTC day."
    )
    rollup.add_argument("--date", required=True, help="UTC day as YYYY-MM-DD.")
    rollup.add_argument(
        "--rollup-dir",
        default=".omega-quotas/",
        help="Directory for usage-<date>.json files.",
    )
    rollup.add_argument(
        "--document",
        default="",
        help="Quota document path (falls back to OMEGA_PRIME_QUOTAS_DOCUMENT).",
    )
    rollup.add_argument(
        "--counters",
        default="",
        help="JSON file mapping caller label to recorded counters.",
    )
    return parser


def _cmd_rollup(args: argparse.Namespace) -> int:
    from omega_prime.quotas import QuotaEngine, load_quota_document, write_rollup_file

    document = args.document or os.environ.get("OMEGA_PRIME_QUOTAS_DOCUMENT", "")
    try:
        limits = load_quota_document(document or None)
        engine = QuotaEngine(mode="report", limits=limits)
        if not engine.is_active:
            print("quotas disabled (no quota document); skipping rollup")
            return 0
        counts: dict = {}
        if args.counters:
            counts = json.loads(Path(args.counters).read_text(encoding="utf-8"))
            if not isinstance(counts, dict):
                raise ValueError("--counters must contain a JSON object")
        engine.seed_day(args.date, counts)
        target = write_rollup_file(
            args.rollup_dir, args.date, engine.build_rollup(args.date)
        )
    except Exception as exc:
        print(f"rollup failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {target}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    if args.command == "rollup":
        return _cmd_rollup(args)
    return 2  # pragma: no cover - argparse enforces the subcommand


if __name__ == "__main__":
    raise SystemExit(main())

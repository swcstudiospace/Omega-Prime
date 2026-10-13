# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Append-only per-run eval history (Phase 84).

Runtime data at the repo root (``.omega-evals/history.ndjson``, gitignored):
one JSON object per line per run per case::

    {run_id, commit, case_id, score, criteria, timestamp}

``run_id`` is ``<commit>-<utc-timestamp>``. Appends create parent dirs.
Reads apply torn-tail repair like the EventStore pattern: a truncated last
line is dropped with a stderr warning, earlier lines intact. A corrupt
non-tail line is fail-closed (``HistoryError`` naming the line). History
writes are dark — nothing gates on them.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

HISTORY_PATH = Path(".omega-evals/history.ndjson")


class HistoryError(ValueError):
    """A malformed history line that is not a repairable torn tail."""


def utc_timestamp() -> str:
    """Current UTC time in compact ISO-8601 (``Z``) form."""
    return (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def resolve_commit() -> str:
    """Best-effort commit id for a run record. Never raises."""
    env_sha = os.environ.get("GITHUB_SHA", "").strip()
    if env_sha:
        return env_sha[:12]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def make_run_id(commit: str, timestamp: str) -> str:
    """``run_id`` is ``<commit>-<utc-timestamp>`` (no spaces)."""
    return f"{commit}-{timestamp}".replace(" ", "T")


def read_history(path: str | Path = HISTORY_PATH) -> list[dict]:
    """Read history records, dropping a truncated last line with a warning."""
    location = Path(path)
    if not location.is_file():
        return []
    text = location.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    records: list[dict] = []
    for lineno, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if lineno == len(lines):
                print(
                    f"history warning: dropping truncated last line "
                    f"(line {lineno} of {location})",
                    file=sys.stderr,
                )
                break
            raise HistoryError(
                f"{location} line {lineno} is not valid JSON "
                "(only a torn tail line is repairable)"
            ) from None
        if not isinstance(record, dict):
            if lineno == len(lines):
                print(
                    f"history warning: dropping non-object last line "
                    f"(line {lineno} of {location})",
                    file=sys.stderr,
                )
                break
            raise HistoryError(f"{location} line {lineno} must be an object")
        records.append(record)
    return records


def append_records(
    case_records: list[dict],
    path: str | Path = HISTORY_PATH,
    commit: str | None = None,
    run_id: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Append one line per case record. Returns the shared ``run_id``."""
    location = Path(path)
    stamp = timestamp or utc_timestamp()
    base_commit = commit or resolve_commit()
    rid = run_id or make_run_id(base_commit, stamp)
    location.parent.mkdir(parents=True, exist_ok=True)
    with location.open("a", encoding="utf-8") as handle:
        for record in case_records:
            row = {
                "run_id": rid,
                "commit": base_commit,
                "case_id": record.get("case_id", record.get("id", "<missing-id>")),
                "score": float(record.get("score", 0.0)),
                "criteria": record.get("criteria", []),
                "timestamp": stamp,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rid


def _append_from_report(report_path: Path, history_path: Path) -> int:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"history append failed: cannot read {report_path}: {exc}", file=sys.stderr
        )
        return 1
    if not isinstance(report, dict) or not isinstance(report.get("rubric_cases"), list):
        print(
            f"history append failed: {report_path} has no rubric_cases list",
            file=sys.stderr,
        )
        return 1
    rid = append_records(report["rubric_cases"], path=history_path)
    print(f"appended {len(report['rubric_cases'])} case records run_id={rid}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """``python3 -m omega_prime.evals.history --report R [--history H]``."""
    parser = argparse.ArgumentParser(prog="omega-prime-evals-history")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--history", type=Path, default=HISTORY_PATH)
    args = parser.parse_args(argv)
    return _append_from_report(args.report, args.history)


if __name__ == "__main__":
    raise SystemExit(main())

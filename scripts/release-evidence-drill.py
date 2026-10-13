#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Offline staging-first promotion evidence drill (Phase 80-02).

Renders all four deploy targets, shows the promotion warnings with a bare
marker (exit 0 preserved), clears them with a staging receipt plus image
digest plus approval record, then promotes the digest through
``omega_prime.grokbot.deploy.promote`` to write the chained evidence JSON.

Stdlib only. No shell, no network, no docker. Takes only ``--out`` for the
evidence directory; no argument reaches a shell.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omega_prime.grokbot import deploy

DRILL_DIGEST = "sha256:" + hashlib.sha256(b"omega-prime-80-02-drill").hexdigest()

MARKER = "promotion.yaml"
STAGING_RECEIPT = "staging-receipt.json"
DIGEST_FILE = "image.digest"
APPROVAL_RECORD = "approval.json"


def _stage(
    results: list[tuple[str, bool, str]], name: str, ok: bool, detail: str
) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline staging-first evidence drill."
    )
    parser.add_argument("--out", required=True, type=Path, metavar="DIR")
    args = parser.parse_args(argv)
    out = args.out

    results: list[tuple[str, bool, str]] = []

    work = Path(tempfile.mkdtemp(prefix="omp80-evidence-drill-"))
    try:
        rendered = 0
        for target in deploy.TARGETS:
            deploy.render(target, work)
            rendered += 1
        _stage(
            results,
            "stage-1-render",
            rendered == 4,
            f"rendered {rendered}/4 targets into temp dir",
        )

        (work / MARKER).write_text("promotion: true\n", encoding="utf-8")
        findings = deploy.check(work)
        ids = {f.id for f in findings if f.severity == "warning"}
        both = {
            "promote-without-staging-evidence",
            "missing-promotion-approval-record",
        } <= ids
        cli_ok = deploy.main(["check", str(work)]) == 0
        _stage(
            results,
            "stage-2-warn",
            both and cli_ok,
            f"marker warns twice with exit 0 (warnings={sorted(ids)})",
        )

        (work / STAGING_RECEIPT).write_text('{"ok": true}\n', encoding="utf-8")
        (work / DIGEST_FILE).write_text(DRILL_DIGEST + "\n", encoding="utf-8")
        (work / APPROVAL_RECORD).write_text(
            '{"approver": "oncall"}\n', encoding="utf-8"
        )
        cleared = deploy.check(work)
        cleared_ids = {f.id for f in cleared}
        quiet = not (
            {
                "promote-without-staging-evidence",
                "missing-promotion-approval-record",
            }
            & cleared_ids
        )
        _stage(
            results,
            "stage-3-clear",
            quiet,
            f"staging+digest+approval clears promotion warnings ({len(cleared)} finding(s) left)",
        )

        out.mkdir(parents=True, exist_ok=True)
        shutil.copytree(work, out, dirs_exist_ok=True)
        code = deploy.promote(DRILL_DIGEST, out, out / APPROVAL_RECORD)
        matches = sorted(glob.glob(str(out / "*promotion*evidence*.json")))
        evidence_ok = False
        keys: object = None
        if code == 0 and matches:
            payload = json.loads(Path(matches[-1]).read_text(encoding="utf-8"))
            keys = sorted(payload.keys())
            evidence_ok = {
                "promoted_digest",
                "previous_digest",
                "approval",
                "timestamp",
            } <= set(payload.keys()) and payload["promoted_digest"] == DRILL_DIGEST
        _stage(
            results,
            "stage-4-promote",
            evidence_ok,
            f"promote exit {code}, evidence keys={keys}",
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"DRILL FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"DRILL OK: {len(results)} stages passed, evidence in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

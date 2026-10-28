#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Triage `npm audit --json` output for the redteam sidecar backend.

Fails closed: only real audit output with no untriaged high-or-above
advisories passes. An npm error payload (an ``"error"`` member), a
document missing both ``vulnerabilities`` and ``advisories`` keys, or
unreadable JSON all fail nonzero naming the parsed error — they never
pass as "zero findings".

Usage: ``AUDIT_JSON=<path> python3 scripts/redteam-audit-triage.py``.
"""

from __future__ import annotations

import json
import os
import sys

# Triaged advisories: finding key -> (date, rationale). Mirrors the
# PYSEC-2026-4114 ignore in the pip-audit job: every entry needs a date
# and a rationale, and anything not listed here fails the job. EMPTY of
# high-or-above as of 2026-10-13 at pin 831a6b0: no high-or-above
# advisory in the redteam dependency tree has been triaged yet. Only
# triage real audit output — never invent entries.
#
# Moderate sweep 2026-10-14 at pin f6d2fc2 (post-dep-bump: prod audit
# `--omit=dev --audit-level=high` is green, 0 findings): the full audit
# carries 3 moderate entries under one advisory, GHSA-82fw-gwwq-j7x9
# (vitest @vitest/mocker path traversal, CVSS 5.9). Keys are the package
# names `triage()` derives for these entries (their `via` rows are dicts
# or package names, so no GHSA/URL string is extracted).
TRIAGED: dict[str, tuple[str, str]] = {
    # Example shape:
    # "GHSA-xxxx-yyyy-zzzz": ("2026-10-13", "dev-only transitive; loopback sidecar never loads it."),
    # Nested zod 3 under @ag-ui/core (GHSA-mrhx-c7xc-m3w5). No fix on the zod 3 line; top-level
    # zod is already 4.6.7. These packages are the War Room UI GenUI stack — TypeScript runtime
    # and UI are not adapted into Omega Prime (VENDOR.md) and are never loaded by the loopback
    # sidecar the redteam adapter talks to.
    "@ag-ui/core": (
        "2026-10-29",
        "High GHSA-mrhx-c7xc-m3w5 via nested zod 3; no fix on the zod 3 line. @ag-ui/core is pulled by @openuidev/react-headless (War Room GenUI). Not adapted into Omega Prime; never loaded by the loopback sidecar.",
    ),
    "@openuidev/react-headless": (
        "2026-10-29",
        "High via @ag-ui/core nested zod 3 (GHSA-mrhx-c7xc-m3w5). War Room GenUI headless kit; not adapted into Omega Prime; never loaded by the loopback sidecar.",
    ),
    "@openuidev/react-ui": (
        "2026-10-29",
        "High via @openuidev/react-headless → @ag-ui/core nested zod 3 (GHSA-mrhx-c7xc-m3w5). War Room GenUI; not adapted into Omega Prime; never loaded by the loopback sidecar.",
    ),
    "zod": (
        "2026-10-29",
        "High GHSA-mrhx-c7xc-m3w5 on the nested zod 3 copy under @ag-ui/core only; top-level zod is 4.6.7 (patched). No fix available on the zod 3 line. Nested copy is War Room GenUI only; never loaded by the loopback sidecar.",
    ),
    "@vitest/coverage-v8": (
        "2026-10-14",
        "Moderate GHSA-82fw-gwwq-j7x9 (vitest @vitest/mocker path traversal, CVSS 5.9); dev-only test coverage transitive, never loaded by the loopback sidecar; default-off family, never in the runtime image.",
    ),
    "@vitest/mocker": (
        "2026-10-14",
        "Moderate GHSA-82fw-gwwq-j7x9 (vitest @vitest/mocker path traversal, CVSS 5.9); dev-only test mocking helper, never loaded by the loopback sidecar; default-off family, never in the runtime image.",
    ),
    "vitest": (
        "2026-10-14",
        "Moderate GHSA-82fw-gwwq-j7x9 (vitest @vitest/mocker path traversal, CVSS 5.9); dev-only test runner, never loaded by the loopback sidecar; default-off family, never in the runtime image.",
    ),
}


def triage(doc: object) -> tuple[list[tuple[str, str, object]], str | None]:
    """Split audit output into high+ findings and a fatal error message.

    Returns ``(findings, error)``: ``error`` is non-None when the document
    is not usable audit output (fail closed); otherwise ``findings`` holds
    ``(key, severity, name)`` rows for triage.
    """
    if not isinstance(doc, dict):
        return [], "audit output is not a JSON object"
    error = doc.get("error")
    if error is not None:
        return [], f"npm audit reported an error: {error}"
    vulnerabilities = doc.get("vulnerabilities")
    advisories = doc.get("advisories")
    if vulnerabilities is None and advisories is None:
        return [], (
            "audit output has neither 'vulnerabilities' nor 'advisories' keys; "
            "refusing to treat an unknown shape as clean"
        )
    findings: list[tuple[str, str, object]] = []
    for name, vuln in (vulnerabilities or {}).items():
        if not isinstance(vuln, dict):
            continue
        severity = str(vuln.get("severity") or "").lower()
        if severity not in ("high", "critical"):
            continue
        via = vuln.get("via") or []
        key = next(
            (
                str(entry)
                for entry in via
                if isinstance(entry, str)
                and ("GHSA" in entry or "CVE" in entry or entry.startswith("https://"))
            ),
            name,
        )
        findings.append((key, severity, name))
    if isinstance(advisories, dict):
        for advisory in advisories.values():
            if not isinstance(advisory, dict):
                continue
            severity = str(advisory.get("severity") or "").lower()
            if severity not in ("high", "critical"):
                continue
            key = (
                advisory.get("url")
                or advisory.get("cves")
                or advisory.get("module_name")
                or str(advisory.get("id"))
            )
            findings.append((str(key), severity, advisory.get("module_name")))
    return findings, None


def main() -> int:
    """Entry point. Returns 0 when clean, 1 otherwise."""
    audit_path = os.environ.get("AUDIT_JSON", "")
    if not audit_path:
        print("redteam-node-audit FAILED: AUDIT_JSON is not set", file=sys.stderr)
        return 1
    try:
        with open(audit_path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        print(
            f"redteam-node-audit FAILED: cannot parse audit JSON ({exc})",
            file=sys.stderr,
        )
        return 1
    findings, error = triage(doc)
    if error is not None:
        print(f"redteam-node-audit FAILED: {error}", file=sys.stderr)
        return 1
    untriaged = [finding for finding in findings if finding[0] not in TRIAGED]
    if untriaged:
        print("redteam-node-audit FAILED: untriaged high/critical advisories:")
        for key, severity, name in untriaged:
            print(f"  {severity} {name}: {key}")
        return 1
    print(
        f"redteam-node-audit OK: {len(findings)} high+ findings, "
        f"{len(untriaged)} untriaged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

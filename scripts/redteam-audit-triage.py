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
import re
import sys

# Triaged advisories: finding key -> (date, rationale). Mirrors the
# PYSEC-2026-4114 ignore in the pip-audit job: every entry needs a date
# and a rationale, and anything not listed here fails the job. Only
# triage real audit output — never invent entries.
#
# Finding keys are scoped: ``"<advisory>::<dependency path>"``. The
# advisory is the GHSA id (else CVE, URL, or ``npm-source:<id>``) of a
# reviewed advisory and the path is the exact installed copy npm reports
# in ``nodes`` (legacy shape: ``findings[].paths``). An entry therefore
# accepts ONE reviewed advisory on ONE reviewed copy: a different
# advisory on the same package, or the same advisory on another copy
# (for example top-level ``node_modules/zod``), derives a different key
# and fails the job. Package names alone are never keys for a resolved
# advisory.
#
# Moderate sweep 2026-10-14 at pin f6d2fc2: the full audit carries 3
# moderate entries under GHSA-82fw-gwwq-j7x9 (vitest @vitest/mocker path
# traversal, CVSS 5.9). Those rows are below the high gate, so
# ``triage()`` never emits them; the entries below record the review in
# the scoped key shape for the dev-tree copies that were reviewed.
TRIAGED: dict[str, tuple[str, str]] = {
    # Example shape:
    # "GHSA-xxxx-yyyy-zzzz::node_modules/pkg": ("2026-10-13", "dev-only transitive; loopback sidecar never loads it."),
    # Nested zod 3 under @ag-ui/core (GHSA-mrhx-c7xc-m3w5, zod <4.6.6). No fix on the zod 3 line;
    # top-level zod is 4.6.7 (patched) and is NOT triaged. These packages are the War Room UI GenUI
    # stack — TypeScript runtime and UI are not adapted into Omega Prime (VENDOR.md) and are never
    # loaded by the loopback sidecar the redteam adapter talks to. Audit at pin d1a2209, 2026-10-29.
    "GHSA-mrhx-c7xc-m3w5::node_modules/@ag-ui/core/node_modules/zod": (
        "2026-10-29",
        "High GHSA-mrhx-c7xc-m3w5 on the nested zod 3 copy under @ag-ui/core only; top-level zod is 4.6.7 (patched) and not triaged. No fix available on the zod 3 line. Nested copy is War Room GenUI only; never loaded by the loopback sidecar.",
    ),
    "GHSA-mrhx-c7xc-m3w5::node_modules/@ag-ui/core": (
        "2026-10-29",
        "High GHSA-mrhx-c7xc-m3w5 via nested zod 3; no fix on the zod 3 line. @ag-ui/core is pulled by @openuidev/react-headless (War Room GenUI). Not adapted into Omega Prime; never loaded by the loopback sidecar.",
    ),
    "GHSA-mrhx-c7xc-m3w5::node_modules/@openuidev/react-headless": (
        "2026-10-29",
        "High via @ag-ui/core nested zod 3 (GHSA-mrhx-c7xc-m3w5). War Room GenUI headless kit; not adapted into Omega Prime; never loaded by the loopback sidecar.",
    ),
    "GHSA-mrhx-c7xc-m3w5::node_modules/@openuidev/react-ui": (
        "2026-10-29",
        "High via @openuidev/react-headless → @ag-ui/core nested zod 3 (GHSA-mrhx-c7xc-m3w5). War Room GenUI; not adapted into Omega Prime; never loaded by the loopback sidecar.",
    ),
    "GHSA-82fw-gwwq-j7x9::node_modules/@vitest/coverage-v8": (
        "2026-10-14",
        "Moderate GHSA-82fw-gwwq-j7x9 (vitest @vitest/mocker path traversal, CVSS 5.9); dev-only test coverage transitive, never loaded by the loopback sidecar; default-off family, never in the runtime image.",
    ),
    "GHSA-82fw-gwwq-j7x9::node_modules/@vitest/mocker": (
        "2026-10-14",
        "Moderate GHSA-82fw-gwwq-j7x9 (vitest @vitest/mocker path traversal, CVSS 5.9); dev-only test mocking helper, never loaded by the loopback sidecar; default-off family, never in the runtime image.",
    ),
    "GHSA-82fw-gwwq-j7x9::node_modules/vitest": (
        "2026-10-14",
        "Moderate GHSA-82fw-gwwq-j7x9 (vitest @vitest/mocker path traversal, CVSS 5.9); dev-only test runner, never loaded by the loopback sidecar; default-off family, never in the runtime image.",
    ),
}

_HIGH = ("high", "critical")
_GHSA_RE = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}", re.IGNORECASE)
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
UNKNOWN_PATH = "<unknown-path>"


def _ghsa(text: str) -> str:
    """Canonical GHSA id form: ``GHSA-`` prefix, lowercase body."""
    return "GHSA-" + text[5:].lower()


def advisory_id(value: object) -> str | None:
    """Normalize one advisory reference to a stable id, or ``None``.

    Accepts a ``via`` advisory object (``url``/``source``), a legacy
    advisory record (``url``/``cves``/``id``), or a bare string. Prefers
    the GHSA id, then a CVE id, then the URL, then ``npm-source:<id>``.
    """
    if isinstance(value, dict):
        url = value.get("url")
        for text in (url, value.get("title")):
            if isinstance(text, str):
                match = _GHSA_RE.search(text)
                if match:
                    return _ghsa(match.group(0))
        cves = value.get("cves")
        if isinstance(cves, list) and cves:
            return str(cves[0]).upper()
        if isinstance(url, str) and url:
            return url
        for field in ("source", "id"):
            if value.get(field) is not None:
                return f"npm-source:{value.get(field)}"
        return None
    if isinstance(value, str):
        match = _GHSA_RE.search(value)
        if match:
            return _ghsa(match.group(0))
        match = _CVE_RE.search(value)
        if match:
            return match.group(0).upper()
        if "GHSA" in value or "CVE" in value or value.startswith("https://"):
            return value
    return None


def _advisories_for(
    name: str, vulnerabilities: dict, seen: frozenset[str] = frozenset()
) -> set[str]:
    """High+ advisory ids reaching package ``name``, following ``via``.

    A ``via`` advisory object (or GHSA/CVE/URL string) is a direct
    advisory; a bare package-name string is a vulnerable dependency whose
    own advisories are resolved recursively (cycle-safe). Advisory objects
    below the high gate are skipped so a low advisory riding on a high
    row is not mistaken for the reason the row is high.
    """
    vuln = vulnerabilities.get(name)
    if not isinstance(vuln, dict) or name in seen:
        return set()
    seen = seen | {name}
    found: set[str] = set()
    for entry in vuln.get("via") or []:
        if isinstance(entry, dict):
            severity = str(entry.get("severity") or "").lower()
            if severity and severity not in _HIGH:
                continue
            ident = advisory_id(entry)
            if ident:
                found.add(ident)
        elif isinstance(entry, str):
            ident = advisory_id(entry)
            if ident:
                found.add(ident)
            else:
                found |= _advisories_for(entry, vulnerabilities, seen)
    return found


def _paths(values: object) -> list[str]:
    """String paths from a ``nodes``/``paths`` list, else the unknown marker."""
    if isinstance(values, list):
        paths = [str(value) for value in values if isinstance(value, str) and value]
        if paths:
            return paths
    return [UNKNOWN_PATH]


def triage(doc: object) -> tuple[list[tuple[str, str, object]], str | None]:
    """Split audit output into high+ findings and a fatal error message.

    Returns ``(findings, error)``: ``error`` is non-None when the document
    is not usable audit output (fail closed); otherwise ``findings`` holds
    ``(key, severity, name)`` rows for triage, one per (advisory, affected
    path) pair, keyed ``"<advisory>::<path>"`` (see ``TRIAGED``). A row
    whose advisory cannot be resolved keys on ``"package:<name>"``.
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
    vuln_map = vulnerabilities if isinstance(vulnerabilities, dict) else {}
    for name, vuln in vuln_map.items():
        if not isinstance(vuln, dict):
            continue
        severity = str(vuln.get("severity") or "").lower()
        if severity not in _HIGH:
            continue
        idents = sorted(_advisories_for(name, vuln_map)) or [f"package:{name}"]
        for ident in idents:
            for path in _paths(vuln.get("nodes")):
                findings.append((f"{ident}::{path}", severity, name))
    if isinstance(advisories, dict):
        for advisory in advisories.values():
            if not isinstance(advisory, dict):
                continue
            severity = str(advisory.get("severity") or "").lower()
            if severity not in _HIGH:
                continue
            module = advisory.get("module_name")
            ident = advisory_id(advisory) or f"package:{module}"
            paths: list[str] = []
            for record in advisory.get("findings") or []:
                if isinstance(record, dict):
                    paths.extend(
                        path
                        for path in _paths(record.get("paths"))
                        if path != UNKNOWN_PATH
                    )
            for path in sorted(set(paths)) or [UNKNOWN_PATH]:
                findings.append((f"{ident}::{path}", severity, module))
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

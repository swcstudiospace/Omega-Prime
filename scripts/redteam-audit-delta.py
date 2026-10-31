#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Diff two npm audit outputs across a redteam pin bump.

Compares the advisories in an old-pin input against a new-pin input and
emits a Markdown report with four sections — advisories new, fixed,
rescinded, and severity drift — plus a one-line verdict.

Each input may be either ``npm audit --json`` output (the ``vulnerabilities``
map with ``severity`` + ``via``, or the legacy ``advisories`` map) or an
``npm-shrinkwrap``/``package-lock.json`` file carrying already-resolved
advisory metadata. Format is auto-detected per file from the top-level
keys; mixing formats (old lock vs new audit JSON) is allowed. Unknown
shapes and npm ``"error"`` payloads fail closed, mirroring
``scripts/redteam-audit-triage.py``.

A lockfile entry contributes advisory records from its ``advisories``
(list or map) or ``advisory`` (single record) member; each record uses
the same key/severity fields as an audit advisory. The entry's ``range``
(when present, else ``version``) is the version spec used to tell
*rescinded* apart from *fixed*: a vanished advisory whose package spec
is unchanged in the new input was rescinded upstream, while one whose
package is gone or re-ranged was fixed by the bump.

Verdict semantics (printed to stdout, also the report footer):

- ``regressed`` — new advisories non-empty, or any severity drift upward.
- ``improved`` — fixed non-empty and new empty (and no upward drift).
- ``unchanged`` — neither (rescinded-only or downward-drift-only changes
  land here; the script informs, it never gates).

Usage: ``python3 scripts/redteam-audit-delta.py --old <audit-json|lock>
--new <audit-json|lock> --out report.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Rank for drift direction. npm audit severities are info/low/moderate/
# high/critical; "medium" is accepted as an alias for moderate since some
# advisory feeds use it. Unknown severities are unranked and compared
# conservatively (any change involving one counts as upward).
SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "low": 1,
    "moderate": 2,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def _finding_key(record: dict, fallback: object) -> str:
    """Derive the stable finding key for one audit/lock advisory record.

    This is the coarse, unscoped key (the triage script's former key,
    kept decoupled so either file runs standalone via ``python3``); it
    only diffs two audits and never gates on ``TRIAGED``, whose keys are
    scoped ``<advisory>::<path>`` by ``scripts/redteam-audit-triage.py``.
    A ``vulnerabilities``-shaped record scans ``via`` for a GHSA/CVE/URL
    entry falling back to the map name; a legacy ``advisories``-shaped
    (or lockfile-embedded) record uses ``url or cves or module_name or
    id``.
    """
    via = record.get("via")
    if isinstance(via, list):
        return next(
            (
                str(entry)
                for entry in via
                if isinstance(entry, str)
                and ("GHSA" in entry or "CVE" in entry or entry.startswith("https://"))
            ),
            str(fallback),
        )
    return str(
        record.get("url")
        or record.get("cves")
        or record.get("module_name")
        or record.get("id")
        or fallback
    )


def _severity(record: dict) -> str:
    """Normalize a severity exactly like the triage script does."""
    return str(record.get("severity") or "").lower() or "unknown"


def _spec_of_audit_vuln(vuln: dict) -> str:
    """Version spec locating one audit ``vulnerabilities`` entry."""
    for field in ("range", "found_in"):
        value = vuln.get(field)
        if isinstance(value, str) and value:
            return value
    fix = vuln.get("fixAvailable")
    if isinstance(fix, str) and fix:
        return fix
    return ""


def _spec_of_advisory(advisory: dict) -> str:
    """Version spec locating one legacy/lock advisory record."""
    found = advisory.get("found_in")
    if isinstance(found, str) and found:
        return found
    if isinstance(found, list) and found:
        return str(found[0])
    return ""


def _lock_package_name(entry_key: str) -> str:
    """Strip the ``node_modules/`` prefix (and any nesting) to a name."""
    parts = [part for part in entry_key.split("node_modules/") if part]
    name = parts[-1].strip("/") if parts else entry_key
    return name


def _records_from_lock_entry(entry: dict) -> list[dict]:
    """Advisory records embedded in one lockfile package entry."""
    records: list[dict] = []
    single = entry.get("advisory")
    if isinstance(single, dict):
        records.append(single)
    multi = entry.get("advisories")
    if isinstance(multi, dict):
        records.extend(value for value in multi.values() if isinstance(value, dict))
    elif isinstance(multi, list):
        records.extend(item for item in multi if isinstance(item, dict))
    return records


def parse_input(
    doc: object,
) -> tuple[dict[str, tuple[str, str, str]], dict[str, str], str | None]:
    """Parse one input into findings, package specs, or a fatal error.

    Returns ``(findings, packages, error)``: ``error`` is non-None when
    the document is unusable (fail closed); otherwise ``findings`` maps
    finding key -> ``(severity, package, spec)`` and ``packages`` maps
    package name -> version spec for the rescinded-vs-fixed distinction.
    """
    if not isinstance(doc, dict):
        return {}, {}, "input is not a JSON object"
    error = doc.get("error")
    if error is not None:
        return {}, {}, f"npm audit reported an error: {error}"
    vulnerabilities = doc.get("vulnerabilities")
    advisories = doc.get("advisories")
    is_lock = "lockfileVersion" in doc or "packages" in doc
    if isinstance(vulnerabilities, dict):
        findings: dict[str, tuple[str, str, str]] = {}
        packages: dict[str, str] = {}
        for name, vuln in vulnerabilities.items():
            if not isinstance(vuln, dict):
                continue
            key = _finding_key(vuln, name)
            severity = _severity(vuln)
            spec = _spec_of_audit_vuln(vuln)
            findings[key] = (severity, str(name), spec)
            packages[str(name)] = spec
        if isinstance(advisories, dict):
            for advisory in advisories.values():
                if not isinstance(advisory, dict):
                    continue
                package = str(advisory.get("module_name") or "")
                key = _finding_key(advisory, package or advisory.get("id"))
                findings[key] = (
                    _severity(advisory),
                    package,
                    _spec_of_advisory(advisory),
                )
                if package:
                    packages.setdefault(package, _spec_of_advisory(advisory))
        return findings, packages, None
    if isinstance(advisories, dict) and not is_lock:
        findings = {}
        packages = {}
        for advisory in advisories.values():
            if not isinstance(advisory, dict):
                continue
            package = str(advisory.get("module_name") or "")
            key = _finding_key(advisory, package or advisory.get("id"))
            spec = _spec_of_advisory(advisory)
            findings[key] = (_severity(advisory), package, spec)
            if package:
                packages.setdefault(package, spec)
        return findings, packages, None
    if is_lock:
        findings = {}
        packages = {}
        lock_packages = doc.get("packages")
        if isinstance(lock_packages, dict):
            for entry_key, entry in lock_packages.items():
                if not isinstance(entry, dict):
                    continue
                if not entry_key:
                    continue
                name = _lock_package_name(str(entry_key))
                version = entry.get("version")
                spec = (
                    entry.get("range") if isinstance(entry.get("range"), str) else None
                )
                if not spec:
                    spec = version if isinstance(version, str) else ""
                packages[name] = spec
                for record in _records_from_lock_entry(entry):
                    key = _finding_key(record, name)
                    findings[key] = (_severity(record), name, spec)
        dependencies = doc.get("dependencies")
        if isinstance(dependencies, dict):
            for name, entry in dependencies.items():
                if not isinstance(entry, dict):
                    continue
                version = entry.get("version")
                spec = (
                    entry.get("range") if isinstance(entry.get("range"), str) else None
                )
                if not spec:
                    spec = version if isinstance(version, str) else ""
                packages.setdefault(str(name), spec)
                for record in _records_from_lock_entry(entry):
                    key = _finding_key(record, name)
                    findings.setdefault(key, (_severity(record), str(name), spec))
        if isinstance(advisories, dict):
            for advisory in advisories.values():
                if not isinstance(advisory, dict):
                    continue
                package = str(advisory.get("module_name") or "")
                key = _finding_key(advisory, package or advisory.get("id"))
                findings.setdefault(
                    key, (_severity(advisory), package, _spec_of_advisory(advisory))
                )
        return findings, packages, None
    return (
        {},
        {},
        "input has neither 'vulnerabilities' nor 'advisories' keys "
        "and is not a package lockfile; refusing to treat an unknown shape as clean",
    )


def _is_upward(old_severity: str, new_severity: str) -> bool:
    """True when a severity drift needs reviewer attention."""
    if old_severity == new_severity:
        return False
    old_rank = SEVERITY_RANK.get(old_severity)
    new_rank = SEVERITY_RANK.get(new_severity)
    if old_rank is None or new_rank is None:
        return True
    return new_rank > old_rank


def compute_delta(
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
    new_packages: dict[str, str],
) -> tuple[list[str], list[str], list[str], list[tuple[str, str, str]], str]:
    """Split the key diff into (new, fixed, rescinded, drift, verdict)."""
    new = sorted(key for key in new_findings if key not in old_findings)
    fixed: list[str] = []
    rescinded: list[str] = []
    for key in sorted(key for key in old_findings if key not in new_findings):
        _, package, old_spec = old_findings[key]
        if package and old_spec and new_packages.get(package) == old_spec:
            rescinded.append(key)
        else:
            fixed.append(key)
    drift = sorted(
        (key, old_findings[key][0], new_findings[key][0])
        for key in old_findings
        if key in new_findings and old_findings[key][0] != new_findings[key][0]
    )
    if new or any(_is_upward(old_sev, new_sev) for _, old_sev, new_sev in drift):
        verdict = "regressed"
    elif fixed:
        verdict = "improved"
    else:
        verdict = "unchanged"
    return new, fixed, rescinded, drift, verdict


def render_report(
    old_label: str,
    new_label: str,
    old_findings: dict[str, tuple[str, str, str]],
    new_findings: dict[str, tuple[str, str, str]],
    new_keys: list[str],
    fixed: list[str],
    rescinded: list[str],
    drift: list[tuple[str, str, str]],
    verdict: str,
) -> str:
    """Render the Markdown delta report including the verdict footer."""
    lines = [
        "# redteam audit delta",
        "",
        f"Old: `{old_label}`",
        f"New: `{new_label}`",
        "",
        "## Advisories new",
        "",
    ]
    if new_keys:
        for key in new_keys:
            severity, package, _ = new_findings[key]
            lines.append(f"- `{key}` — severity {severity} (package `{package}`)")
    else:
        lines.append("_None._")
    lines += ["", "## Fixed", ""]
    if fixed:
        for key in fixed:
            severity, package, _ = old_findings[key]
            lines.append(f"- `{key}` — severity {severity} (package `{package}`)")
    else:
        lines.append("_None._")
    lines += ["", "## Rescinded", ""]
    if rescinded:
        for key in rescinded:
            severity, package, _ = old_findings[key]
            lines.append(f"- `{key}` — severity {severity} (package `{package}`)")
    else:
        lines.append("_None._")
    lines += ["", "## Severity drift", ""]
    if drift:
        for key, old_severity, new_severity in drift:
            package = new_findings[key][1]
            lines.append(
                f"- `{key}` — {old_severity} -> {new_severity} (package `{package}`)"
            )
    else:
        lines.append("_None._")
    lines += ["", f"Verdict: {verdict}", ""]
    return "\n".join(lines)


def _load_input(path: str) -> tuple[object, str | None]:
    """Read one input file; returns (doc, error)."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), None
    except OSError as exc:
        return None, f"cannot read input ({exc})"
    except ValueError as exc:
        return None, f"cannot parse input as JSON ({exc})"


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 once a verdict is computed, else 1 or 2."""
    parser = argparse.ArgumentParser(
        description="Diff two npm audit inputs across a redteam pin bump."
    )
    parser.add_argument(
        "--old", required=True, help="old-pin npm audit JSON or lockfile"
    )
    parser.add_argument(
        "--new", required=True, help="new-pin npm audit JSON or lockfile"
    )
    parser.add_argument("--out", required=True, help="Markdown report destination")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if str(out.parent) not in ("", ".") and not out.parent.is_dir():
        print(
            f"redteam-audit-delta FAILED: --out parent directory does not exist: {out.parent}",
            file=sys.stderr,
        )
        return 2

    old_doc, error = _load_input(args.old)
    if error is not None:
        print(f"redteam-audit-delta FAILED: {args.old}: {error}", file=sys.stderr)
        return 1
    new_doc, error = _load_input(args.new)
    if error is not None:
        print(f"redteam-audit-delta FAILED: {args.new}: {error}", file=sys.stderr)
        return 1

    old_findings, _, error = parse_input(old_doc)
    if error is not None:
        print(f"redteam-audit-delta FAILED: {args.old}: {error}", file=sys.stderr)
        return 1
    new_findings, new_packages, error = parse_input(new_doc)
    if error is not None:
        print(f"redteam-audit-delta FAILED: {args.new}: {error}", file=sys.stderr)
        return 1

    new_keys, fixed, rescinded, drift, verdict = compute_delta(
        old_findings, new_findings, new_packages
    )
    report = render_report(
        args.old,
        args.new,
        old_findings,
        new_findings,
        new_keys,
        fixed,
        rescinded,
        drift,
        verdict,
    )
    try:
        out.write_text(report, encoding="utf-8")
    except OSError as exc:
        print(
            f"redteam-audit-delta FAILED: {args.out}: cannot write report ({exc})",
            file=sys.stderr,
        )
        return 1
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

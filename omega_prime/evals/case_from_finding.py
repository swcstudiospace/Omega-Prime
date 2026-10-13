"""Provision a draft regression case from a confirmed finding (PLAN-F4).

Operator workflow: provision → review → move → activate → bump.

1. Provision: run
       python -m omega_prime.evals.case_from_finding --dedupe-hash <hash> --class <class>
   The tool looks up the row in ``findings.ndjson``, writes one YAML file
   under ``sidecar/cases/drafts/`` with ``status: draft``, and prints its
   path. Re-running the same hash is idempotent (prints the existing path).
2. Review: inspect the draft. The ``payload`` is a verbatim copy of the
   finding row's ``observed_detection`` — nothing is invented (findings rows
   deliberately do not persist the raw gateway response, so there is nothing
   else to copy). ``expected.detection`` is the placeholder
   ``REVIEW-REQUIRED``, valid only on drafts.
3. Move: a human copies the file to ``sidecar/cases/``.
4. Activate: flip ``status`` to ``active`` and set ``expected.detection`` to
   the real detection name.
5. Bump: set ``version`` to ``1.0.0``.

Drafts are inert data excluded from suite loading (``load_suite`` globs
``*.yaml`` non-recursively so ``drafts/`` is never entered, and skips any
parsed doc with ``status: draft``). Nothing auto-activates, ever.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sidecar.findings import findings_path, load_findings
from sidecar.simulation import (
    CASE_CLASSES,
    DRAFTS_DIRNAME,
    REVIEW_PLACEHOLDER,
    CaseError,
    cases_dir,
    load_case_file,
)

CONFIRMED_VERDICTS = ("missed-detection", "no-observation")

DRAFT_VERSION = "0.1.0"

_SOURCE_HASH_RE = re.compile(r"dedupe_hash:\s*([0-9a-fA-F]{64})")


def _yaml_quote(value: str) -> str:
    """Render ``value`` as a double-quoted single-line YAML scalar.

    The output parses back byte-identically through
    :func:`sidecar.simulation._decode_scalar` for any input without a
    carriage return; ``\\r\\n``/``\\r`` are normalized to ``\\n`` because the
    minimal-YAML subset has no escape for them.
    """
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{text}"'


def _render_draft(
    case_id: str,
    klass: str,
    severity: str,
    observed_detection: str,
    dedupe_hash: str,
    source_case_id: str,
) -> str:
    """Render the draft file text (header comment + DATA-ONLY body)."""
    lines = [
        "# Purple-team sidecar simulation case DRAFT (inert data — never executed).",
        "#",
        f"# Provisioned from finding dedupe_hash: {dedupe_hash} (source case: {source_case_id}).",
        "# Inert probe below: verbatim copy of the finding row's observed_detection,",
        "# stored and compared as data, never eval/exec'd, never leaves loopback.",
        "# To activate: move to sidecar/cases/, set status: active, set",
        "# expected.detection to the real detection name, bump version to 1.0.0.",
        f"case_id: {_yaml_quote(case_id)}",
        f"version: {_yaml_quote(DRAFT_VERSION)}",
        f"class: {_yaml_quote(klass)}",
        f"severity: {_yaml_quote(severity)}",
        'transport: "loopback-only"',
        'scope: "llm-own-endpoints"',
        'status: "draft"',
        f"payload: {_yaml_quote(observed_detection)}",
        "expected:",
        f"  detection: {_yaml_quote(REVIEW_PLACEHOLDER)}",
        "",
    ]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="case_from_finding",
        description=(
            "Provision a draft regression case from a confirmed finding. "
            "Looks up --dedupe-hash in findings.ndjson, writes "
            "sidecar/cases/drafts/<class>-<12hex>.yaml with status: draft, "
            "and prints its path. The draft is inert until a human moves it "
            "to sidecar/cases/, sets status: active, sets a real "
            "expected.detection, and bumps version to 1.0.0."
        ),
    )
    parser.add_argument(
        "--dedupe-hash",
        required=True,
        help="64-char hex row identity produced by sidecar/findings.py::dedupe_hash.",
    )
    parser.add_argument(
        "--class",
        dest="klass",
        required=True,
        help=(
            "Technique class for the new case; one of "
            f"{list(CASE_CLASSES)}. The CLI value wins over the row's "
            "recorded class (a mismatch warns, exit still 0)."
        ),
    )
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Override the findings directory (default: sidecar/state.py::reports_dir()).",
    )
    parser.add_argument(
        "--cases-dir",
        default=None,
        help="Override the cases directory holding drafts/ (default: sidecar/simulation.py::cases_dir()).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Provision one draft case from a confirmed finding. See module docstring."""
    args = _parse_args(argv)
    digest = args.dedupe_hash
    klass = args.klass

    if klass not in CASE_CLASSES:
        print(
            f"case_from_finding: unknown class {klass!r} "
            f"(must be one of {list(CASE_CLASSES)})",
            file=sys.stderr,
        )
        return 1

    reports = args.reports_dir
    try:
        rows = load_findings(reports)
    except ValueError as exc:
        print(f"case_from_finding: {exc}", file=sys.stderr)
        return 1

    row = next((r for r in rows if r.get("dedupe_hash") == digest), None)
    if row is None:
        print(
            f"case_from_finding: unknown dedupe_hash {digest!r}: "
            f"no row in {findings_path(reports)}",
            file=sys.stderr,
        )
        return 1

    verdict = row.get("verdict")
    if verdict not in CONFIRMED_VERDICTS:
        print(
            f"case_from_finding: finding {digest!r} has verdict {verdict!r}: "
            "already detected; nothing to provision",
            file=sys.stderr,
        )
        return 1

    row_class = row.get("class")
    if row_class != klass:
        print(
            f"case_from_finding: warning: row class {row_class!r} differs "
            f"from --class {klass!r}; using --class value",
            file=sys.stderr,
        )

    severity = row.get("severity")
    if not isinstance(severity, str) or not severity.strip():
        print(
            f"case_from_finding: finding {digest!r} has no usable "
            f"severity {severity!r}; refusing to provision",
            file=sys.stderr,
        )
        return 1
    observed = row.get("observed_detection")
    if not isinstance(observed, str) or not observed:
        print(
            f"case_from_finding: finding {digest!r} has no recorded "
            "observed_detection; refusing to provision",
            file=sys.stderr,
        )
        return 1

    case_id = f"{klass}-{digest[:12]}"
    base = Path(args.cases_dir) if args.cases_dir is not None else cases_dir()
    drafts_dir = base / DRAFTS_DIRNAME
    draft_path = drafts_dir / f"{case_id}.yaml"

    if draft_path.exists():
        try:
            existing_text = draft_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"case_from_finding: cannot read {draft_path} ({exc})", file=sys.stderr
            )
            return 1
        match = _SOURCE_HASH_RE.search(existing_text)
        if match and match.group(1).lower() == digest.lower():
            print(str(draft_path))
            return 0
        existing_hash = match.group(1) if match else "<unknown>"
        print(
            f"case_from_finding: case_id collision: {draft_path} was provisioned "
            f"from finding {existing_hash}, not {digest} "
            f"(both map to case_id {case_id!r}); refusing to overwrite",
            file=sys.stderr,
        )
        return 1

    for sibling in sorted(base.glob("*.yaml")):
        try:
            doc = load_case_file(sibling)
        except (CaseError, OSError):
            continue
        if doc.get("case_id") == case_id:
            print(
                f"case_from_finding: case_id collision: case_id {case_id!r} "
                f"for finding {digest} already exists in {sibling}; "
                f"refusing to write {draft_path}",
                file=sys.stderr,
            )
            return 1

    source_case_id = row.get("case_id")
    text = _render_draft(
        case_id=case_id,
        klass=klass,
        severity=severity,
        observed_detection=observed,
        dedupe_hash=digest,
        source_case_id=str(source_case_id) if source_case_id else "unknown",
    )
    try:
        drafts_dir.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"case_from_finding: cannot write {draft_path} ({exc})", file=sys.stderr)
        return 1
    print(str(draft_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

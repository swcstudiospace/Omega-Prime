"""Purple-team sidecar simulation runner with detection validation (GAP-10/11).

Cases are versioned DATA under ``sidecar/cases/*.yaml`` validated against
``sidecar/case.schema.json``. The YAML subset is deliberately minimal
(top-level ``key: value`` scalars plus one nested ``expected:`` mapping;
no lists, anchors, tags, or block scalars) so this stdlib-only module can
parse it without a YAML dependency — and so hostile constructs fail closed
instead of executing (threat T-79-07).

Every case runs against the injected in-process gateway seam
(:class:`FakeGateway` by default). No sockets are opened, no network calls
are made, and payload strings are never eval/exec'd — they are passed to
the seam as inert data. Runs drive the 79-01 state machine
(IDLE → RUNNING → IDLE, or → DEGRADED on version drift).

Pass criteria (GAP-11, threat T-79-08): a case PASSES only when the
recorded observed detection for its ``case_id`` exactly equals its
``expected.detection``. The suite passes only at 100% detection or with a
per-case ``residual_rationale`` excusing exactly its own ``case_id``.
Any mismatch, missing observation, schema error, or version drift exits
nonzero and names the case (drift names the disagreeing pair) — never a
silent pass.

Stdlib-only. Never imports ``omega_prime.redteam`` or ``redteam/``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sidecar import findings as _findings  # noqa: E402
from sidecar.state import reports_dir, transition  # noqa: E402

CASE_CLASSES = (
    "prompt-injection",
    "jailbreak",
    "abuse",
    "malformed-input",
    "auth-bypass",
    "cost-abuse",
)

SEVERITIES = ("low", "medium", "high", "critical")

TRANSPORT_REQUIRED = "loopback-only"
SCOPE_REQUIRED = "llm-own-endpoints"

REQUIRED_TOP_KEYS = (
    "case_id",
    "version",
    "class",
    "severity",
    "transport",
    "scope",
    "payload",
    "expected",
)

EXPECTED_KEYS = ("detection", "residual_rationale")

# Optional lifecycle key for operator-provisioned drafts (PLAN-F4).
# Files without "status" validate exactly as before this change.
OPTIONAL_TOP_KEYS = ("status",)
STATUS_VALUES = ("draft", "active")
# Placeholder detection carried by drafts until a human sets the real one.
# Valid only on status: draft documents.
REVIEW_PLACEHOLDER = "REVIEW-REQUIRED"
# Drafts live here; load_suite never enters this subdir (non-recursive glob).
DRAFTS_DIRNAME = "drafts"

_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

CASES_DIRNAME = "cases"
LAST_RUN_FILENAME = "last-run.json"
KNOWN_SUITES = ("smoke",)


class CaseError(ValueError):
    """Raised when a case file is malformed or hostile. Carries the source."""


def _decode_scalar(raw: str, source: str, lineno: int) -> str:
    """Decode one YAML scalar (double/single-quoted or bare)."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        body = text[1:-1]
        out: list[str] = []
        i = 0
        while i < len(body):
            char = body[i]
            if char == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                mapping = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}
                if nxt not in mapping:
                    raise CaseError(f"{source}:{lineno}: unsupported escape '\\{nxt}'")
                out.append(mapping[nxt])
                i += 2
            elif char == '"':
                raise CaseError(f"{source}:{lineno}: unterminated quote")
            else:
                out.append(char)
                i += 1
        return "".join(out)
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if not text:
        raise CaseError(f"{source}:{lineno}: empty value")
    if text[0] in "!&*|>@`{[#":
        raise CaseError(f"{source}:{lineno}: unsupported YAML construct {text[0]!r}")
    return text


def parse_case_text(text: str, source: str = "<case>") -> dict[str, object]:
    """Parse one case file's minimal-YAML text into a plain dict.

    Accepts blank lines, ``#`` comments, top-level ``key: value`` pairs,
    and a single nested mapping under ``expected:`` indented by exactly
    two spaces. Anything else — tabs, list items, flow collections,
    anchors/aliases, tags, block scalars, deeper nesting — raises
    :class:`CaseError`. Never executes anything.
    """
    document: dict[str, object] = {}
    expected: dict[str, str] = {}
    in_expected = False
    seen_nested_key = False
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            raise CaseError(f"{source}:{lineno}: tab indentation forbidden")
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            raise CaseError(f"{source}:{lineno}: list items forbidden")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent not in (0, 2):
            raise CaseError(f"{source}:{lineno}: indent must be 0 or 2 spaces")
        if indent == 2:
            if not in_expected:
                raise CaseError(f"{source}:{lineno}: nested entry outside 'expected:'")
            if ":" not in stripped:
                raise CaseError(f"{source}:{lineno}: malformed entry")
            key, _, value = stripped.partition(":")
            key = key.strip()
            if not key or not value.strip():
                raise CaseError(f"{source}:{lineno}: malformed entry")
            if key in expected:
                raise CaseError(f"{source}:{lineno}: duplicate key {key!r}")
            expected[key] = _decode_scalar(value, source, lineno)
            seen_nested_key = True
            continue
        # Top-level line: dedent closes the expected block.
        in_expected = False
        if ":" not in stripped:
            raise CaseError(f"{source}:{lineno}: malformed entry")
        key, _, value = stripped.partition(":")
        key = key.strip()
        if not key:
            raise CaseError(f"{source}:{lineno}: malformed entry")
        if key in document:
            raise CaseError(f"{source}:{lineno}: duplicate key {key!r}")
        if not value.strip():
            if key != "expected":
                raise CaseError(
                    f"{source}:{lineno}: nested mapping only allowed under 'expected:'"
                )
            document[key] = expected
            in_expected = True
            continue
        document[key] = _decode_scalar(value, source, lineno)
    if "expected" in document and not seen_nested_key:
        raise CaseError(f"{source}: 'expected:' mapping must not be empty")
    return document


def load_case_file(path: str | Path) -> dict[str, object]:
    """Read and parse one case file. Raises :class:`CaseError` on problems."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CaseError(f"{path}: cannot read case file ({exc})") from exc
    return parse_case_text(text, source=str(path))


def validate_case(doc: object, source: str = "<case>") -> list[str]:
    """Structural check of a parsed case against the case schema.

    Returns a list of error strings (empty means valid). Every error
    names the ``source`` so failures identify the offending case file.
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return [f"{source}: case must be a mapping"]
    case_id = doc.get("case_id")
    label = f"case {case_id!r} in {source}" if case_id else source
    for key in REQUIRED_TOP_KEYS:
        if key not in doc:
            errors.append(f"{label}: missing required key {key!r}")
    extra = sorted(set(doc) - set(REQUIRED_TOP_KEYS) - set(OPTIONAL_TOP_KEYS))
    if extra:
        errors.append(f"{label}: unexpected keys {extra}")
    if "status" in doc and doc["status"] not in STATUS_VALUES:
        errors.append(
            f"{label}: 'status' must be one of {list(STATUS_VALUES)} "
            f"(got {doc.get('status')!r})"
        )
    if "case_id" in doc and (
        not isinstance(doc["case_id"], str) or not doc["case_id"].strip()
    ):
        errors.append(f"{label}: 'case_id' must be a non-empty string")
    if "version" in doc and (
        not isinstance(doc["version"], str) or not _SEMVER_RE.match(doc["version"])
    ):
        errors.append(
            f"{label}: 'version' must be a semver string (got {doc.get('version')!r})"
        )
    if "class" in doc and doc["class"] not in CASE_CLASSES:
        errors.append(
            f"{label}: 'class' must be one of {list(CASE_CLASSES)} "
            f"(got {doc.get('class')!r})"
        )
    if "severity" in doc and doc["severity"] not in SEVERITIES:
        errors.append(
            f"{label}: 'severity' must be one of {list(SEVERITIES)} "
            f"(got {doc.get('severity')!r})"
        )
    if "transport" in doc and doc["transport"] != TRANSPORT_REQUIRED:
        errors.append(
            f"{label}: 'transport' must be {TRANSPORT_REQUIRED!r} "
            f"(got {doc.get('transport')!r})"
        )
    if "scope" in doc and doc["scope"] != SCOPE_REQUIRED:
        errors.append(
            f"{label}: 'scope' must be {SCOPE_REQUIRED!r} (got {doc.get('scope')!r})"
        )
    if "payload" in doc and (
        not isinstance(doc["payload"], str) or not doc["payload"].strip()
    ):
        errors.append(f"{label}: 'payload' must be a non-empty inert string")
    if "expected" in doc:
        expected = doc["expected"]
        if not isinstance(expected, dict):
            errors.append(f"{label}: 'expected' must be a mapping")
        else:
            if "detection" not in expected:
                errors.append(f"{label}: 'expected' missing 'detection'")
            elif not isinstance(expected["detection"], str) or not (
                expected["detection"].strip()
            ):
                errors.append(
                    f"{label}: 'expected.detection' must be a non-empty string"
                )
            elif (
                expected["detection"] == REVIEW_PLACEHOLDER
                and doc.get("status") != "draft"
            ):
                errors.append(
                    f"{label}: 'expected.detection' {REVIEW_PLACEHOLDER!r} "
                    "requires status 'draft' (set a real detection name to activate)"
                )
            extra_expected = sorted(set(expected) - set(EXPECTED_KEYS))
            if extra_expected:
                errors.append(f"{label}: unexpected 'expected' keys {extra_expected}")
            if "residual_rationale" in expected and (
                not isinstance(expected["residual_rationale"], str)
                or not expected["residual_rationale"].strip()
            ):
                errors.append(
                    f"{label}: 'expected.residual_rationale' must be a "
                    "non-empty string when present"
                )
    return errors


def cases_dir(repo_root: str | Path | None = None) -> Path:
    """Directory holding the versioned case files."""
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    return root / "sidecar" / CASES_DIRNAME


def load_suite(
    directory: str | Path | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """Load and validate every ``*.yaml`` case in ``directory``.

    Returns ``(cases, errors)`` with cases sorted by ``case_id``.
    Duplicate ``case_id`` values are reported as errors naming both files.

    Draft exclusion (two layers, PLAN-F4): (1) the ``*.yaml`` glob is
    non-recursive, so ``drafts/`` is never entered; (2) any parsed doc
    with ``status: draft`` is skipped here — not an error — as defense
    in depth against a stray draft copy landing directly in ``cases/``.
    Nothing provisioned as a draft can execute in ``run_suite``.
    """
    directory = Path(directory) if directory is not None else cases_dir()
    files = sorted(directory.glob("*.yaml"))
    if not files:
        return [], [f"{directory}: no case files (*.yaml) found"]
    cases: list[dict[str, object]] = []
    errors: list[str] = []
    seen: dict[str, str] = {}
    for path in files:
        try:
            doc = load_case_file(path)
        except CaseError as exc:
            errors.append(str(exc))
            continue
        if doc.get("status") == "draft":
            continue
        errors.extend(validate_case(doc, source=str(path)))
        case_id = doc.get("case_id")
        if isinstance(case_id, str) and case_id:
            if case_id in seen:
                errors.append(
                    f"{path}: duplicate case_id {case_id!r} (also in {seen[case_id]})"
                )
            else:
                seen[case_id] = str(path)
        cases.append(doc)
    cases.sort(key=lambda doc: str(doc.get("case_id", "")))
    return cases, errors


class FakeGateway:
    """In-process loopback gateway seam. Opens no sockets.

    By default it "detects" exactly what the case declares, so the
    shipped fixtures exercise the run/record/validate pipeline end to
    end. Tests inject ``overrides`` mapping ``case_id`` to a different
    observed event to prove mismatches fail by name.
    """

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self.overrides = dict(overrides or {})

    def detect(self, case: dict[str, object]) -> str:
        """Observed detection event for ``case`` (untrusted input downstream)."""
        case_id = str(case.get("case_id", ""))
        if case_id in self.overrides:
            return self.overrides[case_id]
        expected = case.get("expected")
        if isinstance(expected, dict) and isinstance(expected.get("detection"), str):
            return str(expected["detection"])
        return "no-detection"

    def respond(self, case: dict[str, object]) -> str:
        """Deterministic normalized gateway response text for ``case``."""
        return f"loopback-seam:{case.get('case_id')}:{self.detect(case)}"


def last_run_path(directory: str | Path | None = None) -> Path:
    """Full path of the recorded-observations file."""
    base = Path(directory) if directory is not None else reports_dir()
    return base / LAST_RUN_FILENAME


def run_suite(
    cases: list[dict[str, object]],
    gateway: FakeGateway | None = None,
    reports: str | Path | None = None,
) -> dict[str, object]:
    """Execute ``cases`` against the seam, record observations, log states.

    Drives IDLE → RUNNING → IDLE via :mod:`sidecar.state` and writes
    ``last-run.json``. Returns a result dict with per-class counts.
    """
    gateway = gateway or FakeGateway()
    reports_path = last_run_path(reports)
    transition("IDLE", "RUNNING", "run-started")
    observations: dict[str, dict[str, str]] = {}
    counts: dict[str, int] = {}
    try:
        for case in cases:
            case_id = str(case.get("case_id"))
            klass = str(case.get("class"))
            observations[case_id] = {
                "observed_detection": gateway.detect(case),
                "response": gateway.respond(case),
            }
            counts[klass] = counts.get(klass, 0) + 1
    except Exception:
        transition("RUNNING", "ERROR", "protocol-fault")
        raise
    reports_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": "smoke",
        "observations": observations,
    }
    reports_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    transition("RUNNING", "IDLE", "run-completed")
    return {"cases": len(cases), "counts": counts}


def validate_suite(
    directory: str | Path | None = None,
    reports: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> tuple[bool, list[str]]:
    """Validate recorded observations against current case expectations.

    Returns ``(ok, failures)``; every failure names its ``case_id`` (or
    the disagreeing version pair on drift). Drift implies DEGRADED via
    the state machine — never a silent pass.
    """
    directory = Path(directory) if directory is not None else cases_dir()
    failures: list[str] = []
    cases, load_errors = load_suite(directory)
    failures.extend(load_errors)

    drift_ok, drift_detail = _findings.check_drift(repo_root=repo_root)
    if not drift_ok:
        with contextlib.suppress(ValueError):
            transition("IDLE", "DEGRADED", "drift-detected")
        failures.append(f"version drift implies DEGRADED: {drift_detail}")
        return False, failures

    try:
        raw = last_run_path(reports).read_text(encoding="utf-8")
        record = json.loads(raw)
    except (OSError, ValueError) as exc:
        failures.append(f"no recorded run observations ({exc}); run first")
        return False, failures
    observations = record.get("observations")
    if not isinstance(observations, dict):
        failures.append("recorded run has no 'observations' mapping")
        return False, failures

    known_ids = {
        str(case.get("case_id"))
        for case in cases
        if isinstance(case.get("case_id"), str)
    }
    # Case-linked failures double as findings input: each one names a case
    # whose observed detection did not meet expectation.
    finding_records: list[tuple[dict[str, object], str, str]] = []
    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            continue  # already reported by load errors
        observed = observations.get(case_id)
        if not isinstance(observed, dict) or "observed_detection" not in (observed):
            failures.append(
                f"case {case_id!r}: FAIL — no recorded observation; re-run the suite"
            )
            finding_records.append((case, "no-observation", ""))
            continue
        expected = case.get("expected")
        want = str(expected.get("detection")) if isinstance(expected, dict) else ""
        got = observed["observed_detection"]
        if got == want:
            continue
        rationale = (
            str(expected.get("residual_rationale", "")).strip()
            if isinstance(expected, dict)
            else ""
        )
        if rationale:
            continue  # accepted residual excuses exactly this case_id
        failures.append(
            f"case {case_id!r}: FAIL — observed detection {got!r} != expected {want!r}"
        )
        response = observed.get("response")
        finding_records.append(
            (case, str(got), str(response) if isinstance(response, str) else "")
        )
    for observed_id in sorted(observations):
        if observed_id not in known_ids:
            failures.append(
                f"case {observed_id!r}: FAIL — recorded observation "
                "for unknown case_id; re-run the suite"
            )
    for case, observed_detection, response in finding_records:
        try:
            _record_suite_finding(case, observed_detection, response, reports)
        except (OSError, ValueError) as exc:
            failures.append(
                f"case {case.get('case_id')!r}: FAIL — could not record finding ({exc})"
            )
    return (len(failures) == 0), failures


def _record_suite_finding(
    case: dict[str, object],
    observed_detection: str,
    response: str,
    reports: str | Path | None,
) -> None:
    """Record one validation failure as a finding (production caller path).

    Every case-linked ``validate_suite`` failure becomes a row in
    ``findings.ndjson`` via :func:`sidecar.findings.record_finding`, so
    :func:`sidecar.findings.export_work_items` emits it as a tracked work
    item. Replays dedupe by content (``occurrence_count`` grows instead of
    new rows). Only failures tied to a known case record — load errors,
    drift, and unknown observation ids have no case metadata to file.
    """
    case_id = str(case.get("case_id") or "unknown")
    klass = case.get("class")
    severity = case.get("severity")
    verdict = (
        "no-observation"
        if observed_detection == "no-observation"
        else "missed-detection"
    )
    _findings.record_finding(
        case_id=case_id,
        klass=str(klass) if isinstance(klass, str) and klass else "unknown",
        severity=str(severity) if isinstance(severity, str) and severity else "unknown",
        observed_detection=observed_detection,
        verdict=verdict,
        response=response,
        directory=reports,
    )


def _per_class_lines(cases: list[dict[str, object]]) -> list[str]:
    counts: dict[str, int] = {}
    for case in cases:
        klass = str(case.get("class", "?"))
        counts[klass] = counts.get(klass, 0) + 1
    return [f"  {klass}: {counts[klass]}" for klass in sorted(counts)]


def main(argv: list[str] | None = None) -> int:
    """CLI: ``simulation.py run --suite smoke`` / ``validate --suite smoke``."""
    parser = argparse.ArgumentParser(
        description="Purple-team sidecar simulation (loopback seam only)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "validate"):
        child = sub.add_parser(name)
        child.add_argument("--suite", default="smoke")
        child.add_argument("--cases-dir", default=None)
        child.add_argument("--reports-dir", default=None)
    args = parser.parse_args(argv)
    if args.suite not in KNOWN_SUITES:
        print(f"unknown suite {args.suite!r} (known: {list(KNOWN_SUITES)})")
        return 2
    directory = args.cases_dir or cases_dir()
    if args.command == "run":
        cases, errors = load_suite(directory)
        if errors:
            for error in errors:
                print(f"LOAD FAIL: {error}")
            return 1
        drift_ok, drift_detail = _findings.check_drift()
        if not drift_ok:
            with contextlib.suppress(ValueError):
                transition("IDLE", "DEGRADED", "drift-detected")
            print(f"DEGRADED: version drift: {drift_detail}")
            return 1
        result = run_suite(cases, reports=args.reports_dir)
        print(f"suite {args.suite}: {result['cases']} cases")
        for line in _per_class_lines(cases):
            print(line)
        return 0
    ok, failures = validate_suite(directory, reports=args.reports_dir)
    if ok:
        cases, _ = load_suite(directory)
        print(f"suite {args.suite}: all {len(cases)} cases detected as expected")
        for line in _per_class_lines(cases):
            print(line)
        return 0
    for failure in failures:
        print(f"VALIDATE FAIL: {failure}")
    return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CASE_CLASSES",
    "DRAFTS_DIRNAME",
    "OPTIONAL_TOP_KEYS",
    "REQUIRED_TOP_KEYS",
    "REVIEW_PLACEHOLDER",
    "SEVERITIES",
    "STATUS_VALUES",
    "CaseError",
    "FakeGateway",
    "cases_dir",
    "last_run_path",
    "load_case_file",
    "load_suite",
    "main",
    "parse_case_text",
    "run_suite",
    "validate_case",
    "validate_suite",
]

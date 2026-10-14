#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Weekly purple-team engagement report (deterministic markdown).

Reads ``sidecar/reports/`` history (run observations, findings rows, and
the state-transition log) and renders a single reviewable report with a
fixed section order. Byte-identical reruns on identical inputs: rows are
sorted, counts are plain integers, pass rates render as ``p/t (pct%)``
with one decimal, and the only timestamp that varies with the inputs is
the header's ``generated-at`` — which is derived from the newest input
record, never from the wall clock.

Inputs are read-only; the only writes are the rendered report itself
(``--out`` or stdout, plus the canonical per-window copy in the output
directory that later runs use as the findings-delta baseline).

Weekly cadence uses the existing heartbeat pattern (no new job kind)::

    schedule_heartbeat(store, session, prompt, due_at, interval_seconds=604800)

with ``HEARTBEAT_KIND = "session_heartbeat"`` and the exact prompt:

    "Run scripts/engagement-report.py for the past 7 days and attach
    sidecar/reports/reports/<newest report>"

fired through ``tick_heartbeats(store, now, session_runner)``. Ad-hoc
windows use ``--since``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

#: Env override for the history source (mirrors sidecar/state.py).
SIDECAR_REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
#: Env override for the report output dir (mirrors redteam.reports_dir).
REDTEAM_REPORTS_ENV_VAR = "OMEGA_PRIME_REDTEAM_REPORTS_DIR"

TRANSITIONS_FILENAME = "transitions.ndjson"
LAST_RUN_FILENAME = "last-run.json"
FINDINGS_FILENAME = "findings.ndjson"
REPORTS_SUBDIRNAME = "reports"

#: Fallback window endpoint when no input record carries a timestamp.
EPOCH_RFC3339 = "1970-01-01T00:00:00+00:00"

#: Marker embedding the current findings snapshot (dedupe_hash ->
#: case_id) so the next window can diff new/rescinded findings.
SNAPSHOT_RE = re.compile(r"<!-- findings-snapshot: (.*) -->")


class ReportError(Exception):
    """Fatal input/output problem: exit 1 with ``error:`` on stderr."""


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 timestamp; naive values are assumed UTC."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ReportError(f"cannot parse {value!r} as RFC3339 ({exc})") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def fmt_rfc3339(moment: datetime) -> str:
    """Render ``moment`` as RFC3339 with a ``+00:00`` offset."""
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def default_reports_dir() -> Path:
    """History source: env override wins, else ``sidecar/reports/``."""
    override = os.environ.get(SIDECAR_REPORTS_ENV_VAR, "").strip()
    if override:
        return Path(override)
    return REPO_ROOT / "sidecar" / REPORTS_SUBDIRNAME


def output_dir(reports_dir: Path) -> Path:
    """Report output dir: ``redteam.reports_dir`` env wins, else ``reports/``."""
    override = os.environ.get(REDTEAM_REPORTS_ENV_VAR, "").strip()
    if override:
        return Path(override)
    return reports_dir / "reports"


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def parse_case_text(text: str, source: str) -> tuple[str | None, str, str, str]:
    """Extract ``(case_id, expected detection, residual_rationale, class)``.

    Minimal line reader for the fixed case schema (top-level scalars
    plus one nested ``expected:`` mapping). A missing/empty top-level
    ``class:`` yields ``""``. Raises :class:`ValueError` when the text
    is not a mapping at all.
    """
    case_id: str | None = None
    detection = ""
    rationale = ""
    case_class = ""
    in_expected = False
    seen_mapping = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indented = line[:1] in (" ", "\t")
        if not indented and ":" in line:
            seen_mapping = True
            key, _, value = line.partition(":")
            key = key.strip()
            if key == "expected" and not value.strip():
                in_expected = True
            else:
                in_expected = False
                if key == "case_id":
                    case_id = _strip_quotes(value)
                elif key == "class":
                    case_class = _strip_quotes(value)
            continue
        if in_expected and indented and ":" in stripped:
            sub, _, value = stripped.partition(":")
            sub = sub.strip()
            if sub == "detection":
                detection = _strip_quotes(value)
            elif sub == "residual_rationale":
                rationale = _strip_quotes(value)
    if not seen_mapping:
        raise ValueError(f"{source}: not a YAML mapping")
    return case_id, detection, rationale, case_class


def read_cases(
    cases_dir: Path,
) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """Load ``(case_id, detection, rationale, class)`` rows sorted by case id."""
    try:
        files = sorted(cases_dir.glob("*.yaml"))
    except OSError as exc:
        return [], [f"{cases_dir}: cannot list case files ({exc})"]
    if not files:
        return [], [f"{cases_dir}: no case files (*.yaml) found"]
    cases: list[tuple[str, str, str, str]] = []
    errors: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}: cannot read ({exc})")
            continue
        try:
            case_id, detection, rationale, case_class = parse_case_text(text, str(path))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not case_id:
            errors.append(f"{path}: missing case_id")
            continue
        cases.append((case_id, detection, rationale, case_class))
    cases.sort(key=lambda row: row[0])
    return cases, errors


def read_version(repo_root: Path) -> str:
    """Harness version from ``sidecar/VERSION`` (``unknown`` when missing)."""
    try:
        text = (repo_root / "sidecar" / "VERSION").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    return text.strip() or "unknown"


def check_drift(repo_root: Path) -> tuple[bool, str]:
    """Read-only drift check mirroring ``sidecar.findings.check_drift``.

    Compares ``sidecar/VERSION`` plus the adapter tool surface against
    the contract baseline. Verbatim detail strings keep the detection
    gaps section equal to ``validate_suite()`` failures.
    """
    try:
        version = read_version(repo_root)
        if version == "unknown":
            raise ValueError("cannot read sidecar/VERSION")
        contract_path = repo_root / "sidecar" / "contract" / "bot-interface.json"
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read {contract_path} ({exc})") from exc
        except ValueError as exc:
            raise ValueError(f"{contract_path} is not JSON ({exc})") from exc
        if not isinstance(contract, dict):
            raise ValueError(f"{contract_path} must hold a JSON object")
        adapter_path = repo_root / "omega_prime" / "tools" / "redteam.py"
        try:
            source = adapter_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read adapter {adapter_path} ({exc})") from exc
        try:
            tree = ast.parse(source, filename=str(adapter_path))
        except SyntaxError as exc:
            raise ValueError(f"adapter {adapter_path} does not parse ({exc})") from exc
        adapter_names: list[str] | None = None
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "REDTEAM_TOOL_NAMES"
                and isinstance(node.value, (ast.Tuple, ast.List))
            ):
                names: list[str] = []
                for elt in node.value.elts:
                    if not isinstance(elt, ast.Constant) or not isinstance(
                        elt.value, str
                    ):
                        raise ValueError(
                            f"adapter {adapter_path}: REDTEAM_TOOL_NAMES "
                            "holds non-string entries"
                        )
                    names.append(elt.value)
                adapter_names = names
        if adapter_names is None:
            raise ValueError(f"adapter {adapter_path}: REDTEAM_TOOL_NAMES not found")
    except ValueError as exc:
        return False, f"drift-check unreadable input: {exc}"
    contract_version = contract.get("harness_version")
    if version != contract_version:
        return (
            False,
            f"harness_version drift: sidecar/VERSION {version!r} != "
            "contract/bot-interface.json harness_version "
            f"{contract_version!r}",
        )
    contract_names = contract.get("tool_names")
    if sorted(adapter_names) != sorted(contract_names or []):
        missing = sorted(set(contract_names or []) - set(adapter_names))
        extra = sorted(set(adapter_names) - set(contract_names or []))
        return (
            False,
            f"tool-surface drift: omega_prime/tools/redteam.py "
            f"{sorted(adapter_names)} != contract/bot-interface.json "
            f"tool_names {sorted(contract_names or [])} "
            f"(missing {missing}; extra {extra})",
        )
    return (
        True,
        f"no drift: sidecar/VERSION {version!r} and "
        f"{len(adapter_names)} adapter tools agree with the contract",
    )


def normalize_response(response: str) -> str:
    """Canonical gateway-response form (mirrors sidecar.findings)."""
    return re.sub(r"\s+", " ", response.strip())


def finding_hash(case_id: str, response: str) -> str:
    """Hex identity binding ``case_id`` to the normalized response."""
    canonical = case_id + "\x00" + normalize_response(response)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_last_run(path: Path) -> tuple[dict[str, object] | None, bool]:
    """Return ``(observations, mapping_invalid)``.

    ``None`` observations means no run was ever recorded (missing file).
    ``mapping_invalid`` means the JSON parsed but holds no ``observations``
    mapping — a detection-gap failure, not a fatal load error.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError as exc:
        raise ReportError(f"error: cannot read {path} ({exc})") from exc
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise ReportError(
            f"error: cannot parse last-run observations: {path} ({exc})"
        ) from exc
    if not isinstance(record, dict):
        raise ReportError(
            f"error: cannot parse last-run observations: {path} (not a JSON object)"
        )
    observations = record.get("observations")
    if observations is None:
        return {}, False
    if not isinstance(observations, dict):
        return {}, True
    return observations, False


def load_findings(path: Path) -> list[dict[str, object]]:
    """Findings rows; fail closed on corrupt rows (never silent)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ReportError(f"error: cannot read {path} ({exc})") from exc
    rows: list[dict[str, object]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ReportError(
                f"error: corrupt findings row: {path}:{lineno} ({exc})"
            ) from exc
        if not isinstance(row, dict):
            raise ReportError(f"error: corrupt findings row: {path}:{lineno}")
        rows.append(row)
    return rows


def load_transitions(path: Path) -> tuple[list[dict[str, object]], datetime | None]:
    """Transition rows with parsed timestamps; malformed lines warn to stderr.

    Legacy rows without a ``cause`` are normalized to ``"unknown"`` so the
    state-timeline section and its footer never KeyError on old history.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        raise ReportError(f"error: cannot read {path} ({exc})") from exc
    rows: list[dict[str, object]] = []
    newest: datetime | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            print(
                f"warning: skipping malformed transitions line {path}:{lineno}",
                file=sys.stderr,
            )
            continue
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("from"), str)
            or not isinstance(row.get("to"), str)
            or not isinstance(row.get("ts"), str)
        ):
            print(
                f"warning: skipping malformed transitions line {path}:{lineno}",
                file=sys.stderr,
            )
            continue
        try:
            moment = parse_rfc3339(str(row["ts"]))
        except ReportError:
            print(
                f"warning: skipping transitions line with bad timestamp "
                f"{path}:{lineno}",
                file=sys.stderr,
            )
            continue
        row["_dt"] = moment
        row["cause"] = str(row.get("cause") or "unknown")
        rows.append(row)
        if newest is None or moment > newest:
            newest = moment
    return rows, newest


def findings_newest(rows: list[dict[str, object]]) -> datetime | None:
    """Newest findings timestamp across ``ts``/``last_seen`` fields."""
    newest: datetime | None = None
    for row in rows:
        for key in ("ts", "last_seen"):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                moment = parse_rfc3339(value)
            except ReportError:
                continue
            if newest is None or moment > newest:
                newest = moment
    return newest


#: Severity display order for the severity-timeline section (critical
#: first; unknown severities sort last).
SEVERITY_ORDER = ("critical", "high", "medium", "low")


def _severity_rank(severity: object) -> int:
    """Zero-based rank of ``severity`` in SEVERITY_ORDER (unknowns last)."""
    key = severity.lower() if isinstance(severity, str) else ""
    try:
        return SEVERITY_ORDER.index(key)
    except ValueError:
        return len(SEVERITY_ORDER)


def severity_timeline_rows(
    findings: list[dict[str, object]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[str, str, str, str]]:
    """In-window findings as ``(hash12, severity, class, timestamp)`` rows.

    Membership and the displayed timestamp use ``last_seen`` (falling back
    to ``ts``); rows with neither parseable timestamp are skipped. Rows
    missing ``dedupe_hash`` reuse the ``finding_hash(case_id, response)``
    fallback; rows with neither hash nor case_id are skipped. Sort is
    ``(severity rank, timestamp, hash, case_id, class)`` — total and
    input-order independent, so reruns are byte-identical.
    """
    staged: list[tuple[int, str, str, str, str, str]] = []
    for row in findings:
        digest = row.get("dedupe_hash")
        case_id = row.get("case_id")
        if not isinstance(digest, str) or not digest:
            if not isinstance(case_id, str) or not case_id:
                continue
            response = row.get("response")
            digest = finding_hash(
                case_id, response if isinstance(response, str) else ""
            )
        stamp: datetime | None = None
        stamp_text = ""
        for key in ("last_seen", "ts"):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                stamp = parse_rfc3339(value)
            except ReportError:
                continue
            stamp_text = fmt_rfc3339(stamp)
            break
        if stamp is None or not (window_start <= stamp <= window_end):
            continue
        severity = row.get("severity")
        sev_text = (
            severity.lower()
            if isinstance(severity, str) and severity.strip()
            else "unknown"
        )
        klass = row.get("class")
        class_text = klass if isinstance(klass, str) and klass else "unknown"
        case_text = case_id if isinstance(case_id, str) and case_id else "unknown"
        staged.append(
            (
                _severity_rank(sev_text),
                stamp_text,
                digest,
                case_text,
                sev_text,
                class_text,
            )
        )
    staged.sort()
    return [
        (digest[:12], sev, klass, ts_text)
        for _, ts_text, digest, _, sev, klass in staged
    ]


def drill_down_rows(
    findings: list[dict[str, object]],
    klass: str,
) -> list[tuple[str, str, str, str, str]]:
    """Per-finding detail rows for one technique class.

    Returns ``(dedupe_hash, severity, observed_detection, timestamp,
    verdict)`` tuples for every findings row whose ``class`` equals
    ``klass``, sorted by ``dedupe_hash`` — total and input-order
    independent, so reruns are byte-identical. Rows missing both
    ``dedupe_hash`` and ``case_id`` are skipped; the timestamp prefers
    ``last_seen`` over ``ts`` (``unknown`` when neither parses).
    """
    staged: list[tuple[str, str, str, str, str]] = []
    for row in findings:
        if row.get("class") != klass:
            continue
        digest = row.get("dedupe_hash")
        case_id = row.get("case_id")
        if not isinstance(digest, str) or not digest:
            if not isinstance(case_id, str) or not case_id:
                continue
            response = row.get("response")
            digest = finding_hash(
                case_id, response if isinstance(response, str) else ""
            )
        severity = row.get("severity")
        sev_text = (
            severity.lower()
            if isinstance(severity, str) and severity.strip()
            else "unknown"
        )
        observed = row.get("observed_detection")
        observed_text = observed if isinstance(observed, str) else ""
        stamp_text = "unknown"
        for key in ("last_seen", "ts"):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                stamp_text = fmt_rfc3339(parse_rfc3339(value))
            except ReportError:
                continue
            break
        verdict = row.get("verdict")
        verdict_text = verdict if isinstance(verdict, str) and verdict else "unknown"
        staged.append((digest, sev_text, observed_text, stamp_text, verdict_text))
    staged.sort(key=lambda item: item[0])
    return staged


def detection_gaps(
    cases: list[tuple[str, str, str, str]],
    load_errors: list[str],
    observations: dict[str, object],
    last_run_missing: bool,
    mapping_invalid: bool,
    last_run_path: Path,
    drift: tuple[bool, str],
) -> list[str]:
    """Read-only mirror of ``validate_suite()`` failure strings."""
    failures = list(load_errors)
    drift_ok, drift_detail = drift
    if not drift_ok:
        failures.append(f"version drift implies DEGRADED: {drift_detail}")
        return failures
    if last_run_missing:
        failures.append(
            f"no recorded run observations "
            f"([Errno 2] No such file or directory: '{last_run_path}'); "
            "run first"
        )
        return failures
    if mapping_invalid:
        failures.append("recorded run has no 'observations' mapping")
        return failures
    known = {case_id for case_id, _, _, _ in cases}
    for case_id, expected, rationale, _case_class in cases:
        observed = observations.get(case_id)
        if not isinstance(observed, dict) or "observed_detection" not in observed:
            failures.append(
                f"case {case_id!r}: FAIL — no recorded observation; re-run the suite"
            )
            continue
        got = observed["observed_detection"]
        if got == expected:
            continue
        if rationale.strip():
            continue  # accepted residual excuses exactly this case_id
        failures.append(
            f"case {case_id!r}: FAIL — observed detection {got!r} != "
            f"expected {expected!r}"
        )
    for observed_id in sorted(observations):
        if observed_id not in known:
            failures.append(
                f"case {observed_id!r}: FAIL — recorded observation "
                "for unknown case_id; re-run the suite"
            )
    return failures


#: Technique classes from ``sidecar/case.schema.json`` (byte-wise order).
#: Unknown ``class:`` values seen on disk render as extra rows after these.
SCHEMA_CLASSES = (
    "abuse",
    "auth-bypass",
    "cost-abuse",
    "jailbreak",
    "malformed-input",
    "prompt-injection",
)


def _gap_case_ids(text: str) -> list[str]:
    """Quoted ``'case_id'`` tokens plus ``*.yaml`` filename stems in ``text``."""
    tokens = re.findall(r"'([^']*)'", text)
    for stem in re.findall(r"([A-Za-z0-9][A-Za-z0-9_.-]*)\.yaml", text):
        tokens.append(stem)
    return tokens


def coverage_rows(
    cases: list[tuple[str, str, str, str]],
    observations: dict[str, object],
    gaps: list[str],
) -> list[tuple[str, int, int, int, float, int, str]]:
    """Per-class coverage rows ``(class, cases, runs, wins, pct, gaps, verdict)``.

    Pure helper over already-read inputs: ``cases`` counts loaded case
    files of the class; ``runs`` counts those cases present in
    ``observations`` with an ``observed_detection`` key (same pass rule
    as the per-technique table: ``got == expected`` or a non-empty
    ``residual_rationale`` counts as a win); ``gaps`` counts
    ``detection_gaps()`` strings attributable to the class via a
    mentioned ``case_id`` (drift/global errors attribute to no class).
    Row order is the six schema classes byte-wise, then any unknown
    ``class:`` values seen on disk sorted after them.
    """
    by_class: dict[str, list[tuple[str, str, str]]] = {}
    for case_id, expected, rationale, case_class in cases:
        by_class.setdefault(case_class, []).append((case_id, expected, rationale))
    known_class = {case_id: case_class for case_id, _, _, case_class in cases}
    known_ids = set(known_class)
    gap_counts: dict[str, int] = {}
    for failure in gaps:
        attributed: set[str] = set()
        for token in _gap_case_ids(failure):
            if token in known_ids:
                attributed.add(known_class[token])
            else:
                for class_name in list(SCHEMA_CLASSES) + sorted(by_class):
                    if token == class_name or token.startswith(class_name + "-"):
                        attributed.add(class_name)
                        break
        for class_name in attributed:
            gap_counts[class_name] = gap_counts.get(class_name, 0) + 1
    ordered = list(SCHEMA_CLASSES) + sorted(
        name for name in by_class if name not in SCHEMA_CLASSES
    )
    rows: list[tuple[str, int, int, int, int, float, int, str]] = []
    for class_name in ordered:
        members = by_class.get(class_name, [])
        wins = 0
        runs = 0
        for case_id, expected, rationale in members:
            observed = observations.get(case_id)
            if not isinstance(observed, dict) or "observed_detection" not in observed:
                continue
            runs += 1
            if observed["observed_detection"] == expected or str(rationale).strip():
                wins += 1
        pct = 100.0 * wins / runs if runs else 0.0
        class_gaps = gap_counts.get(class_name, 0)
        if not members:
            verdict = "unexercised"
        elif runs > 0 and wins == runs and class_gaps == 0:
            verdict = "covered"
        else:
            verdict = "gappy"
        rows.append((class_name, len(members), runs, wins, pct, class_gaps, verdict))
    return rows


def read_baseline_snapshot(report_path: Path) -> dict[str, str] | None:
    """Findings snapshot (hash -> case_id) embedded in a past report."""
    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = SNAPSHOT_RE.search(text)
    if not match:
        return None
    try:
        snapshot = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(snapshot, dict):
        return None
    clean: dict[str, str] = {}
    for key, value in snapshot.items():
        if isinstance(key, str) and isinstance(value, str):
            clean[key] = value
    return clean


def newest_baseline(
    directory: Path, current_name: str
) -> tuple[str | None, dict[str, str] | None]:
    """Newest previous report name plus its snapshot (if parseable)."""
    try:
        names = sorted(
            path.name
            for path in directory.glob("engagement-*.md")
            if path.is_file() and path.name != current_name
        )
    except OSError:
        return None, None
    if not names:
        return None, None
    snapshot = read_baseline_snapshot(directory / names[-1])
    return names[-1], snapshot


#: Metrics diffed by the week-over-week comparison view (--compare).
COMPARISON_METRICS = ("runs", "pass_pct", "gaps", "new", "rescinded")


def _report_section_lines(text: str, heading: str) -> list[str]:
    """Body lines of one ``## `` section in a rendered report."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            break
    if start is None:
        return []
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def parse_previous_metrics(text: str) -> dict[str, int]:
    """Key metrics from a previously rendered report (missing ⇒ 0).

    Reads ``runs in window``, the per-technique ``wins/total`` rows (same
    integer percentage as :func:`_summary_pct`), the detection-gap bullet
    count, and the ``new/rescinded`` findings line. Deterministic.
    """
    metrics: dict[str, int] = {name: 0 for name in COMPARISON_METRICS}
    for line in _report_section_lines(text, "## Runs summary"):
        match = re.match(r"-\s+runs in window:\s+(\d+)", line.strip())
        if match:
            metrics["runs"] = int(match.group(1))
            break
    wins = 0
    total = 0
    for line in _report_section_lines(text, "## Per-technique pass rates"):
        match = re.match(r"\|\s*[^|]+\|\s*(\d+)/(\d+)", line.strip())
        if match:
            wins += int(match.group(1))
            total += int(match.group(2))
    metrics["pass_pct"] = round(100 * wins / total) if total else 0
    gap_lines = [
        line
        for line in _report_section_lines(text, "## Detection gaps")
        if line.strip().startswith("- ") and line.strip() != "- none"
    ]
    metrics["gaps"] = len(gap_lines)
    for line in _report_section_lines(text, "## Findings deltas"):
        match = re.match(r"-\s*new:\s+(\d+),\s*rescinded:\s+(\d+)", line.strip())
        if match:
            metrics["new"] = int(match.group(1))
            metrics["rescinded"] = int(match.group(2))
            break
    return metrics


def _comparison_arrow(delta: int) -> str:
    """Direction arrow for a metric delta (``→`` when unchanged)."""
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def render_comparison(
    *,
    current: dict[str, int],
    previous: dict[str, int],
    baseline_label: str,
) -> list[str]:
    """Week-over-week comparison lines (``current vs previous (Δ)``)."""
    rows = (
        ("runs", "runs", "", False),
        ("pass rate", "pass_pct", "%", True),
        ("gaps", "gaps", "", False),
        ("findings new", "new", "", False),
        ("findings rescinded", "rescinded", "", False),
    )
    lines = [
        "## Week-over-week comparison",
        "",
        f"- baseline: {baseline_label}",
        "",
        "| metric | current | previous | delta |",
        "| --- | --- | --- | --- |",
    ]
    for label, key, suffix, is_points in rows:
        cur = current.get(key, 0)
        prev = previous.get(key, 0)
        delta = cur - prev
        unit = "pp" if is_points else ""
        lines.append(
            f"| {label} | {cur}{suffix} | {prev}{suffix} | "
            f"{delta:+d}{unit} {_comparison_arrow(delta)} |"
        )
    return lines


def render_report(
    *,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime,
    version: str,
    runs: int,
    pass_rows: list[tuple[str, str]],
    coverage: list[tuple[str, int, int, int, float, int, str]] | None = None,
    gaps: list[str] | None = None,
    current_findings: dict[str, str] | None = None,
    baseline_name: str | None = None,
    baseline: dict[str, str] | None = None,
    transition_counts: list[tuple[str, str, int]] | None = None,
    timeline: list[tuple[str, str, str, str]] | None = None,
    cause_counts: list[tuple[str, int]] | None = None,
    severity_timeline: list[tuple[str, str, str, str]] | None = None,
    comparison: list[str] | None = None,
    drill_down: str | None = None,
    drill_down_rows: list[tuple[str, str, str, str, str]] | None = None,
) -> str:
    """Assemble the report sections in fixed order."""
    gaps = gaps if gaps is not None else []
    current_findings = current_findings if current_findings is not None else {}
    transition_counts = transition_counts if transition_counts is not None else []
    timeline = timeline if timeline is not None else []
    if cause_counts is None:
        counts: dict[str, int] = {}
        for _, _, _, cause in timeline:
            counts[cause] = counts.get(cause, 0) + 1
        cause_counts = sorted(counts.items())
    lines = [
        "# Engagement report",
        "",
        "## Header",
        "",
        f"- window: {fmt_rfc3339(window_start)}..{fmt_rfc3339(window_end)}",
        f"- generated-at: {fmt_rfc3339(generated_at)}",
        f"- harness-version: {version}",
        "",
        "## Runs summary",
        "",
        f"- runs in window: {runs}",
    ]
    if runs == 0:
        lines.append("- no runs in window")
    lines += ["", "## Per-technique pass rates", ""]
    if pass_rows:
        lines += ["| case_id | result |", "| --- | --- |"]
        for case_id, result in pass_rows:
            lines.append(f"| {case_id} | {result} |")
    else:
        lines += ["| case_id | result |", "| --- | --- |"]
        if runs == 0:
            lines.append("- no runs in window")
    lines += ["", "## Coverage matrix", ""]
    lines += [
        "| class | cases | runs | pass rate | gaps | verdict |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for class_name, case_count, class_runs, wins, pct, class_gaps, verdict in (
        coverage if coverage is not None else []
    ):
        rate = f"{wins}/{class_runs} ({pct:.1f}%)" if class_runs else "0/0 (n/a)"
        lines.append(
            f"| {class_name} | {case_count} | {class_runs} | {rate} "
            f"| {class_gaps} | {verdict} |"
        )
    if drill_down is not None:
        lines += ["", f"## Coverage drill-down: {drill_down}", ""]
        lines += [
            "| dedupe_hash | severity | observed_detection | timestamp | verdict |",
            "| --- | --- | --- | --- | --- |",
        ]
        rows = drill_down_rows if drill_down_rows is not None else []
        if rows:
            for digest, sev, observed, stamp, row_verdict in rows:
                lines.append(
                    f"| {digest} | {sev} | {observed} | {stamp} | {row_verdict} |"
                )
        else:
            lines.append(f"- no findings in class {drill_down}")
    lines += ["", "## Detection gaps", ""]
    if gaps:
        for failure in gaps:
            lines.append(f"- {failure}")
    else:
        lines.append("- none")
    lines += ["", "## Findings deltas", ""]
    if baseline is None:
        count = len(current_findings)
        lines.append(
            f"- no baseline: {baseline_name or 'no previous report'}; "
            f"treating all {count} findings as new."
        )
        new_hashes = sorted(current_findings)
        rescinded: list[str] = []
    else:
        new_hashes = sorted(h for h in current_findings if h not in baseline)
        rescinded = sorted(h for h in baseline if h not in current_findings)
    lines.append(f"- new: {len(new_hashes)}, rescinded: {len(rescinded)}")
    lines += ["", "| dedupe_hash | change | case_id |", "| --- | --- | --- |"]
    for digest in new_hashes:
        lines.append(f"| {digest} | new | {current_findings[digest]} |")
    for digest in rescinded:
        case_id = baseline.get(digest, "unknown") if baseline else "unknown"
        lines.append(f"| {digest} | rescinded | {case_id} |")
    lines += ["", "## State transitions", ""]
    lines += ["| from | to | count |", "| --- | --- | --- |"]
    for from_state, to_state, count in transition_counts:
        lines.append(f"| {from_state} | {to_state} | {count} |")
    lines += ["", "## State timeline", ""]
    if timeline:
        for ts_text, from_state, to_state, cause in timeline:
            lines.append(f"- {ts_text} {from_state} -> {to_state} ({cause})")
        causes_text = ", ".join(f"{cause}={count}" for cause, count in cause_counts)
        lines.append(f"- causes: {causes_text}")
    else:
        lines.append("- no transitions in window")
        lines.append("- causes: none")
    lines += ["", "## Severity timeline", ""]
    if severity_timeline is None:
        severity_timeline = []
    if severity_timeline:
        for digest12, sev, klass, ts_text in severity_timeline:
            lines.append(f"- {digest12} {sev} {klass} {ts_text}")
    else:
        lines.append("- no findings in window")
    if comparison:
        lines += ["", *comparison]
    lines += [
        "",
        f"<!-- findings-snapshot: {json.dumps(current_findings, sort_keys=True)} -->",
        "",
    ]
    return "\n".join(lines)


def _collect_window(reports_dir: Path, since_text: str | None) -> dict[str, object]:
    """Load history and compute the shared report window.

    Read-only projection used by both :func:`build_report` and
    :func:`summarize`: window endpoints, in-window transitions, run
    count, transition counts, loaded cases, run observations,
    detection gaps, per-technique rows, the current findings map, and
    the in-window severity-timeline rows.
    Raises :class:`ReportError` on unreadable inputs.
    """
    if not reports_dir.exists():
        raise ReportError(f"error: reports dir does not exist: {reports_dir}")
    if not reports_dir.is_dir():
        raise ReportError(f"error: reports dir is not a directory: {reports_dir}")
    transitions, transitions_newest = load_transitions(
        reports_dir / TRANSITIONS_FILENAME
    )
    findings = load_findings(reports_dir / FINDINGS_FILENAME)
    last_run_path = reports_dir / LAST_RUN_FILENAME
    observations, mapping_invalid = load_last_run(last_run_path)
    last_run_missing = observations is None
    if observations is None:
        observations = {}
    newest = transitions_newest
    findings_stamp = findings_newest(findings)
    if findings_stamp is not None and (newest is None or findings_stamp > newest):
        newest = findings_stamp
    epoch = parse_rfc3339(EPOCH_RFC3339)
    window_end = newest if newest is not None else epoch
    if since_text is not None:
        try:
            window_start = parse_rfc3339(since_text)
        except ReportError as exc:
            raise ReportError(f"error: cannot parse --since ({exc})") from exc
    else:
        window_start = window_end - timedelta(days=7)
    in_window = [
        row
        for row in transitions
        if window_start <= row["_dt"] <= window_end  # type: ignore[operator]
    ]
    runs = sum(
        1 for row in in_window if row["from"] == "IDLE" and row["to"] == "RUNNING"
    )
    counts: dict[tuple[str, str], int] = {}
    for row in in_window:
        key = (str(row["from"]), str(row["to"]))
        counts[key] = counts.get(key, 0) + 1
    transition_counts = sorted(
        (
            (from_state, to_state, count)
            for (from_state, to_state), count in counts.items()
        )
    )
    cases, load_errors = read_cases(REPO_ROOT / "sidecar" / "cases")
    gaps = detection_gaps(
        cases,
        load_errors,
        observations,
        last_run_missing,
        mapping_invalid,
        last_run_path,
        check_drift(REPO_ROOT),
    )
    pass_rows: list[tuple[str, str]] = []
    if runs > 0:
        known = {
            case_id: (expected, rationale) for case_id, expected, rationale, _ in cases
        }
        for case_id in sorted(set(known) | {str(k) for k in observations}):
            observed = observations.get(case_id)
            if (
                case_id in known
                and isinstance(observed, dict)
                and "observed_detection" in observed
            ):
                expected, rationale = known[case_id]
                got = observed["observed_detection"]
                passed = got == expected or bool(rationale.strip())
                total = 1
                wins = 1 if passed else 0
                pct = 100.0 * wins / total
                pass_rows.append((case_id, f"{wins}/{total} ({pct:.1f}%)"))
            else:
                pass_rows.append((case_id, "0/0 (n/a)"))
    current_findings: dict[str, str] = {}
    for row in findings:
        digest = row.get("dedupe_hash")
        case_id = row.get("case_id")
        if not isinstance(digest, str) or not digest:
            if not isinstance(case_id, str) or not case_id:
                continue
            response = row.get("response")
            digest = finding_hash(
                case_id, response if isinstance(response, str) else ""
            )
        current_findings[digest] = (
            case_id if isinstance(case_id, str) and case_id else "unknown"
        )
    return {
        "window_start": window_start,
        "window_end": window_end,
        "in_window": in_window,
        "runs": runs,
        "transition_counts": transition_counts,
        "cases": cases,
        "observations": observations,
        "last_run_missing": last_run_missing,
        "gaps": gaps,
        "pass_rows": pass_rows,
        "current_findings": current_findings,
        "findings": findings,
        "severity_timeline": severity_timeline_rows(findings, window_start, window_end),
        "history_empty": not transitions and not findings and last_run_missing,
    }


def build_report(
    reports_dir: Path,
    since_text: str | None,
    compare_path: Path | None = None,
    drill_down: str | None = None,
) -> tuple[str, str, Path]:
    """Render the report; return ``(markdown, file_name, out_dir)``."""
    window = _collect_window(reports_dir, since_text)
    window_start = window["window_start"]
    window_end = window["window_end"]
    generated_at = window_end
    in_window = window["in_window"]
    runs = window["runs"]
    cases = window["cases"]
    observations = window["observations"]
    gaps = window["gaps"]
    pass_rows = window["pass_rows"]
    current_findings = window["current_findings"]
    timeline = sorted(
        (
            fmt_rfc3339(row["_dt"]),
            str(row["from"]),
            str(row["to"]),
            str(row["cause"]),
        )
        for row in in_window
    )
    cause_tally: dict[str, int] = {}
    for _, _, _, cause in timeline:
        cause_tally[cause] = cause_tally.get(cause, 0) + 1
    cause_counts = sorted(cause_tally.items())
    # Zero report-wide runs still renders the matrix, but every row is
    # 0/0 (n/a): observations only count inside a non-empty window.
    coverage = coverage_rows(cases, observations if runs > 0 else {}, gaps)
    start_day = window_start.date().isoformat().replace(":", "-")
    end_day = window_end.date().isoformat().replace(":", "-")
    file_name = f"engagement-{start_day}_{end_day}.md"
    out_dir = output_dir(reports_dir)
    baseline_name, baseline = newest_baseline(out_dir, file_name)
    version = read_version(REPO_ROOT)
    comparison: list[str] | None = None
    if compare_path is not None:
        try:
            previous_text = compare_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read --compare report {compare_path} ({exc})"
            ) from exc
        if baseline is None:
            new_count = len(current_findings)
            rescinded_count = 0
        else:
            new_count = sum(1 for h in current_findings if h not in baseline)
            rescinded_count = sum(1 for h in baseline if h not in current_findings)
        current_metrics = {
            "runs": runs,
            "pass_pct": _summary_pct(pass_rows),
            "gaps": len(gaps),
            "new": new_count,
            "rescinded": rescinded_count,
        }
        comparison = render_comparison(
            current=current_metrics,
            previous=parse_previous_metrics(previous_text),
            baseline_label=compare_path.name,
        )
    markdown = render_report(
        window_start=window_start,
        window_end=window_end,
        generated_at=generated_at,
        version=version,
        runs=runs,
        pass_rows=pass_rows,
        coverage=coverage,
        gaps=gaps,
        current_findings=current_findings,
        baseline_name=baseline_name,
        baseline=baseline,
        transition_counts=window["transition_counts"],
        timeline=timeline,
        cause_counts=cause_counts,
        severity_timeline=window["severity_timeline"],
        comparison=comparison,
        drill_down=drill_down,
        drill_down_rows=(
            drill_down_rows(window["findings"], drill_down)
            if drill_down is not None
            else None
        ),
    )
    return markdown, file_name, out_dir


def _summary_pct(pass_rows: list[tuple[str, str]]) -> int:
    """Integer pass percentage over ``pass_rows`` (``0`` when no rows)."""
    wins = 0
    total = 0
    for _, result in pass_rows:
        match = re.match(r"(\d+)/(\d+)", result)
        if match:
            wins += int(match.group(1))
            total += int(match.group(2))
    if not total:
        return 0
    return round(100 * wins / total)


def summarize(reports_dir: Path, since_text: str | None = None) -> dict[str, object]:
    """One-line engagement posture ``(state, line, report, generated_at)``.

    Read-only projection of :func:`_collect_window`: ``line`` is
    ``runs=X pass=Y% gaps=Z findings=+N/-M`` with ``Y`` the integer pass
    percentage over ``pass_rows`` and ``+N``/``-M`` diffed against the
    newest baseline via :func:`newest_baseline`. Empty or missing history
    reports ``state: "no-reports"`` with an all-zeros line instead of an
    error. Writes no files. Raises :class:`ReportError` only for genuine
    I/O or parse failures.
    """
    if not reports_dir.exists() or not reports_dir.is_dir():
        return {
            "state": "no-reports",
            "line": "runs=0 pass=0% gaps=0 findings=+0/-0",
            "report": None,
            "generated_at": None,
        }
    window = _collect_window(reports_dir, since_text)
    if window["history_empty"]:
        return {
            "state": "no-reports",
            "line": "runs=0 pass=0% gaps=0 findings=+0/-0",
            "report": None,
            "generated_at": None,
        }
    window_start = window["window_start"]
    window_end = window["window_end"]
    current_findings = window["current_findings"]
    start_day = window_start.date().isoformat().replace(":", "-")
    end_day = window_end.date().isoformat().replace(":", "-")
    file_name = f"engagement-{start_day}_{end_day}.md"
    out_dir = output_dir(reports_dir)
    baseline_name, baseline = newest_baseline(out_dir, file_name)
    if baseline is None:
        new_count = len(current_findings)
        rescinded_count = 0
    else:
        new_count = sum(1 for h in current_findings if h not in baseline)
        rescinded_count = sum(1 for h in baseline if h not in current_findings)
    line = (
        f"runs={window['runs']} pass={_summary_pct(window['pass_rows'])}% "
        f"gaps={len(window['gaps'])} findings=+{new_count}/-{rescinded_count}"
    )
    return {
        "state": "ok",
        "line": line,
        "report": baseline_name,
        "generated_at": fmt_rfc3339(window_end),
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 when the report renders, 1 otherwise."""
    parser = argparse.ArgumentParser(
        description=(
            "Render a deterministic purple-team engagement report from "
            "sidecar/reports/ history."
        ),
        epilog=(
            "Weekly cadence: schedule_heartbeat(store, session, "
            '"Run scripts/engagement-report.py for the past 7 days and '
            'attach sidecar/reports/reports/<newest report>", '
            "due_at, interval_seconds=604800). "
            "The report carries a Coverage matrix section (per-class "
            "cases/runs/pass-rate/gaps/verdict) and a State timeline section "
            "(chronological transitions plus per-cause counts)."
        ),
    )
    parser.add_argument(
        "--since",
        default=None,
        help="window start (inclusive RFC3339); default: 7 days before "
        "the newest input record.",
    )
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="history source; default: $OMEGA_PRIME_SIDECAR_REPORTS_DIR "
        "or sidecar/reports/.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="write the markdown file here; default: stdout. A canonical "
        "per-window copy is always kept in the output directory.",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="previous report markdown to diff against: appends a "
        "## Week-over-week comparison section (runs, pass rate, gaps, "
        "findings new/rescinded as current vs previous (Δ) with arrows).",
    )
    parser.add_argument(
        "--drill-down",
        default=None,
        metavar="CLASS",
        help="after the coverage matrix, render a per-finding detail table "
        "(dedupe_hash, severity, observed_detection, timestamp, verdict) "
        "for technique class CLASS from findings.ndjson.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="emit exactly one summary line "
        "(runs=X pass=Y%% gaps=Z findings=+N/-M) on stdout and write no "
        "files; exit 0 with an all-zeros line when no reports exist, "
        "exit 1 with error: on stderr only for genuine I/O or parse failures.",
    )
    args = parser.parse_args(argv)

    reports_dir = Path(args.reports_dir) if args.reports_dir else default_reports_dir()
    if args.summary:
        try:
            summary = summarize(reports_dir, args.since)
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        sys.stdout.write(str(summary["line"]) + "\n")
        return 0
    try:
        compare_path = Path(args.compare) if args.compare else None
        markdown, file_name, out_dir = build_report(
            reports_dir, args.since, compare_path, drill_down=args.drill_down
        )
    except ReportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / file_name).write_text(markdown, encoding="utf-8")
    except OSError as exc:
        print(
            f"error: cannot write report {out_dir / file_name} ({exc})", file=sys.stderr
        )
        return 1

    if args.out:
        try:
            out_path = Path(args.out)
            if out_path.resolve() != (out_dir / file_name).resolve():
                out_path.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            print(
                f"error: cannot write --out parent: {args.out} ({exc})", file=sys.stderr
            )
            return 1
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

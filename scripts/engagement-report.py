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
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


#: Severity-tier weights for risk-weighted coverage scoring (G12).
#: Higher tiers dominate the weighted score, so a FAIL in a critical
#: class (auth bypass / prompt injection) drags the score more than a
#: FAIL in a moderate class (robustness / cost abuse).
RISK_SEVERITY_WEIGHTS = {
    "critical": 10,
    "high": 5,
    "moderate": 2,
    "low": 1,
}

#: Risk tier per technique class. Members of SCHEMA_CLASSES map to a
#: tier here; unknown classes seen on disk fall back to
#: :data:`DEFAULT_CLASS_RISK_TIER` so scoring stays deterministic.
CLASS_RISK_TIER = {
    "auth-bypass": "critical",
    "prompt-injection": "critical",
    "abuse": "high",
    "jailbreak": "high",
    "cost-abuse": "moderate",
    "malformed-input": "moderate",
}

#: Tier for classes absent from :data:`CLASS_RISK_TIER`.
DEFAULT_CLASS_RISK_TIER = "low"


def risk_weighted_score(
    coverage: list[tuple[str, int, int, int, float, int, str]],
) -> tuple[float, int, int, list[tuple[str, str, int, int, int, int, int]]]:
    """Risk-weighted coverage score over ``coverage`` rows.

    Each class contributes ``weight * wins`` earned points out of
    ``weight * cases`` possible points, where ``weight`` comes from the
    class risk tier (:data:`CLASS_RISK_TIER`, default ``"low"``) via
    :data:`RISK_SEVERITY_WEIGHTS`. Returns ``(score_pct, earned,
    possible, breakdown)`` with ``breakdown`` rows ``(class, tier,
    weight, cases, wins, earned, possible)`` in coverage order
    (deterministic). ``score_pct`` is ``0.0`` when no class has cases.
    """
    breakdown: list[tuple[str, str, int, int, int, int, int]] = []
    earned = 0
    possible = 0
    for class_name, case_count, _runs, wins, _pct, _gaps, _verdict in coverage:
        tier = CLASS_RISK_TIER.get(class_name, DEFAULT_CLASS_RISK_TIER)
        weight = RISK_SEVERITY_WEIGHTS[tier]
        max_points = weight * case_count
        got_points = weight * wins
        earned += got_points
        possible += max_points
        breakdown.append(
            (class_name, tier, weight, case_count, wins, got_points, max_points)
        )
    score = round(100.0 * earned / possible, 1) if possible else 0.0
    return score, earned, possible, breakdown


def render_risk_weighted_score(
    coverage: list[tuple[str, int, int, int, float, int, str]],
) -> list[str]:
    """Risk-weighted score section lines (deterministic, coverage order)."""
    score, earned, possible, breakdown = risk_weighted_score(coverage)
    weights_text = ", ".join(
        f"{tier}={RISK_SEVERITY_WEIGHTS[tier]}"
        for tier in ("critical", "high", "moderate", "low")
    )
    lines = [
        "## Risk-weighted score",
        "",
        f"- risk-weighted score: {score:.1f}% ({earned}/{possible} weighted points)",
        f"- weights: {weights_text}",
        "",
        "| class | tier | weight | cases | wins | weighted |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for class_name, tier, weight, case_count, wins, got, maximum in breakdown:
        lines.append(
            f"| {class_name} | {tier} | {weight} | {case_count} | {wins} "
            f"| {got}/{maximum} |"
        )
    return lines


#: Risk-score bands mapping the 0-100 risk-weighted score to a
#: severity tier for the HTML badge color (higher is better).
RISK_SCORE_TIERS = (
    ("low", 90.0),
    ("moderate", 70.0),
    ("high", 40.0),
    ("critical", 0.0),
)


def risk_score_tier(score: float) -> str:
    """Severity tier for a risk-weighted ``score`` (first band at or below)."""
    for tier, floor in RISK_SCORE_TIERS:
        if score >= floor:
            return tier
    return "critical"


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
    trend_skipped: bool = False,
) -> list[str]:
    """Week-over-week comparison lines (``current vs previous (Δ)``).

    When ``trend_skipped`` is true (``--compare`` without ``--trend``),
    a note records that the trend section was omitted.
    """
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
    ]
    if trend_skipped:
        lines.append("- trend: skipped (no --trend history provided with --compare)")
    lines += [
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


#: Metrics diffed per technique class by the coverage-delta view
#: (``--diff-coverage``). Fixed row order within each class.
COVERAGE_DELTA_METRICS = ("cases", "runs", "pass rate", "gaps", "verdict")

#: Ordinal for verdict-trend ranking (higher is better; unknown last).
_COVERAGE_VERDICT_RANK = {"covered": 2, "gappy": 1, "unexercised": 0}


def _coverage_verdict_rank(verdict: object) -> int:
    """Ordinal of a coverage verdict (unknown verdicts rank last)."""
    if isinstance(verdict, str) and verdict in _COVERAGE_VERDICT_RANK:
        return _COVERAGE_VERDICT_RANK[verdict]
    return -1


def parse_previous_coverage(
    text: str,
) -> dict[str, tuple[int, int, int, float, int, str]]:
    """Per-class coverage from a previously rendered report.

    Parses the ``## Coverage matrix`` table rows
    (``| class | cases | runs | w/r (pct%) | gaps | verdict |``;
    ``0/0 (n/a)`` when the class had no runs). Missing section or
    malformed rows are skipped; absent classes are simply absent from
    the mapping. Deterministic.
    """
    previous: dict[str, tuple[int, int, int, float, int, str]] = {}
    for line in _report_section_lines(text, "## Coverage matrix"):
        match = re.match(
            r"\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*"
            r"(\d+)/(\d+)\s*\((?:([\d.]+)%|n/a)\)\s*\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|",
            line.strip(),
        )
        if not match:
            continue
        class_name = match.group(1).strip()
        if not class_name or class_name == "class":
            continue
        rate_text = (match.group(6) or "").strip()
        try:
            pct = float(rate_text)
        except ValueError:
            pct = 0.0
        previous[class_name] = (
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            pct,
            int(match.group(7)),
            match.group(8).strip(),
        )
    return previous


def _coverage_delta_trend(metric: str, current: float, previous: float) -> str:
    """``improved``/``regressed``/``unchanged`` for one class metric.

    Higher is better for cases, runs, and pass rate; lower is better
    for gaps; verdicts compare by :func:`_coverage_verdict_rank`.
    Exact ties are ``unchanged``.
    """
    if current == previous:
        return "unchanged"
    if metric == "gaps":
        return "improved" if current < previous else "regressed"
    return "improved" if current > previous else "regressed"


def coverage_delta_rows(
    *,
    current: list[tuple[str, int, int, int, float, int, str]],
    previous: dict[str, tuple[int, int, int, float, int, str]],
) -> list[tuple[str, str, str, str, str, str]]:
    """Per-class coverage-delta data rows.

    One ``(class, metric, current, previous, delta, trend)`` tuple per
    ``(class, metric)`` with ``metric`` in :data:`COVERAGE_DELTA_METRICS`
    order; classes are the union of current and previous rows (current
    order first, then extras sorted). ``trend`` is one of
    ``improved``/``regressed``/``unchanged``. Sides missing a class
    render ``n/a`` with an ``n/a →`` delta. Deterministic.
    """
    ordered = [row[0] for row in current]
    for class_name in sorted(previous):
        if class_name not in ordered:
            ordered.append(class_name)
    current_map = {row[0]: row for row in current}
    rows: list[tuple[str, str, str, str, str, str]] = []
    for class_name in ordered:
        row = current_map.get(class_name)
        prev = previous.get(class_name)
        cur_cells: dict[str, str] = {}
        prev_cells: dict[str, str] = {}
        delta_cells: dict[str, str] = {}
        trend_cells: dict[str, str] = {}
        if row is None:
            cur_cells = {metric: "n/a" for metric in COVERAGE_DELTA_METRICS}
        else:
            _, cases, runs, wins, pct, gaps, verdict = row
            rate = f"{wins}/{runs} ({pct:.1f}%)" if runs else "0/0 (n/a)"
            cur_cells = {
                "cases": str(cases),
                "runs": str(runs),
                "pass rate": rate,
                "gaps": str(gaps),
                "verdict": verdict,
            }
        if prev is None:
            prev_cells = {metric: "n/a" for metric in COVERAGE_DELTA_METRICS}
        else:
            p_cases, p_runs, p_wins, p_pct, p_gaps, p_verdict = prev
            p_rate = f"{p_wins}/{p_runs} ({p_pct:.1f}%)" if p_runs else "0/0 (n/a)"
            prev_cells = {
                "cases": str(p_cases),
                "runs": str(p_runs),
                "pass rate": p_rate,
                "gaps": str(p_gaps),
                "verdict": p_verdict,
            }
        if row is None or prev is None:
            delta_cells = {metric: "n/a →" for metric in COVERAGE_DELTA_METRICS}
            trend_cells = {metric: "unchanged" for metric in COVERAGE_DELTA_METRICS}
        else:
            _, cases, runs, wins, pct, gaps, verdict = row
            p_cases, p_runs, p_wins, p_pct, p_gaps, p_verdict = prev
            deltas = {
                "cases": cases - p_cases,
                "runs": runs - p_runs,
                "gaps": gaps - p_gaps,
            }
            for metric, delta in deltas.items():
                delta_cells[metric] = f"{delta:+d} {_comparison_arrow(delta)}"
            rate_delta = pct - p_pct
            arrow = "↑" if rate_delta > 0 else ("↓" if rate_delta < 0 else "→")
            delta_cells["pass rate"] = f"{rate_delta:+.1f}pp {arrow}"
            if verdict == p_verdict:
                delta_cells["verdict"] = "→"
            else:
                delta_cells["verdict"] = f"{p_verdict} → {verdict}"
            trend_cells = {
                "cases": _coverage_delta_trend("cases", float(cases), float(p_cases)),
                "runs": _coverage_delta_trend("runs", float(runs), float(p_runs)),
                "pass rate": _coverage_delta_trend("pass rate", pct, p_pct),
                "gaps": _coverage_delta_trend("gaps", float(gaps), float(p_gaps)),
                "verdict": _coverage_delta_trend(
                    "verdict",
                    float(_coverage_verdict_rank(verdict)),
                    float(_coverage_verdict_rank(p_verdict)),
                ),
            }
        for metric in COVERAGE_DELTA_METRICS:
            rows.append(
                (
                    class_name,
                    metric,
                    cur_cells[metric],
                    prev_cells[metric],
                    delta_cells[metric],
                    trend_cells[metric],
                )
            )
    return rows


def render_coverage_delta(
    *,
    current: list[tuple[str, int, int, int, float, int, str]],
    previous: dict[str, tuple[int, int, int, float, int, str]],
    baseline_label: str,
) -> list[str]:
    """Per-class coverage-delta lines (``current | previous | Δ``)."""
    lines = [
        "## Coverage delta",
        "",
        f"- baseline: {baseline_label}",
        "",
        "| class | metric | current | previous | delta | trend |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for class_name, metric, cur, prev, delta, trend in coverage_delta_rows(
        current=current, previous=previous
    ):
        lines.append(
            f"| {class_name} | {metric} | {cur} | {prev} | {delta} | {trend} |"
        )
    return lines


def parse_previous_snapshot(text: str) -> dict[str, str]:
    """Findings snapshot (hash -> case_id) embedded in a rendered report.

    Missing markers or unparseable payloads yield ``{}`` (all current
    findings read as new), mirroring the no-baseline findings-deltas path.
    Deterministic.
    """
    match = SNAPSHOT_RE.search(text)
    if not match:
        return {}
    try:
        snapshot = json.loads(match.group(1))
    except ValueError:
        return {}
    if not isinstance(snapshot, dict):
        return {}
    return {
        key: value
        for key, value in snapshot.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def drill_down_diff_rows(
    *,
    current: dict[str, str],
    previous: dict[str, str],
) -> list[tuple[str, str, str]]:
    """Drill-down diff rows ``(dedupe_hash, change, case_id)``.

    ``new`` rows (in current, absent previous) sort first by hash, then
    ``resolved`` rows (in previous, absent current) by hash. Total and
    input-order independent, so reruns are byte-identical.
    """
    rows: list[tuple[str, str, str]] = []
    for digest in sorted(h for h in current if h not in previous):
        rows.append((digest, "new", current[digest]))
    for digest in sorted(h for h in previous if h not in current):
        rows.append((digest, "resolved", previous[digest]))
    return rows


def render_drill_down_diff(
    *,
    current: dict[str, str],
    previous: dict[str, str],
    baseline_label: str,
) -> list[str]:
    """Drill-down diff lines (snapshot ``new`` vs ``resolved`` by hash)."""
    rows = drill_down_diff_rows(current=current, previous=previous)
    new_count = sum(1 for _, change, _ in rows if change == "new")
    resolved_count = len(rows) - new_count
    lines = [
        "## Drill-down diff",
        "",
        f"- baseline: {baseline_label}",
        "",
        f"- new: {new_count}, resolved: {resolved_count}",
        "",
        "| dedupe_hash | change | case_id |",
        "| --- | --- | --- |",
    ]
    for digest, change, case_id in rows:
        lines.append(f"| {digest} | {change} | {case_id} |")
    return lines


def _coverage_summary(
    *,
    covered: int,
    total: int,
) -> str:
    """Aggregate coverage cell (``X/Y covered``)."""
    return f"{covered}/{total} covered"


def render_side_by_side(
    *,
    current_runs: int,
    previous_runs: int,
    current_pass: int,
    previous_pass: int,
    current_coverage: str,
    previous_coverage: str,
    current_findings: int,
    previous_findings: int,
    baseline_label: str,
) -> list[str]:
    """Two-column side-by-side comparison lines (current vs previous)."""
    return [
        "## Side-by-side comparison",
        "",
        f"- baseline: {baseline_label}",
        "",
        "| metric | current | previous |",
        "| --- | --- | --- |",
        f"| runs | {current_runs} | {previous_runs} |",
        f"| pass rate | {current_pass}% | {previous_pass}% |",
        f"| coverage | {current_coverage} | {previous_coverage} |",
        f"| findings | {current_findings} | {previous_findings} |",
    ]


#: Column order for the CSV coverage export (K12). Fixed so reruns are
#: byte-identical; per-class rows follow coverage order and the final
#: ``risk-weighted-score`` row carries the aggregate score.
CSV_COLUMNS = (
    "class",
    "cases",
    "runs",
    "wins",
    "pct",
    "gaps",
    "verdict",
    "tier",
    "weight",
    "weighted",
)


def weighted_gaps(
    coverage: list[tuple[str, int, int, int, float, int, str]],
) -> list[tuple[str, int]]:
    """``(class, weighted_points_at_risk)`` per class, sorted by risk.

    Points at risk are ``weight * ((cases - wins) + gaps)`` where
    ``weight`` comes from the class risk tier
    (:data:`CLASS_RISK_TIER`, default ``"low"``): every uncovered case
    and every detection gap counts once, scaled by tier. Sorted by
    descending points, then class name ascending (deterministic).
    """
    scored: list[tuple[str, int]] = []
    for class_name, case_count, _runs, wins, _pct, class_gaps, _verdict in coverage:
        tier = CLASS_RISK_TIER.get(class_name, DEFAULT_CLASS_RISK_TIER)
        weight = RISK_SEVERITY_WEIGHTS[tier]
        scored.append((class_name, weight * ((case_count - wins) + class_gaps)))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def render_exec_summary(
    *,
    runs: int,
    pass_rows: list[tuple[str, str]],
    coverage: list[tuple[str, int, int, int, float, int, str]],
    new_count: int,
    rescinded_count: int,
    previous_runs: int | None = None,
    previous_pass: int | None = None,
) -> list[str]:
    """Executive-summary lines for ``--exec-summary`` (K10).

    Three to six deterministic bullets derived from the report data:
    runs, pass rate vs previous, risk-weighted score, top risk classes
    by weighted gaps, and the findings trend arrow. ``previous_*`` are
    ``None`` when no baseline report exists (the pass-rate bullet then
    notes ``no baseline`` instead of a delta).
    """
    current_pass = _summary_pct(pass_rows)
    score, earned, possible, _breakdown = risk_weighted_score(coverage)
    if previous_pass is None:
        pass_bullet = f"- pass rate: {current_pass}% (no baseline)"
    else:
        delta = current_pass - previous_pass
        pass_bullet = (
            f"- pass rate: {current_pass}% (previous {previous_pass}%, "
            f"{delta:+d}pp {_comparison_arrow(delta)})"
        )
    ranked = [(name, points) for name, points in weighted_gaps(coverage) if points > 0][
        :3
    ]
    if ranked:
        top_text = ", ".join(
            f"{name} ({points} weighted points)" for name, points in ranked
        )
        risk_bullet = f"- top risk classes: {top_text}"
    else:
        risk_bullet = "- top risk classes: none (all classes fully covered)"
    net = new_count - rescinded_count
    return [
        "## Executive summary",
        "",
        f"- runs in window: {runs}",
        pass_bullet,
        f"- risk-weighted score: {score:.1f}% ({earned}/{possible} weighted points)",
        risk_bullet,
        f"- findings: +{new_count} new / -{rescinded_count} rescinded "
        f"({_comparison_arrow(net)})",
    ]


def render_email_block(
    *,
    recipient: str,
    window_start: datetime,
    window_end: datetime,
    runs: int,
    pass_pct: int,
    previous_pass: int | None,
    score: float,
    earned: int,
    possible: int,
    new_count: int,
    rescinded_count: int,
    file_name: str,
) -> list[str]:
    """Copy-paste email block for ``--exec-summary --recipient`` (K10).

    A ``Subject:`` line plus a short plain-text body; the whole block
    stays under 2000 characters by construction (one line per metric,
    no per-class detail beyond the file name pointer).
    """
    if previous_pass is None:
        pass_text = f"{pass_pct}% (no baseline)"
    else:
        delta = pass_pct - previous_pass
        pass_text = (
            f"{pass_pct}% (previous {previous_pass}%, "
            f"{delta:+d}pp {_comparison_arrow(delta)})"
        )
    net = new_count - rescinded_count
    subject = (
        f"Subject: Engagement report "
        f"{window_start.date().isoformat()}..{window_end.date().isoformat()}: "
        f"{runs} runs, {pass_pct}% pass, risk {score:.1f}%"
    )
    return [
        "## Email summary",
        "",
        subject,
        "",
        f"To: {recipient}",
        "",
        "Hi,",
        "",
        f"- window: {fmt_rfc3339(window_start)}..{fmt_rfc3339(window_end)}",
        f"- runs: {runs}",
        f"- pass rate: {pass_text}",
        f"- risk-weighted score: {score:.1f}% ({earned}/{possible} weighted points)",
        f"- findings: +{new_count} new / -{rescinded_count} rescinded "
        f"({_comparison_arrow(net)})",
        f"- full report: {file_name}",
    ]


def coverage_csv_rows(
    coverage: list[tuple[str, int, int, int, float, int, str]],
) -> list[list[str]]:
    """CSV data rows for ``--export-csv`` (K12, without the header).

    One row per class in coverage order plus a final
    ``risk-weighted-score`` row carrying the aggregate score as
    ``pct`` and ``earned/possible`` as ``weighted``. All cells are
    strings; quoting is left to :func:`write_csv_report`.
    """
    score, earned, possible, breakdown = risk_weighted_score(coverage)
    by_class = {row[0]: row for row in coverage}
    rows: list[list[str]] = []
    for class_name, tier, weight, case_count, wins, got, maximum in breakdown:
        _name, _cases, class_runs, _wins, pct, class_gaps, verdict = by_class[
            class_name
        ]
        rows.append(
            [
                class_name,
                str(case_count),
                str(class_runs),
                str(wins),
                f"{pct:.1f}",
                str(class_gaps),
                verdict,
                tier,
                str(weight),
                f"{got}/{maximum}",
            ]
        )
    total_cases = sum(row[1] for row in coverage)
    total_runs = sum(row[2] for row in coverage)
    total_wins = sum(row[3] for row in coverage)
    total_gaps = sum(row[5] for row in coverage)
    rows.append(
        [
            "risk-weighted-score",
            str(total_cases),
            str(total_runs),
            str(total_wins),
            f"{score:.1f}",
            str(total_gaps),
            "",
            "",
            "",
            f"{earned}/{possible}",
        ]
    )
    return rows


def write_csv_report(
    coverage: list[tuple[str, int, int, int, float, int, str]],
    csv_path: Path,
) -> int:
    """Write the coverage matrix as CSV at ``csv_path`` (K12).

    Columns follow :data:`CSV_COLUMNS` in order with ``csv``-module
    quoting (minimal) and ``\\n`` line endings. Returns 0 on success;
    an unwritable output prints ``error:`` to stderr and returns 1.
    """
    try:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writerow(list(CSV_COLUMNS))
            writer.writerows(coverage_csv_rows(coverage))
    except OSError as exc:
        print(f"error: cannot write CSV export {csv_path} ({exc})", file=sys.stderr)
        return 1
    return 0


#: Block elements for ASCII sparklines (U+2581..U+2588, ascending).
SPARKLINE_BLOCKS = ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")

#: Trend-history keys accepted for the per-run pass rate (first hit wins).
PASS_RATE_KEYS = ("pass_rate", "pass_pct", "pass")

#: Trend-history keys accepted for the per-run findings count.
FINDINGS_COUNT_KEYS = ("findings", "findings_count", "count")


def sparkline(values: list[float]) -> str:
    """ASCII sparkline over ``values`` in file order (deterministic).

    Each value maps to one of :data:`SPARKLINE_BLOCKS` by its relative
    position between the min and max; a flat series renders the middle
    block. An empty series renders ``""``.
    """
    if not values:
        return ""
    low = min(values)
    high = max(values)
    if high == low:
        return SPARKLINE_BLOCKS[len(SPARKLINE_BLOCKS) // 2] * len(values)
    span = high - low
    top = len(SPARKLINE_BLOCKS) - 1
    return "".join(
        SPARKLINE_BLOCKS[round((value - low) / span * top)] for value in values
    )


def _trend_number(row: dict[str, object], keys: tuple[str, ...]) -> float | None:
    """First numeric value under ``keys`` (``"80%"`` strings accepted)."""
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            text = value.strip().removesuffix("%").strip()
            try:
                return float(text)
            except ValueError:
                continue
    return None


def load_trend_history(path: Path) -> tuple[list[float], list[int]]:
    """Per-run ``(pass_rates, findings_counts)`` from a trend NDJSON file.

    Same line format as the findings history: one JSON object per line
    (blank lines skipped). Each row carries a pass rate in percentage
    points (``pass_rate``, ``pass_pct``, or ``pass``) and a findings
    count (``findings``, ``findings_count``, or ``count``). File order
    is the run order. Rows persisted by ``--annotate`` (``{"kind":
    "note", ...}``) carry no metrics and are skipped gracefully so
    operator notes never break trend parsing. Fail closed on
    unreadable files and corrupt rows.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"error: cannot read --trend history {path} ({exc})") from exc
    pass_rates: list[float] = []
    findings_counts: list[int] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ReportError(
                f"error: corrupt trend row: {path}:{lineno} ({exc})"
            ) from exc
        if not isinstance(row, dict):
            raise ReportError(f"error: corrupt trend row: {path}:{lineno}")
        if row.get("kind") == "note":
            continue
        rate = _trend_number(row, PASS_RATE_KEYS)
        count = _trend_number(row, FINDINGS_COUNT_KEYS)
        if rate is None or count is None:
            raise ReportError(f"error: corrupt trend row: {path}:{lineno}")
        pass_rates.append(rate)
        findings_counts.append(int(count))
    return pass_rates, findings_counts


def render_trend(
    *,
    pass_rates: list[float],
    findings_counts: list[int],
    source_label: str,
) -> list[str]:
    """Historical trend lines (per-run sparklines in file order)."""
    lines = [
        "## Trend",
        "",
        f"- source: {source_label}",
        "",
        f"- runs: {len(pass_rates)}",
    ]
    if not pass_rates:
        lines.append("- no trend history")
        return lines
    rate_text = ", ".join(f"{rate:.1f}%" for rate in pass_rates)
    count_text = ", ".join(str(count) for count in findings_counts)
    lines += [
        f"- pass rate: {rate_text}",
        f"- pass-rate trend: {sparkline(pass_rates)}",
        f"- findings: {count_text}",
        f"- findings trend: {sparkline([float(n) for n in findings_counts])}",
    ]
    return lines


def parse_annotation(text: str) -> tuple[str, str]:
    """``(case_id, note)`` from one ``--annotate CASE_ID=NOTE`` value.

    Split on the first ``=``; the case id is stripped and must be
    non-empty, the note is stripped (and may be empty). Fail closed on
    values without ``=`` so typos never pass silently.
    """
    case_id, sep, note = text.partition("=")
    if not sep or not case_id.strip():
        raise ReportError(
            f"error: malformed --annotate {text!r} (expected CASE_ID=NOTE)"
        )
    return case_id.strip(), note.strip()


def render_operator_notes(
    notes: list[tuple[str, str]],
    known_case_ids: set[str],
) -> list[str]:
    """Operator-notes section lines (CLI order, deterministic).

    One ``| case_id | note |`` row per note; notes whose case id
    appears nowhere in this report keep their row but gain a
    ``(no matching case in this report)`` flag on the note text.
    """
    lines = [
        "## Operator notes",
        "",
        "| case_id | note |",
        "| --- | --- |",
    ]
    for case_id, note in notes:
        cell = note
        if case_id not in known_case_ids:
            cell = f"{note} (no matching case in this report)".strip()
        lines.append(f"| {case_id} | {cell} |")
    return lines


def load_trend_notes(path: Path) -> list[tuple[str, str]]:
    """``(case_id, note)`` rows persisted in a trend NDJSON file.

    Only ``{"kind": "note", ...}`` rows are collected (file order);
    metric rows and blank lines are skipped gracefully. Fail closed on
    unreadable files and corrupt JSON, mirroring
    :func:`load_trend_history`. Malformed note rows (missing or
    non-string ``case_id``/``note``) are skipped, never fatal.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"error: cannot read --trend history {path} ({exc})") from exc
    notes: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ReportError(
                f"error: corrupt trend row: {path}:{lineno} ({exc})"
            ) from exc
        if not isinstance(row, dict):
            raise ReportError(f"error: corrupt trend row: {path}:{lineno}")
        if row.get("kind") != "note":
            continue
        case_id = row.get("case_id")
        note = row.get("note")
        if not isinstance(case_id, str) or not case_id.strip():
            continue
        if not isinstance(note, str):
            continue
        notes.append((case_id.strip(), note.strip()))
    return notes


def persist_trend_notes(path: Path, notes: list[tuple[str, str]]) -> None:
    """Append ``--annotate`` notes to the trend history NDJSON file.

    One ``{"kind": "note", "case_id": ..., "note": ...}`` line per
    note (CLI order) so future ``--trend`` reports can show them.
    Fail closed when the file cannot be appended to.
    """
    try:
        with path.open("a", encoding="utf-8") as handle:
            for case_id, note in notes:
                handle.write(
                    json.dumps(
                        {"case_id": case_id, "kind": "note", "note": note},
                        sort_keys=True,
                    )
                    + "\n"
                )
    except OSError as exc:
        raise ReportError(
            f"error: cannot persist notes to --trend history {path} ({exc})"
        ) from exc


def merge_notes(
    history_notes: list[tuple[str, str]],
    cli_notes: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """History notes plus CLI notes, exact duplicates removed (stable)."""
    return list(dict.fromkeys(history_notes + list(cli_notes)))


#: Severity colors for the self-contained HTML export (G10). The
#: markdown report itself carries no color, so the export defines this
#: canonical palette once in its inline ``<style>`` block (no external
#: assets).
SEVERITY_COLORS = {
    "critical": "#c0392b",
    "high": "#d35400",
    "medium": "#7d6608",
    "moderate": "#7d6608",
    "low": "#1e8449",
    "unknown": "#616a6b",
}

#: Whole-word severity tokens wrapped in ``sev-*`` spans by the HTML
#: export (report severities render lowercase, so matching stays
#: case-sensitive and deterministic).
_SEVERITY_RE = re.compile(r"\b(critical|high|medium|moderate|low|unknown)\b")

#: Whole-word coverage verdicts wrapped in ``verdict-*`` spans.
_VERDICT_RE = re.compile(r"\b(covered|gappy|unexercised)\b")


def _html_escape(text: str) -> str:
    """Escape ``text`` for HTML body content (deterministic)."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_inline(text: str) -> str:
    """Inline markup for one block line: ```code`` spans plus severity colors.

    Backtick spans become ``<code>`` (severity coloring does not apply
    inside them); elsewhere exact severity/verdict words become
    ``sev-*``/``verdict-*`` spans styled by the inline stylesheet.
    Deterministic single pass.
    """
    parts = re.split(r"(`[^`]*`)", text)
    out: list[str] = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            out.append("<code>" + _html_escape(part[1:-1]) + "</code>")
        else:
            escaped = _html_escape(part)
            escaped = _SEVERITY_RE.sub(
                lambda match: (
                    f'<span class="sev-{match.group(1)}">{match.group(1)}</span>'
                ),
                escaped,
            )
            escaped = _VERDICT_RE.sub(
                lambda match: (
                    f'<span class="verdict-{match.group(1)}">{match.group(1)}</span>'
                ),
                escaped,
            )
            out.append(escaped)
    return "".join(out)


def _html_table_row(cells: list[str], header: bool) -> str:
    """One ``<tr>`` with inline markup applied per cell."""
    tag = "th" if header else "td"
    return (
        "<tr>"
        + "".join(f"<{tag}>{_html_inline(cell)}</{tag}>" for cell in cells)
        + "</tr>"
    )


def _html_table(lines: list[str]) -> str:
    """Render consecutive ``|``-prefixed markdown lines as one table."""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines
    ]
    body: list[str] = []
    data = rows
    if len(rows) >= 2 and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in rows[1]
    ):
        body += ["<thead>", _html_table_row(rows[0], True), "</thead>"]
        data = rows[2:]
    if data:
        body += (
            ["<tbody>"] + [_html_table_row(row, False) for row in data] + ["</tbody>"]
        )
    return "<table>\n" + "\n".join(body) + "\n</table>"


#: Lowercased header cells identifying the coverage-matrix table in
#: markdown (the HTML export anchors this table's class cells).
_COVERAGE_MATRIX_HEADER = ("class", "cases", "runs", "pass rate", "gaps", "verdict")


def _split_table_cells(line: str) -> list[str]:
    """Cell texts of one markdown table line (outer pipes stripped)."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _coverage_slug(class_name: str) -> str:
    """URL-fragment slug for a coverage class (deterministic)."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", class_name.strip()).strip("-")
    return slug or "class"


def _is_coverage_matrix_table(lines: list[str]) -> bool:
    """True when ``lines`` open with the coverage-matrix header row."""
    if not lines:
        return False
    return [cell.lower() for cell in _split_table_cells(lines[0])] == list(
        _COVERAGE_MATRIX_HEADER
    )


def _parse_coverage_matrix(
    markdown: str,
) -> list[tuple[str, int, int, int, int, str]]:
    """``(class, cases, runs, wins, gaps, verdict)`` coverage-matrix rows.

    Parsed back from the rendered markdown table (wins come from the
    ``w/r (pct%)`` pass-rate cell) in table order, so the HTML export
    needs no extra inputs. Unparseable rows are skipped, never fatal.
    """
    rows: list[tuple[str, int, int, int, int, str]] = []
    in_matrix = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_matrix:
                break
            continue
        cells = _split_table_cells(stripped)
        if not in_matrix:
            if [cell.lower() for cell in cells] == list(_COVERAGE_MATRIX_HEADER):
                in_matrix = True
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if len(cells) != len(_COVERAGE_MATRIX_HEADER):
            continue
        try:
            cases = int(cells[1])
            runs = int(cells[2])
            gaps = int(cells[4])
        except ValueError:
            continue
        match = re.match(r"(\d+)\s*/\s*(\d+)", cells[3])
        if not match:
            continue
        rows.append((cells[0], cases, runs, int(match.group(1)), gaps, cells[5]))
    return rows


def _html_coverage_table(lines: list[str]) -> str:
    """Coverage-matrix table with class cells linking to the drill-down.

    Body rows carry ``coverage-row-<slug>`` ids and their class cell
    links to the matching ``coverage-<slug>`` drill-down heading. Only
    internal fragment links are emitted (no external assets).
    """
    rows = [_split_table_cells(line) for line in lines]
    body: list[str] = []
    data = rows
    if len(rows) >= 2 and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in rows[1]
    ):
        body += ["<thead>", _html_table_row(rows[0], True), "</thead>"]
        data = rows[2:]
    if data:
        rendered: list[str] = []
        for row in data:
            if len(row) == len(_COVERAGE_MATRIX_HEADER) and row[0]:
                slug = _coverage_slug(row[0])
                first = (
                    f'<td><a href="#coverage-{slug}">{_html_inline(row[0])}</a></td>'
                )
                rest = "".join(f"<td>{_html_inline(cell)}</td>" for cell in row[1:])
                rendered.append(f'<tr id="coverage-row-{slug}">{first}{rest}</tr>')
            else:
                rendered.append(_html_table_row(row, False))
        body += ["<tbody>"] + rendered + ["</tbody>"]
    return "<table>\n" + "\n".join(body) + "\n</table>"


def coverage_gap_case_ids(
    cases: list[tuple[str, str, str, str]],
    gaps: list[str],
) -> dict[str, list[str]]:
    """Sorted gap case ids per technique class (L12 evidence source).

    Attribution mirrors :func:`coverage_rows`: a quoted ``'case_id'``
    (or ``*.yaml`` stem) token from :func:`_gap_case_ids` counts toward
    its known case's class, else toward the first schema/sighting class
    it names by ``class-`` prefix. Drift/global errors attribute to no
    class. Deterministic (sorted ids per class).
    """
    by_class: dict[str, list[str]] = {}
    for case_id, _expected, _rationale, case_class in cases:
        by_class.setdefault(case_class, []).append(case_id)
    known = {case_id: case_class for case_id, _, _, case_class in cases}
    found: dict[str, set[str]] = {}
    for failure in gaps:
        for token in _gap_case_ids(failure):
            target: str | None = None
            if token in known:
                target = known[token]
            else:
                for class_name in list(SCHEMA_CLASSES) + sorted(by_class):
                    if token == class_name or token.startswith(class_name + "-"):
                        target = class_name
                        break
            if target is not None:
                found.setdefault(target, set()).add(token)
    return {class_name: sorted(ids) for class_name, ids in found.items()}


def findings_titles_by_case(
    findings: list[dict[str, object]],
) -> dict[str, list[str]]:
    """Sorted non-empty finding ``title`` values keyed by ``case_id`` (L12).

    Rows without a string ``case_id`` or without a non-blank string
    ``title`` contribute nothing, so classes with title-less findings
    render the ``no associated findings`` note. Deterministic.
    """
    titles: dict[str, set[str]] = {}
    for row in findings:
        if not isinstance(row, dict):
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            continue
        title = row.get("title")
        if isinstance(title, str) and title.strip():
            titles.setdefault(case_id, set()).add(title.strip())
    return {case_id: sorted(values) for case_id, values in titles.items()}


def _markdown_gap_ids(
    markdown: str,
    classes: list[str],
) -> dict[str, list[str]]:
    """Gap case ids per class parsed from a rendered report (L12 fallback).

    Used when the HTML export only has the markdown text (no loaded
    cases/findings): ``## Detection gaps`` bullets are mined with
    :func:`_gap_case_ids` and attributed to the first matrix class they
    name (exact or ``class-`` prefix). The ``- none`` empty line yields
    no gaps. Deterministic.
    """
    found: dict[str, set[str]] = {}
    for line in _report_section_lines(markdown, "## Detection gaps"):
        stripped = line.strip()
        if not stripped.startswith("- ") or stripped == "- none":
            continue
        for token in _gap_case_ids(stripped[2:]):
            for class_name in classes:
                if token == class_name or token.startswith(class_name + "-"):
                    found.setdefault(class_name, set()).add(token)
                    break
    return {class_name: sorted(ids) for class_name, ids in found.items()}


def _gap_slug(case_id: str) -> str:
    """URL-fragment id for one gap's evidence detail (deterministic)."""
    return "gap-" + _coverage_slug(case_id)


def _html_coverage_drill_down(
    rows: list[tuple[str, int, int, int, int, str]],
    gaps_by_class: dict[str, list[str]] | None = None,
    findings_by_case: dict[str, list[str]] | None = None,
) -> str:
    """Per-class drill-down section after the coverage matrix.

    One anchored ``<h3>`` plus a small metric/value table per class
    (cases, runs, wins, gaps, verdict) in matrix order, followed by the
    per-class gap list: each gap case id links to a collapsible
    ``<details>`` evidence block (case id plus associated finding
    titles, or a ``no associated findings`` note) under the class
    table. Deterministic; stdlib only, no external assets (only
    internal ``#gap-*`` fragment links).
    """
    gap_ids = gaps_by_class if gaps_by_class is not None else {}
    evidence = findings_by_case if findings_by_case is not None else {}
    parts = ["<h2>Coverage drill-down</h2>"]
    for class_name, cases, runs, wins, gaps, verdict in rows:
        slug = _coverage_slug(class_name)
        parts.append(f'<h3 id="coverage-{slug}">{_html_inline(class_name)}</h3>')
        parts.append(
            "<table>\n<thead>\n"
            + _html_table_row(["metric", "value"], True)
            + "\n</thead>\n<tbody>"
        )
        for metric, value in (
            ("cases", str(cases)),
            ("runs", str(runs)),
            ("wins", str(wins)),
            ("gaps", str(gaps)),
        ):
            parts.append(f"<tr><td>{metric}</td><td>{value}</td></tr>")
        parts.append(f"<tr><td>verdict</td><td>{_html_inline(verdict)}</td></tr>")
        parts.append("</tbody>\n</table>")
        class_gaps = gap_ids.get(class_name, [])
        if not class_gaps:
            parts.append("<p>no gaps</p>")
            continue
        parts.append("<ul>")
        for case_id in class_gaps:
            parts.append(
                f'<li><a href="#{_gap_slug(case_id)}">{_html_inline(case_id)}</a></li>'
            )
        parts.append("</ul>")
        for case_id in class_gaps:
            titles = evidence.get(case_id, [])
            parts.append(f'<details id="{_gap_slug(case_id)}">')
            parts.append(f"<summary>{_html_inline(case_id)}</summary>")
            parts.append(f"<p>case id: {_html_inline(case_id)}</p>")
            if titles:
                joined = "; ".join(_html_escape(title) for title in titles)
                parts.append(f"<p>findings: {joined}</p>")
            else:
                parts.append("<p>no associated findings</p>")
            parts.append("</details>")
    return "\n".join(parts)


def _html_stylesheet() -> str:
    """Inline CSS for the HTML export (severity colors + tables)."""
    rules = [
        "body{font-family:sans-serif;max-width:72rem;margin:2rem auto;"
        "padding:0 1rem;color:#111;background:#fff}",
        "table{border-collapse:collapse;margin:1rem 0}",
        "th,td{border:1px solid #999;padding:.25rem .5rem;text-align:left}",
        "th{background:#eee}",
        "code{background:#f4f4f4;padding:0 .25rem}",
        "pre{background:#f4f4f4;padding:1rem;overflow-x:auto}",
        "pre code{background:none;padding:0}",
    ]
    for severity, color in SEVERITY_COLORS.items():
        rules.append(f".sev-{severity}{{color:{color};font-weight:bold}}")
    rules += [
        ".risk-badge{display:inline-block;border:2px solid currentColor;"
        "border-radius:.5rem;padding:.15rem .6rem;margin:.25rem 0 "
        ".5rem .75rem;font-weight:bold;vertical-align:middle}",
        ".verdict-covered{color:#1e8449;font-weight:bold}",
        ".verdict-gappy{color:#c0392b;font-weight:bold}",
        ".verdict-unexercised{color:#616a6b;font-weight:bold}",
    ]
    return "\n".join(rules)


def risk_score_badge(markdown: str) -> str:
    """``<span>`` risk badge for the HTML export (``""`` without a matrix).

    Reuses :func:`risk_weighted_score` over the parsed coverage matrix
    and colors the badge with the severity color of the score tier
    (:func:`risk_score_tier`) via the shared ``sev-*`` stylesheet
    classes. Markdown output is unchanged (HTML-only decoration).
    """
    rows = _parse_coverage_matrix(markdown)
    if not rows:
        return ""
    coverage = [
        (
            class_name,
            cases,
            runs,
            wins,
            (100.0 * wins / runs if runs else 0.0),
            gaps,
            verdict,
        )
        for class_name, cases, runs, wins, gaps, verdict in rows
    ]
    score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
    tier = risk_score_tier(score)
    return f'<span class="risk-badge sev-{tier}">Risk score: {score:.1f} / 100</span>'


def render_html_report(
    markdown: str,
    gaps_by_class: dict[str, list[str]] | None = None,
    findings_by_case: dict[str, list[str]] | None = None,
) -> str:
    """Render the full markdown report as self-contained HTML (stdlib only).

    Minimal deterministic converter: ``#``/``##``/``###`` headings,
    pipe tables (second-row ``---`` separator becomes ``<thead>``),
    fenced code blocks, ``-`` bullet lists, inline ```code`` spans, and
    severity/verdict color spans. Everything else becomes ``<p>``.
    Output carries an inline ``<style>`` block and no external assets
    (the only ``href`` values emitted are internal ``#coverage-*`` and
    ``#gap-*`` fragment links). The findings-snapshot ``<!-- ... -->``
    marker passes through verbatim. The coverage-matrix table's class
    cells link to a per-class drill-down section (one anchored
    metric/value table per class: cases, runs, wins, gaps, verdict,
    plus the gap list with evidence ``<details>`` blocks) emitted
    right after the matrix. A risk-score badge
    (:func:`risk_score_badge`, colored with the severity color of its
    tier) is emitted next to the first ``<h1>``. When
    ``gaps_by_class``/``findings_by_case`` are omitted, gap ids are
    mined from the ``## Detection gaps`` section
    (:func:`_markdown_gap_ids`) with no finding titles. Input-order
    independent for identical inputs (byte-identical reruns).
    """
    title = "Engagement report"
    for line in markdown.splitlines():
        if line.startswith("# "):
            title = line[2:].strip() or title
            break
    body: list[str] = []
    table_buf: list[str] = []
    in_list = False
    in_code = False
    coverage_detail = _parse_coverage_matrix(markdown)
    coverage_emitted = False
    badge = risk_score_badge(markdown)
    badge_emitted = False
    drill_gaps = gaps_by_class
    if drill_gaps is None:
        drill_gaps = _markdown_gap_ids(
            markdown, [class_name for class_name, _, _, _, _, _ in coverage_detail]
        )
    drill_evidence = findings_by_case if findings_by_case is not None else {}

    def flush_table() -> None:
        nonlocal coverage_emitted
        if table_buf:
            if not coverage_emitted and _is_coverage_matrix_table(table_buf):
                body.append(_html_coverage_table(table_buf))
                if coverage_detail:
                    body.append(
                        _html_coverage_drill_down(
                            coverage_detail, drill_gaps, drill_evidence
                        )
                    )
                coverage_emitted = True
            else:
                body.append(_html_table(table_buf))
            table_buf.clear()

    def flush_list() -> None:
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_table()
            flush_list()
            in_code = not in_code
            body.append("<pre><code>" if in_code else "</code></pre>")
            continue
        if in_code:
            body.append(_html_escape(line))
            continue
        if stripped.startswith("|"):
            flush_list()
            table_buf.append(line)
            continue
        flush_table()
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_list()
            level = len(heading.group(1))
            body.append(f"<h{level}>{_html_inline(heading.group(2))}</h{level}>")
            if level == 1 and not badge_emitted:
                if badge:
                    body.append(badge)
                badge_emitted = True
        elif stripped.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_html_inline(stripped[2:])}</li>")
        elif stripped.startswith("<!--") and stripped.endswith("-->"):
            flush_list()
            body.append(stripped)
        elif not stripped:
            flush_list()
        else:
            flush_list()
            body.append(f"<p>{_html_inline(stripped)}</p>")
    flush_table()
    flush_list()
    if in_code:
        body.append("</code></pre>")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{_html_escape(title)}</title>\n"
        "<style>\n"
        f"{_html_stylesheet()}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )


def write_html_report(
    markdown: str,
    html_path: Path,
    gaps_by_class: dict[str, list[str]] | None = None,
    findings_by_case: dict[str, list[str]] | None = None,
) -> int:
    """Write the HTML export of ``markdown`` to ``html_path`` (G10/L12).

    ``gaps_by_class``/``findings_by_case`` feed the per-class gap
    evidence drill-down; when omitted they fall back to the markdown
    mining in :func:`render_html_report`. Returns 0 on success; an
    unwritable output prints ``error:`` to stderr and returns 1 (no
    fallback: HTML needs no external tool).
    """
    try:
        html_path.write_text(
            render_html_report(markdown, gaps_by_class, findings_by_case),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"error: cannot write HTML export {html_path} ({exc})", file=sys.stderr)
        return 1
    return 0


def write_pdf(markdown: str, pdf_path: Path) -> int:
    """Convert ``markdown`` to PDF at ``pdf_path`` via pandoc (thin wrapper).

    Returns 0 on success. When pandoc is unavailable, prints an error to
    stderr and falls back to writing the markdown to the ``.md`` sibling
    of ``pdf_path`` (still returning 0). Pandoc conversion failures and
    unwritable outputs print ``error:`` to stderr and return 1.
    """
    if shutil.which("pandoc") is None:
        fallback = pdf_path.with_suffix(".md")
        try:
            fallback.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write fallback {fallback} ({exc})", file=sys.stderr)
            return 1
        print(
            f"error: pandoc not found; wrote markdown fallback {fallback}",
            file=sys.stderr,
        )
        return 0
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(markdown)
            tmp_name = handle.name
        completed = subprocess.run(
            ["pandoc", "-f", "markdown", "-t", "pdf", "-o", str(pdf_path), tmp_name],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"error: cannot run pandoc ({exc})", file=sys.stderr)
        return 1
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        print(f"error: pandoc conversion failed{suffix}", file=sys.stderr)
        return 1
    return 0


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
    coverage_delta: list[str] | None = None,
    trend: list[str] | None = None,
    drill_down: str | None = None,
    drill_down_rows: list[tuple[str, str, str, str, str]] | None = None,
    drill_down_diff: list[str] | None = None,
    operator_notes: list[str] | None = None,
    side_by_side: list[str] | None = None,
    exec_summary: list[str] | None = None,
    email_block: list[str] | None = None,
    gate_line: str | None = None,
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
    ]
    if exec_summary:
        lines += [*exec_summary, ""]
    lines += [
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
    lines += [
        "",
        *render_risk_weighted_score(coverage if coverage is not None else []),
    ]
    if coverage_delta:
        lines += ["", *coverage_delta]
    if trend:
        lines += ["", *trend]
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
    if drill_down_diff:
        lines += ["", *drill_down_diff]
    if operator_notes:
        lines += ["", *operator_notes]
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
    if side_by_side:
        lines += ["", *side_by_side]
    if email_block:
        lines += ["", *email_block]
    lines += [
        "",
        f"<!-- findings-snapshot: {json.dumps(current_findings, sort_keys=True)} -->",
    ]
    if gate_line is not None:
        lines.append(gate_line)
    lines.append("")
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
    diff_coverage_path: Path | None = None,
    trend_path: Path | None = None,
    diff_drill_down_path: Path | None = None,
    side_by_side_path: Path | None = None,
    annotations: list[tuple[str, str]] | None = None,
    exec_summary: bool = False,
    recipient: str | None = None,
    threshold: str | None = None,
) -> tuple[str, str, Path]:
    """Render the report; return ``(markdown, file_name, out_dir)``.

    ``--compare`` is the combined mode: besides the week-over-week
    section it renders the side-by-side comparison and the drill-down
    diff against the same previous report (equivalent to passing
    ``--side-by-side`` and ``--diff-drill-down`` with that path), plus
    the trend section when ``--trend`` history is also provided. Without
    ``--trend`` the trend is skipped and noted in the comparison
    section. Explicit ``--side-by-side``/``--diff-drill-down`` paths win
    over the ``--compare`` fallback.

    ``annotations`` (``--annotate``) render as an ``## Operator notes``
    section after the drill-down sections; when ``--trend`` history is
    also given, the notes are appended to that file as
    ``{"kind": "note", ...}`` lines and previously persisted notes are
    shown too (exact duplicates removed).

    ``exec_summary`` (``--exec-summary``) renders an ``## Executive
    summary`` section right after the title (before ``## Header``) with
    deterministic bullets over runs, pass rate vs the baseline report,
    the risk-weighted score, top risk classes, and the findings trend.
    ``recipient`` (``--recipient``) additionally renders an ``## Email
    summary`` copy-paste block before the snapshot marker.

    ``threshold`` (``--threshold``) appends the final gate verdict line
    (``Gate: PASS (...)`` or ``Gate: FAIL``) after the snapshot marker;
    callers map a FAIL to exit 3 (see :func:`evaluate_threshold`).
    """
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
            trend_skipped=trend_path is None,
        )
    coverage_delta: list[str] | None = None
    if diff_coverage_path is not None:
        try:
            diff_text = diff_coverage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read --diff-coverage report "
                f"{diff_coverage_path} ({exc})"
            ) from exc
        coverage_delta = render_coverage_delta(
            current=coverage,
            previous=parse_previous_coverage(diff_text),
            baseline_label=diff_coverage_path.name,
        )
    trend: list[str] | None = None
    history_notes: list[tuple[str, str]] = []
    if trend_path is not None:
        history_notes = load_trend_notes(trend_path)
        rates, counts = load_trend_history(trend_path)
        trend = render_trend(
            pass_rates=rates,
            findings_counts=counts,
            source_label=trend_path.name,
        )
        if annotations:
            persist_trend_notes(trend_path, annotations)
    drill_down_source = (
        diff_drill_down_path if diff_drill_down_path is not None else compare_path
    )
    drill_down_flag = (
        "--diff-drill-down" if diff_drill_down_path is not None else "--compare"
    )
    drill_down_diff: list[str] | None = None
    if drill_down_source is not None:
        try:
            drill_text = drill_down_source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read {drill_down_flag} report "
                f"{drill_down_source} ({exc})"
            ) from exc
        drill_down_diff = render_drill_down_diff(
            current=current_findings,
            previous=parse_previous_snapshot(drill_text),
            baseline_label=drill_down_source.name,
        )
    side_by_side_source = (
        side_by_side_path if side_by_side_path is not None else compare_path
    )
    side_by_side_flag = (
        "--side-by-side" if side_by_side_path is not None else "--compare"
    )
    side_by_side: list[str] | None = None
    if side_by_side_source is not None:
        try:
            side_text = side_by_side_source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read {side_by_side_flag} report "
                f"{side_by_side_source} ({exc})"
            ) from exc
        previous_snapshot = parse_previous_snapshot(side_text)
        previous_coverage = parse_previous_coverage(side_text)
        previous_metrics = parse_previous_metrics(side_text)
        covered = sum(1 for row in coverage if row[6] == "covered")
        prev_covered = sum(
            1 for entry in previous_coverage.values() if entry[5] == "covered"
        )
        side_by_side = render_side_by_side(
            current_runs=runs,
            previous_runs=previous_metrics.get("runs", 0),
            current_pass=_summary_pct(pass_rows),
            previous_pass=previous_metrics.get("pass_pct", 0),
            current_coverage=_coverage_summary(covered=covered, total=len(coverage)),
            previous_coverage=_coverage_summary(
                covered=prev_covered, total=len(previous_coverage)
            ),
            current_findings=len(current_findings),
            previous_findings=len(previous_snapshot),
            baseline_label=side_by_side_source.name,
        )
    combined_notes = merge_notes(history_notes, annotations or [])
    known_case_ids = {case_id for case_id, _result in pass_rows}
    known_case_ids |= {
        case_id for case_id in current_findings.values() if isinstance(case_id, str)
    }
    operator_notes = (
        render_operator_notes(combined_notes, known_case_ids)
        if combined_notes
        else None
    )
    exec_summary_lines: list[str] | None = None
    email_lines: list[str] | None = None
    if exec_summary:
        if baseline is None:
            new_count = len(current_findings)
            rescinded_count = 0
        else:
            new_count = sum(1 for h in current_findings if h not in baseline)
            rescinded_count = sum(1 for h in baseline if h not in current_findings)
        baseline_metrics: dict[str, int] | None = None
        if baseline_name is not None:
            baseline_path = out_dir / baseline_name
            try:
                baseline_metrics = parse_previous_metrics(
                    baseline_path.read_text(encoding="utf-8")
                )
            except OSError:
                baseline_metrics = None
        previous_runs = baseline_metrics["runs"] if baseline_metrics else None
        previous_pass = baseline_metrics["pass_pct"] if baseline_metrics else None
        exec_summary_lines = render_exec_summary(
            runs=runs,
            pass_rows=pass_rows,
            coverage=coverage,
            new_count=new_count,
            rescinded_count=rescinded_count,
            previous_runs=previous_runs,
            previous_pass=previous_pass,
        )
        if recipient:
            score, earned, possible, _breakdown = risk_weighted_score(coverage)
            email_lines = render_email_block(
                recipient=recipient,
                window_start=window_start,
                window_end=window_end,
                runs=runs,
                pass_pct=_summary_pct(pass_rows),
                previous_pass=previous_pass,
                score=score,
                earned=earned,
                possible=possible,
                new_count=new_count,
                rescinded_count=rescinded_count,
                file_name=file_name,
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
        coverage_delta=coverage_delta,
        trend=trend,
        drill_down=drill_down,
        drill_down_rows=(
            drill_down_rows(window["findings"], drill_down)
            if drill_down is not None
            else None
        ),
        drill_down_diff=drill_down_diff,
        operator_notes=operator_notes,
        side_by_side=side_by_side,
        exec_summary=exec_summary_lines,
        email_block=email_lines,
        gate_line=(
            str(evaluate_threshold(pass_rows, coverage, threshold)["line"])
            if threshold is not None
            else None
        ),
    )
    return markdown, file_name, out_dir


def _parse_pass_result(result: str) -> tuple[int, int, float | None]:
    """``(wins, total, pct)`` from a ``p/t (pct%)`` pass cell (``n/a`` ⇒ None)."""
    match = re.match(r"(\d+)/(\d+)\s*\(([\d.]+)%\)", result.strip())
    if not match:
        return 0, 0, None
    try:
        pct = float(match.group(3))
    except ValueError:
        return int(match.group(1)), int(match.group(2)), None
    return int(match.group(1)), int(match.group(2)), pct


def build_report_data(
    reports_dir: Path,
    since_text: str | None,
    compare_path: Path | None = None,
    drill_down: str | None = None,
    diff_coverage_path: Path | None = None,
    trend_path: Path | None = None,
    diff_drill_down_path: Path | None = None,
    side_by_side_path: Path | None = None,
    annotations: list[tuple[str, str]] | None = None,
    exec_summary: bool = False,
    recipient: str | None = None,
    threshold: str | None = None,
) -> dict[str, object]:
    """Structured report data with the same inputs as :func:`build_report`.

    Keys are inserted in fixed section order (header first, snapshot
    last) and every list is sorted, so ``json.dumps`` of the result is
    byte-identical across reruns on identical inputs. Read-only apart
    from the harness-version lookup; raises :class:`ReportError` on
    unreadable ``--compare``/``--diff-coverage``/``--diff-drill-down``/
    ``--side-by-side`` inputs. Like :func:`build_report`, ``--compare``
    implies the side-by-side and drill-down-diff objects against the
    same previous report (explicit flags win), and records a trend note
    in the comparison object when no ``--trend`` history is provided.
    """
    window = _collect_window(reports_dir, since_text)
    window_start = window["window_start"]
    window_end = window["window_end"]
    runs = window["runs"]
    cases = window["cases"]
    observations = window["observations"]
    gaps = window["gaps"]
    pass_rows = window["pass_rows"]
    current_findings = window["current_findings"]
    in_window = window["in_window"]
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
    coverage = coverage_rows(cases, observations if runs > 0 else {}, gaps)
    start_day = window_start.date().isoformat().replace(":", "-")
    end_day = window_end.date().isoformat().replace(":", "-")
    file_name = f"engagement-{start_day}_{end_day}.md"
    out_dir = output_dir(reports_dir)
    baseline_name, baseline = newest_baseline(out_dir, file_name)
    if baseline is None:
        new_hashes = sorted(current_findings)
        rescinded: list[str] = []
        no_baseline_note = (
            f"no baseline: {baseline_name or 'no previous report'}; "
            f"treating all {len(current_findings)} findings as new."
        )
    else:
        new_hashes = sorted(h for h in current_findings if h not in baseline)
        rescinded = sorted(h for h in baseline if h not in current_findings)
        no_baseline_note = None
    comparison_data: dict[str, object] | None = None
    if compare_path is not None:
        try:
            previous_text = compare_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read --compare report {compare_path} ({exc})"
            ) from exc
        current_metrics = {
            "runs": runs,
            "pass_pct": _summary_pct(pass_rows),
            "gaps": len(gaps),
            "new": len(new_hashes),
            "rescinded": len(rescinded),
        }
        previous_metrics = parse_previous_metrics(previous_text)
        comparison_data = {
            "baseline": compare_path.name,
            "metrics": [
                {
                    "metric": key,
                    "current": current_metrics[key],
                    "previous": previous_metrics.get(key, 0),
                    "delta": current_metrics[key] - previous_metrics.get(key, 0),
                }
                for key in COMPARISON_METRICS
            ],
        }
        if trend_path is None:
            comparison_data["trend_note"] = (
                "skipped (no --trend history provided with --compare)"
            )
    delta_data: dict[str, object] | None = None
    if diff_coverage_path is not None:
        try:
            diff_text = diff_coverage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read --diff-coverage report "
                f"{diff_coverage_path} ({exc})"
            ) from exc
        delta_data = {
            "baseline": diff_coverage_path.name,
            "rows": [
                {
                    "class": class_name,
                    "metric": metric,
                    "current": cur,
                    "previous": prev,
                    "delta": delta,
                    "trend": trend,
                }
                for class_name, metric, cur, prev, delta, trend in coverage_delta_rows(
                    current=coverage,
                    previous=parse_previous_coverage(diff_text),
                )
            ],
        }
    trend_data: dict[str, object] | None = None
    history_notes: list[tuple[str, str]] = []
    if trend_path is not None:
        history_notes = load_trend_notes(trend_path)
        trend_rates, trend_counts = load_trend_history(trend_path)
        trend_data = {
            "source": trend_path.name,
            "pass_rates": list(trend_rates),
            "findings_counts": list(trend_counts),
            "pass_rate_sparkline": sparkline(trend_rates),
            "findings_sparkline": sparkline([float(n) for n in trend_counts]),
        }
    drill_data: dict[str, object] | None = None
    if drill_down is not None:
        drill_data = {
            "class": drill_down,
            "rows": [
                {
                    "dedupe_hash": digest,
                    "severity": sev,
                    "observed_detection": observed,
                    "timestamp": stamp,
                    "verdict": row_verdict,
                }
                for digest, sev, observed, stamp, row_verdict in drill_down_rows(
                    window["findings"], drill_down
                )
            ],
        }
    data: dict[str, object] = {
        "title": "Engagement report",
        "header": {
            "window_start": fmt_rfc3339(window_start),
            "window_end": fmt_rfc3339(window_end),
            "generated_at": fmt_rfc3339(window_end),
            "harness_version": read_version(REPO_ROOT),
        },
        "runs_summary": {"runs": runs, "empty": runs == 0},
        "pass_rates": [
            {
                "case_id": case_id,
                "result": result,
                "wins": _parse_pass_result(result)[0],
                "total": _parse_pass_result(result)[1],
                "pass_pct": _parse_pass_result(result)[2],
            }
            for case_id, result in pass_rows
        ],
        "coverage": [
            {
                "class": class_name,
                "cases": case_count,
                "runs": class_runs,
                "wins": wins,
                "pass_pct": pct,
                "gaps": class_gaps,
                "verdict": verdict,
            }
            for class_name, case_count, class_runs, wins, pct, class_gaps, verdict in (
                coverage
            )
        ],
        "coverage_delta": delta_data,
        "coverage_drill_down": drill_data,
        "detection_gaps": list(gaps),
        "findings_deltas": {
            "baseline": baseline_name,
            "has_baseline": baseline is not None,
            "no_baseline_note": no_baseline_note,
            "new_count": len(new_hashes),
            "rescinded_count": len(rescinded),
            "new": [
                {"dedupe_hash": digest, "case_id": current_findings[digest]}
                for digest in new_hashes
            ],
            "rescinded": [
                {
                    "dedupe_hash": digest,
                    "case_id": baseline.get(digest, "unknown")
                    if baseline
                    else "unknown",
                }
                for digest in rescinded
            ],
        },
        "state_transitions": [
            {"from": from_state, "to": to_state, "count": count}
            for from_state, to_state, count in window["transition_counts"]
        ],
        "state_timeline": {
            "events": [
                {
                    "timestamp": ts_text,
                    "from": from_state,
                    "to": to_state,
                    "cause": cause,
                }
                for ts_text, from_state, to_state, cause in timeline
            ],
            "cause_counts": dict(sorted(cause_tally.items())),
        },
        "severity_timeline": [
            {
                "dedupe_hash": digest12,
                "severity": sev,
                "class": klass,
                "timestamp": ts_text,
            }
            for digest12, sev, klass, ts_text in window["severity_timeline"]
        ],
        "comparison": comparison_data,
        "snapshot": dict(sorted(current_findings.items())),
    }
    if trend_data is not None:
        data["trend"] = trend_data
    combined_notes = merge_notes(history_notes, annotations or [])
    if combined_notes:
        known_case_ids = {case_id for case_id, _result in pass_rows}
        known_case_ids |= {
            case_id for case_id in current_findings.values() if isinstance(case_id, str)
        }
        data["operator_notes"] = [
            {
                "case_id": case_id,
                "note": note,
                "matched": case_id in known_case_ids,
            }
            for case_id, note in combined_notes
        ]
    drill_down_source = (
        diff_drill_down_path if diff_drill_down_path is not None else compare_path
    )
    drill_down_flag = (
        "--diff-drill-down" if diff_drill_down_path is not None else "--compare"
    )
    if drill_down_source is not None:
        try:
            drill_text = drill_down_source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read {drill_down_flag} report "
                f"{drill_down_source} ({exc})"
            ) from exc
        drill_previous = parse_previous_snapshot(drill_text)
        drill_rows = drill_down_diff_rows(
            current=current_findings, previous=drill_previous
        )
        data["drill_down_diff"] = {
            "baseline": drill_down_source.name,
            "new_count": sum(1 for _, change, _ in drill_rows if change == "new"),
            "resolved_count": sum(
                1 for _, change, _ in drill_rows if change == "resolved"
            ),
            "rows": [
                {"dedupe_hash": digest, "change": change, "case_id": case_id}
                for digest, change, case_id in drill_rows
            ],
        }
    side_by_side_source = (
        side_by_side_path if side_by_side_path is not None else compare_path
    )
    side_by_side_flag = (
        "--side-by-side" if side_by_side_path is not None else "--compare"
    )
    if side_by_side_source is not None:
        try:
            side_text = side_by_side_source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read {side_by_side_flag} report "
                f"{side_by_side_source} ({exc})"
            ) from exc
        side_previous_snapshot = parse_previous_snapshot(side_text)
        side_previous_coverage = parse_previous_coverage(side_text)
        side_previous_metrics = parse_previous_metrics(side_text)
        covered_now = sum(1 for row in coverage if row[6] == "covered")
        covered_prev = sum(
            1 for entry in side_previous_coverage.values() if entry[5] == "covered"
        )
        data["side_by_side"] = {
            "baseline": side_by_side_source.name,
            "metrics": [
                {
                    "metric": "runs",
                    "current": runs,
                    "previous": side_previous_metrics.get("runs", 0),
                },
                {
                    "metric": "pass rate",
                    "current": f"{_summary_pct(pass_rows)}%",
                    "previous": f"{side_previous_metrics.get('pass_pct', 0)}%",
                },
                {
                    "metric": "coverage",
                    "current": _coverage_summary(
                        covered=covered_now, total=len(coverage)
                    ),
                    "previous": _coverage_summary(
                        covered=covered_prev, total=len(side_previous_coverage)
                    ),
                },
                {
                    "metric": "findings",
                    "current": len(current_findings),
                    "previous": len(side_previous_snapshot),
                },
            ],
        }
    if exec_summary:
        start_day = window_start.date().isoformat().replace(":", "-")
        end_day = window_end.date().isoformat().replace(":", "-")
        file_name = f"engagement-{start_day}_{end_day}.md"
        out_dir = output_dir(reports_dir)
        baseline_name, baseline = newest_baseline(out_dir, file_name)
        baseline_metrics: dict[str, int] | None = None
        if baseline_name is not None:
            try:
                baseline_metrics = parse_previous_metrics(
                    (out_dir / baseline_name).read_text(encoding="utf-8")
                )
            except OSError:
                baseline_metrics = None
        previous_pass = baseline_metrics["pass_pct"] if baseline_metrics else None
        previous_runs = baseline_metrics["runs"] if baseline_metrics else None
        if baseline is None:
            new_count = len(current_findings)
            rescinded_count = 0
        else:
            new_count = sum(1 for h in current_findings if h not in baseline)
            rescinded_count = sum(1 for h in baseline if h not in current_findings)
        score, earned, possible, _breakdown = risk_weighted_score(coverage)
        ranked = [
            {"class": name, "weighted_points": points}
            for name, points in weighted_gaps(coverage)
            if points > 0
        ][:3]
        data["exec_summary"] = {
            "runs": runs,
            "pass_pct": _summary_pct(pass_rows),
            "previous_runs": previous_runs,
            "previous_pass_pct": previous_pass,
            "risk_score": score,
            "weighted_earned": earned,
            "weighted_possible": possible,
            "top_risk_classes": ranked,
            "new_count": new_count,
            "rescinded_count": rescinded_count,
            "trend": _comparison_arrow(new_count - rescinded_count),
        }
        if recipient:
            email_lines = render_email_block(
                recipient=recipient,
                window_start=window_start,
                window_end=window_end,
                runs=runs,
                pass_pct=_summary_pct(pass_rows),
                previous_pass=previous_pass,
                score=score,
                earned=earned,
                possible=possible,
                new_count=new_count,
                rescinded_count=rescinded_count,
                file_name=file_name,
            )
            subject = next(line for line in email_lines if line.startswith("Subject:"))
            body = "\n".join(email_lines[email_lines.index(subject) + 1 :]).strip()
            data["email_summary"] = {
                "recipient": recipient,
                "subject": subject,
                "body": body,
            }
    if threshold is not None:
        gate = evaluate_threshold(pass_rows, coverage, threshold)
        data["gate"] = {
            "metric": gate["metric"],
            "value": gate["value"],
            "gate": gate["gate"],
            "passed": gate["passed"],
        }
    return data


def render_json_report(data: dict[str, object]) -> str:
    """Deterministic single-object JSON rendering (fixed key order)."""
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


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


def parse_threshold(spec: str) -> tuple[str, float]:
    """``(metric, gate)`` from a ``--threshold`` value (L10).

    Accepts ``gate:<pct>`` (overall pass rate) and ``gate-risk:<score>``
    (risk-weighted score); both bounds are 0-100 inclusive. Returns the
    report metric name (``pass-rate``/``risk-score``) plus the numeric
    gate. Raises :class:`ReportError` on malformed or out-of-range
    specs (NaN/inf rejected). Deterministic.
    """
    kind, sep, raw = spec.partition(":")
    if sep and kind in ("gate", "gate-risk") and raw.strip():
        try:
            gate = float(raw.strip())
        except ValueError:
            gate = float("nan")
        if math.isfinite(gate) and 0.0 <= gate <= 100.0:
            return ("pass-rate" if kind == "gate" else "risk-score"), gate
    raise ReportError(
        f"error: invalid --threshold {spec!r} "
        "(expected 'gate:<pct>' or 'gate-risk:<score>', 0-100)"
    )


def _format_gate(value: float) -> str:
    """Compact gate rendering (``80`` not ``80.0``; deterministic)."""
    return f"{value:g}"


def evaluate_threshold(
    pass_rows: list[tuple[str, str]],
    coverage: list[tuple[str, int, int, int, float, int, str]],
    spec: str,
) -> dict[str, object]:
    """Gate verdict over the current window (L10).

    Returns ``metric``/``value``/``gate``/``passed`` plus the report
    ``line`` (``Gate: PASS (metric value >= gate)`` or the bare
    ``Gate: FAIL``) and the ``stderr`` detail for failing runs.
    ``value`` is the integer pass percentage for ``gate:`` specs and
    the one-decimal risk-weighted score for ``gate-risk:`` specs.
    """
    metric, gate = parse_threshold(spec)
    if metric == "risk-score":
        score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
        value: float = score
        shown = f"{score:.1f}"
    else:
        value = float(_summary_pct(pass_rows))
        shown = str(int(value))
    passed = value >= gate
    gate_text = _format_gate(gate)
    if passed:
        line = f"Gate: PASS ({metric} {shown} >= {gate_text})"
    else:
        line = "Gate: FAIL"
    return {
        "metric": metric,
        "value": int(value) if metric == "pass-rate" else value,
        "gate": int(gate) if float(gate).is_integer() else gate,
        "passed": passed,
        "line": line,
        "stderr": f"gate: FAIL ({metric} {shown} < {gate_text})",
    }


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
    """Entry point. Returns 0 when the report renders, 1 otherwise (3 on gate FAIL)."""
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
        help="combined comparison against a previous report markdown: "
        "appends the ## Week-over-week comparison section plus the "
        "## Side-by-side comparison and ## Drill-down diff sections "
        "against the same file (as if --side-by-side and "
        "--diff-drill-down were passed with it), and the ## Trend "
        "section when --trend history is also given (otherwise the "
        "skipped trend is noted in the comparison section).",
    )
    parser.add_argument(
        "--diff-coverage",
        default=None,
        metavar="PREVIOUS_REPORT",
        help="previous report markdown to diff coverage against: renders a "
        "## Coverage delta section after the coverage matrix (per-class "
        "cases/runs/pass-rate/gaps/verdict as current | previous | Δ with "
        "an improved/regressed/unchanged trend).",
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
        "--trend",
        default=None,
        metavar="HISTORY_NDJSON",
        help="per-run history NDJSON (one JSON object per line with a pass "
        "rate and a findings count, same line format as findings.ndjson): "
        "renders a ## Trend section with ASCII sparklines after the "
        "coverage delta.",
    )
    parser.add_argument(
        "--diff-drill-down",
        default=None,
        metavar="PREVIOUS_REPORT",
        help="previous report markdown to diff findings snapshots against: "
        "renders a ## Drill-down diff section after the coverage drill-down "
        "(dedupe_hash rows labelled new when only in the current report and "
        "resolved when only in the previous report).",
    )
    parser.add_argument(
        "--side-by-side",
        default=None,
        metavar="PREVIOUS_REPORT",
        help="previous report markdown to compare against: renders a "
        "## Side-by-side comparison section (two-column current vs previous "
        "table over runs, pass rate, coverage, and findings).",
    )
    parser.add_argument(
        "--pdf",
        default=None,
        metavar="OUTPUT_PDF",
        help="also convert the markdown report to PDF at OUTPUT_PDF via "
        "pandoc; when pandoc is unavailable, prints an error to stderr "
        "and falls back to writing the .md sibling instead.",
    )
    parser.add_argument(
        "--export",
        nargs=2,
        default=None,
        metavar=("FORMAT", "OUTPUT"),
        help="also export the markdown report at OUTPUT in FORMAT "
        "(only 'html': self-contained file with an inline <style> block "
        "carrying the severity colors, no external assets; converted "
        "from the markdown with the stdlib-only built-in converter).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full report as a single JSON object (all sections as "
        "structured data, fixed key order) on stdout instead of markdown; "
        "the canonical per-window markdown copy is still kept in the "
        "output directory.",
    )
    parser.add_argument(
        "--annotate",
        action="append",
        default=None,
        metavar="CASE_ID=NOTE",
        help="repeatable operator note (CASE_ID=NOTE): appends an "
        "## Operator notes section (case_id | note table) after the "
        "drill-down sections; notes for case ids absent from the report "
        "are flagged; with --trend the notes persist into the trend "
        "history NDJSON for future reports.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="emit exactly one summary line "
        "(runs=X pass=Y%% gaps=Z findings=+N/-M) on stdout and write no "
        "files; exit 0 with an all-zeros line when no reports exist, "
        "exit 1 with error: on stderr only for genuine I/O or parse failures.",
    )
    parser.add_argument(
        "--exec-summary",
        action="store_true",
        help="render an ## Executive summary section right after the title "
        "(before ## Header) with deterministic bullets over runs, pass "
        "rate vs the baseline report, the risk-weighted score, top risk "
        "classes, and the findings trend.",
    )
    parser.add_argument(
        "--recipient",
        default=None,
        metavar="EMAIL",
        help="with --exec-summary, also render an ## Email summary "
        "copy-paste block (Subject: line plus plain-text body) before the "
        "snapshot marker; ignored without --exec-summary.",
    )
    parser.add_argument(
        "--export-csv",
        default=None,
        metavar="OUTPUT_CSV",
        help="also write the coverage matrix as CSV at OUTPUT_CSV "
        "(class,cases,runs,wins,pct,gaps,verdict,tier,weight,weighted in "
        "coverage order plus a final risk-weighted-score row); stdout "
        "stays markdown.",
    )
    parser.add_argument(
        "--threshold",
        default=None,
        metavar="SPEC",
        help="gate the run on SPEC ('gate:<pct>' over the overall pass "
        "rate, or 'gate-risk:<score>' over the risk-weighted score, "
        "0-100): appends the final 'Gate: PASS (...)'/'Gate: FAIL' line "
        "to the report (a 'gate' object in --json) and exits 3 with a "
        "gate: message on stderr when the value is below the gate; "
        "exit 0 when it meets the gate. Composes with all modes.",
    )
    args = parser.parse_args(argv)

    reports_dir = Path(args.reports_dir) if args.reports_dir else default_reports_dir()
    if args.summary:
        try:
            summary = summarize(reports_dir, args.since)
            summary_gate: dict[str, object] | None = None
            if args.threshold is not None:
                summary_window = _collect_window(reports_dir, args.since)
                summary_runs = summary_window["runs"]
                assert isinstance(summary_runs, int)
                summary_coverage = coverage_rows(
                    summary_window["cases"],  # type: ignore[arg-type]
                    summary_window["observations"] if summary_runs > 0 else {},
                    summary_window["gaps"],  # type: ignore[arg-type]
                )
                summary_gate = evaluate_threshold(
                    summary_window["pass_rows"],  # type: ignore[arg-type]
                    summary_coverage,
                    args.threshold,
                )
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            if summary_gate is not None:
                summary["gate"] = {
                    "metric": summary_gate["metric"],
                    "value": summary_gate["value"],
                    "gate": summary_gate["gate"],
                    "passed": summary_gate["passed"],
                }
            sys.stdout.write(json.dumps(summary, indent=2, sort_keys=False) + "\n")
        else:
            sys.stdout.write(str(summary["line"]) + "\n")
            if summary_gate is not None:
                sys.stdout.write(str(summary_gate["line"]) + "\n")
        if summary_gate is not None and not summary_gate["passed"]:
            print(str(summary_gate["stderr"]), file=sys.stderr)
            return 3
        return 0
    try:
        annotations = (
            [parse_annotation(raw) for raw in args.annotate] if args.annotate else None
        )
        compare_path = Path(args.compare) if args.compare else None
        diff_coverage_path = Path(args.diff_coverage) if args.diff_coverage else None
        trend_path = Path(args.trend) if args.trend else None
        diff_drill_down_path = (
            Path(args.diff_drill_down) if args.diff_drill_down else None
        )
        side_by_side_path = Path(args.side_by_side) if args.side_by_side else None
        markdown, file_name, out_dir = build_report(
            reports_dir,
            args.since,
            compare_path,
            drill_down=args.drill_down,
            diff_coverage_path=diff_coverage_path,
            trend_path=trend_path,
            diff_drill_down_path=diff_drill_down_path,
            side_by_side_path=side_by_side_path,
            annotations=annotations,
            exec_summary=args.exec_summary,
            recipient=args.recipient,
            threshold=args.threshold,
        )
        payload = markdown
        if args.json:
            payload = render_json_report(
                build_report_data(
                    reports_dir,
                    args.since,
                    compare_path,
                    drill_down=args.drill_down,
                    diff_coverage_path=diff_coverage_path,
                    trend_path=trend_path,
                    diff_drill_down_path=diff_drill_down_path,
                    side_by_side_path=side_by_side_path,
                    annotations=annotations,
                    exec_summary=args.exec_summary,
                    recipient=args.recipient,
                    threshold=args.threshold,
                )
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
                out_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            print(
                f"error: cannot write --out parent: {args.out} ({exc})", file=sys.stderr
            )
            return 1
    else:
        sys.stdout.write(payload)
    status = 0
    gate_evidence: dict[str, object] | None = None
    if args.threshold is not None or (
        args.export is not None and args.export[0] == "html"
    ):
        try:
            gate_window = _collect_window(reports_dir, args.since)
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        gate_window_runs = gate_window["runs"]
        assert isinstance(gate_window_runs, int)
        gate_evidence = {
            "pass_rows": gate_window["pass_rows"],
            "coverage": coverage_rows(
                gate_window["cases"],  # type: ignore[arg-type]
                gate_window["observations"] if gate_window_runs > 0 else {},
                gate_window["gaps"],  # type: ignore[arg-type]
            ),
            "cases": gate_window["cases"],
            "gaps": gate_window["gaps"],
            "findings": gate_window["findings"],
        }
    if args.pdf:
        status = write_pdf(markdown, Path(args.pdf)) or status
    if args.export:
        export_format, export_output = args.export
        if export_format != "html":
            print(
                f"error: unsupported --export format {export_format!r} (only 'html')",
                file=sys.stderr,
            )
            return 1
        html_gaps: dict[str, list[str]] | None = None
        html_titles: dict[str, list[str]] | None = None
        if gate_evidence is not None:
            html_gaps = coverage_gap_case_ids(
                gate_evidence["cases"],  # type: ignore[arg-type]
                gate_evidence["gaps"],  # type: ignore[arg-type]
            )
            html_titles = findings_titles_by_case(
                gate_evidence["findings"],  # type: ignore[arg-type]
            )
        status = (
            write_html_report(markdown, Path(export_output), html_gaps, html_titles)
            or status
        )
    if args.export_csv:
        try:
            csv_window = _collect_window(reports_dir, args.since)
            csv_runs = csv_window["runs"]
            csv_coverage = coverage_rows(
                csv_window["cases"],
                csv_window["observations"] if csv_runs > 0 else {},
                csv_window["gaps"],
            )
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        status = write_csv_report(csv_coverage, Path(args.export_csv)) or status
    if args.threshold is not None and gate_evidence is not None:
        gate = evaluate_threshold(
            gate_evidence["pass_rows"],  # type: ignore[arg-type]
            gate_evidence["coverage"],  # type: ignore[arg-type]
            args.threshold,
        )
        if not gate["passed"]:
            print(str(gate["stderr"]), file=sys.stderr)
            return 3
    return status


if __name__ == "__main__":
    raise SystemExit(main())

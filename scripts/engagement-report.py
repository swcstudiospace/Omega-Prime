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
from collections.abc import Sequence
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


#: Trend cell for coverage classes with no runs (no win rate to map).
NO_TREND_GLYPH = "—"


def coverage_trend_glyph(runs: int, pct: float) -> str:
    """One-block win-rate glyph for the coverage-matrix trend column.

    Maps the class win rate (0-100%) across :data:`SPARKLINE_BLOCKS`
    (``▁`` at 0% through ``█`` at 100%); classes with no runs render
    :data:`NO_TREND_GLYPH`. Deterministic, no external dependencies.
    """
    if runs <= 0:
        return NO_TREND_GLYPH
    top = len(SPARKLINE_BLOCKS) - 1
    clamped = min(max(pct, 0.0), 100.0)
    return SPARKLINE_BLOCKS[round(clamped / 100 * top)]


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


#: Metrics shown per row by the three-way trend view (``--delta``), each
#: with whether a higher value counts as improving (findings improve
#: downward; every other metric improves upward).
DELTA_TREND_METRICS = ("runs", "pass rate", "coverage", "findings", "risk score")


def _trend_arrow(current: float, older: float, *, higher_is_better: bool = True) -> str:
    """Net direction arrow for one trend value (``--delta``).

    ``→`` when the values agree within 0.5, otherwise ``↑`` when the
    change counts as improving and ``↓`` when it counts as regressing.
    Deterministic.
    """
    delta = current - older
    if abs(delta) <= 0.5:
        return "→"
    return "↑" if (delta > 0) == higher_is_better else "↓"


def _delta_leg_values(
    metrics: dict[str, int],
    coverage: dict[str, tuple[int, int, int, float, int, str]],
    snapshot: dict[str, str],
) -> tuple[int, int, int, int, int, float]:
    """``(runs, pass_pct, covered, total, findings, risk_score)`` trend leg.

    ``metrics``/``coverage``/``snapshot`` are parsed from one rendered
    report with :func:`parse_previous_metrics`,
    :func:`parse_previous_coverage`, and :func:`parse_previous_snapshot`
    (the same parsers ``--side-by-side``/``--diff-drill-down`` use);
    the risk score is recomputed from the parsed coverage so reports
    predating the score section still yield a value. Deterministic.
    """
    covered = sum(1 for entry in coverage.values() if entry[5] == "covered")
    score, _earned, _possible, _breakdown = risk_weighted_score(
        [(class_name, *values) for class_name, values in sorted(coverage.items())]
    )
    return (
        metrics.get("runs", 0),
        metrics.get("pass_pct", 0),
        covered,
        len(coverage),
        len(snapshot),
        score,
    )


def render_three_way_trend(
    *,
    older_label: str,
    previous_label: str,
    older: tuple[int, int, int, int, int, float],
    previous: tuple[int, int, int, int, int, float],
    current: tuple[int, int, int, int, int, float],
) -> list[str]:
    """Three-way trend lines (``older → previous → current`` per metric).

    Legs are :func:`_delta_leg_values` tuples. Each row carries one net
    arrow from :func:`_trend_arrow` over the older-to-current change
    (``↑`` improving, ``↓`` regressing, ``→`` flat within 0.5).
    Deterministic.
    """
    old_runs, old_pass, old_covered, old_total, old_findings, old_score = older
    prev_runs, prev_pass, prev_covered, prev_total, prev_findings, prev_score = previous
    cur_runs, cur_pass, cur_covered, cur_total, cur_findings, cur_score = current
    return [
        "## Three-way trend",
        "",
        f"- older: {older_label}",
        f"- previous: {previous_label}",
        "",
        f"- runs: {old_runs} → {prev_runs} → {cur_runs} "
        f"({_trend_arrow(float(cur_runs), float(old_runs))})",
        f"- pass rate: {old_pass}% → {prev_pass}% → {cur_pass}% "
        f"({_trend_arrow(float(cur_pass), float(old_pass))})",
        f"- coverage: {old_covered}/{old_total} covered → "
        f"{prev_covered}/{prev_total} covered → "
        f"{cur_covered}/{cur_total} covered "
        f"({_trend_arrow(float(cur_covered), float(old_covered))})",
        f"- findings: {old_findings} → {prev_findings} → {cur_findings} "
        f"({_trend_arrow(float(cur_findings), float(old_findings), higher_is_better=False)})",
        f"- risk-weighted score: {old_score:.1f}% → {prev_score:.1f}% → "
        f"{cur_score:.1f}% ({_trend_arrow(cur_score, old_score)})",
    ]


#: Metrics shown per row by the multi-report trend view
#: (``--compare-multi``): one row per metric, one column per report
#: (oldest to newest).
MULTI_TREND_METRICS = (
    "runs",
    "pass rate",
    "coverage",
    "findings",
    "risk-weighted score",
)


def parse_report_timestamp(text: str) -> datetime | None:
    """``generated-at`` timestamp embedded in a rendered report header.

    The ``## Header`` section carries ``- generated-at: <RFC3339>``;
    missing or unparseable values yield ``None`` so the caller can sort
    such reports last. Deterministic.
    """
    for line in _report_section_lines(text, "## Header"):
        match = re.match(r"-\s*generated-at:\s*(\S+)", line.strip())
        if match:
            try:
                return parse_rfc3339(match.group(1))
            except (ReportError, ValueError):
                return None
    return None


#: One multi-report trend leg: ``(label, runs, pass_pct, covered, total,
#: findings, risk_score)``. ``label`` is the report file name.
MultiTrendLeg = tuple[str, int, int, int, int, int, float]


def collect_multi_report_entries(
    directory: Path,
    flag: str = "--compare-multi",
) -> tuple[list[tuple[MultiTrendLeg, datetime | None]], list[str]]:
    """Trend legs for every report ``*.md`` file in ``directory``.

    Returns ``(legs, skipped)`` where ``legs`` holds one
    ``(leg, timestamp)`` pair per readable report ordered oldest to
    newest by the embedded ``generated-at`` timestamp (files without
    one sort last, name-tiebroken) and ``skipped`` lists the file
    names that could not be read, in sorted order. Metrics reuse the
    ``--side-by-side``/``--diff-drill-down`` parsers
    (:func:`parse_previous_metrics`, :func:`parse_previous_coverage`,
    :func:`parse_previous_snapshot`) with the risk score recomputed
    from the parsed coverage. Deterministic; fail closed when
    ``directory`` is missing or not a directory (``flag`` names the
    option in the error text; ``--spark-history`` reuses this
    collector for the same ordering semantics).
    """
    if not directory.is_dir():
        raise ReportError(
            f"error: cannot read {flag} directory {directory} "
            "(missing or not a directory)"
        )
    try:
        names = sorted(path.name for path in directory.glob("*.md"))
    except OSError as exc:
        raise ReportError(
            f"error: cannot read {flag} directory {directory} ({exc})"
        ) from exc
    legs: list[tuple[MultiTrendLeg, datetime | None]] = []
    skipped: list[str] = []
    for name in names:
        try:
            text = (directory / name).read_text(encoding="utf-8")
        except (OSError, ValueError):
            skipped.append(name)
            continue
        leg = _delta_leg_values(
            parse_previous_metrics(text),
            parse_previous_coverage(text),
            parse_previous_snapshot(text),
        )
        legs.append(((name, *leg), parse_report_timestamp(text)))
    legs.sort(key=lambda entry: (entry[1] is None, entry[1], entry[0][0]))
    return legs, skipped


def render_multi_report_trend(
    *,
    source_label: str,
    legs: list[tuple[MultiTrendLeg, datetime | None]],
    skipped: list[str],
) -> list[str]:
    """Multi-report trend lines (one column per report, oldest to newest).

    One table row per :data:`MULTI_TREND_METRICS` metric with one
    column per report plus a ``trend`` cell joining the per-report
    values with ``→`` and a net arrow from :func:`_trend_arrow` over
    the oldest-to-newest change (``↑`` improving, ``↓`` regressing,
    ``→`` flat within 0.5; findings improve downward). Unreadable
    files are listed as skipped notes; with no readable reports the
    table is omitted and a note records that instead. Deterministic.
    """
    lines = [
        "## Multi-report trend",
        "",
        f"- source: {source_label} ({len(legs)} reports)",
    ]
    for name in skipped:
        lines.append(f"- skipped: {name} (unreadable)")
    if not legs:
        lines.append(f"- no parseable reports in {source_label}")
        return lines
    labels = [leg[0] for leg, _stamp in legs]
    header = ["metric", *labels, "trend"]
    lines += [
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    runs = [leg[1] for leg, _stamp in legs]
    passes = [leg[2] for leg, _stamp in legs]
    covered = [(leg[3], leg[4]) for leg, _stamp in legs]
    findings = [leg[5] for leg, _stamp in legs]
    scores = [leg[6] for leg, _stamp in legs]
    run_cells = [str(value) for value in runs]
    pass_cells = [f"{value}%" for value in passes]
    coverage_cells = [f"{hit}/{total} covered" for hit, total in covered]
    findings_cells = [str(value) for value in findings]
    score_cells = [f"{value:.1f}%" for value in scores]
    lines.append(
        f"| runs | {' | '.join(run_cells)} | "
        f"{' → '.join(run_cells)} ({_trend_arrow(float(runs[-1]), float(runs[0]))}) |"
    )
    lines.append(
        f"| pass rate | {' | '.join(pass_cells)} | "
        f"{' → '.join(pass_cells)} ({_trend_arrow(float(passes[-1]), float(passes[0]))}) |"
    )
    lines.append(
        f"| coverage | {' | '.join(coverage_cells)} | "
        f"{' → '.join(coverage_cells)} "
        f"({_trend_arrow(float(covered[-1][0]), float(covered[0][0]))}) |"
    )
    lines.append(
        f"| findings | {' | '.join(findings_cells)} | "
        f"{' → '.join(findings_cells)} "
        f"({_trend_arrow(float(findings[-1]), float(findings[0]), higher_is_better=False)}) |"
    )
    lines.append(
        f"| risk-weighted score | {' | '.join(score_cells)} | "
        f"{' → '.join(score_cells)} ({_trend_arrow(scores[-1], scores[0])}) |"
    )
    return lines


#: One spark-history row: ``(label, runs, pass_pct, risk_score)``. History
#: rows reuse the multi-report trend legs; the current report appends its
#: own row last (Q10).
SparkHistoryRow = tuple[str, int, int, float]


def spark_history_rows(
    legs: list[tuple[MultiTrendLeg, datetime | None]],
    current_label: str,
    current_leg: tuple[int, int, int, int, int, float],
) -> list[SparkHistoryRow]:
    """History rows plus the current report last (``--spark-history``, Q10).

    ``legs`` are :func:`collect_multi_report_entries` entries (already
    oldest-to-newest); ``current_leg`` is a :func:`_delta_leg_values`
    tuple for the report being rendered. Each row keeps ``(label, runs,
    pass_pct, risk_score)``. Deterministic.
    """
    rows: list[SparkHistoryRow] = [
        (leg[0], leg[1], leg[2], leg[6]) for leg, _stamp in legs
    ]
    rows.append((current_label, current_leg[0], current_leg[1], current_leg[5]))
    return rows


def render_spark_history(
    *,
    source_label: str,
    legs: list[tuple[MultiTrendLeg, datetime | None]],
    skipped: list[str],
    current_label: str,
    current_leg: tuple[int, int, int, int, int, float],
) -> list[str]:
    """Inline run-history table (``--spark-history``, Q10).

    One row per report ``*.md`` in the history directory (ordered by the
    embedded ``generated-at`` timestamp via
    :func:`collect_multi_report_entries`, like ``--compare-multi``) with
    the current report appended as the last row. Columns are the report
    label (``when``), runs, pass %, and risk score; each pass/risk cell
    carries its unicode trend glyph (``↑``/``↓``/``→`` from
    :func:`_trend_arrow`) against the prior row (``→`` for the first
    row). Unreadable files are listed as skipped notes. Deterministic.
    """
    rows = spark_history_rows(legs, current_label, current_leg)
    lines = [
        "## Run history",
        "",
        f"- source: {source_label} ({len(legs)} reports)",
    ]
    for name in skipped:
        lines.append(f"- skipped: {name} (unreadable)")
    lines += [
        "",
        "| when | runs | pass % | risk score |",
        "| --- | --- | --- | --- |",
    ]
    previous: SparkHistoryRow | None = None
    for label, runs, pass_pct, risk_score in rows:
        if previous is None:
            pass_glyph = "→"
            risk_glyph = "→"
        else:
            pass_glyph = _trend_arrow(float(pass_pct), float(previous[2]))
            risk_glyph = _trend_arrow(risk_score, previous[3])
        lines.append(
            f"| {label} | {runs} | {pass_pct}% ({pass_glyph}) | "
            f"{risk_score:.1f}% ({risk_glyph}) |"
        )
        previous = (label, runs, pass_pct, risk_score)
    return lines


#: Tracker backends for the grant-coverage matrix (``--grant-matrix``,
#: Q11), in the canonical findings-sync order (mirrors
#: ``omega_prime.grokbot.findings_sync.TRACKERS``; this script stays
#: stdlib-only so the tuple is repeated here, not imported).
GRANT_TRACKERS = (
    "github",
    "linear",
    "slack",
    "teams",
    "graph",
    "pagerduty",
    "opsgenie",
    "jira",
    "webhook",
)


def load_grant_matrix(
    artifact_path: Path,
) -> tuple[str, set[str] | None, str | None]:
    """RoE grant coverage for ``--grant-matrix`` (Q11).

    Returns ``(artifact_id, covered, error)`` where ``covered`` is the
    set of tracker names the artifact covers (``None`` means all
    trackers: a missing/empty/non-list ``covers`` key, mirroring
    ``roe_lifecycle.grant_trackers``) and ``error`` is a human-readable
    reason when the artifact is unreadable or invalid (fail-soft: the
    caller omits the table and renders the reason as a note instead of
    exiting nonzero). ``artifact_id`` prefers the document's
    ``artifact_id``/``id`` keys, then the file name, then
    ``"unknown-artifact"``. Never raises.
    """
    try:
        raw = artifact_path.read_text(encoding="utf-8")
    except OSError as exc:
        return (
            artifact_path.name,
            None,
            f"cannot read --grant-matrix artifact {artifact_path} ({exc})",
        )
    try:
        document = json.loads(raw)
    except ValueError as exc:
        return (
            artifact_path.name,
            None,
            f"cannot parse --grant-matrix artifact {artifact_path} ({exc})",
        )
    if not isinstance(document, dict):
        return (
            artifact_path.name,
            None,
            f"invalid --grant-matrix artifact {artifact_path} (not a JSON object)",
        )
    artifact_id = "unknown-artifact"
    for key in ("artifact_id", "id"):
        candidate = document.get(key)
        if isinstance(candidate, str) and candidate.strip():
            artifact_id = candidate.strip()
            break
    else:
        if artifact_path.name.strip():
            artifact_id = artifact_path.name.strip()
    covers = document.get("covers")
    if covers is None:
        return (artifact_id, None, None)
    if not isinstance(covers, (list, tuple)):
        return (artifact_id, None, None)
    allowed = {str(name).strip().lower() for name in covers if isinstance(name, str)}
    allowed.discard("")
    return (artifact_id, allowed or None, None)


def render_grant_matrix(
    *,
    artifact_id: str,
    artifact_label: str,
    covered: set[str] | None,
) -> list[str]:
    """Grant-coverage table lines (``--grant-matrix``, Q11).

    One row per :data:`GRANT_TRACKERS` tracker with a ``yes``/``no``
    ``Covered`` cell; ``covered=None`` (missing/empty ``covers``) marks
    every tracker covered. The artifact id names the section header.
    Deterministic.
    """
    lines = [
        f"## Grant coverage: {artifact_id}",
        "",
        f"- source: {artifact_label}",
        "",
        "| tracker | Covered |",
        "| --- | --- |",
    ]
    for tracker in GRANT_TRACKERS:
        cell = "yes" if covered is None or tracker in covered else "no"
        lines.append(f"| {tracker} | {cell} |")
    return lines


def render_md_fragment(
    *,
    coverage: list[tuple[str, int, int, int, float, int, str]],
    gate_line: str | None = None,
) -> str:
    """Embeddable markdown fragment for ``--export-md`` (M12).

    The coverage matrix table plus the risk-weighted score section plus
    the gate line when one is given (``--threshold``); no h1 title and
    no findings-snapshot comment, so the text pastes cleanly into a PR
    comment. Deterministic; the same ``coverage`` rows render
    byte-identically to the full report's sections (including the
    win-rate trend column).
    """
    lines = [
        "## Coverage matrix",
        "",
        "| class | cases | runs | pass rate | gaps | verdict | trend |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for class_name, case_count, class_runs, wins, pct, class_gaps, verdict in coverage:
        rate = f"{wins}/{class_runs} ({pct:.1f}%)" if class_runs else "0/0 (n/a)"
        glyph = coverage_trend_glyph(class_runs, pct)
        lines.append(
            f"| {class_name} | {case_count} | {class_runs} | {rate} "
            f"| {class_gaps} | {verdict} | {glyph} |"
        )
    lines += ["", *render_risk_weighted_score(coverage)]
    if gate_line is not None:
        lines += ["", gate_line]
    lines.append("")
    return "\n".join(lines)


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


def worst_classes(
    coverage: list[tuple[str, int, int, int, float, int, str]],
    count: int,
) -> list[tuple[str, int]]:
    """Top-``count`` classes by weighted gap points (``--worst``, Q12).

    Reuses :func:`weighted_gaps` ordering (descending points, name
    ascending); only classes with points above zero are returned, so the
    result may be shorter than ``count`` (or empty when fully covered).
    A non-positive ``count`` yields ``[]``. Deterministic.
    """
    if count <= 0:
        return []
    return [(name, points) for name, points in weighted_gaps(coverage) if points > 0][
        :count
    ]


def render_exec_summary(
    *,
    runs: int,
    pass_rows: list[tuple[str, str]],
    coverage: list[tuple[str, int, int, int, float, int, str]],
    new_count: int,
    rescinded_count: int,
    previous_runs: int | None = None,
    previous_pass: int | None = None,
    worst_n: int | None = None,
) -> list[str]:
    """Executive-summary lines for ``--exec-summary`` (K10).

    Three to six deterministic bullets derived from the report data:
    runs, pass rate vs previous, risk-weighted score, top risk classes
    by weighted gaps, and the findings trend arrow. ``previous_*`` are
    ``None`` when no baseline report exists (the pass-rate bullet then
    notes ``no baseline`` instead of a delta). ``worst_n`` (``--worst``,
    Q12) appends a ``Worst classes by weighted gaps:`` bullet with the
    top-``worst_n`` classes; ``None`` (flag absent) or ``0`` omits it.
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
    lines = [
        "## Executive summary",
        "",
        f"- runs in window: {runs}",
        pass_bullet,
        f"- risk-weighted score: {score:.1f}% ({earned}/{possible} weighted points)",
        risk_bullet,
        f"- findings: +{new_count} new / -{rescinded_count} rescinded "
        f"({_comparison_arrow(net)})",
    ]
    if worst_n is not None and worst_n > 0:
        lines.append(render_worst_bullet(coverage, worst_n))
    return lines


def render_worst_bullet(
    coverage: list[tuple[str, int, int, int, float, int, str]],
    count: int,
) -> str:
    """``Worst classes by weighted gaps:`` bullet line (``--worst``, Q12)."""
    ranked = worst_classes(coverage, count)
    if ranked:
        detail = ", ".join(
            f"{name} ({points} weighted points)" for name, points in ranked
        )
        return f"- Worst classes by weighted gaps: {detail}"
    return "- Worst classes by weighted gaps: none (all classes fully covered)"


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


JSONL_SCHEMA = "coverage/1"


def coverage_jsonl_rows(
    reports_dir: Path, since_text: str | None = None
) -> list[dict[str, object]]:
    """Rows for ``--export-jsonl`` (R4), one dict per technique class.

    Coverage order (schema classes, then unknown classes sorted); keys
    in fixed order ``schema, class, cases, runs, wins, pass_pct, gaps,
    verdict, tier, weight, earned, possible`` with ``pass_pct`` rounded
    to one decimal and the tier fields taken from the risk breakdown.
    Raises :class:`ReportError` on unreadable history.
    """
    window = _collect_window(reports_dir, since_text)
    runs = window["runs"]
    assert isinstance(runs, int)
    coverage = coverage_rows(
        window["cases"],  # type: ignore[arg-type]
        window["observations"] if runs > 0 else {},
        window["gaps"],  # type: ignore[arg-type]
    )
    by_class = {row[0]: row for row in coverage}
    rows: list[dict[str, object]] = []
    breakdown = risk_weighted_score(coverage)[3]
    for class_name, tier, weight, case_count, wins, got, maximum in breakdown:
        _name, _cases, class_runs, _wins, pct, class_gaps, verdict = by_class[
            class_name
        ]
        rows.append(
            {
                "schema": JSONL_SCHEMA,
                "class": class_name,
                "cases": case_count,
                "runs": class_runs,
                "wins": wins,
                "pass_pct": round(float(pct), 1),
                "gaps": class_gaps,
                "verdict": verdict,
                "tier": tier,
                "weight": weight,
                "earned": got,
                "possible": maximum,
            }
        )
    return rows


def render_jsonl(rows: Sequence[dict[str, object]]) -> str:
    """JSON Lines text: one compact ASCII-only object per row, ``\\n`` ended.

    ``ensure_ascii`` escapes every control character, so no raw newline
    can appear inside a line. Deterministic; ``""`` for no rows.
    """
    return "".join(
        json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n" for row in rows
    )


def write_jsonl_report(rows: Sequence[dict[str, object]], jsonl_path: Path) -> int:
    """Write ``rows`` as JSON Lines at ``jsonl_path`` (R4).

    Returns 0 on success; an unwritable output prints ``error:`` to
    stderr and returns 1.
    """
    try:
        with jsonl_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(render_jsonl(rows))
    except OSError as exc:
        print(
            f"error: cannot write --export-jsonl export {jsonl_path} ({exc})",
            file=sys.stderr,
        )
        return 1
    return 0


def render_recipient_file_text(email_lines: list[str], *, recipient: str | None) -> str:
    """Standalone ``--recipient-file`` text from an email block (N10).

    Subject line first, blank line, then the plain-text body. With a
    ``recipient`` the block is reused verbatim minus the ``## Email
    summary`` heading; without one the ``To:`` line (which has no
    address) is dropped. Deterministic; stays under 2000 characters
    whenever the email block does.
    """
    subject_idx = next(
        index for index, line in enumerate(email_lines) if line.startswith("Subject:")
    )
    chunks = list(email_lines[subject_idx:])
    if recipient is None:
        stripped: list[str] = []
        skip_blank = False
        for line in chunks:
            if line.startswith("To:"):
                skip_blank = True
                continue
            if skip_blank and line == "":
                skip_blank = False
                continue
            stripped.append(line)
        chunks = stripped
    return "\n".join(chunks) + "\n"


def write_recipient_file(text: str, recipient_path: Path) -> int:
    """Write the standalone email file at ``recipient_path`` (N10).

    Returns 0 on success; an unwritable output prints ``error:`` to
    stderr and returns 1.
    """
    try:
        recipient_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(
            f"error: cannot write --recipient-file email: {recipient_path} ({exc})",
            file=sys.stderr,
        )
        return 1
    return 0


#: Verdicts mapping to PASS in the scorecard export (N12); every other
#: verdict (``gappy``, ``unexercised``) maps to FAIL.
SCORECARD_PASS_VERDICTS = frozenset({"covered"})


def render_scorecard(
    coverage: list[tuple[str, int, int, int, float, int, str]],
) -> str:
    """Machine-readable per-class scorecard for ``--export-scorecard`` (N12).

    One deterministic line per coverage class in coverage order
    (``CLASS<TAB>verdict<TAB>PASS|FAIL``) plus a final
    ``OVERALL<TAB><risk-score>`` line carrying the risk-weighted score
    with one decimal. Header comment lines (``#``) document the
    verdict mapping so CI annotation tooling can ``grep -v '^#'`` for
    the data lines.
    """
    score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
    lines = [
        "# engagement scorecard: CLASS<TAB>verdict<TAB>PASS|FAIL "
        "per coverage class plus OVERALL risk score",
        "# mapping: covered->PASS; gappy->FAIL; unexercised->FAIL "
        "(grep -v '^#' for data lines)",
    ]
    for class_name, _cases, _runs, _wins, _pct, _gaps, verdict in coverage:
        status = "PASS" if verdict in SCORECARD_PASS_VERDICTS else "FAIL"
        lines.append(f"{class_name}\t{verdict}\t{status}")
    lines.append(f"OVERALL\t{score:.1f}")
    return "\n".join(lines) + "\n"


def write_scorecard_report(
    coverage: list[tuple[str, int, int, int, float, int, str]],
    scorecard_path: Path,
) -> int:
    """Write the per-class scorecard at ``scorecard_path`` (N12).

    Returns 0 on success; an unwritable output prints ``error:`` to
    stderr and returns 1.
    """
    try:
        scorecard_path.write_text(render_scorecard(coverage), encoding="utf-8")
    except OSError as exc:
        print(
            f"error: cannot write --export-scorecard scorecard: "
            f"{scorecard_path} ({exc})",
            file=sys.stderr,
        )
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

#: Coverage-matrix header with the per-class win-rate trend column (O12).
#: Reports rendered before the trend column keep the six-column header;
#: both shapes identify the matrix and parse identically (the trend glyph
#: cell is ignored by the parsers).
_COVERAGE_MATRIX_HEADER_TRENDED = (*_COVERAGE_MATRIX_HEADER, "trend")


def _is_coverage_matrix_header(cells: list[str]) -> bool:
    """True for either coverage-matrix header shape (plain or trended)."""
    lowered = [cell.lower() for cell in cells]
    return lowered == list(_COVERAGE_MATRIX_HEADER) or lowered == list(
        _COVERAGE_MATRIX_HEADER_TRENDED
    )


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
    return _is_coverage_matrix_header(_split_table_cells(lines[0]))


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
            if _is_coverage_matrix_header(cells):
                in_matrix = True
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if len(cells) not in (
            len(_COVERAGE_MATRIX_HEADER),
            len(_COVERAGE_MATRIX_HEADER_TRENDED),
        ):
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
            if (
                len(row)
                in (
                    len(_COVERAGE_MATRIX_HEADER),
                    len(_COVERAGE_MATRIX_HEADER_TRENDED),
                )
                and row[0]
            ):
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
    three_way: list[str] | None = None,
    multi_trend: list[str] | None = None,
    spark_history: list[str] | None = None,
    grant_matrix: list[str] | None = None,
    exec_summary: list[str] | None = None,
    email_block: list[str] | None = None,
    gate_line: str | None = None,
    sidebar: list[str] | None = None,
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
    if sidebar:
        lines += [*sidebar, ""]
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
        "| class | cases | runs | pass rate | gaps | verdict | trend |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for class_name, case_count, class_runs, wins, pct, class_gaps, verdict in (
        coverage if coverage is not None else []
    ):
        rate = f"{wins}/{class_runs} ({pct:.1f}%)" if class_runs else "0/0 (n/a)"
        glyph = coverage_trend_glyph(class_runs, pct)
        lines.append(
            f"| {class_name} | {case_count} | {class_runs} | {rate} "
            f"| {class_gaps} | {verdict} | {glyph} |"
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
    if three_way:
        lines += ["", *three_way]
    if multi_trend:
        lines += ["", *multi_trend]
    if spark_history:
        lines += ["", *spark_history]
    if grant_matrix:
        lines += ["", *grant_matrix]
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
    delta_path: Path | None = None,
    annotations: list[tuple[str, str]] | None = None,
    exec_summary: bool = False,
    recipient: str | None = None,
    threshold: str | None = None,
    compare_multi_dir: Path | None = None,
    sidebar: bool = False,
    baselines: list[tuple[str, float]] | None = None,
    spark_history_dir: Path | None = None,
    grant_artifact_path: Path | None = None,
    worst: int | None = None,
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
    ``baselines`` (repeatable ``--baseline`` ``(class, floor)`` pairs)
    adds per-class floor gates to that verdict: a class whose win-rate
    is below its floor fails the gate even when the overall threshold
    passes, and the gate line gains the failing ``floors:`` list.

    ``sidebar`` (``--sidebar``) renders the one-glance KPI strip table
    (``Runs | Pass | Coverage | Risk | Gate``) right after the title,
    with signed deltas when a comparison baseline is present (the
    explicit ``--side-by-side``/``--compare`` report, else the newest
    baseline in the output directory).

    ``delta_path`` (``--delta``) renders a ``## Three-way trend`` section
    after the side-by-side comparison (older → previous → current over
    runs, pass rate, coverage, findings, and the risk-weighted score).
    The previous leg is ``--side-by-side``/``--compare`` when given, else
    the newest baseline, else the older report itself.

    ``compare_multi_dir`` (``--compare-multi``) renders a
    ``## Multi-report trend`` section after the three-way trend (one row
    per metric, one column per report oldest to newest). It composes
    with ``--delta``/``--compare``.

    ``spark_history_dir`` (``--spark-history``) renders an inline
    ``## Run history`` table after the multi-report trend: one row per
    report ``*.md`` in the directory (ordered by the embedded
    ``generated-at`` timestamp, like ``--compare-multi``) with the
    current report appended last; columns are when, runs, pass %, and
    risk score, each pass/risk cell carrying its unicode trend glyph
    against the prior row.

    ``grant_artifact_path`` (``--grant-matrix``) renders a
    ``## Grant coverage`` table after the run history: one row per
    known sync tracker with a yes/no ``Covered`` cell from the RoE
    artifact document's ``covers`` key (missing/empty means all
    trackers); the artifact id names the section header. An
    unreadable/invalid artifact omits the table with a skip note
    (fail-soft, never an error).

    ``worst`` (``--worst N``) appends a ``Worst classes by weighted
    gaps:`` bullet to the executive summary (only when it renders) with
    the top-``N`` classes by weighted gaps; ``None`` (flag absent) or
    ``0`` omits the bullet.
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
    prev_leg: tuple[int, int, int, int, int, float] | None = None
    prev_label: str | None = None
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
        prev_leg = _delta_leg_values(
            previous_metrics, previous_coverage, previous_snapshot
        )
        prev_label = side_by_side_source.name
    three_way: list[str] | None = None
    if delta_path is not None:
        try:
            older_text = delta_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportError(
                f"error: cannot read --delta report {delta_path} ({exc})"
            ) from exc
        older_leg = _delta_leg_values(
            parse_previous_metrics(older_text),
            parse_previous_coverage(older_text),
            parse_previous_snapshot(older_text),
        )
        if prev_leg is None and baseline_name is not None:
            candidate = out_dir / baseline_name
            try:
                same_file = candidate.resolve() == delta_path.resolve()
            except OSError:
                same_file = False
            if not same_file:
                try:
                    baseline_text = candidate.read_text(encoding="utf-8")
                except OSError:
                    baseline_text = None
                if baseline_text is not None:
                    prev_leg = _delta_leg_values(
                        parse_previous_metrics(baseline_text),
                        parse_previous_coverage(baseline_text),
                        parse_previous_snapshot(baseline_text),
                    )
                    prev_label = baseline_name
        if prev_leg is None:
            prev_leg = older_leg
            prev_label = delta_path.name
        assert prev_label is not None
        current_score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
        current_leg = (
            runs,
            _summary_pct(pass_rows),
            sum(1 for row in coverage if row[6] == "covered"),
            len(coverage),
            len(current_findings),
            current_score,
        )
        three_way = render_three_way_trend(
            older_label=delta_path.name,
            previous_label=prev_label,
            older=older_leg,
            previous=prev_leg,
            current=current_leg,
        )
    multi_trend: list[str] | None = None
    if compare_multi_dir is not None:
        multi_legs, multi_skipped = collect_multi_report_entries(compare_multi_dir)
        multi_trend = render_multi_report_trend(
            source_label=compare_multi_dir.name,
            legs=multi_legs,
            skipped=multi_skipped,
        )
    spark_history: list[str] | None = None
    if spark_history_dir is not None:
        spark_legs, spark_skipped = collect_multi_report_entries(
            spark_history_dir, flag="--spark-history"
        )
        spark_score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
        spark_current_leg = (
            runs,
            _summary_pct(pass_rows),
            sum(1 for row in coverage if row[6] == "covered"),
            len(coverage),
            len(current_findings),
            spark_score,
        )
        spark_history = render_spark_history(
            source_label=spark_history_dir.name,
            legs=spark_legs,
            skipped=spark_skipped,
            current_label=file_name,
            current_leg=spark_current_leg,
        )
    grant_matrix: list[str] | None = None
    if grant_artifact_path is not None:
        grant_id, grant_covered, grant_error = load_grant_matrix(grant_artifact_path)
        if grant_error is not None:
            grant_matrix = [f"- grant matrix skipped: {grant_error}"]
        else:
            grant_matrix = render_grant_matrix(
                artifact_id=grant_id,
                artifact_label=grant_artifact_path.name,
                covered=grant_covered,
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
            worst_n=worst,
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
    if baselines:
        validate_baseline_classes(baselines, cases)
    gate_info: dict[str, object] | None = None
    if threshold is not None or baselines:
        gate_info = evaluate_threshold(pass_rows, coverage, threshold, baselines)
    sidebar_lines: list[str] | None = None
    if sidebar:
        explicit_source = (
            side_by_side_path if side_by_side_path is not None else compare_path
        )
        explicit_flag = (
            "--side-by-side" if side_by_side_path is not None else "--compare"
        )
        sidebar_previous: tuple[int, int, int, int, float] | None = None
        if explicit_source is not None:
            try:
                sidebar_text = explicit_source.read_text(encoding="utf-8")
            except OSError as exc:
                raise ReportError(
                    f"error: cannot read {explicit_flag} report "
                    f"{explicit_source} ({exc})"
                ) from exc
            sidebar_previous = _sidebar_previous_leg(sidebar_text)
        elif baseline_name is not None:
            try:
                sidebar_text = (out_dir / baseline_name).read_text(encoding="utf-8")
            except OSError:
                sidebar_text = None
            if sidebar_text is not None:
                sidebar_previous = _sidebar_previous_leg(sidebar_text)
        sidebar_gate: str | None = None
        if gate_info is not None:
            sidebar_gate = "PASS" if gate_info["passed"] else "FAIL"
        sidebar_lines = render_sidebar(
            runs=runs,
            pass_rows=pass_rows,
            coverage=coverage,
            gate=sidebar_gate,
            previous=sidebar_previous,
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
        three_way=three_way,
        multi_trend=multi_trend,
        spark_history=spark_history,
        grant_matrix=grant_matrix,
        exec_summary=exec_summary_lines,
        email_block=email_lines,
        gate_line=str(gate_info["line"]) if gate_info is not None else None,
        sidebar=sidebar_lines,
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
    compare_multi_dir: Path | None = None,
    sidebar: bool = False,
    baselines: list[tuple[str, float]] | None = None,
    spark_history_dir: Path | None = None,
    grant_artifact_path: Path | None = None,
    worst: int | None = None,
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
    ``sidebar`` adds the KPI strip object after the header (only when
    flagged); ``baselines`` adds per-class floor gates to the gate
    object (a ``failures`` list, only when floors are given).
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
    if compare_multi_dir is not None:
        multi_legs, multi_skipped = collect_multi_report_entries(compare_multi_dir)
        ordered = [leg for leg, _stamp in multi_legs]
        multi_runs = [leg[1] for leg in ordered]
        multi_passes = [leg[2] for leg in ordered]
        multi_covered = [(leg[3], leg[4]) for leg in ordered]
        multi_findings = [leg[5] for leg in ordered]
        multi_scores = [leg[6] for leg in ordered]
        data["multi_trend"] = {
            "source": compare_multi_dir.name,
            "reports": [leg[0] for leg in ordered],
            "skipped": list(multi_skipped),
            "note": (
                f"no parseable reports in {compare_multi_dir.name}"
                if not ordered
                else None
            ),
            "metrics": [
                {
                    "metric": "runs",
                    "values": list(multi_runs),
                    "trend": _trend_arrow(float(multi_runs[-1]), float(multi_runs[0]))
                    if ordered
                    else "→",
                },
                {
                    "metric": "pass rate",
                    "values": [f"{value}%" for value in multi_passes],
                    "trend": _trend_arrow(
                        float(multi_passes[-1]), float(multi_passes[0])
                    )
                    if ordered
                    else "→",
                },
                {
                    "metric": "coverage",
                    "values": [
                        _coverage_summary(covered=hit, total=total)
                        for hit, total in multi_covered
                    ],
                    "trend": _trend_arrow(
                        float(multi_covered[-1][0]), float(multi_covered[0][0])
                    )
                    if ordered
                    else "→",
                },
                {
                    "metric": "findings",
                    "values": list(multi_findings),
                    "trend": _trend_arrow(
                        float(multi_findings[-1]),
                        float(multi_findings[0]),
                        higher_is_better=False,
                    )
                    if ordered
                    else "→",
                },
                {
                    "metric": "risk-weighted score",
                    "values": [f"{value:.1f}%" for value in multi_scores],
                    "trend": _trend_arrow(multi_scores[-1], multi_scores[0])
                    if ordered
                    else "→",
                },
            ],
        }
    if spark_history_dir is not None:
        spark_legs, spark_skipped = collect_multi_report_entries(
            spark_history_dir, flag="--spark-history"
        )
        spark_score, _spark_earned, _spark_possible, _spark_breakdown = (
            risk_weighted_score(coverage)
        )
        spark_rows = spark_history_rows(
            spark_legs,
            file_name,
            (
                runs,
                _summary_pct(pass_rows),
                sum(1 for row in coverage if row[6] == "covered"),
                len(coverage),
                len(current_findings),
                spark_score,
            ),
        )
        data["run_history"] = {
            "source": spark_history_dir.name,
            "reports": [row[0] for row in spark_rows],
            "skipped": list(spark_skipped),
            "rows": [
                {
                    "report": label,
                    "runs": row_runs,
                    "pass_pct": row_pass,
                    "risk_score": row_score,
                }
                for label, row_runs, row_pass, row_score in spark_rows
            ],
        }
    if grant_artifact_path is not None:
        grant_id, grant_covered, grant_error = load_grant_matrix(grant_artifact_path)
        if grant_error is not None:
            data["grant_matrix"] = {"skipped": grant_error}
        else:
            data["grant_matrix"] = {
                "artifact": grant_artifact_path.name,
                "artifact_id": grant_id,
                "rows": [
                    {
                        "tracker": tracker,
                        "covered": grant_covered is None or tracker in grant_covered,
                    }
                    for tracker in GRANT_TRACKERS
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
        if worst is not None and worst > 0:
            data["exec_summary"]["worst_classes"] = [
                {"class": name, "weighted_points": points}
                for name, points in worst_classes(coverage, worst)
            ]
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
    if baselines:
        validate_baseline_classes(baselines, cases)
    if sidebar:
        explicit_source = (
            side_by_side_path if side_by_side_path is not None else compare_path
        )
        explicit_flag = (
            "--side-by-side" if side_by_side_path is not None else "--compare"
        )
        sidebar_previous: tuple[int, int, int, int, float] | None = None
        if explicit_source is not None:
            try:
                sidebar_text = explicit_source.read_text(encoding="utf-8")
            except OSError as exc:
                raise ReportError(
                    f"error: cannot read {explicit_flag} report "
                    f"{explicit_source} ({exc})"
                ) from exc
            sidebar_previous = _sidebar_previous_leg(sidebar_text)
        elif baseline_name is not None:
            try:
                sidebar_text = (out_dir / baseline_name).read_text(encoding="utf-8")
            except OSError:
                sidebar_text = None
            if sidebar_text is not None:
                sidebar_previous = _sidebar_previous_leg(sidebar_text)
        sidebar_gate: str | None = None
        if threshold is not None or baselines:
            sidebar_gate_info = evaluate_threshold(
                pass_rows, coverage, threshold, baselines
            )
            sidebar_gate = "PASS" if sidebar_gate_info["passed"] else "FAIL"
        data["sidebar"] = sidebar_data(
            runs=runs,
            pass_rows=pass_rows,
            coverage=coverage,
            gate=sidebar_gate,
            previous=sidebar_previous,
        )
    if threshold is not None or baselines:
        gate = evaluate_threshold(pass_rows, coverage, threshold, baselines)
        gate_entry: dict[str, object] = {
            "metric": gate["metric"],
            "value": gate["value"],
            "gate": gate["gate"],
            "passed": gate["passed"],
        }
        if baselines:
            gate_entry["failures"] = gate["failures"]
        data["gate"] = gate_entry
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


class BaselineUsageError(Exception):
    """Unknown ``--baseline`` class: exit 2 with a valid-class listing."""


def parse_baseline(spec: str) -> tuple[str, float]:
    """``(class, floor)`` from one ``--baseline`` value (P12).

    Accepts ``<class>=<pct>`` with ``pct`` 0-100 inclusive. Returns the
    class name plus the numeric floor. Raises :class:`ReportError` on
    malformed or out-of-range specs (NaN/inf rejected); unknown class
    names are rejected later by :func:`validate_baseline_classes`
    (usage error, exit 2). Deterministic.
    """
    name, sep, raw = spec.partition("=")
    if sep and name.strip() and raw.strip():
        try:
            floor = float(raw.strip())
        except ValueError:
            floor = float("nan")
        if math.isfinite(floor) and 0.0 <= floor <= 100.0:
            return name.strip(), floor
    raise ReportError(
        f"error: invalid --baseline {spec!r} (expected 'class=pct', 0-100)"
    )


def validate_baseline_classes(
    baselines: list[tuple[str, float]],
    cases: list[tuple[str, str, str, str]],
) -> None:
    """Reject ``--baseline`` classes seen neither in schema nor on disk.

    Raises :class:`BaselineUsageError` (exit 2) naming the first unknown
    class and listing every valid class (schema classes byte-wise, then
    any extra on-disk classes sorted). Deterministic.
    """
    known = set(SCHEMA_CLASSES) | {case_class for _, _, _, case_class in cases}
    for name, _floor in baselines:
        if name not in known:
            valid = ", ".join([*SCHEMA_CLASSES, *sorted(known - set(SCHEMA_CLASSES))])
            raise BaselineUsageError(
                f"error: unknown --baseline class {name!r} (valid: {valid})"
            )


def _sidebar_previous_leg(text: str) -> tuple[int, int, int, int, float]:
    """``(runs, pass_pct, covered, total, risk_score)`` from a past report.

    Parses ``text`` with the same parsers ``--side-by-side`` uses; the
    risk score is recomputed from the parsed coverage so reports
    predating the score section still yield a value. Deterministic.
    """
    metrics = parse_previous_metrics(text)
    previous_coverage = parse_previous_coverage(text)
    snapshot = parse_previous_snapshot(text)
    leg = _delta_leg_values(metrics, previous_coverage, snapshot)
    return (leg[0], leg[1], leg[2], leg[3], leg[5])


def render_sidebar(
    *,
    runs: int,
    pass_rows: list[tuple[str, str]],
    coverage: list[tuple[str, int, int, int, float, int, str]],
    gate: str | None = None,
    previous: tuple[int, int, int, int, float] | None = None,
) -> list[str]:
    """One-glance KPI strip lines for ``--sidebar`` (P10).

    A single markdown table (``Runs | Pass | Coverage | Risk | Gate``)
    over the current window: run count, integer pass percentage,
    ``X/Y covered`` classes, one-decimal risk-weighted score, and the
    gate verdict (``PASS``/``FAIL`` when a gate is set, else ``n/a``).
    When ``previous`` (a :func:`_sidebar_previous_leg` tuple) is given,
    every value cell gains its signed delta plus a direction arrow.
    Deterministic.
    """
    current_pass = _summary_pct(pass_rows)
    covered = sum(1 for row in coverage if row[6] == "covered")
    total = len(coverage)
    score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
    covered_text = _coverage_summary(covered=covered, total=total)
    if previous is None:
        runs_cell = f"{runs}"
        pass_cell = f"{current_pass}%"
        covered_cell = covered_text
        risk_cell = f"{score:.1f}%"
    else:
        prev_runs, prev_pass, prev_covered, _prev_total, prev_score = previous
        delta_runs = runs - prev_runs
        delta_pass = current_pass - prev_pass
        delta_covered = covered - prev_covered
        delta_score = score - prev_score
        runs_cell = f"{runs} ({delta_runs:+d} {_comparison_arrow(delta_runs)})"
        pass_cell = (
            f"{current_pass}% ({delta_pass:+d}pp {_comparison_arrow(delta_pass)})"
        )
        covered_cell = (
            f"{covered_text} ({delta_covered:+d} {_comparison_arrow(delta_covered)})"
        )
        risk_cell = (
            f"{score:.1f}% ({delta_score:+.1f}pp {_comparison_arrow(delta_score)})"
        )
    gate_cell = gate if gate is not None else "n/a"
    return [
        "| Runs | Pass | Coverage | Risk | Gate |",
        "| --- | --- | --- | --- | --- |",
        f"| {runs_cell} | {pass_cell} | {covered_cell} | {risk_cell} | {gate_cell} |",
    ]


def sidebar_data(
    *,
    runs: int,
    pass_rows: list[tuple[str, str]],
    coverage: list[tuple[str, int, int, int, float, int, str]],
    gate: str | None = None,
    previous: tuple[int, int, int, int, float] | None = None,
) -> dict[str, object]:
    """Structured KPI strip for ``--json --sidebar`` (P10).

    Mirrors :func:`render_sidebar` with numeric values plus the display
    strings; ``previous``/``deltas`` are present only when a comparison
    baseline was parsed. Deterministic.
    """
    current_pass = _summary_pct(pass_rows)
    covered = sum(1 for row in coverage if row[6] == "covered")
    total = len(coverage)
    score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
    data: dict[str, object] = {
        "runs": runs,
        "pass_pct": current_pass,
        "coverage": _coverage_summary(covered=covered, total=total),
        "covered": covered,
        "total": total,
        "risk_score": score,
        "gate": gate,
    }
    if previous is not None:
        prev_runs, prev_pass, prev_covered, prev_total, prev_score = previous
        data["previous"] = {
            "runs": prev_runs,
            "pass_pct": prev_pass,
            "coverage": _coverage_summary(covered=prev_covered, total=prev_total),
            "covered": prev_covered,
            "total": prev_total,
            "risk_score": prev_score,
        }
        data["deltas"] = {
            "runs": runs - prev_runs,
            "pass_pct": current_pass - prev_pass,
            "covered": covered - prev_covered,
            "risk_score": round(score - prev_score, 1),
        }
    return data


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
    spec: str | None,
    baselines: list[tuple[str, float]] | None = None,
) -> dict[str, object]:
    """Gate verdict over the current window (L10, P12 floors).

    Returns ``metric``/``value``/``gate``/``passed`` plus the report
    ``line`` (``Gate: PASS (metric value >= gate)`` or ``Gate: FAIL``
    with a per-class ``floors:`` suffix when floor gates fail) and the
    ``stderr`` detail for failing runs. ``value`` is the integer pass
    percentage for ``gate:`` specs and the one-decimal risk-weighted
    score for ``gate-risk:`` specs. ``baselines`` holds repeatable
    ``--baseline`` ``(class, floor)`` pairs: a class whose win-rate
    ``pct`` is below its floor fails the gate even when the overall
    threshold passes (``failures`` lists ``class``/``pass_pct``/``floor``
    in CLI order). With ``spec`` unset the gate rests on the floors
    alone (``metric`` ``"floors"``). Without baselines the passing and
    failing lines match the L10 shapes exactly.
    """
    failures: list[dict[str, object]] = []
    if baselines:
        pct_by_class = {row[0]: row[4] for row in coverage}
        for class_name, floor in baselines:
            pct = float(pct_by_class.get(class_name, 0.0))
            if pct < floor:
                failures.append(
                    {
                        "class": class_name,
                        "pass_pct": round(pct, 1),
                        "floor": int(floor) if float(floor).is_integer() else floor,
                    }
                )
    floors_text = ", ".join(
        f"{entry['class']} {float(entry['pass_pct']):.1f}% "
        f"< {_format_gate(float(entry['floor']))}%"
        for entry in failures
    )
    if spec is None:
        passed = not failures
        if passed:
            line = "Gate: PASS (floors met)"
        else:
            line = f"Gate: FAIL (floors: {floors_text})"
        return {
            "metric": "floors",
            "value": None,
            "gate": None,
            "passed": passed,
            "line": line,
            "stderr": f"gate: FAIL (floors: {floors_text})",
            "failures": failures,
        }
    metric, gate = parse_threshold(spec)
    if metric == "risk-score":
        score, _earned, _possible, _breakdown = risk_weighted_score(coverage)
        value: float = score
        shown = f"{score:.1f}"
    else:
        value = float(_summary_pct(pass_rows))
        shown = str(int(value))
    passed = value >= gate and not failures
    gate_text = _format_gate(gate)
    if passed:
        line = f"Gate: PASS ({metric} {shown} >= {gate_text})"
    elif failures:
        line = f"Gate: FAIL (floors: {floors_text})"
    else:
        line = "Gate: FAIL"
    stderr = f"gate: FAIL ({metric} {shown} < {gate_text})"
    if failures:
        if passed is False and value >= gate:
            stderr = f"gate: FAIL (floors: {floors_text})"
        else:
            stderr = f"{stderr}; floors: {floors_text}"
    return {
        "metric": metric,
        "value": int(value) if metric == "pass-rate" else value,
        "gate": int(gate) if float(gate).is_integer() else gate,
        "passed": passed,
        "line": line,
        "stderr": stderr,
        "failures": failures,
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


def build_digest(
    reports_dir: Path,
    since_text: str | None = None,
    *,
    threshold: str | None = None,
    baselines: Sequence[tuple[str, float]] = (),
) -> dict[str, object]:
    """Plain-text engagement digest for ``--digest`` (R3).

    Returns ``{"state", "lines", "gate"}``: ``state`` is ``"ok"`` or
    ``"no-reports"``; ``lines`` holds at most four strings without line
    endings: the :func:`summarize` line, ``coverage covered=C/T
    gappy=G unexercised=U risk=S.S``, ``worst=<classes>`` (``worst=none``
    when nothing is at risk) and, only with ``threshold`` or
    ``baselines``, the gate line; ``gate`` is the
    :func:`evaluate_threshold` dict or ``None``. Built only from numbers
    and schema class names (unknown on-disk classes appear as
    ``other``), never from finding or case free text. Empty or missing
    history yields the zero line, an all-zero coverage line and
    ``worst=none``. Raises :class:`ReportError` and
    :class:`BaselineUsageError` like the rest of the script.
    """
    summary = summarize(reports_dir, since_text)
    state = str(summary["state"])
    gated = threshold is not None or bool(baselines)
    window = (
        _collect_window(reports_dir, since_text) if state == "ok" or gated else None
    )
    coverage: list[tuple[str, int, int, int, float, int, str]] = []
    if window is not None:
        runs = window["runs"]
        assert isinstance(runs, int)
        coverage = coverage_rows(
            window["cases"],  # type: ignore[arg-type]
            window["observations"] if runs > 0 else {},
            window["gaps"],  # type: ignore[arg-type]
        )
    gate: dict[str, object] | None = None
    if gated and window is not None:
        if baselines:
            validate_baseline_classes(
                list(baselines),
                window["cases"],  # type: ignore[arg-type]
            )
        gate = evaluate_threshold(
            window["pass_rows"],  # type: ignore[arg-type]
            coverage,
            threshold,
            list(baselines) if baselines else None,
        )
    lines = [str(summary["line"])]
    if state == "ok":
        verdicts = [row[6] for row in coverage]
        score = risk_weighted_score(coverage)[0]
        lines.append(
            f"coverage covered={verdicts.count('covered')}/{len(coverage)} "
            f"gappy={verdicts.count('gappy')} "
            f"unexercised={verdicts.count('unexercised')} risk={score:.1f}"
        )
        worst: list[str] = []
        for name, _points in worst_classes(coverage, len(coverage)):
            shown = name if name in SCHEMA_CLASSES else "other"
            if shown not in worst:
                worst.append(shown)
        lines.append("worst=" + (",".join(worst[:3]) if worst else "none"))
    else:
        lines.append("coverage covered=0/0 gappy=0 unexercised=0 risk=0.0")
        lines.append("worst=none")
    if gate is not None:
        lines.append(str(gate["line"]))
    return {"state": state, "lines": lines, "gate": gate}


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
        "--delta",
        default=None,
        metavar="OLDER_REPORT",
        help="older report markdown for the three-way trend: renders a "
        "## Three-way trend section after the side-by-side comparison "
        "(older → previous → current over runs, pass rate, coverage, "
        "findings, and the risk-weighted score with ↑/↓/→ arrows); the "
        "previous leg is --side-by-side/--compare when given, else the "
        "newest baseline, else the older report itself.",
    )
    parser.add_argument(
        "--compare-multi",
        default=None,
        metavar="REPORTS_DIR",
        help="directory of report markdown files for the multi-report "
        "trend: renders a ## Multi-report trend section after the "
        "three-way trend (one row per metric over runs, pass rate, "
        "coverage, findings, and the risk-weighted score with one "
        "column per report oldest to newest, values joined with → "
        "plus a net ↑/↓/→ arrow); reports order by their embedded "
        "generated-at timestamp (files without one sort last, "
        "name-tiebroken); unreadable files are skipped with a listed "
        "note; with no readable reports the table is omitted with a "
        "note instead. Composes with --delta/--compare.",
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
        "--export-md",
        default=None,
        metavar="OUTPUT_MD",
        help="also write an embeddable markdown fragment at OUTPUT_MD "
        "(the coverage matrix table plus the risk-weighted score section "
        "plus the gate line when --threshold is set; no h1 title, no "
        "snapshot comment, suitable for pasting into a PR comment); "
        "stdout and the canonical report are unchanged.",
    )
    parser.add_argument(
        "--recipient-file",
        default=None,
        metavar="OUTPUT_TXT",
        help="also write the email block (the same content --recipient "
        "renders) as a standalone file at OUTPUT_TXT (Subject line first, "
        "blank line, plain-text body under 2000 chars); without "
        "--recipient or --exec-summary it implies the exec-summary "
        "derivation for the email body; stdout and the canonical report "
        "are unchanged.",
    )
    parser.add_argument(
        "--export-scorecard",
        default=None,
        metavar="OUTPUT_TXT",
        help="also write a machine-readable per-class scorecard at "
        "OUTPUT_TXT (one deterministic CLASS<TAB>verdict<TAB>PASS|FAIL "
        "line per coverage class plus a final OVERALL<TAB>risk-score "
        "line; the verdict mapping is documented in # header comments); "
        "stdout and the canonical report are unchanged.",
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
    parser.add_argument(
        "--sidebar",
        action="store_true",
        help="render a one-glance KPI strip table (Runs | Pass | Coverage "
        "| Risk | Gate) right after the title, with signed deltas when a "
        "comparison baseline is present (a 'sidebar' object in --json, "
        "only when flagged). Composes with all modes.",
    )
    parser.add_argument(
        "--baseline",
        action="append",
        default=None,
        metavar="CLASS=PCT",
        help="repeatable per-class floor gate ('<class>=<pct>', 0-100): a "
        "class whose win-rate is below its floor fails the gate even when "
        "the overall --threshold passes; the gate line and the --json "
        "gate object gain the failing per-class list. Unknown class "
        "names exit 2 listing the valid classes; malformed specs exit 1.",
    )
    parser.add_argument(
        "--spark-history",
        default=None,
        metavar="REPORTS_DIR",
        help="directory of report markdown files for the inline run "
        "history: renders a ## Run history table after the multi-report "
        "trend (one row per report oldest to newest by the embedded "
        "generated-at timestamp, like --compare-multi, with the current "
        "report appended last; columns when, runs, pass %%, and risk "
        "score, each pass/risk cell carrying its unicode trend glyph "
        "against the prior row). A missing directory fails closed.",
    )
    parser.add_argument(
        "--grant-matrix",
        default=None,
        metavar="ARTIFACT_JSON",
        help="RoE artifact JSON for the grant-coverage matrix: renders a "
        "## Grant coverage table (one row per known sync tracker with a "
        "yes/no Covered cell from the artifact document's covers key; "
        "missing/empty covers means all trackers) with the artifact id in "
        "the section header. An unreadable/invalid artifact omits the "
        "table with a skip note (fail-soft, never an error).",
    )
    parser.add_argument(
        "--worst",
        nargs="?",
        const=3,
        default=None,
        type=int,
        metavar="N",
        help="with --exec-summary, append a 'Worst classes by weighted "
        "gaps:' bullet listing the top-N classes by weighted gaps (weight "
        "table reuse; N defaults to 3 when the flag is given without a "
        "value; 0 omits the bullet); --json gains a worst_classes array "
        "only when --exec-summary is set. Ignored without --exec-summary.",
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="print a stdout-only plain-text digest of at most 5 lines "
        "(the --summary line, a 'coverage covered=C/T gappy=G "
        "unexercised=U risk=S.S' line, 'worst=<class,...>' or "
        "'worst=none', plus the 'Gate: ...' line when --threshold or "
        "--baseline is given); deterministic, no timestamps, built only "
        "from numbers and schema class names. Empty or missing history "
        "prints zeros, not an error. Exit 0 ok, 3 gate FAIL, 2 unknown "
        "--baseline class, 1 report/IO error. Cannot be combined with "
        "--json or --summary (exit 1). Composes with --since, "
        "--reports-dir, --threshold, --baseline and --export-jsonl; like "
        "--summary it writes no report and ignores every other "
        "file-writing flag.",
    )
    parser.add_argument(
        "--export-jsonl",
        default=None,
        metavar="OUTPUT_JSONL",
        help="also write the coverage matrix as JSON Lines at OUTPUT_JSONL "
        "(one compact object per technique class in coverage order: "
        "schema, class, cases, runs, wins, pass_pct, gaps, verdict, tier, "
        "weight, earned, possible); stdout is unchanged.",
    )
    args = parser.parse_args(argv)
    if args.worst is not None and args.worst < 0:
        print(
            "error: --worst must be >= 0 (0 omits the bullet)",
            file=sys.stderr,
        )
        return 1
    if args.digest and args.json:
        print("error: --digest cannot be combined with --json", file=sys.stderr)
        return 1
    if args.digest and args.summary:
        print("error: --digest cannot be combined with --summary", file=sys.stderr)
        return 1

    reports_dir = Path(args.reports_dir) if args.reports_dir else default_reports_dir()
    if args.digest:
        try:
            digest_baselines = (
                [parse_baseline(raw) for raw in args.baseline] if args.baseline else ()
            )
            digest = build_digest(
                reports_dir,
                args.since,
                threshold=args.threshold,
                baselines=digest_baselines,
            )
            digest_jsonl_rows = (
                coverage_jsonl_rows(reports_dir, args.since)
                if args.export_jsonl
                else None
            )
        except BaselineUsageError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        digest_lines = digest["lines"]
        digest_gate = digest["gate"]
        assert isinstance(digest_lines, list)
        assert digest_gate is None or isinstance(digest_gate, dict)
        sys.stdout.write("".join(f"{line}\n" for line in digest_lines))
        digest_status = 0
        if digest_jsonl_rows is not None:
            digest_status = write_jsonl_report(
                digest_jsonl_rows, Path(args.export_jsonl)
            )
        if digest_gate is not None and not digest_gate["passed"]:
            print(str(digest_gate["stderr"]), file=sys.stderr)
            return 3
        return digest_status
    if args.summary:
        try:
            summary = summarize(reports_dir, args.since)
            summary_gate: dict[str, object] | None = None
            summary_baselines = (
                [parse_baseline(raw) for raw in args.baseline]
                if args.baseline
                else None
            )
            if args.threshold is not None or summary_baselines:
                summary_window = _collect_window(reports_dir, args.since)
                summary_runs = summary_window["runs"]
                assert isinstance(summary_runs, int)
                summary_coverage = coverage_rows(
                    summary_window["cases"],  # type: ignore[arg-type]
                    summary_window["observations"] if summary_runs > 0 else {},
                    summary_window["gaps"],  # type: ignore[arg-type]
                )
                if summary_baselines:
                    validate_baseline_classes(
                        summary_baselines,
                        summary_window["cases"],  # type: ignore[arg-type]
                    )
                summary_gate = evaluate_threshold(
                    summary_window["pass_rows"],  # type: ignore[arg-type]
                    summary_coverage,
                    args.threshold,
                    summary_baselines,
                )
        except BaselineUsageError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            if summary_gate is not None:
                summary_gate_entry: dict[str, object] = {
                    "metric": summary_gate["metric"],
                    "value": summary_gate["value"],
                    "gate": summary_gate["gate"],
                    "passed": summary_gate["passed"],
                }
                if summary_baselines:
                    summary_gate_entry["failures"] = summary_gate["failures"]
                summary["gate"] = summary_gate_entry
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
        baselines = (
            [parse_baseline(raw) for raw in args.baseline] if args.baseline else None
        )
        compare_path = Path(args.compare) if args.compare else None
        diff_coverage_path = Path(args.diff_coverage) if args.diff_coverage else None
        trend_path = Path(args.trend) if args.trend else None
        diff_drill_down_path = (
            Path(args.diff_drill_down) if args.diff_drill_down else None
        )
        side_by_side_path = Path(args.side_by_side) if args.side_by_side else None
        delta_path = Path(args.delta) if args.delta else None
        compare_multi_dir = Path(args.compare_multi) if args.compare_multi else None
        spark_history_dir = Path(args.spark_history) if args.spark_history else None
        grant_artifact_path = Path(args.grant_matrix) if args.grant_matrix else None
        markdown, file_name, out_dir = build_report(
            reports_dir,
            args.since,
            compare_path,
            drill_down=args.drill_down,
            diff_coverage_path=diff_coverage_path,
            trend_path=trend_path,
            diff_drill_down_path=diff_drill_down_path,
            side_by_side_path=side_by_side_path,
            delta_path=delta_path,
            annotations=annotations,
            exec_summary=args.exec_summary,
            recipient=args.recipient,
            threshold=args.threshold,
            compare_multi_dir=compare_multi_dir,
            sidebar=args.sidebar,
            baselines=baselines,
            spark_history_dir=spark_history_dir,
            grant_artifact_path=grant_artifact_path,
            worst=args.worst,
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
                    compare_multi_dir=compare_multi_dir,
                    sidebar=args.sidebar,
                    baselines=baselines,
                    spark_history_dir=spark_history_dir,
                    grant_artifact_path=grant_artifact_path,
                    worst=args.worst,
                )
            )
    except BaselineUsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
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
    if (
        args.threshold is not None
        or baselines
        or (args.export is not None and args.export[0] == "html")
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
    if args.export_jsonl:
        try:
            jsonl_rows = coverage_jsonl_rows(reports_dir, args.since)
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        status = write_jsonl_report(jsonl_rows, Path(args.export_jsonl)) or status
    if args.export_md:
        try:
            md_window = _collect_window(reports_dir, args.since)
            md_runs = md_window["runs"]
            assert isinstance(md_runs, int)
            md_coverage = coverage_rows(
                md_window["cases"],  # type: ignore[arg-type]
                md_window["observations"] if md_runs > 0 else {},
                md_window["gaps"],  # type: ignore[arg-type]
            )
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        md_gate_line = (
            str(
                evaluate_threshold(
                    md_window["pass_rows"],  # type: ignore[arg-type]
                    md_coverage,  # type: ignore[arg-type]
                    args.threshold,
                    baselines,
                )["line"]
            )
            if args.threshold is not None or baselines
            else None
        )
        try:
            Path(args.export_md).write_text(
                render_md_fragment(coverage=md_coverage, gate_line=md_gate_line),
                encoding="utf-8",
            )
        except OSError as exc:
            print(
                f"error: cannot write --export-md fragment: {args.export_md} ({exc})",
                file=sys.stderr,
            )
            return 1
    if args.recipient_file or args.export_scorecard:
        try:
            extra_window = _collect_window(reports_dir, args.since)
            extra_runs = extra_window["runs"]
            assert isinstance(extra_runs, int)
            extra_coverage = coverage_rows(
                extra_window["cases"],  # type: ignore[arg-type]
                extra_window["observations"] if extra_runs > 0 else {},  # type: ignore[arg-type]
                extra_window["gaps"],  # type: ignore[arg-type]
            )
        except ReportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.export_scorecard:
            status = (
                write_scorecard_report(extra_coverage, Path(args.export_scorecard))
                or status
            )
        if args.recipient_file:
            extra_current = extra_window["current_findings"]
            assert isinstance(extra_current, dict)
            extra_out_dir = output_dir(reports_dir)
            extra_baseline_name, extra_baseline = newest_baseline(
                extra_out_dir, file_name
            )
            if extra_baseline is None:
                extra_new = len(extra_current)
                extra_rescinded = 0
            else:
                extra_new = sum(1 for h in extra_current if h not in extra_baseline)
                extra_rescinded = sum(
                    1 for h in extra_baseline if h not in extra_current
                )
            extra_baseline_metrics: dict[str, int] | None = None
            if extra_baseline_name is not None:
                try:
                    extra_baseline_metrics = parse_previous_metrics(
                        (extra_out_dir / extra_baseline_name).read_text(
                            encoding="utf-8"
                        )
                    )
                except OSError:
                    extra_baseline_metrics = None
            extra_previous_pass = (
                extra_baseline_metrics["pass_pct"] if extra_baseline_metrics else None
            )
            extra_score, extra_earned, extra_possible, _extra_breakdown = (
                risk_weighted_score(extra_coverage)
            )
            extra_block = render_email_block(
                recipient=args.recipient if args.recipient is not None else "",
                window_start=extra_window["window_start"],  # type: ignore[arg-type]
                window_end=extra_window["window_end"],  # type: ignore[arg-type]
                runs=extra_runs,
                pass_pct=_summary_pct(extra_window["pass_rows"]),  # type: ignore[arg-type]
                previous_pass=extra_previous_pass,
                score=extra_score,
                earned=extra_earned,
                possible=extra_possible,
                new_count=extra_new,
                rescinded_count=extra_rescinded,
                file_name=file_name,
            )
            status = (
                write_recipient_file(
                    render_recipient_file_text(extra_block, recipient=args.recipient),
                    Path(args.recipient_file),
                )
                or status
            )
    if (args.threshold is not None or baselines) and gate_evidence is not None:
        gate = evaluate_threshold(
            gate_evidence["pass_rows"],  # type: ignore[arg-type]
            gate_evidence["coverage"],  # type: ignore[arg-type]
            args.threshold,
            baselines,
        )
        if not gate["passed"]:
            print(str(gate["stderr"]), file=sys.stderr)
            return 3
    return status


if __name__ == "__main__":
    raise SystemExit(main())

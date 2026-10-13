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


def parse_case_text(text: str, source: str) -> tuple[str | None, str, str]:
    """Extract ``(case_id, expected detection, residual_rationale)``.

    Minimal line reader for the fixed case schema (top-level scalars
    plus one nested ``expected:`` mapping). Raises :class:`ValueError`
    when the text is not a mapping at all.
    """
    case_id: str | None = None
    detection = ""
    rationale = ""
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
    return case_id, detection, rationale


def read_cases(cases_dir: Path) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Load ``(case_id, detection, rationale)`` rows sorted by case id."""
    try:
        files = sorted(cases_dir.glob("*.yaml"))
    except OSError as exc:
        return [], [f"{cases_dir}: cannot list case files ({exc})"]
    if not files:
        return [], [f"{cases_dir}: no case files (*.yaml) found"]
    cases: list[tuple[str, str, str]] = []
    errors: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}: cannot read ({exc})")
            continue
        try:
            case_id, detection, rationale = parse_case_text(text, str(path))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not case_id:
            errors.append(f"{path}: missing case_id")
            continue
        cases.append((case_id, detection, rationale))
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
    """Transition rows with parsed timestamps; malformed lines warn to stderr."""
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


def detection_gaps(
    cases: list[tuple[str, str, str]],
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
    known = {case_id for case_id, _, _ in cases}
    for case_id, expected, rationale in cases:
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


def render_report(
    *,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime,
    version: str,
    runs: int,
    pass_rows: list[tuple[str, str]],
    gaps: list[str],
    current_findings: dict[str, str],
    baseline_name: str | None,
    baseline: dict[str, str] | None,
    transition_counts: list[tuple[str, str, int]],
) -> str:
    """Assemble the six report sections in fixed order."""
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
    lines += [
        "",
        f"<!-- findings-snapshot: {json.dumps(current_findings, sort_keys=True)} -->",
        "",
    ]
    return "\n".join(lines)


def build_report(reports_dir: Path, since_text: str | None) -> tuple[str, str, Path]:
    """Render the report; return ``(markdown, file_name, out_dir)``."""
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
    generated_at = window_end

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
            case_id: (expected, rationale) for case_id, expected, rationale in cases
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

    start_day = window_start.date().isoformat().replace(":", "-")
    end_day = window_end.date().isoformat().replace(":", "-")
    file_name = f"engagement-{start_day}_{end_day}.md"
    out_dir = output_dir(reports_dir)
    baseline_name, baseline = newest_baseline(out_dir, file_name)

    version = read_version(REPO_ROOT)
    markdown = render_report(
        window_start=window_start,
        window_end=window_end,
        generated_at=generated_at,
        version=version,
        runs=runs,
        pass_rows=pass_rows,
        gaps=gaps,
        current_findings=current_findings,
        baseline_name=baseline_name,
        baseline=baseline,
        transition_counts=transition_counts,
    )
    return markdown, file_name, out_dir


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
            "due_at, interval_seconds=604800)."
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
    args = parser.parse_args(argv)

    reports_dir = Path(args.reports_dir) if args.reports_dir else default_reports_dir()
    try:
        markdown, file_name, out_dir = build_report(reports_dir, args.since)
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

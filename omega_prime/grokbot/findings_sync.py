# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Findings → tracker sync: one-way push from local findings to GitHub issues.

Consumer of ``sidecar/findings.py`` ``work-items.json``: each not-yet-synced
finding row becomes one GitHub issue via the ``gh`` CLI
(``gh api repos/{repo}/issues``). Stdlib + ``gh`` only — no network client
of our own.

Idempotency key is ``dedupe_hash`` (``sha256(canonical(case_id +
normalized response))``): after a successful push the finding row gains
``issue_url`` + ``synced_at`` (RFC3339), so a second run pushes nothing.
Replay of identical content bumps ``occurrence_count`` via the existing
``record_finding`` dedupe without opening a second issue.

Config-off (``redteam.findings_sync.enabled: false``, the default) is fully
inert: the CLI and :func:`tick` exit 0, push nothing, and print
``findings_sync disabled``.

The issue body carries fixed fields only — never the raw gateway payload
(threat T-79-11 keeps raw payload text in the versioned case file).

Linear mode (``redteam.findings_sync.tracker: "linear"``) pushes the same
rows as Linear issues through ``gh api graphql`` (``issueCreate``) on the
same runner seam. Severity maps to Linear priority (critical→urgent(1),
high→high(2), medium→medium(3), low→low(4)); the dedupe hash rides in the
issue description, so idempotency marking (``issue_url`` + ``synced_at``)
is identical to the GitHub mode.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sidecar.findings import findings_path, load_findings  # noqa: E402
from sidecar.state import reports_dir, utc_now_rfc3339  # noqa: E402

#: Message printed when the sync node is disabled (config-off inertness).
DISABLED_MESSAGE = "findings_sync disabled"

#: Message used when no target repo is configured (fail-closed, never guess).
NO_REPO_MESSAGE = "no target repo configured"

#: Default severity → GitHub label mapping. Operator-overridable via
#: ``redteam.findings_sync.labels`` (per-severity replacement).
DEFAULT_LABELS: dict[str, list[str]] = {
    "critical": ["severity:critical", "redteam"],
    "high": ["severity:high", "redteam"],
    "medium": ["severity:medium"],
    "low": ["severity:low"],
}

#: Fallback label for unknown severities.
UNKNOWN_SEVERITY_LABELS = ["redteam"]

#: Supported tracker backends for the findings sync.
TRACKERS = ("github", "linear")

#: Default tracker backend (the existing GitHub-issues mode, unchanged).
DEFAULT_TRACKER = "github"

#: Message used when tracker=linear but no Linear team is configured
#: (fail-closed, never guess).
NO_LINEAR_TEAM_MESSAGE = "no linear team configured"

#: Severity → Linear priority mapping (Linear API: 1=Urgent, 2=High,
#: 3=Medium, 4=Low). Operator labels do not apply to Linear issues.
LINEAR_PRIORITIES: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}

#: Fallback Linear priority for unknown severities (Linear "No Priority").
UNKNOWN_SEVERITY_PRIORITY = 0

#: Linear ``issueCreate`` mutation run through ``gh api graphql``. Variables
#: (``teamId``/``title``/``description``/``priority``) ride as ``-f``/``-F``
#: fields on the same runner seam as the GitHub mode.
LINEAR_ISSUE_MUTATION = (
    "mutation($teamId: String!, $title: String!, "
    "$description: String, $priority: Int) { "
    "issueCreate(input: {teamId: $teamId, title: $title, "
    "description: $description, priority: $priority}) { "
    "success issue { id url } } }"
)

#: ``gh`` invocation timeout (seconds).
_GH_TIMEOUT = 60


class FindingsSyncError(RuntimeError):
    """Base error for findings-sync failures (CLI maps these to exit 1)."""


class GhNotFoundError(FindingsSyncError):
    """The ``gh`` binary is missing — structured, no partial write."""


class GhApiError(FindingsSyncError):
    """The ``gh api`` call failed (nonzero exit or runner exception)."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


#: ``GhRunner`` seam: ``runner(argv, data)`` where ``argv`` is the full
#: ``gh`` command and ``data`` is the issue payload
#: (``{"repo", "title", "body", "labels"}``) for test introspection.
#: Returns a ``CompletedProcess``-like (``returncode``/``stdout``/``stderr``).
GhRunner = Callable[[list[str], dict[str, Any]], Any]


def _default_runner(argv: list[str], data: dict[str, Any]) -> Any:
    """Production runner: resolve ``gh`` on PATH, then ``subprocess.run``."""
    del data  # payload is already encoded in argv via -f fields.
    exe = shutil.which(argv[0]) if argv else None
    if not exe:
        raise GhNotFoundError(
            f"{argv[0] if argv else 'gh'} binary not found on PATH; "
            "install gh (https://cli.github.com) to push findings"
        )
    return subprocess.run(
        [exe, *argv[1:]],
        capture_output=True,
        text=True,
        timeout=_GH_TIMEOUT,
    )


def labels_for_severity(
    severity: Any, overrides: Mapping[str, Any] | None = None
) -> list[str]:
    """Resolve GitHub labels for ``severity`` (case-insensitive).

    Per-severity operator overrides replace the default list entirely;
    unknown severities fall back to ``["redteam"]``.
    """
    key = severity.lower() if isinstance(severity, str) else ""
    if overrides:
        override = overrides.get(key)
        if isinstance(override, list) and all(
            isinstance(item, str) for item in override
        ):
            return list(override)
    return list(DEFAULT_LABELS.get(key, UNKNOWN_SEVERITY_LABELS))


def priority_for_severity(severity: Any) -> int:
    """Resolve the Linear priority for ``severity`` (case-insensitive).

    Critical→urgent (1), high→high (2), medium→medium (3), low→low (4);
    unknown severities fall back to ``0`` (Linear "No Priority").
    """
    key = severity.lower() if isinstance(severity, str) else ""
    return LINEAR_PRIORITIES.get(key, UNKNOWN_SEVERITY_PRIORITY)


def issue_title(row: Mapping[str, Any]) -> str:
    """Issue title — same format as ``export_work_items()`` titles."""
    return (
        f"[{row.get('severity')}] {row.get('case_id')}: "
        f"{row.get('observed_detection')} ({row.get('verdict')})"
    )


def issue_body(row: Mapping[str, Any]) -> str:
    """Issue body: fixed fields only, no raw payload (threat T-79-11)."""
    lines = [
        f"Red-team finding {row.get('case_id')}",
        "",
        f"- case_id: {row.get('case_id')}",
        f"- severity: {row.get('severity')}",
        f"- verdict: {row.get('verdict')}",
        f"- observed_detection: {row.get('observed_detection')}",
        f"- dedupe_hash: {row.get('dedupe_hash')}",
        f"- occurrence_count: {row.get('occurrence_count', 1)}",
        f"- first_seen: {row.get('ts', '')}",
        f"- last_seen: {row.get('last_seen', '')}",
        "",
        "synced by findings_sync",
    ]
    return "\n".join(lines) + "\n"


def _issue_url_from_stdout(stdout: Any, repo: str) -> str:
    """Extract the pushed issue ref from ``gh api`` stdout.

    Prefers ``html_url`` from the JSON response; falls back to a bare-URL
    stdout body. Returns ``""`` when nothing URL-like is present (the row
    is still marked synced — the push itself succeeded).
    """
    del repo
    if isinstance(stdout, bytes):
        try:
            stdout = stdout.decode("utf-8", "replace")
        except Exception:
            return ""
    if not isinstance(stdout, str):
        return ""
    try:
        payload = json.loads(stdout)
    except ValueError:
        text = stdout.strip()
        return text if text.startswith("http") else ""
    if isinstance(payload, dict):
        url = payload.get("html_url", "")
        return url if isinstance(url, str) else ""
    return ""


def _linear_issue_url_from_stdout(stdout: Any) -> str:
    """Extract the pushed Linear issue URL from ``gh api graphql`` stdout.

    Prefers ``data.issueCreate.issue.url`` from the JSON response; falls
    back to a bare-URL stdout body. Returns ``""`` when nothing URL-like
    is present (the row is still marked synced — the push itself
    succeeded).
    """
    if isinstance(stdout, bytes):
        try:
            stdout = stdout.decode("utf-8", "replace")
        except Exception:
            return ""
    if not isinstance(stdout, str):
        return ""
    try:
        payload = json.loads(stdout)
    except ValueError:
        text = stdout.strip()
        return text if text.startswith("http") else ""
    if isinstance(payload, dict):
        try:
            node: Any = payload["data"]["issueCreate"]["issue"]["url"]
        except (KeyError, TypeError):
            return ""
        return node if isinstance(node, str) else ""
    return ""


def _write_rows_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomically rewrite the findings NDJSON store (tmp + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(path)


def sync_findings(
    reports_dir: str | Path | None,
    repo: str,
    *,
    runner: GhRunner | None = None,
    labels: Mapping[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Push every not-yet-synced finding row to ``repo`` as a GitHub issue.

    Rows carrying ``issue_url`` are skipped (idempotent by ``dedupe_hash``).
    On success each pushed row gains ``issue_url`` + ``synced_at`` (RFC3339)
    via one atomic store rewrite. When a push fails, rows pushed earlier in
    this run keep their refs (persisted), the failed row stays unsynced for
    retry on the next run, and a :class:`GhApiError` is raised. With zero
    pushes (or ``dry_run``) the store file is byte-identical and the runner
    is never called in dry-run mode.

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}`` where
    ``pushed`` holds ``{"dedupe_hash", "issue_url"}`` dicts (would-push
    entries with ``issue_url: ""`` under ``dry_run``) and ``skipped`` holds
    already-synced ``dedupe_hash`` strings.
    """
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError(NO_REPO_MESSAGE)
    repo = repo.strip()
    run = runner if runner is not None else _default_runner

    rows = load_findings(reports_dir)
    pushed: list[dict[str, Any]] = []
    skipped: list[str] = []
    dirty = False

    for row in rows:
        digest = str(row.get("dedupe_hash", ""))
        if row.get("issue_url"):
            skipped.append(digest)
            continue
        title = issue_title(row)
        body = issue_body(row)
        row_labels = labels_for_severity(row.get("severity"), labels)
        payload = {"repo": repo, "title": title, "body": body, "labels": row_labels}
        argv = ["gh", "api", f"repos/{repo}/issues", "--method", "POST"]
        argv += ["-f", f"title={title}", "-f", f"body={body}"]
        for label in row_labels:
            argv += ["-f", f"labels[]={label}"]

        if dry_run:
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
            continue

        try:
            completed = run(argv, payload)
        except GhNotFoundError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc

        returncode = getattr(completed, "returncode", 1)
        if returncode != 0:
            _persist_if_dirty(reports_dir, dirty, rows)
            stderr = getattr(completed, "stderr", "")
            raise GhApiError(
                f"gh api repos/{repo}/issues exited {returncode}: {stderr}",
                returncode=returncode if isinstance(returncode, int) else None,
            )
        row["issue_url"] = _issue_url_from_stdout(
            getattr(completed, "stdout", ""), repo
        )
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def sync_findings_linear(
    reports_dir: str | Path | None,
    team_id: str,
    *,
    runner: GhRunner | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Push every not-yet-synced finding row to Linear as an issue.

    Same rows, titles, and bodies as :func:`sync_findings` (the dedupe
    hash rides in the issue description, so idempotency marking via
    ``issue_url`` + ``synced_at`` is identical); only the transport
    differs — ``gh api graphql`` ``issueCreate`` with the severity mapped
    to a Linear priority (critical→1 urgent … low→4 low) instead of
    GitHub labels. Failure semantics mirror :func:`sync_findings`:
    earlier-in-run pushes persist, the failed row stays unsynced, and a
    :class:`GhApiError` is raised. With zero pushes (or ``dry_run``) the
    store file is byte-identical and the runner is never called in
    dry-run mode.

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}``
    with the same entry shapes as :func:`sync_findings`.
    """
    if not isinstance(team_id, str) or not team_id.strip():
        raise ValueError(NO_LINEAR_TEAM_MESSAGE)
    team_id = team_id.strip()
    run = runner if runner is not None else _default_runner

    rows = load_findings(reports_dir)
    pushed: list[dict[str, Any]] = []
    skipped: list[str] = []
    dirty = False

    for row in rows:
        digest = str(row.get("dedupe_hash", ""))
        if row.get("issue_url"):
            skipped.append(digest)
            continue
        title = issue_title(row)
        body = issue_body(row)
        priority = priority_for_severity(row.get("severity"))
        payload = {
            "team_id": team_id,
            "title": title,
            "body": body,
            "priority": priority,
        }
        argv = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={LINEAR_ISSUE_MUTATION}",
            "-f",
            f"teamId={team_id}",
            "-f",
            f"title={title}",
            "-f",
            f"description={body}",
            "-F",
            f"priority={priority}",
        ]

        if dry_run:
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
            continue

        try:
            completed = run(argv, payload)
        except GhNotFoundError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc

        returncode = getattr(completed, "returncode", 1)
        if returncode != 0:
            _persist_if_dirty(reports_dir, dirty, rows)
            stderr = getattr(completed, "stderr", "")
            raise GhApiError(
                f"gh api graphql exited {returncode}: {stderr}",
                returncode=returncode if isinstance(returncode, int) else None,
            )
        row["issue_url"] = _linear_issue_url_from_stdout(
            getattr(completed, "stdout", "")
        )
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def _persist_if_dirty(
    directory: str | Path | None, dirty: bool, rows: list[dict[str, Any]]
) -> None:
    """Persist earlier-in-run pushes so a failed run never double-pushes."""
    if dirty:
        _write_rows_atomic(findings_path(directory), rows)


def _sync_node(config: Mapping[str, Any] | None) -> dict[str, Any]:
    node: Any = (config or {}).get("redteam", {}).get("findings_sync", {})
    return dict(node) if isinstance(node, dict) else {}


def _tracker_for_node(node: Mapping[str, Any]) -> str:
    """Normalized tracker backend for a findings-sync node (default github)."""
    tracker = node.get("tracker", DEFAULT_TRACKER)
    if not isinstance(tracker, str) or not tracker.strip():
        return DEFAULT_TRACKER
    return tracker.strip().lower()


def _tracker_error(message: str, dry_run: bool) -> dict[str, Any]:
    """Structured fail-closed result for a misconfigured sync node."""
    return {
        "pushed": [],
        "skipped": [],
        "dry_run": dry_run,
        "error": message,
    }


def tick(
    config: Mapping[str, Any] | None = None,
    *,
    reports_directory: str | Path | None = None,
    runner: GhRunner | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Heartbeat-schedulable entrypoint: reuse the sync functions.

    Reads ``redteam.findings_sync`` (``enabled``, ``tracker``, ``repo``,
    ``labels``, ``linear_team_id``) from ``config`` (loads via
    ``load_config()`` when omitted). Disabled, repo-less, team-less, or
    unknown-tracker results are structured dicts — never exceptions,
    never a new cron kind. The sync itself stays plain functions;
    schedulers call this ``tick`` following the ``tick``/``schedule``
    precedent.
    """
    if config is None:
        from omega_prime.config import load_config  # deferred: keeps import light

        config = load_config()
    node = _sync_node(config)
    if node.get("enabled") is not True:
        return {"pushed": [], "skipped": [], "dry_run": dry_run, "disabled": True}
    tracker = _tracker_for_node(node)
    if tracker == "linear":
        team_id = node.get("linear_team_id", "")
        if not isinstance(team_id, str) or not team_id.strip():
            return _tracker_error(NO_LINEAR_TEAM_MESSAGE, dry_run)
        result = sync_findings_linear(
            reports_directory,
            team_id.strip(),
            runner=runner,
            dry_run=dry_run,
        )
        result["disabled"] = False
        return result
    if tracker != "github":
        return _tracker_error(f"unknown tracker: {tracker}", dry_run)
    repo = node.get("repo", "")
    if not isinstance(repo, str) or not repo.strip():
        return {
            "pushed": [],
            "skipped": [],
            "dry_run": dry_run,
            "error": NO_REPO_MESSAGE,
        }
    node_labels = node.get("labels")
    result = sync_findings(
        reports_directory,
        repo.strip(),
        runner=runner,
        labels=node_labels if isinstance(node_labels, dict) else None,
        dry_run=dry_run,
    )
    result["disabled"] = False
    return result


def _main_linear(directory: str | Path | None, team_id: str, dry_run: bool) -> int:
    """Run the Linear branch of the CLI (dry-run aware)."""
    if dry_run:
        try:
            result = sync_findings_linear(
                directory if directory is not None else reports_dir(),
                team_id,
                dry_run=True,
            )
        except FindingsSyncError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for entry in result["pushed"]:
            print(f"would push {entry['dedupe_hash']}")
        for digest in result["skipped"]:
            print(f"skip (already synced) {digest}")
        return 0

    try:
        result = sync_findings_linear(
            directory if directory is not None else reports_dir(),
            team_id,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FindingsSyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for entry in result["pushed"]:
        print(f"pushed {entry['dedupe_hash']} -> {entry['issue_url']}")
    for digest in result["skipped"]:
        print(f"skip (already synced) {digest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI: ``findings_sync [--tracker github|linear] [--repo OWNER/NAME] [--reports-dir DIR] [--dry-run]``."""
    parser = argparse.ArgumentParser(
        prog="findings_sync",
        description="Push unsynced red-team findings to tracker issues (one-way).",
    )
    parser.add_argument(
        "--tracker",
        default="",
        help="Tracker backend: github (default) or linear; blank means config",
    )
    parser.add_argument("--repo", default="", help="Target repo OWNER/NAME")
    parser.add_argument("--linear-team-id", default="", help="Linear team ID override")
    parser.add_argument("--reports-dir", default="", help="Reports dir override")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print would-push items; write nothing, call no gh.",
    )
    args = parser.parse_args(argv)

    from omega_prime.config import load_config  # deferred: keeps import light

    node = _sync_node(load_config())
    if node.get("enabled") is not True:
        print(DISABLED_MESSAGE)
        return 0

    tracker = args.tracker.strip().lower() or _tracker_for_node(node)
    directory: str | Path | None = args.reports_dir or None
    if tracker == "linear":
        team_id = args.linear_team_id.strip() or (
            node.get("linear_team_id", "").strip()
            if isinstance(node.get("linear_team_id", ""), str)
            else ""
        )
        if not team_id:
            print(f"error: {NO_LINEAR_TEAM_MESSAGE}", file=sys.stderr)
            return 2
        return _main_linear(directory, team_id, args.dry_run)
    if tracker != "github":
        print(f"error: unknown tracker: {tracker}", file=sys.stderr)
        return 2

    repo = args.repo.strip() or (
        node.get("repo", "").strip() if isinstance(node.get("repo", ""), str) else ""
    )
    if not repo:
        print(f"error: {NO_REPO_MESSAGE}", file=sys.stderr)
        return 2
    node_labels = node.get("labels")
    overrides = node_labels if isinstance(node_labels, dict) else None

    if args.dry_run:
        try:
            result = sync_findings(
                directory if directory is not None else reports_dir(),
                repo,
                labels=overrides,
                dry_run=True,
            )
        except FindingsSyncError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for entry in result["pushed"]:
            print(f"would push {entry['dedupe_hash']}")
        for digest in result["skipped"]:
            print(f"skip (already synced) {digest}")
        return 0

    try:
        result = sync_findings(
            directory if directory is not None else reports_dir(),
            repo,
            labels=overrides,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FindingsSyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for entry in result["pushed"]:
        print(f"pushed {entry['dedupe_hash']} -> {entry['issue_url']}")
    for digest in result["skipped"]:
        print(f"skip (already synced) {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DEFAULT_LABELS",
    "DEFAULT_TRACKER",
    "DISABLED_MESSAGE",
    "LINEAR_ISSUE_MUTATION",
    "LINEAR_PRIORITIES",
    "NO_LINEAR_TEAM_MESSAGE",
    "NO_REPO_MESSAGE",
    "TRACKERS",
    "UNKNOWN_SEVERITY_LABELS",
    "UNKNOWN_SEVERITY_PRIORITY",
    "FindingsSyncError",
    "GhApiError",
    "GhNotFoundError",
    "GhRunner",
    "issue_body",
    "issue_title",
    "labels_for_severity",
    "main",
    "priority_for_severity",
    "sync_findings",
    "sync_findings_linear",
    "tick",
]

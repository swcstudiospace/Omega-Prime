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

Slack mode (``redteam.findings_sync.tracker: "slack"``) posts the same
rows as Slack messages through ``gh api <slack_webhook_url>`` POSTs on
the same runner seam. Severity maps to an attachment color
(critical/high→danger, medium→warning, low→good); the dedupe hash rides
in the message text, and pushed rows are marked with a deterministic
``slack:<dedupe_hash>`` ref, so idempotency is identical to the other
modes.
Teams mode (``redteam.findings_sync.tracker: "teams"``) posts the same
rows as Teams channel messages through ``gh api <teams_webhook_url>``
POSTs on the same runner seam. The payload is an Office 365 connector
card with a severity-colored ``themeColor`` (critical→DC143C,
high→FF8C00, medium→FFD700, low→00BFFF); the dedupe hash rides in the
card summary, and pushed rows are marked with a deterministic
``teams:<dedupe_hash>`` ref, so idempotency is identical to the other
modes.
Graph mode (``redteam.findings_sync.graph``) posts the same
rows as Teams channel messages through the Microsoft Graph API on
the same runner seam: one client-credentials token fetch
(``login.microsoftonline.com``) then ``gh api`` POSTs to
``graph.microsoft.com/v1.0/teams/{team}/channels/{channel}/messages``.
Each message is a ``chatMessage`` whose attachment reuses the
:func:`teams_message` Office 365 connector card structure; pushed rows
are marked with a deterministic ``graph:<dedupe_hash>`` ref, so
idempotency is identical to the other modes. A blank or missing
``graph`` mapping means the feature is off (fail-closed, never crash).
PagerDuty mode (``redteam.findings_sync.tracker: "pagerduty"``) triggers
the same rows as PagerDuty Events API v2 events through
``gh api https://events.pagerduty.com/v2/enqueue`` POSTs on the same
runner seam (with the webhook-retry transport). Each event carries
``event_action: trigger`` with ``dedup_key`` set to the finding's
``dedupe_hash`` (idempotent re-trigger/update semantics), a severity
mapped from the finding severity (critical→critical, high→error,
moderate/medium→warning, low/info→info), and a payload with
``summary``/``source``/``custom_details`` (fixed fields only, never the
raw gateway payload). The routing key comes from
``redteam.findings_sync.pagerduty_routing_key``; blank means off
(fail-closed, never guess). Pushed rows are marked with a deterministic
``pd:<dedupe_hash>`` ref, so idempotency is identical to the other
modes.
Opsgenie mode (``redteam.findings_sync.tracker: "opsgenie"``) creates
the same rows as Opsgenie Alert API v2 alerts through
``gh api https://api.opsgenie.com/v2/alerts`` POSTs on the same
runner seam (with the webhook-retry transport, authorizing via an
``Authorization: GenieKey`` header). Each alert carries ``message``
(the finding title, capped at 130 chars) with ``alias`` set to the
finding's ``dedupe_hash`` (dedupe/update semantics: re-sending the
same alias updates rather than duplicates), a ``description`` with
the finding details, a ``priority`` mapped from the finding severity
(critical→P1, high→P2, moderate/medium→P3, low/info→P5), and a
``details`` dict (fixed fields only, never the raw gateway payload).
The API key comes from ``redteam.findings_sync.opsgenie_api_key``;
blank means off (fail-closed, never guess). Pushed rows are marked
with a deterministic ``og:<dedupe_hash>`` ref, so idempotency is
identical to the other modes.
Jira mode (``redteam.findings_sync.tracker: "jira"``) creates the
same rows as Jira issues through the Jira REST API v3 on the same
runner seam (with the webhook-retry transport, authorizing via a
``Authorization: Basic`` header built from ``email:api_token``).
Each row is first looked up with a JQL search
(``labels = "dedupe:<hash>" AND project = <key>``) so an existing
issue for the same finding is updated (``PUT /issue/{key}``) rather
than duplicated; otherwise a new issue is created
(``POST /issue``) with ``summary`` (the finding title),
``description`` (the finding details in Atlassian document format),
``labels`` (including the ``dedupe:<hash>`` label), ``project``,
and ``issuetype``. Connection details come from the
``redteam.findings_sync.jira`` mapping
(``base_url``/``email``/``api_token``/``project_key``); blank or
incomplete means off (fail-closed, never guess). Pushed rows are
marked with a deterministic ``jira:<issuekey>:<dedupe_hash>`` ref,
so idempotency is identical to the other modes.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import random
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
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
TRACKERS = (
    "github",
    "linear",
    "slack",
    "teams",
    "graph",
    "pagerduty",
    "opsgenie",
    "jira",
)

#: Default tracker backend (the existing GitHub-issues mode, unchanged).
DEFAULT_TRACKER = "github"

#: Message used when tracker=linear but no Linear team is configured
#: (fail-closed, never guess).
NO_LINEAR_TEAM_MESSAGE = "no linear team configured"

#: Message used when tracker=slack but no webhook URL is configured
#: (fail-closed, never guess).
NO_SLACK_WEBHOOK_MESSAGE = "no slack webhook configured"

#: Message used when tracker=teams but no webhook URL is configured
#: (fail-closed, never guess).
NO_TEAMS_WEBHOOK_MESSAGE = "no teams webhook configured"

#: Message used when graph mode is selected but the ``graph`` mapping is
#: blank or missing (fail-closed, never guess, never crash).
NO_GRAPH_CONFIG_MESSAGE = "no graph config configured"

#: Message used when tracker=opsgenie but no API key is configured
#: (fail-closed, never guess).
NO_OPSGENIE_API_KEY_MESSAGE = "no opsgenie api key configured"

#: Message used when tracker=pagerduty but no routing key is configured
#: (fail-closed, never guess).
NO_PAGERDUTY_ROUTING_KEY_MESSAGE = "no pagerduty routing key configured"

#: Message used when tracker=jira but the ``jira`` mapping is blank or
#: incomplete (fail-closed, never guess).
NO_JIRA_CONFIG_MESSAGE = "no jira config configured"

#: Required ``redteam.findings_sync.jira`` fields (all non-blank).
JIRA_FIELDS = ("base_url", "email", "api_token", "project_key")

#: Default Jira issue type for created issues (operator-overridable via
#: the ``jira`` mapping's ``issue_type`` entry).
JIRA_DEFAULT_ISSUE_TYPE = "Task"

#: PagerDuty Events API v2 enqueue endpoint (``event_action: trigger``).
PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

#: Valid PagerDuty Events API v2 severities.
PAGERDUTY_SEVERITIES = ("critical", "error", "warning", "info")

#: OAuth2 scope for the Graph client-credentials token fetch.
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

#: Token endpoint template for the Graph client-credentials flow.
GRAPH_TOKEN_URL_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
)

#: Channel-messages endpoint template for Graph posts.
GRAPH_MESSAGES_URL_TEMPLATE = (
    "https://graph.microsoft.com/v1.0/teams/{team}/channels/{channel}/messages"
)

#: Required ``redteam.findings_sync.graph`` fields (all non-blank).
GRAPH_FIELDS = ("tenant_id", "client_id", "client_secret", "team_id", "channel_id")

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

#: Severity → Slack attachment color mapping (Slack ``good``/``warning``/
#: ``danger`` names; operator labels do not apply to Slack messages).
SLACK_COLORS: dict[str, str] = {
    "critical": "danger",
    "high": "danger",
    "medium": "warning",
    "low": "good",
}

#: Fallback Slack attachment color for unknown severities.
UNKNOWN_SEVERITY_COLOR = "#808080"

#: Severity → Teams Office 365 connector card ``themeColor`` mapping
#: (operator labels do not apply to Teams messages).
TEAMS_THEME_COLORS: dict[str, str] = {
    "critical": "DC143C",
    "high": "FF8C00",
    "medium": "FFD700",
    "low": "00BFFF",
}

#: Fallback Teams card theme color for unknown severities.
UNKNOWN_SEVERITY_THEME_COLOR = "#808080"

#: Finding severity → PagerDuty Events API v2 severity mapping.
#: Operator-overridable per severity via the ``severity_map`` argument
#: of :func:`pagerduty_severity` (per-severity replacement).
PD_SEVERITIES: dict[str, str] = {
    "critical": "critical",
    "high": "error",
    "medium": "warning",
    "moderate": "warning",
    "low": "info",
    "info": "info",
}

#: Fallback PagerDuty severity for unknown severities (Events API v2
#: requires one of ``critical``/``error``/``warning``/``info``).
UNKNOWN_SEVERITY_PD_SEVERITY = "info"

#: Opsgenie Alert API v2 create-alert endpoint.
OPSGENIE_ALERTS_URL = "https://api.opsgenie.com/v2/alerts"

#: Valid Opsgenie Alert API v2 priorities.
OPSGENIE_PRIORITIES = ("P1", "P2", "P3", "P4", "P5")

#: Finding severity → Opsgenie Alert API v2 priority mapping.
#: Operator-overridable per severity via the ``priority_map`` argument
#: of :func:`opsgenie_priority` (per-severity replacement).
OG_PRIORITIES: dict[str, str] = {
    "critical": "P1",
    "high": "P2",
    "medium": "P3",
    "moderate": "P3",
    "low": "P5",
    "info": "P5",
}

#: Fallback Opsgenie priority for unknown severities.
UNKNOWN_SEVERITY_OG_PRIORITY = "P5"

#: Opsgenie alert ``message`` field cap (Alert API v2 limit is 130 chars).
OPSGENIE_MESSAGE_LIMIT = 130

#: ``gh`` invocation timeout (seconds).
_GH_TIMEOUT = 60

#: Webhook POST retry budget: retries after the initial attempt. Only the
#: Slack/Teams/PagerDuty/Opsgenie webhook transports retry; GitHub/Linear modes
#: are unchanged.
WEBHOOK_MAX_RETRIES = 3

#: Base backoff delays (seconds) before webhook retry 1/2/3. Each delay
#: gets up to +1s of jitter (see :func:`_webhook_retry_delay`).
WEBHOOK_RETRY_DELAYS = (1.0, 2.0, 4.0)

#: ``gh`` exit code for CLI usage errors — terminal, never retried.
_GH_USAGE_ERROR_EXIT = 2


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


def _webhook_retry_delay(retry_index: int) -> float:
    """Backoff delay (seconds) before webhook retry ``retry_index`` (0-based)."""
    if retry_index < len(WEBHOOK_RETRY_DELAYS):
        base = WEBHOOK_RETRY_DELAYS[retry_index]
    else:
        base = WEBHOOK_RETRY_DELAYS[-1]
    return base + random.uniform(0, 1.0)


def _run_with_webhook_retry(
    run: GhRunner, argv: list[str], data: dict[str, Any]
) -> Any:
    """Run one webhook POST with exponential-backoff retry.

    Transient failures — a nonzero ``gh`` exit (other than usage-error
    exit 2) or a runner exception such as a network error — retry up to
    :data:`WEBHOOK_MAX_RETRIES` times with :data:`WEBHOOK_RETRY_DELAYS`
    backoff plus jitter; each retry logs a warning to stderr. Terminal
    failures — exit 2, a missing ``gh`` binary
    (:class:`GhNotFoundError`), or an already-classified
    :class:`FindingsSyncError` — raise immediately with no retry.
    """
    label = argv[2] if len(argv) > 2 else "webhook"
    attempt = 0
    while True:
        try:
            completed = run(argv, data)
        except GhNotFoundError:
            raise
        except FindingsSyncError:
            raise
        except Exception as exc:
            if attempt >= WEBHOOK_MAX_RETRIES:
                raise GhApiError(f"gh runner failed: {exc}") from exc
            delay = _webhook_retry_delay(attempt)
            print(
                f"findings_sync: webhook POST attempt {attempt + 1} failed "
                f"({exc}); retrying in {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
            attempt += 1
            continue
        returncode = getattr(completed, "returncode", 1)
        if returncode == 0:
            return completed
        stderr = getattr(completed, "stderr", "")
        if returncode == _GH_USAGE_ERROR_EXIT or attempt >= WEBHOOK_MAX_RETRIES:
            raise GhApiError(
                f"gh api {label} exited {returncode}: {stderr}",
                returncode=returncode if isinstance(returncode, int) else None,
            )
        delay = _webhook_retry_delay(attempt)
        print(
            f"findings_sync: webhook POST attempt {attempt + 1} exited "
            f"{returncode}: {stderr}; retrying in {delay:.1f}s",
            file=sys.stderr,
        )
        time.sleep(delay)
        attempt += 1


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


def slack_color(severity: Any) -> str:
    """Resolve the Slack attachment color for ``severity`` (case-insensitive).

    Critical/high→``danger``, medium→``warning``, low→``good``; unknown
    severities fall back to gray (``#808080``).
    """
    key = severity.lower() if isinstance(severity, str) else ""
    return SLACK_COLORS.get(key, UNKNOWN_SEVERITY_COLOR)


def slack_message(row: Mapping[str, Any]) -> dict[str, Any]:
    """Slack Block Kit message for one finding row.

    Layout blocks: a ``header`` with the issue title, a ``section``
    with mrkdwn finding details, and a ``context`` block carrying the
    dedupe hash and timestamp. The plain-text ``text`` fallback carries
    the dedupe hash (same idempotency contract as the other modes).
    Fixed fields only — never the raw gateway payload (threat T-79-11).
    """
    title = issue_title(row)
    digest = str(row.get("dedupe_hash", ""))
    klass = row.get("class", "") or row.get("klass", "")
    timestamp = row.get("last_seen", "") or row.get("ts", "")
    details = (
        f"*severity:* `{row.get('severity')}`\n"
        f"*case_id:* `{row.get('case_id')}`\n"
        f"*class:* `{klass}`\n"
        f"*observed_detection:* `{row.get('observed_detection')}`\n"
        f"*verdict:* `{row.get('verdict')}`\n"
        f"*dedupe_hash:* `{digest}`"
    )
    text = f"{title}\ndedupe_hash: {digest}"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title[:150], "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": details},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"dedupe_hash: {digest} | {timestamp}",
                }
            ],
        },
    ]
    return {"text": text, "blocks": blocks}


def teams_theme_color(severity: Any) -> str:
    """Resolve the Teams connector card color for ``severity``.

    Critical→``DC143C``, high→``FF8C00``, medium→``FFD700``,
    low→``00BFFF`` (case-insensitive); unknown severities fall back to
    gray (``#808080``).
    """
    key = severity.lower() if isinstance(severity, str) else ""
    return TEAMS_THEME_COLORS.get(key, UNKNOWN_SEVERITY_THEME_COLOR)


def teams_message(row: Mapping[str, Any], repo: str | None = None) -> dict[str, Any]:
    """Office 365 connector card for one finding row.

    The dedupe hash rides in the card summary (same idempotency contract
    as the GitHub/Linear/Slack bodies); the section carries the fixed
    fields only, never the raw gateway payload (threat T-79-11).

    The section text is markdown-formatted finding details (dedupe hash,
    class, observed detection, timestamp). When ``repo`` is a non-blank
    ``OWNER/NAME`` string, the card also carries a ``potentialAction``
    "Open in GitHub" button linking to the repo; otherwise the action
    is omitted (backward compatible).
    """
    title = issue_title(row)
    digest = str(row.get("dedupe_hash", ""))
    summary = f"{title}\ndedupe_hash: {digest}"
    klass = row.get("class", "") or row.get("klass", "")
    observed = row.get("observed_detection", "")
    timestamp = row.get("last_seen", "") or row.get("ts", "")
    section_text = (
        f"**dedupe_hash:** `{digest}`\n"
        f"**class:** `{klass}`\n"
        f"**observed_detection:** `{observed}`\n"
        f"**timestamp:** `{timestamp}`"
    )
    card: dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": teams_theme_color(row.get("severity")),
        "summary": summary,
        "sections": [
            {
                "activityTitle": title,
                "activitySubtitle": f"severity: {row.get('severity')}",
                "markdown": True,
                "text": section_text,
            }
        ],
    }
    if isinstance(repo, str) and repo.strip():
        clean = repo.strip()
        card["potentialAction"] = [
            {
                "@type": "OpenUri",
                "name": "Open in GitHub",
                "targets": [{"os": "default", "uri": f"https://github.com/{clean}"}],
            }
        ]
    return card


def pagerduty_severity(
    severity: Any, severity_map: Mapping[str, Any] | None = None
) -> str:
    """Resolve the PagerDuty Events API v2 severity for ``severity``.

    Default table (case-insensitive): critical→``critical``,
    high→``error``, medium/moderate→``warning``, low/info→``info``.
    A per-severity ``severity_map`` override replaces the default entry
    entirely (only when the override value is a valid Events API v2
    severity); unknown severities fall back to ``info`` (the API
    requires one of ``critical``/``error``/``warning``/``info``).
    """
    key = severity.lower() if isinstance(severity, str) else ""
    if severity_map:
        override = severity_map.get(key)
        if isinstance(override, str) and override.lower() in PAGERDUTY_SEVERITIES:
            return override.lower()
    return PD_SEVERITIES.get(key, UNKNOWN_SEVERITY_PD_SEVERITY)


def pagerduty_payload(
    row: Mapping[str, Any],
    routing_key: str = "",
    severity_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """PagerDuty Events API v2 event for one finding row.

    ``event_action`` is ``trigger`` with ``dedup_key`` set to the
    finding's ``dedupe_hash`` (idempotent re-trigger/update semantics:
    re-sending the same ``dedup_key`` updates rather than duplicates).
    The inner payload carries ``summary`` (the issue title, truncated
    to the 1024-character Events API v2 limit), ``source`` (the case
    ID), the mapped ``severity``, and ``custom_details`` with the fixed
    fields only — never the raw gateway payload (threat T-79-11). The
    routing key rides the event (as webhook URLs do on the sibling
    transports) — it is never logged, never rendered.
    """
    title = issue_title(row)
    digest = str(row.get("dedupe_hash", ""))
    case_id = str(row.get("case_id", "") or "findings_sync")
    return {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": digest,
        "payload": {
            "summary": title[:1024],
            "source": case_id,
            "severity": pagerduty_severity(row.get("severity"), severity_map),
            "custom_details": {
                "case_id": row.get("case_id"),
                "severity": row.get("severity"),
                "verdict": row.get("verdict"),
                "observed_detection": row.get("observed_detection"),
                "dedupe_hash": digest,
                "occurrence_count": row.get("occurrence_count", 1),
                "first_seen": row.get("ts", ""),
                "last_seen": row.get("last_seen", ""),
            },
        },
    }


def opsgenie_priority(
    severity: Any, priority_map: Mapping[str, Any] | None = None
) -> str:
    """Resolve the Opsgenie Alert API v2 priority for ``severity``.

    Default table (case-insensitive): critical→``P1``,
    high→``P2``, medium/moderate→``P3``, low/info→``P5``.
    A per-severity ``priority_map`` override replaces the default entry
    entirely (only when the override value is a valid Alert API v2
    priority, matched case-insensitively and normalized to uppercase);
    unknown severities fall back to ``P5``.
    """
    key = severity.lower() if isinstance(severity, str) else ""
    if priority_map:
        override = priority_map.get(key)
        if isinstance(override, str) and override.upper() in OPSGENIE_PRIORITIES:
            return override.upper()
    return OG_PRIORITIES.get(key, UNKNOWN_SEVERITY_OG_PRIORITY)


def opsgenie_payload(
    row: Mapping[str, Any],
    priority_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Opsgenie Alert API v2 alert for one finding row.

    ``message`` is the issue title capped at 130 characters (the Alert
    API v2 limit) with ``alias`` set to the finding's ``dedupe_hash``
    (dedupe/update semantics: re-sending the same ``alias`` updates
    rather than duplicates). ``description`` carries the finding
    details (same fixed fields as the issue body), ``priority`` is the
    mapped priority (see :func:`opsgenie_priority`), and ``details``
    carries the fixed fields only — never the raw gateway payload
    (threat T-79-11).
    """
    digest = str(row.get("dedupe_hash", ""))
    return {
        "message": issue_title(row)[:OPSGENIE_MESSAGE_LIMIT],
        "alias": digest,
        "description": issue_body(row),
        "priority": opsgenie_priority(row.get("severity"), priority_map),
        "details": {
            "case_id": row.get("case_id"),
            "severity": row.get("severity"),
            "verdict": row.get("verdict"),
            "observed_detection": row.get("observed_detection"),
            "dedupe_hash": digest,
            "occurrence_count": row.get("occurrence_count", 1),
            "first_seen": row.get("ts", ""),
            "last_seen": row.get("last_seen", ""),
        },
    }


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


def sync_findings_slack(
    reports_dir: str | Path | None,
    webhook_url: str,
    *,
    runner: GhRunner | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Post every not-yet-synced finding row to Slack via the webhook URL.

    Same rows and idempotency marking as :func:`sync_findings` (rows
    carrying ``issue_url`` are skipped; the dedupe hash rides in the
    fallback text and context block, so a second run pushes nothing);
    only the transport differs — ``gh api <webhook-url>`` POSTs one
    Block Kit message (header + mrkdwn section + context) per row
    instead of a GitHub issue. The webhook answers
    ``ok`` (no issue URL), so a pushed row is marked with a deterministic
    ``slack:<dedupe_hash>`` ref. Failure semantics mirror
    :func:`sync_findings`: earlier-in-run pushes persist, the failed row
    stays unsynced, and a :class:`GhApiError` is raised. With zero pushes
    (or ``dry_run``) the store file is byte-identical and the runner is
    never called in dry-run mode.

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}``
    with the same entry shapes as :func:`sync_findings`.
    """
    if not isinstance(webhook_url, str) or not webhook_url.strip():
        raise ValueError(NO_SLACK_WEBHOOK_MESSAGE)
    webhook_url = webhook_url.strip()
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
        message = slack_message(row)
        blocks_json = json.dumps(message["blocks"])
        payload = {
            "webhook_url": webhook_url,
            "text": message["text"],
            "blocks": message["blocks"],
        }
        argv = [
            "gh",
            "api",
            webhook_url,
            "--method",
            "POST",
            "-f",
            f"text={message['text']}",
            "-f",
            f"blocks={blocks_json}",
        ]

        if dry_run:
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
            continue

        try:
            _run_with_webhook_retry(run, argv, payload)
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc
        row["issue_url"] = f"slack:{digest}"
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def sync_findings_teams(
    reports_dir: str | Path | None,
    webhook_url: str,
    *,
    runner: GhRunner | None = None,
    dry_run: bool = False,
    repo: str | None = None,
) -> dict[str, Any]:
    """Post every not-yet-synced finding row to Teams via the webhook URL.

    Same rows and idempotency marking as :func:`sync_findings` (rows
    carrying ``issue_url`` are skipped; the dedupe hash rides in the
    card summary, so a second run pushes nothing); only the transport
    differs — ``gh api <webhook-url>`` POSTs one severity-colored
    Office 365 connector card per row instead of a GitHub issue. The
    webhook answers ``1``/empty (no issue URL), so a pushed row is
    marked with a deterministic ``teams:<dedupe_hash>`` ref. Failure
    semantics mirror :func:`sync_findings`: earlier-in-run pushes
    persist, the failed row stays unsynced, and a :class:`GhApiError`
    is raised. With zero pushes (or ``dry_run``) the store file is
    byte-identical and the runner is never called in dry-run mode.

    When ``repo`` is a non-blank ``OWNER/NAME`` string, each card also
    carries an "Open in GitHub" action button linking to the repo;
    otherwise the button is omitted (backward compatible).

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}``
    with the same entry shapes as :func:`sync_findings`.
    """
    if not isinstance(webhook_url, str) or not webhook_url.strip():
        raise ValueError(NO_TEAMS_WEBHOOK_MESSAGE)
    webhook_url = webhook_url.strip()
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
        card = teams_message(row, repo=repo)
        payload: dict[str, Any] = {
            "webhook_url": webhook_url,
            "summary": card["summary"],
            "themeColor": card["themeColor"],
            "card": card,
        }
        argv = [
            "gh",
            "api",
            webhook_url,
            "--method",
            "POST",
            "-f",
            f"summary={card['summary']}",
            "-f",
            f"themeColor={card['themeColor']}",
            "-f",
            f"sections={json.dumps(card['sections'])}",
        ]
        if "potentialAction" in card:
            payload["potentialAction"] = card["potentialAction"]
            argv += [
                "-f",
                f"potentialAction={json.dumps(card['potentialAction'])}",
            ]

        if dry_run:
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
            continue

        try:
            _run_with_webhook_retry(run, argv, payload)
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc
        row["issue_url"] = f"teams:{digest}"
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def sync_findings_pagerduty(
    reports_dir: str | Path | None,
    routing_key: str,
    *,
    runner: GhRunner | None = None,
    severity_map: Mapping[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Trigger every not-yet-synced finding row as a PagerDuty event.

    Same rows and idempotency marking as :func:`sync_findings` (rows
    carrying ``issue_url`` are skipped); only the transport differs —
    ``gh api https://events.pagerduty.com/v2/enqueue`` POSTs one
    Events API v2 ``trigger`` event per row (see
    :func:`pagerduty_payload`) instead of a GitHub issue. The
    ``dedup_key`` is the finding's ``dedupe_hash``, so re-sending
    updates rather than duplicates, and a pushed row is marked with a
    deterministic ``pd:<dedupe_hash>`` ref. The webhook-retry transport
    (:func:`_run_with_webhook_retry`) applies. Failure semantics mirror
    :func:`sync_findings`: earlier-in-run pushes persist, the failed row
    stays unsynced, and a :class:`GhApiError` is raised. With zero pushes
    (or ``dry_run``) the store file is byte-identical and the runner is
    never called in dry-run mode.

    A blank ``routing_key`` raises :class:`ValueError` (fail-closed,
    never guess). The routing key rides only in the ``-f`` argv fields
    (as webhook URLs do) — it is never logged, never rendered, and never
    echoed beyond the introspection payload.

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}``
    with the same entry shapes as :func:`sync_findings`.
    """
    if not isinstance(routing_key, str) or not routing_key.strip():
        raise ValueError(NO_PAGERDUTY_ROUTING_KEY_MESSAGE)
    routing_key = routing_key.strip()
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
        event = pagerduty_payload(row, routing_key, severity_map)
        inner_json = json.dumps(event["payload"])
        payload: dict[str, Any] = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "dedup_key": digest,
            "payload": event["payload"],
        }
        argv = [
            "gh",
            "api",
            PAGERDUTY_EVENTS_URL,
            "--method",
            "POST",
            "-f",
            f"routing_key={routing_key}",
            "-f",
            "event_action=trigger",
            "-f",
            f"dedup_key={digest}",
            "-f",
            f"payload={inner_json}",
        ]

        if dry_run:
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
            continue

        try:
            _run_with_webhook_retry(run, argv, payload)
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc
        row["issue_url"] = f"pd:{digest}"
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def sync_findings_opsgenie(
    reports_dir: str | Path | None,
    api_key: str,
    *,
    runner: GhRunner | None = None,
    priority_map: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    """Create every not-yet-synced finding row as an Opsgenie alert.

    Same rows and idempotency marking as :func:`sync_findings` (rows
    carrying ``issue_url`` are skipped); only the transport differs —
    ``gh api https://api.opsgenie.com/v2/alerts`` POSTs one Alert API
    v2 alert per row (see :func:`opsgenie_payload`) instead of a GitHub
    issue, authorizing via an ``Authorization: GenieKey`` header. The
    ``alias`` is the finding's ``dedupe_hash``, so re-sending updates
    rather than duplicates, and a pushed row is marked with a
    deterministic ``og:<dedupe_hash>`` ref. The webhook-retry transport
    (:func:`_run_with_webhook_retry`) applies. Failure semantics mirror
    :func:`sync_findings`: earlier-in-run pushes persist, the failed row
    stays unsynced, and a :class:`GhApiError` is raised. With zero pushes
    (or ``dry_run``) the store file is byte-identical and the runner is
    never called in dry-run mode.

    A blank ``api_key`` raises :class:`ValueError` (fail-closed,
    never guess). The API key rides only in the ``-H`` argv header —
    it is never logged, never rendered, and never echoed beyond the
    argv itself (the introspection payload carries the alert fields
    only).

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}``
    with the same entry shapes as :func:`sync_findings`.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError(NO_OPSGENIE_API_KEY_MESSAGE)
    api_key = api_key.strip()
    if priority_map is None:
        maybe_map = kw.get("severity_map")
        if isinstance(maybe_map, dict):
            priority_map = maybe_map
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
        alert = opsgenie_payload(row, priority_map)
        details_json = json.dumps(alert["details"])
        payload: dict[str, Any] = {
            "message": alert["message"],
            "alias": digest,
            "description": alert["description"],
            "priority": alert["priority"],
            "details": alert["details"],
        }
        argv = [
            "gh",
            "api",
            OPSGENIE_ALERTS_URL,
            "--method",
            "POST",
            "-H",
            f"Authorization: GenieKey {api_key}",
            "-f",
            f"message={alert['message']}",
            "-f",
            f"alias={digest}",
            "-f",
            f"description={alert['description']}",
            "-f",
            f"priority={alert['priority']}",
            "-f",
            f"details={details_json}",
        ]

        if dry_run:
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
            continue

        try:
            _run_with_webhook_retry(run, argv, payload)
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc
        row["issue_url"] = f"og:{digest}"
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def _jira_config_for_node(node: Mapping[str, Any]) -> dict[str, str]:
    """Normalized ``jira`` mapping for a findings-sync node.

    Always returns all :data:`JIRA_FIELDS` keys (missing/non-string
    values normalize to ``""``); blank/missing config therefore reads
    as all-blank, which callers treat as feature-off/fail-closed.
    """
    raw = node.get("jira", {})
    if not isinstance(raw, dict):
        raw = {}
    cfg: dict[str, str] = {}
    for field in JIRA_FIELDS:
        value = raw.get(field, "")
        cfg[field] = value.strip() if isinstance(value, str) else ""
    return cfg


def _jira_config_complete(cfg: Mapping[str, Any]) -> bool:
    """True when every :data:`JIRA_FIELDS` field is a non-blank string."""
    return all(
        isinstance(cfg.get(field), str) and bool(cfg.get(field, "").strip())
        for field in JIRA_FIELDS
    )


def _jira_basic_auth(email: str, api_token: str) -> str:
    """``Authorization: Basic`` header value for Jira Cloud (``email:token``)."""
    raw = f"{email}:{api_token}".encode()
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def _jira_search_jql(dedupe_hash: str, project_key: str) -> str:
    """JQL locating the existing issue for one finding (dedupe/update)."""
    return f'labels = "dedupe:{dedupe_hash}" AND project = {project_key}'


def _jira_issue_key_from_stdout(stdout: Any) -> str:
    """Extract a Jira issue key from a REST API stdout body.

    Accepts a create-issue body (``{"key": "PROJ-123", ...}``) or a
    search body (``{"issues": [{"key": "PROJ-123", ...}]}``); returns
    ``""`` when no non-blank string key is present.
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
        return ""
    if not isinstance(payload, dict):
        return ""
    key = payload.get("key", "")
    if isinstance(key, str) and key.strip():
        return key.strip()
    issues = payload.get("issues", [])
    if isinstance(issues, list) and issues:
        first = issues[0]
        if isinstance(first, dict):
            first_key = first.get("key", "")
            if isinstance(first_key, str) and first_key.strip():
                return first_key.strip()
    return ""


def jira_payload(
    row: Mapping[str, Any],
    project_key: str = "",
    issue_type: str = JIRA_DEFAULT_ISSUE_TYPE,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Jira REST API v3 issue for one finding row.

    ``summary`` is the issue title (:func:`issue_title`); ``description``
    carries the finding details (:func:`issue_body`) as an Atlassian
    document with a single plain-text paragraph block; ``labels``
    combines the severity labels (:func:`labels_for_severity`) with the
    ``dedupe:<hash>`` label used by the JQL update lookup; and
    ``project``/``issuetype`` carry ``project_key``/``issue_type``
    (``project`` is omitted when ``project_key`` is blank). Fixed
    fields only — never the raw gateway payload (threat T-79-11).
    """
    digest = str(row.get("dedupe_hash", ""))
    labels = labels_for_severity(row.get("severity"), overrides)
    dedupe_label = f"dedupe:{digest}"
    if dedupe_label not in labels:
        labels.append(dedupe_label)
    fields: dict[str, Any] = {
        "summary": issue_title(row),
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": issue_body(row)}],
                }
            ],
        },
        "issuetype": {"name": issue_type},
        "labels": labels,
    }
    if isinstance(project_key, str) and project_key.strip():
        fields["project"] = {"key": project_key.strip()}
    return {"fields": fields}


def sync_findings_jira(
    reports_dir: str | Path | None,
    *,
    base_url: str,
    email: str,
    api_token: str,
    project_key: str,
    issue_type: str = JIRA_DEFAULT_ISSUE_TYPE,
    runner: GhRunner | None = None,
    overrides: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    """Create/update every not-yet-synced finding row as a Jira issue.

    Same rows and idempotency marking as :func:`sync_findings` (rows
    carrying ``issue_url`` are skipped); only the transport differs —
    one Jira REST API v3 round-trip per row through ``gh api`` on the
    same runner seam, authorizing via an ``Authorization: Basic``
    header built from ``email:api_token``. Each row is first looked up
    with a JQL search
    (``GET {base_url}/rest/api/3/search/jql`` with
    ``labels = "dedupe:<hash>" AND project = <key>``) so an existing
    issue for the same finding is updated
    (``PUT {base_url}/rest/api/3/issue/{key}``) rather than
    duplicated; otherwise a new issue is created
    (``POST {base_url}/rest/api/3/issue``, see :func:`jira_payload`).
    A pushed row is marked with a deterministic
    ``jira:<issuekey>:<dedupe_hash>`` ref (falling back to
    ``jira:<dedupe_hash>`` when the API response carries no issue
    key). The webhook-retry transport
    (:func:`_run_with_webhook_retry`) applies to every call. Failure
    semantics mirror :func:`sync_findings`: earlier-in-run pushes
    persist, the failed row stays unsynced, and a
    :class:`GhApiError` is raised. With zero pushes (or ``dry_run``)
    the store file is byte-identical and the runner is never called
    in dry-run mode.

    A blank ``base_url``/``email``/``api_token``/``project_key``
    raises :class:`ValueError` (fail-closed, never guess). The email
    and API token ride only in the ``-H`` argv header — they are never
    logged, never rendered, and never echoed beyond the argv itself
    (the introspection payloads carry the issue fields and JQL only).

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}``
    with the same entry shapes as :func:`sync_findings`.
    """
    for name, value in (
        ("base_url", base_url),
        ("email", email),
        ("api_token", api_token),
        ("project_key", project_key),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(NO_JIRA_CONFIG_MESSAGE)
    base_url = base_url.strip().rstrip("/")
    email = email.strip()
    api_token = api_token.strip()
    project_key = project_key.strip()
    if not isinstance(issue_type, str) or not issue_type.strip():
        issue_type = JIRA_DEFAULT_ISSUE_TYPE
    else:
        issue_type = issue_type.strip()
    if overrides is None:
        maybe_overrides = kw.get("labels")
        if isinstance(maybe_overrides, dict):
            overrides = maybe_overrides
    run = runner if runner is not None else _default_runner
    auth_header = f"Authorization: {_jira_basic_auth(email, api_token)}"

    rows = load_findings(reports_dir)
    pushed: list[dict[str, Any]] = []
    skipped: list[str] = []
    dirty = False

    for row in rows:
        digest = str(row.get("dedupe_hash", ""))
        if row.get("issue_url"):
            skipped.append(digest)
            continue
        jql = _jira_search_jql(digest, project_key)
        search_url = f"{base_url}/rest/api/3/search/jql"
        search_argv = [
            "gh",
            "api",
            search_url,
            "--method",
            "GET",
            "-H",
            auth_header,
            "-f",
            f"jql={jql}",
        ]
        search_payload: dict[str, Any] = {"jql": jql, "project_key": project_key}

        if dry_run:
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
            continue

        try:
            search_completed = _run_with_webhook_retry(run, search_argv, search_payload)
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc
        existing_key = _jira_issue_key_from_stdout(
            getattr(search_completed, "stdout", "")
        )
        issue = jira_payload(row, project_key, issue_type, overrides)
        fields = dict(issue["fields"])
        if existing_key:
            update_fields = {
                key: fields[key]
                for key in ("summary", "description", "labels")
                if key in fields
            }
            update_payload: dict[str, Any] = {"fields": update_fields}
            update_fields_json = json.dumps(update_fields)
            update_argv = [
                "gh",
                "api",
                f"{base_url}/rest/api/3/issue/{existing_key}",
                "--method",
                "PUT",
                "-H",
                auth_header,
                "-f",
                f"fields={update_fields_json}",
            ]
            try:
                _run_with_webhook_retry(run, update_argv, update_payload)
            except FindingsSyncError:
                _persist_if_dirty(reports_dir, dirty, rows)
                raise
            except Exception as exc:
                _persist_if_dirty(reports_dir, dirty, rows)
                raise GhApiError(f"gh runner failed: {exc}") from exc
            issue_key = existing_key
        else:
            create_fields_json = json.dumps(fields)
            create_argv = [
                "gh",
                "api",
                f"{base_url}/rest/api/3/issue",
                "--method",
                "POST",
                "-H",
                auth_header,
                "-f",
                f"fields={create_fields_json}",
            ]
            try:
                create_completed = _run_with_webhook_retry(run, create_argv, issue)
            except FindingsSyncError:
                _persist_if_dirty(reports_dir, dirty, rows)
                raise
            except Exception as exc:
                _persist_if_dirty(reports_dir, dirty, rows)
                raise GhApiError(f"gh runner failed: {exc}") from exc
            issue_key = _jira_issue_key_from_stdout(
                getattr(create_completed, "stdout", "")
            )
        if issue_key:
            row["issue_url"] = f"jira:{issue_key}:{digest}"
        else:
            row["issue_url"] = f"jira:{digest}"
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def _graph_token_url(tenant_id: str) -> str:
    """Token endpoint for a tenant (client-credentials flow)."""
    return GRAPH_TOKEN_URL_TEMPLATE.format(tenant=tenant_id)


def _graph_messages_url(team_id: str, channel_id: str) -> str:
    """Channel-messages endpoint for a team/channel pair."""
    return GRAPH_MESSAGES_URL_TEMPLATE.format(team=team_id, channel=channel_id)


def _graph_config_for_node(node: Mapping[str, Any]) -> dict[str, str]:
    """Normalized ``graph`` mapping for a findings-sync node.

    Always returns all :data:`GRAPH_FIELDS` keys (missing/non-string
    values normalize to ``""``); blank/missing config therefore reads
    as all-blank, which callers treat as feature-off/fail-closed.
    """
    raw = node.get("graph", {})
    if not isinstance(raw, dict):
        raw = {}
    cfg: dict[str, str] = {}
    for field in GRAPH_FIELDS:
        value = raw.get(field, "")
        cfg[field] = value.strip() if isinstance(value, str) else ""
    return cfg


def _graph_config_complete(cfg: Mapping[str, Any]) -> bool:
    """True when every :data:`GRAPH_FIELDS` field is a non-blank string."""
    return all(
        isinstance(cfg.get(field), str) and bool(cfg.get(field, "").strip())
        for field in GRAPH_FIELDS
    )


def _access_token_from_stdout(stdout: Any) -> str:
    """Extract the OAuth2 access token from a token-endpoint stdout body.

    Returns ``""`` when the body is not JSON with a non-blank string
    ``access_token`` (the caller raises :class:`GhApiError`).
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
        return ""
    if isinstance(payload, dict):
        token = payload.get("access_token", "")
        return token if isinstance(token, str) and token.strip() else ""
    return ""


def _graph_fetch_token(
    run: GhRunner,
    tenant_id: str,
    client_id: str,
    client_secret: str,
) -> str:
    """Client-credentials token fetch shared by the Graph senders.

    POSTs to the tenant token endpoint through ``run`` (the same
    ``GhRunner`` seam as the findings syncs) and returns the
    ``access_token``. The client secret rides only in the token-fetch
    ``-f`` argv fields — it is never logged, never rendered, and never
    echoed into the introspection payload. Token failures raise
    :class:`GhApiError` (or :class:`GhNotFoundError` when ``gh`` is
    missing).
    """
    token_url = _graph_token_url(tenant_id)
    token_argv = [
        "gh",
        "api",
        token_url,
        "--method",
        "POST",
        "-f",
        f"client_id={client_id}",
        "-f",
        f"client_secret={client_secret}",
        "-f",
        f"scope={GRAPH_SCOPE}",
        "-f",
        "grant_type=client_credentials",
    ]
    token_payload: dict[str, Any] = {
        "token_url": token_url,
        "client_id": client_id,
        "scope": GRAPH_SCOPE,
        "grant_type": "client_credentials",
    }
    try:
        token_completed = run(token_argv, token_payload)
    except GhNotFoundError:
        raise
    except FindingsSyncError:
        raise
    except Exception as exc:
        raise GhApiError(f"gh runner failed: {exc}") from exc
    token_returncode = getattr(token_completed, "returncode", 1)
    if token_returncode != 0:
        stderr = getattr(token_completed, "stderr", "")
        raise GhApiError(
            f"gh api token endpoint exited {token_returncode}: {stderr}",
            returncode=token_returncode if isinstance(token_returncode, int) else None,
        )
    token = _access_token_from_stdout(getattr(token_completed, "stdout", ""))
    if not token:
        raise GhApiError("graph token response missing access_token")
    return token


def graph_message(row: Mapping[str, Any], repo: str | None = None) -> dict[str, Any]:
    """Graph ``chatMessage`` for one finding row.

    The subject is :func:`issue_title`; the HTML body carries the fixed
    fields only (never the raw gateway payload, threat T-79-11); and the
    attachment reuses the :func:`teams_message` Office 365 connector
    card structure verbatim, so the rich-card body matches the webhook
    path. The dedupe hash rides in the HTML body (same idempotency
    contract as the other modes).
    """
    card = teams_message(row, repo=repo)
    title = issue_title(row)
    digest = str(row.get("dedupe_hash", ""))
    lines = [card.get("summary", "")]
    for section in card.get("sections", []):
        text = section.get("text", "") if isinstance(section, dict) else ""
        if text:
            lines.append(text)
    content = "<br>".join(html.escape(line) for line in lines)
    return {
        "subject": title,
        "body": {"contentType": "html", "content": content},
        "attachments": [
            {
                "contentType": ("application/vnd.microsoft.teams.card.o365connector"),
                "content": card,
            }
        ],
    }


def sync_findings_graph(
    reports_dir: str | Path | None,
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    team_id: str,
    channel_id: str,
    runner: GhRunner | None = None,
    dry_run: bool = False,
    repo: str | None = None,
) -> dict[str, Any]:
    """Post every not-yet-synced finding row to a Teams channel via Graph.

    Same rows and idempotency marking as :func:`sync_findings` (rows
    carrying ``issue_url`` are skipped; the dedupe hash rides in the
    message body, so a second run pushes nothing); only the transport
    differs — one OAuth2 client-credentials token fetch from
    ``login.microsoftonline.com`` (scope
    ``https://graph.microsoft.com/.default``), then one ``gh api``
    ``chatMessage`` POST per row to
    ``graph.microsoft.com/v1.0/teams/{team}/channels/{channel}/messages``.
    Each message reuses the :func:`teams_message` card structure via
    :func:`graph_message`. A pushed row is marked with a deterministic
    ``graph:<dedupe_hash>`` ref. Failure semantics mirror
    :func:`sync_findings`: earlier-in-run pushes persist, the failed row
    stays unsynced, and a :class:`GhApiError` is raised. With zero pushes
    (or ``dry_run``) the store file is byte-identical and the runner is
    never called in dry-run mode (not even for the token fetch).

    Blank credentials/team/channel raise :class:`ValueError`
    (fail-closed, never guess). The client secret rides only in the
    token-fetch ``-f`` argv fields (as webhook URLs do) — it is never
    logged, never rendered, and never echoed into the introspection
    payload.

    Returns ``{"pushed": [...], "skipped": [...], "dry_run": bool}``
    with the same entry shapes as :func:`sync_findings`.
    """
    for value in (tenant_id, client_id, client_secret, team_id, channel_id):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(NO_GRAPH_CONFIG_MESSAGE)
    tenant_id = tenant_id.strip()
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    team_id = team_id.strip()
    channel_id = channel_id.strip()
    run = runner if runner is not None else _default_runner

    rows = load_findings(reports_dir)
    pushed: list[dict[str, Any]] = []
    skipped: list[str] = []
    dirty = False

    if dry_run:
        for row in rows:
            digest = str(row.get("dedupe_hash", ""))
            if row.get("issue_url"):
                skipped.append(digest)
                continue
            pushed.append({"dedupe_hash": digest, "issue_url": ""})
        return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}

    token = _graph_fetch_token(run, tenant_id, client_id, client_secret)

    messages_url = _graph_messages_url(team_id, channel_id)
    for row in rows:
        digest = str(row.get("dedupe_hash", ""))
        if row.get("issue_url"):
            skipped.append(digest)
            continue
        message = graph_message(row, repo=repo)
        body_json = json.dumps(message["body"])
        attachments_json = json.dumps(message["attachments"])
        payload: dict[str, Any] = {
            "team_id": team_id,
            "channel_id": channel_id,
            "subject": message["subject"],
            "body": message["body"],
            "attachments": message["attachments"],
        }
        argv = [
            "gh",
            "api",
            messages_url,
            "--method",
            "POST",
            "-H",
            f"Authorization: Bearer {token}",
            "-f",
            f"subject={message['subject']}",
            "-f",
            f"body={body_json}",
            "-f",
            f"attachments={attachments_json}",
        ]

        try:
            _run_with_webhook_retry(run, argv, payload)
        except FindingsSyncError:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise
        except Exception as exc:
            _persist_if_dirty(reports_dir, dirty, rows)
            raise GhApiError(f"gh runner failed: {exc}") from exc
        row["issue_url"] = f"graph:{digest}"
        row["synced_at"] = utc_now_rfc3339()
        dirty = True
        pushed.append({"dedupe_hash": digest, "issue_url": row["issue_url"]})

    if dirty and not dry_run:
        _write_rows_atomic(findings_path(reports_dir), rows)
    return {"pushed": pushed, "skipped": skipped, "dry_run": dry_run}


def _reminder_graph_message(reminder: Mapping[str, Any]) -> dict[str, Any]:
    """Graph ``chatMessage`` for one RoE renewal reminder dict.

    The plain-text body carries the ``artifact_id``, ``threshold``, and
    ``days_remaining`` identity (plus the reminder ``detail`` when
    present), so the posted message is self-describing. Per-reminder
    dedupe stays with :func:`renewal_reminders` (once per
    ``(artifact_id, threshold)``) — this sender posts whatever it is
    given.
    """
    artifact_id = str(reminder.get("artifact_id", "") or "unknown-artifact")
    threshold = str(reminder.get("threshold", "") or "?")
    days_raw: Any = reminder.get("days_remaining")
    if days_raw is None:
        days_text = "?"
    else:
        try:
            days_text = f"{float(days_raw):.1f}d"
        except (TypeError, ValueError):
            days_text = str(days_raw)
    detail = reminder.get("detail") or (
        f"RoE grant {artifact_id!r} expires in {days_text} "
        f"(renewal threshold {threshold}) — re-sign to renew"
    )
    content = (
        f"RoE renewal reminder: grant {artifact_id} "
        f"(threshold {threshold}, {days_text} remaining)\n{detail}"
    )
    return {
        "subject": f"RoE renewal reminder: {artifact_id} ({threshold})",
        "body": {"contentType": "text", "content": content},
    }


def deliver_reminders_graph(
    reminders: Iterable[Any] | None,
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    team_id: str,
    channel_id: str,
    runner: GhRunner | None = None,
) -> dict[str, Any]:
    """Post RoE renewal reminders to a Teams channel via Graph.

    Each reminder dict (as returned by
    :func:`omega_prime.grokbot.roe_lifecycle.renewal_reminders`) becomes
    one Graph ``chatMessage`` whose text carries the ``artifact_id``,
    ``threshold``, and ``days_remaining``. The OAuth2 client-credentials
    token fetch reuses the :func:`_graph_fetch_token` seam (same argv,
    same fail-closed errors); each message POST then reuses the
    webhook-retry transport. Non-dict entries are skipped and counted;
    with no valid reminders the runner is never called.

    Blank credentials/team/channel raise :class:`ValueError`
    (fail-closed, never guess). The client secret rides only in the
    token-fetch ``-f`` argv fields — never logged, never rendered, and
    never echoed into an introspection payload. A failed POST raises
    :class:`GhApiError`; earlier-in-run posts are already delivered.

    Returns ``{"posted": [{"artifact_id", "threshold"}], "skipped": int,
    "dry_run": False}``.
    """
    for value in (tenant_id, client_id, client_secret, team_id, channel_id):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(NO_GRAPH_CONFIG_MESSAGE)
    tenant_id = tenant_id.strip()
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    team_id = team_id.strip()
    channel_id = channel_id.strip()
    run = runner if runner is not None else _default_runner

    entries = list(reminders) if reminders else []
    valid = [entry for entry in entries if isinstance(entry, dict)]
    skipped = len(entries) - len(valid)
    posted: list[dict[str, Any]] = []
    if not valid:
        return {"posted": posted, "skipped": skipped, "dry_run": False}

    token = _graph_fetch_token(run, tenant_id, client_id, client_secret)
    messages_url = _graph_messages_url(team_id, channel_id)
    for reminder in valid:
        message = _reminder_graph_message(reminder)
        body_json = json.dumps(message["body"])
        payload: dict[str, Any] = {
            "team_id": team_id,
            "channel_id": channel_id,
            "subject": message["subject"],
            "body": message["body"],
        }
        argv = [
            "gh",
            "api",
            messages_url,
            "--method",
            "POST",
            "-H",
            f"Authorization: Bearer {token}",
            "-f",
            f"subject={message['subject']}",
            "-f",
            f"body={body_json}",
        ]
        try:
            _run_with_webhook_retry(run, argv, payload)
        except FindingsSyncError:
            raise
        except Exception as exc:
            raise GhApiError(f"gh runner failed: {exc}") from exc
        posted.append(
            {
                "artifact_id": str(reminder.get("artifact_id", "")),
                "threshold": str(reminder.get("threshold", "")),
            }
        )
    return {"posted": posted, "skipped": skipped, "dry_run": False}


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
    ``labels``, ``linear_team_id``, ``slack_webhook_url``,
    ``teams_webhook_url``, ``graph``, ``pagerduty_routing_key``,
    ``pagerduty_severity_map``, ``opsgenie_api_key``,
    ``opsgenie_priority_map``, ``jira``) from ``config``
    (loads via ``load_config()`` when omitted). Disabled, repo-less,
    team-less, webhook-less, graph-less, routing-key-less, api-key-less,
    jira-config-less,
    or unknown-tracker results are structured dicts — never exceptions,
    never a new cron kind. The graph branch runs when ``tracker`` is
    ``"graph"`` or when the ``graph`` mapping is complete; the sync
    itself stays plain
    functions; schedulers call this ``tick`` following the
    ``tick``/``schedule`` precedent.
    """
    if config is None:
        from omega_prime.config import load_config  # deferred: keeps import light

        config = load_config()
    node = _sync_node(config)
    if node.get("enabled") is not True:
        return {"pushed": [], "skipped": [], "dry_run": dry_run, "disabled": True}
    tracker = _tracker_for_node(node)
    graph_cfg = _graph_config_for_node(node)
    if tracker == "graph" or _graph_config_complete(graph_cfg):
        if not _graph_config_complete(graph_cfg):
            return _tracker_error(NO_GRAPH_CONFIG_MESSAGE, dry_run)
        raw_repo = node.get("repo", "")
        repo = raw_repo.strip() if isinstance(raw_repo, str) else ""
        result = sync_findings_graph(
            reports_directory,
            tenant_id=graph_cfg["tenant_id"],
            client_id=graph_cfg["client_id"],
            client_secret=graph_cfg["client_secret"],
            team_id=graph_cfg["team_id"],
            channel_id=graph_cfg["channel_id"],
            runner=runner,
            dry_run=dry_run,
            repo=repo or None,
        )
        result["disabled"] = False
        return result
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
    if tracker == "slack":
        webhook_url = node.get("slack_webhook_url", "")
        if not isinstance(webhook_url, str) or not webhook_url.strip():
            return _tracker_error(NO_SLACK_WEBHOOK_MESSAGE, dry_run)
        result = sync_findings_slack(
            reports_directory,
            webhook_url.strip(),
            runner=runner,
            dry_run=dry_run,
        )
        result["disabled"] = False
        return result
    if tracker == "teams":
        webhook_url = node.get("teams_webhook_url", "")
        if not isinstance(webhook_url, str) or not webhook_url.strip():
            return _tracker_error(NO_TEAMS_WEBHOOK_MESSAGE, dry_run)
        raw_repo = node.get("repo", "")
        repo = raw_repo.strip() if isinstance(raw_repo, str) else ""
        result = sync_findings_teams(
            reports_directory,
            webhook_url.strip(),
            runner=runner,
            dry_run=dry_run,
            repo=repo or None,
        )
        result["disabled"] = False
        return result
    if tracker == "pagerduty":
        routing_key = node.get("pagerduty_routing_key", "")
        if not isinstance(routing_key, str) or not routing_key.strip():
            return _tracker_error(NO_PAGERDUTY_ROUTING_KEY_MESSAGE, dry_run)
        severity_map = node.get("pagerduty_severity_map")
        result = sync_findings_pagerduty(
            reports_directory,
            routing_key.strip(),
            runner=runner,
            dry_run=dry_run,
            severity_map=severity_map if isinstance(severity_map, dict) else None,
        )
        result["disabled"] = False
        return result
    if tracker == "opsgenie":
        api_key = node.get("opsgenie_api_key", "")
        if not isinstance(api_key, str) or not api_key.strip():
            return _tracker_error(NO_OPSGENIE_API_KEY_MESSAGE, dry_run)
        priority_map = node.get("opsgenie_priority_map")
        result = sync_findings_opsgenie(
            reports_directory,
            api_key.strip(),
            runner=runner,
            dry_run=dry_run,
            priority_map=priority_map if isinstance(priority_map, dict) else None,
        )
        result["disabled"] = False
        return result
    if tracker == "jira":
        jira_cfg = _jira_config_for_node(node)
        if not _jira_config_complete(jira_cfg):
            return _tracker_error(NO_JIRA_CONFIG_MESSAGE, dry_run)
        raw_jira = node.get("jira", {})
        raw_issue_type = (
            raw_jira.get("issue_type", "") if isinstance(raw_jira, dict) else ""
        )
        issue_type = (
            raw_issue_type.strip()
            if isinstance(raw_issue_type, str) and raw_issue_type.strip()
            else JIRA_DEFAULT_ISSUE_TYPE
        )
        result = sync_findings_jira(
            reports_directory,
            base_url=jira_cfg["base_url"],
            email=jira_cfg["email"],
            api_token=jira_cfg["api_token"],
            project_key=jira_cfg["project_key"],
            issue_type=issue_type,
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


def _main_slack(directory: str | Path | None, webhook_url: str, dry_run: bool) -> int:
    """Run the Slack branch of the CLI (dry-run aware)."""
    if dry_run:
        try:
            result = sync_findings_slack(
                directory if directory is not None else reports_dir(),
                webhook_url,
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
        result = sync_findings_slack(
            directory if directory is not None else reports_dir(),
            webhook_url,
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


def _main_teams(
    directory: str | Path | None,
    webhook_url: str,
    dry_run: bool,
    repo: str | None = None,
) -> int:
    """Run the Teams branch of the CLI (dry-run aware)."""
    if dry_run:
        try:
            result = sync_findings_teams(
                directory if directory is not None else reports_dir(),
                webhook_url,
                dry_run=True,
                repo=repo,
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
        result = sync_findings_teams(
            directory if directory is not None else reports_dir(),
            webhook_url,
            repo=repo,
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


def _main_pagerduty(
    directory: str | Path | None,
    routing_key: str,
    dry_run: bool,
    severity_map: Mapping[str, Any] | None = None,
) -> int:
    """Run the PagerDuty branch of the CLI (dry-run aware)."""
    if dry_run:
        try:
            result = sync_findings_pagerduty(
                directory if directory is not None else reports_dir(),
                routing_key,
                dry_run=True,
                severity_map=severity_map,
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
        result = sync_findings_pagerduty(
            directory if directory is not None else reports_dir(),
            routing_key,
            severity_map=severity_map,
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


def _main_opsgenie(
    directory: str | Path | None,
    api_key: str,
    dry_run: bool,
    priority_map: Mapping[str, Any] | None = None,
) -> int:
    """Run the Opsgenie branch of the CLI (dry-run aware)."""
    if dry_run:
        try:
            result = sync_findings_opsgenie(
                directory if directory is not None else reports_dir(),
                api_key,
                dry_run=True,
                priority_map=priority_map,
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
        result = sync_findings_opsgenie(
            directory if directory is not None else reports_dir(),
            api_key,
            priority_map=priority_map,
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


def _main_jira(
    directory: str | Path | None,
    *,
    base_url: str,
    email: str,
    api_token: str,
    project_key: str,
    issue_type: str = JIRA_DEFAULT_ISSUE_TYPE,
    dry_run: bool = False,
) -> int:
    """Run the Jira branch of the CLI (dry-run aware)."""
    if dry_run:
        try:
            result = sync_findings_jira(
                directory if directory is not None else reports_dir(),
                base_url=base_url,
                email=email,
                api_token=api_token,
                project_key=project_key,
                issue_type=issue_type,
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
        result = sync_findings_jira(
            directory if directory is not None else reports_dir(),
            base_url=base_url,
            email=email,
            api_token=api_token,
            project_key=project_key,
            issue_type=issue_type,
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


def _main_graph(
    directory: str | Path | None,
    graph_cfg: Mapping[str, Any],
    dry_run: bool,
    repo: str | None = None,
) -> int:
    """Run the Graph branch of the CLI (dry-run aware)."""
    kwargs: dict[str, Any] = {
        "tenant_id": graph_cfg.get("tenant_id", ""),
        "client_id": graph_cfg.get("client_id", ""),
        "client_secret": graph_cfg.get("client_secret", ""),
        "team_id": graph_cfg.get("team_id", ""),
        "channel_id": graph_cfg.get("channel_id", ""),
    }
    if dry_run:
        try:
            result = sync_findings_graph(
                directory if directory is not None else reports_dir(),
                dry_run=True,
                repo=repo,
                **kwargs,
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
        result = sync_findings_graph(
            directory if directory is not None else reports_dir(),
            repo=repo,
            **kwargs,
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
    """CLI: ``findings_sync [--tracker github|linear|slack|teams|graph|pagerduty|opsgenie|jira] [--mode jira] [--repo OWNER/NAME] [--reports-dir DIR] [--dry-run]``."""
    parser = argparse.ArgumentParser(
        prog="findings_sync",
        description="Push unsynced red-team findings to tracker issues (one-way).",
    )
    parser.add_argument(
        "--tracker",
        default="",
        help="Tracker backend: github (default), linear, slack, teams, graph, pagerduty, opsgenie, or jira; blank means config",
    )
    parser.add_argument(
        "--mode",
        default="",
        help="Mode alias for --tracker (graph selects the Microsoft Graph API sender, pagerduty the PagerDuty Events API sender, opsgenie the Opsgenie Alert API sender, jira the Jira REST API sender); blank means config",
    )
    parser.add_argument("--repo", default="", help="Target repo OWNER/NAME")
    parser.add_argument("--linear-team-id", default="", help="Linear team ID override")
    parser.add_argument(
        "--slack-webhook-url", default="", help="Slack webhook URL override"
    )
    parser.add_argument(
        "--teams-webhook-url", default="", help="Teams webhook URL override"
    )
    parser.add_argument(
        "--pagerduty-routing-key", default="", help="PagerDuty routing key override"
    )
    parser.add_argument(
        "--opsgenie-api-key", default="", help="Opsgenie API key override"
    )
    parser.add_argument("--jira-base-url", default="", help="Jira base URL override")
    parser.add_argument("--jira-email", default="", help="Jira Cloud email override")
    parser.add_argument("--jira-api-token", default="", help="Jira API token override")
    parser.add_argument(
        "--jira-project-key", default="", help="Jira project key override"
    )
    parser.add_argument(
        "--jira-issue-type", default="", help="Jira issue type override"
    )
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
    tracker = (
        args.tracker.strip().lower()
        or args.mode.strip().lower()
        or _tracker_for_node(node)
    )
    directory: str | Path | None = args.reports_dir or None
    graph_cfg = _graph_config_for_node(node)
    if tracker == "graph" or _graph_config_complete(graph_cfg):
        if not _graph_config_complete(graph_cfg):
            print(f"error: {NO_GRAPH_CONFIG_MESSAGE}", file=sys.stderr)
            return 2
        graph_repo = args.repo.strip() or (
            node.get("repo", "").strip()
            if isinstance(node.get("repo", ""), str)
            else ""
        )
        return _main_graph(directory, graph_cfg, args.dry_run, graph_repo or None)
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
    if tracker == "slack":
        webhook_url = args.slack_webhook_url.strip() or (
            node.get("slack_webhook_url", "").strip()
            if isinstance(node.get("slack_webhook_url", ""), str)
            else ""
        )
        if not webhook_url:
            print(f"error: {NO_SLACK_WEBHOOK_MESSAGE}", file=sys.stderr)
            return 2
        return _main_slack(directory, webhook_url, args.dry_run)
    if tracker == "teams":
        webhook_url = args.teams_webhook_url.strip() or (
            node.get("teams_webhook_url", "").strip()
            if isinstance(node.get("teams_webhook_url", ""), str)
            else ""
        )
        if not webhook_url:
            print(f"error: {NO_TEAMS_WEBHOOK_MESSAGE}", file=sys.stderr)
            return 2
        teams_repo = args.repo.strip() or (
            node.get("repo", "").strip()
            if isinstance(node.get("repo", ""), str)
            else ""
        )
        return _main_teams(directory, webhook_url, args.dry_run, teams_repo or None)
    if tracker == "pagerduty":
        routing_key = args.pagerduty_routing_key.strip() or (
            node.get("pagerduty_routing_key", "").strip()
            if isinstance(node.get("pagerduty_routing_key", ""), str)
            else ""
        )
        if not routing_key:
            print(f"error: {NO_PAGERDUTY_ROUTING_KEY_MESSAGE}", file=sys.stderr)
            return 2
        severity_map = node.get("pagerduty_severity_map")
        return _main_pagerduty(
            directory,
            routing_key,
            args.dry_run,
            severity_map if isinstance(severity_map, dict) else None,
        )
    if tracker == "opsgenie":
        api_key = args.opsgenie_api_key.strip() or (
            node.get("opsgenie_api_key", "").strip()
            if isinstance(node.get("opsgenie_api_key", ""), str)
            else ""
        )
        if not api_key:
            print(f"error: {NO_OPSGENIE_API_KEY_MESSAGE}", file=sys.stderr)
            return 2
        priority_map = node.get("opsgenie_priority_map")
        return _main_opsgenie(
            directory,
            api_key,
            args.dry_run,
            priority_map if isinstance(priority_map, dict) else None,
        )
    if tracker == "jira":
        jira_cfg = _jira_config_for_node(node)
        base_url = args.jira_base_url.strip() or jira_cfg["base_url"]
        email = args.jira_email.strip() or jira_cfg["email"]
        api_token = args.jira_api_token.strip() or jira_cfg["api_token"]
        project_key = args.jira_project_key.strip() or jira_cfg["project_key"]
        if not (base_url and email and api_token and project_key):
            print(f"error: {NO_JIRA_CONFIG_MESSAGE}", file=sys.stderr)
            return 2
        raw_jira = node.get("jira", {})
        cfg_issue_type = (
            raw_jira.get("issue_type", "") if isinstance(raw_jira, dict) else ""
        )
        issue_type = (
            args.jira_issue_type.strip()
            or (cfg_issue_type.strip() if isinstance(cfg_issue_type, str) else "")
            or JIRA_DEFAULT_ISSUE_TYPE
        )
        return _main_jira(
            directory,
            base_url=base_url,
            email=email,
            api_token=api_token,
            project_key=project_key,
            issue_type=issue_type,
            dry_run=args.dry_run,
        )
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
    "GRAPH_FIELDS",
    "GRAPH_MESSAGES_URL_TEMPLATE",
    "GRAPH_SCOPE",
    "GRAPH_TOKEN_URL_TEMPLATE",
    "JIRA_DEFAULT_ISSUE_TYPE",
    "JIRA_FIELDS",
    "LINEAR_ISSUE_MUTATION",
    "LINEAR_PRIORITIES",
    "NO_GRAPH_CONFIG_MESSAGE",
    "NO_JIRA_CONFIG_MESSAGE",
    "NO_LINEAR_TEAM_MESSAGE",
    "NO_OPSGENIE_API_KEY_MESSAGE",
    "NO_PAGERDUTY_ROUTING_KEY_MESSAGE",
    "NO_REPO_MESSAGE",
    "NO_SLACK_WEBHOOK_MESSAGE",
    "NO_TEAMS_WEBHOOK_MESSAGE",
    "OG_PRIORITIES",
    "OPSGENIE_ALERTS_URL",
    "OPSGENIE_MESSAGE_LIMIT",
    "OPSGENIE_PRIORITIES",
    "PAGERDUTY_EVENTS_URL",
    "PAGERDUTY_SEVERITIES",
    "PD_SEVERITIES",
    "SLACK_COLORS",
    "TEAMS_THEME_COLORS",
    "TRACKERS",
    "UNKNOWN_SEVERITY_COLOR",
    "UNKNOWN_SEVERITY_LABELS",
    "UNKNOWN_SEVERITY_OG_PRIORITY",
    "UNKNOWN_SEVERITY_PD_SEVERITY",
    "UNKNOWN_SEVERITY_PRIORITY",
    "UNKNOWN_SEVERITY_THEME_COLOR",
    "WEBHOOK_MAX_RETRIES",
    "WEBHOOK_RETRY_DELAYS",
    "FindingsSyncError",
    "GhApiError",
    "GhNotFoundError",
    "GhRunner",
    "deliver_reminders_graph",
    "graph_message",
    "issue_body",
    "issue_title",
    "jira_payload",
    "labels_for_severity",
    "main",
    "opsgenie_payload",
    "opsgenie_priority",
    "pagerduty_payload",
    "pagerduty_severity",
    "priority_for_severity",
    "slack_color",
    "slack_message",
    "sync_findings",
    "sync_findings_graph",
    "sync_findings_jira",
    "sync_findings_linear",
    "sync_findings_opsgenie",
    "sync_findings_pagerduty",
    "sync_findings_slack",
    "sync_findings_teams",
    "teams_message",
    "teams_theme_color",
    "tick",
]

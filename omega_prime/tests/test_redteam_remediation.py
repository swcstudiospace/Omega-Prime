# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic shape checks for the redteam upstream-remediation workflow.

Pure YAML parse + step greps + permission scoping — no network, no `gh`
calls, runnable offline in CI. Mirrors the 79-03 verify conventions.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "redteam-remediation.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_parses_with_three_jobs():
    doc = _load()
    assert set(doc["jobs"]) == {"audit", "remediate", "notify-green"}


def test_triggers_schedule_and_dispatch():
    doc = _load()
    on = doc[True] if True in doc else doc["on"]
    assert on["schedule"] == [{"cron": "30 4 * * 1"}]
    assert "workflow_dispatch" in on
    assert on["workflow_dispatch"]["inputs"]["dry-run"]["default"] is False


def test_audit_reuses_supply_chain_chain():
    doc = _load()
    steps = doc["jobs"]["audit"]["steps"]
    runs = "\n".join(str(s.get("run", "")) for s in steps)
    uses = " ".join(str(s.get("uses", "")) for s in steps)
    assert "git submodule update --init redteam" in runs
    assert "actions/setup-node@" in uses
    assert "npm ci --ignore-scripts" in runs
    assert "npm audit --omit=dev --audit-level=high --json" in runs
    assert "scripts/redteam-audit-triage.py" in runs


def test_remediate_gated_on_audit_failure():
    doc = _load()
    job = doc["jobs"]["remediate"]
    assert job["needs"] == "audit"
    assert "failure()" in job["if"]


def test_notify_green_gated_on_audit_success():
    doc = _load()
    job = doc["jobs"]["notify-green"]
    assert job["needs"] == "audit"
    assert "success()" in job["if"]


def test_upstream_pr_path_targets_purple_team():
    doc = _load()
    text = _text()
    # Upstream repo flows through env; the gh api call appends /pulls.
    assert (
        doc["jobs"]["remediate"]["env"]["UPSTREAM_REPO"] == "swcstudiospace/purple-team"
    )
    assert 'gh api "repos/${UPSTREAM_REPO}/pulls"' in text
    # Advisory IDs travel in the PR body via the advisory table.
    assert "advisories.md" in text
    # Never pushes directly to upstream main.
    assert '-f base="main"' in text
    assert 'git push origin "$BRANCH"' in text


def test_green_comment_posts_sha_with_proof():
    text = _text()
    assert "upstream-sha" in text
    assert "npm audit --omit=dev --audit-level=high exit 0 at" in text
    assert "gh issue comment" in text


def test_degrade_path_artifact_and_issue():
    text = _text()
    assert "actions/upload-artifact@v7" in text
    assert "redteam-remediation-report" in text
    assert "gh issue create -R" in text
    assert "omes-bot" in text
    # Degrade is signal, not red: no nonzero exits in remediate/degrade.
    assert "exit 1" not in text


def test_dry_run_skips_all_gh_writes():
    doc = _load()
    text = _text()
    assert "dry-run" in text
    for job_name in ("remediate", "notify-green"):
        for step in doc["jobs"][job_name]["steps"]:
            run = str(step.get("run", ""))
            if "gh api" in run or "gh issue" in run:
                assert "dry-run" in str(step.get("if", "")), step.get("name")


def test_no_token_probe_without_secret():
    # The PR attempt short-circuits when REMEDIATION_TOKEN is absent.
    text = _text()
    assert "REMEDIATION_TOKEN" in text
    assert "secrets.REMEDIATION_TOKEN" in text


def test_permission_scoping():
    doc = _load()
    assert doc["permissions"] == {"contents": "read"}
    assert doc["jobs"]["audit"]["permissions"] == {"contents": "read"}
    remediate_perms = doc["jobs"]["remediate"]["permissions"]
    assert remediate_perms["pull-requests"] == "write"
    assert remediate_perms["issues"] == "write"
    assert remediate_perms["contents"] == "read"
    notify_perms = doc["jobs"]["notify-green"]["permissions"]
    assert notify_perms["issues"] == "write"
    assert notify_perms["contents"] == "read"
    # No write scopes beyond pull-requests/issues anywhere in the file.
    text = _text()
    assert "contents: write" not in text
    assert "actions: write" not in text


def test_never_approves_or_merges_upstream():
    doc = _load()
    runs = "\n".join(
        str(s.get("run", "")) for job in doc["jobs"].values() for s in job["steps"]
    ).lower()
    assert "auto-merge" not in runs
    assert "pulls/merge" not in runs
    assert "review --approve" not in runs
    assert "gh pr merge" not in runs


def test_never_touches_triage_policy():
    doc = _load()
    # The only sanctioned reference is the read-only audit invocation.
    hits = [
        s.get("name")
        for job in doc["jobs"].values()
        for s in job["steps"]
        if "redteam-audit-triage.py" in str(s.get("run", ""))
    ]
    assert hits == ["npm audit"]
    assert "TRIAGED" not in _text()

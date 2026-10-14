"""Hermetic tests for findings → tracker sync (PLAN-F3). Zero network."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, TypedDict

import pytest

from omega_prime.config import load_config
from omega_prime.grokbot import findings_sync
from omega_prime.grokbot.findings_sync import (
    GRAPH_SCOPE,
    NO_GRAPH_CONFIG_MESSAGE,
    NO_JIRA_CONFIG_MESSAGE,
    NO_LINEAR_TEAM_MESSAGE,
    NO_OPSGENIE_API_KEY_MESSAGE,
    NO_PAGERDUTY_ROUTING_KEY_MESSAGE,
    NO_SLACK_WEBHOOK_MESSAGE,
    NO_TEAMS_WEBHOOK_MESSAGE,
    NO_WEBHOOK_URL_MESSAGE,
    OPSGENIE_ALERTS_URL,
    PAGERDUTY_EVENTS_URL,
    WEBHOOK_PREVIOUS_SIGNATURE_HEADER,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
    GhApiError,
    GhNotFoundError,
    deliver_reminders_graph,
    graph_message,
    issue_body,
    issue_title,
    jira_payload,
    labels_for_severity,
    main,
    opsgenie_payload,
    opsgenie_priority,
    pagerduty_payload,
    pagerduty_severity,
    priority_for_severity,
    sign_payload,
    slack_color,
    slack_message,
    sync_findings,
    sync_findings_graph,
    sync_findings_jira,
    sync_findings_linear,
    sync_findings_opsgenie,
    sync_findings_pagerduty,
    sync_findings_slack,
    sync_findings_teams,
    sync_findings_webhook,
    teams_message,
    teams_theme_color,
    tick,
    webhook_payload,
    webhook_signing_headers,
)
from sidecar.findings import findings_path, load_findings, record_finding

REPO = "OWNER/NAME"


class FakeGh:
    """Fake GhRunner recording (argv, data); zero network."""

    def __init__(self, base_url: str = f"https://github.com/{REPO}/issues/"):
        self.calls: list[tuple[list[str], dict]] = []
        self.base_url = base_url
        self._n = 0

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        self._n += 1
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"html_url": f"{self.base_url}{self._n}"}),
            stderr="",
        )


def _finding(**overrides):
    params = {
        "case_id": "abuse-01",
        "klass": "prompt_injection",
        "severity": "high",
        "observed_detection": "no-detection",
        "verdict": "miss",
        "response": "do anything now",
    }
    params.update(overrides)
    return params


def _seed_two(tmp_path):
    record_finding(directory=tmp_path, **_finding())
    record_finding(
        directory=tmp_path,
        **_finding(case_id="abuse-02", severity="low", response="other text"),
    )


def _enabled_config(**overrides):
    node = {"enabled": True, "repo": REPO, "labels": {}}
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


# Happy path ---------------------------------------------------------------


def test_happy_two_findings_two_calls_with_refs(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = sync_findings(tmp_path, REPO, runner=fake)

    assert len(fake.calls) == 2
    assert len(result["pushed"]) == 2
    assert result["skipped"] == []
    assert result["dry_run"] is False
    for argv, _data in fake.calls:
        assert f"repos/{REPO}/issues" in argv
        assert "--method" in argv and "POST" in argv
    rows = load_findings(tmp_path)
    assert all(row.get("issue_url", "").startswith("https://") for row in rows)
    assert all(row.get("synced_at") for row in rows)


def test_severity_label_mapping_per_spec():
    assert labels_for_severity("critical") == ["severity:critical", "redteam"]
    assert labels_for_severity("high") == ["severity:high", "redteam"]
    assert labels_for_severity("medium") == ["severity:medium"]
    assert labels_for_severity("low") == ["severity:low"]
    assert labels_for_severity("CRITICAL") == ["severity:critical", "redteam"]


def test_labels_sent_on_gh_argv(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="critical"))
    fake = FakeGh()
    sync_findings(tmp_path, REPO, runner=fake)
    argv, data = fake.calls[0]
    assert "-f" in argv
    assert "labels[]=severity:critical" in argv
    assert "labels[]=redteam" in argv
    assert data["labels"] == ["severity:critical", "redteam"]
    assert data["title"].startswith("[critical] abuse-01: ")
    assert "(miss)" in data["title"]


def test_operator_label_override_replaces_default(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="critical"))
    fake = FakeGh()
    sync_findings(tmp_path, REPO, runner=fake, labels={"critical": ["triage"]})
    _argv, data = fake.calls[0]
    assert data["labels"] == ["triage"]


def test_issue_body_fixed_fields_no_raw_payload(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    body = issue_body(row)
    assert "case_id" in body and "abuse-01" in body
    assert row["dedupe_hash"] in body
    assert "synced by findings_sync" in body
    assert "do anything now" not in body  # raw payload stays in the case file
    assert issue_title(row) == "[high] abuse-01: no-detection (miss)"


# Edge cases ---------------------------------------------------------------


def test_presynced_row_skipped(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    row["issue_url"] = "https://github.com/OWNER/NAME/issues/9"
    row["synced_at"] = "2026-01-01T00:00:00+00:00"
    findings_sync._write_rows_atomic(findings_path(tmp_path), [row])
    fake = FakeGh()
    result = sync_findings(tmp_path, REPO, runner=fake)
    assert fake.calls == []
    assert result["pushed"] == []
    assert result["skipped"] == [row["dedupe_hash"]]


def test_unknown_severity_falls_back_to_redteam_only(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="weird"))
    fake = FakeGh()
    sync_findings(tmp_path, REPO, runner=fake)
    assert fake.calls[0][1]["labels"] == ["redteam"]


def test_empty_findings_pushes_nothing(tmp_path):
    fake = FakeGh()
    result = sync_findings(tmp_path, REPO, runner=fake)
    assert result == {"pushed": [], "skipped": [], "dry_run": False}
    assert fake.calls == []


def test_empty_repo_fail_closed():
    with pytest.raises(ValueError, match="no target repo configured"):
        sync_findings(None, "", runner=FakeGh())
    with pytest.raises(ValueError, match="no target repo configured"):
        sync_findings(None, "   ", runner=FakeGh())


# Failure paths ------------------------------------------------------------


def test_nonzero_exit_leaves_row_unsynced_and_retries(tmp_path):
    _seed_two(tmp_path)
    bad = SimpleNamespace(returncode=1, stdout="", stderr="boom")
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        return bad

    with pytest.raises(GhApiError):
        sync_findings(tmp_path, REPO, runner=flaky)
    rows = load_findings(tmp_path)
    assert all("issue_url" not in row for row in rows)

    fake = FakeGh()
    result = sync_findings(tmp_path, REPO, runner=fake)
    assert len(result["pushed"]) == 2  # retry pushes both


def test_runner_exception_mid_run_persists_earlier_push(tmp_path):
    _seed_two(tmp_path)
    good = FakeGh()

    def flaky(argv, data):
        if len(good.calls) == 0:
            return good(argv, data)
        raise OSError("conn reset")

    with pytest.raises(GhApiError):
        sync_findings(tmp_path, REPO, runner=flaky)
    rows = load_findings(tmp_path)
    assert rows[0].get("issue_url", "").startswith("https://")
    assert "issue_url" not in rows[1]

    # Next run pushes only the failed row — no duplicate issue.
    second = FakeGh()
    result = sync_findings(tmp_path, REPO, runner=second)
    assert len(second.calls) == 1
    assert result["skipped"] == [rows[0]["dedupe_hash"]]


def test_missing_gh_binary_structured_error_no_partial_write(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    monkeypatch.setattr(findings_sync.shutil, "which", lambda _name: None)
    with pytest.raises(GhNotFoundError):
        sync_findings(tmp_path, REPO)  # default runner, no fake
    assert findings_path(tmp_path).read_bytes() == before


# Config-off inertness -----------------------------------------------------


def test_tick_disabled_is_inert(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        {"redteam": {"findings_sync": {"enabled": False, "repo": REPO}}},
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["disabled"] is True
    assert result["pushed"] == []
    assert fake.calls == []


def test_tick_enabled_pushes(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(_enabled_config(), reports_directory=tmp_path, runner=fake)
    assert result["disabled"] is False
    assert len(result["pushed"]) == 2
    assert len(fake.calls) == 2


def test_tick_enabled_no_repo_structured_error(tmp_path):
    fake = FakeGh()
    result = tick(
        {"redteam": {"findings_sync": {"enabled": True, "repo": ""}}},
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["error"] == "no target repo configured"
    assert fake.calls == []


def test_cli_disabled_exits_zero_inert(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: {
            "redteam": {"findings_sync": {"enabled": False, "repo": REPO}}
        },
    )
    fake = FakeGh()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--repo", REPO, "--reports-dir", str(tmp_path)]) == 0
    assert "findings_sync disabled" in capsys.readouterr().out
    assert fake.calls == []


def test_cli_empty_repo_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: {
            "redteam": {"findings_sync": {"enabled": True, "repo": ""}}
        },
    )
    assert main(["--reports-dir", str(tmp_path)]) == 2
    assert "no target repo configured" in capsys.readouterr().err


def test_cli_gh_failure_exits_nonzero_row_unsynced(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _enabled_config(),
    )
    monkeypatch.setattr(
        findings_sync,
        "_default_runner",
        lambda argv, data: SimpleNamespace(returncode=1, stdout="", stderr="denied"),
    )
    assert main(["--reports-dir", str(tmp_path)]) == 1
    assert "denied" in capsys.readouterr().err
    assert all("issue_url" not in r for r in load_findings(tmp_path))


# Idempotency + dry-run ----------------------------------------------------


def test_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    first = FakeGh()
    sync_findings(tmp_path, REPO, runner=first)
    assert len(first.calls) == 2
    second = FakeGh()
    result = sync_findings(tmp_path, REPO, runner=second)
    assert second.calls == []
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2


def test_replay_bumps_count_without_second_issue(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    sync_findings(tmp_path, REPO, runner=FakeGh())
    replayed = record_finding(directory=tmp_path, **_finding())
    assert replayed["occurrence_count"] == 2
    assert replayed["dedupe_hash"] == row["dedupe_hash"]
    second = FakeGh()
    result = sync_findings(tmp_path, REPO, runner=second)
    assert second.calls == []  # replay reuses the recorded issue_url
    assert result["skipped"] == [row["dedupe_hash"]]


def test_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()

    def exploding_runner(argv, data):  # pragma: no cover
        raise AssertionError("runner must not be called in dry-run")

    result = sync_findings(tmp_path, REPO, runner=exploding_runner, dry_run=True)
    assert findings_path(tmp_path).read_bytes() == before
    assert len(result["pushed"]) == 2  # would-push items
    assert result["dry_run"] is True
    assert all("issue_url" not in r for r in load_findings(tmp_path))


# Env overrides ------------------------------------------------------------


def test_env_overrides_enable_and_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_ENABLED", "1")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_REPO", REPO)
    node = load_config(tmp_path)["redteam"]["findings_sync"]
    assert node["enabled"] is True
    assert node["repo"] == REPO


def test_env_overrides_tracker_and_linear_team(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_TRACKER", "linear")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_LINEAR_TEAM_ID", "team-123")
    node = load_config(tmp_path)["redteam"]["findings_sync"]
    assert node["tracker"] == "linear"
    assert node["linear_team_id"] == "team-123"


def test_defaults_off_empty_repo(tmp_path):
    node = load_config(tmp_path)["redteam"]["findings_sync"]
    assert node == {
        "enabled": False,
        "repo": "",
        "labels": {},
        "tracker": "github",
        "linear_team_id": "",
        "slack_webhook_url": "",
        "teams_webhook_url": "",
    }


# Linear tracker mode --------------------------------------------------------


TEAM = "team-123"


class FakeLinear:
    """Fake GhRunner returning Linear issueCreate payloads; zero network."""

    def __init__(self, base_url="https://linear.app/spectrum/issue/"):
        self.calls: list[tuple[list[str], dict]] = []
        self.base_url = base_url
        self._n = 0

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        self._n += 1
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": f"iss-{self._n}",
                                "url": f"{self.base_url}{self._n}",
                            },
                        }
                    }
                }
            ),
            stderr="",
        )


def _linear_config(**overrides):
    node = {
        "enabled": True,
        "repo": REPO,
        "labels": {},
        "tracker": "linear",
        "linear_team_id": TEAM,
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_linear_priority_mapping_per_spec():
    assert priority_for_severity("critical") == 1
    assert priority_for_severity("high") == 2
    assert priority_for_severity("medium") == 3
    assert priority_for_severity("low") == 4
    assert priority_for_severity("CRITICAL") == 1
    assert priority_for_severity("weird") == 0
    assert priority_for_severity(None) == 0


def test_linear_happy_two_findings_graphql_calls(tmp_path):
    _seed_two(tmp_path)
    fake = FakeLinear()
    result = sync_findings_linear(tmp_path, TEAM, runner=fake)
    assert len(result["pushed"]) == 2
    assert result["skipped"] == []
    assert result["dry_run"] is False
    for argv, data in fake.calls:
        assert argv[:3] == ["gh", "api", "graphql"]
        assert f"teamId={TEAM}" in argv
        assert data["team_id"] == TEAM
        assert "dedupe_hash" in data["body"]
    assert [call[1]["priority"] for call in fake.calls] == [2, 4]
    rows = load_findings(tmp_path)
    assert all(row["issue_url"].startswith(fake.base_url) for row in rows)
    assert all(row.get("synced_at") for row in rows)


def test_linear_empty_team_fail_closed():
    with pytest.raises(ValueError, match="no linear team configured"):
        sync_findings_linear(None, "   ", runner=FakeLinear())


def test_linear_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeLinear()
    sync_findings_linear(tmp_path, TEAM, runner=fake)
    result = sync_findings_linear(tmp_path, TEAM, runner=fake)
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2
    assert len(fake.calls) == 2


def test_linear_nonzero_exit_leaves_row_unsynced(tmp_path):
    _seed_two(tmp_path)
    calls = []

    def runner(argv, data):
        calls.append((argv, data))
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(GhApiError):
        sync_findings_linear(tmp_path, TEAM, runner=runner)
    assert len(calls) == 1
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_linear_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeLinear()
    result = sync_findings_linear(tmp_path, TEAM, runner=fake, dry_run=True)
    assert len(result["pushed"]) == 2
    assert all(entry["issue_url"] == "" for entry in result["pushed"])
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before


def test_tick_linear_dispatches_graphql(tmp_path):
    _seed_two(tmp_path)
    fake = FakeLinear()
    result = tick(_linear_config(), reports_directory=tmp_path, runner=fake)
    assert result["disabled"] is False
    assert len(fake.calls) == 2
    assert all(call[0][:3] == ["gh", "api", "graphql"] for call in fake.calls)


def test_tick_linear_no_team_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakeLinear()
    result = tick(
        _linear_config(linear_team_id=""), reports_directory=tmp_path, runner=fake
    )
    assert result["error"] == NO_LINEAR_TEAM_MESSAGE
    assert fake.calls == []


def test_tick_unknown_tracker_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakeLinear()
    result = tick(
        _linear_config(tracker="trello"), reports_directory=tmp_path, runner=fake
    )
    assert "unknown tracker" in result["error"]
    assert fake.calls == []


def test_tick_default_tracker_stays_github(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(_enabled_config(), reports_directory=tmp_path, runner=fake)
    assert result["disabled"] is False
    assert all("repos/" in " ".join(call[0]) for call in fake.calls)


def test_cli_linear_pushes_via_graphql(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _linear_config(),
    )
    fake = FakeLinear()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--tracker", "linear", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2
    assert all(call[0][:3] == ["gh", "api", "graphql"] for call in fake.calls)
    assert "pushed" in capsys.readouterr().out


def test_cli_linear_tracker_from_config_without_flag(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _linear_config(),
    )
    fake = FakeLinear()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2


def test_cli_linear_missing_team_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _linear_config(linear_team_id=""),
    )
    assert main(["--tracker", "linear", "--reports-dir", str(tmp_path)]) == 2
    assert "no linear team configured" in capsys.readouterr().err


def test_cli_unknown_tracker_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _enabled_config(),
    )
    assert main(["--tracker", "trello", "--reports-dir", str(tmp_path)]) == 2
    assert "unknown tracker" in capsys.readouterr().err


# Slack tracker mode (batch-04 Feature H3) ------------------------------------

WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


class FakeSlack:
    """Fake GhRunner answering webhook POSTs with Slack ``ok``; zero network."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def _slack_config(**overrides):
    node = {
        "enabled": True,
        "repo": REPO,
        "labels": {},
        "tracker": "slack",
        "linear_team_id": TEAM,
        "slack_webhook_url": WEBHOOK,
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_slack_color_mapping_per_spec():
    assert slack_color("critical") == "danger"
    assert slack_color("high") == "danger"
    assert slack_color("medium") == "warning"
    assert slack_color("low") == "good"
    assert slack_color("CRITICAL") == "danger"
    assert slack_color("weird") == "#808080"
    assert slack_color(None) == "#808080"


def test_slack_message_block_kit_layout(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(severity="critical"))
    message = slack_message(row)
    # Fallback text keeps the dedupe hash (idempotency contract).
    assert row["dedupe_hash"] in message["text"]
    assert issue_title(row) in message["text"]
    header, section, context = message["blocks"]
    assert header["type"] == "header"
    assert header["text"]["type"] == "plain_text"
    assert issue_title(row) in header["text"]["text"]
    assert section["type"] == "section"
    assert section["text"]["type"] == "mrkdwn"
    details = section["text"]["text"]
    for field in ("critical", "abuse-01", "prompt_injection", row["dedupe_hash"]):
        assert field in details
    assert context["type"] == "context"
    context_text = context["elements"][0]["text"]
    assert context["elements"][0]["type"] == "mrkdwn"
    assert row["dedupe_hash"] in context_text
    assert str(row.get("last_seen", "")) in context_text


def test_slack_message_fixed_fields_only(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(response="do anything now"))
    row["raw_response"] = "do anything now"
    assert "do anything now" not in json.dumps(slack_message(row))


def test_slack_message_header_truncates_long_titles(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(case_id="abuse-" + "x" * 300))
    header_text = slack_message(row)["blocks"][0]["text"]["text"]
    assert len(header_text) <= 150


def test_slack_happy_two_findings_two_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeSlack()
    result = sync_findings_slack(tmp_path, WEBHOOK, runner=fake)
    assert len(fake.calls) == 2
    assert all(call[0][:2] == ["gh", "api"] for call in fake.calls)
    assert all(WEBHOOK in call[0] for call in fake.calls)
    assert all("dedupe_hash: " in call[1]["text"] for call in fake.calls)
    # Block Kit rides as -f blocks=<json>; the legacy attachment field is gone.
    assert all(any(arg.startswith("blocks=") for arg in call[0]) for call in fake.calls)
    assert all(
        not any(arg.startswith("attachments=") for arg in call[0])
        for call in fake.calls
    )
    for _argv, data in fake.calls:
        header, section, context = data["blocks"]
        assert (header["type"], section["type"], context["type"]) == (
            "header",
            "section",
            "context",
        )
        assert section["text"]["type"] == "mrkdwn"
        assert "dedupe_hash: " in context["elements"][0]["text"]
    assert len(result["pushed"]) == 2
    assert result["skipped"] == []
    rows = load_findings(tmp_path)
    assert all(row["issue_url"] == f"slack:{row['dedupe_hash']}" for row in rows)
    assert all(row.get("synced_at") for row in rows)


def test_slack_empty_webhook_fail_closed():
    with pytest.raises(ValueError, match="no slack webhook configured"):
        sync_findings_slack(None, "   ", runner=FakeSlack())


def test_slack_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeSlack()
    sync_findings_slack(tmp_path, WEBHOOK, runner=fake)
    result = sync_findings_slack(tmp_path, WEBHOOK, runner=fake)
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2
    assert len(fake.calls) == 2


def test_slack_nonzero_exit_leaves_row_unsynced(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def bad(argv, data):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(GhApiError):
        sync_findings_slack(tmp_path, WEBHOOK, runner=bad)
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_slack_runner_exception_mid_run_persists_earlier_push(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    calls: list = []

    def flaky(argv, data):
        calls.append((argv, data))
        if len(calls) >= 2:
            raise RuntimeError("webhook down")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    with pytest.raises(GhApiError, match="gh runner failed"):
        sync_findings_slack(tmp_path, WEBHOOK, runner=flaky)
    rows = load_findings(tmp_path)
    assert rows[0]["issue_url"] == f"slack:{rows[0]['dedupe_hash']}"
    assert "issue_url" not in rows[1]


def test_slack_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeSlack()
    result = sync_findings_slack(tmp_path, WEBHOOK, runner=fake, dry_run=True)
    assert len(result["pushed"]) == 2
    assert all(entry["issue_url"] == "" for entry in result["pushed"])
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before


def test_tick_slack_dispatches_webhook_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeSlack()
    result = tick(_slack_config(), reports_directory=tmp_path, runner=fake)
    assert result["disabled"] is False
    assert len(fake.calls) == 2
    assert all(WEBHOOK in call[0] for call in fake.calls)


def test_tick_slack_no_webhook_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakeSlack()
    result = tick(
        _slack_config(slack_webhook_url=""),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["error"] == NO_SLACK_WEBHOOK_MESSAGE
    assert fake.calls == []


def test_cli_slack_pushes_via_webhook(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _slack_config(),
    )
    fake = FakeSlack()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--tracker", "slack", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2
    assert "pushed" in capsys.readouterr().out


def test_cli_slack_tracker_from_config_without_flag(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _slack_config(),
    )
    fake = FakeSlack()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2


def test_cli_slack_mode_alias_posts_blocks(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _slack_config(tracker="github"),
    )
    fake = FakeSlack()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--mode", "slack", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2
    assert all("blocks" in call[1] for call in fake.calls)
    assert "pushed" in capsys.readouterr().out


def test_cli_slack_missing_webhook_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _slack_config(slack_webhook_url=""),
    )
    assert main(["--tracker", "slack", "--reports-dir", str(tmp_path)]) == 2
    assert "no slack webhook configured" in capsys.readouterr().err


def test_env_override_slack_webhook_url(monkeypatch):
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_SLACK_WEBHOOK_URL", WEBHOOK)
    node = load_config(None)["redteam"]["findings_sync"]
    assert node["slack_webhook_url"] == WEBHOOK


# Teams tracker mode (batch-05) -----------------------------------------------

TEAMS_WEBHOOK = "https://example.webhook.office.com/webhookb2/XXXX"


class FakeTeams:
    """Fake GhRunner answering webhook POSTs; zero network."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        return SimpleNamespace(returncode=0, stdout="1", stderr="")


def _teams_config(**overrides):
    node = {
        "enabled": True,
        "repo": REPO,
        "labels": {},
        "tracker": "teams",
        "linear_team_id": TEAM,
        "slack_webhook_url": WEBHOOK,
        "teams_webhook_url": TEAMS_WEBHOOK,
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_teams_theme_color_mapping_per_spec():
    assert teams_theme_color("critical") == "DC143C"
    assert teams_theme_color("high") == "FF8C00"
    assert teams_theme_color("medium") == "FFD700"
    assert teams_theme_color("low") == "00BFFF"
    assert teams_theme_color("CRITICAL") == "DC143C"
    assert teams_theme_color("weird") == "#808080"
    assert teams_theme_color(None) == "#808080"


def test_teams_message_hash_in_summary_and_color(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(severity="critical"))
    card = teams_message(row)
    assert card["@type"] == "MessageCard"
    assert card["themeColor"] == "DC143C"
    assert row["dedupe_hash"] in card["summary"]
    assert card["sections"][0]["activityTitle"] == issue_title(row)


def test_teams_happy_two_findings_two_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    result = sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=fake)
    assert len(fake.calls) == 2
    assert all(call[0][:2] == ["gh", "api"] for call in fake.calls)
    assert all(TEAMS_WEBHOOK in call[0] for call in fake.calls)
    assert all("dedupe_hash: " in call[1]["summary"] for call in fake.calls)
    assert all(call[1]["themeColor"] for call in fake.calls)
    assert len(result["pushed"]) == 2
    assert result["skipped"] == []
    rows = load_findings(tmp_path)
    assert all(row["issue_url"] == f"teams:{row['dedupe_hash']}" for row in rows)
    assert all(row.get("synced_at") for row in rows)


def test_teams_empty_webhook_fail_closed():
    with pytest.raises(ValueError, match="no teams webhook configured"):
        sync_findings_teams(None, "   ", runner=FakeTeams())


def test_teams_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=fake)
    result = sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=fake)
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2
    assert len(fake.calls) == 2


def test_teams_nonzero_exit_leaves_row_unsynced(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def bad(argv, data):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(GhApiError):
        sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=bad)
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_teams_runner_exception_mid_run_persists_earlier_push(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    calls: list = []

    def flaky(argv, data):
        calls.append((argv, data))
        if len(calls) >= 2:
            raise RuntimeError("webhook down")
        return SimpleNamespace(returncode=0, stdout="1", stderr="")

    with pytest.raises(GhApiError, match="gh runner failed"):
        sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=flaky)
    rows = load_findings(tmp_path)
    assert rows[0]["issue_url"] == f"teams:{rows[0]['dedupe_hash']}"
    assert "issue_url" not in rows[1]


def test_teams_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeTeams()
    result = sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=fake, dry_run=True)
    assert len(result["pushed"]) == 2
    assert all(entry["issue_url"] == "" for entry in result["pushed"])
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before


def test_tick_teams_dispatches_webhook_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    result = tick(_teams_config(), reports_directory=tmp_path, runner=fake)
    assert result["disabled"] is False
    assert len(fake.calls) == 2
    assert all(TEAMS_WEBHOOK in call[0] for call in fake.calls)


def test_tick_teams_no_webhook_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    result = tick(
        _teams_config(teams_webhook_url=""),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["error"] == NO_TEAMS_WEBHOOK_MESSAGE
    assert fake.calls == []


def test_cli_teams_pushes_via_webhook(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _teams_config(),
    )
    fake = FakeTeams()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--tracker", "teams", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2
    assert "pushed" in capsys.readouterr().out


def test_cli_teams_tracker_from_config_without_flag(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _teams_config(),
    )
    fake = FakeTeams()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2


def test_cli_teams_missing_webhook_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _teams_config(teams_webhook_url=""),
    )
    assert main(["--tracker", "teams", "--reports-dir", str(tmp_path)]) == 2
    assert "no teams webhook configured" in capsys.readouterr().err


def test_env_override_teams_webhook_url(monkeypatch):
    monkeypatch.setenv(
        "OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_TEAMS_WEBHOOK_URL", TEAMS_WEBHOOK
    )
    node = load_config(None)["redteam"]["findings_sync"]
    assert node["teams_webhook_url"] == TEAMS_WEBHOOK


# Webhook retry with exponential backoff --------------------------------------


def _patch_retry_timing(monkeypatch):
    """Hermetic retry timing: deterministic delays, no real sleeping."""
    monkeypatch.setattr(findings_sync.random, "uniform", lambda _a, _b: 0.0)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    return sleeps


def test_slack_retry_succeeds_on_second_attempt(tmp_path, monkeypatch, capsys):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_retry_timing(monkeypatch)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    result = sync_findings_slack(tmp_path, WEBHOOK, runner=flaky)
    assert len(result["pushed"]) == 1
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]
    assert "retrying" in capsys.readouterr().err


def test_slack_retry_on_network_error_then_succeeds(tmp_path, monkeypatch, capsys):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_retry_timing(monkeypatch)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            raise OSError("conn reset")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    result = sync_findings_slack(tmp_path, WEBHOOK, runner=flaky)
    assert len(result["pushed"]) == 1
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]
    assert "retrying" in capsys.readouterr().err


def test_slack_retry_exhausts_on_persistent_failure(tmp_path, monkeypatch, capsys):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_retry_timing(monkeypatch)
    calls: list = []

    def bad(argv, data):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(GhApiError):
        sync_findings_slack(tmp_path, WEBHOOK, runner=bad)
    assert len(calls) == 1 + findings_sync.WEBHOOK_MAX_RETRIES
    assert sleeps == list(findings_sync.WEBHOOK_RETRY_DELAYS)
    assert (
        capsys.readouterr().err.count("retrying") == findings_sync.WEBHOOK_MAX_RETRIES
    )
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_slack_usage_error_does_not_retry(tmp_path, monkeypatch):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_retry_timing(monkeypatch)
    calls: list = []

    def usage(argv, data):
        calls.append(argv)
        return SimpleNamespace(returncode=2, stdout="", stderr="usage error")

    with pytest.raises(GhApiError):
        sync_findings_slack(tmp_path, WEBHOOK, runner=usage)
    assert len(calls) == 1
    assert sleeps == []


def test_slack_empty_webhook_does_not_retry(tmp_path, monkeypatch):
    sleeps = _patch_retry_timing(monkeypatch)
    with pytest.raises(ValueError, match="no slack webhook configured"):
        sync_findings_slack(tmp_path, "   ", runner=FakeSlack())
    assert sleeps == []


def test_teams_retry_succeeds_on_second_attempt(tmp_path, monkeypatch):
    record_finding(directory=tmp_path, **_finding())
    _patch_retry_timing(monkeypatch)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="1", stderr="")

    result = sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=flaky)
    assert len(result["pushed"]) == 1
    assert len(calls) == 2


# Teams rich cards (H9) ---------------------------------------------------------


def test_teams_message_sections_markdown_details(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    card = teams_message(row)
    section = card["sections"][0]
    assert section["markdown"] is True
    assert section["activityTitle"] == issue_title(row)
    text = section["text"]
    assert row["dedupe_hash"] in text
    assert row["class"] in text
    assert row["observed_detection"] in text
    assert (row.get("last_seen") or row.get("ts")) in text


def test_teams_message_action_button_with_repo(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    card = teams_message(row, repo=REPO)
    actions = card["potentialAction"]
    assert actions[0]["name"] == "Open in GitHub"
    assert actions[0]["targets"][0]["uri"] == f"https://github.com/{REPO}"


def test_teams_message_no_action_without_repo(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    assert "potentialAction" not in teams_message(row)
    assert "potentialAction" not in teams_message(row, repo="   ")
    assert "potentialAction" not in teams_message(row, repo=None)


def test_teams_sync_with_repo_posts_action(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    result = sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=fake, repo=REPO)
    assert len(result["pushed"]) == 2
    for argv, data in fake.calls:
        assert "potentialAction=" in " ".join(argv)
        assert data["potentialAction"][0]["name"] == "Open in GitHub"
        assert data["card"]["potentialAction"][0]["name"] == "Open in GitHub"


def test_teams_sync_without_repo_omits_action(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    result = sync_findings_teams(tmp_path, TEAMS_WEBHOOK, runner=fake)
    assert len(result["pushed"]) == 2
    for argv, data in fake.calls:
        assert "potentialAction=" not in " ".join(argv)
        assert "potentialAction" not in data
        assert "potentialAction" not in data["card"]


def test_tick_teams_with_repo_posts_action(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    result = tick(_teams_config(repo=REPO), reports_directory=tmp_path, runner=fake)
    assert len(result["pushed"]) == 2
    for argv, _data in fake.calls:
        assert "potentialAction=" in " ".join(argv)


# Graph API mode (G8) ----------------------------------------------------------


class _GraphCreds(TypedDict):
    tenant_id: str
    client_id: str
    client_secret: str
    team_id: str
    channel_id: str


GRAPH_CREDS: _GraphCreds = {
    "tenant_id": "tenant-123",
    "client_id": "client-123",
    "client_secret": "secret-123",
    "team_id": "team-123",
    "channel_id": "channel-123",
}

GRAPH_TOKEN_URL = "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token"
GRAPH_MESSAGES_URL = (
    "https://graph.microsoft.com/v1.0/teams/team-123/channels/channel-123/messages"
)


class FakeGraph:
    """Fake GhRunner answering the token fetch + message POSTs; zero network."""

    def __init__(self, token_calls=None, fail_token=False, fail_posts=0):
        self.calls: list[tuple[list[str], dict]] = []
        self.token = "fake-access-token"
        self.fail_token = fail_token
        self.fail_posts = fail_posts

    def __call__(self, argv, data):
        self.calls.append((argv, data))
        if argv[2].startswith("https://login.microsoftonline.com/"):
            if self.fail_token:
                return SimpleNamespace(returncode=1, stdout="", stderr="denied")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"access_token": self.token, "expires_in": 3600}),
                stderr="",
            )
        if self.fail_posts > 0:
            self.fail_posts -= 1
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


def _graph_config(**overrides):
    node = {
        "enabled": True,
        "tracker": "graph",
        "repo": REPO,
        "graph": dict(GRAPH_CREDS),
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_graph_message_reuses_teams_card(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(severity="critical"))
    message = graph_message(row, repo=REPO)
    assert message["subject"] == issue_title(row)
    assert message["body"]["contentType"] == "html"
    assert row["dedupe_hash"] in message["body"]["content"]
    card = message["attachments"][0]["content"]
    assert card == teams_message(row, repo=REPO)


def test_graph_happy_token_then_two_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph()
    result = sync_findings_graph(tmp_path, runner=fake, **GRAPH_CREDS)
    assert len(result["pushed"]) == 2
    assert all(entry["issue_url"].startswith("graph:") for entry in result["pushed"])
    assert len(fake.calls) == 3  # one token fetch + two message POSTs
    rows = load_findings(tmp_path)
    assert all(row.get("synced_at") for row in rows)


def test_graph_token_fetch_url_scope_and_grant(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph()
    sync_findings_graph(tmp_path, runner=fake, **GRAPH_CREDS)
    argv, data = fake.calls[0]
    assert argv[:3] == ["gh", "api", GRAPH_TOKEN_URL]
    assert f"scope={GRAPH_SCOPE}" in argv
    assert GRAPH_SCOPE == "https://graph.microsoft.com/.default"
    assert "grant_type=client_credentials" in argv
    assert f"client_id={GRAPH_CREDS['client_id']}" in argv
    assert "client_secret" not in json.dumps(data)  # secret rides argv only


def test_graph_posts_bearer_and_messages_url(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph()
    sync_findings_graph(tmp_path, runner=fake, **GRAPH_CREDS)
    for argv, data in fake.calls[1:]:
        assert argv[:3] == ["gh", "api", GRAPH_MESSAGES_URL]
        assert f"Authorization: Bearer {fake.token}" in argv
        assert data["attachments"][0]["content"]["summary"]
        assert data["team_id"] == GRAPH_CREDS["team_id"]
        assert data["channel_id"] == GRAPH_CREDS["channel_id"]


def test_graph_blank_field_fail_closed(tmp_path):
    def _call(creds: dict[str, str], runner: findings_sync.GhRunner | None) -> object:
        return sync_findings_graph(
            tmp_path,
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            team_id=creds["team_id"],
            channel_id=creds["channel_id"],
            runner=runner,
        )

    for field in GRAPH_CREDS:
        creds: dict[str, str] = {
            "tenant_id": GRAPH_CREDS["tenant_id"],
            "client_id": GRAPH_CREDS["client_id"],
            "client_secret": GRAPH_CREDS["client_secret"],
            "team_id": GRAPH_CREDS["team_id"],
            "channel_id": GRAPH_CREDS["channel_id"],
        }
        creds[field] = "   "
        with pytest.raises(ValueError, match="no graph config configured"):
            _call(creds, FakeGraph())
    with pytest.raises(ValueError, match=NO_GRAPH_CONFIG_MESSAGE):
        sync_findings_graph(
            tmp_path,
            tenant_id="",
            client_id="",
            client_secret="",
            team_id="",
            channel_id="",
            runner=FakeGraph(),
        )


def test_graph_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph()
    sync_findings_graph(tmp_path, runner=fake, **GRAPH_CREDS)
    assert len(fake.calls) == 3
    result = sync_findings_graph(tmp_path, runner=fake, **GRAPH_CREDS)
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2
    assert len(fake.calls) == 4  # token fetch only, no message POSTs


def test_graph_dry_run_calls_no_runner_writes_nothing(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()

    def _boom(argv, data):
        raise AssertionError("runner must not be called in dry-run")

    result = sync_findings_graph(tmp_path, runner=_boom, dry_run=True, **GRAPH_CREDS)
    assert len(result["pushed"]) == 2
    assert all(entry["issue_url"] == "" for entry in result["pushed"])
    assert findings_path(tmp_path).read_bytes() == before


def test_graph_token_failure_raises_rows_unsynced(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph(fail_token=True)
    with pytest.raises(GhApiError):
        sync_findings_graph(tmp_path, runner=fake, **GRAPH_CREDS)
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_graph_runner_exception_mid_run_persists_earlier_push(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    _patch_retry_timing(monkeypatch)
    fake = FakeGraph()
    real = fake.__call__

    def _flaky(argv, data):
        if argv[2].startswith("https://login.microsoftonline.com/"):
            return real(argv, data)
        posts = [
            call
            for call in fake.calls
            if not call[0][2].startswith("https://login.microsoftonline.com/")
        ]
        if posts:  # every message POST after the first blows up (incl. retries)
            fake.calls.append((argv, data))
            raise RuntimeError("network down")
        return real(argv, data)

    with pytest.raises(GhApiError):
        sync_findings_graph(tmp_path, runner=_flaky, **GRAPH_CREDS)
    rows = load_findings(tmp_path)
    assert rows[0]["issue_url"] == f"graph:{rows[0]['dedupe_hash']}"
    assert "issue_url" not in rows[1]


def test_tick_graph_branch_when_config_present(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph()
    result = tick(_graph_config(), reports_directory=tmp_path, runner=fake)
    assert len(result["pushed"]) == 2
    assert result["disabled"] is False
    assert fake.calls[0][0][2] == GRAPH_TOKEN_URL
    assert all(call[0][2] == GRAPH_MESSAGES_URL for call in fake.calls[1:])


def test_tick_graph_selected_by_config_presence_without_tracker(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph()
    config = _graph_config(tracker="github")
    result = tick(config, reports_directory=tmp_path, runner=fake)
    assert len(result["pushed"]) == 2
    assert fake.calls[0][0][2] == GRAPH_TOKEN_URL


def test_tick_graph_tracker_without_config_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGraph()
    config = _graph_config(graph={})
    result = tick(config, reports_directory=tmp_path, runner=fake)
    assert result["error"] == NO_GRAPH_CONFIG_MESSAGE
    assert fake.calls == []


def test_tick_graph_absent_leaves_teams_path_unchanged(tmp_path):
    _seed_two(tmp_path)
    fake = FakeTeams()
    result = tick(_teams_config(), reports_directory=tmp_path, runner=fake)
    assert len(result["pushed"]) == 2


def test_cli_mode_graph_posts(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _graph_config(tracker="github"),
    )
    fake = FakeGraph()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--mode", "graph", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 3
    assert "pushed" in capsys.readouterr().out


def test_cli_tracker_graph_from_flag(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _graph_config(tracker="github"),
    )
    fake = FakeGraph()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--tracker", "graph", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 3


def test_cli_graph_missing_config_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _graph_config(graph={}),
    )
    assert main(["--mode", "graph", "--reports-dir", str(tmp_path)]) == 2
    assert "no graph config configured" in capsys.readouterr().err


def test_cli_graph_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _graph_config(tracker="github"),
    )
    fake = FakeGraph()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--mode", "graph", "--reports-dir", str(tmp_path), "--dry-run"]) == 0
    assert "would push" in capsys.readouterr().out
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before


# Graph-delivered RoE renewal reminders (I11) ----------------------------------


def _reminder(threshold="7d", artifact_id="g11-operator|until", days_remaining=6.5):
    return {
        "artifact_id": artifact_id,
        "threshold": threshold,
        "threshold_days": 7.0,
        "days_remaining": days_remaining,
        "effective_until": "2026-06-07T12:00:00+00:00",
        "detail": (
            f"RoE grant {artifact_id!r} expires in {days_remaining:.1f}d "
            f"(renewal threshold {threshold}) — re-sign to renew"
        ),
    }


def test_deliver_reminders_graph_posts_identity_text():
    fake = FakeGraph()
    result = deliver_reminders_graph(
        [_reminder("14d", days_remaining=10.0), _reminder("7d")],
        runner=fake,
        **GRAPH_CREDS,
    )
    assert [entry["threshold"] for entry in result["posted"]] == ["14d", "7d"]
    assert all(entry["artifact_id"] for entry in result["posted"])
    assert result["skipped"] == 0
    assert result["dry_run"] is False
    assert len(fake.calls) == 3  # one token fetch + two message POSTs
    assert fake.calls[0][0][2] == GRAPH_TOKEN_URL
    for argv, data in fake.calls[1:]:
        assert argv[:3] == ["gh", "api", GRAPH_MESSAGES_URL]
        assert f"Authorization: Bearer {fake.token}" in argv
        assert data["team_id"] == GRAPH_CREDS["team_id"]
        assert data["channel_id"] == GRAPH_CREDS["channel_id"]
        content = data["body"]["content"]
        assert data["body"]["contentType"] == "text"
        assert "g11-operator|until" in content
        assert "7d" in content or "14d" in content
        assert "6.5" in content or "10.0" in content
        assert "re-sign to renew" in content


def test_deliver_reminders_graph_blank_field_fail_closed():
    def _deliver(
        creds: dict[str, str], runner: findings_sync.GhRunner | None
    ) -> object:
        return deliver_reminders_graph(
            [_reminder()],
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            team_id=creds["team_id"],
            channel_id=creds["channel_id"],
            runner=runner,
        )

    for field in ("tenant_id", "client_id", "client_secret", "team_id", "channel_id"):
        creds: dict[str, str] = {
            "tenant_id": GRAPH_CREDS["tenant_id"],
            "client_id": GRAPH_CREDS["client_id"],
            "client_secret": GRAPH_CREDS["client_secret"],
            "team_id": GRAPH_CREDS["team_id"],
            "channel_id": GRAPH_CREDS["channel_id"],
        }
        creds[field] = "   "
        with pytest.raises(ValueError, match="no graph config configured"):
            _deliver(creds, FakeGraph())


def test_deliver_reminders_graph_empty_calls_no_runner():
    fake = FakeGraph()
    assert deliver_reminders_graph([], runner=fake, **GRAPH_CREDS) == {
        "posted": [],
        "skipped": 0,
        "dry_run": False,
    }
    assert fake.calls == []


def test_deliver_reminders_graph_skips_malformed_entries():
    fake = FakeGraph()
    result = deliver_reminders_graph(
        ["nope", None, 42, _reminder()], runner=fake, **GRAPH_CREDS
    )
    assert len(result["posted"]) == 1
    assert result["skipped"] == 3
    assert len(fake.calls) == 2  # token fetch + one message POST


def test_deliver_reminders_graph_token_failure_raises():
    with pytest.raises(GhApiError):
        deliver_reminders_graph(
            [_reminder()], runner=FakeGraph(fail_token=True), **GRAPH_CREDS
        )


def test_deliver_reminders_graph_post_failure_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(GhApiError):
        deliver_reminders_graph(
            [_reminder()], runner=FakeGraph(fail_posts=99), **GRAPH_CREDS
        )


# PagerDuty Events API v2 mode (J8) ---------------------------------------------

ROUTING_KEY = "R0UT1NGK3Y0000000000000000000000"


class FakePagerDuty:
    """Fake GhRunner answering Events API v2 enqueue POSTs; zero network."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


def _pagerduty_config(**overrides):
    node = {
        "enabled": True,
        "tracker": "pagerduty",
        "pagerduty_routing_key": ROUTING_KEY,
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_pagerduty_severity_mapping_per_spec():
    assert pagerduty_severity("critical") == "critical"
    assert pagerduty_severity("high") == "error"
    assert pagerduty_severity("medium") == "warning"
    assert pagerduty_severity("moderate") == "warning"
    assert pagerduty_severity("low") == "info"
    assert pagerduty_severity("info") == "info"
    assert pagerduty_severity("CRITICAL") == "critical"
    assert pagerduty_severity("weird") == "info"
    assert pagerduty_severity(None) == "info"


def test_pagerduty_severity_map_override_replaces_default():
    assert pagerduty_severity("high", {"high": "warning"}) == "warning"
    assert pagerduty_severity("HIGH", {"high": "critical"}) == "critical"
    # Invalid override values fall back to the default table.
    assert pagerduty_severity("high", {"high": "bogus"}) == "error"
    assert pagerduty_severity("high", {"low": "critical"}) == "error"


def test_pagerduty_payload_events_v2_shape(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(severity="high"))
    event = pagerduty_payload(row, ROUTING_KEY)
    assert event["routing_key"] == ROUTING_KEY
    assert event["event_action"] == "trigger"
    assert event["dedup_key"] == row["dedupe_hash"]
    inner = event["payload"]
    assert inner["severity"] == "error"
    assert inner["summary"] == issue_title(row)
    assert inner["source"] == "abuse-01"
    details = inner["custom_details"]
    assert details["dedupe_hash"] == row["dedupe_hash"]
    assert details["verdict"] == "miss"
    assert details["observed_detection"] == "no-detection"


def test_pagerduty_payload_fixed_fields_only(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(response="do anything now"))
    row["raw_response"] = "do anything now"
    assert "do anything now" not in json.dumps(pagerduty_payload(row, ROUTING_KEY))


def test_pagerduty_payload_summary_truncated_to_1024(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(case_id="abuse-" + "x" * 2000))
    assert len(pagerduty_payload(row, ROUTING_KEY)["payload"]["summary"]) <= 1024


def test_pagerduty_happy_two_findings_two_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakePagerDuty()
    result = sync_findings_pagerduty(tmp_path, ROUTING_KEY, runner=fake)
    assert len(result["pushed"]) == 2
    assert result["skipped"] == []
    assert result["dry_run"] is False
    assert len(fake.calls) == 2
    for argv, data in fake.calls:
        assert argv[:3] == ["gh", "api", PAGERDUTY_EVENTS_URL]
        assert data["event_action"] == "trigger"
        assert data["routing_key"] == ROUTING_KEY
        assert data["dedup_key"] == data["payload"]["custom_details"]["dedupe_hash"]
    rows = load_findings(tmp_path)
    assert all(row["issue_url"].startswith("pd:") for row in rows)
    assert all(row.get("synced_at") for row in rows)


def test_pagerduty_severity_override_flows_to_payload(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="high"))
    fake = FakePagerDuty()
    sync_findings_pagerduty(
        tmp_path, ROUTING_KEY, runner=fake, severity_map={"high": "warning"}
    )
    assert fake.calls[0][1]["payload"]["severity"] == "warning"


def test_pagerduty_empty_routing_key_fail_closed():
    with pytest.raises(ValueError, match="no pagerduty routing key configured"):
        sync_findings_pagerduty(None, "   ", runner=FakePagerDuty())


def test_pagerduty_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakePagerDuty()
    sync_findings_pagerduty(tmp_path, ROUTING_KEY, runner=fake)
    result = sync_findings_pagerduty(tmp_path, ROUTING_KEY, runner=fake)
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2
    assert len(fake.calls) == 2


def test_pagerduty_nonzero_exit_leaves_row_unsynced(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(findings_sync.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def bad(argv, data):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(GhApiError):
        sync_findings_pagerduty(tmp_path, ROUTING_KEY, runner=bad)
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_pagerduty_runner_exception_mid_run_persists_earlier_push(
    tmp_path, monkeypatch
):
    _seed_two(tmp_path)
    monkeypatch.setattr(findings_sync.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        raise OSError("conn reset")

    with pytest.raises(GhApiError):
        sync_findings_pagerduty(tmp_path, ROUTING_KEY, runner=flaky)
    rows = load_findings(tmp_path)
    assert rows[0]["issue_url"].startswith("pd:")
    assert "issue_url" not in rows[1]


def test_pagerduty_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    fake = FakePagerDuty()
    before = findings_path(tmp_path).read_bytes()
    result = sync_findings_pagerduty(tmp_path, ROUTING_KEY, runner=fake, dry_run=True)
    assert len(result["pushed"]) == 2
    assert result["dry_run"] is True
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before


def test_pagerduty_retry_succeeds_on_second_attempt(tmp_path, monkeypatch, capsys):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_retry_timing(monkeypatch)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    result = sync_findings_pagerduty(tmp_path, ROUTING_KEY, runner=flaky)
    assert len(result["pushed"]) == 1
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]
    assert "retrying" in capsys.readouterr().err


def test_tick_pagerduty_dispatches_enqueue_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakePagerDuty()
    result = tick(_pagerduty_config(), reports_directory=tmp_path, runner=fake)
    assert result["disabled"] is False
    assert len(fake.calls) == 2
    assert all(PAGERDUTY_EVENTS_URL in call[0] for call in fake.calls)


def test_tick_pagerduty_no_routing_key_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakePagerDuty()
    result = tick(
        _pagerduty_config(pagerduty_routing_key=""),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["error"] == NO_PAGERDUTY_ROUTING_KEY_MESSAGE
    assert fake.calls == []


def test_cli_pagerduty_mode_posts(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _pagerduty_config(tracker="github"),
    )
    fake = FakePagerDuty()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--mode", "pagerduty", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2
    assert "pushed" in capsys.readouterr().out


def test_cli_pagerduty_tracker_from_config_without_flag(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _pagerduty_config(),
    )
    fake = FakePagerDuty()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2


def test_cli_pagerduty_missing_routing_key_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _pagerduty_config(pagerduty_routing_key=""),
    )
    assert main(["--tracker", "pagerduty", "--reports-dir", str(tmp_path)]) == 2
    assert "no pagerduty routing key configured" in capsys.readouterr().err


# Opsgenie Alert API v2 mode (K8) -----------------------------------------------

API_KEY = "eb243592-faa2-4ba2-a551q-1afdf565c889"


class FakeOpsgenie:
    """Fake GhRunner answering Alert API v2 create-alert POSTs; zero network."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


def _opsgenie_config(**overrides):
    node = {
        "enabled": True,
        "tracker": "opsgenie",
        "opsgenie_api_key": API_KEY,
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_opsgenie_priority_mapping_per_spec():
    assert opsgenie_priority("critical") == "P1"
    assert opsgenie_priority("high") == "P2"
    assert opsgenie_priority("medium") == "P3"
    assert opsgenie_priority("moderate") == "P3"
    assert opsgenie_priority("low") == "P5"
    assert opsgenie_priority("info") == "P5"
    assert opsgenie_priority("CRITICAL") == "P1"
    assert opsgenie_priority("weird") == "P5"
    assert opsgenie_priority(None) == "P5"


def test_opsgenie_priority_map_override_replaces_default():
    assert opsgenie_priority("high", {"high": "P1"}) == "P1"
    assert opsgenie_priority("HIGH", {"high": "p4"}) == "P4"
    # Invalid override values fall back to the default table.
    assert opsgenie_priority("high", {"high": "bogus"}) == "P2"
    assert opsgenie_priority("high", {"low": "P1"}) == "P2"


def test_opsgenie_payload_alert_v2_shape(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(severity="high"))
    alert = opsgenie_payload(row)
    assert alert["message"] == issue_title(row)
    assert alert["alias"] == row["dedupe_hash"]
    assert alert["description"] == issue_body(row)
    assert alert["priority"] == "P2"
    details = alert["details"]
    assert details["dedupe_hash"] == row["dedupe_hash"]
    assert details["verdict"] == "miss"
    assert details["observed_detection"] == "no-detection"


def test_opsgenie_payload_fixed_fields_only(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(response="do anything now"))
    row["raw_response"] = "do anything now"
    assert "do anything now" not in json.dumps(opsgenie_payload(row))


def test_opsgenie_payload_message_truncated_to_130(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(case_id="abuse-" + "x" * 2000))
    assert len(opsgenie_payload(row)["message"]) <= 130


def test_opsgenie_happy_two_findings_two_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeOpsgenie()
    result = sync_findings_opsgenie(tmp_path, API_KEY, runner=fake)
    assert len(result["pushed"]) == 2
    assert result["skipped"] == []
    assert result["dry_run"] is False
    assert len(fake.calls) == 2
    for argv, data in fake.calls:
        assert argv[:3] == ["gh", "api", OPSGENIE_ALERTS_URL]
        assert "Authorization: GenieKey " + API_KEY in argv
        assert data["alias"] == data["details"]["dedupe_hash"]
        assert data["message"] == data["message"][:130]
    rows = load_findings(tmp_path)
    assert all(row["issue_url"].startswith("og:") for row in rows)
    assert all(row.get("synced_at") for row in rows)


def test_opsgenie_priority_override_flows_to_payload(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="high"))
    fake = FakeOpsgenie()
    sync_findings_opsgenie(tmp_path, API_KEY, runner=fake, priority_map={"high": "P1"})
    assert fake.calls[0][1]["priority"] == "P1"


def test_opsgenie_empty_api_key_fail_closed():
    with pytest.raises(ValueError, match="no opsgenie api key configured"):
        sync_findings_opsgenie(None, "   ", runner=FakeOpsgenie())


def test_opsgenie_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeOpsgenie()
    sync_findings_opsgenie(tmp_path, API_KEY, runner=fake)
    result = sync_findings_opsgenie(tmp_path, API_KEY, runner=fake)
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2
    assert len(fake.calls) == 2


def test_opsgenie_nonzero_exit_leaves_row_unsynced(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(findings_sync.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def bad(argv, data):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(GhApiError):
        sync_findings_opsgenie(tmp_path, API_KEY, runner=bad)
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_opsgenie_runner_exception_mid_run_persists_earlier_push(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(findings_sync.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        raise OSError("conn reset")

    with pytest.raises(GhApiError):
        sync_findings_opsgenie(tmp_path, API_KEY, runner=flaky)
    rows = load_findings(tmp_path)
    assert rows[0]["issue_url"].startswith("og:")
    assert "issue_url" not in rows[1]


def test_opsgenie_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    fake = FakeOpsgenie()
    before = findings_path(tmp_path).read_bytes()
    result = sync_findings_opsgenie(tmp_path, API_KEY, runner=fake, dry_run=True)
    assert len(result["pushed"]) == 2
    assert result["dry_run"] is True
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before


def test_opsgenie_retry_succeeds_on_second_attempt(tmp_path, monkeypatch, capsys):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_retry_timing(monkeypatch)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    result = sync_findings_opsgenie(tmp_path, API_KEY, runner=flaky)
    assert len(result["pushed"]) == 1
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]
    assert "retrying" in capsys.readouterr().err


def test_tick_opsgenie_dispatches_alert_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeOpsgenie()
    result = tick(_opsgenie_config(), reports_directory=tmp_path, runner=fake)
    assert result["disabled"] is False
    assert len(fake.calls) == 2
    assert all(OPSGENIE_ALERTS_URL in call[0] for call in fake.calls)


def test_tick_opsgenie_no_api_key_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakeOpsgenie()
    result = tick(
        _opsgenie_config(opsgenie_api_key=""),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["error"] == NO_OPSGENIE_API_KEY_MESSAGE
    assert fake.calls == []


def test_cli_opsgenie_mode_posts(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _opsgenie_config(tracker="github"),
    )
    fake = FakeOpsgenie()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--mode", "opsgenie", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2
    assert "pushed" in capsys.readouterr().out


def test_cli_opsgenie_tracker_from_config_without_flag(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _opsgenie_config(),
    )
    fake = FakeOpsgenie()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2


def test_cli_opsgenie_missing_api_key_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _opsgenie_config(opsgenie_api_key=""),
    )
    assert main(["--tracker", "opsgenie", "--reports-dir", str(tmp_path)]) == 2
    assert "no opsgenie api key configured" in capsys.readouterr().err


# Jira REST API v3 mode (L8) ----------------------------------------------------

JIRA_BASE_URL = "https://example.atlassian.net"
JIRA_EMAIL = "bot@example.com"
JIRA_API_TOKEN = "atlassian-api-token"
JIRA_PROJECT = "RED"


def _jira_kwargs(**overrides):
    kwargs = {
        "base_url": JIRA_BASE_URL,
        "email": JIRA_EMAIL,
        "api_token": JIRA_API_TOKEN,
        "project_key": JIRA_PROJECT,
    }
    kwargs.update(overrides)
    return kwargs


class FakeJira:
    """Fake GhRunner answering JQL search + issue create/update; zero network."""

    def __init__(self, existing=()):
        self.calls: list[tuple[list[str], dict]] = []
        self.existing = list(existing)
        self.created = 0

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        url = argv[2]
        if url.endswith("/rest/api/3/search/jql"):
            issues = [{"key": key} for key in self.existing]
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"issues": issues}), stderr=""
            )
        if url.endswith("/rest/api/3/issue"):
            self.created += 1
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"key": f"{JIRA_PROJECT}-{100 + self.created}", "id": "10001"}
                ),
                stderr="",
            )
        if "/rest/api/3/issue/" in url:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


def _jira_config(**overrides):
    node = {
        "enabled": True,
        "tracker": "jira",
        "jira": {
            "base_url": JIRA_BASE_URL,
            "email": JIRA_EMAIL,
            "api_token": JIRA_API_TOKEN,
            "project_key": JIRA_PROJECT,
        },
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_jira_payload_shape(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(severity="high"))
    payload = jira_payload(row, JIRA_PROJECT)
    fields = payload["fields"]
    assert fields["summary"] == issue_title(row)
    assert fields["project"] == {"key": JIRA_PROJECT}
    assert fields["issuetype"] == {"name": "Task"}
    assert f"dedupe:{row['dedupe_hash']}" in fields["labels"]
    assert "severity:high" in fields["labels"]
    description = fields["description"]
    assert description["type"] == "doc"
    paragraph = description["content"][0]
    assert paragraph["type"] == "paragraph"
    assert paragraph["content"][0]["text"] == issue_body(row)


def test_jira_payload_defaults_no_project(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    fields = jira_payload(row)["fields"]
    assert fields["issuetype"] == {"name": "Task"}
    assert "project" not in fields
    assert f"dedupe:{row['dedupe_hash']}" in fields["labels"]


def test_jira_payload_fixed_fields_only(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(response="do anything now"))
    row["raw_response"] = "do anything now"
    assert "do anything now" not in json.dumps(jira_payload(row, JIRA_PROJECT))


def test_jira_happy_two_findings_search_then_create(tmp_path):
    _seed_two(tmp_path)
    fake = FakeJira()
    result = sync_findings_jira(tmp_path, runner=fake, **_jira_kwargs())
    assert len(result["pushed"]) == 2
    assert result["skipped"] == []
    assert result["dry_run"] is False
    rows = load_findings(tmp_path)
    assert all(row.get("synced_at") for row in rows)
    assert rows[0]["issue_url"] == f"jira:{JIRA_PROJECT}-101:{rows[0]['dedupe_hash']}"
    assert rows[1]["issue_url"] == f"jira:{JIRA_PROJECT}-102:{rows[1]['dedupe_hash']}"
    assert len(fake.calls) == 4  # one JQL search + one create per row
    search_argv, search_data = fake.calls[0]
    assert search_argv[2] == f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    assert search_argv[3:5] == ["--method", "GET"]
    assert any(part.startswith("Authorization: Basic ") for part in search_argv)
    expected_jql = (
        f'labels = "dedupe:{rows[0]["dedupe_hash"]}" AND project = {JIRA_PROJECT}'
    )
    assert search_data["jql"] == expected_jql
    create_argv, create_data = fake.calls[1]
    assert create_argv[2] == f"{JIRA_BASE_URL}/rest/api/3/issue"
    assert create_argv[3:5] == ["--method", "POST"]
    assert any(part.startswith("Authorization: Basic ") for part in create_argv)
    assert create_data["fields"]["summary"] == issue_title(rows[0])
    assert f"dedupe:{rows[0]['dedupe_hash']}" in create_data["fields"]["labels"]


def test_jira_secrets_ride_argv_only(tmp_path):
    _seed_two(tmp_path)
    fake = FakeJira()
    sync_findings_jira(tmp_path, runner=fake, **_jira_kwargs())
    for _argv, data in fake.calls:
        assert JIRA_API_TOKEN not in json.dumps(data)
        assert JIRA_EMAIL not in json.dumps(data)


def test_jira_update_existing_issue_no_duplicate(tmp_path):
    row = record_finding(directory=tmp_path, **_finding())
    fake = FakeJira(existing=[f"{JIRA_PROJECT}-9"])
    result = sync_findings_jira(tmp_path, runner=fake, **_jira_kwargs())
    assert len(result["pushed"]) == 1
    assert len(fake.calls) == 2  # search + update, no create POST
    update_argv, update_data = fake.calls[1]
    assert update_argv[2] == f"{JIRA_BASE_URL}/rest/api/3/issue/{JIRA_PROJECT}-9"
    assert update_argv[3:5] == ["--method", "PUT"]
    assert update_data["fields"]["summary"] == issue_title(row)
    assert "project" not in update_data["fields"]
    rows = load_findings(tmp_path)
    assert rows[0]["issue_url"] == f"jira:{JIRA_PROJECT}-9:{row['dedupe_hash']}"


def test_jira_blank_field_fail_closed(tmp_path):
    for field in ("base_url", "email", "api_token", "project_key"):
        kwargs = _jira_kwargs(**{field: "   "})
        with pytest.raises(ValueError, match="no jira config configured"):
            sync_findings_jira(tmp_path, runner=FakeJira(), **kwargs)


def test_jira_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeJira()
    sync_findings_jira(tmp_path, runner=fake, **_jira_kwargs())
    assert len(fake.calls) == 4
    result = sync_findings_jira(tmp_path, runner=fake, **_jira_kwargs())
    assert result["pushed"] == []
    assert len(result["skipped"]) == 2
    assert len(fake.calls) == 4


def test_jira_nonzero_exit_leaves_row_unsynced(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def bad(argv, data):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(GhApiError):
        sync_findings_jira(tmp_path, runner=bad, **_jira_kwargs())
    assert all("issue_url" not in row for row in load_findings(tmp_path))


def test_jira_runner_exception_mid_run_persists_earlier_push(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    calls: list = []
    inner = FakeJira()

    def flaky(argv, data):
        calls.append((argv, data))
        if len(calls) > 2:  # second row's search fails after first row pushed
            raise RuntimeError("jira down")
        return inner(argv, data)

    with pytest.raises(GhApiError, match="gh runner failed"):
        sync_findings_jira(tmp_path, runner=flaky, **_jira_kwargs())
    rows = load_findings(tmp_path)
    assert rows[0]["issue_url"].startswith("jira:")
    assert "issue_url" not in rows[1]


def test_jira_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeJira()
    result = sync_findings_jira(tmp_path, runner=fake, dry_run=True, **_jira_kwargs())
    assert result["dry_run"] is True
    assert len(result["pushed"]) == 2
    assert all(entry["issue_url"] == "" for entry in result["pushed"])
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before


def test_jira_retry_succeeds_on_second_attempt(tmp_path, monkeypatch, capsys):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_retry_timing(monkeypatch)
    calls: list = []
    inner = FakeJira()

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return inner(argv, data)

    result = sync_findings_jira(tmp_path, runner=flaky, **_jira_kwargs())
    assert len(result["pushed"]) == 1
    assert len(calls) == 3  # search retried, then create
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]
    assert "retrying" in capsys.readouterr().err


def test_tick_jira_dispatches_search_and_create(tmp_path):
    _seed_two(tmp_path)
    fake = FakeJira()
    result = tick(_jira_config(), reports_directory=tmp_path, runner=fake)
    assert len(result["pushed"]) == 2
    assert result.get("disabled") is False
    assert all("/rest/api/3/" in call[0][2] for call in fake.calls)


def test_tick_jira_incomplete_config_structured_error(tmp_path):
    _seed_two(tmp_path)
    node = _jira_config()
    node["redteam"]["findings_sync"]["jira"] = {"project_key": JIRA_PROJECT}
    fake = FakeJira()
    result = tick(node, reports_directory=tmp_path, runner=fake)
    assert result["error"] == NO_JIRA_CONFIG_MESSAGE
    assert result["pushed"] == []
    assert fake.calls == []


def test_cli_jira_mode_posts(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _jira_config(tracker="github"),
    )
    fake = FakeJira()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--mode", "jira", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 4
    assert "pushed" in capsys.readouterr().out


def test_cli_jira_tracker_from_config_without_flag(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _jira_config(),
    )
    fake = FakeJira()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 4


def test_cli_jira_missing_config_exits_two(tmp_path, monkeypatch, capsys):
    node = _jira_config()
    node["redteam"]["findings_sync"]["jira"] = {}
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: node,
    )
    assert main(["--tracker", "jira", "--reports-dir", str(tmp_path)]) == 2
    assert "no jira config configured" in capsys.readouterr().err


# Generic-webhook mode with HMAC signing (M8) ----------------------------------

WEBHOOK_URL = "https://example.com/hooks/omega"
SIGNING_SECRET = "current-secret"
PREVIOUS_SECRET = "previous-secret"


class FakeWebhook:
    """Fake GhRunner answering generic-webhook POSTs; zero network."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, data):
        self.calls.append((argv, data))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


def _webhook_config(**overrides):
    node = {
        "enabled": True,
        "tracker": "webhook",
        "webhook_url": WEBHOOK_URL,
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_sign_payload_matches_hmac_sha256_vectors():
    assert (
        sign_payload(b"hello", "secret")
        == "88aab3ede8d3adf94d26ab90d3bafd4a2083070c3bcce9c014ee04a443847c0b"
    )
    assert (
        sign_payload(b"The quick brown fox jumps over the lazy dog", "key")
        == "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    )


def test_webhook_signing_headers_cover_timestamp_dot_payload():
    payload = b'{"dedupe_hash":"abc"}'
    headers = webhook_signing_headers(payload, SIGNING_SECRET, timestamp=1700000000)
    assert headers[WEBHOOK_TIMESTAMP_HEADER] == "1700000000"
    assert headers[WEBHOOK_SIGNATURE_HEADER] == (
        "sha256=" + sign_payload(b"1700000000." + payload, SIGNING_SECRET)
    )
    assert WEBHOOK_PREVIOUS_SIGNATURE_HEADER not in headers


def test_webhook_signing_rotation_emits_previous_signature():
    payload = b'{"dedupe_hash":"abc"}'
    headers = webhook_signing_headers(
        payload, SIGNING_SECRET, PREVIOUS_SECRET, timestamp=1700000000
    )
    assert headers[WEBHOOK_SIGNATURE_HEADER] == (
        "sha256=" + sign_payload(b"1700000000." + payload, SIGNING_SECRET)
    )
    assert headers[WEBHOOK_PREVIOUS_SIGNATURE_HEADER] == (
        "sha256=" + sign_payload(b"1700000000." + payload, PREVIOUS_SECRET)
    )


def test_webhook_signing_blank_secret_is_unsigned():
    payload = b'{"dedupe_hash":"abc"}'
    assert webhook_signing_headers(payload, "", timestamp=1700000000) == {}
    assert webhook_signing_headers(payload, "   ", timestamp=1700000000) == {}


def test_webhook_payload_fixed_fields_only(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(response="do anything now"))
    row["raw_response"] = "do anything now"
    body = webhook_payload(row)
    assert body["title"] == issue_title(row)
    assert body["body"] == issue_body(row)
    assert "do anything now" not in json.dumps(body)


def test_webhook_happy_two_findings_two_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeWebhook()
    result = sync_findings_webhook(tmp_path, WEBHOOK_URL, runner=fake)
    assert len(fake.calls) == 2
    assert all(WEBHOOK_URL in call[0] for call in fake.calls)
    assert all("headers" not in call[1] for call in fake.calls)
    rows = load_findings(tmp_path)
    assert [row["issue_url"] for row in rows] == [
        f"webhook:{row['dedupe_hash']}" for row in rows
    ]
    assert all(row.get("synced_at") for row in rows)
    assert len(result["pushed"]) == 2


def test_webhook_unsigned_argv_has_no_signing_headers(tmp_path):
    record_finding(directory=tmp_path, **_finding())
    fake = FakeWebhook()
    sync_findings_webhook(tmp_path, WEBHOOK_URL, runner=fake)
    argv = fake.calls[0][0]
    assert not [part for part in argv if part.startswith("X-Omega-")]
    assert "-H" not in argv


def test_webhook_signed_argv_carries_signature_headers(tmp_path):
    record_finding(directory=tmp_path, **_finding())
    fake = FakeWebhook()
    sync_findings_webhook(
        tmp_path,
        WEBHOOK_URL,
        runner=fake,
        signing_secret=SIGNING_SECRET,
        previous_signing_secret=PREVIOUS_SECRET,
        timestamp=1700000000,
    )
    argv = fake.calls[0][0]
    headers = {
        argv[i + 1].split(": ", 1)[0]: argv[i + 1].split(": ", 1)[1]
        for i, part in enumerate(argv[:-1])
        if part == "-H"
    }
    assert headers[WEBHOOK_TIMESTAMP_HEADER] == "1700000000"
    assert headers[WEBHOOK_SIGNATURE_HEADER].startswith("sha256=")
    assert headers[WEBHOOK_PREVIOUS_SIGNATURE_HEADER].startswith("sha256=")
    body = fake.calls[0][1]["body"]
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    assert headers[WEBHOOK_SIGNATURE_HEADER] == (
        "sha256=" + sign_payload(b"1700000000." + payload, SIGNING_SECRET)
    )
    assert headers[WEBHOOK_PREVIOUS_SIGNATURE_HEADER] == (
        "sha256=" + sign_payload(b"1700000000." + payload, PREVIOUS_SECRET)
    )


def test_webhook_empty_url_fail_closed():
    with pytest.raises(ValueError, match="no webhook url configured"):
        sync_findings_webhook(None, "   ", runner=FakeWebhook())


def test_webhook_second_run_pushes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeWebhook()
    sync_findings_webhook(tmp_path, WEBHOOK_URL, runner=fake)
    result = sync_findings_webhook(tmp_path, WEBHOOK_URL, runner=fake)
    assert len(fake.calls) == 2
    assert len(result["skipped"]) == 2


def test_webhook_dry_run_writes_nothing_calls_no_runner(tmp_path):
    _seed_two(tmp_path)
    fake = FakeWebhook()
    before = findings_path(tmp_path).read_bytes()
    result = sync_findings_webhook(tmp_path, WEBHOOK_URL, runner=fake, dry_run=True)
    assert fake.calls == []
    assert result["dry_run"] is True
    assert findings_path(tmp_path).read_bytes() == before


def test_webhook_env_secret_signs_when_node_blank(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_SIGNING_SECRET", SIGNING_SECRET
    )
    monkeypatch.delenv(
        "OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_SIGNING_SECRET_PREVIOUS",
        raising=False,
    )
    from omega_prime.grokbot.findings_sync import _webhook_signing_secrets

    current, previous = _webhook_signing_secrets({})
    assert current == SIGNING_SECRET
    assert previous == ""


def test_tick_webhook_dispatches_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeWebhook()
    result = tick(_webhook_config(), reports_directory=tmp_path, runner=fake)
    assert len(fake.calls) == 2
    assert result["disabled"] is False


def test_tick_webhook_no_url_structured_error(tmp_path):
    _seed_two(tmp_path)
    fake = FakeWebhook()
    result = tick(
        _webhook_config(webhook_url=""), reports_directory=tmp_path, runner=fake
    )
    assert result["error"] == NO_WEBHOOK_URL_MESSAGE
    assert fake.calls == []


def test_tick_webhook_env_url_and_secret(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv(
        "OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_SIGNING_SECRET", SIGNING_SECRET
    )
    node = _webhook_config()
    del node["redteam"]["findings_sync"]["webhook_url"]
    fake = FakeWebhook()
    result = tick(node, reports_directory=tmp_path, runner=fake)
    assert len(fake.calls) == 2
    assert result["disabled"] is False
    headers = fake.calls[0][1]["headers"]
    assert headers[WEBHOOK_SIGNATURE_HEADER].startswith("sha256=")


def test_cli_webhook_posts_and_missing_url_exits_two(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _webhook_config(),
    )
    fake = FakeWebhook()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--tracker", "webhook", "--reports-dir", str(tmp_path)]) == 0
    assert len(fake.calls) == 2
    assert "pushed" in capsys.readouterr().out

    node = _webhook_config(webhook_url="")
    monkeypatch.setattr("omega_prime.config.load_config", lambda _root=None: node)
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_URL", raising=False)
    assert main(["--tracker", "webhook", "--reports-dir", str(tmp_path)]) == 2
    assert "no webhook url configured" in capsys.readouterr().err


# Per-grant tracker allowlists in tick (M11) ------------------------------------


def _grant_config(tracker, covers, **overrides):
    node = {
        "enabled": True,
        "tracker": tracker,
        "repo": REPO,
        "slack_webhook_url": WEBHOOK,
        "roe_artifact": {"covers": covers},
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_tick_grant_covered_tracker_dispatches(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _grant_config("github", ["github"]),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result


def test_tick_grant_uncovered_tracker_skipped_with_reason(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _grant_config("github", ["slack", "jira"]),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert fake.calls == []
    assert result["pushed"] == [] and result["skipped"] == []
    assert result["skipped_tracker"] == "github"
    assert "github" in result["reason"] and "grant" in result["reason"]


def test_tick_grant_missing_covers_allows_all(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _grant_config("github", None),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result


def test_tick_grant_empty_covers_allows_all(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _grant_config("github", []),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result


def test_tick_grant_covers_match_case_insensitively(tmp_path):
    _seed_two(tmp_path)
    fake = FakeSlack()
    result = tick(
        _grant_config("slack", ["Slack"]),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result


def test_tick_grant_skip_leaves_store_untouched(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeGh()
    tick(
        _grant_config("github", ["jira"]),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert findings_path(tmp_path).read_bytes() == before


def test_tick_grant_plural_artifacts_union(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    node = _grant_config("github", ["slack"])
    node["redteam"]["findings_sync"]["roe_artifacts"] = [
        {"covers": ["slack"]},
        {"covers": ["github"]},
    ]
    del node["redteam"]["findings_sync"]["roe_artifact"]
    result = tick(node, reports_directory=tmp_path, runner=fake)
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result


def test_tick_no_grant_context_backward_compatible(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        {"redteam": {"findings_sync": {"enabled": True, "repo": REPO}}},
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result


# Webhook payload templates (N8) -------------------------------------------------


def _template_row(tmp_path):
    return record_finding(directory=tmp_path, **_finding())


def test_render_template_spec_example(tmp_path):
    row = _template_row(tmp_path)
    rendered = findings_sync.render_template(
        '{"text": "{{issue_title}} [{{severity}}] {{dedupe_hash}}"}', row
    )
    assert rendered == (
        '{"text": "' + issue_title(row) + " [high] " + row["dedupe_hash"] + '"}'
    )


def test_render_template_all_whitelisted_fields(tmp_path):
    row = _template_row(tmp_path)
    rendered = findings_sync.render_template(
        "{{issue_title}}|{{severity}}|{{class}}|{{dedupe_hash}}|"
        "{{case_id}}|{{observed_detection}}|{{timestamp}}",
        row,
    )
    assert rendered == "|".join(
        [
            issue_title(row),
            "high",
            "prompt_injection",
            row["dedupe_hash"],
            "abuse-01",
            "no-detection",
            row.get("last_seen") or row.get("ts"),
        ]
    )


def test_render_template_unknown_placeholders_render_empty(tmp_path):
    row = _template_row(tmp_path)
    assert findings_sync.render_template("a{{nope}}b{{}}c{{ }}d", row) == "abcd"


def test_render_template_malformed_braces_verbatim(tmp_path):
    row = _template_row(tmp_path)
    assert findings_sync.render_template("a {{unclosed b", row) == "a {{unclosed b"
    assert findings_sync.render_template("a unclosed}} b", row) == "a unclosed}} b"
    assert findings_sync.render_template("no braces", row) == "no braces"


def test_render_template_deterministic_and_no_eval(tmp_path):
    row = _template_row(tmp_path)
    template = "{{severity}} {{__class__}} {{__import__('os').system('x')}}"
    first = findings_sync.render_template(template, row)
    assert first == findings_sync.render_template(template, row) == "high  "
    assert findings_sync.render_template("{{verdict}}", row) == ""


def test_render_template_non_string_template_returns_empty(tmp_path):
    row = _template_row(tmp_path)
    assert findings_sync.render_template(None, row) == ""
    assert findings_sync.render_template(123, row) == ""


def test_webhook_template_posts_rendered_body_verbatim(tmp_path):
    row = _template_row(tmp_path)
    fake = FakeWebhook()
    template = '{"text": "{{issue_title}} [{{severity}}] {{dedupe_hash}}"}'
    result = sync_findings_webhook(
        tmp_path, WEBHOOK_URL, runner=fake, webhook_template=template
    )
    assert len(result["pushed"]) == 1
    expected = findings_sync.render_template(template, row)
    assert fake.calls[0][1]["body"] == expected
    argv = fake.calls[0][0]
    assert f"body={expected}" in argv
    assert "title=" not in " ".join(argv)


def test_webhook_template_signed_over_rendered_bytes(tmp_path):
    _template_row(tmp_path)
    fake = FakeWebhook()
    template = "{{case_id}}:{{severity}}"
    sync_findings_webhook(
        tmp_path,
        WEBHOOK_URL,
        runner=fake,
        signing_secret=SIGNING_SECRET,
        previous_signing_secret=PREVIOUS_SECRET,
        timestamp=1700000000,
        webhook_template=template,
    )
    rendered = fake.calls[0][1]["body"]
    assert rendered == "abuse-01:high"
    expected = webhook_signing_headers(
        rendered.encode("utf-8"), SIGNING_SECRET, PREVIOUS_SECRET, 1700000000
    )
    assert fake.calls[0][1]["headers"] == expected


def test_webhook_blank_template_keeps_fixed_payload(tmp_path):
    _template_row(tmp_path)
    fake = FakeWebhook()
    sync_findings_webhook(tmp_path, WEBHOOK_URL, runner=fake, webhook_template="   ")
    body = fake.calls[0][1]["body"]
    assert isinstance(body, dict)
    assert set(body) == {"title", "body", "dedupe_hash", "severity"}


def test_tick_webhook_template_from_config(tmp_path):
    _seed_two(tmp_path)
    fake = FakeWebhook()
    node = _webhook_config(webhook_template="{{case_id}}:{{severity}}")
    result = tick(node, reports_directory=tmp_path, runner=fake)
    assert len(result["pushed"]) == 2
    assert fake.calls[0][1]["body"] == "abuse-01:high"
    assert fake.calls[1][1]["body"] == "abuse-02:low"


def test_cli_webhook_template_override(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _webhook_config(),
    )
    fake = FakeWebhook()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert (
        main(
            [
                "--tracker",
                "webhook",
                "--webhook-template",
                "{{case_id}}!",
                "--reports-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert fake.calls[0][1]["body"] == "abuse-01!"
    capsys.readouterr()


def test_cli_webhook_template_flag_beats_config(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _webhook_config(webhook_template="config:{{case_id}}"),
    )
    fake = FakeWebhook()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert (
        main(
            [
                "--tracker",
                "webhook",
                "--webhook-template",
                "flag:{{case_id}}",
                "--reports-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert fake.calls[0][1]["body"] == "flag:abuse-01"
    capsys.readouterr()


# Grant usage ledger in tick (N11) -----------------------------------------------


def test_tick_grant_allow_records_ledger_row(tmp_path):
    from omega_prime.grokbot.roe_lifecycle import read_grant_usage

    _seed_two(tmp_path)
    fake = FakeGh()
    ledger = str(tmp_path / "grant-usage.jsonl")
    result = tick(
        _grant_config("github", ["github"], grant_usage_ledger=ledger),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result
    rows = read_grant_usage(ledger)
    assert len(rows) == 1
    assert rows[0]["tracker"] == "github"
    assert rows[0]["verdict"] == "allow"
    assert set(rows[0]) == {"ts", "artifact_id", "tracker", "verdict"}


def test_tick_grant_skip_records_ledger_row(tmp_path):
    from omega_prime.grokbot.roe_lifecycle import read_grant_usage

    _seed_two(tmp_path)
    fake = FakeGh()
    ledger = str(tmp_path / "grant-usage.jsonl")
    result = tick(
        _grant_config("github", ["slack"], grant_usage_ledger=ledger),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["skipped_tracker"] == "github"
    assert fake.calls == []
    rows = read_grant_usage(ledger)
    assert len(rows) == 1
    assert rows[0]["tracker"] == "github"
    assert rows[0]["verdict"] == "skip"


def test_tick_without_ledger_path_writes_nothing(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _grant_config("github", ["github"]),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result
    assert not (tmp_path / "grant-usage.jsonl").exists()


def test_tick_ledger_never_blocks_dispatch(tmp_path, monkeypatch):
    _seed_two(tmp_path)
    fake = FakeGh()
    monkeypatch.setattr(
        "omega_prime.grokbot.roe_lifecycle.record_grant_usage",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = tick(
        _grant_config(
            "github", ["github"], grant_usage_ledger=str(tmp_path / "u.jsonl")
        ),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "skipped_tracker" not in result


# Retry-After rate-limit awareness (O8) -----------------------------------------


def _patch_o8_sleep(monkeypatch):
    """Deterministic retry timing via the module-level ``_sleep`` seam."""
    monkeypatch.setattr(findings_sync.random, "uniform", lambda _a, _b: 0.0)
    sleeps: list[float] = []
    monkeypatch.setattr(findings_sync, "_sleep", sleeps.append)
    return sleeps


def _o8_runner(script):
    """Fake runner playing a script of results; records (argv, data) calls."""
    calls: list = []

    def run(argv, data):
        calls.append((list(argv), dict(data)))
        result = script[min(len(calls) - 1, len(script) - 1)]
        if isinstance(result, BaseException):
            raise result
        return result

    return calls, run


_O8_ARGV = ["gh", "api", "https://example.com/hook", "--method", "POST"]


def test_retry_after_delay_unit_matrix():
    assert findings_sync._retry_after_delay("429\nRetry-After: 5", 1.0) == 5.0
    assert findings_sync._retry_after_delay("429\nretry-after: 9", 1.0) == 9.0
    assert findings_sync._retry_after_delay("429\nRetry-After: 120", 1.0) == 60.0
    assert (
        findings_sync._retry_after_delay(
            "429\nRetry-After: Wed, 21 Oct 2015 07:28:00 GMT", 2.0
        )
        == 2.0
    )
    assert findings_sync._retry_after_delay("429\nRetry-After: soon", 2.0) == 2.0
    assert findings_sync._retry_after_delay("429 slow down", 2.0) == 2.0
    assert findings_sync._retry_after_delay("500\nRetry-After: 30", 2.0) == 2.0
    assert findings_sync._retry_after_delay("boom", 2.0) == 2.0
    assert findings_sync._retry_after_delay(None, 2.0) == 2.0


def test_retry_after_seconds_overrides_backoff(monkeypatch, capsys):
    sleeps = _patch_o8_sleep(monkeypatch)
    calls, run = _o8_runner(
        [
            SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="HTTP 429 Too Many Requests\nRetry-After: 5",
            ),
            SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ]
    )
    completed = findings_sync._run_with_webhook_retry(run, _O8_ARGV, {})
    assert completed.returncode == 0
    assert len(calls) == 2
    assert sleeps == [5.0]
    assert "retrying in 5.0s" in capsys.readouterr().err


def test_retry_after_capped_at_sixty(monkeypatch):
    sleeps = _patch_o8_sleep(monkeypatch)
    calls, run = _o8_runner(
        [
            SimpleNamespace(
                returncode=1, stdout="", stderr="error 429\nRetry-After: 120"
            ),
            SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ]
    )
    findings_sync._run_with_webhook_retry(run, _O8_ARGV, {})
    assert len(calls) == 2
    assert sleeps == [60.0]


def test_retry_after_http_date_falls_back_to_default(monkeypatch):
    sleeps = _patch_o8_sleep(monkeypatch)
    calls, run = _o8_runner(
        [
            SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="429 Too Many Requests\nRetry-After: Wed, 21 Oct 2015 07:28:00 GMT",
            ),
            SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ]
    )
    findings_sync._run_with_webhook_retry(run, _O8_ARGV, {})
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]


def test_retry_after_missing_falls_back_to_default(monkeypatch):
    sleeps = _patch_o8_sleep(monkeypatch)
    calls, run = _o8_runner(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="429 rate limited"),
            SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ]
    )
    findings_sync._run_with_webhook_retry(run, _O8_ARGV, {})
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]


def test_retry_after_unparseable_falls_back_to_default(monkeypatch):
    sleeps = _patch_o8_sleep(monkeypatch)
    calls, run = _o8_runner(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="429\nRetry-After: soon"),
            SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ]
    )
    findings_sync._run_with_webhook_retry(run, _O8_ARGV, {})
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]


def test_retry_after_ignored_for_non_429(monkeypatch):
    sleeps = _patch_o8_sleep(monkeypatch)
    calls, run = _o8_runner(
        [
            SimpleNamespace(
                returncode=1, stdout="", stderr="500 Internal Error\nRetry-After: 30"
            ),
            SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ]
    )
    findings_sync._run_with_webhook_retry(run, _O8_ARGV, {})
    assert len(calls) == 2
    assert sleeps == [findings_sync.WEBHOOK_RETRY_DELAYS[0]]


def test_slack_retry_after_end_to_end(tmp_path, monkeypatch):
    record_finding(directory=tmp_path, **_finding())
    sleeps = _patch_o8_sleep(monkeypatch)
    calls: list = []

    def flaky(argv, data):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="429 Too Many Requests\nRetry-After: 7",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    result = sync_findings_slack(tmp_path, WEBHOOK, runner=flaky)
    assert len(result["pushed"]) == 1
    assert len(calls) == 2
    assert sleeps == [7.0]


# Grant expiry notices via adapters (O11) ---------------------------------------


@pytest.fixture
def fresh_notices():
    """Isolate the in-memory expiry-notice fire record per O11 test."""
    findings_sync.clear_expiry_notices()
    yield
    findings_sync.clear_expiry_notices()


def _expiring_grant(operator, grant_state="expiring-7d"):
    """Inline RoE grant dict landing in ``grant_state`` at the wall clock."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    if grant_state == "expired":
        until = now - timedelta(hours=1)
    elif grant_state == "expiring-1d":
        until = now + timedelta(hours=12)
    elif grant_state == "expiring-7d":
        until = now + timedelta(days=3)
    else:  # valid
        until = now + timedelta(days=30)
    return {"operator": operator, "effective_until": until.isoformat()}


def _notice_config(operator="o11-operator", grant_state="expiring-7d", **overrides):
    node = {
        "enabled": True,
        "tracker": "slack",
        "slack_webhook_url": WEBHOOK,
        "roe_artifact": _expiring_grant(operator, grant_state),
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def test_tick_expiring_grant_delivers_notice_via_slack(tmp_path, fresh_notices):
    fake = FakeSlack()
    result = tick(_notice_config(), reports_directory=tmp_path, runner=fake)
    assert len(fake.calls) == 1
    argv, data = fake.calls[0]
    assert WEBHOOK in argv
    assert "o11-operator" in data["text"]
    assert "expiring-7d" in data["text"]
    assert "re-sign" in data["text"]
    (entry,) = result["expiry_notice"]
    assert entry["delivered"] is True
    assert entry["tracker"] == "slack"
    assert entry["issue_url"].startswith("notice:")
    assert entry["issue_url"].endswith(":expiring-7d")
    assert entry["artifact_id"].startswith("o11-operator|")
    assert entry["state"] == "expiring-7d"


def test_tick_notice_fires_once_per_artifact_state(tmp_path, fresh_notices):
    fake = FakeSlack()
    config = _notice_config(operator="o11-once")
    first = tick(config, reports_directory=tmp_path, runner=fake)
    assert first["expiry_notice"][0]["delivered"] is True
    second = tick(config, reports_directory=tmp_path, runner=fake)
    assert len(fake.calls) == 1
    (entry,) = second["expiry_notice"]
    assert entry["delivered"] is False
    assert entry["duplicate"] is True
    assert entry["issue_url"].startswith("notice:")


def test_tick_expired_grant_notice_says_archive(tmp_path, fresh_notices):
    fake = FakeSlack()
    result = tick(
        _notice_config(operator="o11-expired", grant_state="expired"),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 1
    assert "expired" in fake.calls[0][1]["text"]
    assert "archive" in fake.calls[0][1]["text"]
    (entry,) = result["expiry_notice"]
    assert entry["delivered"] is True
    assert entry["issue_url"].endswith(":expired")


def test_tick_valid_grant_sends_no_notice(tmp_path, fresh_notices):
    fake = FakeSlack()
    result = tick(
        _notice_config(operator="o11-healthy", grant_state="valid"),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert fake.calls == []
    assert "expiry_notice" not in result


def test_tick_notice_digest_only_without_tracker(tmp_path, fresh_notices):
    fake = FakeSlack()
    node = {
        "enabled": True,
        "tracker": "slack",
        "roe_artifact": _expiring_grant("o11-digest"),
    }
    result = tick(
        {"redteam": {"findings_sync": node}},
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["error"] == NO_SLACK_WEBHOOK_MESSAGE
    assert fake.calls == []
    (entry,) = result["expiry_notice"]
    assert entry["delivered"] is False
    assert entry["issue_url"] == ""
    assert "o11-digest" in entry["notice"]
    assert "expiring-7d" in entry["notice"]


def test_tick_notice_dry_run_calls_no_runner(tmp_path, fresh_notices):
    fake = FakeSlack()
    result = tick(
        _notice_config(operator="o11-dry"),
        reports_directory=tmp_path,
        runner=fake,
        dry_run=True,
    )
    assert fake.calls == []
    (entry,) = result["expiry_notice"]
    assert entry["delivered"] is False
    assert entry["dry_run"] is True
    assert "o11-dry" in entry["notice"]


def test_tick_notice_via_github_issues(tmp_path, fresh_notices):
    fake = FakeGh()
    node = {
        "enabled": True,
        "tracker": "github",
        "repo": REPO,
        "roe_artifact": _expiring_grant("o11-github", "expiring-1d"),
    }
    result = tick(
        {"redteam": {"findings_sync": node}},
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 1
    argv, data = fake.calls[0]
    assert f"repos/{REPO}/issues" in argv
    assert "o11-github" in data["body"]
    assert "expiring-1d" in data["body"]
    (entry,) = result["expiry_notice"]
    assert entry["delivered"] is True
    assert entry["tracker"] == "github"
    assert entry["issue_url"].startswith("notice:")
    assert entry["issue_url"].endswith(":expiring-1d")


def test_tick_notice_failure_stays_undelivered_and_retries(
    tmp_path, fresh_notices, monkeypatch
):
    _patch_retry_timing(monkeypatch)

    def bad(argv, data):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    config = _notice_config(operator="o11-retry")
    failed = tick(config, reports_directory=tmp_path, runner=bad)
    (entry,) = failed["expiry_notice"]
    assert entry["delivered"] is False
    assert "error" in entry
    fake = FakeSlack()
    retried = tick(config, reports_directory=tmp_path, runner=fake)
    assert len(fake.calls) == 1
    assert retried["expiry_notice"][0]["delivered"] is True


# Per-tracker quiet hours (P8) ----------------------------------------------------


def _quiet_config(**overrides):
    node = {
        "enabled": True,
        "repo": REPO,
        "labels": {},
        "quiet_hours": {"start": "22:00", "end": "06:00"},
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


_QUIET_NIGHT = datetime(2026, 6, 1, 23, 30)  # inside 22:00-06:00
_QUIET_DAY = datetime(2026, 6, 1, 12, 0)  # outside 22:00-06:00


def test_in_quiet_hours_wrapping_window():
    cfg = {"start": "22:00", "end": "06:00"}
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 23, 30), cfg) is True
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 2, 3, 0), cfg) is True
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 22, 0), cfg) is True
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 12, 0), cfg) is False
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 2, 6, 0), cfg) is False
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 21, 59), cfg) is False


def test_in_quiet_hours_plain_window():
    cfg = {"start": "09:00", "end": "17:00"}
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 9, 0), cfg) is True
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 12, 0), cfg) is True
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 8, 59), cfg) is False
    assert findings_sync.in_quiet_hours(datetime(2026, 6, 1, 17, 0), cfg) is False


def test_in_quiet_hours_feature_off():
    night = datetime(2026, 6, 1, 23, 30)
    assert (
        findings_sync.in_quiet_hours(night, {"start": "22:00", "end": "22:00"}) is False
    )
    assert findings_sync.in_quiet_hours(night, {"start": "", "end": ""}) is False
    assert findings_sync.in_quiet_hours(night, {}) is False
    assert findings_sync.in_quiet_hours(night, None) is False
    assert (
        findings_sync.in_quiet_hours(night, {"start": "evening", "end": "dawn"})
        is False
    )
    assert findings_sync.in_quiet_hours(night, "22:00") is False


def test_in_quiet_hours_never_raises():
    cfg = {"start": "22:00", "end": "06:00"}
    for bad_now in ("nope", 123, object(), ["x"]):
        assert findings_sync.in_quiet_hours(bad_now, cfg) is False
    assert isinstance(findings_sync.in_quiet_hours(None, cfg), bool)


def test_tick_quiet_hours_holds_non_urgent_with_reason(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="critical"))
    record_finding(
        directory=tmp_path, **_finding(case_id="abuse-02", severity="medium")
    )
    record_finding(directory=tmp_path, **_finding(case_id="abuse-03", severity="low"))
    fake = FakeGh()
    result = tick(
        _quiet_config(), reports_directory=tmp_path, runner=fake, now=_QUIET_NIGHT
    )
    assert len(result["pushed"]) == 1
    assert len(fake.calls) == 1
    assert result["quiet_hours"] is True
    assert [e["reason"] for e in result["quiet_skipped"]] == [
        "quiet-hours",
        "quiet-hours",
    ]
    rows = load_findings(tmp_path)
    held = {e["dedupe_hash"] for e in result["quiet_skipped"]}
    assert held == {rows[1]["dedupe_hash"], rows[2]["dedupe_hash"]}
    assert rows[0].get("issue_url")  # critical fired
    assert "issue_url" not in rows[1] and "issue_url" not in rows[2]


def test_tick_quiet_hours_urgent_always_fire(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="critical"))
    record_finding(directory=tmp_path, **_finding(case_id="abuse-02", severity="HIGH"))
    fake = FakeGh()
    result = tick(
        _quiet_config(), reports_directory=tmp_path, runner=fake, now=_QUIET_NIGHT
    )
    assert len(result["pushed"]) == 2
    assert result["quiet_skipped"] == []
    assert result["quiet_hours"] is True


def test_tick_quiet_hours_unknown_severity_waits(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="weird"))
    fake = FakeGh()
    result = tick(
        _quiet_config(), reports_directory=tmp_path, runner=fake, now=_QUIET_NIGHT
    )
    assert result["pushed"] == []
    assert [e["reason"] for e in result["quiet_skipped"]] == ["quiet-hours"]
    assert fake.calls == []


def test_tick_quiet_hours_daytime_dispatches_all(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _quiet_config(), reports_directory=tmp_path, runner=fake, now=_QUIET_DAY
    )
    assert len(result["pushed"]) == 2
    assert "quiet_hours" not in result
    assert "quiet_skipped" not in result


def test_tick_without_quiet_hours_unchanged(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _enabled_config(), reports_directory=tmp_path, runner=fake, now=_QUIET_NIGHT
    )
    assert len(result["pushed"]) == 2
    assert "quiet_hours" not in result


def test_tick_blank_quiet_hours_unchanged(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    config = _quiet_config(quiet_hours={"start": "", "end": ""})
    result = tick(config, reports_directory=tmp_path, runner=fake, now=_QUIET_NIGHT)
    assert len(result["pushed"]) == 2
    assert "quiet_hours" not in result


def test_tick_quiet_hours_dry_run_holds_quiet(tmp_path):
    record_finding(directory=tmp_path, **_finding(severity="critical"))
    record_finding(directory=tmp_path, **_finding(case_id="abuse-02", severity="low"))
    fake = FakeGh()
    result = tick(
        _quiet_config(),
        reports_directory=tmp_path,
        runner=fake,
        now=_QUIET_NIGHT,
        dry_run=True,
    )
    assert fake.calls == []
    assert len(result["pushed"]) == 1
    assert result["pushed"][0]["issue_url"] == ""
    assert [e["reason"] for e in result["quiet_skipped"]] == ["quiet-hours"]


def test_tick_quiet_hours_notices_still_fire(tmp_path, fresh_notices):
    now = datetime.now(UTC)
    grant = tmp_path / "roe.json"
    grant.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operator": "p8-operator",
                "authorized_targets": [
                    {"host": "127.0.0.1", "tools": ["tempest_status"]}
                ],
                "effective_from": (now - timedelta(days=30)).isoformat(),
                "effective_until": (now + timedelta(days=3)).isoformat(),
                "case_classes": ["prompt-injection"],
            }
        ),
        encoding="utf-8",
    )
    record_finding(directory=tmp_path, **_finding(severity="low"))
    node = {
        "enabled": True,
        "repo": REPO,
        "labels": {},
        "quiet_hours": {"start": "22:00", "end": "06:00"},
        "roe_artifact": str(grant),
    }
    fake = FakeGh()
    result = tick(
        {"redteam": {"findings_sync": node}},
        reports_directory=tmp_path,
        runner=fake,
        now=_QUIET_NIGHT,
    )
    assert result["pushed"] == []
    assert [e["reason"] for e in result["quiet_skipped"]] == ["quiet-hours"]
    (entry,) = result["expiry_notice"]
    assert entry["delivered"] is True
    assert entry["state"] == "expiring-7d"
    posted = " ".join(f"{argv} {data}" for argv, data in fake.calls)
    assert f"Renew: /omega-redteam-roe-sign --artifact {grant}" in posted


# Dispatch dry-run ledger (Q8) --------------------------------------------------


def _dispatch_config(tmp_path, **overrides):
    node = {
        "enabled": True,
        "repo": REPO,
        "labels": {},
        "dispatch_dry_run": True,
        "dispatch_ledger": str(tmp_path / "dispatch-ledger.jsonl"),
    }
    node.update(overrides)
    return {"redteam": {"findings_sync": node}}


def _read_ledger_rows(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle.read().splitlines() if line.strip()]


def test_dispatch_dry_run_plans_two_without_calls_or_writes(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeGh()
    result = tick(
        _dispatch_config(tmp_path),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before
    rows = load_findings(tmp_path)
    assert all("issue_url" not in row for row in rows)
    assert len(result["pushed"]) == 2
    assert result["dry_run"] is True
    plan = result["dispatch_plan"]
    assert [entry["dedupe_hash"] for entry in plan] == [
        row["dedupe_hash"] for row in rows
    ]
    ledger = _read_ledger_rows(tmp_path / "dispatch-ledger.jsonl")
    assert len(ledger) == 2
    for entry in ledger:
        assert set(entry) == {"ts", "tracker", "dedupe_hash", "reason"}
        assert entry["tracker"] == "github"
        assert entry["reason"] == findings_sync.DISPATCH_DRY_RUN_REASON
        assert entry["ts"]
    assert [entry["dedupe_hash"] for entry in ledger] == [
        row["dedupe_hash"] for row in rows
    ]


def test_dispatch_dry_run_blank_ledger_stays_in_memory(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _dispatch_config(tmp_path, dispatch_ledger="   "),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert fake.calls == []
    assert len(result["dispatch_plan"]) == 2
    assert not (tmp_path / "dispatch-ledger.jsonl").exists()


def test_dispatch_dry_run_absent_ledger_key_stays_in_memory(tmp_path):
    _seed_two(tmp_path)
    node = _dispatch_config(tmp_path)
    del node["redteam"]["findings_sync"]["dispatch_ledger"]
    fake = FakeGh()
    result = tick(node, reports_directory=tmp_path, runner=fake)
    assert fake.calls == []
    assert len(result["dispatch_plan"]) == 2
    assert not (tmp_path / "dispatch-ledger.jsonl").exists()


def test_dispatch_flag_off_leaves_no_plan_and_dispatches(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _enabled_config(),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert len(fake.calls) == 2
    assert "dispatch_plan" not in result
    assert not (tmp_path / "dispatch-ledger.jsonl").exists()


def test_dispatch_dry_run_tick_param_overrides_config_off(tmp_path):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeGh()
    result = tick(
        _enabled_config(),
        reports_directory=tmp_path,
        runner=fake,
        dispatch_dry_run=True,
    )
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before
    assert len(result["dispatch_plan"]) == 2


def test_dispatch_dry_run_respects_quiet_hours(tmp_path):
    _seed_two(tmp_path)  # high (urgent) + low (held at night)
    fake = FakeGh()
    node = _dispatch_config(tmp_path, quiet_hours={"start": "22:00", "end": "06:00"})
    result = tick(
        {"redteam": {"findings_sync": node["redteam"]["findings_sync"]}},
        reports_directory=tmp_path,
        runner=fake,
        now=_QUIET_NIGHT,
    )
    assert fake.calls == []
    assert result["quiet_hours"] is True
    assert len(result["quiet_skipped"]) == 1
    assert len(result["dispatch_plan"]) == 1
    ledger = _read_ledger_rows(tmp_path / "dispatch-ledger.jsonl")
    assert len(ledger) == 1
    assert ledger[0]["dedupe_hash"] == result["dispatch_plan"][0]["dedupe_hash"]


def test_dispatch_dry_run_skips_already_synced(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    tick(_enabled_config(), reports_directory=tmp_path, runner=fake)
    assert len(fake.calls) == 2
    planner = FakeGh()
    result = tick(
        _dispatch_config(tmp_path),
        reports_directory=tmp_path,
        runner=planner,
    )
    assert planner.calls == []
    assert result["dispatch_plan"] == []
    assert len(result["skipped"]) == 2
    assert not (tmp_path / "dispatch-ledger.jsonl").exists()


def test_dispatch_dry_run_grant_skip_writes_no_ledger(tmp_path):
    _seed_two(tmp_path)
    node = _dispatch_config(tmp_path)
    node["redteam"]["findings_sync"]["roe_artifact"] = {"covers": ["slack"]}
    fake = FakeGh()
    result = tick(node, reports_directory=tmp_path, runner=fake)
    assert fake.calls == []
    assert result["skipped_tracker"] == "github"
    assert "dispatch_plan" not in result
    assert not (tmp_path / "dispatch-ledger.jsonl").exists()


def test_dispatch_dry_run_ledger_failure_never_blocks(tmp_path):
    _seed_two(tmp_path)
    fake = FakeGh()
    result = tick(
        _dispatch_config(tmp_path, dispatch_ledger=str(tmp_path)),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert fake.calls == []
    assert len(result["dispatch_plan"]) == 2


def test_dispatch_dry_run_slack_tracker_in_ledger(tmp_path):
    _seed_two(tmp_path)
    fake = FakeSlack()
    result = tick(
        _dispatch_config(tmp_path, tracker="slack", slack_webhook_url=WEBHOOK),
        reports_directory=tmp_path,
        runner=fake,
    )
    assert fake.calls == []
    ledger = _read_ledger_rows(tmp_path / "dispatch-ledger.jsonl")
    assert len(ledger) == 2
    assert all(entry["tracker"] == "slack" for entry in ledger)
    assert len(result["dispatch_plan"]) == 2


def test_record_dispatch_plan_blank_path_returns_rows_writes_nothing(tmp_path):
    plan = findings_sync.record_dispatch_plan("", "github", ["abc", "def"])
    assert len(plan) == 2
    assert all(
        set(entry) == {"ts", "tracker", "dedupe_hash", "reason"} for entry in plan
    )
    assert [entry["dedupe_hash"] for entry in plan] == ["abc", "def"]
    assert list(tmp_path.iterdir()) == []


def test_record_dispatch_plan_creates_parent_dirs(tmp_path):
    ledger = tmp_path / "nested" / "dir" / "dispatch-ledger.jsonl"
    plan = findings_sync.record_dispatch_plan(str(ledger), "linear", ["abc"])
    assert len(plan) == 1
    assert _read_ledger_rows(ledger) == plan


def test_cli_dispatch_dry_run_plans_without_calls_or_writes(
    tmp_path, monkeypatch, capsys
):
    _seed_two(tmp_path)
    before = findings_path(tmp_path).read_bytes()
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _dispatch_config(tmp_path),
    )
    fake = FakeGh()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before
    out = capsys.readouterr().out
    assert out.count("would push") == 2
    assert len(_read_ledger_rows(tmp_path / "dispatch-ledger.jsonl")) == 2


def test_cli_dispatch_dry_run_flag_beats_config_off(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    node = _enabled_config()
    node["redteam"]["findings_sync"]["dispatch_ledger"] = str(
        tmp_path / "dispatch-ledger.jsonl"
    )
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: node,
    )
    fake = FakeGh()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--dispatch-dry-run", "--reports-dir", str(tmp_path)]) == 0
    assert fake.calls == []
    assert "would push" in capsys.readouterr().out
    assert len(_read_ledger_rows(tmp_path / "dispatch-ledger.jsonl")) == 2


def test_cli_dispatch_dry_run_missing_repo_exits_two(tmp_path, monkeypatch, capsys):
    _seed_two(tmp_path)
    node = _dispatch_config(tmp_path, repo="")
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: node,
    )
    fake = FakeGh()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 2
    assert fake.calls == []
    assert "no target repo configured" in capsys.readouterr().err


# Per-tracker sync budget (R1) ----------------------------------------------

_BASE_KEYS = {"pushed", "skipped", "dry_run", "disabled"}
_BUDGET_KEYS = _BASE_KEYS | {"sync_budget", "budget_held"}

#: tracker name -> (tick config builder, fake runner factory)
_BUDGET_TRACKERS: dict[str, tuple[Callable[..., Any], Callable[[], Any]]] = {
    "github": (_enabled_config, FakeGh),
    "linear": (_linear_config, FakeLinear),
    "slack": (_slack_config, FakeSlack),
    "teams": (_teams_config, FakeTeams),
    "graph": (_graph_config, FakeGraph),
    "pagerduty": (_pagerduty_config, FakePagerDuty),
    "opsgenie": (_opsgenie_config, FakeOpsgenie),
    "jira": (_jira_config, FakeJira),
    "webhook": (_webhook_config, FakeWebhook),
}
_ALL_TRACKERS = list(_BUDGET_TRACKERS)


def _seed_severities(tmp_path, severities):
    """Record one finding per severity; return dedupe hashes in store order."""
    for index, severity in enumerate(severities):
        record_finding(
            directory=tmp_path,
            **_finding(
                case_id=f"budget-{index:02d}",
                severity=severity,
                response=f"budget text {index}",
            ),
        )
    return [row["dedupe_hash"] for row in load_findings(tmp_path)]


def _budget_tick(name, tmp_path, *, tick_kwargs=None, **node):
    build_config, make_fake = _BUDGET_TRACKERS[name]
    fake = make_fake()
    result = tick(
        build_config(**node),
        reports_directory=tmp_path,
        runner=fake,
        **(tick_kwargs or {}),
    )
    return result, fake


def _synced(tmp_path):
    return [bool(row.get("issue_url")) for row in load_findings(tmp_path)]


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_caps_non_urgent_for_every_tracker(tmp_path, name):
    hashes = _seed_severities(tmp_path, ["low", "low", "medium", "low", "low"])
    before = load_findings(tmp_path)
    result, _fake = _budget_tick(name, tmp_path, sync_budget=2)
    assert set(result) == _BUDGET_KEYS
    assert result["sync_budget"] == 2
    # Severity priority: the lone medium (store index 2) and the oldest low
    # take the budget; pushes still follow store order.
    assert [e["dedupe_hash"] for e in result["pushed"]] == [hashes[0], hashes[2]]
    assert result["budget_held"] == [
        {"dedupe_hash": hashes[index], "reason": "sync-budget"} for index in (1, 3, 4)
    ]
    assert _synced(tmp_path) == [True, False, True, False, False]
    after = load_findings(tmp_path)
    for index in (1, 3, 4):  # held rows are untouched
        assert after[index] == before[index]
        assert "issue_url" not in after[index]
        assert "synced_at" not in after[index]


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_urgent_rows_bypass_and_do_not_consume(tmp_path, name):
    hashes = _seed_severities(tmp_path, ["low", "critical", "low", "HIGH", "low"])
    result, _fake = _budget_tick(name, tmp_path, sync_budget=1)
    assert [e["dedupe_hash"] for e in result["pushed"]] == [
        hashes[0],
        hashes[1],
        hashes[3],
    ]
    assert [e["dedupe_hash"] for e in result["budget_held"]] == [hashes[2], hashes[4]]
    assert _synced(tmp_path) == [True, True, False, True, False]


def test_sync_budget_unknown_severity_is_non_urgent(tmp_path):
    hashes = _seed_severities(tmp_path, ["weird", "weird"])
    result, _fake = _budget_tick("github", tmp_path, sync_budget=1)
    assert [e["dedupe_hash"] for e in result["pushed"]] == [hashes[0]]
    assert [e["dedupe_hash"] for e in result["budget_held"]] == [hashes[1]]


def test_sync_budget_quiet_hours_decided_first_and_never_consume(tmp_path):
    hashes = _seed_severities(tmp_path, ["low", "low", "critical"])
    result = tick(
        _quiet_config(sync_budget=1),
        reports_directory=tmp_path,
        runner=FakeGh(),
        now=_QUIET_NIGHT,
    )
    assert [e["dedupe_hash"] for e in result["pushed"]] == [hashes[2]]
    assert [e["dedupe_hash"] for e in result["quiet_skipped"]] == hashes[:2]
    assert all(e["reason"] == "quiet-hours" for e in result["quiet_skipped"])
    assert result["budget_held"] == []
    assert result["sync_budget"] == 1
    # Outside the window the quiet-held rows now spend the budget oldest-first.
    day = tick(
        _quiet_config(sync_budget=1),
        reports_directory=tmp_path,
        runner=FakeGh(),
        now=_QUIET_DAY,
    )
    assert [e["dedupe_hash"] for e in day["pushed"]] == [hashes[0]]
    assert day["budget_held"] == [{"dedupe_hash": hashes[1], "reason": "sync-budget"}]
    assert "quiet_hours" not in day


def test_sync_budget_fifo_and_next_tick_retries_held_rows(tmp_path):
    hashes = _seed_severities(tmp_path, ["low"] * 5)
    cfg = _enabled_config(sync_budget=2)
    first = tick(cfg, reports_directory=tmp_path, runner=FakeGh())
    assert [e["dedupe_hash"] for e in first["pushed"]] == hashes[:2]
    assert [e["dedupe_hash"] for e in first["budget_held"]] == hashes[2:]
    second = tick(cfg, reports_directory=tmp_path, runner=FakeGh())
    assert [e["dedupe_hash"] for e in second["pushed"]] == hashes[2:4]
    assert second["skipped"] == hashes[:2]
    assert [e["dedupe_hash"] for e in second["budget_held"]] == hashes[4:]
    third = tick(cfg, reports_directory=tmp_path, runner=FakeGh())
    assert [e["dedupe_hash"] for e in third["pushed"]] == hashes[4:]
    assert third["budget_held"] == []
    assert _synced(tmp_path) == [True] * 5


def test_sync_budget_configured_with_nothing_to_sync_reports_empty_hold(tmp_path):
    cfg = _enabled_config(sync_budget=3)
    result = tick(cfg, reports_directory=tmp_path, runner=FakeGh())
    assert result == {
        "pushed": [],
        "skipped": [],
        "dry_run": False,
        "disabled": False,
        "sync_budget": 3,
        "budget_held": [],
    }


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_plan_mode_reflects_budget(tmp_path, name):
    hashes = _seed_severities(tmp_path, ["low", "critical", "low", "low"])
    before = findings_path(tmp_path).read_bytes()
    ledger = tmp_path / "dispatch-ledger.jsonl"
    result, fake = _budget_tick(
        name,
        tmp_path,
        sync_budget=1,
        dispatch_dry_run=True,
        dispatch_ledger=str(ledger),
    )
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before
    planned = [e["dedupe_hash"] for e in result["dispatch_plan"]]
    assert planned == [hashes[0], hashes[1]]
    assert [e["dedupe_hash"] for e in result["budget_held"]] == hashes[2:]
    assert [row["dedupe_hash"] for row in _read_ledger_rows(ledger)] == planned


def test_sync_budget_dry_run_counts_identically_and_writes_nothing(tmp_path):
    hashes = _seed_severities(tmp_path, ["low"] * 4)
    before = findings_path(tmp_path).read_bytes()
    fake = FakeGh()
    result = tick(
        _enabled_config(sync_budget=2),
        reports_directory=tmp_path,
        runner=fake,
        dry_run=True,
    )
    assert fake.calls == []
    assert findings_path(tmp_path).read_bytes() == before
    assert [e["dedupe_hash"] for e in result["pushed"]] == hashes[:2]
    assert [e["dedupe_hash"] for e in result["budget_held"]] == hashes[2:]


def test_sync_budget_mapping_form_tracker_override_and_default(tmp_path):
    budget = {"default": 3, "github": 1}
    hashes = _seed_severities(tmp_path, ["low"] * 4)
    github, _fake = _budget_tick("github", tmp_path, sync_budget=budget)
    assert github["sync_budget"] == 1
    assert [e["dedupe_hash"] for e in github["pushed"]] == hashes[:1]
    slack_dir = tmp_path / "slack"
    slack_hashes = _seed_severities(slack_dir, ["low"] * 4)
    slack, _fake = _budget_tick("slack", slack_dir, sync_budget=budget)
    assert slack["sync_budget"] == 3
    assert [e["dedupe_hash"] for e in slack["pushed"]] == slack_hashes[:3]
    assert [e["dedupe_hash"] for e in slack["budget_held"]] == slack_hashes[3:]


def test_sync_budget_mapping_without_entry_for_tracker_is_off(tmp_path):
    _seed_severities(tmp_path, ["low"] * 3)
    result, _fake = _budget_tick("github", tmp_path, sync_budget={"slack": 1})
    assert set(result) == _BASE_KEYS
    assert len(result["pushed"]) == 3


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (None, None),
        (True, "type"),
        (False, "type"),
        (0, "value"),
        (-1, "value"),
        (2.5, "type"),
        ("3", "type"),
        ([3], "type"),
        ({}, None),
        ({"default": "3"}, "entry"),
        ({"default": 0}, "entry"),
        ({"default": True}, "entry"),
    ],
    ids=repr,
)
@pytest.mark.parametrize("name", ["github", "graph", "opsgenie"])
def test_sync_budget_invalid_value_is_off_and_flagged(tmp_path, name, bad, reason):
    _seed_severities(tmp_path, ["low"] * 3)
    result, _fake = _budget_tick(name, tmp_path, sync_budget=bad)
    if reason is None:
        assert set(result) == _BASE_KEYS
    else:
        assert set(result) == _BASE_KEYS | {"sync_budget_invalid"}
        assert result["sync_budget_invalid"] == reason
    assert len(result["pushed"]) == 3
    assert _synced(tmp_path) == [True] * 3


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_unset_result_shape_unchanged(tmp_path, name):
    _seed_severities(tmp_path, ["low", "critical", "low"])
    result, _fake = _budget_tick(name, tmp_path)
    assert set(result) == _BASE_KEYS
    assert len(result["pushed"]) == 3


def test_sync_budget_direct_calls_accept_kwarg_and_ignore_invalid(tmp_path):
    hashes = _seed_severities(tmp_path, ["low", "low", "low"])
    result = sync_findings_opsgenie(
        tmp_path, API_KEY, runner=FakeOpsgenie(), sync_budget=1
    )
    assert [e["dedupe_hash"] for e in result["pushed"]] == hashes[:1]
    assert [e["dedupe_hash"] for e in result["budget_held"]] == hashes[1:]
    other = tmp_path / "other"
    _seed_severities(other, ["low", "low"])
    off = sync_findings_opsgenie(
        other, API_KEY, runner=FakeOpsgenie(), sync_budget=True
    )
    assert set(off) == {"pushed", "skipped", "dry_run"}
    assert len(off["pushed"]) == 2


def test_sync_budget_reason_exported():
    assert findings_sync.SYNC_BUDGET_REASON == "sync-budget"
    assert "SYNC_BUDGET_REASON" in findings_sync.__all__


def test_sync_budget_notices_still_delivered(tmp_path, fresh_notices):
    hashes = _seed_severities(tmp_path, ["low", "low", "low"])
    fake = FakeSlack()
    result = tick(
        _notice_config(sync_budget=1), reports_directory=tmp_path, runner=fake
    )
    assert [e["dedupe_hash"] for e in result["pushed"]] == hashes[:1]
    assert len(result["budget_held"]) == 2
    (entry,) = result["expiry_notice"]
    assert entry["delivered"] is True
    assert len(fake.calls) == 2  # one finding + one notice


def test_cli_dispatch_dry_run_prints_budget_held_rows(tmp_path, monkeypatch, capsys):
    hashes = _seed_severities(tmp_path, ["low"] * 3)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _dispatch_config(tmp_path, sync_budget=1),
    )
    fake = FakeGh()
    monkeypatch.setattr(findings_sync, "_default_runner", fake)
    assert main(["--reports-dir", str(tmp_path)]) == 0
    assert fake.calls == []
    out = capsys.readouterr().out
    assert out.count("would push") == 1
    assert f"would push {hashes[0]}" in out
    for digest in hashes[1:]:
        assert f"skip (sync-budget) {digest}" in out


# Grant-conflict surface (R5) ------------------------------------------------

_CONFLICTS = [
    {
        "kind": "tool-scope-contradiction",
        "grants": ["op-a|2030", "op-b|2030"],
        "detail": "fixed list from the stub detector",
    }
]


def _two_grants_node(**node):
    node["roe_artifacts"] = [
        _expiring_grant("r5-a", "valid"),
        _expiring_grant("r5-b", "valid"),
    ]
    return node


def _stub_conflicts(monkeypatch, found):
    from omega_prime.grokbot import roe_lifecycle

    seen: list[list] = []

    def fake_conflicts(artifacts, *, now=None):
        seen.append(list(artifacts))
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(roe_lifecycle, "grant_conflicts", fake_conflicts, raising=False)
    return seen


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_tick_grant_conflicts_attached_on_every_tracker(
    tmp_path, monkeypatch, fresh_notices, name
):
    seen = _stub_conflicts(monkeypatch, _CONFLICTS)
    _seed_severities(tmp_path, ["low"])
    result, _fake = _budget_tick(name, tmp_path, **_two_grants_node())
    assert result["grant_conflicts"] == _CONFLICTS
    assert len(result["pushed"]) == 1  # advisory: dispatch is unchanged
    assert len(seen) == 1 and len(seen[0]) == 2


def test_tick_grant_conflicts_also_on_misconfig_result(
    tmp_path, monkeypatch, fresh_notices
):
    _stub_conflicts(monkeypatch, _CONFLICTS)
    node = _two_grants_node(slack_webhook_url="")
    result, fake = _budget_tick("slack", tmp_path, **node)
    assert result["error"] == NO_SLACK_WEBHOOK_MESSAGE
    assert result["grant_conflicts"] == _CONFLICTS
    assert fake.calls == []


@pytest.mark.parametrize("found", [[], None, "x", {"kind": "k"}])
def test_tick_grant_conflicts_key_absent_without_conflicts(
    tmp_path, monkeypatch, fresh_notices, found
):
    _stub_conflicts(monkeypatch, found)
    _seed_severities(tmp_path, ["low"])
    result, _fake = _budget_tick("github", tmp_path, **_two_grants_node())
    assert set(result) == _BASE_KEYS


def test_tick_grant_conflicts_absent_for_fewer_than_two_grants(
    tmp_path, monkeypatch, fresh_notices
):
    seen = _stub_conflicts(monkeypatch, _CONFLICTS)
    _seed_severities(tmp_path, ["low"])
    result, _fake = _budget_tick(
        "github", tmp_path, roe_artifact=_expiring_grant("r5-solo", "valid")
    )
    assert set(result) == _BASE_KEYS
    assert seen == []
    plain, _fake = _budget_tick("github", tmp_path / "none")
    assert "grant_conflicts" not in plain


def test_tick_grant_conflicts_detector_failure_changes_nothing(
    tmp_path, monkeypatch, fresh_notices
):
    _stub_conflicts(monkeypatch, RuntimeError("boom"))
    _seed_severities(tmp_path, ["low", "low"])
    result, _fake = _budget_tick("github", tmp_path, **_two_grants_node())
    assert set(result) == _BASE_KEYS
    assert len(result["pushed"]) == 2


def test_tick_grant_conflicts_coexist_with_notice_and_budget(
    tmp_path, monkeypatch, fresh_notices
):
    _stub_conflicts(monkeypatch, _CONFLICTS)
    _seed_severities(tmp_path, ["low", "low"])
    node = _two_grants_node(tracker="slack", slack_webhook_url=WEBHOOK, sync_budget=1)
    node["roe_artifacts"][0] = _expiring_grant("r5-a", "expiring-7d")
    fake = FakeSlack()
    result = tick(
        {"redteam": {"findings_sync": {"enabled": True, **node}}},
        reports_directory=tmp_path,
        runner=fake,
    )
    assert result["grant_conflicts"] == _CONFLICTS
    assert len(result["expiry_notice"]) == 1
    assert len(result["budget_held"]) == 1


# Fix wave FX-3: budget severity priority, invalid-config signal, skip ---------


def _pushed_hashes(result):
    return [entry["dedupe_hash"] for entry in result["pushed"]]


def _held_hashes(result):
    return [entry["dedupe_hash"] for entry in result["budget_held"]]


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_spends_by_severity_rank_but_pushes_in_store_order(tmp_path, name):
    hashes = _seed_severities(
        tmp_path, ["weird", "low", "Medium ", "LOW", "medium", "critical"]
    )
    result, fake = _budget_tick(name, tmp_path, sync_budget=2)
    # Both mediums take the budget (ahead of the older low/unknown rows),
    # the critical row bypasses it, and pushes follow store order.
    assert _pushed_hashes(result) == [hashes[2], hashes[4], hashes[5]]
    assert _held_hashes(result) == [hashes[0], hashes[1], hashes[3]]
    assert _synced(tmp_path) == [False, False, True, False, True, True]
    assert len(fake.calls) >= 3


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_low_outranks_unknown_severity(tmp_path, name):
    hashes = _seed_severities(tmp_path, ["weird", "unknown", "low", "low"])
    result, _fake = _budget_tick(name, tmp_path, sync_budget=2)
    assert _pushed_hashes(result) == hashes[2:]
    assert _held_hashes(result) == hashes[:2]


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_medium_behind_low_backlog_goes_first(tmp_path, name):
    hashes = _seed_severities(tmp_path, ["low"] * 10 + ["medium"])
    result, _fake = _budget_tick(name, tmp_path, sync_budget=2)
    assert _pushed_hashes(result) == [hashes[0], hashes[10]]
    assert len(result["budget_held"]) == 9


def test_sync_budget_severity_priority_is_deterministic_across_ticks(tmp_path):
    hashes = _seed_severities(tmp_path, ["low", "low", "low", "medium", "low"])
    cfg = _enabled_config(sync_budget=2)
    first = tick(cfg, reports_directory=tmp_path, runner=FakeGh())
    assert _pushed_hashes(first) == [hashes[0], hashes[3]]
    second = tick(cfg, reports_directory=tmp_path, runner=FakeGh())
    assert _pushed_hashes(second) == [hashes[1], hashes[2]]
    third = tick(cfg, reports_directory=tmp_path, runner=FakeGh())
    assert _pushed_hashes(third) == [hashes[4]]
    assert third["budget_held"] == []


@pytest.mark.parametrize("name", _ALL_TRACKERS)
def test_sync_budget_dry_and_plan_choose_the_same_rows_as_a_real_run(tmp_path, name):
    severities = ["low", "weird", "medium", "low", "critical", "medium"]
    real_dir, dry_dir, plan_dir = (tmp_path / part for part in ("r", "d", "p"))
    real_hashes = _seed_severities(real_dir, severities)
    _seed_severities(dry_dir, severities)
    _seed_severities(plan_dir, severities)
    real, _fake = _budget_tick(name, real_dir, sync_budget=2)
    dry, dry_fake = _budget_tick(
        name, dry_dir, sync_budget=2, tick_kwargs={"dry_run": True}
    )
    plan, plan_fake = _budget_tick(name, plan_dir, sync_budget=2, dispatch_dry_run=True)
    expected = [real_hashes[index] for index in (2, 4, 5)]
    assert _pushed_hashes(real) == expected
    assert _pushed_hashes(dry) == expected
    assert [e["dedupe_hash"] for e in plan["dispatch_plan"]] == expected
    for held in (real, dry, plan):
        assert _held_hashes(held) == [real_hashes[index] for index in (0, 1, 3)]
    assert plan_fake.calls == []
    assert dry_fake.calls == []


@pytest.mark.parametrize("name", _ALL_TRACKERS)
@pytest.mark.parametrize("bad", [True, "3", 0, -2, 2.5, [1], {}, {"x": 0}])
def test_sync_budget_unset_and_invalid_are_identical_to_each_other(tmp_path, name, bad):
    severities = ["low", "medium", "weird", "high", "low"]
    plain_dir, bad_dir = tmp_path / "plain", tmp_path / "bad"
    _seed_severities(plain_dir, severities)
    _seed_severities(bad_dir, severities)
    plain, plain_fake = _budget_tick(name, plain_dir)
    other, other_fake = _budget_tick(name, bad_dir, sync_budget=bad)
    other.pop("sync_budget_invalid", None)
    assert set(plain) == _BASE_KEYS
    assert other == plain
    # Payload bodies carry wall-clock stamps, so compare the call count.
    assert len(other_fake.calls) == len(plain_fake.calls)
    assert _synced(bad_dir) == _synced(plain_dir) == [True] * 5


def test_sync_budget_invalid_flag_only_when_present_and_unusable(tmp_path):
    for good in (None, 3, {"default": 2}, {"slack": 1}, {}):
        out = tmp_path / f"good-{good!r}".replace("/", "_").replace(" ", "")
        _seed_severities(out, ["low"])
        result, _fake = _budget_tick("github", out, sync_budget=good)
        assert "sync_budget_invalid" not in result
    absent_dir = tmp_path / "absent"
    _seed_severities(absent_dir, ["low"])
    absent, _fake = _budget_tick("github", absent_dir)
    assert "sync_budget_invalid" not in absent


def test_sync_budget_mapping_valid_entry_survives_invalid_sibling(tmp_path):
    hashes = _seed_severities(tmp_path, ["low"] * 3)
    result, _fake = _budget_tick(
        "slack", tmp_path, sync_budget={"slack": 1, "teams": "x"}
    )
    assert result["sync_budget"] == 1
    assert result["sync_budget_invalid"] == "entry"
    assert _pushed_hashes(result) == hashes[:1]
    assert _held_hashes(result) == hashes[1:]


def test_sync_budget_mapping_valid_default_survives_invalid_tracker_entry(tmp_path):
    hashes = _seed_severities(tmp_path, ["low"] * 3)
    result, _fake = _budget_tick(
        "github", tmp_path, sync_budget={"default": 2, "github": 1.5}
    )
    assert result["sync_budget"] == 2
    assert result["sync_budget_invalid"] == "entry"
    assert _pushed_hashes(result) == hashes[:2]


def test_sync_budget_mapping_non_string_key_is_a_bad_entry(tmp_path):
    _seed_severities(tmp_path, ["low"] * 2)
    result, _fake = _budget_tick("github", tmp_path, sync_budget={1: 3})
    assert set(result) == _BASE_KEYS | {"sync_budget_invalid"}
    assert result["sync_budget_invalid"] == "entry"
    assert len(result["pushed"]) == 2


def test_sync_budget_mapping_keyed_by_configured_tracker_is_ignored_under_graph(
    tmp_path,
):
    # Complete graph credentials win as the effective tracker, so a
    # ``github`` entry is simply not looked up: off, and not "invalid".
    _seed_severities(tmp_path, ["low"] * 3)
    cfg = _graph_config(sync_budget={"github": 1}, tracker="github")
    result = tick(cfg, reports_directory=tmp_path, runner=FakeGraph())
    assert set(result) == _BASE_KEYS
    assert len(result["pushed"]) == 3


def test_sync_budget_invalid_flag_rides_on_error_results_but_not_disabled(
    tmp_path,
):
    erroring = _budget_tick("slack", tmp_path, sync_budget=True, slack_webhook_url="")
    result, fake = erroring
    assert result["error"] == NO_SLACK_WEBHOOK_MESSAGE
    assert result["sync_budget_invalid"] == "type"
    assert fake.calls == []
    off, _fake = _budget_tick("github", tmp_path, sync_budget=True, enabled=False)
    assert off["disabled"] is True
    assert "sync_budget_invalid" not in off


def test_sync_budget_invalid_flag_on_dry_and_plan_results(tmp_path):
    _seed_severities(tmp_path, ["low"] * 2)
    dry, _fake = _budget_tick(
        "github", tmp_path, sync_budget=0, tick_kwargs={"dry_run": True}
    )
    assert dry["sync_budget_invalid"] == "value"
    assert len(dry["pushed"]) == 2
    plan, _fake = _budget_tick("github", tmp_path, sync_budget=0, dispatch_dry_run=True)
    assert plan["sync_budget_invalid"] == "value"
    assert len(plan["dispatch_plan"]) == 2


def test_sync_budget_reason_constants_exported():
    for name in (
        "SYNC_BUDGET_INVALID_ENTRY",
        "SYNC_BUDGET_INVALID_TYPE",
        "SYNC_BUDGET_INVALID_VALUE",
        "SYNC_BUDGET_SEVERITY_RANK",
    ):
        assert name in findings_sync.__all__
    rank = findings_sync.SYNC_BUDGET_SEVERITY_RANK
    assert rank["medium"] > rank["low"] > 0


def test_tick_grant_skip_result_carries_conflicts(tmp_path, monkeypatch, fresh_notices):
    seen = _stub_conflicts(monkeypatch, _CONFLICTS)
    _seed_severities(tmp_path, ["low"])
    cfg = _grant_config("github", ["jira"])
    cfg["redteam"]["findings_sync"]["roe_artifacts"] = [
        {"covers": ["jira"]},
        {"covers": ["jira"]},
    ]
    del cfg["redteam"]["findings_sync"]["roe_artifact"]
    fake = FakeGh()
    result = tick(cfg, reports_directory=tmp_path, runner=fake)
    assert result["skipped_tracker"] == "github"
    assert result["grant_conflicts"] == _CONFLICTS
    assert fake.calls == []
    assert len(seen) == 1 and len(seen[0]) == 2


def test_tick_grant_skip_result_unchanged_without_conflicts(
    tmp_path, monkeypatch, fresh_notices
):
    _stub_conflicts(monkeypatch, [])
    _seed_severities(tmp_path, ["low"])
    result = tick(
        _grant_config("github", ["jira"]),
        reports_directory=tmp_path,
        runner=FakeGh(),
    )
    assert set(result) == {
        "pushed",
        "skipped",
        "dry_run",
        "disabled",
        "skipped_tracker",
        "reason",
    }


_HOSTILE_HASH = "ab\x1b[31m\nFORGED: would push evil\u202e"
_ESCAPED_HASH = "ab\\x1b[31m\\nFORGED: would push evil\\u202e"


def _poison_hash(tmp_path, index):
    path = findings_path(tmp_path)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    rows[index]["dedupe_hash"] = _HOSTILE_HASH
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _assert_no_forged_lines(out):
    assert "\x1b" not in out
    assert "\u202e" not in out
    for line in out.splitlines():
        assert line.startswith(("would push ", "skip ("))


def test_cli_dispatch_dry_run_sanitizes_budget_held_hash(tmp_path, monkeypatch, capsys):
    hashes = _seed_severities(tmp_path, ["low"] * 3)
    _poison_hash(tmp_path, 2)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _dispatch_config(tmp_path, sync_budget=1),
    )
    monkeypatch.setattr(findings_sync, "_default_runner", FakeGh())
    assert main(["--reports-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    _assert_no_forged_lines(out)
    assert f"skip (sync-budget) {hashes[1]}" in out
    assert f"skip (sync-budget) {_ESCAPED_HASH}" in out


def test_cli_dispatch_dry_run_sanitizes_quiet_skipped_hash(
    tmp_path, monkeypatch, capsys
):
    _seed_severities(tmp_path, ["low"] * 2)
    _poison_hash(tmp_path, 1)
    monkeypatch.setattr(findings_sync, "in_quiet_hours", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _dispatch_config(
            tmp_path, quiet_hours={"start": "22:00", "end": "06:00"}
        ),
    )
    monkeypatch.setattr(findings_sync, "_default_runner", FakeGh())
    assert main(["--reports-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    _assert_no_forged_lines(out)
    assert f"skip (quiet-hours) {_ESCAPED_HASH}" in out


def test_cli_dispatch_dry_run_sanitizes_plan_and_synced_hashes(
    tmp_path, monkeypatch, capsys
):
    _seed_severities(tmp_path, ["low", "low"])
    path = findings_path(tmp_path)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    rows[1]["issue_url"] = "gh-issue-marker-1"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    _poison_hash(tmp_path, 0)
    monkeypatch.setattr(
        "omega_prime.config.load_config",
        lambda _root=None: _dispatch_config(tmp_path),
    )
    monkeypatch.setattr(findings_sync, "_default_runner", FakeGh())
    assert main(["--reports-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert f"would push {_ESCAPED_HASH}" in out

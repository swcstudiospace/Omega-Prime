"""Hermetic tests for findings → tracker sync (PLAN-F3). Zero network."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import TypedDict

import pytest

from omega_prime.config import load_config
from omega_prime.grokbot import findings_sync
from omega_prime.grokbot.findings_sync import (
    GRAPH_SCOPE,
    NO_GRAPH_CONFIG_MESSAGE,
    NO_LINEAR_TEAM_MESSAGE,
    NO_SLACK_WEBHOOK_MESSAGE,
    NO_TEAMS_WEBHOOK_MESSAGE,
    GhApiError,
    GhNotFoundError,
    graph_message,
    issue_body,
    issue_title,
    labels_for_severity,
    main,
    priority_for_severity,
    slack_color,
    slack_message,
    sync_findings,
    sync_findings_graph,
    sync_findings_linear,
    sync_findings_slack,
    sync_findings_teams,
    teams_message,
    teams_theme_color,
    tick,
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


def test_slack_message_hash_in_text_and_color(tmp_path):
    row = record_finding(directory=tmp_path, **_finding(severity="critical"))
    message = slack_message(row)
    assert row["dedupe_hash"] in message["text"]
    assert message["attachments"][0]["color"] == "danger"
    assert row["dedupe_hash"] in message["attachments"][0]["text"]


def test_slack_happy_two_findings_two_posts(tmp_path):
    _seed_two(tmp_path)
    fake = FakeSlack()
    result = sync_findings_slack(tmp_path, WEBHOOK, runner=fake)
    assert len(fake.calls) == 2
    assert all(call[0][:2] == ["gh", "api"] for call in fake.calls)
    assert all(WEBHOOK in call[0] for call in fake.calls)
    assert all("dedupe_hash: " in call[1]["text"] for call in fake.calls)
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
    for argv, data in fake.calls:
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

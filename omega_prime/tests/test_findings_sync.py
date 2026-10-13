"""Hermetic tests for findings → tracker sync (PLAN-F3). Zero network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from omega_prime.config import load_config
from omega_prime.grokbot import findings_sync
from omega_prime.grokbot.findings_sync import (
    GhApiError,
    GhNotFoundError,
    issue_body,
    issue_title,
    labels_for_severity,
    main,
    sync_findings,
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


def test_defaults_off_empty_repo(tmp_path):
    node = load_config(tmp_path)["redteam"]["findings_sync"]
    assert node == {"enabled": False, "repo": "", "labels": {}}

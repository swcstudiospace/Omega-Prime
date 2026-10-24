"""Settings: explicit auth selection, *_FILE secrets, redacted reprs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omega_prime.ci_runners.settings import (
    DEFAULT_LABELS,
    AgentSettings,
    AuthMode,
    ControllerSettings,
    SecretFile,
    SecretValue,
    SettingsError,
    parse_labels,
)
from omega_prime.tests.ci_runners._secrets import placeholder

TOKEN = placeholder("agent-token")


def base_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "OMEGA_CI_ORG": "acme",
        "OMEGA_CI_RUNNER_GROUP_ID": "7",
        "OMEGA_CI_GITHUB_APP_ID": "123",
        "OMEGA_CI_GITHUB_INSTALLATION_ID": "456",
        "OMEGA_CI_GITHUB_APP_KEY_FILE": str(tmp_path / "app.pem"),
        "OMEGA_CI_WEBHOOK_SECRET_FILE": str(tmp_path / "hook"),
        "OMEGA_CI_DESKTOP_URL": "https://desktop.example.test/",
        "OMEGA_CI_DESKTOP_TOKEN_FILE": str(tmp_path / "desk"),
    }
    env.update(extra)
    return env


def test_controller_app_defaults(tmp_path: Path) -> None:
    s = ControllerSettings.from_env(base_env(tmp_path))
    assert s.scope == "org" and s.org == "acme" and s.repo is None
    assert s.auth_mode is AuthMode.APP and s.app is not None and s.pat_file is None
    assert s.app.app_id == 123 and s.app.installation_id == 456
    assert s.labels == DEFAULT_LABELS and s.max_runners == 8
    assert s.idle_grace_s == 180.0 and s.runner_group_id == 7
    assert s.desktop_url == "https://desktop.example.test"
    assert s.publisher == "jsonl" and s.events_path.name == "events.jsonl"
    assert s.demand_path.name == "demand.json"


def test_org_scope_requires_numeric_group_id(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    del env["OMEGA_CI_RUNNER_GROUP_ID"]
    with pytest.raises(SettingsError, match="RUNNER_GROUP_ID"):
        ControllerSettings.from_env(env)


def test_repo_scope_pins_allowlist(tmp_path: Path) -> None:
    env = base_env(tmp_path, OMEGA_CI_SCOPE="repo", OMEGA_CI_REPO="Acme/Widgets")
    del env["OMEGA_CI_ORG"], env["OMEGA_CI_RUNNER_GROUP_ID"]
    s = ControllerSettings.from_env(env)
    assert s.scope == "repo" and s.org == "Acme" and s.runner_group_id == 1
    assert s.repos == frozenset({"acme/widgets"})


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"OMEGA_CI_SCOPE": "galaxy"}, "SCOPE"),
        ({"OMEGA_CI_REPO": "acme/x"}, "only valid"),
        ({"OMEGA_CI_SCOPE": "repo"}, "OMEGA_CI_REPO"),
        ({"OMEGA_CI_ORG": "-bad"}, "OMEGA_CI_ORG"),
        ({"OMEGA_CI_REPOS": "nope"}, "OMEGA_CI_REPOS"),
        ({"OMEGA_CI_MAX_RUNNERS": "11"}, "1..10"),
        ({"OMEGA_CI_MAX_RUNNERS": "x"}, "not an integer"),
        ({"OMEGA_CI_IDLE_GRACE_S": "nan-ish"}, "not a number"),
        ({"OMEGA_CI_IDLE_GRACE_S": "99999"}, "0..3600"),
        ({"OMEGA_CI_PUBLISHER": "kafka"}, "PUBLISHER"),
        ({"OMEGA_CI_DESKTOP_URL": "http://desktop.example.test"}, "https"),
        ({"OMEGA_CI_DESKTOP_URL": "https://u:p@desktop.example.test"}, "credentials"),
        ({"OMEGA_CI_DESKTOP_URL": "https://d.example.test/?t=1"}, "query"),
        ({"OMEGA_CI_DESKTOP_URL": " "}, "DESKTOP_URL"),
        ({"OMEGA_CI_LABELS": "linux,x64"}, "self-hosted"),
        ({"OMEGA_CI_LABELS": "self-hosted,bad label"}, "LABELS"),
        ({"OMEGA_CI_WEBHOOK_SECRET_FILE": ""}, "WEBHOOK_SECRET_FILE"),
    ],
)
def test_controller_rejects(tmp_path: Path, extra: dict[str, str], match: str) -> None:
    with pytest.raises(SettingsError, match=match):
        ControllerSettings.from_env(base_env(tmp_path, **extra))


def test_partial_app_is_an_error_not_a_fallback(tmp_path: Path) -> None:
    env = base_env(tmp_path, OMEGA_CI_GITHUB_PAT_FILE=str(tmp_path / "pat"))
    del env["OMEGA_CI_GITHUB_INSTALLATION_ID"]
    with pytest.raises(SettingsError, match=r"partial.*INSTALLATION_ID"):
        ControllerSettings.from_env(env)


def test_app_and_pat_together_rejected(tmp_path: Path) -> None:
    env = base_env(tmp_path, OMEGA_CI_GITHUB_PAT_FILE=str(tmp_path / "pat"))
    with pytest.raises(SettingsError, match="not both"):
        ControllerSettings.from_env(env)


def test_pat_only_when_app_absent(tmp_path: Path) -> None:
    env = base_env(tmp_path, OMEGA_CI_GITHUB_PAT_FILE=str(tmp_path / "pat"))
    for key in (
        "OMEGA_CI_GITHUB_APP_ID",
        "OMEGA_CI_GITHUB_INSTALLATION_ID",
        "OMEGA_CI_GITHUB_APP_KEY_FILE",
    ):
        del env[key]
    s = ControllerSettings.from_env(env)
    assert s.auth_mode is AuthMode.PAT and s.app is None


def test_ambient_tokens_are_ignored(tmp_path: Path) -> None:
    env = base_env(
        tmp_path,
        GH_TOKEN=placeholder("ambient-gh"),
        GITHUB_TOKEN=placeholder("ambient-github"),
    )
    for key in (
        "OMEGA_CI_GITHUB_APP_ID",
        "OMEGA_CI_GITHUB_INSTALLATION_ID",
        "OMEGA_CI_GITHUB_APP_KEY_FILE",
    ):
        del env[key]
    with pytest.raises(SettingsError, match="GitHub auth"):
        ControllerSettings.from_env(env)


def test_secret_file_reads_and_never_reprs_value(tmp_path: Path) -> None:
    path = tmp_path / "s"
    path.write_text("  super-secret-value \n")
    path.chmod(0o600)
    ref = SecretFile("X_FILE", path, "https://api.github.com")
    assert ref.read() == "super-secret-value"
    assert "super-secret" not in repr(ref)
    s = ControllerSettings.from_env(
        base_env(tmp_path, OMEGA_CI_WEBHOOK_SECRET_FILE=str(path))
    )
    assert "super-secret" not in repr(s)


def test_secret_file_errors_name_variable_only(tmp_path: Path) -> None:
    aud = "https://api.github.com"
    with pytest.raises(SettingsError, match="X_FILE: secret file unreadable"):
        SecretFile("X_FILE", tmp_path / "missing", aud).read()
    (tmp_path / "empty").write_text("\n")
    (tmp_path / "empty").chmod(0o600)
    with pytest.raises(SettingsError, match="empty"):
        SecretFile("X_FILE", tmp_path / "empty", aud).read()
    shared = tmp_path / "shared"
    shared.write_text(TOKEN)
    shared.chmod(0o640)  # group-readable: the broker's file rule refuses it
    with pytest.raises(SettingsError, match=r"X_FILE: .*mode 0600") as info:
        SecretFile("X_FILE", shared, aud).read()
    assert TOKEN not in str(info.value) and str(shared) not in str(info.value)


def test_secret_reads_go_through_the_credential_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each read is one broker lookup for the secret's own audience host, with
    a filtered environment that holds only that secret's *_FILE path."""
    from omega_prime.credentials import broker as broker_mod

    seen: list[tuple[str, dict[str, str], str, tuple[str, ...]]] = []
    real = broker_mod.CredentialBroker.key_for

    def spy(self: Any, provider: Any, url: str) -> str:
        seen.append((url, dict(self._env), provider.name, tuple(provider.env_vars)))
        assert self._policy.allows_host("api.github.com")
        assert not self._policy.allows_host("evil.example.test")
        return real(self, provider, url)

    monkeypatch.setattr(broker_mod.CredentialBroker, "key_for", spy)
    path = tmp_path / "pat"
    path.write_text(TOKEN)
    path.chmod(0o600)
    ref = SecretFile("OMEGA_CI_GITHUB_PAT_FILE", path, "https://api.github.com")
    assert ref.read() == TOKEN
    assert seen == [
        (
            "https://api.github.com",
            {"OMEGA_CI_GITHUB_PAT_FILE": str(path)},
            "OMEGA_CI_GITHUB_PAT_FILE",
            ("OMEGA_CI_GITHUB_PAT",),
        )
    ]
    value = SecretValue("OMEGA_CI_AGENT_TOKEN", TOKEN, "https://api.github.com")
    assert value.read() == TOKEN
    assert seen[-1][1] == {"OMEGA_CI_AGENT_TOKEN": TOKEN}


def test_secret_without_an_audience_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "s"
    path.write_text(TOKEN)
    path.chmod(0o600)
    with pytest.raises(SettingsError, match="X_FILE"):
        SecretFile("X_FILE", path, "").read()
    with pytest.raises(SettingsError, match="OMEGA_CI_AGENT_TOKEN"):
        SecretValue("OMEGA_CI_AGENT_TOKEN", TOKEN, "").read()


def test_settings_bind_each_secret_to_its_audience(tmp_path: Path) -> None:
    s = ControllerSettings.from_env(base_env(tmp_path))
    assert s.webhook_secret_file.audience == "https://github.com"
    assert s.desktop_token_file.audience == s.desktop_url
    assert s.app is not None and s.app.key_file.audience == "https://api.github.com"
    agent = AgentSettings.from_env(agent_env())
    assert agent.token.audience == agent.controller_url


def test_secret_value_repr_redacted() -> None:
    value = SecretValue("OMEGA_CI_AGENT_TOKEN", TOKEN, "https://ctl.example.test")
    assert value.read() == TOKEN and TOKEN not in repr(value)


def agent_env(**extra: str) -> dict[str, str]:
    env = {
        "OMEGA_CI_AGENT_TOKEN": TOKEN,
        "OMEGA_CI_CONTROLLER_URL": "https://ci.example.test",
    }
    env.update(extra)
    return env


def test_agent_defaults_and_redaction() -> None:
    s = AgentSettings.from_env(agent_env())
    assert s.max_runners == 8 and s.job_uid is None
    assert str(s.runner_root) == "/opt/actions-runner"
    assert TOKEN not in repr(s)


def test_agent_token_file_and_job_ids(tmp_path: Path) -> None:
    env = agent_env(
        OMEGA_CI_AGENT_TOKEN_FILE=str(tmp_path / "t"),
        OMEGA_CI_AGENT_JOB_UID="1001",
        OMEGA_CI_AGENT_JOB_GID="1001",
        OMEGA_CI_CONTROLLER_URL="http://127.0.0.1:8788",
    )
    del env["OMEGA_CI_AGENT_TOKEN"]
    s = AgentSettings.from_env(env)
    assert isinstance(s.token, SecretFile) and s.job_uid == 1001


@pytest.mark.parametrize(
    ("extra", "drop", "match"),
    [
        ({"OMEGA_CI_AGENT_TOKEN_FILE": "/t"}, None, "exclusive"),
        ({"OMEGA_CI_AGENT_TOKEN": "short"}, None, "32"),
        ({}, "OMEGA_CI_AGENT_TOKEN", "required"),
        ({}, "OMEGA_CI_CONTROLLER_URL", "CONTROLLER_URL"),
        ({"OMEGA_CI_CONTROLLER_URL": "http://ci.example.test"}, None, "loopback"),
        ({"OMEGA_CI_AGENT_JOB_UID": "1001"}, None, "together"),
        ({"OMEGA_CI_AGENT_MAX_RUNNERS": "0"}, None, "1..10"),
    ],
)
def test_agent_rejects(extra: dict[str, str], drop: str | None, match: str) -> None:
    env = agent_env(**extra)
    if drop:
        del env[drop]
    with pytest.raises(SettingsError, match=match):
        AgentSettings.from_env(env)


def test_parse_labels_dedupes_and_lowercases() -> None:
    assert parse_labels("Self-Hosted, linux,linux") == ("self-hosted", "linux")
    assert parse_labels(None) == DEFAULT_LABELS


# -- multi-agent serverless pool (00064) ---------------------------------------


def swarm_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = base_env(
        tmp_path,
        OMEGA_CI_DESKTOP_URL="https://runner.example.test",
        OMEGA_CI_DESKTOP_2_URL="https://runner2.example.test",
        OMEGA_CI_DESKTOP_2_TOKEN_FILE=str(tmp_path / "desk2"),
        OMEGA_CI_DESKTOP_3_URL="https://runner3.example.test/",
        OMEGA_CI_DESKTOP_3_TOKEN_FILE=str(tmp_path / "desk3"),
    )
    env.update(extra)
    return {k: v for k, v in env.items() if v}


def test_single_agent_is_the_legacy_block(tmp_path: Path) -> None:
    s = ControllerSettings.from_env(base_env(tmp_path, OMEGA_CI_MAX_RUNNERS="6"))
    [agent] = s.agents
    assert (agent.index, agent.label, agent.url) == (1, "agent1", s.desktop_url)
    assert agent.capacity == 6 and s.max_runners == 6
    assert agent.token_file is s.desktop_token_file


def test_three_agents_default_to_eight_each_and_a_pool_of_24(tmp_path: Path) -> None:
    s = ControllerSettings.from_env(swarm_env(tmp_path))
    assert [a.label for a in s.agents] == ["agent1", "agent2", "agent3"]
    assert [a.capacity for a in s.agents] == [8, 8, 8] and s.max_runners == 24
    assert s.agents[2].url == "https://runner3.example.test"
    # Each token is bound to its own agent, so it can only go there.
    assert [a.token_file.audience for a in s.agents] == [a.url for a in s.agents]
    assert s.desktop_url == "https://runner.example.test"


def test_pool_cap_may_be_lowered_but_not_raised_past_the_sum(tmp_path: Path) -> None:
    s = ControllerSettings.from_env(
        swarm_env(tmp_path, OMEGA_CI_MAX_RUNNERS="20", OMEGA_CI_DESKTOP_3_CAPACITY="4")
    )
    assert s.max_runners == 20 and [a.capacity for a in s.agents] == [8, 8, 4]
    with pytest.raises(SettingsError, match="sum of agent capacities"):
        ControllerSettings.from_env(swarm_env(tmp_path, OMEGA_CI_MAX_RUNNERS="25"))


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"OMEGA_CI_DESKTOP_3_URL": "https://RUNNER2.example.test"}, "duplicates"),
        ({"OMEGA_CI_DESKTOP_2_URL": "http://runner2.example.test"}, "https"),
        ({"OMEGA_CI_DESKTOP_2_CAPACITY": "11"}, "1..10"),
        ({"OMEGA_CI_DESKTOP_CAPACITY": "0"}, "1..10"),
        ({"OMEGA_CI_DESKTOP_2_TOKEN_FILE": ""}, "OMEGA_CI_DESKTOP_2_TOKEN_FILE"),
        ({"OMEGA_CI_DESKTOP_5_URL": "https://runner5.example.test"}, "without gaps"),
        ({"OMEGA_CI_DESKTOP_4_TOKEN_FILE": "/x"}, "without OMEGA_CI_DESKTOP_4_URL"),
    ],
)
def test_bad_agent_blocks_are_refused(
    tmp_path: Path, change: dict[str, str], match: str
) -> None:
    with pytest.raises(SettingsError, match=match):
        ControllerSettings.from_env(swarm_env(tmp_path, **change))


def test_agent_side_cap_stays_ten_per_container() -> None:
    assert (
        AgentSettings.from_env(agent_env(OMEGA_CI_AGENT_MAX_RUNNERS="8")).max_runners
        == 8
    )
    with pytest.raises(SettingsError, match=r"1\.\.10"):
        AgentSettings.from_env(agent_env(OMEGA_CI_AGENT_MAX_RUNNERS="11"))

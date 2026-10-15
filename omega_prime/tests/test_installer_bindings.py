# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Per-install Linear, Notion, and optional gateway setup."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from omega_prime.commands import execute_command
from omega_prime.grokbot.findings_sync import sync_findings_linear
from omega_prime.integrations.binding import fresh_binding, load_binding, save_binding
from omega_prime.integrations.contract import (
    CONTRACT_VERSION,
    REQUIRED_PROPERTIES,
    REQUIRED_STATUS,
    schema_gaps,
)
from omega_prime.integrations.gateways import gateway_status
from omega_prime.integrations.linear import LINEAR_API_URL, LinearClient, LinearError
from omega_prime.integrations.notion import NotionError
from omega_prime.integrations.setup import handle
from omega_prime.integrations.submodules import (
    EMPTY_PRIME,
    INIT_COMMAND,
    check_submodules,
)
from omega_prime.tests.test_omega_commands import RecordingRegistry
from omega_prime.tools.redteam import RedteamClient, RedteamContext
from sidecar.findings import record_finding

ROOT = Path(__file__).resolve().parents[2]
SENTINEL = "SENTINEL-NOT-A-SECRET-5f3a91"
TEAM_A = "team-aaaa"
TEAM_B = "team-bbbb"
PROJECT_A = "project-aaaa"
DB_ID = "database000000000000000000000001"


def _env(tmp_path: Path) -> dict[str, str]:
    return {"OMEGA_PRIME_STATE_DIR": str(tmp_path)}


def _properties() -> dict[str, Any]:
    options = [{"name": name} for name in REQUIRED_STATUS]
    return {
        "Name": {"type": "title"},
        "Status": {"type": "status", "status": {"options": options}},
        "Due date": {"type": "date"},
        "Project": {"type": "relation"},
    }


class FakeNotion:
    def __init__(self) -> None:
        self.matches: list[dict[str, str]] = []
        self.gaps: dict[str, list[str]] = {}
        self.payload = {"properties": _properties()}
        self.pages: list[dict[str, str]] = []
        self.error: NotionError | None = None

    def me(self) -> dict[str, str]:
        if self.error is not None:
            raise self.error
        return {"id": "user", "name": "user"}

    def matching_databases(self) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
        if self.error is not None:
            raise self.error
        return list(self.matches), dict(self.gaps)

    def database(self, database_id: str) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.payload

    def query_tasks(self, database_id: str) -> list[dict[str, str]]:
        return [row for row in self.pages if row["db"] == database_id]

    def create_task(self, database_id: str, name: str) -> dict[str, str]:
        row = {"id": "page0001", "name": name, "db": database_id}
        self.pages.append(row)
        return {"id": row["id"], "name": name}

    def update_task_status(
        self, database_id: str, page_id: str, status: str
    ) -> dict[str, str]:
        known = {row["id"] for row in self.pages}
        if page_id.replace("-", "") not in known:
            raise NotionError(
                "scope",
                "That page is not in the bound Tasks database.",
            )
        return {"id": page_id.replace("-", "")}


class FakeLinear:
    def __init__(
        self,
        teams: list[dict[str, str]] | None = None,
        projects: dict[str, list[dict[str, str]]] | None = None,
    ) -> None:
        self.teams_rows = teams or [{"id": TEAM_A, "name": "Alpha"}]
        self.project_rows = projects or {}
        self.created: list[dict[str, str]] = []
        self.auth = False

    def viewer(self) -> dict[str, str]:
        if self.auth:
            raise LinearError("auth", "Linear rejected the credential.")
        return {"id": "user", "name": "user"}

    def teams(self) -> list[dict[str, str]]:
        if self.auth:
            raise LinearError("auth", "Linear rejected the credential.")
        return list(self.teams_rows)

    def projects(self, team_id: str) -> list[dict[str, str]]:
        return list(self.project_rows.get(team_id, []))

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        priority: int,
        project_id: str = "",
    ) -> dict[str, str]:
        self.created.append({"team": team_id, "title": title, "project": project_id})
        return {"id": "issue-1", "url": "issue-ref"}

    def issue(self, issue_id: str) -> dict[str, str]:
        return {"id": issue_id, "url": "issue-ref", "team_id": TEAM_A}

    def update_issue(self, issue_id: str, team_id: str, title: str) -> dict[str, str]:
        if team_id != TEAM_A:
            raise LinearError("scope", "That issue is not on the selected team.")
        return {"id": issue_id, "url": "issue-ref"}


def _ready(tmp_path: Path, notion: FakeNotion, linear: FakeLinear) -> None:
    env = _env(tmp_path)
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("skip-gateway", env, notion=notion, linear=linear, probe=lambda _url: "ok")


def test_fresh_prompt_is_the_template(tmp_path: Path) -> None:
    result = handle("", _env(tmp_path), probe=lambda _url: "ok")
    assert result["step"] == "template"
    assert result["overall"] == "not-ready"
    assert result["prompt"].startswith("1. Open docs/notion-task-dashboard.md")
    assert "gallery" in result["prompt"]


def test_resume_stays_on_the_incomplete_step(tmp_path: Path) -> None:
    env = _env(tmp_path)
    first = handle("duplicated", env, probe=lambda _url: "ok")
    assert first["step"] == "notion"
    second = handle("", env, probe=lambda _url: "ok")
    assert second["step"] == "notion"
    assert "NOTION_TOKEN" in second["prompt"]


def test_pasted_token_is_refused_and_not_stored(tmp_path: Path) -> None:
    env = _env(tmp_path)
    result = handle(
        f"connected token={SENTINEL}",
        env,
        probe=lambda _url: "ok",
    )
    assert result["error"] == "secret_refused"
    path = tmp_path / "integrations.json"
    assert not path.exists()


def test_schema_mismatch_names_the_gap(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.gaps = {DB_ID: ["Status option Done"]}
    handle("duplicated", env, notion=notion, probe=lambda _url: "ok")
    result = handle("connected", env, notion=notion, probe=lambda _url: "ok")
    assert result["notion"]["dashboard"] == "schema-mismatch"
    assert result["ok"] is False
    assert "Status option Done" in result["prompt"]


def test_unshared_notion_stays_unready(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    handle("duplicated", env, notion=notion, probe=lambda _url: "ok")
    result = handle("connected", env, notion=notion, probe=lambda _url: "ok")
    assert result["notion"]["state"] == "invalid"
    assert result["notion"]["dashboard"] != "ready"
    assert "Share the duplicated dashboard" in result["prompt"]


def test_bad_linear_token_stays_invalid(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    linear = FakeLinear()
    linear.auth = True
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    result = handle(
        "connected", env, notion=notion, linear=linear, probe=lambda _url: "ok"
    )
    assert result["linear"]["state"] == "invalid"
    assert result["ok"] is False
    assert result["linear"]["team_id"] == ""


def test_one_team_is_selected_and_issue_uses_it(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    linear = FakeLinear(projects={TEAM_A: [{"id": PROJECT_A, "name": "Board"}]})
    _ready(tmp_path, notion, linear)
    result = handle(
        "linear-issue File the receipt",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert result["overall"] == "ready-with-gateway-skipped"
    assert linear.created == [
        {"team": TEAM_A, "title": "File the receipt", "project": PROJECT_A}
    ]
    assert "Created Linear issue" in result["prompt"]


def test_many_teams_require_an_explicit_choice(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    linear = FakeLinear(
        teams=[
            {"id": TEAM_A, "name": "Alpha"},
            {"id": TEAM_B, "name": "Beta"},
        ]
    )
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    listed = handle(
        "connected", env, notion=notion, linear=linear, probe=lambda _url: "ok"
    )
    assert listed["step"] == "linear-team"
    assert listed["linear"]["team_id"] == ""
    chosen = handle(
        f"linear-team {TEAM_B}",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert chosen["linear"]["team_id"] == TEAM_B


def test_notion_task_round_trip_uses_the_bound_database(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    linear = FakeLinear()
    _ready(tmp_path, notion, linear)
    added = handle(
        "task add Write the note",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert DB_ID in notion.pages[0]["db"]
    assert "Created task" in added["prompt"]
    listed = handle(
        "task list", env, notion=notion, linear=linear, probe=lambda _url: "ok"
    )
    assert "Write the note" in listed["prompt"]
    refused = handle(
        "task status other-page Done",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert "bound Tasks database" in refused["prompt"]
    assert load_binding(env)["notion"]["state"] == "ready"


def test_skip_gateway_leaves_notion_and_linear_usable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    notion = FakeNotion()
    linear = FakeLinear()
    _ready(tmp_path, notion, linear)
    status = gateway_status("redteam", _env(tmp_path))
    assert status["ok"] is False
    assert status["error"].startswith("not_configured:")
    client = RedteamClient(
        RedteamContext(
            request=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("network")
            )
        )
    )
    refused = client.status()
    assert "not_configured" in refused["error"]


def test_gateway_timeout_is_degraded_and_auth_is_invalid(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    linear = FakeLinear()
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    timed = handle(
        "gateway substrate http://127.0.0.1:9",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "timeout",
    )
    assert timed["gateways"]["substrate"] == "degraded"
    assert "timed out" in timed["prompt"]
    rejected = handle(
        "gateway redteam http://127.0.0.1:9",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "auth",
    )
    assert rejected["gateways"]["redteam"] == "invalid"
    assert "credential" in rejected["prompt"]


def test_bindings_do_not_cross_state_dirs(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    notion = FakeNotion()
    linear = FakeLinear()
    _ready(left, notion, linear)
    other = handle("", _env(right), probe=lambda _url: "ok")
    assert other["step"] == "template"
    assert load_binding(_env(left))["linear"]["team_id"] == TEAM_A
    assert load_binding(_env(right))["linear"]["team_id"] == ""


def test_empty_submodule_names_the_init_command(tmp_path: Path) -> None:
    ok, detail = check_submodules(tmp_path)
    assert ok is False
    assert detail == EMPTY_PRIME
    assert INIT_COMMAND in detail


def test_contract_names_are_in_the_template_doc() -> None:
    text = (ROOT / "docs" / "notion-task-dashboard.md").read_text(encoding="utf-8")
    assert f"Contract version: {CONTRACT_VERSION}." in text
    for name, _kind in REQUIRED_PROPERTIES:
        assert name in text
    for option in REQUIRED_STATUS:
        assert option in text
    assert schema_gaps({"properties": _properties()}) == []
    assert "Status" in schema_gaps({"properties": {"Name": {"type": "title"}}})


def test_binding_file_is_private(tmp_path: Path) -> None:
    save_binding(fresh_binding(), _env(tmp_path))
    path = tmp_path / "integrations.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o077 == 0


def test_connectors_command_keeps_the_env_report(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env["X_API_TOKEN"] = SENTINEL
    body = execute_command("/omega-connectors", RecordingRegistry(), env)
    assert body["ok"] is True
    assert "x" in [row["id"] for row in body["result"]["configured"]]
    encoded = json.dumps(body)
    assert SENTINEL not in encoded
    assert body["result"]["integrations"]["step"] == "template"


def test_findings_sync_uses_the_linear_client(tmp_path: Path) -> None:
    record_finding(
        directory=tmp_path,
        case_id="abuse-01",
        klass="prompt_injection",
        severity="high",
        observed_detection="no-detection",
        verdict="miss",
        response="do anything now",
    )
    client = FakeLinear()

    def boom(_argv: list[str], _data: dict[str, str]) -> None:
        raise AssertionError("gh was called")

    result = sync_findings_linear(
        tmp_path,
        TEAM_A,
        runner=boom,  # type: ignore[arg-type]
        linear_client=client,
    )
    assert result["pushed"]
    assert client.created[0]["team"] == TEAM_A
    assert "issue-ref" in result["pushed"][0]["issue_url"]


def test_linear_auth_does_not_retry() -> None:
    calls: list[int] = []

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        del method, headers, body
        calls.append(1)
        assert url == LINEAR_API_URL
        assert SENTINEL not in url
        return 401, {}

    client = LinearClient(SENTINEL, transport, sleep=lambda _seconds: calls.append(2))
    with pytest.raises(LinearError) as caught:
        client.viewer()
    assert caught.value.code == "auth"
    assert SENTINEL not in str(caught.value)
    assert calls == [1]


def test_linear_rate_limit_retries_then_succeeds() -> None:
    seen: list[int] = []

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        del method, url, headers, body
        seen.append(1)
        if len(seen) < 3:
            return 429, {}
        return 200, {"data": {"viewer": {"id": "user", "name": "user"}}}

    slept: list[float] = []
    client = LinearClient(SENTINEL, transport, sleep=slept.append)
    assert client.viewer()["id"] == "user"
    assert slept == [0.2, 0.4]
    assert SENTINEL not in json.dumps(slept)

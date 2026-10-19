# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Per-install Linear, Notion, and optional gateway setup."""

from __future__ import annotations

import email.message
import io
import json
import os
import stat
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import pytest

from omega_prime.commands import execute_command
from omega_prime.grokbot.findings_sync import GhApiError, sync_findings_linear
from omega_prime.integrations.binding import fresh_binding, load_binding, save_binding
from omega_prime.integrations.contract import (
    CONTRACT_VERSION,
    REQUIRED_PROPERTIES,
    REQUIRED_STATUS,
    schema_gaps,
)
from omega_prime.integrations.gateways import (
    _CrossOriginRedirect,
    _probe_opener,
    _SameOriginRedirect,
    gateway_status,
    health_url,
    probe_gateway,
    probe_url,
)
from omega_prime.integrations.linear import LINEAR_API_URL, LinearClient, LinearError
from omega_prime.integrations.notion import NOTION_API, NotionClient, NotionError
from omega_prime.integrations.setup import handle
from omega_prime.integrations.submodules import (
    EMPTY_PRIME,
    INIT_COMMAND,
    check_submodules,
)
from omega_prime.integrations.transport import TransportError
from omega_prime.mcp_server import _DeskBusClient
from omega_prime.policy.policy import SeatPolicy
from omega_prime.substrate.client import SubstrateClient
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
    assert "insert content" in text
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


def _policy(hosts: list[str]) -> SeatPolicy:
    return SeatPolicy({"version": 1, "network": {"hosts": hosts}})


def _record(directory: Path) -> None:
    record_finding(
        directory=directory,
        case_id="abuse-01",
        klass="prompt_injection",
        severity="high",
        observed_detection="no-detection",
        verdict="miss",
        response="do anything now",
    )


def test_fresh_prompt_requires_insert_content(tmp_path: Path) -> None:
    result = handle("", _env(tmp_path), probe=lambda _url: "ok")
    assert "insert content" in result["prompt"]


def test_policy_blocks_a_gateway_before_the_probe(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    linear = FakeLinear()
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    called: list[str] = []

    def probe(url: str) -> str:
        called.append(url)
        return "ok"

    blocked = handle(
        "gateway substrate http://127.0.0.1:9",
        env,
        notion=notion,
        linear=linear,
        probe=probe,
        policy=_policy([]),
    )
    assert called == []
    assert "saved gateway was not changed" in blocked["prompt"]
    assert load_binding(env)["gateways"]["substrate"]["state"] == "unconfigured"


def test_policy_blocks_linear_before_the_broker_sends(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env["LINEAR_API_KEY"] = SENTINEL
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    handle("duplicated", env, notion=notion, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, probe=lambda _url: "ok")
    result = handle(
        "connected",
        env,
        notion=notion,
        policy=_policy([]),
        probe=lambda _url: "ok",
    )
    assert "api.linear.app" in result["prompt"]
    assert result["linear"]["state"] != "ready"
    assert SENTINEL not in result["prompt"]


def test_failed_gateway_replacement_keeps_the_ready_url(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    linear = FakeLinear()
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    ready = handle(
        "gateway substrate http://127.0.0.1:9",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert ready["gateways"]["substrate"] == "ready"
    replaced = handle(
        "gateway substrate http://127.0.0.1:8",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "timeout",
    )
    assert "previous URL" in replaced["prompt"]
    saved = load_binding(env)["gateways"]["substrate"]
    assert saved["state"] == "ready"
    assert saved["url"] == "http://127.0.0.1:9"


def test_recheck_of_the_active_gateway_records_the_failure(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    linear = FakeLinear()
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle(
        "gateway substrate http://127.0.0.1:9",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    timed = handle(
        "gateway substrate http://127.0.0.1:9/",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "timeout",
    )
    assert "timed out" in timed["prompt"]
    saved = load_binding(env)["gateways"]["substrate"]
    assert saved["state"] == "degraded"
    assert saved["url"] == "http://127.0.0.1:9/"
    handle(
        "gateway redteam http://127.0.0.1:9",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    rejected = handle(
        "gateway redteam http://127.0.0.1:9",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "auth",
    )
    assert "previous URL" not in rejected["prompt"]
    saved = load_binding(env)["gateways"]["redteam"]
    assert saved["state"] == "invalid"
    assert saved["url"] == "http://127.0.0.1:9"


def test_team_switch_drops_the_previous_projects(tmp_path: Path) -> None:
    env = _env(tmp_path)
    notion = FakeNotion()
    notion.matches = [{"id": DB_ID, "name": "Tasks"}]
    linear = FakeLinear(
        teams=[
            {"id": TEAM_A, "name": "Alpha"},
            {"id": TEAM_B, "name": "Beta"},
        ],
        projects={
            TEAM_A: [
                {"id": PROJECT_A, "name": "Board"},
                {"id": "project-aaaa-2", "name": "Other"},
            ]
        },
    )

    def fail_b(team_id: str) -> list[dict[str, str]]:
        if team_id == TEAM_B:
            raise LinearError("transient", "Linear timed out.")
        return list(linear.project_rows.get(team_id, []))

    linear.projects = fail_b  # type: ignore[method-assign]
    handle("duplicated", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    handle("connected", env, notion=notion, linear=linear, probe=lambda _url: "ok")
    chosen = handle(
        f"linear-team {TEAM_A}",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert chosen["step"] == "linear-project"
    failed = handle(
        f"linear-team {TEAM_B}",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert "timed out" in failed["prompt"]
    stale = handle(
        f"linear-project {PROJECT_A}",
        env,
        notion=notion,
        linear=linear,
        probe=lambda _url: "ok",
    )
    assert "not one of the projects" in stale["prompt"]
    assert load_binding(env)["linear"]["project_id"] == ""


def test_probe_rejects_server_errors_and_sends_the_bearer(monkeypatch) -> None:
    def denied(request: Any, timeout: float = 3.0) -> Any:
        del timeout
        assert request.get_header("Authorization") == f"Bearer {SENTINEL}"
        raise urllib.error.HTTPError(
            "http://127.0.0.1:9/",
            503,
            "unavailable",
            hdrs=email.message.Message(),
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr("omega_prime.integrations.gateways._open_probe", denied)
    assert (
        probe_url(
            "http://127.0.0.1:9/",
            headers={"Authorization": f"Bearer {SENTINEL}"},
        )
        == "degraded"
    )

    class Ok:
        status = 200

        def __enter__(self) -> Ok:
            return self

        def __exit__(self, *args: object) -> Literal[False]:
            return False

    monkeypatch.setattr(
        "omega_prime.integrations.gateways._open_probe",
        lambda request, timeout=3.0: Ok(),
    )
    assert probe_url("http://127.0.0.1:9/") == "ok"
    assert health_url("substrate", "http://127.0.0.1:9") == "http://127.0.0.1:9/healthz"
    assert health_url("substrate", "http://127.0.0.1:9/healthz") == (
        "http://127.0.0.1:9/healthz"
    )
    assert health_url("redteam", "http://127.0.0.1:9/") == "http://127.0.0.1:9/health"
    assert health_url("desk_bus", "http://127.0.0.1:9/health") == (
        "http://127.0.0.1:9/health"
    )

    seen: list[str] = []

    def capture(request: Any, timeout: float = 3.0) -> Any:
        del timeout
        seen.append(request.full_url)
        assert request.get_header("Authorization") == f"Bearer {SENTINEL}"
        return Ok()

    monkeypatch.setattr("omega_prime.integrations.gateways._open_probe", capture)
    assert (
        probe_gateway(
            "redteam",
            "http://127.0.0.1:9",
            {"T3MP3ST_BEARER_TOKEN": SENTINEL},
        )
        == "ok"
    )
    assert seen == ["http://127.0.0.1:9/health"]


def test_substrate_probe_sends_a_file_backed_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "SUBSTRATE_TOKEN"
    secret.write_text("file-token\n", encoding="utf-8")
    os.chmod(secret, 0o600)
    seen: list[str | None] = []

    class Ok:
        status = 200

        def __enter__(self) -> Ok:
            return self

        def __exit__(self, *args: object) -> Literal[False]:
            return False

    def capture(request: Any, timeout: float = 3.0) -> Any:
        del timeout
        seen.append(request.get_header("Authorization"))
        return Ok()

    monkeypatch.setattr("omega_prime.integrations.gateways._open_probe", capture)
    assert (
        probe_gateway(
            "substrate",
            "http://127.0.0.1:9",
            {"SUBSTRATE_TOKEN_FILE": str(secret)},
        )
        == "ok"
    )
    assert seen == ["Bearer file-token"]


def _redirect(current: str, location: str) -> urllib.request.Request | None:
    """Run the opener's redirect handler in memory. No socket is opened."""
    opener = _probe_opener()
    installed = opener.handlers  # type: ignore[attr-defined]
    handler = next(item for item in installed if isinstance(item, _SameOriginRedirect))
    request = urllib.request.Request(
        current,
        headers={"Authorization": f"Bearer {SENTINEL}"},
        method="GET",
    )
    return handler.redirect_request(
        request,
        io.BytesIO(b""),
        302,
        "Found",
        email.message.Message(),
        location,
    )


def test_probe_refuses_a_cross_origin_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "http://127.0.0.1:9/health"
    other = "http://127.0.0.2:9/taken"
    with pytest.raises(_CrossOriginRedirect):
        _redirect(origin, other)

    def open_probe(request: urllib.request.Request, timeout: float = 3.0) -> Any:
        del timeout
        assert request.get_header("Authorization") == f"Bearer {SENTINEL}"
        _redirect(request.full_url, other)
        raise AssertionError("cross-origin redirect was followed")

    monkeypatch.setattr("omega_prime.integrations.gateways._open_probe", open_probe)
    assert (
        probe_url(origin, headers={"Authorization": f"Bearer {SENTINEL}"}) == "refused"
    )


def test_probe_follows_a_same_origin_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "http://127.0.0.1:9/health"
    redirected = _redirect(origin, "http://127.0.0.1:9/healthz")
    assert isinstance(redirected, urllib.request.Request)
    assert redirected.full_url == "http://127.0.0.1:9/healthz"
    assert redirected.get_header("Authorization") == f"Bearer {SENTINEL}"

    class Ok:
        status = 200

        def __enter__(self) -> Ok:
            return self

        def __exit__(self, *args: object) -> Literal[False]:
            return False

    def open_probe(request: urllib.request.Request, timeout: float = 3.0) -> Any:
        del timeout
        followed = _redirect(request.full_url, "http://127.0.0.1:9/healthz")
        assert followed is not None
        assert followed.get_header("Authorization") == f"Bearer {SENTINEL}"
        return Ok()

    monkeypatch.setattr("omega_prime.integrations.gateways._open_probe", open_probe)
    assert probe_url(origin, headers={"Authorization": f"Bearer {SENTINEL}"}) == "ok"


def test_create_issue_does_not_retry_a_timeout() -> None:
    calls: list[int] = []

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        del method, url, headers, body
        calls.append(1)
        raise TransportError("timeout", "timed out")

    client = LinearClient(SENTINEL, transport, sleep=lambda _seconds: calls.append(9))
    with pytest.raises(LinearError) as caught:
        client.create_issue(TEAM_A, "title", "body", 0)
    assert caught.value.code == "transient"
    assert calls == [1]


def test_create_issue_omits_a_null_project() -> None:
    seen: list[Mapping[str, Any]] = []

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        del method, url, headers
        assert body is not None
        seen.append(body)
        return 200, {
            "data": {
                "issueCreate": {"success": True, "issue": {"id": "i1", "url": "u"}}
            }
        }

    client = LinearClient(SENTINEL, transport)
    assert client.create_issue(TEAM_A, "title", "body", 2)["id"] == "i1"
    assert "projectId" not in seen[0]["query"]
    assert "projectId" not in seen[0]["variables"]
    assert client.create_issue(TEAM_A, "title", "body", 2, PROJECT_A)["id"] == "i1"
    assert "projectId: $projectId" in seen[1]["query"]
    assert seen[1]["variables"]["projectId"] == PROJECT_A


def test_linear_teams_follow_the_cursor() -> None:
    seen: list[Any] = []

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        del method, url, headers
        assert body is not None
        after = body["variables"]["after"]
        seen.append(after)
        if after is None:
            return 200, {
                "data": {
                    "teams": {
                        "nodes": [{"id": TEAM_A, "name": "Alpha"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                }
            }
        return 200, {
            "data": {
                "teams": {
                    "nodes": [{"id": TEAM_B, "name": "Beta"}],
                    "pageInfo": {"hasNextPage": False},
                }
            }
        }

    client = LinearClient(SENTINEL, transport, sleep=lambda _seconds: None)
    assert [row["id"] for row in client.teams()] == [TEAM_A, TEAM_B]
    assert seen == [None, "cursor-1"]


def test_notion_search_follows_the_cursor_and_update_checks_parent() -> None:
    calls: list[str] = []

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        del headers
        calls.append(f"{method} {url}")
        if url.endswith("/search"):
            assert body is not None
            if body.get("start_cursor") != "cursor-1":
                return 200, {
                    "results": [
                        {
                            "object": "database",
                            "id": DB_ID,
                            "title": [{"plain_text": "Tasks"}],
                        }
                    ],
                    "has_more": True,
                    "next_cursor": "cursor-1",
                }
            return 200, {
                "results": [
                    {
                        "object": "database",
                        "id": "database000000000000000000000002",
                        "title": [{"plain_text": "Other"}],
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            }
        if method == "GET":
            return 200, {"id": "page0001", "parent": {"database_id": DB_ID}}
        return 200, {"id": "page0001"}

    client = NotionClient(SENTINEL, transport, sleep=lambda _seconds: None)
    found = client.search_databases()
    assert [row["name"] for row in found] == ["Tasks", "Other"]
    updated = client.update_task_status(DB_ID, "page0001", "Done")
    assert updated["id"] == "page0001"
    assert any(item.startswith("GET ") for item in calls)
    assert NOTION_API in calls[0]


def test_bad_linear_file_does_not_fall_back_to_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "linear.key"
    secret.write_text("not-a-token\n", encoding="utf-8")
    monkeypatch.setenv("LINEAR_API_KEY_FILE", str(secret))
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    _record(tmp_path)
    with pytest.raises(GhApiError) as caught:
        sync_findings_linear(tmp_path, TEAM_A)
    assert "LINEAR_API_KEY_FILE" in str(caught.value)
    assert "not-a-token" not in str(caught.value)


def test_saved_project_stays_on_its_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["linear"]["state"] = "ready"
    binding["linear"]["team_id"] = TEAM_A
    binding["linear"]["project_id"] = PROJECT_A
    save_binding(binding, _env(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    _record(other)
    client = FakeLinear()
    sync_findings_linear(
        other, TEAM_B, runner=lambda *_args: None, linear_client=client
    )  # type: ignore[arg-type]
    assert client.created[0]["project"] == ""
    matched = tmp_path / "matched"
    matched.mkdir()
    _record(matched)
    second = FakeLinear()
    sync_findings_linear(
        matched, TEAM_A, runner=lambda *_args: None, linear_client=second
    )  # type: ignore[arg-type]
    assert second.created[0]["project"] == PROJECT_A


def test_saved_redteam_url_is_the_one_the_client_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["redteam"] = {
        "state": "ready",
        "url": "http://127.0.0.1:9",
    }
    save_binding(binding, _env(tmp_path))
    monkeypatch.setattr(
        "omega_prime.policy.policy.SeatPolicy.load",
        lambda _path: _policy(["127.0.0.1"]),
    )
    seen: list[str] = []

    def request(
        base: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        del method, path, payload, headers, timeout
        seen.append(base)
        return 200, {}

    client = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
        )
    )
    body = client.status()
    assert body["tool"] == "tempest_status"
    assert seen[0] == "http://127.0.0.1:9"


def test_redteam_uses_the_runtime_policy_not_the_package_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["redteam"] = {
        "state": "ready",
        "url": "http://127.0.0.1:9",
    }
    save_binding(binding, _env(tmp_path))

    def explode(_path: object) -> SeatPolicy:
        raise AssertionError("package policy must not be loaded")

    monkeypatch.setattr("omega_prime.policy.policy.SeatPolicy.load", explode)
    seen: list[str] = []

    def request(
        base: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        del method, path, payload, headers, timeout
        seen.append(base)
        return 200, {}

    allowed = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
            policy=_policy(["127.0.0.1"]),
        )
    )
    assert allowed.status()["tool"] == "tempest_status"
    denied = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
            policy=_policy([]),
        )
    )
    refused = denied.status()
    assert "does not allow" in refused["error"]
    assert seen == ["http://127.0.0.1:9", "http://127.0.0.1:9"]


def test_mission_stop_stays_on_the_gateway_that_started_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["redteam"] = {
        "state": "ready",
        "url": "http://127.0.0.1:9101",
    }
    save_binding(binding, _env(tmp_path))
    bases: list[tuple[str, str]] = []
    stop_status = {"code": 500}

    def request(
        base: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        del method, payload, headers, timeout
        bases.append((base, path))
        if path == "/api/mission/status":
            return 200, {"mission": "m-1", "active": True}
        if path == "/api/mission/stop":
            return stop_status["code"], {}
        return 200, {"missionId": "m-1"}

    client = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
            policy=_policy(["127.0.0.1"]),
        )
    )
    assert client.mission_start(name="engagement")["ok"] is True
    binding["gateways"]["redteam"]["url"] = "http://127.0.0.1:9102"
    save_binding(binding, _env(tmp_path))
    assert client.mission_stop()["ok"] is False
    assert client.operators_list()["ok"] is True
    assert bases == [
        ("http://127.0.0.1:9101", "/api/mission/start"),
        ("http://127.0.0.1:9101", "/api/mission/status"),
        ("http://127.0.0.1:9101", "/api/mission/stop"),
        ("http://127.0.0.1:9101", "/api/mission/status"),
        ("http://127.0.0.1:9101", "/api/operators/list"),
    ]
    stop_status["code"] = 200
    assert client.mission_stop()["ok"] is True
    assert client.operators_list()["ok"] is True
    assert bases[-3:] == [
        ("http://127.0.0.1:9101", "/api/mission/status"),
        ("http://127.0.0.1:9101", "/api/mission/stop"),
        ("http://127.0.0.1:9102", "/api/operators/list"),
    ]


def test_external_stop_releases_the_pinned_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["redteam"] = {
        "state": "ready",
        "url": "http://127.0.0.1:9101",
    }
    save_binding(binding, _env(tmp_path))
    calls: list[tuple[str, str]] = []
    active = {"value": True}
    fail_status = {"value": False}

    def request(
        base: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        del method, payload, headers, timeout
        calls.append((base, path))
        if path == "/api/mission/status":
            if fail_status["value"]:
                raise TimeoutError("status")
            if active["value"]:
                return 200, {"mission": "m-1", "active": True, "phase": "running"}
            return 200, {"active": False, "mission": None, "phase": "idle"}
        return 200, {"missionId": "m-1"}

    client = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
            policy=_policy(["127.0.0.1"]),
        )
    )
    assert client.mission_start(name="engagement")["ok"] is True
    binding["gateways"]["redteam"]["url"] = "http://127.0.0.1:9102"
    save_binding(binding, _env(tmp_path))
    fail_status["value"] = True
    assert client.operators_list()["ok"] is True
    assert calls[-2:] == [
        ("http://127.0.0.1:9101", "/api/mission/status"),
        ("http://127.0.0.1:9101", "/api/operators/list"),
    ]
    fail_status["value"] = False
    active["value"] = False
    assert client.mission_start(name="next")["ok"] is True
    assert calls[-2:] == [
        ("http://127.0.0.1:9101", "/api/mission/status"),
        ("http://127.0.0.1:9102", "/api/mission/start"),
    ]


def test_external_stop_does_not_stop_the_new_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["redteam"] = {
        "state": "ready",
        "url": "http://127.0.0.1:9101",
    }
    save_binding(binding, _env(tmp_path))
    calls: list[tuple[str, str]] = []

    def request(
        base: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        del method, payload, headers, timeout
        calls.append((base, path))
        if path == "/api/mission/status":
            return 200, {"active": False, "phase": "idle", "mission": None}
        if path == "/api/mission/stop":
            raise AssertionError("stop must not be forwarded")
        return 200, {"missionId": "m-1"}

    client = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
            policy=_policy(["127.0.0.1"]),
        )
    )
    assert client.mission_start(name="engagement")["ok"] is True
    binding["gateways"]["redteam"]["url"] = "http://127.0.0.1:9102"
    save_binding(binding, _env(tmp_path))
    stopped = client.mission_stop()
    assert stopped["ok"] is True
    assert stopped["data"]["active"] is False
    assert calls == [
        ("http://127.0.0.1:9101", "/api/mission/start"),
        ("http://127.0.0.1:9101", "/api/mission/status"),
    ]
    assert client.operators_list()["ok"] is True
    assert calls[-1] == ("http://127.0.0.1:9102", "/api/operators/list")


def test_pinned_probe_waits_for_kill_switch_and_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["redteam"] = {
        "state": "ready",
        "url": "http://127.0.0.1:9101",
    }
    save_binding(binding, _env(tmp_path))
    calls: list[tuple[str, str]] = []

    def request(
        base: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        del method, payload, headers, timeout
        calls.append((base, path))
        if path != "/api/mission/start":
            raise AssertionError(path)
        return 200, {"missionId": "m-1"}

    client = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
            policy=_policy(["127.0.0.1"]),
        )
    )
    assert client.mission_start(name="engagement")["ok"] is True
    binding["gateways"]["redteam"]["url"] = "http://127.0.0.1:9102"
    save_binding(binding, _env(tmp_path))
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_KILL", "1")
    assert client.status()["mission"]["error"].startswith("killed:")
    assert client.providers()["llm"]["error"].startswith("killed:")
    hats = client.hats_list()
    assert hats["ok"] is False
    assert hats["grants"]["error"].startswith("killed:")
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_KILL", raising=False)
    client.ctx.roe = {
        "effective_until": "2000-01-01T00:00:00Z",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
    }
    assert "roe-expired" in client.status()["mission"]["error"]
    assert "roe-expired" in client.mission_stop()["error"]
    assert calls == [("http://127.0.0.1:9101", "/api/mission/start")]


def test_hat_dispatch_stays_on_the_gateway_that_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["redteam"] = {
        "state": "ready",
        "url": "http://127.0.0.1:9101",
    }
    save_binding(binding, _env(tmp_path))
    calls: list[tuple[str, str]] = []
    ended = {"value": False}

    def request(
        base: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        del method, payload, headers, timeout
        calls.append((base, path))
        if path == "/api/mission/status":
            if ended["value"]:
                return 200, {"active": False, "phase": "idle", "mission": None}
            return 200, {"mission": "m-1", "active": True, "phase": "running"}
        if path == "/api/operators/spawn":
            ended["value"] = True
            return 200, {"operator": {"id": "op-1"}}
        return 200, {"accepted": True}

    client = RedteamClient(
        RedteamContext(
            gateway_url="http://127.0.0.1:1",
            bearer_token=SENTINEL,
            request=request,
            policy=_policy(["127.0.0.1"]),
        )
    )
    assert client.mission_start(name="engagement")["ok"] is True
    binding["gateways"]["redteam"]["url"] = "http://127.0.0.1:9102"
    save_binding(binding, _env(tmp_path))
    body = client.hat_dispatch(hat="recon", task="scan the edge")
    assert body["ok"] is True
    assert body["operatorId"] == "op-1"
    assert calls == [
        ("http://127.0.0.1:9101", "/api/mission/start"),
        ("http://127.0.0.1:9101", "/api/mission/status"),
        ("http://127.0.0.1:9101", "/api/operators/spawn"),
        ("http://127.0.0.1:9101", "/api/operators/op-1/task"),
    ]
    ended["value"] = True
    follow = client.hat_dispatch(hat="recon", task="again")
    assert follow["ok"] is True
    assert calls[-3:] == [
        ("http://127.0.0.1:9101", "/api/mission/status"),
        ("http://127.0.0.1:9102", "/api/operators/spawn"),
        ("http://127.0.0.1:9102", "/api/operators/op-1/task"),
    ]


def test_skipped_gateways_do_not_use_the_default_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path))
    binding = fresh_binding()
    binding["gateways"]["substrate"]["state"] = "skipped"
    binding["gateways"]["desk_bus"]["state"] = "skipped"
    save_binding(binding, _env(tmp_path))

    class Transport:
        def get(self, *args: object, **kwargs: object) -> dict[str, str]:
            raise AssertionError("network")

        def post(self, *args: object, **kwargs: object) -> dict[str, str]:
            raise AssertionError("network")

        def post_text(self, *args: object, **kwargs: object) -> str:
            raise AssertionError("network")

    substrate = SubstrateClient(
        "http://127.0.0.1:1",
        transport=Transport(),
        honor_binding=True,
        binding_env=_env(tmp_path),
    )
    assert substrate.brief() == ""
    desk = _DeskBusClient(
        "http://127.0.0.1:1",
        honor_binding=True,
        binding_env=_env(tmp_path),
    )
    refused = desk.start_job("runtime", "goal")
    assert refused["ok"] is False
    assert "not_configured" in refused["error"]

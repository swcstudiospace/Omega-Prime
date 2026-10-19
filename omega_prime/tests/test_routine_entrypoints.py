"""Runnable nightly, sweep, ultrathink, and desk-lead entry points."""

from __future__ import annotations

import json
from pathlib import Path

from omega_prime.agent.model import ScriptedModel
from omega_prime.commands.context import nested_dispatch_scope, served_roster_scope
from omega_prime.commands.workflows import run_workflow
from omega_prime.routines.desk_lead import (
    _configured_parent,
    execute_desk_lead,
    host_intake_path,
)
from omega_prime.routines.desk_lead import main as desk_main
from omega_prime.routines.nightly import (
    execute_nightly,
    growth_skills_root,
    nightly_pass,
    writable_skills_root,
)
from omega_prime.routines.nightly import main as nightly_main
from omega_prime.routines.sweep import execute_sweep
from omega_prime.routines.sweep import main as sweep_main
from omega_prime.routines.ultrathink_turn import execute_ultrathink
from omega_prime.routines.ultrathink_turn import main as plan_main
from omega_prime.skills_runtime.manager import skill_view
from omega_prime.tools.growth import register_growth_tools
from omega_prime.tools.lead import IntakeStore
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.x import XClient, XError


class _Registry:
    def __init__(self, script: list[dict]) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.script = list(script)

    def dispatch(self, name: str, arguments: dict) -> str:
        self.calls.append((name, dict(arguments)))
        return json.dumps(self.script.pop(0))


def test_nightly_cli_reviews_a_turns_file(tmp_path: Path) -> None:
    turns = tmp_path / "turns.json"
    turns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "tool", "content": "ok", "is_error": False},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    code = nightly_main(["--state-dir", str(tmp_path / "state"), "--turns", str(turns)])
    assert code == 0
    state = json.loads(
        (tmp_path / "state" / "nightly.json").read_text(encoding="utf-8")
    )
    assert state["reviewed"] == ["t1"]


def test_nightly_without_env_does_not_touch_a_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = execute_nightly({})
    assert result["error"] == "not_configured"
    assert result["publishes"] is False
    assert not (tmp_path / "omega_prime").exists()


def test_sweep_from_a_file_queues_drafts_and_never_publishes(tmp_path: Path) -> None:
    mentions = tmp_path / "mentions.json"
    mentions.write_text(
        json.dumps({"posts": [{"id": "m1", "text": "hello there"}]}),
        encoding="utf-8",
    )
    result = execute_sweep(
        {},
        mentions_path=mentions,
        directory=tmp_path / "state",
        sweep_id="weekday",
    )
    assert result["publishes"] is False
    assert result["drafts"][0]["text"].startswith("[draft-only]")
    assert "error" not in result
    saved = json.loads(
        (tmp_path / "state" / "sweeps" / "weekday.json").read_text(encoding="utf-8")
    )
    assert saved["drafts"][0]["mention_id"] == "m1"


def test_sweep_refuses_a_client_that_can_publish(tmp_path: Path) -> None:
    client = XClient(transport=object(), token="not-used")
    result = execute_sweep({}, directory=tmp_path, client=client)
    assert result["error"] == "publish_refused"
    assert result["publishes"] is False
    assert result["drafts"] == []


def test_sweep_cli_without_token_does_not_publish(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("X_API_TOKEN", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_SWEEP_MENTIONS", raising=False)
    code = sweep_main(["--state-dir", str(tmp_path)])
    assert code == 1


def test_ultrathink_cli_reads_an_explicit_directory(tmp_path: Path) -> None:
    plan = tmp_path / "last-plan.json"
    plan.write_text(
        json.dumps({"specPath": str(tmp_path / "missing.md")}), encoding="utf-8"
    )
    code = plan_main(["--state-dir", str(tmp_path)])
    assert code == 0
    empty = execute_ultrathink({})
    assert empty["found"] is False
    assert empty["publishes"] is False


def test_desk_lead_cli_on_an_empty_queue(tmp_path: Path) -> None:
    code = desk_main(["--state-dir", str(tmp_path), "--limit", "2"])
    assert code == 0
    blocked = execute_desk_lead({})
    assert blocked["error"] == "not_configured"
    assert blocked["publishes"] is False
    assert host_intake_path({}) is None


def test_home_reads_the_served_intake_not_xdg_state(
    tmp_path: Path, monkeypatch
) -> None:
    install = tmp_path / "install"
    monkeypatch.setattr("omega_prime.routines.desk_lead._INSTALL_ROOT", install)
    home = tmp_path / "home"
    served = install / "omega_prime" / "state" / "intake.json"
    IntakeStore(served).submit("from the served queue")
    result = execute_desk_lead({"HOME": str(home)}, limit=1)
    assert result["tickets"][0]["ask"] == "from the served queue"
    assert result["tickets"][0]["status"] == "blocked"
    assert "no parent agent shim" in result["tickets"][0]["reason"]
    assert not (home / ".local").exists()


def test_work_root_and_state_dir_select_the_served_queue(tmp_path: Path) -> None:
    work = tmp_path / "work"
    state = tmp_path / "state"
    home = tmp_path / "home"
    IntakeStore(work / "omega_prime" / "state" / "intake.json").submit("work order")
    work_result = execute_desk_lead(
        {"HOME": str(home), "OMEGA_PRIME_WORK_ROOT": str(work)}, limit=1
    )
    assert work_result["tickets"][0]["ask"] == "work order"
    IntakeStore(state / "intake.json").submit("state order")
    state_result = execute_desk_lead(
        {
            "HOME": str(home),
            "OMEGA_PRIME_WORK_ROOT": str(work),
            "OMEGA_PRIME_STATE_DIR": str(state),
        },
        limit=1,
    )
    assert state_result["tickets"][0]["ask"] == "state order"
    assert not (home / ".local").exists()


def test_cli_without_a_registry_leaves_the_ticket_open(tmp_path: Path) -> None:
    work = tmp_path / "work"
    store = IntakeStore(work / "omega_prime" / "state" / "intake.json")
    record = store.submit("add a banner")
    result = execute_desk_lead(
        {
            "OMEGA_PRIME_WORK_ROOT": str(work),
            "OMEGA_PRIME_DESK_CHILD_MODEL": "scripted",
            "XAI_API_KEY": "not-used",
        },
        limit=1,
    )
    assert result["tickets"][0]["status"] == "blocked"
    assert "no parent agent shim" in result["tickets"][0]["reason"]
    assert result["publishes"] is False
    reopened = store.get(record["intake_id"])
    assert reopened is not None
    assert reopened["status"] == "open"


def test_child_model_uses_the_registry_policy(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    IntakeStore(work / "omega_prime" / "state" / "intake.json").submit("add a banner")
    policy = object()
    model = ScriptedModel([{"role": "assistant", "content": "banner added"}])

    class Host:
        def __init__(self, policy: object) -> None:
            self.policy = policy

        def schemas(self) -> list[dict]:
            return []

    host = Host(policy)

    def fake_model(env: dict, got: object) -> ScriptedModel:
        assert got is policy
        assert env["OMEGA_PRIME_DESK_CHILD_MODEL"] == "scripted"
        return model

    monkeypatch.setattr("omega_prime.mcp_server._desk_child_model", fake_model)
    result = execute_desk_lead(
        {
            "OMEGA_PRIME_WORK_ROOT": str(work),
            "OMEGA_PRIME_DESK_CHILD_MODEL": "scripted",
        },
        limit=1,
        registry=host,
    )
    assert result["tickets"][0]["status"] == "done"
    assert any("add a banner" in json.dumps(seen) for seen in model.seen)


def test_host_child_model_is_reused(monkeypatch) -> None:
    model = ScriptedModel([{"role": "assistant", "content": "banner added"}])

    class Parent:
        child_model = model
        journal = "desk-journal"

    class Driver:
        parent = Parent()

    class Host:
        def __init__(self) -> None:
            self.runtime_bindings = {"desk_driver": Driver()}
            self.policy = object()

        def schemas(self) -> list[dict]:
            return []

    def boom(env: dict, policy: object) -> object:
        raise AssertionError("built a second child model")

    monkeypatch.setattr("omega_prime.mcp_server._desk_child_model", boom)
    parent = _configured_parent({}, Host())
    assert parent is not None
    assert parent.child_model is model
    assert parent.journal == "desk-journal"


def test_child_tools_follow_the_served_roster(monkeypatch) -> None:
    model = ScriptedModel([])

    class Host:
        policy = None

        def schemas(self) -> list[dict]:
            return [
                {"function": {"name": "run_terminal"}},
                {"function": {"name": "lead_doctor"}},
            ]

        def dispatch(self, name: str, arguments: dict) -> str:
            raise AssertionError(name)

    monkeypatch.setattr(
        "omega_prime.mcp_server._desk_child_model", lambda env, policy: model
    )
    parent = _configured_parent({"OMEGA_PRIME_DESK_CHILD_MODEL": "scripted"}, Host())
    assert parent is not None
    calls: list[tuple[str, dict]] = []

    def nested(tool: str, arguments: dict) -> str:
        calls.append((tool, arguments))
        return json.dumps({"ok": True})

    with served_roster_scope(frozenset({"lead_doctor"})):
        tools = parent.tools
        assert set(tools) == {"lead_doctor"}
        with nested_dispatch_scope(nested):
            body = json.loads(tools["lead_doctor"](action="check"))
    assert body == {"ok": True}
    assert calls == [("lead_doctor", {"action": "check"})]


def test_configured_child_uses_the_host_registry(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    IntakeStore(work / "omega_prime" / "state" / "intake.json").submit("add a banner")
    model = ScriptedModel([{"role": "assistant", "content": "banner added"}])

    class HostRegistry:
        def __init__(self) -> None:
            self.schemas_called = 0

        def schemas(self) -> list[dict]:
            self.schemas_called += 1
            return []

    host = HostRegistry()
    monkeypatch.setattr(
        "omega_prime.mcp_server._desk_child_model", lambda env, policy: model
    )
    result = execute_desk_lead(
        {
            "OMEGA_PRIME_WORK_ROOT": str(work),
            "OMEGA_PRIME_DESK_CHILD_MODEL": "scripted",
        },
        limit=1,
        registry=host,
    )
    assert result["tickets"][0]["status"] == "done"
    assert host.schemas_called >= 1


def test_named_model_without_a_provider_still_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    work = tmp_path / "work"
    IntakeStore(work / "omega_prime" / "state" / "intake.json").submit("fix login")

    class Host:
        policy = None

        def schemas(self) -> list[dict]:
            return []

    monkeypatch.setattr(
        "omega_prime.mcp_server._desk_child_model", lambda env, policy: None
    )
    result = execute_desk_lead(
        {
            "OMEGA_PRIME_WORK_ROOT": str(work),
            "OMEGA_PRIME_DESK_CHILD_MODEL": "scripted",
        },
        limit=1,
        registry=Host(),
    )
    assert result["tickets"][0]["status"] == "blocked"
    assert "no parent agent shim" in result["tickets"][0]["reason"]
    assert result["publishes"] is False


def test_nightly_writes_skills_where_skill_view_reads(
    tmp_path: Path, monkeypatch
) -> None:
    skills = tmp_path / "omega_prime" / "skills"
    skills.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "omega_prime.routines.nightly.growth_skills_root", lambda: skills
    )
    assert growth_skills_root().name == "skills"
    turns = [
        {
            "id": "t1",
            "messages": [
                {
                    "role": "user",
                    "content": "please save as a skill name:deploy the steps",
                },
                {
                    "role": "tool",
                    "content": "run ./deploy.sh --prod",
                    "is_error": False,
                },
            ],
        }
    ]
    result = execute_nightly(
        {"HOME": str(tmp_path / "home")},
        turns_path=_write_turns(tmp_path, turns),
        directory=tmp_path / "state",
    )
    assert result["total_created"] == 1
    viewed = json.loads(skill_view("deploy", skills_root=skills))
    assert "run ./deploy.sh --prod" in viewed["content"]
    assert not (tmp_path / "state" / "skills").exists()
    assert (skills / "deploy" / "SKILL.md").is_file()


def test_read_only_install_writes_skills_on_the_state_volume(
    tmp_path: Path, monkeypatch
) -> None:
    install = tmp_path / "omega_prime" / "skills"
    install.mkdir(parents=True)
    shipped = install / "shipped"
    shipped.mkdir()
    (shipped / "SKILL.md").write_text("shipped body\n", encoding="utf-8")
    install.chmod(0o555)
    state = tmp_path / "state"
    monkeypatch.setattr(
        "omega_prime.routines.nightly.growth_skills_root", lambda: install
    )
    turns = [
        {
            "id": "t1",
            "messages": [
                {
                    "role": "user",
                    "content": "please save as a skill name:deploy the steps",
                },
                {
                    "role": "tool",
                    "content": "run ./deploy.sh --prod",
                    "is_error": False,
                },
            ],
        }
    ]
    try:
        result = execute_nightly(
            {"OMEGA_PRIME_STATE_DIR": str(state)},
            turns_path=_write_turns(tmp_path, turns),
            directory=state,
        )
        assert result["total_created"] == 1
        assert writable_skills_root(install, {"OMEGA_PRIME_STATE_DIR": str(state)}) == (
            state / "skills"
        )
        viewed = json.loads(skill_view("deploy", skills_root=state / "skills"))
        assert "run ./deploy.sh --prod" in viewed["content"]
        registry = ToolRegistry()
        register_growth_tools(
            registry,
            skills_root=state / "skills",
            skills_fallback=install,
            memory_dir=tmp_path / "memory",
            session_db=tmp_path / "sessions.db",
        )
        shipped_view = json.loads(registry.dispatch("skill_view", {"name": "shipped"}))
        created_view = json.loads(registry.dispatch("skill_view", {"name": "deploy"}))
        assert "shipped body" in shipped_view["content"]
        assert "run ./deploy.sh --prod" in created_view["content"]
    finally:
        install.chmod(0o755)


def test_failed_skill_save_stays_unreviewed(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    skills.chmod(0o555)
    turns = [
        {
            "id": "t1",
            "messages": [
                {
                    "role": "user",
                    "content": "please save as a skill name:deploy the steps",
                },
                {"role": "tool", "content": "run ./deploy.sh", "is_error": False},
            ],
        }
    ]
    try:
        failed = nightly_pass(tmp_path / "state", skills, turns)
        assert failed["reviewed"] == []
        assert failed["created"] == []
        assert failed["failed"] == ["t1"]
        skills.chmod(0o755)
        saved = nightly_pass(tmp_path / "state", skills, turns)
        assert saved["reviewed"] == ["t1"]
        assert saved["total_created"] == 1
    finally:
        skills.chmod(0o755)


def test_growth_skills_root_is_the_install_directory() -> None:
    root = growth_skills_root()
    assert root == Path(__file__).resolve().parents[1] / "skills"
    assert (root / "desk-gateway" / "SKILL.md").is_file()


def test_sweep_x_error_is_sweep_failed(tmp_path: Path) -> None:
    class Boom:
        def mentions(self, user_id: str | None = None, max_results: int = 10) -> dict:
            raise XError("mentions unavailable")

    result = execute_sweep({}, directory=tmp_path, client=Boom())
    assert result["error"] == "sweep_failed"
    assert result["reason"] == "mentions unavailable"
    assert result["drafts"] == []
    assert result["publishes"] is False


def test_denied_x_mentions_does_not_open_a_private_client(
    tmp_path: Path, monkeypatch
) -> None:
    class PrivateClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("private X client")

    monkeypatch.setattr("omega_prime.tools.x.XClient", PrivateClient)
    registry = _Registry([{"error": "policy forbids x_mentions"}])
    token = "super-secret-token"
    result = execute_sweep(
        {"X_API_TOKEN": token},
        directory=tmp_path,
        registry=registry,
    )
    assert registry.calls[0][0] == "x_mentions"
    assert result["error"] == "sweep_failed"
    assert result["drafts"] == []
    assert result["publishes"] is False
    assert token not in json.dumps(result)
    workflow = run_workflow(
        "sweep",
        _Registry([{"error": "policy forbids x_mentions"}]),
        {"X_API_TOKEN": token, "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    step = workflow["steps"][0]["result"]
    assert step["error"] == "sweep_failed"
    assert step["publishes"] is False
    assert token not in json.dumps(workflow)


def test_dispatched_mentions_stay_drafts(tmp_path: Path) -> None:
    registry = _Registry([{"posts": [{"id": "m9", "text": "hello there"}]}])
    result = execute_sweep(
        {"X_API_TOKEN": "super-secret-token"},
        directory=tmp_path,
        registry=registry,
    )
    assert result["publishes"] is False
    assert result["drafts"][0]["text"].startswith("[draft-only]")
    assert registry.calls == [("x_mentions", {"max_results": 10})]


def test_served_redeploy_example_keeps_gate_fields_on_the_receipt() -> None:
    text = (growth_skills_root() / "desk-gateway" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    call_at = text.index("infra_railway_redeploy {")
    call = text[call_at : text.index("Receipt:", call_at)]
    assert "approval_id" not in call
    assert "rollback_plan" not in call
    assert "prior_deployment_id" in call
    receipt = text[text.index("Receipt:", call_at) : text.index("```", call_at)]
    assert "{{APPROVAL_ID}}" in receipt
    assert "rollback_plan" in receipt


def _write_turns(tmp_path: Path, turns: list[dict]) -> Path:
    path = tmp_path / "turns.json"
    path.write_text(json.dumps(turns), encoding="utf-8")
    return path

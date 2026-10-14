"""Contract tests for the Prime connector layer (CONN-01, CONN-02).

Both sides of each adapter: the tool surface (JSON dicts in) and the
capability surface (typed views out). Round-trips are lossless; malformed
payloads — unknown fields, bool/int confusion, out-of-vocabulary values,
future schema versions — are rejected with typed PrimeErrors.
"""

from __future__ import annotations

import pytest

from omega_prime.agent.messaging import SessionRegistry
from omega_prime.agent.rlm import RlmHost
from omega_prime.prime import SCHEMA_VERSION, PrimeError
from omega_prime.prime.autonomous import (
    AutonomousConnector,
    DriverStatusView,
    StartRequest,
    VerdictView,
)
from omega_prime.prime.autonomous import (
    StatusRequest as AutonomousStatusRequest,
)
from omega_prime.prime.autonomous import (
    StopRequest as AutonomousStopRequest,
)
from omega_prime.prime.goals import (
    AccrueRequest,
    ClearRequest,
    GoalsConnector,
    GoalStatusView,
    PauseRequest,
    ResumeRequest,
    SetGoalRequest,
)
from omega_prime.prime.goals import (
    StatusRequest as GoalStatusRequest,
)
from omega_prime.prime.harness import (
    DeleteRequest as HarnessDeleteRequest,
)
from omega_prime.prime.harness import (
    EntryView,
    GetRequest,
    HarnessConnector,
    ListRequest,
    RefineRequest,
    RollbackRequest,
    UpsertRequest,
)
from omega_prime.prime.messaging import (
    MessageView,
    MessagingConnector,
    ObserveRequest,
    SendRequest,
)
from omega_prime.prime.rlm import (
    ChildResultView,
    CollectRequest,
    CreateSessionRequest,
    DeleteRequest,
    ListSubagentsRequest,
    ProgressNoteRequest,
    RenameRequest,
    RlmConnector,
    SpawnRequest,
)


class _Parent:
    def __init__(self, tmp_path):
        self.delegate_depth = 0
        self.max_depth = 2
        self.max_children = 4
        self.session_dir = str(tmp_path)
        self.session_name = None


def _rlm_connector(tmp_path):
    host = RlmHost(
        _Parent(tmp_path),
        run_child=lambda prompt, model=None, thinking=None: f"done: {prompt}",
    )
    return RlmConnector(host)


# --- schema version guard ----------------------------------------------------


@pytest.mark.parametrize(
    "cls,payload",
    [
        (SpawnRequest, {"prompt": "p", "name": "n"}),
        (CollectRequest, {}),
        (ProgressNoteRequest, {"child_id": "c", "message": "m"}),
        (CreateSessionRequest, {"prompt": "p"}),
        (DeleteRequest, {"target": "kid"}),
        (RenameRequest, {"target": "kid", "name": "new"}),
        (ListSubagentsRequest, {}),
        (UpsertRequest, {"kind": "prompt", "id": "i", "title": "t", "body": "b"}),
        (GetRequest, {"kind": "prompt", "id": "i"}),
        (ListRequest, {}),
        (HarnessDeleteRequest, {"kind": "prompt", "id": "i"}),
        (
            RefineRequest,
            {"trigger": "t", "proposals": [], "trajectory": "text"},
        ),
        (RollbackRequest, {}),
        (SetGoalRequest, {"objective": "o"}),
        (PauseRequest, {}),
        (ResumeRequest, {}),
        (ClearRequest, {}),
        (GoalStatusRequest, {}),
        (AccrueRequest, {"usage": {}}),
        (StartRequest, {}),
        (AutonomousStatusRequest, {}),
        (AutonomousStopRequest, {}),
        (SendRequest, {"sender": "a", "recipient": "b", "body": "x"}),
        (ObserveRequest, {"session": "a"}),
    ],
)
def test_future_schema_version_rejected_everywhere(cls, payload):
    with pytest.raises(PrimeError, match="unsupported_schema_version"):
        cls.from_dict({**payload, "schema_version": SCHEMA_VERSION + 1})


def test_current_schema_version_accepted():
    request = SpawnRequest.from_dict(
        {"prompt": "p", "name": "n", "schema_version": SCHEMA_VERSION}
    )
    assert request.to_dict()["schema_version"] == SCHEMA_VERSION


def test_non_int_schema_version_rejected():
    with pytest.raises(PrimeError, match="schema_version must be int"):
        SpawnRequest.from_dict({"prompt": "p", "name": "n", "schema_version": "1"})


# --- rlm ----------------------------------------------------------------------


def test_spawn_request_round_trip():
    request = SpawnRequest.from_dict({"prompt": "do it", "name": "kid", "model": "m"})
    assert SpawnRequest.from_dict(request.to_dict()) == request


def test_spawn_request_rejects_unknown_field():
    with pytest.raises(PrimeError, match="unknown_field"):
        SpawnRequest.from_dict({"prompt": "p", "name": "n", "surprise": 1})


def test_spawn_request_rejects_bool_model():
    with pytest.raises(PrimeError, match="bad_type"):
        SpawnRequest.from_dict({"prompt": "p", "name": "n", "model": True})


def test_collect_request_validates_targets():
    with pytest.raises(PrimeError, match="targets must be"):
        CollectRequest.from_dict({"targets": 42})
    with pytest.raises(PrimeError, match="entries must be strings"):
        CollectRequest.from_dict({"targets": ["ok", 7]})
    with pytest.raises(PrimeError, match="non-empty string"):
        CollectRequest.from_dict({"targets": "  "})
    with pytest.raises(PrimeError, match="handle row"):
        CollectRequest.from_dict({"targets": {"name": "kid"}})
    assert CollectRequest.from_dict({"targets": ["a"], "timeout_ms": 5}).timeout_ms == 5


def test_collect_request_accepts_handle_rows_and_mixed_lists():
    single = CollectRequest.from_dict({"targets": {"rlm_child_id": "rlm-abc"}})
    assert single.targets == {"rlm_child_id": "rlm-abc"}
    mixed = CollectRequest.from_dict({"targets": ["kid", {"rlm_child_id": "rlm-abc"}]})
    assert mixed.targets == ["kid", {"rlm_child_id": "rlm-abc"}]
    assert CollectRequest.from_dict({"targets": []}).targets == []
    assert CollectRequest.from_dict({}).targets is None


def test_collect_request_rejects_bool_timeout():
    with pytest.raises(PrimeError, match="bad_type"):
        CollectRequest.from_dict({"timeout_ms": True})


def test_session_delete_rename_requests_round_trip():
    create = CreateSessionRequest.from_dict({"prompt": "p", "cwd": None})
    assert "cwd" not in create.to_dict()
    assert CreateSessionRequest.from_dict(create.to_dict()) == create
    with pytest.raises(PrimeError, match="bad_value"):
        CreateSessionRequest.from_dict({"prompt": "p", "cwd": "/tmp"})
    with pytest.raises(PrimeError, match="bad_type"):
        CreateSessionRequest.from_dict({"prompt": "p", "model": True})
    delete = DeleteRequest.from_dict({"target": "kid"})
    assert DeleteRequest.from_dict(delete.to_dict()) == delete
    with pytest.raises(PrimeError, match="required"):
        DeleteRequest.from_dict({})
    rename = RenameRequest.from_dict({"target": None, "name": "parent"})
    assert RenameRequest.from_dict(rename.to_dict()) == rename
    with pytest.raises(PrimeError, match="non-empty string"):
        RenameRequest.from_dict({"target": "kid", "name": ""})


def test_rlm_connector_spawn_collect_round_trip(tmp_path):
    connector = _rlm_connector(tmp_path)
    spawned = connector.spawn(SpawnRequest(prompt="work", name="kid"))
    assert spawned["rlm_child_id"].startswith("rlm-")
    out = connector.collect(CollectRequest(targets=["kid"], timeout_ms=5000))
    assert out["results"][0]["status"] == "done"
    assert out["results"][0]["settled"] is True
    # The tool-surface dict decodes back into the typed view losslessly.
    view = ChildResultView.from_dict(out["results"][0])
    assert view.to_dict() == out["results"][0]


def test_rlm_connector_session_delete_rename_lifecycle(tmp_path):
    connector = _rlm_connector(tmp_path)
    created = connector.create_session(
        CreateSessionRequest(prompt="durable work", name="sess"),
    )
    rows = connector.list_subagents()["subagents"]
    assert {r["session_name"] for r in rows} == {"sess"}
    assert rows[0]["session_id"] == created["session_id"]
    # The typed list channel carries session identity, never answer text.
    assert "session_id" in rows[0] and "active_session_id" in rows[0]
    assert "answer_preview" not in rows[0]
    child_id = rows[0]["rlm_child_id"]
    # Dict-row selectors work wherever the host accepts them.
    collected = connector.collect(
        CollectRequest(targets={"rlm_child_id": child_id}, timeout_ms=5000)
    )
    assert collected["results"][0]["status"] == "done"
    renamed = connector.rename(RenameRequest(target="sess", name="sess2"))
    assert renamed == {"renamed": child_id, "name": "sess2"}
    deleted = connector.delete_subagent(DeleteRequest(target="sess2"))
    assert deleted == {"deleted": child_id, "name": "sess2"}
    assert connector.list_subagents()["subagents"] == []


def test_child_result_view_rejects_unknown_status():
    with pytest.raises(PrimeError, match="not in COLLECT_STATUSES"):
        ChildResultView.from_dict(
            {"rlm_child_id": "x", "status": "teleported", "settled": True}
        )


# --- harness --------------------------------------------------------------------


def test_upsert_request_round_trip():
    request = UpsertRequest.from_dict(
        {
            "kind": "skill",
            "id": "s1",
            "title": "T",
            "body": "B",
            "scope": "global",
            "tags": ["a"],
        }
    )
    assert request.scope == "global"
    assert UpsertRequest.from_dict(request.to_dict()) == request


def test_upsert_request_rejects_unknown_kind():
    with pytest.raises(PrimeError, match="must be one of"):
        UpsertRequest.from_dict(
            {"kind": "widget", "id": "i", "title": "t", "body": "b"}
        )


def test_harness_connector_round_trip(tmp_path):
    connector = HarnessConnector(tmp_path)
    connector.upsert(UpsertRequest(kind="memory", id="m1", title="T", body="B"))
    got = connector.get(GetRequest(kind="memory", id="m1"))
    assert got["entry"]["body"] == "B"
    view = EntryView.from_dict(got["entry"])
    assert view.to_dict() == got["entry"]
    listed = connector.list_entries(ListRequest(kind="memory"))
    assert listed["entries"][0]["id"] == "m1"
    assert connector.delete(HarnessDeleteRequest(kind="memory", id="m1")) == {
        "deleted": True
    }


def test_harness_connector_scopes_are_isolated(tmp_path):
    connector = HarnessConnector(tmp_path)
    connector.upsert(
        UpsertRequest(kind="memory", id="m1", title="T", body="local", scope="local")
    )
    connector.upsert(
        UpsertRequest(kind="memory", id="m1", title="T", body="global", scope="global")
    )
    local = connector.get(GetRequest(kind="memory", id="m1", scope="local"))
    glob = connector.get(GetRequest(kind="memory", id="m1", scope="global"))
    assert local["entry"]["body"] == "local"
    assert glob["entry"]["body"] == "global"


def test_harness_connector_refine_and_rollback(tmp_path):
    connector = HarnessConnector(tmp_path)
    trajectory = "the run produced evidence-line-1 on the dashboard"
    result = connector.refine(
        RefineRequest(
            trigger="review",
            proposals=[
                {
                    "kind": "memory",
                    "id": "m1",
                    "title": "T",
                    "body": "B",
                    "evidence": ["evidence-line-1"],
                }
            ],
            trajectory=trajectory,
        )
    )
    assert [e["id"] for e in result["applied"]] == ["m1"]
    assert connector.rollback(RollbackRequest()) == {"restored": True}
    assert connector.get(GetRequest(kind="memory", id="m1"))["entry"] is None
    with pytest.raises(PrimeError, match="list of objects"):
        RefineRequest.from_dict(
            {"trigger": "t", "proposals": ["not-a-dict"], "trajectory": "x"}
        )


# --- goals ----------------------------------------------------------------------


def test_set_goal_request_rejects_bool_budget():
    with pytest.raises(PrimeError, match="bad_type"):
        SetGoalRequest.from_dict({"objective": "o", "token_budget": True})


def test_goals_connector_lifecycle(tmp_path):
    connector = GoalsConnector(tmp_path / "goals")
    connector.set_goal(
        SetGoalRequest(objective="ship it", token_budget=100, steps=("a", "b"))
    )
    status = connector.status(GoalStatusRequest())
    assert status["prime_status"] == "active"
    assert status["token_budget"] == 100
    # The validated view fields ride the full store envelope, unchanged.
    view = GoalStatusView.from_dict(status)
    for key, value in view.to_dict().items():
        assert status[key] == value
    connector.accrue(AccrueRequest(usage={"total_tokens": 40}))
    assert connector.status(GoalStatusRequest())["tokens_used"] == 40
    # Junk counters are the capability's tolerance: delta 0, no raise.
    connector.accrue(AccrueRequest(usage={"total_tokens": "lots"}))
    assert connector.status(GoalStatusRequest())["tokens_used"] == 40
    assert connector.continuation_prompt()["prompt"] == (
        '<goal_continuation version="1">Continue working toward the goal: '
        "ship it. Next step: a.</goal_continuation>"
    )
    assert connector.pause(PauseRequest())["prime_status"] == "paused"
    assert connector.resume(ResumeRequest())["prime_status"] == "active"
    assert connector.clear(ClearRequest())["prime_status"] == "cleared"


def test_goal_status_view_rejects_unknown_status():
    with pytest.raises(PrimeError, match="prime_status"):
        GoalStatusView.from_dict({"objective": "o", "prime_status": "vibing"})


# --- autonomous -------------------------------------------------------------------


def test_start_request_rejects_shell_string_gate():
    with pytest.raises(PrimeError, match="argv list"):
        StartRequest.from_dict({"gate": "make test"})


def test_start_request_round_trip():
    request = StartRequest.from_dict(
        {"max_turns": 3, "gate": ["true"], "gate_retries": 0, "max_minutes": 1.5}
    )
    assert request.gate == ("true",)
    assert StartRequest.from_dict(request.to_dict()) == request


def test_autonomous_connector_verdict_typing(tmp_path):
    connector = AutonomousConnector(tmp_path)
    driver = connector.start(StartRequest(max_turns=1))
    verdict = connector.after_turn(
        driver, {"completed": False, "usage": {"total_tokens": 5}}
    )
    assert verdict["action"] == "stop"
    assert verdict["reason"] == "autonomous_max_turns"
    view = VerdictView.from_dict(verdict)
    assert view.to_dict() == verdict


def test_autonomous_connector_status_and_stop(tmp_path):
    connector = AutonomousConnector(tmp_path)
    assert connector.status(AutonomousStatusRequest(), None) == {
        "running": False,
        "stopped": None,
    }
    assert connector.stop(AutonomousStopRequest(), None) == {
        "stopped": True,
        "was_running": False,
    }
    driver = connector.start(StartRequest(max_turns=2))
    status = connector.status(AutonomousStatusRequest(), driver)
    assert status["running"] is True
    assert status["budget"] == {
        "max_turns": 2,
        "max_tokens": None,
        "max_minutes": None,
    }
    view = DriverStatusView.from_status(status)
    assert view.running is True
    assert connector.stop(AutonomousStopRequest(), driver) == {
        "stopped": True,
        "was_running": True,
    }
    with pytest.raises(PrimeError, match="not in STOP_REASONS"):
        DriverStatusView.from_status(
            {"running": False, "turns": 1, "tokens": 0, "stopped": "bored"}
        )


def test_autonomous_gate_failure_is_a_structured_stop(tmp_path):
    connector = AutonomousConnector(tmp_path)
    driver = connector.start(StartRequest(gate=("false",), gate_retries=1))
    verdict = connector.after_turn(driver, {"completed": True, "usage": {}})
    assert verdict["action"] == "stop"
    assert verdict["reason"] == "autonomous_gate_failed"
    # A stopped driver stays stopped — no hang, no restart.
    assert (
        connector.after_turn(driver, {"completed": True})["reason"]
        == "autonomous_gate_failed"
    )


def test_verdict_view_rejects_unknown_stop_reason():
    with pytest.raises(PrimeError, match=r"not in STOP_REASONS|must be one of"):
        VerdictView.from_dict({"action": "stop", "reason": "autonomous_bored"})


# --- messaging ------------------------------------------------------------------


def test_send_request_rejects_empty_body():
    with pytest.raises(PrimeError, match="non-empty string"):
        SendRequest.from_dict({"sender": "a", "recipient": "b", "body": ""})


def test_messaging_connector_round_trip():
    connector = MessagingConnector(SessionRegistry())
    connector._registry.register("alpha")
    connector._registry.register("beta")
    out = connector.send(SendRequest(sender="alpha", recipient="beta", body="ping"))
    delivered = out["delivered"]
    view = MessageView.from_dict(delivered)
    assert view.to_dict() == delivered
    inbox = connector.observe(ObserveRequest(session="beta"))
    assert inbox["messages"][0]["body"] == "ping"
    assert inbox["messages"][0]["read"] is True


def test_observe_request_round_trip_and_bool_rejection():
    request = ObserveRequest.from_dict({"session": "a", "mark_read": False})
    assert request.mark_read is False
    assert ObserveRequest.from_dict(request.to_dict()) == request
    assert ObserveRequest.from_dict({"session": "a"}).mark_read is True
    with pytest.raises(PrimeError, match="bad_type"):
        ObserveRequest.from_dict({"session": "a", "mark_read": "yes"})


def test_messaging_connector_unknown_recipient_raises():
    connector = MessagingConnector(SessionRegistry())
    connector._registry.register("alpha")
    with pytest.raises(PrimeError) as excinfo:
        connector.send(SendRequest(sender="alpha", recipient="ghost", body="hi"))
    assert excinfo.value.code == "unknown_recipient"
    assert "ghost" in str(excinfo.value)

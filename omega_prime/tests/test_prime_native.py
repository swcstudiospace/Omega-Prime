# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""PyO3 bindings for the pinned Prime crates."""

import json

import pytest

from omega_prime.prime_kernel.native import (
    extension_path,
    goal_token_delta,
    load_extension,
    native_status,
    parse_kernel_event,
    validate_goal_objective,
)

NINE = [
    "pa-telemetry",
    "pa-types",
    "pa-ai",
    "pa-models",
    "pa-agent",
    "pa-core",
    "pa-daemon",
    "pa-tui",
    "pa-cli",
]


def _extension():
    if extension_path() is None:
        pytest.skip("optional omega_prime_prime extension is not built")
    return load_extension()


def _goal(**overrides):
    state = {
        "active": False,
        "status": "active",
        "objective": "ship the release",
        "tokenBudget": 10,
        "tokensUsed": 3,
        "timeUsedSeconds": 4,
        "continuationsUsed": 1,
        "updatedAt": 9,
    }
    state.update(overrides)
    return state


def _job(**overrides):
    job = {
        "id": "h",
        "status": "active",
        "source": "heartbeat",
        "activeSessionId": "s",
        "sessionId": "s",
        "sessionFile": "s.jsonl",
        "cwd": "/",
        "prompt": "ping the worker",
        "schedule": {
            "kind": "interval",
            "expression": "every 5m",
            "intervalMs": 300_000,
        },
        "createdAt": "2026-10-08T00:00:00Z",
        "updatedAt": "2026-10-08T00:00:00Z",
        "nextRunAt": "2020-01-01T00:00:00Z",
        "deliveryMode": "follow_up",
    }
    job.update(overrides)
    return job


def _refinement(**overrides):
    result = {
        "id": "r1",
        "summary": "tighten the prompt",
        "rationale": "because",
        "expectedOutcome": "faster",
        "appliedEdits": [],
        "harnessStatePath": "harness.json",
        "scope": "global",
    }
    result.update(overrides)
    return result


def test_native_status_accepts_unbuilt_extension():
    status = native_status()
    assert isinstance(status, dict)
    assert isinstance(status["loaded"], bool)
    if status["loaded"] is False:
        assert isinstance(status["error"], str) and status["error"]
        assert status["crates"] in (NINE, [])
        return
    assert status["crates"] == NINE
    assert status["error"] is None
    assert validate_goal_objective("ship it") == "ship it"
    assert goal_token_delta(3, 4) == 7


@pytest.mark.native
def test_goal_bindings_follow_pa_core():
    ext = _extension()
    goals = ext.goals
    assert goals.MAX_THREAD_GOAL_OBJECTIVE_CHARS == 4000
    assert goals.GOAL_STATE_CUSTOM_TYPE == "thread_goal_state"
    with pytest.raises(ValueError, match="must not be empty"):
        goals.validate_goal_objective("  ")
    with pytest.raises(ValueError, match="positive integer"):
        goals.validate_goal_budget(0)
    assert goals.validate_goal_budget(None) is None
    assert goals.validate_goal_budget(5) == 5
    assert goals.goal_token_delta(-3, 4) == 4

    empty = goals.empty_goal_state()
    assert empty["status"] == "idle"
    assert empty["active"] is False
    assert goals.stale_active_goal_failure([]) is None

    normalized = goals.normalize_goal_state(_goal(active=False))
    assert normalized["active"] is True
    assert normalized["createdAt"] == 9
    assert goals.is_persisted_goal_state(normalized) is True
    assert goals.is_persisted_goal_state({"active": True}) is False

    projected = goals.goal_update_dedupe_projection(normalized)
    assert projected["timeUsedSeconds"] == 0
    assert projected["tokensUsed"] == 3

    assert goals.format_goal_usage(normalized) == "3 / 10 tokens"
    response = goals.goal_host_response(normalized, True)
    assert response["goal"]["objective"] == "ship the release"
    assert response["goal"]["status"] == "active"
    assert response["remaining_tokens"] == 7
    assert response.get("completion_budget_report") is None

    message = goals.create_goal_context_message(normalized, "budget_limit")
    assert message["customType"] == "goal_context"
    assert "ship the release" in message["content"]
    assert message["details"]["kind"] == "budget_limit"
    assert message["details"]["continuationsUsed"] == 1
    with pytest.raises(ValueError, match="without an objective"):
        goals.create_goal_context_message(empty, "continuation")


@pytest.mark.native
def test_autonomous_run_uses_pa_core_limits():
    ext = _extension()
    autonomous = ext.autonomous
    assert autonomous.is_unlimited(autonomous.UNLIMITED_AUTONOMOUS_LIMIT) is True
    assert autonomous.is_unlimited(autonomous.UNLIMITED_AUTONOMOUS_LIMIT - 1) is False
    assert autonomous.disabled_status()["enabled"] is False

    run = autonomous.AutonomousRun(None, {"maxTurns": 4})
    assert run.enabled is False
    assert run.status()["limits"]["maxTurns"] == 4
    run.add_usage({"input": 10, "output": 1})
    assert run.turns_used == 0
    run.set_enabled(True)
    assert run.status()["startedAt"] is not None
    run.add_usage({"input": 3, "output": 4, "cacheWrite": 1, "cacheRead": 100})
    assert run.turns_used == 1
    assert run.tokens_used == 8
    run.add_continuation()
    assert run.continuations_used == 1
    run.set_limits({"maxTokens": 8})
    assert run.limit_reason(0) == "max_tokens"
    assert run.describe_limit("max_tokens", 0) == "maxTokens reached (8/8)"
    run.set_enabled(False)
    assert run.status().get("startedAt") is None

    fresh = autonomous.AutonomousRun({"enabled": True})
    assert fresh.status()["limits"]["maxTurns"] == autonomous.DEFAULT_MAX_TURNS
    assert fresh.limit_reason(0) is None
    for _ in range(autonomous.DEFAULT_MAX_TURNS):
        fresh.add_usage(None)
    assert fresh.turns_used == autonomous.DEFAULT_MAX_TURNS
    assert fresh.limit_reason(0) == "max_turns"
    assert fresh.continuation_text().startswith("[autonomous-continuation]\n\n")
    assert (
        autonomous.DEFAULT_AUTONOMOUS_CONTINUATION_PROMPT in fresh.continuation_text()
    )
    assert "subagent-keep-alive" in fresh.keep_alive_text()

    failure = autonomous.gate_failure_continuation(
        {"command": "pytest", "attempt": 2, "exitText": "exit 1", "output": "boom"},
        3,
        0,
    )
    assert "attempt 2/3" in failure
    assert "`pytest`" in failure
    row = autonomous.continuation_loop_row("keep going", 5)
    assert row["role"] == "user"
    assert row["timestamp"] == 5
    assert "keep going" in json.dumps(row)


@pytest.mark.native
def test_protocol_parse_and_encode_use_the_rust_frames():
    ext = _extension()
    protocol = ext.protocol
    assert protocol.REPL_PROTOCOL_VERSION == 3
    ready = {"event": "ready", "protocol": 3}
    line = json.dumps(ready)
    assert protocol.parse_event(line) == ready
    assert parse_kernel_event(line) == ready
    with pytest.raises(ValueError, match="unparseable"):
        protocol.parse_event("not-json")
    with pytest.raises(ValueError, match="without id"):
        protocol.parse_event('{"event":"host_request","data":{}}')

    display = protocol.parse_event(
        '{"event":"display","id":"1","data":{"mime":"text/plain"}}'
    )
    assert display["data"]["mime"] == "text/plain"
    done = protocol.parse_event('{"event":"done","id":"9","ok":true}')
    assert done["id"] == "9"
    assert done["fields"]["ok"] is True

    assert protocol.encode_request({"type": "execute", "code": "1+1"}) == {
        "type": "execute",
        "code": "1+1",
    }
    assert protocol.encode_request({"type": "shutdown"}) == {"type": "shutdown"}
    encoded = protocol.encode_request(
        {
            "type": "snapshot",
            "path": "/tmp/s",
            "manifestPath": "/tmp/m",
            "maxBytes": 10,
            "maxVariableBytes": 4,
            "pruneOversized": True,
        }
    )
    assert encoded == {
        "type": "snapshot",
        "path": "/tmp/s",
        "manifest_path": "/tmp/m",
        "max_bytes": 10,
        "max_variable_bytes": 4,
        "prune_oversized": True,
    }
    with pytest.raises(ValueError, match="unknown kernel request type"):
        protocol.encode_request({"type": "nope"})


@pytest.mark.native
def test_heartbeat_schedule_and_deferral():
    ext = _extension()
    heartbeat = ext.heartbeat
    assert heartbeat.normalize_schedule(None) == heartbeat.DEFAULT_HEARTBEAT_SCHEDULE
    assert heartbeat.normalize_schedule("10m") == "every 10m"
    parsed = heartbeat.parse_schedule("every 10m", 1_000_000)
    assert parsed["schedule"]["kind"] == "interval"
    assert parsed["schedule"]["intervalMs"] == 600_000
    assert parsed["nextRunAt"] == 1_600_000
    assert heartbeat.next_run(parsed["schedule"], 50) == 50 + 600_000
    assert heartbeat.next_run({"kind": "once", "expression": "at later"}, 10) is None
    with pytest.raises(ValueError, match="cannot be empty"):
        heartbeat.parse_schedule("  ", 0)
    with pytest.raises(ValueError, match="steer"):
        heartbeat.normalize_delivery_mode("nope")
    assert heartbeat.streaming_behavior(None) == "steer"
    assert heartbeat.streaming_behavior("follow_up") == "followUp"
    assert heartbeat.parse_command("pause")["command"] == "pause"
    assert heartbeat.parse_command("stop")["command"] == "clear"

    assert heartbeat.is_heartbeat(_job()) is True
    assert heartbeat.is_heartbeat(_job(source="manual")) is False
    assert heartbeat.should_defer(_job(), {"is_streaming": True}) is True
    assert (
        heartbeat.should_defer(_job(deliveryMode="steer"), {"is_streaming": True})
        is False
    )
    assert (
        heartbeat.should_defer(_job(deliveryMode="steer"), {"is_compacting": True})
        is True
    )
    assert (
        heartbeat.should_defer(_job(source="manual"), {"is_compacting": True}) is False
    )
    assert heartbeat.is_due(_job(), 2_000_000_000_000) is True
    assert heartbeat.is_due(_job(status="paused"), 2_000_000_000_000) is False
    assert "ping the worker" in heartbeat.format_job(_job())


@pytest.mark.native
def test_refinement_history_round_trip(tmp_path):
    ext = _extension()
    refinement = ext.refinement
    result = _refinement()
    assert refinement.infer_scope(result) == "global"
    bare = _refinement()
    del bare["scope"]
    assert refinement.infer_scope(bare) is None
    path = refinement.history_path(str(tmp_path))
    assert path.endswith(refinement.HISTORY_FILE_NAME)
    assert refinement.append(str(tmp_path), result) == path
    rows = refinement.load_history(str(tmp_path))
    assert rows[0]["id"] == "r1"
    assert rows[0]["scope"] == "global"


@pytest.mark.native
def test_goal_apply_uses_pa_core_transitions():
    ext = _extension()
    goal = ext.goals.Goal()
    assert goal.apply("")["text"] == "No active goal."
    started = goal.apply("--budget 10 ship the release")
    assert started["command"] == "start"
    assert started["text"] == "Goal active: ship the release"
    assert started["state"]["status"] == "active"
    assert started["state"]["objective"] == "ship the release"
    assert started["state"]["tokenBudget"] == 10
    assert started["state"]["active"] is True
    assert started["context"]["customType"] == "goal_context"
    assert "ship the release" in started["context"]["content"]
    assert goal.accrue(3, 4) == "accounted"
    assert goal.wire()["tokensUsed"] == 7
    assert goal.accrue(6, 0) == "budget_reached"
    limited = goal.wire()
    assert limited["status"] == "budget_limited"
    assert limited["active"] is False
    assert limited["tokensUsed"] == 13
    assert "Reached 10 token goal budget" in limited["lastReason"]

    paused_goal = ext.goals.Goal.from_wire(started["state"])
    paused = paused_goal.apply("pause")
    assert paused["text"] == "Goal paused: ship the release"
    assert paused["state"]["status"] == "paused"
    assert paused["state"]["lastReason"] == "Paused by user"
    resumed = paused_goal.apply("resume")
    assert resumed["state"]["status"] == "active"
    assert resumed["context"]["customType"] == "goal_context"
    cleared = paused_goal.apply("clear")
    assert cleared["cleared"] is True
    assert cleared["text"] == "Goal cleared."
    assert cleared["state"]["status"] == "idle"
    with pytest.raises(ValueError, match="positive integer"):
        ext.goals.Goal().apply("--budget 0 nope")


@pytest.mark.native
def test_slash_parsers_match_pa_core():
    ext = _extension()
    commands = ext.commands
    assert commands.parse_goal("PAUSE") == {"command": "pause"}
    assert commands.parse_goal("--budget 5000 keep going") == {
        "command": "start",
        "objective": "keep going",
        "tokenBudget": 5000,
    }
    parsed = commands.parse_autonomous("on --max-turns 5 --max-tokens 1,000")
    assert parsed["command"] == "on"
    assert parsed["config"]["maxTurns"] == 5
    assert parsed["config"]["maxTokens"] == 1000
    with pytest.raises(ValueError, match="Usage:"):
        commands.parse_autonomous("bogus")
    idle = ext.autonomous.AutonomousRun().status()
    text = commands.format_autonomous_status(idle)
    assert text.startswith("[autonomous-status: off]")
    assert "Turns: 0/12" in text
    assert "Tokens: 0/80,000" in text


@pytest.mark.native
def test_native_tool_validation_rejects_missing_required_argument():
    ext = _extension()
    with pytest.raises(ValueError, match="Validation failed"):
        ext.crates.agent_validate_arguments(
            "read_file",
            {"type": "object", "required": ["path"]},
            {},
        )


@pytest.mark.native
def test_kernel_tools_call_the_rust_commands(tmp_path):
    from omega_prime.prime_kernel.bound import apply_autonomous, apply_goal

    _extension()
    started = apply_goal(tmp_path, "ship it")
    assert started["state"]["objective"] == "ship it"
    again = apply_goal(tmp_path, "status")
    assert again["command"] == "status"
    assert again["state"]["goalId"] == started["state"]["goalId"]
    assert again["text"] == "Goal active: ship it"

    status = apply_autonomous(tmp_path, "status")
    assert status["command"] == "status"
    assert status["text"].startswith("[autonomous-status: off]")
    enabled = apply_autonomous(tmp_path, "on --max-turns 4")
    assert enabled["status"]["enabled"] is True
    assert enabled["status"]["limits"]["maxTurns"] == 4
    assert "Turns: 0/4" in enabled["text"]
    stopped = apply_autonomous(tmp_path, "off")
    assert stopped["status"]["enabled"] is False
    assert stopped["status"].get("startedAt") is None

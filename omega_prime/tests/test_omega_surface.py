# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Grok surface: served omega_command, onboard seed, and the agent intercept."""

from __future__ import annotations

import json
from pathlib import Path

from omega_prime.agent.model import ScriptedModel
from omega_prime.agent.runtime import OmegaPrimeAgent
from omega_prime.mcp_server import default_registry
from omega_prime.tools.omega_command import register_omega_command_tools
from omega_prime.tools.registry import ToolRegistry

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


def test_default_registry_serves_help(tmp_path: Path) -> None:
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    raw = registry.dispatch("omega_command", {"command": "/omega-help"})
    body = json.loads(raw)
    names = [row["name"] for row in body["result"]["commands"]]
    assert body["ok"] is True
    assert "omega-code-review" in names
    assert names.index("omega-desk") < names.index("omega-code-review")
    assert "omega-onboard" in names


def test_onboard_seeds_memory_even_when_doctor_needs_approval(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(state)},
    )
    onboard = json.loads(
        registry.dispatch("omega_command", {"command": "/omega-onboard"})
    )
    assert onboard["ok"] is False
    assert onboard["error"] == "workflow_incomplete"
    doctor = onboard["result"]["steps"][0]
    assert doctor["name"] == "lead_doctor"
    assert doctor["ok"] is False
    recall = json.loads(
        registry.dispatch("omega_command", {"command": "/omega-recall slash"})
    )
    assert recall["ok"] is True
    encoded = json.dumps(recall["result"])
    assert "omega_command" in encoded
    memory_file = OMEGA_PRIME / "memory" / "MEMORY.md"
    assert not memory_file.exists() or "slash command" not in memory_file.read_text(
        encoding="utf-8"
    )


def test_agent_runs_a_slash_command_without_the_model() -> None:
    class Boom:
        def complete(self, _messages: list, _tools: object = None) -> dict:
            raise AssertionError("model called")

    registry = ToolRegistry()
    register_omega_command_tools(registry, {})
    agent = OmegaPrimeAgent(Boom(), registry=registry)
    result = agent.run("/omega-help")
    body = json.loads(result["final_response"])
    assert result["turn_exit_reason"] == "omega_command"
    assert result["api_call_count"] == 0
    assert body["command"] == "omega-help"


def test_agent_runs_a_generated_command_without_the_model() -> None:
    class Boom:
        def complete(self, _messages: list, _tools: object = None) -> dict:
            raise AssertionError("model called")

    registry = ToolRegistry()
    register_omega_command_tools(registry, {})
    agent = OmegaPrimeAgent(Boom(), registry=registry)
    result = agent.run("/omega-code-review the parser change")
    body = json.loads(result["final_response"])
    assert result["turn_exit_reason"] == "omega_command"
    assert result["api_call_count"] == 0
    assert body["ok"] is True, body
    assert body["command"] == "omega-code-review"
    assert body["result"]["kind"] == "skill"
    assert body["result"]["args"] == "the parser change"


def test_prose_that_mentions_a_command_still_calls_the_model() -> None:
    registry = ToolRegistry()
    register_omega_command_tools(registry, {})
    model = ScriptedModel([{"role": "assistant", "content": "use /omega-help"}])
    agent = OmegaPrimeAgent(model, registry=registry)
    result = agent.run("please /omega-help")
    assert model.call_count == 1
    assert result["final_response"] == "use /omega-help"


def test_prose_that_mentions_a_generated_command_still_calls_the_model() -> None:
    registry = ToolRegistry()
    register_omega_command_tools(registry, {})
    model = ScriptedModel([{"role": "assistant", "content": "try /omega-code-review"}])
    agent = OmegaPrimeAgent(model, registry=registry)
    result = agent.run("should I run /omega-code-review here?")
    assert model.call_count == 1
    assert result["final_response"] == "try /omega-code-review"

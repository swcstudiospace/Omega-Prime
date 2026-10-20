# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``stage=`` reaches plan_router from every engine entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.delegate import delegate_task
from omega_prime.agent.model import ScriptedModel
from omega_prime.engine.attach import (
    delegate_via_engine,
    desk_engine_dispatch,
    engine_stage,
    route_turn,
    ultrathink_engine,
)
from omega_prime.engine.omp_driver import probe
from omega_prime.tests.test_engine import _env, _install_fake


def _chatgpt_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_OMP_ALLOW_CHATGPT="1", **extra)
    env.pop("ANTHROPIC_API_KEY")
    return env


def _model_arg(tmp_path: Path) -> str:
    """``--model`` of the latest omp launch the fake logged."""
    rows = [
        json.loads(line)
        for line in (tmp_path / "omp-log.jsonl").read_text("utf-8").splitlines()
    ]
    argv = [row["argv"] for row in rows if "--mode" in row.get("argv", [])][-1]
    return argv[argv.index("--model") + 1]


def test_engine_stage_prefers_explicit_then_env() -> None:
    assert engine_stage({}) is None
    assert engine_stage({"OMEGA_PRIME_ENGINE_STAGE": " "}) is None
    assert engine_stage({"OMEGA_PRIME_ENGINE_STAGE": "Plan"}) == "plan"
    assert engine_stage({"OMEGA_PRIME_ENGINE_STAGE": "plan"}, "execute") == "execute"
    # Unknown values pass through so the router refuses them by name.
    assert engine_stage({}, "ship") == "ship"


@pytest.mark.parametrize(
    ("stage", "model"),
    [("plan", "openai-codex/gpt-6.1-sol"), ("execute", "openai-codex/gpt-6-luna")],
)
def test_route_turn_passes_stage_to_the_router(
    tmp_path: Path, stage: str, model: str
) -> None:
    env = _chatgpt_env(tmp_path)
    agent = Agent(model=ScriptedModel([]))
    agent.force_engine = True
    routed = route_turn(
        agent,
        "ping",
        system_message=None,
        conversation_history=None,
        env=env,
        stage=stage,
    )
    assert routed is not None
    assert routed["final_response"] == "PONG"
    assert routed["engine"]["stage"] == stage
    assert routed["engine"]["fallback_from"] is None
    assert _model_arg(tmp_path) == model


def test_route_turn_reads_agent_stage_then_env(tmp_path: Path, monkeypatch) -> None:
    env = _chatgpt_env(tmp_path, OMEGA_PRIME_ENGINE_STAGE="plan")
    agent = Agent(model=ScriptedModel([]))
    agent.force_engine = True
    routed = route_turn(
        agent, "ping", system_message=None, conversation_history=None, env=env
    )
    assert routed is not None and routed["engine"]["stage"] == "plan"
    agent.engine_stage = "execute"
    routed = route_turn(
        agent, "ping", system_message=None, conversation_history=None, env=env
    )
    assert routed is not None and routed["engine"]["stage"] == "execute"
    assert _model_arg(tmp_path) == "openai-codex/gpt-6-luna"
    # The conversation loop path carries the agent's stage too.
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    looped = run_conversation(agent, "ping")
    assert looped["engine"]["stage"] == "execute"


def test_unstaged_turn_keeps_the_unstaged_router(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    agent = Agent(model=ScriptedModel([]))
    agent.force_engine = True
    routed = route_turn(
        agent, "ping", system_message=None, conversation_history=None, env=env
    )
    assert routed is not None
    assert routed["engine"]["stage"] is None
    assert routed["engine"]["plan"] == "anthropic"


def test_unknown_stage_is_refused_not_run_unstaged(tmp_path: Path) -> None:
    env = _chatgpt_env(tmp_path, OMEGA_PRIME_ENGINE="required")
    agent = Agent(model=ScriptedModel([]))
    agent.force_engine = True
    routed = route_turn(
        agent,
        "ping",
        system_message=None,
        conversation_history=None,
        env=env,
        stage="ship",
    )
    assert routed is not None
    assert routed["failed"] is True
    assert routed["engine"]["reason"] == "plan_not_allowed"
    assert "ship" in routed["engine"]["detail"]
    assert probe(env, stage="ship") == (False, "plan_not_allowed")


def test_probe_uses_the_stage_fallback_order(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    env.pop("ANTHROPIC_API_KEY")
    assert probe(env, stage="plan") == (True, "ok")
    gated = _env(tmp_path, binary)
    gated.pop("ANTHROPIC_API_KEY")
    # Anthropic is not in the stage order, so a staged probe must refuse.
    keyed = _env(tmp_path, binary)
    assert probe(keyed) == (True, "ok")
    assert probe(keyed, stage="execute") == (False, "plan_not_allowed")
    assert probe(gated, stage="execute") == (False, "plan_not_allowed")


def test_delegate_and_desk_carry_the_stage(tmp_path: Path) -> None:
    env = _chatgpt_env(tmp_path)
    staged = delegate_via_engine("say PONG", env=env, stage="execute")
    assert staged is not None
    raw = json.loads(staged)
    assert raw["summary"] == "PONG"
    assert (raw["stage"], raw["model"]) == ("execute", "gpt-6-luna")
    desk = desk_engine_dispatch("ticket", env, cwd=str(tmp_path), stage="plan")
    assert desk is not None and desk["status"] == "done"
    assert _model_arg(tmp_path) == "openai-codex/gpt-6.1-sol"
    plain = delegate_via_engine(
        "say PONG", env=_env(tmp_path, tmp_path / "bin" / "omp")
    )
    assert plain is not None
    assert "stage" not in json.loads(plain)


def test_staged_parent_delegates_to_the_execute_stage(
    tmp_path: Path, monkeypatch
) -> None:
    env = _chatgpt_env(tmp_path, OMEGA_PRIME_ENGINE="auto")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class Parent:
        def __init__(self, stage: str | None) -> None:
            self.child_model = None
            self.delegate_depth = 0
            self.tools: dict = {}
            self.engine_stage = stage

    raw = json.loads(delegate_task(Parent("plan"), "say PONG"))
    assert (raw["stage"], raw["model"]) == ("execute", "gpt-6-luna")
    monkeypatch.setenv("OMEGA_PRIME_ENGINE_STAGE", "plan")
    raw = json.loads(delegate_task(Parent(None), "say PONG"))
    assert raw["stage"] == "plan"


def test_ultrathink_stage_from_argument_plan_or_env(tmp_path: Path) -> None:
    env = _chatgpt_env(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("Do the small thing.\n", encoding="utf-8")
    plan = {"found": True, "spec_path": str(spec), "stage": "plan"}
    body = ultrathink_engine(plan, env)
    assert body["used"] is True
    assert (body["stage"], body["model"]) == ("plan", "gpt-6.1-sol")
    body = ultrathink_engine(plan, env, stage="execute")
    assert (body["stage"], body["model"]) == ("execute", "gpt-6-luna")
    body = ultrathink_engine(
        {"found": True, "spec_path": str(spec)},
        {**env, "OMEGA_PRIME_ENGINE_STAGE": "execute"},
    )
    assert body["stage"] == "execute"


def test_zai_has_its_own_plan_and_build_pair() -> None:
    from omega_prime.engine.plan_router import model_id, select

    zai = {"OMEGA_PRIME_OMP_ALLOW_ZAI": "1"}
    assert select(zai, stage="plan").decision.model_id == "glm-5.3"
    assert select(zai, stage="execute").decision.model_id == "glm-5.3-flash"
    # Unstaged Z.ai keeps the single default model.
    assert select(zai, requested="zai").decision.model_id == "glm-5.3"
    assert model_id("zai", {}) == "glm-5.3"
    # The ChatGPT pair is unchanged, and Z.ai is still its stage fallback.
    both = {**zai, "OMEGA_PRIME_OMP_ALLOW_CHATGPT": "1"}
    assert select(both, stage="plan").decision.model_id == "gpt-6.1-sol"
    assert select(both, stage="execute").decision.model_id == "gpt-6-luna"
    exhausted = {"chatgpt": _quota(0.0)}
    build = select(both, exhausted, stage="execute")
    assert (build.decision.plan, build.decision.model_id) == ("zai", "glm-5.3-flash")
    assert build.fallback_from == "chatgpt"
    # Z.ai can be the primary for a job, not only the fallback.
    picked = select(both, stage="execute", requested="zai")
    assert (picked.decision.model_id, picked.fallback_from) == ("glm-5.3-flash", None)
    # Overrides still win per stage, then per plan.
    staged = {**zai, "OMEGA_PRIME_OMP_MODEL_ZAI_EXECUTE": "glm-5.2-flash"}
    assert select(staged, stage="execute").decision.model_id == "glm-5.2-flash"
    whole = {**zai, "OMEGA_PRIME_OMP_MODEL_ZAI": "glm-5.4"}
    assert select(whole, stage="execute").decision.model_id == "glm-5.4"


def _quota(remaining: float):
    from omega_prime.engine.plan_router import Quota

    return Quota(remaining, None, None, "5h", None, "live")


def test_zai_stage_runs_build_on_flash(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_OMP_ALLOW_ZAI="1")
    env.pop("ANTHROPIC_API_KEY")
    agent = Agent(model=ScriptedModel([]))
    agent.force_engine = True
    routed = route_turn(
        agent,
        "ping",
        system_message=None,
        conversation_history=None,
        env=env,
        stage="execute",
    )
    assert routed is not None
    assert routed["engine"]["model"] == "glm-5.3-flash"
    assert routed["engine"]["fallback_from"] == "chatgpt"
    assert _model_arg(tmp_path) == "zai/glm-5.3-flash"

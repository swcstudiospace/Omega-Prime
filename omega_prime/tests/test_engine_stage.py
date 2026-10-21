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


def test_child_stage_normalises_and_refuses_unknown() -> None:
    from omega_prime.agent.delegate import _child_stage

    class P:
        def __init__(self, stage: object) -> None:
            self.engine_stage = stage

    assert _child_stage(P(None)) is None
    assert _child_stage(P("  ")) is None
    assert _child_stage(P(" PLAN ")) == "execute"
    assert _child_stage(P("execute")) == "execute"
    # Unknown values are not silently turned into a build.
    assert _child_stage(P("ship")) == "ship"


def test_staged_parent_with_child_model_builds_on_the_engine_first(
    tmp_path: Path, monkeypatch
) -> None:
    env = _chatgpt_env(tmp_path, OMEGA_PRIME_ENGINE="auto")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    child = ScriptedModel([{"role": "assistant", "content": "from-python-child"}])

    class Parent:
        def __init__(self, stage: str | None) -> None:
            self.child_model = child
            self.delegate_depth = 0
            self.tools: dict = {}
            self.engine_stage = stage

    raw = json.loads(delegate_task(Parent("plan"), "say PONG"))
    assert (raw["summary"], raw["stage"], raw["model"]) == (
        "PONG",
        "execute",
        "gpt-6-luna",
    )
    assert child.call_count == 0
    # Unstaged parents keep their configured Python child.
    raw = json.loads(delegate_task(Parent(None), "say hi"))
    assert raw["summary"] == "from-python-child"
    # A staged parent whose engine is off still falls back to the child.
    monkeypatch.setenv("OMEGA_PRIME_ENGINE", "off")
    child2 = ScriptedModel([{"role": "assistant", "content": "child-again"}])
    parent = Parent("plan")
    parent.child_model = child2
    assert json.loads(delegate_task(parent, "say hi"))["summary"] == "child-again"


def test_mcp_desk_parent_reads_the_stage_from_its_env(monkeypatch) -> None:
    from omega_prime.mcp_server import _DeskParent
    from omega_prime.tools.registry import ToolRegistry

    monkeypatch.delenv("OMEGA_PRIME_ENGINE_STAGE", raising=False)
    assert _DeskParent(ToolRegistry(), None).engine_stage is None
    monkeypatch.setenv("OMEGA_PRIME_ENGINE_STAGE", "plan")
    assert _DeskParent(ToolRegistry(), None).engine_stage == "plan"


def test_real_desk_parent_routes_a_staged_delegate_to_the_execute_engine(
    tmp_path: Path, monkeypatch
) -> None:
    """The MCP server's own parent, with a full live registry, not a stub."""
    from omega_prime.mcp_server import _DeskParent, _RlmParent
    from omega_prime.tools.delegate import register_delegate_tools
    from omega_prime.tools.registry import ToolRegistry

    env = _chatgpt_env(tmp_path, OMEGA_PRIME_ENGINE="auto")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    registry = ToolRegistry()
    registry.register(
        "noop", "no-op", {"type": "object", "properties": {}}, lambda: "ok"
    )
    child = ScriptedModel([{"role": "assistant", "content": "from-python-child"}])
    parent = _DeskParent(registry, child, {"OMEGA_PRIME_ENGINE_STAGE": "plan"})
    register_delegate_tools(registry, parent)
    assert parent.tools, "the desk parent maps the live registry"
    raw = json.loads(registry.dispatch("delegate_task", {"goal": "say PONG"}))
    assert (raw["summary"], raw["stage"], raw["model"]) == (
        "PONG",
        "execute",
        "gpt-6-luna",
    )
    assert child.call_count == 0
    # RLM children keep the desk child model even when staged.
    rlm = _RlmParent(registry, child, tmp_path / "rlm")
    monkeypatch.setenv("OMEGA_PRIME_ENGINE_STAGE", "plan")
    assert rlm.engine_full_tools is False


def test_staged_mcp_server_has_a_delegate_parent_without_a_child_provider(
    tmp_path: Path,
) -> None:
    from omega_prime.mcp_server import default_registry
    from omega_prime.tests.test_desk_wiring import ROOT
    from omega_prime.tools.delegate import DELEGATE_PARENT_BINDING

    base = {"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")}
    bare = default_registry(ROOT, tmp_path / "home", env=base)
    assert bare.runtime_bindings[DELEGATE_PARENT_BINDING] is None
    staged = default_registry(
        ROOT, tmp_path / "home", env={**base, "OMEGA_PRIME_ENGINE_STAGE": "plan"}
    )
    parent = staged.runtime_bindings[DELEGATE_PARENT_BINDING]
    assert parent is not None and parent.child_model is None
    assert parent.engine_stage == "plan"


def test_staged_omp_process_env_names_the_stage(tmp_path: Path) -> None:
    import stat as _stat

    env = _chatgpt_env(tmp_path)
    fake = Path(env["OMEGA_PRIME_OMP_BIN"])
    wrapper = tmp_path / "bin" / "omp-env"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "${{OMEGA_PRIME_ENGINE_STAGE:-none}}" >> {tmp_path}/stage.log\n'
        f'exec {fake} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | _stat.S_IEXEC)
    env["OMEGA_PRIME_OMP_BIN"] = str(wrapper)
    from omega_prime.engine.omp_driver import run

    assert run("PONG", cwd=tmp_path, env=env, stage="execute").ok
    assert run("PONG", cwd=tmp_path, env=env).ok
    seen = (tmp_path / "stage.log").read_text("utf-8").split()
    # --version and usage probes run unstaged; the RPC process carries the stage.
    assert "execute" in seen
    assert seen[-1] == "none"


def test_execute_ultrathink_keeps_a_saved_plan_stage(tmp_path: Path) -> None:
    from omega_prime.routines.ultrathink_turn import execute_ultrathink

    env = _chatgpt_env(tmp_path, OMEGA_PRIME_ENGINE="auto")
    spec = tmp_path / "spec.md"
    spec.write_text("Do the small thing.\n", encoding="utf-8")
    plan_dir = tmp_path / "state" / "ultrathink"
    plan_dir.mkdir(parents=True)
    (plan_dir / "last-plan.json").write_text(
        json.dumps({"specPath": str(spec), "stage": "execute"}), encoding="utf-8"
    )
    turn = execute_ultrathink(env)
    assert turn["found"] is True
    assert turn["engine"]["used"] is True
    assert (turn["engine"]["stage"], turn["engine"]["model"]) == (
        "execute",
        "gpt-6-luna",
    )


def test_probe_checks_the_requested_plan_for_a_stage(tmp_path: Path) -> None:
    """``probe`` asks the router the same question ``run(plan=..., stage=...)`` will."""
    from omega_prime.engine import omp_driver

    asked: list[tuple[str | None, str | None]] = []

    def fake_select(env, usage=None, *, requested=None, stage=None, fallback=None):
        asked.append((requested, stage))
        raise omp_driver.PlanNotAllowed("stop here")

    env = _chatgpt_env(tmp_path)
    original = omp_driver.select
    omp_driver.select = fake_select
    try:
        assert probe(env, stage="execute", plan="anthropic") == (
            False,
            "plan_not_allowed",
        )
        assert probe(env, plan="zai") == (False, "plan_not_allowed")
        assert probe(env, stage="plan") == (False, "plan_not_allowed")
    finally:
        omp_driver.select = original
    assert asked == [("anthropic", "execute"), ("zai", None), (None, "plan")]


def test_route_turn_probes_with_the_agents_plan(tmp_path: Path, monkeypatch) -> None:
    from omega_prime.engine import attach

    seen: list[dict] = []

    def fake_probe(env, **kwargs):
        seen.append(kwargs)
        return False, "plan_not_allowed"

    monkeypatch.setattr(attach, "probe", fake_probe)
    monkeypatch.setattr(attach, "_live_model", lambda model: True)
    agent = Agent(model=ScriptedModel([]))
    agent.engine_plan = "anthropic"  # type: ignore[attr-defined]
    env = {**_chatgpt_env(tmp_path), "OMEGA_PRIME_ENGINE": "auto"}
    assert (
        route_turn(
            agent,
            "hi",
            system_message=None,
            conversation_history=None,
            env=env,
            stage="execute",
        )
        is None
    )
    assert seen == [{"stage": "execute", "plan": "anthropic"}]


def test_engine_delegate_carries_depth_and_work_root(
    tmp_path: Path, monkeypatch
) -> None:
    from omega_prime.engine import attach

    calls: list[dict] = []

    def fake_delegate(goal, **kwargs):
        calls.append(kwargs)
        return json.dumps({"summary": "ok"})

    monkeypatch.setattr(attach, "delegate_via_engine", fake_delegate)

    class Parent:
        def __init__(self, depth: int) -> None:
            self.child_model = None
            self.tools: dict = {}
            self.engine_stage = "plan"
            self.delegate_depth = depth
            self.engine_cwd = tmp_path

    assert json.loads(delegate_task(Parent(1), "build it"))["summary"] == "ok"
    assert calls[0]["delegate_depth"] == 2
    assert calls[0]["cwd"] == str(tmp_path)
    assert calls[0]["stage"] == "execute"
    refused = json.loads(delegate_task(Parent(2), "build it"))
    assert "max_depth" in refused["error"] and len(calls) == 1


def test_desk_parent_starts_at_the_engine_childs_depth_in_its_work_root(
    tmp_path: Path, monkeypatch
) -> None:
    from omega_prime.mcp_server import _DeskParent
    from omega_prime.tools.registry import ToolRegistry

    monkeypatch.delenv("OMEGA_PRIME_DELEGATE_DEPTH", raising=False)
    top = _DeskParent(ToolRegistry(), None, {})
    assert (top.delegate_depth, top.engine_cwd) == (0, None)
    nested = _DeskParent(
        ToolRegistry(), None, {"OMEGA_PRIME_DELEGATE_DEPTH": "2"}, work=tmp_path
    )
    assert (nested.delegate_depth, nested.engine_cwd) == (2, str(tmp_path))
    raw = json.loads(delegate_task(nested, "go deeper", max_depth=nested.max_depth))
    assert "max_depth" in raw["error"]
    # An unreadable depth fails closed instead of resetting to zero.
    bad = _DeskParent(ToolRegistry(), None, {"OMEGA_PRIME_DELEGATE_DEPTH": "x"})
    assert bad.delegate_depth >= bad.max_depth


def test_mcp_server_desk_parent_uses_the_resolved_work_root(tmp_path: Path) -> None:
    from omega_prime.mcp_server import default_registry
    from omega_prime.tests.test_desk_wiring import ROOT
    from omega_prime.tools.delegate import DELEGATE_PARENT_BINDING

    work = tmp_path / "work"
    work.mkdir()
    env = {
        "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
        "OMEGA_PRIME_ENGINE_STAGE": "plan",
    }
    staged = default_registry(ROOT, tmp_path / "home", env=env, work_root=work)
    parent = staged.runtime_bindings[DELEGATE_PARENT_BINDING]
    assert parent is not None and parent.engine_cwd == str(work)


def test_omp_process_env_names_the_delegate_depth(tmp_path: Path) -> None:
    import stat as _stat

    env = _chatgpt_env(tmp_path)
    fake = Path(env["OMEGA_PRIME_OMP_BIN"])
    wrapper = tmp_path / "bin" / "omp-depth"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "${{OMEGA_PRIME_DELEGATE_DEPTH:-none}}" >> {tmp_path}/depth.log\n'
        f'exec {fake} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | _stat.S_IEXEC)
    env["OMEGA_PRIME_OMP_BIN"] = str(wrapper)
    from omega_prime.engine.omp_driver import run

    assert run("PONG", cwd=tmp_path, env=env, delegate_depth=2).ok
    assert "2" in (tmp_path / "depth.log").read_text("utf-8").split()

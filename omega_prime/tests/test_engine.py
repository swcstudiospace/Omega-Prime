# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic omp engine tests. The fake binary never opens a network socket."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.delegate import delegate_task
from omega_prime.agent.model import ScriptedModel
from omega_prime.agent.runtime import OmegaPrimeAgent
from omega_prime.engine.jobs import maybe_tmux
from omega_prime.engine.omp_driver import OmpSession, run, session_abort, session_steer
from omega_prime.engine.plan_router import (
    PlanNotAllowed,
    evaluate_all,
    parse_usage,
    select,
)
from omega_prime.engine.protocol import redact
from omega_prime.engine.watch import main as watch_main
from omega_prime.providers.base import ProviderModel
from omega_prime.providers.grok import GrokProvider
from omega_prime.routines.desk_lead import make_dispatch
from omega_prime.routines.ultrathink_turn import execute_ultrathink
from omega_prime.tools.capacity import register_capacity_tools
from omega_prime.tools.lead import IntakeStore
from omega_prime.tools.registry import ToolRegistry

_FAKE = """#!/usr/bin/env python3
import json, os, sys

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

def log(row):
    path = os.environ.get("OMEGA_FAKE_OMP_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\\n")

args = sys.argv[1:]
log({"argv": args, "cwd": os.getcwd()})
if "--version" in args:
    print(os.environ.get("OMEGA_FAKE_OMP_VERSION", "18.8.4"))
    raise SystemExit(0)
if "usage" in args:
    print(os.environ.get("OMEGA_FAKE_OMP_USAGE", '{"providers":[]}'))
    raise SystemExit(0)
emit({"type": "ready"})
for line in sys.stdin:
    text = line.strip()
    if not text:
        continue
    cmd = json.loads(text)
    log({"cmd": cmd.get("type")})
    kind = cmd.get("type")
    cid = cmd.get("id")
    if kind == "prompt":
        reply = os.environ.get("OMEGA_FAKE_OMP_REPLY", "PONG")
        emit({"id": cid, "type": "response", "command": "prompt", "success": True})
        emit({"type": "agent_start"})
        emit({"type": "turn_start"})
        emit({"type": "message_update", "delta": {"text": reply}})
        emit({"type": "tool_execution_start", "toolName": "read", "toolCallId": "t1"})
        emit({"type": "tool_execution_update", "toolName": "read", "toolCallId": "t1"})
        emit({"type": "tool_execution_end", "toolName": "read", "toolCallId": "t1"})
        end = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": reply}],
                "usage": {"input": 10, "output": 2},
                "cost": 0.0001,
            },
        }
        leaked = os.environ.get("OMEGA_FAKE_OMP_API_KEY", "")
        if leaked:
            end["apiKey"] = leaked
        emit(end)
        emit({"type": "turn_end"})
        emit({"type": "agent_end"})
    elif kind == "get_session_stats":
        emit({
            "id": cid,
            "type": "response",
            "command": kind,
            "success": True,
            "data": {"turns": 1},
        })
    else:
        emit({"id": cid, "type": "response", "command": kind, "success": True})
"""

_SECRET = "super-secret-key-value"


def _shaped_token() -> str:
    """A credential-shaped value built at runtime, not stored as a literal."""
    return "".join(("s", "k", "-", "should", "-", "not", "-", "leak"))


def _install_fake(tmp_path: Path) -> Path:
    binary = tmp_path / "bin" / "omp"
    binary.parent.mkdir()
    binary.write_text(_FAKE, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def _env(tmp_path: Path, binary: Path, **extra: str) -> dict[str, str]:
    base = {
        "PATH": str(binary.parent) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
        "OMEGA_PRIME_OMP_BIN": str(binary),
        "OMEGA_PRIME_ENGINE_UNSANDBOXED": "1",
        "OMEGA_PRIME_ENGINE_TMUX": "0",
        "OMEGA_FAKE_OMP_LOG": str(tmp_path / "omp-log.jsonl"),
        "OMEGA_FAKE_OMP_REPLY": "PONG",
        "OMEGA_FAKE_OMP_USAGE": json.dumps(
            {
                "providers": [
                    {"provider": "anthropic", "remaining": 80, "window": "month"},
                    {"provider": "xai", "remaining": 10, "window": "week"},
                ]
            }
        ),
        "ANTHROPIC_API_KEY": _SECRET,
    }
    base.update(extra)
    return base


def _events(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_default_allowlist_is_empty_and_keys_do_not_unlock_subscriptions() -> None:
    empty = {item.plan: item for item in evaluate_all({})}
    assert all(item.allowlisted is False for item in empty.values())
    zai = {item.plan: item for item in evaluate_all({"ZAI_API_KEY": _SECRET})}
    assert zai["zai"].allowlisted is False
    opted = {
        item.plan: item for item in evaluate_all({"OMEGA_PRIME_OMP_ALLOW_ZAI": "1"})
    }
    assert opted["zai"].allowlisted is True
    assert opted["zai"].auth == "subscription_opt_in"
    anthropic = {
        item.plan: item
        for item in evaluate_all({"OMEGA_PRIME_OMP_ALLOW_ANTHROPIC": "1"})
    }
    assert anthropic["anthropic"].allowlisted is False
    assert anthropic["anthropic"].verdict == "not_allowed"
    muse = {
        item.plan: item for item in evaluate_all({"OMEGA_PRIME_OMP_ALLOW_MUSE": "1"})
    }
    assert muse["muse"].allowlisted is False
    token = {
        item.plan: item for item in evaluate_all({"ANTHROPIC_AUTH_TOKEN": _SECRET})
    }
    assert token["anthropic"].allowlisted is False


def test_api_keys_allow_their_profiles_and_router_picks_remaining_quota() -> None:
    env = {
        "ANTHROPIC_API_KEY": _SECRET,
        "XAI_API_KEY": _SECRET,
        "META_API_KEY": _SECRET,
        "CURSOR_API_KEY": _SECRET,
    }
    chosen = {item.plan: item for item in evaluate_all(env)}
    assert chosen["anthropic"].auth == "api_key"
    assert chosen["xai"].auth == "api_key"
    assert chosen["muse"].auth == "api_key"
    assert chosen["cursor"].auth == "api_key"
    usage = parse_usage(
        {
            "providers": {
                "anthropic": {"remaining": 5, "window": "month"},
                "xai": {"windows": [{"remaining": 40, "type": "week"}]},
            }
        }
    )
    pick = select(env, usage)
    assert pick.decision.plan == "xai"
    assert pick.quota is not None and pick.quota.remaining == 40
    try:
        select({}, requested="anthropic")
    except PlanNotAllowed as exc:
        assert "not allowed" in exc.detail
    else:
        raise AssertionError("expected PlanNotAllowed")


def test_fake_omp_session_writes_events_mcp_and_commands(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_FAKE_OMP_API_KEY=_shaped_token())
    result = run("Reply with PONG", cwd=tmp_path, env=env, job_id="job-alpha")
    assert result.ok is True
    assert result.final_text == "PONG"
    assert result.plan == "anthropic"
    assert result.usage.get("input") == 10
    assert result.cost == 0.0001
    assert result.commands == [
        "set_event_filter",
        "set_model",
        "set_thinking_level",
        "prompt",
        "get_session_stats",
    ]
    assert result.stats.get("turns") == 1
    kinds = [row["type"] for row in _events(Path(result.events_path))]
    for name in (
        "ready",
        "agent_start",
        "turn_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "agent_end",
    ):
        assert name in kinds
    blob = Path(result.events_path).read_text(encoding="utf-8")
    assert _shaped_token() not in blob
    assert "[redacted]" in blob
    profile = tmp_path / "state" / "jobs" / "job-alpha" / "profile" / "mcp.json"
    mcp = json.loads(profile.read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["omega-prime"]
    assert server["command"] == sys.executable
    from omega_prime.engine.mcp_bridge import install_root

    assert server["args"] == [
        "-m",
        "omega_prime.mcp_server",
        "--root",
        str(install_root()),
        "--work-root",
        str(Path(tmp_path).resolve()),
    ]
    assert server["type"] == "stdio"
    logged = [
        json.loads(line)
        for line in (tmp_path / "omp-log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    argv = next(row["argv"] for row in logged if "--mode" in row.get("argv", []))
    assert "--mode" in argv and "rpc" in argv
    assert "--skills" in argv
    skills = argv[argv.index("--skills") + 1]
    assert skills.endswith("omega_prime/skills")
    commands = [row.get("cmd") for row in logged if "cmd" in row]
    assert commands == result.commands
    code = watch_main(["job-alpha", "--state-dir", str(tmp_path / "state")])
    assert code == 0


def test_sandbox_refuses_without_bwrap_and_wraps_when_present(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_ENGINE_UNSANDBOXED="0")
    refused = run("PONG", cwd=tmp_path, env=env, job_id="job-bare")
    assert refused.ok is False
    assert refused.reason == "sandbox_unavailable"
    bwrap = tmp_path / "bin" / "bwrap"
    bwrap.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$OMEGA_FAKE_OMP_BWRAP_LOG"\n'
        'while [ "$1" != "--" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    bwrap.chmod(bwrap.stat().st_mode | stat.S_IEXEC)
    wrapped_env = _env(
        tmp_path,
        binary,
        OMEGA_PRIME_ENGINE_UNSANDBOXED="0",
        OMEGA_FAKE_OMP_BWRAP_LOG=str(tmp_path / "bwrap.txt"),
    )
    result = run("PONG", cwd=tmp_path, env=wrapped_env, job_id="job-bwrap")
    assert result.ok is True
    assert result.sandbox == "bwrap"
    logged = (tmp_path / "bwrap.txt").read_text(encoding="utf-8")
    assert "--ro-bind" in logged
    assert "--unshare-pid" in logged


def test_major_other_than_18_is_refused(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_FAKE_OMP_VERSION="19.0.0")
    result = run("PONG", cwd=tmp_path, env=env, job_id="job-ver")
    assert result.ok is False
    assert result.reason == "version_unsupported"


def test_steer_and_abort_round_trip(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    events = tmp_path / "events.jsonl"
    proc = subprocess.Popen(
        [str(binary), "--mode", "rpc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": env["PATH"],
            "OMEGA_FAKE_OMP_LOG": env["OMEGA_FAKE_OMP_LOG"],
            "OMEGA_FAKE_OMP_REPLY": "PONG",
        },
    )
    session = OmpSession(proc, events)
    try:
        session.wait_type("ready", 5)
        steered = session_steer(session, "continue")
        aborted = session_abort(session)
    finally:
        session.close()
    assert steered.get("success") is True
    assert aborted.get("command") == "abort"
    kinds = [row["type"] for row in _events(events)]
    assert "ready" in kinds


def test_loops_prefer_the_fake_engine(tmp_path, monkeypatch) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_ENGINE="auto")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    model = ScriptedModel([{"role": "assistant", "content": "from-python"}])
    plain = Agent(model=model)
    fallback = run_conversation(plain, "ping")
    assert fallback["final_response"] == "from-python"
    assert model.call_count == 1

    forced = Agent(model=model)
    forced.force_engine = True
    routed = run_conversation(forced, "ping")
    assert routed["final_response"] == "PONG"
    assert routed["turn_exit_reason"] == "text_response(finish_reason=engine)"
    assert routed["api_calls"] == 1
    assert routed["completed"] is True
    assert model.call_count == 1

    planned_model = ScriptedModel([{"role": "assistant", "content": "from-python"}])
    planned = Agent(
        model=planned_model,
        plan_mode=True,
        tools={"read_file": lambda **kwargs: "ok"},
        force_engine=True,
    )
    kept = run_conversation(planned, "ping")
    assert kept["final_response"] == "from-python"
    assert planned_model.call_count == 1

    class Boom:
        """Transport that fails if the Python provider path is taken."""

        def post(self, url: str, headers: dict, body: dict) -> dict:
            raise AssertionError("provider HTTP was called")

        def get(self, url: str, headers: dict, params: dict) -> dict:
            raise AssertionError("provider HTTP was called")

        def stream(self, url: str, headers: dict, body: dict) -> Iterator[str]:
            raise AssertionError("provider HTTP was called")

    provider = ProviderModel(GrokProvider(), "grok-4", Boom(), api_key="k")
    prime = OmegaPrimeAgent(provider, registry=ToolRegistry())
    coded = prime.run("ping")
    assert coded["final_response"] == "PONG"
    assert coded["turn_exit_reason"] == "text_response(finish_reason=engine)"
    assert coded["api_calls"] == 1
    assert coded["completed"] is True

    class Parent:
        def __init__(self) -> None:
            self.child_model = None
            self.delegate_depth = 0
            self.tools: dict = {}

    raw = json.loads(delegate_task(Parent(), "say PONG"))
    assert raw["summary"] == "PONG"
    assert raw["engine"] == "omp"

    intake = IntakeStore(tmp_path / "intake.json")
    intake.submit("fix the widget")
    from omega_prime.routines.desk_lead import run_lead_pass

    outcome = run_lead_pass(intake, make_dispatch(None, tmp_path, env=env))
    assert outcome["tickets"][0]["status"] == "done"
    assert outcome["invalid"] == []
    assert "PONG" in outcome["receipts"][0]["receipt"]["commands"][0]["output_tail"]

    spec = tmp_path / "spec.md"
    spec.write_text("Do the small thing.\n", encoding="utf-8")
    plan_dir = tmp_path / "state" / "ultrathink"
    plan_dir.mkdir(parents=True)
    (plan_dir / "last-plan.json").write_text(
        json.dumps({"specPath": str(spec)}), encoding="utf-8"
    )
    os.utime(plan_dir / "last-plan.json", None)
    turn = execute_ultrathink(env)
    assert turn["found"] is True
    assert turn["engine"]["used"] is True
    assert turn["engine"]["final_text"] == "PONG"
    assert turn["publishes"] is False


def test_omega_capacity_hides_secrets(tmp_path, monkeypatch) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    registry = ToolRegistry()
    register_capacity_tools(registry)
    body = json.loads(registry.dispatch("omega_capacity", {}))
    encoded = json.dumps(body)
    assert _SECRET not in encoded
    assert body["selected"]["plan"] == "anthropic"
    assert body["allowlist"]["refused_subscription_oauth"] == ["anthropic", "muse"]
    assert body["fleet"]["status"] == "not_configured"
    anthropic = next(item for item in body["plans"] if item["plan"] == "anthropic")
    assert anthropic["allowlisted"] is True
    assert anthropic["remaining"] == 80
    zai = next(item for item in body["plans"] if item["plan"] == "zai")
    assert zai["allowlisted"] is False


def test_redact_replaces_secret_fields() -> None:
    cleaned = redact({"apiKey": _SECRET, "note": "ok", "nested": [{"token": "x"}]})
    assert cleaned["apiKey"] == "[redacted]"
    assert cleaned["note"] == "ok"
    assert cleaned["nested"][0]["token"] == "[redacted]"
    assert "super-secret" not in json.dumps(cleaned)
    embedded = redact({"note": "api_key=" + _SECRET})
    assert _SECRET not in json.dumps(embedded)
    assert embedded["note"].startswith("api_key=")


def test_percent_only_window_prefers_the_open_plan() -> None:
    usage = {
        "anthropic": parse_usage(
            {"providers": [{"provider": "anthropic", "percentUsed": 100}]}
        )["anthropic"],
        "xai": parse_usage({"providers": [{"provider": "xai", "percentUsed": 10}]})[
            "xai"
        ],
    }
    choice = select(
        {"ANTHROPIC_API_KEY": "present", "XAI_API_KEY": "present"},
        usage,
    )
    assert choice.decision.plan == "xai"


def test_tmux_session_name(tmp_path: Path) -> None:
    tmux = tmp_path / "bin" / "tmux"
    tmux.parent.mkdir()
    tmux.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" >> "$OMEGA_FAKE_OMP_TMUX_LOG"\nexit 1\n',
        encoding="utf-8",
    )
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "tmux.txt"
    env = {
        "PATH": str(tmux.parent),
        "OMEGA_PRIME_ENGINE_TMUX": "1",
        "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
        "OMEGA_FAKE_OMP_TMUX_LOG": str(log),
    }
    # has-session exits 1, then new-session also exits 1, so the helper
    # reports None. The argv still shows the session name. Use a tmux that
    # succeeds on new-session.
    tmux.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" >> "$OMEGA_FAKE_OMP_TMUX_LOG"\n'
        'if [ "$1" = "has-session" ]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    session = maybe_tmux("job-watch", env, enabled=True)
    assert session == "omega-job-watch"
    text = log.read_text(encoding="utf-8")
    assert "omega-job-watch" in text
    assert "omega_prime.engine.watch" in text

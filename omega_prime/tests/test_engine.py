# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic omp engine tests. The fake binary never opens a network socket."""

from __future__ import annotations

import io
import json
import os
import queue
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from omega_prime.agent.budget import IterationBudget
from omega_prime.agent.conversation_loop import (
    Agent,
    _seal_engine_turn,
    run_conversation,
)
from omega_prime.agent.delegate import delegate_task
from omega_prime.agent.interrupt import InterruptFlag
from omega_prime.agent.model import ScriptedModel
from omega_prime.agent.runtime import OmegaPrimeAgent
from omega_prime.engine.attach import _PROMPT_LIMIT, _TRANSCRIPT_CHARS, _with_history
from omega_prime.engine.jobs import append_event, maybe_tmux
from omega_prime.engine.omp_driver import (
    EngineError,
    OmpSession,
    run,
    session_abort,
    session_steer,
)
from omega_prime.engine.plan_router import (
    PlanNotAllowed,
    evaluate_all,
    parse_usage,
    select,
)
from omega_prime.engine.protocol import redact
from omega_prime.engine.watch import main as watch_main
from omega_prime.engine.watch import tail
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
log({"argv": args, "cwd": os.getcwd(), "agent_dir": os.environ.get("PI_CODING_AGENT_DIR")})
if "--version" in args:
    print(os.environ.get("OMEGA_FAKE_OMP_VERSION", "18.8.4"))
    raise SystemExit(0)
if "usage" in args:
    print(os.environ.get("OMEGA_FAKE_OMP_USAGE", '{"providers":[]}'))
    raise SystemExit(0)
emit({"type": "ready"})
if os.environ.get("OMEGA_FAKE_OMP_EXIT_AFTER_READY") == "1":
    raise SystemExit(0)
for line in sys.stdin:
    text = line.strip()
    if not text:
        continue
    cmd = json.loads(text)
    log({"cmd": cmd.get("type")})
    kind = cmd.get("type")
    cid = cmd.get("id")
    if kind == "prompt":
        if os.environ.get("OMEGA_FAKE_OMP_BLOCK_PROMPT") == "1":
            for abort_line in sys.stdin:
                abort_cmd = json.loads(abort_line)
                log({"cmd": abort_cmd.get("type")})
                if abort_cmd.get("type") == "abort":
                    emit({
                        "id": abort_cmd.get("id"),
                        "type": "response",
                        "command": "abort",
                        "success": True,
                    })
                    break
            continue
        if os.environ.get("OMEGA_FAKE_OMP_EXIT_AFTER_PROMPT") == "1":
            emit({"id": cid, "type": "response", "command": "prompt", "success": True})
            emit({
                "type": "tool_execution_end",
                "toolName": "write_file",
                "toolCallId": "t1",
            })
            raise SystemExit(0)
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
    # Point the bwrap lookup at a path that does not exist, so the refusal
    # holds on hosts that ship bubblewrap on PATH (the CI runner does not).
    env = _env(
        tmp_path,
        binary,
        OMEGA_PRIME_ENGINE_UNSANDBOXED="0",
        OMEGA_PRIME_BWRAP_BIN=str(tmp_path / "no-bwrap"),
    )
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


def test_prompt_wait_has_no_elapsed_lifetime_limit(monkeypatch, tmp_path: Path) -> None:
    from omega_prime.engine import omp_driver

    class LongSession:
        agent_ended = False
        events_path = tmp_path / "long-events.jsonl"

        def __init__(self) -> None:
            self.calls = 0

        def _next(self, timeout: float):
            self.calls += 1
            monkeypatch.setattr(omp_driver.time, "monotonic", lambda: self.calls * 0.11)
            if self.calls == 1:
                return {"id": "prompt-id", "type": "response", "success": True}
            if self.calls <= 1201:
                return None
            self.agent_ended = True
            return {"type": "agent_end"}

    session = LongSession()
    _accept = omp_driver._accept_prompt
    _accept(cast(OmpSession, session), "prompt-id", None)
    assert session.calls > 1200
    assert session.agent_ended


def test_session_distinguishes_idle_eof_reader_fault_and_dead_child(
    tmp_path: Path,
) -> None:
    class Alive:
        returncode: int | None = None

        def poll(self):
            return None

    class BlockingReader:
        def __init__(self, error: BaseException | None = None) -> None:
            self.release = threading.Event()
            self.error = error

        def __iter__(self):
            self.release.wait(1)
            if self.error:
                raise self.error
            return
            yield ""

    idle = BlockingReader()
    session = OmpSession(
        type("Proc", (Alive,), {"stdout": idle, "stderr": io.StringIO()})(),
        tmp_path / "idle.jsonl",
    )
    assert session._next(0.02) is None
    idle.release.set()
    try:
        session._next(0.2)
    except EngineError as exc:
        assert exc.reason == "process"
    else:
        raise AssertionError("clean EOF must fail")

    faulty = BlockingReader(OSError("reader broke"))
    failed = OmpSession(
        type("Proc", (Alive,), {"stdout": faulty, "stderr": io.StringIO()})(),
        tmp_path / "fault.jsonl",
    )
    faulty.release.set()
    try:
        failed._next(0.2)
    except EngineError as exc:
        assert "reader failed" in exc.detail
    else:
        raise AssertionError("reader fault must fail")

    inherited_pipe = BlockingReader()

    class Dead(Alive):
        returncode: int | None = 17

        def poll(self):
            return self.returncode

    dead = OmpSession(
        type("Proc", (Dead,), {"stdout": inherited_pipe, "stderr": io.StringIO()})(),
        tmp_path / "dead.jsonl",
    )
    try:
        dead._next(0.02)
    except EngineError as exc:
        assert "exited" in exc.detail
    else:
        raise AssertionError("dead child must fail even with inherited stdout open")
    inherited_pipe.release.set()


def test_close_kills_and_reaps_an_uncooperative_child(tmp_path: Path) -> None:
    class Uncooperative:
        stdin = None
        stdout = io.StringIO()
        stderr = io.StringIO()

        def __init__(self) -> None:
            self.waits = 0
            self.killed = False

        def wait(self, timeout: float):
            self.waits += 1
            if not self.killed:
                raise subprocess.TimeoutExpired("omp", timeout)
            return -9

        def kill(self) -> None:
            self.killed = True

    proc = Uncooperative()
    session = OmpSession(cast(subprocess.Popen[str], proc), tmp_path / "cleanup.jsonl")
    assert session.close() == -9
    assert proc.killed and proc.waits == 2


def test_interrupt_reaches_attach_and_delegate_engine_paths_once(
    tmp_path: Path, monkeypatch
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(
        tmp_path,
        binary,
        OMEGA_PRIME_ENGINE="required",
        OMEGA_FAKE_OMP_BLOCK_PROMPT="1",
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    def run_cancelled(call, interrupt: InterruptFlag):
        result: list = []
        worker = threading.Thread(target=lambda: result.append(call()), daemon=True)
        worker.start()
        log = Path(env["OMEGA_FAKE_OMP_LOG"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "prompt" not in _omp_commands(log):
            time.sleep(0.01)
        assert "prompt" in _omp_commands(log)
        interrupt.set("stop")
        worker.join(7)
        assert not worker.is_alive()
        return result[0]

    flag = InterruptFlag()
    budget = IterationBudget(1)
    model = ScriptedModel([{"role": "assistant", "content": "python"}])
    agent = Agent(model=model, budget=budget, interrupt=flag, force_engine=True)
    attached = run_cancelled(lambda: run_conversation(agent, "wait"), flag)
    assert attached["engine"]["started"] is True
    assert attached["turn_exit_reason"] == "cancelled"
    assert attached["interrupted"] is True
    assert attached["failed"] is False
    assert budget.remaining == 0 and model.call_count == 0
    assert _omp_commands(Path(env["OMEGA_FAKE_OMP_LOG"])).count("abort") == 1

    log = Path(env["OMEGA_FAKE_OMP_LOG"])
    log.unlink()
    delegate_flag = InterruptFlag()

    class Parent:
        child_model = None
        delegate_depth = 0

        def __init__(self) -> None:
            self.interrupt = delegate_flag
            self.tools: dict = {}

    delegated = run_cancelled(
        lambda: json.loads(delegate_task(Parent(), "wait")), delegate_flag
    )
    assert delegated["error"] == "cancelled"
    assert delegated["detail"] == "prompt cancelled"
    assert _omp_commands(log).count("abort") == 1


def test_agent_end_already_queued_wins_cancellation_race(tmp_path: Path) -> None:
    from omega_prime.engine.omp_driver import _accept_prompt

    class RaceSession:
        def __init__(self) -> None:
            self.agent_ended = False
            self.events_path = tmp_path / "race.jsonl"
            self.events: list[dict] = []
            self._queue: queue.Queue[str] = queue.Queue()
            self._queue.put(json.dumps({"type": "agent_end"}))

        def _next(self, timeout: float):
            return None

        def _raise_stream_failure(self):
            raise AssertionError("completion is already queued")

    flag = InterruptFlag()
    flag.set("stop")
    session = RaceSession()
    _accept_prompt(cast(OmpSession, session), "prompt-id", flag)
    assert session.agent_ended


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


def test_refused_launch_does_not_spend_the_caller_budget(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing-omp"
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = _env(tmp_path, missing, OMEGA_PRIME_ENGINE="required")
    env["PATH"] = str(empty)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    budget = IterationBudget(1)
    model = ScriptedModel([{"role": "assistant", "content": "from-python"}])
    agent = Agent(model=model, budget=budget)
    agent.force_engine = True
    refused = run_conversation(agent, "ping")
    assert refused["failed"] is True
    assert refused["api_calls"] == 0
    assert refused["engine"]["started"] is False
    assert budget.remaining == 1
    assert model.call_count == 0


def test_successful_engine_turn_spends_one_caller_iteration(
    tmp_path, monkeypatch
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_ENGINE="auto")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    budget = IterationBudget(1)
    model = ScriptedModel([{"role": "assistant", "content": "from-python"}])
    agent = Agent(model=model, budget=budget)
    agent.force_engine = True
    routed = run_conversation(agent, "ping")
    assert routed["final_response"] == "PONG"
    assert routed["api_calls"] == 1
    assert routed["engine"]["started"] is True
    assert budget.remaining == 0
    assert model.call_count == 0


def test_started_failure_spends_the_caller_budget() -> None:
    budget = IterationBudget(1)
    agent = Agent(
        model=ScriptedModel([{"role": "assistant", "content": "from-python"}]),
        budget=budget,
    )
    _seal_engine_turn(
        agent,
        {
            "messages": [
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "engine refused: process"},
            ],
            "api_calls": 0,
            "engine": {"started": True},
            "turn_exit_reason": "process",
            "final_response": "engine refused: process",
        },
        0,
    )
    assert budget.remaining == 0


def test_current_request_survives_a_long_transcript() -> None:
    history = [
        {"role": "assistant", "content": "x" * _TRANSCRIPT_CHARS} for _ in range(30)
    ]
    sentinel = "CURRENT-REQUEST-SENTINEL"
    text = _with_history(sentinel, history)
    assert text.endswith("Current request:\n" + sentinel)
    assert len(text) <= _PROMPT_LIMIT
    huge = "Q" * (_PROMPT_LIMIT + 50)
    truncated = _with_history(huge, history)
    assert truncated == huge[:_PROMPT_LIMIT]
    # A request that leaves no room must not keep the prior transcript.
    # ``s[-0:]`` is ``s``, so a zero room used to return the whole history.
    packed = "Q" * (_PROMPT_LIMIT - len("Prior transcript:\n\n\nCurrent request:\n"))
    capped = _with_history(packed, history)
    assert len(capped) <= _PROMPT_LIMIT
    assert "Prior transcript:" not in capped
    assert capped == packed
    tighter = _with_history(packed + "Z", history)
    assert len(tighter) <= _PROMPT_LIMIT
    assert "Prior transcript:" not in tighter


def test_failed_launch_records_job_end_and_follow_stops(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_FAKE_OMP_EXIT_AFTER_READY="1")
    result = run("ping", env=env, job_id="job-died")
    assert result.ok is False
    assert result.started is False
    events = _events(Path(result.events_path))
    assert events[-1]["type"] == "job_end"
    assert events[-1]["ok"] is False
    assert "agent_end" not in {row["type"] for row in events}
    buf = io.StringIO()
    started = time.monotonic()
    code = tail(Path(result.events_path), follow=True, write=buf)
    elapsed = time.monotonic() - started
    assert code == 0
    assert elapsed < 2
    assert "job_end" in buf.getvalue()


def test_job_end_write_failure_still_returns_the_original_result(
    tmp_path, monkeypatch
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_FAKE_OMP_EXIT_AFTER_READY="1")
    real = append_event

    def fail_job_end(path: Path, event: dict[str, object]) -> None:
        if event.get("type") == "job_end":
            raise OSError("disk full")
        real(path, event)

    monkeypatch.setattr("omega_prime.engine.omp_driver.append_event", fail_job_end)
    result = run("ping", env=env, job_id="job-full")
    assert result.ok is False
    assert result.reason != ""
    assert result.started is False
    events = _events(Path(result.events_path))
    assert events
    assert events[-1]["type"] != "job_end"


def test_refusal_before_the_event_file_writes_no_job_end(tmp_path: Path) -> None:
    missing = tmp_path / "missing-omp"
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = _env(tmp_path, missing)
    env["PATH"] = str(empty)
    result = run("ping", env=env, job_id="job-missing")
    assert result.ok is False
    assert result.reason == "not_configured"
    assert result.started is False
    assert not (tmp_path / "state" / "jobs" / "job-missing" / "events.jsonl").exists()


def _omp_commands(path: Path) -> list[str]:
    if not path.is_file():
        return []
    commands = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cmd = row.get("cmd")
        if isinstance(cmd, str):
            commands.append(cmd)
    return commands


def test_plan_mode_and_child_hook_never_start_omp(tmp_path, monkeypatch) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_PRIME_ENGINE="omp")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    log = tmp_path / "omp-log.jsonl"

    def drop_writes(tools: dict) -> None:
        tools.pop("write_file", None)

    hooked = Agent(
        model=ScriptedModel([{"role": "assistant", "content": "from-python"}]),
        tools={
            "write_file": lambda **kwargs: "wrote",
            "read_file": lambda **kwargs: "ok",
        },
    )
    hooked.force_engine = True
    hooked.child_tool_hook = drop_writes
    kept = run_conversation(hooked, "edit the file")
    assert kept["final_response"] == "from-python"
    assert "prompt" not in _omp_commands(log)

    class Parent:
        def __init__(self) -> None:
            self.child_model = None
            self.delegate_depth = 0
            self.tools = {"write_file": lambda **kwargs: "wrote"}
            self.child_tool_hook = drop_writes
            self.plan_mode = True

    raw = json.loads(delegate_task(Parent(), "edit the file"))
    assert "error" in raw
    assert "prompt" not in _omp_commands(log)


def test_started_engine_does_not_fall_back_to_python(tmp_path, monkeypatch) -> None:
    binary = _install_fake(tmp_path)
    env = _env(
        tmp_path,
        binary,
        OMEGA_FAKE_OMP_EXIT_AFTER_PROMPT="1",
        OMEGA_PRIME_ENGINE="auto",
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class Boom:
        def post(self, url: str, headers: dict, body: dict) -> dict:
            raise AssertionError("provider HTTP was called")

        def get(self, url: str, headers: dict, params: dict) -> dict:
            raise AssertionError("provider HTTP was called")

        def stream(self, url: str, headers: dict, body: dict) -> Iterator[str]:
            raise AssertionError("provider HTTP was called")

    provider = ProviderModel(GrokProvider(), "grok-4", Boom(), api_key="k")
    agent = Agent(model=provider)
    refused = run_conversation(agent, "append a line")
    assert refused["failed"] is True
    assert refused["api_calls"] == 0
    assert refused["engine"]["started"] is True
    assert refused["completed"] is False


def test_follow_waits_for_the_event_file(tmp_path: Path) -> None:
    state = tmp_path / "state"
    path = state / "jobs" / "job-wait" / "events.jsonl"

    def publish() -> None:
        time.sleep(0.2)
        path.parent.mkdir(parents=True)
        path.write_text('{"type":"job_end","ok":false}\n', encoding="utf-8")

    threading.Thread(target=publish, daemon=True).start()
    started = time.monotonic()
    code = watch_main(
        ["job-wait", "--follow", "--state-dir", str(state), "--timeout", "5"]
    )
    assert code == 0
    assert time.monotonic() - started >= 0.15
    missing = watch_main(["job-absent", "--state-dir", str(state)])
    assert missing == 1


def test_chatgpt_plan_is_opt_in_through_openai_codex() -> None:
    off = {item.plan: item for item in evaluate_all({})}
    assert off["chatgpt"].allowlisted is False
    assert off["chatgpt"].provider == "openai-codex"
    for name in ("OPENAI_API_KEY", "OPENAI_CODEX_OAUTH_TOKEN"):
        keyed = {item.plan: item for item in evaluate_all({name: _SECRET})}
        assert keyed["chatgpt"].allowlisted is False
        assert _SECRET not in keyed["chatgpt"].reason
    env = {"OMEGA_PRIME_OMP_ALLOW_CHATGPT": "1"}
    opted = {item.plan: item for item in evaluate_all(env)}
    assert opted["chatgpt"].allowlisted is True
    assert opted["chatgpt"].auth == "subscription_opt_in"
    assert opted["chatgpt"].model_id == "gpt-6.1-sol"
    assert all(
        item.allowlisted is False for name, item in opted.items() if name != "chatgpt"
    )
    pick = select(env, requested="chatgpt")
    assert (pick.decision.provider, pick.decision.model_id) == (
        "openai-codex",
        "gpt-6.1-sol",
    )
    override = select(
        {**env, "OMEGA_PRIME_OMP_MODEL_CHATGPT": "gpt-5.6-sol"}, requested="chatgpt"
    )
    assert override.decision.model_id == "gpt-5.6-sol"
    try:
        select({"OPENAI_API_KEY": _SECRET}, requested="chatgpt")
    except PlanNotAllowed as exc:
        assert "OMEGA_PRIME_OMP_ALLOW_CHATGPT" in exc.detail
    else:
        raise AssertionError("expected PlanNotAllowed")


def test_openai_codex_usage_row_maps_to_chatgpt_plan() -> None:
    usage = parse_usage(
        {"providers": [{"provider": "openai-codex", "percentUsed": 25}]}
    )
    assert usage["chatgpt"].percent == 25
    env = {"OMEGA_PRIME_OMP_ALLOW_CHATGPT": "1", "OMEGA_PRIME_OMP_ALLOW_ZAI": "1"}
    usage["zai"] = parse_usage(
        {"providers": [{"provider": "zai", "percentUsed": 100}]}
    )["zai"]
    assert select(env, usage).decision.plan == "chatgpt"


def test_stage_routing_falls_back_to_zai_when_chatgpt_is_gated_off() -> None:
    env = {"OMEGA_PRIME_OMP_ALLOW_ZAI": "1", "OPENAI_API_KEY": _SECRET}
    for stage in ("plan", "execute"):
        pick = select(env, stage=stage)
        assert (pick.decision.provider, pick.decision.model_id) == ("zai", "glm-5.3")
        assert pick.stage == stage
        assert pick.fallback_from == "chatgpt"
        assert pick.fallback_reason == "chatgpt: off the allowlist"
        assert _SECRET not in (pick.fallback_reason or "")
    try:
        select({}, stage="plan")
    except PlanNotAllowed as exc:
        assert "OMEGA_PRIME_OMP_ALLOW_CHATGPT" in exc.detail
        assert "plan stage" in exc.detail
    else:
        raise AssertionError("expected PlanNotAllowed")
    try:
        select(env, stage="ship")
    except PlanNotAllowed as exc:
        assert "unknown stage" in exc.detail
    else:
        raise AssertionError("expected PlanNotAllowed")


def test_stage_routing_falls_back_to_zai_when_chatgpt_quota_is_exhausted() -> None:
    env = {"OMEGA_PRIME_OMP_ALLOW_CHATGPT": "1", "OMEGA_PRIME_OMP_ALLOW_ZAI": "1"}
    full = parse_usage(
        {"providers": [{"provider": "openai-codex", "percentUsed": 100}]}
    )
    pick = select(env, full, stage="execute")
    assert pick.decision.plan == "zai"
    assert pick.fallback_from == "chatgpt"
    assert pick.fallback_reason == "chatgpt: quota window exhausted"
    empty = parse_usage({"providers": [{"provider": "openai-codex", "remaining": 0}]})
    assert select(env, empty, stage="plan").decision.plan == "zai"
    open_window = parse_usage(
        {"providers": [{"provider": "openai-codex", "percentUsed": 40}]}
    )
    kept = select(env, open_window, stage="plan")
    assert (kept.decision.plan, kept.fallback_from) == ("chatgpt", None)
    both_full = {
        **full,
        **parse_usage({"providers": [{"provider": "zai", "percentUsed": 100}]}),
    }
    try:
        select(env, both_full, stage="plan")
    except PlanNotAllowed as exc:
        assert "exhausted" in exc.detail
    else:
        raise AssertionError("expected PlanNotAllowed")
    # Without a stage the router keeps its old rule: a requested plan is used
    # as long as it is allowlisted.
    assert select(env, full, requested="chatgpt").decision.plan == "chatgpt"


def test_stage_models_and_overrides() -> None:
    env = {"OMEGA_PRIME_OMP_ALLOW_CHATGPT": "1", "OMEGA_PRIME_OMP_ALLOW_ZAI": "1"}
    assert select(env, stage="plan").decision.model_id == "gpt-6.1-sol"
    assert select(env, stage="execute").decision.model_id == "gpt-6-luna"
    assert select(env, requested="chatgpt").decision.model_id == "gpt-6.1-sol"
    staged = {**env, "OMEGA_PRIME_OMP_MODEL_CHATGPT_EXECUTE": "gpt-6-sol"}
    assert select(staged, stage="execute").decision.model_id == "gpt-6-sol"
    assert select(staged, stage="plan").decision.model_id == "gpt-6.1-sol"
    whole = {**env, "OMEGA_PRIME_OMP_MODEL_CHATGPT": "gpt-5.6-sol"}
    assert select(whole, stage="plan").decision.model_id == "gpt-5.6-sol"
    assert select(whole, stage="execute").decision.model_id == "gpt-5.6-sol"
    both = {**whole, "OMEGA_PRIME_OMP_MODEL_CHATGPT_PLAN": "gpt-6-astra"}
    assert select(both, stage="plan").decision.model_id == "gpt-6-astra"
    zai_only = {
        "OMEGA_PRIME_OMP_ALLOW_ZAI": "1",
        "OMEGA_PRIME_OMP_MODEL_ZAI_EXECUTE": "glm-5.3-flash",
    }
    assert select(zai_only, stage="execute").decision.model_id == "glm-5.3-flash"
    assert select(zai_only, stage="plan").decision.model_id == "glm-5.3"
    forced = select(env, stage="plan", requested="zai", fallback=())
    assert (forced.decision.plan, forced.fallback_from) == ("zai", None)


def _logged_argv(tmp_path: Path) -> list[str]:
    logged = [
        json.loads(line)
        for line in (tmp_path / "omp-log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    return next(row["argv"] for row in logged if "--mode" in row.get("argv", []))


def test_stage_job_uses_stage_model_and_the_shared_login_store(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    store = tmp_path / "home" / ".omp" / "agent"
    store.mkdir(parents=True)
    (store / "agent.db").write_bytes(b"login-store-placeholder")
    env = _env(tmp_path, binary, OMEGA_PRIME_OMP_ALLOW_CHATGPT="1")
    result = run("PONG", cwd=tmp_path, env=env, job_id="job-luna", stage="execute")
    assert result.ok is True
    assert (result.provider, result.model) == ("openai-codex", "gpt-6-luna")
    assert (result.stage, result.fallback_from) == ("execute", None)
    argv = _logged_argv(tmp_path)
    assert argv[argv.index("--model") + 1] == "openai-codex/gpt-6-luna"
    # No per-job omp profile: omp rejects a path as a profile name, and a named
    # profile would read ~/.omp/profiles/<name>/agent/agent.db, which has no
    # login. Sessions still stay per job.
    assert "--profile" not in argv
    job = tmp_path / "state" / "jobs" / "job-luna"
    assert argv[argv.index("--session-dir") + 1] == str(job / "sessions")
    assert not list(job.rglob("agent.db"))
    session = json.loads((job / "session.json").read_text(encoding="utf-8"))
    assert (session["model"], session["stage"]) == ("gpt-6-luna", "execute")
    fallback = run(
        "PONG",
        cwd=tmp_path,
        env=_env(tmp_path, binary, OMEGA_PRIME_OMP_ALLOW_ZAI="1"),
        job_id="job-glm",
        stage="plan",
    )
    assert (fallback.provider, fallback.model) == ("zai", "glm-5.3")
    assert fallback.fallback_from == "chatgpt"


def test_sandbox_binds_the_shared_login_store_writable(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    store = tmp_path / "home" / ".omp"
    (store / "agent").mkdir(parents=True)
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
    env = _env(
        tmp_path,
        binary,
        OMEGA_PRIME_ENGINE_UNSANDBOXED="0",
        OMEGA_FAKE_OMP_BWRAP_LOG=str(tmp_path / "bwrap.txt"),
        OMEGA_PRIME_OMP_ALLOW_CHATGPT="1",
    )
    result = run("PONG", cwd=tmp_path, env=env, job_id="job-store", stage="plan")
    assert result.ok is True and result.model == "gpt-6.1-sol"
    args = (tmp_path / "bwrap.txt").read_text(encoding="utf-8").splitlines()
    binds = [args[i + 1] for i, item in enumerate(args) if item == "--bind"]
    assert str(store) in binds
    custom = tmp_path / "custom-omp"
    custom.mkdir()
    env["OMEGA_PRIME_OMP_HOME"] = str(custom)
    run("PONG", cwd=tmp_path, env=env, job_id="job-custom", stage="plan")
    args = (tmp_path / "bwrap.txt").read_text(encoding="utf-8").splitlines()
    binds = [args[i + 1] for i, item in enumerate(args) if item == "--bind"]
    assert str(custom) in binds and str(store) not in binds


def test_custom_omp_home_is_the_store_the_child_reads(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    (tmp_path / "home" / ".omp" / "agent").mkdir(parents=True)
    custom = tmp_path / "custom-omp"
    (custom / "agent").mkdir(parents=True)
    log = tmp_path / "omp-log.jsonl"
    env = _env(tmp_path, binary, OMEGA_PRIME_OMP_ALLOW_CHATGPT="1")
    run("PONG", cwd=tmp_path, env=env, job_id="job-default", stage="plan")
    rows = [
        row
        for row in map(json.loads, log.read_text(encoding="utf-8").splitlines())
        if "argv" in row
    ]
    # Default: omp resolves $HOME/.omp/agent itself; nothing overrides it.
    assert rows and all(row["agent_dir"] is None for row in rows)
    log.unlink()
    env["OMEGA_PRIME_OMP_HOME"] = str(custom)
    result = run("PONG", cwd=tmp_path, env=env, job_id="job-custom", stage="plan")
    assert result.ok is True
    rows = [
        row
        for row in map(json.loads, log.read_text(encoding="utf-8").splitlines())
        if "argv" in row
    ]
    kinds = {
        "version": [r for r in rows if "--version" in r["argv"]],
        "usage": [r for r in rows if "usage" in r["argv"]],
        "rpc": [r for r in rows if "--mode" in r["argv"]],
    }
    assert all(kinds.values()), kinds
    # Version probe, usage check and RPC run all read the custom login store.
    assert {row["agent_dir"] for row in rows} == {str(custom / "agent")}

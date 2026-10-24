# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic omp engine tests. The fake binary never opens a network socket."""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import queue
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

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
from omega_prime.engine.ui_control import UIControlError, UIControlStore
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
import asyncio, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def discover(config):
    server = config["mcpServers"]["omega-prime"]
    command = server["command"]
    if os.environ.get("OMEGA_FAKE_OMP_MCP_FAILURE") == "missing":
        command = "/missing/omega-prime-mcp"
    command_args = server.get("args", [])
    if os.environ.get("OMEGA_FAKE_OMP_MCP_FAILURE") == "import":
        command_args = ["-c", "import omega_prime_missing_module"]
    mcp_env = os.environ.copy()
    mcp_env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (server.get("cwd"), mcp_env.get("PYTHONPATH", "")))
    )

    async def _discover():
        params = StdioServerParameters(
            command=command,
            args=command_args,
            cwd=server.get("cwd"),
            env=mcp_env,
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
        return [tool.name for tool in result.tools]

    try:
        return asyncio.run(_discover())
    except FileNotFoundError as exc:
        log({"discovery_error": type(exc).__name__})
        return []
    except (KeyError, ValueError) as exc:
        log({"discovery_error": type(exc).__name__})
        return []
    except Exception as exc:
        log({"discovery_error": "RuntimeError"})
        return []

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
mode = args[args.index("--mode") + 1] if "--mode" in args else "rpc"
subscribed_events = []
work = args[args.index("--cwd") + 1] if "--cwd" in args else os.getcwd()
configured = None
discovered = []
if os.environ.get("OMEGA_FAKE_OMP_DISCOVER") == "1":
    project_mcp = os.path.join(work, ".omp", "mcp.json")
    try:
        with open(project_mcp, encoding="utf-8") as handle:
            configured = json.load(handle)
            log({"project_mcp": configured})
        discovered = [
            f"mcp__omega-prime_{name}" for name in discover(configured)
        ]
        log({"discovered": discovered})
    except Exception as exc:
        log({"discovery_error": type(exc).__name__})
log({"argv": args, "cwd": os.getcwd(), "agent_dir": os.environ.get("PI_CODING_AGENT_DIR")})
if "--help" in args:
    print("Usage: omp --thinking {off,minimal,low,medium,high,xhigh,max}")
    raise SystemExit(0)
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
    log({"cmd": cmd.get("type"), "id": cmd.get("id"), "events": cmd.get("events")})
    kind = cmd.get("type")
    cid = cmd.get("id")
    if kind == "set_event_filter":
        subscribed_events = cmd.get("events", [])
        emit({"id": cid, "type": "response", "command": kind, "success": True})
    elif kind == "prompt":
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
        if (
            mode == "rpc-ui"
            and os.environ.get("OMEGA_FAKE_OMP_UI_ENABLED") == "1"
            and os.environ.get("OMEGA_FAKE_OMP_UI_DISABLED") != "1"
        ):
            external_id = "rpc-external-104"
            method = os.environ.get("OMEGA_FAKE_OMP_UI_METHOD", "select")
            request: dict[str, object] = {
                "type": "extension_ui_request",
                "id": external_id,
                "method": method,
            }
            if method == "ask":
                request["questions"] = [
                    {
                        "id": "route",
                        "question": "Which route should be used?",
                        "options": [{"label": "safe"}, {"label": "fast"}],
                    }
                ]
            elif method == "confirm":
                request.update({"title": "Confirm route", "message": "Use safe route?"})
            else:
                request.update({
                    "title": "Choose a route with " + "=".join(("token", "-".join(("ui", "temporary", "value")))),
                    "options": ["fast", "safe"],
                })
            emit(request)
            if os.environ.get("OMEGA_FAKE_OMP_UI_EOF") == "1":
                raise SystemExit(0)
            while True:
                answer_line = sys.stdin.readline()
                if not answer_line:
                    raise SystemExit(0)
                answer_cmd = json.loads(answer_line)
                answer_kind = answer_cmd.get("type")
                log({
                    "cmd": answer_kind,
                    "id": answer_cmd.get("id"),
                    "events": answer_cmd.get("events"),
                })
                if answer_kind == "abort":
                    emit({
                        "id": answer_cmd.get("id"),
                        "type": "response",
                        "command": "abort",
                        "success": True,
                    })
                    emit({
                        "id": cid,
                        "type": "response",
                        "command": "prompt",
                        "success": True,
                    })
                    emit({"type": "agent_end"})
                    raise SystemExit(0)
                if (
                    answer_kind == "extension_ui_response"
                    and answer_cmd.get("id") == external_id
                ):
                    log({"ui_response": answer_cmd})
                    break
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
    elif kind == "get_state":
        emit({
            "id": cid,
            "type": "response",
            "command": kind,
            "success": True,
            "data": {"dumpTools": discovered},
        })
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
    fake = _FAKE.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1)
    binary.write_text(fake, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def _env(tmp_path: Path, binary: Path, **extra: str) -> dict[str, str]:
    base = {
        "PATH": str(binary.parent) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(
            filter(
                None,
                (
                    str(Path(__file__).resolve().parents[2]),
                    os.environ.get("PYTHONPATH", ""),
                ),
            )
        ),
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


def _ui_store(tmp_path: Path) -> UIControlStore:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return UIControlStore(
        tmp_path / "operator-state",
        writable_binds=[work, tmp_path / "state", tmp_path / "home"],
    )


def _wait_for_ui_pending(store: UIControlStore, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending = store.list_pending(job=job_id)
        if pending:
            return pending[0]
        time.sleep(0.01)
    raise AssertionError("fake OMP did not publish its UI request")


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


def test_engine_body_reads_result_fields() -> None:
    from omega_prime.engine.attach import _engine_body
    from omega_prime.engine.omp_driver import EngineResult

    result = EngineResult(
        True,
        "job-engine-body",
        usage={"input_tokens": 17, "output_tokens": 9},
        plan="anthropic",
        provider="anthropic",
        model="claude",
        events_path="/tmp/events.jsonl",
        started=True,
        mcp_status="attached",
        cleanup_conflicts=["preserved config"],
    )

    assert _engine_body(result) == {
        "job_id": "job-engine-body",
        "plan": "anthropic",
        "provider": "anthropic",
        "model": "claude",
        "events": "/tmp/events.jsonl",
        "usage": {"input_tokens": 17, "output_tokens": 9},
        "cost": None,
        "sandbox": "",
        "mcp_status": "attached",
        "mcp_cleanup_conflicts": ["preserved config"],
        "started": True,
    }


def test_engine_usage_reaches_prime_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    import omega_prime.agent.conversation_loop as loop
    from omega_prime.engine.attach import _accepted
    from omega_prime.engine.omp_driver import EngineResult

    observed: dict[str, Any] = {}

    def evaluate_goal(_hooks: Any, _agent: Any, usage: Any, **_kwargs: Any) -> dict:
        observed["usage"] = usage
        return {}

    monkeypatch.setattr(loop, "evaluate_goal", evaluate_goal)
    monkeypatch.setattr(loop, "evaluate_autonomous", lambda *_args, **_kwargs: {})
    agent = Agent(
        model=ScriptedModel([]),
        prime_hooks=object(),
    )
    routed = _accepted(
        "ping",
        None,
        EngineResult(
            True,
            "job-token-usage",
            final_text="done",
            usage={"input_tokens": 17, "output_tokens": 9},
            started=True,
        ),
    )

    result = _seal_engine_turn(agent, routed, 0)

    assert observed["usage"] == {"total_tokens": 26}
    assert result["usage"] == {"total_tokens": 26}


def test_fake_omp_session_discovers_configured_mcp_tools(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    root_mcp = tmp_path / "mcp.json"
    root_contents = b"root omp config remains separate"
    root_mcp.write_bytes(root_contents)
    env = _env(
        tmp_path,
        binary,
        OMEGA_FAKE_OMP_API_KEY=_shaped_token(),
        OMEGA_FAKE_OMP_DISCOVER="1",
    )
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
        "get_state",
        "prompt",
        "get_session_stats",
    ]
    assert result.mcp_status == "attached"
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
    project_mcp = tmp_path / ".omp" / "mcp.json"
    assert result.mcp_status == "attached"
    assert root_mcp.read_bytes() == root_contents
    from omega_prime.engine.mcp_bridge import install_root

    logged = [
        json.loads(line)
        for line in (tmp_path / "omp-log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    configured = next(row["project_mcp"] for row in logged if "project_mcp" in row)
    discovered = next(row["discovered"] for row in logged if "discovered" in row)
    assert discovered
    assert all(name.startswith("mcp__omega-prime_") for name in discovered)
    server = configured["mcpServers"]["omega-prime"]
    assert server["command"] == sys.executable
    assert server["args"] == [
        "-m",
        "omega_prime.mcp_server",
        "--root",
        str(install_root()),
        "--work-root",
        str(Path(tmp_path).resolve()),
    ]
    assert server["type"] == "stdio"
    assert server["cwd"] == str(install_root())
    assert "CURSOR_PLUGIN_ROOT" not in json.dumps(configured)
    events = _events(Path(result.events_path))
    assert any(
        row.get("type") == "mcp_attachment" and row.get("status") == "attached"
        for row in events
    )
    assert not project_mcp.exists()
    assert not (tmp_path / ".omp").exists()
    argv = next(row["argv"] for row in logged if "--mode" in row.get("argv", []))
    assert "--mode" in argv and "rpc" in argv
    assert "--cwd" in argv and argv[argv.index("--cwd") + 1] == str(tmp_path.resolve())
    assert "--skills" in argv
    skills = argv[argv.index("--skills") + 1]
    assert skills == "*"
    commands = [row.get("cmd") for row in logged if "cmd" in row]
    assert commands == result.commands
    rpc_filter = next(
        row["events"] for row in logged if row.get("cmd") == "set_event_filter"
    )
    assert "extension_ui_request" not in rpc_filter
    code = watch_main(["job-alpha", "--state-dir", str(tmp_path / "state")])
    assert result.mode == "rpc"
    assert result.omp_major == 18
    assert code == 0


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [("missing", "FileNotFoundError"), ("import", "RuntimeError")],
)
def test_mcp_discovery_reports_stdio_startup_failures(
    tmp_path: Path, failure: str, expected_error: str
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(
        tmp_path,
        binary,
        OMEGA_FAKE_OMP_DISCOVER="1",
        OMEGA_FAKE_OMP_MCP_FAILURE=failure,
    )
    result = run("PONG", cwd=tmp_path, env=env, job_id=f"job-mcp-{failure}")
    assert result.ok is True
    rows = [
        json.loads(line)
        for line in Path(env["OMEGA_FAKE_OMP_LOG"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {"discovery_error": expected_error} in rows
    assert {"discovered": []} in rows


def test_rpc_ui_requires_safe_control_before_launch(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    result = run(
        "Choose a route",
        cwd=tmp_path,
        env=env,
        job_id="job-ui-missing",
        mode="rpc-ui",
    )
    assert result.ok is False
    assert result.reason == "ui_control_unavailable"
    assert not Path(env["OMEGA_FAKE_OMP_LOG"]).exists()
    events = _events(Path(result.events_path))
    assert any(
        row.get("type") == "ui_control_attachment"
        and row.get("status") == "attach_failed"
        for row in events
    )
    session = json.loads(
        (tmp_path / "state" / "jobs" / "job-ui-missing" / "session.json").read_text(
            encoding="utf-8"
        )
    )
    assert session["mode"] == "rpc-ui"
    assert session["ui_summary"]["status"] == "attach_failed"


def test_rpc_ui_dispatches_one_original_id_response_through_control_store(
    tmp_path: Path,
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_FAKE_OMP_VERSION="18.12.3")
    env["OMEGA_FAKE_OMP_UI_ENABLED"] = "1"
    job_id = "job-ui-answer"
    with _ui_store(tmp_path) as store:
        failures: list[BaseException] = []

        def queue_answer() -> None:
            try:
                pending = _wait_for_ui_pending(store, job_id)
                identity = pending["identity"]
                stale = {**identity, "turn_generation": "old-turn"}
                with pytest.raises(UIControlError):
                    store.answer(stale, "select", "safe")
                with pytest.raises(UIControlError):
                    store.answer(identity, "select", "not-an-option")
                store.answer(identity, "select", "safe")
            except BaseException as exc:
                failures.append(exc)

        operator = threading.Thread(target=queue_answer, daemon=True)
        operator.start()
        result = run(
            "Choose a route",
            cwd=tmp_path / "work",
            env=env,
            job_id=job_id,
            mode="rpc-ui",
            thinking="max",
            ui_control=store,
        )
        operator.join(5)
        assert not operator.is_alive()
        assert not failures
        assert result.ok is True
        assert result.final_text == "PONG"
        assert result.mode == "rpc-ui"
        assert result.omp_major == 18
        assert result.ui_summary == {
            "status": "attached",
            "pending": 0,
            "answered": 1,
            "rejected_stale": 0,
            "aborted": 0,
        }
        events = _events(Path(result.events_path))
        ui_types = [row["type"] for row in events if row["type"].startswith("ui_")]
        assert ui_types.index("ui_pending") < ui_types.index("ui_answered")
        assert any(
            row.get("type") == "ui_control_attachment"
            and row.get("status") == "attached"
            for row in events
        )
        pending_event = next(row for row in events if row["type"] == "ui_pending")
        identity = pending_event["identity"]
        assert identity["job_id"] == job_id
        assert len(identity["session_generation"]) == 32
        assert len(identity["turn_generation"]) == 32
        assert identity["session_generation"] != identity["turn_generation"]
        assert identity["request_id"] == "rpc-external-104"
        rendered_pending = json.dumps(pending_event)
        assert "-".join(("ui", "temporary", "value")) not in rendered_pending
        assert "token=[REDACTED]" in rendered_pending
        rows = [
            json.loads(line)
            for line in Path(env["OMEGA_FAKE_OMP_LOG"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        responses = [row["ui_response"] for row in rows if "ui_response" in row]
        assert responses == [
            {
                "type": "extension_ui_response",
                "id": "rpc-external-104",
                "value": "safe",
            }
        ]
        assert sum(row.get("cmd") == "extension_ui_response" for row in rows) == 1
        filter_events = next(
            row["events"] for row in rows if row.get("cmd") == "set_event_filter"
        )
        assert "extension_ui_request" in filter_events
        with pytest.raises(UIControlError):
            store.answer(identity, "select", "safe")
        assert store.status(job=job_id)["pending_count"] == 0
    session = json.loads(
        (tmp_path / "state" / "jobs" / job_id / "session.json").read_text(
            encoding="utf-8"
        )
    )
    assert session["mode"] == "rpc-ui"
    assert session["omp_major"] == 18
    assert session["mcp_status"] == result.mcp_status
    assert session["ui_summary"] == result.ui_summary
    argv = next(row["argv"] for row in rows if "--mode" in row.get("argv", []))
    assert argv[argv.index("--thinking") + 1] == "max"


@pytest.mark.parametrize(
    ("method", "answer_kind", "answer_value", "expected"),
    [
        (
            "confirm",
            "confirm",
            False,
            {
                "type": "extension_ui_response",
                "id": "rpc-external-104",
                "confirmed": False,
            },
        ),
        (
            "ask",
            "text",
            "use the safe route",
            {
                "type": "extension_ui_response",
                "id": "rpc-external-104",
                "answers": [
                    {
                        "id": "route",
                        "selectedOptions": [],
                        "customInput": "use the safe route",
                    }
                ],
            },
        ),
    ],
)
def test_rpc_ui_dispatches_confirm_and_ask_answers(
    tmp_path: Path,
    method: str,
    answer_kind: str,
    answer_value: object,
    expected: dict[str, object],
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    env["OMEGA_FAKE_OMP_UI_METHOD"] = method
    env["OMEGA_FAKE_OMP_UI_ENABLED"] = "1"
    job_id = f"job-ui-{method}"
    with _ui_store(tmp_path) as store:
        failures: list[BaseException] = []

        def queue_answer() -> None:
            try:
                pending = _wait_for_ui_pending(store, job_id)
                store.answer(pending["identity"], answer_kind, answer_value)
            except BaseException as exc:
                failures.append(exc)

        operator = threading.Thread(target=queue_answer, daemon=True)
        operator.start()
        result = run(
            "Answer the UI request",
            cwd=tmp_path / "work",
            env=env,
            job_id=job_id,
            mode="rpc-ui",
            ui_control=store,
        )
        operator.join(5)
        assert not operator.is_alive()
        assert not failures
        assert result.ok is True
        assert result.ui_summary["answered"] == 1
        rows = [
            json.loads(line)
            for line in Path(env["OMEGA_FAKE_OMP_LOG"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        responses = [row["ui_response"] for row in rows if "ui_response" in row]
        assert responses == [expected]
        assert sum(row.get("cmd") == "extension_ui_response" for row in rows) == 1
        assert store.status(job=job_id)["pending_count"] == 0


def test_rpc_ui_queued_abort_invalidates_pending_request_and_aborts_once(
    tmp_path: Path,
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    env["OMEGA_FAKE_OMP_UI_ENABLED"] = "1"
    job_id = "job-ui-abort"
    with _ui_store(tmp_path) as store:
        failures: list[BaseException] = []
        identities: list[dict[str, object]] = []

        def queue_abort() -> None:
            try:
                pending = _wait_for_ui_pending(store, job_id)
                identity = pending["identity"]
                identities.append(identity)
                store.abort(job_id, identity["session_generation"])
            except BaseException as exc:
                failures.append(exc)

        operator = threading.Thread(target=queue_abort, daemon=True)
        operator.start()
        result = run(
            "Choose a route",
            cwd=tmp_path / "work",
            env=env,
            job_id=job_id,
            mode="rpc-ui",
            ui_control=store,
        )
        operator.join(5)
        assert not operator.is_alive()
        assert not failures
        assert result.ok is False
        assert result.reason == "aborted"
        assert result.ui_summary["aborted"] == 1
        assert result.ui_summary["pending"] == 0
        with pytest.raises(UIControlError):
            store.answer(identities[0], "select", "safe")
        commands = [
            row["cmd"]
            for row in (
                json.loads(line)
                for line in Path(env["OMEGA_FAKE_OMP_LOG"])
                .read_text(encoding="utf-8")
                .splitlines()
            )
            if "cmd" in row
        ]
        assert commands.count("abort") == 1
        assert "extension_ui_response" not in commands
        events = _events(Path(result.events_path))
        assert any(row.get("type") == "ui_rejected_stale" for row in events)


def test_rpc_ui_cancel_sends_one_abort_and_marks_request_aborted(
    tmp_path: Path,
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    env["OMEGA_FAKE_OMP_UI_ENABLED"] = "1"
    job_id = "job-ui-cancel"
    cancel_event = threading.Event()
    with _ui_store(tmp_path) as store:
        failures: list[BaseException] = []

        def cancel_after_pending() -> None:
            try:
                _wait_for_ui_pending(store, job_id)
                cancel_event.set()
            except BaseException as exc:
                failures.append(exc)

        operator = threading.Thread(target=cancel_after_pending, daemon=True)
        operator.start()
        result = run(
            "Choose a route",
            cwd=tmp_path / "work",
            env=env,
            job_id=job_id,
            mode="rpc-ui",
            ui_control=store,
            cancel_event=cancel_event,
        )
        operator.join(5)
        assert not operator.is_alive()
        assert not failures
        assert result.ok is False
        assert result.reason == "cancelled"
        assert result.ui_summary["aborted"] == 1
        commands = [
            row["cmd"]
            for row in (
                json.loads(line)
                for line in Path(env["OMEGA_FAKE_OMP_LOG"])
                .read_text(encoding="utf-8")
                .splitlines()
            )
            if "cmd" in row
        ]
        assert commands.count("abort") == 1
        assert "extension_ui_response" not in commands


def test_rpc_ui_eof_resolves_pending_request_instead_of_waiting_for_timeout(
    tmp_path: Path,
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(
        tmp_path,
        binary,
        OMEGA_FAKE_OMP_UI_EOF="1",
        OMEGA_FAKE_OMP_UI_ENABLED="1",
    )
    job_id = "job-ui-eof"
    with _ui_store(tmp_path) as store:
        result = run(
            "Choose a route",
            cwd=tmp_path / "work",
            env=env,
            job_id=job_id,
            mode="rpc-ui",
            ui_control=store,
        )
        assert result.ok is False
        assert result.reason == "process"
        assert result.started is True
        assert store.status(job=job_id)["pending_count"] == 0
        events = _events(Path(result.events_path))
        assert any(row.get("type") == "ui_rejected_stale" for row in events)


def test_rpc_ui_writer_failure_does_not_retry_response(
    tmp_path: Path, monkeypatch
) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    env["OMEGA_FAKE_OMP_UI_ENABLED"] = "1"
    job_id = "job-ui-write-failed"
    writes: list[dict[str, object]] = []
    original_send_frame = OmpSession.send_frame

    def fail_ui_response(self: OmpSession, payload: dict[str, object]) -> None:
        if payload.get("type") == "extension_ui_response":
            writes.append(payload)
            raise BrokenPipeError("test UI response failure")
        original_send_frame(self, payload)

    monkeypatch.setattr(OmpSession, "send_frame", fail_ui_response)
    with _ui_store(tmp_path) as store:
        failures: list[BaseException] = []

        def queue_answer() -> None:
            try:
                pending = _wait_for_ui_pending(store, job_id)
                store.answer(pending["identity"], "select", "safe")
            except BaseException as exc:
                failures.append(exc)

        operator = threading.Thread(target=queue_answer, daemon=True)
        operator.start()
        result = run(
            "Choose a route",
            cwd=tmp_path / "work",
            env=env,
            job_id=job_id,
            mode="rpc-ui",
            ui_control=store,
        )
        operator.join(5)
        assert not operator.is_alive()
        assert not failures
        assert result.ok is False
        assert result.reason == "ui_send_failed"
        assert len(writes) == 1
        assert store.status(job=job_id)["pending_count"] == 0


def test_omp_18_12_help_and_rpc_launch_preserve_thinking_max(tmp_path: Path) -> None:
    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary, OMEGA_FAKE_OMP_VERSION="18.12.3")
    help_result = subprocess.run(
        [str(binary), "--help"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    version_result = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "max" in help_result.stdout
    assert version_result.stdout.strip() == "18.12.3"
    result = run(
        "Reply with PONG",
        cwd=tmp_path,
        env=env,
        job_id="job-max",
        thinking="max",
    )
    assert result.ok is True
    assert result.omp_major == 18
    argv = next(
        row["argv"]
        for row in (
            json.loads(line)
            for line in Path(env["OMEGA_FAKE_OMP_LOG"])
            .read_text(encoding="utf-8")
            .splitlines()
        )
        if "--mode" in row.get("argv", [])
    )
    assert argv[argv.index("--thinking") + 1] == "max"


def test_existing_project_mcp_config_is_preserved_and_attachment_backs_off(
    tmp_path: Path,
) -> None:
    binary = _install_fake(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    config_dir = work / ".omp"
    config_dir.mkdir()
    config = config_dir / "mcp.json"
    original = b'{"mcpServers":{"user-server":{"type":"stdio","command":"keep"}}}\n'
    config.write_bytes(original)
    mode = config.stat().st_mode
    root_config = work / "mcp.json"
    root_config.write_bytes(b"root config remains separate\n")
    result = run(
        "continue without MCP",
        cwd=work,
        env=_env(tmp_path, binary),
        job_id="job-existing",
    )
    assert result.ok is True
    assert result.final_text == "PONG"
    assert result.mcp_status == "attach_failed"
    assert config.read_bytes() == original
    assert config.stat().st_mode == mode
    assert root_config.read_bytes() == b"root config remains separate\n"
    assert not list(config_dir.glob(".omega-prime-owner-*"))
    events = [
        row
        for row in _events(Path(result.events_path))
        if row.get("type") == "mcp_attachment"
    ]
    assert len(events) == 1 and events[0]["status"] == "attach_failed"
    commands = [
        row["cmd"] for row in _events(tmp_path / "omp-log.jsonl") if "cmd" in row
    ]
    assert commands.count("prompt") == 1


def test_attachment_crash_before_spawn_recovers_only_dead_owner(tmp_path: Path) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    attachment = mcp_bridge.try_acquire_attachment(work, state, "job-crash")
    skills_dir = work / ".omp" / "skills"
    skills_dir.mkdir()
    target = work / "python-target"
    target.write_text("target", encoding="utf-8")
    link = skills_dir / "python"
    link.symlink_to(target)
    dir_stat = skills_dir.stat(follow_symlinks=False)
    link_stat = link.stat(follow_symlinks=False)
    attachment.register_artifact(".omp/skills", (dir_stat.st_dev, dir_stat.st_ino))
    attachment.register_artifact(
        ".omp/skills/python", (link_stat.st_dev, link_stat.st_ino)
    )
    boot_id = (
        Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    )
    attachment._owner_identity = {
        "pid": 999999999,
        "start_time": "1",
        "boot_id": boot_id,
    }
    attachment._write_journal(None)
    os.close(attachment._omp_fd)
    os.close(attachment._parent_fd)

    assert mcp_bridge.recover_attachments(state, work) == []
    assert not (work / ".omp").exists()
    assert not list((state / "mcp-attachments").glob("*.json"))
    assert target.read_text(encoding="utf-8") == "target"


def test_attachment_post_journal_failure_unwinds_owned_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    original_link = mcp_bridge.os.link

    def fail_after_journal(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(mcp_bridge.os, "link", fail_after_journal)
    interrupted = False
    try:
        mcp_bridge.try_acquire_attachment(work, state, "job-interrupted")
    except KeyboardInterrupt:
        interrupted = True
    finally:
        monkeypatch.setattr(mcp_bridge.os, "link", original_link)
    assert interrupted
    assert not (work / ".omp").exists()
    assert not list((state / "mcp-attachments").glob("*.json"))


def test_attachment_registers_and_cleans_owned_artifact(tmp_path: Path) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    target = work / "target"
    target.write_text("owned target", encoding="utf-8")
    attachment = mcp_bridge.try_acquire_attachment(
        work, tmp_path / "state", "job-artifact"
    )
    skills_dir = work / ".omp" / "skills"
    skills_dir.mkdir()
    artifact = skills_dir / "python"
    artifact.symlink_to(target)
    directory = skills_dir.stat(follow_symlinks=False)
    prepared = artifact.stat(follow_symlinks=False)
    attachment.register_artifact(".omp/skills", (directory.st_dev, directory.st_ino))
    attachment.register_artifact(
        ".omp/skills/python", (prepared.st_dev, prepared.st_ino)
    )
    journal = json.loads(attachment._journal_path.read_text(encoding="utf-8"))
    assert journal["artifacts"][".omp/skills"] == [directory.st_dev, directory.st_ino]
    assert journal["artifacts"][".omp/skills/python"] == [
        prepared.st_dev,
        prepared.st_ino,
    ]

    assert attachment.release() == []
    assert not artifact.exists()
    assert not skills_dir.exists()
    assert not (work / ".omp").exists()
    assert target.read_text(encoding="utf-8") == "owned target"


def test_attachment_preserves_in_place_modified_config(tmp_path: Path) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    attachment = mcp_bridge.try_acquire_attachment(
        work, tmp_path / "state", "job-modified"
    )
    config = work / ".omp" / "mcp.json"
    identity = config.stat().st_ino
    config.write_bytes(config.read_bytes() + b" ")

    conflicts = attachment.release()

    assert any(
        "mcp.json" in conflict and "content" in conflict for conflict in conflicts
    )
    assert config.stat().st_ino == identity
    assert config.read_bytes().endswith(b" ")
    assert attachment._journal_path.exists()


def test_recovery_preserves_in_place_modified_config(tmp_path: Path) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    attachment = mcp_bridge.try_acquire_attachment(work, state, "job-recovery-modified")
    config = work / ".omp" / "mcp.json"
    identity = config.stat().st_ino
    config.write_bytes(config.read_bytes() + b" ")
    boot_id = (
        Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    )
    attachment._owner_identity = {
        "pid": 999999999,
        "start_time": "1",
        "boot_id": boot_id,
    }
    attachment._write_journal(None)
    os.close(attachment._omp_fd)
    os.close(attachment._parent_fd)

    conflicts = mcp_bridge.recover_attachments(state, work)

    assert any(
        "mcp.json" in conflict and "content" in conflict for conflict in conflicts
    )
    assert config.stat().st_ino == identity
    assert config.read_bytes().endswith(b" ")
    assert list((state / "mcp-attachments").glob("*.json"))


def test_attachment_preserves_replaced_nested_user_artifact(tmp_path: Path) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    first_target = work / "first"
    user_target = work / "user"
    first_target.write_text("owned", encoding="utf-8")
    user_target.write_text("user", encoding="utf-8")
    attachment = mcp_bridge.try_acquire_attachment(
        work, tmp_path / "state", "job-conflict"
    )
    skills_dir = work / ".omp" / "skills"
    skills_dir.mkdir()
    artifact = skills_dir / "python"
    artifact.symlink_to(first_target)
    identity = artifact.stat(follow_symlinks=False)
    attachment.register_artifact(
        ".omp/skills/python", (identity.st_dev, identity.st_ino)
    )

    artifact.unlink()
    artifact.symlink_to(user_target)
    assert attachment.release()
    assert artifact.resolve() == user_target
    assert (work / ".omp" / "mcp.json").is_file()
    assert user_target.read_text(encoding="utf-8") == "user"


def test_attachment_pins_artifacts_so_replacements_never_share_an_inode(
    tmp_path: Path,
) -> None:
    # ext4 and tmpfs hand a freed inode number straight to the next entry, so a
    # bare (st_dev, st_ino) check would mistake a user's replacement for the
    # owned artifact. The lease keeps every registered inode allocated.
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    attachment = mcp_bridge.try_acquire_attachment(work, tmp_path / "state", "job-pin")
    attachment.create_directory_artifact(".omp/skills")
    link = work / ".omp" / "skills" / "python"
    owned = attachment.create_symlink_artifact(".omp/skills/python", "owned")
    skills = str(work / ".omp" / "skills")

    def held_under_skills() -> int:
        held = 0
        for fd in os.listdir("/proc/self/fd"):
            with contextlib.suppress(OSError):
                held += os.readlink(f"/proc/self/fd/{fd}").startswith(skills)
        return held

    assert held_under_skills() == 2

    for _ in range(50):
        link.unlink()
        link.symlink_to("user")
        replaced = link.stat(follow_symlinks=False)
        assert (replaced.st_dev, replaced.st_ino) != owned

    assert attachment.release() == [".omp/skills/python artifact identity changed"]
    assert os.readlink(link) == "user"
    assert held_under_skills() == 0


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


def test_cancelled_engine_turn_consumes_interrupt_before_next_message(
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

    flag = InterruptFlag()
    model = ScriptedModel([{"role": "assistant", "content": "worked"}])
    agent = Agent(model=model, interrupt=flag, force_engine=True)
    result: list = []
    worker = threading.Thread(
        target=lambda: result.append(run_conversation(agent, "cancel me")),
        daemon=True,
    )
    worker.start()
    log = Path(env["OMEGA_FAKE_OMP_LOG"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "prompt" not in _omp_commands(log):
        time.sleep(0.01)
    assert "prompt" in _omp_commands(log)
    flag.set("stop this turn")
    worker.join(7)

    assert not worker.is_alive()
    cancelled = result[0]
    assert cancelled["turn_exit_reason"] == "cancelled"
    assert cancelled["interrupted"] is True
    assert cancelled["interrupt_message"] == "stop this turn"
    assert flag.is_set() is False
    assert flag.message is None

    agent.force_engine = False
    next_turn = run_conversation(agent, "next message")
    assert next_turn["final_response"] == "worked"
    assert next_turn["completed"] is True
    assert next_turn["interrupted"] is False
    assert model.call_count == 1


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


def test_cancel_drain_is_bounded_while_producer_keeps_enqueuing(
    tmp_path: Path, monkeypatch
) -> None:
    from omega_prime.engine import omp_driver
    from omega_prime.engine.omp_driver import _accept_prompt

    snapshot = [
        {"type": "message_update", "delta": {"text": "queued-one"}},
        {"type": "message_update", "delta": {"text": "queued-two"}},
    ]

    class NoisyQueue(queue.Queue[str]):
        def __init__(self) -> None:
            super().__init__()
            self.produced = 0
            self.remaining = 100
            for event in snapshot:
                self.put(json.dumps(event))

        def get_nowait(self) -> str:
            item = super().get_nowait()
            if self.remaining:
                self.produced += 1
                self.remaining -= 1
                self.put(
                    json.dumps(
                        {
                            "type": "message_update",
                            "delta": {"text": f"later-{self.produced}"},
                        }
                    )
                )
            return item

    class NoisySession:
        def __init__(self) -> None:
            self.agent_ended = False
            self.events_path = tmp_path / "noisy-cancel.jsonl"
            self.events: list[dict] = []
            self._queue = NoisyQueue()
            self._abort_sent = False

        def _next(self, timeout: float):
            return None

        def _raise_stream_failure(self) -> None:
            raise AssertionError("no stream failure was queued")

        def abort_once(self) -> None:
            OmpSession.abort_once(cast(OmpSession, self))

    session = NoisySession()
    aborts: list[tuple[int, int]] = []

    def record_abort(actual: OmpSession) -> None:
        aborts.append((len(actual.events), actual._queue.qsize()))

    monkeypatch.setattr(omp_driver, "session_abort", record_abort)
    interrupt = InterruptFlag()
    interrupt.set("stop")

    try:
        _accept_prompt(cast(OmpSession, session), "prompt-id", interrupt)
    except EngineError as exc:
        assert exc.reason == "cancelled"
    else:
        raise AssertionError("prompt cancellation was not reported")

    session.abort_once()

    assert aborts == [(2, 2)]
    assert session._queue.produced == 2
    assert session._queue.remaining == 98
    assert session.events == snapshot
    assert _events(session.events_path) == snapshot


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


def test_cli_ui_status_renders_attachment_and_cleanup_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from omega_prime.engine.cli import main as cli_main

    state = tmp_path / "state"
    job = state / "jobs" / "job-status"
    job.mkdir(parents=True)
    events_path = job / "events.jsonl"
    append_event(
        events_path,
        {"type": "ui_control_attachment", "status": "attached"},
    )
    append_event(
        events_path,
        {
            "type": "mcp_attachment",
            "status": "attached",
            "cleanup_conflicts": ["cleanup failed: PermissionError"],
        },
    )
    # Keep relevant events visible even when ordinary watch events are newer.
    for _ in range(25):
        append_event(events_path, {"type": "message_update", "text": "noise"})

    assert (
        cli_main(["ui", "status", "--job", "job-status", "--state-dir", str(state)])
        == 0
    )
    status_output = capsys.readouterr().out
    payload = json.loads(status_output)
    assert "ui_control_attachment status=attached" in status_output
    assert "mcp_attachment status=attached" in status_output
    assert any(
        event
        == 'mcp_attachment status=attached cleanup_conflicts=["cleanup failed: PermissionError"]'
        for event in payload["events"]
    )


def test_rpc_ui_cancel_after_queued_agent_end_returns_the_finished_turn(
    tmp_path: Path,
) -> None:
    from omega_prime.engine.omp_driver import _accept_prompt_ui

    class RaceSession:
        def __init__(self) -> None:
            self.agent_ended = False
            self.events_path = tmp_path / "ui-race.jsonl"
            self.events: list[dict] = []
            self._queue: queue.Queue[str] = queue.Queue()
            self._queue.put(json.dumps({"type": "agent_end"}))
            self.sent: list[str] = []

        def _next(self, timeout: float):
            return None

        def send(self, kind: str, **_: Any) -> str:
            self.sent.append(kind)
            return "id"

        def _raise_stream_failure(self):
            raise AssertionError("completion is already queued")

    flag = InterruptFlag()
    flag.set("stop")
    session = RaceSession()
    commands: list[str] = []
    summary = {
        "status": "attached",
        "pending": 0,
        "answered": 0,
        "rejected_stale": 0,
        "aborted": 0,
    }
    with _ui_store(tmp_path) as store:
        _accept_prompt_ui(
            cast(OmpSession, session),
            "prompt-id",
            math.inf,
            job_id="job-ui-race",
            session_generation="s" * 32,
            turn_generation="t" * 32,
            ui_control=store,
            clarify_broker=None,
            cancel_event=flag,
            events_path=session.events_path,
            commands=commands,
            ui_summary=summary,
        )
    assert session.agent_ended
    assert "abort" not in session.sent and "abort" not in commands
    assert summary["aborted"] == 0


def test_ui_pending_event_keeps_each_ask_question_and_its_options() -> None:
    from omega_prime.engine.omp_driver import _ui_event_summary
    from omega_prime.engine.protocol import AskOption, AskQuestion, AskRequest

    summary = _ui_event_summary(
        AskRequest(
            id="ask-1",
            questions=(
                AskQuestion(
                    "q-a", "Which branch?", (AskOption("main"), AskOption("dev"))
                ),
                AskQuestion("q-b", "Ship it?", (AskOption("yes"), AskOption("no"))),
            ),
        )
    )
    assert summary["method"] == "ask"
    assert summary["questions"] == [
        {"id": "q-a", "question": "Which branch?", "options": ["main", "dev"]},
        {"id": "q-b", "question": "Ship it?", "options": ["yes", "no"]},
    ]
    from omega_prime.engine.watch import format_event

    rendered = format_event({"type": "ui_pending", "request": summary})
    assert "Ship it?" in rendered and '"yes"' in rendered


def test_mcp_child_gets_the_shared_agent_folder_outside_ui_control(
    tmp_path: Path,
) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    attachment = mcp_bridge.try_acquire_attachment(work, state, "job-mcp-state")
    try:
        config = json.loads((work / ".omp" / "mcp.json").read_text(encoding="utf-8"))
    finally:
        attachment.release()
    entry = config["mcpServers"]["omega-prime"]
    assert entry["env"]["OMEGA_PRIME_AGENT_STATE_DIR"] == str(state / "agent")
    assert entry["env"]["OMEGA_PRIME_STATE_DIR"] == str(state / "agent")

    binary = _install_fake(tmp_path)
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
    result = run("PONG", cwd=work, env=env, job_id="job-mcp-bind", stage="plan")
    assert result.ok is True
    args = (tmp_path / "bwrap.txt").read_text(encoding="utf-8").splitlines()
    binds = [args[i + 1] for i, item in enumerate(args) if item == "--bind"]
    assert str(state / "agent") in binds
    assert str(state) not in binds
    assert not any(
        str(state / "ui-control") == bind
        or str(state / "ui-control").startswith(bind.rstrip("/") + "/")
        for bind in binds
    )
    # The operator control store still accepts this bind set.
    UIControlStore(state, writable_binds=[Path(bind) for bind in binds]).close()


def test_recovery_conflicts_reach_the_run_result_and_session_record(
    tmp_path: Path,
) -> None:
    from omega_prime.engine import mcp_bridge

    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    attachment = mcp_bridge.try_acquire_attachment(work, state, "job-old-run")
    config = work / ".omp" / "mcp.json"
    config.write_bytes(config.read_bytes() + b" ")
    boot_id = (
        Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    )
    attachment._owner_identity = {
        "pid": 999999999,
        "start_time": "1",
        "boot_id": boot_id,
    }
    attachment._write_journal(None)
    os.close(attachment._omp_fd)
    os.close(attachment._parent_fd)

    binary = _install_fake(tmp_path)
    env = _env(tmp_path, binary)
    result = run("PONG", cwd=work, env=env, job_id="job-new-run")

    assert result.ok is True
    assert any("mcp.json" in item for item in result.cleanup_conflicts)
    session = json.loads(
        (state / "jobs" / "job-new-run" / "session.json").read_text(encoding="utf-8")
    )
    assert session["cleanup_conflicts"] == result.cleanup_conflicts
    events = _events(state / "jobs" / "job-new-run" / "events.jsonl")
    assert any(
        event.get("type") == "mcp_attachment" and event.get("cleanup_conflicts")
        for event in events
    )
    assert config.read_bytes().endswith(b" ")


def test_relative_state_dir_gives_an_absolute_agent_bind(
    tmp_path: Path, monkeypatch
) -> None:
    from omega_prime.engine.jobs import agent_data_dir

    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    resolved = agent_data_dir({"OMEGA_PRIME_STATE_DIR": "state"})
    assert resolved.is_absolute()
    assert resolved == caller / "state" / "agent"
    explicit = agent_data_dir(
        {"OMEGA_PRIME_STATE_DIR": "state", "OMEGA_PRIME_AGENT_STATE_DIR": "data"}
    )
    assert explicit == caller / "data"


def test_sandbox_never_mounts_settings_secrets_or_dashboard_writable(
    tmp_path: Path,
) -> None:
    """Only the agent folder is writable; operator files are hidden."""
    state = tmp_path / "state"
    (state / "secrets").mkdir(parents=True)
    (state / "secrets" / "TOKEN").write_text("x", encoding="utf-8")
    (state / "dashboard").mkdir()
    (state / "settings.json").write_text("{}", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    binary = _install_fake(tmp_path)
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
    result = run("PONG", cwd=work, env=env, job_id="job-private", stage="plan")
    assert result.ok is True
    args = (tmp_path / "bwrap.txt").read_text(encoding="utf-8").splitlines()
    args = args[: args.index("--")]
    binds = [args[i + 1] for i, item in enumerate(args) if item == "--bind"]
    assert str(state / "agent") in binds
    for name in ("secrets", "dashboard", "settings.json"):
        private = str(state / name)
        assert not any(
            private == bind or private.startswith(bind.rstrip("/") + "/")
            for bind in binds
        ), (name, binds)
    tmpfs = [args[i + 1] for i, item in enumerate(args) if item == "--tmpfs"]
    remount = [args[i + 1] for i, item in enumerate(args) if item == "--remount-ro"]
    assert str(state / "secrets") in tmpfs and str(state / "secrets") in remount
    assert str(state / "dashboard") in tmpfs and str(state / "dashboard") in remount
    masked = [
        args[i + 2]
        for i, item in enumerate(args)
        if item == "--ro-bind" and args[i + 1] == "/dev/null"
    ]
    assert str(state / "settings.json") in masked
    # Masks come after every writable bind, so a later bind cannot reopen them.
    last_bind = max(i for i, item in enumerate(args) if item == "--bind")
    first_mask = min(
        i
        for i, item in enumerate(args)
        if item == "--tmpfs" or (item == "--ro-bind" and args[i + 1] == "/dev/null")
    )
    assert first_mask > last_bind


def test_sandbox_refuses_a_writable_bind_inside_a_private_path(tmp_path: Path) -> None:
    from omega_prime.engine.sandbox import SandboxUnavailable, wrap

    secrets = tmp_path / "state" / "secrets"
    secrets.mkdir(parents=True)
    bwrap = tmp_path / "bwrap"
    bwrap.write_text("#!/bin/sh\n", encoding="utf-8")
    bwrap.chmod(0o755)
    with pytest.raises(SandboxUnavailable):
        wrap(
            ["omp"],
            env={"OMEGA_PRIME_BWRAP_BIN": str(bwrap)},
            binds=[secrets / "inner"],
            hidden=[secrets],
        )

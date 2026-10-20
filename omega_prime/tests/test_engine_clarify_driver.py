# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Provider-free end-to-end test for MCP clarification through rpc-ui."""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from omega_prime.agent.interrupt import InterruptFlag
from omega_prime.engine import attach, omp_driver
from omega_prime.engine.plan_router import Choice, Decision
from omega_prime.engine.ui_control import UIControlStore


class _CancelFlag(omp_driver._CancelEvent):
    def __init__(self) -> None:
        self._event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()


def _fake_omp(path: Path) -> None:
    source = f"""#!{sys.executable}
import json
import queue
import sys
import threading
from pathlib import Path

from omega_prime.engine.clarify_ipc import ClarifySocketClient


def send(event):
    sys.stdout.write(json.dumps(event) + "\\n")
    sys.stdout.flush()


project = Path(sys.argv[sys.argv.index("--cwd") + 1])
commands = queue.Queue()
answers = queue.Queue()

def read_commands():
    for line in sys.stdin:
        commands.put(json.loads(line))

reader = threading.Thread(target=read_commands, daemon=True)
reader.start()
send({{"type": "ready"}})
while True:
    command = commands.get()
    kind = command["type"]
    if kind == "get_state":
        send({{"type": "response", "id": command["id"], "success": True,
              "data": {{"dumpTools": ["mcp__omega-prime_omega_command"]}}}})
    elif kind == "prompt":
        config = json.loads((project / ".omp" / "mcp.json").read_text())
        socket_path = config["mcpServers"]["omega-prime"]["env"]["OMEGA_PRIME_CLARIFY_SOCKET"]
        def ask():
            try:
                answers.put(ClarifySocketClient(socket_path).ask("Which option?"))
            except Exception:
                answers.put("")
        threading.Thread(target=ask, daemon=True).start()
        aborted = False
        while True:
            try:
                answer = answers.get_nowait()
                break
            except queue.Empty:
                try:
                    followup = commands.get(timeout=0.01)
                except queue.Empty:
                    continue
                if followup["type"] == "abort":
                    send({{"type": "response", "id": followup["id"], "success": True}})
                    send({{"type": "agent_end"}})
                    aborted = True
                    break
        if not aborted:
            send({{"type": "message_end", "message": {{"role": "assistant", "content": answer}}}})
            send({{"type": "response", "id": command["id"], "success": True}})
            send({{"type": "agent_end"}})
    elif kind == "get_session_stats":
        send({{"type": "response", "id": command["id"], "success": True, "data": {{}}}})
        break
    else:
        send({{"type": "response", "id": command["id"], "success": True}})
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def test_rpc_ui_routes_mcp_clarify_to_one_operator_answer(tmp_path: Path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    state = tmp_path / ("state-" + "s" * 40) / ("nested-" + "n" * 40)
    state.mkdir(mode=0o700, parents=True)
    job_id = "j" * 64
    binary = tmp_path / "fake-omp"
    _fake_omp(binary)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "OMEGA_PRIME_STATE_DIR": str(state),
        "OMEGA_PRIME_OMP_BIN": str(binary),
    }
    choice = Choice(
        Decision(
            "chatgpt", "openai-codex", "gpt-6.1-sol", True, "test", "allowed", "test"
        ),
        None,
    )
    monkeypatch.setattr(omp_driver, "omp_version", lambda *_args: (18, 12, 3))
    monkeypatch.setattr(omp_driver, "read_usage", lambda *_args: {})
    monkeypatch.setattr(omp_driver, "select", lambda *_args, **_kwargs: choice)
    monkeypatch.setattr(omp_driver, "sandbox_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        omp_driver, "wrap", lambda argv, **_kwargs: (list(argv), "test")
    )

    result_box = []
    cancel = _CancelFlag()

    def execute() -> None:
        with UIControlStore(
            state,
            writable_binds=[state / "jobs" / job_id, work],
        ) as driver_store:
            result_box.append(
                omp_driver.run(
                    "Ask the operator",
                    job_id=job_id,
                    cwd=work,
                    mode="rpc-ui",
                    ui_control=driver_store,
                    cancel_event=cancel,
                    env=env,
                    tmux=False,
                    unsandboxed=True,
                )
            )

    foreign_identity = {
        "job_id": "foreign-job",
        "session_generation": "foreign-session",
        "turn_generation": "foreign-turn",
        "request_id": "foreign-request",
    }
    with UIControlStore(
        state,
        writable_binds=[state / "jobs" / job_id, work],
    ) as operator:
        operator.register_request(
            foreign_identity, {"method": "ask", "questions": [{"id": "q"}]}
        )
        operator.answer(foreign_identity, "text", "not for this session")
    worker = threading.Thread(target=execute)
    worker.start()
    pending: list[dict[str, Any]] = []
    try:
        with UIControlStore(
            state,
            writable_binds=[state / "jobs" / job_id, work],
        ) as operator:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                pending = operator.list_pending(job=job_id)
                if pending:
                    break
                time.sleep(0.01)
            assert pending
            assert pending[0]["kind"] == "ask"
            assert pending[0]["prompt"] == "Which option?"
            operator.answer(pending[0]["identity"], "text", "choose safe")
            worker.join(timeout=5)
            assert not worker.is_alive(), "rpc-ui clarification did not complete"
            assert operator.status()["queued_commands"] == 1
    finally:
        if worker.is_alive():
            cancel.set()
            worker.join(timeout=5)

    assert len(result_box) == 1
    result = result_box[0]
    assert result.ok
    assert result.final_text == "choose safe"
    assert result.mcp_status == "attached"
    assert result.ui_summary["answered"] == 1
    assert not (work / ".omp").exists()


def test_interactive_route_supplies_safe_rpc_ui_store_and_interrupt(
    tmp_path: Path, monkeypatch
):
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    interrupt = InterruptFlag()
    agent = SimpleNamespace(
        force_engine=True,
        engine_cwd=work,
        engine_job_id="job-route",
        engine_full_tools=True,
        interrupt=interrupt,
        plan_mode=False,
        child_tool_hook=None,
        tool_roster=None,
        model=None,
    )
    env = {
        "OMEGA_PRIME_ENGINE": "omp",
        "OMEGA_PRIME_STATE_DIR": str(state),
        "HOME": str(home),
    }
    observed: dict[str, Any] = {}

    def fake_run(_prompt: str, **kwargs: Any) -> omp_driver.EngineResult:
        observed.update(kwargs)
        assert isinstance(kwargs["ui_control"], UIControlStore)
        assert kwargs["ui_control"].status() == {
            "pending": [],
            "pending_count": 0,
            "queued_commands": 0,
        }
        return omp_driver.EngineResult(True, "job-route", final_text="done")

    monkeypatch.setattr(attach, "run", fake_run)
    result = attach.route_turn(
        agent,
        "hello",
        system_message=None,
        conversation_history=[],
        env=env,
    )

    assert result is not None
    assert result["final_response"] == "done"
    assert observed["mode"] == "rpc-ui"
    assert observed["cancel_event"] is interrupt


def test_interrupt_during_skill_attach_releases_acquired_attachment(
    tmp_path: Path, monkeypatch
):
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    binary = tmp_path / "fake-omp"
    _fake_omp(binary)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "OMEGA_PRIME_STATE_DIR": str(state),
        "OMEGA_PRIME_OMP_BIN": str(binary),
    }
    choice = Choice(
        Decision(
            "chatgpt", "openai-codex", "gpt-6.1-sol", True, "test", "allowed", "test"
        ),
        None,
    )
    monkeypatch.setattr(omp_driver, "omp_version", lambda *_args: (18, 12, 3))
    monkeypatch.setattr(omp_driver, "read_usage", lambda *_args: {})
    monkeypatch.setattr(omp_driver, "select", lambda *_args, **_kwargs: choice)
    monkeypatch.setattr(omp_driver, "sandbox_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        omp_driver, "wrap", lambda argv, **_kwargs: (list(argv), "test")
    )
    released: list[bool] = []
    attachment = SimpleNamespace(release=lambda: released.append(True))
    monkeypatch.setattr(omp_driver, "recover_attachments", lambda *_args: None)
    monkeypatch.setattr(
        omp_driver, "try_acquire_attachment", lambda *_args, **_kwargs: attachment
    )

    def interrupt_attach(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(omp_driver, "attach_skills", interrupt_attach)
    with UIControlStore(
        state, writable_binds=[state / "jobs" / "job-interrupt", work]
    ) as control:
        try:
            omp_driver.run(
                "interrupt while attaching",
                job_id="job-interrupt",
                cwd=work,
                mode="rpc-ui",
                ui_control=control,
                env=env,
                tmux=False,
                unsandboxed=True,
            )
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("attach interruption did not propagate")

    assert released == [True]

"""Phase 10: sessions, tasks, modes, extensions, autolearn, and controls."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.extensions import ExtensionHooks
from omega_prime.agent.model import ScriptedModel
from omega_prime.agent.modes import PLAN_MODE_BLOCKED, apply_plan_mode
from omega_prime.agent.security import screen_argv
from omega_prime.learning.advisor import advise, format_advisories
from omega_prime.learning.autolearn import Autolearn
from omega_prime.learning.goals import GoalStore
from omega_prime.session.persist import load_session, load_task, save_session, save_task
from omega_prime.skills_runtime.manager import skill_view
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.exec_jobs import JobControl, register_exec_job_tools
from omega_prime.tools.registry import ToolRegistry


def _tool_call(name: str, arguments: str = "{}", call_id: str = "call-1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def test_saved_session_and_task_load_on_a_later_run(tmp_path: Path):
    store = tmp_path / "store"
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    session_path = save_session(store, "s1", messages, {"model": "fake"})
    task_path = save_task(store, "t1", "Fix it", status="doing", notes="halfway")

    assert Path(session_path).is_file()
    assert Path(task_path).is_file()

    session = load_session(store, "s1")
    task = load_task(store, "t1")
    assert session["messages"] == messages
    assert session["metadata"] == {"model": "fake"}
    assert task["title"] == "Fix it"
    assert task["status"] == "doing"
    assert task["notes"] == "halfway"

    with pytest.raises(KeyError):
        load_session(store, "nope")
    with pytest.raises(KeyError):
        load_task(store, "nope")
    with pytest.raises(ValueError):
        save_task(store, "t2", "Bad", status="vibing")


def test_plan_mode_rejects_a_write_and_keeps_reads(tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("original", encoding="utf-8")
    writes = []

    def write_file(path: str, content: str) -> str:
        writes.append((path, content))
        target.write_text(content, encoding="utf-8")
        return "written"

    def read_file(path: str) -> str:
        return target.read_text(encoding="utf-8")

    assert "write_file" in PLAN_MODE_BLOCKED
    assert "read_file" not in PLAN_MODE_BLOCKED

    tools = {"write_file": write_file, "read_file": read_file}
    model = ScriptedModel(
        [
            _tool_call("write_file", '{"path": "note.txt", "content": "changed"}'),
            _tool_call("read_file", '{"path": "note.txt"}'),
            {"role": "assistant", "content": "done"},
        ]
    )
    agent = Agent(model=model, tools=tools, max_iterations=6, plan_mode=True)
    result = run_conversation(agent, "change it, then read it")

    assert agent.tools is tools
    assert writes == []
    assert target.read_text(encoding="utf-8") == "original"
    rows = [row for row in result["messages"] if row.get("role") == "tool"]
    assert rows[0]["content"] == "error: plan mode forbids write_file"
    assert rows[0]["is_error"] is True
    assert rows[1]["content"] == "original"
    assert rows[1].get("is_error") is not True


def test_apply_plan_mode_keeps_unblocked_identities():
    def read_file():
        return "r"

    def write_file():
        return "w"

    wrapped = apply_plan_mode({"read_file": read_file, "write_file": write_file})

    assert wrapped["read_file"] is read_file
    assert wrapped["write_file"]() == "error: plan mode forbids write_file"


def test_extension_hook_runs_before_the_model_call():
    seen = []
    hooks = ExtensionHooks()

    def annotate(messages, tools):
        seen.append(len(messages))
        messages.append({"role": "user", "content": "extension-note"})

    hooks.register_before_model(annotate)
    model = ScriptedModel([{"role": "assistant", "content": "done"}])
    agent = Agent(model=model, tools={}, max_iterations=4, extensions=hooks)
    result = run_conversation(agent, "ping")

    assert result["final_response"] == "done"
    assert seen == [2]
    assert model.seen[0][-1] == {"role": "user", "content": "extension-note"}


def test_first_extension_stop_wins_and_skips_the_model():
    ran = []
    hooks = ExtensionHooks()

    def _first(messages: object, tools: object) -> dict:
        ran.append("first")
        return {"stop": True, "reason": "ext"}

    def _second(messages: object, tools: object) -> dict:
        ran.append("second")
        return {"stop": True}

    hooks.register_before_model(_first)
    hooks.register_before_model(_second)
    model = ScriptedModel([{"role": "assistant", "content": "never"}])
    agent = Agent(model=model, tools={}, max_iterations=4, extensions=hooks)
    result = run_conversation(agent, "ping")

    assert ran == ["first"]
    assert model.call_count == 0
    assert result["turn_exit_reason"] == "ext"


def test_autolearn_writes_through_the_skill_manager(tmp_path: Path):
    skills = tmp_path / "skills"
    learner = Autolearn(skills, min_tool_calls=2)

    skipped = learner.consider_turn(0, "latin", "# latin\n")
    assert skipped == {"learned": False, "reason": "fewer than 2 tool calls"}
    assert not skills.exists()

    nouns = (
        "---\nname: latin\ndescription: Latin help.\n---\n# latin\n\nDecline nouns.\n"
    )
    verbs = (
        "---\nname: latin\ndescription: Latin help.\n---\n# latin\n\nDecline verbs.\n"
    )
    first = learner.consider_turn(2, "latin", nouns)
    assert first == {"learned": True, "skill": "latin"}
    viewed = json.loads(skill_view("latin", skills_root=skills))
    assert "Decline nouns." in viewed["content"]

    second = learner.consider_turn(3, "latin", verbs)
    assert second == {"learned": True, "skill": "latin"}
    updated = json.loads(skill_view("latin", skills_root=skills))
    assert "Decline verbs." in updated["content"]


def test_goals_track_an_objective_with_steps(tmp_path: Path):
    store = GoalStore(tmp_path / "goals")
    store.set_objective("Ship it")
    store.add_step("Write")
    store.add_step("Test")
    store.complete_step(0)

    status = store.status()
    assert status["objective"] == "Ship it"
    assert status["steps"] == [
        {"text": "Write", "done": True},
        {"text": "Test", "done": False},
    ]

    reopened = GoalStore(tmp_path / "goals")
    assert reopened.status() == status

    with pytest.raises(IndexError):
        store.complete_step(9)


def test_advisor_renders_escaped_blocks():
    rendered = format_advisories(
        [
            {"note": "Fix <this> & that", "severity": "concern"},
            {"note": "Ship it", "severity": "blocker", "advisor": 'qa"x'},
        ]
    )

    assert rendered.splitlines() == [
        '<advisory severity="concern">Fix &lt;this&gt; &amp; that</advisory>',
        '<advisory severity="blocker" advisor="qa&quot;x">Ship it</advisory>',
    ]

    one = advise("Tidy", severity="nit")
    assert one["rendered"] == '<advisory severity="nit">Tidy</advisory>'

    with pytest.raises(ValueError):
        format_advisories([{"note": "x", "severity": "yikes"}])


def test_job_control_runs_polls_and_kills(tmp_path: Path):
    jobs = JobControl(tmp_path)

    quick = jobs.start([sys.executable, "-c", "print('hi')"])
    done = jobs.wait(quick, timeout=10)
    assert done["running"] is False
    assert done["exit_code"] == 0
    assert done["stdout"] == "hi\n"
    assert jobs.wait(quick)["stdout"] == "hi\n"

    with pytest.raises(ValueError, match="job refused"):
        jobs.start(["sudo", "true"])

    sleeper = jobs.start([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert jobs.poll(sleeper)["running"] is True
    finally:
        killed = jobs.kill(sleeper)
    assert killed["running"] is False

    with pytest.raises(KeyError):
        jobs.poll("job-999")


def test_exec_job_tools_use_approval_gated_registry_dispatch(tmp_path: Path):
    approval_log = ApprovalLog()
    registry = ToolRegistry(approval_log=approval_log)
    register_exec_job_tools(registry, tmp_path)
    names = ("exec_job_start", "exec_job_poll", "exec_job_wait", "exec_job_kill")
    assert all(registry.approval_required(name) for name in names)

    marker = tmp_path / "should-not-exist"
    argv = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch()",
    ]
    denied = json.loads(registry.dispatch("exec_job_start", {"argv": argv}))
    assert denied == {"error": "approval required", "tool": "exec_job_start"}
    assert marker.exists() is False

    for name in names:
        assert approval_log.approve(name, "operator")["approved"] is True
    started = json.loads(
        registry.dispatch(
            "exec_job_start",
            {"argv": [sys.executable, "-c", "print('job-output')"]},
        )
    )
    job_id = started["id"]
    assert (
        json.loads(registry.dispatch("exec_job_poll", {"job_id": job_id}))["id"]
        == job_id
    )
    completed = json.loads(
        registry.dispatch("exec_job_wait", {"job_id": job_id, "timeout": 5})
    )
    assert completed["stdout"] == "job-output\n"
    assert (
        json.loads(registry.dispatch("exec_job_wait", {"job_id": job_id})) == completed
    )

    another = ToolRegistry(approval_log=approval_log)

    class DenyExecStart:
        def allows_tool(self, name: str) -> bool:
            return name != "exec_job_start"

    policy = DenyExecStart()
    policy_registry = ToolRegistry(approval_log=approval_log, policy=policy)
    register_exec_job_tools(policy_registry, tmp_path)
    policy_marker = tmp_path / "policy-should-not-exist"
    policy_denied = json.loads(
        policy_registry.dispatch(
            "exec_job_start",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(policy_marker)!r}).touch()",
                ]
            },
        )
    )
    assert policy_denied == {
        "error": "policy forbids exec_job_start",
        "tool": "exec_job_start",
    }
    assert policy_marker.exists() is False

    policy_denied_job_id = "job-1"
    denied_kill = json.loads(
        policy_registry.dispatch("exec_job_kill", {"job_id": policy_denied_job_id})
    )
    assert denied_kill == {"error": f"KeyError: 'unknown job: {policy_denied_job_id}'"}
    register_exec_job_tools(another, tmp_path)
    assert json.loads(another.dispatch("exec_job_poll", {"job_id": job_id})) == {
        "error": f"KeyError: 'unknown job: {job_id}'"
    }


def test_exec_job_wait_timeout_preserves_running_process_and_bounds_timeout(
    tmp_path: Path,
):
    jobs = JobControl(tmp_path)
    job_id = jobs.start([sys.executable, "-c", "import time; time.sleep(2)"])
    assert jobs.wait(job_id, timeout=0.01) == {"id": job_id, "running": True}
    assert jobs.poll(job_id) == {"id": job_id, "running": True}
    with pytest.raises(ValueError):
        jobs.wait(job_id, timeout=31)
    try:
        assert jobs.kill(job_id)["running"] is False
    finally:
        if jobs.poll(job_id)["running"]:
            jobs.kill(job_id)


def test_exec_job_kill_escalates_when_terminate_does_not_finish(tmp_path: Path):
    import signal
    import time

    jobs = JobControl(tmp_path)
    ready_file = tmp_path / "child-ready"
    job_id = jobs.start(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "pathlib.Path(sys.argv[1]).write_text('ready'); "
                "time.sleep(30)"
            ),
            str(ready_file),
        ]
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_file.exists(), "child did not finish starting"

        result = jobs.kill(job_id)
        assert result == {
            "id": job_id,
            "running": False,
            "exit_code": -signal.SIGKILL,
            "stdout": "",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
        assert jobs.poll(job_id) == result
    finally:
        jobs.shutdown()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_exec_job_kill_stops_group_worker_that_ignores_terminate(tmp_path: Path):
    """A worker that ignores SIGTERM and dropped the pipes still dies on kill."""
    import signal
    import time

    jobs = JobControl(tmp_path)
    pid_file = tmp_path / "worker-pid"
    worker = (
        "import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "null = os.open(os.devnull, os.O_RDWR); "
        "[os.dup2(null, fd) for fd in (0, 1, 2)]; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    leader = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL); "
        "time.sleep(60)"
    )
    job_id = jobs.start([sys.executable, "-c", leader, worker, str(pid_file)])
    worker_pid = 0
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), "worker did not start"
        worker_pid = int(pid_file.read_text())

        result = jobs.kill(job_id)
        assert result["running"] is False
        assert result["exit_code"] == -signal.SIGTERM

        deadline = time.monotonic() + 5
        alive = True
        while alive and time.monotonic() < deadline:
            try:
                os.kill(worker_pid, 0)
            except ProcessLookupError:
                alive = False
            else:
                time.sleep(0.02)
        assert alive is False, "SIGTERM-ignoring worker survived kill()"
    finally:
        if worker_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(worker_pid, signal.SIGKILL)
        jobs.shutdown()


def test_exec_job_drains_large_stdout_stderr_and_caches_concurrent_reaps(
    tmp_path: Path,
):
    jobs = JobControl(tmp_path)
    job_id = jobs.start(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('o' * 300000); "
            "sys.stderr.write('e' * 300000)",
        ]
    )
    barrier = threading.Barrier(5)
    results = []

    def wait_for_result():
        barrier.wait()
        results.append(jobs.wait(job_id, timeout=10))

    workers = [threading.Thread(target=wait_for_result) for _ in range(5)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(results) == 5
    assert all(result == results[0] for result in results)
    assert len(results[0]["stdout"]) == 300000
    assert len(results[0]["stderr"]) == 300000
    assert jobs.poll(job_id) == results[0]


@pytest.mark.parametrize("collection", ["poll", "wait", "kill"])
def test_exec_job_reports_per_stream_truncation_in_every_result(
    tmp_path: Path, collection: str
):
    import time

    jobs = JobControl(tmp_path)
    ready_file = tmp_path / "output-ready"
    script = (
        "import os, pathlib, sys, time; "
        "os.write(1, b'o' * (1024 * 1024 + 1)); "
        "os.write(2, b'e' * (1024 * 1024)); "
        "pathlib.Path(sys.argv[1]).write_text('ready'); "
        "time.sleep(30)"
        if collection == "kill"
        else (
            "import os, pathlib, sys; "
            "os.write(1, b'o' * (1024 * 1024 + 1)); "
            "os.write(2, b'e' * (1024 * 1024)); "
            "pathlib.Path(sys.argv[1]).write_text('ready')"
        )
    )
    job_id = jobs.start([sys.executable, "-c", script, str(ready_file)])
    if collection == "kill":
        deadline = time.monotonic() + 5
        while not ready_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_file.exists(), "child did not finish writing output"
        result = jobs.kill(job_id)
    elif collection == "wait":
        result = jobs.wait(job_id, timeout=10)
    else:
        deadline = time.monotonic() + 10
        result = jobs.poll(job_id)
        while result.get("running") and time.monotonic() < deadline:
            time.sleep(0.01)
            result = jobs.poll(job_id)

    assert result["running"] is False
    assert result["stdout"] == "o" * (1024 * 1024)
    assert result["stderr"] == "e" * (1024 * 1024)
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is False
    assert jobs.poll(job_id) == result
    assert jobs.wait(job_id, timeout=1) == result
    assert jobs.kill(job_id) == result


def test_plan_mode_blocks_exec_job_start_and_kill():
    called = []
    tools = {
        "exec_job_start": lambda **_kwargs: called.append("start"),
        "exec_job_kill": lambda **_kwargs: called.append("kill"),
        "exec_job_poll": lambda **_kwargs: called.append("poll"),
    }
    wrapped = apply_plan_mode(tools)
    assert wrapped["exec_job_start"]() == "error: plan mode forbids exec_job_start"
    assert wrapped["exec_job_kill"]() == "error: plan mode forbids exec_job_kill"
    assert "exec_job_start" in PLAN_MODE_BLOCKED
    assert "exec_job_kill" in PLAN_MODE_BLOCKED
    assert "exec_job_poll" not in PLAN_MODE_BLOCKED
    assert called == []


def test_screen_argv_refuses_danger_and_accepts_plain():
    assert screen_argv(["sudo", "true"]) == {
        "ok": False,
        "reason": "refused binary: sudo",
    }
    assert screen_argv(["rm", "-rf", "/"])["ok"] is False
    assert screen_argv(["rm", "--no-preserve-root", "x"])["ok"] is False
    assert screen_argv(["echo", "a\x00b"])["ok"] is False
    assert screen_argv("not-a-list")["ok"] is False
    assert screen_argv([sys.executable, "-c", "print(1)"]) == {"ok": True}

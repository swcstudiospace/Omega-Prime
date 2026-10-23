from __future__ import annotations

import json
import shlex
from collections.abc import Sequence

import pytest

from omega_prime.sandbox_control.herdr_pane import (
    HerdrAgentPane,
    agent_pane_argv,
    session_script,
    stop_session_argv,
)
from omega_prime.sandbox_control.provider import RunResult, SandboxControlError


def test_agent_pane_argv_is_interactive_tty_without_rpc_or_setsid() -> None:
    argv, tag = agent_pane_argv(
        "/os",
        "ompsbx-demo-abc123",
        model="zai/glm-4.6",
        thinking="low",
        tag="ab12cd34ef56",
    )
    assert tag == "ab12cd34ef56"
    assert argv[:6] == ["/os", "sandbox", "exec", "-n", "ompsbx-demo-abc123", "--tty"]
    assert "--mode" not in argv and "rpc-ui" not in argv
    assert "setsid" not in argv  # setsid would detach omp from the PTY
    tail = argv[argv.index("--") + 1 :]
    assert tail[:2] == ["sh", "-c"]
    assert tail[4:] == [
        "--model",
        "zai/glm-4.6",
        "--thinking",
        "low",
        "--approval-mode",
        "yolo",
    ]
    assert "TERM=xterm-256color" in argv
    assert ". /sandbox/home/.omp-sandbox.env" in tail[2]
    assert "/tmp/.ompsbx-ab12cd34ef56.sid" in tail[2]


def test_agent_pane_argv_resume_and_validation() -> None:
    argv, _ = agent_pane_argv("/os", "ompsbx-a-1", model="m", resume=True, yolo=False)
    assert argv[-1] == "--continue" and "yolo" not in argv
    with pytest.raises(SandboxControlError):
        agent_pane_argv("/os", "ompsbx-a-1", model="m; rm -rf /")
    with pytest.raises(SandboxControlError):
        agent_pane_argv("/os", "ompsbx-a-1", model="m", thinking="max")
    with pytest.raises(SandboxControlError):
        agent_pane_argv("/os", "evil", model="m")


def test_session_tag_must_be_hex() -> None:
    with pytest.raises(SandboxControlError):
        session_script("x; reboot")
    stop = stop_session_argv("/os", "ompsbx-a-1", "ab12cd34")
    assert 'pkill -KILL -s "$(cat /tmp/.ompsbx-ab12cd34.sid)"' in stop[-1]


class FakeHerdr:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> RunResult:
        self.calls.append(list(argv))
        if argv[1:3] == ["workspace", "create"]:
            payload = {
                "result": {
                    "root_pane": {"pane_id": "wX:p1"},
                    "workspace": {"workspace_id": "wX"},
                }
            }
            return RunResult(0, json.dumps(payload), "")
        return RunResult(0, "", "")


def test_launch_runs_quoted_command_and_reports_agent() -> None:
    herdr = FakeHerdr()
    pane = HerdrAgentPane("/herdr", herdr)
    argv, tag = agent_pane_argv("/os", "ompsbx-a-1", model="m", tag="ab12cd34")
    handle = pane.launch(argv, session_tag=tag, label="00045 demo", host_cwd="/w")
    assert handle.as_row() == {"pane_id": "wX:p1", "workspace": "wX"}
    run = next(c for c in herdr.calls if c[1:3] == ["pane", "run"])
    assert shlex.split(run[4]) == argv  # one shell-safe string
    report = next(c for c in herdr.calls if c[1:3] == ["pane", "report-agent"])
    assert report[report.index("--state") + 1] == "working"


def test_prompt_interrupt_stop_keys() -> None:
    herdr = FakeHerdr()
    pane = HerdrAgentPane("/herdr", herdr)
    pane.prompt("wX:p1", "/gsd-plan-phase MAGIC")
    pane.interrupt("wX:p1")
    pane.stop("wX:p1")
    keys = [c[4:] for c in herdr.calls if c[1:3] == ["pane", "send-keys"]]
    assert keys == [["Enter"], ["esc"], ["C-c", "C-c"]]
    with pytest.raises(SandboxControlError, match="multiline_prompt"):
        pane.prompt("wX:p1", "a\nb")


def test_herdr_failure_raises() -> None:
    pane = HerdrAgentPane("/herdr", lambda _argv: RunResult(1, "", "nope"))
    with pytest.raises(SandboxControlError, match="herdr_failed"):
        pane.report("wX:p1", "idle")

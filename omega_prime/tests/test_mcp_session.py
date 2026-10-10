"""Phase 75-10: canonical session client boundaries over the official SDK server.

Every fixture server imports ``MCPServer`` from ``mcp.server`` (the official
installed SDK; ``mcp_v2`` does not exist and ``FastMCP`` is obsolete) and all
effects are asserted on reopened bytes. No canned wire-echo or one-line
framing fixtures remain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import pytest

from omega_prime.tools.mcp_session import mcp_session_call

#: Runtime-assembled canaries so static secret scans do not flag the fixture.
_CANARY_RECOGNIZED = "omk" + "_test-canary-9f2c7a41d3e84b"
_CANARY_PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
_CANARY_PATH = os.path.join("canary-root-7f3a", "nested", "leave.txt")
_CANARY_GENERIC = "Gx7" + "Q2mZ9pL4vX8nB6cD3fH5jK1wE0Rt5YuI"
_GROK_CANARY = "grok-test-value-" + "c41d9e27b"
_OPENAI_CANARY = "openai-test-value-" + "d52e8f38c"
_AWS_CANARY = "aws-test-value-" + "e63f9a49d"

_ALL_CANARIES = (
    _CANARY_RECOGNIZED,
    _CANARY_PEM,
    _CANARY_PATH,
    _CANARY_GENERIC,
)

_FIXTURE_SERVER = """\
import asyncio
import json
import os
import sys

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

home = sys.argv[1]
server = MCPServer("phase75-fixture")


@server.tool()
def write_probe(name: str, text: str) -> str:
    with open(os.path.join(home, name), "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


@server.tool()
def read_probe(name: str) -> str:
    with open(os.path.join(home, name), encoding="utf-8") as handle:
        return handle.read()


@server.tool()
def oops_probe() -> str:
    raise ToolError("probe says nope")


@server.tool()
def fail_probe() -> str:
    raise RuntimeError("unexpected crash stays server-side")


@server.tool()
def dump_env() -> str:
    safe = {
        key: os.environ.get(key, "<absent>")
        for key in (
            "TMPDIR",
            "TEMP",
            "TMP",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_RUNTIME_DIR",
            "PYTHONDONTWRITEBYTECODE",
            "PATH",
            "HOME",
        )
    }
    secret = {
        key: ("present" if key in os.environ else "absent")
        for key in ("GROK_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY")
    }
    return json.dumps({"safe": safe, "secret": secret})


asyncio.run(server.run_stdio_async())
"""

_HANG_SERVER = """\
import os
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}")
time.sleep(30)
"""

_MALFORMED_SERVER = """\
import sys
import time

sys.stderr.write("STDERR " + sys.argv[1] + "\\n")
sys.stderr.write("STDERR " + sys.argv[2] + "\\n")
sys.stderr.write("STDERR " + sys.argv[3] + "\\n")
sys.stderr.flush()
sys.stdout.write("this is not json-rpc\\n")
sys.stdout.write(sys.argv[4] + " trailing garbage\\n")
sys.stdout.flush()
time.sleep(30)
"""


def _fixture(tmp_path: Path) -> list[str]:
    script = tmp_path / "fixture_server.py"
    script.write_text(_FIXTURE_SERVER, encoding="utf-8")
    return [sys.executable, str(script), str(tmp_path)]


def _is_error_flag(outcome: dict) -> bool:
    result = outcome.get("result", {})
    return bool(result.get("is_error", result.get("isError", False)))


def test_initialized_session_writes_and_reads_real_bytes(tmp_path: Path):
    command = _fixture(tmp_path)
    written = mcp_session_call(
        command,
        "write_probe",
        {"name": "probe.txt", "text": "actual local MCP file effect"},
        cwd=str(tmp_path),
        timeout=30,
    )
    assert "error" not in written
    assert written["result"]["content"] == [
        {"type": "text", "text": "actual local MCP file effect"}
    ]
    assert (tmp_path / "probe.txt").read_bytes() == b"actual local MCP file effect"
    json.dumps(written)

    read = mcp_session_call(
        command, "read_probe", {"name": "probe.txt"}, cwd=str(tmp_path), timeout=30
    )
    assert "error" not in read
    assert read["result"]["content"] == [
        {"type": "text", "text": "actual local MCP file effect"}
    ]


def test_same_real_effect_inside_running_loop(tmp_path: Path):
    command = _fixture(tmp_path)

    async def _main() -> dict:
        return mcp_session_call(
            command,
            "write_probe",
            {"name": "loop.txt", "text": "loop-effect-7"},
            cwd=str(tmp_path),
            timeout=30,
        )

    outcome = asyncio.run(_main())
    assert "error" not in outcome
    assert outcome["result"]["content"] == [{"type": "text", "text": "loop-effect-7"}]
    assert (tmp_path / "loop.txt").read_bytes() == b"loop-effect-7"
    json.dumps(outcome)


def test_tool_error_returns_error_and_result(tmp_path: Path):
    command = _fixture(tmp_path)
    deliberate = mcp_session_call(
        command, "oops_probe", {}, cwd=str(tmp_path), timeout=30
    )
    assert deliberate["error"] == "Error executing tool oops_probe: probe says nope"
    assert _is_error_flag(deliberate) is True

    crash = mcp_session_call(command, "fail_probe", {}, cwd=str(tmp_path), timeout=30)
    assert crash["error"] == "Error executing tool fail_probe"
    assert _is_error_flag(crash) is True
    assert "stays server-side" not in crash["error"]


def test_refusals_start_nothing(tmp_path: Path):
    probe = tmp_path / "spawned"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(probe)!r}).write_text('x')",
    ]
    assert (
        mcp_session_call(" ".join(command), "t", {}, cwd=str(tmp_path))["error"]
        == "command must be an argv list, not a string"
    )
    assert "error" in mcp_session_call([], "t", {}, cwd=str(tmp_path))
    assert "error" in mcp_session_call(command, "", {}, cwd=str(tmp_path))
    assert "error" in mcp_session_call(command, "t", [], cwd=str(tmp_path))
    assert "error" in mcp_session_call(command, "t", {}, cwd=str(tmp_path), timeout=0)
    assert "error" in mcp_session_call(command, "t", {}, cwd=str(tmp_path / "nope"))
    assert not probe.exists()
    assert not (tmp_path / ".mcp-child").exists()


def test_missing_executable_is_a_spawn_error(tmp_path: Path):
    outcome = mcp_session_call(
        [str(tmp_path / "no-such-server"), "--x"], "t", {}, cwd=str(tmp_path)
    )
    assert "Error" in outcome["error"] or "error" in outcome["error"].lower()
    assert "result" not in outcome


def _dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def test_timeout_names_the_timeout_and_reaps_child_and_descendants(tmp_path: Path):
    script = tmp_path / "hang.py"
    script.write_text(_HANG_SERVER, encoding="utf-8")
    pid_file = tmp_path / "hang.pid"
    outcome = mcp_session_call(
        [sys.executable, str(script), str(pid_file)],
        "t",
        {},
        cwd=str(tmp_path),
        timeout=2,
    )
    assert outcome == {"error": "timed out after 2 seconds"}
    parent_pid, child_pid = (
        int(part) for part in pid_file.read_text(encoding="utf-8").split()
    )
    for _ in range(200):
        if _dead(parent_pid) and _dead(child_pid):
            break
        time.sleep(0.05)
    assert _dead(parent_pid)
    assert _dead(child_pid)


def test_stderr_and_malformed_diagnostics_stay_private(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    script = tmp_path / "malformed_server.py"
    script.write_text(_MALFORMED_SERVER, encoding="utf-8")
    command = [
        sys.executable,
        str(script),
        _CANARY_RECOGNIZED,
        _CANARY_PEM,
        _CANARY_PATH,
        _CANARY_GENERIC,
    ]
    with caplog.at_level(logging.DEBUG, logger="mcp"):
        outcome = mcp_session_call(command, "t", {}, cwd=str(tmp_path), timeout=5)
    assert "error" in outcome
    assert "result" not in outcome
    blob = json.dumps(outcome)
    for canary in _ALL_CANARIES:
        assert canary not in blob
    captured = caplog.text
    for canary in _ALL_CANARIES:
        assert canary not in captured


def test_child_env_is_restricted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROK_API_KEY", _GROK_CANARY)
    monkeypatch.setenv("OPENAI_API_KEY", _OPENAI_CANARY)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", _AWS_CANARY)
    command = _fixture(tmp_path)
    outcome = mcp_session_call(command, "dump_env", {}, cwd=str(tmp_path), timeout=30)
    assert "error" not in outcome
    payload = json.loads(outcome["result"]["content"][0]["text"])
    assert payload["secret"] == {
        "GROK_API_KEY": "absent",
        "OPENAI_API_KEY": "absent",
        "AWS_SECRET_ACCESS_KEY": "absent",
    }
    child_root = tmp_path / ".mcp-child"
    assert payload["safe"]["TMPDIR"] == str(child_root / "tmp")
    for key in (
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
    ):
        assert payload["safe"][key].startswith(str(child_root))
    assert payload["safe"]["PYTHONDONTWRITEBYTECODE"] == "1"
    blob = json.dumps(outcome)
    for canary in (_GROK_CANARY, _OPENAI_CANARY, _AWS_CANARY):
        assert canary not in blob

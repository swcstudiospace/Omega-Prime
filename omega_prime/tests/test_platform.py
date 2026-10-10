"""Phase 6: local platform tools, the approval gate, and plugin registration."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime.tooling.roster import roster_names
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.browser import BrowserSession
from omega_prime.tools.coding import CODING_TOOL_NAMES
from omega_prime.tools.delegate import DELEG_TOOL_NAMES
from omega_prime.tools.growth import GROWTH_TOOL_NAMES
from omega_prime.tools.offer import offered_schemas
from omega_prime.tools.omega_command import OMEGA_COMMAND_TOOL_NAMES
from omega_prime.tools.platform import PLATFORM_TOOL_NAMES, register_platform_tools
from omega_prime.tools.plugins import load_plugins
from omega_prime.tools.registry import ToolRegistry

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROSTER = OMEGA_PRIME / "contracts" / "tool-rosters" / "omega-prime.yaml"

_SCHEMA = {"type": "object", "properties": {}, "required": []}


def _load(raw: str) -> dict:
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


class _PlatformFakeRoot:
    def __init__(self, authority: object, deadline_at: float) -> None:
        self.identity = object()
        self.authority = authority
        self.deadline_at = deadline_at

        class _Abort:
            reason = None

        self.abort_handle = _Abort()


class _PlatformFakeScope:
    def __init__(self, root: _PlatformFakeRoot) -> None:
        self.operation = root
        self.deadline_at = root.deadline_at

    def close(self) -> None:
        return None


class _PlatformFakeTransport:
    def __init__(self, page: str = "<page>example</page>") -> None:
        self.authority = object()
        self.page = page
        self.urls: list[str] = []

        class _Limits:
            decoded_max = 16_777_216
            decoder_workspace_max = 262_144

        self.limits = _Limits()

    def begin_operation(self, *, deadline_at: float) -> _PlatformFakeRoot:
        return _PlatformFakeRoot(self.authority, deadline_at)

    def current_operation(self) -> None:
        return None

    def finish_operation(
        self, operation: _PlatformFakeRoot, *, deadline_at: float
    ) -> object:
        from collections import namedtuple

        _Drain = namedtuple(
            "_Drain",
            "operation scopes_closed pending_scopes abort_reason errors complete",
        )
        return _Drain(operation.identity, 1, 0, None, (), True)

    def open_scope(
        self, *, operation: _PlatformFakeRoot, deadline_at: float, parent: object = None
    ) -> _PlatformFakeScope:
        return _PlatformFakeScope(operation)

    def fetch(
        self, request: object, *, scope: object, sample_limit: object = None
    ) -> object:
        from types import SimpleNamespace
        from urllib.parse import urlparse

        url = getattr(request, "url", "")
        self.urls.append(url)
        host = urlparse(url).hostname or ""
        if host != "example.test":
            raise AssertionError("unexpected host")
        return SimpleNamespace(url=url, status=200, body=b"<title>t</title>")

    def decode_response(
        self, response: Any, *, request: object, scope: object, budget: object
    ) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(url=response.url, status=200, body=b"<title>t</title>")


def _platform_factory(transport: _PlatformFakeTransport) -> object:
    from pathlib import Path as _Path

    from omega_prime.tools.browser_egress import GuardedBrowserFactory as _Factory
    from omega_prime.tools.browser_egress import HostBrowserConfig as _Config
    from omega_prime.tools.browser_egress import StubLaunchBackend as _Backend

    config = _Config(
        executable=_Path("/opt/google/chrome/chrome"),
        bwrap=_Path("/usr/bin/bwrap"),
        manifest=(),
        delegated_cgroup=_Path("/sys/fs/cgroup/omega-prime-browser"),
        profile_id="hermetic-platform",
    )
    return _Factory._for_test(transport, config=config, launcher=_Backend())  # type: ignore[arg-type]


class _Page:
    """Legacy shape retained for import compatibility; no longer injected."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.page = "<page>example</page>"

    def navigate(self, url: str) -> str:
        self.urls.append(url)
        return self.page

    def snapshot(self) -> str:
        return self.page


def test_execute_code_runs_python_and_refuses_empty_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    home.mkdir()
    registry = ToolRegistry()
    assert register_platform_tools(registry, home=home) == list(PLATFORM_TOOL_NAMES)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("empty code started a process")

    with monkeypatch.context() as patch:
        patch.setattr("omega_prime.tools.execute.subprocess.Popen", fail_popen)
        empty = _load(registry.dispatch("execute_code", {"code": "  \n\t"}))
    assert "error" in empty
    assert _files(home) == []

    ran = _load(registry.dispatch("execute_code", {"code": "print(1)"}))
    assert ran["stdout"].strip() == "1"
    assert ran["exit_code"] == 0
    where = _load(
        registry.dispatch("execute_code", {"code": "import os; print(os.getcwd())"})
    )
    assert where["exit_code"] == 0
    assert Path(where["stdout"].strip()).resolve() == home.resolve()
    failed = _load(
        registry.dispatch("execute_code", {"code": "import sys; sys.exit(3)"})
    )
    assert failed["exit_code"] == 3
    assert "error" not in failed

    pid_path = home / "sleep.pid"
    timed = _load(
        registry.dispatch(
            "execute_code",
            {
                "code": (
                    "import os, time\n"
                    f"open({str(pid_path)!r}, 'w', encoding='utf-8').write(str(os.getpid()))\n"
                    "time.sleep(30)\n"
                ),
                "timeout": 1,
            },
        )
    )
    assert "error" in timed
    assert timed["exit_code"] != 0
    assert "stdout" in timed and "stderr" in timed
    pid = int(pid_path.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


_PLATFORM_FIXTURE_SERVER = """\
import asyncio
import os
import sys

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

home = sys.argv[1]
server = MCPServer("phase75-platform-fixture")


@server.tool()
def write_note(name: str, text: str) -> str:
    with open(os.path.join(home, name), "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


@server.tool()
def read_note(name: str) -> str:
    with open(os.path.join(home, name), encoding="utf-8") as handle:
        return handle.read()


@server.tool()
def oops_note() -> str:
    raise ToolError("note says nope")


asyncio.run(server.run_stdio_async())
"""


def _platform_server(home: Path) -> list[str]:
    script = home / "platform_server.py"
    script.write_text(_PLATFORM_FIXTURE_SERVER, encoding="utf-8")
    return [sys.executable, str(script), str(home)]


def test_mcp_call_runs_the_initialized_session_with_a_real_file_effect(
    tmp_path: Path,
):
    home = tmp_path / "home"
    home.mkdir()
    command = _platform_server(home)
    registry = ToolRegistry()
    register_platform_tools(registry, home=home)

    written = _load(
        registry.dispatch(
            "mcp_call",
            {
                "command": command,
                "tool": "write_note",
                "arguments": {"name": "note.txt", "text": "platform file effect"},
            },
        )
    )
    assert written["result"]["content"] == [
        {"type": "text", "text": "platform file effect"}
    ]
    assert (home / "note.txt").read_bytes() == b"platform file effect"

    read = _load(
        registry.dispatch(
            "mcp_call",
            {
                "command": command,
                "tool": "read_note",
                "arguments": {"name": "note.txt"},
            },
        )
    )
    assert read["result"]["content"] == [
        {"type": "text", "text": "platform file effect"}
    ]


def test_mcp_call_refuses_a_string_command_and_a_missing_cwd(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    probe = home / "spawned"
    registry = ToolRegistry()
    register_platform_tools(registry, home=home)

    refused = _load(
        registry.dispatch(
            "mcp_call",
            {
                "command": (
                    f"{sys.executable} -c \"open({str(probe)!r}, 'w').write('x')\""
                ),
                "tool": "write_note",
                "arguments": {"name": "note.txt", "text": "x"},
            },
        )
    )
    assert refused["error"] == "command must be an argv list, not a string"
    assert not probe.exists()

    absent_home = tmp_path / "absent-home"
    absent_registry = ToolRegistry()
    register_platform_tools(absent_registry, home=absent_home)
    missing = _load(
        absent_registry.dispatch(
            "mcp_call",
            {
                "command": _platform_server(home),
                "tool": "read_note",
                "arguments": {"name": "note.txt"},
            },
        )
    )
    assert "cwd is not a directory" in missing["error"]
    assert "result" not in missing


def test_mcp_call_surfaces_a_tool_error_with_its_result(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    command = _platform_server(home)
    registry = ToolRegistry()
    register_platform_tools(registry, home=home)

    outcome = _load(
        registry.dispatch(
            "mcp_call", {"command": command, "tool": "oops_note", "arguments": {}}
        )
    )
    assert outcome["error"] == "Error executing tool oops_note: note says nope"
    result = outcome["result"]
    assert result.get("is_error", result.get("isError")) is True


def test_browser_navigate_and_snapshot_use_the_injected_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def refuse_socket(*_args, **_kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", refuse_socket)
    transport = _PlatformFakeTransport()
    factory = _platform_factory(transport)
    registry = ToolRegistry()
    register_platform_tools(registry, home=tmp_path, browser=BrowserSession(factory))
    navigated = _load(
        registry.dispatch(
            "browser_navigate", {"url": "https://example.test/omega_prime"}
        )
    )
    assert navigated["url"] == "https://example.test/omega_prime"
    assert navigated["page"] == {
        "title": "t",
        "url": "https://example.test/omega_prime",
    }
    assert transport.urls == ["https://example.test/omega_prime"]
    assert _load(registry.dispatch("browser_snapshot", {})) == {
        "page": {"title": "t", "url": "https://example.test/omega_prime"}
    }

    closed = ToolRegistry()
    register_platform_tools(closed, home=tmp_path, browser=None)
    assert "error" in _load(
        closed.dispatch("browser_navigate", {"url": "https://example.test"})
    )
    assert "error" in _load(closed.dispatch("browser_snapshot", {}))
    assert transport.urls == ["https://example.test/omega_prime"]
    missing = BrowserSession(None)
    assert "error" in missing.browser_navigate("https://example.test")
    assert "error" in missing.browser_snapshot()


def test_browser_direct_failure_blocks_cache_and_snapshot(tmp_path: Path) -> None:
    transport = _PlatformFakeTransport()
    factory = _platform_factory(transport)
    direct = BrowserSession(factory)
    bad = direct.browser_navigate("https://forbidden.example/x")
    assert "error" in bad
    assert "error" in direct.browser_snapshot()


def test_approval_gate_blocks_until_a_person_approves():
    calls: list[str] = []

    def handler() -> dict:
        calls.append("ran")
        return {"ok": True}

    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    registry.register(
        "danger", "Needs a person.", _SCHEMA, handler, requires_approval=True
    )
    blocked = _load(registry.dispatch("danger", {}))
    assert blocked == {"error": "approval required", "tool": "danger"}
    assert calls == []
    refused = log.approve("danger", "bot-00-omega-prime")
    assert refused["approved"] is False
    assert log.is_approved("danger") is False
    still = _load(registry.dispatch("danger", {}))
    assert still == {"error": "approval required", "tool": "danger"}
    assert calls == []
    accepted = log.approve("danger", "person")
    assert accepted["approved"] is True
    assert log.is_approved("danger") is True
    assert _load(registry.dispatch("danger", {})) == {"ok": True}
    assert calls == ["ran"]

    bare_calls: list[str] = []

    def bare_handler() -> dict:
        bare_calls.append("ran")
        return {"ok": True}

    bare = ToolRegistry()
    bare.register(
        "danger", "Needs a person.", _SCHEMA, bare_handler, requires_approval=True
    )
    assert _load(bare.dispatch("danger", {})) == {
        "error": "approval required",
        "tool": "danger",
    }
    assert bare_calls == []


def test_load_plugins_registers_echo_and_skips_a_symlink_outside_home(tmp_path: Path):
    home = tmp_path / "home"
    echo = home / "plugins" / "echo"
    echo.mkdir(parents=True)
    (echo / "plugin.py").write_text(
        "def register(registry):\n"
        "    def plugin_echo():\n"
        "        return {'payload': 'echo'}\n"
        "    registry.register(\n"
        "        'plugin_echo',\n"
        "        'Return a fixed payload.',\n"
        "        {'type': 'object', 'properties': {}, 'required': []},\n"
        "        plugin_echo,\n"
        "    )\n",
        encoding="utf-8",
    )
    bare = home / "plugins" / "bare"
    bare.mkdir()
    (bare / "plugin.py").write_text("VALUE = 1\n", encoding="utf-8")

    outside = tmp_path / "outside" / "leave"
    outside.mkdir(parents=True)
    marker = tmp_path / "outside" / "loaded.txt"
    (outside / "plugin.py").write_text(
        "def register(registry):\n"
        "    from pathlib import Path\n"
        f"    Path({str(marker)!r}).write_text('yes', encoding='utf-8')\n"
        "    registry.register(\n"
        "        'plugin_leave',\n"
        "        'Should not load.',\n"
        "        {'type': 'object', 'properties': {}, 'required': []},\n"
        "        lambda: {'payload': 'nope'},\n"
        "    )\n",
        encoding="utf-8",
    )
    (home / "plugins" / "leave").symlink_to(outside, target_is_directory=True)

    registry = ToolRegistry()
    loaded = load_plugins(home, registry)
    assert loaded == ["plugin_echo"]
    assert _load(registry.dispatch("plugin_echo", {})) == {"payload": "echo"}
    names = [item["function"]["name"] for item in registry.schemas()]
    assert "plugin_leave" not in names
    assert marker.exists() is False

    boom_home = tmp_path / "boom"
    boom = boom_home / "plugins" / "boom"
    boom.mkdir(parents=True)
    (boom / "plugin.py").write_text(
        "def register(registry):\n    raise RuntimeError('plugin failed')\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="plugin failed"):
        load_plugins(boom_home, ToolRegistry())


def test_offered_schemas_include_platform_names_and_omit_an_extra_tool(tmp_path: Path):
    registry = ToolRegistry()
    registered = register_platform_tools(registry, home=tmp_path)
    assert registered == list(PLATFORM_TOOL_NAMES)
    registry.register(
        "not_on_roster", "Registered but not offered.", _SCHEMA, lambda: {"ok": True}
    )
    roster = roster_names(ROSTER.read_text(encoding="utf-8"))
    start = (
        len(CODING_TOOL_NAMES)
        + len(GROWTH_TOOL_NAMES)
        + len(DELEG_TOOL_NAMES)
        + len(OMEGA_COMMAND_TOOL_NAMES)
    )
    assert roster[start : start + len(PLATFORM_TOOL_NAMES)] == list(PLATFORM_TOOL_NAMES)
    offered = offered_schemas(registry, roster)
    names = [item["function"]["name"] for item in offered]
    assert names == list(PLATFORM_TOOL_NAMES)
    for platform_name in PLATFORM_TOOL_NAMES:
        assert platform_name in names
    assert "not_on_roster" not in names
    assert _load(registry.dispatch("not_on_roster", {})) == {"ok": True}

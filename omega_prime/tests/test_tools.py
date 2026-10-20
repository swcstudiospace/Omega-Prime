"""Phase 3: the coding-tool registry, rooted file and terminal tools, and the roster."""

from __future__ import annotations

import difflib
import json
import socket
from pathlib import Path

from omega_prime.tooling.catalog import EXEC_JOB_TOOL_NAMES
from omega_prime.tooling.roster import roster_names
from omega_prime.tools.agent_message import MESSAGING_TOOL_NAMES
from omega_prime.tools.autonomous import AUTONOMOUS_TOOL_NAMES
from omega_prime.tools.capacity import CAPACITY_TOOL_NAMES
from omega_prime.tools.clarify import ClarifyLog, clarify
from omega_prime.tools.coding import CODING_TOOL_NAMES, register_coding_tools
from omega_prime.tools.cron_admin import CRON_ADMIN_TOOL_NAMES
from omega_prime.tools.delegate import DELEG_TOOL_NAMES
from omega_prime.tools.discord import DISCORD_TOOL_NAMES
from omega_prime.tools.dispatch import DISPATCH_TOOL_NAMES
from omega_prime.tools.durable_surface import DURABLE_TOOL_NAMES
from omega_prime.tools.goals import GOAL_TOOL_NAMES
from omega_prime.tools.growth import GROWTH_TOOL_NAMES
from omega_prime.tools.harness import HARNESS_TOOL_NAMES
from omega_prime.tools.heartbeat import HEARTBEAT_TOOL_NAMES
from omega_prime.tools.ide import IDE_TOOL_NAMES
from omega_prime.tools.infra import INFRA_TOOL_NAMES
from omega_prime.tools.lead import LEAD_TOOL_NAMES
from omega_prime.tools.learning_surface import LEARNING_TOOL_NAMES
from omega_prime.tools.mobile import MOBILE_TOOL_NAMES
from omega_prime.tools.offer import offered_schemas
from omega_prime.tools.omega_command import OMEGA_COMMAND_TOOL_NAMES
from omega_prime.tools.packs import PACKS_TOOL_NAMES
from omega_prime.tools.platform import PLATFORM_TOOL_NAMES
from omega_prime.tools.prime_runtime import KERNEL_TOOL_NAMES
from omega_prime.tools.quality import QUALITY_TOOL_NAMES
from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.rlm import RLM_TOOL_NAMES
from omega_prime.tools.substrate_tools import SUBSTRATE_TOOL_NAMES
from omega_prime.tools.systems import SYS_TOOL_NAMES
from omega_prime.tools.telegram import TELEGRAM_TOOL_NAMES
from omega_prime.tools.ultrathink import ULT_TOOL_NAMES
from omega_prime.tools.webpack import WEB_TOOL_NAMES
from omega_prime.tools.x import X_TOOL_NAMES

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROSTER = OMEGA_PRIME / "contracts" / "tool-rosters" / "omega-prime.yaml"


def _registry(root: Path, web=None, vision=None) -> ToolRegistry:
    registry = ToolRegistry()
    register_coding_tools(registry, root, web=web, vision=vision)
    return registry


def _load(raw: str) -> dict:
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_read_file_returns_bytes_written_by_write_file(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    registry = _registry(root)
    text = "omega-prime-bytes-v1\nsecond line\n"
    written = _load(
        registry.dispatch("write_file", {"path": "note.txt", "content": text})
    )
    assert written["bytes_written"] == len(text.encode("utf-8"))
    assert (root / "note.txt").read_bytes() == text.encode("utf-8")

    read = _load(registry.dispatch("read_file", {"path": "note.txt"}))
    assert read["content"] == text
    assert read["content"].encode("utf-8") == (root / "note.txt").read_bytes()
    via_parent = _load(registry.dispatch("read_file", {"path": "sub/../note.txt"}))
    assert via_parent["content"] == text

    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "alias.txt").symlink_to(outside)
    escaped = _load(registry.dispatch("read_file", {"path": "../secret.txt"}))
    assert "error" in escaped
    assert "escapes" in escaped["error"]
    through_link = _load(registry.dispatch("read_file", {"path": "alias.txt"}))
    assert "error" in through_link
    assert "secret" not in json.dumps(through_link)
    refused = _load(
        registry.dispatch("write_file", {"path": str(outside), "content": "pwned"})
    )
    assert "error" in refused
    linked_write = _load(
        registry.dispatch("write_file", {"path": "alias.txt", "content": "pwned"})
    )
    assert "error" in linked_write
    assert outside.read_text(encoding="utf-8") == "secret"


def test_patch_file_matches_and_leaves_a_mismatch_unchanged(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    registry = _registry(root)
    original = "alpha\nbeta\ngamma\n"
    desired = "alpha\nBETA\ngamma\n"
    target = root / "code.txt"
    target.write_bytes(original.encode("utf-8"))
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(True),
            desired.splitlines(True),
            fromfile="code.txt",
            tofile="code.txt",
        )
    )
    applied = _load(registry.dispatch("patch_file", {"path": "code.txt", "diff": diff}))
    assert applied["applied"] is True
    assert "error" not in applied
    assert target.read_bytes() == desired.encode("utf-8")

    original_lines = [f"line-{index}\n" for index in range(1, 21)]
    desired_lines = list(original_lines)
    desired_lines[1] = "LINE-2\n"
    desired_lines[15] = "LINE-16\n"
    two_hunks = "".join(
        difflib.unified_diff(
            original_lines, desired_lines, fromfile="code.txt", tofile="code.txt"
        )
    )
    assert two_hunks.count("\n@@") >= 2
    target.write_bytes("".join(original_lines).encode("utf-8"))
    applied = _load(
        registry.dispatch("patch_file", {"path": "code.txt", "diff": two_hunks})
    )
    assert applied.get("applied") is True
    assert target.read_bytes() == "".join(desired_lines).encode("utf-8")

    created = root / "fresh.txt"
    created.write_bytes(b"")
    prepend = "".join(
        difflib.unified_diff([], ["head\n"], fromfile="fresh.txt", tofile="fresh.txt")
    )
    applied = _load(
        registry.dispatch("patch_file", {"path": "fresh.txt", "diff": prepend})
    )
    assert applied.get("applied") is True
    assert created.read_bytes() == b"head\n"

    target.write_bytes(original.encode("utf-8"))
    before = target.read_bytes()
    mismatched = diff.replace(" gamma", " nope", 1)
    refused = _load(
        registry.dispatch("patch_file", {"path": "code.txt", "diff": mismatched})
    )
    assert refused["error"] == "hunk context does not match"
    assert target.read_bytes() == before

    longer = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n"
    target.write_bytes(longer.encode("utf-8"))
    before = target.read_bytes()
    partial = (
        "--- code.txt\n"
        "+++ code.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        "@@ -7,2 +7,2 @@\n"
        " WRONG\n"
        "-seven\n"
        "+SEVEN\n"
    )
    later = _load(
        registry.dispatch("patch_file", {"path": "code.txt", "diff": partial})
    )
    assert later["error"] == "hunk context does not match"
    assert target.read_bytes() == before


def test_patch_file_applies_a_deleted_line_that_looks_like_a_file_header(
    tmp_path: Path,
):
    root = tmp_path / "ws"
    root.mkdir()
    registry = _registry(root)
    original = "keep\n-- comment\nkeep\n"
    desired = "keep\n-- note\nkeep\n"
    target = root / "code.txt"
    target.write_bytes(original.encode("utf-8"))
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(True),
            desired.splitlines(True),
            fromfile="code.txt",
            tofile="code.txt",
        )
    )
    assert "\n--- comment\n" in diff
    applied = _load(registry.dispatch("patch_file", {"path": "code.txt", "diff": diff}))
    assert applied.get("applied") is True
    assert "error" not in applied
    assert target.read_bytes() == desired.encode("utf-8")

    before = target.read_bytes()
    mismatched = diff.replace(" keep", " nope", 1)
    refused = _load(
        registry.dispatch("patch_file", {"path": "code.txt", "diff": mismatched})
    )
    assert refused["error"] == "hunk context does not match"
    assert target.read_bytes() == before


def test_search_text_finds_a_fixture_and_not_a_path_outside_the_root(tmp_path: Path):
    root = tmp_path / "ws"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "hit.txt").write_text("find-me please\n", encoding="utf-8")
    (root / "miss.txt").write_text("nothing\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "also.txt").write_text("xx find-me yy\n", encoding="utf-8")
    (outside / "hit.txt").write_text("find-me please\n", encoding="utf-8")
    (outside / "nested").mkdir()
    (outside / "nested" / "hidden.txt").write_text("find-me\n", encoding="utf-8")
    (root / "linked.txt").symlink_to(outside / "hit.txt")
    (root / "out").symlink_to(outside / "nested")

    found = _load(_registry(root).dispatch("search_text", {"query": "find-me"}))
    assert found["paths"] == ["hit.txt", "sub/also.txt"]
    for relative in found["paths"]:
        assert not Path(relative).is_absolute()
        (root / relative).resolve().relative_to(root.resolve())
        assert "outside" not in relative
    assert "linked.txt" not in found["paths"]
    assert all(not item.startswith("out/") for item in found["paths"])

    escaped = _load(
        _registry(root).dispatch(
            "search_text", {"query": "find-me", "path": "../outside"}
        )
    )
    assert "error" in escaped
    assert "paths" not in escaped or outside.as_posix() not in json.dumps(escaped)


def test_run_terminal_reports_exit_code_and_refuses_a_path_outside_the_root(
    tmp_path: Path,
):
    root = tmp_path / "ws"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    registry = _registry(root)
    ran = _load(
        registry.dispatch("run_terminal", {"argv": ["python3", "-c", "print(1)"]})
    )
    assert ran["exit_code"] == 0
    assert ran["stdout"].strip() == "1"
    assert ran["stderr"] == ""
    assert "error" not in ran

    marker = outside / "ran"
    refused = _load(
        registry.dispatch(
            "run_terminal",
            {
                "argv": ["python3", "-c", f"open({str(marker)!r}, 'w').write('x')"],
                "cwd": str(outside),
            },
        )
    )
    assert "error" in refused
    assert "escapes" in refused["error"]
    assert "exit_code" not in refused
    assert not marker.exists()

    relative = _load(
        registry.dispatch(
            "run_terminal",
            {"argv": ["python3", "-c", "print(1)"], "cwd": ".."},
        )
    )
    assert "escapes" in relative["error"]

    shell = _load(registry.dispatch("run_terminal", {"argv": "python3 -c 'print(1)'"}))
    assert "shell" in shell["error"]
    assert "exit_code" not in shell


def test_todo_round_trip_and_clarify_returns_the_question(tmp_path: Path):
    registry = _registry(tmp_path)
    items = [
        {"id": "1", "content": "ship the tool", "status": "pending"},
        {"id": "2", "content": "read it back", "status": "in_progress"},
    ]
    stored = _load(registry.dispatch("todo_write", {"todos": items}))
    assert stored["todos"] == items
    items[0]["content"] = "mutated after write"
    assert (
        _load(registry.dispatch("todo_read", {}))["todos"][0]["content"]
        == "ship the tool"
    )
    items[0]["content"] = "ship the tool"
    replacement = [{"id": "9", "content": "replaced", "status": "completed"}]
    assert (
        _load(registry.dispatch("todo_write", {"todos": replacement}))["todos"]
        == replacement
    )
    assert _load(registry.dispatch("todo_read", {}))["todos"] == replacement

    question = "which root should the patch touch?"
    answered = _load(registry.dispatch("clarify", {"question": question}))
    assert answered["question"] == question
    log = ClarifyLog()
    assert clarify(log, question) == {"question": question}
    assert log.questions == [question]


def test_web_and_vision_return_the_transport_payload_without_opening_a_socket(
    tmp_path: Path, monkeypatch
):
    search_payload = {
        "results": [{"title": "Omega Prime", "url": "https://example.test/omega_prime"}]
    }
    extract_payload = {
        "pages": [{"url": "https://example.test/omega_prime", "text": "local"}]
    }
    vision_payload = {"analysis": "a diagram of a registry"}
    calls: list[tuple] = []

    class Web:
        def search(self, query, limit=5):
            calls.append(("search", query, limit))
            return search_payload

        def extract(self, urls):
            calls.append(("extract", tuple(urls)))
            return extract_payload

    class Vision:
        def analyze(self, image_url, question, region=None):
            calls.append(("analyze", image_url, question, region))
            return vision_payload

    def refuse_socket(*_args, **_kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", refuse_socket)
    monkeypatch.setattr(socket, "create_connection", refuse_socket)

    registry = _registry(tmp_path, web=Web(), vision=Vision())
    assert (
        _load(registry.dispatch("web_search", {"query": "omega_prime", "limit": 2}))
        == search_payload
    )
    assert (
        _load(
            registry.dispatch(
                "web_extract", {"urls": ["https://example.test/omega_prime"]}
            )
        )
        == extract_payload
    )
    assert (
        _load(
            registry.dispatch(
                "vision_analyze",
                {"image_url": "file:///tmp/diagram.png", "question": "what is this?"},
            )
        )
        == vision_payload
    )
    assert calls == [
        ("search", "omega_prime", 2),
        ("extract", ("https://example.test/omega_prime",)),
        ("analyze", "file:///tmp/diagram.png", "what is this?", None),
    ]

    bare = _registry(tmp_path)
    assert "error" in _load(bare.dispatch("web_search", {"query": "omega_prime"}))
    assert "error" in _load(
        bare.dispatch("vision_analyze", {"image_url": "x", "question": "y"})
    )


def test_offered_schemas_omit_a_registered_tool_that_is_not_on_the_roster(
    tmp_path: Path,
):
    registry = _registry(tmp_path)
    registry.register(
        "not_on_roster",
        "Registered but not offered.",
        {"type": "object", "properties": {}, "required": []},
        lambda: {"ok": True},
    )
    offered = offered_schemas(registry, ["read_file", "todo_read"])
    names = [item["function"]["name"] for item in offered]
    assert names == ["read_file", "todo_read"]
    assert "not_on_roster" not in names
    assert "write_file" not in names
    assert _load(registry.dispatch("not_on_roster", {})) == {"ok": True}


def test_dispatch_of_an_unknown_name_returns_a_json_error():
    registry = ToolRegistry()
    raw = registry.dispatch("no_such_tool", {})
    assert isinstance(raw, str)
    parsed = _load(raw)
    assert "error" in parsed
    assert "no_such_tool" in parsed["error"]
    broken = _load(registry.dispatch("no_such_tool", "{"))
    assert "error" in broken


def test_roster_lists_exactly_the_names_register_coding_tools_registers(tmp_path: Path):
    registry = ToolRegistry()
    registered = register_coding_tools(registry, tmp_path)
    schema_names = [item["function"]["name"] for item in registry.schemas()]
    assert registered == list(CODING_TOOL_NAMES)
    assert schema_names == list(CODING_TOOL_NAMES)
    assert roster_names(ROSTER.read_text(encoding="utf-8")) == list(
        CODING_TOOL_NAMES
        + GROWTH_TOOL_NAMES
        + DELEG_TOOL_NAMES
        + OMEGA_COMMAND_TOOL_NAMES
        + PLATFORM_TOOL_NAMES
        + IDE_TOOL_NAMES
        + EXEC_JOB_TOOL_NAMES
        + X_TOOL_NAMES
        + TELEGRAM_TOOL_NAMES
        + DISCORD_TOOL_NAMES
        + LEAD_TOOL_NAMES
        + SYS_TOOL_NAMES
        + WEB_TOOL_NAMES
        + MOBILE_TOOL_NAMES
        + INFRA_TOOL_NAMES
        + QUALITY_TOOL_NAMES
        + PACKS_TOOL_NAMES
        + ULT_TOOL_NAMES
        + SUBSTRATE_TOOL_NAMES
        + CRON_ADMIN_TOOL_NAMES
        + LEARNING_TOOL_NAMES
        + DURABLE_TOOL_NAMES
        + CAPACITY_TOOL_NAMES
        + DISPATCH_TOOL_NAMES
        + RLM_TOOL_NAMES
        + HARNESS_TOOL_NAMES
        + GOAL_TOOL_NAMES
        + HEARTBEAT_TOOL_NAMES
        + AUTONOMOUS_TOOL_NAMES
        + MESSAGING_TOOL_NAMES
        + KERNEL_TOOL_NAMES
        + REDTEAM_TOOL_NAMES
    )

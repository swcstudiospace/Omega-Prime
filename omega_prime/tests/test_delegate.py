"""Isolated delegate children and the due-job tick."""

from __future__ import annotations

import json
from pathlib import Path

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.delegate import delegate_task, join_delegate
from omega_prime.agent.model import ScriptedModel
from omega_prime.cron.scheduler import JobStore, run_due_jobs
from omega_prime.tools.delegate import register_delegate_tools
from omega_prime.tools.registry import ToolRegistry

CHILD_TOOL = "child_only_tool"
CHILD_MARKER = "child-tool-marker"
CHILD_FINAL = "child-final-response"


def _tool_call(
    name: str, arguments: dict | None = None, call_id: str = "call-1"
) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            }
        ],
    }


def _load(raw: str) -> dict:
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _user_texts(seen: list) -> list:
    return [
        message.get("content")
        for message in seen
        if isinstance(message, dict) and message.get("role") == "user"
    ]


def test_parent_tool_row_is_the_child_final_response_only():
    calls: list[str] = []

    def child_only_tool():
        calls.append(CHILD_TOOL)
        return CHILD_MARKER

    child_model = ScriptedModel(
        [
            _tool_call(CHILD_TOOL),
            {"role": "assistant", "content": CHILD_FINAL},
        ]
    )
    parent_model = ScriptedModel(
        [
            _tool_call("delegate_task", {"goal": "find the marker"}),
            {"role": "assistant", "content": "parent-done"},
        ]
    )
    parent = Agent(model=parent_model, tools={}, max_iterations=4)
    parent.child_model = child_model
    registry = ToolRegistry()
    register_delegate_tools(registry, parent)
    handler = registry._tools["delegate_task"].handler
    parent.tools = {"delegate_task": handler, CHILD_TOOL: child_only_tool}

    result = run_conversation(parent, "please delegate", system_message="sys")

    tool_row = next(
        message for message in result["messages"] if message.get("role") == "tool"
    )
    assert tool_row["name"] == "delegate_task"
    assert _load(tool_row["content"])["summary"] == CHILD_FINAL
    blob = json.dumps(result["messages"])
    assert CHILD_MARKER not in blob
    assert CHILD_TOOL not in blob
    assert calls == [CHILD_TOOL]
    assert parent_model.call_count == 2
    assert child_model.call_count == 2
    assert CHILD_MARKER in json.dumps(child_model.seen)
    assert CHILD_TOOL in json.dumps(child_model.seen)
    assert CHILD_MARKER not in json.dumps(parent_model.seen)
    assert CHILD_TOOL not in json.dumps(parent_model.seen)
    assert _user_texts(child_model.seen[0]) == ["find the marker"]


def test_child_tool_map_is_a_shallow_copy():
    calls: list[str] = []

    def keep():
        return "kept"

    def drop_me():
        calls.append("drop")
        return "gone"

    tools = {"keep": keep, "drop_me": drop_me}
    parent_model = ScriptedModel([])
    child_model = ScriptedModel(
        [
            _tool_call("drop_me"),
            {"role": "assistant", "content": "after-pop"},
        ]
    )
    parent = Agent(model=parent_model, tools=tools)
    parent_tools = parent.tools
    assert parent_tools is not None
    parent.child_model = child_model
    before_id = id(parent_tools)
    before = dict(parent_tools)
    captured: dict[str, dict] = {}

    def hook(child_tools):
        assert child_tools is not parent_tools
        captured["tools"] = child_tools
        child_tools.pop("drop_me")

    parent.child_tool_hook = hook
    parsed = _load(delegate_task(parent, "go"))

    assert parsed["summary"] == "after-pop"
    assert calls == []
    assert "drop_me" not in captured["tools"]
    assert id(parent_tools) == before_id
    assert dict(parent_tools) == before
    assert parent_tools["keep"] is keep
    assert parent_tools["drop_me"] is drop_me
    assert parent_model.call_count == 0


def test_depth_and_batch_limits_run_no_child():
    child_model = ScriptedModel([{"role": "assistant", "content": "leaf-ok"}])
    parent_model = ScriptedModel([{"role": "assistant", "content": "parent-unused"}])

    def delegate_on_parent():
        return "parent-callable"

    parent_tools = {"delegate_task": delegate_on_parent, "other": lambda: "x"}
    parent = Agent(model=parent_model, tools=parent_tools)
    parent.child_model = child_model
    seen: dict[str, set] = {}

    def hook(child_tools):
        seen["keys"] = set(child_tools)

    parent.child_tool_hook = hook
    parsed = _load(delegate_task(parent, "one", max_depth=1))
    assert parsed["summary"] == "leaf-ok"
    assert "delegate_task" not in seen["keys"]
    assert "other" in seen["keys"]
    assert child_model.call_count == 1
    assert parent_model.call_count == 0
    assert parent.tools is parent_tools
    assert parent.tools["delegate_task"] is delegate_on_parent

    blocked = ScriptedModel([{"role": "assistant", "content": "should-not-run"}])
    deep = Agent(model=parent_model, tools={})
    deep.delegate_depth = 1
    deep.child_model = blocked

    def boom(_tools):
        raise AssertionError("child was built")

    deep.child_tool_hook = boom
    refused = _load(delegate_task(deep, "again", max_depth=1))
    assert "error" in refused
    assert blocked.call_count == 0
    assert parent_model.call_count == 0

    batch_model = ScriptedModel([{"role": "assistant", "content": "should-not-run"}])
    batch_parent = Agent(model=parent_model, tools={})
    batch_parent.child_model = batch_model
    batch_parent.child_tool_hook = boom
    too_many = _load(
        delegate_task(
            batch_parent,
            "ignored",
            tasks=[{"goal": "a"}, {"goal": "b"}],
            max_children=1,
        )
    )
    assert "error" in too_many
    assert batch_model.call_count == 0

    registry = ToolRegistry()
    limited = Agent(model=parent_model, tools={})
    limited.max_children = 1
    limited.child_model = batch_model
    limited.child_tool_hook = boom
    register_delegate_tools(registry, limited)
    dispatched = _load(
        registry.dispatch("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}]})
    )
    assert "error" in dispatched
    assert batch_model.call_count == 0

    room = ScriptedModel(
        [
            {"role": "assistant", "content": "first-child"},
            {"role": "assistant", "content": "second-child"},
        ]
    )
    wide = Agent(model=parent_model, tools={})
    wide.delegate_depth = 2
    wide.max_depth = 3
    wide.max_children = 2
    wide.child_model = room
    wide_registry = ToolRegistry()
    register_delegate_tools(wide_registry, wide)
    both = _load(
        wide_registry.dispatch(
            "delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}]}
        )
    )
    assert both["summaries"] == ["first-child", "second-child"]
    assert room.call_count == 2
    assert _user_texts(room.seen[0]) == ["a"]
    assert _user_texts(room.seen[1]) == ["b"]
    assert parent_model.call_count == 0


def test_background_join_runs_the_child_once():
    child_model = ScriptedModel([{"role": "assistant", "content": "bg-summary"}])
    parent_model = ScriptedModel([{"role": "assistant", "content": "parent-unused"}])
    parent = Agent(model=parent_model, tools={})
    parent.child_model = child_model
    hooks: list[str] = []
    parent.child_tool_hook = lambda _tools: hooks.append("hook")
    registry = ToolRegistry()
    register_delegate_tools(registry, parent)

    raw = registry.dispatch("delegate_task", {"goal": "later", "background": True})
    pending = _load(raw)
    assert pending["pending"] is True
    assert isinstance(pending["handle"], str) and pending["handle"]
    assert "summary" not in pending
    assert "bg-summary" not in raw
    assert hooks == []
    assert child_model.call_count == 0
    assert parent_model.call_count == 0

    summary = join_delegate(pending["handle"])
    assert summary == "bg-summary"
    assert hooks == ["hook"]
    assert child_model.call_count == 1

    again = join_delegate(pending["handle"])
    assert again == summary
    assert hooks == ["hook"]
    assert child_model.call_count == 1
    assert parent_model.call_count == 0


def _scheduled_parent(child_model: ScriptedModel, tools: dict | None = None) -> Agent:
    parent = Agent(model=ScriptedModel([]), tools=tools or {})
    parent.child_model = child_model
    return parent


def test_due_prompt_dispatches_through_live_registry_tool(tmp_path: Path):
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    effect = tmp_path / "effect.txt"
    registry = ToolRegistry()
    registry.register(
        "write_effect",
        "Write harmless effect",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        lambda path: effect.write_text("executed", encoding="utf-8") or {"ok": True},
    )

    def dispatch(path: str):
        return _load(registry.dispatch("write_effect", {"path": path}))

    child_model = ScriptedModel(
        [
            _tool_call("write_effect", {"path": str(effect)}),
            {"role": "assistant", "content": "scheduled result"},
        ]
    )
    parent = _scheduled_parent(child_model, {"write_effect": dispatch})
    now = 1_700_000_000
    job_id = store.schedule("perform the harmless effect", now)

    ran = run_due_jobs(store, now, parent)

    assert [job["id"] for job in ran] == [job_id]
    assert effect.read_text(encoding="utf-8") == "executed"
    assert ran[0]["last_result"] == {
        "state": "succeeded",
        "summary": "scheduled result",
    }
    assert ran[0]["history"][-1]["scheduled_for"] == now
    assert ran[0]["history"][-1]["disposition"] == "succeeded"
    assert JobStore(path).jobs == store.jobs


def test_missing_parent_and_empty_delegate_output_are_durable_blocks(tmp_path: Path):
    now = 1_700_000_000
    absent_store = JobStore(tmp_path / "absent.json")
    empty_store = JobStore(tmp_path / "empty.json")
    absent_store.schedule("blocked without parent", now)
    empty_id = empty_store.schedule("blocked empty", now)
    parent = _scheduled_parent(ScriptedModel([{"role": "assistant", "content": ""}]))

    absent_run = run_due_jobs(absent_store, now, None)
    empty_run = run_due_jobs(empty_store, now, parent)

    absent = JobStore(absent_store.path).jobs[0]
    empty = JobStore(empty_store.path).jobs[0]
    assert absent_run[0]["last_result"]["state"] == "blocked"
    assert absent_run[0]["last_result"]["error"] == (
        "ValueError: delegate returned error or pending output"
    )
    assert "delegate parent unavailable" not in json.dumps(absent_run[0]["last_result"])
    assert empty_run[0]["id"] == empty_id
    assert empty_run[0]["last_result"]["state"] == "blocked"
    assert "empty" in empty_run[0]["last_result"]["error"]
    assert absent["completed"] is True
    assert empty["completed"] is True
    assert absent["history"][-1]["scheduled_for"] == now
    assert empty["history"][-1]["scheduled_for"] == now


def test_stale_prompt_is_missed_without_delegate_dispatch(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.json")
    now = 1_700_000_301
    job_id = store.schedule("too late to run", now - 301)
    parent = _scheduled_parent(ScriptedModel([]))

    ran = run_due_jobs(store, now, parent)
    settled = JobStore(store.path).jobs[0]

    assert [job["id"] for job in ran] == [job_id]
    child_model = parent.child_model
    assert isinstance(child_model, ScriptedModel)
    assert child_model.call_count == 0
    assert settled["last_result"]["state"] == "missed"
    assert settled["history"][-1]["disposition"] == "missed"
    assert settled["completed"] is True


def test_malformed_delegate_output_is_a_blocked_receipt(tmp_path: Path, monkeypatch):
    from omega_prime.agent import delegate as delegate_module

    store = JobStore(tmp_path / "jobs.json")
    now = 1_700_000_000
    job_id = store.schedule("malformed delegate output", now)
    monkeypatch.setattr(
        delegate_module, "delegate_task", lambda parent, prompt: "{not-json"
    )
    parent = _scheduled_parent(ScriptedModel([]))

    ran = run_due_jobs(store, now, parent)

    assert ran[0]["id"] == job_id
    assert ran[0]["last_result"]["state"] == "blocked"
    assert "malformed" in ran[0]["last_result"]["error"]
    assert JobStore(store.path).jobs[0]["history"][-1]["scheduled_for"] == now


def test_delegate_exception_settles_without_persisting_error_text_and_later_job_executes(
    tmp_path: Path,
):
    store = JobStore(tmp_path / "jobs.json")
    now = 1_700_000_000
    failed_id = store.schedule("first", now)
    later_id = store.schedule("later", now)
    sentinel = "credential-shaped-scheduler-sentinel"

    class RaisingModel:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(sentinel)
            return {"role": "assistant", "content": "later succeeded"}

    parent = _scheduled_parent(RaisingModel())  # type: ignore[arg-type]
    ran = run_due_jobs(store, now, parent)
    rows = {job["id"]: job for job in JobStore(store.path).jobs}

    assert [job["id"] for job in ran] == [failed_id, later_id]
    assert rows[failed_id]["last_result"]["state"] == "failed"
    assert (
        rows[failed_id]["last_result"]["error"]
        == "RuntimeError: scheduler runner failed"
    )
    assert sentinel not in json.dumps(rows[failed_id]["last_result"])
    assert rows[later_id]["last_result"] == {
        "state": "succeeded",
        "summary": "later succeeded",
    }
    assert rows[failed_id]["claim_token"] is None
    assert rows[later_id]["completed"] is True


def test_delegate_error_payload_is_not_persisted(tmp_path: Path, monkeypatch):
    from omega_prime.agent import delegate as delegate_module

    sentinel = "credential-shaped-delegate-sentinel"
    store = JobStore(tmp_path / "jobs.json")
    now = 1_700_000_000
    job_id = store.schedule("delegate error", now)
    monkeypatch.setattr(
        delegate_module,
        "delegate_task",
        lambda parent, prompt: json.dumps({"error": sentinel}),
    )
    parent = _scheduled_parent(ScriptedModel([]))

    ran = run_due_jobs(store, now, parent)
    receipt = JobStore(store.path).jobs[0]["last_result"]

    assert ran[0]["id"] == job_id
    assert receipt["state"] == "blocked"
    assert receipt["error"] == "ValueError: delegate returned error or pending output"
    assert sentinel not in json.dumps(receipt)

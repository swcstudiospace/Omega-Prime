"""Phase 15: journaled turns, cron history, checkpointed workflows."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.model import ScriptedModel
from omega_prime.cron.scheduler import HISTORY_LIMIT, JobStore
from omega_prime.durable.journal import TurnJournal
from omega_prime.durable.workflows import load_checkpoint, run_workflow


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


def test_journaled_turn_matches_its_transcript(tmp_path: Path):
    journal = TurnJournal(tmp_path / "runs.db")
    model = ScriptedModel(
        [_tool_call("echo"), {"role": "assistant", "content": "finished"}]
    )
    agent = Agent(
        model=model,
        tools={"echo": lambda: "pong"},
        max_iterations=4,
        journal=journal,
        run_id="run-1",
    )
    result = run_conversation(agent, "ping", system_message="sys")

    assert journal.transcript("run-1") == result["messages"]
    assert journal.open_runs() == []
    assert journal.transcript("nope") == []

    fresh = TurnJournal(tmp_path / "runs.db")
    assert fresh.transcript("run-1") == result["messages"]


def test_crashed_turn_resumes_from_the_journal(tmp_path: Path):
    journal = TurnJournal(tmp_path / "runs.db")
    journal.begin_run("crash-1")
    journal.append("crash-1", {"role": "system", "content": "sys"})
    journal.append("crash-1", {"role": "user", "content": "go"})
    journal.append(
        "crash-1",
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
    )
    # No finish: the process died here.
    assert journal.open_runs() == ["crash-1"]

    resumed = TurnJournal(tmp_path / "runs.db")
    history = resumed.transcript("crash-1")
    assert [row["role"] for row in history] == ["system", "user", "assistant"]

    model = ScriptedModel([{"role": "assistant", "content": "recovered"}])
    agent = Agent(model=model, tools={}, max_iterations=2, journal=resumed)
    result = run_conversation(agent, "continue", conversation_history=history)

    assert result["final_response"] == "recovered"
    assert agent.run_id is not None
    continued = resumed.transcript(agent.run_id)
    assert [row["role"] for row in continued] == ["user", "assistant"]
    assert continued[-1]["content"] == "recovered"


def test_cron_history_is_bounded_and_ticks_do_not_repeat(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.json")
    calls: list[str] = []
    store.schedule("do it", 100, interval_seconds=10)

    assert len(store.tick(100, calls.append) or []) == 1
    assert store.tick(100, calls.append) == []
    assert calls == ["do it"]

    job = store.jobs[0]
    assert job["history"] == [{"ran_at": 100, "result": None}]
    assert job["claimed_at"] is None

    for tick in range(110, 110 + 25 * 10, 10):

        def _run(prompt: str, tick: int = tick) -> str:
            return f"ran@{tick}"

        store.tick(tick, _run)

    assert len(store.jobs[0]["history"]) == HISTORY_LIMIT
    assert store.jobs[0]["history"][-1] == {"ran_at": 350, "result": "ran@350"}
    assert store.jobs[0]["history"][0]["ran_at"] == (350 - (HISTORY_LIMIT - 1) * 10)

    reloaded = JobStore(tmp_path / "jobs.json")
    assert len(reloaded.jobs[0]["history"]) == HISTORY_LIMIT


def test_old_job_store_backfills_and_ticks(tmp_path: Path):
    path = tmp_path / "jobs.json"
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "old",
                        "prompt": "legacy",
                        "due_at": 50,
                        "interval_seconds": None,
                        "completed": False,
                        "last_result": None,
                        "last_ran_at": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = JobStore(path)
    ran = store.tick(50, lambda prompt: "legacy-done")

    assert [job["id"] for job in ran] == ["old"]
    assert store.jobs[0]["completed"] is True
    assert store.jobs[0]["history"] == [{"ran_at": 50, "result": "legacy-done"}]


def test_workflow_resumes_from_its_last_checkpoint(tmp_path: Path):
    effects: list[str] = []

    def step_one(state: dict) -> dict:
        effects.append("one")
        return {**state, "one": True}

    attempts: list[str] = []

    def step_two(state: dict) -> dict:
        effects.append("two")
        if not attempts:
            attempts.append("crashed")
            raise RuntimeError("crash")
        return {**state, "two": True}

    def step_three(state: dict) -> dict:
        effects.append("three")
        return {**state, "three": True}

    steps: list[Callable[[dict], dict]] = [step_one, step_two, step_three]
    with pytest.raises(RuntimeError, match="crash"):
        run_workflow(tmp_path, "wf-1", steps, {"seed": 1})

    checkpoint = load_checkpoint(tmp_path, "wf-1")
    assert checkpoint is not None
    assert checkpoint["done"] == 1
    assert effects == ["one", "two"]

    finished = run_workflow(tmp_path, "wf-1", steps)

    assert finished["done"] is True
    assert finished["state"] == {"seed": 1, "one": True, "two": True, "three": True}
    assert effects == ["one", "two", "two", "three"]
    final_checkpoint = load_checkpoint(tmp_path, "wf-1")
    assert final_checkpoint is not None and final_checkpoint["done"] == 3

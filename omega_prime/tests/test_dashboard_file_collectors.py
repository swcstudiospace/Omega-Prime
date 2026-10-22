# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for bounded, redacted local dashboard collectors."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

from omega_prime.dashboard.collectors.agentos import make_agentos_collector
from omega_prime.dashboard.collectors.command_log import make_command_log_collector
from omega_prime.dashboard.collectors.omega_jobs import make_omega_jobs_collector
from omega_prime.dashboard.collectors.omp_sessions import make_omp_sessions_collector
from omega_prime.dashboard.collectors.sandbox_files import make_sandbox_files_collector
from omega_prime.dashboard.collectors.tasks_json import make_tasks_json_collector
from omega_prime.dashboard.collectors.wiring import make_wiring_collector
from omega_prime.dashboard.status_file import write_tasks

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
_TOK = "TOKEN=" + token_hex(8)


def _clock() -> datetime:
    return _NOW


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_omp_sessions_parses_tail_and_caches(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "one.jsonl"
    records = [
        {
            "type": "session",
            "id": "s1",
            "cwd": "/tmp/work",
            "timestamp": "2026-10-21T11:00:00+00:00",
        },
        {"type": "title", "title": "A title"},
        {"type": "model_change", "model": "m1"},
        {
            "type": "model_usage",
            "model": "m2",
            "usage": {"input": 4, "output": 5},
            "stopReason": "done",
        },
        {"type": "other", "unknown": "ignored"},
        {"type": "message", "content": {"tool": "todo_write", "done": 2, "total": 3}},
    ]
    path.write_text(
        "{bad}\n" + "\n".join(json.dumps(row) for row in records) + "\n",
        encoding="utf-8",
    )
    collect = make_omp_sessions_collector(root=root, now_fn=_clock)
    result = collect()
    assert result.items[0]["kind"] == "omp_session"
    assert result.items[0]["session_id"] == "s1"
    assert result.items[0]["title"] == "A title"
    assert result.items[0]["model"] == "m2"
    assert result.items[0]["usage"] == {"input": 4, "output": 5}
    assert "stop_reason" not in result.items[0]
    assert result.items[0]["cwd"] == "/tmp/work"
    assert result.items[0]["progress"] == {"done": 2, "total": 3}
    assert result.extra == {"malformed": 1}
    assert collect() == result


def test_omp_sessions_large_tail_and_cache_invalidation(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "big.jsonl"
    path.write_bytes(
        b"x" * 300_000
        + b"\n"
        + json.dumps({"type": "title", "title": "last"}).encode()
        + b"\n"
    )
    collect = make_omp_sessions_collector(root=root, now_fn=_clock)
    assert collect().items[0]["title"] == "last"
    old = path.stat()
    path.write_bytes(
        b"x" * 300_000
        + b"\n"
        + json.dumps({"type": "title", "title": "next"}).encode()
        + b"\n"
    )
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert collect().items[0]["title"] == "last"
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 10_000_000))
    assert collect().items[0]["title"] == "next"


def test_wiring_collects_phases_and_handles_runner_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    (runs / "p1").mkdir(parents=True)
    (runs / "p1" / "phase.log").write_text(
        '11:00:00 sol start\ntool todo {"done":1,"total":4}\n', encoding="utf-8"
    )
    (runs / "p1" / "sol.done").touch()
    (runs / "p2").mkdir()
    porcelain = f"worktree {repo.parent / 'p1'}\nbranch refs/heads/feature\n\nworktree {repo.parent / 'p3'}\nbranch refs/heads/third\n"

    def runner(args: list[str]) -> str:
        return porcelain if args[-1] == "--porcelain" else "abc123\n"

    result = make_wiring_collector(
        repo_root=repo, runs_root=runs, now_fn=_clock, runner=runner
    )()
    rows = {row["phase"]: row for row in result.items}
    assert rows["p1"]["stage"] == "luna"
    assert rows["p1"]["progress"] == {"done": 1, "total": 4}
    assert rows["p1"]["branch"] == "feature"
    assert rows["p2"]["stage"] == "pending"
    assert rows["p3"]["stage"] == "pending"
    missing_runs = make_wiring_collector(
        repo_root=repo,
        runs_root=tmp_path / "missing",
        now_fn=_clock,
        runner=lambda args: "",
    )()
    assert missing_runs.status == "empty"

    def broken(args: list[str]) -> str:
        raise TimeoutError("timed out")

    assert (
        make_wiring_collector(
            repo_root=repo, runs_root=runs, now_fn=_clock, runner=broken
        )().status
        == "error"
    )


def test_sandbox_files_progress_e2e_and_receipts(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "PROGRESS.md").write_text("older\n latest " + _TOK, encoding="utf-8")
    _dump(root / ".state" / "e2e" / "run-1.jsonl", {"ok": True})
    _dump(root / ".state" / "receipts" / "receipt.json", {"ok": True})
    result = make_sandbox_files_collector(sandbox_root=root, now_fn=_clock)()
    assert result.detail == "latest TOKEN=[REDACTED]"
    assert {row["kind"] for row in result.items} == {"sandbox_e2e", "sandbox_receipt"}
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    assert (
        make_sandbox_files_collector(sandbox_root=empty_root, now_fn=_clock)().status
        == "empty"
    )


def test_omega_jobs_final_text_activity_and_recency(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    job = root / "job-1"
    _dump(
        job / "session.json",
        {"model": "m", "ok": True, "final_text": "line one\nline two " + _TOK},
    )
    (job / "events.jsonl").write_text("", encoding="utf-8")
    result = make_omega_jobs_collector(jobs_root=root, now_fn=_clock)()
    assert result.items[0]["final_text_line"] == "line one"
    assert result.items[0]["ok"] is True
    assert result.extra["total_jobs"] == 1


def test_command_log_redacts_and_tasks_status(tmp_path: Path) -> None:
    log = tmp_path / "commands.jsonl"
    log.write_text(
        json.dumps({"cmd": "echo " + _TOK, "exit_code": 2, "cwd": "/tmp"}) + "\n",
        encoding="utf-8",
    )
    result = make_command_log_collector(log_path=log, now_fn=_clock)()
    assert result.extra["count"] == 1
    assert result.extra["last_exit_codes"] == [2]
    assert _TOK not in json.dumps(result.extra)
    assert (
        make_command_log_collector(
            log_path=tmp_path / "missing", now_fn=_clock
        )().status
        == "unavailable"
    )
    tasks_path = tmp_path / "tasks.json"
    payload = {
        "version": 1,
        "updated_at": _NOW.isoformat(),
        "tasks": [],
        "waiting_on_ove": [],
        "finished": [],
    }
    write_tasks(payload, path=tasks_path)
    assert (
        make_tasks_json_collector(tasks_path=tasks_path, now_fn=_clock)().status == "ok"
    )
    assert (
        make_tasks_json_collector(
            tasks_path=tmp_path / "missing.json", now_fn=_clock
        )().status
        == "missing"
    )
    tasks_path.write_text("{bad", encoding="utf-8")
    assert (
        make_tasks_json_collector(tasks_path=tasks_path, now_fn=_clock)().status
        == "unreadable"
    )


def test_agentos_routing_summary(tmp_path: Path) -> None:
    root = tmp_path / "agentos"
    _dump(root / "gsd-routing.json", {"routes": ["a", "b"]})
    (root / "state").mkdir()
    (root / "state" / "one").touch()
    result = make_agentos_collector(agentos_root=root, now_fn=_clock)()
    assert result.extra == {"routing_summary": "2 routes", "state_entries": 1}
    assert (
        make_agentos_collector(
            agentos_root=tmp_path / "missing", now_fn=_clock
        )().status
        == "unavailable"
    )


def test_tasks_json_collector_preserves_validated_numbering_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks.json"
    write_tasks(
        {
            "version": 1,
            "updated_at": _NOW.isoformat(),
            "tasks": [
                {
                    "id": "tasks:stable",
                    "title": "Stable",
                    "owner": {"kind": "human", "ref": "owner"},
                    "state": "working",
                    "seq": 7,
                }
            ],
            "waiting_on_ove": [],
            "finished": [],
            "next_seq": 8,
            "identity_registry": {},
        },
        path=path,
    )
    result = make_tasks_json_collector(tasks_path=path, now_fn=_clock)()
    assert result.status == "ok"
    assert result.extra["tasks"][0]["seq"] == 7
    assert result.extra["next_seq"] == 8
    assert result.extra["identity_registry"] == {}


def test_bridge_task_fields_survive_a_write_read_cycle(tmp_path: Path) -> None:
    from omega_prime.dashboard.model import normalize_tasks

    wiring = {
        "phase": "104",
        "branch": "feat/x",
        "head_sha": "abc1234",
        "stage": "review",
        "repo": "swcstudiospace/Omega-Prime",
        "worktree": "/workspace/x",
    }
    tasks_path = tmp_path / "tasks.json"
    write_tasks(
        {
            "version": 1,
            "updated_at": _NOW.isoformat(),
            "tasks": [
                {
                    "id": "live",
                    "title": "Live work",
                    "owner": {"kind": "agent", "ref": "a1", "label": "Planner"},
                    "state": "working",
                    "model": "model-x",
                    "wiring": wiring,
                },
                {
                    "id": "shipped",
                    "title": "Shipped",
                    "owner": {"kind": "agent", "ref": "a2"},
                    "state": "done",
                    "summary": "merged #58",
                },
            ],
            "waiting_on_ove": [],
            "finished": [],
        },
        path=tasks_path,
    )
    source = make_tasks_json_collector(tasks_path=tasks_path, now_fn=_clock)()
    snap = normalize_tasks({"tasks_json": source}, now=_NOW).to_dict()
    live = next(t for t in snap["active"] if t["id"] == "tasks:live")
    assert live["owner"]["label"] == "Planner"
    assert live["model"] == "model-x"
    assert live["wiring"] == wiring
    shipped = next(t for t in snap["finished"] if t["id"] == "tasks:shipped")
    assert shipped["summary"] == "merged #58"
    assert shipped["owner"]["label"] == "a2"

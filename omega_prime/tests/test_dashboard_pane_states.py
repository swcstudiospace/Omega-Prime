# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Pane states, pane titles, finished sandbox e2e runs and ended tasks."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from omega_prime.dashboard.collectors.herdr import make_herdr_collector
from omega_prime.dashboard.collectors.sandbox_files import (
    make_sandbox_files_collector,
)
from omega_prime.dashboard.model import SourceResult, normalize_tasks

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def by_kind(*results: SourceResult) -> dict[str, SourceResult]:
    """Bucket collector items by kind, as the poller does."""
    out: dict[str, SourceResult] = {}
    for result in results:
        for item in result.items:
            out.setdefault(item["kind"], SourceResult(item["kind"], "ok")).items.append(
                item
            )
    return out


def _snapshot() -> dict:
    panes = [
        {
            "pane_id": "w3:p1",
            "label": "task-dashboard-build",
            "agent_status": "unknown",
            "cwd": "/tmp/dash",
        },
        {
            "pane_id": "w4:p1",
            "label": "task-dashboard-server",
            "agent_status": "unknown",
            "cwd": "/tmp/dash",
        },
    ]
    return {"result": {"snapshot": {"workspaces": [{"tabs": [{"panes": panes}]}]}}}


def _process_info(pane: str) -> dict:
    if pane == "w4:p1":
        procs = [
            {
                "argv": ["/venv/bin/python", "-m", "omega_prime.dashboard"],
                "name": "python",
                "pid": 20,
            }
        ]
        shell = 10
    else:
        procs = [{"argv": ["/bin/sh"], "name": "sh", "pid": 11}]
        shell = 11
    return {
        "result": {"process_info": {"foreground_processes": procs, "shell_pid": shell}}
    }


def _runner(argv: list[str]) -> str:
    if argv[:3] == ["herdr", "api", "snapshot"]:
        return json.dumps(_snapshot())
    assert argv[:4] == ["herdr", "pane", "process-info", "--pane"]
    return json.dumps(_process_info(argv[4]))


def test_shell_panes_get_real_state_and_readable_title() -> None:
    herdr = make_herdr_collector(runner=_runner)()
    snap = normalize_tasks(by_kind(herdr), now=NOW)
    by_id = {t.id: t for t in snap.active}
    server, build = by_id["herdr:w4:p1"], by_id["herdr:w3:p1"]
    assert (server.title, server.state) == ("Dashboard server", "working")
    assert "omega_prime.dashboard" in str(server.detail)
    assert build.state == "idle"
    assert build.title.startswith("Task dashboard build")
    assert "idle shell" in str(build.detail)
    blob = json.dumps(snap.to_dict())
    assert "unknown" not in blob


def test_pane_without_process_info_is_idle_not_unknown() -> None:
    def runner(argv: list[str]) -> str:
        if argv[:3] == ["herdr", "api", "snapshot"]:
            return json.dumps(_snapshot())
        raise OSError("no info")

    snap = normalize_tasks(by_kind(make_herdr_collector(runner=runner)()), now=NOW)
    assert {t.state for t in snap.active} == {"idle"}
    assert "unknown" not in json.dumps(snap.to_dict())


def test_proc_detail_without_mode_does_not_say_unknown() -> None:
    src = {
        "herdr_pane": SourceResult(
            "herdr_pane",
            "ok",
            [
                {
                    "kind": "herdr_pane",
                    "pane_id": "w2:p1",
                    "name": "swarm-smoke",
                    "agent_status": "idle",
                    "cwd": "/tmp/smoke",
                }
            ],
        ),
        "omp_proc": SourceResult(
            "omp_proc", "ok", [{"kind": "omp_proc", "pid": 7, "cwd": "/tmp/smoke"}]
        ),
    }
    task = normalize_tasks(src, now=NOW).active[0]
    assert task.detail == "pid 7"


def _write_run(root: Path, rid: str, oks: list[bool]) -> None:
    e2e = root / ".state" / "e2e"
    e2e.mkdir(parents=True, exist_ok=True)
    (e2e / f"{rid}.jsonl").write_text('{"kind": "out"}\n')
    checks = [{"check": f"c{i}", "ok": ok} for i, ok in enumerate(oks)]
    (e2e / f"{rid}.summary.json").write_text(json.dumps({"checks": checks}))
    stamp = NOW.timestamp() - 3600
    os.utime(e2e / f"{rid}.jsonl", (stamp, stamp))


def test_finished_sandbox_e2e_moves_to_finished_with_result(tmp_path: Path) -> None:
    _write_run(tmp_path, "20261021-021833", [True] * 30)
    collect = make_sandbox_files_collector(sandbox_root=tmp_path, now_fn=lambda: NOW)
    snap = normalize_tasks(by_kind(collect()), now=NOW, quiet_minutes=15)
    assert not [t for t in snap.active if t.id.startswith("sandbox:e2e:")]
    done = next(f for f in snap.finished if f.id == "sandbox:e2e:20261021-021833")
    assert (done.result, done.summary) == ("passed", "ALL PASS 30/30")


def test_failed_sandbox_e2e_reports_failed(tmp_path: Path) -> None:
    _write_run(tmp_path, "20261021-012659", [True] * 23 + [False] * 2)
    collect = make_sandbox_files_collector(sandbox_root=tmp_path, now_fn=lambda: NOW)
    snap = normalize_tasks(by_kind(collect()), now=NOW)
    done = next(f for f in snap.finished if f.id.startswith("sandbox:e2e:"))
    assert (done.result, done.summary) == ("failed", "FAILED 23/25")


def test_running_sandbox_e2e_without_summary_stays_active(tmp_path: Path) -> None:
    e2e = tmp_path / ".state" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "20261021-090000.jsonl").write_text("{}\n")
    stamp = NOW.timestamp() - 60
    os.utime(e2e / "20261021-090000.jsonl", (stamp, stamp))
    collect = make_sandbox_files_collector(sandbox_root=tmp_path, now_fn=lambda: NOW)
    snap = normalize_tasks(by_kind(collect()), now=NOW)
    assert [t.state for t in snap.active if t.id.startswith("sandbox:e2e:")] == [
        "working"
    ]


def test_tasks_json_done_task_is_not_in_active() -> None:
    tasks = [{"id": "gb:omp-sandbox-rework", "title": "rework", "state": "done"}]
    snap = normalize_tasks(
        {"tasks_json": SourceResult("tasks_json", "ok", [], extra={"tasks": tasks})},
        now=NOW,
    )
    assert snap.active == () or list(snap.active) == []
    assert [f.id for f in snap.finished] == ["tasks:gb:omp-sandbox-rework"]

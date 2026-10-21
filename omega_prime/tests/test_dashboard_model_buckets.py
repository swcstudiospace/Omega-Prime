from __future__ import annotations

from datetime import UTC, datetime, timedelta

from omega_prime.dashboard.model import SourceResult, normalize_tasks

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def _source(name: str, rows: list[dict], extra: dict | None = None) -> SourceResult:
    return SourceResult(name, "ok", rows, extra=extra or {})


def test_done_and_failed_are_finished_only():
    snapshot = normalize_tasks({"tasks_json": _source("tasks_json", [], {"tasks": [
        {"id": "done-1", "title": "Done", "state": "done", "owner": {"kind": "human", "ref": "owner", "label": "Owner"}, "started_at": "start", "last_activity_at": "finish", "progress": {"done": 4, "total": 4}, "model": "model-x", "note": "detail", "links": [{"label": "review", "path_or_url": "https://github.com/example/repo"}], "wiring": {"phase": "106", "worktree": "/tmp/wt"}},
        {"id": "failed-1", "title": "Failed", "state": "failed", "last_activity_at": "failed-at"},
    ]})}, now=NOW)
    assert snapshot.active == []
    finished = {item.id: item.to_dict() for item in snapshot.finished}
    assert finished["tasks:done-1"]["result"] == "ok"
    assert finished["tasks:failed-1"]["result"] == "failed"
    assert finished["tasks:done-1"]["owner"]["label"] == "Owner"
    assert finished["tasks:done-1"]["model"] == "model-x"
    assert finished["tasks:done-1"]["progress"] == {"done": 4, "total": 4}
    assert finished["tasks:done-1"]["links"][0]["label"] == "review"
    assert finished["tasks:done-1"]["wiring"]["worktree"] == "/tmp/wt"


def test_unknown_exited_and_quiet_sessions_are_idle_only():
    stamp = NOW.isoformat()
    sources = {
        "herdr_pane": _source("herdr_pane", [
            {"pane_id": "unknown", "agent_status": "unknown", "session_path": "/unknown", "has_live_agent": True},
            {"pane_id": "nonlive", "agent_status": "working", "session_path": "/nonlive", "has_live_agent": False},
            {"pane_id": "stopped", "agent_status": "working", "session_path": "/stopped", "has_live_agent": True},
            {"pane_id": "working", "agent_status": "working", "session_path": "/working", "has_live_agent": True},
            {"pane_id": "idle", "agent_status": "idle", "session_path": "/idle", "has_live_agent": True},
            {"pane_id": "blocked", "agent_status": "blocked", "session_path": "/blocked", "has_live_agent": True},
        ]),
        "omp_session": _source("omp_session", [
            {"session_path": "/unknown", "last_ts": stamp},
            {"session_path": "/nonlive", "last_ts": stamp},
            {"session_path": "/stopped", "last_ts": stamp, "stop_reason": "exited"},
            {"session_path": "/working", "last_ts": stamp},
            {"session_path": "/idle", "last_ts": stamp},
            {"session_path": "/blocked", "last_ts": stamp},
        ]),
        "sandbox_e2e": _source("sandbox_e2e", [
            {"run_id": "quiet-run", "last_ts": (NOW - timedelta(minutes=30)).isoformat()},
            {"run_id": "working-run", "last_ts": stamp},
        ]),
        "omp_proc": _source("omp_proc", []),
    }
    snapshot = normalize_tasks(sources, now=NOW, quiet_minutes=15)
    idle_ids = {task.id for task in snapshot.idle_sessions}
    active_ids = {task.id for task in snapshot.active}
    assert {"herdr:unknown", "herdr:nonlive", "herdr:stopped", "sandbox:e2e:quiet-run"} <= idle_ids
    assert not idle_ids & active_ids
    assert {"herdr:working", "herdr:idle", "herdr:blocked", "sandbox:e2e:working-run"} <= active_ids
    assert any(item.related_task_id == "herdr:blocked" for item in snapshot.waiting_on_ove)


def test_idle_changes_snapshot_hash_and_keeps_number_across_restart(tmp_path):
    from omega_prime.dashboard.poller import DashboardPoller

    path = tmp_path / "tasks.json"

    def build(status: str, title: str, at: datetime) -> DashboardPoller:
        poller = DashboardPoller([], task_ids_path=path, clock=lambda: at)
        poller._results["herdr"] = _source(
            "herdr", [{
                "pane_id": "durable", "agent_status": status,
                "terminal_title": title, "has_live_agent": True,
            }]
        )
        poller._build_snapshot()
        return poller

    active = build("working", "Durable", NOW)
    seq = active.snapshot()["active"][0]["seq"]
    idle = build("unknown", "Durable", NOW + timedelta(minutes=1))
    idle_row = idle.snapshot()["idle_sessions"][0]
    assert idle_row["seq"] == seq
    idle_hash = idle.snapshot_hash()
    changed_idle = build("unknown", "Idle changed", NOW + timedelta(minutes=1))
    assert changed_idle.snapshot_hash() != idle_hash

    restored = build("working", "Durable", NOW + timedelta(minutes=2))
    restored_row = restored.snapshot()["active"][0]
    assert restored_row["seq"] == seq
    assert restored_row["timeline"][-1]["state"] == "working"
    assert idle_row["timeline"][-1]["state"] == "unknown"

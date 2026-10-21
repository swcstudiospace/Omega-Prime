from datetime import UTC, datetime

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.poller import CollectorSpec, DashboardPoller

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def test_persisted_task_numbering_reaches_snapshot(tmp_path):
    bridge = tmp_path / "tasks.json"
    poller = DashboardPoller([], task_ids_path=bridge, clock=lambda: NOW)
    poller._results["tasks_json"] = SourceResult(
        "tasks_json",
        "ok",
        extra={
            "tasks": [
                {
                    "id": "tasks:stable",
                    "title": "Stable task",
                    "state": "working",
                    "owner": {"kind": "human", "ref": "owner"},
                },
                {
                    "id": "tasks:idle",
                    "title": "Idle task",
                    "state": "queued",
                    "owner": {"kind": "human", "ref": "owner"},
                },
            ],
            "waiting_on_ove": [],
            "finished": [],
        },
    )
    poller._build_snapshot()
    first = poller.snapshot()
    assert first is not None
    assert first["idle_sessions"][0]["id"] == "tasks:idle"
    assert first["idle_sessions"][0]["seq"] == 1
    assert first["active"][0]["seq"] == 2
    assert first["active"][0]["display_id"] == "00002 - Stable task"
    assert set(first) == {"generated_at", "sources", "active", "pr_gate", "waiting_on_ove", "finished", "idle_sessions"}

    restarted = DashboardPoller([], task_ids_path=bridge, clock=lambda: NOW)
    restarted._results["tasks_json"] = poller._results["tasks_json"]
    restarted._build_snapshot()
    second = restarted.snapshot()
    assert second is not None
    assert second["active"][0]["seq"] == 2
    assert second["active"][0]["display_id"] == "00002 - Stable task"
    assert second["idle_sessions"][0]["seq"] == first["idle_sessions"][0]["seq"]
    assert second["active"][0]["timeline"] == first["active"][0]["timeline"]


def test_github_command_center_extra_is_bounded_and_redacted():
    import asyncio

    extra = {
        "command_center": {
            "repositories": [{"name": "repo", "token": "ghp_12345678901234567890"}],
            "ci_cd": [],
            "recent_merges": [],
            "meta": {"secret": "secret_value"},
            "unbounded": "drop me",
        },
        "unrelated": "drop me too",
    }
    poller = DashboardPoller(
        [CollectorSpec("github", lambda: SourceResult("github", "ok", extra=extra), 1.0, 1.0)],
        clock=lambda: NOW,
    )
    asyncio.run(poller._collect(poller.specs[0]))
    poller._build_snapshot()
    snapshot = poller.snapshot()
    assert snapshot is not None
    status = snapshot["sources"]["github"]
    assert set(status["command_center"]) == {"repositories", "ci_cd", "recent_merges", "meta"}
    rendered = str(status["command_center"])
    assert "ghp_12345678901234567890" not in rendered
    assert "secret_value" not in rendered
    assert "unrelated" not in status

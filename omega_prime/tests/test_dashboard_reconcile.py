# SPDX-License-Identifier: AGPL-3.0-only
"""Protocol default 4: dry-run-by-default reconciler CLI over fake Herdr runners."""
import json
from datetime import UTC, datetime, timedelta

from omega_prime.dashboard.reconcile import main
from omega_prime.dashboard.status_file import locked_tasks

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _iso(minutes_before):
    return (NOW - timedelta(minutes=minutes_before)).isoformat()


def _seed(path, rows, updated_at="2026-10-21T11:30:00+00:00"):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": updated_at,
                "tasks": rows,
                "waiting_on_ove": [],
                "finished": [],
                "next_seq": 1,
                "identity_registry": {},
            }
        )
        + "\n"
    )


def _row(rid, **over):
    row = {
        "id": rid,
        "title": rid,
        "owner": {"kind": "human", "ref": "o"},
        "state": "working",
        "last_activity_at": _iso(1),
    }
    row.update(over)
    return row


def _panes():
    return {
        "panes": [
            {"pane_id": "w1:p1", "workspace": "00031-b"},
            {"pane_id": "w2:p1", "workspace": "00031-c"},
        ]
    }


def _runner(calls=None, dead_pid=4242, list_payload=None):
    def runner(argv):
        if calls is not None:
            calls.append(argv)
        if argv[1:3] == ["pane", "list"]:
            return json.dumps(_panes() if list_payload is None else list_payload)
        if argv[1:3] == ["pane", "process-info"]:
            return json.dumps({"result": {"pid": dead_pid}})
        raise AssertionError(f"unexpected argv {argv[:3]}")

    return runner


def _alive(pid):
    return pid != 4242


def _report(capsys):
    return json.loads(capsys.readouterr().out)


def _rows(path):
    with locked_tasks(path, write=False) as data:
        return {row["id"]: row for row in data["tasks"]}


def _fixture_rows():
    return [
        _row(
            "gb:00031-a",
            herdr_pane={"pane_id": "w1:p2", "workspace": "00031-a"},
            last_activity_at=_iso(30),
        ),
        _row(
            "gb:00031-b",
            herdr_pane={"pane_id": "w1:p1", "workspace": "00031-b"},
            last_activity_at=_iso(30),
        ),
        _row("gb:00031-c"),
    ]


def test_dry_run_reports_without_touching_the_file(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows())
    before = path.read_bytes()
    code = main(
        ["--tasks", str(path)],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    report = _report(capsys)
    assert report["herdr"] == "ok"
    assert report["protected"] is False
    assert report["dead"] == [
        {"id": "gb:00031-a", "reason": "pane_gone", "pane_id": "w1:p2", "pid": None},
        {"id": "gb:00031-b", "reason": "pid_dead", "pane_id": "w1:p1", "pid": None},
    ]
    assert report["attach"] == [
        {"id": "gb:00031-c", "pane_id": "w2:p1", "workspace": "00031-c"}
    ]
    assert path.read_bytes() == before


def test_apply_attaches_orphan_and_marks_dead_rows(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows())
    calls: list[list[str]] = []
    code = main(
        ["--tasks", str(path), "--apply"],
        runner=_runner(calls),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    process_calls = [c for c in calls if c[1:3] == ["pane", "process-info"]]
    assert process_calls == [["herdr", "pane", "process-info", "--pane", "w1:p1"]]
    rows = _rows(path)
    assert set(rows) == {"gb:00031-a", "gb:00031-b", "gb:00031-c"}
    assert rows["gb:00031-c"]["herdr_pane"] == {
        "pane_id": "w2:p1",
        "workspace": "00031-c",
    }
    assert rows["gb:00031-a"]["stale"]["reason"] == "pane_gone"
    assert rows["gb:00031-a"]["stale"]["since"] == _iso(30)
    assert rows["gb:00031-b"]["stale"]["reason"] == "pid_dead"
    assert all(row["state"] == "working" for row in rows.values())


def test_young_updated_at_protects_unless_forced(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows(), updated_at=_iso(5))
    before = path.read_bytes()
    code = main(
        ["--tasks", str(path), "--apply"],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    assert _report(capsys)["protected"] is True
    assert path.read_bytes() == before

    code = main(
        ["--tasks", str(path), "--apply", "--force"],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    assert _report(capsys)["protected"] is False
    assert _rows(path)["gb:00031-c"]["herdr_pane"]["pane_id"] == "w2:p1"


def test_old_updated_at_applies_without_force(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows(), updated_at=_iso(30))
    code = main(
        ["--tasks", str(path), "--apply"],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    assert _rows(path)["gb:00031-c"]["herdr_pane"]["pane_id"] == "w2:p1"


def test_stale_minutes_threshold_tunes_no_heartbeat(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    rows = [_row("gb:00031-d", last_heartbeat_at=_iso(10), last_activity_at=None)]
    _seed(path, rows)
    code = main(
        ["--tasks", str(path)],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    assert _report(capsys)["no_heartbeat"] == []

    code = main(
        ["--tasks", str(path), "--stale-minutes", "5"],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    report = _report(capsys)
    assert report["no_heartbeat"] == [{"id": "gb:00031-d", "since": _iso(10)}]

    code = main(
        ["--tasks", str(path), "--stale-minutes", "5", "--apply"],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    assert _rows(path)["gb:00031-d"]["stale"] == {
        "reason": "no_heartbeat",
        "since": _iso(10),
    }


def test_unavailable_runner_reports_no_pane_deaths(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows())

    def broken(argv):
        raise OSError("herdr missing")

    code = main(
        ["--tasks", str(path)],
        runner=broken,
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    report = _report(capsys)
    assert report["herdr"] == "unavailable"
    assert report["dead"] == []
    assert report["attach"] == []


def test_report_never_prints_row_secrets(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, [_row("gb:00031-a", note="GITHUB_PAT=ghp_supersecrettokenvalue")])
    code = main(
        ["--tasks", str(path)],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "ghp_supersecrettokenvalue" not in out
    assert json.loads(out)["dead"] == []


def test_invalid_tasks_file_reports_error_and_returns_one(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    path.write_text("not json at all")
    code = main(
        ["--tasks", str(path)],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert json.loads(err)["error"] == "invalid tasks file"

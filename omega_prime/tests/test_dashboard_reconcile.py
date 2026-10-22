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
    # Canary assembled at runtime: no complete credential-shaped literal.
    canary = "gh" + "p_" + "canary" + "0" * 12
    _seed(path, [_row("gb:00031-a", note="GITHUB" + "_PAT=" + canary)])
    code = main(
        ["--tasks", str(path)],
        runner=_runner(),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert canary not in out
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


# --- Greptile round 1 regressions -------------------------------------------


def test_mapped_row_with_dead_pid_is_dead_even_when_herdr_unavailable(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, [_row("gb:mapped", pid=4242, herdr_pane={"pane_id": "w1:p1"})])

    def runner(argv):
        raise OSError("herdr missing")

    assert (
        main(["--tasks", str(path)], runner=runner, pid_alive=_alive, clock=lambda: NOW)
        == 0
    )
    report = _report(capsys)
    assert report["herdr"] == "unavailable"
    assert [(row["id"], row["reason"]) for row in report["dead"]] == [
        ("gb:mapped", "pid_dead")
    ]


def test_mapped_row_with_dead_pid_is_dead_when_pane_listed(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, [_row("gb:mapped", pid=4242, herdr_pane={"pane_id": "w1:p1"})])
    runner = _runner(dead_pid=999)
    assert (
        main(["--tasks", str(path)], runner=runner, pid_alive=_alive, clock=lambda: NOW)
        == 0
    )
    assert [(row["id"], row["reason"]) for row in _report(capsys)["dead"]] == [
        ("gb:mapped", "pid_dead")
    ]


def test_exited_pane_is_reported_pane_exited(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(
        path,
        [
            _row("gb:flag", herdr_pane={"pane_id": "w1:p1"}),
            _row("gb:status", herdr_pane={"pane_id": "w2:p1"}),
        ],
    )
    payload = {
        "panes": [
            {"pane_id": "w1:p1", "exited": True},
            {"pane_id": "w2:p1", "status": "Exited"},
        ]
    }
    runner = _runner(dead_pid=999, list_payload=payload)
    assert (
        main(["--tasks", str(path)], runner=runner, pid_alive=_alive, clock=lambda: NOW)
        == 0
    )
    dead = {row["id"]: row["reason"] for row in _report(capsys)["dead"]}
    assert dead == {"gb:flag": "pane_exited", "gb:status": "pane_exited"}


def test_unusable_pane_listing_is_unavailable_not_pane_gone(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, [_row("gb:mapped", herdr_pane={"pane_id": "w1:p1"})])
    runner = _runner(dead_pid=999, list_payload={"panes": [{}]})
    assert (
        main(["--tasks", str(path)], runner=runner, pid_alive=_alive, clock=lambda: NOW)
        == 0
    )
    report = _report(capsys)
    assert report["herdr"] == "unavailable"
    assert report["dead"] == []


# --- Greptile round 3 regressions -------------------------------------------


def test_herdr_timeouts_degrade_to_unavailable_evidence(tmp_path, capsys):
    import subprocess

    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows())

    def list_times_out(argv):
        raise subprocess.TimeoutExpired(argv, 15)

    assert (
        main(
            ["--tasks", str(path)],
            runner=list_times_out,
            pid_alive=_alive,
            clock=lambda: NOW,
        )
        == 0
    )
    assert _report(capsys)["herdr"] == "unavailable"

    def info_times_out(argv):
        if argv[1:3] == ["pane", "list"]:
            return json.dumps(_panes())
        raise subprocess.TimeoutExpired(argv, 15)

    assert (
        main(
            ["--tasks", str(path)],
            runner=info_times_out,
            pid_alive=_alive,
            clock=lambda: NOW,
        )
        == 0
    )
    report = _report(capsys)
    assert report["herdr"] == "ok"
    # w1:p2 is absent from the pane list; w1:p1's pid is unknown, not dead.
    assert {d["id"]: d["reason"] for d in report["dead"]} == {"gb:00031-a": "pane_gone"}


def test_herdr_calls_run_without_holding_the_tasks_lock(tmp_path, capsys):
    import fcntl
    import os

    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows())
    lock_path = path.with_name(path.name + ".lock")
    observed = []

    def probing(argv):
        fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed.append("free")
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BlockingIOError:
            observed.append("held")
        finally:
            os.close(fd)
        return _runner()(argv)

    for argv in (["--tasks", str(path)], ["--tasks", str(path), "--apply"]):
        assert main(argv, runner=probing, pid_alive=_alive, clock=lambda: NOW) == 0
        capsys.readouterr()
    assert observed and set(observed) == {"free"}


def test_apply_rechecks_rows_reread_after_evidence(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows())

    def finishing_runner(argv):
        # While Herdr evidence is gathered, another writer finishes the row
        # whose pane is gone; the apply pass must not stamp it stale.
        if argv[1:3] == ["pane", "list"]:
            with locked_tasks(path, write=True) as data:
                for row in data["tasks"]:
                    if row["id"] == "gb:00031-a":
                        row["state"] = "done"
                from omega_prime.dashboard.status_file import _write_locked

                _write_locked(data, path)
        return _runner()(argv)

    assert (
        main(
            ["--tasks", str(path), "--apply"],
            runner=finishing_runner,
            pid_alive=_alive,
            clock=lambda: NOW,
        )
        == 0
    )
    capsys.readouterr()
    rows = _rows(path)
    assert rows["gb:00031-a"]["state"] == "done"
    assert rows["gb:00031-a"].get("stale") is None, rows["gb:00031-a"].get("stale")


def test_apply_skips_rows_remapped_during_evidence(tmp_path, capsys):
    """Greptile r4: a row mapped to a new pane after the pane list was read
    is not judged against that old list, so no false pane_gone is saved."""
    path = tmp_path / "tasks.json"
    _seed(path, _fixture_rows())

    def remapping_runner(argv):
        if argv[1:3] == ["pane", "list"]:
            payload = _runner()(argv)
            from omega_prime.dashboard.status_file import _write_locked

            with locked_tasks(path, write=True) as data:
                for row in data["tasks"]:
                    if row["id"] == "gb:00031-b":
                        row["herdr_pane"] = {"pane_id": "w9:p1", "workspace": "00031-b"}
                _write_locked(data, path)
            return payload
        return _runner()(argv)

    assert (
        main(
            ["--tasks", str(path), "--apply", "--force"],
            runner=remapping_runner,
            pid_alive=_alive,
            clock=lambda: NOW,
        )
        == 0
    )
    report = _report(capsys)
    rows = _rows(path)
    assert rows["gb:00031-b"]["herdr_pane"]["pane_id"] == "w9:p1"
    assert rows["gb:00031-b"].get("stale") is None, rows["gb:00031-b"].get("stale")
    assert "gb:00031-b" not in {entry["id"] for entry in report["dead"]}
    assert rows["gb:00031-a"]["stale"]["reason"] == "pane_gone"


def _seed_with_finished(path, rows, finished):
    _seed(path, rows)
    data = json.loads(path.read_text())
    data["finished"] = finished
    path.write_text(json.dumps(data) + "\n")


def test_owner_mapped_row_is_not_reattached_and_is_judged(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(
        path,
        [
            _row(
                "gb:00031-c",
                owner={"kind": "herdr-pane", "ref": "w1:p1"},
                last_activity_at=_iso(30),
            )
        ],
    )
    calls: list[list[str]] = []
    code = main(
        ["--tasks", str(path), "--apply"],
        runner=_runner(calls),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    report = _report(capsys)
    assert report["attach"] == []
    assert report["dead"] == [
        {"id": "gb:00031-c", "reason": "pid_dead", "pane_id": "w1:p1", "pid": None}
    ]
    assert ["herdr", "pane", "process-info", "--pane", "w1:p1"] in calls
    assert _rows(path)["gb:00031-c"].get("herdr_pane") is None


def test_pane_kept_by_a_finished_row_is_not_reassigned(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    finished = [
        {
            "id": "gb:old",
            "title": "gb:old",
            "finished_at": _iso(5),
            "result": "ok",
            "herdr_pane": {"pane_id": "w2:p1", "workspace": "00031-c"},
        }
    ]
    _seed_with_finished(path, [_row("gb:00031-c")], finished)
    code = main(
        ["--tasks", str(path)], runner=_runner(), pid_alive=_alive, clock=lambda: NOW
    )
    assert code == 0
    assert _report(capsys)["attach"] == []


def test_attach_skips_exited_panes_with_the_same_label(tmp_path, capsys):
    path = tmp_path / "tasks.json"
    _seed(path, [_row("gb:00031-c")])
    payload = {
        "panes": [
            {"pane_id": "w1:p1", "workspace": "00031-c", "exited": True},
            {"pane_id": "w2:p1", "workspace": "00031-c"},
        ]
    }
    code = main(
        ["--tasks", str(path)],
        runner=_runner(list_payload=payload),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    assert _report(capsys)["attach"] == [
        {"id": "gb:00031-c", "pane_id": "w2:p1", "workspace": "00031-c"}
    ]


def test_attach_refuses_a_workspace_label_shared_by_two_tasks(tmp_path, capsys):
    """Greptile: gb:build and tasks:build share the label "build"; an orphan
    "build" pane is ambiguous and must not be saved on either row."""
    path = tmp_path / "tasks.json"
    _seed(path, [_row("gb:build"), _row("tasks:build")])
    payload = {"panes": [{"pane_id": "w3:p1", "workspace": "build"}]}
    code = main(
        ["--tasks", str(path), "--apply", "--force"],
        runner=_runner(list_payload=payload),
        pid_alive=_alive,
        clock=lambda: NOW,
    )
    assert code == 0
    assert _report(capsys)["attach"] == []
    rows = _rows(path)
    assert rows["gb:build"].get("herdr_pane") is None
    assert rows["tasks:build"].get("herdr_pane") is None

# SPDX-License-Identifier: AGPL-3.0-only
import json
from datetime import UTC, datetime

import pytest

from omega_prime.dashboard.herdr_launch import launch, record_pane, workspace_label
from omega_prime.dashboard.status_file import locked_tasks, write_tasks

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _empty(path):
    write_tasks(
        {
            "version": 1,
            "updated_at": "now",
            "tasks": [],
            "waiting_on_ove": [],
            "finished": [],
        },
        path=path,
    )


def test_launch_creates_pane_runs_command_and_records_it(tmp_path):
    path = tmp_path / "tasks.json"
    _empty(path)
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[1:3] == ["workspace", "create"]:
            return json.dumps({"result": {"root_pane": {"pane_id": "w9:p1"}}})
        return "{}"

    row = launch(
        path=path,
        identity="gb:00099-x",
        title="00099 - X",
        cwd="/workspace/x",
        command=["bash", "drive.sh", "--stage", "plan"],
        runner=runner,
        now=NOW,
    )
    assert calls[0][:3] == ["herdr", "workspace", "create"] and "00099-x" in calls[0]
    assert calls[1] == ["herdr", "pane", "run", "w9:p1", "bash drive.sh --stage plan"]
    assert not any(
        c[1:3] in (["pane", "send-text"], ["pane", "send-keys"]) for c in calls
    )
    assert row["herdr_pane"] == {"pane_id": "w9:p1", "workspace": "00099-x"}
    with locked_tasks(path, write=False) as data:
        saved = data["tasks"][0]
    assert saved["state"] == "working" and saved["herdr_pane"]["pane_id"] == "w9:p1"


def test_record_pane_attaches_existing_task_and_unblocks(tmp_path):
    path = tmp_path / "tasks.json"
    write_tasks(
        {
            "version": 1,
            "updated_at": "now",
            "waiting_on_ove": [],
            "finished": [],
            "tasks": [
                {
                    "id": "gb:a",
                    "title": "a",
                    "owner": {"kind": "grokbot-executor", "ref": "/w"},
                    "state": "blocked",
                    "note": "kept",
                }
            ],
        },
        path=path,
    )
    record_pane(
        path=path,
        identity="gb:a",
        title="a",
        pane_id="w2:p3",
        workspace=None,
        owner_ref="/w",
        now=NOW,
    )
    with locked_tasks(path, write=False) as data:
        row = data["tasks"][0]
    assert (
        row["herdr_pane"] == {"pane_id": "w2:p3"}
        and row["state"] == "working"
        and row["note"] == "kept"
    )


def test_bad_pane_id_is_refused(tmp_path):
    path = tmp_path / "tasks.json"
    _empty(path)
    with pytest.raises(ValueError):
        record_pane(
            path=path,
            identity="gb:a",
            title="a",
            pane_id="w2:p3\n--evil",
            workspace=None,
            owner_ref="/w",
        )


@pytest.mark.parametrize("pane_id", ["wA:p1", "w1F:pB"])
def test_hex_numbered_pane_is_recorded(tmp_path, pane_id):
    """Herdr numbers workspaces past w9 as wA, wB, ...; those panes must record."""
    path = tmp_path / "tasks.json"
    _empty(path)
    record_pane(
        path=path,
        identity="gb:a",
        title="a",
        pane_id=pane_id,
        workspace=None,
        owner_ref="/w",
        now=NOW,
    )
    with locked_tasks(path, write=False) as data:
        assert data["tasks"][0]["herdr_pane"] == {"pane_id": pane_id}


@pytest.mark.parametrize("pane_id", ["wA:p1;rm", "w:p1", "wA:", "../w1:p1"])
def test_malformed_pane_ids_stay_refused(tmp_path, pane_id):
    path = tmp_path / "tasks.json"
    _empty(path)
    with pytest.raises(ValueError):
        record_pane(
            path=path,
            identity="gb:a",
            title="a",
            pane_id=pane_id,
            workspace=None,
            owner_ref="/w",
        )


def test_workspace_label_is_safe():
    assert workspace_label("gb:00031-Triage Run!") == "00031-triage-run"


def _runner_for(pane_id, calls):
    def runner(argv):
        calls.append(argv)
        if argv[1:3] == ["workspace", "create"]:
            return json.dumps({"result": {"root_pane": {"pane_id": pane_id}}})
        return "{}"

    return runner


def test_cli_strips_only_the_leading_separator(tmp_path, monkeypatch):
    """An inner ``--`` belongs to the task command and must reach Herdr."""
    from omega_prime.dashboard import herdr_launch

    path = tmp_path / "tasks.json"
    _empty(path)
    seen = {}

    def fake_launch(**kwargs):
        seen["command"] = list(kwargs["command"])
        return {"id": kwargs["identity"], "herdr_pane": {"pane_id": "w9:p1"}}

    monkeypatch.setattr(herdr_launch, "launch", fake_launch)
    code = herdr_launch.main(
        [
            "--id",
            "gb:a",
            "--title",
            "a",
            "--tasks",
            str(path),
            "--",
            "rm",
            "--",
            "-scratch",
        ]
    )
    assert code == 0
    assert seen["command"] == ["rm", "--", "-scratch"]


def test_attach_with_displayed_id_updates_the_stored_row(tmp_path):
    """The dashboard shows ``tasks:a`` for a stored ``a``; both name one task."""
    path = tmp_path / "tasks.json"
    write_tasks(
        {
            "version": 1,
            "updated_at": "now",
            "waiting_on_ove": [],
            "finished": [],
            "tasks": [
                {
                    "id": "a",
                    "title": "a",
                    "owner": {"kind": "grokbot-executor", "ref": "/w"},
                    "state": "working",
                }
            ],
        },
        path=path,
    )
    record_pane(
        path=path,
        identity="tasks:a",
        title="a",
        pane_id="w2:p3",
        workspace=None,
        owner_ref="/w",
        now=NOW,
    )
    with locked_tasks(path, write=False) as data:
        rows = data["tasks"]
    assert len(rows) == 1
    assert rows[0]["id"] == "a" and rows[0]["herdr_pane"] == {"pane_id": "w2:p3"}


@pytest.mark.parametrize("finished", ["done", "failed"])
def test_launch_restarts_a_finished_task_but_attach_keeps_its_state(tmp_path, finished):
    path = tmp_path / "tasks.json"
    row = {
        "id": "gb:a",
        "title": "a",
        "owner": {"kind": "grokbot-executor", "ref": "/w"},
        "state": finished,
        "started_at": "2026-10-01T00:00:00+00:00",
    }
    doc = {"version": 1, "updated_at": "now", "waiting_on_ove": [], "finished": []}
    write_tasks({**doc, "tasks": [dict(row)]}, path=path)
    record_pane(
        path=path,
        identity="gb:a",
        title="a",
        pane_id="w2:p3",
        workspace=None,
        owner_ref="/w",
        now=NOW,
    )
    with locked_tasks(path, write=False) as data:
        assert data["tasks"][0]["state"] == finished

    launch(
        path=path,
        identity="gb:a",
        title="a",
        cwd="/w",
        command=["bash", "run.sh"],
        runner=_runner_for("w9:p1", []),
        now=NOW,
    )
    with locked_tasks(path, write=False) as data:
        saved = data["tasks"][0]
    assert saved["state"] == "working"
    assert saved["started_at"] == NOW.isoformat()
    assert saved["herdr_pane"]["pane_id"] == "w9:p1"


def test_launch_checks_the_task_file_before_starting_work(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text("{not json", encoding="utf-8")
    calls: list[list[str]] = []
    with pytest.raises(ValueError):
        launch(
            path=path,
            identity="gb:a",
            title="a",
            cwd="/w",
            command=["bash", "x"],
            runner=_runner_for("w9:p1", calls),
            now=NOW,
        )
    assert calls == []


def test_launch_names_the_running_pane_when_saving_fails(tmp_path, monkeypatch):
    from omega_prime.dashboard import herdr_launch

    path = tmp_path / "tasks.json"
    _empty(path)
    calls: list[list[str]] = []

    def broken_record(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(herdr_launch, "record_pane", broken_record)
    with pytest.raises(herdr_launch.PaneNotRecordedError) as caught:
        launch(
            path=path,
            identity="gb:a",
            title="a",
            cwd="/w",
            command=["bash", "x"],
            runner=_runner_for("w9:p1", calls),
            now=NOW,
        )
    assert caught.value.pane_id == "w9:p1"
    assert "--attach w9:p1" in str(caught.value)
    assert [c[1:3] for c in calls] == [["workspace", "create"], ["pane", "run"]]

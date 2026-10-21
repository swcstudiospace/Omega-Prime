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

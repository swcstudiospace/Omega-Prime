from __future__ import annotations

from datetime import UTC, datetime

import pytest

from omega_prime.dashboard.collectors.tasks_json import make_tasks_json_collector
from omega_prime.dashboard.model import normalize_tasks
from omega_prime.dashboard.status_file import read_tasks, write_tasks
from omega_prime.dashboard.terminal import resolve_task_pane, sanitize_terminal_text


def test_task_mapping_roundtrips_validation_collector_and_snapshot(tmp_path):
    path = tmp_path / "tasks.json"
    write_tasks({"version": 1, "updated_at": "2026-10-21T00:00:00Z", "tasks": [{"id": "work", "title": "work", "owner": {"kind": "worker", "ref": "x"}, "state": "working", "herdr_pane": {"pane_id": "w2:p4", "workspace": "clean"}}], "waiting_on_ove": [], "finished": [{"id": "done", "title": "done", "finished_at": "2026-10-21T00:00:00Z", "result": "ok", "herdr_pane": {"pane_id": "w3:p5"}}]}, path=path)
    loaded = read_tasks(path).data
    assert loaded["tasks"][0]["herdr_pane"]["pane_id"] == "w2:p4"
    assert loaded["finished"][0]["herdr_pane"]["pane_id"] == "w3:p5"
    row = make_tasks_json_collector(tasks_path=path, now_fn=lambda: datetime.now(UTC))()
    snapshot = normalize_tasks({"tasks_json": row}, now=datetime.now(UTC)).to_dict()
    assert snapshot["active"][0]["herdr_pane"]["pane_id"] == "w2:p4"
    assert snapshot["finished"][0]["herdr_pane"]["pane_id"] == "w3:p5"


def test_invalid_pane_mapping_is_rejected(tmp_path):
    path = tmp_path / "tasks.json"
    with pytest.raises(ValueError):
        write_tasks({"version": 1, "updated_at": "now", "tasks": [{"id": "x", "title": "x", "owner": {"kind": "worker", "ref": "y"}, "state": "working", "herdr_pane": {"pane_id": "w2:p4\\n--evil"}}], "waiting_on_ove": [], "finished": []}, path=path)


def test_pane_owner_fallback_is_explicit_and_validated():
    assert resolve_task_pane({"owner": {"kind": "herdr-pane", "ref": "w1:p3"}}) == "w1:p3"
    assert resolve_task_pane({"owner": {"kind": "worker", "ref": "w1:p3"}}) is None


def test_sanitizer_redacts_controls_lines_bytes_and_tokens():
    text = "\x1b[31mBearer abcdefghijkl\x1b[0m\x85\n\t" + "\n".join(f"line{i}" for i in range(205))
    safe = sanitize_terminal_text(text)
    assert "\x1b" not in safe and "\x85" not in safe and "abcdefghijkl" not in safe and "line0\n" not in safe
    assert len(safe.splitlines()) <= 200
    assert len(safe.encode()) <= 65536

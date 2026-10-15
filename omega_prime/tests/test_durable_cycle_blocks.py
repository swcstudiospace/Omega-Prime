# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""durable_status cycle and sync_backlog blocks are read-only."""

from __future__ import annotations

import json
from pathlib import Path

from omega_prime.tools.durable_surface import (
    ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR,
    register_durable_surface_tools,
)
from omega_prime.tools.registry import ToolRegistry

_PRIOR_KEYS = (
    "journal",
    "path",
    "open_runs",
    "entry_count",
    "last_run_id",
    "workflow_checkpoints",
    "roe",
    "engagement",
    "releases",
    "sidecar",
)


def _status(tmp_path: Path, monkeypatch, reports: Path | None) -> dict:
    if reports is None:
        monkeypatch.delenv(ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(ENGAGEMENT_SIDECAR_REPORTS_ENV_VAR, str(reports))
    registry = ToolRegistry()
    register_durable_surface_tools(registry, tmp_path)
    return json.loads(registry.dispatch("durable_status", {}))


def test_missing_reports_dir_is_empty_cycle_and_backlog(
    tmp_path: Path, monkeypatch
) -> None:
    body = _status(tmp_path, monkeypatch, tmp_path / "absent-reports")
    assert body["journal"] == "absent"
    for key in _PRIOR_KEYS:
        assert key in body
    assert body["cycle"] == {
        "id": None,
        "state": None,
        "age_seconds": None,
        "pending_tag": None,
    }
    assert body["sync_backlog"] == {
        "trackers": {"default": {"depth": 0, "oldest_age_seconds": None}}
    }


def test_checkpoint_and_open_finding_are_read_only(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    workflows = reports / "purple-cycle" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "cycle-7.json").write_text(
        json.dumps(
            {
                "done": 1,
                "state": {"cycle_id": "cycle-7", "status": "running", "steps": []},
            }
        ),
        encoding="utf-8",
    )
    (reports / "purple-cycle-pending.json").write_text(
        json.dumps({"tag": "await-review"}), encoding="utf-8"
    )
    (reports / "findings.ndjson").write_text(
        "\n".join(
            [
                json.dumps(
                    {"timestamp": 1_700_000_000, "issue_url": "https://example/1"}
                ),
                json.dumps({"timestamp": 1_700_000_100, "title": "open"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    before = sorted(path.relative_to(reports).as_posix() for path in reports.rglob("*"))
    body = _status(tmp_path, monkeypatch, reports)
    again = _status(tmp_path, monkeypatch, reports)
    after = sorted(path.relative_to(reports).as_posix() for path in reports.rglob("*"))
    assert before == after
    assert body["cycle"]["id"] == "cycle-7"
    assert body["cycle"]["state"] == "running"
    assert body["cycle"]["pending_tag"] == "await-review"
    assert isinstance(body["cycle"]["age_seconds"], int)
    assert body["sync_backlog"]["trackers"]["default"]["depth"] == 1
    age = body["sync_backlog"]["trackers"]["default"]["oldest_age_seconds"]
    assert isinstance(age, int) and age > 1_000
    assert again["sync_backlog"]["trackers"]["default"]["depth"] == 1
    assert again["cycle"]["id"] == "cycle-7"


def test_unfinished_checkpoint_without_status_is_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    reports = tmp_path / "reports"
    workflows = reports / "purple-cycle" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "purple-cycle-job-1000-run.json").write_text(
        json.dumps(
            {
                "done": 1,
                "state": {
                    "cycle_id": "purple-cycle-job-1000-run",
                    "mode": "run",
                    "selected": ["preflight"],
                    "steps": [{"name": "preflight", "status": "ok"}],
                    "blocked": None,
                },
            }
        ),
        encoding="utf-8",
    )
    body = _status(tmp_path, monkeypatch, reports)
    assert body["cycle"]["id"] == "purple-cycle-job-1000-run"
    assert body["cycle"]["state"] == "incomplete"


def test_run_cycle_checkpoint_reports_completed(tmp_path: Path, monkeypatch) -> None:
    from omega_prime.grokbot.purple_cycle import run_cycle

    reports = tmp_path / "reports"
    monkeypatch.setattr(
        "omega_prime.grokbot.pin_bump.audit_scheduled_job",
        lambda *_args, **_kwargs: {"verdict": "unchanged"},
    )
    result = run_cycle(
        mode="run",
        root=tmp_path / "root",
        reports_dir=reports,
        config={"redteam": {"findings_sync": {"enabled": False}}},
        env={
            "OMEGA_PRIME_REDTEAM_ENABLED": "1",
            "OMEGA_PRIME_REDTEAM_ROE_REQUIRED": "0",
        },
        fetch=lambda: [{"name": "v1.0.0", "commit": {"sha": "sha-v1.0.0"}}],
    )
    assert result["state"] == "completed"
    checkpoint = json.loads(Path(result["checkpoint"]).read_text(encoding="utf-8"))
    assert "status" not in checkpoint
    assert "outcome" not in checkpoint.get("state", {})
    body = _status(tmp_path, monkeypatch, reports)
    assert body["cycle"]["id"] == result["cycle_id"]
    assert body["cycle"]["state"] == "completed"


def test_record_finding_ts_sets_backlog_age(tmp_path: Path, monkeypatch) -> None:
    from sidecar.findings import record_finding

    reports = tmp_path / "reports"
    record_finding(
        "case-1",
        "prompt-injection",
        "low",
        "none",
        "detected",
        "observed text",
        directory=reports,
    )
    body = _status(tmp_path, monkeypatch, reports)
    tracker = body["sync_backlog"]["trackers"]["default"]
    assert tracker["depth"] == 1
    assert isinstance(tracker["oldest_age_seconds"], int)
    assert 0 <= tracker["oldest_age_seconds"] < 120

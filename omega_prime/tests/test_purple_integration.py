# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Cross-feature integration of the batch-18 purple-team surfaces.

Real RoE grant files, real findings store, real ``findings_sync.tick``, the
real ``/omega-redteam-sync-plan`` command and the real purple-cycle CLI.
Nothing here monkeypatches a unit under test; the only seams are the
tracker runner fake and the config the command loads.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from omega_prime.commands import execute_command
from omega_prime.grokbot import findings_sync, purple_cycle
from sidecar.findings import record_finding

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 14, 12, 30, 45, tzinfo=UTC)
REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
STATE_ENV_VAR = "OMEGA_PRIME_STATE_DIR"
REPO = "OWNER/NAME"


class FakeGh:
    """Counting GhRunner fake that returns a created-issue payload."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], data: dict[str, Any]) -> Any:
        self.calls.append(list(argv))
        payload = {"html_url": f"https://github.com/{REPO}/issues/{len(self.calls)}"}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


def _forbidden(argv: Any, data: Any) -> Any:
    raise AssertionError("the tracker runner must not be reached")


def _grant(path: Path, name: str, operator: str) -> str:
    """A grant active for decades around any realistic clock."""
    document = {
        "schema_version": 1,
        "artifact_id": name,
        "operator": operator,
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": "2020-01-01T00:00:00+00:00",
        "effective_until": "2099-01-01T00:00:00+00:00",
        "case_classes": ["prompt-injection"],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _config(grants: list[str], **node: Any) -> dict[str, Any]:
    return {
        "redteam": {
            "roe_artifacts": grants,
            "findings_sync": {
                "enabled": True,
                "tracker": "github",
                "repo": REPO,
                "labels": {},
                **node,
            },
        }
    }


def _seed_findings(reports: Path, count: int) -> list[str]:
    reports.mkdir(parents=True, exist_ok=True)
    return [
        record_finding(
            directory=reports,
            case_id=f"abuse-{index:02d}",
            klass="abuse",
            severity="low",
            observed_detection="no-detection",
            verdict="miss",
            response=f"response {index}",
        )["dedupe_hash"]
        for index in range(count)
    ]


def _snapshot(*bases: Path) -> dict[str, bytes | None]:
    found: dict[str, bytes | None] = {}
    for base in bases:
        if base.exists():
            for path in sorted(base.rglob("*")):
                found[str(path)] = path.read_bytes() if path.is_file() else None
    return found


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    reports = tmp_path / "reports"
    state = tmp_path / "state"
    monkeypatch.setenv(REPORTS_ENV_VAR, str(reports))
    monkeypatch.setenv(STATE_ENV_VAR, str(state))
    conflicting = [
        _grant(tmp_path / "grant-a.json", "grant-a", "alice"),
        _grant(tmp_path / "grant-b.json", "grant-b", "bob"),
    ]
    single = [_grant(tmp_path / "grant-solo.json", "grant-solo", "alice")]
    return SimpleNamespace(
        tmp=tmp_path,
        reports=reports,
        state=state,
        env={REPORTS_ENV_VAR: str(reports), STATE_ENV_VAR: str(state)},
        conflicting=conflicting,
        single=single,
    )


def _plan_tick(config: dict[str, Any], reports: Path) -> dict[str, Any]:
    return findings_sync.tick(
        config,
        reports_directory=reports,
        runner=_forbidden,
        dispatch_dry_run=True,
        now=NOW,
    )


def test_tick_shows_conflicts_and_budget_hold_in_one_result(world) -> None:
    hashes = _seed_findings(world.reports, 3)
    result = _plan_tick(_config(world.conflicting, sync_budget=1), world.reports)
    assert [row["dedupe_hash"] for row in result["pushed"]] == hashes[:1]
    assert result["sync_budget"] == 1
    assert result["budget_held"] == [
        {"dedupe_hash": digest, "reason": "sync-budget"} for digest in hashes[1:]
    ]
    assert len(result["grant_conflicts"]) == 1
    conflict = result["grant_conflicts"][0]
    assert conflict["kind"] == "operator-mismatch"
    assert [Path(grant).name for grant in conflict["grants"]] == [
        "grant-a.json",
        "grant-b.json",
    ]


def test_single_grant_and_no_budget_add_neither_key(world) -> None:
    _seed_findings(world.reports, 3)
    result = _plan_tick(_config(world.single), world.reports)
    assert "grant_conflicts" not in result
    assert "budget_held" not in result
    assert "sync_budget" not in result
    only_budget = _plan_tick(_config(world.single, sync_budget=2), world.reports)
    assert "grant_conflicts" not in only_budget
    assert len(only_budget["budget_held"]) == 1
    only_conflicts = _plan_tick(_config(world.conflicting), world.reports)
    assert len(only_conflicts["grant_conflicts"]) == 1
    assert "budget_held" not in only_conflicts


def test_sync_plan_matches_the_real_tick_and_writes_nothing(world, monkeypatch) -> None:
    hashes = _seed_findings(world.reports, 4)
    ledger = world.tmp / "ledgers" / "dispatch.ndjson"
    grants_ledger = world.tmp / "ledgers" / "grants.ndjson"
    config = _config(
        world.conflicting,
        sync_budget=2,
        dispatch_ledger=str(ledger),
        grant_usage_ledger=str(grants_ledger),
    )
    monkeypatch.setattr("omega_prime.config.load_config", lambda *a, **k: config)

    before = _snapshot(world.tmp)
    body = execute_command("/omega-redteam-sync-plan", object(), world.env)
    assert _snapshot(world.tmp) == before
    assert not ledger.parent.exists()
    assert body["ok"] is True, body
    plan = body["result"]

    replica = world.tmp / "replica"
    shutil.copytree(world.reports, replica)
    runner = FakeGh()
    real = findings_sync.tick(config, reports_directory=replica, runner=runner, now=NOW)
    dispatched = [row["dedupe_hash"] for row in real["pushed"]]
    assert dispatched == hashes[:2]
    assert plan["would_sync"] == dispatched
    assert plan["budget_held"] == real["budget_held"]
    assert plan["grant_conflicts"] == real["grant_conflicts"]
    assert len(runner.calls) == 2
    assert plan["tracker"] == "github"
    assert plan["disabled"] is False


def test_cycle_findings_plan_agrees_with_the_sync_plan_command(
    world, monkeypatch
) -> None:
    _seed_findings(world.reports, 3)
    config = _config(world.conflicting, sync_budget=1)
    monkeypatch.setattr("omega_prime.config.load_config", lambda *a, **k: config)
    plan = execute_command("/omega-redteam-sync-plan", object(), world.env)["result"]
    cycle = purple_cycle.run_cycle(
        mode="plan",
        config=config,
        env=world.env,
        reports_dir=world.reports,
        now=NOW,
        steps=["findings-plan"],
    )
    (step,) = [s for s in cycle["steps"] if s["name"] == "findings-plan"]
    assert step["status"] == "ok"
    assert step["detail"]["would_sync"] == len(plan["would_sync"]) == 1
    assert step["detail"]["budget_held"] == len(plan["budget_held"]) == 2
    assert step["detail"]["grant_conflicts"] is True
    assert plan["grant_conflicts"]


def test_purple_cycle_cli_plan_mode_exits_zero_and_writes_nothing(world) -> None:
    _seed_findings(world.reports, 2)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OMEGA_PRIME_")
    }
    env.update(world.env)
    before = _snapshot(world.tmp)
    done = subprocess.run(
        [sys.executable, "-m", "omega_prime.grokbot.purple_cycle", "--json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["mode"] == "plan"
    assert payload["state"] == "completed"
    assert payload["checkpoint"] is None and payload["ledger"] is None
    assert [step["name"] for step in payload["steps"]] == list(purple_cycle.STEP_NAMES)
    assert _snapshot(world.tmp) == before
    assert not world.state.exists()

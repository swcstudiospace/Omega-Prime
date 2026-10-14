# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Chat-native reporting surface: report, coverage, sync-plan and cycle commands.

Hermetic: the reports dir (and the state dir) always point at ``tmp_path``;
the real engagement-report script and the real findings-sync tick run, with
no subprocess, socket or tracker call.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime import commands
from omega_prime.commands import (
    COMMANDS,
    command_catalog,
    execute_command,
    redteam_common,
    redteam_coverage,
    redteam_cycle,
    redteam_report,
    redteam_sync_plan,
)
from omega_prime.grokbot import findings_sync, purple_cycle
from sidecar.findings import record_finding
from sidecar.simulation import load_suite
from sidecar.state import transition

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
STATE_ENV_VAR = "OMEGA_PRIME_STATE_DIR"
SECRET = "sk-live-NEVER-ECHO-0123456789"
MODULES = (
    redteam_report,
    redteam_coverage,
    redteam_sync_plan,
    redteam_cycle,
)
NEW_NAMES = [module.NAME for module in MODULES]


def _env(monkeypatch: Any, tmp_path: Path, **extra: str) -> dict[str, str]:
    """Env mapping and process env agree on an isolated reports/state dir."""
    reports = tmp_path / "reports"
    state = tmp_path / "state"
    monkeypatch.setenv(REPORTS_ENV_VAR, str(reports))
    monkeypatch.setenv(STATE_ENV_VAR, str(state))
    env = {
        REPORTS_ENV_VAR: str(reports),
        STATE_ENV_VAR: str(state),
        "OMEGA_PRIME_REDTEAM_ROE_KEY": SECRET,
    }
    env.update(extra)
    return env


def _reports(env: dict[str, str]) -> Path:
    return Path(env[REPORTS_ENV_VAR])


def _seed_history(reports: Path, *, miss: int = 0) -> None:
    """Sidecar history: one suite run, a transition pair and one finding.

    ``miss`` cases get an observed detection that differs from the expected
    one, which turns them into coverage gaps.
    """
    reports.mkdir(parents=True, exist_ok=True)
    cases, errors = load_suite()
    assert not errors
    observations = {}
    for index, case in enumerate(cases):
        expected = case.get("expected")
        detection = expected.get("detection") if isinstance(expected, dict) else ""
        if index < miss:
            detection = f"{detection}-missed"
        observations[str(case["case_id"])] = {
            "observed_detection": str(detection),
            "response": "loopback-seam",
        }
    (reports / "last-run.json").write_text(
        json.dumps({"suite": "smoke", "observations": observations}) + "\n",
        encoding="utf-8",
    )
    transition("IDLE", "RUNNING", "run-started", directory=reports)
    transition("RUNNING", "IDLE", "run-completed", directory=reports)
    record_finding(
        directory=reports,
        case_id="abuse-01",
        klass="abuse",
        severity="medium",
        observed_detection="no-detection",
        verdict="pass",
        response="loopback-seam:abuse-01:none",
    )


def _snapshot(*bases: Path) -> dict[str, bytes | None]:
    found: dict[str, bytes | None] = {}
    for base in bases:
        if base.exists():
            for path in sorted(base.rglob("*")):
                found[str(path)] = path.read_bytes() if path.is_file() else None
    return found


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a subprocess was spawned")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, boom)
    monkeypatch.setattr(os, "system", boom)


def _call(env: dict[str, str], text: str) -> dict[str, Any]:
    body = execute_command(text, object(), env)
    assert isinstance(body, dict)
    assert body["command"] == text.split()[0].lstrip("/")
    assert body["ok"] is True or body["ok"] is False
    assert set(body) == (
        {"ok", "command", "result"}
        if body["ok"]
        else {"ok", "command", "error", "result"}
    )
    assert isinstance(body["result"], dict)
    assert SECRET not in json.dumps(body)
    return body


# --- registration ----------------------------------------------------------


def test_registration_order_usage_and_catalog() -> None:
    assert len(COMMANDS) == 24
    assert [spec.name for spec in COMMANDS[-4:]] == NEW_NAMES
    by_name = {spec.name: spec for spec in COMMANDS}
    catalog = {row["name"]: row for row in command_catalog()}
    for module in MODULES:
        spec = by_name[module.NAME]
        assert spec.usage == module.USAGE
        assert spec.summary == module.SUMMARY
        assert spec.when == module.WHEN
        assert spec.needs_args is False
        assert spec.kind == "curated"
        assert catalog[module.NAME]["usage"] == module.USAGE
        assert module.USAGE.startswith(f"/{module.NAME}")
        assert module.__all__ == ["NAME", "SUMMARY", "USAGE", "WHEN", "Error", "run"]
        assert module.Error is redteam_common.Error


def test_the_four_commands_are_in_help_after_the_older_curated_ones() -> None:
    names = [row["name"] for row in command_catalog()]
    assert names.index("omega-redteam-triage-apply") + 1 == names.index(
        "omega-redteam-report"
    )
    assert names[names.index("omega-redteam-report") :][:4] == NEW_NAMES


# --- the dispatcher bug fixed alongside ------------------------------------


def test_unknown_kind_and_failed_import_return_a_dict_not_a_raise(
    monkeypatch: Any,
) -> None:
    unknown = commands._redteam("omega-redteam-x", "", {}, "no-such-kind")
    assert unknown["ok"] is False
    assert unknown["command"] == "omega-redteam-x"
    assert unknown["error"] == "command_failed"
    monkeypatch.setitem(
        commands._REDTEAM_MODULES, "report", "omega_prime.commands.does_not_exist"
    )
    broken = commands._redteam("omega-redteam-report", "", {}, "report")
    assert broken["ok"] is False
    assert broken["error"] == "command_failed"
    assert "ModuleNotFoundError" in broken["result"]["error"]


def test_a_module_error_without_the_shared_class_is_command_failed(
    monkeypatch: Any,
) -> None:
    module = importlib.import_module("omega_prime.commands.redteam_report")

    def run(args: str, env: Any) -> Any:
        raise ValueError("odd")

    monkeypatch.setattr(module, "run", run)
    body = commands._redteam("omega-redteam-report", "", {}, "report")
    assert body["error"] == "command_failed"
    assert body["result"] == {"error": "ValueError: odd"}


# --- /omega-redteam-report -------------------------------------------------


def test_report_digest_over_real_history(monkeypatch: Any, tmp_path: Path) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    body = _call(env, "/omega-redteam-report")
    assert body["ok"] is True
    result = body["result"]
    assert set(result) == {"mode", "state", "lines", "gate"}
    assert result["mode"] == "digest"
    assert result["state"] == "ok"
    assert result["gate"] is None
    assert len(result["lines"]) == 3
    assert result["lines"][0].startswith("runs=1 pass=")
    assert result["lines"][1].startswith("coverage covered=")
    assert result["lines"][2].startswith("worst=")
    again = _call(env, "/omega-redteam-report")
    assert again["result"] == result


def test_report_summary_is_the_script_summary_line(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    digest = _call(env, "/omega-redteam-report")["result"]
    summary = _call(env, "/omega-redteam-report --summary")["result"]
    assert summary["mode"] == "summary"
    assert summary["state"] == "ok"
    assert summary["gate"] is None
    assert summary["lines"] == [digest["lines"][0]]
    module = redteam_common.load_engagement_module(redteam_report.USAGE)
    assert summary["lines"] == [module.summarize(_reports(env), None)["line"]]


def test_report_failing_gate_is_information_not_an_error(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env), miss=3)
    failing = _call(env, "/omega-redteam-report --threshold gate:100")
    assert failing["ok"] is True
    gate = failing["result"]["gate"]
    assert gate["passed"] is False
    assert gate["metric"] == "pass-rate"
    assert failing["result"]["lines"][-1] == gate["line"]
    assert gate["line"].startswith("Gate: FAIL")
    passing = _call(env, "/omega-redteam-report --threshold gate:0")
    assert passing["result"]["gate"]["passed"] is True
    risk = _call(env, "/omega-redteam-report --threshold gate-risk:100")
    assert risk["result"]["gate"]["metric"] == "risk-score"
    assert risk["result"]["gate"]["passed"] is False


def test_report_since_narrows_the_window(monkeypatch: Any, tmp_path: Path) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    future = _call(env, "/omega-redteam-report --since 2099-01-01T00:00:00Z")
    assert future["ok"] is True
    assert future["result"]["lines"][0].startswith("runs=0 ")
    past = _call(env, "/omega-redteam-report --since 2000-01-01T00:00:00+00:00")
    assert past["result"]["lines"][0].startswith("runs=1 ")


def test_report_without_history_is_no_reports_even_with_a_gate(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    missing = _call(env, "/omega-redteam-report --threshold gate:80")
    assert missing["ok"] is True
    assert missing["result"]["state"] == "no-reports"
    assert missing["result"]["gate"] is None
    assert missing["result"]["lines"][0] == "runs=0 pass=0% gaps=0 findings=+0/-0"
    _reports(env).mkdir()
    empty = _call(env, "/omega-redteam-report --summary")
    assert empty["result"]["state"] == "no-reports"
    assert not _reports(env).exists() or list(_reports(env).iterdir()) == []


def test_report_is_read_only_and_spawns_nothing(
    monkeypatch: Any, tmp_path: Path, no_subprocess: None
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env), miss=2)
    before = _snapshot(tmp_path)
    for text in (
        "/omega-redteam-report",
        "/omega-redteam-report --summary",
        "/omega-redteam-report --threshold gate:50 --since 2000-01-01T00:00:00Z",
    ):
        assert _call(env, text)["ok"] is True
    assert _snapshot(tmp_path) == before


def test_report_script_errors_return_the_exception_type_only(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    module = redteam_common.load_engagement_module(redteam_report.USAGE)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise module.ReportError(f"cannot read {tmp_path}/private {SECRET}")

    monkeypatch.setattr(module, "build_digest", broken)
    monkeypatch.setattr(module, "summarize", broken)
    for text in ("/omega-redteam-report", "/omega-redteam-report --summary"):
        body = _call(env, text)
        assert body["ok"] is False
        assert body["error"] == "report_failed"
        assert body["result"] == {
            "usage": redteam_report.USAGE,
            "reason": "ReportError",
        }
        assert "private" not in json.dumps(body)


def test_report_and_coverage_script_unavailable(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(redteam_common, "_ENGAGEMENT_MODULE", None)

    def missing(*args: Any, **kwargs: Any) -> Any:
        raise OSError(f"no such file {tmp_path}/hidden")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", missing)
    for text in ("/omega-redteam-report", "/omega-redteam-coverage"):
        body = _call(env, text)
        assert body["ok"] is False
        assert body["error"] == "report_unavailable"
        assert body["result"]["reason"] == "OSError"
        assert "hidden" not in json.dumps(body)


def test_reports_dir_falls_back_to_process_env_then_sidecar_default(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from sidecar import state

    usage = redteam_report.USAGE
    other = tmp_path / "other"
    monkeypatch.setenv(REPORTS_ENV_VAR, str(other))
    assert redteam_common.resolve_reports_dir({}, usage) == other
    mapped = tmp_path / "mapped"
    resolved = redteam_common.resolve_reports_dir({REPORTS_ENV_VAR: str(mapped)}, usage)
    assert resolved == mapped
    monkeypatch.delenv(REPORTS_ENV_VAR)
    assert redteam_common.resolve_reports_dir({}, usage) == state.reports_dir()
    monkeypatch.setitem(sys.modules, "sidecar", None)
    with pytest.raises(redteam_common.Error) as caught:
        redteam_common.resolve_reports_dir({}, usage)
    assert caught.value.error == "sidecar_unavailable"
    assert caught.value.result == {"usage": usage, "reason": "ModuleNotFoundError"}


# --- /omega-redteam-coverage -----------------------------------------------


def test_coverage_rows_match_the_script_and_jsonl_flag(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env), miss=2)
    body = _call(env, "/omega-redteam-coverage")
    result = body["result"]
    assert set(result) == {"rows", "total"}
    module = redteam_common.load_engagement_module(redteam_coverage.USAGE)
    expected = module.coverage_jsonl_rows(_reports(env), None)
    assert result["rows"] == expected
    assert result["total"] == len(expected) > 0
    assert {row["verdict"] for row in result["rows"]} >= {"covered", "gappy"}
    for row in result["rows"]:
        assert row["schema"] == "coverage/1"
    classes = [row["class"] for row in result["rows"]]
    assert classes == [row["class"] for row in expected]
    with_jsonl = _call(env, "/omega-redteam-coverage --jsonl")["result"]
    assert set(with_jsonl) == {"rows", "total", "jsonl"}
    assert with_jsonl["rows"] == result["rows"]
    assert with_jsonl["jsonl"] == module.render_jsonl(expected)
    assert [json.loads(line) for line in with_jsonl["jsonl"].splitlines()] == expected


def test_coverage_since_and_empty_history(monkeypatch: Any, tmp_path: Path) -> None:
    env = _env(monkeypatch, tmp_path)
    empty = _call(env, "/omega-redteam-coverage --jsonl")
    assert empty["result"] == {"rows": [], "total": 0, "jsonl": ""}
    _seed_history(_reports(env))
    future = _call(env, "/omega-redteam-coverage --since 2099-01-01T00:00:00Z")
    assert future["ok"] is True
    assert all(row["runs"] == 0 for row in future["result"]["rows"])


def test_coverage_is_read_only_and_spawns_nothing(
    monkeypatch: Any, tmp_path: Path, no_subprocess: None
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env), miss=1)
    before = _snapshot(tmp_path)
    for text in ("/omega-redteam-coverage", "/omega-redteam-coverage --jsonl"):
        assert _call(env, text)["ok"] is True
    assert _snapshot(tmp_path) == before


def test_coverage_script_errors_return_the_exception_type_only(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    module = redteam_common.load_engagement_module(redteam_coverage.USAGE)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise module.ReportError(f"unreadable {tmp_path}/private")

    monkeypatch.setattr(module, "coverage_jsonl_rows", broken)
    body = _call(env, "/omega-redteam-coverage")
    assert body["error"] == "report_failed"
    assert body["result"] == {"usage": redteam_coverage.USAGE, "reason": "ReportError"}


# --- /omega-redteam-sync-plan ----------------------------------------------


class SyncFixture:
    """A findings-sync config with ledgers named, plus seeded findings."""

    def __init__(self, monkeypatch: Any, tmp_path: Path, **node: Any) -> None:
        self.env = _env(monkeypatch, tmp_path)
        self.reports = _reports(self.env)
        self.dispatch_ledger = tmp_path / "ledgers" / "dispatch.ndjson"
        self.grant_ledger = tmp_path / "ledgers" / "grants.ndjson"
        self.config: dict[str, Any] = {
            "redteam": {
                "findings_sync": {
                    "enabled": True,
                    "tracker": "github",
                    "repo": "OWNER/NAME",
                    "labels": {},
                    "dispatch_ledger": str(self.dispatch_ledger),
                    "grant_usage_ledger": str(self.grant_ledger),
                    **node,
                }
            }
        }
        self.config_before = json.dumps(self.config, sort_keys=True)
        self.ticks: list[dict[str, Any]] = []
        real_tick = findings_sync.tick

        def spy(config: Any, **kwargs: Any) -> Any:
            self.ticks.append({"config": config, **kwargs})
            return real_tick(config, **kwargs)

        monkeypatch.setattr(findings_sync, "tick", spy)
        monkeypatch.setattr(
            "omega_prime.config.load_config", lambda *a, **k: self.config
        )

    def seed(self, count: int, severity: str = "low") -> list[str]:
        self.reports.mkdir(parents=True, exist_ok=True)
        return [
            record_finding(
                directory=self.reports,
                case_id=f"abuse-{index:02d}",
                klass="abuse",
                severity=severity,
                observed_detection="no-detection",
                verdict="miss",
                response=f"response {index}",
            )["dedupe_hash"]
            for index in range(count)
        ]


def test_sync_plan_lists_what_would_sync_and_writes_nothing(
    monkeypatch: Any, tmp_path: Path, no_subprocess: None
) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path)
    hashes = fixture.seed(3)
    before = _snapshot(tmp_path)
    body = _call(fixture.env, "/omega-redteam-sync-plan")
    assert body["ok"] is True
    assert body["result"] == {
        "tracker": "github",
        "disabled": False,
        "would_sync": hashes,
        "skipped": 0,
        "quiet_held": [],
        "budget_held": [],
        "grant_conflicts": [],
    }
    assert _snapshot(tmp_path) == before
    assert not fixture.dispatch_ledger.exists()
    assert not fixture.grant_ledger.exists()
    assert not fixture.dispatch_ledger.parent.exists()
    assert json.dumps(fixture.config, sort_keys=True) == fixture.config_before


def test_sync_plan_forces_ledgers_off_and_never_reaches_the_runner(
    monkeypatch: Any, tmp_path: Path
) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path)
    fixture.seed(2)
    assert _call(fixture.env, "/omega-redteam-sync-plan")["ok"] is True
    (tick,) = fixture.ticks
    node = tick["config"]["redteam"]["findings_sync"]
    assert node["dispatch_ledger"] == ""
    assert node["grant_usage_ledger"] == ""
    assert tick["config"] is not fixture.config
    assert tick["dispatch_dry_run"] is True
    assert Path(tick["reports_directory"]) == fixture.reports
    with pytest.raises(AssertionError):
        tick["runner"](["gh"], {})


def test_sync_plan_reports_budget_held_rows(monkeypatch: Any, tmp_path: Path) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path, sync_budget=1)
    hashes = fixture.seed(3)
    result = _call(fixture.env, "/omega-redteam-sync-plan")["result"]
    assert result["would_sync"] == hashes[:1]
    assert result["budget_held"] == [
        {"dedupe_hash": digest, "reason": "sync-budget"} for digest in hashes[1:]
    ]


def test_sync_plan_reports_quiet_held_rows(monkeypatch: Any, tmp_path: Path) -> None:
    fixture = SyncFixture(
        monkeypatch, tmp_path, quiet_hours={"start": "00:00", "end": "23:59"}
    )
    hashes = fixture.seed(2)
    result = _call(fixture.env, "/omega-redteam-sync-plan")["result"]
    assert result["would_sync"] == []
    assert [row["dedupe_hash"] for row in result["quiet_held"]] == hashes


def test_sync_plan_counts_already_synced_findings(
    monkeypatch: Any, tmp_path: Path
) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path)
    hashes = fixture.seed(2)
    store = fixture.reports / "findings.ndjson"
    rows = [json.loads(line) for line in store.read_text("utf-8").splitlines()]
    rows[0]["issue_url"] = "https://github.com/OWNER/NAME/issues/1"
    rows[0]["synced_at"] = "2026-01-01T00:00:00+00:00"
    store.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    result = _call(fixture.env, "/omega-redteam-sync-plan")["result"]
    assert result["would_sync"] == hashes[1:]
    assert result["skipped"] == 1


def test_sync_plan_disabled_config_plans_nothing(
    monkeypatch: Any, tmp_path: Path
) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path, enabled=False)
    fixture.seed(1)
    result = _call(fixture.env, "/omega-redteam-sync-plan")["result"]
    assert result["disabled"] is True
    assert result["would_sync"] == []


def test_sync_plan_tick_error_is_plan_failed(monkeypatch: Any, tmp_path: Path) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path, repo="")
    fixture.seed(1)
    body = _call(fixture.env, "/omega-redteam-sync-plan")
    assert body["ok"] is False
    assert body["error"] == "plan_failed"
    assert body["result"]["usage"] == redteam_sync_plan.USAGE
    assert body["result"]["reason"] == "tick-error"


def test_sync_plan_config_or_tick_exceptions_return_the_type_only(
    monkeypatch: Any, tmp_path: Path
) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"secret detail {SECRET}")

    monkeypatch.setattr("omega_prime.config.load_config", broken)
    body = _call(fixture.env, "/omega-redteam-sync-plan")
    assert body["ok"] is False
    assert body["error"] == "plan_failed"
    assert body["result"] == {
        "usage": redteam_sync_plan.USAGE,
        "reason": "RuntimeError",
    }


def test_sync_plan_takes_no_arguments(monkeypatch: Any, tmp_path: Path) -> None:
    fixture = SyncFixture(monkeypatch, tmp_path)
    for text in (
        "/omega-redteam-sync-plan now",
        "/omega-redteam-sync-plan --dispatch",
        "/omega-redteam-sync-plan --reports-dir /etc",
    ):
        body = _call(fixture.env, text)
        assert body["ok"] is False
        assert body["error"] == "bad_args"
        assert body["result"] == {"usage": redteam_sync_plan.USAGE}
    assert fixture.ticks == []


# --- /omega-redteam-cycle --------------------------------------------------


def test_cycle_plan_is_pure_and_deterministic(
    monkeypatch: Any, tmp_path: Path, no_subprocess: None
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    before = _snapshot(tmp_path)
    body = _call(env, "/omega-redteam-cycle")
    assert body["ok"] is True
    result = body["result"]
    assert result["mode"] == "plan"
    assert result["state"] == "completed"
    assert (result["ok"], result["exit"]) == (True, 0)
    assert result["checkpoint"] is None and result["ledger"] is None
    assert [step["name"] for step in result["steps"]] == list(purple_cycle.STEP_NAMES)
    by_name = {step["name"]: step for step in result["steps"]}
    assert by_name["release-watch"]["reason"] == "plan-mode"
    assert by_name["findings-dispatch"]["reason"] == "plan-mode"
    assert by_name["engagement"]["status"] == "ok"
    assert len(by_name["engagement"]["detail"]["digest"]) == 3
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "state").exists()
    again = _call(env, "/omega-redteam-cycle")
    assert again["result"]["steps"] == result["steps"]


def test_cycle_steps_flag_selects_a_subset(monkeypatch: Any, tmp_path: Path) -> None:
    env = _env(monkeypatch, tmp_path)
    result = _call(env, "/omega-redteam-cycle --steps preflight,engagement")["result"]
    reasons = {step["name"]: step["reason"] for step in result["steps"]}
    assert reasons["findings-plan"] == "not-selected"
    assert reasons["engagement"] == "ok"
    assert reasons["preflight"] != "not-selected"


@pytest.mark.parametrize(
    "steps", ["nope", "preflight,nope", "preflight,,engagement", ",", "findings-apply"]
)
def test_cycle_unknown_or_empty_steps_are_bad_args(
    monkeypatch: Any, tmp_path: Path, steps: str
) -> None:
    env = _env(monkeypatch, tmp_path)
    body = _call(env, f"/omega-redteam-cycle --steps '{steps}'")
    assert body["ok"] is False
    assert body["error"] == "bad_args"
    assert body["result"] == {"usage": redteam_cycle.USAGE}


def test_cycle_run_flag_runs_the_real_cycle_with_fake_upstream(
    monkeypatch: Any, tmp_path: Path, no_subprocess: None
) -> None:
    env = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(purple_cycle, "_default_fetch", lambda: [])
    body = _call(env, "/omega-redteam-cycle --run")
    assert body["ok"] is True, body
    result = body["result"]
    assert result["mode"] == "run"
    assert result["state"] == "completed"
    assert result["resumed"] is False
    by_name = {step["name"]: step for step in result["steps"]}
    assert by_name["release-watch"]["reason"] == "seeded"
    assert by_name["findings-dispatch"]["reason"] == "not-selected"
    assert Path(result["checkpoint"]).parent == tmp_path / "state" / "workflows"
    assert (_reports(env) / "release-watch.json").is_file()
    again = _call(env, "/omega-redteam-cycle --run")["result"]
    assert again["resumed"] is True
    assert again["steps"] == result["steps"]


def test_cycle_run_flag_selects_run_mode_and_passes_only_safe_arguments(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    seen: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"ok": True, "state": "completed", "exit": 0, "mode": kwargs["mode"]}

    monkeypatch.setattr(purple_cycle, "run_cycle", fake)
    _call(env, "/omega-redteam-cycle")
    _call(env, "/omega-redteam-cycle --run --steps engagement,preflight")
    assert [call["mode"] for call in seen] == ["plan", "run"]
    assert seen[0]["steps"] is None
    assert seen[1]["steps"] == ["engagement", "preflight"]
    for call in seen:
        assert set(call) == {"mode", "steps", "env", "reports_dir"}
        assert call["reports_dir"] == _reports(env)
        assert call["env"][REPORTS_ENV_VAR] == env[REPORTS_ENV_VAR]


def test_cycle_state_dir_falls_back_to_the_process_env(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    del env[STATE_ENV_VAR]
    merged = redteam_cycle._cycle_env(env)
    assert merged[STATE_ENV_VAR] == str(tmp_path / "state")
    own = redteam_cycle._cycle_env({**env, STATE_ENV_VAR: "/somewhere"})
    assert own[STATE_ENV_VAR] == "/somewhere"


def test_cycle_step_failure_is_an_error_envelope(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)

    def down() -> Any:
        raise RuntimeError(f"upstream down {SECRET}")

    monkeypatch.setattr(purple_cycle, "_default_fetch", down)
    body = _call(env, "/omega-redteam-cycle --run")
    assert body["ok"] is False
    assert body["error"] == "cycle_failed"
    assert body["result"]["state"] == "failed"
    assert body["result"]["exit"] == 1
    failed = [s for s in body["result"]["steps"] if s["status"] == "error"]
    assert [s["name"] for s in failed] == ["release-watch"]
    assert failed[0]["detail"] == {"error_type": "RuntimeError"}
    assert "upstream down" not in json.dumps(body)


def test_cycle_library_value_error_and_blocked_are_structured(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)

    def invalid(**kwargs: Any) -> Any:
        raise ValueError(f"unknown step {SECRET}")

    monkeypatch.setattr(purple_cycle, "run_cycle", invalid)
    body = _call(env, "/omega-redteam-cycle")
    assert body["error"] == "bad_args"
    assert body["result"] == {"usage": redteam_cycle.USAGE}

    def blocked(**kwargs: Any) -> dict[str, Any]:
        return {"state": "blocked", "exit": 3}

    monkeypatch.setattr(purple_cycle, "run_cycle", blocked)
    refused = _call(env, "/omega-redteam-cycle")
    assert refused["ok"] is False
    assert refused["error"] == "cycle_blocked"


@pytest.mark.parametrize(
    "text",
    [
        "--apply",
        "--mode apply",
        "--mode run",
        "--force-new",
        "--root /tmp",
        "--reports-dir /etc",
        "--now 2026-01-01T00:00:00Z",
        "--json",
        "apply",
        "--run --apply",
        "--run=1",
        "--steps",
        "--steps --run",
    ],
)
def test_cycle_can_never_reach_apply_or_take_paths(
    monkeypatch: Any, tmp_path: Path, text: str
) -> None:
    env = _env(monkeypatch, tmp_path)

    def forbidden(**kwargs: Any) -> Any:
        raise AssertionError("the library must not be reached")

    monkeypatch.setattr(purple_cycle, "run_cycle", forbidden)
    body = _call(env, f"/omega-redteam-cycle {text}")
    assert body["ok"] is False
    assert body["error"] == "bad_args"
    assert body["result"] == {"usage": redteam_cycle.USAGE}


# --- argument abuse, every command -----------------------------------------

_SINCE = " --since 2026-01-01T00:00:00Z"
COMMON_ABUSE = [
    "--bogus",
    "--reports-dir /etc",
    "--out x",
    "--export-jsonl /tmp/x.jsonl",
    "positional",
    "-h",
    "--",
    "'unterminated",
    "; rm -rf /",
    "$(touch /tmp/pwned)",
    "`id`",
    "--since",
    f"--run{_SINCE}",
    "\x00",
    "--since 2026-01-01T00:00:00Z\x00",
    "a" * 600,
    "--summary --summary",
    "--jsonl --jsonl",
    "--run --run",
    "--steps a --steps b",
    f"--since 2026-01-01T00:00:00Z{_SINCE}",
    "--threshold gate:80 --threshold gate:80",
]


@pytest.mark.parametrize("module", MODULES, ids=NEW_NAMES)
@pytest.mark.parametrize("abuse", COMMON_ABUSE)
def test_hostile_arguments_are_rejected_with_the_envelope(
    monkeypatch: Any, tmp_path: Path, no_subprocess: None, module: Any, abuse: str
) -> None:
    env = _env(monkeypatch, tmp_path)
    before = _snapshot(tmp_path)
    body = _call(env, f"/{module.NAME} {abuse}")
    assert body["ok"] is False
    assert body["error"] in {"bad_args", "bad_since", "bad_threshold"}
    assert body["result"]["usage"] == module.USAGE
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "value",
    [
        "yesterday",
        "2026-13-01T00:00:00Z",
        "2026-01-01T25:00:00Z",
        "2026-01-01T00:00:00Z; rm -rf /",
        "2026-01-01T00:00:00Z && id",
        "$(date)",
        "20260101T000000Z",
        "2026-01-01 00:00:00",
        "9" * 40,
        "",
        "../../etc/passwd",
    ],
)
@pytest.mark.parametrize("module", [redteam_report, redteam_coverage])
def test_invalid_since_is_bad_since(
    monkeypatch: Any, tmp_path: Path, module: Any, value: str
) -> None:
    env = _env(monkeypatch, tmp_path)
    body = _call(env, f"/{module.NAME} --since '{value}'")
    assert body["ok"] is False
    assert body["error"] in {"bad_since", "bad_args"}
    assert body["result"] == {"usage": module.USAGE}
    if value:
        assert body["error"] == "bad_since"


@pytest.mark.parametrize(
    "value",
    [
        "gate:101",
        "gate:100.01",
        "gate:abc",
        "gate:",
        "gate:-1",
        "gate:80.123",
        "gate:1000",
        "gate-risk:",
        "gate-risk:101",
        "risk:50",
        "gate:80;id",
        "GATE:80",
        "gate:80 ",
        "gate: 80",
        "gate:nan",
        "gate:1e2",
    ],
)
def test_invalid_threshold_is_bad_threshold(
    monkeypatch: Any, tmp_path: Path, value: str
) -> None:
    env = _env(monkeypatch, tmp_path)
    body = _call(env, f"/omega-redteam-report --threshold '{value}'")
    assert body["ok"] is False
    assert body["error"] == "bad_threshold"
    assert body["result"] == {"usage": redteam_report.USAGE}


@pytest.mark.parametrize(
    "value", ["gate:0", "gate:100", "gate:99.5", "gate-risk:0", "gate-risk:75.25"]
)
def test_threshold_boundaries_are_accepted(
    monkeypatch: Any, tmp_path: Path, value: str
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    body = _call(env, f"/omega-redteam-report --threshold {value}")
    assert body["ok"] is True, body
    assert body["result"]["gate"] is not None


@pytest.mark.parametrize(
    "text",
    [
        "/omega-redteam-report --summary --threshold gate:80",
        "/omega-redteam-report --jsonl",
        "/omega-redteam-coverage --summary",
        "/omega-redteam-coverage --threshold gate:80",
        "/omega-redteam-cycle --since 2026-01-01T00:00:00Z",
        "/omega-redteam-cycle --jsonl",
    ],
)
def test_flags_belong_to_their_own_command(
    monkeypatch: Any, tmp_path: Path, text: str
) -> None:
    env = _env(monkeypatch, tmp_path)
    body = _call(env, text)
    assert body["ok"] is False
    assert body["error"] == "bad_args"


def test_quoted_shell_metacharacters_are_inert_values(
    monkeypatch: Any, tmp_path: Path, no_subprocess: None
) -> None:
    env = _env(monkeypatch, tmp_path)
    marker = tmp_path / "pwned"
    body = _call(env, f"/omega-redteam-report --since 'x; touch {marker}'")
    assert body["error"] == "bad_since"
    assert not marker.exists()


def test_env_values_are_never_echoed_on_any_path(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env = _env(monkeypatch, tmp_path)
    _seed_history(_reports(env))
    monkeypatch.setattr(purple_cycle, "_default_fetch", lambda: [])
    for text in (
        "/omega-redteam-report",
        "/omega-redteam-report --bogus",
        "/omega-redteam-coverage --jsonl",
        "/omega-redteam-sync-plan",
        "/omega-redteam-cycle",
        "/omega-redteam-cycle --run",
    ):
        _call(env, text)  # _call asserts SECRET is absent from every body

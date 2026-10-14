# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The /omega-redteam-* slash-command suite: gate, reads, and triage proposals.

Hermetic: stdlib sidecar helpers plus a fixture-signed RoE only. The reports
dir always points at ``tmp_path`` (env mapping and process env agree, so the
suite runner's internal transition log lands there too). No sockets open.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from omega_prime.commands import (
    COMMANDS,
    command_catalog,
    execute_command,
    redteam_findings,
    redteam_run,
    redteam_status,
    redteam_triage,
)
from sidecar import harness
from sidecar.findings import dedupe_hash, record_finding
from sidecar.roe import sign_artifact
from sidecar.state import transition

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"
FIXTURE_KEY_HEX = hashlib.sha256(b"omega-prime fixture - not a secret").hexdigest()


def _window(days_from_now: int = -1, days_to_now: int = 7) -> tuple[str, str]:
    now = datetime.now(UTC)
    start = (now + timedelta(days=days_from_now)).isoformat(timespec="seconds")
    end = (now + timedelta(days=days_to_now)).isoformat(timespec="seconds")
    return start, end


def _document(**overrides: Any) -> dict[str, Any]:
    start, end = _window()
    document: dict[str, Any] = {
        "schema_version": 1,
        "operator": "test-operator",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": start,
        "effective_until": end,
        "case_classes": ["prompt-injection"],
    }
    document.update(overrides)
    return document


def _write_signed(tmp_path: Path, document: dict[str, Any] | None = None) -> Path:
    artifact = tmp_path / "roe.json"
    artifact.write_text(json.dumps(document or _document()), encoding="utf-8")
    sign_artifact(artifact, FIXTURE_KEY_HEX.encode())
    return artifact


def _env(
    monkeypatch: Any,
    tmp_path: Path,
    **overrides: str,
) -> tuple[dict[str, str], Path]:
    """Gate env mapping plus an isolated reports dir both sides agree on."""
    reports = tmp_path / "reports"
    monkeypatch.setenv(REPORTS_ENV_VAR, str(reports))
    env = {
        "OMEGA_PRIME_REDTEAM_ENABLED": "1",
        "OMEGA_PRIME_REDTEAM_ROE_FILE": "",
        "OMEGA_PRIME_REDTEAM_ROE_KEY": FIXTURE_KEY_HEX,
        REPORTS_ENV_VAR: str(reports),
    }
    env.update(overrides)
    # The gate reads the caller mapping, never the process env: clear ambient
    # kill/require switches only when the test does not set them itself.
    for name in ("OMEGA_PRIME_REDTEAM_KILL", "OMEGA_PRIME_REDTEAM_ROE_REQUIRED"):
        if name not in overrides:
            monkeypatch.delenv(name, raising=False)
    return env, reports


def _idle_env(
    monkeypatch: Any, tmp_path: Path, **overrides: str
) -> tuple[dict[str, str], Path]:
    artifact = _write_signed(tmp_path)
    return _env(
        monkeypatch,
        tmp_path,
        OMEGA_PRIME_REDTEAM_ROE_FILE=str(artifact),
        **overrides,
    )


def _assert_no_writes(reports: Path) -> None:
    assert not reports.exists() or list(reports.iterdir()) == []


def _seed_finding(reports: Path, case_id: str, detection: str) -> dict[str, Any]:
    return record_finding(
        directory=reports,
        case_id=case_id,
        klass="abuse",
        severity="medium",
        observed_detection=detection,
        verdict="pass",
        response=f"loopback-seam:{case_id}:{detection}",
    )


def test_help_lists_the_four_new_commands_with_exact_usage() -> None:
    assert len(COMMANDS) == 24
    by_name = {spec.name: spec for spec in COMMANDS}
    assert by_name["omega-redteam-run"].usage == redteam_run.USAGE
    assert by_name["omega-redteam-status"].usage == redteam_status.USAGE
    assert by_name["omega-redteam-findings"].usage == redteam_findings.USAGE
    assert by_name["omega-redteam-triage"].usage == redteam_triage.USAGE
    assert by_name["omega-redteam-run"].needs_args is True
    assert by_name["omega-redteam-status"].needs_args is False
    assert by_name["omega-redteam-findings"].needs_args is False
    assert by_name["omega-redteam-triage"].needs_args is True
    catalog = {row["name"]: row for row in command_catalog()}
    for module in (redteam_run, redteam_status, redteam_findings, redteam_triage):
        assert catalog[module.NAME]["usage"] == module.USAGE


def test_run_blocked_when_roe_missing(monkeypatch: Any, tmp_path: Path) -> None:
    env, reports = _env(monkeypatch, tmp_path)
    body = execute_command("/omega-redteam-run --suite smoke", object(), env)
    assert body["ok"] is False
    assert body["command"] == "omega-redteam-run"
    assert body["error"] == "harness_blocked"
    line = body["result"]["line"]
    assert line.startswith(
        f"sidecar {harness.harness_version()}: BLOCKED (roe-missing): "
    )
    _assert_no_writes(reports)


def test_run_blocked_by_kill_switch_despite_valid_roe(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, reports = _idle_env(monkeypatch, tmp_path, OMEGA_PRIME_REDTEAM_KILL="1")
    body = execute_command("/omega-redteam-run --suite smoke", object(), env)
    assert body["ok"] is False
    assert body["result"]["cause"] == "kill-switch-set"
    assert "kill-switch-set" in body["result"]["line"]
    _assert_no_writes(reports)


def test_run_blocked_when_roe_expired(monkeypatch: Any, tmp_path: Path) -> None:
    start, end = _window(days_from_now=-9, days_to_now=-1)
    artifact = _write_signed(
        tmp_path, _document(effective_from=start, effective_until=end)
    )
    env, reports = _env(
        monkeypatch, tmp_path, OMEGA_PRIME_REDTEAM_ROE_FILE=str(artifact)
    )
    body = execute_command("/omega-redteam-run --suite smoke", object(), env)
    assert body["ok"] is False
    assert body["result"]["cause"] == "roe-expired"
    assert "(roe-expired)" in body["result"]["line"]
    _assert_no_writes(reports)


def test_run_blocked_when_roe_tampered(monkeypatch: Any, tmp_path: Path) -> None:
    artifact = _write_signed(tmp_path)
    document = json.loads(artifact.read_text(encoding="utf-8"))
    document["operator"] = "mallory"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    env, reports = _env(
        monkeypatch, tmp_path, OMEGA_PRIME_REDTEAM_ROE_FILE=str(artifact)
    )
    body = execute_command("/omega-redteam-run --suite smoke", object(), env)
    assert body["ok"] is False
    assert body["result"]["cause"] == "roe-invalid"
    _assert_no_writes(reports)


def test_run_idle_executes_the_smoke_suite(monkeypatch: Any, tmp_path: Path) -> None:
    env, reports = _idle_env(monkeypatch, tmp_path)
    body = execute_command("/omega-redteam-run --suite smoke", object(), env)
    assert body["ok"] is True, body
    result = body["result"]
    assert result["suite"] == "smoke"
    assert result["cases"] > 0
    assert sum(result["counts"].values()) == result["cases"]
    assert (reports / "last-run.json").is_file()
    transitions = (reports / "transitions.ndjson").read_text(encoding="utf-8")
    assert "run-started" in transitions
    assert "run-completed" in transitions


def test_run_unknown_suite_names_what_is_available(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, reports = _idle_env(monkeypatch, tmp_path)
    body = execute_command("/omega-redteam-run --suite nope", object(), env)
    assert body["ok"] is False
    assert body["error"] == "unknown_suite"
    assert body["result"]["available"] == ["smoke"]
    assert body["result"]["usage"] == redteam_run.USAGE
    _assert_no_writes(reports)


def test_run_without_args_is_missing_args(monkeypatch: Any, tmp_path: Path) -> None:
    env, _ = _idle_env(monkeypatch, tmp_path)
    body = execute_command("/omega-redteam-run", object(), env)
    assert body["ok"] is False
    assert body["error"] == "missing_args"


def test_run_without_suite_flag_names_usage(monkeypatch: Any, tmp_path: Path) -> None:
    env, reports = _idle_env(monkeypatch, tmp_path)
    body = execute_command("/omega-redteam-run --verbose", object(), env)
    assert body["ok"] is False
    assert body["error"] == "missing_suite"
    assert body["result"]["usage"] == redteam_run.USAGE
    _assert_no_writes(reports)


def test_status_idle_with_no_transitions(monkeypatch: Any, tmp_path: Path) -> None:
    env, _ = _env(monkeypatch, tmp_path)
    body = execute_command("/omega-redteam-status", object(), env)
    assert body["ok"] is True, body
    assert body["result"] == {"state": "IDLE", "transitions": [], "total": 0}


def test_status_reports_state_plus_recent_transitions(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, reports = _env(monkeypatch, tmp_path)
    transition("IDLE", "RUNNING", "run-started", directory=reports)
    transition("RUNNING", "IDLE", "run-completed", directory=reports)
    transition("IDLE", "DEGRADED", "drift-detected", directory=reports)
    body = execute_command("/omega-redteam-status", object(), env)
    assert body["ok"] is True, body
    result = body["result"]
    assert result["state"] == "DEGRADED"
    assert result["total"] == 3
    assert [row["to"] for row in result["transitions"]] == [
        "RUNNING",
        "IDLE",
        "DEGRADED",
    ]


def test_findings_tails_seeded_rows_newest_last(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, reports = _env(monkeypatch, tmp_path)
    _seed_finding(reports, "abuse-01", "first-detected")
    _seed_finding(reports, "abuse-01", "second-detected")
    _seed_finding(reports, "cost-abuse-01", "third-detected")
    body = execute_command("/omega-redteam-findings", object(), env)
    assert body["ok"] is True, body
    assert body["result"]["total"] == 3
    assert [row["observed_detection"] for row in body["result"]["findings"]] == [
        "first-detected",
        "second-detected",
        "third-detected",
    ]
    limited = execute_command("/omega-redteam-findings --limit 2", object(), env)
    assert limited["ok"] is True, limited
    assert [row["observed_detection"] for row in limited["result"]["findings"]] == [
        "second-detected",
        "third-detected",
    ]


def test_findings_limit_clamps_and_rejects(monkeypatch: Any, tmp_path: Path) -> None:
    env, reports = _env(monkeypatch, tmp_path)
    _seed_finding(reports, "abuse-01", "only-detected")
    zero = execute_command("/omega-redteam-findings --limit 0", object(), env)
    assert zero["ok"] is True, zero
    assert len(zero["result"]["findings"]) == 1
    huge = execute_command("/omega-redteam-findings --limit 999", object(), env)
    assert huge["ok"] is True, huge
    assert huge["result"]["limit"] == 100
    assert len(huge["result"]["findings"]) == 1
    bad = execute_command("/omega-redteam-findings --limit abc", object(), env)
    assert bad["ok"] is False
    assert bad["error"] == "bad_limit"
    assert bad["result"]["usage"] == redteam_findings.USAGE


def test_findings_empty_store_is_empty_not_an_error(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, _ = _env(monkeypatch, tmp_path)
    body = execute_command("/omega-redteam-findings --limit 2", object(), env)
    assert body["ok"] is True, body
    assert body["result"] == {"findings": [], "total": 0, "limit": 2}


def test_triage_appends_exactly_one_proposal_row(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, reports = _env(monkeypatch, tmp_path)
    row = _seed_finding(reports, "abuse-01", "first-detected")
    findings_before = (reports / "findings.ndjson").read_bytes()
    body = execute_command(
        f"/omega-redteam-triage {row['dedupe_hash']} --rationale confirmed benign",
        object(),
        env,
    )
    assert body["ok"] is True, body
    proposal = body["result"]["proposal"]
    assert proposal["dedupe_hash"] == row["dedupe_hash"]
    assert proposal["rationale"] == "confirmed benign"
    assert proposal["proposer"] == "operator"
    assert proposal["ts"]
    queue = reports / "review-queue.ndjson"
    rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert set(rows[0]) == {"dedupe_hash", "rationale", "ts", "proposer"}
    assert rows[0] == proposal
    assert (reports / "findings.ndjson").read_bytes() == findings_before


def test_triage_rejects_bad_hash_and_bad_rationale(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, reports = _env(monkeypatch, tmp_path)
    short = execute_command(
        "/omega-redteam-triage abc123 --rationale looks fine", object(), env
    )
    assert short["ok"] is False
    assert short["error"] == "bad_hash"
    assert short["result"]["usage"] == redteam_triage.USAGE
    good_hash = dedupe_hash("abuse-01", "loopback-seam:abuse-01:first")
    empty = execute_command(
        f"/omega-redteam-triage {good_hash} --rationale ", object(), env
    )
    assert empty["ok"] is False
    assert empty["error"] == "bad_rationale"
    long = execute_command(
        f"/omega-redteam-triage {good_hash} --rationale {'r' * 501}",
        object(),
        env,
    )
    assert long["ok"] is False
    assert long["error"] == "bad_rationale"
    missing = execute_command(f"/omega-redteam-triage {good_hash}", object(), env)
    assert missing["ok"] is False
    assert missing["error"] == "missing_rationale"
    assert not (reports / "review-queue.ndjson").exists()


def test_every_redteam_failure_keeps_the_dict_envelope(
    monkeypatch: Any, tmp_path: Path
) -> None:
    env, _ = _env(monkeypatch, tmp_path)
    for text in (
        "/omega-redteam-run --suite nope",
        "/omega-redteam-findings --limit abc",
        "/omega-redteam-triage abc123 --rationale looks fine",
        "/omega-redteam-status extra words are ignored",
    ):
        body = execute_command(text, object(), env)
        assert set(body) >= {"ok", "command", "result"}
        assert body["command"].startswith("omega-redteam-")

"""Hermetic tests for the purple-cycle orchestration library and CLI (R7)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from omega_prime.durable import workflows
from omega_prime.grokbot import pin_bump, purple_cycle, release_watch
from omega_prime.grokbot.purple_cycle import MODES, STEP_NAMES, main, run_cycle
from omega_prime.redteam import roe_audit
from sidecar.findings import load_findings, record_finding

NOW = datetime(2026, 10, 14, 12, 30, 45, tzinfo=UTC)
REPO = "OWNER/NAME"
SECRET = "SENTINEL-NOT-A-SECRET-5f3a91"
#: Tag-shaped sentinel the tag grammar rejects (no leading ``v<digits>.``).
TOKEN_TAG = "SENTINEL-TAG-5f3a91-not-a-version"

IDLE_ENV = {
    "OMEGA_PRIME_REDTEAM_ENABLED": "1",
    "OMEGA_PRIME_REDTEAM_ROE_REQUIRED": "0",
    "OMEGA_PRIME_SECRET_TOKEN": SECRET,
}
KILLED_ENV = {**IDLE_ENV, "OMEGA_PRIME_REDTEAM_KILL": "1"}


def _tags(*names: str) -> list[dict[str, Any]]:
    return [{"name": name, "commit": {"sha": f"sha-{name}"}} for name in names]


def _config(**node: Any) -> dict[str, Any]:
    return {"redteam": {"findings_sync": node}}


def _sync_config(**overrides: Any) -> dict[str, Any]:
    return _config(**{"enabled": True, "repo": REPO, "labels": {}, **overrides})


class Fetch:
    """Counting upstream-tags fake."""

    def __init__(self, *names: str) -> None:
        self.names = names
        self.calls = 0

    def __call__(self) -> list[dict[str, Any]]:
        self.calls += 1
        return _tags(*self.names)


class Runner:
    """Counting GhRunner fake returning a created-issue payload."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], data: dict[str, Any]) -> Any:
        self.calls.append(list(argv))
        payload = {"html_url": f"gh-issue-{len(self.calls)}"}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


class PinSpy:
    """Replaces ``audit_scheduled_job``; apply/non-audit bump are unreachable."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.verdict = "unchanged"

    def __call__(self, config: Any, *, root: Any = None, run: Any = None) -> Any:
        self.calls.append({"config": dict(config), "root": root, "run": run})
        return {"verdict": self.verdict, "semver_compatible": True, "exit_code": 0}


@pytest.fixture
def pin(monkeypatch: pytest.MonkeyPatch) -> PinSpy:
    spy = PinSpy()
    monkeypatch.setattr(pin_bump, "audit_scheduled_job", spy)

    def _forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the cycle must never apply or run a non-audit bump")

    monkeypatch.setattr(pin_bump, "apply_scheduled_bump", _forbidden)
    monkeypatch.setattr(pin_bump, "bump", _forbidden)
    return spy


@pytest.fixture
def dirs(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=tmp_path / "root",
        reports=tmp_path / "reports",
        tmp=tmp_path,
    )


def _snapshot(*bases: Path) -> dict[str, bytes | None]:
    found: dict[str, bytes | None] = {}
    for base in bases:
        for path in sorted(base.rglob("*")):
            found[str(path)] = path.read_bytes() if path.is_file() else None
    return found


def _cycle(dirs: SimpleNamespace, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "mode": "run",
        "root": dirs.root,
        "reports_dir": dirs.reports,
        "config": _config(enabled=False),
        "env": IDLE_ENV,
        "now": NOW,
        "fetch": Fetch("v1.0.0"),
        "runner": Runner(),
    }
    params.update(overrides)
    return run_cycle(**params)


def _by_name(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {rec["name"]: rec for rec in result["steps"]}


def _triples(result: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(r["name"], r["status"], r["reason"]) for r in result["steps"]]


def _seed_release_state(dirs: SimpleNamespace, *names: str) -> None:
    release_watch.check_release_watch(
        dirs.reports / "release-watch.json", lambda: _tags(*names), NOW
    )


def _checkpoint_file(dirs: SimpleNamespace, cycle_id: str) -> Path:
    return dirs.reports / "purple-cycle" / "workflows" / f"{cycle_id}.json"


def _ledger_rows(dirs: SimpleNamespace) -> list[dict[str, Any]]:
    text = (dirs.reports / "purple-cycle.ndjson").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(STEP_NAMES, 0)
    for name in STEP_NAMES:
        inner = purple_cycle._STEP_FUNCS[name]

        def counted(
            ctx: Any, state: Any, _name: str = name, _inner: Any = inner
        ) -> Any:
            counts[_name] += 1
            return _inner(ctx, state)

        monkeypatch.setitem(purple_cycle._STEP_FUNCS, name, counted)
    return counts


# --------------------------------------------------------------------------
# contract constants and modes


def test_contract_constants() -> None:
    assert STEP_NAMES == (
        "preflight",
        "release-watch",
        "pin-bump-audit",
        "engagement",
        "findings-plan",
        "findings-dispatch",
    )
    assert MODES == ("plan", "run", "apply")


def test_step_order_and_statuses_per_mode(dirs, pin) -> None:
    plan = _cycle(dirs, mode="plan")
    assert [r["name"] for r in plan["steps"]] == list(STEP_NAMES)
    assert _triples(plan) == [
        ("preflight", "ok", "ok"),
        ("release-watch", "skipped", "plan-mode"),
        ("pin-bump-audit", "skipped", "plan-mode"),
        ("engagement", "ok", "ok"),
        ("findings-plan", "skipped", "disabled"),
        ("findings-dispatch", "skipped", "plan-mode"),
    ]
    assert (plan["ok"], plan["exit"], plan["state"]) == (True, 0, "completed")
    assert plan["cycle_id"] == "purple-cycle-20261014-plan"
    assert plan["checkpoint"] is None and plan["ledger"] is None

    run = _cycle(dirs, mode="run")
    assert _triples(run) == [
        ("preflight", "ok", "ok"),
        ("release-watch", "ok", "seeded"),
        ("pin-bump-audit", "skipped", "no-new-release"),
        ("engagement", "ok", "ok"),
        ("findings-plan", "skipped", "disabled"),
        ("findings-dispatch", "skipped", "not-selected"),
    ]
    assert run["cycle_id"] == "purple-cycle-20261014-run"
    assert run["resumed"] is False and run["blocked"] is None
    assert run["checkpoint"] == str(
        dirs.reports / "purple-cycle" / "workflows" / "purple-cycle-20261014-run.json"
    )
    assert run["ledger"] == str(dirs.reports / "purple-cycle.ndjson")

    apply = _cycle(dirs, mode="apply", config=_apply_config(dirs, enabled=False))
    assert _triples(apply)[-1] == ("findings-dispatch", "skipped", "disabled")
    assert (apply["ok"], apply["exit"], apply["state"]) == (True, 0, "completed")


def test_result_key_set_and_step_shape(dirs, pin) -> None:
    result = _cycle(dirs, mode="plan")
    assert set(result) == {
        "ok",
        "exit",
        "mode",
        "cycle_id",
        "state",
        "resumed",
        "blocked",
        "steps",
        "checkpoint",
        "ledger",
    }
    for rec in result["steps"]:
        assert set(rec) == {"name", "status", "exit", "reason", "detail"}


def test_preflight_records_harness_and_roe(dirs, pin) -> None:
    ok = _by_name(_cycle(dirs, mode="plan"))["preflight"]
    assert ok["detail"]["harness_state"] == "IDLE"
    assert ok["detail"]["roe_state"] == "none"

    blocked = _by_name(_cycle(dirs, mode="plan", env=KILLED_ENV))["preflight"]
    assert blocked["status"] == "warn" and blocked["reason"] == "harness-blocked"
    assert blocked["detail"]["harness_state"] == "BLOCKED"
    assert blocked["detail"]["harness_cause"] == "kill-switch-set"


def _roe_artifact(path: Path, *, until: datetime) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 1,
        "operator": "alice",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": (until - timedelta(days=30)).isoformat(timespec="seconds"),
        "effective_until": until.isoformat(timespec="seconds"),
        "case_classes": ["prompt-injection"],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _apply_config(
    dirs: SimpleNamespace, *, until: datetime | None = None, **node: Any
) -> dict[str, Any]:
    """Config with a valid RoE artifact (apply refuses without one)."""
    window = until if until is not None else NOW + timedelta(days=30)
    artifact = _roe_artifact(dirs.tmp / "roe-valid.json", until=window)
    return {"redteam": {"roe_file": artifact, "findings_sync": node}}


@pytest.mark.parametrize(
    ("delta", "state", "status"),
    [
        (timedelta(days=30), "valid", "ok"),
        (timedelta(days=3), "expiring-7d", "warn"),
        (timedelta(hours=5), "expiring-1d", "warn"),
        (timedelta(days=-2), "expired", "warn"),
    ],
)
def test_preflight_roe_lifecycle_states(dirs, pin, delta, state, status) -> None:
    artifact = _roe_artifact(dirs.tmp / "roe.json", until=NOW + delta)
    config = {"redteam": {"roe_file": artifact, "findings_sync": {}}}
    pre = _by_name(_cycle(dirs, mode="plan", config=config))["preflight"]
    assert pre["detail"]["roe_state"] == state
    assert pre["status"] == status
    assert pre["detail"]["roe_artifacts"] == 1
    assert artifact not in json.dumps(pre)


def test_preflight_never_writes_roe_audit(dirs, pin) -> None:
    artifact = _roe_artifact(dirs.tmp / "roe.json", until=NOW - timedelta(days=2))
    config = {"redteam": {"roe_files": [artifact]}}
    before = _snapshot(dirs.tmp)
    _cycle(dirs, mode="plan", config=config)
    assert _snapshot(dirs.tmp) == before


# --------------------------------------------------------------------------
# plan mode purity


def test_plan_mode_is_pure_and_repeatable(dirs, pin) -> None:
    dirs.reports.mkdir(parents=True)
    record_finding(
        directory=dirs.reports,
        case_id="abuse-01",
        klass="prompt_injection",
        severity="high",
        observed_detection="no-detection",
        verdict="miss",
        response="do anything now",
    )
    ledger = dirs.tmp / "dispatch.ndjson"
    grants = dirs.tmp / "grants.ndjson"
    config = _sync_config(dispatch_ledger=str(ledger), grant_usage_ledger=str(grants))
    config_before = json.dumps(config, sort_keys=True)
    fetch = Fetch("v1.0.0")

    def forbidden(argv: Any, data: Any) -> Any:
        raise AssertionError("runner must never be called in plan mode")

    before = _snapshot(dirs.tmp)
    first = _cycle(dirs, mode="plan", config=config, fetch=fetch, runner=forbidden)
    second = _cycle(dirs, mode="plan", config=config, fetch=fetch, runner=forbidden)
    assert _snapshot(dirs.tmp) == before
    assert first == second
    assert fetch.calls == 0 and pin.calls == []
    assert not ledger.exists() and not grants.exists()
    assert not dirs.root.exists()
    assert json.dumps(config, sort_keys=True) == config_before
    plan = _by_name(first)["findings-plan"]
    assert plan["status"] == "ok"
    assert plan["detail"]["would_sync"] == 1
    assert plan["detail"]["dry_run"] is True
    assert len(load_findings(dirs.reports)) == 1
    assert "synced_at" not in load_findings(dirs.reports)[0]


def test_findings_plan_never_writes_ledgers_even_when_named(dirs, pin) -> None:
    dirs.reports.mkdir(parents=True)
    record_finding(
        directory=dirs.reports,
        case_id="abuse-01",
        klass="prompt_injection",
        severity="high",
        observed_detection="no-detection",
        verdict="miss",
        response="x",
    )
    ledger = dirs.tmp / "d.ndjson"
    grants = dirs.tmp / "g.ndjson"
    for mode in ("run", "apply"):
        config = _sync_config(
            dispatch_ledger=str(ledger),
            grant_usage_ledger=str(grants),
            dispatch_dry_run=False,
        )
        result = _cycle(
            dirs,
            mode=mode,
            config=config,
            steps=["preflight", "findings-plan"],
            runner=None,
            force_new=True,
        )
        assert _by_name(result)["findings-plan"]["detail"]["would_sync"] == 1
    assert not ledger.exists() and not grants.exists()


def test_findings_plan_flags_grant_conflicts_and_quiet_counts(
    dirs, pin, monkeypatch
) -> None:
    from omega_prime.grokbot import findings_sync

    def fake_tick(config: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["dispatch_dry_run"] is True
        assert kwargs["now"] == NOW
        return {
            "pushed": [{"dedupe_hash": "a", "issue_url": ""}],
            "skipped": ["b", "c"],
            "dry_run": True,
            "quiet_hours": True,
            "quiet_skipped": [{"dedupe_hash": "d"}],
            "grant_conflicts": [{"tracker": "x"}],
            "budget_held": [
                {"dedupe_hash": "e", "reason": "sync-budget"},
                {"dedupe_hash": "f", "reason": "sync-budget"},
            ],
        }

    monkeypatch.setattr(findings_sync, "tick", fake_tick)
    rec = _by_name(_cycle(dirs, mode="plan", config=_sync_config()))["findings-plan"]
    assert rec["detail"] == {
        "would_sync": 1,
        "already_synced": 2,
        "quiet_held": 1,
        "grant_conflicts": True,
        "dry_run": True,
        "budget_held": 2,
    }


def test_findings_plan_structured_error_is_a_warning(dirs, pin) -> None:
    config = _config(enabled=True, repo="", labels={})
    rec = _by_name(_cycle(dirs, mode="plan", config=config))["findings-plan"]
    assert rec["status"] == "warn" and rec["reason"] == "plan-error"


# --------------------------------------------------------------------------
# run mode: release-watch and pin-bump audit


def test_new_tag_triggers_audit_only_for_that_tag(dirs, pin) -> None:
    _seed_release_state(dirs, "v1.0.0")
    result = _cycle(dirs, fetch=Fetch("v1.2.0", "v1.1.0", "v1.0.0"))
    watch = _by_name(result)["release-watch"]
    assert watch["reason"] == "new-release"
    assert watch["detail"]["new_tags"] == ["v1.2.0", "v1.1.0"]
    assert watch["detail"]["newest_tag"] == "v1.2.0"
    assert len(pin.calls) == 1
    assert pin.calls[0]["config"] == {"to": "v1.2.0"}
    assert pin.calls[0]["root"] == dirs.root
    audit = _by_name(result)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("ok", "ok")
    assert audit["detail"]["verdict"] == "unchanged"
    assert audit["detail"]["tag"] == "v1.2.0"


def test_pin_audit_regressed_is_a_warning_without_apply(dirs, pin) -> None:
    _seed_release_state(dirs, "v1.0.0")
    pin.verdict = "regressed"
    result = _cycle(
        dirs,
        mode="apply",
        fetch=Fetch("v1.1.0", "v1.0.0"),
        config=_apply_config(dirs, enabled=False),
    )
    audit = _by_name(result)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("warn", "regressed")
    assert result["exit"] == 0 and result["state"] == "completed"
    assert len(pin.calls) == 1


def test_no_new_tag_skips_pin_audit(dirs, pin) -> None:
    _seed_release_state(dirs, "v1.0.0")
    result = _cycle(dirs, fetch=Fetch("v1.0.0"))
    assert _triples(result)[1:3] == [
        ("release-watch", "ok", "no-new-release"),
        ("pin-bump-audit", "skipped", "no-new-release"),
    ]
    assert pin.calls == []


def test_real_audit_runs_audit_only_and_never_applies(
    dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def spy_bump(to: str, **kwargs: Any) -> int:
        seen.append({"to": to, **kwargs})
        print("[audit-only] audit verdict: improved")
        return 0

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("apply_scheduled_bump must be unreachable")

    monkeypatch.setattr(pin_bump, "bump", spy_bump)
    monkeypatch.setattr(pin_bump, "apply_scheduled_bump", forbidden)
    _seed_release_state(dirs, "v1.0.0")
    result = _cycle(dirs, mode="apply", fetch=Fetch("v2.0.0", "v1.0.0"))
    assert len(seen) == 1
    call = seen[0]
    assert call["to"] == "v2.0.0"
    assert call["audit_only"] is True
    assert call["open_pr"] is False and call["bump_commit"] is False
    assert call["dry_run"] is False
    assert call["root"] == dirs.root
    audit = _by_name(result)["pin-bump-audit"]
    assert audit["detail"]["verdict"] == "improved"


def test_pin_run_timeout_is_a_non_halting_audit_failure(
    dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def spy_bump(to: str, *, run: Any, **kwargs: Any) -> int:
        run(["git", "ls-remote", "somewhere"], cwd=None)
        return 0

    def timing_out(cmd: Any, cwd: Any = None) -> Any:
        raise subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr(pin_bump, "bump", spy_bump)
    _seed_release_state(dirs, "v1.0.0")
    result = _cycle(dirs, fetch=Fetch("v1.1.0", "v1.0.0"), pin_run=timing_out)
    audit = _by_name(result)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("warn", "audit-failed")
    assert audit["detail"] == {
        "tag": "v1.1.0",
        "error_type": "timeout",
        "attempts": 1,
    }
    assert (result["ok"], result["exit"], result["state"]) == (True, 0, "completed")
    # A failed audit never starves the steps after it.
    statuses = [rec["status"] for rec in result["steps"][3:]]
    assert statuses == ["ok", "skipped", "skipped"]


def test_default_pin_run_has_per_command_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: Any, **kwargs: Any) -> Any:
        captured.update(kwargs, cmd=cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(purple_cycle.subprocess, "run", fake_run)
    purple_cycle._default_pin_run(["git", "status"], cwd=None)
    assert captured["timeout"] == purple_cycle.PIN_RUN_TIMEOUT_SECONDS == 300
    assert captured["cmd"] == ["git", "status"]


def test_fetch_error_is_a_step_error_not_a_crash(dirs, pin) -> None:
    def broken() -> Any:
        raise RuntimeError(f"gh failed: {SECRET}")

    result = _cycle(dirs, fetch=broken)
    watch = _by_name(result)["release-watch"]
    assert (watch["status"], watch["reason"]) == ("error", "step-error")
    assert watch["detail"] == {"error_type": "RuntimeError"}
    assert SECRET not in json.dumps(result)
    assert result["exit"] == 1 and result["state"] == "failed"
    # Only the completed preflight step is checkpointed; the errored one is not.
    checkpoint = json.loads(Path(result["checkpoint"]).read_text(encoding="utf-8"))
    assert checkpoint["done"] == 1
    assert [rec["name"] for rec in checkpoint["state"]["steps"]] == ["preflight"]


def test_default_fetch_is_used_when_none_injected(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake_upstream(repo: str) -> list[dict[str, Any]]:
        seen.append(repo)
        return _tags("v1.0.0")

    monkeypatch.setattr(release_watch, "fetch_upstream_tags", fake_upstream)
    result = _cycle(dirs, fetch=None)
    assert seen == [release_watch.DEFAULT_REPO]
    assert _by_name(result)["release-watch"]["status"] == "ok"


# --------------------------------------------------------------------------
# engagement


def test_engagement_summary_and_optional_digest(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _by_name(_cycle(dirs, mode="plan"))["engagement"]
    assert real["status"] == "ok"
    assert real["detail"]["state"] in ("ok", "no-reports")
    assert "line" in real["detail"]

    class Without:
        @staticmethod
        def summarize(reports_dir: Path, since: Any) -> dict[str, Any]:
            return {"state": "ok", "line": "runs=3 pass=90% gaps=1 findings=+2/-0"}

    monkeypatch.setattr(purple_cycle, "_load_engagement_module", lambda: Without)
    plain = _by_name(_cycle(dirs, mode="plan"))["engagement"]
    assert plain["detail"] == {
        "state": "ok",
        "line": "runs=3 pass=90% gaps=1 findings=+2/-0",
    }

    class With(Without):
        @staticmethod
        def build_digest(reports_dir: Path) -> list[str]:
            return ["coverage prompt_injection 80%", "", "gaps=1"]

    monkeypatch.setattr(purple_cycle, "_load_engagement_module", lambda: With)
    digest = _by_name(_cycle(dirs, mode="plan"))["engagement"]
    assert digest["status"] == "ok"
    assert digest["detail"]["digest"] == ["coverage prompt_injection 80%", "gaps=1"]

    class Broken(Without):
        @staticmethod
        def build_digest(reports_dir: Path) -> list[str]:
            raise TypeError("signature drift")

    monkeypatch.setattr(purple_cycle, "_load_engagement_module", lambda: Broken)
    broken = _by_name(_cycle(dirs, mode="plan"))["engagement"]
    assert (broken["status"], broken["reason"]) == ("warn", "digest-error")
    assert broken["detail"]["digest_error"] is True


# --------------------------------------------------------------------------
# durability, resume and ledger


def test_resume_after_step_failure_reruns_only_the_failed_step(
    dirs, pin, counters, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = {"n": 0}

    class Flaky:
        @staticmethod
        def summarize(reports_dir: Path, since: Any) -> dict[str, Any]:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("boom-free-text")
            return {"state": "no-reports", "line": "runs=0"}

    monkeypatch.setattr(purple_cycle, "_load_engagement_module", lambda: Flaky)
    fetch = Fetch("v1.0.0")
    first = _cycle(dirs, fetch=fetch)
    assert first["state"] == "failed" and first["exit"] == 1
    assert first["resumed"] is False
    assert _triples(first)[:4] == [
        ("preflight", "ok", "ok"),
        ("release-watch", "ok", "seeded"),
        ("pin-bump-audit", "skipped", "no-new-release"),
        ("engagement", "error", "step-error"),
    ]
    assert "boom-free-text" not in json.dumps(first)
    assert counters == {
        "preflight": 1,
        "release-watch": 1,
        "pin-bump-audit": 1,
        "engagement": 1,
        "findings-plan": 0,
        "findings-dispatch": 0,
    }

    second = _cycle(dirs, fetch=fetch)
    assert second["state"] == "completed" and second["exit"] == 0
    assert second["resumed"] is True
    assert counters == {
        "preflight": 1,
        "release-watch": 1,
        "pin-bump-audit": 1,
        "engagement": 2,
        "findings-plan": 1,
        "findings-dispatch": 1,
    }
    assert fetch.calls == 1
    assert [r["name"] for r in second["steps"]] == list(STEP_NAMES)
    assert _by_name(second)["release-watch"] == _by_name(first)["release-watch"]
    rows = _ledger_rows(dirs)
    assert [(r["step"], r["status"]) for r in rows] == [
        ("preflight", "ok"),
        ("release-watch", "ok"),
        ("pin-bump-audit", "skipped"),
        ("engagement", "error"),
        ("engagement", "ok"),
        ("findings-plan", "skipped"),
        # findings-dispatch is "not-selected" in run mode: no ledger row.
    ]


def test_finished_cycle_rerun_executes_nothing(dirs, pin, counters) -> None:
    fetch = Fetch("v1.0.0")
    first = _cycle(dirs, fetch=fetch)
    snapshot = dict(counters)
    ledger_before = (dirs.reports / "purple-cycle.ndjson").read_bytes()
    second = _cycle(dirs, fetch=fetch)
    assert counters == snapshot
    assert fetch.calls == 1
    assert second == {**first, "resumed": True}
    assert (dirs.reports / "purple-cycle.ndjson").read_bytes() == ledger_before


def test_force_new_gets_its_own_cycle_id_and_checkpoint(dirs, pin) -> None:
    first = _cycle(dirs)
    forced = _cycle(dirs, force_new=True)
    assert first["cycle_id"] == "purple-cycle-20261014-run"
    assert forced["cycle_id"] == "purple-cycle-20261014-run-123045"
    assert forced["resumed"] is False
    names = sorted(
        p.name for p in (dirs.reports / "purple-cycle" / "workflows").glob("*.json")
    )
    assert names == [
        "purple-cycle-20261014-run-123045.json",
        "purple-cycle-20261014-run.json",
    ]
    other_day = _cycle(dirs, now=NOW + timedelta(days=1))
    assert other_day["cycle_id"] == "purple-cycle-20261015-run"
    assert other_day["resumed"] is False


def test_cycle_id_uses_utc_date_of_injected_now(dirs, pin) -> None:
    late = datetime(2026, 10, 14, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    result = _cycle(dirs, mode="plan", now=late)
    assert result["cycle_id"] == "purple-cycle-20261015-plan"
    naive = _cycle(dirs, mode="plan", now=datetime(2026, 1, 2, 3, 4, 5))
    assert naive["cycle_id"] == "purple-cycle-20260102-plan"


def test_checkpoint_and_ledger_contents_hold_no_secrets(dirs, pin) -> None:
    _seed_release_state(dirs, "v1.0.0")
    fetch = Fetch(TOKEN_TAG, "v1.0.0")
    env = {**IDLE_ENV, "OMEGA_PRIME_REDTEAM_ROE_KEY": SECRET}
    result = _cycle(
        dirs,
        mode="apply",
        fetch=fetch,
        env=env,
        config=_apply_config(dirs, enabled=False),
    )
    watch = _by_name(result)["release-watch"]
    assert watch["detail"]["new_count"] == 1
    assert watch["detail"]["new_tags"] == []
    assert watch["detail"]["newest_tag"] is None
    audit = _by_name(result)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("warn", "unsafe-tag")
    assert pin.calls == []

    checkpoint = _checkpoint_file(dirs, result["cycle_id"]).read_text(encoding="utf-8")
    ledger = (dirs.reports / "purple-cycle.ndjson").read_text(encoding="utf-8")
    document = json.loads(checkpoint)
    assert document["done"] == len(STEP_NAMES)
    assert set(document["state"]) == {
        "cycle_id",
        "mode",
        "now",
        "selected",
        "steps",
        "blocked",
    }
    for text in (json.dumps(result), checkpoint, ledger):
        assert SECRET not in text
        assert TOKEN_TAG not in text
        assert "SENTINEL-TAG" not in text
    for row in _ledger_rows(dirs):
        assert set(row) == {"ts", "cycle_id", "step", "status", "exit", "reason"}
        assert row["ts"] == "2026-10-14T12:30:45+00:00"
        assert row["cycle_id"] == "purple-cycle-20261014-apply"
    assert ledger.count("\n") == len(STEP_NAMES)
    for line in ledger.splitlines():
        assert line == json.dumps(json.loads(line), sort_keys=True)


def test_ledger_write_failure_never_raises(dirs, pin, monkeypatch) -> None:
    dirs.reports.mkdir(parents=True)
    (dirs.reports / "purple-cycle.ndjson").mkdir()
    result = _cycle(dirs)
    assert result["state"] == "completed" and result["exit"] == 0


def test_unreadable_checkpoint_fails_closed(dirs, pin, counters) -> None:
    target = _checkpoint_file(dirs, "purple-cycle-20261014-run")
    target.parent.mkdir(parents=True)
    target.write_text("{nope")
    result = _cycle(dirs)
    assert (result["ok"], result["exit"], result["state"]) == (False, 1, "failed")
    first = result["steps"][0]
    assert (first["name"], first["status"], first["reason"]) == (
        "preflight",
        "error",
        "checkpoint-unreadable",
    )
    assert all(count == 0 for count in counters.values())


def test_foreign_checkpoint_shape_fails_closed(dirs, pin, counters) -> None:
    target = _checkpoint_file(dirs, "purple-cycle-20261014-run")
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"done": 3, "state": {"steps": []}}))
    result = _cycle(dirs)
    assert result["state"] == "failed" and result["exit"] == 1
    assert all(count == 0 for count in counters.values())


# --------------------------------------------------------------------------
# apply gating


def test_apply_refused_when_harness_blocked(dirs, pin, counters) -> None:
    runner = Runner()
    dirs.reports.mkdir(parents=True)
    record_finding(
        directory=dirs.reports,
        case_id="abuse-01",
        klass="prompt_injection",
        severity="high",
        observed_detection="no-detection",
        verdict="miss",
        response="x",
    )
    result = _cycle(
        dirs, mode="apply", env=KILLED_ENV, config=_sync_config(), runner=runner
    )
    assert runner.calls == []
    assert (result["ok"], result["exit"], result["state"]) == (False, 3, "blocked")
    assert result["blocked"] == {
        "cause": "kill-switch-set",
        "step": "findings-dispatch",
    }
    dispatch = _by_name(result)["findings-dispatch"]
    assert (dispatch["status"], dispatch["reason"], dispatch["exit"]) == (
        "blocked",
        "apply-refused",
        3,
    )
    # Completed steps stay completed.
    assert _by_name(result)["preflight"]["status"] == "warn"
    assert _by_name(result)["engagement"]["status"] == "ok"
    assert _by_name(result)["findings-plan"]["status"] == "ok"
    assert all(rec["status"] != "error" for rec in result["steps"])
    assert counters["findings-dispatch"] == 1
    assert "synced_at" not in load_findings(dirs.reports)[0]
    last = _ledger_rows(dirs)[-1]
    assert (last["step"], last["status"], last["exit"]) == (
        "findings-dispatch",
        "blocked",
        3,
    )
    # A refusal is NOT checkpointed: a rerun re-evaluates the gate, and the
    # completed steps are not executed again.
    again = _cycle(
        dirs, mode="apply", env=KILLED_ENV, config=_sync_config(), runner=runner
    )
    assert again == {**result, "resumed": True}
    assert counters["findings-dispatch"] == 2
    assert counters["preflight"] == 1 and counters["findings-plan"] == 1


def _apply_sync(dirs: SimpleNamespace, **overrides: Any) -> dict[str, Any]:
    return _apply_config(
        dirs, **{"enabled": True, "repo": REPO, "labels": {}, **overrides}
    )


def test_apply_refused_on_expired_roe_even_when_harness_idle(dirs, pin) -> None:
    artifact = _roe_artifact(dirs.tmp / "roe.json", until=NOW - timedelta(days=1))
    config = {
        "redteam": {
            "roe_file": artifact,
            "findings_sync": {"enabled": True, "repo": REPO, "labels": {}},
        }
    }
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["exit"] == 3 and result["state"] == "blocked"
    assert result["blocked"] == {"cause": "expired", "step": "findings-dispatch"}


def test_apply_refused_on_missing_roe_artifact(dirs, pin) -> None:
    config = {
        "redteam": {
            "roe_file": str(dirs.tmp / "absent.json"),
            "findings_sync": {"enabled": True, "repo": REPO, "labels": {}},
        }
    }
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": "missing", "step": "findings-dispatch"}


def test_apply_allowed_when_idle_dispatches_real_rows(dirs, pin) -> None:
    dirs.reports.mkdir(parents=True)
    for case_id, response in (("abuse-01", "a"), ("abuse-02", "b")):
        record_finding(
            directory=dirs.reports,
            case_id=case_id,
            klass="prompt_injection",
            severity="high",
            observed_detection="no-detection",
            verdict="miss",
            response=response,
        )
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=_apply_sync(dirs), runner=runner)
    assert (result["ok"], result["exit"], result["state"]) == (True, 0, "completed")
    assert result["blocked"] is None
    assert len(runner.calls) == 2
    plan = _by_name(result)["findings-plan"]
    assert plan["detail"]["would_sync"] == 2
    dispatch = _by_name(result)["findings-dispatch"]
    assert (dispatch["status"], dispatch["reason"]) == ("ok", "ok")
    assert dispatch["detail"]["would_sync"] == 2
    assert dispatch["detail"]["dry_run"] is False
    rows = load_findings(dirs.reports)
    assert all(row.get("issue_url") for row in rows)


def test_apply_dispatch_runner_error_is_a_step_error(dirs, pin) -> None:
    config = _apply_config(dirs, enabled=True, repo="", labels={})
    result = _cycle(dirs, mode="apply", config=config)
    dispatch = _by_name(result)["findings-dispatch"]
    assert (dispatch["status"], dispatch["reason"]) == ("error", "dispatch-error")
    assert result["exit"] == 1 and result["state"] == "failed"


def test_dispatch_not_run_in_run_mode_even_with_idle_harness(dirs, pin) -> None:
    runner = Runner()
    result = _cycle(dirs, mode="run", config=_sync_config(), runner=runner)
    assert _by_name(result)["findings-dispatch"]["reason"] == "not-selected"
    assert runner.calls == []


def test_apply_with_only_dispatch_selected_still_gates(dirs, pin) -> None:
    runner = Runner()
    result = _cycle(
        dirs,
        mode="apply",
        env=KILLED_ENV,
        config=_sync_config(),
        runner=runner,
        steps=["findings-dispatch"],
    )
    assert result["exit"] == 3 and runner.calls == []
    assert result["blocked"]["cause"] == "kill-switch-set"


# --------------------------------------------------------------------------
# step selection and argument validation


def test_steps_subset_runs_in_canonical_order(dirs, pin) -> None:
    result = _cycle(
        dirs, mode="run", steps=["engagement", "preflight"], fetch=Fetch("v1.0.0")
    )
    assert _triples(result) == [
        ("preflight", "ok", "ok"),
        ("release-watch", "skipped", "not-selected"),
        ("pin-bump-audit", "skipped", "not-selected"),
        ("engagement", "ok", "ok"),
        ("findings-plan", "skipped", "not-selected"),
        ("findings-dispatch", "skipped", "not-selected"),
    ]
    rows = _ledger_rows(dirs)
    assert [r["step"] for r in rows] == ["preflight", "engagement"]


def test_invalid_arguments_raise_before_doing_anything(dirs, pin) -> None:
    before = _snapshot(dirs.tmp)
    with pytest.raises(ValueError, match="unknown mode"):
        _cycle(dirs, mode="destroy")
    with pytest.raises(ValueError, match="unknown step"):
        _cycle(dirs, steps=["preflight", "nope"])
    with pytest.raises(ValueError, match="steps"):
        _cycle(dirs, steps=[])
    with pytest.raises(ValueError, match="steps"):
        _cycle(dirs, steps="preflight")
    assert _snapshot(dirs.tmp) == before


def test_config_loaded_only_when_not_injected(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omega_prime.config as config_module

    calls: list[Any] = []

    def fake_load(root: Any = None) -> dict[str, Any]:
        calls.append(root)
        return _config(enabled=False)

    monkeypatch.setattr(config_module, "load_config", fake_load)
    _cycle(dirs, mode="plan")
    assert calls == []
    _cycle(dirs, mode="plan", config=None)
    assert calls == [dirs.root]


def test_config_load_failure_fails_closed_in_apply(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omega_prime.config as config_module

    def broken(root: Any = None) -> dict[str, Any]:
        raise OSError("unreadable")

    monkeypatch.setattr(config_module, "load_config", broken)
    plan = _cycle(dirs, mode="plan", config=None)
    assert _by_name(plan)["preflight"]["reason"] == "config-error"
    assert _by_name(plan)["findings-plan"]["reason"] == "config-error"
    assert plan["exit"] == 1


def test_reports_dir_defaults_from_environment(
    tmp_path: Path, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "from-env"
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(reports))
    result = run_cycle(
        mode="run",
        root=tmp_path / "root",
        config=_config(enabled=False),
        env=IDLE_ENV,
        now=NOW,
        fetch=Fetch("v1.0.0"),
    )
    assert result["ledger"] == str(reports / "purple-cycle.ndjson")
    assert (reports / "release-watch.json").is_file()
    explicit = tmp_path / "explicit"
    result = run_cycle(
        mode="plan",
        reports_dir=explicit,
        root=tmp_path / "root",
        config=_config(enabled=False),
        env={**IDLE_ENV, "OMEGA_PRIME_SIDECAR_REPORTS_DIR": str(tmp_path / "x")},
        now=NOW,
    )
    assert result["ok"] is True


# --------------------------------------------------------------------------
# CLI


def _cli(
    argv: list[str], dirs: SimpleNamespace, *, now: bool = True, **overrides: Any
) -> int:
    kwargs: dict[str, Any] = {
        "env": IDLE_ENV,
        "config": _config(enabled=False),
        "fetch": Fetch("v1.0.0"),
        "runner": Runner(),
    }
    kwargs.update(overrides)
    base = ["--reports-dir", str(dirs.reports), "--root", str(dirs.root)]
    if now:
        base += ["--now", "2026-10-14T12:30:45+00:00"]
    return main([*argv, *base], **kwargs)


def _live_apply_config(dirs: SimpleNamespace, **node: Any) -> dict[str, Any]:
    """Apply config whose RoE is valid against the REAL clock (CLI apply)."""
    return _apply_config(dirs, until=datetime.now(UTC) + timedelta(days=30), **node)


def test_cli_json_output_shape_and_exit_zero(dirs, pin, capsys) -> None:
    code = _cli(["--json"], dirs)
    out = capsys.readouterr()
    assert code == 0 and out.err == ""
    lines = out.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert lines[0] == json.dumps(payload, sort_keys=True)
    assert payload["mode"] == "plan" and payload["exit"] == 0
    assert payload["cycle_id"] == "purple-cycle-20261014-plan"
    assert [s["name"] for s in payload["steps"]] == list(STEP_NAMES)
    assert payload == run_cycle(
        mode="plan",
        root=dirs.root,
        reports_dir=dirs.reports,
        config=_config(enabled=False),
        env=IDLE_ENV,
        now=NOW,
    )


def test_cli_human_table(dirs, pin, capsys) -> None:
    code = _cli(["--mode", "run"], dirs)
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert out[0] == "purple-cycle purple-cycle-20261014-run state=completed exit=0"
    assert out[1:] == [
        "preflight ok ok",
        "release-watch ok seeded",
        "pin-bump-audit skipped no-new-release",
        "engagement ok ok",
        "findings-plan skipped disabled",
        "findings-dispatch skipped not-selected",
    ]


def test_cli_apply_flag_is_alias_and_blocked_exit_is_three(dirs, pin, capsys) -> None:
    code = _cli(["--apply", "--json"], dirs, now=False, env=KILLED_ENV)
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["mode"] == "apply" and payload["state"] == "blocked"
    assert payload["blocked"]["step"] == "findings-dispatch"


def test_cli_apply_with_explicit_apply_mode_is_fine(dirs, pin, capsys) -> None:
    config = _live_apply_config(dirs, enabled=False)
    argv = ["--apply", "--mode", "apply", "--json"]
    assert _cli(argv, dirs, now=False, config=config) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "apply"


def test_cli_step_error_exit_is_one(dirs, pin, capsys) -> None:
    def broken() -> Any:
        raise RuntimeError("down")

    assert _cli(["--mode", "run"], dirs, fetch=broken) == 1
    assert "release-watch error step-error" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "plan", "--apply"],
        ["--mode", "run", "--apply"],
        ["--mode", "explode"],
        ["--steps", "preflight,nope"],
        ["--steps", ","],
        ["--bogus"],
    ],
)
def test_cli_usage_errors_exit_two(dirs, pin, capsys, argv) -> None:
    before = _snapshot(dirs.tmp)
    assert _cli(argv, dirs) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err
    assert _snapshot(dirs.tmp) == before


def test_cli_bad_now_exit_two(dirs, pin, capsys) -> None:
    code = main(
        ["--now", "yesterday", "--reports-dir", str(dirs.reports)],
        env=IDLE_ENV,
        config=_config(enabled=False),
    )
    assert code == 2
    assert "--now" in capsys.readouterr().err


def test_cli_help_exits_zero(capsys) -> None:
    assert main(["--help"]) == 0
    text = capsys.readouterr().out
    assert "--mode" in text and "--apply" in text and "--steps" in text
    assert "--force-new" in text and "--json" in text


def test_cli_steps_flag_selects_subset(dirs, pin, capsys) -> None:
    assert _cli(["--steps", "engagement, preflight", "--json"], dirs) == 0
    payload = json.loads(capsys.readouterr().out)
    statuses = {s["name"]: s["reason"] for s in payload["steps"]}
    assert statuses["findings-plan"] == "not-selected"
    assert statuses["preflight"] == "ok"


def test_cli_output_never_contains_secrets(dirs, pin, capsys) -> None:
    _seed_release_state(dirs, "v1.0.0")
    env = {**IDLE_ENV, "OMEGA_PRIME_REDTEAM_ROE_KEY": SECRET}
    config = _live_apply_config(dirs, enabled=False)
    common: dict[str, Any] = {
        "now": False,
        "env": env,
        "config": config,
        "fetch": Fetch(TOKEN_TAG, "v1.0.0"),
    }
    _cli(["--mode", "apply", "--json"], dirs, **common)
    _cli(["--mode", "apply"], dirs, **common)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert TOKEN_TAG not in captured.out + captured.err


# --------------------------------------------------------------------------
# batch 18 integration fixes: real tick keys, RoE conflicts, real digest


def _seed_low_findings(dirs: SimpleNamespace, count: int) -> None:
    dirs.reports.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        record_finding(
            directory=dirs.reports,
            case_id=f"abuse-{index:02d}",
            klass="abuse",
            severity="low",
            observed_detection="no-detection",
            verdict="miss",
            response=f"response {index}",
        )


def test_findings_plan_reports_real_budget_held_count(dirs, pin) -> None:
    _seed_low_findings(dirs, 3)
    rec = _by_name(
        _cycle(dirs, mode="plan", config=_sync_config(sync_budget=1)),
    )["findings-plan"]
    assert rec["status"] == "ok"
    assert rec["detail"] == {
        "would_sync": 1,
        "already_synced": 0,
        "quiet_held": 0,
        "grant_conflicts": False,
        "dry_run": True,
        "budget_held": 2,
    }
    assert "budget_skipped" not in rec["detail"]


def test_findings_plan_detail_has_no_budget_key_when_unset(dirs, pin) -> None:
    _seed_low_findings(dirs, 2)
    rec = _by_name(_cycle(dirs, mode="plan", config=_sync_config()))["findings-plan"]
    assert rec["detail"] == {
        "would_sync": 2,
        "already_synced": 0,
        "quiet_held": 0,
        "grant_conflicts": False,
        "dry_run": True,
    }


def test_findings_plan_counts_real_quiet_hours_held_rows(dirs, pin) -> None:
    _seed_low_findings(dirs, 2)
    quiet = {"start": "00:00", "end": "23:59"}
    rec = _by_name(
        _cycle(dirs, mode="plan", config=_sync_config(quiet_hours=quiet)),
    )["findings-plan"]
    assert rec["detail"]["quiet_held"] == 2
    assert rec["detail"]["would_sync"] == 0


def _two_roe_files(dirs, *, operators: tuple[str, str]) -> list[str]:
    paths = []
    for index, operator in enumerate(operators):
        path = dirs.tmp / f"roe-{index}.json"
        _roe_artifact(path, until=NOW + timedelta(days=30))
        document = json.loads(path.read_text(encoding="utf-8"))
        document["operator"] = operator
        document["artifact_id"] = f"grant-{index}"
        path.write_text(json.dumps(document), encoding="utf-8")
        paths.append(str(path))
    return paths


def test_preflight_warns_when_roe_grants_conflict(dirs, pin) -> None:
    paths = _two_roe_files(dirs, operators=("alice", "bob"))
    config = {"redteam": {"roe_files": paths, "findings_sync": {}}}
    pre = _by_name(_cycle(dirs, mode="plan", config=config))["preflight"]
    assert (pre["status"], pre["reason"]) == ("warn", "roe-conflicts")
    assert pre["exit"] == 0
    assert pre["detail"]["roe_conflicts"] == 1
    assert pre["detail"]["roe_state"] == "valid"
    assert pre["detail"]["roe_artifacts"] == 2


def test_preflight_roe_conflicts_zero_for_agreeing_grants(dirs, pin) -> None:
    paths = _two_roe_files(dirs, operators=("alice", "alice"))
    config = {"redteam": {"roe_files": paths, "findings_sync": {}}}
    pre = _by_name(_cycle(dirs, mode="plan", config=config))["preflight"]
    assert (pre["status"], pre["reason"]) == ("ok", "ok")
    assert pre["detail"]["roe_conflicts"] == 0


def test_preflight_has_no_conflict_key_below_two_grants(dirs, pin) -> None:
    artifact = _roe_artifact(dirs.tmp / "roe.json", until=NOW + timedelta(days=30))
    config = {"redteam": {"roe_files": [artifact], "findings_sync": {}}}
    pre = _by_name(_cycle(dirs, mode="plan", config=config))["preflight"]
    assert "roe_conflicts" not in pre["detail"]
    none = _by_name(_cycle(dirs, mode="plan"))["preflight"]
    assert "roe_conflicts" not in none["detail"]


def test_preflight_conflict_detector_failure_is_fail_open(
    dirs, pin, monkeypatch
) -> None:
    from omega_prime.grokbot import roe_lifecycle

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(roe_lifecycle, "grant_conflicts", boom)
    paths = _two_roe_files(dirs, operators=("alice", "bob"))
    config = {"redteam": {"roe_files": paths, "findings_sync": {}}}
    pre = _by_name(_cycle(dirs, mode="plan", config=config))["preflight"]
    assert (pre["status"], pre["reason"]) == ("ok", "ok")
    assert pre["detail"]["roe_conflicts"] == 0


def test_roe_conflicts_never_change_apply_gating(dirs, pin) -> None:
    paths = _two_roe_files(dirs, operators=("alice", "bob"))
    config = {"redteam": {"roe_files": paths, "findings_sync": {"enabled": False}}}
    result = _cycle(dirs, mode="apply", config=config)
    by_name = _by_name(result)
    assert by_name["preflight"]["reason"] == "roe-conflicts"
    assert by_name["findings-dispatch"]["status"] != "blocked"
    assert (result["ok"], result["exit"], result["blocked"]) == (True, 0, None)


def _seed_history(reports: Path) -> None:
    from sidecar.simulation import load_suite

    reports.mkdir(parents=True, exist_ok=True)
    cases, errors = load_suite()
    assert not errors
    observations = {}
    for case in cases:
        expected = case.get("expected")
        detection = expected.get("detection") if isinstance(expected, dict) else ""
        observations[str(case["case_id"])] = {
            "observed_detection": str(detection),
            "response": "loopback-seam",
        }
    (reports / "last-run.json").write_text(
        json.dumps({"suite": "smoke", "observations": observations}) + "\n",
        encoding="utf-8",
    )


def test_engagement_step_uses_the_real_build_digest(dirs, pin) -> None:
    _seed_history(dirs.reports)
    before = _snapshot(dirs.tmp)
    rec = _by_name(_cycle(dirs, mode="plan"))["engagement"]
    assert _snapshot(dirs.tmp) == before
    assert (rec["status"], rec["reason"]) == ("ok", "ok")
    assert "digest_error" not in rec["detail"]
    digest = rec["detail"]["digest"]
    assert len(digest) == 3
    assert digest[0] == rec["detail"]["line"]
    assert digest[1].startswith("coverage covered=")
    assert digest[2].startswith("worst=")


def test_engagement_step_real_digest_without_history(dirs, pin) -> None:
    rec = _by_name(_cycle(dirs, mode="plan"))["engagement"]
    assert rec["status"] == "ok"
    assert rec["detail"]["state"] == "no-reports"
    assert rec["detail"]["digest"] == [
        "runs=0 pass=0% gaps=0 findings=+0/-0",
        "coverage covered=0/0 gappy=0 unexercised=0 risk=0.0",
        "worst=none",
    ]


# --------------------------------------------------------------------------
# batch 18 review fixes (FX-5a)


def _seed_one_finding(dirs: SimpleNamespace) -> None:
    dirs.reports.mkdir(parents=True, exist_ok=True)
    record_finding(
        directory=dirs.reports,
        case_id="abuse-01",
        klass="prompt_injection",
        severity="high",
        observed_detection="no-detection",
        verdict="miss",
        response="x",
    )


def _boom() -> Any:
    raise RuntimeError("upstream down")


def _good_records(count: int) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "status": "ok",
            "exit": 0,
            "reason": "ok",
            "detail": {"harness_state": "IDLE", "roe_state": "valid"},
        }
        for name in STEP_NAMES[:count]
    ]


def _write_checkpoint(
    dirs: SimpleNamespace, cycle_id: str, document: Any, *, raw: str | None = None
) -> None:
    target = _checkpoint_file(dirs, cycle_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw if raw is not None else json.dumps(document))


def _stored(cycle_id: str, mode: str, **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "cycle_id": cycle_id,
        "mode": mode,
        "now": "2026-10-14T12:30:45+00:00",
        "selected": list(STEP_NAMES),
        "steps": _good_records(1),
        "blocked": None,
    }
    state.update(overrides)
    return {"done": len(state["steps"]), "state": state}


# --- 1. apply gate is fresh on every pass -----------------------------------


def test_resume_reevaluates_roe_expiry_and_never_dispatches(dirs, pin) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs, until=NOW + timedelta(hours=2))
    runner = Runner()
    first = _cycle(dirs, mode="apply", config=config, runner=runner, fetch=_boom)
    assert first["state"] == "failed" and runner.calls == []
    # Same UTC day, six hours later: the RoE window has closed.
    later = _cycle(
        dirs,
        mode="apply",
        config=config,
        runner=runner,
        now=NOW + timedelta(hours=6),
    )
    assert later["resumed"] is True
    assert (later["exit"], later["state"]) == (3, "blocked")
    assert later["blocked"] == {"cause": "expired", "step": "findings-dispatch"}
    assert runner.calls == []
    assert "synced_at" not in load_findings(dirs.reports)[0]


def test_resume_reevaluates_kill_switch_and_never_dispatches(dirs, pin) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs)
    runner = Runner()
    first = _cycle(dirs, mode="apply", config=config, runner=runner, fetch=_boom)
    assert first["state"] == "failed"
    second = _cycle(dirs, mode="apply", config=config, runner=runner, env=KILLED_ENV)
    assert second["resumed"] is True
    assert second["blocked"] == {
        "cause": "kill-switch-set",
        "step": "findings-dispatch",
    }
    assert runner.calls == []


def test_fabricated_checkpoint_facts_do_not_bypass_the_live_gate(dirs, pin) -> None:
    _seed_one_finding(dirs)
    artifact = _roe_artifact(dirs.tmp / "roe-old.json", until=NOW - timedelta(days=1))
    config = {
        "redteam": {
            "roe_file": artifact,
            "findings_sync": {"enabled": True, "repo": REPO, "labels": {}},
        }
    }
    cycle_id = "purple-cycle-20261014-apply"
    stored = _stored(cycle_id, "apply", steps=_good_records(5))
    _write_checkpoint(dirs, cycle_id, stored)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert result["resumed"] is True
    assert result["blocked"] == {"cause": "expired", "step": "findings-dispatch"}
    assert runner.calls == []


# --- 2. apply gate hardening -------------------------------------------------


def test_apply_refused_when_no_roe_is_configured(dirs, pin) -> None:
    _seed_one_finding(dirs)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=_sync_config(), runner=runner)
    assert runner.calls == []
    assert (result["exit"], result["state"]) == (3, "blocked")
    assert result["blocked"] == {"cause": "roe-missing", "step": "findings-dispatch"}
    # Plan and run stay informational: no RoE is only a harmless "none".
    for mode in ("plan", "run"):
        other = _cycle(dirs, mode=mode, config=_sync_config(), runner=runner)
        assert other["state"] == "completed" and other["exit"] == 0
        assert _by_name(other)["preflight"]["detail"]["roe_state"] == "none"
    assert runner.calls == []


def test_apply_refused_when_configured_roe_is_revoked(dirs, pin) -> None:
    _seed_one_finding(dirs)
    artifact = _roe_artifact(dirs.tmp / "roe-r.json", until=NOW + timedelta(days=30))
    log = dirs.tmp / "audit.jsonl"
    roe_audit.revoke_artifact(artifact, audit_log=str(log), detail="compromised")
    log_before = log.read_bytes()
    config = {
        "redteam": {
            "roe_file": artifact,
            "roe_audit_log": str(log),
            "findings_sync": {"enabled": True, "repo": REPO, "labels": {}},
        }
    }
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": "revoked", "step": "findings-dispatch"}
    pre = _by_name(result)["preflight"]
    assert (pre["status"], pre["reason"]) == ("warn", "roe-revoked")
    assert log.read_bytes() == log_before  # read-only
    # Without the audit log configured the same artifact is not seen revoked.
    del config["redteam"]["roe_audit_log"]
    unaware = _cycle(dirs, mode="apply", config=config, runner=runner, force_new=True)
    assert unaware["state"] == "completed"
    assert len(runner.calls) == 1


def test_apply_ignores_other_artifacts_revocation(dirs, pin) -> None:
    _seed_one_finding(dirs)
    other = _roe_artifact(dirs.tmp / "other.json", until=NOW + timedelta(days=30))
    log = dirs.tmp / "audit.jsonl"
    roe_audit.revoke_artifact(other, audit_log=str(log))
    config = _apply_sync(dirs)
    config["redteam"]["roe_audit_log"] = str(log)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert result["state"] == "completed" and len(runner.calls) == 1


# --- 3. blocked is not checkpointed ------------------------------------------


def test_blocked_apply_is_retried_after_the_kill_switch_clears(dirs, pin) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs)
    runner = Runner()
    blocked = _cycle(dirs, mode="apply", config=config, runner=runner, env=KILLED_ENV)
    assert blocked["state"] == "blocked" and runner.calls == []
    stored = json.loads(_checkpoint_file(dirs, blocked["cycle_id"]).read_text())
    assert stored["done"] == len(STEP_NAMES) - 1
    assert all(rec["status"] != "blocked" for rec in stored["state"]["steps"])
    retried = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert retried["resumed"] is True
    assert (retried["exit"], retried["state"]) == (0, "completed")
    assert len(runner.calls) == 1
    assert _by_name(retried)["findings-dispatch"]["status"] == "ok"
    # The now-complete cycle returns its stored result with zero executions.
    again = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert again == retried
    assert len(runner.calls) == 1


# --- 4. cycle identity and checkpoint validation ------------------------------


def test_default_selection_keeps_the_plain_cycle_id(dirs, pin) -> None:
    explicit = _cycle(dirs, mode="plan", steps=list(reversed(STEP_NAMES)))
    assert explicit["cycle_id"] == "purple-cycle-20261014-plan"


def test_subset_selection_gets_a_stable_short_token(dirs, pin) -> None:
    one = _cycle(dirs, mode="plan", steps=["engagement", "preflight"])
    two = _cycle(dirs, mode="plan", steps=["preflight", "engagement"])
    other = _cycle(dirs, mode="plan", steps=["preflight"])
    assert one["cycle_id"] == two["cycle_id"]
    assert one["cycle_id"] != other["cycle_id"]
    for result in (one, other):
        head, _, token = result["cycle_id"].rpartition("-s")
        assert head == "purple-cycle-20261014-plan"
        assert len(token) == 8 and all(ch in "0123456789abcdef" for ch in token)
    forced = _cycle(dirs, mode="plan", steps=["preflight"], force_new=True)
    assert forced["cycle_id"] == other["cycle_id"] + "-123045"


def test_steps_subset_no_longer_poisons_the_days_full_cycle(dirs, pin) -> None:
    fetch = Fetch("v1.0.0")
    subset = _cycle(dirs, steps=["preflight"], fetch=fetch)
    assert subset["state"] == "completed" and fetch.calls == 0
    full = _cycle(dirs, fetch=fetch)
    assert full["resumed"] is False
    assert fetch.calls == 1
    assert _by_name(full)["release-watch"]["reason"] == "seeded"
    assert full["cycle_id"] != subset["cycle_id"]


_RUN_ID = "purple-cycle-20261014-run"


def _bad_checkpoints() -> dict[str, Any]:
    ok_steps = _good_records(1)
    return {
        "all-bare-records": {
            "done": 6,
            "state": {"steps": [{"name": "preflight"}] * 6},
        },
        "blocked-is-a-string": _stored(_RUN_ID, "run", blocked="x"),
        "blocked-missing-keys": _stored(_RUN_ID, "run", blocked={"cause": 1}),
        "wrong-mode": _stored(_RUN_ID, "plan"),
        "wrong-cycle-id": _stored("purple-cycle-other-run", "run"),
        "wrong-selected": _stored(_RUN_ID, "run", selected=["preflight"]),
        "selected-missing": _stored(_RUN_ID, "run", selected=None),
        "unknown-status": _stored(
            _RUN_ID, "run", steps=[{**ok_steps[0], "status": "weird"}]
        ),
        "exit-not-int": _stored(_RUN_ID, "run", steps=[{**ok_steps[0], "exit": "0"}]),
        "exit-bool": _stored(_RUN_ID, "run", steps=[{**ok_steps[0], "exit": True}]),
        "reason-not-str": _stored(
            _RUN_ID, "run", steps=[{**ok_steps[0], "reason": None}]
        ),
        "detail-not-dict": _stored(
            _RUN_ID, "run", steps=[{**ok_steps[0], "detail": []}]
        ),
        "detail-missing": _stored(
            _RUN_ID,
            "run",
            steps=[{k: v for k, v in ok_steps[0].items() if k != "detail"}],
        ),
        "steps-out-of-order": _stored(
            _RUN_ID, "run", steps=[{**ok_steps[0], "name": "engagement"}]
        ),
    }


@pytest.mark.parametrize("label", sorted(_bad_checkpoints()))
def test_malformed_or_foreign_checkpoint_is_unreadable_not_an_exception(
    dirs, pin, counters, label: str
) -> None:
    _write_checkpoint(dirs, _RUN_ID, _bad_checkpoints()[label])
    result = _cycle(dirs, mode="run")
    assert (result["ok"], result["exit"], result["state"]) == (False, 1, "failed")
    first = result["steps"][0]
    assert (first["status"], first["reason"]) == ("error", "checkpoint-unreadable")
    assert all(count == 0 for count in counters.values())


def test_well_formed_checkpoint_still_resumes(dirs, pin, counters) -> None:
    _write_checkpoint(dirs, _RUN_ID, _stored(_RUN_ID, "run"))
    result = _cycle(dirs, mode="run")
    assert result["resumed"] is True and result["state"] == "completed"
    assert counters["preflight"] == 0 and counters["release-watch"] == 1


# --- 5. lost audits -----------------------------------------------------------


def test_errored_audit_tag_is_audited_by_a_later_cycle(dirs, pin) -> None:
    _seed_release_state(dirs, "v1.0.0")
    pin.verdict = "error"
    first = _cycle(dirs, fetch=Fetch("v1.1.0", "v1.0.0"))
    audit = _by_name(first)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("warn", "audit-failed")
    pending = dirs.reports / "purple-cycle-pending.json"
    assert json.loads(pending.read_text(encoding="utf-8")) == {
        "attempts": 1,
        "tag": "v1.1.0",
    }

    pin.verdict = "unchanged"
    nxt = _cycle(dirs, now=NOW + timedelta(days=1), fetch=Fetch("v1.1.0", "v1.0.0"))
    assert _by_name(nxt)["release-watch"]["reason"] == "no-new-release"
    audit = _by_name(nxt)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("ok", "ok")
    assert audit["detail"]["tag"] == "v1.1.0"
    assert [call["config"] for call in pin.calls] == [{"to": "v1.1.0"}] * 2
    assert not pending.exists()

    third = _cycle(dirs, now=NOW + timedelta(days=2), fetch=Fetch("v1.1.0", "v1.0.0"))
    assert _by_name(third)["pin-bump-audit"]["reason"] == "no-new-release"
    assert len(pin.calls) == 2


def test_audit_exception_also_records_the_pending_tag(
    dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exploding(config: Any, *, root: Any = None, run: Any = None) -> Any:
        raise RuntimeError("audit crashed")

    monkeypatch.setattr(pin_bump, "audit_scheduled_job", exploding)
    _seed_release_state(dirs, "v1.0.0")
    result = _cycle(dirs, fetch=Fetch("v1.1.0", "v1.0.0"))
    audit = _by_name(result)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("warn", "audit-failed")
    assert audit["detail"]["error_type"] == "RuntimeError"
    pending = dirs.reports / "purple-cycle-pending.json"
    assert json.loads(pending.read_text(encoding="utf-8")) == {
        "attempts": 1,
        "tag": "v1.1.0",
    }
    assert list(dirs.reports.glob(".purple-cycle-pending.json.*")) == []


def test_pending_tag_is_never_written_in_plan_mode(dirs, pin) -> None:
    dirs.reports.mkdir(parents=True)
    (dirs.reports / "purple-cycle-pending.json").write_text('{"tag": "v1.1.0"}')
    before = _snapshot(dirs.tmp)
    result = _cycle(dirs, mode="plan")
    assert _by_name(result)["pin-bump-audit"]["reason"] == "plan-mode"
    assert _snapshot(dirs.tmp) == before
    assert pin.calls == []


@pytest.mark.parametrize(
    "content", ["{nope", "[]", '{"tag": "bad tag\n"}', '{"tag": 3}']
)
def test_unusable_pending_file_is_ignored(dirs, pin, content: str) -> None:
    dirs.reports.mkdir(parents=True)
    (dirs.reports / "purple-cycle-pending.json").write_text(content)
    result = _cycle(dirs, fetch=Fetch("v1.0.0"))
    assert _by_name(result)["pin-bump-audit"]["reason"] == "no-new-release"
    assert pin.calls == []


# --- 6. durable location ------------------------------------------------------


def test_checkpoint_lives_under_reports_not_root_or_repo(dirs, pin) -> None:
    result = _cycle(dirs)
    expected = _checkpoint_file(dirs, result["cycle_id"])
    assert expected.is_file()
    assert result["checkpoint"] == str(expected)
    assert not dirs.root.exists()


def test_checkpoint_is_not_placed_in_the_state_dir_or_repo(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = dirs.tmp / "state"
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(state))
    env = {**IDLE_ENV, "OMEGA_PRIME_STATE_DIR": str(state)}
    result = _cycle(dirs, root=None, env=env)
    assert result["checkpoint"].startswith(str(dirs.reports))
    assert not state.exists()
    repo_workflows = Path(purple_cycle.__file__).resolve().parents[2] / "workflows"
    assert not (repo_workflows / f"{result['cycle_id']}.json").exists()


# --- 8. CLI: --now and abbreviations ------------------------------------------


@pytest.mark.parametrize(
    "argv", [["--apply"], ["--mode", "apply"], ["--apply", "--json"]]
)
def test_cli_rejects_now_with_apply_mode(dirs, pin, capsys, argv) -> None:
    before = _snapshot(dirs.tmp)
    assert _cli(argv, dirs) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "--now" in captured.err
    assert _snapshot(dirs.tmp) == before


def test_cli_now_is_still_fine_outside_apply_mode(dirs, pin, capsys) -> None:
    assert _cli(["--mode", "run", "--json"], dirs) == 0
    assert json.loads(capsys.readouterr().out)["cycle_id"].startswith(
        "purple-cycle-20261014-run"
    )


@pytest.mark.parametrize("argv", [["--ap"], ["--mod", "apply"], ["--ste", "preflight"]])
def test_cli_rejects_abbreviated_options(dirs, pin, capsys, argv) -> None:
    before = _snapshot(dirs.tmp)
    assert _cli(argv, dirs, now=False) == 2
    assert capsys.readouterr().out == ""
    assert _snapshot(dirs.tmp) == before


# --- 9. regex validators ------------------------------------------------------


@pytest.mark.parametrize("tag", ["1.2\n", "v1.2.3\n", "v\u0661.\u0662.\u0663", "v1.2 "])
def test_safe_tag_rejects_newline_and_unicode_digits(tag: str) -> None:
    assert purple_cycle._safe_tag(tag) is None


def test_safe_tag_accepts_tags_pin_bump_accepts() -> None:
    for tag in ("v1.2.3", "v1.2", "v1.2.3-rc.1", "v10.20.30-build.5", "v1.2.3_x"):
        assert purple_cycle._safe_tag(tag) == tag
        assert pin_bump.TAG_RE.match(tag)


@pytest.mark.parametrize("tag", ["1.2", "v2.0.0-rc.1+build", "v1", "v1.2.3+x"])
def test_safe_tag_rejects_tags_pin_bump_would_reject(tag: str) -> None:
    assert purple_cycle._safe_tag(tag) is None


def test_word_rejects_trailing_newline() -> None:
    assert purple_cycle._word("abc\n") == "unknown"
    assert purple_cycle._word("abc\n", "dflt") == "dflt"
    assert purple_cycle._word("abc-1.2_x") == "abc-1.2_x"


# --- 10. engagement script import never writes bytecode ------------------------


def test_engagement_script_is_loaded_without_writing_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "engagement-report.py").write_text(
        "import sys\nSEEN = sys.dont_write_bytecode\n", encoding="utf-8"
    )
    monkeypatch.setattr(purple_cycle, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(purple_cycle, "_ENGAGEMENT_MODULE", None)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    module = purple_cycle._load_engagement_module()
    assert module.SEEN is True
    assert sys.dont_write_bytecode is False


def test_engagement_script_load_restores_flag_on_import_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "engagement-report.py").write_text("raise RuntimeError('x')\n")
    monkeypatch.setattr(purple_cycle, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(purple_cycle, "_ENGAGEMENT_MODULE", None)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    with pytest.raises(RuntimeError):
        purple_cycle._load_engagement_module()
    assert sys.dont_write_bytecode is False


# --- 11. default clock seam ---------------------------------------------------


def test_default_now_comes_from_the_utc_now_seam(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        purple_cycle, "_utc_now", lambda: datetime(2031, 2, 3, 4, 5, 6, 789, tzinfo=UTC)
    )
    result = run_cycle(
        mode="plan",
        reports_dir=dirs.reports,
        config=_config(enabled=False),
        env=IDLE_ENV,
    )
    assert result["cycle_id"] == "purple-cycle-20310203-plan"


def test_explicit_now_never_reads_the_seam(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> datetime:
        raise AssertionError("explicit now must not read the clock")

    monkeypatch.setattr(purple_cycle, "_utc_now", forbidden)
    assert _cycle(dirs, mode="plan")["cycle_id"] == "purple-cycle-20261014-plan"


# --- 12. round 2: failing audits never starve apply ---------------------------


def _add_finding(dirs: SimpleNamespace, number: int) -> None:
    dirs.reports.mkdir(parents=True, exist_ok=True)
    record_finding(
        directory=dirs.reports,
        case_id=f"abuse-{number:02d}",
        klass="prompt_injection",
        severity="high",
        observed_detection="no-detection",
        verdict="miss",
        response=f"r{number}",
    )


def test_persistently_failing_audit_never_starves_apply(dirs, pin) -> None:
    _seed_release_state(dirs, "v1.0.0")
    pin.verdict = "error"
    config = _apply_sync(dirs)
    runner = Runner()
    outcomes: list[tuple[str, str, int]] = []
    for day in range(4):
        _add_finding(dirs, day)
        result = _cycle(
            dirs,
            mode="apply",
            config=config,
            runner=runner,
            now=NOW + timedelta(days=day),
            fetch=Fetch("v1.1.0", "v1.0.0"),
        )
        audit = _by_name(result)["pin-bump-audit"]
        outcomes.append((audit["status"], audit["reason"], len(runner.calls)))
        assert (result["ok"], result["exit"], result["state"]) == (True, 0, "completed")
        assert _by_name(result)["findings-dispatch"]["reason"] == "ok"
    assert outcomes == [
        ("warn", "audit-failed", 1),
        ("warn", "audit-failed", 2),
        ("warn", "audit-abandoned", 3),
        ("skipped", "no-new-release", 4),
    ]
    # The abandoned tag is not audited again and the pending file is gone.
    assert len(pin.calls) == 3
    assert not (dirs.reports / "purple-cycle-pending.json").exists()


def test_audit_failure_detail_is_type_only_and_secret_free(
    dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exploding(config: Any, *, root: Any = None, run: Any = None) -> Any:
        raise RuntimeError(f"{SECRET} /root/.ssh")

    monkeypatch.setattr(pin_bump, "audit_scheduled_job", exploding)
    _seed_release_state(dirs, "v1.0.0")
    result = _cycle(dirs, fetch=Fetch("v1.1.0", "v1.0.0"))
    audit = _by_name(result)["pin-bump-audit"]
    assert audit["detail"] == {
        "tag": "v1.1.0",
        "error_type": "RuntimeError",
        "attempts": 1,
    }
    ledger = (dirs.reports / "purple-cycle.ndjson").read_text(encoding="utf-8")
    assert SECRET not in json.dumps(result) + ledger


@pytest.mark.parametrize(
    ("content", "expected_calls"),
    [
        ('{"tag": "v1.1.0"}', 1),
        ('{"tag": "v1.1.0", "attempts": 2}', 1),
        ('{"tag": "v1.1.0", "attempts": 3}', 0),
        ('{"tag": "v1.1.0", "attempts": 99}', 0),
        ('{"tag": "v1.1.0", "attempts": true}', 1),
        ('{"tag": "v1.1.0", "attempts": "x"}', 1),
        ('{"tag": "v1.1.0", "attempts": -4}', 1),
    ],
)
def test_pending_attempts_are_bounded_and_forgiving(
    dirs, pin, content: str, expected_calls: int
) -> None:
    dirs.reports.mkdir(parents=True)
    (dirs.reports / "purple-cycle-pending.json").write_text(content)
    _cycle(dirs, fetch=Fetch("v1.0.0"))
    assert len(pin.calls) == expected_calls


def test_tag_regex_is_linear_time_on_adversarial_input() -> None:
    hostile = [
        "v1.1-" + ".".join("a" * 26) + "!",
        "v1.1-" + ".".join("a" * 26) + "_",
        "v1." + "1." * 28 + "!",
        "v1.1" + ".a" * 29 + "+",
    ]
    started = time.perf_counter()
    for text in hostile:
        assert len(text) <= 64
        purple_cycle._safe_tag(text)
    assert time.perf_counter() - started < 0.1


# --- 13. round 2: the pending tag is written before the checkpoint ------------


def _fail_checkpoint_at(monkeypatch: pytest.MonkeyPatch, done: int) -> Any:
    original = workflows._write

    def failing(directory: Any, workflow_id: str, document: dict[str, Any]) -> None:
        if document["done"] == done:
            raise OSError("simulated crash")
        original(directory, workflow_id, document)

    monkeypatch.setattr(workflows, "_write", failing)
    return original


def test_new_tag_survives_a_crash_before_the_release_watch_checkpoint(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_release_state(dirs, "v1.0.0")
    original = _fail_checkpoint_at(monkeypatch, 2)
    first = _cycle(dirs, fetch=Fetch("v1.1.0", "v1.0.0"))
    failed = [rec for rec in first["steps"] if rec["status"] == "error"]
    assert [(r["name"], r["reason"]) for r in failed] == [
        ("release-watch", "checkpoint-error")
    ]
    pending = dirs.reports / "purple-cycle-pending.json"
    assert json.loads(pending.read_text(encoding="utf-8")) == {
        "attempts": 0,
        "tag": "v1.1.0",
    }
    assert pin.calls == []

    monkeypatch.setattr(workflows, "_write", original)
    second = _cycle(
        dirs, now=NOW + timedelta(minutes=1), fetch=Fetch("v1.1.0", "v1.0.0")
    )
    assert _by_name(second)["release-watch"]["reason"] == "no-new-release"
    audit = _by_name(second)["pin-bump-audit"]
    assert (audit["status"], audit["detail"]["tag"]) == ("ok", "v1.1.0")
    assert [call["config"] for call in pin.calls] == [{"to": "v1.1.0"}]
    assert not pending.exists()


def test_seeding_run_writes_no_pending_tag(dirs, pin) -> None:
    _cycle(dirs, fetch=Fetch("v1.0.0"))
    assert not (dirs.reports / "purple-cycle-pending.json").exists()


def test_checkpoint_failure_after_dispatch_names_the_step_that_ran(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs)
    runner = Runner()
    original = _fail_checkpoint_at(monkeypatch, len(STEP_NAMES))
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert len(runner.calls) == 1
    failed = [rec for rec in result["steps"] if rec["status"] == "error"]
    assert [(r["name"], r["reason"]) for r in failed] == [
        ("findings-dispatch", "checkpoint-error")
    ]
    monkeypatch.setattr(workflows, "_write", original)
    again = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert again["state"] == "completed"
    assert len(runner.calls) == 1  # the rerun dispatches nothing twice


# --- 14. round 2: revocation cannot be dodged by path aliasing ----------------


def _revoked_config(
    dirs: SimpleNamespace,
    *,
    log: Path,
    scope: str = "redteam",
    key: str = "roe_audit_log",
) -> dict[str, Any]:
    config = _apply_sync(dirs)
    if scope == "redteam":
        config["redteam"][key] = str(log)
    elif scope == "findings_sync":
        config["redteam"]["findings_sync"][key] = str(log)
    else:
        config[key] = str(log)
    return config


def _alias_cases(dirs: SimpleNamespace) -> dict[str, tuple[str, str | None]]:
    """``label -> (audit row path, configured path override)``."""
    real = str(dirs.tmp / "roe-valid.json")
    symlink = dirs.tmp / "link.json"
    hardlink = dirs.tmp / "hard.json"
    symlink.symlink_to(real)
    os.link(real, hardlink)
    return {
        "relative-row": ("roe-valid.json", None),
        "dot-row": ("./roe-valid.json", None),
        "dotdot-row": ("sub/../roe-valid.json", None),
        "trailing-space-row": (real + " ", None),
        "symlink-row": (str(symlink), None),
        "hardlink-row": (str(hardlink), None),
        "relative-config-absolute-row": (real, "roe-valid.json"),
        "dot-config-plain-row": ("roe-valid.json", "./roe-valid.json"),
        "symlink-config-real-row": (real, str(symlink)),
        "hardlink-config-real-row": (real, str(hardlink)),
    }


@pytest.mark.parametrize(
    "label",
    [
        "relative-row",
        "dot-row",
        "dotdot-row",
        "trailing-space-row",
        "symlink-row",
        "hardlink-row",
        "relative-config-absolute-row",
        "dot-config-plain-row",
        "symlink-config-real-row",
        "hardlink-config-real-row",
    ],
)
def test_apply_refused_for_every_path_alias_of_a_revoked_grant(
    dirs, pin, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs)
    cases = _alias_cases(dirs)
    row_path, configured = cases[label]
    log = dirs.tmp / "audit.jsonl"
    roe_audit.log_revoked(log, row_path, "op", "compromised")
    config["redteam"]["roe_audit_log"] = str(log)
    if configured is not None:
        config["redteam"]["roe_file"] = configured
    monkeypatch.chdir(dirs.tmp)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": "revoked", "step": "findings-dispatch"}


@pytest.mark.parametrize(
    ("scope", "key"),
    [
        ("redteam", "audit_log"),
        ("redteam", "roe_audit_log_path"),
        ("findings_sync", "roe_audit_log"),
        ("findings_sync", "audit_log"),
        ("top", "roe_audit_log"),
        ("top", "audit_log"),
    ],
)
def test_audit_log_is_found_under_every_scope_the_code_base_reads(
    dirs, pin, scope: str, key: str
) -> None:
    _seed_one_finding(dirs)
    log = dirs.tmp / "audit.jsonl"
    roe_audit.log_revoked(log, str(dirs.tmp / "roe-valid.json"), "op", "x")
    config = _revoked_config(dirs, log=log, scope=scope, key=key)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": "revoked", "step": "findings-dispatch"}


def test_unrelated_relative_revocation_row_does_not_block(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_one_finding(dirs)
    log = dirs.tmp / "audit.jsonl"
    roe_audit.log_revoked(log, "someone-else.json", "op", "x")
    config = _revoked_config(dirs, log=log)
    monkeypatch.chdir(dirs.tmp)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert result["state"] == "completed" and len(runner.calls) == 1


# --- 15. round 2: config-level kill / disabled --------------------------------


@pytest.mark.parametrize(
    ("node", "cause"),
    [({"kill": True}, "kill-switch-set"), ({"enabled": False}, "disabled")],
)
def test_apply_refused_by_config_level_switches_with_an_idle_env(
    dirs, pin, node: dict[str, Any], cause: str
) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs)
    config["redteam"].update(node)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": cause, "step": "findings-dispatch"}
    pre = _by_name(result)["preflight"]
    assert (pre["reason"], pre["detail"]["harness_state"]) == (
        "harness-blocked",
        "BLOCKED",
    )


def test_config_enabled_true_and_absent_do_not_block(dirs, pin) -> None:
    for number, node in enumerate(({"enabled": True}, {})):
        _add_finding(dirs, number)
        config = _apply_sync(dirs)
        config["redteam"].update(node)
        runner = Runner()
        result = _cycle(
            dirs,
            mode="apply",
            config=config,
            runner=runner,
            now=NOW + timedelta(days=number),
        )
        assert result["state"] == "completed", node
        assert len(runner.calls) == 1


# --- 16. round 2: the gate covers the union of RoE artifacts -------------------


def _expired_artifact(dirs: SimpleNamespace) -> str:
    return _roe_artifact(dirs.tmp / "roe-old.json", until=NOW - timedelta(days=3))


@pytest.mark.parametrize(
    "where",
    [
        ("findings_sync", "roe_artifacts"),
        ("findings_sync", "roe_artifact"),
        ("findings_sync", "roe_artifact_paths"),
        ("redteam", "roe_artifacts"),
        ("redteam", "roe_artifact"),
    ],
)
def test_expired_grant_in_any_tick_key_blocks_apply(
    dirs, pin, where: tuple[str, str]
) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs)
    scope, key = where
    old = _expired_artifact(dirs)
    value: Any = old if key == "roe_artifact" else [old]
    if scope == "redteam":
        config["redteam"][key] = value
    else:
        config["redteam"]["findings_sync"][key] = value
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": "expired", "step": "findings-dispatch"}
    assert _by_name(result)["preflight"]["detail"]["roe_artifacts"] == 2


def test_inline_document_and_missing_artifact_in_tick_keys_block(dirs, pin) -> None:
    _seed_one_finding(dirs)
    old = json.loads(Path(_expired_artifact(dirs)).read_text(encoding="utf-8"))
    absent = str(dirs.tmp / "absent.json")
    for value, cause in (([old], "expired"), ([absent], "missing")):
        config = _apply_sync(dirs)
        config["redteam"]["findings_sync"]["roe_artifacts"] = value
        runner = Runner()
        result = _cycle(
            dirs, mode="apply", config=config, runner=runner, force_new=True
        )
        assert runner.calls == []
        assert result["blocked"]["cause"] == cause


def test_revoked_grant_named_only_by_a_tick_key_blocks_apply(dirs, pin) -> None:
    _seed_one_finding(dirs)
    extra = _roe_artifact(dirs.tmp / "roe-extra.json", until=NOW + timedelta(days=30))
    log = dirs.tmp / "audit.jsonl"
    roe_audit.log_revoked(log, extra, "op", "x")
    config = _revoked_config(dirs, log=log)
    config["redteam"]["findings_sync"]["roe_artifact"] = extra
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": "revoked", "step": "findings-dispatch"}


def test_roe_artifacts_alone_with_a_valid_grant_is_not_roe_missing(dirs, pin) -> None:
    _seed_one_finding(dirs)
    grant = _roe_artifact(dirs.tmp / "roe-only.json", until=NOW + timedelta(days=30))
    config = _sync_config()
    config["redteam"]["findings_sync"]["roe_artifacts"] = [grant]
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert result["state"] == "completed" and len(runner.calls) == 1
    assert _by_name(result)["preflight"]["detail"]["roe_state"] == "valid"


def test_duplicate_artifact_across_keys_counts_once(dirs, pin) -> None:
    config = _apply_sync(dirs)
    config["redteam"]["findings_sync"]["roe_artifacts"] = [
        config["redteam"]["roe_file"]
    ]
    pre = _by_name(_cycle(dirs, mode="plan", config=config))["preflight"]
    assert pre["detail"]["roe_artifacts"] == 1
    assert "roe_conflicts" not in pre["detail"]


# --- 17. round 2: forged checkpoints cannot raise or smuggle text --------------


def _mutate_first(**changes: Any) -> Any:
    def apply(stored: dict[str, Any]) -> None:
        stored["state"]["steps"][0].update(changes)

    return apply


def _mutate_detail(detail: dict[str, Any]) -> Any:
    return _mutate_first(detail=detail)


_FORGERIES: dict[str, Any] = {
    "status-list": _mutate_first(status=[1]),
    "status-dict": _mutate_first(status={"a": 1}),
    "status-none": _mutate_first(status=None),
    "exit-mismatch": _mutate_first(exit=3),
    "reason-esc": _mutate_first(reason="\x1b[31mred"),
    "reason-too-long": _mutate_first(reason="a" * 41),
    "reason-uppercase": _mutate_first(reason="Not-A-Word"),
    "reason-empty": _mutate_first(reason=""),
    "extra-key": _mutate_first(extra="smuggled"),
    "detail-huge-string": _mutate_detail({"x": "A" * 5_000_000}),
    "detail-esc": _mutate_detail({"line": "\x1b[31mred"}),
    "detail-secret-size": _mutate_detail({f"k{i}": "s" * 150 for i in range(40)}),
    "detail-nested-dict": _mutate_detail({"a": {"b": 1}}),
    "detail-bad-key": _mutate_detail({"Bad Key": 1}),
    "detail-long-list": _mutate_detail({"xs": list(range(65))}),
    "detail-list-of-lists": _mutate_detail({"xs": [[1]]}),
    "blocked-esc-cause": lambda stored: stored["state"].update(
        blocked={"cause": "\x1b[31mred", "step": "findings-dispatch"}
    ),
    "blocked-unknown-step": lambda stored: stored["state"].update(
        blocked={"cause": "kill-switch-set", "step": "nope"}
    ),
    "blocked-extra-key": lambda stored: stored["state"].update(
        blocked={"cause": "x", "step": "findings-dispatch", "extra": 1}
    ),
}


@pytest.mark.parametrize("label", sorted(_FORGERIES))
def test_forged_checkpoint_fields_are_unreadable_never_exceptions(
    dirs, pin, counters, label: str
) -> None:
    stored = _stored(_RUN_ID, "run")
    _FORGERIES[label](stored)
    _write_checkpoint(dirs, _RUN_ID, stored)
    result = _cycle(dirs, mode="run")
    assert (result["ok"], result["exit"], result["state"]) == (False, 1, "failed")
    first = result["steps"][0]
    assert (first["status"], first["reason"]) == ("error", "checkpoint-unreadable")
    serialized = json.dumps(result)
    assert "\x1b" not in serialized and "AAAA" not in serialized
    assert len(serialized) < 4096
    assert all(count == 0 for count in counters.values())


def test_generated_records_always_pass_read_back_validation(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = SimpleNamespace(
        summarize=lambda reports, since: {"state": "ok", "line": "x" * 300},
        build_digest=lambda reports: ["y" * 300] * 40,
    )
    monkeypatch.setattr(purple_cycle, "_load_engagement_module", lambda: module)
    first = _cycle(dirs)
    detail = _by_name(first)["engagement"]["detail"]
    assert len(json.dumps(detail, sort_keys=True)) <= 4096
    assert 0 < len(detail["digest"]) <= 20
    again = _cycle(dirs)
    assert again["resumed"] is True and again["state"] == "completed"


# --- 18. round 2: clock freshness and ledger noise -----------------------------


def test_default_clock_is_re_read_at_the_dispatch_gate(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs, until=NOW + timedelta(hours=1))
    reads: list[int] = []

    def clock() -> datetime:
        reads.append(1)
        return NOW if len(reads) == 1 else NOW + timedelta(hours=2)

    monkeypatch.setattr(purple_cycle, "_utc_now", clock)
    runner = Runner()
    result = run_cycle(
        mode="apply",
        root=dirs.root,
        reports_dir=dirs.reports,
        config=config,
        env=IDLE_ENV,
        fetch=Fetch("v1.0.0"),
        runner=runner,
    )
    assert runner.calls == []
    assert result["blocked"] == {"cause": "expired", "step": "findings-dispatch"}


def test_injected_now_stays_authoritative_at_the_gate(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_one_finding(dirs)
    config = _apply_sync(dirs, until=NOW + timedelta(hours=1))

    def forbidden() -> datetime:
        raise AssertionError("an injected now must not read the clock")

    monkeypatch.setattr(purple_cycle, "_utc_now", forbidden)
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert result["state"] == "completed" and len(runner.calls) == 1


def test_blocked_reruns_append_one_secret_free_ledger_row(dirs, pin) -> None:
    config = _sync_config()
    for _ in range(5):
        result = _cycle(dirs, mode="apply", env=KILLED_ENV, config=config)
        assert result["state"] == "blocked"
    blocked = [row for row in _ledger_rows(dirs) if row["status"] == "blocked"]
    assert [(row["step"], row["reason"]) for row in blocked] == [
        ("findings-dispatch", "apply-refused:kill-switch-set")
    ]
    assert set(blocked[0]) == {"ts", "cycle_id", "step", "status", "exit", "reason"}

    # A different cause is a different row; flipping back adds nothing new.
    disabled_env = {"OMEGA_PRIME_REDTEAM_ROE_REQUIRED": "0"}
    _cycle(dirs, mode="apply", env=disabled_env, config=config)
    _cycle(dirs, mode="apply", env=KILLED_ENV, config=config)
    reasons = [r["reason"] for r in _ledger_rows(dirs) if r["status"] == "blocked"]
    assert reasons == ["apply-refused:kill-switch-set", "apply-refused:disabled"]


def test_non_vocabulary_blocked_cause_is_not_written_to_the_ledger(dirs) -> None:
    ctx = SimpleNamespace(
        reports_dir=dirs.reports, now=NOW, cycle_id="purple-cycle-x-apply"
    )
    rec = purple_cycle._record(
        "findings-dispatch", "blocked", "apply-refused", {"cause": f"A B {SECRET}"}
    )
    purple_cycle._append_ledger(ctx, rec)  # type: ignore[arg-type]
    (row,) = _ledger_rows(dirs)
    assert row["reason"] == "apply-refused"
    assert SECRET not in json.dumps(row)


# --- 14. round 3: crash-safe release audits and schema-checked apply grants ---


def test_pending_tag_is_persisted_before_the_seen_state_is_saved(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_release_state(dirs, "v1.0.0")
    state_before = (dirs.reports / "release-watch.json").read_bytes()

    def failing(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated crash before the seen-state write")

    monkeypatch.setattr(release_watch, "_write_state", failing)
    first = _cycle(dirs, fetch=Fetch("v1.1.0", "v1.0.0"))
    assert _by_name(first)["release-watch"]["reason"] == "step-error"
    pending = dirs.reports / "purple-cycle-pending.json"
    assert json.loads(pending.read_text(encoding="utf-8")) == {
        "attempts": 0,
        "tag": "v1.1.0",
    }
    assert (dirs.reports / "release-watch.json").read_bytes() == state_before
    assert pin.calls == []


def test_crash_right_after_the_seen_state_write_cannot_lose_the_audit(
    dirs, pin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_release_state(dirs, "v1.0.0")
    original = release_watch._write_state

    def write_then_crash(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise OSError("simulated crash between the two writes")

    monkeypatch.setattr(release_watch, "_write_state", write_then_crash)
    first = _cycle(dirs, fetch=Fetch("v1.1.0", "v1.0.0"))
    assert _by_name(first)["release-watch"]["reason"] == "step-error"
    stored = json.loads((dirs.reports / "release-watch.json").read_text("utf-8"))
    assert "v1.1.0" in [pair["tag"] for pair in stored["last_seen"]]

    monkeypatch.setattr(release_watch, "_write_state", original)
    second = _cycle(
        dirs, now=NOW + timedelta(minutes=1), fetch=Fetch("v1.1.0", "v1.0.0")
    )
    assert _by_name(second)["release-watch"]["reason"] == "no-new-release"
    audit = _by_name(second)["pin-bump-audit"]
    assert (audit["status"], audit["detail"]["tag"]) == ("ok", "v1.1.0")
    assert [call["config"] for call in pin.calls] == [{"to": "v1.1.0"}]


def test_unsafe_newest_tag_is_never_written_as_pending(dirs, pin) -> None:
    _seed_release_state(dirs, "v1.0.0")
    _cycle(dirs, fetch=Fetch(TOKEN_TAG, "v1.0.0"))
    assert not (dirs.reports / "purple-cycle-pending.json").exists()


GREPTILE_GRANT = {"effective_until": "2099-01-01T00:00:00Z"}


def _inline_grant(until: datetime, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "operator": "alice",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": (until - timedelta(days=30)).isoformat(timespec="seconds"),
        "effective_until": until.isoformat(timespec="seconds"),
        "case_classes": ["prompt-injection"],
    }
    document.update(overrides)
    return document


def _grant_config(grant: Any) -> dict[str, Any]:
    return {
        "redteam": {
            "findings_sync": {
                "enabled": True,
                "repo": REPO,
                "labels": {},
                "roe_artifact": grant,
            }
        }
    }


def test_apply_refuses_the_incomplete_inline_grant(dirs, pin) -> None:
    runner = Runner()
    result = _cycle(
        dirs, mode="apply", config=_grant_config(GREPTILE_GRANT), runner=runner
    )
    assert runner.calls == []
    assert (result["exit"], result["state"]) == (3, "blocked")
    assert result["blocked"] == {"cause": "invalid", "step": "findings-dispatch"}
    plan = _by_name(_cycle(dirs, mode="plan", config=_grant_config(GREPTILE_GRANT)))
    pre = plan["preflight"]
    assert (pre["status"], pre["reason"]) == ("warn", "roe-invalid")
    assert pre["detail"]["roe_state"] == "invalid"


def test_apply_accepts_a_minimal_schema_valid_inline_grant(dirs, pin) -> None:
    dirs.reports.mkdir(parents=True)
    _seed_one_finding(dirs)
    grant = _inline_grant(NOW + timedelta(days=30))
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=_grant_config(grant), runner=runner)
    assert (result["ok"], result["exit"], result["state"]) == (True, 0, "completed")
    assert result["blocked"] is None
    assert len(runner.calls) == 1
    assert _by_name(result)["preflight"]["detail"]["roe_state"] == "valid"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.pop("operator"),
        lambda doc: doc.update(operator="  "),
        lambda doc: doc.update(authorized_targets=[]),
        lambda doc: doc.update(case_classes=["no-such-class"]),
        lambda doc: doc.update(schema_version=99),
    ],
)
def test_apply_refuses_schema_invalid_file_and_inline_grants(dirs, pin, mutate) -> None:
    until = NOW + timedelta(days=30)
    document = _inline_grant(until)
    mutate(document)
    path = dirs.tmp / "bad-grant.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    for grant in (str(path), document):
        runner = Runner()
        result = _cycle(dirs, mode="apply", config=_grant_config(grant), runner=runner)
        assert runner.calls == []
        assert result["blocked"] == {"cause": "invalid", "step": "findings-dispatch"}


def test_schema_invalid_grant_next_to_a_valid_one_still_refuses(dirs, pin) -> None:
    good = _roe_artifact(dirs.tmp / "roe-good.json", until=NOW + timedelta(days=30))
    config = _grant_config(GREPTILE_GRANT)
    config["redteam"]["roe_file"] = good
    runner = Runner()
    result = _cycle(dirs, mode="apply", config=config, runner=runner)
    assert runner.calls == []
    assert result["blocked"] == {"cause": "invalid", "step": "findings-dispatch"}

"""Hermetic tests for the purple-cycle orchestration library and CLI (R7)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from omega_prime.grokbot import pin_bump, purple_cycle, release_watch
from omega_prime.grokbot.purple_cycle import MODES, STEP_NAMES, main, run_cycle
from sidecar.findings import load_findings, record_finding

NOW = datetime(2026, 10, 14, 12, 30, 45, tzinfo=UTC)
REPO = "OWNER/NAME"
SECRET = "sk-live-SUPERSECRET0123456789"
TOKEN_TAG = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"

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
        payload = {"html_url": f"https://github.com/{REPO}/issues/{len(self.calls)}"}
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
        dirs.root / "workflows" / "purple-cycle-20261014-run.json"
    )
    assert run["ledger"] == str(dirs.reports / "purple-cycle.ndjson")

    apply = _cycle(dirs, mode="apply")
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
    result = _cycle(dirs, mode="apply", fetch=Fetch("v1.1.0", "v1.0.0"))
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


def test_pin_run_timeout_becomes_error_status(
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
    assert (audit["status"], audit["reason"]) == ("error", "step-error")
    assert audit["detail"]["timeout"] is True
    assert (result["ok"], result["exit"], result["state"]) == (False, 1, "failed")
    # Steps after the error are reported as halted, not executed.
    assert _triples(result)[3:] == [
        ("engagement", "skipped", "halted"),
        ("findings-plan", "skipped", "halted"),
        ("findings-dispatch", "skipped", "halted"),
    ]


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
        raise RuntimeError(f"gh failed token={SECRET}")

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
    names = sorted(p.name for p in (dirs.root / "workflows").glob("*.json"))
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
    result = _cycle(dirs, mode="apply", fetch=fetch, env=env)
    watch = _by_name(result)["release-watch"]
    assert watch["detail"]["new_count"] == 1
    assert watch["detail"]["new_tags"] == []
    assert watch["detail"]["newest_tag"] is None
    audit = _by_name(result)["pin-bump-audit"]
    assert (audit["status"], audit["reason"]) == ("warn", "unsafe-tag")
    assert pin.calls == []

    checkpoint = (dirs.root / "workflows" / f"{result['cycle_id']}.json").read_text(
        encoding="utf-8"
    )
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
        assert "ghp_" not in text
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
    workflow_dir = dirs.root / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "purple-cycle-20261014-run.json").write_text("{nope")
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
    workflow_dir = dirs.root / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "purple-cycle-20261014-run.json").write_text(
        json.dumps({"done": 3, "state": {"steps": []}})
    )
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
    # The refusal is recorded state: a rerun returns it without executing.
    again = _cycle(
        dirs, mode="apply", env=KILLED_ENV, config=_sync_config(), runner=runner
    )
    assert again == {**result, "resumed": True}
    assert counters["findings-dispatch"] == 1


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
    result = _cycle(dirs, mode="apply", config=_sync_config(), runner=runner)
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
    config = _config(enabled=True, repo="", labels={})
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


def _cli(argv: list[str], dirs: SimpleNamespace, **overrides: Any) -> int:
    kwargs: dict[str, Any] = {
        "env": IDLE_ENV,
        "config": _config(enabled=False),
        "fetch": Fetch("v1.0.0"),
        "runner": Runner(),
    }
    kwargs.update(overrides)
    base = [
        "--reports-dir",
        str(dirs.reports),
        "--root",
        str(dirs.root),
        "--now",
        "2026-10-14T12:30:45+00:00",
    ]
    return main([*argv, *base], **kwargs)


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
    code = _cli(["--apply", "--json"], dirs, env=KILLED_ENV)
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["mode"] == "apply" and payload["state"] == "blocked"
    assert payload["blocked"]["step"] == "findings-dispatch"


def test_cli_apply_with_explicit_apply_mode_is_fine(dirs, pin, capsys) -> None:
    assert _cli(["--apply", "--mode", "apply", "--json"], dirs) == 0
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
    _cli(
        ["--mode", "apply", "--json"],
        dirs,
        env=env,
        fetch=Fetch(TOKEN_TAG, "v1.0.0"),
    )
    _cli(["--mode", "apply"], dirs, env=env, fetch=Fetch(TOKEN_TAG, "v1.0.0"))
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

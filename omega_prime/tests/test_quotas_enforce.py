# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 82-02: soft warnings, hard pre-dispatch denial, lineage, rollup."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from omega_prime.grokbot.interceptors import (
    ToolCall,
    ToolOutcome,
    run_tool_call,
)
from omega_prime.grokbot.metrics import MetricsInterceptor, MetricsRegistry
from omega_prime.grokbot.security import SCOPE_CALL, Principal
from omega_prime.quotas import (
    UNKNOWN_CALLER,
    QuotaEngine,
    QuotaLimits,
    load_quota_document,
    render_rollup,
    write_rollup_file,
)

FIXED_NOW = 1_700_000_000.0  # 2023-11-14T22:13:20Z
DAY = "2023-11-14"

ALICE = Principal("tok-alice", frozenset({SCOPE_CALL}), "alice")
BOB = Principal("tok-bob", frozenset({SCOPE_CALL}), "bob")


def _call(principal: Principal | None, name: str = "tool_x") -> ToolCall:
    return ToolCall(name, {}, principal, transport="sse", request_id="r1")


def _ok() -> ToolOutcome:
    return ToolOutcome(status="ok", is_error=False, duration_ms=0.0)


def _engine(
    registry: MetricsRegistry,
    limits: dict[str, QuotaLimits],
    mode: str = "report",
    **kwargs: Any,
) -> QuotaEngine:
    return QuotaEngine(
        registry, mode=mode, limits=limits, clock=lambda: FIXED_NOW, **kwargs
    )


def _chain(registry: MetricsRegistry, engine: QuotaEngine) -> list:
    # Observer first so the denial metric sees quota denials.
    return [MetricsInterceptor(registry, tool_names=lambda: ["tool_x"]), engine]


# -- soft mode ---------------------------------------------------------


def test_soft_warns_once_without_denying(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "quotas.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "callers": {"alice": {"requests_per_day": 20}}}
        ),
        encoding="utf-8",
    )
    registry = MetricsRegistry()
    engine = _engine(registry, load_quota_document(str(path)), mode="soft")
    with caplog.at_level(logging.WARNING, logger="omega_prime.quotas"):
        for _ in range(17):  # 85% of 20
            assert engine.before(_call(ALICE)) is None
            engine.after(_call(ALICE), _ok())
        for _ in range(3):  # past the limit: still no denial in soft
            assert engine.before(_call(ALICE)) is None
            engine.after(_call(ALICE), _ok())
    warnings = [r for r in caplog.records if r.name == "omega_prime.quotas"]
    assert len(warnings) == 1
    assert "alice" in warnings[0].getMessage()
    text = registry.render()
    assert 'omega_caller_quota_warn{caller="alice"} 1' in text


def test_report_stays_silent_at_same_load(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "quotas.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "callers": {"alice": {"requests_per_day": 20}}}
        ),
        encoding="utf-8",
    )
    engine = _engine(MetricsRegistry(), load_quota_document(str(path)), mode="report")
    with caplog.at_level(logging.WARNING, logger="omega_prime.quotas"):
        for _ in range(20):
            assert engine.before(_call(ALICE)) is None
            engine.after(_call(ALICE), _ok())
    assert [r for r in caplog.records if r.name == "omega_prime.quotas"] == []


# -- hard mode ---------------------------------------------------------


def test_hard_denies_over_limit_pre_dispatch() -> None:
    registry = MetricsRegistry()
    engine = _engine(registry, {"alice": QuotaLimits(requests_per_day=3)}, mode="hard")
    chain = _chain(registry, engine)
    effects: list[str] = []

    def dispatch() -> str:
        effects.append("write")
        return json.dumps({"ok": True})

    # 1-under the limit succeeds, at-limit succeeds, over-limit denies.
    for _ in range(3):
        payload, outcome = run_tool_call(chain, _call(ALICE), dispatch)
        assert outcome.status == "ok" and json.loads(payload) == {"ok": True}
    payload, outcome = run_tool_call(chain, _call(ALICE), dispatch)
    assert outcome.status == "denied"
    assert outcome.denial_code == "quota"
    decoded = json.loads(payload)
    assert decoded["tool"] == "tool_x"
    assert decoded["error"].startswith("quota: ")
    assert "alice" in decoded["error"]
    assert effects == ["write", "write", "write"]  # zero partial effects
    text = registry.render()
    assert 'omega_tool_denials_total{code="quota"} 1' in text
    counts = engine.daily_counts(DAY)["alice"]
    assert (counts["requests"], counts["tool_calls"], counts["denied"]) == (4, 3, 1)


def test_hard_denial_message_names_caller_and_kind() -> None:
    engine = _engine(
        MetricsRegistry(), {"alice": QuotaLimits(requests_per_day=1)}, mode="hard"
    )
    assert engine.before(_call(ALICE)) is None
    denial = engine.before(_call(ALICE))
    assert denial is not None and denial.code == "quota"
    assert "alice" in denial.message and "requests" in denial.message


def test_hard_tool_calls_cap_denies_before_dispatch() -> None:
    engine = _engine(
        MetricsRegistry(),
        {"alice": QuotaLimits(tool_calls_per_day=2)},
        mode="hard",
    )
    for _ in range(2):
        assert engine.before(_call(ALICE)) is None
        engine.after(_call(ALICE), _ok())
    denial = engine.before(_call(ALICE))
    assert denial is not None and denial.code == "quota"
    assert "tool calls" in denial.message


def test_hard_unknown_identity_never_denied() -> None:
    registry = MetricsRegistry()
    engine = _engine(registry, {"alice": QuotaLimits(requests_per_day=1)}, mode="hard")
    for _ in range(50):
        assert engine.before(_call(None)) is None
        engine.after(_call(None), _ok())
    counts = engine.daily_counts(DAY)[UNKNOWN_CALLER]
    assert (counts["requests"], counts["denied"]) == (50, 0)


def test_hard_unlisted_caller_is_unlimited() -> None:
    engine = _engine(
        MetricsRegistry(), {"alice": QuotaLimits(requests_per_day=1)}, mode="hard"
    )
    for _ in range(10):
        assert engine.before(_call(BOB)) is None
        engine.after(_call(BOB), _ok())


# -- lineage -----------------------------------------------------------


def test_rotated_caller_keeps_quota_label(tmp_path: Any) -> None:
    from omega_prime.grokbot.tokens import create_token, rotate_token

    store = tmp_path / "tokens.json"
    _, old = create_token(store, scopes=["read", "call"], label="alice")
    _, _, new = rotate_token(store, label="alice", overlap_hours=1.0)
    assert new.label == old.label == "alice"
    assert new.rotated_from == old.sha256
    engine = _engine(
        MetricsRegistry(), {"alice": QuotaLimits(requests_per_day=2)}, mode="hard"
    )
    old_principal = Principal(old.id, frozenset({SCOPE_CALL}), old.label)
    new_principal = Principal(new.id, frozenset({SCOPE_CALL}), new.label)
    assert engine.before(_call(old_principal)) is None
    assert engine.before(_call(new_principal)) is None
    # One shared bucket: the rotated caller is not a new unlimited caller.
    assert engine.before(_call(new_principal)) is not None
    assert engine.daily_counts(DAY)["alice"]["requests"] == 3


def test_label_resolver_folds_renamed_lineage() -> None:
    renamed = Principal("tok-new", frozenset({SCOPE_CALL}), "alice-v2")
    engine = _engine(
        MetricsRegistry(),
        {"alice": QuotaLimits(requests_per_day=1)},
        mode="hard",
        label_resolver=lambda p: (
            "alice"
            if getattr(p, "label", "") == "alice-v2"
            else (getattr(p, "label", "") or UNKNOWN_CALLER)
        ),
    )
    assert engine.before(_call(renamed)) is None
    assert engine.daily_counts(DAY)["alice"]["requests"] == 1


# -- live transitions --------------------------------------------------


def test_mode_transitions_take_effect_without_restart() -> None:
    registry = MetricsRegistry()
    engine = _engine(
        registry, {"alice": QuotaLimits(requests_per_day=2)}, mode="report"
    )
    for _ in range(5):  # report enforces nothing
        assert engine.before(_call(ALICE)) is None
    engine.set_mode("soft")  # warnings only, still nothing denied
    for _ in range(5):
        assert engine.before(_call(ALICE)) is None
    engine.set_mode("hard")  # already over the limit: denies now
    assert engine.before(_call(ALICE)) is not None
    engine.set_mode("report")  # escape hatch: unlimited again, no restart
    assert engine.before(_call(ALICE)) is None
    with pytest.raises(ValueError):
        engine.set_mode("bogus")


def test_refresh_from_config_reloads_live() -> None:
    engine = _engine(MetricsRegistry(), {}, mode="report")
    assert not engine.is_active
    engine.refresh_from_config(
        {"quotas": {"mode": "hard", "document": "", "rollup_dir": ".omega-quotas/"}}
    )
    assert engine.mode == "hard"


# -- cardinality cap ---------------------------------------------------


def test_label_cap_folds_excess_into_overflow() -> None:
    registry = MetricsRegistry()
    engine = _engine(
        registry, {"vip": QuotaLimits(requests_per_day=10**6)}, mode="report"
    )
    for i in range(70):
        principal = Principal(f"tok-{i}", frozenset({SCOPE_CALL}), f"caller-{i:02d}")
        assert engine.before(_call(principal)) is None
    counts = engine.daily_counts(DAY)
    assert "_overflow_" in counts
    assert sum(entry["requests"] for entry in counts.values()) == 70
    assert len(counts) <= 65
    assert 'caller="_overflow_"' in registry.render()


# -- rollup ------------------------------------------------------------


def test_rollup_reconciles_with_metrics_and_is_idempotent(
    tmp_path: Any,
) -> None:
    registry = MetricsRegistry()
    engine = _engine(
        registry,
        {
            "alice": QuotaLimits(requests_per_day=100, tool_calls_per_day=100),
            "bob": QuotaLimits(requests_per_day=100),
        },
        mode="report",
    )
    chain = _chain(registry, engine)
    for _ in range(3):
        _, outcome = run_tool_call(chain, _call(ALICE), lambda: '{"ok": true}')
        assert outcome.status == "ok"
    for _ in range(2):
        _, outcome = run_tool_call(chain, _call(BOB), lambda: '{"ok": true}')
        assert outcome.status == "ok"
    payload = engine.build_rollup(DAY)
    assert payload["date"] == DAY
    assert payload["callers"]["alice"]["requests"] == 3
    assert payload["callers"]["alice"]["tool_calls"] == 3
    assert payload["callers"]["bob"]["requests"] == 2
    assert payload["callers"]["alice"]["limits"] == {
        "requests_per_day": 100,
        "tool_calls_per_day": 100,
    }
    text = registry.render()
    assert 'omega_caller_requests_total{caller="alice"} 3' in text
    assert 'omega_caller_tool_calls_total{caller="bob"} 2' in text
    first = write_rollup_file(tmp_path, DAY, payload)
    before = first.read_bytes()
    second = write_rollup_file(tmp_path, DAY, engine.build_rollup(DAY))
    assert second.read_bytes() == before
    assert json.loads(before)["callers"]["alice"]["requests"] == 3


def test_rollup_render_is_deterministic() -> None:
    payload = {
        "date": DAY,
        "callers": {
            "bob": {
                "denied": 0,
                "limits": {"requests_per_day": 1, "tool_calls_per_day": None},
                "requests": 2,
                "tool_calls": 2,
                "warn": False,
            }
        },
    }
    assert render_rollup(payload) == render_rollup(json.loads(json.dumps(payload)))


# -- CLI ---------------------------------------------------------------


def test_rollup_cli_writes_and_skips(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from omega_prime.quotas.__main__ import main

    monkeypatch.delenv("OMEGA_PRIME_QUOTAS_DOCUMENT", raising=False)
    doc = tmp_path / "quotas.json"
    doc.write_text(
        json.dumps(
            {"schema_version": 1, "callers": {"alice": {"requests_per_day": 5}}}
        ),
        encoding="utf-8",
    )
    counters = tmp_path / "counters.json"
    counters.write_text(
        json.dumps({"alice": {"requests": 2, "tool_calls": 2, "denied": 0}}),
        encoding="utf-8",
    )
    outdir = tmp_path / "rollups"
    assert (
        main(
            [
                "rollup",
                "--date",
                DAY,
                "--rollup-dir",
                str(outdir),
                "--document",
                str(doc),
                "--counters",
                str(counters),
            ]
        )
        == 0
    )
    target = outdir / f"usage-{DAY}.json"
    first = target.read_bytes()
    assert json.loads(first)["callers"]["alice"]["requests"] == 2
    capsys.readouterr()
    assert (
        main(
            [
                "rollup",
                "--date",
                DAY,
                "--rollup-dir",
                str(outdir),
                "--document",
                str(doc),
                "--counters",
                str(counters),
            ]
        )
        == 0
    )
    assert target.read_bytes() == first  # byte-identical rerun
    # Disabled run logs one skip line and writes nothing.
    emptydir = tmp_path / "empty"
    assert main(["rollup", "--date", DAY, "--rollup-dir", str(emptydir)]) == 0
    out, _ = capsys.readouterr()
    assert "skipping rollup" in out
    assert list(emptydir.glob("*.json")) == [] if emptydir.exists() else True
    # A malformed date with an enabled document fails named (exit 1).
    assert (
        main(
            [
                "rollup",
                "--date",
                "not-a-day",
                "--rollup-dir",
                str(outdir),
                "--document",
                str(doc),
            ]
        )
        == 1
    )


def test_warn_refires_after_utc_rollover_with_gauge_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warn on day 1, roll to day 2, exceed 80% again: warn + gauge (P2)."""
    now = [FIXED_NOW]
    registry = MetricsRegistry()
    engine = QuotaEngine(
        registry,
        mode="soft",
        limits={"alice": QuotaLimits(requests_per_day=10)},
        clock=lambda: now[0],
    )
    with caplog.at_level(logging.WARNING, logger="omega_prime.quotas"):
        for _ in range(8):  # 80% of 10 on day 1
            assert engine.before(_call(ALICE)) is None
            engine.after(_call(ALICE), _ok())
    assert 'omega_caller_quota_warn{caller="alice"} 1' in registry.render()
    day1_warnings = [r for r in caplog.records if r.name == "omega_prime.quotas"]
    assert len(day1_warnings) == 1
    caplog.clear()
    now[0] = FIXED_NOW + 86400  # next UTC day
    with caplog.at_level(logging.WARNING, logger="omega_prime.quotas"):
        for _ in range(10):  # past the threshold plus follow-up traffic
            assert engine.before(_call(ALICE)) is None
            engine.after(_call(ALICE), _ok())
    day2_warnings = [r for r in caplog.records if r.name == "omega_prime.quotas"]
    assert len(day2_warnings) == 1
    assert "alice" in day2_warnings[0].getMessage()
    assert 'omega_caller_quota_warn{caller="alice"} 1' in registry.render()


# -- overflow enforcement isolation --------------------------------------


def test_overflow_callers_enforced_independently() -> None:
    """70 callers, hard mode: sharing ends at the export label (P1).

    Buckets stay per-caller past the 64-label cap, so caller-65's first
    request is allowed even after caller-64 consumed its own budget; the
    exported `_overflow_` entry still sums both.
    """
    registry = MetricsRegistry()
    labels = [f"c{i:02d}" for i in range(70)]
    engine = _engine(
        registry,
        {label: QuotaLimits(requests_per_day=1) for label in labels},
        mode="hard",
    )
    c64 = Principal("tok-64", frozenset({SCOPE_CALL}), "c64")
    c65 = Principal("tok-65", frozenset({SCOPE_CALL}), "c65")
    assert engine.caller_label(c64) == "_overflow_"
    assert engine.caller_label(c65) == "_overflow_"
    assert engine.before(_call(c64)) is None  # own budget, first request
    assert engine.before(_call(c65)) is None  # not denied for c64's usage
    assert engine.before(_call(c64)) is not None  # own second request denies
    counts = engine.daily_counts(DAY)
    assert counts["_overflow_"]["requests"] == 3
    assert sum(entry["requests"] for entry in counts.values()) == 3
    assert len(counts) == 1
    assert 'caller="_overflow_"' in registry.render()


# -- host-side counters dump ---------------------------------------------


def test_counters_dump_feeds_rollup_cli(tmp_path: Any) -> None:
    """Serve-side dump round-trips through `rollup --counters` (P1)."""
    from omega_prime.quotas.__main__ import main

    registry = MetricsRegistry()
    engine = _engine(
        registry,
        {
            "alice": QuotaLimits(requests_per_day=100, tool_calls_per_day=100),
            "bob": QuotaLimits(requests_per_day=100),
        },
        mode="report",
    )
    chain = _chain(registry, engine)
    for _ in range(3):
        _, outcome = run_tool_call(chain, _call(ALICE), lambda: '{"ok": true}')
        assert outcome.status == "ok"
    for _ in range(2):
        _, outcome = run_tool_call(chain, _call(BOB), lambda: '{"ok": true}')
        assert outcome.status == "ok"
    outdir = tmp_path / "rollups"
    dumped = engine.export_counters(outdir)
    assert dumped == [outdir / f"counters-{DAY}.json"]
    snapshot = json.loads(dumped[0].read_text(encoding="utf-8"))
    assert (snapshot["alice"]["requests"], snapshot["alice"]["tool_calls"]) == (3, 3)
    assert (snapshot["bob"]["requests"], snapshot["bob"]["tool_calls"]) == (2, 2)
    assert (
        engine.dump_counters_file(outdir, DAY).read_bytes() == dumped[0].read_bytes()
    )  # byte-identical re-dump
    doc = tmp_path / "quotas.json"
    doc.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "callers": {
                    "alice": {"requests_per_day": 100, "tool_calls_per_day": 100},
                    "bob": {"requests_per_day": 100},
                },
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "rollup",
                "--date",
                DAY,
                "--rollup-dir",
                str(outdir),
                "--document",
                str(doc),
                "--counters",
                str(dumped[0]),
            ]
        )
        == 0
    )
    usage = json.loads((outdir / f"usage-{DAY}.json").read_text(encoding="utf-8"))
    assert usage["callers"]["alice"]["requests"] == 3
    assert usage["callers"]["bob"]["requests"] == 2
    # Without --counters the CLI falls back to the host's dumped file.
    (outdir / f"usage-{DAY}.json").unlink()
    assert (
        main(
            [
                "rollup",
                "--date",
                DAY,
                "--rollup-dir",
                str(outdir),
                "--document",
                str(doc),
            ]
        )
        == 0
    )
    rediscovered = json.loads(
        (outdir / f"usage-{DAY}.json").read_text(encoding="utf-8")
    )
    assert rediscovered["callers"]["alice"]["requests"] == 3


# -- live reload via the maintenance tick ---------------------------------


def test_maintenance_tick_flips_mode_live_without_restart(tmp_path: Any) -> None:
    """The serve tick activates hard mode and back with no restart (P1)."""
    from omega_prime.quotas import QuotasMaintenance

    doc = tmp_path / "quotas.json"
    doc.write_text(
        json.dumps(
            {"schema_version": 1, "callers": {"alice": {"requests_per_day": 1}}}
        ),
        encoding="utf-8",
    )
    rollup_dir = tmp_path / "rollups"
    state = {"mode": "report", "document": ""}
    registry = MetricsRegistry()
    engine = _engine(registry, {}, mode="report")
    assert not engine.is_active
    maint = QuotasMaintenance(
        engine,
        root=tmp_path,
        config_loader=lambda: {"quotas": {**state, "rollup_dir": str(rollup_dir)}},
    )
    tick = maint.tick_once()
    assert tick["ok"] and tick["mode"] == "report" and tick["dumped"] == []
    assert engine.before(_call(ALICE)) is None  # inert: unlimited
    state.update(mode="hard", document=str(doc))
    tick = maint.tick_once()
    assert tick["ok"] and tick["mode"] == "hard"
    assert engine.is_active
    assert engine.before(_call(ALICE)) is None
    assert engine.before(_call(ALICE)) is not None  # over budget: denied live
    assert 'omega_caller_requests_total{caller="alice"} 2' in registry.render()
    tick = maint.tick_once()  # next tick dumps the counted day
    assert tick["ok"] and tick["mode"] == "hard"
    assert tick["dumped"] == [str(rollup_dir / f"counters-{DAY}.json")]
    snapshot = json.loads(
        (rollup_dir / f"counters-{DAY}.json").read_text(encoding="utf-8")
    )
    assert snapshot["alice"]["requests"] == 2
    state["mode"] = "report"  # escape hatch, still no restart
    assert maint.tick_once()["mode"] == "report"
    assert engine.before(_call(ALICE)) is None
    maint.start()
    maint.stop()


def test_refresh_bad_document_keeps_previous_mode_and_limits(
    tmp_path: Any,
) -> None:
    """A bad document never half-applies: mode and limits stay put."""
    from omega_prime.quotas import QuotaDocumentError

    engine = _engine(
        MetricsRegistry(), {"alice": QuotaLimits(requests_per_day=5)}, mode="hard"
    )
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(QuotaDocumentError):
        engine.refresh_from_config({"quotas": {"mode": "report", "document": str(bad)}})
    assert engine.mode == "hard"
    assert engine.limits["alice"].requests_per_day == 5

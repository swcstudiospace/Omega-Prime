# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 82-02: quota document loader + report-only parity and counters."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from omega_prime.grokbot.interceptors import ToolCall, run_tool_call
from omega_prime.grokbot.metrics import MetricsInterceptor, MetricsRegistry
from omega_prime.grokbot.security import SCOPE_CALL, Principal
from omega_prime.quotas import (
    QuotaDocumentError,
    QuotaEngine,
    QuotaLimits,
    load_quota_document,
)

ALICE = Principal("tok-alice", frozenset({SCOPE_CALL}), "alice")
BOB = Principal("tok-bob", frozenset({SCOPE_CALL}), "bob")


def _call(principal: Principal | None, name: str = "tool_x") -> ToolCall:
    return ToolCall(name, {}, principal, transport="sse", request_id="r1")


def _dispatch(payload: str = '{"ok": true}') -> Any:
    return lambda: payload


def _valid_doc(tmp_path: Any, callers: dict) -> str:
    path = tmp_path / "quotas.json"
    path.write_text(
        json.dumps({"schema_version": 1, "callers": callers}), encoding="utf-8"
    )
    return str(path)


# -- loader ------------------------------------------------------------


def test_loader_valid_document(tmp_path: Any) -> None:
    path = _valid_doc(
        tmp_path,
        {"alice": {"requests_per_day": 100, "tool_calls_per_day": 50}, "bob": {}},
    )
    limits = load_quota_document(path)
    assert limits["alice"] == QuotaLimits(requests_per_day=100, tool_calls_per_day=50)
    assert limits["bob"] == QuotaLimits()


def test_loader_missing_file_and_empty_path_are_inert(tmp_path: Any) -> None:
    assert load_quota_document(str(tmp_path / "absent.json")) == {}
    assert load_quota_document("") == {}
    assert load_quota_document(None) == {}


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "callers": {}},
        {"callers": {}},
        {"schema_version": 1, "callers": {}, "surprise": True},
        {"schema_version": 1, "callers": []},
        {"schema_version": 1, "callers": {"alice": {"requests_per_day": -5}}},
        {"schema_version": 1, "callers": {"alice": {"requests_per_day": 0}}},
        {"schema_version": 1, "callers": {"alice": {"tool_calls_per_day": True}}},
        {"schema_version": 1, "callers": {"alice": {"bogus_kind": 3}}},
        {"schema_version": 1, "callers": {"alice": "unlimited"}},
        {"schema_version": 1, "callers": {"": {"requests_per_day": 3}}},
        {"schema_version": 1, "callers": {"_unknown_": {"requests_per_day": 3}}},
        ["not", "an", "object"],
    ],
)
def test_loader_rejects_bad_documents(tmp_path: Any, document: Any) -> None:
    path = tmp_path / "quotas.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QuotaDocumentError):
        load_quota_document(str(path))


def test_loader_rejects_non_json(tmp_path: Any) -> None:
    path = tmp_path / "quotas.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(QuotaDocumentError):
        load_quota_document(str(path))


# -- report mode -------------------------------------------------------


def test_report_counts_per_caller_without_refusals(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    path = _valid_doc(
        tmp_path,
        {"alice": {"requests_per_day": 1000}, "bob": {"requests_per_day": 1000}},
    )
    registry = MetricsRegistry()
    engine = QuotaEngine(registry, mode="report", limits=load_quota_document(path))
    observer = MetricsInterceptor(registry, tool_names=lambda: ["tool_x"])
    with caplog.at_level(logging.WARNING, logger="omega_prime.quotas"):
        for _ in range(3):
            payload, outcome = run_tool_call(
                [engine, observer], _call(ALICE), _dispatch()
            )
            assert outcome.status == "ok"
            assert outcome.denial_code is None
            assert json.loads(payload) == {"ok": True}
        for _ in range(2):
            _, outcome = run_tool_call([engine, observer], _call(BOB), _dispatch())
            assert outcome.status == "ok"
    assert [r for r in caplog.records if r.name == "omega_prime.quotas"] == []
    text = registry.render()
    assert 'omega_caller_requests_total{caller="alice"} 3' in text
    assert 'omega_caller_requests_total{caller="bob"} 2' in text
    assert 'omega_caller_tool_calls_total{caller="alice"} 3' in text
    assert 'omega_caller_tool_calls_total{caller="bob"} 2' in text
    assert "omega_caller_quota_warn" in text


def test_report_never_denies_past_the_limit(tmp_path: Any) -> None:
    path = _valid_doc(tmp_path, {"alice": {"requests_per_day": 2}})
    engine = QuotaEngine(
        MetricsRegistry(), mode="report", limits=load_quota_document(path)
    )
    for _ in range(10):
        assert engine.before(_call(ALICE)) is None
        engine.after(_call(ALICE), _ok())
    assert engine.daily_counts(_today(engine))["alice"]["requests"] == 10


def _ok() -> Any:
    from omega_prime.grokbot.interceptors import ToolOutcome

    return ToolOutcome(status="ok", is_error=False, duration_ms=0.0)


def _today(engine: QuotaEngine) -> str:
    from omega_prime.quotas import _utc_day

    return _utc_day(engine._clock())


def test_inert_engine_registers_no_series_and_counts_nothing() -> None:
    registry = MetricsRegistry()
    engine = QuotaEngine(registry, mode="report", limits={})
    assert not engine.is_active
    assert registry.render() == ""
    payload, outcome = run_tool_call([engine], _call(ALICE), _dispatch())
    assert outcome.status == "ok" and json.loads(payload) == {"ok": True}
    assert registry.render() == ""


def test_inert_engine_missing_document_file(tmp_path: Any) -> None:
    registry = MetricsRegistry()
    engine = QuotaEngine(
        registry,
        mode="report",
        limits=load_quota_document(str(tmp_path / "absent.json")),
    )
    assert not engine.is_active
    assert registry.render() == ""


# -- config defaults ---------------------------------------------------


def test_quotas_config_defaults_and_bogus_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("os.environ", {})
    from omega_prime.config import load_config

    assert load_config()["quotas"] == {
        "mode": "report",
        "document": "",
        "rollup_dir": ".omega-quotas/",
    }
    monkeypatch.setattr(
        "os.environ", {"OMEGA_PRIME_QUOTAS_MODE": "bogus", "OMEGA_PRIME_QUOTAS_X": "1"}
    )
    assert load_config()["quotas"]["mode"] == "report"
    monkeypatch.setattr(
        "os.environ",
        {
            "OMEGA_PRIME_QUOTAS_MODE": "hard",
            "OMEGA_PRIME_QUOTAS_DOCUMENT": "/tmp/q.json",
        },
    )
    node = load_config()["quotas"]
    assert (node["mode"], node["document"]) == ("hard", "/tmp/q.json")

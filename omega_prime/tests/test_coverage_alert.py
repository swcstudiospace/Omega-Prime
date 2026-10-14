# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Coverage-matrix alerts (batch-03 Feature E): heartbeat kind + matrix read.

Hermetic ``tmp_path`` fixture reports with fixed timestamps — no wall-clock
leakage. The matrix parser is exercised against renderer-shaped markdown;
the tick dedup against injected checkers (plus one end-to-end run through
the real report script module).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omega_prime.config import load_config
from omega_prime.cron.heartbeat import (
    COVERAGE_ALERT_DEFAULT_THRESHOLD,
    COVERAGE_ALERT_KIND,
    check_coverage_alert,
    clear_coverage_alert,
    list_coverage_alerts,
    make_coverage_checker,
    read_coverage_matrix,
    schedule_coverage_alert,
    tick_coverage_alert,
)
from omega_prime.cron.scheduler import JobStore

REDTEAM_REPORTS_ENV = "OMEGA_PRIME_REDTEAM_REPORTS_DIR"
SIDECAR_REPORTS_ENV = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"

T_DAY1 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
T_DAY1_LATER = datetime(2026, 10, 8, 18, 0, 0, tzinfo=UTC)
T_DAY2 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)

MATRIX_REPORT = """# Engagement report

## Header

- window: 2026-10-01T00:00:00+00:00..2026-10-08T00:00:00+00:00

## Coverage matrix

| class | cases | runs | pass rate | gaps | verdict |
| --- | --- | --- | --- | --- | --- |
| abuse | 2 | 2 | 2/2 (100.0%) | 0 | covered |
| jailbreak | 2 | 2 | 1/2 (50.0%) | 1 | gappy |
| weird-class | 1 | 0 | 0/0 (n/a) | 0 | gappy |

## Detection gaps

- none
"""


def _store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "cron" / "jobs.json")


def _history_with_report(tmp_path: Path, name: str, text: str) -> Path:
    """History dir whose ``reports/`` subdir holds one engagement report."""
    history = tmp_path / "history"
    out_dir = history / "reports"
    out_dir.mkdir(parents=True)
    (out_dir / name).write_text(text, encoding="utf-8")
    return history


def _alert_below(*classes: str) -> dict:
    return {
        "state": "alert",
        "threshold": 80.0,
        "below": [{"class": cls, "pass_rate": 50.0} for cls in classes],
        "report": "engagement-x.md",
        "checked_at": "2026-10-08T12:00:00+00:00",
    }


def test_schedule_validates_interval(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="interval"):
        schedule_coverage_alert(store, interval_seconds=0, due_at=0)
    with pytest.raises(ValueError, match="interval"):
        schedule_coverage_alert(store, interval_seconds=-5, due_at=0)
    with pytest.raises(ValueError, match="interval"):
        schedule_coverage_alert(store, interval_seconds=True, due_at=0)


def test_schedule_list_clear_roundtrip(tmp_path):
    store = _store(tmp_path)
    job_id = schedule_coverage_alert(store, due_at=T_DAY1.timestamp())
    jobs = list_coverage_alerts(store)
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["kind"] == COVERAGE_ALERT_KIND
    assert clear_coverage_alert(store, job_id) == {"cleared": job_id}
    assert list_coverage_alerts(store) == []
    assert "error" in clear_coverage_alert(store, job_id)


def test_read_coverage_matrix_parses_newest(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    history = _history_with_report(
        tmp_path, "engagement-2026-10-01_2026-10-08.md", MATRIX_REPORT
    )
    older = history / "reports" / "engagement-2026-09-01_2026-10-01.md"
    older.write_text(
        MATRIX_REPORT.replace("1/2 (50.0%)", "2/2 (100.0%)"), encoding="utf-8"
    )
    matrix = read_coverage_matrix(history)
    assert matrix["report"] == "engagement-2026-10-01_2026-10-08.md"
    assert matrix["classes"]["abuse"] == {"pass_rate": 100.0, "verdict": "covered"}
    assert matrix["classes"]["jailbreak"] == {"pass_rate": 50.0, "verdict": "gappy"}
    # 0/0 (n/a) rows are unjudgeable, never alerting.
    assert matrix["classes"]["weird-class"] == {"pass_rate": None, "verdict": "gappy"}


def test_read_coverage_matrix_no_reports(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    history = tmp_path / "history"
    (history / "reports").mkdir(parents=True)
    assert read_coverage_matrix(history) == {"report": None, "classes": {}}


def test_read_coverage_matrix_missing_dir_never_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    assert read_coverage_matrix(tmp_path / "absent") == {"report": None, "classes": {}}


def test_read_coverage_matrix_env_output_override(tmp_path, monkeypatch):
    out_dir = tmp_path / "custom-out"
    out_dir.mkdir()
    (out_dir / "engagement-only.md").write_text(MATRIX_REPORT, encoding="utf-8")
    monkeypatch.setenv(REDTEAM_REPORTS_ENV, str(out_dir))
    history = tmp_path / "history"
    history.mkdir()
    matrix = read_coverage_matrix(history)
    assert matrix["report"] == "engagement-only.md"
    assert matrix["classes"]["jailbreak"]["pass_rate"] == 50.0


def test_check_alert_fires_below_threshold(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    history = _history_with_report(tmp_path, "engagement-week.md", MATRIX_REPORT)
    result = check_coverage_alert(history, T_DAY1)
    assert result["state"] == "alert"
    assert result["threshold"] == COVERAGE_ALERT_DEFAULT_THRESHOLD == 80.0
    assert result["below"] == [{"class": "jailbreak", "pass_rate": 50.0}]
    assert result["report"] == "engagement-week.md"
    assert result["checked_at"] == "2026-10-08T12:00:00+00:00"


def test_check_ok_when_all_above(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    text = MATRIX_REPORT.replace("1/2 (50.0%)", "2/2 (100.0%)")
    history = _history_with_report(tmp_path, "engagement-week.md", text)
    result = check_coverage_alert(history, T_DAY1, 80.0)
    assert result["state"] == "ok"
    assert result["below"] == []


def test_check_custom_threshold(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    history = _history_with_report(tmp_path, "engagement-week.md", MATRIX_REPORT)
    # 50.0 is not below 50.0 (strict): silent at exactly the threshold.
    assert check_coverage_alert(history, T_DAY1, 50.0)["state"] == "ok"
    assert check_coverage_alert(history, T_DAY1, 50.1)["state"] == "alert"


def test_check_na_classes_never_fire(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    na_only = MATRIX_REPORT.replace("2/2 (100.0%)", "0/0 (n/a)").replace(
        "1/2 (50.0%)", "0/0 (n/a)"
    )
    history = _history_with_report(tmp_path, "engagement-week.md", na_only)
    result = check_coverage_alert(history, T_DAY1, 100.0)
    assert result["state"] == "ok"
    assert result["below"] == []


def test_check_no_reports_state(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    result = check_coverage_alert(tmp_path / "absent", T_DAY1)
    assert result["state"] == "no-reports"
    assert result["below"] == []
    assert result["report"] is None


def test_check_garbage_threshold_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    history = _history_with_report(tmp_path, "engagement-week.md", MATRIX_REPORT)
    for bad in ("bogus", None, True, 0, -5, 101, float("nan")):
        result = check_coverage_alert(history, T_DAY1, bad)
        assert result["threshold"] == 80.0
    assert check_coverage_alert(history, T_DAY1, 100)["threshold"] == 100


def test_tick_fires_once_per_utc_day_per_class(tmp_path):
    store = _store(tmp_path)
    schedule_coverage_alert(store, interval_seconds=60, due_at=0)

    fired = tick_coverage_alert(store, T_DAY1, lambda d, n: _alert_below("jailbreak"))
    assert len(fired) == 1
    assert fired[0]["last_fired"] == {"jailbreak": "2026-10-08"}

    # Same UTC day, later stamp: silent bookkeeping, history still recorded.
    again = tick_coverage_alert(
        store, T_DAY1_LATER, lambda d, n: _alert_below("jailbreak")
    )
    assert again == []
    assert len(fired[0]["history"]) == 2

    # Next UTC day: fires again.
    next_day = tick_coverage_alert(
        store, T_DAY2, lambda d, n: _alert_below("jailbreak")
    )
    assert len(next_day) == 1
    assert next_day[0]["last_fired"] == {"jailbreak": "2026-10-09"}


def test_tick_dedups_per_class_not_per_job(tmp_path):
    store = _store(tmp_path)
    schedule_coverage_alert(store, interval_seconds=60, due_at=0)

    first = tick_coverage_alert(store, T_DAY1, lambda d, n: _alert_below("a", "b"))
    assert len(first) == 1
    # Same day, only the already-fired class below: silent.
    silent = tick_coverage_alert(store, T_DAY1_LATER, lambda d, n: _alert_below("b"))
    assert silent == []
    # Same day, one unfired class joins: fires (and records only the new one).
    refire = tick_coverage_alert(
        store, T_DAY1_LATER.timestamp() + 3600, lambda d, n: _alert_below("b", "c")
    )
    assert len(refire) == 1
    assert refire[0]["last_fired"] == {
        "a": "2026-10-08",
        "b": "2026-10-08",
        "c": "2026-10-08",
    }


def test_tick_ok_and_no_reports_are_silent_but_recorded(tmp_path):
    store = _store(tmp_path)
    schedule_coverage_alert(store, interval_seconds=60, due_at=0)
    for stamp, result in (
        (
            T_DAY1,
            {
                "state": "ok",
                "threshold": 80.0,
                "below": [],
                "report": "r",
                "checked_at": "x",
            },
        ),
        (
            T_DAY1_LATER,
            {
                "state": "no-reports",
                "threshold": 80.0,
                "below": [],
                "report": None,
                "checked_at": "x",
            },
        ),
    ):
        assert tick_coverage_alert(store, stamp, lambda d, n, r=result: r) == []
    jobs = list_coverage_alerts(store)
    assert len(jobs[0]["history"]) == 2
    assert jobs[0]["last_result"]["state"] == "no-reports"


def test_tick_checker_exception_recorded_without_corruption(tmp_path):
    store = _store(tmp_path)
    schedule_coverage_alert(store, interval_seconds=60, due_at=0)

    def checker(reports_dir, now):
        raise RuntimeError("boom")

    assert tick_coverage_alert(store, T_DAY1, checker) == []
    jobs = list_coverage_alerts(store)
    assert jobs[0]["last_result"]["state"] == "error"
    assert "boom" in jobs[0]["last_result"]["error"]


def test_tick_ignores_other_kinds(tmp_path):
    store = _store(tmp_path)
    store.schedule("plain job", 0)
    calls = []
    fired = tick_coverage_alert(
        store,
        T_DAY1,
        lambda d, n: calls.append((d, n)) or _alert_below("a"),  # type: ignore[func-returns-value]
    )
    assert fired == []
    assert calls == []


def test_make_coverage_checker_never_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    checker = make_coverage_checker(80.0)
    result = checker(tmp_path / "absent", T_DAY1)
    assert result["state"] == "no-reports"
    assert result["threshold"] == 80.0


def test_make_coverage_checker_end_to_end_through_real_script(tmp_path, monkeypatch):
    monkeypatch.delenv(REDTEAM_REPORTS_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_REPORTS_ENV, raising=False)
    history = _history_with_report(tmp_path, "engagement-week.md", MATRIX_REPORT)
    monkeypatch.setenv(SIDECAR_REPORTS_ENV, str(history))
    store = _store(tmp_path)
    schedule_coverage_alert(store, interval_seconds=60, due_at=0)
    fired = tick_coverage_alert(store, T_DAY1, make_coverage_checker(80.0))
    assert len(fired) == 1
    assert fired[0]["last_result"]["below"] == [
        {"class": "jailbreak", "pass_rate": 50.0}
    ]


def test_coverage_threshold_config_default_and_overrides(tmp_path, monkeypatch):
    config = load_config(tmp_path)
    assert config["redteam"]["coverage_alert_threshold"] == 80.0

    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_COVERAGE_ALERT_THRESHOLD", "70")
    assert load_config(tmp_path)["redteam"]["coverage_alert_threshold"] == 70.0

    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_COVERAGE_ALERT_THRESHOLD", "bogus")
    assert load_config(tmp_path)["redteam"]["coverage_alert_threshold"] == 80.0

    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_COVERAGE_ALERT_THRESHOLD")
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"redteam": {"coverage_alert_threshold": 95.5}}), encoding="utf-8"
    )
    assert load_config(tmp_path)["redteam"]["coverage_alert_threshold"] == 95.5

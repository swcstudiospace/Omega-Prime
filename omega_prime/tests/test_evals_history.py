# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 84: history append/read round-trip, torn-tail repair, trend deltas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.evals.history import append_records, make_run_id, read_history
from omega_prime.evals.trend import (
    compare_baseline,
    find_regressions,
    per_case_trend,
)


def _seed_two_runs(path: Path) -> None:
    append_records(
        [{"case_id": "k", "score": 0.9, "criteria": []}],
        path=path,
        commit="c",
        run_id="c-t1",
        timestamp="t1",
    )
    append_records(
        [{"case_id": "k", "score": 0.5, "criteria": []}],
        path=path,
        commit="c",
        run_id="c-t2",
        timestamp="t2",
    )


def test_append_read_round_trip_preserves_order(tmp_path: Path):
    history = tmp_path / "history.ndjson"
    _seed_two_runs(history)
    records = read_history(history)
    assert [r["run_id"] for r in records] == ["c-t1", "c-t2"]
    assert [r["timestamp"] for r in records] == ["t1", "t2"]
    assert [r["score"] for r in records] == [0.9, 0.5]
    assert all(r["commit"] == "c" for r in records)


def test_make_run_id_combines_commit_and_timestamp():
    assert (
        make_run_id("abc123", "2026-10-13T00:00:00Z") == "abc123-2026-10-13T00:00:00Z"
    )


def test_truncated_tail_dropped_with_earlier_lines_intact(tmp_path: Path, capsys):
    history = tmp_path / "history.ndjson"
    _seed_two_runs(history)
    with history.open("a", encoding="utf-8") as handle:
        handle.write('{"run_id": "c-t3", "case_id": "k", "score": 0.')
    records = read_history(history)
    assert [r["run_id"] for r in records] == ["c-t1", "c-t2"]
    assert "dropping truncated last line" in capsys.readouterr().err


def test_trend_reports_regressed_case_and_delta(tmp_path: Path):
    history = tmp_path / "history.ndjson"
    _seed_two_runs(history)
    records = read_history(history)
    trends = per_case_trend(records)
    assert trends["k"]["first"] == pytest.approx(0.9)
    assert trends["k"]["last"] == pytest.approx(0.5)
    assert trends["k"]["min"] == pytest.approx(0.5)
    assert trends["k"]["mean"] == pytest.approx(0.7)
    assert trends["k"]["delta"] == pytest.approx(-0.4)
    regressed = find_regressions(records, threshold=0.1)
    assert regressed == [("k", pytest.approx(-0.4))]


def test_flat_history_reports_stable(tmp_path: Path):
    history = tmp_path / "history.ndjson"
    for stamp in ("t1", "t2"):
        append_records(
            [{"case_id": "k", "score": 0.9, "criteria": []}],
            path=history,
            commit="c",
            run_id=f"c-{stamp}",
            timestamp=stamp,
        )
    records = read_history(history)
    assert find_regressions(records, threshold=0.1) == []
    assert per_case_trend(records)["k"]["delta"] == pytest.approx(0.0)


def test_single_run_never_regresses(tmp_path: Path):
    history = tmp_path / "history.ndjson"
    append_records(
        [{"case_id": "k", "score": 0.2, "criteria": []}],
        path=history,
        commit="c",
        run_id="c-t1",
        timestamp="t1",
    )
    assert find_regressions(read_history(history), threshold=0.1) == []


def test_baseline_compare_names_below_baseline_drop():
    baseline = {
        "cases": [
            {"case_id": "k", "score": 0.9, "min_score": 0.8},
            {"case_id": "steady", "score": 1.0, "min_score": 1.0},
        ]
    }
    report = {
        "rubric_cases": [
            {"case_id": "k", "score": 0.5, "min_score": 0.8, "criteria": []},
            {"case_id": "steady", "score": 1.0, "min_score": 1.0, "criteria": []},
        ]
    }
    regressed, line = compare_baseline(baseline, report, threshold=0.1)
    assert regressed is True
    assert "k" in line
    assert "steady" not in line


def test_baseline_compare_below_min_score_is_regression():
    baseline = {"cases": [{"case_id": "k", "score": 0.9, "min_score": 0.9}]}
    report = {
        "rubric_cases": [
            {"case_id": "k", "score": 0.85, "min_score": 0.9, "criteria": []}
        ]
    }
    regressed, line = compare_baseline(baseline, report, threshold=0.1)
    assert regressed is True
    assert "min_score" in line


def test_baseline_compare_green_on_flat():
    baseline = {"cases": [{"case_id": "k", "score": 0.9, "min_score": 0.8}]}
    report = {
        "rubric_cases": [
            {"case_id": "k", "score": 0.9, "min_score": 0.8, "criteria": []}
        ]
    }
    regressed, _ = compare_baseline(baseline, report, threshold=0.1)
    assert regressed is False


def test_append_repairs_torn_tail_instead_of_fusing(tmp_path: Path):
    path = tmp_path / "history.ndjson"
    good = '{"run_id":"a","commit":"c","case_id":"k","score":0.9,"criteria":[],"timestamp":"t"}'
    torn = '{"run_id":"b","commit":"c","case_id":'  # crash mid-write
    path.write_text(good + "\n" + torn, encoding="utf-8")
    record = {
        "case_id": "k2",
        "score": 0.8,
        "criteria": [],
    }
    append_records([record], str(path), commit="c2", timestamp="t2")
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2  # torn line dropped, not fused
    rows = [json.loads(line) for line in lines]  # every line parses
    assert rows[0]["case_id"] == "k" and rows[0]["run_id"] == "a"
    assert rows[1]["case_id"] == "k2" and rows[1]["run_id"] != "a"

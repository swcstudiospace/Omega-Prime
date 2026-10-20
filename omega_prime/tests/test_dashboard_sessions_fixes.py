# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Regression tests for OMP dashboard session collection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from omega_prime.dashboard.collectors.omp_sessions import make_omp_sessions_collector
from omega_prime.dashboard.model import SourceResult, normalize_tasks

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
_STAMP = "2026-10-21T11:59:00+00:00"


def _write_session(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _collect(path: Path) -> dict[str, object]:
    collect = make_omp_sessions_collector(root=path.parent, now_fn=lambda: _NOW)
    return collect().items[0]


def test_model_turn_stop_reason_does_not_end_session(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "active.jsonl"
    _write_session(
        path,
        [
            {"type": "session", "data": {"id": "active", "cwd": "/work"}},
            {
                "type": "model_usage",
                "timestamp": _STAMP,
                "data": {"stopReason": "toolUse"},
            },
        ],
    )

    row = _collect(path)

    assert "stop_reason" not in row


def test_signal_custom_record_stops_session(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "stopped.jsonl"
    _write_session(
        path,
        [
            {"type": "session", "data": {"id": "stopped"}},
            {
                "type": "model_usage",
                "timestamp": _STAMP,
                "data": {"stopReason": "toolUse"},
            },
            {
                "type": "custom",
                "data": {
                    "kind": "signal",
                    "reason": "sigterm",
                    "recordedAt": _STAMP,
                },
            },
        ],
    )

    assert _collect(path)["stop_reason"] == "stopped"


def test_normal_custom_record_uses_reason(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "normal.jsonl"
    _write_session(
        path,
        [
            {"type": "session", "data": {"id": "normal"}},
            {
                "type": "custom",
                "data": {"kind": "normal", "reason": "dispose"},
            },
        ],
    )

    assert _collect(path)["stop_reason"] == "dispose"


def test_session_cwd_reaches_normalizer_without_process(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "cwd.jsonl"
    _write_session(
        path,
        [
            {
                "type": "session",
                "timestamp": _STAMP,
                "data": {"id": "cwd-session", "cwd": "/work"},
            }
        ],
    )
    row = _collect(path)
    snapshot = normalize_tasks(
        {
            "omp_session": SourceResult("omp_sessions", "ok", items=[row]),
            "omp_proc": SourceResult("omp_proc", "empty", items=[]),
        },
        now=_NOW,
    )

    assert row["cwd"] == "/work"
    assert len(snapshot.active) == 1
    assert snapshot.active[0].state == "stopped?"


def test_unchanged_session_file_uses_cached_row(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "cached.jsonl"
    _write_session(path, [{"type": "session", "data": {"id": "cached"}}])
    collect = make_omp_sessions_collector(root=path.parent, now_fn=lambda: _NOW)

    first = collect()
    second = collect()

    assert second.items[0] is first.items[0]


def test_large_session_file_keeps_header_cwd_from_file_start(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions" / "large.jsonl"
    records: list[dict[str, object]] = [
        {"type": "session", "timestamp": _STAMP, "data": {"id": "big", "cwd": "/work"}}
    ]
    records.extend(
        {"type": "message", "data": {"n": index, "filler": "x" * 60}}
        for index in range(4000)
    )
    records.append({"type": "model_usage", "timestamp": _STAMP, "data": {"model": "m"}})
    _write_session(path, records)

    row = _collect(path)

    assert row["cwd"] == "/work"
    assert row["session_id"] == "big"
    assert row["started_at"] == _STAMP
    assert "stop_reason" not in row


def test_small_file_without_session_record_stays_headerless(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions" / "headerless.jsonl"
    _write_session(
        path,
        [{"type": "model_usage", "timestamp": _STAMP, "data": {"model": "m"}}],
    )

    row = _collect(path)

    assert "cwd" not in row
    assert "session_id" not in row

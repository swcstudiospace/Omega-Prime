# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""finding/1 JSONL export and the chat --jsonl switch. No tracker writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.commands import redteam_findings
from sidecar.findings import (
    FINDING_SCHEMA,
    export_findings_jsonl,
    record_finding,
    render_findings_jsonl,
)

_ALLOWED = {"schema", "case_id", "severity", "dedupe_hash", "synced"}


def _row(directory: Path, case_id: str, response: str) -> dict:
    return record_finding(
        case_id,
        "abuse",
        "high",
        response,
        "detected",
        response,
        directory=directory,
    )


def test_empty_rows_write_a_finished_file_with_no_objects(tmp_path: Path) -> None:
    destination = tmp_path / "findings.jsonl"
    export_findings_jsonl([], destination)
    text = destination.read_text(encoding="utf-8")
    assert text == "\n"
    assert render_findings_jsonl([]) == "\n"
    assert [line for line in text.splitlines() if line.strip()] == []


def test_export_rewrites_a_regular_file_and_leaves_no_temp(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    stored = _row(reports, "abuse-01", "loopback body")
    stored["issue_url"] = "https://example.invalid/issues/1"
    stored["response"] = "raw gateway body must not leak"
    destination = tmp_path / "findings.jsonl"
    destination.write_text("OLD\n", encoding="utf-8")

    export_findings_jsonl([stored], destination)

    text = destination.read_text(encoding="utf-8")
    assert text != "OLD\n"
    assert text.endswith("\n")
    objects = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(objects) == 1
    assert set(objects[0]) == _ALLOWED
    assert objects[0]["schema"] == FINDING_SCHEMA == "finding/1"
    assert objects[0]["case_id"] == "abuse-01"
    assert objects[0]["severity"] == "high"
    assert objects[0]["dedupe_hash"] == stored["dedupe_hash"]
    assert objects[0]["synced"] is True
    assert "raw gateway" not in text
    assert str(tmp_path) not in text
    assert [path.name for path in tmp_path.iterdir() if path.is_file()] == [
        "findings.jsonl"
    ]


def test_unsynced_row_sets_synced_false(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    stored = _row(reports, "cost-01", "second")
    text = render_findings_jsonl([stored])
    obj = json.loads(text.strip())
    assert obj["synced"] is False
    assert "issue_url" not in obj


def test_symlink_destination_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.jsonl"
    target.write_text("KEEP\n", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises(OSError, match="symlink"):
        export_findings_jsonl(
            [{"case_id": "x", "severity": "low", "dedupe_hash": "ab"}],
            link,
        )
    assert target.read_text(encoding="utf-8") == "KEEP\n"
    assert link.is_symlink()


def test_chat_jsonl_renders_text_and_creates_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _row(reports, "abuse-01", "first")
    _row(reports, "abuse-02", "second")
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(reports))
    before = sorted(path.name for path in tmp_path.rglob("*"))

    body = redteam_findings.run("--limit 1 --jsonl", {})

    assert set(body) == {"findings", "total", "limit", "jsonl"}
    assert body["limit"] == 1
    assert body["total"] == 2
    assert len(body["findings"]) == 1
    assert body["findings"][0]["case_id"] == "abuse-02"
    objects = [json.loads(line) for line in body["jsonl"].splitlines() if line.strip()]
    assert len(objects) == 1
    assert objects[0]["case_id"] == "abuse-02"
    assert str(tmp_path) not in body["jsonl"]
    assert sorted(path.name for path in tmp_path.rglob("*")) == before
    plain = redteam_findings.run("--jsonl", {})
    assert "jsonl" not in redteam_findings.run("", {})
    assert plain["jsonl"].count("\n") == 2


def test_bad_flags_write_nothing(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    before = sorted(path.name for path in tmp_path.rglob("*"))
    with pytest.raises(redteam_findings.Error) as caught:
        redteam_findings.run("--jsonl --nope", {})
    assert caught.value.error == "bad_args"
    assert caught.value.result["usage"] == redteam_findings.USAGE
    assert "--jsonl" in redteam_findings.USAGE
    with pytest.raises(redteam_findings.Error) as limited:
        redteam_findings.run("--limit abc", {})
    assert limited.value.error == "bad_limit"
    assert limited.value.result["usage"] == redteam_findings.USAGE
    assert sorted(path.name for path in tmp_path.rglob("*")) == before

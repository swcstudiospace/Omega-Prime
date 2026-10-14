"""RoE grant audit log: append-only JSONL create/expire/revoke rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from omega_prime.grokbot.roe_lifecycle import check_roe_lifecycle, main
from omega_prime.redteam import roe_audit
from omega_prime.redteam.roe import sign_artifact

END = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
KEY = b"test-key-123"


def _document(**overrides) -> dict:
    start = (END - timedelta(days=30)).isoformat(timespec="seconds")
    document = {
        "schema_version": 1,
        "operator": "test-operator",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": start,
        "effective_until": END.isoformat(timespec="seconds"),
        "case_classes": ["prompt-injection"],
    }
    document.update(overrides)
    return document


def _write_artifact(tmp_path, document=None) -> Path:
    artifact = tmp_path / "roe.json"
    artifact.write_text(
        json.dumps(document if document is not None else _document()),
        encoding="utf-8",
    )
    return artifact


def _row_keys(row) -> None:
    assert set(row) >= {
        "timestamp",
        "event",
        "artifact_path",
        "operator",
        "detail",
    }


def test_sign_artifact_appends_created_row(tmp_path):
    artifact = _write_artifact(tmp_path)
    log = tmp_path / "audit.jsonl"
    sign_artifact(artifact, KEY, audit_log=log)
    rows = roe_audit.read_log(log)
    assert len(rows) == 1
    _row_keys(rows[0])
    assert rows[0]["event"] == roe_audit.EVENT_CREATED
    assert rows[0]["artifact_path"] == str(artifact)
    assert rows[0]["operator"] == "test-operator"


def test_sign_artifact_without_audit_log_still_signs(tmp_path):
    artifact = _write_artifact(tmp_path)
    sig = sign_artifact(artifact, KEY)
    assert sig.is_file()


def test_expired_check_appends_expired_row(tmp_path):
    artifact = _write_artifact(tmp_path)
    log = tmp_path / "audit.jsonl"
    after = END + timedelta(days=1)
    result = check_roe_lifecycle(str(artifact), after, audit_log=log)
    assert result["state"] == "expired"
    rows = roe_audit.read_log(log)
    assert len(rows) == 1
    _row_keys(rows[0])
    assert rows[0]["event"] == roe_audit.EVENT_EXPIRED
    assert rows[0]["operator"] == "test-operator"


def test_valid_check_appends_nothing(tmp_path):
    artifact = _write_artifact(tmp_path)
    log = tmp_path / "audit.jsonl"
    result = check_roe_lifecycle(str(artifact), END - timedelta(days=10))
    assert result["state"] == "valid"
    # explicit audit_log on a valid grant: no row
    result = check_roe_lifecycle(str(artifact), END - timedelta(days=10), audit_log=log)
    assert result["state"] == "valid"
    assert roe_audit.read_log(log) == []


def test_revoke_removes_sig_and_appends_row(tmp_path):
    artifact = _write_artifact(tmp_path)
    sign_artifact(artifact, KEY)
    log = tmp_path / "audit.jsonl"
    assert roe_audit.revoke_artifact(str(artifact), audit_log=log) is True
    rows = roe_audit.read_log(log)
    assert len(rows) == 1
    assert rows[0]["event"] == roe_audit.EVENT_REVOKED
    assert rows[0]["artifact_path"] == str(artifact)


def test_read_log_skips_corrupt_lines_and_missing(tmp_path):
    log = tmp_path / "audit.jsonl"
    assert roe_audit.read_log(log) == []
    assert roe_audit.read_log(None) == []
    log.write_text('{"event": 1}\nnot-json\n', encoding="utf-8")
    rows = roe_audit.read_log(log)
    assert rows == [{"event": 1}]


def test_log_write_failure_warns_and_skips(tmp_path):
    import warnings

    artifact = _write_artifact(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.mkdir()  # opening a directory for append raises OSError
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert roe_audit.append_event(blocker, "grant-created") is None
    assert any("audit" in str(w.message).lower() for w in caught)
    # sign_artifact still succeeds when the audit path is unwritable
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sig = sign_artifact(artifact, KEY, audit_log=blocker)
    assert sig.is_file()


def test_cli_audit_log_flag_includes_rows(tmp_path, capsys):
    artifact = _write_artifact(tmp_path)
    log = tmp_path / "audit.jsonl"
    now = (END + timedelta(days=2)).isoformat()
    code = main(
        [
            "check",
            "--artifact",
            str(artifact),
            "--now",
            now,
            "--json",
            "--audit-log",
            str(log),
        ]
    )
    assert code == 3  # expired
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "expired"
    assert len(payload["audit_log"]) == 1
    assert payload["audit_log"][0]["event"] == roe_audit.EVENT_EXPIRED

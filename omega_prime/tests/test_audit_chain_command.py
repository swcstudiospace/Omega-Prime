# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-audit`` verifies a temp audit log and never creates one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.commands.audit_chain import USAGE, Error, run
from omega_prime.grokbot.audit import GrokBotAuditTracer, default_audit_path

_TOKEN_TOOL = "sk-" + ("a" * 24)


def _env(path: Path) -> dict[str, str]:
    return {"OMEGA_PRIME_AUDIT_LOG": str(path)}


def test_missing_log_is_empty_and_creates_nothing(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "audit.jsonl"
    first = run("", _env(target))
    second = run("--tail 3", _env(target))
    assert first == {"ok": False, "verified": 0, "status": "empty"}
    assert second == first
    assert not target.exists()
    assert not target.parent.exists()


def test_default_state_dir_missing_creates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "state"
    result = run("", {"OMEGA_PRIME_STATE_DIR": str(root)})
    assert result == {"ok": False, "verified": 0, "status": "empty"}
    assert not default_audit_path(root).exists()
    assert not root.exists()


def test_valid_chain_verifies_and_tail_drops_secrets(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    tracer = GrokBotAuditTracer(log_file)
    tracer.log_event(
        "tool_call",
        tool_name="read_file",
        caller="grok",
        status="ok",
        details={"token": "sensitive12345"},
    )
    tracer.log_event(
        "tool_call",
        tool_name=_TOKEN_TOOL,
        caller="operator",
        status="ok",
        details={"note": "plain"},
    )
    before = log_file.read_bytes()

    verified = run("", _env(log_file))
    tailed = run("--tail 2", _env(log_file))
    again = run("--tail 2", _env(log_file))

    assert verified == {"ok": True, "verified": 2, "status": "verified"}
    assert "tail" not in verified
    assert tailed["ok"] is True
    assert tailed["verified"] == 2
    assert tailed["status"] == "verified"
    assert tailed["tail"][0]["tool_name"] == "read_file"
    assert set(tailed["tail"][0]) <= {
        "seq",
        "timestamp",
        "event",
        "tool_name",
        "status",
    }
    assert "details" not in tailed["tail"][0]
    assert "hash" not in tailed["tail"][0]
    assert "caller" not in tailed["tail"][0]
    assert "tool_name" not in tailed["tail"][1]
    assert _TOKEN_TOOL not in str(tailed)
    assert "sensitive12345" not in str(tailed)
    assert again == tailed
    assert log_file.read_bytes() == before


def test_flipped_byte_is_broken_and_omits_tail(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    tracer = GrokBotAuditTracer(log_file)
    tracer.log_event("tool_call", tool_name="read_file", status="ok")
    tracer.log_event("tool_call", tool_name="run_terminal", status="ok")
    raw = bytearray(log_file.read_bytes())
    raw[raw.index(b"\n") // 2] ^= 0x01
    log_file.write_bytes(raw)
    before = log_file.read_bytes()

    result = run("--tail 5", _env(log_file))

    assert result["ok"] is False
    assert result["status"] == "broken"
    assert result["verified"] == 0
    assert isinstance(result["detail"], str) and result["detail"]
    assert "tail" not in result
    assert "read_file" not in str(result)
    assert log_file.read_bytes() == before


def test_tampered_rotated_backup_is_broken(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    tracer = GrokBotAuditTracer(log_file, max_bytes=1, backups=2)
    tracer.log_event("tool_call", tool_name="read_file", status="ok")
    tracer.log_event("tool_call", tool_name="read_file", status="ok")
    tracer.log_event("tool_call", tool_name="read_file", status="ok")
    backup = tmp_path / "audit.jsonl.1"
    assert backup.is_file()
    record = json.loads(backup.read_text(encoding="utf-8").splitlines()[0])
    record["caller"] = "mallory"
    backup.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert GrokBotAuditTracer(log_file).verify_integrity()[0] is True

    result = run("", _env(log_file))

    assert result["ok"] is False
    assert result["status"] == "broken"
    assert "tail" not in result
    assert "mallory" not in str(result)


def test_bad_args_do_not_create_a_file(tmp_path: Path) -> None:
    target = tmp_path / "audit.jsonl"
    for args in ("--tail", "--tail 0", "--tail 21", "--tail no", "--limit 1", "verify"):
        with pytest.raises(Error) as caught:
            run(args, _env(target))
        assert caught.value.error == "bad_args"
        assert caught.value.result == {"usage": USAGE}
    assert not target.exists()

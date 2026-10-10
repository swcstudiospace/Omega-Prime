# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the redacted Grok Bot diagnostics bundle collector."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path

import pytest

from omega_prime import __version__ as PACKAGE_VERSION
from omega_prime.grokbot.audit import GrokBotAuditTracer
from omega_prime.grokbot.diag_bundle import BUNDLE_FILENAME, collect_bundle
from omega_prime.grokbot.doctor import DiagnosticCheck
from omega_prime.grokbot.secret_guard import SecretLeakError

#: Recognized-prefix fixture, assembled at runtime from a fixed suffix.
#: Long enough (>= 16) for the audit scrubber and the secret gate alike.
_FIXED_OMK_TOKEN = "omk_" + "7QwX4mZ8VbN19f2Kd"

#: Fixed diverse lowercase-plus-digit run (all 26 letters + 10 digits) and a
#: slash-bearing path carrying it. Trips the generic gate heuristic; no audit
#: pattern scrubs it, so the collector scan is the boundary that must abort.
_GENERIC_RUN = "abcdefghijklmnopqrstuvwxyz0123456789"
_GENERIC_PATH = "/cache/" + _GENERIC_RUN + "/manifest.json"

_MANIFEST_MARKER = "grokbot-fixture-marker-7f3a"
_SENTINEL_BYTES = b'{"sentinel": true}\n'


def _checks() -> list[DiagnosticCheck]:
    return [
        DiagnosticCheck(
            id="python_version",
            category="env",
            title="Python version",
            status="PASS",
            message="ok",
        )
    ]


def _checks_with_message(message: str) -> list[DiagnosticCheck]:
    return [
        DiagnosticCheck(
            id="disk_space",
            category="env",
            title="Disk space",
            status="FAIL",
            message=message,
        )
    ]


def _manifest_file(tmp_path: Path) -> Path:
    target = tmp_path / "manifest.json"
    target.write_text(
        json.dumps({"name": _MANIFEST_MARKER, "version": "1.0.0"}),
        encoding="utf-8",
    )
    return target


def _secret_bearing_manifest_file(tmp_path: Path) -> Path:
    """Manifest whose *contents* carry a synthetic secret (clean basename)."""
    target = tmp_path / "manifest.json"
    target.write_text(
        json.dumps(
            {
                "name": _MANIFEST_MARKER,
                "version": "1.0.0",
                "notes": f"rotation pending for {_FIXED_OMK_TOKEN}",
            }
        ),
        encoding="utf-8",
    )
    return target


def _supervisor_state(tmp_path: Path, *, last_error: str | None = None) -> Path:
    target = tmp_path / "supervisor.json"
    target.write_text(
        json.dumps(
            {
                "running": False,
                "pid": None,
                "restarts": 0,
                "uptime_seconds": 0.0,
                "last_exit_code": None,
                "last_error": last_error,
            }
        ),
        encoding="utf-8",
    )
    return target


def _audit_log(tmp_path: Path) -> Path:
    target = tmp_path / "audit.jsonl"
    tracer = GrokBotAuditTracer(target)
    tracer.log_event("tool_call", tool_name="todo_read", details={"args": "clean"})
    tracer.log_event("tool_call", tool_name="todo_read", details={"args": "clean"})
    return target


def _audit_log_with_blob(tmp_path: Path, blob: str) -> Path:
    """Historical audit record carrying an unscanned-at-write string."""
    target = tmp_path / "audit.jsonl"
    tracer = GrokBotAuditTracer(target)
    tracer.log_event("tool_call", tool_name="todo_read", details={"args": "clean"})
    tracer.log_event("tool_call", tool_name="cache_read", details={"cache_path": blob})
    return target


def _collect(tmp_path: Path, **kwargs) -> Path:
    # Lazy defaults: an explicitly supplied input file must never be
    # overwritten by fixture creation for another parameter.
    if "manifest_path" not in kwargs:
        kwargs["manifest_path"] = _manifest_file(tmp_path)
    if "audit_path" not in kwargs:
        kwargs["audit_path"] = _audit_log(tmp_path)
    if "supervisor_state_path" not in kwargs:
        kwargs["supervisor_state_path"] = _supervisor_state(tmp_path)
    if "doctor_checks" not in kwargs:
        kwargs["doctor_checks"] = _checks()
    return collect_bundle(tmp_path / "bundle", **kwargs)


def _inputs_with_generic_path(tmp_path: Path, source: str) -> dict:
    """Collector inputs with the token-shaped path in one serialized class."""
    if source == "doctor":
        return {"doctor_checks": _checks_with_message(f"read {_GENERIC_PATH}")}
    if source == "supervisor":
        return {
            "supervisor_state_path": _supervisor_state(
                tmp_path, last_error=f"read {_GENERIC_PATH}"
            )
        }
    if source == "audit":
        return {"audit_path": _audit_log_with_blob(tmp_path, _GENERIC_PATH)}
    raise AssertionError(f"unknown secret source: {source}")


@pytest.mark.parametrize("source", ["doctor", "supervisor", "audit"])
def test_token_shaped_path_aborts_before_create_or_replace(tmp_path: Path, source: str):
    """A token-shaped serialized input aborts before any bundle write.

    Proven against real on-disk effects: no new file appears, and a
    preexisting sentinel bundle keeps byte-identical contents.
    """
    out_dir = tmp_path / "bundle"
    kwargs: dict = {
        "manifest_path": _manifest_file(tmp_path),
        "audit_path": _audit_log(tmp_path),
        "supervisor_state_path": _supervisor_state(tmp_path),
        "doctor_checks": _checks(),
    }
    kwargs.update(_inputs_with_generic_path(tmp_path, source))
    with pytest.raises(SecretLeakError):
        collect_bundle(out_dir, **kwargs)
    assert not (out_dir / BUNDLE_FILENAME).exists()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / BUNDLE_FILENAME).write_bytes(_SENTINEL_BYTES)
    kwargs.update(_inputs_with_generic_path(tmp_path, source))
    with pytest.raises(SecretLeakError):
        collect_bundle(out_dir, **kwargs)
    assert (out_dir / BUNDLE_FILENAME).read_bytes() == _SENTINEL_BYTES


def test_bundle_assembles_from_fixtures(tmp_path: Path):
    out = _collect(tmp_path)
    bundle = json.loads(out.read_text(encoding="utf-8"))
    assert bundle["doctor"]["passed"] is True
    assert bundle["manifest"]["present"] is True
    assert bundle["supervisor"]["present"] is True
    assert bundle["supervisor"]["state"]["restarts"] == 0
    assert bundle["audit"]["count"] == 2


def test_clean_bundle_is_private_and_excludes_raw_manifest(tmp_path: Path):
    """Success path: intended sections, private mode, content exclusion.

    The input manifest carries synthetic secret bytes, but only its
    basename/digest/size may enter the bundle -- never its contents.
    """
    manifest = _secret_bearing_manifest_file(tmp_path)
    raw = manifest.read_bytes()
    out = _collect(tmp_path, manifest_path=manifest)
    bundle = json.loads(out.read_text(encoding="utf-8"))
    assert bundle["manifest"]["present"] is True
    assert bundle["manifest"]["path"] == "manifest.json"
    assert bundle["manifest"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert bundle["manifest"]["size_bytes"] == len(raw)
    assert bundle["doctor"]["passed"] is True
    assert bundle["supervisor"]["present"] is True
    assert bundle["audit"]["count"] == 2
    # Digest + metadata only: the manifest content itself is never embedded.
    assert "content" not in bundle["manifest"]
    bundle_bytes = out.read_bytes()
    assert _MANIFEST_MARKER.encode() not in bundle_bytes
    assert _FIXED_OMK_TOKEN.encode() not in bundle_bytes
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert stat.S_IMODE(out.parent.stat().st_mode) == 0o700


def test_recognized_token_in_path_valued_audit_detail_is_redacted(
    tmp_path: Path,
):
    """Real audit tracer scrubs a recognized token inside a Path detail."""
    target = tmp_path / "audit.jsonl"
    tracer = GrokBotAuditTracer(target)
    token_path = Path(f"/var/cache/report-{_FIXED_OMK_TOKEN}.json")
    stored = tracer.log_event(
        "tool_call", tool_name="cache_read", details={"report": token_path}
    )
    assert _FIXED_OMK_TOKEN not in json.dumps(stored, sort_keys=True)
    recent = tracer.read_recent(10)
    assert recent, "expected the logged record to be readable"
    assert _FIXED_OMK_TOKEN not in json.dumps(recent, sort_keys=True)
    log_bytes = target.read_bytes()
    assert _FIXED_OMK_TOKEN.encode() not in log_bytes


def test_output_contains_versions_and_digests(tmp_path: Path):
    from omega_prime.mcp_server import SERVER_VERSION

    manifest = _manifest_file(tmp_path)
    raw = manifest.read_bytes()
    out = _collect(tmp_path, manifest_path=manifest)
    bundle = json.loads(out.read_text(encoding="utf-8"))
    assert bundle["versions"] == {"server": SERVER_VERSION, "package": PACKAGE_VERSION}
    assert bundle["manifest"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert bundle["manifest"]["size_bytes"] == len(raw)
    # Digest + metadata only: the manifest content itself is never embedded.
    assert "content" not in bundle["manifest"]
    assert _MANIFEST_MARKER not in out.read_text(encoding="utf-8")


def test_no_token_bytes_in_bundle_dir(tmp_path: Path):
    out = _collect(tmp_path)
    for entry in out.parent.iterdir():
        if entry.is_file():
            assert _FIXED_OMK_TOKEN.encode() not in entry.read_bytes()
            assert _GENERIC_RUN.encode() not in entry.read_bytes()


def test_doctor_skipped_without_fixtures(tmp_path: Path, monkeypatch) -> None:
    """No fixtures means a skipped doctor section, never a live host probe."""
    monkeypatch.setattr(
        "omega_prime.grokbot.doctor.run_doctor_checks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live doctor must not run")
        ),
    )
    out = collect_bundle(
        tmp_path / "bundle",
        manifest_path=_manifest_file(tmp_path),
        audit_path=_audit_log(tmp_path),
        supervisor_state_path=_supervisor_state(tmp_path),
    )
    bundle = json.loads(out.read_text(encoding="utf-8"))
    assert bundle["doctor"] == {"present": False, "status": "skipped"}


def test_server_version_unavailable_when_import_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    """Blocking the heavy import reports an unavailable server version."""
    monkeypatch.setitem(sys.modules, "omega_prime.mcp_server", None)
    out = collect_bundle(
        tmp_path / "bundle",
        manifest_path=_manifest_file(tmp_path),
        audit_path=_audit_log(tmp_path),
        supervisor_state_path=_supervisor_state(tmp_path),
        doctor_checks=_checks(),
    )
    bundle = json.loads(out.read_text(encoding="utf-8"))
    assert bundle["versions"] == {"server": "unavailable", "package": PACKAGE_VERSION}

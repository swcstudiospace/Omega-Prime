"""RoE validator tests: five fail-closed modes plus fixture-signed IDLE."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sidecar import harness
from sidecar.roe import (
    CASE_CLASSES,
    authorize,
    sign_artifact,
    validate_schema,
    validate_targets,
)

FIXTURE_KEY = hashlib.sha256(b"omega-prime fixture - not a secret").hexdigest().encode()


def _window(days_from_now: int = -1, days_to_now: int = 7) -> tuple[str, str]:
    now = datetime.now(UTC)
    start = (now + timedelta(days=days_from_now)).isoformat(timespec="seconds")
    end = (now + timedelta(days=days_to_now)).isoformat(timespec="seconds")
    return start, end


def _document(**overrides) -> dict:
    start, end = _window()
    document = {
        "schema_version": 1,
        "operator": "test-operator",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": start,
        "effective_until": end,
        "case_classes": ["prompt-injection"],
    }
    document.update(overrides)
    return document


def _write_signed(tmp_path, document: dict | None = None, key: bytes = FIXTURE_KEY):
    document = document if document is not None else _document()
    artifact = tmp_path / "roe.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    sign_artifact(artifact, key)
    return artifact


def _harness_env(monkeypatch, tmp_path, **overrides):
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_KILL", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_ENABLED", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_ROE_REQUIRED", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_ROE_FILE", raising=False)
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_ROE_KEY", raising=False)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)


def test_missing_artifact_fails_closed(tmp_path):
    decision = authorize(tmp_path / "absent.json", key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-missing")
    decision = authorize("", key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-missing")


def test_expired_artifact_fails_closed(tmp_path):
    start, end = _window(days_from_now=-9, days_to_now=-1)
    artifact = _write_signed(
        tmp_path, _document(effective_from=start, effective_until=end)
    )
    decision = authorize(artifact, key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-expired")
    assert "expired" in decision.detail


def test_tampered_payload_fails_closed(tmp_path):
    artifact = _write_signed(tmp_path)
    document = json.loads(artifact.read_text(encoding="utf-8"))
    document["operator"] = "mallory"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    decision = authorize(artifact, key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-invalid")
    assert "signature" in decision.detail


def test_unsigned_artifact_fails_closed(tmp_path):
    artifact = tmp_path / "roe.json"
    artifact.write_text(json.dumps(_document()), encoding="utf-8")
    decision = authorize(artifact, key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-invalid")
    assert "unsigned" in decision.detail
    signed = _write_signed(tmp_path)
    decision = authorize(signed, key=None)
    assert (decision.ok, decision.cause) == (False, "roe-invalid")


def test_wrong_key_fails_closed(tmp_path):
    artifact = _write_signed(tmp_path)
    decision = authorize(artifact, key=b"wrong-key")
    assert (decision.ok, decision.cause) == (False, "roe-invalid")


def test_schema_violation_fails_closed(tmp_path):
    document = _document()
    del document["operator"]
    artifact = _write_signed(tmp_path, document)
    decision = authorize(artifact, key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-invalid")
    assert "schema" in decision.detail


def test_non_loopback_target_fails_closed(tmp_path):
    document = _document(
        authorized_targets=[{"host": "10.9.8.7", "tools": ["tempest_status"]}]
    )
    assert validate_targets(document)
    artifact = _write_signed(tmp_path, document)
    decision = authorize(artifact, key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-invalid")
    assert "loopback" in decision.detail


def test_unknown_case_class_fails_closed(tmp_path):
    document = _document(case_classes=["prompt-injection", "mind-control"])
    assert validate_schema(document)
    artifact = _write_signed(tmp_path, document)
    decision = authorize(artifact, key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-invalid")


def test_fixture_signed_artifact_reaches_idle(tmp_path):
    artifact = _write_signed(tmp_path)
    decision = authorize(artifact, key=FIXTURE_KEY)
    assert decision.ok and decision.cause == "ok"
    assert "test-operator" in decision.detail


def test_harness_check_default_config_exits_blocked(monkeypatch, tmp_path, capsys):
    _harness_env(monkeypatch, tmp_path)
    assert harness.main(["--check"]) == 3
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "disabled" in out


def test_harness_check_enabled_without_roe_exits_blocked(monkeypatch, tmp_path, capsys):
    _harness_env(monkeypatch, tmp_path, OMEGA_PRIME_REDTEAM_ENABLED="1")
    assert harness.main(["--check"]) == 3
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "roe-missing" in out


def test_harness_check_valid_roe_reports_idle(monkeypatch, tmp_path, capsys):
    artifact = _write_signed(tmp_path)
    _harness_env(
        monkeypatch,
        tmp_path,
        OMEGA_PRIME_REDTEAM_ENABLED="1",
        OMEGA_PRIME_REDTEAM_ROE_FILE=str(artifact),
        OMEGA_PRIME_REDTEAM_ROE_KEY=FIXTURE_KEY.decode(),
    )
    assert harness.main(["--check"]) == 0
    assert "IDLE" in capsys.readouterr().out


def test_harness_check_kill_switch_wins_over_valid_roe(monkeypatch, tmp_path, capsys):
    artifact = _write_signed(tmp_path)
    _harness_env(
        monkeypatch,
        tmp_path,
        OMEGA_PRIME_REDTEAM_ENABLED="1",
        OMEGA_PRIME_REDTEAM_ROE_FILE=str(artifact),
        OMEGA_PRIME_REDTEAM_ROE_KEY=FIXTURE_KEY.decode(),
        OMEGA_PRIME_REDTEAM_KILL="1",
    )
    assert harness.main(["--check"]) == 3
    assert "kill-switch-set" in capsys.readouterr().out


def test_case_classes_match_planned_six():
    assert set(CASE_CLASSES) == {
        "prompt-injection",
        "jailbreak",
        "abuse",
        "malformed-input",
        "auth-bypass",
        "cost-abuse",
    }


def test_sidecar_wrapper_reexports_canonical_runtime_module():
    """sidecar.roe is the harness-side wrapper over omega_prime.redteam.roe."""
    import omega_prime.redteam.roe as canonical
    import sidecar.roe as wrapper

    for name in (
        "CASE_CLASSES",
        "CAUSE_EXPIRED",
        "CAUSE_INVALID",
        "CAUSE_MISSING",
        "LOOPBACK_HOSTS",
        "REQUIRED_KEYS",
        "SCHEMA_VERSION",
        "RoeDecision",
        "authorize",
        "canonical_bytes",
        "sign_artifact",
        "signature_path",
        "validate_schema",
        "validate_targets",
        "verify_signature",
    ):
        assert getattr(wrapper, name) is getattr(canonical, name), name


def test_grant_allowlist_unions_targets_and_defaults_to_all():
    from omega_prime.redteam.roe import grant_tool_allowlist

    assert grant_tool_allowlist({"authorized_targets": [{"host": "127.0.0.1"}]}) is None
    assert grant_tool_allowlist(
        {
            "authorized_targets": [
                {"host": "127.0.0.1", "tools": ["tempest_status"]},
                {"host": "localhost", "tools": ["tempest_report"]},
            ]
        }
    ) == {"tempest_status", "tempest_report"}
    assert grant_tool_allowlist("not-a-grant") is None


def test_grant_expired_detects_window_and_unparseable():
    from datetime import timedelta

    from omega_prime.redteam.roe import grant_expired

    now = datetime.now(UTC)
    past = (now - timedelta(days=1)).isoformat(timespec="seconds")
    future = (now + timedelta(days=1)).isoformat(timespec="seconds")
    assert grant_expired({"effective_until": past}) is True
    assert grant_expired({"effective_until": future}) is False
    assert grant_expired({"effective_until": "not-a-time"}) is None
    assert grant_expired(None) is None


def _write_signed_as(tmp_path, name, document, key=FIXTURE_KEY):
    """Signed artifact under an explicit filename (concurrent grants)."""
    artifact = tmp_path / name
    artifact.write_text(json.dumps(document), encoding="utf-8")
    sign_artifact(artifact, key)
    return artifact


def test_authorize_all_unions_two_grants(tmp_path):
    """H7 happy: two grants with different case classes both authorize."""
    from omega_prime.redteam.roe import authorize_all, grant_tool_allowlist

    first = _write_signed_as(
        tmp_path,
        "roe-a.json",
        _document(
            operator="alice",
            authorized_targets=[{"host": "127.0.0.1", "tools": ["tempest_status"]}],
            case_classes=["prompt-injection"],
        ),
    )
    second = _write_signed_as(
        tmp_path,
        "roe-b.json",
        _document(
            operator="bob",
            authorized_targets=[{"host": "localhost", "tools": ["tempest_report"]}],
            case_classes=["jailbreak"],
        ),
    )
    decision = authorize_all([first, second], key=FIXTURE_KEY)
    assert decision is not None
    assert decision.ok and decision.cause == "ok"
    assert "2/2" in decision.detail
    assert decision is not None and decision.roe is not None
    assert set(decision.roe["case_classes"]) == {"prompt-injection", "jailbreak"}
    assert grant_tool_allowlist(decision.roe) == {"tempest_status", "tempest_report"}


def test_authorize_all_survives_one_expiry(tmp_path):
    """H7 edge: one grant expiring leaves only the other's tools."""
    from omega_prime.redteam.roe import authorize_all, grant_tool_allowlist

    live = _write_signed_as(
        tmp_path,
        "roe-live.json",
        _document(
            authorized_targets=[{"host": "127.0.0.1", "tools": ["tempest_status"]}],
            case_classes=["prompt-injection"],
        ),
    )
    start, end = _window(days_from_now=-9, days_to_now=-1)
    stale = _write_signed_as(
        tmp_path,
        "roe-stale.json",
        _document(
            effective_from=start,
            effective_until=end,
            authorized_targets=[{"host": "localhost", "tools": ["tempest_report"]}],
            case_classes=["jailbreak"],
        ),
    )
    decision = authorize_all([live, stale], key=FIXTURE_KEY)
    assert decision is not None
    assert decision.ok and decision.cause == "ok"
    assert "1/2" in decision.detail
    assert grant_tool_allowlist(decision.roe) == {"tempest_status"}
    assert decision is not None and decision.roe is not None
    assert decision.roe["case_classes"] == ["prompt-injection"]


def test_authorize_all_single_and_empty_match_authorize(tmp_path):
    """H7 compat: one path (or none) behaves exactly like authorize()."""
    from omega_prime.redteam.roe import authorize_all

    signed = _write_signed(tmp_path)
    assert authorize_all(signed, key=FIXTURE_KEY).ok is True
    assert authorize_all([signed], key=FIXTURE_KEY).ok is True
    assert authorize_all([], key=FIXTURE_KEY).cause == "roe-missing"
    assert authorize_all(None, key=FIXTURE_KEY).cause == "roe-missing"


def test_authorize_all_failure_prefers_expired_over_missing(tmp_path):
    """H7 edge: with no valid grant the most informative cause wins."""
    from omega_prime.redteam.roe import authorize_all

    start, end = _window(days_from_now=-9, days_to_now=-1)
    stale = _write_signed_as(
        tmp_path, "roe-stale.json", _document(effective_from=start, effective_until=end)
    )
    decision = authorize_all([stale, tmp_path / "absent.json"], key=FIXTURE_KEY)
    assert (decision.ok, decision.cause) == (False, "roe-expired")
    missing = authorize_all(
        [tmp_path / "absent-a.json", tmp_path / "absent-b.json"], key=FIXTURE_KEY
    )
    assert (missing.ok, missing.cause) == (False, "roe-missing")

"""Tests for the v10 config surface (omega_prime/config.py)."""

from __future__ import annotations

import json

from omega_prime.config import (
    PRIME_FAMILIES,
    family_config,
    load_config,
    prime_enabled,
)


def test_defaults_all_families_off(tmp_path):
    config = load_config(tmp_path)
    for family in PRIME_FAMILIES:
        assert prime_enabled(config, family) is False


def test_missing_file_is_defaults(tmp_path):
    config = load_config(tmp_path / "nowhere")
    assert prime_enabled(config, "rlm") is False


def test_file_enables_family(tmp_path):
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"prime": {"rlm": {"enabled": True}}})
    )
    config = load_config(tmp_path)
    assert prime_enabled(config, "rlm") is True
    assert prime_enabled(config, "harness") is False


def test_env_override_enables_family(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_PRIME_PRIME_RLM_ENABLED", "1")
    config = load_config(tmp_path)
    assert prime_enabled(config, "rlm") is True


def test_env_override_beats_file(tmp_path, monkeypatch):
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"prime": {"rlm": {"enabled": True}}})
    )
    monkeypatch.setenv("OMEGA_PRIME_PRIME_RLM_ENABLED", "0")
    config = load_config(tmp_path)
    assert prime_enabled(config, "rlm") is False


def test_unknown_family_is_disabled(tmp_path):
    config = load_config(tmp_path)
    assert prime_enabled(config, "nonexistent") is False


def test_family_config_returns_subtree(tmp_path):
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"prime": {"autonomous": {"enabled": True, "max_turns": 8}}})
    )
    config = load_config(tmp_path)
    assert family_config(config, "autonomous")["max_turns"] == 8
    # Defaults pre-populate every family as disabled.
    assert family_config(config, "rlm") == {"enabled": False}


def test_non_object_file_rejected(tmp_path):
    (tmp_path / "omega-prime.json").write_text("[1, 2]")
    try:
        load_config(tmp_path)
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_sandbox_defaults_off(tmp_path):
    config = load_config(tmp_path)
    assert config["sandbox"] == {
        "enabled": False,
        "policy": "omega_prime/hosting/openshell/sandbox-policy.yaml",
        "scope": "gates",
    }


def test_sandbox_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_PRIME_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("OMEGA_PRIME_SANDBOX_POLICY", "/tmp/custom-policy.yaml")
    monkeypatch.setenv("OMEGA_PRIME_SANDBOX_SCOPE", "gates+execute")
    config = load_config(tmp_path)
    assert config["sandbox"] == {
        "enabled": True,
        "policy": "/tmp/custom-policy.yaml",
        "scope": "gates+execute",
    }


def test_sandbox_env_enabled_falsy_stays_off(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_PRIME_SANDBOX_ENABLED", "0")
    config = load_config(tmp_path)
    assert config["sandbox"]["enabled"] is False


def test_sandbox_bogus_scope_falls_back_to_gates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OMEGA_PRIME_SANDBOX_SCOPE", "everything")
    config = load_config(tmp_path)
    assert config["sandbox"]["scope"] == "gates"
    assert "sandbox.scope" in capsys.readouterr().err


def test_sandbox_bogus_scope_in_file_falls_back(tmp_path, capsys):
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"sandbox": {"scope": "everything"}})
    )
    config = load_config(tmp_path)
    assert config["sandbox"]["scope"] == "gates"
    assert "sandbox.scope" in capsys.readouterr().err


def test_sandbox_file_enables_gates(tmp_path):
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"sandbox": {"enabled": True}})
    )
    config = load_config(tmp_path)
    assert config["sandbox"]["enabled"] is True
    assert config["sandbox"]["scope"] == "gates"

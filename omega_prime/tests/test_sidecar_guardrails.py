"""Sidecar guardrails: kill precedence, fail-closed registration, state gauge."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from omega_prime.config import load_config, redteam_enabled, redteam_kill
from omega_prime.grokbot.metrics import MetricsRegistry
from omega_prime.metrics import (
    SIDECAR_STATE_GAUGE,
    SIDECAR_STATE_VALUES,
    register_sidecar_state,
    sidecar_state_value,
)
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.redteam import (
    REDTEAM_TOOL_NAMES,
    RedteamClient,
    RedteamContext,
    current_sidecar_state,
    sidecar_tracking_start,
    sidecar_tracking_stop,
)
from sidecar.roe import sign_artifact

FIXTURE_KEY = hashlib.sha256(b"omega-prime fixture - not a secret").hexdigest().encode()

EXPECTED_TOOL_NAMES = (
    "tempest_status",
    "tempest_mission_start",
    "tempest_mission_stop",
    "tempest_report",
    "tempest_operators_list",
    "tempest_operator_task",
    "tempest_findings",
    "tempest_providers",
    "tempest_purple_synthesize",
    "tempest_hats_list",
    "tempest_hat_dispatch",
)

REDTEAM_ENV_KEYS = (
    "OMEGA_PRIME_REDTEAM_ENABLED",
    "OMEGA_PRIME_REDTEAM_KILL",
    "OMEGA_PRIME_REDTEAM_ROE_REQUIRED",
    "OMEGA_PRIME_REDTEAM_ROE_FILE",
    "OMEGA_PRIME_REDTEAM_ROE_KEY",
    "OMEGA_PRIME_REDTEAM_GATEWAY_URL",
)


@pytest.fixture
def sidecar_env(monkeypatch, tmp_path):
    """Isolated redteam env + reports dir; tracking always off on exit."""
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(tmp_path / "reports"))
    for key in REDTEAM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    sidecar_tracking_stop()
    yield tmp_path
    sidecar_tracking_stop()


def _write_signed_roe(directory, key: bytes = FIXTURE_KEY):
    now = datetime.now(UTC)
    document = {
        "schema_version": 1,
        "operator": "guardrail-fixture",
        "authorized_targets": [{"host": "127.0.0.1"}],
        "effective_from": (now - timedelta(days=1)).isoformat(timespec="seconds"),
        "effective_until": (now + timedelta(days=1)).isoformat(timespec="seconds"),
        "case_classes": ["prompt-injection"],
    }
    artifact = directory / "roe.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    sign_artifact(artifact, key)
    return artifact


def _build(monkeypatch, tmp_path, **env):
    from omega_prime.mcp_server import default_registry

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    install = tmp_path / "install"
    (install / "omega_prime").mkdir(parents=True, exist_ok=True)
    return default_registry(
        install, tmp_path / "home", approval_log=ApprovalLog(), env={}
    )


def _registered_names(registry) -> set[str]:
    return {s["function"]["name"] for s in registry.schemas()}


def test_rostered_surface_still_exactly_the_eleven_names():
    assert tuple(REDTEAM_TOOL_NAMES) == EXPECTED_TOOL_NAMES


def test_config_defaults_are_restrictive(sidecar_env):
    config = load_config()
    assert redteam_enabled(config) is False
    assert redteam_kill(config) is False
    assert config["redteam"]["roe_required"] is True
    assert config["redteam"]["roe_file"] == ""


def test_config_env_overrides_for_new_keys(monkeypatch, sidecar_env):
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_KILL", "1")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ROE_REQUIRED", "0")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ROE_FILE", "/etc/roe.json")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ROE_KEY", "sekrit")
    config = load_config()
    assert redteam_kill(config) is True
    assert config["redteam"]["roe_required"] is False
    assert config["redteam"]["roe_file"] == "/etc/roe.json"
    assert config["redteam"]["roe_key"] == "sekrit"


def test_default_off_registers_nothing_and_stays_silent(monkeypatch, sidecar_env):
    registry = _build(monkeypatch, sidecar_env)
    assert not (_registered_names(registry) & set(REDTEAM_TOOL_NAMES))
    assert registry.runtime_bindings["sidecar_state"] == "BLOCKED"
    assert not (sidecar_env / "reports" / "transitions.ndjson").exists()


def test_enabled_without_roe_is_blocked_with_zero_registrations(
    monkeypatch, sidecar_env
):
    registry = _build(monkeypatch, sidecar_env, OMEGA_PRIME_REDTEAM_ENABLED="1")
    assert not (_registered_names(registry) & set(REDTEAM_TOOL_NAMES))
    assert registry.runtime_bindings["sidecar_state"] == "BLOCKED"


def test_enabled_with_valid_roe_reaches_idle_with_all_eleven(monkeypatch, sidecar_env):
    artifact = _write_signed_roe(sidecar_env)
    registry = _build(
        monkeypatch,
        sidecar_env,
        OMEGA_PRIME_REDTEAM_ENABLED="1",
        OMEGA_PRIME_REDTEAM_ROE_FILE=str(artifact),
        OMEGA_PRIME_REDTEAM_ROE_KEY=FIXTURE_KEY.decode(),
    )
    assert _registered_names(registry) & set(REDTEAM_TOOL_NAMES) == set(
        REDTEAM_TOOL_NAMES
    )
    assert registry.runtime_bindings["sidecar_state"] == "IDLE"


def test_kill_switch_takes_precedence_over_valid_roe(monkeypatch, sidecar_env):
    artifact = _write_signed_roe(sidecar_env)
    registry = _build(
        monkeypatch,
        sidecar_env,
        OMEGA_PRIME_REDTEAM_ENABLED="1",
        OMEGA_PRIME_REDTEAM_ROE_FILE=str(artifact),
        OMEGA_PRIME_REDTEAM_ROE_KEY=FIXTURE_KEY.decode(),
        OMEGA_PRIME_REDTEAM_KILL="1",
    )
    assert not (_registered_names(registry) & set(REDTEAM_TOOL_NAMES))
    assert registry.runtime_bindings["sidecar_state"] == "BLOCKED"


def test_state_gauge_visible_in_metrics_render():
    registry = MetricsRegistry()
    register_sidecar_state(registry, "BLOCKED")
    text = registry.render()
    assert SIDECAR_STATE_GAUGE in text
    assert f"{SIDECAR_STATE_GAUGE} 3" in text


def test_kill_switch_aborts_in_flight_calls_before_transport(monkeypatch, sidecar_env):
    calls: list = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("transport must not run under kill switch")

    client = RedteamClient(RedteamContext(request=_boom))
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_KILL", "1")
    body = client.status()
    assert calls == []
    assert body["mission"]["error"].startswith("killed:")
    assert body["llm"]["error"].startswith("killed:")


def test_gateway_boundary_logs_run_transitions(monkeypatch, sidecar_env):
    def _ok(base_url, method, path, payload, headers, timeout):
        return 200, {"ok": True}

    client = RedteamClient(RedteamContext(request=_ok))
    monkeypatch.delenv("OMEGA_PRIME_REDTEAM_KILL", raising=False)
    sidecar_tracking_start("IDLE")
    try:
        assert client.status()["tool"] == "tempest_status"
    finally:
        sidecar_tracking_stop()
    assert current_sidecar_state() is None
    log = sidecar_env / "reports" / "transitions.ndjson"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert ("IDLE", "RUNNING") in [(r["from"], r["to"]) for r in rows]
    assert ("RUNNING", "IDLE") in [(r["from"], r["to"]) for r in rows]
    assert all("ts" in r for r in rows)


def test_state_gauge_covers_all_five_states():
    assert set(SIDECAR_STATE_VALUES) == {
        "IDLE",
        "RUNNING",
        "DEGRADED",
        "BLOCKED",
        "ERROR",
    }
    assert sidecar_state_value("IDLE") == 0.0
    assert sidecar_state_value("BLOCKED") == 3.0
    with pytest.raises(ValueError, match="unknown sidecar state"):
        sidecar_state_value("FLYING")

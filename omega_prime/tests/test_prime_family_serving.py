"""RLM and messaging flags actually serve their families."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from omega_prime.agent.messaging import SessionRegistry
from omega_prime.config import PRIME_FAMILIES
from omega_prime.mcp_server import default_registry, roster_names
from omega_prime.tooling.roster import load_roster_names
from omega_prime.tools.agent_message import (
    MESSAGING_TOOL_NAMES,
    register_messaging_tools,
)
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.rlm import RLM_TOOL_NAMES
from sidecar.roe import sign_artifact

ROOT = Path(__file__).resolve().parents[2]
ROSTER = ROOT / "omega_prime" / "contracts" / "tool-rosters" / "omega-prime.yaml"


def _flags(monkeypatch, **enabled: str) -> None:
    for family in PRIME_FAMILIES:
        monkeypatch.setenv(f"OMEGA_PRIME_PRIME_{family.upper()}_ENABLED", "0")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ENABLED", "0")
    for family, value in enabled.items():
        monkeypatch.setenv(f"OMEGA_PRIME_PRIME_{family.upper()}_ENABLED", value)


def _names(registry) -> set[str]:
    return {item["function"]["name"] for item in registry.schemas()}


def test_flags_off_omit_rlm_and_messaging(tmp_path: Path, monkeypatch) -> None:
    _flags(monkeypatch)
    registry = default_registry(ROOT, tmp_path)
    names = _names(registry)
    assert set(RLM_TOOL_NAMES).isdisjoint(names)
    assert set(MESSAGING_TOOL_NAMES).isdisjoint(names)
    roster = roster_names(ROSTER.read_text(encoding="utf-8"))
    served = [name for name in roster if name in names]
    assert len(served) == 121


def test_rlm_flag_serves_not_configured_without_a_provider(
    tmp_path: Path, monkeypatch
) -> None:
    _flags(monkeypatch, rlm="1")
    log = ApprovalLog()
    log.approve("rlm_spawn", "tester")
    registry = default_registry(ROOT, tmp_path, approval_log=log)
    names = _names(registry)
    assert set(RLM_TOOL_NAMES) <= names
    body = json.loads(registry.dispatch("rlm_spawn", {"prompt": "look", "name": "kid"}))
    assert body["error"] == "not_configured: provider"
    listed = json.loads(registry.dispatch("rlm_list_subagents", {}))
    assert isinstance(listed, list)


def test_messaging_flag_shares_one_session_registry() -> None:
    log = ApprovalLog()
    log.approve("agent_message_send", "tester")
    sessions = SessionRegistry()
    sender = ToolRegistry(approval_log=log)
    observer = ToolRegistry(approval_log=log)
    register_messaging_tools(sender, "seat-a", session_registry=sessions)
    register_messaging_tools(observer, "seat-b", session_registry=sessions)
    sent = json.loads(
        sender.dispatch("agent_message_send", {"recipient": "seat-b", "body": "hello"})
    )
    assert sent.get("error") is None
    seen = json.loads(observer.dispatch("agent_observe", {}))
    assert any(item.get("body") == "hello" for item in seen)


def test_messaging_flag_registers_on_the_host(tmp_path: Path, monkeypatch) -> None:
    _flags(monkeypatch, messaging="1")
    registry = default_registry(ROOT, tmp_path)
    assert set(MESSAGING_TOOL_NAMES) <= _names(registry)


def test_all_prime_flags_serve_the_roster(tmp_path: Path, monkeypatch) -> None:
    _flags(monkeypatch, **{family: "1" for family in PRIME_FAMILIES})
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ENABLED", "1")
    registry = default_registry(ROOT, tmp_path)
    served = _names(registry)
    roster = set(load_roster_names(ROSTER))
    # The RoE gate ships BLOCKED: red-team enabled without a signed artifact
    # registers nothing, so the served set is the roster minus the gated
    # family (phase 79 SIDE-02; full-roster path is the next test).
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    assert served == roster - set(REDTEAM_TOOL_NAMES)
    assert len(roster) == 172


def test_signed_roe_completes_the_roster(tmp_path: Path, monkeypatch) -> None:
    _flags(monkeypatch, **{family: "1" for family in PRIME_FAMILIES})
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ENABLED", "1")
    fixture_key = (
        hashlib.sha256(b"omega-prime fixture - not a secret").hexdigest().encode()
    )
    now = datetime.now(UTC)
    document = {
        "schema_version": 1,
        "operator": "prime-family-fixture",
        "authorized_targets": [{"host": "127.0.0.1"}],
        "effective_from": (now - timedelta(days=1)).isoformat(timespec="seconds"),
        "effective_until": (now + timedelta(days=1)).isoformat(timespec="seconds"),
        "case_classes": ["prompt-injection"],
    }
    artifact = tmp_path / "roe.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    sign_artifact(artifact, fixture_key)
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ROE_FILE", str(artifact))
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ROE_KEY", fixture_key.decode())
    registry = default_registry(ROOT, tmp_path)
    served = _names(registry)
    roster = set(load_roster_names(ROSTER))
    assert served == roster
    assert len(roster) == 172

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Slash commands and workflows. No model, no network, no gate suite."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from omega_prime.commands import execute_command, parse_omega_command
from omega_prime.mcp_server import default_registry
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


class RecordingRegistry:
    """Dispatch stand-in. ``script`` is a queue of JSON-ready dicts."""

    def __init__(self, script: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.script = list(script or [])

    def dispatch(self, name: str, arguments: dict) -> str:
        self.calls.append((name, dict(arguments)))
        if self.script:
            return json.dumps(self.script.pop(0))
        return json.dumps({"ok": True})


def test_parse_accepts_only_a_whole_command() -> None:
    assert parse_omega_command("please /omega-help") is None
    assert parse_omega_command("/omega-help") == ("omega-help", "")
    assert parse_omega_command("  /omega-recall ships  ") == ("omega-recall", "ships")
    assert parse_omega_command("/OMEGA-Doctor") == ("omega-doctor", "")


def test_parse_accepts_multi_segment_generated_names() -> None:
    assert parse_omega_command("/omega-code-review") == ("omega-code-review", "")
    assert parse_omega_command("/omega-python-clean  src  ") == (
        "omega-python-clean",
        "src",
    )
    assert parse_omega_command("/OMEGA-Desk-Lead") == ("omega-desk-lead", "")
    assert parse_omega_command("please /omega-code-review") is None
    assert parse_omega_command("/omega-code-review-") is None


def test_unknown_command_does_not_dispatch() -> None:
    registry = RecordingRegistry()
    body = execute_command("/omega-nope", registry, {})
    assert body["ok"] is False
    assert body["error"] == "unknown_command"
    assert "omega-help" in body["commands"]
    assert "omega-code-review" in body["commands"]
    assert "omega-onboard-routine" in body["commands"]
    assert registry.calls == []


def test_retain_refuses_a_secret_before_dispatch() -> None:
    registry = RecordingRegistry()
    secret = "X_API_TOKEN=abc"
    body = execute_command(f"/omega-retain {secret}", registry, {})
    encoded = json.dumps(body)
    assert body["error"] == "secret_refused"
    assert registry.calls == []
    assert secret not in encoded
    assert "abc" not in encoded


def test_retain_tags_ordinary_text() -> None:
    registry = RecordingRegistry([{"success": True}])
    body = execute_command("/omega-retain the desk ships on Friday", registry, {})
    assert body["ok"] is True
    name, arguments = registry.calls[0]
    assert name == "memory"
    assert arguments["content"].startswith("[hindsight:omega-prime-lead] ")
    assert "Friday" in arguments["content"]


def test_connectors_never_echo_a_token() -> None:
    token = "super-secret-value"
    env = {"X_API_TOKEN": token, "ASC_KEY_ID": "only-one"}
    body = execute_command("/omega-connectors", RecordingRegistry(), env)
    encoded = json.dumps(body)
    configured = [row["id"] for row in body["result"]["configured"]]
    requested = [row["id"] for row in body["result"]["requests"]]
    assert "x" in configured
    assert "telegram" in requested
    assert "appstore" in requested
    assert token not in encoded
    assert "only-one" not in encoded


def test_doctor_green_false_is_a_failed_command() -> None:
    red = {"green": False, "seat": "s", "checks": {"prompt": {"green": False}}}
    registry = RecordingRegistry([red])
    body = execute_command("/omega-doctor", registry, {})
    assert body["ok"] is False
    assert body["error"] == "doctor_failed"
    assert body["result"] == red

    denied = {"ok": False, "reason": "seat is not registered"}
    registry = RecordingRegistry([denied])
    body = execute_command("/omega-doctor", registry, {})
    assert body["ok"] is False
    assert body["error"] == "doctor_failed"
    assert body["result"] == denied


def test_onboard_failure_stays_incomplete_but_runs_every_step() -> None:
    registry = RecordingRegistry(
        [
            {"error": "approval required", "tool": "lead_doctor"},
            {"seats": []},
            {"success": True},
        ]
    )
    body = execute_command("/omega-onboard", registry, {})
    assert body["ok"] is False
    assert body["error"] == "workflow_incomplete"
    names = [call[0] for call in registry.calls]
    assert names == ["lead_doctor", "lead_roster_status", "memory"]
    steps = body["result"]["steps"]
    assert [row["name"] for row in steps] == [
        "lead_doctor",
        "lead_roster_status",
        "seed_memory",
        "connectors",
    ]
    assert steps[0]["ok"] is False
    assert steps[0]["result"]["error"] == "approval required"
    assert steps[1]["ok"] is True
    assert steps[2]["ok"] is True
    assert steps[-1]["name"] == "connectors"
    assert any(row["id"] == "telegram" for row in steps[-1]["result"]["requests"])


def test_python_clean_stopping_error_keeps_workflow_failed() -> None:
    registry = RecordingRegistry([{"exit_code": 2, "stdout": "", "stderr": "nope"}])
    body = execute_command("/omega-python", registry, {})
    assert body["ok"] is False
    assert body["error"] == "workflow_failed"
    assert len(body["result"]["steps"]) == 1
    assert len(registry.calls) == 1


def test_desk_green_false_step_stays_incomplete() -> None:
    red = {"green": False, "seat": "s", "checks": {"prompt": {"green": False}}}
    registry = RecordingRegistry(
        [red, {"seats": []}, {"work_order": None, "queue": {}}]
    )
    body = execute_command("/omega-desk", registry, {})
    assert body["ok"] is False
    assert body["error"] == "workflow_incomplete"
    steps = body["result"]["steps"]
    assert [row["name"] for row in steps] == [
        "lead_doctor",
        "lead_roster_status",
        "lead_intake_next",
    ]
    assert steps[0]["ok"] is False
    assert steps[0]["result"] == red
    assert steps[1]["ok"] is True
    assert steps[2]["ok"] is True


def test_registered_omega_command_forwards_doctor_context(tmp_path: Path) -> None:
    registry, _ = _real_registry(tmp_path, "lead_doctor")
    sha, declared = _prepared_seat(registry)
    raw = registry.dispatch(
        "omega_command",
        {
            "command": "/omega-doctor",
            "doctor_context": {"prompt_sha256": sha, "installed_skills": declared},
        },
    )
    body = json.loads(raw)
    assert body["ok"] is True, body
    assert body["command"] == "omega-doctor", body
    assert body["result"]["green"] is True, body
    raw = registry.dispatch("omega_command", {"command": "/omega-doctor"})
    missing = json.loads(raw)
    assert missing["ok"] is False, missing
    assert missing["error"] == "doctor_failed", missing


def _real_registry(tmp_path: Path, *approvals: str) -> tuple[ToolRegistry, dict]:
    log = ApprovalLog()
    for tool in approvals:
        assert log.approve(tool, "phase-75-09").get("approved") is True
    env = {"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")}
    return default_registry(ROOT, tmp_path / "home", approval_log=log, env=env), env


def _prepared_seat(registry: ToolRegistry) -> tuple[str, list[str]]:
    registered = json.loads(
        registry.dispatch(
            "lead_doctor", {"action": "register", "agent_uuid": "phase-75-09-seat"}
        )
    )
    assert registered.get("ok") is True, registered
    probed = json.loads(registry.dispatch("lead_doctor", {"action": "check"}))
    declared = probed["checks"]["skills"]["declared"]
    assert isinstance(declared, list) and declared, probed
    installed = json.loads(
        registry.dispatch("lead_doctor", {"action": "install_prompt"})
    )
    sha256 = installed.get("sha256")
    prompt = installed.get("prompt")
    assert isinstance(sha256, str) and sha256, installed.keys()
    assert isinstance(prompt, str) and prompt, installed.keys()
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == sha256
    return sha256, list(declared)


def test_real_doctor_contextual_health_and_repeat(tmp_path: Path) -> None:
    registry, env = _real_registry(tmp_path, "lead_doctor")
    sha, declared = _prepared_seat(registry)
    context = {"prompt_sha256": sha, "installed_skills": declared}
    first = execute_command("/omega-doctor", registry, env, doctor_context=context)
    assert first["ok"] is True, first
    assert first["result"]["green"] is True, first
    assert first["result"]["checks"]["prompt"]["green"] is True, first
    assert first["result"]["checks"]["skills"]["green"] is True, first
    repeat = execute_command("/omega-doctor", registry, env, doctor_context=context)
    assert repeat["ok"] is True, repeat
    assert repeat["result"]["green"] is True, repeat


def test_real_doctor_missing_context_stays_unhealthy_and_preserves_sha(
    tmp_path: Path,
) -> None:
    registry, env = _real_registry(tmp_path, "lead_doctor")
    sha, declared = _prepared_seat(registry)
    missing = execute_command("/omega-doctor", registry, env)
    assert missing["ok"] is False, missing
    assert missing["error"] == "doctor_failed", missing
    assert missing["result"]["green"] is False, missing
    context = {"prompt_sha256": sha, "installed_skills": declared}
    healthy = execute_command("/omega-doctor", registry, env, doctor_context=context)
    assert healthy["ok"] is True, healthy
    assert healthy["result"]["green"] is True, healthy


def test_real_doctor_extra_context_runs_a_missing_check(tmp_path: Path) -> None:
    registry, env = _real_registry(tmp_path, "lead_doctor")
    sha, declared = _prepared_seat(registry)
    body = execute_command(
        "/omega-doctor",
        registry,
        env,
        doctor_context={
            "prompt_sha256": sha,
            "installed_skills": declared,
            "action": "register",
            "agent_uuid": "someone-else",
        },
    )
    assert body["ok"] is False, body
    assert body["error"] == "doctor_failed", body
    assert "registered" not in body["result"], body
    assert body["result"]["green"] is False, body


def test_real_doctor_check_runs_without_a_grant(tmp_path: Path) -> None:
    """The read-only check is ungated. A write still needs a human grant."""
    registry, env = _real_registry(tmp_path)
    body = execute_command(
        "/omega-doctor",
        registry,
        env,
        doctor_context={"prompt_sha256": "abc", "installed_skills": ["lead-pack"]},
    )
    assert body["ok"] is False, body
    assert body["error"] == "doctor_failed", body
    assert body["result"].get("error") != "approval required"
    assert body["result"].get("green") is False
    denied = json.loads(
        registry.dispatch(
            "lead_doctor",
            {"action": "register", "agent_uuid": "phase-75-09-seat"},
        )
    )
    assert denied["error"] == "approval required", denied
    assert denied["tool"] == "lead_doctor"


def test_real_desk_without_context_stays_incomplete(tmp_path: Path) -> None:
    registry, env = _real_registry(tmp_path, "lead_doctor", "lead_intake_next")
    body = execute_command("/omega-desk", registry, env)
    assert body["ok"] is False, body
    assert body["error"] == "workflow_incomplete", body
    steps = body["result"]["steps"]
    assert [row["name"] for row in steps] == [
        "lead_doctor",
        "lead_roster_status",
        "lead_intake_next",
    ]
    assert steps[0]["ok"] is False, steps
    assert steps[0]["result"]["green"] is False, steps
    assert steps[1]["ok"] is True, steps
    assert steps[2]["ok"] is True, steps

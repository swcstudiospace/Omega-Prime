"""Tests for Grok Bot Preflight Health Doctor."""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from omega_prime.assemble import _prime_family_tools
from omega_prime.config import PRIME_FAMILIES, load_config
from omega_prime.grokbot import doctor as doctor_module
from omega_prime.grokbot.audit import GrokBotAuditTracer, default_audit_path
from omega_prime.grokbot.doctor import (
    DiagnosticCheck,
    GrokBotDoctor,
    run_doctor_checks,
)
from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

NEW_CHECK_IDS = (
    "policy_loads",
    "roster_consistency",
    "prime_family_rlm",
    "prime_family_harness",
    "prime_family_goals",
    "prime_family_heartbeat",
    "prime_family_autonomous",
    "prime_family_messaging",
    "prime_family_kernel",
    "redteam_availability",
    "scheduled_jobs",
    "template_integrity",
    "audit_chain",
    "bind_exposure",
    "token_strength",
    "connector_secrets",
)


def _by_id(checks: list[DiagnosticCheck]) -> dict[str, DiagnosticCheck]:
    return {c.id: c for c in checks}


def _contracts_tree(tmp_path: Path) -> Path:
    """A root holding only a copy of the shipped contracts."""
    source = find_repo_root() / "omega_prime" / "contracts"
    shutil.copytree(source, tmp_path / "omega_prime" / "contracts")
    return tmp_path


def test_doctor_run_all_checks():
    doctor = GrokBotDoctor()
    checks = doctor.run_all_checks(check_port=59123)
    check_ids = [c.id for c in checks]

    assert "python_version" in check_ids
    assert "virtual_env" in check_ids
    assert "submodule_prime_agent" in check_ids
    assert "prompt_assembled" in check_ids
    assert "mcp_dependencies" in check_ids
    assert "manifest_generation" in check_ids
    assert "port_availability" in check_ids
    assert "audit_writeable" in check_ids

    # Port 59123 should be free
    port_check = next(c for c in checks if c.id == "port_availability")
    assert port_check.status == "PASS"


def test_run_doctor_checks_summary():
    summary = run_doctor_checks(port=59124)
    assert "passed" in summary
    assert "checks" in summary
    assert len(summary["checks"]) >= 8


def test_new_check_ids_present_and_shape():
    summary = run_doctor_checks(port=59125)
    ids = {c["id"] for c in summary["checks"]}
    assert set(NEW_CHECK_IDS) <= ids
    for check in summary["checks"]:
        assert set(check) == {
            "id",
            "name",
            "category",
            "status",
            "message",
            "fix_hint",
        }
        assert check["status"] in {"ok", "warn", "fail"}


@pytest.mark.parametrize("family", PRIME_FAMILIES)
def test_known_prime_families_are_reported_off_by_default(tmp_path, family):
    doctor = GrokBotDoctor(tmp_path, env={})
    config = load_config(tmp_path, env={})
    check = next(
        item
        for item in doctor._check_prime_family_availability(
            config, registered_tools=set()
        )
        if item.id == f"prime_family_{family}"
    )
    assert check.status == "PASS"
    assert "disabled by configuration; execution was not checked" in check.message


def test_enabled_prime_family_missing_exact_tools_fails(tmp_path):
    config = load_config(tmp_path, env={"OMEGA_PRIME_PRIME_RLM_ENABLED": "1"})
    check = next(
        item
        for item in GrokBotDoctor(tmp_path, env={})._check_prime_family_availability(
            config, registered_tools=set()
        )
        if item.id == "prime_family_rlm"
    )
    assert check.status == "FAIL"
    assert "expected tools are missing" in check.message
    assert _prime_family_tools()["rlm"][0] in check.message


def test_roster_uses_supplied_family_environment_without_loading_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    roster_path = tmp_path / "omega_prime/contracts/tool-rosters/omega-prime.yaml"
    roster_path.parent.mkdir(parents=True)
    roster_path.write_text("tools: []\n", encoding="utf-8")
    from omega_prime import mcp_server
    from omega_prime.tools import plugins

    rlm_tool = _prime_family_tools()["rlm"][0]
    monkeypatch.setattr(mcp_server, "roster_names", lambda _text: [rlm_tool])
    monkeypatch.setenv("OMEGA_PRIME_PRIME_RLM_ENABLED", "1")
    monkeypatch.setenv("OMEGA_PRIME_PLUGINS", "1")
    loaded_plugins = []
    monkeypatch.setattr(
        plugins, "load_plugins", lambda *args, **kwargs: loaded_plugins.append(True)
    )

    doctor = GrokBotDoctor(
        tmp_path,
        env={
            "OMEGA_PRIME_PRIME_RLM_ENABLED": "0",
            "OMEGA_PRIME_PLUGINS": "1",
        },
    )
    check = doctor._check_roster()
    rlm_check = next(
        item
        for item in doctor._check_prime_family_availability(
            doctor._load_family_config(), registered_tools=doctor._registered_tool_names
        )
        if item.id == "prime_family_rlm"
    )

    assert check.status == "PASS"
    assert doctor._registered_tool_names is not None
    assert rlm_tool not in doctor._registered_tool_names
    assert "disabled by configuration" in rlm_check.message
    assert loaded_plugins == []


def test_unknown_prefix_like_roster_tool_is_not_flag_gated(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    from omega_prime import mcp_server

    roster = tmp_path / "omega_prime/contracts/tool-rosters/omega-prime.yaml"
    roster.parent.mkdir(parents=True)
    roster.write_text("tools: []\n", encoding="utf-8")
    known = _prime_family_tools()["rlm"][0]
    monkeypatch.setattr(
        mcp_server, "roster_names", lambda _text: [known, "rlm_unknown"]
    )
    observed_env = {}

    def make_registry(*_args, **kwargs):
        observed_env.update(kwargs["env"])
        return SimpleNamespace(schemas=lambda: [], runtime_bindings={})

    monkeypatch.setattr(mcp_server, "default_registry", make_registry)
    doctor = GrokBotDoctor(
        tmp_path,
        env={"OMEGA_PRIME_PLUGINS": "1", "OMEGA_PRIME_PRIME_RLM_ENABLED": "1"},
    )
    check = doctor._check_roster()
    assert observed_env["OMEGA_PRIME_PRIME_RLM_ENABLED"] == "1"
    assert observed_env["OMEGA_PRIME_PLUGINS"] == "0"
    assert check.status == "WARN"
    assert "rlm_unknown" in check.message
    assert known not in check.message


def test_redteam_availability_requires_supplied_authorization_metadata(tmp_path):
    doctor = GrokBotDoctor(tmp_path, env={})
    off_config = load_config(tmp_path, env={})
    off = doctor._check_redteam_availability(
        off_config, registered_tools=set(), runtime_bindings={"sidecar_state": "IDLE"}
    )
    assert off.status == "PASS"

    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"redteam": {"enabled": True}}), encoding="utf-8"
    )
    enabled = load_config(tmp_path, env={})
    blocked = doctor._check_redteam_availability(
        enabled,
        registered_tools=set(),
        runtime_bindings={
            "sidecar_state": "BLOCKED",
            "sidecar_cause": "roe-missing",
        },
    )
    assert blocked.status == "WARN"
    assert "roe-missing" in blocked.message

    unobserved = doctor._check_redteam_availability(
        enabled, registered_tools=set(), runtime_bindings={}
    )
    assert unobserved.status == "WARN"
    assert "authorization was not observed" in unobserved.message

    authorized_missing = doctor._check_redteam_availability(
        enabled, registered_tools=set(), runtime_bindings={"sidecar_state": "IDLE"}
    )
    assert authorized_missing.status == "FAIL"
    assert "no allowed tools are registered" in authorized_missing.message

    served = doctor._check_redteam_availability(
        enabled,
        registered_tools=set(REDTEAM_TOOL_NAMES),
        runtime_bindings={"sidecar_state": "IDLE"},
    )
    assert served.status == "PASS"
    assert "all registered authorized tools" in served.message


def test_redteam_authorization_accepts_registered_allowed_subset(tmp_path):
    doctor = GrokBotDoctor(tmp_path, env={})
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"redteam": {"enabled": True}}), encoding="utf-8"
    )
    enabled = load_config(tmp_path, env={})

    check = doctor._check_redteam_availability(
        enabled,
        registered_tools={next(iter(REDTEAM_TOOL_NAMES))},
        runtime_bindings={"sidecar_state": "IDLE"},
    )

    assert check.status == "PASS"


def test_registry_bindings_refresh_each_collection_unless_caller_supplied(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    from omega_prime import mcp_server

    roster = tmp_path / "omega_prime/contracts/tool-rosters/omega-prime.yaml"
    roster.parent.mkdir(parents=True)
    roster.write_text("tools: []\n", encoding="utf-8")
    bindings = iter([{"prime_heartbeat": "first"}, {"prime_heartbeat": "second"}])
    monkeypatch.setattr(mcp_server, "roster_names", lambda _text: [])
    monkeypatch.setattr(
        mcp_server,
        "default_registry",
        lambda *_args, **_kwargs: SimpleNamespace(
            schemas=lambda: [], runtime_bindings=next(bindings)
        ),
    )
    doctor = GrokBotDoctor(tmp_path, env={})

    doctor._check_roster()
    assert doctor.runtime_bindings == {"prime_heartbeat": "first"}
    doctor._check_roster()
    assert doctor.runtime_bindings == {"prime_heartbeat": "second"}

    supplied = {"prime_heartbeat": "caller"}
    caller_doctor = GrokBotDoctor(tmp_path, env={}, runtime_bindings=supplied)
    caller_doctor._check_roster()
    assert caller_doctor.runtime_bindings is supplied


def test_scheduled_job_report_is_redacted_and_store_read_only(tmp_path):
    path = tmp_path / "cron/jobs.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "kind": "prompt",
                        "due_at": 1000,
                        "completed": False,
                        "prompt": "private-prompt-marker",
                        "last_result": "private-result-marker",
                        "history": [{"result": "private-history-marker"}],
                    },
                    {
                        "kind": "session_heartbeat",
                        "due_at": 3000,
                        "completed": False,
                        "session": "private-session-marker",
                        "prompt": "private-prompt-marker",
                    },
                    {
                        "kind": "unrecognized_private_kind",
                        "due_at": 3000,
                        "completed": False,
                        "prompt": "private-prompt-marker",
                    },
                    {"kind": "prompt", "due_at": 1000, "completed": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    original = path.read_bytes()
    original_mtime = path.stat().st_mtime_ns
    config = load_config(tmp_path, env={})
    doctor = GrokBotDoctor(tmp_path, env={})
    first = doctor._check_scheduled_jobs(config, now=2000)
    second = doctor._check_scheduled_jobs(config, now=2000)

    assert first.status == "WARN"
    assert "3 pending schedule(s)" in first.message
    assert "prompt: 1" in first.message
    assert "session_heartbeat: 1" in first.message
    assert "unknown: 1" in first.message
    assert "1 are overdue" in first.message
    assert "heartbeat: 1" in first.message
    assert "runner not observed by this diagnostic" in first.message
    assert "named heartbeat session(s) lack supplied binding metadata" in first.message
    assert first.message == second.message
    for marker in (
        "private-prompt-marker",
        "private-result-marker",
        "private-history-marker",
        "private-session-marker",
        "unrecognized_private_kind",
    ):
        assert marker not in first.message
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == original_mtime


def test_scheduled_job_report_observes_supplied_bindings_not_ticks(tmp_path):
    path = tmp_path / "cron/jobs.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {"kind": "prompt", "due_at": 3000, "completed": False},
                    {
                        "kind": "session_heartbeat",
                        "due_at": 3000,
                        "completed": False,
                        "session": "private-session-marker",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    config = load_config(tmp_path, env={})
    bindings = {
        "desk_driver": object(),
        "delegate_parent": object(),
        "prime_heartbeat": object(),
        "heartbeat_sessions": ["private-session-marker"],
    }
    check = GrokBotDoctor(tmp_path, env={})._check_scheduled_jobs(
        config, runtime_bindings=bindings, now=2000
    )
    assert check.status == "WARN"
    assert "In-process binding metadata was supplied" in check.message
    assert "a runner tick was not observed" in check.message
    assert "lack supplied binding metadata" not in check.message


def test_prompt_schedule_requires_both_driver_bindings(tmp_path):
    path = tmp_path / "cron/jobs.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"jobs": [{"kind": "prompt", "due_at": 3000, "completed": False}]}),
        encoding="utf-8",
    )
    config = load_config(tmp_path, env={})
    doctor = GrokBotDoctor(tmp_path, env={})

    missing = doctor._check_scheduled_jobs(
        config, runtime_bindings={"desk_driver": object()}, now=2000
    )
    present = doctor._check_scheduled_jobs(
        config,
        runtime_bindings={"desk_driver": object(), "delegate_parent": object()},
        now=2000,
    )

    assert "Delivery runner not observed" in missing.message
    assert "In-process binding metadata was supplied" in present.message
    assert "runner tick was not observed" in present.message


def test_scheduled_jobs_require_real_prime_heartbeat_binding(tmp_path):
    config = load_config(tmp_path, env={})
    path = tmp_path / "cron/jobs.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {"jobs": [{"kind": "heartbeat", "due_at": 3000, "completed": False}]}
        ),
        encoding="utf-8",
    )
    check = GrokBotDoctor(tmp_path, env={})._check_scheduled_jobs(
        config, runtime_bindings={"heartbeat": object()}, now=2000
    )

    assert "Delivery runner not observed" in check.message

    assert "private-session-marker" not in check.message


def test_completed_schedules_are_pass_without_runner_claim(tmp_path):
    path = tmp_path / "cron/jobs.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"jobs": [{"kind": "prompt", "completed": True}]}),
        encoding="utf-8",
    )
    check = GrokBotDoctor(tmp_path, env={})._check_scheduled_jobs(
        load_config(tmp_path, env={}), now=2000
    )
    assert check.status == "PASS"
    assert "No pending schedules" in check.message


def test_manifest_check_reports_counts():
    checks = _by_id(GrokBotDoctor().run_all_checks(check_port=59126))
    message = checks["manifest_generation"].message
    assert "served tools" in message
    assert "approval-gated" in message


def test_policy_loads_fails_for_corrupt_tree(tmp_path: Path):
    root = _contracts_tree(tmp_path)
    policy = root / "omega_prime" / "contracts" / "policies" / "omega-prime.json"
    assert policy.is_file()
    policy.write_text("{not valid json", encoding="utf-8")

    checks = _by_id(GrokBotDoctor(root).run_all_checks(check_port=59127))
    assert checks["policy_loads"].status == "FAIL"
    assert checks["policy_loads"].message

    summary = run_doctor_checks(root, port=59127)
    assert summary["passed"] is False


def test_policy_loads_passes_for_intact_tree(tmp_path: Path):
    root = _contracts_tree(tmp_path)
    checks = _by_id(GrokBotDoctor(root).run_all_checks(check_port=59128))
    assert checks["policy_loads"].status == "PASS"


@pytest.mark.parametrize(
    ("host", "auth", "expected"),
    [
        ("127.0.0.1", False, "PASS"),
        ("127.0.0.1", True, "PASS"),
        ("localhost", False, "PASS"),
        ("::1", False, "PASS"),
        ("0.0.0.0", False, "FAIL"),
        ("0.0.0.0", True, "PASS"),
        ("", False, "FAIL"),
        ("192.0.2.10", False, "FAIL"),
        ("192.0.2.10", True, "PASS"),
    ],
)
def test_bind_exposure_matrix(host: str, auth: bool, expected: str):
    check = GrokBotDoctor._check_bind_exposure(host, auth)
    assert check.id == "bind_exposure"
    assert check.status == expected


def test_bind_exposure_via_run_doctor_checks():
    open_bind = run_doctor_checks(port=59129, host="0.0.0.0", auth_enabled=False)
    by_id = {c["id"]: c for c in open_bind["checks"]}
    assert by_id["bind_exposure"]["status"] == "fail"
    assert open_bind["passed"] is False

    guarded = run_doctor_checks(port=59129, host="0.0.0.0", auth_enabled=True)
    by_id = {c["id"]: c for c in guarded["checks"]}
    assert by_id["bind_exposure"]["status"] == "ok"


def test_token_strength_never_leaks_value():
    weak = "weak-secret-value-123"
    doctor = GrokBotDoctor(env={"MCP_AUTH_TOKEN": weak})
    check = doctor._check_token_strength()
    assert check.status == "WARN"
    assert weak not in json.dumps(check.__dict__)

    strong = "S" * 40
    check = GrokBotDoctor(env={"MCP_AUTH_TOKEN": strong})._check_token_strength()
    assert check.status == "PASS"
    assert strong not in json.dumps(check.__dict__)

    unset = GrokBotDoctor(env={})._check_token_strength()
    assert unset.status == "PASS"


def test_secret_values_absent_from_json_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    secrets = {
        "MCP_AUTH_TOKEN": "tok-secret-abcdefghijklmnopqrstuvwxyz0123456789",
        "XAI_API_KEY": "xai-secret-value-1",
        "TELEGRAM_BOT_TOKEN": "telegram-secret-value-2",
        "SUBSTRATE_TOKEN": "substrate-secret-value-3",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    summary = run_doctor_checks(port=59130)
    rendered = json.dumps(summary)
    assert doctor_module.main(["--json", "--port", "59130"]) in (0, 1)
    out = capsys.readouterr().out
    for value in secrets.values():
        assert value not in rendered
        assert value not in out
    json.loads(out)


def test_connector_secrets_lists_names_only():
    env = {
        "XAI_API_KEY": "xai-value",
        "DISCORD_BOT_TOKEN": "discord-value",
        "UNRELATED_SECRET": "unrelated-value",
    }
    check = GrokBotDoctor(env=env)._check_connector_secrets()
    assert check.status == "PASS"
    assert "XAI_API_KEY" in check.message
    assert "DISCORD_BOT_TOKEN" in check.message
    assert "TELEGRAM_BOT_TOKEN" not in check.message
    assert "UNRELATED_SECRET" not in check.message
    for value in env.values():
        assert value not in check.message

    none = GrokBotDoctor(env={})._check_connector_secrets()
    assert none.status == "PASS"


def test_port_check_honors_host_and_stays_warn_when_taken():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]

        taken = GrokBotDoctor._check_port("127.0.0.1", port)
        assert taken.id == "port_availability"
        assert taken.status == "WARN"
        assert "in use" in taken.message

        wildcard = GrokBotDoctor._check_port("0.0.0.0", port)
        assert wildcard.status == "WARN"

    # Documentation-range address is not assigned to this host: the configured
    # host is what gets bound, so the probe cannot succeed.
    foreign = GrokBotDoctor._check_port("192.0.2.77", 59131)
    assert foreign.status == "WARN"
    assert "192.0.2.77" in foreign.message

    free = GrokBotDoctor._check_port("127.0.0.1", port)
    assert free.status == "PASS"


def test_audit_chain_pass_when_absent_or_intact_and_fail_when_broken(tmp_path: Path):
    absent = GrokBotDoctor(tmp_path)._check_audit_chain()
    assert absent.status == "PASS"

    tracer = GrokBotAuditTracer(default_audit_path(tmp_path))
    tracer.log_event("tool_call", tool_name="read_file")
    tracer.log_event("tool_call", tool_name="read_file")
    intact = GrokBotDoctor(tmp_path)._check_audit_chain()
    assert intact.status == "PASS"
    assert "2 records" in intact.message

    log = default_audit_path(tmp_path)
    text = log.read_text(encoding="utf-8")
    log.write_text(text.replace("read_file", "write_file", 1), encoding="utf-8")
    broken = GrokBotDoctor(tmp_path)._check_audit_chain()
    assert broken.status == "FAIL"
    assert "broken" in broken.message


def test_template_integrity_check_present_for_repo():
    checks = _by_id(GrokBotDoctor().run_all_checks(check_port=59132))
    assert checks["template_integrity"].status in {"PASS", "FAIL"}
    assert checks["roster_consistency"].status in {"PASS", "WARN"}


def _fake_checks(*statuses: str) -> list[DiagnosticCheck]:
    return [
        DiagnosticCheck(
            id=f"c{i}", category="T", title=f"c{i}", status=status, message="m"
        )
        for i, status in enumerate(statuses)
    ]


@pytest.mark.parametrize(
    ("statuses", "strict", "expected"),
    [
        (("PASS", "PASS"), False, 0),
        (("PASS", "WARN"), False, 0),
        (("PASS", "WARN"), True, 1),
        (("PASS", "FAIL"), False, 1),
        (("PASS", "FAIL"), True, 1),
        (("PASS",), True, 0),
    ],
)
def test_main_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    statuses: tuple[str, ...],
    strict: bool,
    expected: int,
):
    seen: dict[str, object] = {}

    def fake_run_all(self, check_port=8000, *, host="127.0.0.1", auth_enabled=False):
        seen.update(port=check_port, host=host, auth=auth_enabled)
        return _fake_checks(*statuses)

    monkeypatch.setattr(GrokBotDoctor, "run_all_checks", fake_run_all)
    argv = ["--host", "0.0.0.0", "--auth", "--port", "9001", "--json"]
    if strict:
        argv.append("--strict")
    assert doctor_module.main(argv) == expected
    assert seen == {"port": 9001, "host": "0.0.0.0", "auth": True}
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is ("FAIL" not in statuses)
    assert payload["strict"] is strict
    assert [c["id"] for c in payload["checks"]] == [
        f"c{i}" for i in range(len(statuses))
    ]


def test_main_human_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        GrokBotDoctor,
        "run_all_checks",
        lambda self, check_port=8000, **kw: _fake_checks("PASS"),
    )
    assert doctor_module.main([]) == 0
    assert "All preflight checks passed" in capsys.readouterr().out

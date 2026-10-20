"""Tests for Grok Bot Preflight Health Doctor."""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from omega_prime.grokbot import doctor as doctor_module
from omega_prime.grokbot.audit import GrokBotAuditTracer, default_audit_path
from omega_prime.grokbot.doctor import (
    DiagnosticCheck,
    GrokBotDoctor,
    run_doctor_checks,
)
from omega_prime.grokbot.manifest import find_repo_root

NEW_CHECK_IDS = (
    "policy_loads",
    "roster_consistency",
    "template_integrity",
    "audit_chain",
    "bind_exposure",
    "token_strength",
    "connector_secrets",
    "ui_control_health",
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


def test_ui_control_health_passes_for_existing_private_store(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    control = state / "ui-control"
    control.mkdir(mode=0o700)

    check = GrokBotDoctor(env={"OMEGA_PRIME_STATE_DIR": str(state)})._check_ui_control()

    assert check.id == "ui_control_health"
    assert check.status == "PASS"
    assert str(control) in check.message
    assert "mode=0700" in check.message


def test_ui_control_health_fails_when_control_overlaps_writable_bind(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "ui-control").mkdir(mode=0o700)
    env = {
        "OMEGA_PRIME_STATE_DIR": str(state),
        "OMEGA_PRIME_WORK_ROOT": str(state / "ui-control" / "child"),
    }

    check = GrokBotDoctor(env=env)._check_ui_control()

    assert check.status == "FAIL"
    assert "overlaps" in check.message


def test_ui_control_health_fails_for_unwritable_control_mode(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    control = state / "ui-control"
    control.mkdir(mode=0o700)
    control.chmod(0o500)

    check = GrokBotDoctor(env={"OMEGA_PRIME_STATE_DIR": str(state)})._check_ui_control()

    assert check.status == "FAIL"
    assert "mode 0700" in check.message


def test_ui_control_health_fails_for_unsafe_symlink_path(tmp_path: Path):
    actual_state = tmp_path / "actual-state"
    actual_state.mkdir(mode=0o700)
    (actual_state / "ui-control").mkdir(mode=0o700)
    state_link = tmp_path / "state-link"
    state_link.symlink_to(actual_state, target_is_directory=True)

    check = GrokBotDoctor(
        env={"OMEGA_PRIME_STATE_DIR": str(state_link)}
    )._check_ui_control()

    assert check.status == "FAIL"
    assert "symlink" in check.message


def test_ui_control_health_does_not_create_missing_store(tmp_path: Path):
    state = tmp_path / "missing-state"

    check = GrokBotDoctor(env={"OMEGA_PRIME_STATE_DIR": str(state)})._check_ui_control()

    assert check.status == "WARN"
    assert "RPC-UI asks are unavailable" in check.message
    assert not state.exists()


def test_ui_control_health_checks_existing_store_children(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    control = state / "ui-control"
    control.mkdir(mode=0o700)
    requests = control / "requests"
    queue = control / "queue"
    requests.mkdir(mode=0o700)
    queue.mkdir(mode=0o700)
    (requests / "request.json").write_text("{}", encoding="utf-8")
    (requests / "request.json").chmod(0o644)

    check = GrokBotDoctor(env={"OMEGA_PRIME_STATE_DIR": str(state)})._check_ui_control()

    assert check.status == "FAIL"
    assert "control record has unsafe type, owner or mode" in check.message


def test_ui_control_health_allows_absent_request_and_queue_directories(
    tmp_path: Path,
):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "ui-control").mkdir(mode=0o700)

    check = GrokBotDoctor(env={"OMEGA_PRIME_STATE_DIR": str(state)})._check_ui_control()

    assert check.status == "PASS"


@pytest.mark.parametrize("unsafe_child", ["queue_mode", "requests_symlink"])
def test_ui_control_health_fails_for_unsafe_child_directory(
    tmp_path: Path, unsafe_child: str
):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    control = state / "ui-control"
    control.mkdir(mode=0o700)
    requests = control / "requests"
    queue = control / "queue"
    requests.mkdir(mode=0o700)
    queue.mkdir(mode=0o700)
    if unsafe_child == "queue_mode":
        queue.chmod(0o755)
    else:
        requests.rmdir()
        target = tmp_path / "requests-target"
        target.mkdir(mode=0o700)
        requests.symlink_to(target, target_is_directory=True)

    check = GrokBotDoctor(env={"OMEGA_PRIME_STATE_DIR": str(state)})._check_ui_control()

    assert check.status == "FAIL"
    assert "unsafe" in check.message or "symlink" in check.message

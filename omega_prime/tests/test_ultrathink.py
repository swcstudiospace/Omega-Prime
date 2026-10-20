"""Phase 35: ultrathink bridge tools and turn routine behind fakes."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from omega_prime.grokbot.doctor import GrokBotDoctor, summarize_checks
from omega_prime.integrations.ultrathink import (
    READINESS_ENV_KEYS,
    ULTRATHINK_REQUIRED_BINS,
    ship_enabled,
    ultrathink_readiness,
)
from omega_prime.routines.ultrathink_turn import (
    _candidates,
    candidate_state_dirs,
    resolve_turn_plan,
)
from omega_prime.setup_check import check_ultrathink, run_checks
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.ultrathink import (
    UltrathinkClient,
    UltrathinkContext,
    register_ultrathink_tools,
)


def _run_factory(routes, calls=None):
    calls = [] if calls is None else calls

    def run(argv, timeout=120):
        calls.append(argv)
        for binary, sub, result in routes:
            if argv[0].endswith(f"/bin/{binary}") and argv[1 : 1 + len(sub)] == sub:
                return dict(result)
        return {"exit_code": 1, "stdout": "", "stderr": "no route"}

    return run


def _ctx(**overrides):
    calls = overrides.pop("calls", None)
    routes = [
        (
            "ultrathink",
            ["status"],
            {"exit_code": 0, "stdout": "Prompt Uplift on\n", "stderr": ""},
        ),
        (
            "ultrathink-mcp",
            ["track", "complete"],
            {"exit_code": 0, "stdout": '{"ok": true}', "stderr": ""},
        ),
        (
            "ultrathink-mcp",
            ["session", "mark"],
            {"exit_code": 0, "stdout": '{"synced": true}', "stderr": ""},
        ),
        (
            "ultrathink-ship",
            ["assess"],
            {"exit_code": 0, "stdout": '{"done": true}', "stderr": ""},
        ),
        (
            "ultrathink-ship",
            ["pr"],
            {"exit_code": 0, "stdout": '{"ok": true}', "stderr": ""},
        ),
        (
            "ultrathink-ship",
            ["review"],
            {"exit_code": 0, "stdout": '{"ready": true}', "stderr": ""},
        ),
        (
            "ultrathink-ship",
            ["merge"],
            {"exit_code": 0, "stdout": '{"merged": true}', "stderr": ""},
        ),
    ]
    base = dict(root="/opt/ultrathink", run=_run_factory(routes, calls))
    base.update(overrides)
    return UltrathinkContext(**base)


def test_bridge_verbs_and_argv():
    calls: list[list[str]] = []
    client = UltrathinkClient(_ctx(env={"ULTRATHINK_SHIP": "1"}, calls=calls))
    assert client.track_complete("s.json") == {"ok": True, "result": {"ok": True}}
    assert client.session_mark("s.json", "synced") == {
        "ok": True,
        "result": {"synced": True},
    }
    assert client.ship_assess("s.json") == {"ok": True, "result": {"done": True}}
    assert client.ship_pr("s.json") == {"ok": True, "result": {"ok": True}}
    assert client.ship_review("s.json") == {"ok": True, "result": {"ready": True}}
    assert client.ship_merge("s.json") == {"ok": True, "result": {"merged": True}}
    assert [call[1:] for call in calls[-3:]] == [
        ["pr", "--state", "s.json"],
        ["review", "--state", "s.json"],
        ["merge", "--state", "s.json"],
    ]
    with pytest.raises(ValueError):
        client.track_complete("")
    with pytest.raises(ValueError):
        client.session_mark("s.json", "bogus")


@pytest.mark.parametrize(
    "value",
    [None, "", "0", "false", "FALSE", "TRUE", "True", " true ", "1 ", "yes", "on", "2"],
)
def test_ship_disabled_strict_values(value):
    calls: list[list[str]] = []
    env = {} if value is None else {"ULTRATHINK_SHIP": value}
    client = UltrathinkClient(_ctx(env=env, calls=calls))
    for action in (client.ship_pr, client.ship_review, client.ship_merge):
        assert "ship_disabled" in action("s.json")["error"]
    assert calls == []


def test_ship_disabled_precedes_root_and_nonmutating_verbs_remain_available():
    calls: list[list[str]] = []
    client = UltrathinkClient(_ctx(calls=calls, root=""))
    assert "ship_disabled" in client.ship_pr("s.json")["error"]
    assert calls == []
    assert "not_configured" in client.status()["error"]
    assert "not_configured" in client.ship_assess("s.json")["error"]
    assert "not_configured" in client.track_complete("s.json")["error"]
    assert "not_configured" in client.session_mark("s.json", "synced")["error"]


def test_unconfigured_nonzero_and_approvals():
    bare = UltrathinkClient(UltrathinkContext())
    assert "not_configured" in bare.status()["error"]
    assert "ship_disabled" in bare.ship_pr("s.json")["error"]
    enabled_unconfigured = UltrathinkClient(
        UltrathinkContext(env={"ULTRATHINK_SHIP": "true"})
    )
    assert "not_configured" in enabled_unconfigured.ship_pr("s.json")["error"]
    failing = UltrathinkClient(
        _ctx(
            run=lambda argv, timeout=120: {
                "exit_code": 2,
                "stdout": "",
                "stderr": "boom",
            }
        )
    )
    assert "upstream_error" in failing.ship_assess("s.json")["error"]
    assert "boom" in failing.ship_assess("s.json")["output_tail"]

    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    register_ultrathink_tools(
        registry,
        UltrathinkClient(_ctx(env={"ULTRATHINK_SHIP": "true"})),
    )
    for gated in (
        "ult_track_complete",
        "ult_session_mark",
        "ult_ship_pr",
        "ult_ship_review",
        "ult_ship_merge",
    ):
        args = (
            {"state": "s.json", "mark": "synced"}
            if gated == "ult_session_mark"
            else {"state": "s.json"}
        )
        assert json.loads(registry.dispatch(gated, args)) == {
            "error": "approval required",
            "tool": gated,
        }
    assert log.approve("ult_ship_pr", "ada").get("approved") is True
    assert (
        json.loads(registry.dispatch("ult_ship_pr", {"state": "s.json"}))["ok"] is True
    )
    assert json.loads(registry.dispatch("ult_status", {}))["ok"] is True
    assert log.approve("ult_session_mark", "ada").get("approved") is True
    assert (
        "kicked-off or synced"
        in json.loads(
            registry.dispatch("ult_session_mark", {"state": "s.json", "mark": "bogus"})
        )["error"]
    )


def test_resolve_turn_plan(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    spec = second / "spec.xml"
    spec.write_text("<ORIGINAL>hi</ORIGINAL>", encoding="utf-8")
    (second / "last-plan.json").write_text(
        json.dumps({"specPath": str(spec)}), encoding="utf-8"
    )
    found = resolve_turn_plan(state_dirs=[first, second])
    assert found["found"] is True and found["spec_exists"] is True
    assert found["stale"] is False
    assert found["plan_path"].endswith("last-plan.json")
    (first / "last-plan.json").write_text("not json", encoding="utf-8")
    assert resolve_turn_plan(state_dirs=[first])["found"] is False
    assert resolve_turn_plan(state_dirs=[tmp_path / "missing"])["found"] is False
    assert resolve_turn_plan(env={"HOME": str(tmp_path)})["found"] is False
    state = tmp_path / "state"
    (state / "ultrathink").mkdir(parents=True)
    (state / "ultrathink" / "last-plan.json").write_text(
        json.dumps({"specPath": str(spec)}), encoding="utf-8"
    )
    preferred = resolve_turn_plan(
        env={"HOME": str(tmp_path), "OMEGA_PRIME_STATE_DIR": str(state)}
    )
    assert preferred["found"] is True
    assert preferred["plan_path"].endswith("ultrathink/last-plan.json")
    aged = tmp_path / "aged"
    aged.mkdir()
    plan_file = aged / "last-plan.json"
    plan_file.write_text(json.dumps({"specPath": str(spec)}), encoding="utf-8")
    old = time.time() - 25 * 3600.0
    os.utime(plan_file, (old, old))

    stale = resolve_turn_plan(state_dirs=[aged])
    assert stale["found"] is False and stale["stale"] is True
    assert resolve_turn_plan(state_dirs=[aged], max_age_hours=48.0)["found"] is True


def _isolated_env(tmp_path, **overrides):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home)}
    for key in READINESS_ENV_KEYS:
        if key != "HOME":
            env.pop(key, None)
    env.update(overrides)
    return env


def _fake_root(tmp_path, modes=None):
    root = tmp_path / "ultrathink"
    bindir = root / "bin"
    bindir.mkdir(parents=True)
    modes = modes or {}
    for name in ULTRATHINK_REQUIRED_BINS:
        mode = modes.get(name, "exec")
        path = bindir / name
        if mode == "dir":
            path.mkdir()
        elif mode != "missing":
            path.write_text("", encoding="utf-8")
            path.chmod(0o755 if mode == "exec" else 0o644)
    return root


def test_candidate_state_dirs_precedence_and_pi_exclusivity(tmp_path):
    env = _isolated_env(
        tmp_path,
        OMEGA_PRIME_STATE_DIR="/state",
        ULTRATHINK_STATE_DIR="/ultra",
        GROK_PLUGIN_DATA="/plugin",
        GROK_HOME="/grok",
        HERMES_HOME="/hermes",
        XDG_CONFIG_HOME="/xdg",
        PI_CODING_AGENT_DIR="/pi",
    )
    expected = [
        Path("/state/ultrathink"),
        Path("/ultra"),
        Path("/plugin/ultrathink"),
        Path("/grok/plugin-data/ultrathink"),
        Path("/hermes/ultrathink"),
        Path("/xdg/muse/ultrathink"),
        Path("/pi/ultrathink"),
        Path(env["HOME"]) / ".claude/ultrathink",
    ]
    assert candidate_state_dirs(env) == expected
    assert _candidates(env) == [path / "last-plan.json" for path in expected]
    defaults = candidate_state_dirs({"HOME": env["HOME"]})
    assert defaults == [
        Path(env["HOME"]) / ".grok/plugin-data/ultrathink",
        Path(env["HOME"]) / ".hermes/ultrathink",
        Path(env["HOME"]) / ".config/muse/ultrathink",
        Path(env["HOME"]) / ".omp/agent/ultrathink",
        Path(env["HOME"]) / ".claude/ultrathink",
    ]


@pytest.mark.parametrize("root_value", [None, ""])
def test_readiness_not_configured_never_probes_bin(tmp_path, monkeypatch, root_value):
    distractor = _fake_root(tmp_path)
    env = _isolated_env(
        tmp_path, **({"ULTRATHINK_ROOT": root_value} if root_value is not None else {})
    )
    original_is_file = Path.is_file
    original_is_dir = Path.is_dir

    def guard(method):
        def checked(path):
            assert not (path == distractor / "bin" or path.parent == distractor / "bin")
            return method(path)

        return checked

    monkeypatch.setattr(Path, "is_file", guard(original_is_file))
    monkeypatch.setattr(Path, "is_dir", guard(original_is_dir))
    monkeypatch.setattr(
        os, "access", lambda path, mode: pytest.fail("unexpected access probe")
    )
    result = ultrathink_readiness(env)
    assert result["state"] == "not_configured"
    assert result["root"] == ""
    assert result["preferred_state_dir"]
    assert result["problems"] == []


def test_readiness_root_is_file_reports_all_missing_binaries(tmp_path):
    root = tmp_path / "root-file"
    root.write_text("not a directory", encoding="utf-8")

    result = ultrathink_readiness(_isolated_env(tmp_path, ULTRATHINK_ROOT=str(root)))

    assert result["state"] == "root_invalid"
    assert result["root"] == str(root)
    assert result["problems"] == [
        f"bin/{name}: missing" for name in ULTRATHINK_REQUIRED_BINS
    ]


@pytest.mark.parametrize("name", ULTRATHINK_REQUIRED_BINS)
@pytest.mark.parametrize(
    "mode,reason",
    [("missing", "missing"), ("dir", "not a file"), ("noexec", "not executable")],
)
def test_readiness_invalid_binary_matrix(tmp_path, name, mode, reason):
    root = _fake_root(tmp_path, {name: mode})
    result = ultrathink_readiness(_isolated_env(tmp_path, ULTRATHINK_ROOT=str(root)))
    assert result["state"] == "root_invalid"
    assert result["problems"] == [f"bin/{name}: {reason}"]


@pytest.mark.parametrize(
    "value", [None, "", "0", "false", "TRUE", "True", " true ", "yes", "on", "2"]
)
def test_readiness_ship_literals_off(tmp_path, value):
    root = _fake_root(tmp_path)
    env = _isolated_env(tmp_path, ULTRATHINK_ROOT=str(root))
    if value is not None:
        env["ULTRATHINK_SHIP"] = value
    assert ultrathink_readiness(env)["state"] == "ready_ship_off"
    assert ultrathink_readiness(env)["ship_enabled"] is ship_enabled(env)


@pytest.mark.parametrize("value", ["1", "true"])
def test_readiness_ship_literals_on(tmp_path, value):
    root = _fake_root(tmp_path)
    env = _isolated_env(tmp_path, ULTRATHINK_ROOT=str(root), ULTRATHINK_SHIP=value)
    assert ultrathink_readiness(env)["state"] == "ready_ship_on"


def test_readiness_readonly_guards(tmp_path, monkeypatch):
    root = _fake_root(tmp_path)
    env = _isolated_env(tmp_path, ULTRATHINK_ROOT=str(root))
    for candidate in candidate_state_dirs(env):
        candidate.mkdir(parents=True, exist_ok=True)
        (candidate / "last-plan.json").write_text("{malformed", encoding="utf-8")
    (root / "omega-prime.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawn"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("spawn"))
    from omega_prime import config
    from omega_prime.routines import ultrathink_turn

    monkeypatch.setattr(
        ultrathink_turn,
        "resolve_turn_plan",
        lambda *a, **k: pytest.fail("plan resolution"),
    )
    monkeypatch.setattr(
        config, "load_config", lambda *a, **k: pytest.fail("config read")
    )
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert path.name not in ("last-plan.json", "omega-prime.json")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    assert ultrathink_readiness(env)["state"] == "ready_ship_off"


@pytest.mark.parametrize(
    "value,state,ok,ship,root_kind",
    [
        (None, "not_configured", None, "off", None),
        ("invalid", "root_invalid", False, "off", "invalid"),
        ("off", "ready_ship_off", True, "off", "valid"),
        ("on", "ready_ship_on", True, "on", "valid"),
    ],
)
def test_check_ultrathink_row_mapping(
    tmp_path, monkeypatch, value, state, ok, ship, root_kind
):
    env = _isolated_env(tmp_path)
    if root_kind == "valid":
        root = _fake_root(tmp_path)
        monkeypatch.setenv("ULTRATHINK_ROOT", str(root))
    elif root_kind == "invalid":
        root = tmp_path / "missing"
        monkeypatch.setenv("ULTRATHINK_ROOT", str(root))
    else:
        root = None
        monkeypatch.delenv("ULTRATHINK_ROOT", raising=False)
    ship_value = "true" if value == "on" else None
    if ship_value is None:
        monkeypatch.delenv("ULTRATHINK_SHIP", raising=False)
    else:
        monkeypatch.setenv("ULTRATHINK_SHIP", ship_value)
    monkeypatch.setenv("HOME", env["HOME"])
    result, detail = check_ultrathink(tmp_path)
    assert result is ok
    assert state in detail
    assert ("ULTRATHINK_ROOT not set" if root is None else str(root)) in detail
    assert f"ship {ship}" in detail
    assert "plan discovery dir:" in detail
    assert str(Path(env["HOME"]) / ".grok/plugin-data/ultrathink") in detail
    assert ship_value is None or f"ULTRATHINK_SHIP={ship_value}" not in detail
    assert set(run_checks(tmp_path)["ultrathink"]) == {"ok", "detail"}


@pytest.mark.parametrize(
    "state,status,root_kind,ship",
    [
        ("not_configured", "WARN", None, "off"),
        ("root_invalid", "FAIL", "invalid", "off"),
        ("ready_ship_off", "PASS", "valid", "off"),
        ("ready_ship_on", "PASS", "valid", "on"),
    ],
)
def test_doctor_ultrathink_row(tmp_path, state, status, root_kind, ship):
    env = _isolated_env(tmp_path)
    if root_kind == "valid":
        root = _fake_root(tmp_path)
        env["ULTRATHINK_ROOT"] = str(root)
    elif root_kind == "invalid":
        root = tmp_path / "missing"
        env["ULTRATHINK_ROOT"] = str(root)
    else:
        root = None
    ship_value = "1" if ship == "on" else None
    if ship_value is not None:
        env["ULTRATHINK_SHIP"] = ship_value
    doctor = GrokBotDoctor(tmp_path, env=env)
    check = doctor._check_ultrathink()
    assert check.id == "ultrathink_readiness"
    assert check.category == "Integrations" and check.status == status
    assert state in check.message
    assert ("ULTRATHINK_ROOT not set" if root is None else str(root)) in check.message
    assert f"ship {ship}" in check.message
    assert "plan discovery dir:" in check.message
    assert str(Path(env["HOME"]) / ".grok/plugin-data/ultrathink") in check.message
    assert ship_value is None or f"ULTRATHINK_SHIP={ship_value}" not in check.message
    assert (check.remediation is not None) is (status in ("WARN", "FAIL"))
    assert [c.id for c in doctor.run_all_checks(check_port=59123)].count(
        "ultrathink_readiness"
    ) == 1
    summary = summarize_checks([check])
    assert set(summary["checks"][0]) == {
        "id",
        "name",
        "category",
        "status",
        "message",
        "fix_hint",
    }

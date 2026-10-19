"""Phase 35: ultrathink bridge tools and turn routine behind fakes."""

from __future__ import annotations

import json

import pytest

from omega_prime.routines.ultrathink_turn import resolve_turn_plan
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.ultrathink import (
    UltrathinkClient,
    UltrathinkContext,
    register_ultrathink_tools,
)


def _run_factory(routes):
    calls: list[list] = []

    def run(argv, timeout=120):
        calls.append(argv)
        for binary, sub, result in routes:
            if argv[0].endswith(f"/bin/{binary}") and argv[1 : 1 + len(sub)] == sub:
                return dict(result)
        return {"exit_code": 1, "stdout": "", "stderr": "no route"}

    return run


def _ctx(**overrides):
    base = dict(
        root="/opt/ultrathink",
        run=_run_factory(
            [
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
        ),
    )
    base.update(overrides)
    return UltrathinkContext(**base)


def test_bridge_verbs_and_argv():
    client = UltrathinkClient(_ctx())
    assert client.status() == {"ok": True, "output": "Prompt Uplift on\n"}
    assert client.track_complete("s.json") == {"ok": True, "result": {"ok": True}}
    assert client.session_mark("s.json", "synced") == {
        "ok": True,
        "result": {"synced": True},
    }
    assert client.ship_assess("s.json") == {"ok": True, "result": {"done": True}}
    assert client.ship_pr("s.json") == {"ok": True, "result": {"ok": True}}
    assert client.ship_review("s.json") == {"ok": True, "result": {"ready": True}}
    assert client.ship_merge("s.json") == {"ok": True, "result": {"merged": True}}
    with pytest.raises(ValueError):
        client.track_complete("")
    with pytest.raises(ValueError):
        client.session_mark("s.json", "bogus")


def test_unconfigured_nonzero_and_approvals():
    bare = UltrathinkClient(UltrathinkContext())
    assert "not_configured" in bare.status()["error"]
    assert "not_configured" in bare.ship_pr("s.json")["error"]
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
    register_ultrathink_tools(registry, UltrathinkClient(_ctx()))
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
    import os as _os
    import time as _time

    aged = tmp_path / "aged"
    aged.mkdir()
    plan_file = aged / "last-plan.json"
    plan_file.write_text(json.dumps({"specPath": str(spec)}), encoding="utf-8")
    old = _time.time() - 25 * 3600.0
    _os.utime(plan_file, (old, old))
    stale = resolve_turn_plan(state_dirs=[aged])
    assert stale["found"] is False and stale["stale"] is True
    assert resolve_turn_plan(state_dirs=[aged], max_age_hours=48.0)["found"] is True

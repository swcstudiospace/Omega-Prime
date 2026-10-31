"""Phase 32: app pack tool family behind fakes."""

from __future__ import annotations

import json

import pytest

from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.packs import PacksClient, PacksContext, register_packs_tools
from omega_prime.tools.registry import ToolRegistry

PACKS = {
    "desklanes": {
        "tools": [
            "desklanes_api_smoke",
            "desklanes_scoreboard_get",
            "desklanes_push_test",
            "desklanes_store_listing_get",
            "desklanes_crash_reports",
        ],
        "seats": ["web", "android", "ios"],
    },
    "kanbanos": {
        "tools": [
            "kanbanos_api_smoke",
            "kanbanos_supabase_query",
            "kanbanos_push_test",
            "kanbanos_feature_flags",
            "kanbanos_crash_reports",
        ],
        "seats": ["web", "android", "ios"],
    },
}


def _http_factory(routes):
    calls: list[tuple] = []

    def http(method, url, headers=None, params=None, json_body=None, timeout=10.0):
        calls.append((method, url))
        for prefix, result in routes:
            if url.endswith(prefix):
                return dict(result)
        return {"error": "upstream_error", "reason": "no route"}

    return http


def _ctx(**overrides):
    base = {
        "packs": dict(PACKS),
        "api_bases": {"desklanes": "https://api.example.test"},
        "http": _http_factory([]),
    }
    base.update(overrides)
    return PacksContext(**base)


def test_tools_load_unload_and_ceiling():
    client = PacksClient(_ctx())
    first = client.tools_load("desklanes", "t-1")
    assert first["ok"] is True and first["live_tools"] == 5
    assert first["tools"] == sorted(PACKS["desklanes"]["tools"])
    second = client.tools_load("kanbanos", "t-1")
    assert second["live_tools"] == 10
    assert "no pack named nope" in client.tools_load("nope", "t-1")["error"]
    assert sorted(client.tools_load("nope", "t-1")["available"]) == [
        "desklanes",
        "kanbanos",
    ]
    estrange = PacksClient(_ctx(absorbed=("lead",)))
    assert "forbidden" in estrange.tools_load("desklanes", "t-1")["error"]
    big = PacksClient(
        _ctx(packs={"huge": {"tools": [f"t-{i}" for i in range(21)], "seats": ["web"]}})
    )
    assert "ceiling" in big.tools_load("huge", "t-1")["error"]
    unloaded = client.tools_load("desklanes", "t-1", unload=True)
    assert unloaded["ok"] is True and unloaded["live_tools"] == 5
    assert client.tools_load("desklanes", "t-1", unload=True)["loaded"] == []


def test_api_backends_shapes_and_guards():
    http = _http_factory(
        [
            ("/health", {"ok": True, "status": 200, "body": {"version": "1.2"}}),
            ("/desk/query", {"ok": True, "status": 200, "body": {"rows": [{"a": 1}]}}),
            ("/desk/push-test", {"ok": True, "status": 200, "body": {"sent": 1}}),
            ("/desk/flags", {"ok": True, "status": 200, "body": {"dark": True}}),
            ("/desk/crashes", {"ok": True, "status": 200, "body": [{"sig": "x"}]}),
            ("/api/scoreboard", {"ok": True, "status": 200, "body": [{"lane": 1}]}),
            (
                "/desk/store-listing",
                {"ok": True, "status": 200, "body": {"title": "Lanes"}},
            ),
            (
                "/api/render-jobs/j-1",
                {"ok": True, "status": 200, "body": {"state": "done"}},
            ),
        ]
    )
    client = PacksClient(_ctx(http=http))
    smoke = client.api_smoke("desklanes")
    assert smoke["ok"] is True and smoke["body"] == {"version": "1.2"}
    assert isinstance(smoke["ms"], float)
    assert client.supabase_query("desklanes", "select 1")["body"] == {
        "rows": [{"a": 1}]
    }
    assert "only SELECT" in client.supabase_query("desklanes", "delete from t")["error"]
    assert (
        "write keyword"
        in client.supabase_query("desklanes", "select * from t; drop table t")["error"]
    )
    with pytest.raises(ValueError):
        client.supabase_query("desklanes", "select 1", 0)
    pushed = client.push_test("desklanes", "android", "dev-1")
    assert pushed == {"ok": True, "result": {"sent": 1}}
    assert client.feature_flags("desklanes") == {"flags": {"dark": True}}
    assert client.crash_reports("desklanes") == {"groups": [{"sig": "x"}]}
    assert client.scoreboard_get("desklanes") == {"lanes": [{"lane": 1}]}
    with pytest.raises(ValueError):
        client.scoreboard_get("desklanes", True)
    assert client.store_listing_get("desklanes", "play") == {
        "listing": {"title": "Lanes"}
    }
    assert client.render_job_status("desklanes", "j-1") == {"job": {"state": "done"}}
    down = PacksClient(_ctx(http=_http_factory([])))
    assert down.api_smoke("desklanes").get("error") == "upstream_error"
    assert "no route" in down.push_test("desklanes", "android", "d")["error"]
    assert down.feature_flags("desklanes").get("error") == "upstream_error"
    assert down.render_job_status("desklanes", "j-9").get("error") == "upstream_error"
    exploding = PacksClient(_ctx(http=lambda *a, **k: 1 / 0))
    assert (
        "ZeroDivisionError" in exploding.render_job_status("desklanes", "j-1")["error"]
    )


def test_unconfigured_and_approvals():
    bare = PacksClient(PacksContext())
    assert "not_configured" in bare.api_smoke("desklanes")["error"]
    assert bare.supabase_query("desklanes", "select 1")["rows"] == []
    assert "not_configured" in bare.push_test("desklanes", "android", "d")["error"]
    assert bare.feature_flags("desklanes")["flags"] == {}
    assert bare.crash_reports("desklanes")["groups"] == []
    assert bare.scoreboard_get("desklanes")["lanes"] == []
    assert "not_configured" in bare.store_listing_get("desklanes", "play")["error"]
    assert "not_configured" in bare.render_job_status("desklanes", "j")["error"]

    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    register_packs_tools(registry, PacksClient(_ctx()))
    assert json.loads(
        registry.dispatch("app_tools_load", {"app": "desklanes", "task_id": "t-1"})
    ) == {"error": "approval required", "tool": "app_tools_load"}
    assert json.loads(
        registry.dispatch(
            "app_push_test",
            {"app": "desklanes", "platform": "ios", "device_label": "d-1"},
        )
    ) == {"error": "approval required", "tool": "app_push_test"}
    assert log.approve("app_tools_load", "ada").get("approved") is True
    loaded = json.loads(
        registry.dispatch("app_tools_load", {"app": "desklanes", "task_id": "t-1"})
    )
    assert loaded["ok"] is True and loaded["live_tools"] == 5
    smoke = json.loads(registry.dispatch("app_api_smoke", {"app": "desklanes"}))
    assert smoke.get("error") == "upstream_error"
    assert (
        "limit must be"
        in json.loads(
            registry.dispatch("app_scoreboard_get", {"app": "x", "limit": 0})
        )["error"]
    )

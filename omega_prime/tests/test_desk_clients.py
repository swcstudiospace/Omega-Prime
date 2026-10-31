"""Phase 65: desk service clients behind a fake transport. No network."""

from __future__ import annotations

import json
import re
from pathlib import Path

from omega_prime.integrations.desk_clients import (
    AscClient,
    GreptileClient,
    PlayClient,
    RailwayClient,
    VercelClient,
    asc_signing_available,
    clients_from_env,
    guarded_browser_factory,
    urllib_transport,
)
from omega_prime.mcp_server import default_registry
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.browser_egress import GuardedBrowserFactory
from omega_prime.tools.infra import InfraClient, InfraContext
from omega_prime.tools.mobile import MobileClient, MobileContext
from omega_prime.tools.quality import QualityClient, QualityContext
from omega_prime.tools.webpack import WebClient, WebContext

_RAILWAY_PROJECT = {
    "id": "p-acme",
    "services": {
        "edges": [
            {
                "node": {
                    "id": "s-1",
                    "name": "web",
                    "serviceInstances": {
                        "edges": [
                            {
                                "node": {
                                    "latestDeployment": {
                                        "id": "d-9",
                                        "status": "SUCCESS",
                                    }
                                }
                            }
                        ]
                    },
                }
            }
        ]
    },
    "environments": {"edges": [{"node": {"id": "e-1", "name": "production"}}]},
}
_SECRET = "s3cret-value"
_OTHER_SECRET = "other-secret"


def _install(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    (install / "omega_prime").mkdir(parents=True, exist_ok=True)
    return install


def _context(registry, tool_name: str):
    handler = registry._tools[tool_name].handler
    found = _find_context(handler, set())
    if found is None:
        raise AssertionError(f"{tool_name} did not close over a context")
    return found


def _find_context(fn, seen: set[int]):
    if id(fn) in seen or not callable(fn):
        return None
    seen.add(id(fn))
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        ctx = getattr(value, "ctx", None)
        if ctx is not None:
            return ctx
        if callable(value):
            found = _find_context(value, seen)
            if found is not None:
                return found
    return None


def _railway_transport(requests: list[dict]):
    def transport(request: dict) -> dict:
        requests.append(request)
        query = (request.get("body") or {}).get("query") or ""
        if "serviceInstanceRedeploy" in query:
            return {
                "status": 200,
                "body": {"data": {"serviceInstanceRedeploy": True}},
            }
        if "deploymentLogs" in query:
            return {"status": 200, "body": {"data": {"deploymentLogs": ["l1", "l2"]}}}
        if "variables(" in query:
            return {
                "status": 200,
                "body": {
                    "data": {"variables": {"PORT": _SECRET, "TOKEN": _OTHER_SECRET}}
                },
            }
        if "projects(" in query:
            return {
                "status": 200,
                "body": {
                    "data": {
                        "projects": {
                            "edges": [{"node": {"id": "p-acme", "name": "acme"}}],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                },
            }
        if "project(id:" in query:
            return {"status": 200, "body": {"data": {"project": _RAILWAY_PROJECT}}}
        if "deployment(id:" in query:
            return {
                "status": 200,
                "body": {
                    "data": {
                        "deployment": {
                            "id": "d-8",
                            "projectId": "p-acme",
                            "serviceId": "s-1",
                        }
                    }
                },
            }
        raise AssertionError(query)

    return transport


def test_railway_maps_canned_body_and_never_asks_for_values():
    requests: list[dict] = []
    railway = RailwayClient("railway-token", transport=_railway_transport(requests))
    client = InfraClient(InfraContext(railway=railway, projects=["acme"]))
    status = client.railway_status()
    assert status["projects"][0]["id"] == "p-acme"
    assert status["projects"][0]["services"][0]["name"] == "web"
    assert status["projects"][0]["services"][0]["latest_deployment"]["id"] == "d-9"
    assert status["projects"][0]["environments"][0]["name"] == "production"
    logs = client.railway_logs("acme", "web")
    assert logs == {"deployment_id": "d-9", "lines": ["l1", "l2"]}
    older = client.railway_logs("acme", "web", deployment_id="d-8")
    assert older == {"deployment_id": "d-8", "lines": ["l1", "l2"]}
    assert railway.project_id("acme") == "p-acme"
    assert railway.deployment("d-8")["body"]["data"]["deployment"]["serviceId"] == "s-1"
    names = client.railway_variable_names("acme", "web")
    assert names == {"names": ["PORT", "TOKEN"]}
    assert _SECRET not in json.dumps(names)
    assert _OTHER_SECRET not in json.dumps(names)
    variable_requests = [
        item
        for item in requests
        if "variables(" in ((item.get("body") or {}).get("query") or "")
    ]
    assert variable_requests
    for item in variable_requests:
        query = item["body"]["query"]
        assert "value" not in query
        assert re.search(r"variables\([^)]*\)\s*\{", query) is None
        assert "value" not in item["body"]["variables"]
        blob = json.dumps(item["body"])
        assert _SECRET not in blob
        assert _OTHER_SECRET not in blob
    redeployed = client.railway_redeploy("acme", "web", "d-9")
    assert redeployed["ok"] is True
    assert redeployed["result"]["data"]["serviceInstanceRedeploy"] is True
    mutations = [
        item["body"]["query"]
        for item in requests
        if "mutation " in ((item.get("body") or {}).get("query") or "")
    ]
    assert len(mutations) == 1
    assert "serviceInstanceRedeploy" in mutations[0]
    assert "variableUpsert" not in mutations[0]
    assert "variableDelete" not in mutations[0]
    assert all(
        item["url"] == "https://backboard.railway.com/graphql/v2" for item in requests
    )
    assert all(
        item["headers"]["Authorization"] == "Bearer railway-token" for item in requests
    )
    assert "railway-token" not in repr(railway)
    assert RailwayClient("railway-token")._transport is urllib_transport


def test_railway_graphql_error_redacts_the_token():
    def explode(request: dict) -> dict:
        raise RuntimeError(request["headers"]["Authorization"])

    failed = RailwayClient("railway-secret", transport=explode).logs("d-1", 3)
    assert failed["error"] == "upstream_error"
    assert "railway-secret" not in json.dumps(failed)
    assert "[redacted]" in failed["reason"]

    def denied(_request: dict) -> dict:
        return {
            "status": 200,
            "body": {"errors": [{"message": "denied railway-secret"}], "data": None},
        }

    result = RailwayClient("railway-secret", transport=denied).logs("d-1", 3)
    assert result["error"] == "upstream_error"
    assert "denied" in result["reason"]
    assert "railway-secret" not in result["reason"]
    assert "body" not in result


def test_greptile_maps_canned_body():
    requests: list[dict] = []

    def transport(request: dict) -> dict:
        requests.append(request)
        if request["method"] == "POST":
            return {"status": 200, "body": {"review_id": "r-1"}}
        if request["url"].endswith("/reviews/comments"):
            return {"status": 200, "body": [{"id": "c-1", "addressed": False}]}
        return {"status": 200, "body": {"review_id": "r-1", "conclusion": "pass"}}

    greptile = GreptileClient(
        "greptile-key", github_token="github-token", transport=transport
    )
    client = QualityClient(QualityContext(root=".", greptile=greptile))
    triggered = client.greptile_review("trigger", pr_number=3)
    assert triggered["ok"] is True
    assert triggered["result"] == {"review_id": "r-1"}
    got = client.greptile_review("get", review_id="r-1")
    assert got["result"]["conclusion"] == "pass"
    comments = client.greptile_review("comments", pr_number=3)
    assert comments["result"] == [{"id": "c-1", "addressed": False}]
    assert requests[0]["url"] == "https://api.greptile.com/v2/reviews"
    assert requests[0]["body"]["repository"] == "swcstudiospace/Omega-Prime"
    assert requests[0]["body"]["prNumber"] == 3
    assert requests[0]["headers"]["X-Github-Token"] == "github-token"
    assert requests[1]["url"] == "https://api.greptile.com/v2/reviews/r-1"
    assert requests[2]["params"]["prNumber"] == 3
    bare = GreptileClient("greptile-key", transport=transport)
    bare.trigger("owner/repo", 1)
    assert "X-Github-Token" not in requests[-1]["headers"]
    assert "greptile-key" not in repr(greptile)


def test_vercel_maps_canned_body_and_transport_error_is_not_allowed():
    deployment = {
        "uid": "d-1",
        "state": "READY",
        "target": "production",
        "url": "acme.vercel.app",
        "created": 1,
        "meta": {},
    }

    def transport(request: dict) -> dict:
        url = request["url"]
        if url.startswith("https://api.vercel.com/v9/projects"):
            return {
                "status": 200,
                "body": {"projects": [{"id": "prj_1", "name": "acme"}]},
            }
        if url.startswith("https://api.vercel.com/v7/deployments"):
            assert request["params"]["projectId"] == "acme"
            assert request["params"]["limit"] == 10
            return {"status": 200, "body": {"deployments": [deployment]}}
        if url.startswith("https://api.vercel.com/v13/deployments/"):
            return {"status": 200, "body": {"uid": "d-9", "state": "READY"}}
        if "/promote/" in url:
            return {"status": 200, "body": {"aliased": True}}
        if "/rollback/" in url:
            return {"status": 200, "body": {"aliased": True}}
        raise AssertionError(url)

    vercel = VercelClient("vercel-token", transport=transport)
    client = WebClient(WebContext(root=".", vercel=vercel))
    listed = client.vercel_deployments("acme")
    assert listed["deployments"] == [deployment]
    assert (
        client.vercel_deployments("acme", deployment_id="d-9")["deployment"]["uid"]
        == "d-9"
    )
    assert client.vercel_promote("acme", "d-1") == {
        "ok": True,
        "result": {"aliased": True},
    }
    assert client.vercel_rollback("acme", "d-1") == {
        "ok": True,
        "result": {"aliased": True},
    }
    assert "allowlist" in client.vercel_deployments("evil")["error"]

    def down(_request: dict) -> dict:
        return {"status": 0, "body": {"reason": "transport down"}}

    blocked = WebClient(
        WebContext(root=".", vercel=VercelClient("vercel-token", transport=down))
    )
    assert VercelClient("vercel-token", transport=down).allowed("acme") is False
    refused = blocked.vercel_deployments("acme")
    assert "forbidden" in refused["error"]
    assert "allowlist" in refused["error"]


def test_play_maps_canned_body():
    requests: list[dict] = []

    def transport(request: dict) -> dict:
        requests.append(request)
        url = request["url"]
        if url.endswith(":commit"):
            return {"status": 200, "body": {"id": "e-1"}}
        if request["method"] == "PUT":
            return {"status": 200, "body": {"ok": True}}
        if request["method"] == "GET":
            return {
                "status": 200,
                "body": {
                    "track": "production",
                    "releases": [
                        {"name": "r1", "status": "completed", "versionCodes": ["1"]}
                    ],
                },
            }
        if url.endswith("/edits"):
            return {"status": 200, "body": {"id": "e-1"}}
        raise AssertionError(url)

    play = PlayClient("play-token", transport=transport)
    client = MobileClient(MobileContext(root=".", play=play))
    status = client.play_track_status("com.example.app", "production")
    assert status["edit_id"] == "e-1"
    assert status["track"]["track"] == "production"
    rolled = client.play_staged_rollout(
        "com.example.app", "production", 8, 0.2, "crash>2%"
    )
    assert rolled["ok"] is True
    assert rolled["edit_id"] == "e-1"
    assert any(
        item["method"] == "PUT" and "releases" in item["body"] for item in requests
    )
    assert any(item["url"].endswith(":commit") for item in requests)
    assert all(
        item["url"].startswith("https://androidpublisher.googleapis.com/")
        for item in requests
    )
    assert "play-token" not in repr(play)


def test_asc_maps_canned_body(tmp_path: Path):
    if not asc_signing_available():
        built = clients_from_env(
            {
                "ASC_KEY_ID": "kid",
                "ASC_ISSUER_ID": "issuer",
                "ASC_PRIVATE_KEY": "not-a-signer",
            }
        )
        assert built["asc"] is None
        return
    pem = _pem()
    key_path = tmp_path / "AuthKey.p8"
    key_path.write_text(pem, encoding="utf-8")
    requests: list[dict] = []

    def transport(request: dict) -> dict:
        requests.append(request)
        url = request["url"]
        assert url.startswith("https://api.appstoreconnect.apple.com/v1/")
        assert "BEGIN PRIVATE KEY" not in json.dumps(request)
        token = request["headers"]["Authorization"]
        assert token.startswith("Bearer ")
        assert token.removeprefix("Bearer ").count(".") == 2
        if url.endswith("/apps"):
            assert request["params"]["filter[bundleId]"] == "com.example.app"
            return {"status": 200, "body": {"data": [{"id": "app-1"}]}}
        if url.endswith("/builds"):
            return {
                "status": 200,
                "body": {
                    "data": [
                        {
                            "id": "b-1",
                            "attributes": {
                                "version": "42",
                                "processingState": "VALID",
                                "uploadedDate": "today",
                                "expired": False,
                            },
                        }
                    ]
                },
            }
        if url.endswith("/appStoreVersions"):
            return {"status": 200, "body": {"data": [{"id": "v-1"}]}}
        if url.endswith("/appStoreVersionPhasedReleases"):
            assert request["body"]["data"]["type"] == "appStoreVersionPhasedReleases"
            return {
                "status": 200,
                "body": {
                    "data": {"id": "pr-1", "type": "appStoreVersionPhasedReleases"}
                },
            }
        raise AssertionError(url)

    asc = AscClient("kid", "issuer", str(key_path), transport=transport)
    client = MobileClient(MobileContext(root=".", asc=asc))
    flight = client.testflight_status("com.example.app")
    assert flight["app_id"] == "app-1"
    assert flight["builds"][0]["version"] == "42"
    assert flight["builds"][0]["processingState"] == "VALID"
    released = client.appstore_phased_release("1.2.3", "crash-rate")
    assert released["ok"] is True
    assert released["phased_release"]["id"] == "pr-1"
    inline = AscClient("kid", "issuer", pem, transport=transport)
    direct = inline.request(
        "GET", "/apps", params={"filter[bundleId]": "com.example.app", "limit": 1}
    )
    assert direct["body"]["data"][0]["id"] == "app-1"


def test_asc_does_not_invent_a_signature(monkeypatch):
    called = False

    def transport(_request: dict) -> dict:
        nonlocal called
        called = True
        return {"status": 200, "body": {"data": []}}

    monkeypatch.setattr(
        "omega_prime.integrations.desk_clients.asc_signing_available",
        lambda: False,
    )
    client = AscClient("kid", "issuer", "not-a-key", transport=transport)
    result = client.request("GET", "/apps", params={"limit": 1})
    assert called is False
    assert result["error"] == "not_configured"
    assert "signature" not in result["reason"]
    assert (
        clients_from_env(
            {
                "ASC_KEY_ID": "kid",
                "ASC_ISSUER_ID": "issuer",
                "ASC_PRIVATE_KEY": "not-a-key",
            }
        )["asc"]
        is None
    )


def test_registry_without_tokens_leaves_clients_unset(tmp_path: Path):
    log = ApprovalLog()
    assert log.approve("qua_greptile_review", "ada").get("approved") is True
    install = _install(tmp_path)
    registry = default_registry(install, tmp_path / "home", approval_log=log, env={})
    infra = _context(registry, "infra_railway_status")
    quality = _context(registry, "qua_greptile_review")
    web = _context(registry, "web_vercel_deployments")
    mobile = _context(registry, "mob_play_track_status")
    assert infra.railway is None
    assert infra.projects == []
    assert quality.greptile is None
    assert Path(quality.root) == install
    assert web.vercel is None
    assert mobile.play is None
    assert mobile.asc is None
    assert (
        "not_configured"
        in json.loads(registry.dispatch("infra_railway_status", {}))["error"]
    )
    greptile = json.loads(
        registry.dispatch("qua_greptile_review", {"action": "get", "review_id": "r-1"})
    )
    assert "not_configured" in greptile["error"]
    vercel = json.loads(
        registry.dispatch("web_vercel_deployments", {"project": "acme"})
    )
    assert "not_configured" in vercel["error"]
    play = json.loads(
        registry.dispatch(
            "mob_play_track_status",
            {"package_name": "com.example.app", "track": "production"},
        )
    )
    assert "not_configured" in play["error"]
    asc = json.loads(
        registry.dispatch("mob_testflight_status", {"bundle_id": "com.example.app"})
    )
    assert "not_configured" in asc["error"]
    blank = clients_from_env(
        {
            "RAILWAY_TOKEN": " ",
            "GREPTILE_API_KEY": "",
            "VERCEL_TOKEN": "\n",
            "PLAY_CONSOLE_TOKEN": " ",
            "ASC_KEY_ID": "kid",
            "ASC_ISSUER_ID": "issuer",
            "ASC_PRIVATE_KEY": " ",
        }
    )
    assert blank["railway"] is None
    assert blank["greptile"] is None
    assert blank["vercel"] is None
    assert blank["play"] is None
    assert blank["asc"] is None
    assert blank["projects"] == []


def test_registry_with_tokens_attaches_clients(tmp_path: Path):
    install = _install(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    registry = default_registry(
        install,
        tmp_path / "home",
        env={
            "RAILWAY_TOKEN": "railway-token",
            "OMEGA_PRIME_RAILWAY_PROJECTS": "acme, beta",
            "GREPTILE_API_KEY": "greptile-key",
            "GREPTILE_GITHUB_TOKEN": "github-token",
            "VERCEL_TOKEN": "vercel-token",
            "PLAY_CONSOLE_TOKEN": "play-token",
            "ASC_KEY_ID": "kid",
            "ASC_ISSUER_ID": "issuer",
            "ASC_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nunused\n-----END PRIVATE KEY-----",
        },
        work_root=work,
    )
    infra = _context(registry, "infra_railway_status")
    quality = _context(registry, "qua_greptile_review")
    web = _context(registry, "web_vercel_deployments")
    mobile = _context(registry, "mob_testflight_status")
    assert isinstance(infra.railway, RailwayClient)
    assert infra.projects == ["acme", "beta"]
    assert isinstance(quality.greptile, GreptileClient)
    assert Path(quality.root) == work
    assert quality.run is None
    assert quality.acks is None
    assert quality.vcs is None
    assert isinstance(web.vercel, VercelClient)
    assert Path(web.root) == install
    assert web.fetch is not None
    assert web.transport is not None
    assert isinstance(mobile.play, PlayClient)
    assert Path(mobile.root) == install
    if asc_signing_available():
        assert isinstance(mobile.asc, AscClient)
    else:
        assert mobile.asc is None


def test_browser_factory_attached_only_when_it_constructs(tmp_path, monkeypatch):
    install = _install(tmp_path)
    real = guarded_browser_factory(object())
    registry = default_registry(install, tmp_path / "home", env={})
    attached = _context(registry, "web_vercel_deployments").browser_factory
    if real is None:
        assert attached is None
    else:
        assert isinstance(attached, GuardedBrowserFactory)

    class Exploding:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("browser unavailable")

    monkeypatch.setattr(
        "omega_prime.tools.browser_egress.GuardedBrowserFactory",
        Exploding,
    )
    failed = default_registry(install, tmp_path / "home", env={})
    assert _context(failed, "web_vercel_deployments").browser_factory is None

    class Stub:
        def __init__(self, transport, *, config):
            self.transport = transport
            self.config = config

    monkeypatch.setattr(
        "omega_prime.tools.browser_egress.GuardedBrowserFactory",
        Stub,
    )
    built = default_registry(install, tmp_path / "home", env={})
    factory = _context(built, "web_vercel_deployments").browser_factory
    assert isinstance(factory, Stub)
    assert factory.config is None
    assert factory.transport is not None


def _pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")

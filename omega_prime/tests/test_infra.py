"""Phase 30: infra tool family behind fakes."""

from __future__ import annotations

import json
from typing import Any

from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.infra import InfraClient, InfraContext, register_infra_tools
from omega_prime.tools.registry import ToolRegistry


class FakeRailway:
    def __init__(self, services=None):
        self.services_map = (
            services
            if services is not None
            else {"web": {"id": "s-1", "latest": {"id": "d-9"}}}
        )
        self.calls: list[tuple] = []

    def project_id(self, name):
        return f"p-{name}" if name in ("acme",) else None

    def project_status(self, name):
        self.calls.append(("status", name))
        edges = [
            {
                "node": {
                    "id": v["id"],
                    "name": k,
                    "serviceInstances": {
                        "edges": [{"node": {"latestDeployment": v["latest"]}}]
                    },
                }
            }
            for k, v in self.services_map.items()
        ]
        return {
            "body": {
                "data": {
                    "project": {
                        "id": f"p-{name}",
                        "services": {"edges": edges},
                        "environments": {
                            "edges": [{"node": {"id": "e-1", "name": "production"}}]
                        },
                    }
                }
            }
        }

    def logs(self, deployment_id, lines):
        self.calls.append(("logs", deployment_id, lines))
        return {"body": {"data": {"deploymentLogs": ["l1", "l2"]}}}

    def deployment(self, deployment_id):
        self.calls.append(("deployment", deployment_id))
        owners = {
            "d-8": ("p-acme", "s-1"),
            "d-7": ("p-acme", "s-2"),
            "d-6": ("p-other", "s-1"),
        }
        if deployment_id not in owners:
            return {"body": {"data": {"deployment": {}}}}
        project_id, service_id = owners[deployment_id]
        return {
            "body": {
                "data": {
                    "deployment": {
                        "id": deployment_id,
                        "projectId": project_id,
                        "serviceId": service_id,
                    }
                }
            }
        }

    def variable_names(self, project_id, environment_id, service_id):
        self.calls.append(("vars", project_id, environment_id, service_id))
        return {"names": ["PORT", "TOKEN"]}

    def redeploy(self, service_id, environment_id):
        self.calls.append(("redeploy", service_id, environment_id))
        return {"body": {"deployment": {"id": "d-10"}}}


def _run_factory(outputs):
    calls: list[list] = []

    def run(argv, timeout=10):
        calls.append(argv)
        for prefix, result in outputs:
            if argv[: len(prefix)] == prefix:
                return dict(result)
        return {"exit_code": 1, "stdout": "", "stderr": "no"}

    return run


def _ctx(**overrides):
    base: dict[str, Any] = {"railway": FakeRailway(), "projects": ["acme"]}
    base.update(overrides)
    return InfraContext(**base)


def test_railway_status_logs_vars_redeploy():
    client = InfraClient(_ctx())
    status = client.railway_status()
    assert status["projects"][0]["services"][0]["name"] == "web"
    logs = client.railway_logs("acme", "web")
    assert logs == {"deployment_id": "d-9", "lines": ["l1", "l2"]}
    assert client.railway_variable_names("acme", "web") == {"names": ["PORT", "TOKEN"]}
    redeployed = client.railway_redeploy("acme", "web", "d-9")
    assert redeployed["ok"] is True and redeployed["prior_deployment_id"] == "d-9"
    assert "not found" in client.railway_logs("acme", "nope")["error"]
    assert "not found" in client.railway_redeploy("acme", "nope", "d-9")["error"]
    bare = InfraClient(InfraContext(projects=["acme"]))
    assert "not_configured" in bare.railway_status()["error"]
    assert "not_configured" in bare.railway_logs("a", "b")["error"]
    assert "not_configured" in bare.railway_redeploy("a", "b", "d")["error"]
    empty = InfraClient(InfraContext(railway=FakeRailway()))
    assert "no Railway project" in empty.railway_status()["error"]


def test_railway_logs_binds_deployment_to_service():
    client = InfraClient(_ctx())
    latest = client.railway_logs("acme", "web", deployment_id="d-9")
    assert latest == {"deployment_id": "d-9", "lines": ["l1", "l2"]}
    older = client.railway_logs("acme", "web", deployment_id="d-8")
    assert older == {"deployment_id": "d-8", "lines": ["l1", "l2"]}
    assert (
        "another service"
        in client.railway_logs("acme", "web", deployment_id="d-7")["error"]
    )
    assert (
        "another project"
        in client.railway_logs("acme", "web", deployment_id="d-6")["error"]
    )
    assert (
        "not_found" in client.railway_logs("acme", "web", deployment_id="d-0")["error"]
    )
    assert (
        "not found" in client.railway_logs("acme", "nope", deployment_id="d-8")["error"]
    )

    class NoLookup(FakeRailway):
        # A client without the deployment lookup: getattr falls back to None.
        deployment = None  # type: ignore[assignment]

    stuck = InfraClient(_ctx(railway=NoLookup()))
    assert (
        stuck.railway_logs("acme", "web", deployment_id="d-9")["deployment_id"] == "d-9"
    )
    assert (
        "cannot verify"
        in stuck.railway_logs("acme", "web", deployment_id="d-8")["error"]
    )


def test_tailscale_parse_and_probes():
    doc = {
        "Self": {"HostName": "me", "Tags": ["t"], "TailscaleIPs": ["100.1"]},
        "Peer": {
            "x": {
                "HostName": "fw",
                "Online": True,
                "TailscaleIPs": ["100.2"],
                "Tags": [],
                "OS": "linux",
            }
        },
    }
    run = _run_factory(
        [
            (
                ["tailscale", "status", "--json"],
                {"exit_code": 0, "stdout": json.dumps(doc), "stderr": ""},
            ),
            (
                ["timeout", "3", "bash", "-c", "exec 3<>/dev/tcp/fw/80"],
                {"exit_code": 0, "stdout": "", "stderr": ""},
            ),
            (
                ["timeout", "3", "bash", "-c", "exec 3<>/dev/tcp/fw/81"],
                {"exit_code": 1, "stdout": "", "stderr": ""},
            ),
        ]
    )
    client = InfraClient(InfraContext(run=run, forwarders={"fw": [80, 81]}))
    status = client.tailscale_status()
    assert status["self"]["hostname"] == "me"
    assert status["forwarders"]["fw"] == {
        "present": True,
        "online": True,
        "ports": {"80": True, "81": False},
    }
    assert status["peers"]["fw"]["online"] is True
    down = InfraClient(InfraContext(run=_run_factory([])))
    assert "failed" in down.tailscale_status()["error"]
    bad = InfraClient(
        InfraContext(
            run=lambda argv, timeout=10: {
                "exit_code": 0,
                "stdout": "nope",
                "stderr": "",
            }
        )
    )
    assert "no JSON" in bad.tailscale_status()["error"]


def test_vps_units_db_health_and_approvals(tmp_path):
    run = _run_factory(
        [
            (
                [
                    "systemctl",
                    "show",
                    "web",
                    "--property=ActiveState,SubState,ExecMainStartTimestamp,NRestarts",
                ],
                {
                    "exit_code": 0,
                    "stdout": "ActiveState=active\nSubState=running\nExecMainStartTimestamp=Mon\nNRestarts=2\n",
                    "stderr": "",
                },
            ),
        ]
    )
    health = {
        "greptime": lambda: {"ok": True},
        "ragflow": lambda: {"ok": False, "error": "down"},
        "boom": lambda: 1 / 0,
    }
    client = InfraClient(_ctx(run=run, health=health))
    units = client.vps_units(["web"])
    assert units["units"]["web"] == {
        "active": "active",
        "sub": "running",
        "since": "Mon",
        "restarts": "2",
    }
    summary = client.db_health()
    assert summary["summary"] == {
        "greptime": "ok",
        "ragflow": "down",
        "boom": "division by zero",
    }
    assert (
        InfraClient(InfraContext()).db_health()["note"] == "no health checks configured"
    )

    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    register_infra_tools(registry, client)
    assert (
        "failed" in json.loads(registry.dispatch("infra_tailscale_status", {}))["error"]
    )
    assert json.loads(
        registry.dispatch(
            "infra_railway_redeploy",
            {"project": "acme", "service": "web", "prior_deployment_id": "d-9"},
        )
    ) == {"error": "approval required", "tool": "infra_railway_redeploy"}
    assert log.approve("infra_railway_redeploy", "ada").get("approved") is True
    assert (
        json.loads(
            registry.dispatch(
                "infra_railway_redeploy",
                {"project": "acme", "service": "web", "prior_deployment_id": "d-9"},
            )
        )["ok"]
        is True
    )

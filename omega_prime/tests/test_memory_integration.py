"""Production shared-memory composition using fake backends only."""

from __future__ import annotations

import json
from pathlib import Path

from omega_prime.commands import execute_command
from omega_prime.mcp_server import default_registry
from omega_prime.substrate.client import _check_base_url
from omega_prime.tools.approvals import ApprovalLog

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


class _UnauthorizedError(Exception):
    status = "unauthorized"


class _FakeSubstrate:
    def __init__(self, base_url: str, *, token=None, **kwargs):
        self.base_url = base_url
        self.writes: list[tuple] = []
        self.search_error: Exception | None = None
        self.write_response = {"status": "accepted"}

    def _resolve_base(self) -> tuple[str | None, str | None]:
        try:
            base = _check_base_url(self.base_url)
        except ValueError:
            return None, "configured substrate URL is unsafe"
        if base is None:
            return None, "substrate gateway is not configured"
        return base, None

    def memory_search(self, query, scope, limit, *, repo=None):
        if self.search_error is not None:
            raise self.search_error
        if "substrate integration" not in query:
            return []
        return [{"text": "substrate integration hit", "scope": scope, "repo": repo}]

    def memory_write(
        self,
        scope,
        kind,
        content,
        *,
        idempotency_key,
        expected_version=None,
        source_event=None,
    ):
        self.writes.append(
            (scope, kind, content, idempotency_key, expected_version, source_event)
        )
        return self.write_response


class _FakeHindsight:
    bank = "test-bank"

    def __init__(self, base_url: str, *, broker, transport, policy):
        self.base_url = base_url.rstrip("/")
        self.writes: list[tuple] = []
        self.recall_error: Exception | None = None

    def recall(self, query, *, limit=5):
        if self.recall_error is not None:
            raise self.recall_error
        return [{"text": "hindsight integration hit", "scope": "default", "repo": None}]

    def retain(self, items, *, context=None, document_id=None, tags=None):
        self.writes.append((items, context, document_id, tags))
        return {"ok": True, "count": len(items)}


def _doctor(registry, approval_log):
    approval_log.approve("lead_doctor", "ada")
    return json.loads(registry.dispatch("lead_doctor", {}))


def test_unset_remotes_stay_local_and_doctor_does_not_lookup_or_write_secrets(
    tmp_path, monkeypatch
):
    import omega_prime.mcp_server as server

    built = []
    monkeypatch.setattr(
        server,
        "SubstrateClient",
        lambda *args, **kwargs: _FakeSubstrate(*args, **kwargs),
    )
    import omega_prime.credentials.broker as broker_module
    import omega_prime.memory.hindsight_service as hindsight_module

    key_lookups = []
    original_secret_value = broker_module.secret_value

    def track_secret_lookup(env, name):
        if name in {"HINDSIGHT_API_KEY", "HINDSIGHT_API_TOKEN"}:
            key_lookups.append(name)
        return original_secret_value(env, name)

    monkeypatch.setattr(broker_module, "secret_value", track_secret_lookup)

    def forbidden_hindsight(*args, **kwargs):
        built.append("hindsight")
        raise AssertionError("unset Hindsight must not be constructed")

    monkeypatch.setattr(hindsight_module, "HindsightService", forbidden_hindsight)
    monkeypatch.setattr(
        server, "CredentialBroker", lambda *args, **kwargs: built.append("broker")
    )
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    result = _doctor(registry, approval_log)
    source_rows = result["checks"]["memory_backends"]["sources"]
    assert result["checks"]["memory_backends"]["selected_backend"] == "local"
    assert source_rows["local"]["state"] == "empty"
    assert source_rows["local"]["attempted"] is True
    assert source_rows["substrate"] == {
        "state": "not_configured",
        "attempted": False,
        "error": "substrate gateway is not configured",
    }
    assert source_rows["hindsight"] == {"state": "not_configured", "attempted": False}
    assert built == []
    assert key_lookups == []


def test_explicit_substrate_is_shared_for_growth_retain_lead_recall_and_doctor(
    tmp_path, monkeypatch
):
    import omega_prime.mcp_server as server

    clients = []

    def make_substrate(url, **kwargs):
        client = _FakeSubstrate(url, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(server, "SubstrateClient", make_substrate)
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "SUBSTRATE_URL": "local",
        },
    )
    substrate = clients[0]
    retained = json.loads(
        registry.dispatch(
            "memory",
            {"action": "add", "target": "memory", "content": "growth routed memory"},
        )
    )
    assert retained["ok"] is True
    assert len(substrate.writes) == 1
    approval_log.approve("lead_memory_retain", "ada")
    lead_retained = json.loads(
        registry.dispatch(
            "lead_memory_retain",
            {"content": "direct lead retained memory", "source": "integration test"},
        )
    )
    assert lead_retained["ok"] is True
    assert len(substrate.writes) == 2
    recalled = json.loads(
        registry.dispatch("lead_memory_recall", {"query": "substrate integration"})
    )
    assert recalled["backends"]["substrate"] == {
        "state": "answered",
        "attempted": True,
    }
    assert recalled["results"][0]["content"] == "substrate integration hit"

    doctor = _doctor(registry, approval_log)
    assert doctor["checks"]["memory_backends"]["sources"]["substrate"] == {
        "state": "empty",
        "attempted": True,
    }
    assert doctor["checks"]["memory_backends"]["configuration"]["substrate"] == "empty"
    assert len(substrate.writes) == 2


def test_explicit_hindsight_routes_growth_retain_and_lead_recall_with_doctor(
    tmp_path, monkeypatch
):
    import omega_prime.memory.hindsight_service as hindsight_module

    clients = []

    def make_hindsight(url, **kwargs):
        client = _FakeHindsight(url, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(hindsight_module, "HindsightService", make_hindsight)
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "HINDSIGHT_URL": "https://hindsight.test",
            "OMEGA_PRIME_MEMORY_BACKEND": "hindsight",
        },
    )
    hindsight = clients[0]
    retained = json.loads(
        registry.dispatch(
            "memory",
            {"action": "add", "target": "memory", "content": "growth hindsight memory"},
        )
    )
    assert retained["ok"] is True
    assert len(hindsight.writes) == 1
    recalled = json.loads(
        registry.dispatch("lead_memory_recall", {"query": "hindsight integration"})
    )
    assert recalled["backends"]["hindsight"] == {
        "state": "answered",
        "attempted": True,
    }
    assert recalled["results"][0]["content"] == "hindsight integration hit"
    doctor = _doctor(registry, approval_log)
    assert doctor["checks"]["memory_backends"]["selected_backend"] == "hindsight"
    assert doctor["checks"]["memory_backends"]["sources"]["hindsight"] == {
        "state": "answered",
        "attempted": True,
    }
    assert doctor["checks"]["memory_backends"]["configuration"]["hindsight"] == "empty"
    assert len(hindsight.writes) == 1


def test_selected_hindsight_failure_makes_memory_health_red(tmp_path, monkeypatch):
    import omega_prime.memory.hindsight_service as hindsight_module

    def make_hindsight(url, **kwargs):
        client = _FakeHindsight(url, **kwargs)
        client.recall_error = _UnauthorizedError("unauthorized")
        return client

    monkeypatch.setattr(hindsight_module, "HindsightService", make_hindsight)
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "HINDSIGHT_URL": "https://hindsight.test",
            "OMEGA_PRIME_MEMORY_BACKEND": "hindsight",
        },
    )

    memory_backends = _doctor(registry, approval_log)["checks"]["memory_backends"]

    assert memory_backends["selected_backend"] == "hindsight"
    assert memory_backends["green"] is False
    assert memory_backends["sources"]["local"]["state"] in {"answered", "empty"}
    assert memory_backends["sources"]["hindsight"] == {
        "state": "unauthorized",
        "attempted": True,
        "error": "unauthorized",
    }


def test_unselected_hindsight_failure_does_not_make_local_memory_health_red(
    tmp_path, monkeypatch
):
    import omega_prime.memory.hindsight_service as hindsight_module

    def make_hindsight(url, **kwargs):
        client = _FakeHindsight(url, **kwargs)
        client.recall_error = _UnauthorizedError("unauthorized")
        return client

    monkeypatch.setattr(hindsight_module, "HindsightService", make_hindsight)
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "HINDSIGHT_URL": "https://hindsight.test",
        },
    )

    memory_backends = _doctor(registry, approval_log)["checks"]["memory_backends"]

    assert memory_backends["selected_backend"] == "local"
    assert memory_backends["green"] is True
    assert memory_backends["sources"]["local"]["state"] in {"answered", "empty"}
    assert memory_backends["sources"]["hindsight"]["state"] == "unauthorized"


def test_doctor_preserves_authorized_source_failure_state(tmp_path, monkeypatch):
    import omega_prime.mcp_server as server

    clients = []

    def make_substrate(url, **kwargs):
        client = _FakeSubstrate(url, **kwargs)
        client.search_error = _UnauthorizedError("unauthorized")
        clients.append(client)
        return client

    monkeypatch.setattr(server, "SubstrateClient", make_substrate)
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "SUBSTRATE_URL": "local",
        },
    )
    doctor = _doctor(registry, approval_log)
    substrate = doctor["checks"]["memory_backends"]["sources"]["substrate"]
    assert substrate["state"] == "unauthorized"
    assert substrate["attempted"] is True
    assert clients[0].writes == []


def test_invalid_substrate_binding_is_visible_without_remote_client(
    tmp_path, monkeypatch
):
    import omega_prime.integrations.gateways as gateways

    monkeypatch.setattr(
        gateways,
        "gateway_block",
        lambda name, env: (
            {"error": "invalid: substrate binding rejected"}
            if name == "substrate"
            else None
        ),
    )
    monkeypatch.setattr(gateways, "ready_gateway_url", lambda name, env: "")
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "OMEGA_PRIME_MEMORY_BACKEND": "substrate",
        },
    )

    doctor = _doctor(registry, approval_log)
    substrate = doctor["checks"]["memory_backends"]["sources"]["substrate"]
    assert substrate["state"] == "unreachable"
    assert substrate["attempted"] is False

    approval_log.approve("lead_memory_retain", "ada")
    retained = json.loads(
        registry.dispatch(
            "lead_memory_retain",
            {"content": "offline retained", "source": "integration test"},
        )
    )
    assert retained["ok"] is False
    assert retained["local_saved"] is True
    assert retained["backends"]["substrate"]["state"] == "unreachable"


def test_real_slash_retain_reports_remote_failure_and_recall_keeps_local(
    tmp_path, monkeypatch
):
    import omega_prime.mcp_server as server

    clients = []

    def make_substrate(url, **kwargs):
        client = _FakeSubstrate(url, **kwargs)
        client.write_response = {"status": "conflict"}
        clients.append(client)
        return client

    monkeypatch.setattr(server, "SubstrateClient", make_substrate)
    approval_log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=approval_log,
        env={
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "SUBSTRATE_URL": "https://substrate.test/mcp",
        },
    )
    approval_log.approve("lead_memory_retain", "ada")
    content = "command integration fact"
    retained = execute_command(
        f"/omega-retain --source integration-test -- {content}",
        registry,
        {
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "SUBSTRATE_URL": "https://substrate.test/mcp",
        },
    )
    assert retained["ok"] is False
    assert retained["result"]["local_saved"] is True
    assert retained["result"]["backends"]["substrate"]["state"] == "unreachable"
    assert clients[0].writes[0][3] == retained["result"]["idempotency_key"]

    recalled = execute_command(
        "/omega-recall command integration fact",
        registry,
        {
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
            "SUBSTRATE_URL": "https://substrate.test/mcp",
        },
    )
    assert recalled["ok"] is True
    assert recalled["result"]["backends"]["local"]["state"] == "answered"
    assert recalled["result"]["backends"]["substrate"]["state"] == "empty"
    assert recalled["result"]["backends"]["hindsight"]["state"] == "not_configured"
    entry = next(
        row for row in recalled["result"]["results"] if row["content"] == content
    )
    assert any(source["backend"] == "local" for source in entry["sources"])

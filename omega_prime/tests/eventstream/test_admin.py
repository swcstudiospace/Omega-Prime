"""Pulsar private admin REST contract tests."""

from __future__ import annotations

import io
import json
import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from omega_prime.eventstream.admin import AdminError, PulsarAdminPort
from omega_prime.eventstream.config import EventStreamConfig


class RestRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.resources: dict[str, object] = {}
        self.mutations: list[tuple[str, str, dict[str, object] | None]] = []
        self.archives: list[bytes] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = None
        content_type = request.headers.get("content-type", "")
        if request.content:
            if content_type.startswith("application/json"):
                body = json.loads(request.content)
            elif content_type.startswith("multipart/form-data"):
                boundary = content_type.split("boundary=", 1)[1].encode()
                parts = request.content.split(b"--" + boundary)
                config_part = next(
                    part for part in parts if b'name="functionConfig"' in part
                )
                raw_config = config_part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
                body = json.loads(raw_config)
                archive_part = next(part for part in parts if b'name="data"' in part)
                self.archives.append(
                    archive_part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
                )
        self.calls.append((request.method, request.url.path, body))
        if request.method == "GET":
            if request.url.path not in self.resources:
                return httpx.Response(404)
            return httpx.Response(200, json=self.resources[request.url.path])
        self.mutations.append((request.method, request.url.path, body))
        self.resources[request.url.path] = body if body is not None else {}
        # Mirror Pulsar 5.0: bare topic PUT creates a non-partitioned topic whose
        # existence is probed via GET .../partitions -> {"partitions": 0}.
        path = request.url.path
        if (
            request.method == "PUT"
            and path.startswith("/admin/v2/persistent/")
            and not path.endswith("/partitions")
        ):
            self.resources[f"{path}/partitions"] = {"partitions": 0, "deleted": False}
        if (
            request.method == "PUT"
            and path.endswith("/partitions")
            and isinstance(body, int)
        ):
            self.resources[path] = {"partitions": body, "deleted": False}
        # Pulsar 5.0: per-role permission GET is 405; clients read the collection.
        # Mirror POSTed role grants into the parent collection map.
        if request.method == "POST" and "/permissions/" in path:
            parent, _, role = path.rpartition("/")
            grants = self.resources.get(parent)
            if not isinstance(grants, dict):
                grants = {}
            updated = dict(grants)
            updated[role] = body
            self.resources[parent] = updated
        # Pulsar GET schema returns {type, data, ...} while POST sends {type, schema}.
        if (
            request.method == "POST"
            and path.endswith("/schema")
            and isinstance(body, dict)
        ):
            self.resources[path] = {
                "version": 0,
                "type": body.get("type"),
                "data": body.get("schema"),
                "properties": body.get("properties") or {},
            }
        return httpx.Response(204)


async def make_admin(recorder: RestRecorder) -> PulsarAdminPort:
    admin = PulsarAdminPort(attempts=1)
    admin._client = httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(recorder.handle)
    )
    return admin


@pytest.mark.asyncio
async def test_admin_secret_file_is_revalidated_on_each_request(tmp_path: Path) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("private-token", encoding="utf-8")
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    config = EventStreamConfig(token_file=token_path)
    recorder = RestRecorder()
    admin = await make_admin(recorder)
    admin._config = config
    await admin._request("GET", "/private", allow_not_found=True)
    assert admin._client is not None
    assert "Authorization" not in admin._client.headers
    token_path.chmod(0o644)
    with pytest.raises(Exception, match="0600"):
        await admin._request("GET", "/private")
    await admin.close()


@pytest.mark.asyncio
async def test_remote_admin_requires_ca_and_token() -> None:
    with pytest.raises(Exception, match="TLS CA"):
        EventStreamConfig(admin_url="https://broker.example")


@pytest.mark.asyncio
async def test_topic_paths_reject_non_omega() -> None:
    from omega_prime.eventstream.admin import _url_path

    assert (
        _url_path("persistent://omega/sdlc/pr") == "/admin/v2/persistent/omega/sdlc/pr"
    )
    with pytest.raises(ValueError):
        _url_path("persistent://other/sdlc/pr")
    with pytest.raises(ValueError):
        _url_path("persistent://omega/sdlc")


@pytest.mark.asyncio
async def test_reconcile_dry_run_apply_and_second_apply_are_idempotent() -> None:
    recorder = RestRecorder()
    admin = await make_admin(recorder)
    admin._config = EventStreamConfig()
    dry = await admin.reconcile(dry_run=True)
    assert dry.actions
    assert recorder.mutations == []
    assert recorder.calls and all(method == "GET" for method, _, _ in recorder.calls)
    assert any(
        path.endswith("/sdlc/pr/partitions") and method == "PUT"
        for method, path, _ in dry.actions
    )
    await admin.reconcile()
    assert recorder.mutations
    assert len(recorder.mutations) == len(dry.actions)
    assert all(method != "DELETE" for method, _, _ in recorder.calls)
    applied = len(recorder.mutations)
    second = await admin.reconcile()
    assert len(recorder.mutations) == applied
    assert second.actions == ()
    assert second.changes == ()
    await admin.close()


@pytest.mark.asyncio
async def test_reconcile_uses_pulsar_endpoints_and_exact_policy_and_grant_bodies() -> (
    None
):
    recorder = RestRecorder()
    admin = await make_admin(recorder)
    await admin.reconcile()
    namespace_paths = {
        path.rsplit("/", 1)[-1]
        for method, path, _ in recorder.mutations
        if method == "PUT"
        and path.startswith("/admin/v2/namespaces/omega/")
        and path.count("/") == 5
    }
    assert namespace_paths == {
        "sdlc",
        "auth",
        "job",
        "sandbox",
        "task",
        "memory",
        "control",
        "audit",
        "system",
    }
    partition_paths = {
        path
        for method, path, _ in recorder.mutations
        if method == "PUT" and path.endswith("/partitions")
    }
    assert partition_paths == {
        "/admin/v2/persistent/omega/sdlc/pr/partitions",
        "/admin/v2/persistent/omega/task/state/partitions",
    }
    assert (
        "PUT",
        "/admin/v2/persistent/omega/sdlc/pr/partitions",
        4,
    ) in recorder.mutations
    retention_minutes = {
        "sdlc": 10080,
        "auth": 43200,
        "job": 10080,
        "sandbox": 2880,
        "task": 2880,
        "memory": 43200,
        "control": 43200,
        "audit": 576000,
        "system": 43200,
    }
    retention_bodies = {
        path.split("/")[5]: body
        for method, path, body in recorder.mutations
        if method == "POST" and path.endswith("/retention")
    }
    assert retention_bodies == {
        namespace: {
            "retentionTimeInMinutes": minutes,
            "retentionSizeInMB": 5120 if namespace == "sdlc" else -1,
        }
        for namespace, minutes in retention_minutes.items()
    }
    compatibility = {
        path.split("/")[5]: body
        for method, path, body in recorder.mutations
        if method == "PUT" and path.endswith("/schemaCompatibilityStrategy")
    }
    assert compatibility == {
        "sdlc": "BACKWARD_TRANSITIVE",
        "auth": "BACKWARD_TRANSITIVE",
        "job": "BACKWARD_TRANSITIVE",
        "sandbox": "BACKWARD_TRANSITIVE",
        "task": "BACKWARD_TRANSITIVE",
        "memory": "BACKWARD_TRANSITIVE",
        "control": "BACKWARD_TRANSITIVE",
        "audit": "FULL_TRANSITIVE",
    }
    regular_topics = {
        "/".join(path.removesuffix("/partitions").split("/")[5:7])
        for method, path, _ in recorder.mutations
        if method == "PUT" and path.startswith("/admin/v2/persistent/omega/")
    }
    assert regular_topics == {
        "sdlc/pr",
        "sdlc/review",
        "sdlc/ci",
        "sdlc/pr-state",
        "auth/session",
        "job/lifecycle",
        "sandbox/lifecycle",
        "task/state",
        "task/heartbeat",
        "memory/hindsight",
        "control/signal",
        "control/action",
        "control/hitl",
        "control/deadline",
        "audit/log",
        "system/retry.gate",
        "system/dlq.gate",
        "system/retry.blocked",
        "system/dlq.blocked",
        "system/retry.review",
        "system/dlq.review",
        "system/retry.escalator",
        "system/dlq.escalator",
        "system/retry.merge-executor",
        "system/dlq.merge-executor",
        "system/retry.audit-projector",
        "system/dlq.audit-projector",
    }
    grants = {
        (path, tuple(body))
        for method, path, body in recorder.mutations
        if method == "POST" and "/permissions/" in path and body is not None
    }
    assert (
        "/admin/v2/namespaces/omega/sdlc/permissions/omega-ingress",
        ("produce",),
    ) in grants
    assert (
        "/admin/v2/persistent/omega/sdlc/ci/permissions/omega-vps-leaf",
        ("produce",),
    ) in grants
    assert {path.rsplit("/", 1)[-1] for path, _ in grants} == {
        "omega-box",
        "omega-audit",
        "omega-vps-leaf",
        "omega-ingress",
        "omega-dashboard",
        "omega-functions",
    }
    assert not any(
        path == "/admin/v2/namespaces/omega/sdlc/permissions/omega-vps-leaf"
        for path, _ in grants
    )
    assert not any(
        path.startswith("/admin/v2/schemas/omega/system/")
        for _, path, _ in recorder.mutations
    )
    assert (
        "POST",
        "/admin/v2/namespaces/omega/sdlc/deduplication",
        True,  # Pulsar 5.0 contract: bare JSON boolean, not an object.
    ) in recorder.mutations
    assert all(method != "DELETE" for method, _, _ in recorder.calls)
    await admin.close()


@pytest.mark.asyncio
async def test_reconcile_rejects_incompatible_policy_drift_before_writing() -> None:
    recorder = RestRecorder()
    recorder.resources["/admin/v2/namespaces/omega/sdlc/retention"] = {
        "retentionTimeInMinutes": 1,
        "retentionSizeInMB": 5,
    }
    admin = await make_admin(recorder)
    with pytest.raises(AdminError, match="drift"):
        await admin.reconcile()
    assert recorder.mutations == []
    await admin.close()


@pytest.mark.asyncio
async def test_reconcile_preserves_exact_grants_and_native_schema_payload() -> None:
    recorder = RestRecorder()
    admin = await make_admin(recorder)
    await admin.reconcile()
    grants = [
        (method, path, body)
        for method, path, body in recorder.mutations
        if "/permissions/" in path
    ]
    assert any(
        path.endswith("/permissions/omega-vps-leaf")
        and "/sdlc/ci/" in path
        and body == ["produce"]
        for _, path, body in grants
    )
    assert {path.rsplit("/", 1)[-1] for _, path, _ in grants} == {
        "omega-box",
        "omega-audit",
        "omega-vps-leaf",
        "omega-ingress",
        "omega-dashboard",
        "omega-functions",
    }
    schema = next(
        body
        for _, path, body in recorder.mutations
        if path == "/admin/v2/schemas/omega/sdlc/pr/schema"
    )
    assert (
        isinstance(schema, dict)
        and schema["type"] == "JSON"
        and isinstance(schema["schema"], str)
    )
    assert "$schema" not in schema["schema"]
    functions = [
        (path, body)
        for _, path, body in recorder.mutations
        if "/functions/omega/" in path
    ]
    assert len(functions) == 3
    assert all(
        body is not None
        and body["processingGuarantees"] in {"ATLEAST_ONCE", "EFFECTIVELY_ONCE"}
        for _, body in functions
    )
    expected_classes = {
        "greptile-score": "omega_prime.eventstream.functions.greptile_score.GreptileScoreFunction",
        "ci-rollup": "omega_prime.eventstream.functions.ci_rollup.CiRollupFunction",
        "pr-state-projector": "omega_prime.eventstream.functions.pr_state_projector.PrStateProjectorFunction",
    }
    for path, body in functions:
        name = path.rsplit("/", 1)[-1]
        assert body is not None
        assert body["className"] == expected_classes[name]
    with zipfile.ZipFile(io.BytesIO(recorder.archives[0])) as archive:
        contents = archive.namelist()
        assert "requirements.txt" in contents
        assert any(
            path.startswith("src/omega_prime/eventstream/functions/")
            for path in contents
        )
        assert not any(path.startswith("greptile-score/") for path in contents)
    assert any(
        path.endswith("/sdlc/retention")
        and body == {"retentionTimeInMinutes": 10080, "retentionSizeInMB": 5120}
        for _, path, body in recorder.mutations
    )


@pytest.mark.asyncio
async def test_authenticated_reconcile_refuses_unverified_function_identity_before_writes(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "admin-token"
    token_path.write_text("admin-token", encoding="utf-8")
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    recorder = RestRecorder()
    admin = await make_admin(recorder)
    admin._config = EventStreamConfig(token_file=token_path)
    dry = await admin.reconcile(dry_run=True)
    assert dry.dry_run and dry.actions
    assert recorder.mutations == []
    with pytest.raises(AdminError, match="verified least-privilege"):
        await admin.reconcile()
    assert recorder.mutations == []
    await admin.close()


@pytest.mark.asyncio
async def test_reconcile_rejects_broadened_leaf_namespace_acl_before_writing() -> None:
    recorder = RestRecorder()
    recorder.resources["/admin/v2/namespaces/omega/sdlc/permissions/omega-vps-leaf"] = [
        "produce"
    ]
    admin = await make_admin(recorder)
    with pytest.raises(AdminError, match="drift"):
        await admin.reconcile()
    assert recorder.mutations == []
    await admin.close()


@pytest.mark.asyncio
async def test_reconcile_fails_closed_on_drift_and_sanitizes_transport_errors() -> None:
    recorder = RestRecorder()
    recorder.resources["/admin/v2/tenants/omega"] = {"adminRoles": ["wrong"]}
    admin = await make_admin(recorder)
    with pytest.raises(AdminError, match="drift"):
        await admin.reconcile()
    await admin.close()
    failed = PulsarAdminPort(attempts=1)
    failed._client = httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, text="denied token=secret")
        ),
    )
    with pytest.raises(AdminError, match="request failed") as error:
        await failed._request("GET", "/private")
    assert "secret" not in str(error.value)
    await failed.close()


@pytest.mark.asyncio
async def test_admin_configure_loopback_gate_and_ensure_topic() -> None:
    with pytest.raises(ValueError, match="bounds"):
        PulsarAdminPort(attempts=0)
    with pytest.raises(ValueError, match="bounds"):
        PulsarAdminPort(timeout=0)

    bare = PulsarAdminPort(attempts=1)
    with pytest.raises(AdminError, match="not configured"):
        await bare.reconcile()

    gated = PulsarAdminPort(attempts=1)

    class _Cfg:
        admin_url = "https://pulsar.internal:8443"
        broker_url = "pulsar://pulsar.internal:6650"
        tls_ca_file = None
        token_file = None

        def read_secret(self, name: str) -> str:
            raise AssertionError(name)

    with pytest.raises(AdminError, match="loopback"):
        await gated.configure(_Cfg())  # type: ignore[arg-type]

    recorder = RestRecorder()
    admin = await make_admin(recorder)
    recorder.resources["/admin/v2/namespaces/omega/sdlc"] = {}
    # Pulsar 5.0 contract: the cluster brokers endpoint returns a list of ids.
    recorder.resources["/admin/v2/brokers/standalone"] = ["127.0.0.1:8080"]
    await admin.ensure_topic("persistent://omega/sdlc/pr", partitions=4)
    await admin.ensure_topic("persistent://omega/sdlc/review", partitions=1)
    with pytest.raises(ValueError, match="negative"):
        await admin.ensure_topic("persistent://omega/sdlc/pr", partitions=-1)
    assert (
        "PUT",
        "/admin/v2/persistent/omega/sdlc/pr/partitions",
        4,
    ) in recorder.mutations
    assert ("PUT", "/admin/v2/persistent/omega/sdlc/review", None) in recorder.mutations
    assert await admin.status() == {
        "cluster": "standalone",
        "active_brokers": ["127.0.0.1:8080"],
    }
    recorder.resources["/admin/v2/brokers/standalone"] = {"broker": "standalone"}
    with pytest.raises(AdminError, match="malformed"):
        await admin.status()
    await admin.close()


def test_admin_unit_helpers_for_retention_and_schema() -> None:
    from omega_prime.eventstream.admin import (
        AdminError,
        _duration_ms,
        _schema_equivalent,
        _size_mb,
    )

    assert _duration_ms("30d") == 30 * 86_400_000
    assert _duration_ms("2h") == 2 * 3_600_000
    assert _size_mb("1GB") == 1024
    assert _size_mb("5120MB") == 5120
    with pytest.raises(AdminError, match="retention duration"):
        _duration_ms("30x")
    with pytest.raises(AdminError, match="retention size"):
        _size_mb("12KB")
    expected = {"type": "JSON", "schema": '{"type":"record","name":"X","fields":[]}'}
    assert _schema_equivalent(
        {"type": "JSON", "data": '{\n  "type": "record", "name": "X", "fields": []\n}'},
        expected,
    )
    assert not _schema_equivalent({"type": "AVRO"}, expected)
    assert not _schema_equivalent("nope", expected)


@pytest.mark.asyncio
async def test_function_archives_import_without_repository_on_sys_path(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys
    import textwrap

    recorder = RestRecorder()
    admin = await make_admin(recorder)
    await admin.reconcile()
    await admin.close()
    assert len(recorder.archives) == 3
    classes = {
        "greptile_score": "GreptileScoreFunction",
        "ci_rollup": "CiRollupFunction",
        "pr_state_projector": "PrStateProjectorFunction",
    }
    seen: set[str] = set()
    for index, raw in enumerate(recorder.archives):
        target = tmp_path / f"archive-{index}"
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            archive.extractall(target)
            names = archive.namelist()
        module = next(
            Path(name).stem
            for name in names
            if name.startswith("src/omega_prime/eventstream/functions/")
            and Path(name).stem in classes
        )
        seen.add(module)
        src = target / "src"
        script = textwrap.dedent(
            f"""
            import importlib, sys
            from pathlib import Path
            root = Path({str(src)!r}).resolve()
            sys.path[:] = [str(root)] + [
                entry for entry in sys.path
                if entry and Path(entry).resolve() != Path({str(Path.cwd())!r}).resolve()
            ]
            loaded = importlib.import_module(
                "omega_prime.eventstream.functions.{module}"
            )
            assert hasattr(loaded, {classes[module]!r})
            for name, mod in list(sys.modules.items()):
                if name == "omega_prime" or name.startswith("omega_prime."):
                    locations = [getattr(mod, "__file__", None)] + list(
                        getattr(mod, "__path__", []) or []
                    )
                    for location in filter(None, locations):
                        assert Path(location).resolve().is_relative_to(root), (
                            name, location
                        )
            """
        )
        result = subprocess.run(
            [sys.executable, "-P", "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1"},
        )
        assert result.returncode == 0, result.stderr[-2000:]
    assert seen == set(classes)


def _inherit_namespace_grants(recorder: RestRecorder) -> None:
    """Mirror Pulsar 5.0: topic permission listings include namespace grants."""
    namespace_grants = {
        path.split("/")[5]: grants
        for path, grants in recorder.resources.items()
        if path.startswith("/admin/v2/namespaces/omega/")
        and path.endswith("/permissions")
        and isinstance(grants, dict)
    }
    for path in list(recorder.resources):
        if path.startswith("/admin/v2/persistent/omega/") and path.endswith(
            "/partitions"
        ):
            topic = path[: -len("/partitions")]
            namespace = topic.split("/")[5]
            current = recorder.resources.get(f"{topic}/permissions")
            merged = dict(namespace_grants.get(namespace, {}))
            if isinstance(current, dict):
                merged.update(current)
            if merged:
                recorder.resources[f"{topic}/permissions"] = merged


@pytest.mark.asyncio
async def test_reconcile_treats_inherited_namespace_grants_as_declared() -> None:
    recorder = RestRecorder()
    original = recorder.handle

    async def handle(request: httpx.Request) -> httpx.Response:
        # Pulsar 5.0 answers GET on a per-role permissions path with 405.
        parent, _, _role = request.url.path.rpartition("/")
        if request.method == "GET" and parent.endswith("/permissions"):
            return httpx.Response(405)
        return await original(request)

    recorder.handle = handle  # type: ignore[method-assign]
    admin = await make_admin(recorder)
    await admin.reconcile(functions=False)
    _inherit_namespace_grants(recorder)
    second = await admin.reconcile(functions=False)
    assert second.actions == ()
    # An operation beyond the declared topic + inherited namespace grant is drift.
    topic_grants = recorder.resources["/admin/v2/persistent/omega/sdlc/pr/permissions"]
    assert isinstance(topic_grants, dict)
    topic_grants["omega-dashboard"] = ["consume", "produce"]
    with pytest.raises(AdminError, match="drift"):
        await admin.reconcile(functions=False)
    await admin.close()


@pytest.mark.asyncio
async def test_reconcile_functions_false_skips_function_endpoints() -> None:
    recorder = RestRecorder()
    admin = await make_admin(recorder)
    result = await admin.reconcile(functions=False)
    assert result.actions
    assert not any("/admin/v3/functions/" in path for _, path, _ in recorder.calls)
    await admin.close()


@pytest.mark.asyncio
async def test_reconcile_reports_disabled_functions_worker() -> None:
    recorder = RestRecorder()
    original = recorder.handle

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/admin/v3/functions/"):
            return httpx.Response(
                409, json={"reason": "Pulsar Function Worker is not enabled"}
            )
        return await original(request)

    recorder.handle = handle  # type: ignore[method-assign]
    admin = await make_admin(recorder)
    with pytest.raises(AdminError, match="Functions worker is not enabled"):
        await admin.reconcile(dry_run=True)
    await admin.close()

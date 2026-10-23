"""Private Pulsar REST administration and fail-closed topology reconciliation."""

from __future__ import annotations

import ast
import hashlib
import io
import ipaddress
import json
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit

import httpx
import yaml

from omega_prime.credentials.redact import redact_value
from omega_prime.eventstream.backoff import create_with_backoff
from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.ports import AdminPort

_TOPOLOGY = Path(__file__).with_name("topology.yaml")


def function_module_sources(package_root: Path, module: str) -> tuple[Path, ...]:
    """Return every ``omega_prime`` source file a Function module imports.

    The deployed Function archive must be importable without the repository on
    ``sys.path``, so its Python sources are derived from the module's static
    import closure (including function-local imports and each parent package's
    ``__init__.py``) instead of a hand-maintained list that can drift when a
    Function gains a new shared import.
    """
    root = package_root.resolve()

    def resolve(dotted: str) -> list[Path]:
        parts = dotted.split(".")
        if not parts or parts[0] != "omega_prime":
            return []
        found: list[Path] = []
        for depth in range(1, len(parts) + 1):
            package_init = root.joinpath(*parts[1:depth], "__init__.py")
            if package_init.is_file():
                found.append(package_init)
        leaf = root.joinpath(*parts[1:]).with_suffix(".py") if len(parts) > 1 else None
        if leaf is not None and leaf.is_file():
            found.append(leaf)
        return found

    def dotted_name(path: Path) -> str:
        relative = path.relative_to(root).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(["omega_prime", *parts])

    start = root / "eventstream" / "functions" / f"{module}.py"
    if not start.is_file():
        raise AdminError(f"Function module {module!r} is missing")
    pending = [start, *resolve(dotted_name(start))]
    seen: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        current = dotted_name(source)
        current_package = (
            current if source.name == "__init__.py" else current.rsplit(".", 1)[0]
        )
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    anchor = current_package.split(".")
                    anchor = anchor[: len(anchor) - (node.level - 1)]
                    base = ".".join([*anchor, *([base] if base else [])])
                targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            for target in targets:
                pending.extend(resolve(target))
    return tuple(sorted(seen))


class AdminError(RuntimeError):
    """Sanitized REST or unsupported-drift failure."""


class ReconcileAction(NamedTuple):
    method: str
    path: str
    description: str


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    dry_run: bool
    actions: tuple[ReconcileAction, ...]
    changes: tuple[str, ...]


def _load_topology() -> dict[str, Any]:
    with _TOPOLOGY.open(encoding="utf-8") as source:
        value = yaml.safe_load(source)
    if not isinstance(value, dict):
        raise AdminError("eventstream topology is malformed")
    return value


def _url_path(topic: str) -> str:
    parts = topic.removeprefix("persistent://").split("/")
    if (
        not topic.startswith("persistent://omega/")
        or len(parts) != 3
        or any(not part for part in parts)
    ):
        raise ValueError("topic must belong to tenant omega")
    tenant, namespace, name = parts
    return f"/admin/v2/persistent/{quote(tenant)}/{quote(namespace)}/{quote(name, safe='.')}"


class PulsarAdminPort(AdminPort):
    """Typed asynchronous admin client using Pulsar's private v2 REST API."""

    def __init__(self, *, attempts: int = 3, timeout: float = 5.0) -> None:
        if not 1 <= attempts <= 5 or timeout <= 0:
            raise ValueError("admin retry and timeout bounds are invalid")
        self._attempts = attempts
        self._timeout = timeout
        self._config: EventStreamConfig | None = None
        self._client: httpx.AsyncClient | None = None

    async def configure(self, config: EventStreamConfig) -> None:
        await self.close()
        self._config = config
        verify: bool | str = str(config.tls_ca_file) if config.tls_ca_file else True
        hostname = urlsplit(config.admin_url).hostname
        try:
            loopback = hostname == "localhost" or (
                hostname is not None and ipaddress.ip_address(hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if config.token_file is None and not loopback:
            raise AdminError("no-auth admin access is restricted to loopback")
        self._client = httpx.AsyncClient(
            base_url=config.admin_url, verify=verify, timeout=self._timeout
        )

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: object = None,
        form_data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        allow_not_found: bool = False,
        allow_method_not_allowed: bool = False,
    ) -> httpx.Response:
        client = self._client
        if client is None:
            raise AdminError("admin port is not configured")

        async def request_once() -> httpx.Response:
            headers = None
            if self._config is not None and self._config.token_file is not None:
                headers = {
                    "Authorization": f"Bearer {self._config.read_secret('token')}"
                }
            response = await client.request(
                method,
                path,
                json=json_body,
                data=form_data,
                files=files,
                headers=headers,
            )
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                raise httpx.HTTPStatusError(
                    "transient Pulsar admin response",
                    request=response.request,
                    response=response,
                )
            if allow_not_found and response.status_code == 404:
                return response
            if allow_method_not_allowed and response.status_code == 405:
                return response
            response.raise_for_status()
            return response

        try:
            return await create_with_backoff(
                request_once,
                attempts=self._attempts,
                retryable=lambda exc: (
                    isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
                    or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code in {408, 429, 500, 502, 503, 504}
                    )
                ),
            )
        except Exception as exc:
            raise AdminError(
                f"Pulsar admin request failed: {redact_value(str(exc))}"
            ) from None

    async def ensure_topic(self, topic: str, *, partitions: int = 1) -> None:
        if partitions < 0:
            raise ValueError("partitions cannot be negative")
        path = _url_path(topic)
        namespace = topic.removeprefix("persistent://").split("/")[1]
        await self._request("GET", f"/admin/v2/namespaces/omega/{quote(namespace)}")
        await self._request(
            "PUT",
            f"{path}/partitions" if partitions > 1 else path,
            json_body=partitions if partitions > 1 else None,
        )

    async def reconcile(self, *, dry_run: bool = False) -> ReconcileResult:
        if self._client is None:
            raise AdminError("admin port is not configured")
        topology = _load_topology()
        actions: list[ReconcileAction] = []
        changes: list[str] = []
        pending: list[tuple[str, str, dict[str, Any]]] = []

        async def reconcile_resource(
            method: str,
            path: str,
            body: object,
            description: str,
            *,
            equivalent: Any = None,
            read_path: str | None = None,
        ) -> None:
            response = await self._request(
                "GET", read_path or path, allow_not_found=True
            )
            # Pulsar returns 200 with an empty body for unset namespace policies
            # such as retention; treat that as absent rather than drift.
            if response.status_code != 404 and response.content.strip():
                try:
                    current = response.json()
                except (ValueError, json.JSONDecodeError):
                    current = response.text
                matches = (
                    equivalent(current) if equivalent is not None else current == body
                )
                if matches:
                    return
                raise AdminError(f"unsupported or destructive drift at {path}")
            action = ReconcileAction(method, path, description)
            actions.append(action)
            changes.append(description)
            if not dry_run:
                pending.append((method, path, {"json_body": body}))

        async def reconcile_policy(
            method: str, path: str, body: object, description: str
        ) -> None:
            await reconcile_resource(
                method,
                path,
                body,
                description,
                equivalent=lambda current: (
                    isinstance(current, dict)
                    and isinstance(body, dict)
                    and all(current.get(key) == value for key, value in body.items())
                ),
            )

        await reconcile_resource(
            "PUT",
            "/admin/v2/tenants/omega",
            {"allowedClusters": ["standalone"], "adminRoles": ["omega-admin"]},
            "create tenant omega",
            equivalent=lambda current: (
                isinstance(current, dict)
                and current.get("allowedClusters") == ["standalone"]
                and current.get("adminRoles") == ["omega-admin"]
            ),
        )
        for namespace, policy in topology["tenant"]["namespaces"].items():
            await reconcile_resource(
                "PUT",
                f"/admin/v2/namespaces/omega/{quote(namespace)}",
                {},
                f"create namespace {namespace}",
                equivalent=lambda current: isinstance(current, dict),
            )
            retention = policy.get("retention", {})
            base = f"/admin/v2/namespaces/omega/{quote(namespace)}"
            retention_path = f"{base}/retention"
            if "size" in retention:
                retention_size = _size_mb(retention["size"])
            else:
                current_response = await self._request(
                    "GET", retention_path, allow_not_found=True
                )
                if (
                    current_response.status_code == 404
                    or not current_response.content.strip()
                ):
                    retention_size = -1
                else:
                    try:
                        current_retention = current_response.json()
                        if not isinstance(current_retention, dict):
                            raise TypeError("retention policy must be an object")
                        retention_size = int(
                            current_retention.get("retentionSizeInMB", -1)
                        )
                    except (TypeError, ValueError, AttributeError):
                        raise AdminError(
                            f"invalid retention policy at {retention_path}"
                        ) from None
            retention_body = {
                "retentionTimeInMinutes": (
                    _duration_ms(retention["time"]) // 60_000
                    if "time" in retention
                    else -1
                ),
                "retentionSizeInMB": retention_size,
            }
            await reconcile_policy(
                "POST",
                retention_path,
                retention_body,
                f"set retention {namespace}",
            )
            await reconcile_policy(
                "POST",
                f"{base}/deduplication",
                {"enableDeduplication": bool(policy.get("deduplication", False))},
                f"set deduplication {namespace}",
            )
            # Pulsar's namespace schema compatibility endpoint returns the active
            # strategy as a scalar; only set a missing strategy.
            compatibility = policy.get("schemaCompatibility")
            if compatibility and compatibility != "none-envelope-bytes":
                path = f"{base}/schemaCompatibilityStrategy"
                response = await self._request("GET", path, allow_not_found=True)
                try:
                    current = (
                        None
                        if response.status_code == 404 or not response.content.strip()
                        else response.json()
                    )
                except (ValueError, json.JSONDecodeError):
                    current = response.text.strip() or None
                # Pulsar reports an unset strategy as the scalar "UNDEFINED".
                unset = current in (
                    None,
                    "",
                    "UNDEFINED",
                    {"schemaCompatibilityStrategy": "UNDEFINED"},
                )
                matched = current == compatibility or current == {
                    "schemaCompatibilityStrategy": compatibility
                }
                if unset:
                    description = f"set schema compatibility {namespace}"
                    actions.append(ReconcileAction("POST", path, description))
                    changes.append(description)
                    if not dry_run:
                        pending.append(("POST", path, {"json_body": compatibility}))
                elif not matched:
                    raise AdminError(f"unsupported or destructive drift at {path}")

        from omega_prime.eventstream.adapters.pulsar import native_json_schema

        for spec in topology["topics"]["definitions"]:
            namespace, name = spec["name"].split("/", 1)
            topic = f"persistent://omega/{namespace}/{name}"
            topic_path = _url_path(topic)
            if spec["partitions"]:
                partition_path = f"{topic_path}/partitions"
                await reconcile_resource(
                    "PUT",
                    partition_path,
                    spec["partitions"],
                    f"create partitioned topic {spec['name']}",
                    equivalent=lambda current, desired=spec["partitions"]: (
                        current == desired
                        or (
                            isinstance(current, dict)
                            and current.get("partitions") == desired
                        )
                    ),
                )
            else:
                # Pulsar 5.0 rejects GET on the bare topic path (405); partition
                # metadata is the supported existence probe for both shapes.
                partition_path = f"{topic_path}/partitions"
                await reconcile_resource(
                    "PUT",
                    topic_path,
                    {},
                    f"create topic {spec['name']}",
                    read_path=partition_path,
                    equivalent=lambda current: (
                        current == 0
                        or (
                            isinstance(current, dict)
                            and int(current.get("partitions", -1)) == 0
                        )
                    ),
                )
            if spec.get("compacted"):
                threshold = 100 * 1024 * 1024
                await reconcile_resource(
                    "POST",
                    f"/admin/v2/persistent/omega/{quote(namespace)}/{quote(name, safe='.')}/compactionThreshold",
                    threshold,
                    f"configure compacted topic {spec['name']}",
                    equivalent=lambda current, desired=threshold: current == desired,
                )
            schema_path = f"/admin/v2/schemas/omega/{quote(namespace)}/{quote(name, safe='.')}/schema"
            schema = {
                "type": "JSON",
                "schema": json.dumps(native_json_schema(topic), separators=(",", ":")),
                "properties": {"schemaType": "JSON"},
            }
            await reconcile_resource(
                "POST",
                schema_path,
                schema,
                f"register native schema {spec['name']}",
                equivalent=lambda current, expected=schema: _schema_equivalent(
                    current, expected
                ),
            )

        permissions: dict[str, set[str]] = {}
        for role in topology["identity"]["applicationRoles"]:
            for direction in ("produces", "consumes"):
                grant = role[direction]
                operation = "produce" if direction == "produces" else "consume"
                for namespace in grant.get("namespaces", []):
                    path = (
                        f"/admin/v2/namespaces/omega/{quote(namespace)}"
                        f"/permissions/{quote(role['name'])}"
                    )
                    permissions.setdefault(path, set()).add(operation)
                for topic_name in grant.get("topics", []):
                    namespace, name = topic_name.split("/", 1)
                    path = (
                        f"{_url_path(f'persistent://omega/{namespace}/{name}')}"
                        f"/permissions/{quote(role['name'])}"
                    )
                    permissions.setdefault(path, set()).add(operation)

        async def reject_undeclared_grant(path: str) -> None:
            response = await self._request(
                "GET",
                path,
                allow_not_found=True,
                allow_method_not_allowed=True,
            )
            if response.status_code == 404:
                return
            if response.status_code == 405:
                # Pulsar 5.0 allows GET only on the permissions collection, not
                # the per-role path (Allow: DELETE,POST,OPTIONS).
                parent, _, role = path.rpartition("/")
                collection = await self._request("GET", parent, allow_not_found=True)
                if collection.status_code == 404:
                    return
                try:
                    grants = collection.json()
                except (ValueError, json.JSONDecodeError):
                    grants = {}
                if (
                    isinstance(grants, Mapping)
                    and role in grants
                    and grants[role] not in ([], {}, None)
                ):
                    raise AdminError(f"unsupported or destructive drift at {path}")
                return
            try:
                existing = response.json()
            except (ValueError, json.JSONDecodeError):
                existing = response.text
            if existing not in ([], {}):
                raise AdminError(f"unsupported or destructive drift at {path}")

        roles = [role["name"] for role in topology["identity"]["applicationRoles"]]
        for namespace in topology["tenant"]["namespaces"]:
            for role in roles:
                path = (
                    f"/admin/v2/namespaces/omega/{quote(namespace)}"
                    f"/permissions/{quote(role)}"
                )
                if path not in permissions:
                    await reject_undeclared_grant(path)
        topic_names = [spec["name"] for spec in topology["topics"]["definitions"]]
        topic_names.extend(
            f"system/{prefix}.{subscription}"
            for subscription in {
                handler["name"]
                for handler in topology["subscriptions"]["handlers"]
                if handler["name"] != "dlq-triage"
            }
            for prefix in ("retry", "dlq")
        )
        for topic_name in topic_names:
            namespace, name = topic_name.split("/", 1)
            topic_path = _url_path(f"persistent://omega/{namespace}/{name}")
            for role in roles:
                path = f"{topic_path}/permissions/{quote(role)}"
                if path not in permissions:
                    await reject_undeclared_grant(path)

        for path, operations in sorted(permissions.items()):
            body = sorted(operations)
            parent, _, role = path.rpartition("/")
            description = f"set exact permissions {role}"
            # Pulsar 5.0 rejects GET on the per-role permissions path (405).
            # Read the collection and treat a missing role as absent.
            response = await self._request("GET", parent, allow_not_found=True)
            if response.status_code != 404 and response.content.strip():
                try:
                    grants = response.json()
                except (ValueError, json.JSONDecodeError):
                    grants = None
                if isinstance(grants, Mapping) and role in grants:
                    current_ops = grants.get(role)
                    if isinstance(current_ops, (list, tuple, set)) and set(
                        current_ops
                    ) == set(body):
                        continue
                    raise AdminError(f"unsupported or destructive drift at {path}")
                if grants not in (None, {}, []):
                    # Collection present but role absent — safe to grant.
                    pass
            actions.append(ReconcileAction("POST", path, description))
            changes.append(description)
            if not dry_run:
                pending.append(("POST", path, {"json_body": body}))
        subscriptions = sorted(
            {
                handler["name"]
                for handler in topology["subscriptions"]["handlers"]
                if handler["name"] != "dlq-triage"
            }
        )
        for subscription in subscriptions:
            for prefix in ("retry", "dlq"):
                topic = f"persistent://omega/system/{prefix}.{subscription}"
                topic_path = _url_path(topic)
                await reconcile_resource(
                    "PUT",
                    topic_path,
                    {},
                    f"create {prefix} topic {subscription}",
                    read_path=f"{topic_path}/partitions",
                    equivalent=lambda current: (
                        current == 0
                        or (
                            isinstance(current, dict)
                            and int(current.get("partitions", -1)) == 0
                        )
                    ),
                )
        module_classes = {
            "greptile-score": ("greptile_score", "GreptileScoreFunction"),
            "ci-rollup": ("ci_rollup", "CiRollupFunction"),
            "pr-state-projector": ("pr_state_projector", "PrStateProjectorFunction"),
        }
        topic_map = {
            "sdlc/pr": "persistent://omega/sdlc/pr",
            "sdlc/review": "persistent://omega/sdlc/review",
            "sdlc/ci": "persistent://omega/sdlc/ci",
            "sdlc/pr-state": "persistent://omega/sdlc/pr-state",
        }
        package_root = Path(__file__).resolve().parents[1]
        schema_sources = tuple(
            sorted((package_root / "eventstream" / "schemas").glob("*.json"))
        )
        for function in topology["functions"]["definitions"]:
            name = function["name"]
            module, class_name = module_classes[name]
            if name == "greptile-score":
                input_names, output_name = ["sdlc/review"], "sdlc/review"
            elif name == "ci-rollup":
                input_names, output_name = ["sdlc/ci"], "sdlc/ci"
            else:
                input_names = ["sdlc/pr", "sdlc/review", "sdlc/ci"]
                output_name = "sdlc/pr-state"
            inputs = [topic_map[item] for item in input_names]
            output = topic_map[output_name]
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                paths = (
                    *function_module_sources(package_root, module),
                    *schema_sources,
                )
                for source in paths:
                    if source.is_file():
                        relative = source.relative_to(package_root)
                        info = zipfile.ZipInfo(
                            f"src/omega_prime/{relative.as_posix()}",
                            date_time=(1980, 1, 1, 0, 0, 0),
                        )
                        info.compress_type = zipfile.ZIP_DEFLATED
                        bundle.writestr(info, source.read_bytes())
                requirements = zipfile.ZipInfo(
                    "requirements.txt", date_time=(1980, 1, 1, 0, 0, 0)
                )
                requirements.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(requirements, "pydantic>=2.13,<3\n")
            archive_bytes = archive.getvalue()
            user_config = dict(function.get("userConfig", {}))
            user_config["_omega_artifact_sha256"] = hashlib.sha256(
                archive_bytes
            ).hexdigest()
            function_config = {
                "tenant": "omega",
                "namespace": "sdlc",
                "name": name,
                "className": f"omega_prime.eventstream.functions.{module}.{class_name}",
                "runtime": "PYTHON",
                "processingGuarantees": function["mode"],
                "parallelism": int(function.get("parallelism", 1)),
                "inputSpecs": {
                    topic: {"schemaType": "BYTES", "receiverQueueSize": 100}
                    for topic in inputs
                },
                "output": output,
                "outputSchemaType": "BYTES",
                "autoAck": True,
                "subName": f"omega-{name}",
                "cleanupSubscription": False,
                "userConfig": user_config,
            }
            path = f"/admin/v3/functions/omega/sdlc/{quote(name)}"
            current_response = await self._request("GET", path, allow_not_found=True)
            if current_response.status_code != 404:
                try:
                    current = current_response.json()
                except (ValueError, json.JSONDecodeError):
                    current = None
                keys = (
                    "className",
                    "runtime",
                    "processingGuarantees",
                    "parallelism",
                    "inputSpecs",
                    "output",
                    "outputSchemaType",
                    "userConfig",
                )
                if not isinstance(current, dict) or not all(
                    current.get(key) == function_config[key] for key in keys
                ):
                    raise AdminError(f"unsupported or destructive drift at {path}")
                continue
            description = f"deploy function {name}"
            actions.append(ReconcileAction("POST", path, description))
            changes.append(description)
            if not dry_run:
                pending.append(
                    (
                        "POST",
                        path,
                        {
                            "form_data": {
                                "functionConfig": json.dumps(function_config)
                            },
                            "files": {
                                "data": (
                                    f"{name}.zip",
                                    archive_bytes,
                                    "application/zip",
                                )
                            },
                        },
                    )
                )
        if (
            not dry_run
            and self._config is not None
            and self._config.token_file is not None
        ):
            raise AdminError(
                "secure Function deployment requires a verified least-privilege "
                "FunctionAuthProvider; the admin REST contract cannot provide "
                "FunctionAuthenticationSpec"
            )
        for method, path, kwargs in pending:
            await self._request(method, path, **kwargs)
        return ReconcileResult(dry_run, tuple(actions), tuple(changes))

    async def status(self) -> dict[str, object]:
        response = await self._request("GET", "/admin/v2/brokers/standalone")
        value = response.json()
        if not isinstance(value, dict):
            raise AdminError("broker status response is malformed")
        return value


def _schema_equivalent(current: object, expected: Mapping[str, object]) -> bool:
    """Compare Pulsar GET schema payloads to the POST body we would send.

    Pulsar returns the body under ``data`` (often pretty-printed) while we POST
    under ``schema`` with compact separators; compare the parsed Avro documents.
    """
    if not isinstance(current, Mapping):
        return False
    if current.get("type") != expected.get("type"):
        return False
    raw = current.get("schema", current.get("data"))
    expected_raw = expected.get("schema")
    if not isinstance(raw, str) or not isinstance(expected_raw, str):
        return bool(raw == expected_raw)
    try:
        return bool(json.loads(raw) == json.loads(expected_raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw == expected_raw


def _duration_ms(value: str) -> int:
    units = {"d": 86_400_000, "h": 3_600_000, "m": 60_000}
    number, unit = int(value[:-1]), value[-1]
    if unit not in units:
        raise AdminError("unsupported retention duration")
    return number * units[unit]


def _size_mb(value: str) -> int:
    if value == "-1":
        return -1
    amount, unit = int(value[:-2]), value[-2:].upper()
    if unit == "GB":
        return amount * 1024
    if unit == "MB":
        return amount
    raise AdminError("unsupported retention size")


__all__ = ["AdminError", "PulsarAdminPort", "ReconcileAction", "ReconcileResult"]

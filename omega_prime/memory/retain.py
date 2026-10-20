"""Local-first shared-memory retention with bound operation identities."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from omega_prime.credentials.redact import redact_value
from omega_prime.memory.backends import (
    MAX_COMPLETED_OPERATIONS,
    LocalRecord,
    MemoryBackends,
    encode_local_record,
    normalize_content,
    validate_scope_repo,
)
from omega_prime.substrate.client import MEMORY_KINDS

_LOCAL_BANK = "omega-prime-lead"
_REMOTE_METADATA_MARKER = "omega-memory:v1:"


def retain_memory(
    backends: MemoryBackends,
    content: str,
    *,
    scope: str,
    repo: str | None,
    kind: str,
    operation_id: str | None = None,
    expected_version: Any = None,
    source: str | None = None,
    receipt_path: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    graph_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Persist one typed memory locally, then publish to configured destinations."""
    scope, repo = validate_scope_repo(scope, repo)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    content = normalize_content(content)
    if not isinstance(kind, str) or kind not in MEMORY_KINDS:
        raise ValueError("unknown memory kind")
    if operation_id is not None and (
        not isinstance(operation_id, str) or not operation_id.strip()
    ):
        raise ValueError("operation_id must be a non-empty string or None")
    if any(
        value is not None and not isinstance(value, str)
        for value in (source, receipt_path, graph_id, task_id)
    ):
        raise ValueError("memory evidence must be a string or None")
    if tags is None:
        tags = ()
    if not isinstance(tags, (list, tuple)) or any(
        not isinstance(tag, str) for tag in tags
    ):
        raise ValueError("tags must be a list of strings")
    tags = tuple(tags)
    safe_fields = {
        "scope": scope,
        "repo": repo,
        "content": content,
        "source": source,
        "receipt_path": receipt_path,
        "tags": tags,
        "graph_id": graph_id,
        "task_id": task_id,
        "expected_version": expected_version,
    }
    if redact_value(safe_fields) != safe_fields:
        return _secret_refused(backends)
    try:
        expected_json = json.dumps(
            expected_version,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError("expected_version must be JSON-compatible") from None

    selected = backends.selected_backend
    if selected not in {"local", "substrate", "hindsight"}:
        raise ValueError("invalid memory backend selector")
    record = LocalRecord(
        bank=_LOCAL_BANK,
        scope=scope,
        repo=repo,
        kind=kind,
        content=content,
        source=source,
        receipt_path=receipt_path,
        tags=tags,
        graph_id=graph_id,
        task_id=task_id,
    )
    encoded = encode_local_record(record)
    destinations = _destinations(backends)
    fingerprint_data = {
        "record": json.loads(encoded.split("[omega-memory:v1]", 1)[1]),
        "expected_version": json.loads(expected_json),
        "selected_backend": selected,
        "destinations": destinations,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    with backends.operation_lock:
        if operation_id is not None:
            bound = backends.operation_bindings.get(operation_id)
            if bound is None or bound != fingerprint:
                return _refused(backends, operation_id)
            backends.operation_bindings.move_to_end(operation_id)
            completed = backends.operation_results.get(operation_id)
            if completed is not None:
                backends.operation_results.move_to_end(operation_id)
                return dict(completed)
            if backends.in_flight_bindings.get(operation_id) == fingerprint:
                return _refused(backends, operation_id)
            if operation_id in backends.in_flight_bindings:
                return _refused(backends, operation_id)
            backends.in_flight_bindings[operation_id] = fingerprint
            key = operation_id
        else:
            key = str(uuid.uuid4())
            backends.operation_bindings[key] = fingerprint
            backends.in_flight_bindings[key] = fingerprint

    result = _perform_retain(backends, record, encoded, key, expected_version)
    with backends.operation_lock:
        backends.in_flight_bindings.pop(key, None)
        backends.operation_results[key] = dict(result)
        while len(backends.operation_results) > MAX_COMPLETED_OPERATIONS:
            oldest = next(iter(backends.operation_results))
            backends.operation_results.pop(oldest, None)
            backends.operation_bindings.pop(oldest, None)
    return result


def _perform_retain(
    backends: MemoryBackends,
    record: LocalRecord,
    encoded: str,
    key: str,
    expected_version: Any,
) -> dict[str, Any]:
    selected = backends.selected_backend
    states: dict[str, dict[str, Any]] = {
        "local": {"state": "empty", "attempted": True, "participation": "required"},
        "substrate": _remote_initial(
            backends.substrate is not None,
            backends.substrate is not None or selected == "substrate",
        ),
        "hindsight": _remote_initial(
            backends.hindsight is not None, selected == "hindsight"
        ),
    }
    errors: list[str] = []
    for name in ("substrate", "hindsight"):
        if backends.errors.get(name):
            state = backends.states.get(name, "unreachable")
            if state not in {"not_configured", "unreachable", "unauthorized"}:
                state = "unreachable"
            states[name].update(
                state=state,
                error="configured memory backend is unavailable",
            )
            if state != "not_configured" and (
                name == "substrate" or selected == "hindsight"
            ):
                errors.append(f"{name} memory backend is unavailable")
    try:
        local = backends.local_store.add("memory", encoded)
    except Exception:
        local = {"success": False}
    if not isinstance(local, Mapping) or local.get("success") is not True:
        states["local"].update(state="unreachable", error="local memory write failed")
        return {
            "ok": False,
            "local_saved": False,
            "idempotency_key": key,
            "selected_backend": selected,
            "scope": record.scope,
            "repo": record.repo,
            "backends": states,
            "error": "local memory write failed",
        }
    states["local"]["state"] = "answered"

    metadata = _remote_metadata(record)
    if backends.substrate is not None:
        states["substrate"]["attempted"] = True
        try:
            response = backends.substrate.memory_write(
                record.scope,
                record.kind,
                record.content,
                idempotency_key=key,
                expected_version=expected_version,
                source_event=metadata,
            )
            if isinstance(response, Mapping) and response.get("status") == "accepted":
                states["substrate"]["state"] = "answered"
            else:
                states["substrate"].update(
                    state="unreachable", error="substrate refused memory write"
                )
                errors.append("substrate refused memory write")
        except Exception as exc:
            states["substrate"].update(
                state=_exception_state(exc), error=_safe_error(exc)
            )
            errors.append("substrate memory write failed")
    if selected == "substrate" and backends.substrate is None:
        state = backends.states.get("substrate", "not_configured")
        if state not in {"not_configured", "unreachable", "unauthorized"}:
            state = "unreachable"
        states["substrate"].update(
            state=state, error="selected substrate backend is unavailable"
        )
        errors.append("selected substrate backend is unavailable")

    if selected == "hindsight":
        if backends.hindsight is None:
            state = backends.states.get("hindsight", "not_configured")
            if state not in {"not_configured", "unreachable", "unauthorized"}:
                state = "unreachable"
            states["hindsight"].update(
                state=state,
                attempted=False,
                error="selected hindsight backend is unavailable",
            )
            errors.append("selected hindsight backend is unavailable")
        else:
            states["hindsight"]["attempted"] = True
            try:
                hindsight_tags = list(record.tags)
                hindsight_tags.append(metadata)
                response = backends.hindsight.retain(
                    [record.content],
                    context=metadata,
                    tags=hindsight_tags,
                )
                if isinstance(response, Mapping) and response.get("ok") is True:
                    states["hindsight"]["state"] = "answered"
                else:
                    states["hindsight"].update(
                        state="unreachable", error="hindsight refused memory write"
                    )
                    errors.append("hindsight refused memory write")
            except Exception as exc:
                states["hindsight"].update(
                    state=_exception_state(exc), error=_safe_error(exc)
                )
                errors.append("hindsight memory write failed")
    ok = not errors
    result: dict[str, Any] = {
        "ok": ok,
        "local_saved": True,
        "idempotency_key": key,
        "selected_backend": selected,
        "scope": record.scope,
        "repo": record.repo,
        "backends": states,
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def _remote_metadata(record: LocalRecord) -> str:
    payload = {
        "scope": record.scope,
        "repo": record.repo,
        "source": record.source,
    }
    return _REMOTE_METADATA_MARKER + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _secret_refused(backends: MemoryBackends) -> dict[str, Any]:
    return {
        "ok": False,
        "local_saved": False,
        "idempotency_key": None,
        "selected_backend": backends.selected_backend,
        "backends": {
            "local": {
                "state": "empty",
                "attempted": False,
                "participation": "required",
            },
            "substrate": _remote_initial(
                backends.substrate is not None,
                backends.substrate is not None
                or backends.selected_backend == "substrate",
            ),
            "hindsight": _remote_initial(
                backends.hindsight is not None,
                backends.selected_backend == "hindsight",
            ),
        },
        "error": "secret content or metadata refused",
    }


def _remote_initial(configured: bool, selected: bool) -> dict[str, Any]:
    if not configured:
        return {
            "state": "not_configured",
            "attempted": False,
            "participation": "not_configured",
        }
    if not selected:
        return {"state": "empty", "attempted": False, "participation": "not_attempted"}
    return {"state": "empty", "attempted": False, "participation": "selected"}


def _destinations(backends: MemoryBackends) -> dict[str, str | None]:
    return {
        "substrate": _identity(backends.substrate),
        "hindsight": _identity(backends.hindsight),
    }


def _identity(client: Any) -> str | None:
    if client is None:
        return None
    base_url = getattr(client, "base_url", None)
    bank = getattr(client, "bank", None)
    return json.dumps(
        {"base_url": base_url, "bank": bank},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _exception_state(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    return status if status in {"unreachable", "unauthorized"} else "unreachable"


def _safe_error(exc: Exception) -> str:
    # Arbitrary exception text is not a safe user-facing diagnostic.
    return "remote memory write failed"


def _refused(backends: MemoryBackends, operation_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "local_saved": False,
        "idempotency_key": operation_id,
        "selected_backend": backends.selected_backend,
        "backends": {
            "local": {
                "state": "empty",
                "attempted": False,
                "participation": "required",
            },
            "substrate": _remote_initial(
                backends.substrate is not None,
                backends.substrate is not None
                or backends.selected_backend == "substrate",
            ),
            "hindsight": _remote_initial(
                backends.hindsight is not None,
                backends.selected_backend == "hindsight",
            ),
        },
        "error": "operation identity is unknown, in flight, or bound to other input",
    }


__all__ = ["retain_memory"]

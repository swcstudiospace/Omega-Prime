"""Unified local and remote memory recall with tenant-safe merging."""

from __future__ import annotations

import json
from typing import Any

from omega_prime.credentials.redact import redact_text, redact_value
from omega_prime.memory.backends import (
    LegacyRecord,
    LocalRecord,
    MemoryBackends,
    content_hash,
    decode_local_record,
    normalize_content,
    validate_query,
    validate_scope_repo,
)
from omega_prime.memory.retain import _REMOTE_METADATA_MARKER
from omega_prime.substrate.client import SubstrateProtocolError, SubstrateToolError

_ERROR_TYPE_NAMES = {
    SubstrateProtocolError: "protocol_error",
    SubstrateToolError: "tool_error",
}


def error_type(error: Any) -> str | None:
    """Return stable typed error identity without exposing exception details."""
    for error_class, name in _ERROR_TYPE_NAMES.items():
        if isinstance(error, error_class):
            return name
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in {"protocol_error", "tool_error"}:
        return code
    return None


_BACKEND_NAMES = ("local", "substrate", "hindsight")
_DEFAULT_BANKS = ("omega-prime-lead", "ultrathink", "omega-prime-desk")
_LEGACY_BANKS = {"omega-prime-lead": "omes-lead", "omega-prime-desk": "omes-desk"}


def recall_memory(
    backends: MemoryBackends,
    query: str,
    *,
    scope: str,
    repo: str | None,
    limit: int,
    banks: list[str] | None = None,
    include_shared: bool = True,
) -> dict[str, Any]:
    """Recall and merge records from the local, substrate, and Hindsight sources."""
    validate_query(query, limit)
    scope, repo = validate_scope_repo(scope, repo)
    if not isinstance(include_shared, bool):
        raise ValueError("include_shared must be a boolean")
    if banks is not None and (
        not isinstance(banks, list)
        or any(
            not isinstance(bank, str)
            or not bank
            or bank != bank.strip()
            or any(char in bank for char in "[]\r\n")
            for bank in banks
        )
    ):
        raise ValueError("banks must be a list of non-empty bank names")

    selected_banks = list(banks) if banks else list(_DEFAULT_BANKS)
    if not include_shared:
        selected_banks = [
            bank
            for bank in selected_banks
            if bank not in {"ultrathink", "omega-prime-desk", "omes-desk"}
        ]
    banks_to_read = list(
        dict.fromkeys(
            bank
            for selected in selected_banks
            for bank in (selected, _LEGACY_BANKS.get(selected))
            if bank is not None
        )
    )

    statuses: dict[str, dict[str, Any]] = {
        name: {"state": "not_configured", "attempted": False} for name in _BACKEND_NAMES
    }
    merged: dict[str, dict[str, Any]] = {}

    def diagnostic(value: Any) -> str:
        try:
            text = str(value)
            cleaned = redact_text(text)
            return cleaned[:300] or "source request failed"
        except Exception:
            return "source request failed"

    def fail(name: str, state: Any, error: Any) -> None:
        status = {
            "state": state
            if isinstance(state, str) and state in {"unreachable", "unauthorized"}
            else "unreachable",
            "attempted": True,
            "error": diagnostic(error),
        }
        discriminator = error_type(error)
        if discriminator is not None:
            status["error_type"] = discriminator
        statuses[name] = status

    def add(content: str, source: dict[str, Any]) -> bool:
        normalized = normalize_content(content)
        if not normalized:
            return False
        digest = f"sha256:{content_hash(normalized)}"
        result = merged.get(digest)
        if result is None:
            result = {
                "content": redact_text(normalized),
                "content_hash": digest,
                "sources": [],
            }
            merged[digest] = result
        safe_source = redact_value(source)
        if safe_source not in result["sources"]:
            result["sources"].append(safe_source)
        return True

    def source_row(
        backend: str,
        hit: dict[str, Any],
        *,
        fallback_bank: str | None = None,
        trusted_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {"backend": backend}
        for key, value in hit.items():
            if (
                isinstance(key, str)
                and key not in {"text", "content", "backend"}
                and not (
                    key in {"context", "source_event"}
                    and isinstance(value, str)
                    and value.startswith(_REMOTE_METADATA_MARKER)
                )
                and not (
                    key in {"scope", "repo"}
                    and (trusted_metadata is None or key not in trusted_metadata)
                )
            ):
                row[key] = value
        if trusted_metadata is not None:
            row.update(trusted_metadata)
        if fallback_bank is not None and "bank" not in row:
            row["bank"] = fallback_bank
        return row

    def remote_metadata(hit: dict[str, Any], backend: str) -> dict[str, Any] | None:
        candidates = (
            (hit.get("source_event"),)
            if backend == "substrate"
            else (hit.get("context"),)
        )
        metadata: dict[str, Any] | None = None
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate.startswith(
                _REMOTE_METADATA_MARKER
            ):
                continue
            try:
                decoded = json.loads(candidate[len(_REMOTE_METADATA_MARKER) :])
                if not isinstance(decoded, dict):
                    return None
                metadata_scope_value = decoded.get("scope")
                metadata_repo_value = decoded.get("repo")
                if not isinstance(metadata_scope_value, str):
                    return None
                if metadata_repo_value is not None and not isinstance(
                    metadata_repo_value, str
                ):
                    return None
                trusted_scope, trusted_repo = validate_scope_repo(
                    metadata_scope_value, metadata_repo_value
                )
                trusted = {"scope": trusted_scope, "repo": trusted_repo}
                if "source" in decoded:
                    source = decoded["source"]
                    if source is not None and not isinstance(source, str):
                        return None
                    trusted["source"] = source
            except (TypeError, ValueError):
                return None
            if metadata is not None and metadata != trusted:
                return None
            metadata = trusted
        return metadata

    def in_scope(
        hit: dict[str, Any],
        trusted_metadata: dict[str, Any] | None,
    ) -> bool:
        hit_scope = hit.get("scope")
        hit_repo = hit.get("repo")
        if trusted_metadata is not None:
            metadata_scope = trusted_metadata["scope"]
            metadata_repo = trusted_metadata["repo"]
            if hit_scope is not None and hit_scope != metadata_scope:
                return False
            if hit_repo is not None and hit_repo != metadata_repo:
                return False
            hit_scope, hit_repo = metadata_scope, metadata_repo
        elif repo is not None:
            # Backend response fields and caller-controlled tags are not proof of
            # repository ownership. Repo-scoped remote hits need retained metadata.
            return False

        if repo is not None:
            return hit_scope == scope and hit_repo == repo
        return hit_repo is None and (hit_scope is None or hit_scope == scope)

    def source_response_state(
        name: str, payload: Any, fallback_bank: str | None = None
    ) -> None:
        if isinstance(payload, dict):
            error_state = payload.get("status")
            if isinstance(error_state, str) and error_state in {
                "unauthorized",
                "unreachable",
            }:
                fail(name, error_state, payload.get("error") or error_state)
                return
        if not isinstance(payload, list):
            fail(name, "unreachable", "source returned a malformed response")
            return

        found = False
        malformed = False
        for hit in payload:
            if not isinstance(hit, dict) or not isinstance(hit.get("text"), str):
                malformed = True
                continue
            marker_value = hit.get("source_event" if name == "substrate" else "context")
            has_marker = isinstance(marker_value, str) and marker_value.startswith(
                _REMOTE_METADATA_MARKER
            )
            trusted_metadata = remote_metadata(hit, name)
            if has_marker and trusted_metadata is None:
                continue
            if not in_scope(hit, trusted_metadata):
                continue
            found = (
                add(
                    hit["text"],
                    source_row(
                        name,
                        hit,
                        fallback_bank=fallback_bank,
                        trusted_metadata=trusted_metadata,
                    ),
                )
                or found
            )
        if malformed:
            fail(name, "unreachable", "source returned malformed hit data")
        else:
            statuses[name] = {
                "state": "answered" if found else "empty",
                "attempted": True,
            }

    # Refresh before every read: a long-lived store may have been changed elsewhere.
    statuses["local"] = {"state": "empty", "attempted": True}
    try:
        backends.local_store.load_from_disk()
        _entries, read_error = backends.local_store._read("memory")
        if read_error is not None:
            fail(
                "local",
                "unreachable",
                read_error.get("error", "memory file unreadable"),
            )
            entries = []
        else:
            entries = _entries
        lowered = query.strip().casefold()
        found = False
        malformed = False
        for stored in entries:
            if not stored.startswith("[hindsight:"):
                # Old MEMORY.md entries without a bank envelope are unscoped.
                continue
            decoded: LocalRecord | LegacyRecord | None = None
            matched_bank: str | None = None
            for bank in banks_to_read:
                try:
                    decoded = decode_local_record(stored, bank)
                except (TypeError, ValueError):
                    malformed = True
                    break
                if decoded is not None:
                    matched_bank = bank
                    break
            if decoded is None or matched_bank is None:
                continue
            if isinstance(decoded, LocalRecord):
                if decoded.scope != scope or decoded.repo != repo:
                    continue
                content = decoded.content
                source = {
                    "backend": "local",
                    "bank": decoded.bank,
                    "scope": decoded.scope,
                    "repo": decoded.repo,
                    "kind": decoded.kind,
                    "source": decoded.source,
                    "receipt_path": decoded.receipt_path,
                    "tags": list(decoded.tags),
                    "graph_id": decoded.graph_id,
                    "task_id": decoded.task_id,
                }
            else:
                if repo is not None:
                    continue
                content = decoded.content
                source = {"backend": "local", "bank": decoded.bank}
            normalized = normalize_content(content)
            if lowered not in normalized.casefold():
                continue
            found = add(normalized, source) or found
        if malformed:
            fail("local", "unreachable", "local memory contains malformed records")
        elif read_error is None:
            statuses["local"] = {
                "state": "answered" if found else "empty",
                "attempted": True,
            }
    except Exception as exc:
        fail("local", "unreachable", exc)

    if backends.substrate is not None:
        statuses["substrate"] = {"state": "empty", "attempted": True}
        try:
            source_limit = limit * 10
            payload = backends.substrate.memory_search(
                query, scope, source_limit, repo=repo
            )
            source_response_state("substrate", payload)
        except Exception as exc:
            state = getattr(exc, "status", None)
            fail("substrate", state, exc)
    elif backends.errors.get("substrate"):
        state = backends.states.get("substrate", "unreachable")
        if state not in {"not_configured", "unreachable", "unauthorized"}:
            state = "unreachable"
        statuses["substrate"] = {
            "state": state,
            "attempted": False,
            "error": diagnostic(backends.errors["substrate"]),
        }
    if backends.hindsight is not None:
        statuses["hindsight"] = {"state": "empty", "attempted": True}
        try:
            payload = backends.hindsight.recall(query, limit=limit * 10)
            source_response_state(
                "hindsight",
                payload,
                fallback_bank=getattr(backends.hindsight, "bank", None),
            )
        except Exception as exc:
            state = getattr(exc, "status", None)
            fail("hindsight", state, exc)
    elif backends.errors.get("hindsight"):
        state = backends.states.get("hindsight", "unreachable")
        if state not in {"not_configured", "unreachable", "unauthorized"}:
            state = "unreachable"
        statuses["hindsight"] = {
            "state": state,
            "attempted": False,
            "error": diagnostic(backends.errors["hindsight"]),
        }

    return {
        "selected_backend": backends.selected_backend,
        "scope": scope,
        "repo": repo,
        "backends": statuses,
        "results": list(merged.values())[:limit],
    }


__all__ = ["recall_memory"]

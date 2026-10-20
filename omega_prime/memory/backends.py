"""Shared memory backend configuration and local-record contracts.

This module deliberately resolves only explicitly supplied/configured endpoints;
transport policy and phase-97 endpoint resolution remain owned by their clients.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from omega_prime.memory.store import ENTRY_DELIMITER, MEMORY_CHAR_LIMIT, MemoryStore

MAX_COMPLETED_OPERATIONS = 1024


SELECTOR_ENV = "OMEGA_PRIME_MEMORY_BACKEND"
VALID_BACKENDS = ("local", "substrate", "hindsight")
BACKEND_STATES = ("answered", "empty", "not_configured", "unreachable", "unauthorized")
_RECORD_MARKER = "[omega-memory:v1]"
_OUTER_BANK = re.compile(r"^\[hindsight:([^\[\]\r\n]+)\] (.*)$", re.DOTALL)
_SCOPE_REPO_MAX = 256


@dataclass(frozen=True)
class LocalRecord:
    bank: str
    scope: str
    repo: str | None
    kind: str
    content: str
    source: str | None = None
    receipt_path: str | None = None
    tags: tuple[str, ...] = ()
    graph_id: str | None = None
    task_id: str | None = None
    v: int = 1


@dataclass(frozen=True)
class LegacyRecord:
    bank: str
    scope: None
    repo: None
    kind: str
    content: str
    source: None = None
    receipt_path: None = None
    tags: tuple[str, ...] = ()
    graph_id: None = None
    task_id: None = None
    v: None = None


@dataclass
class MemoryBackends:
    """Resolved dependencies and shared in-process retain identity state."""

    local_store: MemoryStore
    substrate: Any | None
    hindsight: Any | None
    trusted_scope: str
    trusted_repo: str | None
    selected_backend: str
    states: dict[str, str]
    errors: dict[str, str] = field(default_factory=dict)
    # The ledger policy lives in retain.py; these containers are shared by callers.
    operation_bindings: OrderedDict[str, str] = field(default_factory=OrderedDict)
    operation_results: OrderedDict[str, dict[str, Any]] = field(
        default_factory=OrderedDict
    )
    in_flight_bindings: dict[str, str] = field(default_factory=dict)
    operation_lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def store(self) -> MemoryStore:
        """The local store, under the short name used by memory consumers."""
        return self.local_store

    @property
    def config_states(self) -> dict[str, str]:
        """Safe per-source configuration states (no endpoint or secret values)."""
        return self.states


def resolve_memory_backends(
    *,
    local_store: MemoryStore | None = None,
    substrate: Any | None = None,
    hindsight: Any | None = None,
    substrate_url: str | None = None,
    hindsight_url: str | None = None,
    scope: str = "default",
    repo: str | None = None,
    environ: Mapping[str, str] | None = None,
    substrate_factory: Callable[[str], Any] | None = None,
    hindsight_factory: Callable[[str], Any] | None = None,
) -> MemoryBackends:
    """Resolve safe dependencies without probing services or hidden fallbacks.

    A caller may pass a client already resolved by the authoritative runtime
    resolver, or an explicitly configured URL with a constructor. An implicit
    loopback substrate default is never treated as configured.
    """
    validate_scope_repo(scope, repo)
    env = os.environ if environ is None else environ
    raw_selector = env.get(SELECTOR_ENV)
    configured_substrate_url = substrate_url or env.get("SUBSTRATE_URL") or None
    # Do not mistake a transport's historical loopback URL for explicit setup.
    if configured_substrate_url is not None and _is_loopback_url(
        configured_substrate_url
    ):
        configured_substrate_url = None
    configured_hindsight_url = hindsight_url or env.get("HINDSIGHT_URL") or None

    substrate_configured = substrate is not None or configured_substrate_url is not None
    hindsight_configured = hindsight is not None or configured_hindsight_url is not None
    if raw_selector is None:
        selected = "substrate" if configured_substrate_url is not None else "local"
        # An injected client alone can be a phase-97-resolved URL; no URL is
        # introspected from the client because it may be a hidden default.
        if substrate is not None and configured_substrate_url is None:
            selected = "local"
    else:
        selected = raw_selector.strip().lower()
        if selected not in VALID_BACKENDS:
            raise ValueError("invalid memory backend selector")

    if substrate is None and configured_substrate_url is not None and substrate_factory:
        substrate = substrate_factory(configured_substrate_url)
    if hindsight is None and configured_hindsight_url is not None and hindsight_factory:
        hindsight = hindsight_factory(configured_hindsight_url)

    # Explicit selection is retained even if unavailable: callers can report a
    # configuration failure rather than silently changing write preference.
    states = {
        "local": "empty",
        "substrate": "empty"
        if substrate_configured or substrate is not None
        else "not_configured",
        "hindsight": "empty"
        if hindsight_configured or hindsight is not None
        else "not_configured",
    }
    # A configured URL without a factory/client is still configuration present,
    # but not an operational dependency; model it as unavailable, not ready.
    if configured_substrate_url is not None and substrate is None:
        states["substrate"] = "not_configured"
    if configured_hindsight_url is not None and hindsight is None:
        states["hindsight"] = "not_configured"
    return MemoryBackends(
        local_store=local_store
        or MemoryStore(os.getenv("OMEGA_PRIME_MEMORY_DIR", "omega_prime/state/memory")),
        substrate=substrate,
        hindsight=hindsight,
        trusted_scope=scope,
        trusted_repo=repo,
        selected_backend=selected,
        states=states,
    )


def validate_scope_repo(scope: str, repo: str | None) -> tuple[str, str | None]:
    """Validate tenant identifiers before any backend I/O."""
    if (
        not isinstance(scope, str)
        or not scope
        or scope != scope.strip()
        or len(scope) > _SCOPE_REPO_MAX
    ):
        raise ValueError("scope must be a non-empty, trimmed identifier")
    if any(ord(char) < 32 or ord(char) == 127 for char in scope):
        raise ValueError("scope contains a control character")
    if repo is not None:
        if (
            not isinstance(repo, str)
            or not repo
            or repo != repo.strip()
            or len(repo) > _SCOPE_REPO_MAX
        ):
            raise ValueError("repo must be a non-empty, trimmed identifier or None")
        if any(ord(char) < 32 or ord(char) == 127 for char in repo):
            raise ValueError("repo contains a control character")
    return scope, repo


def validate_query(query: str, limit: int) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    validate_limit(limit)


def validate_content(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    return normalize_content(content)


def validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")


def normalize_content(content: str) -> str:
    """Canonical content: normalize newlines, then trim only outer whitespace."""
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    return content.replace("\r\n", "\n").replace("\r", "\n").strip()


def content_hash(content: str) -> str:
    """SHA-256 of canonical content, with case and bracket text preserved."""
    canonical = normalize_content(content)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_local_record(record: LocalRecord | Mapping[str, Any]) -> str:
    """Encode the complete, once-wrapped record passed to MemoryStore.add."""
    values = _record_values(record)
    _validate_record_values(values)
    if ENTRY_DELIMITER in values["content"]:
        raise ValueError("content contains the memory entry delimiter")
    payload = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    encoded = f"[hindsight:{values['bank']}] {_RECORD_MARKER}{payload}"
    if len(encoded) > MEMORY_CHAR_LIMIT:
        raise ValueError("encoded memory record exceeds the memory character limit")
    return encoded


def decode_local_record(
    stored_text: str, expected_bank: str
) -> LocalRecord | LegacyRecord | None:
    """Decode an expected-bank record; return None for another bank."""
    _validate_bank(expected_bank)
    if not isinstance(stored_text, str):
        raise ValueError("stored memory record must be text")
    match = _OUTER_BANK.match(stored_text)
    if match is None:
        raise ValueError("malformed memory bank envelope")
    bank, body = match.groups()
    _validate_bank(bank)
    if bank != expected_bank:
        return None
    if not body.startswith(_RECORD_MARKER):
        if "[omega-memory:" in body:
            raise ValueError("malformed or unsupported memory record version")
        return LegacyRecord(
            bank=bank, scope=None, repo=None, kind="memory", content=body
        )
    raw_json = body[len(_RECORD_MARKER) :]
    try:
        payload = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("malformed versioned memory record") from None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("unsupported versioned memory record")
    required = {
        "v",
        "bank",
        "scope",
        "repo",
        "kind",
        "content",
        "source",
        "receipt_path",
        "tags",
        "graph_id",
        "task_id",
    }
    if set(payload) != required:
        raise ValueError("invalid versioned memory record fields")
    if payload["bank"] != bank:
        raise ValueError("memory record bank mismatch")
    _validate_record_values(payload)
    if ENTRY_DELIMITER in payload["content"]:
        raise ValueError("content contains the memory entry delimiter")
    return LocalRecord(
        bank=payload["bank"],
        scope=payload["scope"],
        repo=payload["repo"],
        kind=payload["kind"],
        content=payload["content"],
        source=payload["source"],
        receipt_path=payload["receipt_path"],
        tags=tuple(payload["tags"]),
        graph_id=payload["graph_id"],
        task_id=payload["task_id"],
        v=payload["v"],
    )


def _record_values(record: LocalRecord | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(record, LocalRecord):
        source = {
            name: getattr(record, name)
            for name in (
                "v",
                "bank",
                "scope",
                "repo",
                "kind",
                "content",
                "source",
                "receipt_path",
                "tags",
                "graph_id",
                "task_id",
            )
        }
    elif isinstance(record, Mapping):
        source = dict(record)
        source.setdefault("v", 1)
        for name, default in (
            ("source", None),
            ("receipt_path", None),
            ("tags", []),
            ("graph_id", None),
            ("task_id", None),
        ):
            source.setdefault(name, default)
    else:
        raise ValueError("record must be a LocalRecord or mapping")
    return {
        "v": source.get("v"),
        "bank": source.get("bank"),
        "scope": source.get("scope"),
        "repo": source.get("repo"),
        "kind": source.get("kind"),
        "content": source.get("content"),
        "source": source.get("source"),
        "receipt_path": source.get("receipt_path"),
        "tags": list(source.get("tags", ())),
        "graph_id": source.get("graph_id"),
        "task_id": source.get("task_id"),
    }


def _validate_record_values(values: Mapping[str, Any]) -> None:
    if values.get("v") != 1 or isinstance(values.get("v"), bool):
        raise ValueError("unsupported memory record version")
    scope = values.get("scope")
    if not isinstance(scope, str):
        raise ValueError("scope must be a non-empty string")
    validate_scope_repo(scope, values.get("repo"))
    for name in ("kind", "content"):
        if not isinstance(values.get(name), str) or not values[name].strip():
            raise ValueError(f"{name} must be a non-empty string")
    if not isinstance(values.get("tags"), (list, tuple)) or any(
        not isinstance(tag, str) for tag in values["tags"]
    ):
        raise ValueError("tags must be a list of strings")
    for name in ("source", "receipt_path", "graph_id", "task_id"):
        if values.get(name) is not None and not isinstance(values[name], str):
            raise ValueError(f"{name} must be a string or None")


def _validate_bank(bank: Any) -> None:
    if (
        not isinstance(bank, str)
        or not bank
        or bank != bank.strip()
        or any(char in bank for char in "[]\r\n")
    ):
        raise ValueError("invalid memory bank")


def _is_loopback_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


__all__ = [
    "BACKEND_STATES",
    "MAX_COMPLETED_OPERATIONS",
    "SELECTOR_ENV",
    "VALID_BACKENDS",
    "LegacyRecord",
    "LocalRecord",
    "MemoryBackends",
    "content_hash",
    "decode_local_record",
    "encode_local_record",
    "normalize_content",
    "resolve_memory_backends",
    "validate_content",
    "validate_limit",
    "validate_query",
    "validate_scope_repo",
]

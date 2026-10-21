"""Substrate tool family: product docs plus graph coordination.

``substrate_docs_search`` calls substrate-mcp ``docs_search`` (RAGflow-backed
retrieval) through an injected client. The ``substrate_graph_*`` tools
coordinate desk-pack handoffs through the substrate graph ops with lease
handling. Failures are plain ``{"error": ...}`` results, never exceptions
out of dispatch. The three mutating graph tools require approval.
"""

from __future__ import annotations

import math
from typing import Any

from omega_prime.credentials.redact import redact_text, redact_value
from omega_prime.substrate.client import (
    SubstrateError,
    SubstrateNotConfiguredError,
    SubstrateProtocolError,
    SubstrateToolError,
    SubstrateUnauthorizedError,
    SubstrateUnreachableError,
)

_CONTENT_CAP = 1500
_LABEL_CAP = 500
_ERROR_CAP = 500


def _substrate_failure(exc: Exception) -> str:
    if isinstance(exc, SubstrateNotConfiguredError):
        state = "not_configured"
    elif isinstance(exc, SubstrateUnauthorizedError):
        state = "unauthorized"
    elif isinstance(exc, SubstrateProtocolError):
        state = "protocol_error"
    elif isinstance(exc, SubstrateToolError):
        state = "tool_error"
    elif isinstance(exc, SubstrateUnreachableError):
        state = "unreachable"
    else:
        state = "unreachable"
    return redact_text(f"{state}: {exc}")[:_ERROR_CAP]


SUBSTRATE_TOOL_NAMES = (
    "substrate_docs_search",
    "substrate_graph_claim",
    "substrate_graph_release",
    "substrate_graph_complete",
    "substrate_graph_heartbeat",
)

APPROVALS = {
    "substrate_graph_claim",
    "substrate_graph_release",
    "substrate_graph_complete",
}


def register_substrate_tools(registry: Any, client: Any) -> list[str]:
    """Register every substrate tool. ``client`` is an injected substrate client."""

    def substrate_docs_search_tool(query: str) -> dict:
        return docs_search(client, query)

    def substrate_graph_claim_tool(
        graph_id: str, node_id: str, session_id: str, ttl_seconds: int = 3600
    ) -> dict:
        return graph_claim(client, graph_id, node_id, session_id, ttl_seconds)

    def substrate_graph_release_tool(
        graph_id: str, node_id: str, lease_id: str | None = None
    ) -> dict:
        return graph_release(client, graph_id, node_id, lease_id)

    def substrate_graph_complete_tool(
        graph_id: str, node_id: str, lease_id: str | None = None
    ) -> dict:
        return graph_complete(client, graph_id, node_id, lease_id)

    def substrate_graph_heartbeat_tool(
        graph_id: str, node_id: str, lease_id: str | None = None
    ) -> dict:
        return graph_heartbeat(client, graph_id, node_id, lease_id)

    handlers = {
        "substrate_docs_search": substrate_docs_search_tool,
        "substrate_graph_claim": substrate_graph_claim_tool,
        "substrate_graph_release": substrate_graph_release_tool,
        "substrate_graph_complete": substrate_graph_complete_tool,
        "substrate_graph_heartbeat": substrate_graph_heartbeat_tool,
    }
    if set(handlers) != set(SUBSTRATE_TOOL_NAMES):
        raise RuntimeError("substrate tool handlers drifted from SUBSTRATE_TOOL_NAMES")
    for name in SUBSTRATE_TOOL_NAMES:
        description, parameters = _SCHEMAS[name]
        registry.register(
            name,
            description,
            parameters,
            handlers[name],
            requires_approval=name in APPROVALS,
        )
    return list(SUBSTRATE_TOOL_NAMES)


def docs_search(client: Any, query: str) -> dict:
    """Search product docs via the substrate. Failures are ``{"error": ...}``.

    Model-facing success returns redacted excerpt ``chunks`` with bounded
    provenance metadata only; the raw retrieval body is never exported.
    """
    if not isinstance(query, str) or query == "":
        return {"error": "query must be a non-empty string"}
    try:
        result = client.docs_search(query)
    except Exception as exc:
        return {"error": _substrate_failure(exc)}
    if not isinstance(result, dict) or not result.get("ok"):
        reason = result.get("error") if isinstance(result, dict) else None
        if not isinstance(reason, str) or reason == "":
            reason = "docs plane unconfigured"
        return redact_value({"error": redact_text(reason)[:_ERROR_CAP]})
    body = result.get("body")
    if not isinstance(body, dict):
        return redact_value({"error": "docs plane returned a malformed response"})
    code = body.get("code", 0)
    if isinstance(code, bool) or not isinstance(code, int):
        return redact_value({"error": "docs plane returned a malformed response"})
    if code != 0:
        message = body.get("message")
        if not isinstance(message, str) or message == "":
            message = "unknown retrieval error"
        return redact_value(
            {"error": redact_text(f"docs plane error {code}: {message}")[:_ERROR_CAP]}
        )
    raw = _raw_chunks(body)
    if raw is None:
        return redact_value({"error": "docs plane returned a malformed response"})
    return redact_value({"chunks": _chunks(raw)})


def _raw_chunks(body: dict) -> list | None:
    """Extract the chunk container, or ``None`` when malformed.

    Nested ``data.chunks`` and top-level ``chunks`` lists are supported; a
    genuine ``[]`` is valid. Absent/null/wrongly typed containers are
    malformed so a broken docs response never reads as an empty search.
    """
    data = body.get("data")
    if data is not None and not isinstance(data, dict):
        return None
    if isinstance(data, dict) and "chunks" in data:
        raw = data["chunks"]
        return raw if isinstance(raw, list) else None
    if "chunks" in body:
        raw = body["chunks"]
        return raw if isinstance(raw, list) else None
    return None


def _chunks(raw: list) -> list[dict[str, Any]]:
    """Shape a chunk list like ``lead_docs_search`` results.

    ``content`` is redacted and capped; ``document``/``dataset_id`` fall back
    across RAGflow's key spellings and keep only redacted, bounded strings;
    ``score`` keeps only finite numbers so zero survives and vendor strings do
    not. IDs and numeric positions retain bounded citation provenance.
    Non-dict entries are skipped.
    """
    shaped: list[dict[str, Any]] = []
    for chunk in raw:
        if not isinstance(chunk, dict):
            continue
        content = chunk.get("content", "")
        if not isinstance(content, str):
            content = ""
        document: str | None = None
        for key in ("document", "document_keyword", "docnm_kwd"):
            candidate = chunk.get(key)
            if isinstance(candidate, str) and candidate != "":
                document = redact_text(candidate)[:_LABEL_CAP]
                break
        dataset_id = chunk.get("dataset_id")
        if not isinstance(dataset_id, str) or dataset_id == "":
            dataset_id = None
        else:
            dataset_id = redact_text(dataset_id)[:_LABEL_CAP]
        score = chunk.get("score")
        if score is None:
            score = chunk.get("similarity")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or (isinstance(score, float) and not math.isfinite(score))
        ):
            score = None
        excerpt: dict[str, Any] = {
            "content": redact_text(content)[:_CONTENT_CAP],
            "document": document,
            "dataset_id": dataset_id,
            "score": score,
        }
        for key, source in (("chunk_id", "id"), ("document_id", "document_id")):
            identifier = chunk.get(key) or chunk.get(source)
            if isinstance(identifier, str) and identifier:
                excerpt[key] = redact_text(identifier)[:_LABEL_CAP]
        positions = chunk.get("positions")
        if isinstance(positions, list):
            safe_positions = [
                position
                for position in positions[:32]
                if isinstance(position, list)
                and 1 <= len(position) <= 5
                and all(
                    type(coordinate) is int and coordinate >= 0
                    for coordinate in position
                )
            ]
            if safe_positions:
                excerpt["positions"] = safe_positions
        shaped.append(excerpt)
    return shaped


def graph_claim(
    client: Any, graph_id: str, node_id: str, session_id: str, ttl_seconds: int = 3600
) -> dict:
    """Claim a graph node for this surface. Returns the lease dict."""
    error = _graph_error(graph_id, node_id)
    if error is not None:
        return error
    if not isinstance(session_id, str) or session_id == "":
        return {"error": "session_id must be a non-empty string"}
    try:
        return client.graph_claim(graph_id, node_id, session_id, ttl_seconds)
    except SubstrateError as exc:
        return {"error": _substrate_failure(exc)}
    except ValueError as exc:
        return {"error": str(exc)}


def graph_release(
    client: Any, graph_id: str, node_id: str, lease_id: str | None = None
) -> dict:
    """Release this surface's claim on a node."""
    error = _graph_error(graph_id, node_id)
    if error is not None:
        return error
    if not isinstance(lease_id, str) or lease_id == "":
        return {"error": "lease_id must be a non-empty string"}
    try:
        return {"result": client.graph_release(graph_id, node_id, lease_id)}
    except SubstrateError as exc:
        return {"error": _substrate_failure(exc)}
    except ValueError as exc:
        return {"error": str(exc)}


def graph_complete(
    client: Any, graph_id: str, node_id: str, lease_id: str | None = None
) -> dict:
    """Mark a node completed and drop its claim."""
    error = _graph_error(graph_id, node_id)
    if error is not None:
        return error
    if not isinstance(lease_id, str) or lease_id == "":
        return {"error": "lease_id must be a non-empty string"}
    try:
        return {"result": client.graph_complete(graph_id, node_id, lease_id)}
    except SubstrateError as exc:
        return {"error": _substrate_failure(exc)}
    except ValueError as exc:
        return {"error": str(exc)}


def graph_heartbeat(
    client: Any, graph_id: str, node_id: str, lease_id: str | None = None
) -> dict:
    """Refresh the lease TTL for an active claim."""
    error = _graph_error(graph_id, node_id)
    if error is not None:
        return error
    if not isinstance(lease_id, str) or lease_id == "":
        return {"error": "lease_id must be a non-empty string"}
    try:
        return client.graph_heartbeat(graph_id, node_id, lease_id)
    except SubstrateError as exc:
        return {"error": _substrate_failure(exc)}
    except ValueError as exc:
        return {"error": str(exc)}


def _graph_error(graph_id: str, node_id: str) -> dict | None:
    if not isinstance(graph_id, str) or graph_id == "":
        return {"error": "graph_id must be a non-empty string"}
    if not isinstance(node_id, str) or node_id == "":
        return {"error": "node_id must be a non-empty string"}
    return None


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


def _graph_ref(required: list[str], extra: dict | None = None) -> dict:
    properties = {
        "graph_id": _string("Graph ID, e.g. ut-<ts36>-<8hex>."),
        "node_id": _string("Graph-of-Thought node id within the graph."),
    }
    if extra:
        properties.update(extra)
    return {"type": "object", "properties": properties, "required": required}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "substrate_docs_search": (
        "Search the product documentation (RAGflow-backed retrieval through "
        "the substrate). Returns redacted excerpt chunks with provenance "
        "(document, dataset, score) when a dataset answers, or an error when "
        "the docs plane is unconfigured or returns a malformed response.",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language question for the docs.",
                }
            },
            "required": ["query"],
        },
    ),
    "substrate_graph_claim": (
        "Claim a Graph-of-Thought node for this surface. Returns the lease "
        "(expiry + token) or the existing claim. Requires approval.",
        _graph_ref(
            ["graph_id", "node_id", "session_id"],
            {
                "session_id": _string("This session's id, recorded on the claim."),
                "ttl_seconds": {
                    "type": "integer",
                    "description": "Lease TTL in seconds.",
                    "default": 3600,
                },
            },
        ),
    ),
    "substrate_graph_release": (
        "Release this surface's claim on a node without marking it complete. "
        "Requires approval.",
        _graph_ref(
            ["graph_id", "node_id", "lease_id"],
            {"lease_id": _string("Lease ID from the claim result.")},
        ),
    ),
    "substrate_graph_complete": (
        "Mark a node completed and drop its claim. Requires approval.",
        _graph_ref(
            ["graph_id", "node_id", "lease_id"],
            {"lease_id": _string("Lease ID from the claim result.")},
        ),
    ),
    "substrate_graph_heartbeat": (
        "Refresh the lease TTL for an active claim using its lease_id.",
        _graph_ref(
            ["graph_id", "node_id", "lease_id"],
            {"lease_id": _string("Lease ID from the claim result.")},
        ),
    ),
}

__all__ = [
    "APPROVALS",
    "SUBSTRATE_TOOL_NAMES",
    "docs_search",
    "graph_claim",
    "graph_complete",
    "graph_heartbeat",
    "graph_release",
    "register_substrate_tools",
]

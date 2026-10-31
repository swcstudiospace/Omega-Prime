"""Structured turn traces: spans for model calls, tools, and policy verdicts.

A `Tracer` collects `{"seq", "ts", "kind", "name", "fields"}` spans from the
loop (`kind="model"`), the registry (`kind="tool"`, plus `kind="policy"` on
refusals), and the workspace (`kind="policy"` on read-only write refusals).
String field values are stored redacted. Nothing attached, nothing recorded.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from omega_prime.credentials.redact import redact_text


class Tracer:
    """In-memory span collector with a stable export shape."""

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id if trace_id else uuid.uuid4().hex
        self._spans: list[dict[str, Any]] = []

    def span(
        self,
        kind: str,
        name: str,
        fields: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> dict[str, Any]:
        """Append one span. Return it."""
        cleaned = {
            str(key): redact_text(value) if isinstance(value, str) else value
            for key, value in (fields or {}).items()
        }
        entry: dict[str, Any] = {
            "seq": len(self._spans) + 1,
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "kind": str(kind),
            "name": str(name),
            "fields": cleaned,
        }
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        self._spans.append(entry)
        return entry

    def spans(self) -> list[dict[str, Any]]:
        """Every span in order."""
        return list(self._spans)

    def to_dict(self) -> dict[str, Any]:
        """``{"trace_id", "spans"}`` for export."""
        return {"trace_id": self.trace_id, "spans": self.spans()}


__all__ = ["Tracer"]

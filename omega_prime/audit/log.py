"""Persisted audit log: one NDJSON record per tool dispatch.

Each record carries a sequence number, a UTC timestamp, the tool name, and
the verdict (`allowed`, `denied`, `error`, `unknown`). Tool arguments are
deliberately absent — they may carry secrets, and phase 14's redaction story
leans on that guarantee.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega_prime.credentials.redact import redact_text

VERDICTS = ("allowed", "denied", "error", "unknown")


class AuditLog:
    """Append-only NDJSON file. Sequence continues across handles."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = self._last_seq()

    def append(
        self, tool: str, verdict: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Append one record. The reason is stored redacted."""
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
        self._seq += 1
        record: dict[str, Any] = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tool": str(tool),
            "verdict": verdict,
        }
        if reason is not None:
            record["reason"] = redact_text(str(reason))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def records(self) -> list[dict[str, Any]]:
        """Every record in file order."""
        if not self.path.is_file():
            return []
        found: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if line.strip() == "":
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"corrupt audit log {self.path} at line {number}: {exc}"
                    ) from exc
                if not isinstance(record, dict):
                    raise ValueError(
                        f"corrupt audit log {self.path} at line {number}: not an object"
                    )
                found.append(record)
        return found

    def violations(self) -> list[dict[str, Any]]:
        """The `denied` records, in order."""
        return [
            record for record in self.records() if record.get("verdict") == "denied"
        ]

    def _last_seq(self) -> int:
        highest = 0
        for record in self.records():
            seq = record.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                highest = max(highest, seq)
        return highest


__all__ = ["VERDICTS", "AuditLog"]

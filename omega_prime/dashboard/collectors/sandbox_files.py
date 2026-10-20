# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Collect sandbox progress, E2E runs, and receipts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_line, redact_value


def make_sandbox_files_collector(
    *, sandbox_root: Path, now_fn: Callable[[], datetime], window_hours: float = 24.0
) -> Callable[[], SourceResult]:
    """Build a cached collector for sandbox filesystem artifacts."""
    cache: dict[Path, tuple[int, int, object]] = {}

    def unchanged(path: Path) -> object | None:
        stat = path.stat()
        prior = cache.get(path)
        if prior is not None and prior[:2] == (stat.st_mtime_ns, stat.st_size):
            return prior[2]
        cache[path] = (stat.st_mtime_ns, stat.st_size, None)
        return None

    def collect() -> SourceResult:
        if not sandbox_root.exists():
            return SourceResult(
                "sandbox", "unavailable", error=f"{sandbox_root}: path not found"
            )
        detail = None
        progress_file = sandbox_root / "PROGRESS.md"
        if progress_file.is_file():
            prior = unchanged(progress_file)
            if isinstance(prior, str):
                detail = prior
            else:
                size = progress_file.stat().st_size
                with progress_file.open("rb") as stream:
                    stream.seek(max(0, size - 262144))
                    raw = stream.read(262144)
                if size > 262144 and raw and not raw.startswith(b"\n"):
                    first_newline = raw.find(b"\n")
                    raw = b"" if first_newline < 0 else raw[first_newline + 1 :]
                lines = raw.decode("utf-8", errors="replace").splitlines()
                detail = redact_line(
                    next(
                        (line.strip() for line in reversed(lines) if line.strip()), ""
                    ),
                    cap=200,
                )
                stat = progress_file.stat()
                cache[progress_file] = (stat.st_mtime_ns, stat.st_size, detail)
        cutoff = now_fn() - timedelta(hours=window_hours)
        items: list[dict[str, object]] = []
        e2e_dir = sandbox_root / ".state" / "e2e"
        recent: list[tuple[int, Path]] = []
        if e2e_dir.is_dir():
            for path in e2e_dir.glob("*.jsonl"):
                try:
                    stat = path.stat()
                    if (
                        datetime.fromtimestamp(stat.st_mtime, tz=cutoff.tzinfo)
                        >= cutoff
                    ):
                        recent.append((stat.st_mtime_ns, path))
                except OSError:
                    continue
        if recent:
            _, path = max(recent)
            stat = path.stat()
            items.append(
                redact_value(
                    {
                        "kind": "sandbox_e2e",
                        "run_id": path.stem,
                        "last_ts": datetime.fromtimestamp(
                            stat.st_mtime, tz=cutoff.tzinfo
                        ).isoformat(),
                        "path": str(path),
                        "stage": None,
                    }
                )
            )
        receipt_dir = sandbox_root / ".state" / "receipts"
        if receipt_dir.is_dir():
            for path in sorted(receipt_dir.glob("*.json")):
                try:
                    stat = path.stat()
                    if (
                        datetime.fromtimestamp(stat.st_mtime, tz=cutoff.tzinfo)
                        >= cutoff
                    ):
                        items.append(
                            redact_value(
                                {
                                    "kind": "sandbox_receipt",
                                    "name": path.stem,
                                    "last_ts": datetime.fromtimestamp(
                                        stat.st_mtime, tz=cutoff.tzinfo
                                    ).isoformat(),
                                    "path": str(path),
                                }
                            )
                        )
                except OSError:
                    continue
        present = progress_file.exists() or e2e_dir.exists() or receipt_dir.exists()
        return SourceResult(
            "sandbox", "ok" if present else "empty", items=items, detail=detail
        )

    return collect

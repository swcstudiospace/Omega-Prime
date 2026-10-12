"""Lead pack: desk core+lead tools as Omega Prime registry families.

Ports `services/desk-gateway/src/desk_gateway/tools/{core,lead}.py` (reads
fail open with data; writes need approval) onto Omega Prime machinery: memory and
receipt checks reuse the Omega Prime store and `validate_receipt`, events land in a
local NDJSON store, and intake/graph/bus/docs/notify run behind injected
seams that default to unconfigured. No live calls in tests.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from omega_prime.credentials.redact import redact_text, redact_value
from omega_prime.memory.hindsight import Hindsight
from omega_prime.memory.store import MemoryStore
from omega_prime.receipts import ReceiptError, load_executions, validate_receipt
from omega_prime.tooling.fs import atomic_write_text, ensure_private_dir
from omega_prime.tools.registry import ToolRegistry

_fcntl: ModuleType | None
try:
    import fcntl as _fcntl_module
except ImportError:  # pragma: no cover - non-POSIX platforms
    _fcntl = None
else:
    _fcntl = _fcntl_module

LEAD_TOOL_NAMES = (
    "lead_brief",
    "lead_docs_search",
    "lead_memory_retain",
    "lead_memory_recall",
    "lead_ownership_resolve",
    "lead_receipt_check",
    "lead_event_emit",
    "lead_doctor",
    "lead_render_prompt",
    "lead_intake_next",
    "lead_intake_ack",
    "lead_graph_register",
    "lead_graph_state",
    "lead_bus_start",
    "lead_bus_wait",
    "lead_roster_status",
)

BRIEF_TTL_SEC = 300
EVENT_PAYLOAD_LIMIT = 4096
GITHUB_ISSUE = re.compile(
    r"^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/issues/(\d+)$"
)
ACK_STATUSES = ("accepted", "rejected", "in_progress", "done")
GRAPH_ACTIONS = {
    "claim": "graph_claim",
    "release": "graph_release",
    "complete": "graph_complete",
}


def _error(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"error": f"{code}: {reason}", **extra}


_DEFAULT_ROSTER = Path("omega_prime/grokbot/rosters/default.json")


def _absorbed_seats(root: Path) -> list[dict[str, Any]]:
    """Seats folded into bot-00-omega-prime. Missing file → empty list."""
    path = root / _DEFAULT_ROSTER
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    seats = data.get("absorbed_seats")
    if not isinstance(seats, dict):
        return []
    absorbed_into = data.get("bot_id") or "bot-00-omega-prime"
    rows: list[dict[str, Any]] = []
    for name, entry in seats.items():
        record = entry if isinstance(entry, dict) else {}
        rows.append(
            {
                "seat": name,
                "pack": record.get("pack"),
                "source_bot_id": record.get("bot_id"),
                "absorbed_pack_of": absorbed_into,
                "registered_gateway_seat": False,
            }
        )
    return rows


class IntakeStore:
    """JSON work-order queue. Claims are exclusive per order.

    The persisted file is authoritative at every public operation. Reads
    reload the file first; mutations run as serialized
    reload-mutate-atomic-replace transactions under the instance thread
    lock plus an exclusive `flock` on the stable `intake.json.lock`
    sidecar, so concurrent stores and processes never lose each other's
    committed orders, claims, or field updates. The sidecar is never
    unlinked: replacing it would split writers into different lock
    domains. A corrupt existing file raises instead of being treated as
    empty. Stale-lease completion is not fenced: `ack` remains an
    ordered merge write with no expected-claim condition.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.orders: list[dict] = []
        self._thread_lock = threading.Lock()
        self._load()

    @property
    def _lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Hold the thread lock and the sidecar `flock` for one transaction."""
        with self._thread_lock:
            ensure_private_dir(self.path.parent)
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if _fcntl is not None:
                    _fcntl.flock(fd, _fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _find(self, intake_id: str) -> dict | None:
        """One order by id from the current snapshot, or None."""
        for record in self.orders:
            if record.get("intake_id") == intake_id:
                return record
        return None

    def _reclaim_locked(self, max_age_seconds: float, current: float) -> list[str]:
        """Release expired `in_progress` claims in the current snapshot."""
        released: list[str] = []
        for record in self.orders:
            if record.get("status") != "in_progress":
                continue
            claimed = record.get("claimed_at")
            if (
                isinstance(claimed, (int, float))
                and current - claimed <= max_age_seconds
            ):
                continue
            record["status"] = "open"
            record["by"] = None
            record["claimed_at"] = None
            released.append(str(record.get("intake_id")))
        return released

    def submit(
        self, ask: str, origin: str = "local", links: list | None = None
    ) -> dict:
        """Append one open order and return it."""
        record = {
            "intake_id": "in-" + secrets.token_hex(6),
            "origin": origin,
            "ask": ask,
            "status": "open",
            "links": list(links or []),
            "graph_id": None,
            "message": None,
            "by": None,
        }
        with self._exclusive():
            self._load()
            self.orders.append(record)
            self._save()
        return record

    def next(
        self,
        origin: str | None,
        by: str,
        *,
        reclaim_after: float = 3600.0,
        now: float | None = None,
    ) -> dict | None:
        """Claim the oldest open order for `by`. None when empty.

        Orders stuck `in_progress` past `reclaim_after` seconds are
        released first, so a crashed dispatcher never strands the
        queue. Pass `now` (epoch seconds) in tests. Reclamation,
        selection, and the claim commit as one transaction; reclaimed
        orders persist even when no open order matches `origin`.
        """
        current = time.time() if now is None else now
        with self._exclusive():
            self._load()
            released = self._reclaim_locked(reclaim_after, current)
            selected: dict | None = None
            for record in self.orders:
                if record.get("status") != "open":
                    continue
                if origin is not None and record.get("origin") != origin:
                    continue
                record["status"] = "in_progress"
                record["by"] = by
                record["claimed_at"] = current
                selected = record
                break
            if released or selected is not None:
                self._save()
            return selected

    def release(self, intake_id: str) -> bool:
        """Return one order to open. False when missing."""
        with self._exclusive():
            self._load()
            record = self._find(intake_id)
            if record is None:
                return False
            record["status"] = "open"
            record["by"] = None
            record["claimed_at"] = None
            self._save()
        return True

    def reclaim_stale(
        self, max_age_seconds: float = 3600.0, now: float | None = None
    ) -> list[str]:
        """Release `in_progress` orders older than the lease. Returns ids."""
        current = time.time() if now is None else now
        with self._exclusive():
            self._load()
            released = self._reclaim_locked(max_age_seconds, current)
            if released:
                self._save()
            return released

    def get(self, intake_id: str) -> dict | None:
        """One order by id, or None. Reloads the file first."""
        with self._thread_lock:
            self._load()
            return self._find(intake_id)

    def ack(self, intake_id: str, ack: dict) -> dict | None:
        """Merge `ack` into the order. None when missing."""
        with self._exclusive():
            self._load()
            record = self._find(intake_id)
            if record is None:
                return None
            record.update(ack)
            self._save()
            return record

    def counts(self) -> dict[str, int]:
        """Orders per status. Reloads the file first."""
        with self._thread_lock:
            self._load()
            totals: dict[str, int] = {}
            for record in self.orders:
                status = str(record.get("status", "open"))
                totals[status] = totals.get(status, 0) + 1
            return totals

    def _load(self) -> None:
        if not self.path.exists():
            self.orders = []
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        orders = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(orders, list):
            raise ValueError(f"intake store {self.path} is not an orders list")
        self.orders = orders

    def _save(self) -> None:
        text = json.dumps({"orders": self.orders}, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(self.path, text)


class RosterStore:
    """Pack registration + doctor results. JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.packs: dict[str, dict] = {}
        self.doctor: dict[str, dict] = {}
        self._load()

    def register(self, pack: str, record: dict) -> dict:
        """Record one pack's registration."""
        self.packs[pack] = dict(record)
        self._save()
        return {"packs": sorted(self.packs)}

    def pack_records(self, pack: str) -> list[str]:
        """Names of loaded packs relevant to `pack` (all registered)."""
        _ = pack
        return sorted(self.packs)

    def doctor_result(self, pack: str, result: dict) -> None:
        """Remember one pack's latest doctor outcome."""
        self.doctor[pack] = dict(result)
        self._save()

    def doctor_results(self) -> dict[str, dict]:
        """Latest doctor outcome per pack."""
        return dict(self.doctor)

    def _load(self) -> None:
        if not self.path.exists():
            self.packs = {}
            self.doctor = {}
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"roster store {self.path} is not an object")
        packs = data.get("packs", {})
        doctor = data.get("doctor", {})
        self.packs = packs if isinstance(packs, dict) else {}
        self.doctor = doctor if isinstance(doctor, dict) else {}

    def _save(self) -> None:
        text = (
            json.dumps(
                {"packs": self.packs, "doctor": self.doctor},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        atomic_write_text(self.path, text)


class EventStore:
    """Append-only NDJSON event mirror."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._seq = 0
        for _ in self.records():
            self._seq += 1

    def append(self, event: dict) -> dict:
        """Append one event with seq + timestamp. Payloads stay redacted."""
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **{k: redact_value(v) for k, v in event.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def records(self) -> list[dict]:
        """Every record in file order.

        A torn final line (a crash mid-append leaves one) is skipped; a
        corrupt line anywhere else still raises instead of silently
        dropping history.
        """
        if not self.path.is_file():
            return []
        lines = [
            line
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows = []
        for index, line in enumerate(lines):
            try:
                rows.append(json.loads(line))
            except ValueError:
                if index != len(lines) - 1:
                    raise
        return rows


@dataclass
class LeadContext:
    """Everything the lead tools need. Optionals default to unconfigured."""

    bot_id: str = "bot-00-omega-prime"
    root: str | Path = "."
    registry: Any = None
    memory: MemoryStore | None = None
    intake: IntakeStore | None = None
    roster: RosterStore | None = None
    events: EventStore | None = None
    substrate: Any = None
    bus: Any = None
    docs_index: Any = None
    notify: Any = None
    seat_prompt: str = "omega_prime/prompts/bot-00-omega-prime.xml"
    assembled_prompt: str = "omega_prime/prompts-assembled/OMEGA_PRIME.xml"
    _brief_cache: dict = field(default_factory=dict, repr=False)


class LeadClient:
    """Desk core+lead tools bound to one context."""

    def __init__(self, ctx: LeadContext) -> None:
        self.ctx = ctx

    # -- reads -----------------------------------------------------------

    def brief(
        self,
        graph_id: str | None = None,
        task_id: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Seat brief: recall + packs + intake + reminders. Cached 300s."""
        key = f"{graph_id or ''}:{task_id or ''}"
        now = time.time()
        if not refresh:
            cached = self.ctx._brief_cache.get(key)
            if cached and now - cached[0] < BRIEF_TTL_SEC:
                return {**cached[1], "cached": True}
        query = task_id or graph_id or "current work"
        recall = self.memory_recall(query, limit=5, include_shared=True)
        packs = self.ctx.roster.pack_records("lead") if self.ctx.roster else []
        intake = self.ctx.intake.counts() if self.ctx.intake else {}
        result = {
            "seat": "Omega Prime",
            "generated_at": now,
            "recall": recall,
            "loaded_packs": packs,
            "intake_queue": intake,
            "reminders": [
                "Run lead_ownership_resolve before the first edit.",
                "A completion claim needs a receipt that passes lead_receipt_check.",
            ],
        }
        self.ctx._brief_cache[key] = (now, result)
        return result

    def docs_search(
        self, query: str, limit: int = 8, repo: str | None = None
    ) -> dict[str, Any]:
        """Search the docs index. A chunk is not a repo fact until the file is opened."""
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if self.ctx.docs_index is None:
            return {
                "results": [],
                "error": "not_configured: docs index is not configured",
            }
        raw = self.ctx.docs_index(query, limit, repo)
        results = []
        for chunk in raw if isinstance(raw, list) else []:
            if not isinstance(chunk, dict):
                continue
            results.append(
                {
                    "content": redact_text(str(chunk.get("content", "")))[:1500],
                    "document": chunk.get("document"),
                    "dataset_id": chunk.get("dataset_id"),
                    "score": chunk.get("score"),
                }
            )
        return {
            "results": results,
            "note": "a chunk is not a repository fact until the file is opened",
        }

    def memory_recall(
        self,
        query: str,
        limit: int = 8,
        include_shared: bool = True,
        banks: list[str] | None = None,
    ) -> dict[str, Any]:
        """Recall Omega Prime memory banks matching `query` (case-insensitive)."""
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if self.ctx.memory is None:
            return {
                "banks": [],
                "results": [],
                "error": "not_configured: memory store is missing",
            }
        own = "omega-prime-lead"
        names = (
            list(banks)
            if banks
            else ([own] + (["omega-prime-desk"] if include_shared else []))
        )
        legacy_aliases = {
            "omega-prime-lead": "omes-lead",
            "omega-prime-desk": "omes-desk",
        }
        results = []
        for bank in names:
            entries = list(Hindsight(self.ctx.memory, bank).recall(query, limit=limit))
            legacy_bank = legacy_aliases.get(bank)
            if legacy_bank and len(entries) < limit:
                legacy_entries = Hindsight(self.ctx.memory, legacy_bank).recall(
                    query, limit=limit - len(entries)
                )
                seen_texts = set(entries)
                for le in legacy_entries:
                    if le not in seen_texts:
                        entries.append(le)
                        seen_texts.add(le)
            results.append(
                {"bank": bank, "entries": [redact_value(e) for e in entries]}
            )
        return {"banks": names, "results": results}

    def ownership_resolve(self, paths: list[str]) -> dict[str, Any]:
        """Resolve each path's owner from ownership.yaml (last match wins)."""
        if (
            not isinstance(paths, list)
            or not paths
            or any(not isinstance(p, str) for p in paths)
        ):
            raise ValueError("paths must be a non-empty list of strings")
        manifest = Path(self.ctx.root) / "omega_prime" / "ownership.yaml"
        if not manifest.is_file():
            return _error("not_configured", "ownership.yaml is not in this root")
        rules = _load_ownership(manifest.read_text(encoding="utf-8"))
        results = []
        for path in paths:
            owner = _resolve_owner(path, rules)
            results.append(
                {
                    "path": path,
                    "owner": owner,
                    "mine": owner == self.ctx.bot_id,
                    "unowned": owner is None,
                }
            )
        return {"results": results}

    def receipt_check(
        self,
        receipt: dict | None = None,
        receipt_path: str | None = None,
        bot: str | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Validate a receipt dict (or a JSON file under root). Never raises."""
        _ = strict
        if receipt is None and receipt_path:
            target = Path(self.ctx.root) / receipt_path
            if not target.is_file():
                return _error("not_found", f"{receipt_path} is not under this root")
            try:
                receipt = json.loads(target.read_text(encoding="utf-8"))
            except ValueError:
                return _error("invalid_receipt", "receipt is not valid JSON")
        if receipt is None:
            return _error("invalid_args", "receipt or receipt_path is required")
        if redact_text(json.dumps(receipt)) != json.dumps(receipt):
            return {
                "ok": False,
                "problems": ["receipt contains a credential shape (PD-4)"],
            }
        try:
            validate_receipt(receipt, executions=load_executions())
        except ReceiptError as exc:
            return {
                "ok": False,
                "problems": str(exc).splitlines() or [str(exc)],
                "bot": bot or self.ctx.bot_id,
            }
        return {"ok": True, "bot": bot or self.ctx.bot_id}

    def render_prompt(self) -> dict[str, Any]:
        """Assemble the seat prompt in an exported tree; return text + sha256."""
        root = Path(self.ctx.root)
        script = root / "omega_prime" / "scripts" / "assemble-prompts.sh"
        if not script.is_file():
            return _error("not_configured", "assemble-prompts.sh is not in this root")
        with tempfile.TemporaryDirectory(prefix="omega-prime-render-") as tmp:
            tree = Path(tmp)
            # Copy the whole package: assemble.py imports tool-name constants
            # from omega_prime.tools, and the shadowing namespace package here
            # bypasses any installed editable finder, so a partial allowlist
            # breaks on the first transitive import (receipts, offer, ...).
            shutil.copytree(
                root / "omega_prime",
                tree / "omega_prime",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            venv = root / ".venv"
            if venv.is_dir():
                # The script resolves its interpreter as $REPO/.venv/bin/python;
                # without this link it falls back to system python3, which lacks
                # the package dependencies the assembler imports.
                os.symlink(venv, tree / ".venv", target_is_directory=True)
            try:
                run = subprocess.run(
                    [
                        "bash",
                        str(tree / "omega_prime" / "scripts" / "assemble-prompts.sh"),
                    ],
                    cwd=str(tree),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return _error("upstream_error", f"assembler failed: {exc}")
            if run.returncode != 0:
                return _error(
                    "upstream_error", f"assemble-prompts failed: {run.stderr[-300:]}"
                )
            out = tree / "omega_prime" / "prompts-assembled" / "OMEGA_PRIME.xml"
            if not out.is_file():
                return _error("not_found", "assembler produced no prompt for this seat")
            text = out.read_text(encoding="utf-8")
        if "{{" in text:
            return _error("upstream_error", "assembled prompt still has placeholders")
        return {"prompt": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}

    def bus_wait(
        self, job_id: str, timeout_sec: int = 180, poll_sec: int = 2
    ) -> dict[str, Any]:
        """Wait for one bus job. Passthrough to the injected bus."""
        if not isinstance(job_id, str) or job_id == "":
            raise ValueError("job_id must be a non-empty string")
        if self.ctx.bus is None:
            return _error("not_configured", "agent bus is not configured")
        result = self.ctx.bus.wait_job(job_id, int(timeout_sec), int(poll_sec))
        if not isinstance(result, dict) or result.get("error"):
            reason = (
                result.get("reason", "agent bus unavailable")
                if isinstance(result, dict)
                else "agent bus unavailable"
            )
            return _error(
                result.get("error", "upstream_error")
                if isinstance(result, dict)
                else "upstream_error",
                str(reason),
            )
        return {
            "ok": True,
            "job": result.get("body"),
            "timed_out": bool(result.get("timed_out")),
        }

    def roster_status(self) -> dict[str, Any]:
        """Registered packs, tool counts, intake queue, completeness.

        Absorbed desk seats come from ``grokbot/rosters/default.json`` when
        that file is under the install root. They are packs folded into
        ``bot-00-omega-prime``, not separately registered gateway seats.
        """
        packs = []
        records = self.ctx.roster.packs if self.ctx.roster else {}
        doctor = self.ctx.roster.doctor_results() if self.ctx.roster else {}
        for name in sorted(records):
            entry = records[name] or {}
            packs.append(
                {
                    "pack": name,
                    "version": entry.get("version"),
                    "tools": entry.get("tools", []),
                    "doctor": doctor.get(name),
                }
            )
        intake = self.ctx.intake.counts() if self.ctx.intake else {}
        return {
            "packs": packs,
            "intake_queue": intake,
            "complete": bool(packs),
            "absorbed_seats": _absorbed_seats(Path(self.ctx.root)),
        }

    # -- writes (approval-gated at registration) -------------------------

    def memory_retain(
        self,
        content: str,
        receipt_path: str | None = None,
        source: str | None = None,
        tags: list[str] | None = None,
        graph_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Keep one memory entry. Needs evidence; secrets are refused."""
        if not isinstance(content, str) or content.strip() == "":
            raise ValueError("content must be a non-empty string")
        if not receipt_path and not source:
            return _error("evidence_required", "retain needs receipt_path or source")
        if redact_text(content) != content:
            return _error(
                "secret_refused",
                "content contains a credential shape; remove it and retry",
            )
        if self.ctx.memory is None:
            return _error("not_configured", "memory store is missing")
        bits = ["[pack:lead]", content.strip()]
        for label, value in (
            ("receipt_path", receipt_path),
            ("source", source),
            ("graph_id", graph_id),
            ("task_id", task_id),
        ):
            if value:
                bits.append(f"[{label}:{value}]")
        for tag in tags or []:
            bits.append(f"[{tag}]")
        joined = " ".join(bits)
        if redact_text(joined) != joined:
            return _error(
                "secret_refused",
                "content contains a credential shape; remove it and retry",
            )
        kept = Hindsight(self.ctx.memory, "omega-prime-lead").retain(joined)
        if not kept.get("ok"):
            return _error("store_refused", str(kept.get("reason", "store refused")))
        return {"ok": True, "bank": "omega-prime-lead"}

    def event_emit(
        self,
        kind: str,
        payload: dict | None = None,
        graph_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one lead event locally; forward to the emitter when set."""
        if not isinstance(kind, str) or kind == "":
            raise ValueError("kind must be a non-empty string")
        body = dict(payload or {})
        if len(json.dumps(body)) > EVENT_PAYLOAD_LIMIT:
            return _error("payload_too_large", "payload exceeds 4 KB")
        event = {
            "kind": kind,
            "graph_id": graph_id,
            "task_id": task_id,
            "payload": body,
            "actor": "agent",
        }
        stored = self.ctx.events.append(event) if self.ctx.events else None
        if self.ctx.substrate is None:
            return {"ok": True, "stored": stored, "emitted": False}
        outgoing = stored if isinstance(stored, dict) else redact_value(event)
        result = self.ctx.substrate.emit(
            {k: v for k, v in outgoing.items() if v is not None}
        )
        if not isinstance(result, dict) or result.get("error"):
            reason = (
                result.get("reason", "event not accepted")
                if isinstance(result, dict)
                else "event not accepted"
            )
            code = (
                result.get("error", "upstream_error")
                if isinstance(result, dict)
                else "upstream_error"
            )
            return _error(str(code), str(reason), local_mirror=True)
        return {"ok": True, "stored": stored, "substrate": result.get("body")}

    def doctor(
        self,
        action: str = "check",
        agent_uuid: str | None = None,
        prompt_sha256: str | None = None,
        installed_skills: list[str] | None = None,
    ) -> dict[str, Any]:
        """check/register/install_prompt/repair the lead seat integrity."""
        if action not in ("check", "register", "install_prompt", "repair"):
            raise ValueError(
                "action must be check, register, install_prompt, or repair"
            )
        if action == "register":
            if not agent_uuid:
                return _error("invalid_args", "register needs agent_uuid")
            if self.ctx.roster is None:
                return _error("not_configured", "roster store is missing")
            self.ctx.roster.register("lead", {"agent_uuid": agent_uuid, "version": 1})
            registered = sorted(self.ctx.roster.packs)
            return {"ok": True, "registered": registered, "missing": []}
        if action in ("install_prompt", "repair"):
            rendered = self.render_prompt()
            if rendered.get("error"):
                return rendered
            if self.ctx.roster is not None:
                self.ctx.roster.doctor_result(
                    "lead", {"expected_prompt_sha256": rendered["sha256"]}
                )
            return {
                "ok": True,
                "sha256": rendered["sha256"],
                "prompt": rendered["prompt"],
            }
        checks: dict[str, dict[str, Any]] = {}
        expected = (
            (self.ctx.roster.doctor_results().get("lead") or {})
            if self.ctx.roster
            else {}
        )
        expected_sha = expected.get("expected_prompt_sha256")
        prompt_ok = bool(
            expected_sha and prompt_sha256 and expected_sha == prompt_sha256
        )
        checks["prompt"] = {
            "green": prompt_ok,
            "detail": "sha256 matches the rendered prompt"
            if prompt_ok
            else "run install_prompt, then check with prompt_sha256",
        }
        declared = _declared_skills(Path(self.ctx.root) / self.ctx.seat_prompt)
        installed = set(installed_skills or [])
        missing = (
            sorted(s for s in declared if s not in installed) if installed else declared
        )
        checks["skills"] = {
            "green": bool(installed) and not missing,
            "declared": declared,
            "missing": missing,
        }
        checks["memory"] = {
            "green": self.ctx.memory is not None,
            "detail": "" if self.ctx.memory is not None else "memory store is missing",
        }
        live = len(self.ctx.registry.schemas()) if self.ctx.registry is not None else 0
        base = len(LEAD_TOOL_NAMES)
        checks["tools"] = {"green": live >= base, "pack": base, "live": live}
        packs = sorted(self.ctx.roster.packs) if self.ctx.roster else []
        checks["roster"] = {"green": "lead" in packs, "registered": packs}
        checks["substrate"] = {
            "green": self.ctx.substrate is not None,
            "detail": ""
            if self.ctx.substrate is not None
            else "substrate is not configured",
        }
        green = all(c["green"] for c in checks.values())
        if self.ctx.roster is not None:
            outcome: dict[str, Any] = {
                "green": green,
                "checks": {k: v["green"] for k, v in checks.items()},
            }
            # Preserve the installed expected prompt digest: a check must
            # never erase its own prerequisite, and the expected value is
            # only ever set by install_prompt/repair from rendered bytes —
            # never derived from a caller-supplied claim.
            prior_expected = expected.get("expected_prompt_sha256")
            if prior_expected is not None:
                outcome["expected_prompt_sha256"] = prior_expected
            self.ctx.roster.doctor_result("lead", outcome)
        return {"green": green, "seat": "Omega Prime", "checks": checks}

    def intake_next(self, origin: str | None = None) -> dict[str, Any]:
        """Claim the oldest open work order. None + counts when empty."""
        if self.ctx.intake is None:
            return _error("not_configured", "intake store is missing")
        record = self.ctx.intake.next(origin, self.ctx.bot_id)
        counts = self.ctx.intake.counts()
        if record is None:
            return {"work_order": None, "queue": counts}
        return {
            "work_order": record,
            "queue": counts,
            "next": "Stage 1: ORIGINAL is work_order.ask verbatim",
        }

    def intake_ack(
        self,
        intake_id: str,
        status: str,
        graph_id: str | None = None,
        message: str | None = None,
        links: list[str] | None = None,
    ) -> dict[str, Any]:
        """Ack one order; notify a GitHub issue link when the origin has one."""
        if not isinstance(intake_id, str) or intake_id == "":
            raise ValueError("intake_id must be a non-empty string")
        if status not in ACK_STATUSES:
            raise ValueError(f"status must be one of {', '.join(ACK_STATUSES)}")
        if self.ctx.intake is None:
            return _error("not_configured", "intake store is missing")
        # One transactional merge against the freshly loaded row; a missing
        # row is the existing not_found result. Notification stays outside
        # the store lock.
        ack: dict[str, Any] = {"status": status, "by": self.ctx.bot_id}
        if graph_id is not None:
            ack["graph_id"] = graph_id
        if message is not None:
            ack["message"] = message
        if links is not None:
            ack["links"] = list(links)
        updated = self.ctx.intake.ack(intake_id, ack)
        if updated is None:
            return _error("not_found", f"no intake {intake_id}")
        notify: dict[str, Any] = {
            "delivered": False,
            "reason": "origin has no callback",
        }
        for link in updated.get("links") or []:
            match = GITHUB_ISSUE.match(link) if isinstance(link, str) else None
            if match and updated.get("origin") == "github":
                lines = [f"**Omega Prime** · Lead · {status}"]
                if graph_id:
                    lines.append(f"Graph ID: `{graph_id}`")
                if message:
                    lines.append(message)
                for extra in links or []:
                    lines.append(f"- {extra}")
                lines.append(f"intake `{intake_id}`")
                body = "\n\n".join(lines)
                if self.ctx.notify is None:
                    notify = {
                        "delivered": False,
                        "reason": "notifier is not configured",
                        "target": link,
                    }
                else:
                    delivered = self.ctx.notify(link, body)
                    notify = {"delivered": bool(delivered), "target": link}
                break
        return {"ok": True, "intake": updated, "notify": notify}

    def graph_register(
        self, graph_id: str, repo: str, nodes: list[dict] | None = None
    ) -> dict[str, Any]:
        """Register one planning graph with the substrate plane."""
        if not isinstance(graph_id, str) or graph_id == "":
            raise ValueError("graph_id must be a non-empty string")
        if not isinstance(repo, str) or repo == "":
            raise ValueError("repo must be a non-empty string")
        if self.ctx.substrate is None:
            return _error("not_configured", "substrate is not configured")
        payload: dict[str, Any] = {
            "graph_id": graph_id,
            "repo": repo,
            "status": "planning",
        }
        if nodes:
            payload["nodes"] = [
                {
                    k: v
                    for k, v in {
                        "node_id": n["node_id"],
                        "linear_identifier": n.get("linear_id"),
                        "state": "open",
                    }.items()
                    if v is not None
                }
                for n in nodes
            ]
        result = self.ctx.substrate.call_tool("graph_register", payload, timeout=10)
        if not isinstance(result, dict) or result.get("error"):
            reason = (
                result.get("reason", "graph_register failed")
                if isinstance(result, dict)
                else "graph_register failed"
            )
            code = (
                result.get("error", "upstream_error")
                if isinstance(result, dict)
                else "upstream_error"
            )
            return _error(str(code), str(reason))
        return {"ok": True, "graph_id": graph_id, "substrate": result.get("content")}

    def graph_state(
        self,
        graph_id: str,
        node_id: str,
        action: str,
        surface: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Claim, release, or complete one graph node."""
        if action not in GRAPH_ACTIONS:
            raise ValueError("action must be claim, release, or complete")
        if (
            not isinstance(graph_id, str)
            or graph_id == ""
            or not isinstance(node_id, str)
            or node_id == ""
        ):
            raise ValueError("graph_id and node_id must be non-empty strings")
        if self.ctx.substrate is None:
            return _error("not_configured", "substrate is not configured")
        tool = GRAPH_ACTIONS[action]
        payload: dict[str, Any] = {
            "graph_id": graph_id,
            "node_id": node_id,
            "session_id": f"grok-bot:{surface or 'lead'}:{graph_id}",
        }
        if action == "complete":
            payload["result"] = {
                "summary": note or "completed via Omega Prime lead pack",
                "tests_pass": False,
            }
        result = self.ctx.substrate.call_tool(tool, payload, timeout=10)
        if not isinstance(result, dict) or result.get("error"):
            reason = (
                result.get("reason", f"{tool} failed")
                if isinstance(result, dict)
                else f"{tool} failed"
            )
            code = (
                result.get("error", "upstream_error")
                if isinstance(result, dict)
                else "upstream_error"
            )
            return _error(str(code), str(reason))
        return {"ok": True, "action": action, "substrate": result.get("content")}

    def bus_start(
        self,
        runtime: str,
        goal: str,
        provider: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start one agent-bus job. Passthrough to the injected bus."""
        if (
            not isinstance(runtime, str)
            or runtime == ""
            or not isinstance(goal, str)
            or goal == ""
        ):
            raise ValueError("runtime and goal must be non-empty strings")
        if self.ctx.bus is None:
            return _error("not_configured", "agent bus is not configured")
        result = self.ctx.bus.start_job(runtime, goal, provider, idempotency_key)
        if not isinstance(result, dict) or result.get("error"):
            reason = (
                result.get("reason", "agent bus refused the job")
                if isinstance(result, dict)
                else "agent bus refused the job"
            )
            code = (
                result.get("error", "upstream_error")
                if isinstance(result, dict)
                else "upstream_error"
            )
            return _error(str(code), str(reason))
        body = result.get("body") or {}
        if isinstance(body, dict):
            body.pop("wsUrl", None)
        return {"ok": True, "job": body}


def _load_ownership(text: str) -> list[tuple[str, str]]:
    """Parse `pattern:`/`owner:` pairs. Last match wins at resolve time."""
    rules: list[tuple[str, str]] = []
    pattern: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped.startswith("pattern:"):
            pattern = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("owner:") and pattern is not None:
            rules.append((pattern, stripped.split(":", 1)[1].strip().strip("\"'")))
            pattern = None
    return rules


def _resolve_owner(path: str, rules: list[tuple[str, str]]) -> str | None:
    owner: str | None = None
    candidate = PurePosixPath(path)
    for pattern, name in rules:
        if pattern in ("**", "**/*"):
            matched = True
        else:
            try:
                matched = candidate.match(pattern)
            except ValueError:
                matched = False
            if not matched:
                matched = fnmatch.fnmatch(str(candidate), pattern)
        if matched:
            owner = name
    return owner


def _declared_skills(prompt_path: Path) -> list[str]:
    """Skill names declared via `<skill path="skills/.../SKILL.md">`."""
    if not prompt_path.is_file():
        return []
    names = []
    for path in re.findall(
        r'<skill path="skills/([^"]+)/SKILL\.md"',
        prompt_path.read_text(encoding="utf-8"),
    ):
        name = path.split("/")[-1]
        if name not in names:
            names.append(name)
    return names


def register_lead_tools(registry: ToolRegistry, client: LeadClient) -> list[str]:
    """Register the 16 lead tools. State-changing tools require approval."""

    def _wrap(fn: Callable, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}

    handlers: dict[str, Callable[..., Any]] = {
        "lead_brief": lambda graph_id=None, task_id=None, refresh=False: _wrap(
            client.brief, graph_id=graph_id, task_id=task_id, refresh=refresh
        ),
        "lead_docs_search": lambda query, limit=8, repo=None: _wrap(
            client.docs_search, query, limit=limit, repo=repo
        ),
        "lead_memory_retain": lambda content, receipt_path=None, source=None, tags=None, graph_id=None, task_id=None: (
            _wrap(
                client.memory_retain,
                content,
                receipt_path=receipt_path,
                source=source,
                tags=tags,
                graph_id=graph_id,
                task_id=task_id,
            )
        ),
        "lead_memory_recall": lambda query, limit=8, include_shared=True, banks=None: (
            _wrap(
                client.memory_recall,
                query,
                limit=limit,
                include_shared=include_shared,
                banks=banks,
            )
        ),
        "lead_ownership_resolve": lambda paths: _wrap(client.ownership_resolve, paths),
        "lead_receipt_check": lambda receipt=None, receipt_path=None, bot=None, strict=True: (
            _wrap(
                client.receipt_check,
                receipt=receipt,
                receipt_path=receipt_path,
                bot=bot,
                strict=strict,
            )
        ),
        "lead_event_emit": lambda kind, payload=None, graph_id=None, task_id=None: (
            _wrap(
                client.event_emit,
                kind,
                payload=payload,
                graph_id=graph_id,
                task_id=task_id,
            )
        ),
        "lead_doctor": lambda action="check", agent_uuid=None, prompt_sha256=None, installed_skills=None: (
            _wrap(
                client.doctor,
                action=action,
                agent_uuid=agent_uuid,
                prompt_sha256=prompt_sha256,
                installed_skills=installed_skills,
            )
        ),
        "lead_render_prompt": lambda: _wrap(client.render_prompt),
        "lead_intake_next": lambda origin=None: _wrap(
            client.intake_next, origin=origin
        ),
        "lead_intake_ack": lambda intake_id, status, graph_id=None, message=None, links=None: (
            _wrap(
                client.intake_ack,
                intake_id,
                status,
                graph_id=graph_id,
                message=message,
                links=links,
            )
        ),
        "lead_graph_register": lambda graph_id, repo, nodes=None: _wrap(
            client.graph_register, graph_id, repo, nodes=nodes
        ),
        "lead_graph_state": lambda graph_id, node_id, action, surface=None, note=None: (
            _wrap(
                client.graph_state,
                graph_id,
                node_id,
                action,
                surface=surface,
                note=note,
            )
        ),
        "lead_bus_start": lambda runtime, goal, provider=None, idempotency_key=None: (
            _wrap(
                client.bus_start,
                runtime,
                goal,
                provider=provider,
                idempotency_key=idempotency_key,
            )
        ),
        "lead_bus_wait": lambda job_id, timeout_sec=180, poll_sec=2: _wrap(
            client.bus_wait, job_id, timeout_sec=timeout_sec, poll_sec=poll_sec
        ),
        "lead_roster_status": lambda: _wrap(client.roster_status),
    }
    if set(handlers) != set(LEAD_TOOL_NAMES):
        raise RuntimeError("Lead tool handlers drifted from LEAD_TOOL_NAMES")
    approvals = {
        "lead_intake_next",
        "lead_intake_ack",
        "lead_graph_register",
        "lead_graph_state",
        "lead_bus_start",
        "lead_memory_retain",
        "lead_event_emit",
        "lead_doctor",
    }
    for name in LEAD_TOOL_NAMES:
        description, parameters = _SCHEMAS[name]
        registry.register(
            name,
            description,
            parameters,
            handlers[name],
            requires_approval=(name in approvals),
        )
    return list(LEAD_TOOL_NAMES)


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "lead_brief": (
        "Seat brief: recall, packs, intake, reminders. Read-only.",
        _object(
            {
                "graph_id": _string("Graph id for recall scope."),
                "task_id": _string("Task id for recall scope."),
                "refresh": {"type": "boolean", "description": "Bypass the cache."},
            },
            [],
        ),
    ),
    "lead_docs_search": (
        "Search the docs index. Read-only.",
        _object(
            {
                "query": _string("Search text."),
                "limit": {"type": "integer"},
                "repo": _string("Repo short name."),
            },
            ["query"],
        ),
    ),
    "lead_memory_retain": (
        "Keep one memory entry. Needs receipt_path or source. Requires approval.",
        _object(
            {
                "content": _string("Entry text."),
                "receipt_path": _string("Evidence path."),
                "source": _string("Evidence source."),
                "tags": {"type": "array", "items": {"type": "string"}},
                "graph_id": _string("Graph id."),
                "task_id": _string("Task id."),
            },
            ["content"],
        ),
    ),
    "lead_memory_recall": (
        "Recall memory banks matching a query. Read-only.",
        _object(
            {
                "query": _string("Search text."),
                "limit": {"type": "integer"},
                "include_shared": {"type": "boolean"},
                "banks": {"type": "array", "items": {"type": "string"}},
            },
            ["query"],
        ),
    ),
    "lead_ownership_resolve": (
        "Resolve path owners from ownership.yaml. Read-only.",
        _object({"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"]),
    ),
    "lead_receipt_check": (
        "Validate a receipt dict or file. Read-only; never raises.",
        _object(
            {
                "receipt": {"type": "object"},
                "receipt_path": _string("Path under root."),
                "bot": _string("Bot id."),
                "strict": {"type": "boolean"},
            },
            [],
        ),
    ),
    "lead_event_emit": (
        "Append a lead event; forward when configured. Requires approval.",
        _object(
            {
                "kind": _string("Event kind."),
                "payload": {"type": "object"},
                "graph_id": _string("Graph id."),
                "task_id": _string("Task id."),
            },
            ["kind"],
        ),
    ),
    "lead_doctor": (
        "Check, register, or repair seat integrity. Requires approval.",
        _object(
            {
                "action": _string("check, register, install_prompt, or repair."),
                "agent_uuid": _string("Seat agent uuid."),
                "prompt_sha256": _string("Installed prompt sha."),
                "installed_skills": {"type": "array", "items": {"type": "string"}},
            },
            [],
        ),
    ),
    "lead_render_prompt": (
        "Assemble the seat prompt in an exported tree. Read-only.",
        _object({}, []),
    ),
    "lead_intake_next": (
        "Claim the oldest open work order. Requires approval.",
        _object({"origin": _string("Origin filter.")}, []),
    ),
    "lead_intake_ack": (
        "Ack one order; notify a GitHub link when present. Requires approval.",
        _object(
            {
                "intake_id": _string("Order id."),
                "status": _string("accepted, rejected, in_progress, or done."),
                "graph_id": _string("Graph id."),
                "message": _string("Status note."),
                "links": {"type": "array", "items": {"type": "string"}},
            },
            ["intake_id", "status"],
        ),
    ),
    "lead_graph_register": (
        "Register a planning graph. Requires approval.",
        _object(
            {
                "graph_id": _string("Graph id."),
                "repo": _string("Repo."),
                "nodes": {"type": "array", "items": {"type": "object"}},
            },
            ["graph_id", "repo"],
        ),
    ),
    "lead_graph_state": (
        "Claim, release, or complete a graph node. Requires approval.",
        _object(
            {
                "graph_id": _string("Graph id."),
                "node_id": _string("Node id."),
                "action": _string("claim, release, or complete."),
                "surface": _string("Surface name."),
                "note": _string("Completion note."),
            },
            ["graph_id", "node_id", "action"],
        ),
    ),
    "lead_bus_start": (
        "Start one agent-bus job. Requires approval.",
        _object(
            {
                "runtime": _string("Runtime name."),
                "goal": _string("Job goal."),
                "provider": _string("Provider."),
                "idempotency_key": _string("Key."),
            },
            ["runtime", "goal"],
        ),
    ),
    "lead_bus_wait": (
        "Wait for one bus job. Read-only.",
        _object(
            {
                "job_id": _string("Job id."),
                "timeout_sec": {"type": "integer"},
                "poll_sec": {"type": "integer"},
            },
            ["job_id"],
        ),
    ),
    "lead_roster_status": (
        "Registered packs, absorbed desk seats, intake queue. Read-only.",
        _object({}, []),
    ),
}


__all__ = [
    "LEAD_TOOL_NAMES",
    "EventStore",
    "IntakeStore",
    "LeadClient",
    "LeadContext",
    "RosterStore",
    "register_lead_tools",
]

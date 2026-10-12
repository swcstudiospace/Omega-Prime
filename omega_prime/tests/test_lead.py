"""Phase 26-01: lead core+lead tool families behind tmp stores and fakes."""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from omega_prime.credentials.redact import REDACTED
from omega_prime.memory.store import MemoryStore
from omega_prime.receipts import append_execution
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.lead import (
    LEAD_TOOL_NAMES,
    EventStore,
    IntakeStore,
    LeadClient,
    LeadContext,
    RosterStore,
    register_lead_tools,
)
from omega_prime.tools.registry import ToolRegistry

ASSEMBLE_STUB = """#!/bin/bash
mkdir -p omega_prime/prompts-assembled
printf '<seat>omega-prime-test</seat>' > omega_prime/prompts-assembled/OMEGA_PRIME.xml
"""

SEAT_PROMPT = """<agent id="bot-00-omega-prime">
<skill path="skills/lead-pack/SKILL.md" />
<skill path="skills/debugging/SKILL.md" />
</agent>
"""

OWNERSHIP = """version: 1
bots:
  bot-00-omega-prime:
    name: Omega Prime
paths:
  - pattern: "**"
    owner: bot-00-omega-prime
  - pattern: "omega_prime/skills/*"
    owner: bot-99-guest
"""

# Built from fragments so no complete credential shape sits in source; the
# redaction detector still sees the joined value at runtime.
FAKE_KEY = "sk-" + "abcdefgh" + "1234"


class FakeSubstrate:
    def __init__(self, content=None, error=None):
        self.calls: list[tuple] = []
        self.content = content if content is not None else {"ok": True}
        self.error = error

    def call_tool(self, name, payload, timeout=10):
        self.calls.append((name, payload, timeout))
        if self.error:
            return {"error": self.error[0], "reason": self.error[1]}
        return {"content": self.content}

    def emit(self, event):
        self.calls.append(("emit", event))
        if self.error:
            return {"error": self.error[0], "reason": self.error[1]}
        return {"body": {"accepted": True}}


class FakeBus:
    def __init__(self):
        self.calls: list[tuple] = []

    def start_job(self, runtime, goal, provider, key):
        self.calls.append(("start", runtime, goal, provider, key))
        return {"body": {"job_id": "job-1", "wsUrl": "wss://x"}}

    def wait_job(self, job_id, timeout_sec, poll_sec):
        self.calls.append(("wait", job_id, timeout_sec, poll_sec))
        return {"body": {"job_id": job_id, "state": "done"}}


def _ctx(tmp_path: Path, **overrides) -> LeadContext:
    root = tmp_path / "root"
    (root / "omega_prime" / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "omega_prime" / "prompts" / "bot-00-omega-prime.xml").write_text(
        SEAT_PROMPT, encoding="utf-8"
    )
    (root / "omega_prime" / "ownership.yaml").write_text(OWNERSHIP, encoding="utf-8")
    script = root / "omega_prime" / "scripts" / "assemble-prompts.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    script.write_text(ASSEMBLE_STUB, encoding="utf-8")
    script.chmod(0o755)
    base: dict[str, Any] = dict(
        root=root,
        memory=MemoryStore(tmp_path / "memory"),
        intake=IntakeStore(tmp_path / "intake.json"),
        roster=RosterStore(tmp_path / "roster.json"),
        events=EventStore(tmp_path / "events.ndjson"),
    )
    base.update(overrides)
    return LeadContext(**base)


def _good_receipt() -> dict:
    return {
        "commands": [{"cmd": "true", "exit_code": 0}],
        "claims": [{"claim": "done", "evidence_command_index": 0}],
        "unverified": [],
    }


def test_intake_claim_ack_round_trip(tmp_path: Path):
    client = LeadClient(_ctx(tmp_path))
    intake = client.ctx.intake
    assert intake is not None
    assert client.intake_next()["work_order"] is None
    first = intake.submit(
        "fix login", origin="github", links=["https://github.com/o/r/issues/7"]
    )
    intake.submit("polish copy", origin="local")
    claimed = client.intake_next()
    assert claimed["work_order"]["intake_id"] == first["intake_id"]
    assert claimed["work_order"]["status"] == "in_progress"
    assert claimed["queue"] == {"in_progress": 1, "open": 1}
    assert "ORIGINAL" in claimed["next"]
    second = client.intake_next(origin="local")["work_order"]
    assert second["ask"] == "polish copy"

    notes: list[tuple] = []

    def _notify(url: str, body: dict) -> bool:
        notes.append((url, body))
        return True

    client.ctx.notify = _notify
    acked = client.intake_ack(
        first["intake_id"],
        "done",
        graph_id="g-1",
        message="shipped",
        links=["https://github.com/o/r/issues/7"],
    )
    assert acked["ok"] is True
    assert acked["intake"]["status"] == "done"
    assert acked["notify"]["delivered"] is True
    assert "Graph ID: `g-1`" in notes[0][1]
    assert "intake `in-" in notes[0][1]

    local = client.intake_ack(second["intake_id"], "done")
    assert local["notify"] == {"delivered": False, "reason": "origin has no callback"}
    assert "not_found" in client.intake_ack("in-missing", "done")["error"]

    store = intake
    stranded = store.submit("stuck work")
    store.next(None, "gone-worker", now=1000.0)
    stuck = store.get(stranded["intake_id"])
    assert stuck is not None and stuck["status"] == "in_progress"
    assert store.next(None, "new-worker", now=1000.0) is None
    reclaimed = store.next(None, "new-worker", now=1000.0 + 3601.0)
    assert reclaimed is not None
    assert reclaimed["intake_id"] == stranded["intake_id"]
    assert store.release(stranded["intake_id"]) is True
    reopened = store.get(stranded["intake_id"])
    assert reopened is not None and reopened["status"] == "open"
    assert store.release("in-missing") is False


def test_graph_and_bus_passthrough_and_unconfigured(tmp_path: Path):
    substrate = FakeSubstrate(content={"graph": "ok"})
    bus = FakeBus()
    client = LeadClient(_ctx(tmp_path, substrate=substrate, bus=bus))
    registered = client.graph_register(
        "g-1", "o/r", [{"node_id": "n1", "linear_id": "L-2"}]
    )
    assert registered == {"ok": True, "graph_id": "g-1", "substrate": {"graph": "ok"}}
    name, payload, timeout = substrate.calls[0]
    assert name == "graph_register" and timeout == 10
    assert payload["nodes"] == [
        {"node_id": "n1", "linear_identifier": "L-2", "state": "open"}
    ]

    done = client.graph_state("g-1", "n1", "complete", note="all green")
    assert done["action"] == "complete"
    assert substrate.calls[1][1]["result"] == {
        "summary": "all green",
        "tests_pass": False,
    }
    assert substrate.calls[1][1]["session_id"] == "grok-bot:lead:g-1"

    started = client.bus_start("local", "run tests", provider="p", idempotency_key="k")
    assert started == {"ok": True, "job": {"job_id": "job-1"}}
    assert bus.calls[0] == ("start", "local", "run tests", "p", "k")
    waited = client.bus_wait("job-1")
    assert waited["job"]["state"] == "done" and waited["timed_out"] is False

    bare = LeadClient(_ctx(tmp_path))
    assert "not_configured" in bare.graph_register("g", "r")["error"]
    assert "not_configured" in bare.graph_state("g", "n", "claim")["error"]
    assert "not_configured" in bare.bus_start("r", "g")["error"]
    assert "not_configured" in bare.bus_wait("j")["error"]
    assert "not_configured" in bare.docs_search("q")["error"]


def test_roster_status_completeness(tmp_path: Path):
    client = LeadClient(_ctx(tmp_path))
    assert client.roster_status() == {
        "packs": [],
        "intake_queue": {},
        "complete": False,
        "absorbed_seats": [],
    }
    roster = client.ctx.roster
    assert roster is not None
    intake = client.ctx.intake
    assert intake is not None
    roster.register("lead", {"version": 1, "tools": list(LEAD_TOOL_NAMES)})
    intake.submit("ask")
    status = client.roster_status()
    assert status["complete"] is True
    assert status["packs"][0]["pack"] == "lead"
    assert len(status["packs"][0]["tools"]) == 16
    assert status["intake_queue"] == {"open": 1}
    assert status["absorbed_seats"] == []


def test_roster_status_absorbed_seats_from_default_json(tmp_path: Path):
    client = LeadClient(_ctx(tmp_path))
    roster = (
        Path(client.ctx.root) / "omega_prime" / "grokbot" / "rosters" / "default.json"
    )
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(
        json.dumps(
            {
                "bot_id": "bot-00-omega-prime",
                "absorbed_seats": {
                    "LEAD": {"bot_id": "bot-00-programming-lead", "pack": "lead"},
                    "SYSTEMS": {"bot_id": "bot-01-systems-backend", "pack": "systems"},
                    "WEB": {"bot_id": "bot-02-web-edge", "pack": "web"},
                    "ANDROID": {"bot_id": "bot-03-android", "pack": "mobile"},
                    "IOS": {"bot_id": "bot-04-ios", "pack": "mobile"},
                    "INFRA": {"bot_id": "bot-05-infrastructure", "pack": "infra"},
                    "QUALITY": {"bot_id": "bot-06-quality-security", "pack": "quality"},
                },
            }
        ),
        encoding="utf-8",
    )
    status = client.roster_status()
    assert [row["seat"] for row in status["absorbed_seats"]] == [
        "LEAD",
        "SYSTEMS",
        "WEB",
        "ANDROID",
        "IOS",
        "INFRA",
        "QUALITY",
    ]
    assert {row["absorbed_pack_of"] for row in status["absorbed_seats"]} == {
        "bot-00-omega-prime"
    }
    assert all(
        row["registered_gateway_seat"] is False for row in status["absorbed_seats"]
    )
    assert status["packs"] == []


def test_doctor_register_install_and_check(tmp_path: Path):
    registry = ToolRegistry()
    ctx = _ctx(tmp_path, registry=registry)
    client = LeadClient(ctx)
    register_lead_tools(registry, client)

    assert "invalid_args" in client.doctor("register")["error"]
    assert client.doctor("register", agent_uuid="u-1")["registered"] == ["lead"]
    installed = client.doctor("install_prompt")
    assert (
        installed["ok"] is True
        and installed["prompt"] == "<seat>omega-prime-test</seat>"
    )
    import hashlib

    sha = hashlib.sha256(b"<seat>omega-prime-test</seat>").hexdigest()
    assert installed["sha256"] == sha

    check = client.doctor(
        "check", prompt_sha256=sha, installed_skills=["lead-pack", "debugging"]
    )
    assert check["checks"]["prompt"]["green"] is True
    assert check["checks"]["skills"] == {
        "green": True,
        "declared": ["lead-pack", "debugging"],
        "missing": [],
    }
    assert check["checks"]["memory"]["green"] is True
    assert check["checks"]["tools"] == {"green": True, "pack": 16, "live": 16}
    assert check["checks"]["roster"]["green"] is True
    assert check["checks"]["substrate"]["green"] is False
    assert check["green"] is False

    bare = client.doctor("check")
    assert bare["checks"]["prompt"]["green"] is False
    assert bare["checks"]["skills"]["missing"] == ["lead-pack", "debugging"]


def test_render_prompt_refuses_placeholders(tmp_path: Path):
    ctx = _ctx(tmp_path)
    script = Path(ctx.root) / "omega_prime" / "scripts" / "assemble-prompts.sh"
    script.write_text(
        "#!/bin/bash\nmkdir -p omega_prime/prompts-assembled\nprintf '{{X}}' > omega_prime/prompts-assembled/OMEGA_PRIME.xml\n",
        encoding="utf-8",
    )
    rendered = LeadClient(ctx).render_prompt()
    assert "placeholders" in rendered["error"]
    assert (
        "not_configured"
        in LeadClient(LeadContext(root=tmp_path / "empty")).render_prompt()["error"]
    )


def test_render_prompt_skips_runtime_state(tmp_path: Path):
    ctx = _ctx(tmp_path)
    package = Path(ctx.root) / "omega_prime"
    state = package / "state"
    state.mkdir()
    (state / "events.ndjson").write_text('{"seq": 1}\n', encoding="utf-8")
    if hasattr(os, "mkfifo"):
        # A named pipe cannot be copied: copying live state would fail here.
        os.mkfifo(state / "live.pipe")
    (package / "sessions.db").write_bytes(b"runtime")
    script = package / "scripts" / "assemble-prompts.sh"
    script.write_text(
        "#!/bin/bash\n"
        "mkdir -p omega_prime/prompts-assembled\n"
        "seen=clean\n"
        "[ -e omega_prime/state ] && seen=state\n"
        "[ -e omega_prime/sessions.db ] && seen=db\n"
        "[ -e omega_prime/ownership.yaml ] || seen=missing-input\n"
        "printf '<seat>%s</seat>' \"$seen\" > omega_prime/prompts-assembled/OMEGA_PRIME.xml\n",
        encoding="utf-8",
    )
    rendered = LeadClient(ctx).render_prompt()
    assert rendered.get("prompt") == "<seat>clean</seat>", rendered
    assert (state / "events.ndjson").read_text(encoding="utf-8") == '{"seq": 1}\n'


def test_memory_retain_and_recall(tmp_path: Path):
    client = LeadClient(_ctx(tmp_path))
    assert "evidence_required" in client.memory_retain("note")["error"]
    assert (
        "secret_refused"
        in client.memory_retain(f"key {FAKE_KEY}", source="chat")["error"]
    )
    kept = client.memory_retain(
        "desk uses receipts", source="chat", tags=["core"], graph_id="g-1"
    )
    assert kept == {"ok": True, "bank": "omega-prime-lead"}
    recalled = client.memory_recall("receipts")
    assert recalled["banks"] == ["omega-prime-lead", "omega-prime-desk"]
    assert any(
        "desk uses receipts" in entry for entry in recalled["results"][0]["entries"]
    )
    assert client.memory_recall("nothing-here")["results"][0]["entries"] == []
    assert "not_configured" in LeadClient(LeadContext()).memory_recall("q")["error"]


def test_receipt_check_paths(tmp_path: Path, monkeypatch: Any):
    monkeypatch.setenv("OMEGA_PRIME_COMMAND_LOG", str(tmp_path / "command-log.jsonl"))
    append_execution("true", 0, "", cwd=str(tmp_path))
    ctx = _ctx(tmp_path)
    client = LeadClient(ctx)
    assert client.receipt_check(_good_receipt()) == {
        "ok": True,
        "bot": "bot-00-omega-prime",
    }
    bad = client.receipt_check(
        {
            "commands": [],
            "claims": [{"claim": "x", "evidence_command_index": 5}],
            "unverified": [],
        }
    )
    assert bad["ok"] is False and bad["problems"]
    leaked = dict(_good_receipt(), unverified=["token" + "=abc123"])
    assert client.receipt_check(leaked)["problems"] == [
        "receipt contains a credential shape (PD-4)"
    ]
    assert "invalid_args" in client.receipt_check()["error"]
    assert "not_found" in client.receipt_check(receipt_path="nope.json")["error"]
    (Path(ctx.root) / "r.json").write_text("not json", encoding="utf-8")
    assert "invalid_receipt" in client.receipt_check(receipt_path="r.json")["error"]
    (Path(ctx.root) / "good.json").write_text(
        json.dumps(_good_receipt()), encoding="utf-8"
    )
    assert client.receipt_check(receipt_path="good.json")["ok"] is True


def test_ownership_last_match_wins_and_unowned(tmp_path: Path):
    client = LeadClient(_ctx(tmp_path))
    result = client.ownership_resolve(
        ["omega_prime/agent/loop.py", "omega_prime/skills/x/SKILL.md"]
    )
    assert result["results"][0] == {
        "path": "omega_prime/agent/loop.py",
        "owner": "bot-00-omega-prime",
        "mine": True,
        "unowned": False,
    }
    assert result["results"][1]["owner"] == "bot-99-guest"
    assert result["results"][1]["mine"] is False
    (Path(client.ctx.root) / "omega_prime" / "ownership.yaml").write_text(
        "version: 1\npaths: []\n", encoding="utf-8"
    )
    lonely = client.ownership_resolve(["x.py"])
    assert lonely["results"][0]["unowned"] is True


def test_events_size_cap_audit_and_emit(tmp_path: Path):
    emitted: list[dict] = []

    class Emitter:
        def emit(self, event):
            emitted.append(event)
            return {"body": {"accepted": True}}

    client = LeadClient(_ctx(tmp_path, substrate=FakeSubstrate()))
    client.ctx.substrate.emit = Emitter().emit
    assert (
        "payload_too_large"
        in client.event_emit("note", payload={"blob": "x" * 5000})["error"]
    )
    outcome = client.event_emit("ticketed", payload={"ticket": "t-1"}, graph_id="g-1")
    assert outcome["ok"] is True and emitted and emitted[0]["kind"] == "ticketed"
    events = client.ctx.events
    assert events is not None
    rows = events.records()
    assert rows[0]["payload"] == {"ticket": "t-1"} and rows[0]["seq"] == 1

    token = "ghp_" + "x" * 36
    nested = client.event_emit("ticketed", payload={"meta": {"client_secret": token}})
    assert nested["ok"] is True
    assert events.records()[-1]["payload"] == {"meta": {"client_secret": REDACTED}}
    assert emitted[-1]["payload"] == {"meta": {"client_secret": REDACTED}}

    (tmp_path / "b").mkdir()
    local = LeadClient(_ctx(tmp_path / "b"))
    stored_only = local.event_emit("note")
    assert stored_only["emitted"] is False and stored_only["stored"]["seq"] == 1

    (tmp_path / "c").mkdir()
    failing = LeadClient(
        _ctx(tmp_path / "c", substrate=FakeSubstrate(error=("upstream_error", "down")))
    )
    assert failing.event_emit("note")["local_mirror"] is True


def test_brief_cache_docs_search_and_approvals(tmp_path: Path):
    ctx = _ctx(
        tmp_path,
        docs_index=lambda q, limit, repo: [
            {"content": "c" * 2000, "document": "d", "dataset_id": "k", "score": 0.9}
        ],
    )
    client = LeadClient(ctx)
    first = client.brief(task_id="t-1")
    assert first["reminders"] and first["loaded_packs"] == [] and "cached" not in first
    second = client.brief(task_id="t-1")
    assert second["cached"] is True
    assert client.brief(task_id="t-1", refresh=True).get("cached") is None
    docs = client.docs_search("receipts")
    assert len(docs["results"][0]["content"]) == 1500
    assert "not a repository fact" in docs["note"]

    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    register_lead_tools(registry, client)
    assert json.loads(registry.dispatch("lead_roster_status", {}))["complete"] is False
    assert json.loads(
        registry.dispatch("lead_bus_start", {"runtime": "r", "goal": "g"})
    ) == {"error": "approval required", "tool": "lead_bus_start"}
    assert "error" in json.loads(
        registry.dispatch("lead_memory_retain", {"content": ""})
    )
    assert log.approve("lead_event_emit", "ada").get("approved") is True
    assert json.loads(registry.dispatch("lead_event_emit", {"kind": "k"}))["ok"] is True


# -- Phase 75-01: file-authoritative intake transactions ----------------------
# Every case below uses real temporary files and public IntakeStore
# operations, inspecting committed JSON and fresh observers — never
# implementation internals, sleeps, or source text.


def test_warm_observer_sees_independent_submit(tmp_path: Path):
    path = tmp_path / "intake.json"
    observer = IntakeStore(path)  # initialized while the file is absent
    assert observer.get("in-missing") is None
    assert observer.counts() == {}
    submitted = IntakeStore(path).submit("warm order", origin="github")
    # No reconstruction: the original observer sees the order directly.
    seen = observer.get(submitted["intake_id"])
    assert seen is not None
    assert seen["ask"] == "warm order"
    assert seen["origin"] == "github"
    assert seen["status"] == "open"
    assert observer.counts() == {"open": 1}
    claimed = observer.next(None, "warm-worker", now=5000.0)
    assert claimed is not None
    assert claimed["intake_id"] == submitted["intake_id"]
    assert claimed["status"] == "in_progress"
    persisted = IntakeStore(path).get(submitted["intake_id"])
    assert persisted is not None and persisted["status"] == "in_progress"
    assert persisted["by"] == "warm-worker"


def test_stale_producers_both_persist_fifo(tmp_path: Path):
    path = tmp_path / "intake.json"
    stale_a = IntakeStore(path)
    stale_b = IntakeStore(path)  # same old snapshot: the file is absent
    first = stale_a.submit("first ask", origin="local")
    second = stale_b.submit("second ask", origin="github")
    fresh = IntakeStore(path)
    assert [r["intake_id"] for r in fresh.orders] == [
        first["intake_id"],
        second["intake_id"],
    ]
    assert [r["ask"] for r in fresh.orders] == ["first ask", "second ask"]
    assert [r["origin"] for r in fresh.orders] == ["local", "github"]
    assert fresh.counts() == {"open": 2}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert [r["intake_id"] for r in raw["orders"]] == [
        first["intake_id"],
        second["intake_id"],
    ]


def test_stale_ack_preserves_concurrent_claim_and_fields(tmp_path: Path):
    path = tmp_path / "intake.json"
    seed = IntakeStore(path)
    first = seed.submit("first")
    second = seed.submit("second")
    stale_acker = IntakeStore(path)  # snapshot before the foreign claim
    foreign = IntakeStore(path)
    claimed = foreign.next(None, "foreign-worker", now=8000.0)
    assert claimed is not None and claimed["intake_id"] == first["intake_id"]
    foreign.ack(second["intake_id"], {"message": "foreign note", "graph_id": "g-9"})
    acked = stale_acker.ack(first["intake_id"], {"status": "done"})
    assert acked is not None and acked["status"] == "done"
    fresh = IntakeStore(path)
    done = fresh.get(first["intake_id"])
    assert done is not None and done["status"] == "done"
    assert done["by"] == "foreign-worker"  # merge: omitted fields untouched
    assert done["claimed_at"] == 8000.0
    kept = fresh.get(second["intake_id"])
    assert kept is not None and kept["status"] == "open"
    assert kept["message"] == "foreign note"
    assert kept["graph_id"] == "g-9"


def test_ack_merge_and_explicit_empty_links(tmp_path: Path):
    path = tmp_path / "intake.json"
    record = IntakeStore(path).submit("merge me", links=["https://example.com/a"])
    warm = IntakeStore(path)  # snapshot before the independent update
    IntakeStore(path).ack(
        record["intake_id"],
        {
            "graph_id": "g-1",
            "links": ["https://example.com/a", "https://example.com/b"],
        },
    )
    merged = warm.ack(record["intake_id"], {"status": "done", "message": "shipped"})
    assert merged is not None
    assert merged["graph_id"] == "g-1"
    assert merged["links"] == ["https://example.com/a", "https://example.com/b"]
    assert merged["message"] == "shipped"
    assert merged["status"] == "done"
    cleared = IntakeStore(path).ack(record["intake_id"], {"links": []})
    assert cleared is not None and cleared["links"] == []
    assert cleared["status"] == "done"  # untouched by the links-only patch
    assert cleared["graph_id"] == "g-1"


def _claim_worker(
    path_str: str, barrier: Any, results: Any, by: str, now: float
) -> None:
    """Child-process entry: race for one open order, never hang the parent."""
    try:
        store = IntakeStore(path_str)
        barrier.wait(timeout=30)
        record = store.next(None, by, now=now)
        results.put(record["intake_id"] if record is not None else None)
    except Exception as exc:  # pragma: no cover - failure marker only
        results.put(f"error:{type(exc).__name__}")


def test_exclusive_claim_across_processes(tmp_path: Path):
    path = tmp_path / "intake.json"
    record = IntakeStore(path).submit("race me")
    count = 4
    barrier = multiprocessing.Barrier(count)
    results: Any = multiprocessing.Queue()
    workers = [
        multiprocessing.Process(
            target=_claim_worker,
            args=(str(path), barrier, results, f"worker-{index}", 9000.0),
        )
        for index in range(count)
    ]
    try:
        for worker in workers:
            worker.start()
        outcomes = [results.get(timeout=30) for _ in workers]
    finally:
        for worker in workers:
            worker.join(timeout=30)
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
    assert outcomes.count(record["intake_id"]) == 1  # exactly one winner
    assert outcomes.count(None) == count - 1
    fresh = IntakeStore(path)
    final = fresh.get(record["intake_id"])
    assert final is not None and final["status"] == "in_progress"
    assert fresh.counts() == {"in_progress": 1}


def test_concurrent_distinct_claims_without_duplication(tmp_path: Path):
    path = tmp_path / "intake.json"
    seed = IntakeStore(path)
    ids = [seed.submit(f"order-{index}")["intake_id"] for index in range(6)]
    barrier = threading.Barrier(6)
    won: list = []
    guard = threading.Lock()

    def _claim(by: str) -> None:
        store = IntakeStore(path)  # separate instance per claimant
        barrier.wait(timeout=30)
        record = store.next(None, by, now=9500.0)
        with guard:
            won.append(record["intake_id"] if record is not None else None)

    threads = [
        threading.Thread(target=_claim, args=(f"t-{index}",)) for index in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(w for w in won if w is not None) == sorted(ids)
    assert IntakeStore(path).counts() == {"in_progress": 6}


def test_stale_reclaim_never_reopens_renewed_claim(tmp_path: Path):
    path = tmp_path / "intake.json"
    seed = IntakeStore(path)
    record = seed.submit("leased")
    seed.next(None, "worker-a", now=1000.0)
    stale = IntakeStore(path)  # snapshot with the old claimed_at
    IntakeStore(path).ack(record["intake_id"], {"claimed_at": 5000.0})
    assert stale.reclaim_stale(max_age_seconds=3600.0, now=5010.0) == []
    current = IntakeStore(path).get(record["intake_id"])
    assert current is not None and current["status"] == "in_progress"
    assert current["claimed_at"] == 5000.0


def test_lease_boundary_expiry_and_missing_timestamp(tmp_path: Path):
    path = tmp_path / "intake.json"
    seed = IntakeStore(path)
    exact = seed.submit("exact boundary")
    seed.next(None, "worker-a", now=1000.0)
    assert IntakeStore(path).reclaim_stale(max_age_seconds=3600.0, now=4600.0) == []
    exact_state = IntakeStore(path).get(exact["intake_id"])
    assert exact_state is not None and exact_state["status"] == "in_progress"
    released = IntakeStore(path).reclaim_stale(max_age_seconds=3600.0, now=4601.0)
    assert released == [exact["intake_id"]]
    reopened_exact = IntakeStore(path).get(exact["intake_id"])
    assert reopened_exact is not None and reopened_exact["status"] == "open"
    timeless = seed.submit("no timestamp", origin="timer-a")
    seed.next("timer-a", "worker-b", now=2000.0)
    seed.ack(timeless["intake_id"], {"claimed_at": None})
    assert IntakeStore(path).reclaim_stale(max_age_seconds=3600.0, now=2001.0) == [
        timeless["intake_id"]
    ]
    non_numeric = seed.submit("bad timestamp", origin="timer-b")
    seed.next("timer-b", "worker-c", now=3000.0)
    seed.ack(non_numeric["intake_id"], {"claimed_at": "soon"})
    assert IntakeStore(path).reclaim_stale(max_age_seconds=3600.0, now=3001.0) == [
        non_numeric["intake_id"]
    ]


def test_reclaim_persists_without_matching_claim(tmp_path: Path):
    path = tmp_path / "intake.json"
    seed = IntakeStore(path)
    record = seed.submit("github job", origin="github")
    seed.next(None, "worker-a", now=1000.0)
    # No local order is open, but the expired github claim must still persist
    # as open through the origin-filtered lookup.
    assert IntakeStore(path).next("local", "worker-b", now=1000.0 + 7200.0) is None
    reopened = IntakeStore(path).get(record["intake_id"])
    assert reopened is not None and reopened["status"] == "open"
    assert reopened["by"] is None and reopened["claimed_at"] is None


def test_release_ack_statuses_and_missing_ids(tmp_path: Path):
    path = tmp_path / "intake.json"
    store = IntakeStore(path)
    record = store.submit("status tour")
    for status in ("accepted", "rejected", "in_progress", "done", "open"):
        updated = IntakeStore(path).ack(record["intake_id"], {"status": status})
        assert updated is not None and updated["status"] == status
    assert store.release(record["intake_id"]) is True
    reopened = IntakeStore(path).get(record["intake_id"])
    assert reopened is not None and reopened["status"] == "open"
    assert reopened["by"] is None and reopened["claimed_at"] is None
    assert store.release("in-missing") is False
    assert store.get("in-missing") is None
    assert store.ack("in-missing", {"status": "done"}) is None


def test_concurrent_readers_never_see_partial_json(tmp_path: Path):
    path = tmp_path / "intake.json"
    IntakeStore(path).submit("base")
    errors: list = []
    stop = threading.Event()

    def _read() -> None:
        while not stop.is_set():
            try:
                IntakeStore(path).counts()
                IntakeStore(path).get("in-nope")
            except Exception as exc:  # pragma: no cover - must stay empty
                errors.append(exc)

    readers = [threading.Thread(target=_read) for _ in range(4)]
    for reader in readers:
        reader.start()
    try:
        for index in range(50):
            IntakeStore(path).submit(f"order-{index}")
    finally:
        stop.set()
    for reader in readers:
        reader.join(timeout=30)
    assert errors == []
    assert sum(IntakeStore(path).counts().values()) == 51
    leftovers = [p for p in path.parent.iterdir() if p.name.startswith(".intake.json.")]
    assert leftovers == []


def test_failed_replace_preserves_bytes_and_reloads(tmp_path: Path, monkeypatch: Any):
    path = tmp_path / "intake.json"
    store = IntakeStore(path)
    first = store.submit("kept")
    before = path.read_bytes()

    def _boom(target: Any, text: str) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr("omega_prime.tools.lead.atomic_write_text", _boom)
    with pytest.raises(OSError, match="disk gone"):
        store.submit("lost")
    assert path.read_bytes() == before
    assert [p for p in path.parent.iterdir() if p.suffix == ".tmp"] == []
    monkeypatch.undo()
    # The failed append stays out of the file: the next transaction reloads
    # instead of committing failed cached state.
    second = store.submit("second")
    ids = [r["intake_id"] for r in IntakeStore(path).orders]
    assert ids == [first["intake_id"], second["intake_id"]]


def test_json_format_and_unicode_round_trip(tmp_path: Path):
    path = tmp_path / "intake.json"
    ask = "héllo wörld ✓絵文字"
    record = IntakeStore(path).submit(
        ask, origin="github", links=["https://example.com/ü"]
    )
    raw = path.read_bytes().decode("utf-8")
    assert raw.endswith("\n") and not raw.endswith("\n\n")
    assert "✓" in raw  # ensure_ascii=False, not escaped
    parsed = json.loads(raw)
    assert list(parsed.keys()) == ["orders"]
    expected = (
        json.dumps({"orders": IntakeStore(path).orders}, ensure_ascii=False, indent=2)
        + "\n"
    )
    assert raw == expected
    round_tripped = IntakeStore(path).get(record["intake_id"])
    assert round_tripped is not None and round_tripped["ask"] == ask


def test_corrupt_file_raises_and_stale_writer_preserves(tmp_path: Path):
    path = tmp_path / "intake.json"
    store = IntakeStore(path)
    store.submit("original")
    path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(ValueError):
        IntakeStore(path)
    with pytest.raises(ValueError):
        store.submit("new")
    with pytest.raises(ValueError):
        store.counts()
    assert path.read_bytes() == b"{corrupt"  # never clobbered by stale state
    path.write_text('{"orders": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="not an orders list"):
        IntakeStore(path)


def test_doctor_check_preserves_expected_sha(tmp_path: Path):
    registry = ToolRegistry()
    ctx = _ctx(tmp_path, registry=registry)
    client = LeadClient(ctx)
    register_lead_tools(registry, client)
    installed = client.doctor("install_prompt")
    sha = installed["sha256"]
    first = client.doctor(
        "check", prompt_sha256=sha, installed_skills=["lead-pack", "debugging"]
    )
    assert first["checks"]["prompt"]["green"] is True
    assert ctx.roster is not None
    assert ctx.roster.doctor_results()["lead"]["expected_prompt_sha256"] == sha
    repeat = client.doctor(
        "check", prompt_sha256=sha, installed_skills=["lead-pack", "debugging"]
    )
    assert repeat["checks"]["prompt"]["green"] is True
    assert ctx.roster.doctor_results()["lead"]["expected_prompt_sha256"] == sha
    # A context-free check is not green, yet the installed digest survives.
    bare = client.doctor("check")
    assert bare["checks"]["prompt"]["green"] is False
    assert ctx.roster.doctor_results()["lead"]["expected_prompt_sha256"] == sha
    # A wrong caller digest is not green and never becomes the expectation.
    wrong = client.doctor("check", prompt_sha256="0" * 64)
    assert wrong["checks"]["prompt"]["green"] is False
    assert ctx.roster.doctor_results()["lead"]["expected_prompt_sha256"] == sha
    again = client.doctor(
        "check", prompt_sha256=sha, installed_skills=["lead-pack", "debugging"]
    )
    assert again["checks"]["prompt"]["green"] is True


# -- Phase 75 re-review WR-04: roster saves through the shared atomic path ---
# Every roster mutation must reach the same atomic-write helper the intake
# store uses, so a crash mid-save never leaves a torn file that breaks every
# lead tool at construction time.


def test_roster_save_uses_shared_atomic_write(tmp_path: Path, monkeypatch: Any):
    import omega_prime.tools.lead as lead_mod

    path = tmp_path / "roster.json"
    calls: list[tuple[Any, Any]] = []
    real_atomic = lead_mod.atomic_write_text

    def _record(target: Any, text: str) -> None:
        calls.append((target, text))
        real_atomic(target, text)

    monkeypatch.setattr(lead_mod, "atomic_write_text", _record)
    store = RosterStore(path)
    store.register("lead-pack", {"version": "1"})
    store.doctor_result("lead-pack", {"green": True})
    assert len(calls) == 2
    assert all(target == path for target, _ in calls)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["packs"]["lead-pack"]["version"] == "1"
    assert persisted["doctor"]["lead-pack"] == {"green": True}
    assert RosterStore(path).packs["lead-pack"]["version"] == "1"


def test_roster_failed_save_preserves_bytes(tmp_path: Path, monkeypatch: Any):
    import omega_prime.tools.lead as lead_mod

    path = tmp_path / "roster.json"
    store = RosterStore(path)
    store.register("lead-pack", {"version": "1"})
    before = path.read_bytes()

    def _boom(target: Any, text: str) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(lead_mod, "atomic_write_text", _boom)
    with pytest.raises(OSError, match="disk gone"):
        store.register("other-pack", {"version": "2"})
    assert path.read_bytes() == before
    assert [p for p in path.parent.iterdir() if p.suffix == ".tmp"] == []


# -- Phase 75 re-review WR-05: torn trailing line is skipped, not fatal -----
# Boundary: only the LAST non-blank line may be torn (a crash mid-append
# leaves exactly that). Corruption in any earlier line still raises instead
# of silently dropping history.


def test_event_store_skips_torn_trailing_line(tmp_path: Path):
    path = tmp_path / "events.ndjson"
    store = EventStore(path)
    first = store.append({"kind": "one"})
    second = store.append({"kind": "two"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 999, "torn": ')
    rows = EventStore(path).records()
    assert [row["seq"] for row in rows] == [first["seq"], second["seq"]]
    rebuilt = EventStore(path)
    third = rebuilt.append({"kind": "three"})
    fourth = rebuilt.append({"kind": "four"})
    assert third["seq"] == second["seq"] + 1
    assert fourth["seq"] == third["seq"] + 1
    reopened = EventStore(path).records()
    assert [row["kind"] for row in reopened] == ["one", "two", "three", "four"]
    assert [row["seq"] for row in reopened] == [1, 2, 3, 4]


def test_event_store_torn_tail_does_not_eat_a_live_stores_append(tmp_path: Path):
    path = tmp_path / "events.ndjson"
    store = EventStore(path)
    store.append({"kind": "one"})
    # A crash in another writer tears the tail after this store loaded.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 999, "torn": ')
    store.append({"kind": "two"})
    store.append({"kind": "three"})
    assert [row["kind"] for row in EventStore(path).records()] == [
        "one",
        "two",
        "three",
    ]


def test_event_store_keeps_whole_record_missing_its_newline(tmp_path: Path):
    path = tmp_path / "events.ndjson"
    path.write_text('{"seq": 1, "kind": "one"}', encoding="utf-8")
    store = EventStore(path)
    store.append({"kind": "two"})
    assert [row["kind"] for row in EventStore(path).records()] == ["one", "two"]


def test_event_store_raises_on_earlier_corrupt_line(tmp_path: Path):
    path = tmp_path / "events.ndjson"
    store = EventStore(path)
    store.append({"kind": "one"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
    store.append({"kind": "two"})
    with pytest.raises(ValueError):
        EventStore(path).records()


def test_event_stores_sharing_a_path_never_mint_duplicate_seqs(tmp_path: Path):
    path = tmp_path / "events.ndjson"
    first = EventStore(path)
    second = EventStore(path)
    appended = [
        first.append({"kind": "a1"}),
        second.append({"kind": "b1"}),
        first.append({"kind": "a2"}),
        second.append({"kind": "b2"}),
    ]
    seqs = [row["seq"] for row in appended]
    assert len(set(seqs)) == len(seqs)
    reopened = EventStore(path).records()
    assert [row["kind"] for row in reopened] == ["a1", "b1", "a2", "b2"]
    assert [row["seq"] for row in reopened] == [1, 2, 3, 4]


def test_event_store_counts_records_when_last_lacks_a_seq(tmp_path: Path):
    path = tmp_path / "events.ndjson"
    path.write_text('{"kind": "one"}\n{"kind": "two"}\n', encoding="utf-8")
    record = EventStore(path).append({"kind": "three"})
    assert record["seq"] == 3


# -- Phase 75 re-review WR-06: secret refusal covers retained metadata ------
# The same detector that gates `content` also gates the assembled entry, so
# a credential in tags/source/graph metadata is refused, not retained.


def test_memory_retain_refuses_secrets_in_metadata(tmp_path: Path):
    client = LeadClient(_ctx(tmp_path))
    assert (
        "secret_refused"
        in client.memory_retain("clean note", source="chat", tags=[FAKE_KEY])["error"]
    )
    assert (
        "secret_refused"
        in client.memory_retain("clean note", source=f"chat {FAKE_KEY}")["error"]
    )
    assert (
        "secret_refused"
        in client.memory_retain("clean note", source="chat", graph_id=f"g-{FAKE_KEY}")[
            "error"
        ]
    )
    assert (
        "secret_refused"
        in client.memory_retain("clean note", source="chat", task_id=f"t-{FAKE_KEY}")[
            "error"
        ]
    )
    assert (
        "secret_refused"
        in client.memory_retain("clean note", receipt_path=f"r-{FAKE_KEY}.json")[
            "error"
        ]
    )
    kept = client.memory_retain(
        "clean note", source="chat", tags=["core"], graph_id="g-1"
    )
    assert kept == {"ok": True, "bank": "omega-prime-lead"}

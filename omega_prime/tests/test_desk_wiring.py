"""Phase 64 desk runtime wiring: shared stores, work root, served delegate.

Covers the DESK-01/02/03/08 acceptance surface: `lead_doctor` green on
memory/tools/substrate without credentials, one shared `MemoryStore`, desk
stores under `OMEGA_PRIME_STATE_DIR`, `--work-root` plumbing (coding jail at
the work root, roster/ownership lookups on the install root), and
`delegate_task` served with and without a provider env.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from omega_prime.agent.conversation_loop import Agent
from omega_prime.agent.delegate import delegate_task as run_delegate_task
from omega_prime.agent.model import ScriptedModel
from omega_prime.cron.desk_driver import DeskDriver
from omega_prime.cron.scheduler import (
    JobStore,
    list_desk_lead_passes,
    schedule_desk_lead_pass,
)
from omega_prime.mcp_server import (
    _DeskParent,
    default_registry,
    load_runtime,
    resolve_work_root,
)
from omega_prime.receipts import validate_receipt
from omega_prime.routines.desk_lead import desk_intake_path, run_lead_pass
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.lead import IntakeStore

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


def test_help_advertises_work_root():
    proc = subprocess.run(
        [sys.executable, "-m", "omega_prime.mcp_server", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0
    assert "--work-root" in proc.stdout


def test_lead_doctor_green_on_memory_tools_substrate_without_credentials(tmp_path):
    # `lead_doctor` is a gated tool (its register/repair actions mutate the
    # roster seat), so a host grants approval up front (`--approve`), the way
    # every other lead_doctor construction in this suite does. No credentials
    # are involved: memory/tools/substrate go green on the shared seams.
    log = ApprovalLog()
    log.approve("lead_doctor", "ada")
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=log,
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    result = json.loads(registry.dispatch("lead_doctor", {}))
    checks = result["checks"]
    assert checks["memory"]["green"] is True
    assert checks["tools"]["green"] is True
    assert checks["substrate"]["green"] is True


def test_shared_memory_store_identity(tmp_path):
    """The growth `memory` tool and the lead pack see one MemoryStore."""
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    kept = json.loads(
        registry.dispatch(
            "memory",
            {
                "action": "add",
                "target": "memory",
                "content": "[hindsight:omega-prime-lead] desk-wiring sentinel",
            },
        )
    )
    assert kept.get("success") is True, kept
    recall = json.loads(
        registry.dispatch("lead_memory_recall", {"query": "desk-wiring sentinel"})
    )
    assert "desk-wiring sentinel" in json.dumps(recall)


def test_desk_stores_live_under_state_dir(tmp_path):
    state = tmp_path / "state"
    log = ApprovalLog()
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        approval_log=log,
        env={"OMEGA_PRIME_STATE_DIR": str(state)},
    )
    assert log.approve("lead_event_emit", "ada").get("approved") is True
    assert log.approve("lead_doctor", "ada").get("approved") is True
    emitted = json.loads(registry.dispatch("lead_event_emit", {"kind": "note"}))
    # The substrate plane is absent in tests: the emission degrades loudly
    # (local_mirror) instead of pretending it was delivered.
    assert emitted.get("local_mirror") is True, emitted
    registered = json.loads(
        registry.dispatch(
            "lead_doctor", {"action": "register", "agent_uuid": "agent-1"}
        )
    )
    assert registered.get("ok") is True, registered
    assert (state / "events.ndjson").is_file()
    assert (state / "packs.json").is_file()
    # The cron desk pass shares this exact intake file (DESK-04 contract).
    assert desk_intake_path(ROOT, state_dir=str(state)) == state / "intake.json"


def test_delegate_without_provider_env_is_not_configured(tmp_path):
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    out = json.loads(registry.dispatch("delegate_task", {"goal": "do a thing"}))
    assert out == {"error": "not_configured: provider"}
    assert registry.runtime_bindings["delegate_parent"] is None


def test_delegate_parent_with_provider_env(tmp_path):
    env = {
        "XAI_API_KEY": "key-for-tests",
        "OMEGA_PRIME_DESK_CHILD_MODEL": "grok-4",
        "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
    }
    registry = default_registry(ROOT, tmp_path / "home", env=env)
    parent = registry.runtime_bindings["delegate_parent"]
    assert parent is not None
    assert parent.child_model is not None
    assert "read_file" in parent.tools
    assert "delegate_task" in parent.tools
    # Explicit provider selection also works, and an unknown one is loud.
    named = default_registry(
        ROOT,
        tmp_path / "home",
        env={
            "OMEGA_PRIME_DESK_CHILD_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "key-for-tests",
            "OMEGA_PRIME_DESK_CHILD_MODEL": "claude-test",
            "OMEGA_PRIME_STATE_DIR": str(tmp_path / "state"),
        },
    )
    assert named.runtime_bindings["delegate_parent"] is not None


def test_delegate_parent_shim_runs_a_scripted_child(tmp_path):
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    parent = _DeskParent(
        registry, ScriptedModel([{"role": "assistant", "content": "child-final"}])
    )
    raw = run_delegate_task(parent, "report the desk state")
    assert json.loads(raw)["summary"] == "child-final"


def test_work_root_jails_coding_tools_and_keeps_install_lookups(tmp_path):
    work = tmp_path / "foreign-repo"
    (work / "src").mkdir(parents=True)
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
        work_root=work,
    )
    written = json.loads(
        registry.dispatch("write_file", {"path": "src/mod.py", "content": "DESK = 1\n"})
    )
    assert written.get("bytes_written") == len("DESK = 1\n")
    assert (work / "src" / "mod.py").is_file()
    found = json.loads(registry.dispatch("search_text", {"query": "DESK = 1"}))
    assert "src/mod.py" in json.dumps(found)
    escaped = json.loads(
        registry.dispatch("read_file", {"path": "../omega_prime/ownership.yaml"})
    )
    assert "escapes the root" in escaped.get("error", "")
    # Ownership (and roster/policy) still resolve from the install root.
    owners = json.loads(
        registry.dispatch("lead_ownership_resolve", {"paths": ["omega_prime/x.py"]})
    )
    assert owners["results"][0]["owner"] == "bot-00-omega-prime"


def test_default_coding_jail_is_unchanged(tmp_path):
    install = tmp_path / "install"
    (install / "omega_prime").mkdir(parents=True)
    registry = default_registry(install, tmp_path / "home", env={})
    written = json.loads(
        registry.dispatch("write_file", {"path": "kept.txt", "content": "x"})
    )
    assert (install / "omega_prime" / "kept.txt").is_file()
    assert written.get("bytes_written") == 1


def test_load_runtime_work_root_contract(tmp_path):
    work = tmp_path / "work-root"
    work.mkdir()
    from_env = load_runtime(
        ROOT,
        tmp_path / "home",
        no_roster=True,
        env={"OMEGA_PRIME_WORK_ROOT": str(work)},
    )
    assert from_env.work_root == work
    assert from_env.parent is None
    from_flag = load_runtime(
        ROOT,
        tmp_path / "home",
        no_roster=True,
        work_root=work,
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    assert from_flag.work_root == work
    default = load_runtime(
        ROOT,
        tmp_path / "home",
        no_roster=True,
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    assert default.work_root == ROOT


def test_resolve_work_root_precedence(tmp_path):
    root = tmp_path / "root"
    assert resolve_work_root(root, {}) == root
    assert resolve_work_root(root, {"OMEGA_PRIME_WORK_ROOT": " /tmp/w "}) == Path(
        "/tmp/w"
    )
    assert resolve_work_root(root, {"OMEGA_PRIME_WORK_ROOT": ""}) == root
    assert resolve_work_root(root, {}, tmp_path / "explicit") == tmp_path / "explicit"


def test_desk_driver_runs_due_passes_without_manual_calls(tmp_path):
    """DESK-04: the driver ticks the shared JobStore file, serialized."""
    root = tmp_path / "install"
    store_path = root / "cron" / "jobs.json"
    intake_path = tmp_path / "state" / "intake.json"
    record = IntakeStore(intake_path).submit("add a banner to the readme")
    store = JobStore(store_path)
    schedule_desk_lead_pass(
        store, due_at=1000, interval_seconds=60, intake_path=intake_path
    )
    parent = Agent(model=ScriptedModel([]), tools={})
    parent.child_model = ScriptedModel(
        [{"role": "assistant", "content": "banner added; verified with a read"}]
    )
    driver = DeskDriver(root, parent=parent, work_root=tmp_path, clock=lambda: 1000.0)
    driver.tick_once()
    # The pass claimed, dispatched through the parent shim, and acked done —
    # on the same JobStore file the plain cron tick reads, without a manual
    # run_lead_pass call.
    after = IntakeStore(intake_path)
    done = after.get(record["intake_id"])
    assert done is not None and done["status"] == "done"
    ran = list_desk_lead_passes(JobStore(store_path))
    assert len(ran) == 1 and ran[0]["last_ran_at"] == 1000.0
    assert ran[0]["due_at"] == 1060.0
    # start() is idempotent while the worker lives; stop() is repeatable.
    driver.start()
    driver.stop(wait=True)
    driver.stop(wait=True)


def test_warm_registry_consumes_independent_intake_and_acks(tmp_path):
    """75-01: a runtime built before the producer commit still serves it.

    The registry (and its warm IntakeStore) is constructed first; a
    separate producer then commits an open order to the same
    state/intake.json. Dispatching /omega-desk on the original runtime
    with real caller doctor context must run a fully healthy workflow
    that returns that order with its original ask plus a persisted
    in-progress claim — never null/empty while the file holds the order.
    """
    state = tmp_path / "state"
    env = {"OMEGA_PRIME_STATE_DIR": str(state)}
    runtime = load_runtime(
        ROOT,
        tmp_path / "home",
        no_roster=True,
        approvals=[
            ("lead_doctor", "host"),
            ("lead_intake_next", "host"),
            ("lead_intake_ack", "host"),
        ],
        env=env,
    )
    registry = runtime.registry
    intake_path = state / "intake.json"
    assert desk_intake_path(ROOT, state_dir=str(state)) == intake_path
    # Prepare the real disposable seat: registration plus installed
    # prompt bytes and their digest, through the public handlers.
    registered = json.loads(
        registry.dispatch(
            "lead_doctor", {"action": "register", "agent_uuid": "warm-test-seat"}
        )
    )
    assert registered["registered"] == ["lead"]
    installed = json.loads(
        registry.dispatch("lead_doctor", {"action": "install_prompt"})
    )
    assert installed["prompt"] and isinstance(installed["sha256"], str)
    assert (
        installed["sha256"] == hashlib.sha256(installed["prompt"].encode()).hexdigest()
    )
    probe = json.loads(registry.dispatch("lead_doctor", {"action": "check"}))
    declared = probe["checks"]["skills"]["declared"]
    assert declared  # the real seat prompt names real skills
    doctor_context = {
        "prompt_sha256": installed["sha256"],
        "installed_skills": declared,
    }
    # The producer commits after the warm store exists; the runtime is
    # never rebuilt between producer and consumer.
    submitted = IntakeStore(intake_path).submit("warm desk order", origin="local")
    payload = json.loads(
        registry.dispatch(
            "omega_command",
            {"command": "/omega-desk", "doctor_context": doctor_context},
        )
    )
    assert payload["ok"] is True
    steps = {step["name"]: step for step in payload["result"]["steps"]}
    doctor_result = steps["lead_doctor"]["result"]
    assert doctor_result["green"] is True  # real seat, real context: healthy
    assert all(step["ok"] for step in payload["result"]["steps"])
    assert steps["lead_roster_status"]["result"]["intake_queue"] == {"open": 1}
    claimed = steps["lead_intake_next"]["result"]
    assert claimed["work_order"]["intake_id"] == submitted["intake_id"]
    assert claimed["work_order"]["ask"] == "warm desk order"
    assert claimed["work_order"]["status"] == "in_progress"
    assert claimed["queue"] == {"in_progress": 1}  # sparse map: no zero keys
    persisted = IntakeStore(intake_path).get(submitted["intake_id"])
    assert persisted is not None and persisted["status"] == "in_progress"
    # A real local effect, then acknowledgement through the public
    # registered handler — visible to an independent observer.
    scratch = tmp_path / "scratch" / "desk-proof.txt"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(f"{submitted['intake_id']}: did the thing\n", encoding="utf-8")
    acked = json.loads(
        registry.dispatch(
            "lead_intake_ack",
            {
                "intake_id": submitted["intake_id"],
                "status": "done",
                "message": "did the thing",
            },
        )
    )
    assert acked["ok"] is True
    assert acked["intake"]["status"] == "done"
    assert acked["intake"]["message"] == "did the thing"
    observer = IntakeStore(intake_path)
    done = observer.get(submitted["intake_id"])
    assert done is not None and done["status"] == "done"
    assert done["message"] == "did the thing"
    assert observer.counts() == {"done": 1}
    assert scratch.read_text(encoding="utf-8").startswith(submitted["intake_id"])
    # Reverse direction: a desk pass through a separately constructed
    # store completes the next order while this registry stays warm.
    second = IntakeStore(intake_path).submit("second desk order")
    separate = IntakeStore(intake_path)

    def _dispatch(ticket):
        with scratch.open("a", encoding="utf-8") as handle:
            handle.write(f"{ticket['ticket_id']}:{ticket['ask']}\n")
        receipt = {
            "commands": [{"cmd": "true", "exit_code": 0}],
            "claims": [{"claim": "done", "evidence_command_index": 0}],
            "unverified": [],
        }
        validate_receipt(receipt)
        return {"receipt": receipt}

    outcome = run_lead_pass(separate, _dispatch)
    assert [t["status"] for t in outcome["tickets"]] == ["done"]
    assert outcome["tickets"][0]["intake_id"] == second["intake_id"]
    roster = json.loads(registry.dispatch("lead_roster_status", {}))
    assert roster["intake_queue"] == {"done": 2}
    third = IntakeStore(intake_path).submit("third desk order")
    nxt = json.loads(registry.dispatch("lead_intake_next", {}))
    assert nxt["work_order"]["intake_id"] == third["intake_id"]
    assert nxt["work_order"]["ask"] == "third desk order"
    claimed_third = IntakeStore(intake_path).get(third["intake_id"])
    assert claimed_third is not None and claimed_third["status"] == "in_progress"
    lines = scratch.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == f"{submitted['intake_id']}: did the thing"
    assert lines[1].startswith("t-") and lines[1].endswith(":second desk order")


def test_typed_schedule_creation_is_atomic_with_concurrent_store_writer(
    tmp_path, monkeypatch
):
    from threading import Event, Thread

    path = tmp_path / "cron" / "jobs.json"
    scheduler_store = JobStore(path)
    concurrent_store = JobStore(path)
    original_save = scheduler_store._save
    entered_save = Event()
    release_save = Event()
    writer_started = Event()
    created = []

    def paused_save():
        entered_save.set()
        assert release_save.wait(timeout=5)
        original_save()

    monkeypatch.setattr(scheduler_store, "_save", paused_save)
    scheduler = Thread(
        target=lambda: created.append(
            schedule_desk_lead_pass(scheduler_store, due_at=100, interval_seconds=60)
        )
    )
    scheduler.start()
    assert entered_save.wait(timeout=5)

    def create_generic():
        writer_started.set()
        concurrent_store.schedule("concurrent", 200)

    writer = Thread(target=create_generic)
    writer.start()
    assert writer_started.wait(timeout=5)
    release_save.set()
    scheduler.join(timeout=5)
    writer.join(timeout=5)
    assert not scheduler.is_alive()
    assert not writer.is_alive()
    persisted = JobStore(path).jobs
    assert {job.get("kind") for job in persisted} == {None, "desk_lead_pass"}
    assert len(persisted) == 2

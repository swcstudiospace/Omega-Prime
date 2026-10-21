"""Phase 26-02: lead pass routine over the intake queue.

Phase 64 (DESK-04) adds the production wiring tests: the dispatch closure
through a fake parent shim, the explicit no-parent blocked receipt, and the
``desk_lead_pass`` cron job kind (serialization + no lock across the fake
model call, mirroring the heartbeat test patterns).
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from omega_prime.agent.conversation_loop import Agent
from omega_prime.agent.model import ScriptedModel
from omega_prime.cron.scheduler import (
    DESK_LEAD_KIND,
    JobStore,
    list_desk_lead_passes,
    run_desk_lead_passes,
    run_due_jobs,
    schedule_desk_lead_pass,
    tick_desk_lead_passes,
)
from omega_prime.receipts import validate_receipt
from omega_prime.routines.desk_lead import (
    desk_intake_path,
    make_dispatch,
    run_lead_pass,
)
from omega_prime.tools.lead import IntakeStore
from omega_prime.tools.todo import TodoStore, todo_read


def _receipt() -> dict:
    return {
        "commands": [{"cmd": "true", "exit_code": 0}],
        "claims": [{"claim": "done", "evidence_command_index": 0}],
        "unverified": [],
    }


def test_pass_shape_and_report(tmp_path):
    intake = IntakeStore(tmp_path / "intake.json")
    intake.submit("fix login")
    intake.submit("polish copy")
    todos = TodoStore()
    outcome = run_lead_pass(intake, lambda ticket: {"receipt": _receipt()}, todos=todos)
    assert [t["status"] for t in outcome["tickets"]] == ["done", "done"]
    assert len(outcome["receipts"]) == 2 and outcome["invalid"] == []
    assert outcome["report"].startswith("2/2 tickets done.")
    assert [t["status"] for t in todo_read(todos)["todos"]] == ["done", "done"]
    assert intake.counts() == {"done": 2}


def test_limit_bounds_the_batch(tmp_path):
    intake = IntakeStore(tmp_path / "intake.json")
    for ask in ("a", "b", "c"):
        intake.submit(ask)
    outcome = run_lead_pass(intake, lambda ticket: {"receipt": _receipt()}, limit=2)
    assert len(outcome["tickets"]) == 2
    assert intake.counts() == {"done": 2, "open": 1}


def test_no_dispatcher_blocks_and_reopens(tmp_path):
    intake = IntakeStore(tmp_path / "intake.json")
    record = intake.submit("fix login")
    outcome = run_lead_pass(intake)
    assert outcome["tickets"][0]["status"] == "blocked"
    assert outcome["tickets"][0]["reason"] == "no dispatcher"
    reopened = intake.get(record["intake_id"])
    assert reopened is not None and reopened["status"] == "open"
    assert "0/1 tickets done." in outcome["report"]


def test_missing_and_invalid_receipts_block(tmp_path):
    intake = IntakeStore(tmp_path / "intake.json")
    intake.submit("no receipt")
    intake.submit("bad receipt")
    calls = {"n": 0}

    def dispatch(ticket):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"note": "forgot the receipt"}
        return {
            "receipt": {
                "commands": [],
                "claims": [{"claim": "x", "evidence_command_index": 9}],
                "unverified": [],
            }
        }

    outcome = run_lead_pass(intake, dispatch)
    assert [t["status"] for t in outcome["tickets"]] == ["blocked", "blocked"]
    assert outcome["receipts"] == []
    assert len(outcome["invalid"]) == 1
    assert intake.counts() == {"open": 2}


# -- Phase 64 DESK-04: production dispatch closure --------------------------


def _fake_parent(summary: str = "changed the readme; verified with a read") -> Agent:
    """A fake runtime.parent shim: real child turns on a scripted model."""
    parent = Agent(model=ScriptedModel([]), tools={})
    parent.child_model = ScriptedModel([{"role": "assistant", "content": summary}])
    return parent


def test_end_to_end_intake_claim_dispatch_receipt_ack(tmp_path):
    intake = IntakeStore(tmp_path / "state" / "intake.json")
    record = intake.submit("add a banner to the readme")
    parent = _fake_parent()
    outcome = run_lead_pass(intake, make_dispatch(parent, tmp_path))
    ticket = outcome["tickets"][0]
    assert ticket["status"] == "done"
    assert outcome["invalid"] == []
    receipt = outcome["receipts"][0]["receipt"]
    validate_receipt(receipt)  # the receipt holds
    assert receipt["task_id"] == ticket["ticket_id"]
    assert receipt["bot"] == "bot-00-omega-prime"
    assert receipt["commands"][0]["exit_code"] == 0
    assert receipt["claims"][0]["evidence_command_index"] == 0
    # The ask reached the child turn.
    child_model = parent.child_model
    assert child_model is not None
    assert any(record["ask"] in json.dumps(messages) for messages in child_model.seen)
    done = intake.get(record["intake_id"])
    assert done is not None
    assert done["status"] == "done"
    assert done["by"] == "bot-00-omega-prime"
    assert done["message"] == f"{ticket['ticket_id']} done"
    assert intake.counts() == {"done": 1}


def test_no_parent_produces_explicit_blocked_receipt(tmp_path):
    intake = IntakeStore(tmp_path / "intake.json")
    record = intake.submit("fix login")
    outcome = run_lead_pass(intake, make_dispatch(None, tmp_path))
    ticket = outcome["tickets"][0]
    assert ticket["status"] == "blocked"
    assert "no parent agent shim" in ticket["reason"]
    blocked_receipt = ticket["receipt"]
    assert isinstance(blocked_receipt, dict)
    # An honest failure receipt holds its own shape.
    validate_receipt(blocked_receipt)
    assert blocked_receipt["commands"][0]["exit_code"] == 1
    assert blocked_receipt["claims"][0]["expects_failure"] is True
    assert outcome["receipts"] == []  # never read as done
    reopened = intake.get(record["intake_id"])
    assert reopened is not None
    assert reopened["status"] == "open"
    assert intake.counts() == {"open": 1}


def test_failed_child_is_blocked_not_done(tmp_path):
    intake = IntakeStore(tmp_path / "intake.json")
    record = intake.submit("fix login")
    # A shim whose child model is missing: delegate refuses explicitly.
    parent = Agent(model=ScriptedModel([]), tools={})
    parent.child_model = None
    outcome = run_lead_pass(intake, make_dispatch(parent, tmp_path))
    ticket = outcome["tickets"][0]
    assert ticket["status"] == "blocked"
    assert "child agent failed" in ticket["reason"]
    validate_receipt(ticket["receipt"])
    assert ticket["receipt"]["commands"][0]["exit_code"] == 1
    assert outcome["receipts"] == []
    reopened = intake.get(record["intake_id"])
    assert reopened is not None
    assert reopened["status"] == "open"


def test_desk_intake_path_prefers_the_state_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("OMEGA_PRIME_STATE_DIR", raising=False)
    assert (
        desk_intake_path(tmp_path) == tmp_path / "omega_prime" / "state" / "intake.json"
    )
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path / "state"))
    assert desk_intake_path(tmp_path) == tmp_path / "state" / "agent" / "intake.json"
    assert (
        desk_intake_path(tmp_path, state_dir=tmp_path / "other")
        == tmp_path / "other" / "agent" / "intake.json"
    )
    # An explicit agent folder wins over the state root.
    monkeypatch.setenv("OMEGA_PRIME_AGENT_STATE_DIR", str(tmp_path / "data"))
    assert desk_intake_path(tmp_path) == tmp_path / "data" / "intake.json"


# -- Phase 64 DESK-04: the desk_lead_pass cron job kind ---------------------


def _store(tmp_path):
    return JobStore(tmp_path / "cron" / "jobs.json")


def test_schedule_and_list_desk_pass(tmp_path):
    store = _store(tmp_path)
    job_id = schedule_desk_lead_pass(
        store,
        due_at=1000,
        interval_seconds=60,
        intake_path=tmp_path / "intake.json",
        limit=2,
    )
    jobs = list_desk_lead_passes(store)
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["kind"] == DESK_LEAD_KIND
    assert jobs[0]["intake_path"] == str(tmp_path / "intake.json")
    assert jobs[0]["limit"] == 2
    # Persisted.
    assert len(list_desk_lead_passes(_store(tmp_path))) == 1
    with pytest.raises(ValueError, match="interval_seconds"):
        schedule_desk_lead_pass(store, due_at=0, interval_seconds=0)
    with pytest.raises(ValueError, match="limit"):
        schedule_desk_lead_pass(store, due_at=0, interval_seconds=60, limit=0)


def test_desk_pass_tick_serializes_and_holds_no_lock_during_run(tmp_path):
    store = _store(tmp_path)
    first = schedule_desk_lead_pass(store, due_at=1000, interval_seconds=60)
    second = schedule_desk_lead_pass(store, due_at=1000, interval_seconds=60)
    observed = []

    def runner(job):
        # While the fake model call runs, the claim is already on disk and
        # the store file is readable: no lock is held across the call.
        on_disk = json.loads(store.path.read_text(encoding="utf-8"))["jobs"]
        row = next(j for j in on_disk if j["id"] == job["id"])
        observed.append((job["id"], row["claimed_at"], row["last_ran_at"]))
        return {"tickets": []}

    ran = tick_desk_lead_passes(store, 1000, runner)
    assert [job["id"] for job in ran] == [first, second]
    assert [row[0] for row in observed] == [first, second]
    # The claim was persisted before the runner started, and the job had not
    # run yet (the run record lands only after the runner returns).
    assert observed[0][1] == 1000
    assert observed[0][2] is None
    assert observed[1][1] == 1000
    # A same-instant re-tick never double-runs a pass.
    assert tick_desk_lead_passes(store, 1000, runner) == []
    assert len(observed) == 2


def test_scheduler_runs_the_desk_pass_end_to_end(tmp_path):
    store = _store(tmp_path)
    intake_path = tmp_path / "state" / "intake.json"
    intake = IntakeStore(intake_path)
    record = intake.submit("add a banner to the readme")
    job_id = schedule_desk_lead_pass(
        store, due_at=1000, interval_seconds=60, intake_path=intake_path
    )
    ran = run_desk_lead_passes(store, 1000, _fake_parent(), tmp_path)
    assert [job["id"] for job in ran] == [job_id]
    result = ran[0]["last_result"]
    assert result["tickets"][0]["status"] == "done"
    validate_receipt(result["receipts"][0]["receipt"])
    # The runner built its own IntakeStore from the same file (a separate
    # reader, as in production), so the outcome is read back through the
    # file — this test's in-memory snapshot never saw the pass's writes.
    after = IntakeStore(intake_path)
    done = after.get(record["intake_id"])
    assert done is not None
    assert done["status"] == "done"
    # The interval reschedules; nothing is due again at the same instant.
    assert ran[0]["due_at"] == 1060
    assert run_desk_lead_passes(store, 1000, _fake_parent(), tmp_path) == []


def test_no_parent_scheduler_pass_blocks_explicitly_not_no_dispatcher(tmp_path):
    """Production never produces dispatch=None's "no dispatcher" outcome."""
    store = _store(tmp_path)
    intake_path = desk_intake_path(tmp_path)
    intake = IntakeStore(intake_path)
    intake.submit("fix login")
    schedule_desk_lead_pass(
        store, due_at=1000, interval_seconds=60, intake_path=intake_path
    )
    ran = run_desk_lead_passes(store, 1000, parent=None, work_root=tmp_path)
    result = ran[0]["last_result"]
    ticket = result["tickets"][0]
    assert ticket["status"] == "blocked"
    assert "no parent agent shim" in ticket["reason"]
    assert "no dispatcher" not in json.dumps(result)
    validate_receipt(ticket["receipt"])


def test_run_due_jobs_leaves_desk_jobs_to_the_desk_tick(tmp_path):
    store = _store(tmp_path)
    model = ScriptedModel([{"role": "assistant", "content": "should-not-run"}])
    desk_id = schedule_desk_lead_pass(
        store,
        due_at=1000,
        interval_seconds=60,
        intake_path=tmp_path / "intake.json",
    )
    plain_id = store.schedule("plain", 1000)
    ran = run_due_jobs(store, 1000, model, {})
    assert [job["id"] for job in ran] == [plain_id]
    assert model.call_count == 1
    desk_job = next(job for job in store.jobs if job["id"] == desk_id)
    assert desk_job.get("last_ran_at") is None  # untouched by the model tick
    # The desk driver still owns it.
    desk_ran = tick_desk_lead_passes(store, 1000, lambda job: {})
    assert [job["id"] for job in desk_ran] == [desk_id]


def test_interleaved_producer_and_claim_survive_dispatch(tmp_path):
    """75-01: writes committed mid-dispatch are never lost or reverted.

    Dispatch pauses at a deterministic boundary while a separate store
    submits an order and claims/updates another. Resuming must complete
    the dispatched tickets without deleting the new order or reverting
    the foreign claim — verified through both the original warm store
    and a fresh observer, plus the real scratch artifact.
    """
    intake_path = tmp_path / "intake.json"
    intake = IntakeStore(intake_path)  # warm store held across the pass
    first = intake.submit("first ticket")
    second = intake.submit("second ticket")
    scratch = tmp_path / "scratch.txt"
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def _dispatch(ticket):
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            assert release.wait(timeout=30)
        with scratch.open("a", encoding="utf-8") as handle:
            handle.write(f"{ticket['ticket_id']}:{ticket['ask']}\n")
        return {"receipt": _receipt()}

    holder: dict = {}
    errors: list = []

    def _run() -> None:
        try:
            holder["outcome"] = run_lead_pass(intake, _dispatch)
        except Exception as exc:  # pragma: no cover - failure marker only
            errors.append(exc)

    worker = threading.Thread(target=_run)
    worker.start()
    try:
        assert entered.wait(timeout=30)
        # Interleave while the first ticket's dispatch is paused: the
        # oldest open order is the second ticket (the first is claimed).
        # The foreign claim carries a fresh wall-clock timestamp so the
        # pass's own legitimate reclamation (which runs on real time)
        # keeps it: age stays far under the lease, no fencing involved.
        foreign = IntakeStore(intake_path)
        third = foreign.submit("third ticket")
        foreign_now = time.time()
        claimed_second = foreign.next(None, "foreign-worker", now=foreign_now)
        assert claimed_second is not None
        assert claimed_second["intake_id"] == second["intake_id"]
        foreign.ack(second["intake_id"], {"message": "foreign progress"})
        release.set()
    finally:
        worker.join(timeout=60)
    assert errors == []
    assert not worker.is_alive()
    outcome = holder["outcome"]
    assert [t["status"] for t in outcome["tickets"]] == ["done", "done"]
    assert outcome["invalid"] == []
    assert {t["intake_id"] for t in outcome["tickets"]} == {
        first["intake_id"],
        third["intake_id"],
    }
    for row in outcome["receipts"]:
        validate_receipt(row["receipt"])
    for observer in (intake, IntakeStore(intake_path)):
        done_first = observer.get(first["intake_id"])
        assert done_first is not None and done_first["status"] == "done"
        done_third = observer.get(third["intake_id"])
        assert done_third is not None and done_third["status"] == "done"
        kept = observer.get(second["intake_id"])
        assert kept is not None and kept["status"] == "in_progress"
        assert kept["by"] == "foreign-worker"
        assert kept["claimed_at"] == foreign_now
        assert kept["message"] == "foreign progress"
        assert observer.counts() == {"done": 2, "in_progress": 1}
    lines = scratch.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(":first ticket")
    assert lines[1].endswith(":third ticket")


def test_scheduled_pass_uses_the_hosts_effective_intake_file(tmp_path, monkeypatch):
    """Greptile PR 50: a settings-only agent folder is not in os.environ."""
    from omega_prime.cron.desk_driver import DeskDriver

    monkeypatch.delenv("OMEGA_PRIME_AGENT_STATE_DIR", raising=False)
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path / "exported"))
    root = tmp_path / "root"
    store = JobStore(root / "cron" / "jobs.json")
    host_intake = tmp_path / "settings-agent" / "intake.json"
    record = IntakeStore(host_intake).submit("fix login")
    schedule_desk_lead_pass(store, due_at=1000, interval_seconds=60)
    driver = DeskDriver(
        root,
        parent=None,
        work_root=tmp_path,
        clock=lambda: 1000.0,
        intake_path=host_intake,
    )
    driver.tick_once()
    job = JobStore(root / "cron" / "jobs.json").jobs[0]
    tickets = job["last_result"]["tickets"]
    # The pass read the host's queue: its one record became a ticket.
    assert [t.get("intake_id") for t in tickets] == [record["intake_id"]]
    assert not (tmp_path / "exported" / "agent" / "intake.json").exists()

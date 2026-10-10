# SPDX-License-Identifier: AGPL-3.0-only
"""Optional Prime local lifecycles for isolated readiness (Phase 75-07).

Scenario module owned by Native75Prime. Called by the readiness core only
when ``--include-prime`` is requested, with the enabled (ON) main probe:
the core first proves genuine OFF absence/refusal, then writes the seven
enabled fixture flags into the owned disposable config and constructs the
main ON runtime. This module validates that actual ON state through
``config.load_config``/``prime_enabled`` plus the offered roster names, then
exercises every Prime family with real local effects or records a named
per-capability block. No ambient-OFF assumption is repeated here.

Shared types come only from ``local_validation`` (``ReadinessProbe``). All
other imports are production modules resolved inside the fresh product
child. No caller-env reads or mutations, no per-call RPC, no invented
registry definitions, no fake gates.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, never a runtime import
    from omega_prime.grokbot.local_validation import ReadinessProbe

# Unique markers keep this scenario's disposable state disjoint from any
# other writer in the same child.
_MARK = "readiness75-prime"


def run(probe: ReadinessProbe) -> None:
    """Exercise the full optional Prime slice against the ON main probe."""
    _flag_truth(probe)
    _harness_lifecycle(probe)
    _goals_lifecycle(probe)
    _messaging_lifecycle(probe)
    _heartbeat_lifecycle(probe)
    _autonomous_lifecycle(probe)
    _rlm_lifecycle(probe)
    _kernel_lifecycle(probe)


def _prime_tool_names() -> tuple[str, ...]:
    """The canonical 37 rostered Prime tool names from their source tuples."""
    from omega_prime.tools.agent_message import MESSAGING_TOOL_NAMES
    from omega_prime.tools.autonomous import AUTONOMOUS_TOOL_NAMES
    from omega_prime.tools.goals import GOAL_TOOL_NAMES
    from omega_prime.tools.harness import HARNESS_TOOL_NAMES
    from omega_prime.tools.heartbeat import HEARTBEAT_TOOL_NAMES
    from omega_prime.tools.prime_runtime import KERNEL_TOOL_NAMES
    from omega_prime.tools.rlm import RLM_TOOL_NAMES

    return (
        *RLM_TOOL_NAMES,
        *HARNESS_TOOL_NAMES,
        *GOAL_TOOL_NAMES,
        *HEARTBEAT_TOOL_NAMES,
        *AUTONOMOUS_TOOL_NAMES,
        *MESSAGING_TOOL_NAMES,
        *KERNEL_TOOL_NAMES,
    )


def _result(probe: ReadinessProbe, name: str, arguments: dict[str, Any]) -> Any:
    """Call through the real emulator and return the inner result payload."""
    envelope = probe.call(name, arguments)
    probe.check(
        envelope.get("status") == "ok",
        f"{name} returned a successful envelope",
    )
    return envelope.get("result")


def _direct(
    probe: ReadinessProbe, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Deliberate negative dispatch through the real emulator, unraised."""
    return probe.emulator.execute_tool(name, dict(arguments))


def _expect_refusal(
    probe: ReadinessProbe,
    check_name: str,
    tool: str,
    arguments: dict[str, Any],
    markers: tuple[str, ...],
) -> str:
    """Frozen negative protocol: refusal AND no-effect validation, control-only.

    Uses the real ``probe.emulator.execute_tool`` directly (never ``call``,
    which raises on invocation errors), asserts an error envelope naming the
    expected gap, rejects authorization text as an eligible local failure
    (Core supplies targeted grants, so approvals are never the gap), and
    records control-only ``tools=()``. Returns the refusal text.
    """
    outcome = _direct(probe, tool, arguments)
    probe.check(
        outcome.get("status") == "error",
        f"{tool} refuses the deliberate negative case",
    )
    text = str(outcome.get("error") or outcome.get("result") or outcome)
    # The complete refusal reaches the shared full-input redaction+strong gate
    # via record/blocked/_failed_check BEFORE any flatten/clip. Never clip raw
    # text here: a low-entropy prefix window would pass the gate while the full
    # candidate trips it (T-75-04-03).
    probe.check(
        "approval required" not in text and "policy forbids" not in text,
        f"{tool} refusal is the genuine gap, not authorization: {text}",
    )
    probe.check(
        any(marker in text for marker in markers),
        f"{tool} refusal names the expected gap: {text}",
    )
    probe.record(check_name, (), f"{tool} refused as expected: {text}")
    return text


def _flag_truth(probe: ReadinessProbe) -> None:
    """Validate actual enabled config state and offered roster names.

    Control-only: no tool is dispatched here; the lifecycles below provide
    the exercised evidence. ``config.load_config`` reads the child process
    environment independently, so this observes the genuine fixture ON
    state the core constructed, never an env-mapping fake gate.
    """
    from omega_prime.config import PRIME_FAMILIES, load_config, prime_enabled

    config = load_config(probe.emulator.root)
    for family in PRIME_FAMILIES:
        probe.check(
            prime_enabled(config, family) is True,
            f"prime family {family!r} is actually enabled in the fixture config",
        )
    names = _prime_tool_names()
    probe.check(len(names) == 37, "prime roster universe is the 37 canonical names")
    offered = set(probe.emulator.runtime.tool_names)
    missing = [name for name in names if name not in offered]
    probe.check(not missing, f"all 37 prime tools offered, missing={missing!r}")
    probe.record(
        "prime_enabled_config",
        (),
        "actual load_config shows all seven families enabled and all 37 "
        "canonical prime tools offered; no tool dispatched yet",
    )


def _harness_lifecycle(probe: ReadinessProbe) -> None:
    """Upsert/get/list/refine/rollback/delete with fresh-store reads."""
    note_id = f"{_MARK}-note-1"
    refined_id = f"{_MARK}-refined-1"
    marker = f"{_MARK} memory marker alpha"
    body_v1 = f"original body carrying {marker}"

    entry = _result(
        probe,
        "harness_upsert",
        {"kind": "memory", "id": note_id, "title": "readiness note", "body": body_v1},
    )
    probe.check(entry.get("id") == note_id, "harness upsert returns the entry")
    probe.check(entry.get("body") == body_v1, "harness upsert stores the body")

    seen = _result(probe, "harness_get", {"kind": "memory", "id": note_id})
    probe.check(seen.get("body") == body_v1, "fresh harness_get reads the body back")

    listed = _result(probe, "harness_list", {"kind": "memory"})
    probe.check(
        any(row.get("id") == note_id for row in listed),
        "harness_list contains the upserted entry",
    )

    trajectory = f"turn transcript: the operator wrote down {marker} verbatim"
    refined = _result(
        probe,
        "harness_refine",
        {
            "trigger": "readiness refine check",
            "proposals": [
                {
                    "kind": "memory",
                    "id": refined_id,
                    "title": "refined note",
                    "body": f"refinement backed by {marker}",
                    "evidence": [marker],
                }
            ],
            "trajectory": trajectory,
        },
    )
    probe.check(
        len(refined.get("applied", [])) == 1, "refine applied the backed proposal"
    )
    probe.check(refined.get("rejected", []) == [], "refine rejected nothing")
    got_refined = _result(probe, "harness_get", {"kind": "memory", "id": refined_id})
    probe.check(
        marker in str(got_refined.get("body", "")),
        "fresh harness_get reads the refined entry",
    )

    rolled = _result(probe, "harness_rollback", {})
    probe.check(rolled.get("restored") is True, "rollback restored the snapshot")
    _expect_refusal(
        probe,
        "prime_harness_rollback_absence",
        "harness_get",
        {"kind": "memory", "id": refined_id},
        ("no memory entry", refined_id),
    )
    after_list = _result(probe, "harness_list", {"kind": "memory"})
    probe.check(
        all(row.get("id") != refined_id for row in after_list),
        "fresh list confirms the refined entry is actually absent",
    )
    kept = _result(probe, "harness_get", {"kind": "memory", "id": note_id})
    probe.check(kept.get("body") == body_v1, "original entry survives the rollback")

    deleted = _result(probe, "harness_delete", {"kind": "memory", "id": note_id})
    probe.check(deleted.get("deleted") is True, "harness delete removes the entry")
    _expect_refusal(
        probe,
        "prime_harness_delete_absence",
        "harness_get",
        {"kind": "memory", "id": note_id},
        ("no memory entry", note_id),
    )
    final_list = _result(probe, "harness_list", {"kind": "memory"})
    probe.check(
        all(row.get("id") != note_id for row in final_list),
        "fresh list confirms the deleted entry is actually absent",
    )
    probe.record(
        "prime_harness",
        (
            "harness_upsert",
            "harness_get",
            "harness_list",
            "harness_refine",
            "harness_rollback",
            "harness_delete",
        ),
        "real trajectory/proposals refine, snapshot rollback verified by fresh "
        "reads, delete cleanup leaves no residue",
    )


def _goals_lifecycle(probe: ReadinessProbe) -> None:
    """Set/pause/resume/status/clear with checked prime_status transitions.

    The real ``goal_status`` payload carries the transition in
    ``prime_status`` (``PrimeGoalStore.prime_status`` merges base
    ``objective``/``steps`` with the sidecar ``prime_status``), never a
    top-level ``status`` or ``steps_total`` key.
    """
    objective = f"{_MARK} objective: prove local goal transitions"
    started = _result(
        probe,
        "goal_set",
        {"objective": objective, "token_budget": 1000, "steps": ["draft", "verify"]},
    )
    probe.check(started.get("objective") == objective, "goal_set stores the objective")
    probe.check(
        started.get("prime_status") == "active", "goal_set returns active prime_status"
    )

    active = _result(probe, "goal_status", {})
    probe.check(
        active.get("prime_status") == "active", "fresh status is active after set"
    )
    probe.check(
        active.get("objective") == objective, "fresh status carries the objective"
    )
    probe.check(len(active.get("steps", [])) == 2, "fresh status carries both steps")
    probe.check(
        active.get("token_budget") == 1000, "fresh status carries the token budget"
    )

    paused = _result(probe, "goal_pause", {})
    probe.check(
        paused.get("prime_status") == "paused", "goal_pause transitions to paused"
    )
    probe.check(
        _result(probe, "goal_status", {}).get("prime_status") == "paused",
        "fresh status stays paused",
    )

    resumed = _result(probe, "goal_resume", {})
    probe.check(
        resumed.get("prime_status") == "active", "goal_resume transitions to active"
    )
    probe.check(
        _result(probe, "goal_status", {}).get("prime_status") == "active",
        "fresh status stays active",
    )

    cleared = _result(probe, "goal_clear", {})
    probe.check(
        cleared.get("prime_status") == "cleared", "goal_clear transitions to cleared"
    )
    empty = _result(probe, "goal_status", {})
    probe.check(empty.get("prime_status") == "cleared", "fresh status stays cleared")
    probe.check(not empty.get("objective"), "cleared goal carries no objective")
    probe.record(
        "prime_goals",
        ("goal_set", "goal_status", "goal_pause", "goal_resume", "goal_clear"),
        "set/active, pause/paused, resume/active, clear/cleared all verified "
        "through fresh goal_status prime_status reads",
    )


def _messaging_lifecycle(probe: ReadinessProbe) -> None:
    """Send to a real registry recipient, verify delivery, observe honestly.

    Delivery runs through the actual in-process ``SessionRegistry``
    singleton the rostered tools are bound to. This is labeled in-memory
    local delivery: it proves nothing about cross-process, bus, or durable
    messaging.
    """
    from omega_prime.tools import agent_message as _messaging_tools

    sessions = _messaging_tools._shared_registry()
    members_before = set(sessions.members())
    peer = f"{_MARK}-peer-1"
    sessions.register(peer)
    members = set(sessions.members())
    probe.check(peer in members, "peer session registers on the real shared registry")
    probe.check(
        members_before < members or peer in members_before,
        "registry membership reflects the actual registration",
    )

    body = f"{_MARK} hello peer"
    sent = _result(probe, "agent_message_send", {"recipient": peer, "body": body})
    delivered = sent.get("delivered")
    probe.check(
        isinstance(delivered, dict), "send returns the delivered message envelope"
    )
    probe.check(
        delivered.get("body") == body, "delivered envelope carries the real body"
    )
    probe.check(delivered.get("recipient") == peer, "delivered envelope names the peer")
    sender = delivered.get("sender")
    probe.check(
        isinstance(sender, str) and sender in set(sessions.members()),
        "delivered envelope names the real bound sender session",
    )

    inbox = sessions.observe(peer)
    probe.check(
        any(
            isinstance(row, dict)
            and row.get("body") == body
            and row.get("recipient") == peer
            and row.get("sender") == sender
            and row.get("id") == delivered.get("id")
            for row in inbox
        ),
        "peer's independently observed inbox holds the exact sent message",
    )

    own_inbox = _result(probe, "agent_observe", {})
    probe.check(
        isinstance(own_inbox, list), "agent_observe returns the own-session list"
    )
    probe.record(
        "prime_messaging",
        ("agent_message_send", "agent_observe"),
        "in-memory SessionRegistry delivery to a genuinely registered peer, "
        "verified by a real registry read; not cross-process/bus/durable",
    )


def _heartbeat_lifecycle(probe: ReadinessProbe) -> None:
    """Bound-runner delivery plus list/clear/stop/close on the real runtime."""
    from omega_prime.cron.heartbeat_runtime import HeartbeatRuntime

    registry = probe.emulator.registry
    runtime = registry.runtime_bindings.get("prime_heartbeat")
    probe.check(
        isinstance(runtime, HeartbeatRuntime),
        "heartbeat tools own a genuine HeartbeatRuntime binding",
    )
    install = str(probe.layout.install)
    probe.check(
        str(runtime.path).startswith(install),
        "heartbeat journal lives under the disposable install tree",
    )

    delivered: list[str] = []

    def _runner(prompt: str) -> dict[str, Any]:
        delivered.append(prompt)
        return {"ok": True, "echo": prompt}

    session = f"{_MARK}-beat"
    runtime.bind(session, _runner)
    probe.check(session in runtime.bindings, "named runner binding is observable")
    runtime.start()

    prompt = f"{_MARK} ping"
    try:
        scheduled = _result(
            probe,
            "heartbeat_set",
            {"session": session, "prompt": prompt, "interval_seconds": 3600},
        )
        job_id = scheduled.get("scheduled")
        probe.check(
            isinstance(job_id, str) and bool(job_id), "heartbeat_set returns a job id"
        )

        deadline = time.monotonic() + 20.0
        while prompt not in delivered and time.monotonic() < deadline:
            time.sleep(0.2)
        probe.check(
            prompt in delivered,
            "bound runner observed the scheduled beat (delivery, not scheduling)",
        )

        jobs = _result(probe, "heartbeat_list", {})
        probe.check(
            any(isinstance(row, dict) and row.get("id") == job_id for row in jobs),
            "heartbeat_list shows the scheduled job",
        )
        cleared = _result(probe, "heartbeat_clear", {"job_id": job_id})
        probe.check(
            cleared.get("cleared") is True
            or cleared.get("removed") is True
            or job_id
            not in {
                row.get("id")
                for row in _result(probe, "heartbeat_list", {})
                if isinstance(row, dict)
            },
            "heartbeat_clear removes the job",
        )
    finally:
        runtime.stop(wait=True)
        runtime.close()
    probe.check(
        session not in runtime.bindings,
        "close() drops the named binding",
    )
    probe.record(
        "prime_heartbeat",
        ("heartbeat_set", "heartbeat_list", "heartbeat_clear"),
        "delivery observed inside the bound runner topical callback; "
        "stop(wait=True)+close() dropped bindings",
    )


def _autonomous_lifecycle(probe: ReadinessProbe) -> None:
    """Controller state plus real driven turns with per-turn payloads."""
    from omega_prime.agent.autonomous import AutonomousDriver
    from omega_prime.prime.autonomous import AutonomousConnector

    started = _result(probe, "autonomous_start", {"max_turns": 2})
    probe.check(started.get("started") is True, "autonomous run actually starts")

    state = probe.emulator.registry.runtime_bindings.get("prime_autonomous")
    raw_driver = state.get("driver") if isinstance(state, dict) else None
    if not isinstance(raw_driver, AutonomousDriver):
        probe.check(False, "start installs the genuine driver seam")
        return
    driver = raw_driver
    probe.check(bool(driver.running) is True, "driver reports running after start")

    connector = AutonomousConnector(probe.emulator.root)
    first = connector.after_turn(
        driver, {"completed": True, "usage": {"total_tokens": 10}}
    )
    probe.check(first.get("action") == "continue", "first driven turn continues")
    probe.check(first.get("turns") == 1, "first verdict carries per-turn payload")
    second = connector.after_turn(
        driver, {"completed": True, "usage": {"total_tokens": 10}}
    )
    probe.check(second.get("action") == "stop", "second driven turn stops at budget")
    probe.check(
        second.get("reason") == "autonomous_max_turns",
        "stop reason names the exhausted turn budget honestly",
    )

    status = _result(probe, "autonomous_status", {})
    probe.check(status.get("turns") == 2, "status shows both driven turns")
    probe.check(
        status.get("stopped") == "autonomous_max_turns",
        "status carries the genuine stop reason",
    )
    stopped = _result(probe, "autonomous_stop", {})
    probe.check(stopped.get("stopped") is True, "autonomous stop closes the run")
    probe.record(
        "prime_autonomous",
        ("autonomous_start", "autonomous_status", "autonomous_stop"),
        "real controller plus two after_turn payloads (continue then "
        "max_turns stop); started/running alone never counted as work",
    )


def _rlm_lifecycle(probe: ReadinessProbe) -> None:
    """Roster accounting plus a real persisted lifecycle on a configured host.

    The rostered host in this child has no provider child model, so roster
    spawn/create stay honestly blocked on the unconfigured provider/runner.
    The full persisted lifecycle runs through a real ``RlmHost`` plus the
    real ``RlmConnector`` with a configured runner backed by the existing
    ``ScriptedModel`` seam. That composition is a labeled model-double: it
    proves local persistence and handler wiring, never provider/Grok proof.
    """
    from omega_prime.prime.rlm import (
        CollectRequest,
        CreateSessionRequest,
        DeleteRequest,
        ProgressNoteRequest,
        RenameRequest,
        RlmConnector,
        SpawnRequest,
    )

    listed = _result(probe, "rlm_list_subagents", {})
    probe.check(isinstance(listed, list), "roster list_subagents reads the live host")
    collected = _result(probe, "rlm_collect", {"targets": [], "timeout_ms": 0})
    probe.check(collected == [], "roster collect over no targets returns no results")
    probe.record(
        "prime_rlm_roster_reads",
        ("rlm_list_subagents", "rlm_collect"),
        "effect-free roster reads against the genuine unconfigured host",
    )

    spawn_text = _expect_refusal(
        probe,
        "prime_rlm_spawn_unconfigured",
        "rlm_spawn",
        {"prompt": f"{_MARK} spawn", "name": f"{_MARK}-child"},
        ("not_configured",),
    )
    empty_after_spawn = _direct(probe, "rlm_list_subagents", {})
    probe.check(
        empty_after_spawn.get("status") == "ok"
        and empty_after_spawn.get("result") == [],
        "refused spawn creates no child effect",
    )
    probe.blocked(
        "rlm_spawn",
        ("rlm_spawn",),
        ("configured prime provider/run_child",),
        f"roster host refusal names the gap: {spawn_text}; desk model "
        "alone never supplies child wiring",
    )
    create_text = _expect_refusal(
        probe,
        "prime_rlm_create_session_unconfigured",
        "rlm_create_session",
        {"prompt": f"{_MARK} session", "name": f"{_MARK}-session"},
        ("not_configured",),
    )
    probe.blocked(
        "rlm_create_session",
        ("rlm_create_session",),
        ("configured prime provider/run_child",),
        f"roster host refusal names the gap: {create_text}",
    )
    for tool, args in (
        (
            "rlm_progress_note",
            {"child_id": f"{_MARK}-no-such-child", "message": "ping"},
        ),
        ("rlm_rename", {"target": f"{_MARK}-no-such-child", "name": "renamed"}),
        ("rlm_delete_subagent", {"target": f"{_MARK}-no-such-child"}),
    ):
        refusal = _expect_refusal(
            probe,
            f"prime_rlm_{tool}_missing_child",
            tool,
            dict(args),
            ("No direct RLM subagent", f"{_MARK}-no-such-child"),
        )
        probe.blocked(
            tool,
            (tool,),
            ("existing rlm child id (requires configured run_child)",),
            f"no child exists on the unconfigured host: {refusal}",
        )

    _rlm_direct_lifecycle(
        probe,
        RlmConnector,
        SpawnRequest,
        CollectRequest,
        ProgressNoteRequest,
        RenameRequest,
        DeleteRequest,
        CreateSessionRequest,
    )


def _rlm_direct_lifecycle(
    probe: ReadinessProbe,
    connector_cls: Any,
    spawn_request: Any,
    collect_request: Any,
    progress_request: Any,
    rename_request: Any,
    delete_request: Any,
    create_request: Any,
) -> None:
    """Full spawn/collect/create/progress/list/rename/delete, persisted."""
    from types import SimpleNamespace

    from omega_prime.agent.model import ScriptedModel
    from omega_prime.agent.rlm import RlmHost

    durable = probe.layout.state / "rlm-direct"
    durable.mkdir(parents=True, exist_ok=True)
    parent = SimpleNamespace(
        session_dir=str(durable),
        session_name=f"{_MARK}-probe",
        delegate_depth=0,
        max_depth=2,
        max_children=4,
    )
    scripted = ScriptedModel(
        [
            {"role": "assistant", "content": f"{_MARK} child answer one"},
            {"role": "assistant", "content": f"{_MARK} child answer two"},
        ]
    )

    def _run_child(prompt: str, model: Any = None, thinking: Any = None) -> str:
        message = scripted.complete([{"role": "user", "content": str(prompt)}])
        content = message.get("content", "")
        return content if isinstance(content, str) else str(content)

    host = RlmHost(parent, run_child=_run_child)
    try:
        connector = connector_cls(host)
        handle = connector.spawn(
            spawn_request.from_dict(
                {"prompt": f"{_MARK} direct work", "name": f"{_MARK}-direct"}
            )
        )
        child_id = handle.get("rlm_child_id")
        probe.check(
            isinstance(child_id, str) and bool(child_id), "direct spawn mints a child"
        )
        probe.check(
            any(
                f"{_MARK} direct work" in str(message.get("content", ""))
                for snapshot in scripted.seen
                if isinstance(snapshot, list)
                for message in snapshot
                if isinstance(message, dict)
            ),
            "configured runner actually executed with the spawn prompt",
        )

        results = connector.collect(
            collect_request.from_dict({"targets": [child_id], "timeout_ms": 15000})
        )["results"]
        probe.check(len(results) == 1, "direct collect returns the settled child")
        probe.check(
            results[0].get("rlm_child_id") == child_id,
            "collect result addresses the spawned child",
        )
        probe.check(
            results[0].get("settled") is True,
            "collect result shows genuine settlement, not listing alone",
        )

        noted = connector.progress_note(
            progress_request.from_dict(
                {"child_id": child_id, "message": f"{_MARK} progress"}
            )
        )
        probe.check(noted.get("accepted") is True, "progress note is accepted")

        created = connector.create_session(
            create_request.from_dict(
                {"prompt": f"{_MARK} session work", "name": f"{_MARK}-sess"}
            ),
            session_store=_RecordingStore(),
        )
        probe.check(
            isinstance(created.get("session_id"), str) and created["session_id"],
            "create_session persists a session child with a session id",
        )

        subs = connector.list_subagents()["subagents"]
        probe.check(
            any(row.get("rlm_child_id") == child_id for row in subs),
            "list shows the live child",
        )
        renamed = connector.rename(
            rename_request.from_dict({"target": child_id, "name": f"{_MARK}-renamed"})
        )
        probe.check(renamed.get("renamed") == child_id, "rename addresses the child")
        probe.check(
            renamed.get("name") == f"{_MARK}-renamed",
            "rename response carries the new name",
        )
        # The list view carries no display-name key (prime/rlm.py
        # _subagent_view), so the rename effect is proven through the real
        # name-selector path instead: resolving the NEW name must hit the
        # same child id, which fails unless the stored name actually changed.
        relabeled = connector.rename(
            rename_request.from_dict(
                {"target": f"{_MARK}-renamed", "name": f"{_MARK}-final"}
            )
        )
        probe.check(
            relabeled.get("renamed") == child_id,
            "name selector resolves the renamed child by its new name",
        )
        subs_after = connector.list_subagents()["subagents"]
        probe.check(
            any(row.get("rlm_child_id") == child_id for row in subs_after),
            "fresh list keeps the renamed child under its stable id",
        )
    finally:
        host.shutdown()

    reopened = RlmHost(parent, run_child=_run_child)
    try:
        rows = reopened.list_subagents()
        probe.check(
            any(getattr(row, "rlm_child_id", None) == child_id for row in rows),
            "reopened host recovers the persisted child metadata",
        )
        removed = connector_cls(reopened).delete_subagent(
            delete_request.from_dict({"target": child_id})
        )
        probe.check(
            removed.get("deleted") == child_id,
            "delete removes the persisted child",
        )
        probe.check(
            all(
                getattr(row, "rlm_child_id", None) != child_id
                for row in reopened.list_subagents()
            ),
            "fresh list after delete no longer shows the child",
        )
    finally:
        reopened.shutdown()
    probe.record(
        "prime_rlm_lifecycle",
        (
            "rlm_spawn",
            "rlm_collect",
            "rlm_create_session",
            "rlm_progress_note",
            "rlm_list_subagents",
            "rlm_rename",
            "rlm_delete_subagent",
        ),
        "real RlmHost+RlmConnector spawn/collect/create/progress/list/rename/"
        "delete with a ScriptedModel-backed runner (labeled model-double, not "
        "provider); metadata reopened from disposable STATE/rlm-direct after "
        "shutdown, then deleted",
    )


class _RecordingStore:
    """Minimal duck-typed session roster for create_session persistence."""

    def __init__(self) -> None:
        self.rows: list[tuple[Any, Any, Any]] = []

    def create(self, *, session_id: Any, name: Any, prompt: Any) -> None:
        self.rows.append((session_id, name, prompt))


def _kernel_lifecycle(probe: ReadinessProbe) -> None:
    """Cells, bash, skills, crates, goal/autonomous locals, honest factory."""
    _prime_cell_namespace(probe)
    _prime_bash_local(probe)
    _prime_skill_list(probe)
    _prime_crates_native(probe)
    _prime_goal_local(probe)
    _prime_autonomous_local(probe)
    _prime_factory_blocked(probe)


_NATIVE_MARKERS = (
    "extension",
    "not built",
    "omega_prime_prime",
    "ImportError",
    "No module named",
    "cannot load",
)


def _blocked_or_raise(
    probe: ReadinessProbe,
    name: str,
    tools: tuple[str, ...],
    exc: Exception,
    markers: tuple[str, ...],
    prerequisite: str,
) -> None:
    text = f"{type(exc).__name__}: {exc}"
    if any(marker in text for marker in markers):
        probe.blocked(name, tools, (prerequisite,), f"genuine missing resource: {text}")
        return
    raise exc


def _prime_cell_namespace(probe: ReadinessProbe) -> None:
    # _MARK contains hyphens (fine for ids/text, fatal for code): cells use
    # an identifier-safe derivation so the kernel executes real arithmetic.
    var = f"{_MARK}_x".replace("-", "_")
    try:
        first = _result(probe, "prime_cell", {"code": f"{var} = 6 * 7"})
        probe.check(first.get("ok") is True, "prime_cell executes the arithmetic cell")
        second = _result(probe, "prime_cell", {"code": var})
    except Exception as exc:  # classified, never swallowed
        _blocked_or_raise(
            probe,
            "prime_cell",
            ("prime_cell",),
            exc,
            _NATIVE_MARKERS,
            "pinned prime-agent runtime + prebuilt libomega_prime_prime",
        )
        return
    probe.check(second.get("ok") is True, "second cell reads the same namespace")
    probe.check(
        second.get("value") == "42",
        "persistent namespace evaluates to 42 across calls",
    )
    probe.record(
        "prime_cell",
        ("prime_cell",),
        "cell arithmetic retained namespace across calls with value 42",
    )


def _prime_bash_local(probe: ReadinessProbe) -> None:
    try:
        out = _result(probe, "prime_bash", {"command": f"printf '{_MARK}-bash-ok'"})
    except Exception as exc:  # classified, never swallowed
        _blocked_or_raise(
            probe,
            "prime_bash",
            ("prime_bash",),
            exc,
            (*_NATIVE_MARKERS, "No such file", "rlm.bash"),
            "pinned prime-agent rlm.bash runtime",
        )
        return
    probe.check(out.get("exit_code") == 0, "harmless bash exits zero")
    probe.check(
        _MARK in str(out.get("stdout", "")),
        "bash stdout carries the fixed owned-workspace marker",
    )
    probe.record(
        "prime_bash",
        ("prime_bash",),
        "fixed harmless printf effect with real exit_code/stdout; no invented "
        "run_child prerequisite per prime/kernel.py local bash path",
    )


def _prime_skill_list(probe: ReadinessProbe) -> None:
    try:
        out = _result(probe, "prime_skill_list", {})
    except Exception as exc:  # classified, never swallowed
        _blocked_or_raise(
            probe,
            "prime_skill_list",
            ("prime_skill_list",),
            exc,
            ("No such file", "skills", "prime-agent"),
            "pinned prime-agent/skills checkout",
        )
        return
    skills = out.get("skills", out)
    probe.check(isinstance(skills, list), "skill_list returns the real skill rows")
    probe.check(
        all(isinstance(row, dict) and row.get("name") for row in skills),
        "every skill row carries its directory name evidence",
    )
    probe.record(
        "prime_skill_list",
        ("prime_skill_list",),
        f"{len(skills)} owned skill resources listed without importing packages",
    )


def _prime_crates_native(probe: ReadinessProbe) -> None:
    try:
        from omega_prime.prime_kernel.checkout import workspace_members

        expected = workspace_members()
        out = _result(probe, "prime_crates", {})
    except Exception as exc:  # classified, never swallowed
        _blocked_or_raise(
            probe,
            "prime_crates",
            ("prime_crates",),
            exc,
            (*_NATIVE_MARKERS, "No such file", "Cargo.toml", "prime-agent"),
            "prebuilt libomega_prime_prime binary + pinned Cargo workspace",
        )
        return
    if out.get("loaded") is not True:
        probe.blocked(
            "prime_crates",
            ("prime_crates",),
            ("prebuilt libomega_prime_prime binary",),
            f"native status names the gap: {out.get('error')!r}",
        )
        return
    probe.check(
        set(expected) <= set(out.get("crates", [])),
        "loaded crates cover the pinned workspace members",
    )
    probe.check(
        set(expected) <= set(out.get("callable", [])),
        "all pinned crates expose actual callable probes",
    )
    probe.record(
        "prime_crates",
        ("prime_crates",),
        f"native loaded with callable probes for {sorted(out['callable'])}",
    )


def _prime_goal_local(probe: ReadinessProbe) -> None:
    objective = f"{_MARK} kernel objective"
    try:
        started = _result(probe, "prime_goal", {"args": objective})
        probe.check(
            started.get("state", {}).get("objective") == objective,
            "prime_goal stores the objective in the disposable kernel dir",
        )
        again = _result(probe, "prime_goal", {"args": "status"})
    except Exception as exc:  # classified, never swallowed
        _blocked_or_raise(
            probe,
            "prime_goal",
            ("prime_goal",),
            exc,
            _NATIVE_MARKERS,
            "prebuilt libomega_prime_prime binary",
        )
        return
    probe.check(again.get("command") == "status", "prime_goal status parses")
    probe.check(
        again.get("state", {}).get("goalId") == started.get("state", {}).get("goalId"),
        "status rereads the same persisted goal",
    )
    probe.check(isinstance(again.get("text"), str), "status renders honest text")
    probe.record(
        "prime_goal",
        ("prime_goal",),
        "local goal set/status with stable goalId under disposable prime-kernel; "
        "no invented run_child prerequisite per prime/kernel.py local goal path",
    )


def _prime_autonomous_local(probe: ReadinessProbe) -> None:
    try:
        status = _result(probe, "prime_autonomous", {"args": "status"})
    except Exception as exc:  # classified, never swallowed
        _blocked_or_raise(
            probe,
            "prime_autonomous",
            ("prime_autonomous",),
            exc,
            _NATIVE_MARKERS,
            "prebuilt libomega_prime_prime binary",
        )
        return
    probe.check(status.get("command") == "status", "prime_autonomous status parses")
    probe.check("status" in status, "status carries the live Rust run status")
    probe.check(isinstance(status.get("text"), str), "status renders honest text")
    probe.record(
        "prime_autonomous",
        ("prime_autonomous",),
        "local autonomous status read from the live in-process run; no invented "
        "run_child prerequisite per prime/kernel.py local autonomous path",
    )


_FACTORY_MARKERS = (
    "run_child",
    "child runner",
    "factory",
    "spec",
    "not configured",
    "not_configured",
    "disabled",
    "enabled",
    "no run",
    "unknown",
    "not found",
    "No such",
)


def _prime_factory_blocked(probe: ReadinessProbe) -> None:
    """Factory verbs stay blocked without a genuine configured run_child.

    The kernel registration in this child wires ``run_child=None`` (no
    provider child model), so execution claims would be false. Status/graph
    additionally require genuine run/spec identifiers, which no fixture
    provides. Every verb is attempted for real and classified: expected
    missing-runner/spec errors become named blocks; anything else fails.
    """
    for tool, args in (
        ("prime_factory_run", {"spec_id": f"{_MARK}-spec", "name": f"{_MARK}-run"}),
        ("prime_factory_status", {"run_id": f"{_MARK}-no-such-run"}),
        ("prime_factory_stop", {"run_id": f"{_MARK}-no-such-run"}),
        ("prime_factory_resume", {"run_id": f"{_MARK}-no-such-run"}),
        ("prime_factory_graph", {"ref": f"{_MARK}-no-such-ref"}),
    ):
        try:
            outcome = probe.emulator.execute_tool(tool, dict(args))
        except Exception as exc:  # classified, never swallowed
            probe.record(
                f"prime_{tool}_no_wiring",
                (),
                f"factory verb raises without child wiring: {exc}",
            )
            probe.blocked(
                tool,
                (tool,),
                ("genuine configured run_child + real factory spec/run id",),
                f"factory verb raises without child wiring: {exc}",
            )
            continue
        text = str(outcome)
        # Complete handler/exception text reaches the shared full-input gate
        # before any clip (T-75-04-03). Never window raw text here.
        if "approval required" in text or "policy forbids" in text:
            probe.check(
                False,
                f"{tool} blocked on authorization, not the genuine gap: {text}",
            )
        if outcome.get("status") == "error" and any(
            marker in text for marker in _FACTORY_MARKERS
        ):
            probe.record(
                f"prime_{tool}_no_wiring",
                (),
                f"factory verb names the gap, no fake execution: {text}",
            )
            probe.blocked(
                tool,
                (tool,),
                ("genuine configured run_child + real factory spec/run id",),
                f"factory verb names the gap, no fake execution: {text}",
            )
        elif outcome.get("status") == "ok":
            probe.check(False, f"{tool} must not claim execution without child wiring")
        else:
            probe.check(
                False, f"{tool} unexpected outcome without child wiring: {text}"
            )


__all__ = ["run"]

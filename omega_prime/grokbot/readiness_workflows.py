# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 75-06 readiness workflows: slash composition, warm intake, cron, learning, durable.

Scenario module executed inside the fresh isolated product child. ``run(probe)``
drives the workflow slice end to end through the real production registry via
``ReadinessProbe.call`` and records only semantically validated evidence:

- slash composition (``/omega-onboard``, ``/omega-desk``, ``/omega-connectors``,
  ``/omega-python``, ``/omega-retain``/``/omega-recall``) with explicit caller
  doctor context, per-step plus audit plus intake-transition assertions, and
  truthful failed negatives for missing/invalid context;
- warm intake delegation: the probe runtime itself is the warm runtime built
  before scenarios, an independent producer submits to the same persisted
  intake file, the original runtime claims/acks, and a fresh observer confirms;
- scheduled desk: cron create/list/remove plus a blocked-first tick and a
  deterministic-model-double tick through the real desk parent, delegate
  driver, tools, and store, against the disposable cron jobs journal;
- learning capture roundtrip plus advisor rendering over real artifacts;
- durable status over its actual journal with the desk-parent boundary stated.

Shared types come only from ``local_validation``; this module never imports
``readiness``. No test, gate, commit, or planning writes happen here.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from omega_prime.grokbot.local_validation import ReadinessProbe

__all__ = ["run"]


def run(probe: ReadinessProbe) -> None:
    """Execute the workflow slice against the warm probe runtime."""
    doctor_context = _prepare_doctor_seat(probe)
    _slash_slice(probe, doctor_context)
    _warm_intake_slice(probe, doctor_context)
    _cron_slice(probe)
    _learning_slice(probe)
    _durable_slice(probe)


def _result(probe: ReadinessProbe, name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch through production and return the unchanged envelope result."""
    return probe.call(name, arguments)["result"]


# -- disposable-tree path resolution (mirrors production wiring) --------------


def _runtime_root(probe: ReadinessProbe) -> Path:
    return Path(probe.emulator.root)


def _layout_env(probe: ReadinessProbe) -> dict[str, str]:
    return dict(probe.layout.env)


def _work_root(probe: ReadinessProbe) -> Path:
    from omega_prime.mcp_server import resolve_work_root

    return resolve_work_root(_runtime_root(probe), _layout_env(probe))


def _state_value(probe: ReadinessProbe) -> str:
    from omega_prime.routines.desk_lead import STATE_DIR_ENV

    state = _layout_env(probe).get(STATE_DIR_ENV) or ""
    return state if isinstance(state, str) else ""


def _intake_path(probe: ReadinessProbe) -> Path:
    from omega_prime.routines.desk_lead import desk_intake_path

    state = _state_value(probe)
    return desk_intake_path(_work_root(probe), state_dir=state or None)


def _coding_root(probe: ReadinessProbe) -> Path:
    root = _runtime_root(probe)
    work = _work_root(probe)
    return work if work != root else root / "omega_prime"


def _memory_dir(probe: ReadinessProbe) -> Path:
    """Disposable memory directory, mirroring the runtime wiring."""
    from omega_prime.agent_state import agent_state_dir

    agent = agent_state_dir(_layout_env(probe))
    if agent is not None:
        return agent / "memory"
    return _runtime_root(probe) / "omega_prime" / "memory"


def _skills_root(probe: ReadinessProbe) -> Path:
    return _runtime_root(probe) / "omega_prime" / "skills"


def _cron_jobs_path(probe: ReadinessProbe) -> Path:
    return _runtime_root(probe) / "cron" / "jobs.json"


def _durable_journal_path(probe: ReadinessProbe) -> Path:
    return _runtime_root(probe) / "cron" / "turns.sqlite"


def _desk_turns_path(probe: ReadinessProbe) -> Path:
    from omega_prime.agent_state import agent_state_dir
    from omega_prime.mcp_server import _desk_store_path

    agent = agent_state_dir(_layout_env(probe))
    return _desk_store_path(
        _work_root(probe), str(agent) if agent is not None else "", "turns.sqlite"
    )


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# -- doctor seat preparation ---------------------------------------------------


def _prepare_doctor_seat(probe: ReadinessProbe) -> dict[str, Any]:
    """Register, assemble, and install the disposable seat, then prove green.

    Returns the explicit caller ``doctor_context`` (prompt sha plus actual
    installed skill files) every slash scenario threads through the 75-09
    seam. This proves only the disposable local seat, never a live install.
    """
    agent_uuid = _unique("readiness-seat")
    registered = _result(
        probe, "lead_doctor", {"action": "register", "agent_uuid": agent_uuid}
    )
    probe.check("error" not in registered, "doctor register reports no error")
    probe.check(
        "lead" in (registered.get("registered") or []),
        "doctor register records the lead pack",
    )

    rendered = _result(probe, "lead_render_prompt", {})
    probe.check("error" not in rendered, "rendered seat prompt reports no error")
    probe.check(
        isinstance(rendered.get("sha256"), str) and bool(rendered["sha256"]),
        "rendered seat prompt carries a sha256",
    )
    probe.check(
        isinstance(rendered.get("prompt"), str) and bool(rendered["prompt"]),
        "rendered seat prompt carries prompt text",
    )

    # The real install_prompt returns the installed bytes plus their digest
    # (no success flag): prove the bytes/hash agreement directly instead of
    # trusting a flag the production contract does not send.
    installed = _result(probe, "lead_doctor", {"action": "install_prompt"})
    probe.check("error" not in installed, "install_prompt reports no error")
    sha = installed.get("sha256")
    text = installed.get("prompt")
    probe.check(
        isinstance(sha, str) and len(sha) == 64,
        "install_prompt returns a sha256 hex digest",
    )
    probe.check(
        isinstance(text, str) and bool(text.strip()),
        "install_prompt returns prompt bytes",
    )
    probe.check(
        hashlib.sha256(text.encode("utf-8")).hexdigest() == sha,
        "installed digest matches the installed bytes",
    )
    probe.check(
        sha == rendered["sha256"],
        "install_prompt stores the assembled prompt sha",
    )

    skills_root = _skills_root(probe)
    skills = sorted(
        entry.name
        for entry in skills_root.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    )
    probe.check(len(skills) > 0, "disposable install ships actual skill files")
    context = {"prompt_sha256": sha, "installed_skills": skills}

    healthy = _result(
        probe,
        "lead_doctor",
        {
            "action": "check",
            "prompt_sha256": context["prompt_sha256"],
            "installed_skills": context["installed_skills"],
        },
    )
    probe.check(healthy.get("green") is True, "prepared seat checks green")
    for name, row in (healthy.get("checks") or {}).items():
        probe.check(
            isinstance(row, dict) and row.get("green") is True,
            f"prepared doctor check {name} is green",
        )
    # Real declared/installed inventory agreement: every skill the seat
    # prompt declares is actually present on disk, and none is missing.
    skills_row = (healthy.get("checks") or {}).get("skills") or {}
    declared = skills_row.get("declared") or []
    probe.check(
        (skills_row.get("missing") or []) == [],
        "doctor reports no missing skills for the prepared seat",
    )
    probe.check(
        len(declared) > 0 and set(declared) <= set(skills),
        "every declared skill is an actually installed skill file",
    )
    # A repeated check must stay green: reopening the check never erases the
    # prerequisite the install just stored.
    again = _result(
        probe,
        "lead_doctor",
        {
            "action": "check",
            "prompt_sha256": context["prompt_sha256"],
            "installed_skills": context["installed_skills"],
        },
    )
    probe.check(again.get("green") is True, "repeated doctor check stays green")

    probe.record(
        "slash-doctor-seat-prepared",
        ("lead_doctor", "lead_render_prompt"),
        f"register/install_prompt/check green for disposable seat "
        f"{agent_uuid} with {len(skills)} installed skills covering every "
        "declared skill; installed digest verified against installed bytes; "
        "repeated check green",
    )
    return context


# -- slash slice ---------------------------------------------------------------


def _slash_result(probe: ReadinessProbe, command: str, context: dict[str, Any]) -> Any:
    payload = _result(
        probe, "omega_command", {"command": command, "doctor_context": context}
    )
    probe.check(isinstance(payload, dict), f"{command} returns a result object")
    return payload


def _workflow_steps(payload: Any) -> list[Any]:
    body = payload.get("result") if isinstance(payload, dict) else None
    steps = body.get("steps") if isinstance(body, dict) else None
    return steps if isinstance(steps, list) else []


def _assert_audit_entry(probe: ReadinessProbe, tool_name: str) -> None:
    """The outer slash dispatch leaves a real audit trail entry."""
    from omega_prime.grokbot.audit import default_audit_path

    candidates = [probe.layout.audit, default_audit_path(_runtime_root(probe))]
    texts: list[str] = []
    for candidate in candidates:
        path = Path(candidate)
        try:
            if path.is_file():
                texts.append(
                    path.read_bytes()[-200000:].decode("utf-8", errors="replace")
                )
            elif path.is_dir():
                for child in sorted(path.iterdir()):
                    if child.is_file():
                        try:
                            texts.append(
                                child.read_bytes()[-200000:].decode(
                                    "utf-8", errors="replace"
                                )
                            )
                        except OSError:
                            continue
        except OSError:
            continue
    probe.check(
        any(tool_name in text for text in texts),
        f"audit trail records the {tool_name} dispatch",
    )


def _slash_slice(probe: ReadinessProbe, context: dict[str, Any]) -> None:
    _onboard_positive(probe, context)
    _onboard_missing_context_negative(probe)
    _onboard_invalid_context_negative(probe)
    _desk_positive_empty_queue(probe, context)
    _connectors_slice(probe, context)
    _python_clean_slice(probe, context)
    _retain_recall_slice(probe, context)


def _onboard_positive(probe: ReadinessProbe, context: dict[str, Any]) -> None:
    payload = _slash_result(probe, "/omega-onboard", context)
    probe.check(payload.get("ok") is True, "contextual onboard reports overall ok")
    steps = _workflow_steps(payload)
    probe.check(
        [step.get("name") for step in steps]
        == ["lead_doctor", "lead_roster_status", "seed_memory", "connectors"],
        "onboard runs every inner step in source order",
    )
    for step in steps:
        probe.check(step.get("ok") is True, f"onboard step {step.get('name')} ok")

    doctor = steps[0].get("result") or {}
    probe.check(doctor.get("green") is True, "onboard doctor step is green")
    probe.check(
        all(
            row.get("green") is True
            for row in (doctor.get("checks") or {}).values()
            if isinstance(row, dict)
        ),
        "every onboard doctor sub-check is green",
    )

    roster = steps[1].get("result") or {}
    packs = roster.get("packs") or []
    probe.check(
        any(pack.get("pack") == "lead" for pack in packs if isinstance(pack, dict)),
        "onboard roster step shows the registered lead pack",
    )
    probe.check(roster.get("complete") is True, "onboard roster step is complete")
    queue = roster.get("intake_queue")
    probe.check(isinstance(queue, dict), "onboard roster step reports intake counts")

    seed = steps[2].get("result") or {}
    probe.check("error" not in seed, "onboard seed step retains without error")

    connectors = steps[3].get("result") or {}
    probe.check(
        isinstance(connectors.get("configured"), list)
        and isinstance(connectors.get("requests"), list),
        "onboard connectors step reports configured plus requests",
    )
    for row in connectors["requests"]:
        probe.check(
            isinstance(row, dict) and set(row) <= {"id", "env", "ask"},
            "connector request rows carry ids and env names only, never values",
        )

    _assert_audit_entry(probe, "omega_command")
    probe.record(
        "slash-onboard-positive",
        ("omega_command", "lead_doctor", "lead_roster_status", "memory"),
        "onboard green with explicit caller context; every step, audit entry, "
        "roster pack, and intake-transition shape asserted",
    )


def _assert_onboard_unhealthy(probe: ReadinessProbe, payload: Any, label: str) -> None:
    probe.check(
        isinstance(payload, dict) and payload.get("ok") is False,
        f"{label} onboard reports overall failure, never success",
    )
    steps = _workflow_steps(payload)
    probe.check(
        [step.get("name") for step in steps]
        == ["lead_doctor", "lead_roster_status", "seed_memory", "connectors"],
        f"{label} onboard still runs every subsequent step",
    )
    probe.check(steps[0].get("ok") is False, f"{label} doctor step fails")
    doctor = steps[0].get("result") or {}
    probe.check(doctor.get("green") is False, f"{label} doctor stays non-green")
    checks = doctor.get("checks") or {}
    probe.check(
        isinstance(checks, dict)
        and checks.get("prompt", {}).get("green") is False
        and checks.get("skills", {}).get("green") is False,
        f"{label} prompt and skills checks stay red",
    )
    for step in steps[1:]:
        probe.check(
            step.get("ok") is True,
            f"{label} subsequent step {step.get('name')} effects preserved",
        )


def _onboard_missing_context_negative(probe: ReadinessProbe) -> None:
    envelope = probe.emulator.execute_tool(
        "omega_command", {"command": "/omega-onboard"}
    )
    probe.check(
        envelope.get("status") == "error",
        "context-free onboard returns the genuine error envelope",
    )
    probe.check(
        envelope.get("error") == "workflow_incomplete",
        "context-free onboard names the incomplete workflow",
    )
    _assert_onboard_unhealthy(probe, envelope.get("result"), "missing-context")
    probe.record(
        "slash-onboard-missing-context-unhealthy",
        (),
        "context-free onboard returns the genuine workflow_incomplete error "
        "envelope with a red doctor step while later steps keep their "
        "effects; control-only, no fabricated seat state",
    )


def _onboard_invalid_context_negative(probe: ReadinessProbe) -> None:
    envelope = probe.emulator.execute_tool(
        "omega_command",
        {
            "command": "/omega-onboard",
            "doctor_context": {
                "prompt_sha256": "0" * 64,
                "installed_skills": ["no-such-skill"],
            },
        },
    )
    probe.check(
        envelope.get("status") == "error",
        "invalid-context onboard returns the genuine error envelope",
    )
    probe.check(
        envelope.get("error") == "workflow_incomplete",
        "invalid-context onboard names the incomplete workflow",
    )
    _assert_onboard_unhealthy(probe, envelope.get("result"), "invalid-context")
    probe.record(
        "slash-onboard-invalid-context-unhealthy",
        (),
        "wrong-sha onboard returns the genuine workflow_incomplete error "
        "envelope with red prompt/skills checks while later steps keep "
        "their effects; control-only",
    )


def _desk_positive_empty_queue(probe: ReadinessProbe, context: dict[str, Any]) -> None:
    payload = _slash_result(probe, "/omega-desk", context)
    probe.check(payload.get("ok") is True, "contextual desk reports overall ok")
    steps = _workflow_steps(payload)
    probe.check(
        [step.get("name") for step in steps]
        == ["lead_doctor", "lead_roster_status", "lead_intake_next"],
        "desk runs doctor, roster, and intake-next steps",
    )
    for step in steps:
        probe.check(step.get("ok") is True, f"desk step {step.get('name')} ok")
    probe.check(
        (steps[0].get("result") or {}).get("green") is True,
        "desk doctor step is green",
    )
    claimed = steps[2].get("result") or {}
    probe.check(
        claimed.get("work_order") is None,
        "desk on an empty queue claims nothing",
    )
    queue = claimed.get("queue") or {}
    probe.check(isinstance(queue, dict), "desk intake-next step reports queue counts")
    probe.check(queue.get("open", 0) == 0, "empty queue shows zero open")
    probe.check(queue.get("in_progress", 0) == 0, "empty queue shows zero in progress")
    roster_counts = (steps[1].get("result") or {}).get("intake_queue") or {}
    probe.check(roster_counts.get("open", 0) == 0, "roster agrees zero open")
    probe.check(
        roster_counts.get("in_progress", 0) == 0, "roster agrees zero in progress"
    )
    probe.record(
        "slash-desk-positive",
        ("omega_command", "lead_doctor", "lead_roster_status", "lead_intake_next"),
        "desk green with explicit caller context over an empty queue",
    )


def _connectors_slice(probe: ReadinessProbe, context: dict[str, Any]) -> None:
    payload = _slash_result(probe, "/omega-connectors", context)
    probe.check(payload.get("ok") is True, "connectors reports ok")
    body = payload.get("result") or {}
    probe.check(
        isinstance(body.get("configured"), list)
        and isinstance(body.get("requests"), list),
        "connectors reports configured plus requests",
    )
    for row in body["requests"]:
        probe.check(
            isinstance(row, dict) and set(row) <= {"id", "env", "ask"},
            "connector request rows carry ids and env names only",
        )
    probe.record(
        "slash-connectors",
        ("omega_command",),
        f"{len(body['configured'])} connectors configured, "
        f"{len(body['requests'])} request live-provider setup",
    )
    missing = sorted(
        {
            name
            for row in body["requests"]
            if isinstance(row, dict)
            for name in (row.get("env") or [])
            if isinstance(name, str)
        }
    )
    if missing:
        probe.blocked(
            "slash-connectors-live-providers",
            (),
            tuple(missing),
            "connector requests name real host credential variables; ids: "
            + ", ".join(
                str(row.get("id")) for row in body["requests"] if isinstance(row, dict)
            ),
        )


def _python_clean_slice(probe: ReadinessProbe, context: dict[str, Any]) -> None:
    payload = _slash_result(probe, "/omega-python", context)
    probe.check(payload.get("ok") is True, "python-clean reports overall ok")
    body = payload.get("result") or {}
    probe.check(
        body.get("workflow") == "python-clean",
        "omega-python runs the python-clean workflow, never the gates suite",
    )
    probe.check(
        "qua_gates_run" not in json.dumps(body),
        "python-clean involves no gates-run tool",
    )
    steps = body.get("steps") or []
    probe.check(len(steps) == 2, "python-clean runs ruff plus compileall steps")
    for step in steps:
        probe.check(step.get("name") == "run_terminal", "python-clean step is terminal")
        probe.check(step.get("ok") is True, "python-clean terminal step exits zero")
        probe.check(
            (step.get("result") or {}).get("exit_code") == 0,
            "python-clean exit code is zero under the disposable tree",
        )
    probe.record(
        "slash-python-clean",
        ("omega_command", "run_terminal"),
        "ruff check plus compileall both exit zero over the disposable install; "
        "source executables are the genuine prerequisite",
    )


def _retain_recall_slice(probe: ReadinessProbe, context: dict[str, Any]) -> None:
    marker = f"readiness-workflow-{uuid.uuid4().hex}"
    retained = _result(
        probe,
        "omega_command",
        {"command": f"/omega-retain --source readiness -- {marker}"},
    )
    probe.check(retained.get("ok") is True, "retain reports ok")

    recalled = _result(
        probe, "omega_command", {"command": f"/omega-recall {marker.split()[-1]}"}
    )
    probe.check(recalled.get("ok") is True, "recall reports ok")
    entries = (recalled.get("result") or {}).get("results") or []
    probe.check(
        any(
            isinstance(entry, dict) and marker in entry.get("content", "")
            for entry in entries
        ),
        "retained text roundtrips through recall",
    )
    probe.record(
        "slash-retain-recall",
        ("omega_command", "memory", "lead_memory_recall"),
        "retain stores one entry and unified recall returns its content envelope",
    )


# -- warm intake slice ---------------------------------------------------------


def _warm_intake_slice(probe: ReadinessProbe, context: dict[str, Any]) -> None:
    from omega_prime.tools.lead import IntakeStore

    # The probe runtime itself is the warm runtime: core constructed it before
    # any scenario ran, and nothing below rebuilds it.
    intake_path = _intake_path(probe)
    producer = IntakeStore(intake_path)
    ask = f"readiness warm proof {uuid.uuid4().hex}: stage the disposable note"
    order = producer.submit(ask, origin="local")
    order_id = order["intake_id"]
    probe.check(order.get("status") == "open", "producer order starts open")
    stored = json.loads(intake_path.read_text(encoding="utf-8"))
    probe.check(
        any(row.get("intake_id") == order_id for row in stored.get("orders", [])),
        "producer order persists in the shared intake file",
    )

    desk = _slash_result(probe, "/omega-desk", context)
    probe.check(desk.get("ok") is True, "warm desk reports overall ok")
    steps = {step.get("name"): step for step in _workflow_steps(desk)}
    claimed = (steps["lead_intake_next"].get("result") or {}).get("work_order")
    probe.check(isinstance(claimed, dict), "warm desk claims the producer order")
    assert isinstance(claimed, dict)
    probe.check(claimed.get("intake_id") == order_id, "claimed id matches producer")
    probe.check(claimed.get("ask") == ask, "claimed ask matches producer verbatim")
    queue = (steps["lead_intake_next"].get("result") or {}).get("queue") or {}
    probe.check(queue.get("open", 0) == 0, "claim drains the open order")
    probe.check(queue.get("in_progress", 0) == 1, "queue shows one in-progress claim")
    # The roster step runs before the claim step, so it sees the producer
    # order still open: before-open to claimed transition, not equal maps.
    pre_counts = (steps["lead_roster_status"].get("result") or {}).get(
        "intake_queue"
    ) or {}
    probe.check(pre_counts.get("open", 0) == 1, "roster sees the order open pre-claim")
    probe.check(pre_counts.get("in_progress", 0) == 0, "nothing in progress pre-claim")

    observer = IntakeStore(intake_path).get(order_id)
    probe.check(observer is not None, "fresh observer reads the claimed order")
    assert observer is not None
    probe.check(observer.get("status") == "in_progress", "claim persists in progress")
    probe.check(bool(observer.get("by")), "claim records the claiming bot")
    probe.check(observer.get("claimed_at") is not None, "claim records its timestamp")

    drained = _slash_result(probe, "/omega-workflow desk", context)
    probe.check(drained.get("ok") is True, "second desk run reports overall ok")
    drained_steps = {step.get("name"): step for step in _workflow_steps(drained)}
    drained_next = drained_steps["lead_intake_next"].get("result") or {}
    probe.check(
        drained_next.get("work_order") is None,
        "claimed queue offers nothing further",
    )
    probe.check(
        (drained_next.get("queue") or {}).get("in_progress", 0) == 1,
        "second desk run still sees the single claim",
    )
    probe.check(
        (drained_next.get("queue") or {}).get("open", 0) == 0,
        "second desk run sees nothing open",
    )

    scratch = f"readiness-warm-proof-{uuid.uuid4().hex[:8]}.txt"
    scratch_text = f"warm proof effects {uuid.uuid4().hex}"
    written = _result(probe, "write_file", {"path": scratch, "content": scratch_text})
    probe.check("error" not in written, "warm scratch file writes")
    read_back = _result(probe, "read_file", {"path": scratch})
    probe.check(
        read_back.get("content") == scratch_text,
        "warm scratch file reads back verbatim",
    )
    probe.check(
        (_coding_root(probe) / scratch).read_text(encoding="utf-8") == scratch_text,
        "warm effect lands in the disposable coding workspace",
    )

    graph_id = _unique("readiness-warm-graph")
    message = f"warm proof done {uuid.uuid4().hex[:8]}"
    links = ["local://readiness-warm-proof"]
    acked = _result(
        probe,
        "lead_intake_ack",
        {
            "intake_id": order_id,
            "status": "done",
            "graph_id": graph_id,
            "message": message,
            "links": links,
        },
    )
    probe.check(acked.get("ok") is True, "public ack reports ok")
    probe.check(
        (acked.get("intake") or {}).get("status") == "done",
        "public ack marks the order done",
    )

    final = IntakeStore(intake_path).get(order_id)
    probe.check(final is not None, "fresh observer reads the acked order")
    assert final is not None
    probe.check(final.get("status") == "done", "done persists for a fresh observer")
    probe.check(final.get("graph_id") == graph_id, "graph id persists")
    probe.check(final.get("message") == message, "ack message persists")
    probe.check(final.get("links") == links, "ack links persist")
    probe.check(bool(final.get("by")), "ack records the acking bot")

    probe.record(
        "warm-intake-delegation",
        (
            "omega_command",
            "lead_doctor",
            "lead_roster_status",
            "lead_intake_next",
            "write_file",
            "read_file",
            "lead_intake_ack",
        ),
        f"warm runtime predates independent producer {order_id}; original "
        "runtime claims without rebuild, performs a real file effect, acks "
        "publicly, and a fresh store confirms every persisted field",
    )
    with contextlib.suppress(OSError):
        (_coding_root(probe) / scratch).unlink()


# -- cron slice ----------------------------------------------------------------


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _cron_slice(probe: ReadinessProbe) -> None:
    from omega_prime.cron.heartbeat import HEARTBEAT_KIND
    from omega_prime.cron.scheduler import (
        DESK_LEAD_KIND,
        JobStore,
        run_desk_lead_passes,
    )
    from omega_prime.mcp_server import DESK_CHILD_MODEL_ENV, DESK_CHILD_PROVIDER_ENV
    from omega_prime.routines.desk_lead import DEFAULT_BOT
    from omega_prime.tools.lead import IntakeStore

    intake_path = _intake_path(probe)
    jobs_path = _cron_jobs_path(probe)
    work_root = _work_root(probe)

    blocked_ask = f"readiness cron blocked proof {uuid.uuid4().hex}"
    blocked_order = IntakeStore(intake_path).submit(blocked_ask, origin="local")

    now0 = time.time()
    created = _result(
        probe,
        "cron_job_create",
        {
            "kind": DESK_LEAD_KIND,
            "prompt": "readiness desk pass proof",
            "due_at": now0 - 5,
            "interval_seconds": 30,
            "intake_path": str(intake_path),
            "limit": 2,
        },
    )
    probe.check(
        isinstance(created.get("created"), str), "desk pass job creation returns an id"
    )
    job_id = created["created"]
    probe.check(created.get("kind") == DESK_LEAD_KIND, "created job has the desk kind")
    probe.check(created.get("interval_seconds") == 30, "created job keeps its interval")

    listed = _result(probe, "cron_jobs_list", {})
    probe.check(
        any(
            row.get("id") == job_id and row.get("kind") == DESK_LEAD_KIND
            for row in (listed.get("jobs") or [])
            if isinstance(row, dict)
        ),
        "desk pass job lists with its kind before the first tick",
    )

    heartbeat = _result(
        probe,
        "cron_job_create",
        {
            "kind": HEARTBEAT_KIND,
            "prompt": "readiness heartbeat placeholder",
            "due_at": now0 + 3600,
            "interval_seconds": 300,
            "session": "readiness-session",
        },
    )
    probe.check(
        isinstance(heartbeat.get("created"), str),
        "heartbeat job creation returns an id",
    )
    heartbeat_id = heartbeat["created"]
    probe.check(
        heartbeat.get("kind") == HEARTBEAT_KIND,
        "heartbeat job carries the session kind",
    )

    # First tick: no parent shim, so the pass blocks explicitly and requeues.
    tick1 = time.time()
    ran_blocked = run_desk_lead_passes(JobStore(jobs_path), tick1, None, work_root)
    probe.check(len(ran_blocked) == 1, "blocked tick fires the due desk pass once")
    probe.check(ran_blocked[0].get("id") == job_id, "blocked tick fires our job")
    blocked_result = ran_blocked[0].get("last_result") or {}
    blocked_tickets = blocked_result.get("tickets") or []
    probe.check(len(blocked_tickets) == 1, "blocked pass produces one ticket")
    ticket = blocked_tickets[0]
    probe.check(ticket.get("status") == "blocked", "pass without parent is blocked")
    probe.check(
        "no parent agent shim" in str(ticket.get("reason")),
        "blocked reason names the missing parent shim",
    )
    blocked_receipt = ticket.get("receipt") or {}
    probe.check(
        blocked_receipt.get("commands", [{}])[0].get("exit_code") == 1,
        "blocked ticket carries a failure receipt",
    )
    probe.check(
        blocked_receipt.get("task_id") == ticket.get("ticket_id"),
        "failure receipt is bound to the blocked ticket",
    )
    blocked_claims = blocked_receipt.get("claims") or []
    probe.check(
        any(
            claim.get("expects_failure") is True
            for claim in blocked_claims
            if isinstance(claim, dict)
        ),
        "failure receipt marks its claim expects_failure",
    )
    # The shim command never executed, so the public execution-backed
    # checker honestly rejects it: structured problems, never a
    # fabricated ok. Control-only lifecycle, not delivered evidence.
    blocked_verdict = _result(probe, "lead_receipt_check", {"receipt": blocked_receipt})
    probe.check(
        blocked_verdict.get("ok") is False,
        "uncaptured failure receipt is honestly rejected",
    )
    blocked_problems = blocked_verdict.get("problems") or []
    probe.check(
        isinstance(blocked_problems, list) and len(blocked_problems) > 0,
        "rejection carries structured problems",
    )
    probe.check(
        any("captured" in str(problem) for problem in blocked_problems),
        "rejection names the uncaptured shim command",
    )
    probe.record(
        "cron-blocked-pass-control",
        (),
        "parent-less desk pass: blocked ticket with an exit-1 failure receipt "
        "bound to the ticket and marked expects_failure; the public receipt "
        "checker rejects it with uncaptured-command problems; the order "
        "requeues open and the job reschedules; control-only",
    )
    reopened = IntakeStore(intake_path).get(blocked_order["intake_id"])
    probe.check(
        reopened is not None and reopened.get("status") == "open",
        "blocked pass requeues the order open",
    )
    rescheduled = next(
        job
        for job in JobStore(jobs_path).jobs
        if isinstance(job, dict) and job.get("id") == job_id
    )
    probe.check(
        rescheduled.get("due_at") == tick1 + 30,
        "blocked pass still reschedules on its interval",
    )

    # Second tick: only the existing ScriptedModel seam plus the real desk
    # parent, delegate driver, tools, and store. Labeled a model double below;
    # never provider proof.
    from omega_prime.agent.model import ScriptedModel
    from omega_prime.mcp_server import _DeskParent

    model_ask = f"readiness cron model-double proof {uuid.uuid4().hex}"
    model_order = IntakeStore(intake_path).submit(model_ask, origin="local")
    scratch = f"readiness-cron-proof-{uuid.uuid4().hex[:8]}.txt"
    file_text = f"cron model-double effect {uuid.uuid4().hex}"
    memo_marker = uuid.uuid4().hex
    memo = f"cron model-double memo {memo_marker} stored through the child tools"
    # Two tickets dispatch, so each child gets its own real tool turns:
    # file plus memory effects, then a final report. No child coasts on a
    # default empty response.
    child_model = ScriptedModel(
        [
            _tool_call(
                "write_file", {"path": scratch, "content": file_text}, "cron-file-1"
            ),
            _tool_call(
                "memory",
                {"action": "add", "target": "memory", "content": memo},
                "cron-memo-1",
            ),
            {
                "role": "assistant",
                "content": f"cron proof done first ticket: wrote {scratch}",
            },
            _tool_call(
                "write_file", {"path": scratch, "content": file_text}, "cron-file-2"
            ),
            _tool_call(
                "memory",
                {"action": "add", "target": "memory", "content": memo},
                "cron-memo-2",
            ),
            {
                "role": "assistant",
                "content": f"cron proof done second ticket: wrote {scratch}",
            },
        ]
    )
    desk_parent = _DeskParent(probe.registry, child_model)
    tick2 = tick1 + 31.0
    ran_model = run_desk_lead_passes(JobStore(jobs_path), tick2, desk_parent, work_root)
    probe.check(len(ran_model) == 1, "model-double tick fires the due desk pass")
    model_result = ran_model[0].get("last_result") or {}
    probe.check(
        (model_result.get("invalid") or []) == [],
        "model-double pass produces no invalid receipts",
    )
    # Both open orders (the requeued blocked proof plus the new one) dispatch
    # with limit 2; each must complete for real.
    done_tickets = model_result.get("tickets") or []
    probe.check(len(done_tickets) == 2, "model-double pass produces two tickets")
    for item in done_tickets:
        probe.check(
            isinstance(item, dict) and item.get("status") == "done",
            "every model-double ticket is done",
        )
    ours = next(
        item
        for item in done_tickets
        if item.get("intake_id") == model_order["intake_id"]
    )
    probe.check(
        model_ask in json.dumps(child_model.seen),
        "the ticket ask reaches the child turn",
    )
    probe.check(
        blocked_ask in json.dumps(child_model.seen),
        "the requeued ask reaches its own child turn",
    )
    probe.check(
        child_model.call_count == 6,
        "both children turn three times each instead of coasting",
    )
    probe.check(
        (_coding_root(probe) / scratch).read_text(encoding="utf-8") == file_text,
        "child file effect persists in the disposable workspace",
    )
    # Plain memory adds are not banked Hindsight entries, so lead recall
    # must never see them (bank isolation is correct). Prove the child
    # effect instead through a fresh production store over the same
    # disposable directory plus the independently reopened MEMORY.md bytes.
    from omega_prime.memory.store import MemoryStore

    fresh_memory = MemoryStore(_memory_dir(probe))
    probe.check(
        any(memo_marker in entry for entry in fresh_memory.memory_entries),
        "fresh store reloads the child memory entry",
    )
    memory_file = _memory_dir(probe) / "MEMORY.md"
    probe.check(memory_file.is_file(), "memory file exists in the disposable store")
    probe.check(
        memo_marker in memory_file.read_text(encoding="utf-8"),
        "reopened memory bytes persist the child entry",
    )
    receipts = {
        row.get("ticket_id"): row.get("receipt")
        for row in (model_result.get("receipts") or [])
        if isinstance(row, dict)
    }
    done_receipt = receipts.get(ours.get("ticket_id")) or {}
    probe.check(isinstance(done_receipt, dict), "model-double ticket has a receipt")
    # Production's own acceptance is structural: run_lead_pass calls
    # validate_receipt with no executions. Prove the receipt holds there.
    # The public execution-backed checker honestly reports uncaptured for
    # shim commands, which are never terminal executions; that rejection is
    # asserted as the true boundary, never flipped to ok.
    from omega_prime.receipts import ReceiptError, validate_receipt

    structural_problems: list[str] = []
    try:
        validate_receipt(done_receipt)
    except ReceiptError as exc:
        structural_problems = str(exc).splitlines() or [str(exc)]
    probe.check(
        structural_problems == [],
        "success receipt holds production structural validation",
    )
    tool_verdict = _result(probe, "lead_receipt_check", {"receipt": done_receipt})
    probe.check(
        tool_verdict.get("ok") is False,
        "shim success receipt honestly misses execution backing",
    )
    tool_problems = tool_verdict.get("problems") or []
    probe.check(
        any("captured" in str(problem) for problem in tool_problems),
        "tool boundary names the uncaptured shim command",
    )
    probe.check(
        done_receipt.get("bot") == DEFAULT_BOT,
        "model-double receipt names the desk bot",
    )
    # Real banked retain with the genuine pass receipt as evidence, then a
    # genuine lead-bank recall roundtrip through production bank isolation.
    receipt_rel = f"readiness-cron-receipt-{uuid.uuid4().hex[:8]}.json"
    receipt_write = _result(
        probe, "write_file", {"path": receipt_rel, "content": json.dumps(done_receipt)}
    )
    probe.check("error" not in receipt_write, "evidence receipt file writes")
    banked_marker = uuid.uuid4().hex
    banked = _result(
        probe,
        "lead_memory_retain",
        {
            "content": "cron desk proof memo "
            + banked_marker
            + " retained from a validating pass",
            "receipt_path": receipt_rel,
        },
    )
    probe.check(banked.get("ok") is True, "banked retain reports ok")
    probe.check(
        banked.get("local_saved") is True, "retain saves the unified local record"
    )
    banked_recall = _result(probe, "lead_memory_recall", {"query": banked_marker})
    banked_entries = banked_recall.get("results") or []
    probe.check(
        any(
            isinstance(entry, dict) and banked_marker in entry.get("content", "")
            for entry in banked_entries
        ),
        "banked memo roundtrips through unified lead recall",
    )
    settled = IntakeStore(intake_path).get(model_order["intake_id"])
    probe.check(
        settled is not None and settled.get("status") == "done",
        "model-double pass marks the order done",
    )
    earlier = IntakeStore(intake_path).get(blocked_order["intake_id"])
    probe.check(
        earlier is not None and earlier.get("status") == "done",
        "requeued order also completes through the model double",
    )
    journaled = next(
        job
        for job in JobStore(jobs_path).jobs
        if isinstance(job, dict) and job.get("id") == job_id
    )
    probe.check(
        journaled.get("due_at") == tick2 + 30,
        "model-double pass reschedules on its interval",
    )
    journal_tickets = (journaled.get("last_result") or {}).get("tickets") or []
    probe.check(
        isinstance(journaled.get("last_result"), dict)
        and any(
            t.get("intake_id") == model_order["intake_id"] and t.get("status") == "done"
            for t in journal_tickets
            if isinstance(t, dict)
        ),
        "the disposable cron jobs journal holds the done pass result",
    )

    probe.record(
        "cron-desk-pass-blocked-then-model-double",
        (
            "cron_job_create",
            "cron_jobs_list",
            "lead_memory_retain",
            "lead_memory_recall",
            "write_file",
            "memory",
        ),
        "desk_lead_pass with explicit intake path and limit 2: parent-less "
        "tick blocks with a ticket-bound expects_failure receipt the public "
        "checker honestly rejects (control-only), then a "
        "deterministic-model-double tick (ScriptedModel driving the real "
        "DeskParent, delegate runner, registry tools, and IntakeStore) "
        "produces a real file plus plain memory proven through a fresh "
        "store and reopened bytes, a real banked retain with the pass "
        "receipt as evidence roundtripping through lead recall, a receipt "
        "holding production structural validation, done status, and "
        "interval rescheduling in INSTALL/cron/jobs.json; the public "
        "checker honestly reports the shim command uncaptured",
    )
    probe.blocked(
        "cron-delegation-live-provider",
        (),
        (DESK_CHILD_MODEL_ENV, DESK_CHILD_PROVIDER_ENV),
        "desk passes ran parent-less and through a labeled ScriptedModel "
        "double only; no live provider model was configured or claimed",
    )
    # Public roster handler boundary: the main runtime has no configured
    # desk child provider/model/credential, so the genuine public
    # delegate_task honestly refuses. The refusal payload carries an error
    # envelope, so it goes through the emulator directly like the other
    # negatives; the ScriptedModel composition above proves local effects
    # only and never counts as this roster tool.
    public_refusal = probe.emulator.execute_tool(
        "delegate_task", {"goal": "readiness roster probe"}
    )
    probe.check(
        public_refusal.get("status") == "error",
        "public delegate_task refusal is an error envelope",
    )
    probe.check(
        public_refusal.get("error") == "not_configured: provider",
        "refusal names the missing provider",
    )
    probe.check(
        (public_refusal.get("result") or {}).get("error") == "not_configured: provider",
        "refusal payload carries no fabricated child",
    )
    probe.record(
        "cron-delegate-task-public-refusal",
        (),
        "public delegate_task returns not_configured: provider with no "
        "fabricated child; control-only",
    )
    probe.blocked(
        "cron-delegate-task-live-model",
        ("delegate_task",),
        (
            DESK_CHILD_MODEL_ENV,
            DESK_CHILD_PROVIDER_ENV,
            "XAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ),
        "public delegate_task needs a configured child model id plus a "
        "provider selector or provider key; the isolated fixture configures "
        "none",
    )
    for doomed in (job_id, heartbeat_id):
        removed = _result(probe, "cron_job_remove", {"job_id": doomed})
        probe.check(
            removed.get("removed") == doomed,
            f"cron job {doomed} removes",
        )
    remaining = _result(probe, "cron_jobs_list", {})
    remaining_ids = {
        row.get("id") for row in (remaining.get("jobs") or []) if isinstance(row, dict)
    }
    probe.check(job_id not in remaining_ids, "desk pass job no longer lists")
    probe.check(heartbeat_id not in remaining_ids, "heartbeat job no longer lists")
    probe.check(
        all(
            job.get("id") not in (job_id, heartbeat_id)
            for job in JobStore(jobs_path).jobs
            if isinstance(job, dict)
        ),
        "removed jobs are gone from the disposable cron journal",
    )
    probe.record(
        "cron-lifecycle-create-list-remove",
        ("cron_job_create", "cron_jobs_list", "cron_job_remove"),
        "desk_lead_pass (kind, interval, intake path, limit) plus "
        "session_heartbeat (kind, interval, session) create, list with kinds, "
        "then remove with journal cleanup",
    )
    with contextlib.suppress(OSError):
        (_coding_root(probe) / scratch).unlink()
    with contextlib.suppress(OSError):
        (_coding_root(probe) / receipt_rel).unlink()


# -- learning slice ------------------------------------------------------------


def _learning_slice(probe: ReadinessProbe) -> None:
    tag = uuid.uuid4().hex[:12]
    skill = f"readiness-proof-{tag}"
    marker = uuid.uuid4().hex
    lesson = (
        "---\n"
        f"name: {skill}\n"
        "description: Readiness proof lesson.\n"
        "---\n"
        f"\n# {skill}\n\n"
        f"Marker {marker}: always verify effects, never trust dispatch success.\n"
    )
    captured = _result(
        probe,
        "autolearn_turn",
        {"tool_calls": 3, "name": skill, "content": lesson},
    )
    probe.check(
        captured == {"learned": True, "skill": skill},
        "autolearn captures the substantive turn as a skill",
    )
    viewed = _result(probe, "skill_view", {"name": skill})
    probe.check(
        marker in str(viewed.get("content")),
        "captured lesson roundtrips through skill_view",
    )
    skill_file = _skills_root(probe) / skill / "SKILL.md"
    probe.check(skill_file.is_file(), "captured skill persists as SKILL.md")
    probe.check(
        skill_file.read_text(encoding="utf-8") == viewed["content"],
        "skill_view reads the actual persisted skill bytes",
    )
    probe.record(
        "learning-capture-roundtrip",
        ("autolearn_turn", "skill_view"),
        f"turn capture creates INSTALL skill {skill} and skill_view returns "
        "its persisted content",
    )

    note_text = "Readiness proof note."
    note = _result(
        probe,
        "advisor_note",
        {"note": note_text, "severity": "concern", "advisor": "readiness"},
    )
    probe.check(note.get("note") == note_text, "advisor note keeps its text")
    probe.check(note.get("severity") == "concern", "advisor note keeps severity")
    probe.check(note.get("advisor") == "readiness", "advisor note keeps advisor")
    probe.check(
        note.get("rendered") == '<advisory severity="concern" advisor="readiness">'
        "Readiness proof note.</advisory>",
        "advisor note renders the exact advisory block",
    )
    rendered = _result(probe, "advisor_render", {"notes": [note]})
    probe.check(
        rendered.get("rendered") == note["rendered"],
        "advisor render reproduces the advisory block",
    )
    probe.record(
        "learning-advisor-render",
        ("advisor_note", "advisor_render"),
        "local advisor note plus render asserted with exact result fields",
    )


# -- durable slice -------------------------------------------------------------


def _durable_slice(probe: ReadinessProbe) -> None:
    from omega_prime.durable.journal import TurnJournal

    journal_path = _durable_journal_path(probe)
    probe.check(
        not journal_path.exists(),
        "durable journal starts absent in the disposable install",
    )
    empty = _result(probe, "durable_status", {})
    probe.check(empty.get("journal") == "absent", "empty journal reports absent")
    probe.check(
        empty.get("path") == str(journal_path),
        "absent report names the actual cron journal path",
    )
    probe.check(empty.get("open_runs") == 0, "absent journal has no open runs")
    probe.check(empty.get("entry_count") == 0, "absent journal has no entries")

    tag = uuid.uuid4().hex[:8]
    run_closed = f"readiness-run-a-{tag}"
    run_open = f"readiness-run-b-{tag}"
    journal = TurnJournal(journal_path)
    journal.begin_run(run_closed)
    journal.append(run_closed, {"event": "proof-row-one"})
    journal.append(run_closed, {"event": "proof-row-two"})
    journal.finish_run(run_closed, "done", "proof complete")
    journal.begin_run(run_open)
    journal.append(run_open, {"event": "proof-row-open"})

    status = _result(probe, "durable_status", {})
    probe.check(status.get("journal") == "present", "written journal reports present")
    probe.check(
        status.get("path") == str(journal_path),
        "present report names the actual cron journal path",
    )
    probe.check(status.get("open_runs") == 1, "one run stays open")
    probe.check(status.get("entry_count") == 3, "all three rows count")
    probe.check(status.get("last_run_id") == run_open, "last run id matches")

    workflows_dir = _runtime_root(probe) / "workflows"
    expected_checkpoints = (
        len(list(workflows_dir.glob("*.json"))) if workflows_dir.is_dir() else 0
    )
    probe.check(
        status.get("workflow_checkpoints") == expected_checkpoints,
        "checkpoint count matches the actual workflows directory",
    )
    desk_turns = _desk_turns_path(probe)
    probe.check(
        str(desk_turns) != str(journal_path),
        "durable journal path differs from the desk-parent journal path",
    )
    probe.record(
        "durable-status-journal",
        ("durable_status",),
        "durable_status reads its actual journal INSTALL/cron/turns.sqlite "
        "plus INSTALL/workflows counts; it does not observe the desk-parent "
        "journal STATE/turns.sqlite or work-root desk state (paths proven "
        "distinct and effects verified above; ephemeral locations withheld)",
    )

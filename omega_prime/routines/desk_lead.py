"""Lead pass routine: claim intake, ticket, dispatch, consolidate, report.

The desk LEAD flow as an Omega Prime routine. Each open order becomes one ticket;
tickets dispatch through an injected `dispatch(ticket)` callable (the desk
never invents specialist work, so without a dispatcher tickets go blocked);
intakes are acked from the outcomes and every receipt is validated before
the report consolidates it. Invalid receipts are listed, never dropped.

Production wiring (Phase 64 DESK-04): :func:`make_dispatch` builds the real
dispatch closure — ticket → one child agent turn through the runtime's parent
shim → a receipt dict in the :mod:`omega_prime.receipts` shape — and
:func:`desk_intake_path` names the one ``IntakeStore`` file the lead tools and
the cron ``desk_lead_pass`` job kind (``omega_prime.cron.scheduler``) share.
A dispatcher that cannot run a child returns an explicit blocked outcome with
a failure receipt; success is never fabricated. ``dispatch=None`` stays a
test-only shape: production callers always pass a closure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from omega_prime.receipts import ReceiptError, validate_receipt
from omega_prime.tools.lead import IntakeStore
from omega_prime.tools.todo import TodoStore, todo_write

DEFAULT_BOT = "bot-00-omega-prime"

#: The state-dir environment variable the lead tools (64-A wiring) and the
#: cron desk pass both resolve the shared stores from.
STATE_DIR_ENV = "OMEGA_PRIME_STATE_DIR"


def desk_intake_path(
    work_root: str | Path | None = None,
    *,
    state_dir: str | Path | None = None,
) -> Path:
    """The one ``IntakeStore`` file the lead tools and the cron job share.

    ``OMEGA_PRIME_STATE_DIR`` wins — the lead tools build their stores under
    it — so a cron pass and ``lead_intake_next``/``lead_intake_ack`` see the
    same records. Without that env, the work root's ``omega_prime/state``
    directory is used. Pass ``state_dir`` explicitly in tests to pin the file.
    """
    if state_dir is None:
        state_dir = os.environ.get(STATE_DIR_ENV) or ""
    if state_dir:
        return Path(state_dir) / "intake.json"
    base = Path(work_root) if work_root is not None else Path.cwd()
    return base / "omega_prime" / "state" / "intake.json"


def make_dispatch(
    parent: Any,
    work_root: str | Path | None = None,
    *,
    bot: str = DEFAULT_BOT,
    env: Mapping[str, str] | None = None,
) -> Callable[[dict], dict]:
    """Build the production dispatch closure for :func:`run_lead_pass`.

    ``parent`` is the runtime's in-process agent shim (``runtime.parent``):
    an object with ``child_model`` (and ``tools``) able to spawn child agent
    turns, or ``None`` when no provider environment is configured. Each
    ticket becomes one child goal through the shim; the child's outcome
    becomes a receipt in the :mod:`omega_prime.receipts` shape — a real
    receipt for a real child run, and an explicit blocked outcome with a
    failure receipt when the parent is missing or the child fails. Success
    is never fabricated.
    """

    def dispatch(ticket: dict) -> dict:
        ticket_id = str(ticket.get("ticket_id") or "ticket")
        cmd = "delegate_task(goal=desk ticket)"
        ask = str(ticket.get("ask") or "").strip()
        root_line = f"Work root: {work_root}\n" if work_root is not None else ""
        goal = (
            f"Desk ticket {ticket_id} (intake {ticket.get('intake_id')}).\n"
            f"Ask: {ask}\n"
            f"{root_line}\n"
            "Do the work with your tools inside the work root, then reply "
            "with a short final report of what you changed and verified."
        )
        if parent is None:
            if env is not None:
                from omega_prime.engine.attach import desk_engine_dispatch

                routed = desk_engine_dispatch(goal, env)
                if routed is not None:
                    receipt = routed.get("receipt")
                    if routed.get("status") == "done" and isinstance(receipt, dict):
                        receipt["task_id"] = ticket_id
                        receipt["bot"] = bot
                    return routed
            reason = "no parent agent shim: no provider environment configured"
            return {
                "status": "blocked",
                "reason": reason,
                "receipt": _failure_receipt(bot, ticket_id, cmd, reason, reason),
            }
        from omega_prime.agent.delegate import delegate_task  # late import: no cycle

        try:
            raw = delegate_task(parent, goal)
        except Exception as exc:  # model/provider failure is an explicit block
            reason = f"child dispatch raised {type(exc).__name__}: {exc}"
            return {
                "status": "blocked",
                "reason": reason,
                "receipt": _failure_receipt(bot, ticket_id, cmd, reason, str(exc)),
            }
        summary, error = _child_summary(raw)
        if error is not None:
            reason = f"child agent failed: {error}"
            return {
                "status": "blocked",
                "reason": reason,
                "receipt": _failure_receipt(bot, ticket_id, cmd, reason, error),
            }
        receipt = {
            "task_id": ticket_id,
            "bot": bot,
            "commands": [
                {
                    "cmd": cmd,
                    "exit_code": 0,
                    "output_tail": summary[-2000:],
                }
            ],
            "claims": [
                {
                    "claim": (
                        f"desk ticket {ticket_id} dispatched through the "
                        "parent shim; the child agent turned once and "
                        "reported a final response"
                    ),
                    "evidence_command_index": 0,
                }
            ],
            "unverified": [
                "the child's final response is not independently re-executed"
            ],
            "destructive": False,
            "approved_by": None,
        }
        return {"status": "done", "receipt": receipt, "summary": summary}

    return dispatch


def _child_summary(raw: Any) -> tuple[str, str | None]:
    """Extract the child's summary from a ``delegate_task`` result.

    Returns ``(summary, None)`` on a real child response and
    ``("", error)`` when the delegate refused or the child said nothing.
    """
    if not isinstance(raw, str):
        return "", f"delegate returned {type(raw).__name__}, not a string"
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return "", error
        summary = payload.get("summary", payload.get("summaries"))
        if isinstance(summary, list):
            summary = "\n".join(str(item) for item in summary)
        if isinstance(summary, str):
            return summary, None
        return "", "delegate result has no summary"
    if not raw.strip():
        return "", "empty final response"
    return raw, None


def _failure_receipt(
    bot: str, ticket_id: str, cmd: str, reason: str, tail: str
) -> dict:
    """An explicit, shape-valid failure receipt: the dispatch did not hold."""
    return {
        "task_id": ticket_id,
        "bot": bot,
        "commands": [{"cmd": cmd, "exit_code": 1, "output_tail": str(tail)[-2000:]}],
        "claims": [
            {
                "claim": reason,
                "evidence_command_index": 0,
                "expects_failure": True,
            }
        ],
        "unverified": [],
        "destructive": False,
        "approved_by": None,
    }


def run_lead_pass(
    intake: IntakeStore,
    dispatch: Callable[[dict], dict] | None = None,
    *,
    todos: TodoStore | None = None,
    limit: int = 5,
    by: str = DEFAULT_BOT,
) -> dict[str, Any]:
    """Run one lead pass over the intake queue. Return tickets + report."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    tickets: list[dict] = []
    receipts: list[dict] = []
    invalid: list[dict] = []
    seen: set[str] = set()
    while len(tickets) < limit:
        record = None
        skipped: list[dict] = []
        while True:
            candidate = intake.next(None, by)
            if candidate is None:
                break
            if candidate["intake_id"] in seen:
                skipped.append(candidate)
                continue
            record = candidate
            break
        for other in skipped:
            intake.ack(other["intake_id"], {"status": "open", "by": by})
        if record is None:
            break
        seen.add(record["intake_id"])
        ticket = {
            "ticket_id": f"t-{record['intake_id']}",
            "intake_id": record["intake_id"],
            "ask": record["ask"],
            "status": "dispatched",
        }
        if dispatch is None:
            ticket["status"] = "blocked"
            ticket["reason"] = "no dispatcher"
            intake.ack(record["intake_id"], {"status": "open", "by": by})
            tickets.append(ticket)
            continue
        outcome = dispatch(ticket)
        if isinstance(outcome, dict) and outcome.get("status") == "blocked":
            # An explicit dispatcher block: requeue with the dispatcher's
            # reason and keep the failure receipt on the ticket — listed,
            # never dropped, never read as done.
            ticket["status"] = "blocked"
            ticket["reason"] = str(
                outcome.get("reason") or "dispatcher reported blocked"
            )
            failed = outcome.get("receipt")
            if isinstance(failed, dict):
                ticket["receipt"] = failed
                try:
                    validate_receipt(failed)
                except ReceiptError as exc:
                    invalid.append(
                        {
                            "ticket_id": ticket["ticket_id"],
                            "problems": str(exc).splitlines(),
                        }
                    )
            intake.ack(record["intake_id"], {"status": "open", "by": by})
            tickets.append(ticket)
            continue
        receipt = outcome.get("receipt") if isinstance(outcome, dict) else None
        if not isinstance(receipt, dict):
            ticket["status"] = "blocked"
            ticket["reason"] = "dispatcher returned no receipt"
            intake.ack(record["intake_id"], {"status": "open", "by": by})
            tickets.append(ticket)
            continue
        try:
            validate_receipt(receipt)
        except ReceiptError as exc:
            ticket["status"] = "blocked"
            ticket["reason"] = f"invalid receipt: {exc}"
            invalid.append(
                {"ticket_id": ticket["ticket_id"], "problems": str(exc).splitlines()}
            )
            intake.ack(record["intake_id"], {"status": "open", "by": by})
            tickets.append(ticket)
            continue
        ticket["status"] = "done"
        receipts.append({"ticket_id": ticket["ticket_id"], "receipt": receipt})
        intake.ack(
            record["intake_id"],
            {"status": "done", "by": by, "message": f"{ticket['ticket_id']} done"},
        )
        tickets.append(ticket)
    if todos is not None:
        todo_write(
            todos,
            [
                {"content": f"{t['ticket_id']}: {t['ask']}", "status": t["status"]}
                for t in tickets
            ],
        )
    done = sum(1 for t in tickets if t["status"] == "done")
    blocked = [t["ticket_id"] for t in tickets if t["status"] == "blocked"]
    lines = [f"{done}/{len(tickets)} tickets done."]
    for row in receipts:
        lines.append(f"- {row['ticket_id']}: receipt holds.")
    for ticket_id in blocked:
        lines.append(f"- {ticket_id}: blocked.")
    return {
        "tickets": tickets,
        "receipts": receipts,
        "invalid": invalid,
        "report": "\n".join(lines),
    }


_INSTALL_ROOT = Path(__file__).resolve().parents[2]
_WORK_ROOT_ENV = "OMEGA_PRIME_WORK_ROOT"
_CHILD_MODEL_ENV = "OMEGA_PRIME_DESK_CHILD_MODEL"


class _HostScopedParent:
    """Child parent whose tools re-enter the host call path.

    ``child_model`` is the host's model when the registry already has one,
    so ticket text uses that transport and credential broker. Tool calls go
    through :func:`omega_prime.commands.workflows.dispatch_tool`, which
    honors the active roster and interceptors. Names outside the served
    roster are not offered.
    """

    def __init__(self, model: Any, registry: Any, journal: Any = None) -> None:
        self.child_model = model
        self._registry = registry
        self.delegate_depth = 0
        self.journal = journal

    @property
    def tools(self) -> dict[str, Any]:
        from omega_prime.commands.context import get_served_roster
        from omega_prime.commands.workflows import dispatch_tool

        roster = get_served_roster()
        policy = getattr(self._registry, "policy", None)
        allows = getattr(policy, "allows_tool", None)
        names: list[str] = []
        for item in self._registry.schemas():
            function = item.get("function") if isinstance(item, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(name, str) or name == "":
                continue
            if roster is not None and name not in roster:
                continue
            if callable(allows) and not allows(name):
                continue
            names.append(name)

        def _call(tool: str) -> Any:
            def run(**arguments: Any) -> str:
                payload = dispatch_tool(self._registry, tool, arguments)
                return json.dumps(payload)

            return run

        return {name: _call(name) for name in names}


def host_intake_path(
    env: Mapping[str, str] | None = None,
    *,
    state_dir_path: str | Path | None = None,
) -> Path | None:
    """The intake file the lead tools open, or None when no host is named.

    ``state_dir_path`` and ``OMEGA_PRIME_STATE_DIR`` win, matching
    :func:`desk_intake_path`. Otherwise the file is
    ``<work root>/omega_prime/state/intake.json`` from
    ``OMEGA_PRIME_WORK_ROOT`` or the install root — the same path
    ``lead_intake_next`` uses. ``HOME`` marks a live host; it does not
    move the queue to ``~/.local/state``. An empty caller env returns
    None so a unit test does not create a queue in the working tree.
    """
    if state_dir_path is not None:
        return desk_intake_path(state_dir=state_dir_path)
    mapping: Mapping[str, str] = os.environ if env is None else env
    configured = mapping.get(STATE_DIR_ENV, "").strip()
    if configured:
        return desk_intake_path(state_dir=configured)
    if not (
        mapping.get(_WORK_ROOT_ENV, "").strip()
        or mapping.get("HOME", "").strip()
        or mapping.get("XDG_STATE_HOME", "").strip()
    ):
        return None
    from omega_prime.mcp_server import resolve_work_root

    # Empty state_dir skips the process env, so the passed mapping wins.
    return desk_intake_path(resolve_work_root(_INSTALL_ROOT, mapping), state_dir="")


def _host_child_model(registry: Any) -> Any | None:
    bindings = getattr(registry, "runtime_bindings", None)
    if not isinstance(bindings, dict):
        return None
    driver = bindings.get("desk_driver")
    parent = getattr(driver, "parent", None)
    return getattr(parent, "child_model", None)


def _host_journal(registry: Any) -> Any:
    bindings = getattr(registry, "runtime_bindings", None)
    if not isinstance(bindings, dict):
        return None
    driver = bindings.get("desk_driver")
    return getattr(getattr(driver, "parent", None), "journal", None)


def _configured_parent(env: Mapping[str, str], registry: Any | None) -> Any | None:
    """The host parent, or None when this pass cannot run a child.

    A CLI without a registry stays blocked: an empty tool map would still
    mark the intake done. A registry reuses the host's child model so the
    seat policy and credential broker stay on the call. A registry that has
    no host model yet builds one with that registry's policy.
    """
    if registry is None or not callable(getattr(registry, "schemas", None)):
        return None
    model = _host_child_model(registry)
    if model is None and str(env.get(_CHILD_MODEL_ENV) or "").strip():
        from omega_prime.mcp_server import _desk_child_model

        model = _desk_child_model(dict(env), getattr(registry, "policy", None))
    if model is None:
        return None
    return _HostScopedParent(model, registry, _host_journal(registry))


def _dispatch_root(env: Mapping[str, str]) -> Path | None:
    raw = env.get(_WORK_ROOT_ENV, "").strip()
    if raw:
        return Path(raw)
    if env.get("HOME", "").strip() or env.get("XDG_STATE_HOME", "").strip():
        return _INSTALL_ROOT
    return None


def execute_desk_lead(
    env: Mapping[str, str] | None = None,
    *,
    limit: int = 5,
    state_dir_path: str | Path | None = None,
    registry: Any | None = None,
) -> dict[str, Any]:
    """Run one lead pass. Without a child model, tickets stay blocked.

    An empty caller env does not write ``omega_prime/state`` in the working
    tree.     A live host reads the same intake file as the lead tools. A child
    runs only through a registry, using the host model and the served
    roster. Without that registry, tickets stay blocked.
    """
    path = host_intake_path(env, state_dir_path=state_dir_path)
    if path is None:
        return {
            "error": "not_configured",
            "reason": (
                "Set OMEGA_PRIME_STATE_DIR or OMEGA_PRIME_WORK_ROOT, or run "
                "with HOME set, before the desk-lead pass."
            ),
            "tickets": [],
            "receipts": [],
            "invalid": [],
            "report": "",
            "publishes": False,
        }
    mapping: Mapping[str, str] = os.environ if env is None else env
    intake = IntakeStore(path)
    outcome = run_lead_pass(
        intake,
        make_dispatch(
            _configured_parent(mapping, registry),
            _dispatch_root(mapping),
            env=mapping,
        ),
        limit=limit,
    )
    outcome["publishes"] = False
    return outcome


def main(argv: list[str] | None = None) -> int:
    """``python -m omega_prime.routines.desk_lead``."""
    parser = argparse.ArgumentParser(prog="python -m omega_prime.routines.desk_lead")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    result = execute_desk_lead(
        os.environ, limit=args.limit, state_dir_path=args.state_dir
    )
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BOT",
    "STATE_DIR_ENV",
    "desk_intake_path",
    "execute_desk_lead",
    "host_intake_path",
    "main",
    "make_dispatch",
    "run_lead_pass",
]

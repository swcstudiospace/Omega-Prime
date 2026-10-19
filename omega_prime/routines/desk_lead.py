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
        if parent is None:
            reason = "no parent agent shim: no provider environment configured"
            return {
                "status": "blocked",
                "reason": reason,
                "receipt": _failure_receipt(bot, ticket_id, cmd, reason, reason),
            }
        ask = str(ticket.get("ask") or "").strip()
        root_line = f"Work root: {work_root}\n" if work_root is not None else ""
        goal = (
            f"Desk ticket {ticket_id} (intake {ticket.get('intake_id')}).\n"
            f"Ask: {ask}\n"
            f"{root_line}\n"
            "Do the work with your tools inside the work root, then reply "
            "with a short final report of what you changed and verified."
        )
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


def execute_desk_lead(
    env: Mapping[str, str] | None = None,
    *,
    limit: int = 5,
    state_dir_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run one lead pass. Without a child model, tickets stay blocked.

    An empty caller env does not write ``omega_prime/state`` in the working
    tree. A live host uses ``OMEGA_PRIME_STATE_DIR`` or ``HOME``.
    """
    from omega_prime.routines import state_dir

    if state_dir_path is not None:
        root: Path | None = Path(state_dir_path)
    else:
        root = state_dir(env)
    if root is None:
        return {
            "error": "not_configured",
            "reason": "Set OMEGA_PRIME_STATE_DIR (or HOME) before the desk-lead pass.",
            "tickets": [],
            "receipts": [],
            "invalid": [],
            "report": "",
            "publishes": False,
        }
    intake = IntakeStore(desk_intake_path(state_dir=root))
    outcome = run_lead_pass(intake, make_dispatch(None), limit=limit)
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
    "main",
    "make_dispatch",
    "run_lead_pass",
]

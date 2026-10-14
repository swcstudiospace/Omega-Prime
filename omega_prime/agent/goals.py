# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Persistent goals with budgets — a behavior port of Prime Agent's goals.

Source contract: ``pa-core/src/goals.rs`` + ``pa-daemon/src/goal_continuation.rs``
@ ``967eb13f`` (MIT, PrimeIntellect — see VENDOR.md).

A goal persists across turns until it is completed, paused, or cleared
(LOOP-01). This module extends the Omp :class:`GoalStore` (objective + ordered
steps) with Prime's loop-level semantics, kept in a sidecar document so the
base ``goals.json`` schema is unchanged and pre-v10 readers are unaffected:

- an optional token budget with per-turn usage accrual
  (``goal_token_delta_for_usage``),
- turn-boundary continuation prompts (the daemon's ``goal_continuation`` seam:
  while a goal is active and unfinished, the loop is steered to continue),
- stale-active detection (a goal with no accrual for ``stale_after_turns``),
- a completion report.

State is ``active`` / ``paused`` / ``completed``. A cleared goal is removed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from omega_prime.learning.goals import GoalStore

STATUSES: tuple[str, ...] = ("active", "paused", "completed")
_SIDECAR_NAME = "prime_goal.json"
DEFAULT_STALE_AFTER_TURNS = 3


def goal_token_delta_for_usage(usage: Any) -> int:
    """The tokens one turn consumed, from a model usage dict.

    Prime accrues ``prompt_tokens + completion_tokens`` (total when present).
    A missing/invalid usage yields 0 — accrual never raises.
    """
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    delta = 0
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            delta += value
    return delta


class PrimeGoalStore(GoalStore):
    """``GoalStore`` plus Prime goal semantics in a sidecar document."""

    def __init__(self, directory: str | Path) -> None:
        super().__init__(directory)
        self._sidecar_path = Path(directory) / _SIDECAR_NAME
        self._prime: dict[str, Any] = self._load_sidecar()

    # -- lifecycle ----------------------------------------------------------

    def set_objective(
        self,
        text: str,
        *,
        token_budget: int | None = None,
        stale_after_turns: int = DEFAULT_STALE_AFTER_TURNS,
    ) -> dict:
        """Set the objective and (re)activate the goal. Blank text is a ValueError."""
        _check_budget(token_budget)
        super().set_objective(text)
        self._prime = {
            "status": "active",
            "token_budget": token_budget,
            "tokens_used": 0,
            "turns_since_accrual": 0,
            "stale_after_turns": stale_after_turns,
            "set_at": _now(),
            "completed_at": None,
        }
        self._save_sidecar()
        return self.prime_status()

    def replace_goal(
        self,
        objective: str,
        steps: list[str],
        *,
        token_budget: int | None = None,
        stale_after_turns: int = DEFAULT_STALE_AFTER_TURNS,
    ) -> dict:
        """Replace the whole goal and return the Prime status with the new steps.

        The objective, budget, and every step are validated before anything is
        written (``ValueError`` on a blank objective or step, or a bad budget),
        so a rejected request leaves ``goals.json`` and ``prime_goal.json``
        untouched. The supplied ``steps`` replace any previous steps; the Prime
        state restarts as ``active`` with zero tokens used. Each document is
        written once. A crash between the two file writes is not covered:
        ``goals.json`` may then hold the new goal while the sidecar still holds
        the old Prime state.
        """
        if not isinstance(objective, str) or objective.strip() == "":
            raise ValueError("objective must be a non-empty string")
        _check_budget(token_budget)
        if any(not isinstance(step, str) or step.strip() == "" for step in steps):
            raise ValueError("step must be a non-empty string")
        self._document["objective"] = objective
        self._document["steps"] = [{"text": step, "done": False} for step in steps]
        self._save()
        self._prime = {
            "status": "active",
            "token_budget": token_budget,
            "tokens_used": 0,
            "turns_since_accrual": 0,
            "stale_after_turns": stale_after_turns,
            "set_at": _now(),
            "completed_at": None,
        }
        self._save_sidecar()
        return self.prime_status()

    def pause(self) -> dict:
        self._require_goal()
        self._prime["status"] = "paused"
        self._save_sidecar()
        return self.prime_status()

    def resume(self) -> dict:
        self._require_goal()
        self._prime["status"] = "active"
        self._prime["turns_since_accrual"] = 0
        self._save_sidecar()
        return self.prime_status()

    def complete(self) -> dict:
        """Mark the goal completed and return the completion report."""
        self._require_goal()
        self._prime["status"] = "completed"
        self._prime["completed_at"] = _now()
        self._save_sidecar()
        return self.completion_report()

    def clear(self) -> dict:
        """Remove the objective, steps, and Prime state."""
        self._document["objective"] = ""
        self._document["steps"] = []
        self._save()
        self._prime = {"status": "cleared"}
        self._save_sidecar()
        return self.prime_status()

    # -- per-turn accrual + continuation -------------------------------------

    def accrue_turn(self, usage: Any) -> dict:
        """Accrue one turn's token usage against the budget.

        Returns the Prime status after accrual. Accrual on a non-active goal
        is a no-op. Hitting or passing the budget does not complete the goal;
        the loop reads ``budget_exhausted`` and stops (LOOP-03).
        """
        if self._prime.get("status") != "active":
            return self.prime_status()
        delta = goal_token_delta_for_usage(usage)
        if delta > 0:
            self._prime["tokens_used"] += delta
            self._prime["turns_since_accrual"] = 0
        else:
            self._prime["turns_since_accrual"] += 1
        self._save_sidecar()
        return self.prime_status()

    def continuation_prompt(self) -> str | None:
        """The turn-boundary continuation prompt, or None when not continuing.

        Prime's daemon steers the loop to continue while a goal is active,
        unfinished, within budget, and not stale. Otherwise None.
        """
        if self._prime.get("status") != "active":
            return None
        if not self._document.get("objective"):
            return None
        if self.budget_exhausted():
            return None
        if self.is_stale():
            return None
        remaining = [
            step["text"] for step in self._document["steps"] if not step["done"]
        ]
        if not remaining:
            return None
        objective = escape(str(self._document["objective"]))
        next_step = escape(str(remaining[0]))
        return (
            f'<goal_continuation version="1">Continue working toward the goal: '
            f"{objective}. Next step: {next_step}.</goal_continuation>"
        )

    # -- status ---------------------------------------------------------------

    def budget_exhausted(self) -> bool:
        budget = self._prime.get("token_budget")
        return (
            isinstance(budget, int)
            and not isinstance(budget, bool)
            and self._prime.get("tokens_used", 0) >= budget
        )

    def is_stale(self) -> bool:
        if self._prime.get("status") != "active":
            return False
        return self._prime.get("turns_since_accrual", 0) >= self._prime.get(
            "stale_after_turns", DEFAULT_STALE_AFTER_TURNS
        )

    def completion_report(self) -> dict:
        """A structured report of the goal's outcome."""
        steps = self._document["steps"]
        return {
            "objective": self._document["objective"],
            "status": self._prime.get("status", "cleared"),
            "steps_total": len(steps),
            "steps_done": sum(1 for step in steps if step["done"]),
            "tokens_used": self._prime.get("tokens_used", 0),
            "token_budget": self._prime.get("token_budget"),
            "completed_at": self._prime.get("completed_at"),
        }

    def prime_status(self) -> dict:
        """The merged base + Prime status."""
        status = self.status()
        status.update(
            {
                "prime_status": self._prime.get("status", "cleared"),
                "token_budget": self._prime.get("token_budget"),
                "tokens_used": self._prime.get("tokens_used", 0),
                "budget_exhausted": self.budget_exhausted(),
                "stale": self.is_stale(),
            }
        )
        return status

    # -- sidecar persistence ---------------------------------------------------

    def _require_goal(self) -> None:
        if not self._document.get("objective"):
            raise ValueError("no active goal: set an objective first")

    def _load_sidecar(self) -> dict[str, Any]:
        if not self._sidecar_path.is_file():
            return {"status": "cleared"}
        try:
            document = json.loads(self._sidecar_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "cleared"}
        return document if isinstance(document, dict) else {"status": "cleared"}

    def _save_sidecar(self) -> None:
        self._sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        self._sidecar_path.write_text(
            json.dumps(self._prime, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _check_budget(token_budget: int | None) -> None:
    if token_budget is not None and (
        isinstance(token_budget, bool)
        or not isinstance(token_budget, int)
        or token_budget <= 0
    ):
        raise ValueError("token_budget must be a positive int or None")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [
    "DEFAULT_STALE_AFTER_TURNS",
    "STATUSES",
    "PrimeGoalStore",
    "goal_token_delta_for_usage",
]

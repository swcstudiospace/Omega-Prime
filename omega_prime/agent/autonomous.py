# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Bounded autonomous mode — a behavior port of Prime Agent's autonomous driver.

Source contract: ``pa-core/src/autonomous/`` (driver + gates) +
``pa-cli/src/print_autonomous.rs`` @ ``967eb13f`` (MIT, PrimeIntellect).

The engine holds no autonomous logic; the host drives. An
:class:`AutonomousDriver` is consulted at turn finalization: it decides
whether the loop continues (with an optional continuation prompt) or stops
cleanly with a structured reason. Budgets — max turns / tokens / minutes —
are normalized at start. A quality gate is a shell command run via the
terminal tool with a retry window; gate failure steers the loop with the
failure text.

Prime's honest semantics are preserved verbatim: **a passed gate checks only
what that gate verifies; reaching a limit does not imply task success.**
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omega_prime.tools.terminal import run_terminal

#: Stop reasons the driver can return (verbatim Prime continuation shape:
#: turn_end → turn_start with a continuation row, no run boundary).
STOP_MAX_TURNS = "autonomous_max_turns"
STOP_MAX_TOKENS = "autonomous_max_tokens"
STOP_MAX_MINUTES = "autonomous_max_minutes"
STOP_GATE_PASSED = "autonomous_gate_passed"
STOP_GATE_FAILED = "autonomous_gate_failed"
STOP_COMPLETED = "autonomous_completed"


@dataclass
class AutonomousBudget:
    """Normalized limits. ``None`` means unbounded on that axis."""

    max_turns: int | None = None
    max_tokens: int | None = None
    max_minutes: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive int or None")
        if self.max_minutes is not None and (
            isinstance(self.max_minutes, bool)
            or not isinstance(self.max_minutes, (int, float))
            or self.max_minutes <= 0
        ):
            raise ValueError("max_minutes must be a positive number or None")


@dataclass
class AutonomousDriver:
    """Drives one autonomous run within budgets, behind a quality gate.

    ``gate`` is a shell command (argv list) run via the terminal tool after
    each turn; ``gate_retries`` is the retry window before a gate failure
    stops the run. ``root`` bounds the gate's working directory.
    """

    root: str | Path
    budget: AutonomousBudget = field(default_factory=AutonomousBudget)
    gate: list[str] | None = None
    gate_retries: int = 1
    gate_timeout: float = 60.0
    _turns: int = 0
    _tokens: int = 0
    _started_monotonic: float | None = None
    _stopped: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.gate, str):
            raise ValueError("gate must be an argv list, not a shell string")
        if self.gate is not None and not self.gate:
            raise ValueError("gate must be a non-empty argv list")
        if isinstance(self.gate_retries, bool) or self.gate_retries < 0:
            raise ValueError("gate_retries must be >= 0")

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Normalize the budget window. Called once before the first turn."""
        self._turns = 0
        self._tokens = 0
        self._started_monotonic = time.monotonic()
        self._stopped = None

    @property
    def running(self) -> bool:
        return self._started_monotonic is not None and self._stopped is None

    # -- turn finalization ------------------------------------------------------

    def after_turn(self, result: dict) -> dict:
        """Consult the driver after one turn. Returns a verdict dict.

        ``{"action": "continue", "prompt": str}`` to steer the loop on, or
        ``{"action": "stop", "reason": str, ...}`` to stop cleanly. Budgets
        are checked first (a limit stop is immediate and honest); the gate
        runs only when the turn produced a completed text response.
        """
        if self._stopped is not None:
            return {"action": "stop", "reason": self._stopped}
        self._turns += 1
        self._tokens += _usage_tokens(result)

        limit = self._limit_reached()
        if limit is not None:
            return self._stop(limit)

        # A turn that did not complete (budget/interrupt/failure) is not
        # gated; the loop's own exit reason stands.
        if not result.get("completed"):
            return self._stop(STOP_COMPLETED, detail=result.get("turn_exit_reason"))

        gate = self._run_gate()
        if gate is not None:
            return gate

        return {
            "action": "continue",
            "prompt": (
                '<autonomous_continuation version="1">'
                "Continue autonomously toward the goal."
                "</autonomous_continuation>"
            ),
            "turns": self._turns,
            "tokens": self._tokens,
        }

    # -- internals ----------------------------------------------------------------

    def _limit_reached(self) -> str | None:
        budget = self.budget
        if budget.max_turns is not None and self._turns >= budget.max_turns:
            return STOP_MAX_TURNS
        if budget.max_tokens is not None and self._tokens >= budget.max_tokens:
            return STOP_MAX_TOKENS
        if budget.max_minutes is not None and self._started_monotonic is not None:
            elapsed_minutes = (time.monotonic() - self._started_monotonic) / 60.0
            if elapsed_minutes >= budget.max_minutes:
                return STOP_MAX_MINUTES
        return None

    def _run_gate(self) -> dict | None:
        """Run the quality gate with its retry window. None ⇒ keep going."""
        if self.gate is None:
            return None
        attempts = self.gate_retries + 1
        last: dict = {}
        for _ in range(attempts):
            last = run_terminal(self.root, self.gate, timeout=self.gate_timeout)
            if last.get("exit_code") == 0:
                # A passed gate checks only what that gate verifies.
                return self._stop(
                    STOP_GATE_PASSED,
                    gate=list(self.gate),
                    note="gate passed; this verifies only what the gate checks",
                )
        return self._stop(
            STOP_GATE_FAILED,
            gate=list(self.gate),
            stderr=last.get("stderr", ""),
            note="gate failed after the retry window; steered with failure text",
        )

    def _stop(self, reason: str, **extra: Any) -> dict:
        self._stopped = reason
        return {
            "action": "stop",
            "reason": reason,
            "turns": self._turns,
            "tokens": self._tokens,
            **extra,
        }

    def status(self) -> dict:
        return {
            "running": self.running,
            "turns": self._turns,
            "tokens": self._tokens,
            "stopped": self._stopped,
            "budget": {
                "max_turns": self.budget.max_turns,
                "max_tokens": self.budget.max_tokens,
                "max_minutes": self.budget.max_minutes,
            },
            "gate": list(self.gate) if self.gate else None,
        }


def _usage_tokens(result: dict) -> int:
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    return 0


__all__ = [
    "STOP_COMPLETED",
    "STOP_GATE_FAILED",
    "STOP_GATE_PASSED",
    "STOP_MAX_MINUTES",
    "STOP_MAX_TOKENS",
    "STOP_MAX_TURNS",
    "AutonomousBudget",
    "AutonomousDriver",
]

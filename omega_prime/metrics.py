# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Purple-team sidecar state gauge (Phase 79).

Exposes the current sidecar harness state (``IDLE``/``RUNNING``/``DEGRADED``/
``BLOCKED``/``ERROR``) as a Prometheus gauge, ``omega_sidecar_state``, for
the ``/metrics`` output. The gauge carries the state enum as a number only —
never RoE contents, operator names, or bearer material (T-79-06).

State values: ``IDLE`` 0, ``RUNNING`` 1, ``DEGRADED`` 2, ``BLOCKED`` 3,
``ERROR`` 4.

The registry argument is duck-typed (anything exposing ``gauge(name,
help)`` returning an object with ``set(float)``), so this module stays
import-light and works with
:mod:`omega_prime.grokbot.metrics` ``MetricsRegistry`` without importing it.
Attach the served registry's gauge from the host that owns ``/metrics``
using :func:`register_sidecar_state`; the MCP registration path records the
current verdict at ``registry.runtime_bindings["sidecar_state"]``.
"""

from __future__ import annotations

from typing import Any

SIDECAR_STATE_GAUGE = "omega_sidecar_state"
SIDECAR_STATE_HELP = (
    "Purple-team sidecar harness state "
    "(0=IDLE, 1=RUNNING, 2=DEGRADED, 3=BLOCKED, 4=ERROR)."
)

SIDECAR_STATE_VALUES = {
    "IDLE": 0.0,
    "RUNNING": 1.0,
    "DEGRADED": 2.0,
    "BLOCKED": 3.0,
    "ERROR": 4.0,
}


def sidecar_state_value(state: str) -> float:
    """Numeric gauge value for a sidecar state. Unknown states raise."""
    try:
        return SIDECAR_STATE_VALUES[state]
    except KeyError:
        raise ValueError(f"unknown sidecar state: {state!r}") from None


def register_sidecar_state(registry: Any, state: str) -> Any:
    """Register (or fetch) the gauge on ``registry`` and set it to ``state``."""
    gauge = registry.gauge(SIDECAR_STATE_GAUGE, SIDECAR_STATE_HELP)
    gauge.set(sidecar_state_value(state))
    return gauge


__all__ = [
    "SIDECAR_STATE_GAUGE",
    "SIDECAR_STATE_HELP",
    "SIDECAR_STATE_VALUES",
    "register_sidecar_state",
    "sidecar_state_value",
]

"""Autoscaling ephemeral GitHub Actions runners (phase 120, task 00050).

Event-driven, no timers: a ``workflow_job`` webhook feeds a pure
:class:`~omega_prime.ci_runners.scaler.Scaler`; the controller turns its
decisions into single-use JIT runners on the serverless Railway Desktop, and
Railway's own sleep is the teardown. Idle reaping is a one-shot delayed event.

The core (settings, jobs, webhook, scaler, delayed, publisher) is stdlib-only.
``cryptography`` (GitHub App JWT) and ``uvicorn`` (serving) load lazily.
"""

from __future__ import annotations

__all__: list[str] = []

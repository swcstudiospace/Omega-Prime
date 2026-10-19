# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""omp RPC engine.

Omega Prime supplies tools, skills, prompts, and loop policy. The agent loop
is ``omp --mode rpc``. This package never calls a model HTTP API.
"""

from omega_prime.engine.omp_driver import EngineError, EngineResult, run

__all__ = ["EngineError", "EngineResult", "run"]

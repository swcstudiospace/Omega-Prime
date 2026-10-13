"""Rules-of-engagement artifact schema and fail-closed validator (GAP-07).

Harness-side wrapper: the canonical implementation lives in
:mod:`omega_prime.redteam.roe` (shipped in the wheel and the image).
This module re-exports it so checkout workflows — ``sidecar/harness.py
--check`` and the operator signing command — keep working unchanged.
New code MUST import from ``omega_prime.redteam.roe`` directly.
"""

from __future__ import annotations

from omega_prime.redteam.roe import (
    CASE_CLASSES,
    CAUSE_EXPIRED,
    CAUSE_INVALID,
    CAUSE_MISSING,
    LOOPBACK_HOSTS,
    REQUIRED_KEYS,
    SCHEMA_VERSION,
    RoeDecision,
    authorize,
    canonical_bytes,
    grant_expired,
    grant_tool_allowlist,
    sign_artifact,
    signature_path,
    validate_schema,
    validate_targets,
    verify_signature,
)

__all__ = [
    "CASE_CLASSES",
    "CAUSE_EXPIRED",
    "CAUSE_INVALID",
    "CAUSE_MISSING",
    "LOOPBACK_HOSTS",
    "REQUIRED_KEYS",
    "SCHEMA_VERSION",
    "RoeDecision",
    "authorize",
    "canonical_bytes",
    "grant_expired",
    "grant_tool_allowlist",
    "sign_artifact",
    "signature_path",
    "validate_schema",
    "validate_targets",
    "verify_signature",
]

"""Rules-of-engagement artifact schema and fail-closed validator (GAP-07).

Stdlib-only. Never imports ``omega_prime.redteam`` or ``redteam/`` (the
submodule) at runtime, and performs no I/O beyond local files (fails_when:
no network, no fetching).

Signature design (documented choice): the artifact carries a *detached*
HMAC-SHA256 over the canonical JSON (adjacent ``<artifact>.sig`` file
holding the lowercase hex digest), verified with a config-supplied key
(``redteam.roe_key`` / ``OMEGA_PRIME_REDTEAM_ROE_KEY``) that is *never*
fetched at runtime. The stdlib has no asymmetric-signature verification, so
a public-key scheme would add a dependency for no operational gain here:
the key is provisioned out-of-band by the operator at signing time, and any
mismatch — missing key, missing ``.sig``, or digest inequality (compared
with :func:`hmac.compare_digest`) — fails closed to ``roe-invalid``.

Failure taxonomy (exact causes, never a generic error):

- ``roe-missing``: empty path, absent file, or unreadable file.
- ``roe-expired``: ``now`` is past ``effective_until``.
- ``roe-invalid``: unparseable JSON, schema mismatch, unsigned artifact,
  signature mismatch, not-yet-effective window, non-loopback
  ``authorized_targets`` entry, or unknown ``case_classes`` entry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CASE_CLASSES = (
    "prompt-injection",
    "jailbreak",
    "abuse",
    "malformed-input",
    "auth-bypass",
    "cost-abuse",
)

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

SCHEMA_VERSION = 1

REQUIRED_KEYS = (
    "schema_version",
    "operator",
    "authorized_targets",
    "effective_from",
    "effective_until",
    "case_classes",
)

CAUSE_MISSING = "roe-missing"
CAUSE_EXPIRED = "roe-expired"
CAUSE_INVALID = "roe-invalid"


@dataclass(frozen=True)
class RoeDecision:
    """Outcome of :func:`authorize`. ``cause`` is ``"ok"`` on success."""

    ok: bool
    cause: str
    detail: str
    roe: dict[str, Any] | None = None


def canonical_bytes(document: dict[str, Any]) -> bytes:
    """Canonical JSON bytes covered by the detached signature."""
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signature_path(artifact: str | Path) -> Path:
    """Adjacent ``.sig`` path for an artifact (``<artifact>.sig``)."""
    return Path(artifact).with_name(Path(artifact).name + ".sig")


def sign_artifact(artifact: str | Path, key: bytes) -> Path:
    """HMAC-sign a JSON artifact file; writes and returns the ``.sig`` path.

    Operator-side act (signing installs authorization). The artifact file
    must already hold the final canonical document.
    """
    if not key:
        raise ValueError("signing requires a non-empty key")
    document = json.loads(Path(artifact).read_text(encoding="utf-8"))
    digest = hmac.new(key, canonical_bytes(document), hashlib.sha256).hexdigest()
    sig_path = signature_path(artifact)
    sig_path.write_text(digest + "\n", encoding="utf-8")
    return sig_path


def verify_signature(document: dict[str, Any], sig_text: str, key: bytes) -> bool:
    """Detached-signature check with constant-time digest comparison."""
    if not key:
        return False
    expected = hmac.new(key, canonical_bytes(document), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_text.strip().lower())


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def validate_schema(document: Any) -> list[str]:
    """Structural check against the RoE schema. Empty list means valid."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["document must be a JSON object"]
    for key in REQUIRED_KEYS:
        if key not in document:
            errors.append(f"missing required key: {key}")
    if errors:
        return errors
    if document["schema_version"] != SCHEMA_VERSION:
        errors.append(
            f"unsupported schema_version: {document['schema_version']!r} "
            f"(expected {SCHEMA_VERSION})"
        )
    if not isinstance(document["operator"], str) or not document["operator"].strip():
        errors.append("operator must be a non-empty string")
    targets = document["authorized_targets"]
    if not isinstance(targets, list) or not targets:
        errors.append("authorized_targets must be a non-empty list")
    else:
        for index, entry in enumerate(targets):
            if not isinstance(entry, dict) or not isinstance(entry.get("host"), str):
                errors.append(f"authorized_targets[{index}] must name a host")
                continue
            tools = entry.get("tools", [])
            if not isinstance(tools, list) or not all(
                isinstance(tool, str) and tool for tool in tools
            ):
                errors.append(
                    f"authorized_targets[{index}].tools must be a string list"
                )
    for bound in ("effective_from", "effective_until"):
        if _parse_time(document.get(bound)) is None:
            errors.append(f"{bound} must be an RFC3339 datetime")
    classes = document["case_classes"]
    if not isinstance(classes, list) or not classes:
        errors.append("case_classes must be a non-empty list")
    else:
        unknown = [c for c in classes if c not in CASE_CLASSES]
        if unknown:
            errors.append(
                f"unknown case_classes: {', '.join(sorted(map(str, unknown)))}"
            )
    return errors


def validate_targets(document: dict[str, Any]) -> list[str]:
    """Loopback-only check. Empty list means every target is loopback."""
    errors: list[str] = []
    targets = document.get("authorized_targets")
    if not isinstance(targets, list):
        return ["authorized_targets must be a list"]
    for entry in targets:
        host = entry.get("host") if isinstance(entry, dict) else None
        if host not in LOOPBACK_HOSTS:
            errors.append(
                f"non-loopback authorized target refused: {host!r} "
                "(loopback-only contract)"
            )
    return errors


def _now(now: datetime | None) -> datetime:
    current = now if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current


def authorize(
    artifact: str | Path | None,
    *,
    key: bytes | None,
    now: datetime | None = None,
) -> RoeDecision:
    """Full fail-closed pipeline: load → schema → signature → window → scope.

    Returns a :class:`RoeDecision` with an exact cause; never raises on
    artifact problems (only on programmer errors such as a bad type).
    """
    path = str(artifact or "").strip()
    if not path:
        return RoeDecision(
            False,
            CAUSE_MISSING,
            "RoE missing: redteam.roe_file is empty — sidecar ships BLOCKED "
            "until the operator signs an artifact",
        )
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return RoeDecision(
            False, CAUSE_MISSING, f"RoE missing: cannot read {path!r} ({exc})"
        )
    try:
        document = json.loads(raw)
    except ValueError as exc:
        return RoeDecision(
            False, CAUSE_INVALID, f"RoE invalid: {path!r} is not JSON ({exc})"
        )
    schema_errors = validate_schema(document)
    if schema_errors:
        return RoeDecision(
            False,
            CAUSE_INVALID,
            f"RoE invalid: schema mismatch ({'; '.join(schema_errors)})",
        )
    if not key:
        return RoeDecision(
            False,
            CAUSE_INVALID,
            "RoE invalid: unsigned artifact — no verification key configured "
            "(redteam.roe_key is empty)",
        )
    sig_file = signature_path(path)
    try:
        sig_text = sig_file.read_text(encoding="utf-8")
    except OSError:
        return RoeDecision(
            False,
            CAUSE_INVALID,
            f"RoE invalid: unsigned artifact — missing signature file "
            f"{sig_file.name!r}",
        )
    if not verify_signature(document, sig_text, key):
        return RoeDecision(
            False,
            CAUSE_INVALID,
            "RoE invalid: signature mismatch — artifact tampered or wrong key",
        )
    current = _now(now)
    start = _parse_time(document["effective_from"])
    end = _parse_time(document["effective_until"])
    assert start is not None and end is not None  # guaranteed by validate_schema
    if current < start:
        return RoeDecision(
            False,
            CAUSE_INVALID,
            f"RoE invalid: not yet effective (effective_from "
            f"{document['effective_from']!r})",
        )
    if current > end:
        return RoeDecision(
            False,
            CAUSE_EXPIRED,
            f"RoE expired: effective_until {document['effective_until']!r} "
            "is in the past — re-sign to renew",
        )
    target_errors = validate_targets(document)
    if target_errors:
        return RoeDecision(
            False, CAUSE_INVALID, f"RoE invalid: {'; '.join(target_errors)}"
        )
    operator = document["operator"].strip()
    window = f"{document['effective_from']}..{document['effective_until']}"
    return RoeDecision(
        True,
        "ok",
        f"RoE authorizes operator {operator!r} for {window}",
        document,
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
    "sign_artifact",
    "signature_path",
    "validate_schema",
    "validate_targets",
    "verify_signature",
]

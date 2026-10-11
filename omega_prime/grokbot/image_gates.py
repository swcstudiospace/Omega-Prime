# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Fail-closed publish-gate identities for the Grok Bot image lane.

Pure helpers only: exact identity comparison across three distinct,
non-interchangeable domains (local image ID, archive checksum, registry
manifest digest) plus a gate evaluator that returns an explicit allow or a
fixed safe refusal naming the failing condition.

No orchestration, no network, no retry, no rebuild path. Any helper file
handling reuses the existing atomic writer and the unchanged secret gate, and
refusal text never echoes observed values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets
from omega_prime.tooling.fs import atomic_write_text

#: Fixed safe outcome text. Refusals name the failing condition only and never
#: echo observed SHAs, digests, refs, or run ids.
ALLOW_DETAIL = "publish allowed: validated bytes and source bound"
REFUSAL_MISSING_EVIDENCE = "publish refused: missing evidence"
REFUSAL_AMBIGUOUS_EVIDENCE = "publish refused: ambiguous evidence"
REFUSAL_UNPROTECTED_REF = "publish refused: unprotected ref"
REFUSAL_RUN_MISMATCH = "publish refused: workflow run mismatch"
REFUSAL_SOURCE_SHA_MISMATCH = "publish refused: source commit mismatch"
REFUSAL_ARCHIVE_MISMATCH = "publish refused: archive identity mismatch"
REFUSAL_IMAGE_MISMATCH = "publish refused: local image identity mismatch"
REFUSAL_CHECK_FAILED = "publish refused: required check did not pass"
REFUSAL_ENVIRONMENT_UNPROTECTED = "publish refused: environment protection unavailable"

_ALLOW = "allow"
_REFUSED = "refused"

_COMMIT_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_SHA256_HEX_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_IMAGE_ID_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_CHECK_NAME_RE = re.compile(r"\A[A-Za-z0-9:_.-]{1,128}\Z")

#: Markers that make a present value ambiguous rather than comparable. A
#: truncated digest, a wildcard, or a placeholder word is not evidence.
_AMBIGUOUS_TOKENS = frozenset(
    {"...", "…", "unknown", "ambiguous", "truncated", "missing", "null", "none", "n/a"}
)


@dataclass(frozen=True)
class ImageId:
    """Local image ID domain (``docker inspect --format '{{.Id}}'``)."""

    value: str


@dataclass(frozen=True)
class ArchiveChecksum:
    """Archive checksum domain (SHA-256 of the transferred image tarball)."""

    value: str


@dataclass(frozen=True)
class ManifestDigest:
    """Registry manifest digest domain (observed only after a push)."""

    value: str


#: The only checks that can authorize publication. Fixed here, never
#: caller-configurable: an empty or caller-supplied set must not allow.
REQUIRED_CHECKS: tuple[str, ...] = ("validation", "sbom", "scan")


def _is_missing(value: str) -> bool:
    return not value or not value.strip()


def _is_ambiguous(value: str) -> bool:
    lowered = value.strip().lower()
    if any(ch.isspace() for ch in value.strip()):
        return True
    if "*" in value or "?" in value:
        return True
    return lowered in _AMBIGUOUS_TOKENS


def _is_commit_sha(value: str) -> bool:
    return _COMMIT_SHA_RE.match(value) is not None


def _is_archive_checksum(value: str) -> bool:
    return _SHA256_HEX_RE.match(value) is not None


def _is_image_id(value: str) -> bool:
    return _IMAGE_ID_RE.match(value) is not None


def _is_manifest_digest(value: str) -> bool:
    return _IMAGE_ID_RE.match(value) is not None


def _is_run_id(value: str) -> bool:
    return value.isdigit() and len(value) > 0


def image_id_matches(expected: ImageId, observed: ImageId) -> bool:
    """Exact equality within the image-ID domain; never interchangeable."""
    if not isinstance(expected, ImageId) or not isinstance(observed, ImageId):
        raise TypeError("image IDs must both be ImageId")
    if not _is_image_id(expected.value) or not _is_image_id(observed.value):
        return False
    return expected.value == observed.value


def archive_checksum_matches(
    expected: ArchiveChecksum, observed: ArchiveChecksum
) -> bool:
    """Exact equality within the archive-checksum domain."""
    if not isinstance(expected, ArchiveChecksum) or not isinstance(
        observed, ArchiveChecksum
    ):
        raise TypeError("archive checksums must both be ArchiveChecksum")
    if not _is_archive_checksum(expected.value) or not _is_archive_checksum(
        observed.value
    ):
        return False
    return expected.value == observed.value


def manifest_digest_matches(expected: ManifestDigest, observed: ManifestDigest) -> bool:
    """Exact equality within the manifest-digest domain."""
    if not isinstance(expected, ManifestDigest) or not isinstance(
        observed, ManifestDigest
    ):
        raise TypeError("manifest digests must both be ManifestDigest")
    if not _is_manifest_digest(expected.value) or not _is_manifest_digest(
        observed.value
    ):
        return False
    return expected.value == observed.value


@dataclass(frozen=True)
class PublishGateEvidence:
    """Everything the publish gate binds. Empty values are missing evidence."""

    ref_protected: bool | None
    source_sha_expected: str
    source_sha_observed: str
    archive_expected: ArchiveChecksum
    archive_observed: ArchiveChecksum
    image_expected: ImageId
    image_observed: ImageId
    run_id_expected: str
    run_id_observed: str
    checks: Mapping[str, str]


@dataclass(frozen=True)
class GateDecision:
    """Explicit allow or named safe refusal. JSON-serializable via `to_dict`."""

    allowed: bool
    reason: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        """Stable mapping: `allowed`, `reason`, `detail`."""
        return {"allowed": self.allowed, "reason": self.reason, "detail": self.detail}


def _refuse(code: str, detail: str) -> GateDecision:
    return GateDecision(allowed=False, reason=f"{_REFUSED}:{code}", detail=detail)


def evaluate_publish_gate(evidence: PublishGateEvidence) -> GateDecision:
    """Allow only when every binding matches exactly; otherwise refuse closed.

    Missing evidence refuses before shape is considered; malformed, truncated,
    or placeholder values refuse as ambiguous; an unprotected or unknown ref,
    a run/source/archive/image mismatch, or any required check that is absent
    or not ``success`` refuses with the condition named. The first failing
    condition wins and no observed value enters the refusal text.
    """
    scalars = (
        evidence.source_sha_expected,
        evidence.source_sha_observed,
        evidence.archive_expected.value,
        evidence.archive_observed.value,
        evidence.image_expected.value,
        evidence.image_observed.value,
        evidence.run_id_expected,
        evidence.run_id_observed,
    )
    if evidence.ref_protected is None or any(_is_missing(v) for v in scalars):
        return _refuse("missing-evidence", REFUSAL_MISSING_EVIDENCE)
    shaped = (
        (_is_commit_sha, evidence.source_sha_expected),
        (_is_commit_sha, evidence.source_sha_observed),
        (_is_archive_checksum, evidence.archive_expected.value),
        (_is_archive_checksum, evidence.archive_observed.value),
        (_is_image_id, evidence.image_expected.value),
        (_is_image_id, evidence.image_observed.value),
        (_is_run_id, evidence.run_id_expected),
        (_is_run_id, evidence.run_id_observed),
    )
    if any(_is_ambiguous(v) for v in scalars) or any(
        not well_formed(value) for well_formed, value in shaped
    ):
        return _refuse("ambiguous-evidence", REFUSAL_AMBIGUOUS_EVIDENCE)
    if evidence.ref_protected is not True:
        return _refuse("unprotected-ref", REFUSAL_UNPROTECTED_REF)
    if evidence.run_id_observed != evidence.run_id_expected:
        return _refuse("run-mismatch", REFUSAL_RUN_MISMATCH)
    if evidence.source_sha_observed != evidence.source_sha_expected:
        return _refuse("source-sha-mismatch", REFUSAL_SOURCE_SHA_MISMATCH)
    if not archive_checksum_matches(
        evidence.archive_expected, evidence.archive_observed
    ):
        return _refuse("archive-mismatch", REFUSAL_ARCHIVE_MISMATCH)
    if not image_id_matches(evidence.image_expected, evidence.image_observed):
        return _refuse("image-mismatch", REFUSAL_IMAGE_MISMATCH)
    for name in REQUIRED_CHECKS:
        if evidence.checks.get(name) != "success":
            if _CHECK_NAME_RE.match(name) is not None:
                detail = f"{REFUSAL_CHECK_FAILED}: {name}"
            else:
                detail = REFUSAL_CHECK_FAILED
            return _refuse("check-failed", detail)
    return GateDecision(allowed=True, reason=_ALLOW, detail=ALLOW_DETAIL)


def write_decision_json(path: Path | str, decision: GateDecision) -> Path:
    """Atomically write a decision file after the unchanged secret gate.

    The payload carries fixed labels only, so the gate is defense in depth;
    a trip raises instead of writing.
    """
    target = Path(path)
    text = json.dumps(decision.to_dict(), sort_keys=True) + "\n"
    assert_no_secrets(text, "image-gate-decision")
    atomic_write_text(target, text)
    return target


#: Fixed conformance admission text. Refusals never echo report content.
CONFORMANCE_ALLOW_DETAIL = "conformance admitted"
CONFORMANCE_REFUSAL_INCOMPLETE = "conformance refused: incomplete evidence"
CONFORMANCE_REFUSAL_NOT_PASSING = "conformance refused: required proof did not pass"

#: The ten transport-independent verifier checks (real protocol fields).
_COMMON_CHECK_IDS: tuple[str, ...] = (
    "health",
    "ready",
    "auth_required",
    "auth_missing",
    "auth_query_token",
    "auth_invalid",
    "origin_rejected",
    "request_id",
    "manifest",
    "metrics",
)

#: The five per-transport verifier checks (real protocol fields).
_TRANSPORT_CHECK_STEPS: tuple[str, ...] = (
    "initialize",
    "list_tools",
    "call_tool",
    "unknown_tool",
    "gated_tool_refused",
)

#: The two genuine admin approval checks (never satisfiable by a skip).
_ADMIN_CHECK_IDS: tuple[str, ...] = (
    "admin:scope_enforced",
    "admin:approval_roundtrip",
)


def expected_conformance_ids(transport: str) -> tuple[str, ...]:
    """The exact 17 check IDs one passing per-transport report must carry."""
    if transport not in ("sse", "http"):
        raise ValueError("transport must be 'sse' or 'http'")
    return (
        _COMMON_CHECK_IDS
        + tuple(f"{transport}:{step}" for step in _TRANSPORT_CHECK_STEPS)
        + _ADMIN_CHECK_IDS
    )


def admit_conformance_report(
    report: Mapping[str, object],
    *,
    transport: str,
    verifier_exit: int,
) -> GateDecision:
    """Admit only a complete, passing, count-coherent report for `transport`.

    The report shape is the actual producer contract (`base_url`, `checks`,
    `counts`, `ok`, `server_version`): no other flag is read. Exactly the 17
    expected IDs must appear once each with status ``pass``, the reported
    counts must equal the recomputed rows (17 pass, zero fail, zero skip),
    ``ok`` must be true, and the observed verifier exit must be zero.
    Duplicates, unknown IDs, skips, a single-transport or partial row set, or
    incoherent counts all refuse. No observed value enters the refusal text.
    """
    expected = set(expected_conformance_ids(transport))
    if verifier_exit != 0:
        return _refuse("proof-not-passing", CONFORMANCE_REFUSAL_NOT_PASSING)
    if not isinstance(report, dict):
        return _refuse("incomplete-evidence", CONFORMANCE_REFUSAL_INCOMPLETE)
    raw_checks = report.get("checks")
    raw_counts = report.get("counts")
    if not isinstance(raw_checks, list) or not isinstance(raw_counts, dict):
        return _refuse("incomplete-evidence", CONFORMANCE_REFUSAL_INCOMPLETE)
    rows: list[tuple[str, str]] = []
    for entry in raw_checks:
        if not isinstance(entry, dict):
            return _refuse("incomplete-evidence", CONFORMANCE_REFUSAL_INCOMPLETE)
        check_id = entry.get("id")
        status = entry.get("status")
        if not isinstance(check_id, str) or not isinstance(status, str):
            return _refuse("incomplete-evidence", CONFORMANCE_REFUSAL_INCOMPLETE)
        rows.append((check_id, status))
    ids = [check_id for check_id, _ in rows]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        return _refuse("incomplete-evidence", CONFORMANCE_REFUSAL_INCOMPLETE)
    recomputed = {"pass": 0, "fail": 0, "skip": 0}
    for _, status in rows:
        if status not in recomputed:
            return _refuse("proof-not-passing", CONFORMANCE_REFUSAL_NOT_PASSING)
        recomputed[status] += 1
    reported = raw_counts
    coherent = (
        isinstance(reported.get("pass"), int)
        and not isinstance(reported.get("pass"), bool)
        and isinstance(reported.get("fail"), int)
        and not isinstance(reported.get("fail"), bool)
        and isinstance(reported.get("skip"), int)
        and not isinstance(reported.get("skip"), bool)
        and reported.get("pass") == recomputed["pass"]
        and reported.get("fail") == recomputed["fail"]
        and reported.get("skip") == recomputed["skip"]
    )
    if (
        not coherent
        or recomputed != {"pass": 17, "fail": 0, "skip": 0}
        or report.get("ok") is not True
    ):
        return _refuse("proof-not-passing", CONFORMANCE_REFUSAL_NOT_PASSING)
    return GateDecision(allowed=True, reason=_ALLOW, detail=CONFORMANCE_ALLOW_DETAIL)


class EvidenceError(ValueError):
    """An evidence file is missing, gated, malformed, or wrongly shaped."""


#: Exact evidence-file schema. No extra keys, no coercion, no guessing.
_EVIDENCE_KEYS: tuple[str, ...] = (
    "artifact",
    "archive_sha256",
    "bytes_artifact",
    "bytes_artifact_id",
    "checks",
    "event",
    "image_id",
    "ref",
    "run_id",
    "source_sha",
)


def _check_evidence_record(record: object) -> dict[str, object]:
    if not isinstance(record, dict):
        raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE)
    if set(record) != set(_EVIDENCE_KEYS):
        raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE)
    for key in _EVIDENCE_KEYS:
        if key == "checks":
            continue
        value = record[key]
        if not isinstance(value, str) or not value:
            raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE)
    for key in ("run_id", "bytes_artifact_id"):
        value = record[key]
        if not isinstance(value, str) or not value.isdigit():
            raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE)
    checks = record["checks"]
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED_CHECKS):
        raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE)
    for status in checks.values():
        if not isinstance(status, str) or not status:
            raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE)
    return record


def dump_evidence_json(path: Path | str, record: Mapping[str, object]) -> Path:
    """Validate then atomically write an evidence file after the secret gate."""
    checked = _check_evidence_record(dict(record))
    target = Path(path)
    text = json.dumps(checked, sort_keys=True) + "\n"
    assert_no_secrets(text, "image-evidence")
    atomic_write_text(target, text)
    return target


def load_evidence_json(path: Path | str) -> dict[str, object]:
    """Load an evidence file: gate the whole text before parsing, strictly.

    The unchanged secret gate runs on the full file text before ``json``
    parsing; duplicate keys, malformed JSON, a missing file, or any schema
    deviation raises :class:`EvidenceError` with the fixed safe refusal. Raw
    values are never coerced into trusted evidence: every field must already
    be a correctly shaped string.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE) from None
    try:
        assert_no_secrets(text, "image-evidence")
    except SecretLeakError:
        raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE) from None

    def _reject_duplicates(pairs: list[tuple[str, object]]) -> object:
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE)
            seen.add(key)
        return dict(pairs)

    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicates)
    except (ValueError, EvidenceError) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError(REFUSAL_AMBIGUOUS_EVIDENCE) from None
    return _check_evidence_record(parsed)


__all__ = [
    "ALLOW_DETAIL",
    "CONFORMANCE_ALLOW_DETAIL",
    "CONFORMANCE_REFUSAL_INCOMPLETE",
    "CONFORMANCE_REFUSAL_NOT_PASSING",
    "REFUSAL_AMBIGUOUS_EVIDENCE",
    "REFUSAL_ARCHIVE_MISMATCH",
    "REFUSAL_CHECK_FAILED",
    "REFUSAL_ENVIRONMENT_UNPROTECTED",
    "REFUSAL_IMAGE_MISMATCH",
    "REFUSAL_MISSING_EVIDENCE",
    "REFUSAL_RUN_MISMATCH",
    "REFUSAL_SOURCE_SHA_MISMATCH",
    "REFUSAL_UNPROTECTED_REF",
    "REQUIRED_CHECKS",
    "ArchiveChecksum",
    "EvidenceError",
    "GateDecision",
    "ImageId",
    "ManifestDigest",
    "PublishGateEvidence",
    "admit_conformance_report",
    "archive_checksum_matches",
    "dump_evidence_json",
    "evaluate_publish_gate",
    "expected_conformance_ids",
    "image_id_matches",
    "load_evidence_json",
    "manifest_digest_matches",
    "write_decision_json",
]

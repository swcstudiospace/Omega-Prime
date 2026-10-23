# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Consumer-behavior regressions for the image gates.

Every test exercises the public helper surface (identity comparison, gate
evaluation, conformance admission, evidence files) through observable
allow/refusal outcomes. No workflow text, source spelling, or incidental
counts are pinned here.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

import pytest

from omega_prime.grokbot.image_gates import (
    ALLOW_DETAIL,
    CONFORMANCE_ALLOW_DETAIL,
    CONFORMANCE_REFUSAL_INCOMPLETE,
    CONFORMANCE_REFUSAL_NOT_PASSING,
    REFUSAL_AMBIGUOUS_EVIDENCE,
    REFUSAL_ARCHIVE_MISMATCH,
    REFUSAL_CHECK_FAILED,
    REFUSAL_IMAGE_MISMATCH,
    REFUSAL_MISSING_EVIDENCE,
    REFUSAL_RUN_MISMATCH,
    REFUSAL_SOURCE_SHA_MISMATCH,
    REFUSAL_UNPROTECTED_REF,
    ArchiveChecksum,
    EvidenceError,
    GateDecision,
    ImageId,
    ManifestDigest,
    PublishGateEvidence,
    admit_conformance_report,
    archive_checksum_matches,
    dump_evidence_json,
    evaluate_publish_gate,
    image_id_matches,
    load_evidence_json,
    manifest_digest_matches,
    normalize_podman_image_id,
    write_decision_json,
)
from omega_prime.grokbot.secret_guard import SecretLeakError

_SOURCE_SHA = "a1" * 20
_ARCHIVE = "b2" * 32
_IMAGE_ID = "sha256:" + "c3" * 32
_RUN_ID = "123456789"

#: Recognized-prefix token, assembled at runtime so no complete credential
#: shape is committed to source.
_SMUGGLED_TOKEN = "_".join(("omk", "smuggled" + "token9"))

#: The exact 17 check IDs a passing per-transport report carries. These are
#: real verifier protocol fields asserted as the admission contract.
_SSE_IDS = (
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
    "sse:initialize",
    "sse:list_tools",
    "sse:call_tool",
    "sse:unknown_tool",
    "sse:gated_tool_refused",
    "admin:scope_enforced",
    "admin:approval_roundtrip",
)


def _evidence(**overrides: object) -> PublishGateEvidence:
    base: dict[str, object] = {
        "ref_protected": True,
        "source_sha_expected": _SOURCE_SHA,
        "source_sha_observed": _SOURCE_SHA,
        "archive_expected": ArchiveChecksum(_ARCHIVE),
        "archive_observed": ArchiveChecksum(_ARCHIVE),
        "image_expected": ImageId(_IMAGE_ID),
        "image_observed": ImageId(_IMAGE_ID),
        "run_id_expected": _RUN_ID,
        "run_id_observed": _RUN_ID,
        "checks": {"validation": "success", "sbom": "success", "scan": "success"},
    }
    base.update(overrides)
    return PublishGateEvidence(**base)  # type: ignore[arg-type]


def _report(ids: tuple[str, ...] = _SSE_IDS) -> dict[str, object]:
    names = list(ids)
    return {
        "base_url": "http://127.0.0.1:8000",
        "server_version": "6.0.0",
        "ok": True,
        "counts": {"pass": len(names), "fail": 0, "skip": 0},
        "checks": [
            {"id": name, "title": name, "status": "pass", "detail": name}
            for name in names
        ],
    }


def _evidence_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "artifact": "validated-image-bytes",
        "archive_sha256": _ARCHIVE,
        "bytes_artifact": "validated-image-bytes",
        "bytes_artifact_id": _RUN_ID,
        "checks": {"validation": "success", "sbom": "success", "scan": "success"},
        "event": "workflow_dispatch",
        "image_id": _IMAGE_ID,
        "ref": "refs/heads/main",
        "run_id": _RUN_ID,
        "source_sha": _SOURCE_SHA,
    }
    record.update(overrides)
    return record


def test_podman_bare_hex_image_id_matches_the_prefixed_form() -> None:
    """Podman ``.Id`` is bare hex; the publish check requires ``sha256:``."""
    bare = "c3" * 32
    normalized = normalize_podman_image_id(bare)
    prefixed = "sha256:" + bare
    assert normalized == prefixed
    assert image_id_matches(ImageId(normalized), ImageId(prefixed))
    assert normalize_podman_image_id("  " + bare + "\n") == prefixed
    assert normalize_podman_image_id(prefixed) == prefixed
    # A short or already-qualified value is not rewritten into a valid id.
    assert normalize_podman_image_id(bare[:32]) == bare[:32]
    assert normalize_podman_image_id("sha256:" + bare[:32]) == "sha256:" + bare[:32]
    assert (
        image_id_matches(ImageId(normalize_podman_image_id("abc")), ImageId(prefixed))
        is False
    )


def test_allow_binds_the_exact_identities() -> None:
    decision = evaluate_publish_gate(_evidence())
    assert decision.allowed is True
    assert decision.reason == "allow"
    assert decision.detail == ALLOW_DETAIL
    assert decision.to_dict() == {
        "allowed": True,
        "reason": "allow",
        "detail": ALLOW_DETAIL,
    }


def test_wrong_source_sha_refuses() -> None:
    decision = evaluate_publish_gate(
        _evidence(source_sha_observed="d4" * 20),
    )
    assert decision.allowed is False
    assert decision.reason == "refused:source-sha-mismatch"
    assert decision.detail == REFUSAL_SOURCE_SHA_MISMATCH


def test_changed_archive_bytes_refuse() -> None:
    assert (
        archive_checksum_matches(ArchiveChecksum(_ARCHIVE), ArchiveChecksum("e5" * 32))
        is False
    )
    decision = evaluate_publish_gate(
        _evidence(archive_observed=ArchiveChecksum("e5" * 32)),
    )
    assert decision.allowed is False
    assert decision.detail == REFUSAL_ARCHIVE_MISMATCH


def test_changed_local_image_id_refuses() -> None:
    assert image_id_matches(ImageId(_IMAGE_ID), ImageId("sha256:" + "f6" * 32)) is False
    decision = evaluate_publish_gate(
        _evidence(image_observed=ImageId("sha256:" + "f6" * 32)),
    )
    assert decision.allowed is False
    assert decision.detail == REFUSAL_IMAGE_MISMATCH


def test_unprotected_ref_refuses() -> None:
    decision = evaluate_publish_gate(_evidence(ref_protected=False))
    assert decision.allowed is False
    assert decision.detail == REFUSAL_UNPROTECTED_REF


def test_unknown_ref_protection_refuses_as_missing() -> None:
    decision = evaluate_publish_gate(_evidence(ref_protected=None))
    assert decision.allowed is False
    assert decision.detail == REFUSAL_MISSING_EVIDENCE


def test_missing_evidence_refuses() -> None:
    decision = evaluate_publish_gate(_evidence(source_sha_observed=""))
    assert decision.allowed is False
    assert decision.detail == REFUSAL_MISSING_EVIDENCE


def test_truncated_sha_refuses_as_ambiguous() -> None:
    decision = evaluate_publish_gate(_evidence(source_sha_observed="a1b2c3d"))
    assert decision.allowed is False
    assert decision.detail == REFUSAL_AMBIGUOUS_EVIDENCE


def test_placeholder_evidence_refuses_as_ambiguous() -> None:
    decision = evaluate_publish_gate(
        _evidence(archive_observed=ArchiveChecksum("unknown")),
    )
    assert decision.allowed is False
    assert decision.detail == REFUSAL_AMBIGUOUS_EVIDENCE


def test_run_mismatch_refuses() -> None:
    decision = evaluate_publish_gate(_evidence(run_id_observed="987654321"))
    assert decision.allowed is False
    assert decision.detail == REFUSAL_RUN_MISMATCH


def test_binding_archive_mismatch_refuses_by_name() -> None:
    decision = evaluate_publish_gate(
        _evidence(archive_observed=ArchiveChecksum("08" * 32)),
    )
    assert decision.allowed is False
    assert decision.reason == "refused:archive-mismatch"
    assert decision.detail == REFUSAL_ARCHIVE_MISMATCH


def test_binding_image_id_mismatch_refuses_by_name() -> None:
    decision = evaluate_publish_gate(
        _evidence(image_observed=ImageId("sha256:" + "09" * 32)),
    )
    assert decision.allowed is False
    assert decision.reason == "refused:image-mismatch"
    assert decision.detail == REFUSAL_IMAGE_MISMATCH


def test_binding_run_id_mismatch_refuses_by_name() -> None:
    decision = evaluate_publish_gate(_evidence(run_id_observed="555555555"))
    assert decision.allowed is False
    assert decision.reason == "refused:run-mismatch"
    assert decision.detail == REFUSAL_RUN_MISMATCH


def test_binding_source_commit_mismatch_refuses_by_name() -> None:
    decision = evaluate_publish_gate(
        _evidence(source_sha_observed="07" * 20),
    )
    assert decision.allowed is False
    assert decision.reason == "refused:source-sha-mismatch"
    assert decision.detail == REFUSAL_SOURCE_SHA_MISMATCH


def test_binding_all_match_allows() -> None:
    decision = evaluate_publish_gate(_evidence())
    assert decision.allowed is True
    assert decision.reason == "allow"
    assert decision.detail == ALLOW_DETAIL


def test_failed_required_check_refuses_and_names_the_check() -> None:
    decision = evaluate_publish_gate(
        _evidence(
            checks={"validation": "success", "sbom": "success", "scan": "failure"}
        ),
    )
    assert decision.allowed is False
    assert decision.reason == "refused:check-failed"
    assert decision.detail == f"{REFUSAL_CHECK_FAILED}: scan"


def test_absent_required_check_refuses() -> None:
    decision = evaluate_publish_gate(
        _evidence(checks={"validation": "success", "sbom": "success"}),
    )
    assert decision.allowed is False
    assert decision.reason == "refused:check-failed"


def test_skipped_required_check_is_not_success() -> None:
    decision = evaluate_publish_gate(
        _evidence(
            checks={"validation": "success", "sbom": "skipped", "scan": "success"},
        ),
    )
    assert decision.allowed is False
    assert decision.reason == "refused:check-failed"


def test_identity_domains_are_not_interchangeable() -> None:
    same_hex = "c3" * 32
    assert ImageId(f"sha256:{same_hex}") != ArchiveChecksum(same_hex)
    assert ArchiveChecksum(same_hex) != ManifestDigest(f"sha256:{same_hex}")
    assert (
        manifest_digest_matches(
            ManifestDigest(f"sha256:{same_hex}"), ManifestDigest(f"sha256:{same_hex}")
        )
        is True
    )
    for bad_call in (
        lambda: image_id_matches(ImageId(_IMAGE_ID), ArchiveChecksum(_ARCHIVE)),  # type: ignore[arg-type]
        lambda: archive_checksum_matches(ArchiveChecksum(_ARCHIVE), ImageId(_IMAGE_ID)),  # type: ignore[arg-type]
        lambda: manifest_digest_matches(ManifestDigest(_IMAGE_ID), ImageId(_IMAGE_ID)),  # type: ignore[arg-type]
    ):
        try:
            bad_call()
        except TypeError:
            continue
        raise AssertionError("cross-domain comparison must raise TypeError")


def test_refusal_text_carries_no_observed_values() -> None:
    observed = "d4" * 20
    decision = evaluate_publish_gate(_evidence(source_sha_observed=observed))
    assert observed not in decision.detail
    assert observed not in decision.reason


def test_secret_bearing_decision_never_overwrites_the_receipt(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "decision.json"
    receipt.write_text('{"allowed": true}\n', encoding="utf-8")
    smuggled = GateDecision(
        allowed=False,
        reason="refused:check-failed",
        detail=f"publish refused: {_SMUGGLED_TOKEN}",
    )
    with pytest.raises(SecretLeakError):
        write_decision_json(receipt, smuggled)
    assert receipt.read_text(encoding="utf-8") == '{"allowed": true}\n'


def test_conformance_admits_the_complete_passing_report() -> None:
    decision = admit_conformance_report(_report(), transport="sse", verifier_exit=0)
    assert decision.allowed is True
    assert decision.reason == "allow"
    assert decision.detail == CONFORMANCE_ALLOW_DETAIL


def test_conformance_refuses_a_skipped_admin_check() -> None:
    report = _report()
    checks = report["checks"]
    assert isinstance(checks, list)
    skipped = dict(checks[-1])
    skipped["status"] = "skip"
    report["checks"] = [*checks[:-1], skipped]
    report["counts"] = {"pass": 16, "fail": 0, "skip": 1}
    report["ok"] = True
    decision = admit_conformance_report(report, transport="sse", verifier_exit=0)
    assert decision.allowed is False
    assert decision.detail == CONFORMANCE_REFUSAL_NOT_PASSING


def test_conformance_refuses_duplicate_rows() -> None:
    report = _report()
    checks = report["checks"]
    assert isinstance(checks, list)
    report["checks"] = [*checks, dict(checks[0])]
    decision = admit_conformance_report(report, transport="sse", verifier_exit=0)
    assert decision.allowed is False
    assert decision.detail == CONFORMANCE_REFUSAL_INCOMPLETE


def test_conformance_refuses_a_partial_row_set() -> None:
    report = _report(ids=_SSE_IDS[:-1])
    decision = admit_conformance_report(report, transport="sse", verifier_exit=0)
    assert decision.allowed is False
    assert decision.detail == CONFORMANCE_REFUSAL_INCOMPLETE


def test_conformance_refuses_single_transport_evidence() -> None:
    swapped = tuple(
        name.replace("sse:", "http:") if ":" in name else name for name in _SSE_IDS
    )
    decision = admit_conformance_report(
        _report(ids=swapped), transport="sse", verifier_exit=0
    )
    assert decision.allowed is False


def test_conformance_refuses_incoherent_counts() -> None:
    report = _report()
    report["counts"] = {"pass": 17, "fail": 0, "skip": 1}
    decision = admit_conformance_report(report, transport="sse", verifier_exit=0)
    assert decision.allowed is False
    assert decision.detail == CONFORMANCE_REFUSAL_NOT_PASSING


def test_conformance_refuses_a_nonzero_verifier_exit() -> None:
    decision = admit_conformance_report(_report(), transport="sse", verifier_exit=1)
    assert decision.allowed is False


def test_evidence_file_with_duplicate_keys_refuses(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    valid = json.dumps(_evidence_record())
    duplicated = '{"artifact": "other-artifact", ' + valid[1:]
    path.write_text(duplicated, encoding="utf-8")
    with pytest.raises(EvidenceError):
        load_evidence_json(path)


def test_evidence_file_with_wrong_schema_refuses(tmp_path: Path) -> None:
    record = _evidence_record()
    del record["source_sha"]
    record["extra"] = "nope"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(EvidenceError):
        load_evidence_json(path)


def test_push_ref_evidence_passes_and_a_token_branch_still_refuses(
    tmp_path: Path,
) -> None:
    """A long push ref is provenance. A token-shaped branch segment is not.

    ``github.ref`` on ``push`` is ``refs/heads/<branch>``. ``/`` is in the
    token alphabet, so a branch at least as long as
    ``cursor/grokbot-publish-gaps-0615`` is one 32+ candidate and used to
    trip ``dump_evidence_json``. Pull-request refs (``refs/pull/N/merge``)
    and ``refs/heads/main`` stay under that length, which is why only the
    push-event image job failed.
    """
    for ref in (
        "refs/heads/cursor/grokbot-publish-gaps-0615",
        "refs/heads/cursor/publish-template-gaps-5913",
    ):
        path = tmp_path / "evidence.json"
        written = dump_evidence_json(path, _evidence_record(event="push", ref=ref))
        loaded = load_evidence_json(written)
        assert loaded["ref"] == ref
        assert loaded["event"] == "push"

    token_branch = string.ascii_lowercase + string.digits
    with pytest.raises(SecretLeakError) as caught:
        dump_evidence_json(
            tmp_path / "secret.json",
            _evidence_record(ref="refs/heads/" + token_branch),
        )
    assert "token-pattern" in str(caught.value)
    assert token_branch not in str(caught.value)

    # A slash in the middle makes each piece shorter than 32. The segments
    # joined back together are still the token, so the split must not pass.
    split = token_branch[:18] + "/" + token_branch[18:]
    with pytest.raises(SecretLeakError) as split_caught:
        dump_evidence_json(
            tmp_path / "split.json",
            _evidence_record(ref="refs/heads/" + split),
        )
    assert "token-pattern" in str(split_caught.value)
    assert split not in str(split_caught.value)

    # Repeated padding drops the joined suffix under the entropy cutoff
    # while leaving an intact token segment. Both have to trip.
    padded = "a" * 21 + "/" + token_branch
    with pytest.raises(SecretLeakError) as padded_caught:
        dump_evidence_json(
            tmp_path / "padded.json",
            _evidence_record(ref="refs/heads/" + padded),
        )
    assert "token-pattern" in str(padded_caught.value)
    assert token_branch not in str(padded_caught.value)
    heavier = "a" * 40 + "/" + token_branch
    with pytest.raises(SecretLeakError) as heavier_caught:
        dump_evidence_json(
            tmp_path / "heavier.json",
            _evidence_record(ref="refs/heads/" + heavier),
        )
    assert "token-pattern" in str(heavier_caught.value)
    assert token_branch not in str(heavier_caught.value)


def test_evidence_file_with_a_secret_refuses(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    record = _evidence_record(artifact=_SMUGGLED_TOKEN)
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(EvidenceError):
        load_evidence_json(path)

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for the seat-to-sandbox policy generator (Phase 83, SAND-01).

Hermetic: stdlib + fixture seat JSONs only. No openshell binary, no network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omega_prime.sandbox import policy_gen
from omega_prime.sandbox.policy_gen import (
    CANONICAL_OUT,
    CANONICAL_SEAT,
    CREATE_COMMAND,
    GENERATOR,
    PORTABLE_HOME_SCRATCH,
    PORTABLE_STATE_DIR,
    REGENERATE_COMMAND,
    SYSTEM_READ_ONLY,
    build_policy_doc,
    digest_bytes,
    digest_file,
    load_seat_document,
    render_policy,
)

REPO = Path(__file__).resolve().parents[2]
CANONICAL_SEAT_PATH = REPO / CANONICAL_SEAT
COMMITTED_POLICY_PATH = REPO / CANONICAL_OUT
GOLDEN_PATH = Path(__file__).resolve().parent / "data" / "sandbox-policy-golden.yaml"


def _seat_doc(**overrides) -> dict:
    doc: dict = {
        "version": 1,
        "seat": "bot-test",
        "tools": {"allow": ["read_file"]},
        "paths": {"read_only": []},
        "network": {"hosts": []},
    }
    doc.update(overrides)
    return doc


def _write_seat(tmp_path: Path, doc: dict) -> Path:
    seat = tmp_path / "seat.json"
    seat.write_text(json.dumps(doc), encoding="utf-8")
    return seat


def _body_lines(rendered: bytes) -> list[str]:
    """Non-comment, non-blank lines (the generated policy body)."""
    return [
        line
        for line in rendered.decode("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_read_only_globs_mapped_alongside_system_set(tmp_path):
    seat = _write_seat(
        tmp_path,
        _seat_doc(paths={"read_only": ["prompts/**", "contracts/**"]}),
    )
    policy, document = load_seat_document(seat)
    doc = build_policy_doc(policy, document, seat)
    read_only = doc["filesystem_policy"]["read_only"]
    for entry in SYSTEM_READ_ONLY:
        assert entry in read_only
    assert "prompts/**" in read_only
    assert "contracts/**" in read_only
    # System set stays first; seat globs follow in seat order.
    assert read_only[: len(SYSTEM_READ_ONLY)] == list(SYSTEM_READ_ONLY)
    assert read_only[-2:] == ["prompts/**", "contracts/**"]


def test_empty_hosts_default_deny(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc())
    rendered = render_policy(seat)
    assert b"network_policies: {}" in rendered


def test_nonempty_hosts_allowlisted_exactly(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc(network={"hosts": ["api.x.ai"]}))
    rendered = render_policy(seat).decode("utf-8")
    assert "api.x.ai" in rendered
    assert "network_policies: {}" not in rendered
    assert "evil.example" not in rendered
    policy, document = load_seat_document(seat)
    doc = build_policy_doc(policy, document, seat)
    entries = doc["network_policies"]
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry["endpoints"][0]["host"] == "api.x.ai"
    assert entry["endpoints"][0]["enforcement"] == "enforce"


def test_read_write_covers_omega_defaults(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc())
    policy, document = load_seat_document(seat)
    doc = build_policy_doc(policy, document, seat)
    read_write = doc["filesystem_policy"]["read_write"]
    assert PORTABLE_HOME_SCRATCH in read_write
    assert PORTABLE_STATE_DIR in read_write
    for fixed in ("/sandbox", "/tmp", "/dev/null"):
        assert fixed in read_write


def test_read_write_defaults_are_machine_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/fake-home")
    seat = _write_seat(tmp_path, _seat_doc())
    rendered = render_policy(seat).decode("utf-8")
    assert PORTABLE_HOME_SCRATCH in rendered
    assert PORTABLE_STATE_DIR in rendered
    assert "/root/" not in rendered
    assert str(Path.cwd()) not in rendered


def test_read_write_explicit_overrides_render_concrete_paths(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc())
    policy, document = load_seat_document(seat)
    doc = build_policy_doc(
        policy, document, seat, home="/tmp/fake-home", work_root="/tmp/fake-work"
    )
    read_write = doc["filesystem_policy"]["read_write"]
    assert "/tmp/fake-home/.omega-prime" in read_write
    assert "/tmp/fake-work/omega_prime/state" in read_write
    rendered = render_policy(
        seat, home="/tmp/fake-home", work_root="/tmp/fake-work"
    ).decode("utf-8")
    assert "/tmp/fake-home/.omega-prime" in rendered
    assert "/tmp/fake-work/omega_prime/state" in rendered


def test_read_write_derived_not_hardcoded():
    source = Path(policy_gen.__file__).read_text(encoding="utf-8")
    assert "/root/" not in source
    assert "bot-00-omega-prime" not in source


def test_determinism_two_renders_byte_identical(tmp_path):
    seat = _write_seat(
        tmp_path,
        _seat_doc(
            paths={"read_only": ["prompts/**"]},
            network={"hosts": ["api.x.ai"]},
        ),
    )
    assert render_policy(seat) == render_policy(seat)


def test_unknown_top_level_key_rejected(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc(sandbox_extra=True))
    with pytest.raises(ValueError, match="unknown top-level keys"):
        render_policy(seat)


def test_version_mismatch_rejected(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc(version=2))
    with pytest.raises(ValueError, match="version"):
        render_policy(seat)


def test_missing_seat_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        render_policy(tmp_path / "nope.json")


def test_digest_helper_matches_sha256(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc())
    rendered = render_policy(seat)
    assert digest_bytes(rendered) == hashlib.sha256(rendered).hexdigest()
    out = tmp_path / "policy.yaml"
    out.write_bytes(rendered)
    assert digest_file(out) == digest_bytes(rendered)


def test_header_names_generator_and_commands(tmp_path):
    seat = _write_seat(tmp_path, _seat_doc())
    head = render_policy(seat).decode("utf-8").splitlines()
    assert head[0] == f"# Generated by {GENERATOR} — DO NOT EDIT by hand."
    assert head[1] == f"# Regenerate: {REGENERATE_COMMAND}"
    assert any("bot-test" in line for line in head[:5])
    assert any(CREATE_COMMAND in line for line in head)


def test_snapshot_golden_pins_canonical_output():
    assert GOLDEN_PATH.is_file(), (
        "golden file missing; regenerate per REGENERATE_COMMAND"
    )
    assert render_policy(CANONICAL_SEAT_PATH) == GOLDEN_PATH.read_bytes()


def test_cutover_committed_policy_is_generated():
    assert COMMITTED_POLICY_PATH.is_file()
    committed = COMMITTED_POLICY_PATH.read_bytes()
    fresh = render_policy(CANONICAL_SEAT_PATH)
    assert committed == fresh


def test_cutover_body_below_header_matches_generator():
    committed = COMMITTED_POLICY_PATH.read_bytes()
    fresh = render_policy(CANONICAL_SEAT_PATH)
    assert _body_lines(committed) == _body_lines(fresh)


def test_canonical_render_preserves_shipped_semantics():
    rendered = render_policy(CANONICAL_SEAT_PATH).decode("utf-8")
    assert "include_workdir: true" in rendered
    assert "best_effort" in rendered
    assert "network_policies: {}" in rendered

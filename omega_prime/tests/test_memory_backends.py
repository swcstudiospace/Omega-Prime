"""Behavioral contracts for shared memory backend configuration and records."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict

import pytest

from omega_prime.memory.backends import (
    LegacyRecord,
    LocalRecord,
    content_hash,
    decode_local_record,
    encode_local_record,
    normalize_content,
    resolve_memory_backends,
    validate_scope_repo,
)
from omega_prime.memory.store import ENTRY_DELIMITER, MEMORY_CHAR_LIMIT, MemoryStore


@pytest.mark.parametrize("selector", ["local", "substrate", "hindsight"])
def test_valid_backend_selector_is_preserved(selector: str, tmp_path) -> None:
    resolved = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        environ={"OMEGA_PRIME_MEMORY_BACKEND": selector},
    )
    assert resolved.selected_backend == selector
    assert resolved.local_store is not None
    assert resolved.states["local"] == "empty"


def test_invalid_selector_fails_without_fallback(tmp_path) -> None:
    with pytest.raises(ValueError, match="selector"):
        resolve_memory_backends(
            local_store=MemoryStore(tmp_path / "memory"),
            environ={"OMEGA_PRIME_MEMORY_BACKEND": "remote"},
        )


def test_default_selects_substrate_only_for_explicit_non_loopback_url(tmp_path) -> None:
    client = object()
    resolved = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate_url="https://memory.example/api",
        substrate_factory=lambda url: (
            client if url == "https://memory.example/api" else None
        ),
        environ={},
    )
    assert resolved.selected_backend == "substrate"
    assert resolved.substrate is client
    assert resolved.states["substrate"] == "empty"


@pytest.mark.parametrize(
    "url", [None, "http://127.0.0.1:7410", "http://localhost:7410"]
)
def test_default_is_local_without_explicit_remote_configuration(url, tmp_path) -> None:
    resolved = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"), substrate_url=url, environ={}
    )
    assert resolved.selected_backend == "local"
    assert resolved.substrate is None
    assert resolved.states["substrate"] == "not_configured"


@pytest.mark.parametrize(
    "selector,backend", [("substrate", "substrate"), ("hindsight", "hindsight")]
)
def test_explicit_missing_remote_remains_selected_and_not_configured(
    selector, backend, tmp_path
) -> None:
    resolved = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        environ={"OMEGA_PRIME_MEMORY_BACKEND": selector},
    )
    assert resolved.selected_backend == selector
    assert resolved.states[backend] == "not_configured"
    assert resolved.local_store is not None


def test_local_write_preference_keeps_configured_remote_available(tmp_path) -> None:
    remote = object()
    resolved = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate=remote,
        environ={"OMEGA_PRIME_MEMORY_BACKEND": "local"},
    )
    assert resolved.selected_backend == "local"
    assert resolved.substrate is remote
    assert resolved.states["substrate"] == "empty"


@pytest.mark.parametrize(
    "scope,repo",
    [
        ("", None),
        (" scope", None),
        ("scope\n", None),
        ("scope", ""),
        ("scope", " repo"),
        ("scope", "repo\x00"),
    ],
)
def test_scope_and_repo_reject_invalid_identifiers(scope, repo) -> None:
    with pytest.raises(ValueError):
        validate_scope_repo(scope, repo)


def test_scope_and_repo_allow_scoped_identifier_and_optional_repo() -> None:
    assert validate_scope_repo("team-alpha", "owner/repository") == (
        "team-alpha",
        "owner/repository",
    )
    assert validate_scope_repo("team-alpha", None) == ("team-alpha", None)


@pytest.mark.parametrize(
    "raw,canonical",
    [
        ("  hello\r\nworld  ", "hello\nworld"),
        ("hello\rworld", "hello\nworld"),
        ("[user:note] Keep This", "[user:note] Keep This"),
    ],
)
def test_content_normalization_and_hash_vectors(raw, canonical) -> None:
    assert normalize_content(raw) == canonical
    assert content_hash(raw) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_content_hash_preserves_case_and_bracketed_user_text() -> None:
    assert content_hash("Case") != content_hash("case")
    assert content_hash("[user:note] text") == content_hash("  [user:note] text  ")


def test_record_encoding_is_exact_compact_complete_local_envelope() -> None:
    record = LocalRecord(
        bank="team",
        scope="org",
        repo="owner/repo",
        kind="fact",
        content="a\nb",
        source="lead",
        receipt_path="receipts/1.json",
        tags=("one", "two"),
        graph_id="graph-1",
        task_id="task-1",
    )
    payload = {
        "bank": "team",
        "content": "a\nb",
        "graph_id": "graph-1",
        "kind": "fact",
        "receipt_path": "receipts/1.json",
        "repo": "owner/repo",
        "scope": "org",
        "source": "lead",
        "tags": ["one", "two"],
        "task_id": "task-1",
        "v": 1,
    }
    expected = "[hindsight:team] [omega-memory:v1]" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    encoded = encode_local_record(record)
    assert encoded == expected
    assert encoded.count("[hindsight:") == 1


def test_local_record_round_trip_includes_all_evidence_fields() -> None:
    record = LocalRecord(
        bank="team",
        scope="org",
        repo=None,
        kind="decision",
        content="use stable IDs",
        source="lead",
        receipt_path="r.json",
        tags=("design",),
        graph_id="g",
        task_id="t",
    )
    assert decode_local_record(encode_local_record(record), "team") == record


def test_decoded_content_is_written_directly_to_store_without_second_envelope(
    tmp_path,
) -> None:
    store = MemoryStore(tmp_path)
    encoded = encode_local_record(
        {
            "bank": "team",
            "scope": "org",
            "repo": None,
            "kind": "fact",
            "content": "known",
        }
    )
    assert store.add("memory", encoded)["success"] is True
    assert store.memory_entries == [encoded]
    decoded = decode_local_record(store.memory_entries[0], "team")
    assert decoded is not None and decoded.content == "known"


def test_other_bank_is_ineligible_not_malformed() -> None:
    assert decode_local_record("[hindsight:other] plain legacy text", "team") is None


def test_expected_bank_plain_record_decodes_as_unscoped_legacy() -> None:
    decoded = decode_local_record("[hindsight:team] old plain content", "team")
    assert decoded == LegacyRecord(
        bank="team", scope=None, repo=None, kind="memory", content="old plain content"
    )


@pytest.mark.parametrize(
    "stored",
    [
        "[hindsight:team] [omega-memory:v1]{bad json}",
        "[hindsight:team] [omega-memory:v2]{}",
        '[hindsight:team] [omega-memory:v1]{"v":2}',
        "[hindsight:team] [omega-memory:v1]{} trailing",
    ],
)
def test_recognized_malformed_or_unknown_record_fails_closed(stored) -> None:
    with pytest.raises(ValueError):
        decode_local_record(stored, "team")


def test_record_full_wrapper_accepts_exact_memory_budget() -> None:
    values = {
        "bank": "team",
        "scope": "org",
        "repo": None,
        "kind": "fact",
        "content": "x",
    }
    encoded = encode_local_record(values)
    exact_size_content = "x" * (MEMORY_CHAR_LIMIT - len(encoded) + 1)
    values["content"] = exact_size_content
    assert len(encode_local_record(values)) == MEMORY_CHAR_LIMIT


def test_record_decoder_checks_inner_bank_and_exact_schema() -> None:
    raw = json.dumps(
        {
            "v": 1,
            "bank": "elsewhere",
            "scope": "org",
            "repo": None,
            "kind": "fact",
            "content": "text",
            "source": None,
            "receipt_path": None,
            "tags": [],
            "graph_id": None,
            "task_id": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    with pytest.raises(ValueError, match="bank mismatch"):
        decode_local_record(f"[hindsight:team] [omega-memory:v1]{raw}", "team")


def test_record_rejects_store_delimiter_and_counts_full_wrapper_budget() -> None:
    values = {
        "bank": "team",
        "scope": "org",
        "repo": None,
        "kind": "fact",
        "content": "bad" + ENTRY_DELIMITER + "entry",
    }
    with pytest.raises(ValueError, match="delimiter"):
        encode_local_record(values)
    # JSON escaping expands this character; the wrapper and metadata count too.
    values["content"] = "x" * MEMORY_CHAR_LIMIT
    with pytest.raises(ValueError, match="limit"):
        encode_local_record(values)


def test_memory_backend_ledger_containers_are_shared(tmp_path) -> None:
    backends = resolve_memory_backends(local_store=MemoryStore(tmp_path), environ={})
    backends.operation_bindings["operation"] = "digest"
    backends.operation_results["first"] = {"ok": True}
    backends.operation_results["second"] = {"ok": True}
    assert backends.operation_bindings["operation"] == "digest"
    assert isinstance(backends.operation_results, OrderedDict)

    # A successful access moves an entry to the MRU end; eviction removes the LRU.
    backends.operation_results.move_to_end("first")
    assert list(backends.operation_results) == ["second", "first"]
    assert backends.operation_results.popitem(last=False) == ("second", {"ok": True})
    assert list(backends.operation_results) == ["first"]
    assert backends.operation_lock is not None

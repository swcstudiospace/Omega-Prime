"""Phase 11: Hermes and Omp memory calls share one store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.memory.hindsight import Hindsight
from omega_prime.memory.mnemopi import Mnemopi
from omega_prime.memory.omp import OmpMemory
from omega_prime.memory.provider import BuiltinMemoryProvider
from omega_prime.memory.store import MemoryStore


def _hermes_add(
    provider: BuiltinMemoryProvider, content: str, target: str = "memory"
) -> dict:
    return json.loads(
        provider.handle_tool_call(
            "memory", {"action": "add", "target": target, "content": content}
        )
    )


def test_hermes_write_reads_back_through_the_omp_shape(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem")
    provider = BuiltinMemoryProvider(store)

    written = _hermes_add(provider, "The hatch code is 4179.")
    assert written["success"] is True

    omp = OmpMemory(store)
    assert omp.recall("hatch") == ["The hatch code is 4179."]

    edited = omp.edit("hatch code is 4179", "The hatch code is 4180.")
    assert edited == {"ok": True}
    assert "4180" in store.render()
    assert "4179" not in store.render()


def test_legacy_bank_notes_can_be_replaced_and_removed(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem")
    store.add("memory", "[hindsight:team] legacy first note")
    store.add("memory", "[hindsight:team] legacy second note")
    provider = BuiltinMemoryProvider(store)

    replaced = json.loads(
        provider.handle_tool_call(
            "memory",
            {
                "action": "replace",
                "old_text": "legacy first note",
                "content": "replacement note",
            },
        )
    )
    assert replaced["success"] is True
    assert store.memory_entries == [
        "[hindsight:team] replacement note",
        "[hindsight:team] legacy second note",
    ]

    removed = json.loads(
        provider.handle_tool_call(
            "memory", {"action": "remove", "old_text": "legacy second note"}
        )
    )
    assert removed["success"] is True
    assert store.memory_entries == ["[hindsight:team] replacement note"]


def test_memory_tool_reloads_stale_store_before_replacing_legacy_note(
    tmp_path: Path,
):
    memory_dir = tmp_path / "mem"
    stale_store = MemoryStore(memory_dir)
    writer = MemoryStore(memory_dir)
    writer.add("memory", "[hindsight:team] legacy note from another store")
    writer.add("memory", "concurrent plain note")
    provider = BuiltinMemoryProvider(stale_store)

    replaced = json.loads(
        provider.handle_tool_call(
            "memory",
            {
                "action": "replace",
                "old_text": "legacy note from another store",
                "content": "updated legacy note",
            },
        )
    )

    assert replaced["success"] is True
    assert MemoryStore(memory_dir).memory_entries == [
        "[hindsight:team] updated legacy note",
        "concurrent plain note",
    ]


def test_memory_tool_does_not_match_inside_malformed_versioned_bank_note(
    tmp_path: Path,
):
    store = MemoryStore(tmp_path / "mem")
    malformed = '[hindsight:team] [omega-memory:v1]{"content":"raw-only secret"}'
    store.add("memory", malformed)
    provider = BuiltinMemoryProvider(store)

    replaced = json.loads(
        provider.handle_tool_call(
            "memory",
            {
                "action": "replace",
                "old_text": "raw-only secret",
                "content": "must not replace malformed record",
            },
        )
    )

    assert replaced["success"] is False
    assert store.memory_entries == [malformed]


def test_omp_recall_covers_both_targets_and_honors_limits(tmp_path: Path):

    store = MemoryStore(tmp_path / "mem")
    provider = BuiltinMemoryProvider(store)
    _hermes_add(provider, "memory alpha one")
    _hermes_add(provider, "memory alpha two")
    _hermes_add(provider, "user alpha three", target="user")

    omp = OmpMemory(store)
    assert omp.recall("alpha") == [
        "memory alpha one",
        "memory alpha two",
        "user alpha three",
    ]
    assert omp.recall("alpha", limit=2) == ["memory alpha one", "memory alpha two"]
    assert omp.recall("ALPHA", limit=1) == ["memory alpha one"]
    assert omp.recall("") == []
    assert omp.retain("   ") == {
        "ok": False,
        "reason": "content must be a non-empty string",
    }
    with pytest.raises(ValueError):
        omp.recall("alpha", limit=0)


def test_hindsight_banks_share_the_file_without_leaking(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem")
    first = Hindsight(store, "alpha")
    second = Hindsight(store, "beta")

    assert first.retain("alpha lesson one") == {"ok": True}
    assert first.retain_batch(["alpha lesson two", "   ", "alpha lesson three"]) == {
        "ok": True,
        "count": 2,
    }
    assert second.retain("beta lesson one") == {"ok": True}

    assert first.recall("lesson") == [
        "alpha lesson one",
        "alpha lesson two",
        "alpha lesson three",
    ]
    assert second.recall("lesson") == ["beta lesson one"]
    assert first.recall("beta") == []
    assert all("[" not in hit for hit in first.recall("lesson"))


def test_mnemopi_remembers_recalls_gets_and_forgets(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem")
    client = Mnemopi(store, "main")

    first = client.remember("mnemopi first fact")
    second = client.remember("mnemopi second fact")
    assert first == "mem-1"
    assert second == "mem-2"

    assert client.recall("mnemopi") == [
        {"id": "mem-1", "content": "mnemopi first fact"},
        {"id": "mem-2", "content": "mnemopi second fact"},
    ]
    assert client.recall("second", top_k=1) == [
        {"id": "mem-2", "content": "mnemopi second fact"}
    ]
    assert client.get("mem-1") == {"id": "mem-1", "content": "mnemopi first fact"}
    assert client.get("mem-999") is None

    assert client.forget("mem-1") is True
    assert client.get("mem-1") is None
    assert client.forget("mem-1") is False

    with pytest.raises(ValueError):
        client.remember("   ")


def test_three_clients_coexist_in_one_directory(tmp_path: Path):
    memory_dir = tmp_path / "mem"
    store = MemoryStore(memory_dir)
    OmpMemory(store).retain("omp shared value")
    Hindsight(store, "bank").retain("hindsight shared value")
    Mnemopi(store, "bank").remember("mnemopi shared value")

    text = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "omp shared value" in text
    assert "hindsight shared value" in text
    assert "mnemopi shared value" in text

    fresh = MemoryStore(memory_dir)
    assert OmpMemory(fresh).recall("omp shared") == ["omp shared value"]
    assert Hindsight(fresh, "bank").recall("hindsight shared") == [
        "hindsight shared value"
    ]
    assert Mnemopi(fresh, "bank").recall("mnemopi shared") == [
        {"id": "mem-1", "content": "mnemopi shared value"}
    ]
    # The next id survives the reload: no id is reused.
    assert Mnemopi(fresh, "bank").remember("another") == "mem-2"

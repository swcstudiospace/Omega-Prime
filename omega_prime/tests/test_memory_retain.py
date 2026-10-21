"""Behavioral contracts for local-first shared-memory retention."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from omega_prime.memory.backends import (
    MemoryBackends,
    MemoryStore,
    resolve_memory_backends,
)
from omega_prime.memory.recall import recall_memory
from omega_prime.memory.retain import retain_memory


class Local:
    def __init__(self, events: list[str], result: dict[str, Any] | None = None) -> None:
        self.events = events
        self.result = result or {"success": True}
        self.records: list[str] = []

    def add(self, target: str, value: str) -> dict[str, Any]:
        self.events.append("local")
        self.records.append(value)
        return self.result

    @property
    def memory_entries(self) -> list[str]:
        return list(self.records)

    def load_from_disk(self) -> None:
        return None


class Substrate:
    base_url = "https://substrate.example/mcp"

    def __init__(self, events: list[str], response: Any = None) -> None:
        self.events = events
        self.response = response if response is not None else {"status": "accepted"}
        self.calls: list[tuple[Any, ...]] = []

    def memory_write(self, scope: str, kind: str, text: str, **kwargs: Any) -> Any:
        self.events.append("substrate")
        self.calls.append((scope, kind, text, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class Hindsight:
    base_url = "https://hindsight.example"
    bank = "team"

    def __init__(self, events: list[str], response: Any = None) -> None:
        self.events = events
        self.response = response if response is not None else {"ok": True, "count": 1}
        self.calls: list[tuple[Any, ...]] = []

    def retain(self, items: list[str], **kwargs: Any) -> Any:
        self.events.append("hindsight")
        self.calls.append((items, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _backends(
    tmp_path: Path,
    events: list[str],
    *,
    selected: str = "local",
    local: Any = None,
    substrate: Any = None,
    hindsight: Any = None,
) -> MemoryBackends:
    return resolve_memory_backends(
        local_store=cast(MemoryStore, local or Local(events)),
        substrate=substrate,
        hindsight=hindsight,
        scope="tenant-a",
        repo="repo-a",
        environ={"OMEGA_PRIME_MEMORY_BACKEND": selected},
    )


def test_retain_is_local_first_and_forwards_expected_version_and_identity(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    substrate = Substrate(events)
    hindsight = Hindsight(events)
    local = Local(events)
    backends = _backends(
        tmp_path, events, local=local, substrate=substrate, hindsight=hindsight
    )

    result = retain_memory(
        backends,
        "  remember\r\nthis  ",
        scope="tenant-a",
        repo="repo-a",
        kind="fact",
        expected_version="v7",
        source="conversation",
        tags=["stable"],
    )

    assert events == ["local", "substrate"]
    assert result["ok"] is True and result["local_saved"] is True
    key = result["idempotency_key"]
    assert substrate.calls[0][3] == {
        "idempotency_key": key,
        "expected_version": "v7",
        "source_event": (
            'omega-memory:v1:{"repo":"repo-a","scope":"tenant-a",'
            '"source":"conversation"}'
        ),
    }
    assert "[omega-memory:v1]" in local.records[0]
    assert "remember\\nthis" in local.records[0]
    assert hindsight.calls == []
    assert result["backends"]["hindsight"]["attempted"] is False


def test_local_records_use_the_ownership_bank_independent_of_hindsight(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    local = Local(events)
    backends = _backends(tmp_path, events, local=local, hindsight=Hindsight(events))

    result = retain_memory(
        backends, "own-bank fact", scope="tenant-a", repo="repo-a", kind="fact"
    )

    assert result["ok"] is True
    assert local.records[0].startswith("[hindsight:omega-prime-lead] ")
    assert '"bank":"omega-prime-lead"' in local.records[0]


def test_owned_bank_is_recallable_when_shared_banks_are_excluded(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    local = MemoryStore(tmp_path / "memory")
    backends = _backends(tmp_path, events, local=local, hindsight=Hindsight(events))
    retain_memory(
        backends,
        "owned searchable fact",
        scope="tenant-a",
        repo="repo-a",
        kind="fact",
    )

    result = recall_memory(
        backends,
        "searchable",
        scope="tenant-a",
        repo="repo-a",
        limit=5,
        banks=["omega-prime-lead"],
        include_shared=False,
    )

    assert [row["content"] for row in result["results"]] == ["owned searchable fact"]


def test_secret_gate_refuses_content_and_all_evidence_before_any_write(
    tmp_path: Path,
) -> None:
    for field in (
        "content",
        "source",
        "receipt_path",
        "tags",
        "graph_id",
        "task_id",
        "expected_version",
        "scope",
        "repo",
    ):
        events: list[str] = []
        substrate, hindsight = Substrate(events), Hindsight(events)
        backends = _backends(
            tmp_path,
            events,
            selected="hindsight",
            substrate=substrate,
            hindsight=hindsight,
        )
        values: dict[str, Any] = {
            "content": "safe fact",
            "source": None,
            "receipt_path": None,
            "tags": None,
            "graph_id": None,
            "task_id": None,
            "expected_version": None,
            "scope": "tenant-a",
            "repo": "repo-a",
        }
        marker = "api_" + "key=" + "test-value"
        values[field] = [marker] if field == "tags" else marker

        result = retain_memory(
            backends,
            values.pop("content"),
            scope=values.pop("scope"),
            repo=values.pop("repo"),
            kind="fact",
            **values,
        )

        assert result["ok"] is False and result["local_saved"] is False
        assert result["error"] == "secret content or metadata refused"
        assert events == []
        assert "test-value" not in str(result)


def test_remote_writes_keep_scope_and_repo_in_reversible_metadata(
    tmp_path: Path,
) -> None:
    import json

    events: list[str] = []
    substrate, hindsight = Substrate(events), Hindsight(events)
    backends = _backends(
        tmp_path,
        events,
        selected="hindsight",
        substrate=substrate,
        hindsight=hindsight,
    )

    result = retain_memory(
        backends,
        "scoped fact",
        scope="tenant-a",
        repo="repo-a",
        kind="fact",
        source="lead",
    )

    assert result["ok"] is True
    substrate_metadata = substrate.calls[0][3]["source_event"]
    assert substrate_metadata.startswith("omega-memory:v1:")
    assert json.loads(substrate_metadata.removeprefix("omega-memory:v1:")) == {
        "repo": "repo-a",
        "scope": "tenant-a",
        "source": "lead",
    }
    hindsight_metadata = hindsight.calls[0][1]["context"]
    assert hindsight_metadata.startswith("omega-memory:v1:")
    assert json.loads(hindsight_metadata.removeprefix("omega-memory:v1:")) == {
        "repo": "repo-a",
        "scope": "tenant-a",
        "source": "lead",
    }

    assert hindsight.calls[0][1]["tags"] == [hindsight_metadata]


def test_destination_participation_follows_selector_but_substrate_always_writes(
    tmp_path: Path,
) -> None:
    for selector in ("local", "substrate", "hindsight"):
        events: list[str] = []
        substrate, hindsight = Substrate(events), Hindsight(events)
        backends = _backends(
            tmp_path,
            events,
            selected=selector,
            substrate=substrate,
            hindsight=hindsight,
        )
        result = retain_memory(
            backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
        )
        expected = ["local", "substrate"] + (
            ["hindsight"] if selector == "hindsight" else []
        )
        assert events == expected
        assert result["ok"] is True
        assert result["backends"]["substrate"]["attempted"] is True
        assert result["backends"]["hindsight"]["attempted"] is (selector == "hindsight")


def test_local_refusal_short_circuits_all_remote_writes(tmp_path: Path) -> None:
    events: list[str] = []
    substrate, hindsight = Substrate(events), Hindsight(events)
    backends = _backends(
        tmp_path,
        events,
        selected="hindsight",
        local=Local(events, {"success": False}),
        substrate=substrate,
        hindsight=hindsight,
    )
    result = retain_memory(
        backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert events == ["local"]
    assert result["ok"] is False and result["local_saved"] is False
    assert substrate.calls == [] and hindsight.calls == []


def test_remote_refusal_keeps_local_save_and_is_not_success(tmp_path: Path) -> None:
    events: list[str] = []
    substrate = Substrate(events, {"status": "conflict", "reason": "secret detail"})
    backends = _backends(tmp_path, events, substrate=substrate)
    result = retain_memory(
        backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert events == ["local", "substrate"]
    assert result["local_saved"] is True and result["ok"] is False
    assert result["backends"]["substrate"]["state"] == "unreachable"
    assert "secret detail" not in str(result)


def test_bound_operation_key_reuse_is_stable_and_mismatch_or_unknown_refused(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    local, substrate = Local(events), Substrate(events)
    backends = _backends(tmp_path, events, local=local, substrate=substrate)
    first = retain_memory(
        backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    events.clear()
    repeated = retain_memory(
        backends,
        "fact",
        scope="tenant-a",
        repo="repo-a",
        kind="fact",
        operation_id=first["idempotency_key"],
    )
    assert repeated == first
    assert events == []
    mismatch = retain_memory(
        backends,
        "other",
        scope="tenant-a",
        repo="repo-a",
        kind="fact",
        operation_id=first["idempotency_key"],
    )
    unknown = retain_memory(
        backends,
        "fact",
        scope="tenant-a",
        repo="repo-a",
        kind="fact",
        operation_id="unknown",
    )
    assert mismatch["ok"] is False and unknown["ok"] is False
    assert events == []


def test_validation_precedes_io_and_remote_exception_is_sanitized(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    substrate = Substrate(events, RuntimeError("raw transport failure"))
    backends = _backends(tmp_path, events, substrate=substrate)
    try:
        retain_memory(backends, "fact", scope=" tenant-a", repo="repo-a", kind="fact")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid scope accepted")
    assert events == []
    result = retain_memory(
        backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert result["local_saved"] is True and result["ok"] is False
    assert "raw transport failure" not in str(result)


def test_substrate_unconfigured_selection_reports_configuration_failure(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    backends = _backends(tmp_path, events, selected="substrate")
    result = retain_memory(
        backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert result["local_saved"] is True and result["ok"] is False
    assert result["backends"]["substrate"]["state"] == "not_configured"


def test_hindsight_refusal_is_reported_after_local_and_substrate(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    substrate = Substrate(events, {"status": "accepted"})
    hindsight = Hindsight(events, {"ok": False})
    backends = _backends(
        tmp_path,
        events,
        selected="hindsight",
        substrate=substrate,
        hindsight=hindsight,
    )
    result = retain_memory(
        backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert events == ["local", "substrate", "hindsight"]
    assert result["local_saved"] is True and result["ok"] is False
    assert result["backends"]["substrate"]["state"] == "answered"
    assert result["backends"]["hindsight"]["state"] == "unreachable"


def test_configured_backend_construction_error_keeps_truthful_state(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    substrate = _backends(tmp_path, events, selected="substrate")
    substrate.errors["substrate"] = "invalid configuration"
    substrate.states["substrate"] = "unreachable"
    substrate_result = retain_memory(
        substrate, "fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert substrate_result["local_saved"] is True
    assert substrate_result["ok"] is False
    assert substrate_result["backends"]["substrate"]["state"] == "unreachable"

    local = _backends(tmp_path, events, selected="local")
    local.errors["substrate"] = "private endpoint detail"
    local.states["substrate"] = "unreachable"
    local_result = retain_memory(
        local, "local fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert local_result["local_saved"] is True
    assert local_result["ok"] is False
    assert local_result["backends"]["substrate"]["state"] == "unreachable"
    assert "private endpoint detail" not in str(local_result)

    hindsight = _backends(tmp_path, events, selected="local")
    hindsight.errors["hindsight"] = "private endpoint detail"
    hindsight.states["hindsight"] = "unreachable"
    hindsight_result = retain_memory(
        hindsight, "other fact", scope="tenant-a", repo="repo-a", kind="fact"
    )
    assert hindsight_result["local_saved"] is True
    assert hindsight_result["ok"] is True
    assert hindsight_result["backends"]["hindsight"]["state"] == "unreachable"
    assert hindsight_result["backends"]["hindsight"]["attempted"] is False
    assert "private endpoint detail" not in str(hindsight_result)


def test_substrate_configuration_error_fails_every_selector(tmp_path: Path) -> None:
    for selector in ("local", "substrate", "hindsight"):
        events: list[str] = []
        backends = _backends(tmp_path, events, selected=selector)
        backends.errors["substrate"] = "construction failed"
        backends.states["substrate"] = "unreachable"

        result = retain_memory(
            backends, "fact", scope="tenant-a", repo="repo-a", kind="fact"
        )

        assert result["local_saved"] is True
        assert result["ok"] is False
        assert result["backends"]["substrate"]["state"] == "unreachable"


def test_intentionally_unconfigured_substrate_does_not_fail_local_retain(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    backends = _backends(tmp_path, events, selected="local")
    backends.errors["substrate"] = "gateway skipped because not configured"
    backends.states["substrate"] = "not_configured"

    result = retain_memory(
        backends, "local fact", scope="tenant-a", repo="repo-a", kind="fact"
    )

    assert result["local_saved"] is True
    assert result["ok"] is True
    assert result["backends"]["substrate"]["state"] == "not_configured"
    assert result["backends"]["substrate"]["attempted"] is False

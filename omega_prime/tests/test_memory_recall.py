"""Behavioral tests for unified, tenant-safe memory recall."""

import json
from typing import Any

from omega_prime.memory.backends import (
    LocalRecord,
    encode_local_record,
    resolve_memory_backends,
)
from omega_prime.memory.recall import recall_memory
from omega_prime.memory.store import MemoryStore


class Substrate:
    def __init__(self, hits=None, error=None):
        self.hits = [] if hits is None else hits
        self.error = error
        self.calls = []

    def memory_search(self, query, scope, limit, *, repo=None):
        self.calls.append((query, scope, limit, repo))
        if self.error:
            raise self.error
        return self.hits


class Hindsight:
    bank = "remote-bank"

    def __init__(self, hits=None, error=None):
        self.hits = [] if hits is None else hits
        self.error = error
        self.calls = []

    def recall(self, query, *, limit):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return self.hits


def _record(
    content: str,
    *,
    repo: str | None = "repo-a",
    bank: str = "omega-prime-lead",
    source: str = "runbook",
) -> LocalRecord:
    return LocalRecord(
        bank=bank,
        scope="team",
        repo=repo,
        kind="fact",
        content=content,
        source=source,
        receipt_path=None,
        tags=(),
        graph_id=None,
        task_id=None,
    )


def _remote_metadata(repo: str | None) -> str:
    return "omega-memory:v1:" + json.dumps({"scope": "team", "repo": repo})


class TrackingMemoryStore(MemoryStore):
    def __init__(self, directory: Any) -> None:
        self.load_calls = 0
        super().__init__(directory)

    def load_from_disk(self) -> None:
        self.load_calls += 1
        super().load_from_disk()


def test_repo_scoped_local_recall_excludes_legacy_and_unscoped_records(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.add("memory", encode_local_record(_record("Ship it after review")))
    store.add(
        "memory",
        encode_local_record(_record("Wrong repository review", repo="repo-b")),
    )
    store.add(
        "memory",
        encode_local_record(_record("No repository review", repo=None)),
    )
    store.add("memory", "[hindsight:omes-lead] legacy review note")
    backends = resolve_memory_backends(
        local_store=store, scope="team", repo="repo-a", environ={}
    )

    result = recall_memory(backends, "review", scope="team", repo="repo-a", limit=5)

    assert [hit["content"] for hit in result["results"]] == ["Ship it after review"]
    assert result["results"][0]["content_hash"].startswith("sha256:")
    assert len(result["results"][0]["content_hash"]) == len("sha256:") + 64
    assert result["selected_backend"] == "local"
    assert result["scope"] == "team"
    assert result["repo"] == "repo-a"
    assert result["backends"]["local"] == {"state": "answered", "attempted": True}
    assert result["backends"]["substrate"] == {
        "state": "not_configured",
        "attempted": False,
    }
    assert result["backends"]["hindsight"] == {
        "state": "not_configured",
        "attempted": False,
    }
    assert result["results"][0]["sources"] == [
        {
            "backend": "local",
            "bank": "omega-prime-lead",
            "scope": "team",
            "repo": "repo-a",
            "kind": "fact",
            "source": "runbook",
            "receipt_path": None,
            "tags": [],
            "graph_id": None,
            "task_id": None,
        }
    ]


def test_unscoped_local_recall_keeps_legacy_banks(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.add("memory", "[hindsight:omes-lead] legacy review note")
    backends = resolve_memory_backends(local_store=store, scope="team", environ={})

    result = recall_memory(backends, "review", scope="team", repo=None, limit=5)

    assert [hit["content"] for hit in result["results"]] == ["legacy review note"]
    assert result["results"][0]["sources"] == [
        {"backend": "local", "bank": "omes-lead"}
    ]
    assert result["backends"]["local"]["state"] == "answered"


def test_local_recall_redacts_content_and_nested_source_fields(tmp_path):
    secret = "ordinary-test-fragment"
    store = MemoryStore(tmp_path / "memory")
    store.add(
        "memory",
        encode_local_record(
            _record(
                f"deployment token: {secret}",
                source=f"Bearer {secret}",
            )
        ),
    )
    backends = resolve_memory_backends(local_store=store, scope="team", environ={})

    result = recall_memory(backends, "deployment", scope="team", repo="repo-a", limit=1)

    hit = result["results"][0]
    assert secret not in hit["content"]
    assert "[REDACTED]" in hit["content"]
    assert secret not in str(hit["sources"])
    assert "[REDACTED]" in hit["sources"][0]["source"]


def test_plain_legacy_memory_entries_do_not_make_local_recall_unreachable(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.add("memory", "Old plain memory note about review")
    backends = resolve_memory_backends(local_store=store, scope="team", environ={})

    result = recall_memory(backends, "review", scope="team", repo=None, limit=2)

    assert result["results"] == []
    assert result["backends"]["local"] == {"state": "empty", "attempted": True}


def test_unreadable_memory_file_reports_local_unreachable(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    (tmp_path / "memory" / "MEMORY.md").write_bytes(b"\xff")
    backends = resolve_memory_backends(local_store=store, scope="team", environ={})

    result = recall_memory(backends, "anything", scope="team", repo=None, limit=2)

    assert result["backends"]["local"]["state"] == "unreachable"
    assert result["backends"]["local"]["attempted"] is True
    assert "could not be read" in result["backends"]["local"]["error"]


def test_remote_hits_merge_with_stable_source_order_and_full_provenance(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.add("memory", encode_local_record(_record("shared\r\nfact")))
    substrate = Substrate(
        [
            {
                "text": " shared\r\nfact ",
                "id": "sub-1",
                "bank": "sub-bank",
                "scope": "team",
                "repo": "repo-a",
                "source_event": _remote_metadata("repo-a"),
                "score": 0.8,
                "source": "substrate-index",
                "evidence": {"path": "docs/a"},
            }
        ]
    )
    hindsight = Hindsight(
        [
            {
                "text": "shared\nfact",
                "id": "hind-1",
                "bank": "hind-bank",
                "scope": "team",
                "repo": "repo-a",
                "context": _remote_metadata("repo-a"),
                "score": 0.9,
                "source": "hindsight-observation",
            }
        ]
    )
    backends = resolve_memory_backends(
        local_store=store,
        substrate=substrate,
        hindsight=hindsight,
        scope="team",
        repo="repo-a",
        environ={"OMEGA_PRIME_MEMORY_BACKEND": "local"},
    )

    result = recall_memory(backends, "fact", scope="team", repo="repo-a", limit=5)

    assert substrate.calls == [("fact", "team", 50, "repo-a")]
    assert hindsight.calls == [("fact", 50)]
    assert result["results"] == [
        {
            "content": "shared\nfact",
            "content_hash": result["results"][0]["content_hash"],
            "sources": [
                {
                    "backend": "local",
                    "bank": "omega-prime-lead",
                    "scope": "team",
                    "repo": "repo-a",
                    "kind": "fact",
                    "source": "runbook",
                    "receipt_path": None,
                    "tags": [],
                    "graph_id": None,
                    "task_id": None,
                },
                {
                    "backend": "substrate",
                    "id": "sub-1",
                    "bank": "sub-bank",
                    "scope": "team",
                    "repo": "repo-a",
                    "score": 0.8,
                    "source": "substrate-index",
                    "evidence": {"path": "docs/a"},
                },
                {
                    "backend": "hindsight",
                    "id": "hind-1",
                    "bank": "hind-bank",
                    "scope": "team",
                    "repo": "repo-a",
                    "score": 0.9,
                    "source": "hindsight-observation",
                },
            ],
        }
    ]
    assert result["results"][0]["content_hash"].startswith("sha256:")
    assert result["backends"]["substrate"] == {"state": "answered", "attempted": True}
    assert result["backends"]["hindsight"] == {"state": "answered", "attempted": True}


def test_remote_scoping_and_include_shared_only_narrow_local_banks(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.add("memory", encode_local_record(_record("lead local fact")))
    store.add(
        "memory",
        encode_local_record(_record("shared local fact", bank="omega-prime-desk")),
    )
    substrate = Substrate(
        [
            {
                "text": "no repo fact",
                "scope": "team",
                "source_event": _remote_metadata(None),
            },
            {
                "text": "no scope fact",
                "repo": "repo-a",
                "source_event": _remote_metadata(None),
            },
            {
                "text": "wrong repo fact",
                "scope": "team",
                "repo": "repo-b",
                "source_event": _remote_metadata("repo-b"),
            },
            {
                "text": "wrong scope fact",
                "scope": "other",
                "repo": "repo-a",
                "source_event": _remote_metadata("repo-a"),
            },
            {
                "text": "substrate fact",
                "scope": "team",
                "repo": "repo-a",
                "source_event": _remote_metadata("repo-a"),
            },
        ]
    )
    hindsight = Hindsight(
        [
            {
                "text": "hindsight no repo fact",
                "scope": "team",
                "context": _remote_metadata(None),
            },
            {
                "text": "hindsight wrong repo fact",
                "scope": "team",
                "repo": "repo-b",
                "context": _remote_metadata("repo-b"),
            },
            {
                "text": "hindsight fact",
                "scope": "team",
                "repo": "repo-a",
                "context": _remote_metadata("repo-a"),
            },
        ]
    )
    backends = resolve_memory_backends(
        local_store=store,
        substrate=substrate,
        hindsight=hindsight,
        scope="team",
        repo="repo-a",
        environ={},
    )

    result = recall_memory(
        backends,
        "fact",
        scope="team",
        repo="repo-a",
        limit=10,
        include_shared=False,
    )

    assert [hit["content"] for hit in result["results"]] == [
        "lead local fact",
        "substrate fact",
        "hindsight fact",
    ]
    assert substrate.calls == [("fact", "team", 100, "repo-a")]
    assert hindsight.calls == [("fact", 100)]
    assert result["backends"]["substrate"]["state"] == "answered"
    assert result["backends"]["hindsight"]["state"] == "answered"


def test_substrate_marker_metadata_restores_remote_scope_and_repo(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    marker = "omega-memory:v1:" + json.dumps({"scope": "team", "repo": "repo-a"})
    substrate = Substrate(
        [
            {"text": "same repo fact", "source_event": marker},
            {
                "text": "wrong repo fact",
                "source_event": "omega-memory:v1:"
                + json.dumps({"scope": "team", "repo": "repo-b"}),
            },
            {
                "text": "malformed provenance",
                "source_event": "omega-memory:v1:not-json",
            },
        ]
    )
    backends = resolve_memory_backends(
        local_store=store, substrate=substrate, scope="team", repo="repo-a", environ={}
    )

    result = recall_memory(backends, "fact", scope="team", repo="repo-a", limit=10)

    assert [hit["content"] for hit in result["results"]] == ["same repo fact"]


def test_hindsight_marker_metadata_restores_remote_scope_and_repo(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    marker = "omega-memory:v1:" + json.dumps({"scope": "team", "repo": "repo-a"})
    hindsight = Hindsight(
        [
            {"text": "same repo fact", "context": marker},
            {
                "text": "tags cannot establish ownership",
                "tags": [_remote_metadata("repo-a")],
            },
            {
                "text": "spoofed response metadata",
                "scope": "team",
                "repo": "repo-a",
            },
            {
                "text": "conflicting response metadata",
                "scope": "team",
                "repo": "repo-a",
                "context": _remote_metadata("repo-b"),
            },
            {"text": "malformed provenance", "context": "omega-memory:v1:{}"},
        ]
    )
    backends = resolve_memory_backends(
        local_store=store, hindsight=hindsight, scope="team", repo="repo-a", environ={}
    )

    result = recall_memory(backends, "fact", scope="team", repo="repo-a", limit=10)

    assert [hit["content"] for hit in result["results"]] == ["same repo fact"]


def test_remote_response_cannot_spoof_repository_metadata(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    hindsight = Hindsight(
        [
            {
                "text": "repo-a presented as repo-b",
                "scope": "team",
                "repo": "repo-b",
                "context": _remote_metadata("repo-a"),
            },
            {
                "text": "untrusted repo-b response field",
                "scope": "team",
                "repo": "repo-b",
            },
            {
                "text": "trusted repo-b fact",
                "scope": "team",
                "repo": "repo-b",
                "context": _remote_metadata("repo-b"),
            },
        ]
    )
    backends = resolve_memory_backends(
        local_store=store, hindsight=hindsight, scope="team", repo="repo-b", environ={}
    )

    result = recall_memory(backends, "fact", scope="team", repo="repo-b", limit=10)

    assert [hit["content"] for hit in result["results"]] == ["trusted repo-b fact"]


def test_remote_sources_preserve_order_and_status_before_global_limit(tmp_path):
    substrate = Substrate([{"text": "first"}, {"text": "second"}])
    hindsight = Hindsight([{"text": "third"}])
    backends = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate=substrate,
        hindsight=hindsight,
        environ={},
    )

    result = recall_memory(backends, "search", scope="default", repo=None, limit=2)

    assert [hit["content"] for hit in result["results"]] == ["first", "second"]
    assert all(hit["content_hash"].startswith("sha256:") for hit in result["results"])
    assert result["backends"]["local"] == {"state": "empty", "attempted": True}
    assert result["backends"]["substrate"] == {"state": "answered", "attempted": True}
    assert result["backends"]["hindsight"] == {"state": "answered", "attempted": True}


def test_source_failures_are_independent_and_structured(tmp_path):
    class Unauthorized(RuntimeError):
        status = "unauthorized"

    substrate = Substrate(error=Unauthorized("Bearer exposed-secret"))
    hindsight = Hindsight([{"text": "still available", "score": 1.0}])
    backends = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate=substrate,
        hindsight=hindsight,
        scope="team",
        environ={},
    )

    result = recall_memory(backends, "available", scope="team", repo=None, limit=2)

    assert [hit["content"] for hit in result["results"]] == ["still available"]
    assert result["backends"]["substrate"]["state"] == "unauthorized"
    assert result["backends"]["substrate"]["attempted"] is True
    assert "[REDACTED]" in result["backends"]["substrate"]["error"]
    assert result["backends"]["hindsight"] == {"state": "answered", "attempted": True}


def test_unscoped_recall_excludes_repo_scoped_local_and_remote_records(tmp_path):
    class QuerySubstrate(Substrate):
        def memory_search(self, query, scope, limit, *, repo=None):
            super().memory_search(query, scope, limit, repo=repo)
            return [hit for hit in self.hits if query.lower() in hit["text"].lower()]

    class QueryHindsight(Hindsight):
        def recall(self, query, *, limit):
            super().recall(query, limit=limit)
            return [hit for hit in self.hits if query.lower() in hit["text"].lower()]

    store = MemoryStore(tmp_path / "memory")
    store.add("memory", encode_local_record(_record("open common fact", repo=None)))
    store.add(
        "memory",
        encode_local_record(_record("private repo fact", repo="repo-a")),
    )
    substrate = QuerySubstrate(
        [
            {"text": "unscoped remote fact", "source_event": _remote_metadata(None)},
            {"text": "repo-a remote fact", "source_event": _remote_metadata("repo-a")},
            {"text": "repo-a without metadata", "repo": "repo-a"},
        ]
    )
    hindsight = QueryHindsight(
        [
            {"text": "unscoped episode", "context": _remote_metadata(None)},
            {"text": "repo-a episode", "context": _remote_metadata("repo-a")},
        ]
    )
    backends = resolve_memory_backends(
        local_store=store,
        substrate=substrate,
        hindsight=hindsight,
        scope="team",
        environ={},
    )

    result = recall_memory(backends, "fact", scope="team", repo=None, limit=10)
    contents = {hit["content"] for hit in result["results"]}

    assert "open common fact" in contents
    assert "private repo fact" not in contents
    assert "unscoped remote fact" in contents
    assert "repo-a remote fact" not in contents
    assert "repo-a without metadata" not in contents
    episodes = recall_memory(backends, "episode", scope="team", repo=None, limit=10)
    episode_contents = {hit["content"] for hit in episodes["results"]}
    assert "unscoped episode" in episode_contents
    assert "repo-a episode" not in episode_contents


def test_protocol_and_tool_error_types_are_preserved_in_diagnostics(tmp_path):
    from omega_prime.substrate.client import SubstrateProtocolError, SubstrateToolError

    for exc, expected in (
        (SubstrateProtocolError("bad response"), "protocol_error"),
        (SubstrateToolError("tool failed"), "tool_error"),
    ):
        backends = resolve_memory_backends(
            local_store=MemoryStore(tmp_path / expected),
            substrate=Substrate(error=exc),
            environ={},
        )
        result = recall_memory(backends, "q", scope="default", repo=None, limit=1)
        assert result["backends"]["substrate"]["state"] == "unreachable"
        assert result["backends"]["substrate"]["error_type"] == expected


def test_exception_text_does_not_imply_authorization_failure(tmp_path):
    substrate = Substrate(error=RuntimeError("unauthorized"))
    backends = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate=substrate,
        environ={},
    )

    result = recall_memory(backends, "q", scope="default", repo=None, limit=1)

    assert result["backends"]["substrate"]["state"] == "unreachable"


def test_malformed_payload_is_not_classified_as_empty(tmp_path):
    substrate = Substrate([{"text": 7}])
    hindsight = Hindsight([])
    backends = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate=substrate,
        hindsight=hindsight,
        environ={},
    )

    result = recall_memory(backends, "q", scope="default", repo=None, limit=2)

    assert result["backends"]["local"] == {"state": "empty", "attempted": True}
    assert result["backends"]["substrate"]["state"] == "unreachable"
    assert (
        result["backends"]["substrate"]["error"] == "source returned malformed hit data"
    )
    assert result["backends"]["hindsight"] == {"state": "empty", "attempted": True}


def test_remote_recall_redacts_content_and_nested_source_fields(tmp_path):
    secret = "ordinary-test-fragment"
    payload = [
        {
            "text": f"remote deployment token: {secret}",
            "scope": "team",
            "source": f"Bearer {secret}",
            "evidence": {"nested": [f"api_key={secret}"]},
        }
    ]
    backends = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate=Substrate(payload),
        scope="team",
        environ={},
    )

    result = recall_memory(backends, "deployment", scope="team", repo=None, limit=1)

    hit = result["results"][0]
    assert secret not in hit["content"]
    assert "[REDACTED]" in hit["content"]
    assert secret not in str(hit["sources"])
    assert "[REDACTED]" in hit["sources"][0]["evidence"]["nested"][0]


def test_remote_limits_apply_after_scope_filtering_and_deduplication(tmp_path):
    substrate = Substrate(
        [
            {"text": "wrong scope", "scope": "other"},
            {"text": "repeat", "scope": "team"},
            {"text": "repeat", "scope": "team"},
            {"text": "substrate second", "scope": "team"},
        ]
    )
    hindsight = Hindsight(
        [
            {"text": "wrong scope", "scope": "other"},
            {"text": "repeat", "scope": "team"},
            {"text": "repeat", "scope": "team"},
            {"text": "hindsight second", "scope": "team"},
        ]
    )
    backends = resolve_memory_backends(
        local_store=MemoryStore(tmp_path / "memory"),
        substrate=substrate,
        hindsight=hindsight,
        scope="team",
        environ={},
    )

    result = recall_memory(backends, "search", scope="team", repo=None, limit=2)

    assert [hit["content"] for hit in result["results"]] == [
        "repeat",
        "substrate second",
    ]
    assert substrate.calls == [("search", "team", 20, None)]
    assert hindsight.calls == [("search", 20)]


def test_validation_happens_before_any_source_io(tmp_path):
    substrate = Substrate([{"text": "unobserved"}])
    hindsight = Hindsight([{"text": "unobserved"}])
    store = TrackingMemoryStore(tmp_path / "memory")
    backends = resolve_memory_backends(
        local_store=store, substrate=substrate, hindsight=hindsight, environ={}
    )
    store.load_calls = 0

    invalid_arguments: tuple[dict[str, Any], ...] = (
        {"limit": True},
        {"limit": 0},
        {"include_shared": 1},
        {"banks": ["bad\nbank"]},
    )
    for invalid in invalid_arguments:
        arguments: dict[str, Any] = {
            "limit": 1,
            "include_shared": True,
            "banks": None,
        }
        arguments.update(invalid)
        try:
            recall_memory(
                backends,
                "q",
                scope="default",
                repo=None,
                **arguments,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid recall arguments were accepted: {invalid}")

    assert store.load_calls == 0
    assert substrate.calls == []
    assert hindsight.calls == []

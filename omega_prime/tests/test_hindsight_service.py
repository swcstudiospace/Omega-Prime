"""Phase 40: Hindsight service client + local-fallback bridge."""

from __future__ import annotations

import pytest

from omega_prime.memory.hindsight import Hindsight
from omega_prime.memory.hindsight_bridge import HindsightBridge
from omega_prime.memory.hindsight_service import HindsightError, HindsightService
from omega_prime.memory.store import MemoryStore
from omega_prime.providers.base import ProviderError

TOKEN = "hs-secret-token"
BANK_URL = "https://hs.local/v1/default/banks/ultrathink"


class _FakeTransport:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[dict] = []

    def _next(self, method: str, url: str, headers: dict, body: dict):
        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        if not self._script:
            raise AssertionError("fake transport script exhausted")
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def post(self, url: str, headers: dict, body: dict):
        return self._next("POST", url, headers, body)

    def get(self, url: str, headers: dict, params: dict):
        return self._next("GET", url, headers, params)


class _FakeBroker:
    def __init__(self, token: str = TOKEN) -> None:
        self._token = token

    def key_for(self, provider, url: str) -> str:
        return self._token

    def redact(self, text):
        if isinstance(text, str) and self._token:
            return text.replace(self._token, "[REDACTED]")
        return text


def _service(
    script: list, broker: bool = True
) -> tuple[HindsightService, _FakeTransport]:
    transport = _FakeTransport(script)
    return (
        HindsightService(
            "https://hs.local",
            transport=transport,
            broker=_FakeBroker() if broker else None,
        ),
        transport,
    )


def test_retain_str_body_and_auth() -> None:
    service, transport = _service(
        [{"success": True, "bank_id": "ultrathink", "items_count": 2}]
    )
    result = service.retain(
        ["sky is blue", "grass is green"],
        context="ctx",
        document_id="d1",
        tags=["t1"],
    )
    assert result == {"ok": True, "count": 2}
    request = transport.requests[0]
    assert request["url"] == BANK_URL + "/memories"
    assert request["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert request["body"] == {
        "items": [
            {
                "content": "sky is blue",
                "context": "ctx",
                "document_id": "d1",
                "tags": ["t1"],
            },
            {
                "content": "grass is green",
                "context": "ctx",
                "document_id": "d1",
                "tags": ["t1"],
            },
        ]
    }


def test_retain_dict_items_and_refusal() -> None:
    service, transport = _service([{"success": True, "bank_id": "u", "items_count": 1}])
    assert service.retain([{"content": "x", "tags": ["k"]}]) == {"ok": True, "count": 1}
    assert transport.requests[0]["body"] == {
        "items": [
            {"content": "x", "tags": ["k"]},
        ]
    }
    refusing, _ = _service([{"success": False, "bank_id": "u", "items_count": 0}])
    result = refusing.retain(["x"])
    assert result["ok"] is False and "ultrathink" in result["reason"]


def test_retain_blanks_skipped_without_request() -> None:
    service, transport = _service([])
    assert service.retain(["", "   "]) == {"ok": True, "count": 0}
    assert transport.requests == []
    with pytest.raises(ValueError):
        # Intentional misuse: a bare string is not a list of items.
        service.retain("nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        service.retain([{"content": "  "}])
    with pytest.raises(ValueError):
        service.retain([42])


def test_retain_transport_failure_raises() -> None:
    service, _ = _service([ProviderError("down")])
    with pytest.raises(HindsightError):
        service.retain(["x"])


def test_recall_texts_and_limit() -> None:
    results = [{"id": f"m{i}", "text": f"fact {i}"} for i in range(5)]
    service, transport = _service([{"results": results}])
    assert service.recall("sky", limit=2) == ["fact 0", "fact 1"]
    assert transport.requests[0]["body"] == {
        "query": "sky",
        "types": ["observation"],
        "budget": "low",
        "max_tokens": 2000,
    }
    assert transport.requests[0]["url"] == BANK_URL + "/memories/recall"


def test_recall_empty_query_and_errors() -> None:
    service, transport = _service([])
    assert service.recall("") == []
    assert transport.requests == []
    with pytest.raises(ValueError):
        service.recall("x", limit=0)
    with pytest.raises(ValueError):
        service.recall("x", types=["nope"])
    failing, _ = _service([ProviderError("down")])
    with pytest.raises(HindsightError):
        failing.recall("x")
    malformed, _ = _service([{"results": "nope"}])
    with pytest.raises(HindsightError):
        malformed.recall("x")


def test_reflect_text_passthrough() -> None:
    service, transport = _service([{"text": "## answer\n"}])
    assert service.reflect("why?") == "## answer\n"
    assert transport.requests[0]["url"] == BANK_URL + "/reflect"
    assert transport.requests[0]["body"]["budget"] == "low"
    quiet, quiet_transport = _service([])
    assert quiet.reflect("") == ""
    assert quiet_transport.requests == []
    with pytest.raises(ValueError):
        service.reflect("x", budget="ultra")
    failing, _ = _service([ProviderError("down")])
    with pytest.raises(HindsightError):
        failing.reflect("x")


def test_search_pages_hits() -> None:
    hits = [{"page_id": "p1", "title": "t"}, "junk"]
    service, transport = _service([{"results": hits, "total": 1}])
    assert service.search_pages("sky") == [{"page_id": "p1", "title": "t"}]
    request = transport.requests[0]
    assert request["method"] == "GET"
    assert request["url"] == BANK_URL + "/knowledge-base/search"
    assert request["body"] == {"q": "sky", "limit": 3}
    quiet, quiet_transport = _service([])
    assert quiet.search_pages("") == []
    assert quiet_transport.requests == []
    failing, _ = _service([ProviderError("down")])
    with pytest.raises(HindsightError):
        failing.search_pages("x")


def test_bad_bank_and_base_url() -> None:
    with pytest.raises(ValueError):
        HindsightService("https://hs.local", bank="has space")
    with pytest.raises(ValueError):
        HindsightService("notaurl")
    assert HindsightService("https://hs.local/").base_url == "https://hs.local"


def test_default_base_url_is_unset_and_refuses_to_construct() -> None:
    from pathlib import Path

    from omega_prime.memory.hindsight_service import DEFAULT_BASE_URL

    assert DEFAULT_BASE_URL == ""
    with pytest.raises(ValueError, match="non-empty"):
        HindsightService()
    body = (
        Path(__file__).resolve().parents[1] / "memory" / "hindsight_service.py"
    ).read_text(encoding="utf-8")
    assert "hindsight-api-production" not in body
    assert "up.railway.app" not in body


def test_token_never_leaks() -> None:
    service, _ = _service([ProviderError(f"bad key {TOKEN}")])
    with pytest.raises(HindsightError) as exc:
        service.recall("x")
    assert TOKEN not in str(exc.value)


def _bridge(script: list, tmp_path) -> tuple[HindsightBridge, _FakeTransport]:
    service, transport = _service(script)
    store = MemoryStore(tmp_path / "mem")
    local = Hindsight(store, bank="ultrathink")
    local.retain("local sky fact")
    return HindsightBridge(service, local), transport


def test_bridge_service_ok_passthrough(tmp_path) -> None:
    bridge, transport = _bridge(
        [
            {"success": True, "bank_id": "u", "items_count": 1},
            {"results": [{"id": "m1", "text": "shared sky fact"}]},
        ],
        tmp_path,
    )
    assert bridge.retain("sky") == {"ok": True, "count": 1}
    assert bridge.recall("sky") == ["shared sky fact"]
    assert len(transport.requests) == 2


def test_bridge_falls_back_to_local(tmp_path) -> None:
    bridge, _ = _bridge([ProviderError("down")] * 4, tmp_path)
    assert bridge.retain("local grass fact") == {"ok": True}
    assert bridge.recall("local") == ["local sky fact", "local grass fact"]
    # Intentional misuse: blanks and non-strings are skipped, not sent.
    assert bridge.retain_batch(["a", "", 42, "b"]) == {"ok": True, "count": 2}  # type: ignore[list-item]
    assert bridge.reflect("why?") == ""
    assert bridge.search_pages("sky") == []


def test_bridge_batch_service_path_skips_blanks(tmp_path) -> None:
    bridge, transport = _bridge(
        [{"success": True, "bank_id": "u", "items_count": 2}], tmp_path
    )
    # Intentional misuse: blanks and non-strings are skipped, not sent.
    assert bridge.retain_batch(["a", "", "b", 7]) == {"ok": True, "count": 2}  # type: ignore[list-item]
    assert transport.requests[0]["body"] == {
        "items": [{"content": "a"}, {"content": "b"}]
    }
    assert bridge.retain("  ") == {
        "ok": False,
        "reason": "content must be a non-empty string",
    }
    # Intentional misuse: a bare string is not a list of items.
    assert bridge.retain_batch("nope") == {  # type: ignore[arg-type]
        "ok": False,
        "reason": "items must be a list of strings",
    }

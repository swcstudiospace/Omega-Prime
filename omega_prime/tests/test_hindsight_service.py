"""Hindsight service client contract tests."""

from __future__ import annotations

import pytest

from omega_prime.memory.hindsight_service import HindsightError, HindsightService
from omega_prime.providers.base import ProviderError
from omega_prime.providers.http import HttpTransport

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


def test_recall_hits_and_limit() -> None:
    results = [
        {"id": f"m{i}", "text": f"fact {i}", "score": i, "metadata": {"source": "test"}}
        for i in range(5)
    ]
    service, transport = _service([{"results": results}])
    assert service.recall("sky", limit=2) == results[:2]
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
    hits = [{"page_id": "p1", "title": "t"}]
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
    for url in (
        "http://hs.local",
        "https://user:pass@hs.local",
        "https://hs.local/path",
        "https://hs.local?token=secret",
        "https://hs.local#fragment",
    ):
        with pytest.raises(ValueError):
            HindsightService(url)
    for bank in ("has space", "../admin", "bank/other", "%2fadmin"):
        with pytest.raises(ValueError):
            HindsightService("https://hs.local", bank=bank)
    assert HindsightService("https://hs.local/").base_url == "https://hs.local"


def test_default_base_url_is_unset_and_refuses_to_construct() -> None:
    from omega_prime.memory.hindsight_service import DEFAULT_BASE_URL

    assert DEFAULT_BASE_URL == ""
    with pytest.raises(ValueError, match="explicitly configured"):
        HindsightService()


def test_token_never_leaks() -> None:
    service, _ = _service([ProviderError(f"bad key {TOKEN}")])
    with pytest.raises(HindsightError) as exc:
        service.recall("x")
    assert TOKEN not in str(exc.value)


def test_malformed_responses_raise_typed_error() -> None:
    service, _ = _service([{"results": ["ignored malformed row"]}])
    with pytest.raises(HindsightError) as exc:
        service.recall("sky")
    assert exc.value.code == "malformed_response"
    assert exc.value.status == "unreachable"

    service, _ = _service([{}])
    with pytest.raises(HindsightError) as exc:
        service.retain(["sky"])
    assert exc.value.code == "malformed_response"


def test_auth_failure_uses_structured_status_and_redaction_failure_is_safe() -> None:
    service, _ = _service([ProviderError("HTTP 401 from endpoint")])
    with pytest.raises(HindsightError) as exc:
        service.recall("sky")
    assert exc.value.status == "unreachable"
    assert exc.value.code == "transport_error"

    class TypedStatusError(Exception):
        status_code = 401

    service, _ = _service([TypedStatusError("request denied")])
    with pytest.raises(HindsightError) as exc:
        service.recall("sky")
    assert exc.value.status == "unauthorized"
    assert exc.value.code == "http_401"

    class BrokenRedactor(_FakeBroker):
        def redact(self, text):
            raise RuntimeError(TOKEN)

    transport = _FakeTransport([ProviderError(f"private {TOKEN}")])
    service = HindsightService(
        "https://hs.local", transport=transport, broker=BrokenRedactor()
    )
    with pytest.raises(HindsightError) as exc:
        service.recall("sky")
    assert TOKEN not in str(exc.value)


def test_http_status_error_is_typed_unauthorized_end_to_end(monkeypatch) -> None:
    transport = HttpTransport(max_retries=0)
    response_body = b'{"secret":"response body must stay private"}'
    monkeypatch.setattr(
        transport,
        "_request",
        lambda *_args: (403, {}, response_body),
    )
    service = HindsightService(
        "https://hs.local", transport=transport, broker=_FakeBroker()
    )

    with pytest.raises(HindsightError) as exc:
        service.recall("sky")

    assert exc.value.status == "unauthorized"
    assert exc.value.code == "http_403"
    assert "response body must stay private" not in str(exc.value)
    assert "hs.local" not in str(exc.value)


def test_broker_failure_is_sanitized_unauthorized() -> None:
    private_detail = "broker response contains private credential details"

    class FailingBroker(_FakeBroker):
        def key_for(self, provider, url: str) -> str:
            raise RuntimeError(private_detail)

    service = HindsightService(
        "https://hs.local", transport=_FakeTransport([]), broker=FailingBroker()
    )

    with pytest.raises(HindsightError) as exc:
        service.recall("sky")

    assert exc.value.status == "unauthorized"
    assert exc.value.code == "credentials_unavailable"
    assert private_detail not in str(exc.value)


def test_default_hindsight_transport_disables_retries() -> None:
    service = HindsightService("https://hs.local")

    assert service._transport._max_retries == 0

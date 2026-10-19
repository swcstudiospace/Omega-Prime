"""Episodic memory against a configured Hindsight service.

The base URL defaults to empty, which means the remote bank is disabled.
Callers pass ``HINDSIGHT_URL`` when they operate their own service. There is
no hosted default.

``HindsightService`` speaks the hindsight-api bank routes — retain, recall,
reflect, knowledge-page search — on the shared ``ultrathink`` bank. The
client is truthful: transport, auth, and malformed-response failures raise
``HindsightError`` so the caller (the bridge, the loop) decides how to
degrade. Request/response shapes follow hindsight-api OpenAPI v0.9.1; recall
defaults match the hindsight-coding-agent plugin body.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from omega_prime.memory.hindsight import _check_bank
from omega_prime.providers.http import HttpTransport

DEFAULT_BANK = "ultrathink"
DEFAULT_BASE_URL = ""

_HINDSIGHT_PROVIDER = SimpleNamespace(
    name="hindsight",
    requires_key=True,
    env_vars=("HINDSIGHT_API_KEY", "HINDSIGHT_API_TOKEN"),
)

_RECALL_TYPES = ("world", "experience", "observation")
_BUDGETS = ("low", "mid", "high")


class HindsightError(Exception):
    """The Hindsight call failed and the caller must know."""


class HindsightService:
    """Retain/recall/reflect/search on one Hindsight bank."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        bank: str = DEFAULT_BANK,
        *,
        transport: Any = None,
        broker: Any = None,
        timeout: float = 30.0,
    ) -> None:
        _check_bank(bank)
        self._base_url = _check_base_url(base_url)
        self._bank = bank
        self._broker = broker
        self._transport = transport or HttpTransport(timeout=timeout)

    @property
    def base_url(self) -> str:
        """The hindsight-api base URL, without a trailing slash."""
        return self._base_url

    @property
    def bank(self) -> str:
        """The bank every call is scoped to."""
        return self._bank

    def retain(
        self,
        items: list,
        *,
        context: str | None = None,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Keep each item in the bank. Blank strings are skipped, not errors."""
        payload_items = _retain_items(items, context, document_id, tags)
        if not payload_items:
            return {"ok": True, "count": 0}
        try:
            response = self._transport.post(
                self._bank_url + "/memories",
                self._headers(),
                {"items": payload_items},
            )
        except Exception as exc:
            raise HindsightError(
                f"hindsight retain failed: {self._redact(_reason(exc))}"
            ) from exc
        if not isinstance(response, dict):
            raise HindsightError("hindsight retain returned no result object")
        if response.get("success") is True:
            count = response.get("items_count", len(payload_items))
            return {
                "ok": True,
                "count": count if isinstance(count, int) else len(payload_items),
            }
        return {"ok": False, "reason": f"bank {self._bank} refused retain"}

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        types: list[str] | None = None,
        max_tokens: int = 2000,
    ) -> list[str]:
        """Recall result texts for ``query``, most relevant first."""
        _check_limit(limit)
        if not isinstance(query, str) or query == "":
            return []
        wanted = ["observation"] if types is None else list(types)
        for entry in wanted:
            if entry not in _RECALL_TYPES:
                raise ValueError(f"unknown recall type {entry!r}")
        body = {
            "query": query,
            "types": wanted,
            "budget": "low",
            "max_tokens": max_tokens,
        }
        try:
            response = self._transport.post(
                self._bank_url + "/memories/recall", self._headers(), body
            )
        except Exception as exc:
            raise HindsightError(
                f"hindsight recall failed: {self._redact(_reason(exc))}"
            ) from exc
        results = response.get("results", []) if isinstance(response, dict) else []
        if not isinstance(results, list):
            raise HindsightError("hindsight recall returned no results list")
        texts = [
            item["text"]
            for item in results
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return texts[:limit]

    def reflect(
        self, query: str, *, budget: str = "low", max_tokens: int = 4096
    ) -> str:
        """Deep synthesis over the bank for ``query``."""
        if budget not in _BUDGETS:
            raise ValueError(f"unknown reflect budget {budget!r}")
        if not isinstance(query, str) or query == "":
            return ""
        try:
            response = self._transport.post(
                self._bank_url + "/reflect",
                self._headers(),
                {"query": query, "budget": budget, "max_tokens": max_tokens},
            )
        except Exception as exc:
            raise HindsightError(
                f"hindsight reflect failed: {self._redact(_reason(exc))}"
            ) from exc
        if not isinstance(response, dict) or not isinstance(response.get("text"), str):
            raise HindsightError("hindsight reflect returned no synthesis text")
        return response["text"]

    def search_pages(self, query: str, *, limit: int = 3) -> list[dict]:
        """Knowledge-page search hits for ``query``."""
        _check_limit(limit)
        if not isinstance(query, str) or query == "":
            return []
        try:
            response = self._transport.get(
                self._bank_url + "/knowledge-base/search",
                self._headers(),
                {"q": query, "limit": limit},
            )
        except Exception as exc:
            raise HindsightError(
                f"hindsight page search failed: {self._redact(_reason(exc))}"
            ) from exc
        results = response.get("results", []) if isinstance(response, dict) else []
        if not isinstance(results, list):
            raise HindsightError("hindsight page search returned no results list")
        return [item for item in results if isinstance(item, dict)]

    @property
    def _bank_url(self) -> str:
        return f"{self._base_url}/v1/default/banks/{self._bank}"

    def _headers(self) -> dict:
        if self._broker is None:
            return {}
        token = self._broker.key_for(_HINDSIGHT_PROVIDER, self._base_url)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _redact(self, text: str) -> str:
        if self._broker is None:
            return text
        try:
            return self._broker.redact(text)
        except Exception:
            return text


def _retain_items(
    items: list,
    context: str | None,
    document_id: str | None,
    tags: list[str] | None,
) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("items must be a list of strings or dicts")
    payload: list[dict] = []
    for item in items:
        if isinstance(item, str):
            if item.strip() == "":
                continue
            entry: dict[str, Any] = {"content": item.strip()}
            if context is not None:
                entry["context"] = context
            if document_id is not None:
                entry["document_id"] = document_id
            if tags is not None:
                entry["tags"] = list(tags)
            payload.append(entry)
        elif isinstance(item, dict):
            content = item.get("content")
            if not isinstance(content, str) or content.strip() == "":
                raise ValueError(
                    "retain dict items must carry non-empty string content"
                )
            payload.append(dict(item))
        else:
            raise ValueError("items must be a list of strings or dicts")
    return payload


def _check_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")


def _check_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or base_url == "":
        raise ValueError("base_url must be a non-empty http(s) URL")
    try:
        parts = urlsplit(base_url)
    except ValueError:
        raise ValueError("base_url must be a non-empty http(s) URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base_url must be a non-empty http(s) URL")
    return base_url.rstrip("/")


def _reason(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


__all__ = ["DEFAULT_BANK", "DEFAULT_BASE_URL", "HindsightError", "HindsightService"]

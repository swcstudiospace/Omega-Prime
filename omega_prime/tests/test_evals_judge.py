# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 84: the judge is provably zero-network when the flag is off."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime.evals import judge, rubric

CRITERION = {"id": "tone", "weight": 1.0, "check": {"type": "judge"}}

OFF_CONFIG = {"evals": {"judge": {"enabled": False, "model": ""}}}
ON_CONFIG = {"evals": {"judge": {"enabled": True, "model": "test-model"}}}


def test_judge_off_records_skipped_with_zero_network(monkeypatch):
    """Flag off: socket creation raises, socket-capable imports are blocked."""
    import socket

    def _no_network(*args, **kwargs):
        raise AssertionError("network attempted with judge off")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    blocked = ("socket", "ssl", "http.client", "urllib.request")

    class _BlockImports:
        def find_module(self, name, path=None):
            if name in blocked or any(
                name == part or name.startswith(part + ".") for part in blocked
            ):
                return self
            return None

        def load_module(self, name):
            raise AssertionError(f"socket-capable import attempted: {name}")

    hook: Any = _BlockImports()
    sys.meta_path.insert(0, hook)
    try:
        record = judge.score_criterion(
            dict(CRITERION), "transcript", OFF_CONFIG, environ={}
        )
    finally:
        sys.meta_path.remove(hook)
    assert record.get("judge") == "skipped"
    assert record.get("score") == 0.0
    assert "judge_unavailable" not in record


@pytest.mark.parametrize(
    "config", [{}, {"evals": {"judge": {"enabled": True, "model": ""}}}]
)
def test_judge_refuses_without_model_or_credential(config):
    record = judge.score_criterion(dict(CRITERION), "transcript", config, environ={})
    assert record.get("judge") == "skipped"
    assert "judge_unavailable" not in record


def test_judge_enabled_but_no_credential_skips():
    record = judge.score_criterion(dict(CRITERION), "transcript", ON_CONFIG, environ={})
    assert record.get("judge") == "skipped"


def test_judge_on_with_stubbed_endpoint_stores_score_and_rationale(monkeypatch):
    environ = {
        "OMEGA_PRIME_EVALS_JUDGE_API_KEY": "secret",
        "OMEGA_PRIME_EVALS_JUDGE_ENDPOINT": "https://judge.example/score",
    }
    seen: dict = {}

    def _stub(url, payload, timeout=30, api_key=None):
        seen["url"] = url
        seen["api_key"] = api_key
        return {"score": 0.8, "rationale": "polite refusal"}

    monkeypatch.setattr(judge, "_post_json", _stub)
    record = judge.score_criterion(
        dict(CRITERION), "transcript", ON_CONFIG, environ=environ
    )
    assert record.get("judge") == "scored"
    assert record.get("score") == pytest.approx(0.8)
    assert record.get("rationale") == "polite refusal"
    assert record.get("model") == "test-model"
    assert seen["api_key"] == "secret", "the configured key must reach the request"


def test_judge_endpoint_down_records_unavailable_and_completes(monkeypatch):
    environ = {
        "OMEGA_PRIME_EVALS_JUDGE_API_KEY": "secret",
        "OMEGA_PRIME_EVALS_JUDGE_ENDPOINT": "https://judge.example/score",
    }

    def _down(url, payload, timeout=30, api_key=None):
        raise ConnectionError("endpoint down")

    monkeypatch.setattr(judge, "_post_json", _down)
    record = judge.score_criterion(
        dict(CRITERION), "transcript", ON_CONFIG, environ=environ
    )
    assert record.get("score") == 0.0
    assert record.get("judge_unavailable") is True
    assert record.get("judge") == "unavailable"


def test_rubric_judge_criterion_skips_when_disabled(tmp_path: Path):
    case = {
        "case_id": "judge-demo",
        "user": "hi",
        "script": [{"role": "assistant", "content": "hello"}],
        "criteria": [
            {"id": "human-tone", "weight": 1.0, "check": {"type": "judge"}},
        ],
    }
    record = rubric.run_rubric_case(case, tmp_path / "judge-demo", config=OFF_CONFIG)
    assert record["criteria"][0].get("judge") == "skipped"
    assert record["score"] == pytest.approx(0.0)


def test_judge_module_top_level_imports_no_socket_stack():
    mods = ("socket", "ssl", "http.client", "urllib.request")
    saved = {module: sys.modules.pop(module, None) for module in mods}
    try:
        importlib.reload(judge)
        for module in mods:
            assert module not in sys.modules, f"{module} imported at judge top level"
    finally:
        for module in mods:
            mod = saved[module]
            if mod is not None:
                sys.modules[module] = mod
            else:
                sys.modules.pop(module, None)


def test_post_json_attaches_bearer_authorization_header(monkeypatch):
    """The configured key travels as ``Authorization: Bearer`` (hermetic)."""
    import urllib.request

    seen: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"score": 1.0, "rationale": "ok"}'

    def _capture(request, timeout=None):
        seen["auth"] = request.get_header("Authorization")
        seen["content_type"] = request.get_header("Content-type")
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _capture)
    answer = judge._post_json(
        "https://judge.example/score", {"model": "m"}, api_key="secret"
    )
    assert answer["score"] == 1.0
    assert seen["auth"] == "Bearer secret"
    assert seen["content_type"] == "application/json"


def test_post_json_without_key_sends_no_authorization_header(monkeypatch):
    import urllib.request

    seen: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"score": 0.0}'

    def _capture(request, timeout=None):
        seen["auth"] = request.get_header("Authorization")
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _capture)
    judge._post_json("https://judge.example/score", {"model": "m"})
    assert seen["auth"] is None

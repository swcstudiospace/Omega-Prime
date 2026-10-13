# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Phase 84: rubric schema validation, deterministic scoring, runner flags."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.evals import rubric
from omega_prime.evals.rubric import (
    EmptyCriteriaError,
    InvalidWeightsError,
    MissingCaseIdError,
    UnknownPredicateError,
    load_rubric_cases,
    run_rubric_case,
    score_outcome,
)
from omega_prime.evals.runner import main, run_suite

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"
RUBRIC_FILE = CASES / "rubric_cases.json"


def _case(**overlay):
    base = {
        "case_id": "demo",
        "criteria": [
            {
                "id": "says-hi",
                "weight": 0.5,
                "check": {"type": "final_contains", "text": "hi"},
            },
            {
                "id": "quiet-tool",
                "weight": 0.5,
                "check": {"type": "tool_not_called", "name": "nope"},
            },
        ],
    }
    base.update(overlay)
    return base


def test_valid_case_loads_and_scores():
    outcome = {
        "result": {"final_response": "hi there"},
        "calls": [],
        "payload": {},
    }
    record = score_outcome(_case(), outcome)
    assert record["case_id"] == "demo"
    assert record["score"] == pytest.approx(1.0)
    assert [c["score"] for c in record["criteria"]] == [1.0, 1.0]


def test_partial_score_weights_criteria():
    outcome = {
        "result": {"final_response": "bye"},
        "calls": [],
        "payload": {},
    }
    record = score_outcome(_case(), outcome)
    assert record["score"] == pytest.approx(0.5)


def test_unknown_predicate_raises_named_error():
    bad = _case(
        criteria=[{"id": "x", "weight": 1.0, "check": {"type": "vibes", "level": 11}}]
    )
    with pytest.raises(UnknownPredicateError):
        score_outcome(bad, {"result": {}, "calls": [], "payload": {}})


def test_weights_not_summing_to_one_raise_named_error():
    bad = _case(
        criteria=[
            {
                "id": "a",
                "weight": 0.5,
                "check": {"type": "tool_not_called", "name": "x"},
            },
            {
                "id": "b",
                "weight": 0.4,
                "check": {"type": "tool_not_called", "name": "y"},
            },
        ]
    )
    with pytest.raises(InvalidWeightsError):
        score_outcome(bad, {"result": {}, "calls": [], "payload": {}})


def test_missing_case_id_raises_named_error():
    bad = _case()
    del bad["case_id"]
    with pytest.raises(MissingCaseIdError):
        score_outcome(bad, {"result": {}, "calls": [], "payload": {}})


def test_empty_criteria_raises_named_error():
    with pytest.raises(EmptyCriteriaError):
        score_outcome(_case(criteria=[]), {"result": {}, "calls": [], "payload": {}})


def test_shipped_rubric_cases_validate():
    cases = load_rubric_cases(RUBRIC_FILE)
    assert len(cases) >= 1
    redteam_ids = [
        case["id"]
        for case in json.loads((CASES / "redteam.json").read_text(encoding="utf-8"))
    ]
    for rid in redteam_ids:
        stem = rid[len("redteam-") :] if rid.startswith("redteam-") else rid
        assert any(
            rid
            in " ".join(
                str(rc.get(key, ""))
                for key in ("case_id", "covers", "source", "redteam")
            )
            or stem
            in " ".join(
                str(rc.get(key, ""))
                for key in ("case_id", "covers", "source", "redteam")
            )
            for rc in cases
        ), f"rubric cases miss redteam class {rid}"


def test_runner_rubric_emits_scores(tmp_path: Path):
    out = tmp_path / "rubric.json"
    assert main([str(CASES), "--rubric", str(RUBRIC_FILE), "--output", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert all("score" in record for record in report["rubric_cases"])
    assert all("min_score" in record for record in report["rubric_cases"])


def test_runner_without_rubric_keeps_deterministic_shape(tmp_path: Path):
    report = run_suite(CASES, tmp_path)
    assert set(report.keys()) == {"passed", "failed", "results"}
    assert report["failed"] == 0
    assert main([str(CASES)]) == 0


def test_rubric_min_score_fails_invocation(tmp_path: Path):
    impossible = _case(min_score=1.0)
    impossible["criteria"] = [
        {
            "id": "never",
            "weight": 1.0,
            "check": {"type": "final_contains", "text": "zzz-no-such-text"},
        }
    ]
    record = run_rubric_case(impossible, tmp_path / "impossible")
    assert record["score"] < record["min_score"]
    assert record["passed"] is False


def test_final_omits_alias_and_tool_rows(tmp_path: Path):
    case = _case(
        criteria=[
            {"id": "a", "weight": 0.5, "check": {"type": "final_omits", "text": "x"}},
            {"id": "b", "weight": 0.5, "check": {"type": "tool_rows", "count": 0}},
        ]
    )
    outcome = {
        "result": {"final_response": "ok", "messages": []},
        "calls": [],
        "payload": {},
    }
    assert score_outcome(case, outcome)["score"] == pytest.approx(1.0)
    assert (
        rubric.evaluate_predicate({"type": "final_omits", "text": "x"}, outcome) is True
    )


@pytest.mark.parametrize(
    "weight", [-1.0, -0.5, float("nan"), float("inf"), float("-inf")]
)
def test_non_finite_or_negative_weight_raises_named_error(weight):
    bad = _case(
        criteria=[
            {
                "id": "x",
                "weight": weight,
                "check": {"type": "final_contains", "text": "hi"},
            }
        ]
    )
    with pytest.raises(InvalidWeightsError):
        score_outcome(bad, {"result": {}, "calls": [], "payload": {}})


def test_negative_weight_summing_to_one_still_raises():
    bad = _case(
        criteria=[
            {
                "id": "a",
                "weight": 2.0,
                "check": {"type": "final_contains", "text": "hi"},
            },
            {
                "id": "b",
                "weight": -1.0,
                "check": {"type": "tool_not_called", "name": "x"},
            },
        ]
    )
    with pytest.raises(InvalidWeightsError):
        score_outcome(bad, {"result": {}, "calls": [], "payload": {}})


def test_zero_weights_remain_valid():
    case = _case(
        criteria=[
            {
                "id": "a",
                "weight": 0.0,
                "check": {"type": "final_contains", "text": "nope"},
            },
            {
                "id": "b",
                "weight": 1.0,
                "check": {"type": "tool_not_called", "name": "x"},
            },
        ]
    )
    record = score_outcome(case, {"result": {}, "calls": [], "payload": {}})
    assert record["score"] == pytest.approx(1.0)


def _judge_rubric_file(tmp_path: Path, min_score: float = 1.0) -> Path:
    path = tmp_path / "judge_rubric.json"
    path.write_text(
        json.dumps(
            [
                {
                    "case_id": "judge-cli",
                    "user": "hi",
                    "script": [{"role": "assistant", "content": "hello"}],
                    "min_score": min_score,
                    "criteria": [
                        {"id": "tone", "weight": 1.0, "check": {"type": "judge"}}
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_cli_passes_enabled_judge_config_to_suite(tmp_path: Path, monkeypatch):
    """An enabled judge config reaches the rubric suite through the CLI."""
    import omega_prime.config
    from omega_prime.evals import judge as _judge

    monkeypatch.setattr(
        omega_prime.config,
        "load_config",
        lambda *args, **kwargs: {
            "evals": {"judge": {"enabled": True, "model": "cli-model"}}
        },
    )
    monkeypatch.setenv("OMEGA_PRIME_EVALS_JUDGE_API_KEY", "cli-secret")
    monkeypatch.setenv(
        "OMEGA_PRIME_EVALS_JUDGE_ENDPOINT", "https://judge.example/score"
    )

    def _stub(url, payload, timeout=30, api_key=None):
        assert api_key == "cli-secret"
        return {"score": 1.0, "rationale": "cli judged"}

    monkeypatch.setattr(_judge, "_post_json", _stub)
    out = tmp_path / "report.json"
    assert (
        main(
            [
                str(CASES),
                "--rubric",
                str(_judge_rubric_file(tmp_path)),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    (record,) = report["rubric_cases"]
    assert record["criteria"][0]["judge"] == "scored"
    assert record["criteria"][0]["model"] == "cli-model"
    assert record["score"] == pytest.approx(1.0)


def test_cli_judge_stays_off_when_unset(tmp_path: Path, monkeypatch, capsys):
    """Default-off config means the CLI never attempts judge network."""
    import omega_prime.config
    from omega_prime.evals import judge as _judge

    monkeypatch.setattr(omega_prime.config, "load_config", lambda *args, **kwargs: {})
    monkeypatch.delenv("OMEGA_PRIME_EVALS_JUDGE_API_KEY", raising=False)

    def _no_network(url, payload, timeout=30, api_key=None):
        raise AssertionError("judge network attempted with default-off config")

    monkeypatch.setattr(_judge, "_post_json", _no_network)
    out = tmp_path / "report.json"
    assert (
        main(
            [
                str(CASES),
                "--rubric",
                str(_judge_rubric_file(tmp_path, min_score=0.0)),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    (record,) = report["rubric_cases"]
    assert record["criteria"][0]["judge"] == "skipped"
    assert "judge_unavailable" not in record["criteria"][0]
    capsys.readouterr()

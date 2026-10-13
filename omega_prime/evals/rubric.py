# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Scored rubric cases beside the deterministic eval runner (Phase 84).

A rubric case is data: ``{case_id, <replay>, criteria, min_score}`` where the
replay fields are the deterministic case surface from ``runner.py``
(``registry``/``tool``/``policy``/``arguments``/``returns`` for registry
cases, ``user``/``script``/``tools`` for loop cases) plus coverage metadata
(``covers``/``source``/``redteam`` naming the redteam scenario class).
``criteria`` is a non-empty list of ``{id, weight, check}`` where ``check``
is ``{"type": <predicate>, ...params}``. Total case score in [0,1] is the
weighted sum of criterion scores.

Deterministic predicates evaluate the replay outcome (no model, no
network). ``judge``-kind criteria score via the judge module interface
(imported lazily so this module alone never imports network-capable
modules); when the judge is disabled they record ``judge: skipped`` and
contribute 0 with ``judge_unavailable`` unset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TOLERANCE = 1e-9


class RubricError(ValueError):
    """Base error for rubric case validation failures."""


class MissingCaseIdError(RubricError):
    """A rubric case has a missing or empty ``case_id``."""


class EmptyCriteriaError(RubricError):
    """A rubric case has a missing or empty ``criteria`` list."""


class UnknownPredicateError(RubricError):
    """A criterion names a predicate id outside the known set."""


class InvalidWeightsError(RubricError):
    """Criterion weights do not sum to 1 (tolerance 1e-9)."""


#: Deterministic predicates over the replay outcome. ``judge`` is the only
#: live-model predicate and is scored through the judge module interface.
DETERMINISTIC_PREDICATES = frozenset(
    {
        "final_contains",
        "final_not_contains",
        "final_omits",
        "tool_called",
        "tool_not_called",
        "refusal_contains",
        "payload_contains",
        "tool_rows",
        "exit_reason",
    }
)

KNOWN_PREDICATES = DETERMINISTIC_PREDICATES | {"judge"}


def _check_type(criterion: dict) -> str:
    check = criterion.get("check")
    if isinstance(check, str):
        return check
    if isinstance(check, dict) and isinstance(check.get("type"), str):
        return check["type"]
    raise UnknownPredicateError(
        f"criterion {criterion.get('id', '<missing-id>')!r}: "
        "check must be a predicate id string or an object with a string 'type'"
    )


def validate_case(case: Any) -> dict:
    """Validate one rubric case, returning it unchanged. Raises named errors."""
    if not isinstance(case, dict):
        raise MissingCaseIdError("rubric case must be an object")
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise MissingCaseIdError("rubric case needs a non-empty string case_id")
    criteria = case.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise EmptyCriteriaError(
            f"rubric case {case_id!r}: criteria must be a non-empty list"
        )
    total = 0.0
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise RubricError(
                f"rubric case {case_id!r}: each criterion must be an object"
            )
        kind = _check_type(criterion)
        if kind not in KNOWN_PREDICATES:
            raise UnknownPredicateError(
                f"rubric case {case_id!r}: unknown predicate {kind!r}"
            )
        weight = criterion.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise InvalidWeightsError(
                f"rubric case {case_id!r}: criterion "
                f"{criterion.get('id', '<missing-id>')!r} needs a numeric weight"
            )
        total += float(weight)
    if abs(total - 1.0) > _TOLERANCE:
        raise InvalidWeightsError(
            f"rubric case {case_id!r}: weights sum to {total!r}, expected 1"
        )
    return case


def load_rubric_cases(path: str | Path) -> list[dict]:
    """Load and validate rubric cases from a JSON file.

    Accepts a bare list (the ``redteam.json`` convention) or an object with
    a ``cases`` / ``rubric_cases`` list.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = raw.get("cases", raw.get("rubric_cases")) if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        raise RubricError(
            f"{path}: rubric file must be a list or hold cases/rubric_cases"
        )
    return [validate_case(case) for case in cases]


def evaluate_predicate(check: str | dict, outcome: dict) -> bool:
    """Evaluate one deterministic predicate against a replay outcome."""
    params = {"type": check} if isinstance(check, str) else dict(check)
    kind = params.get("type")
    if kind not in DETERMINISTIC_PREDICATES:
        raise UnknownPredicateError(f"unknown predicate {kind!r}")
    result = outcome.get("result", {}) if isinstance(outcome, dict) else {}
    messages = result.get("messages", []) if isinstance(result, dict) else []
    final = result.get("final_response", "") if isinstance(result, dict) else ""
    calls = outcome.get("calls", []) if isinstance(outcome, dict) else []
    payload = outcome.get("payload", {}) if isinstance(outcome, dict) else {}

    if kind == "final_contains":
        return bool(params.get("text", "") in str(final))
    if kind in ("final_not_contains", "final_omits"):
        return bool(params.get("text", "") not in str(final))
    if kind == "tool_called":
        return bool(params.get("name") in list(calls))
    if kind == "tool_not_called":
        return bool(params.get("name") not in list(calls))
    if kind == "refusal_contains":
        wanted = str(params.get("text", ""))
        for row in messages:
            if (
                isinstance(row, dict)
                and row.get("role") == "tool"
                and wanted in str(row.get("content", ""))
            ):
                return True
        return bool(wanted in str(payload.get("error", "")))
    if kind == "payload_contains":
        blob = json.dumps(payload, ensure_ascii=False)
        return bool(str(params.get("text", "")) in blob)
    if kind == "tool_rows":
        count = sum(
            1 for row in messages if isinstance(row, dict) and row.get("role") == "tool"
        )
        return bool(count == params.get("count"))
    if kind == "exit_reason":
        reason = result.get("turn_exit_reason") if isinstance(result, dict) else None
        return bool(reason == params.get("reason"))
    raise UnknownPredicateError(f"unknown predicate {kind!r}")


def _transcript_text(outcome: dict) -> str:
    result = outcome.get("result", {}) if isinstance(outcome, dict) else {}
    final = result.get("final_response", "") if isinstance(result, dict) else ""
    if isinstance(final, str) and final:
        return final
    return json.dumps(outcome, ensure_ascii=False, default=str)


def score_outcome(case: dict, outcome: dict, config: dict | None = None) -> dict:
    """Score a validated rubric case against a replay outcome.

    Returns ``{case_id, score, criteria, min_score}``. ``judge``-kind
    criteria delegate to the judge module (lazy import); a disabled judge
    records ``judge: skipped`` and contributes 0.
    """
    validate_case(case)
    scored: list[dict] = []
    total = 0.0
    for criterion in case["criteria"]:
        kind = _check_type(criterion)
        weight = float(criterion["weight"])
        if kind == "judge":
            from omega_prime.evals import judge as _judge

            record = _judge.score_criterion(
                criterion, _transcript_text(outcome), config
            )
            record = dict(record)
            record.setdefault("id", criterion.get("id"))
            record.setdefault("weight", weight)
            record.setdefault("check", criterion.get("check"))
            total += weight * float(record.get("score", 0.0))
            scored.append(record)
            continue
        passed = evaluate_predicate(criterion.get("check"), outcome)
        total += weight if passed else 0.0
        scored.append(
            {
                "id": criterion.get("id"),
                "weight": weight,
                "score": 1.0 if passed else 0.0,
                "passed": passed,
                "check": criterion.get("check"),
            }
        )
    return {
        "case_id": case["case_id"],
        "score": total,
        "criteria": scored,
        "min_score": float(case.get("min_score", 1.0)),
    }


def _deterministic_case(case: dict) -> dict:
    """Project a rubric case onto the deterministic replay surface."""
    nested = case.get("case")
    if isinstance(nested, dict):
        det = dict(nested)
        det.setdefault("id", case["case_id"])
        return det
    det = {"id": case["case_id"]}
    for key in (
        "registry",
        "family",
        "tool",
        "policy",
        "arguments",
        "returns",
        "requires_approval",
        "approve",
        "approved_by",
        "user",
        "script",
        "tools",
        "turns",
    ):
        if key in case:
            det[key] = case[key]
    if isinstance(det.get("turns"), dict):
        det.update(det.pop("turns"))
    return det


def run_rubric_case(
    case: dict, workdir: str | Path, config: dict | None = None
) -> dict:
    """Replay one rubric case's deterministic turns, then score it."""
    from omega_prime.evals import runner as _runner

    validate_case(case)
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    det = _deterministic_case(case)
    try:
        if det.get("registry"):
            outcome = _runner._run_registry_case(det, work)
        elif isinstance(det.get("user"), str) and isinstance(det.get("script"), list):
            outcome = _runner._run_loop_case(det, work)
        else:
            outcome = {"result": {"final_response": ""}, "calls": [], "payload": {}}
    except Exception as exc:
        outcome = {
            "result": {"final_response": ""},
            "calls": [],
            "payload": {"error": f"case raised: {exc}"},
        }
    record = score_outcome(case, outcome, config)
    record["passed"] = bool(record["score"] >= record["min_score"])
    return record


def run_rubric_suite(
    cases: list[dict], workdir: str | Path, config: dict | None = None
) -> list[dict]:
    """Replay and score every rubric case under ``workdir``."""
    work = Path(workdir)
    records: list[dict] = []
    for index, case in enumerate(cases):
        records.append(run_rubric_case(case, work / f"rubric-{index}", config=config))
    return records

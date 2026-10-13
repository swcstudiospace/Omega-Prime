# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Optional live-model judge for rubric criteria (Phase 84).

Strictly flag-gated: this module's top level imports stdlib-only
(``json``/``os`` — never ``socket``, ``ssl``, ``http.client``,
``urllib.request``, or third-party clients). Every socket-capable import
happens INSIDE the enabled-only code path, after the flag + model +
credential checks pass.

Rules (plan-06 rollout contract):

- Flag off (or model empty, or credential absent) => the criterion records
  ``judge: skipped``, contributes 0, and zero network is attempted — no key
  reads beyond a presence check, no client construction.
- Endpoint down => the criterion scores 0 with ``judge_unavailable: true``;
  the suite still completes (recorded, never fabricated, never blocks).
- Rationale + model id are stored per record; judge-scored criteria stay
  separable from deterministic ones via the ``judge`` field
  (``skipped`` / ``scored`` / ``unavailable``).

The endpoint credential comes from the environment/secret store only
(``OMEGA_PRIME_EVALS_JUDGE_API_KEY``) — never from the config file. The
endpoint URL comes from ``OMEGA_PRIME_EVALS_JUDGE_ENDPOINT``; empty means
the endpoint is down (``judge_unavailable``), not an error.
"""

from __future__ import annotations

import json
import os
from typing import Any

CREDENTIAL_ENV_VAR = "OMEGA_PRIME_EVALS_JUDGE_API_KEY"
ENDPOINT_ENV_VAR = "OMEGA_PRIME_EVALS_JUDGE_ENDPOINT"


def judge_enabled(config: dict | None) -> bool:
    """Whether the judge may start: enabled AND a model is configured."""
    if not isinstance(config, dict):
        return False
    node = config.get("evals", {})
    judge = node.get("judge", {}) if isinstance(node, dict) else {}
    if not isinstance(judge, dict):
        return False
    return bool(judge.get("enabled") is True and judge.get("model"))


def judge_model(config: dict | None) -> str:
    """Configured judge model id (empty when unconfigured)."""
    if not isinstance(config, dict):
        return ""
    node = config.get("evals", {})
    judge = node.get("judge", {}) if isinstance(node, dict) else {}
    model = judge.get("model", "") if isinstance(judge, dict) else ""
    return model if isinstance(model, str) else ""


def _credential_present(environ: Any) -> bool:
    env = environ if environ is not None else os.environ
    try:
        return bool(str(env.get(CREDENTIAL_ENV_VAR, "")).strip())
    except Exception:
        return False


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    """POST a JSON payload, return the decoded JSON response.

    Separated for stubbing in tests. Only ever called on the enabled path
    (socket-capable imports live here, nowhere else in this module).
    """
    import http.client
    import ssl
    import urllib.request

    _ = (http.client, ssl)  # named so the enabled path owns its imports
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def score_criterion(
    criterion: dict,
    transcript: str,
    config: dict | None,
    environ: Any | None = None,
) -> dict:
    """Score one ``judge``-kind criterion against the transcript.

    Judge-off (or model empty, or credential absent) returns a
    ``judge: skipped`` record scoring 0 with ``judge_unavailable`` unset
    and no network attempted. Endpoint failure returns score 0 with
    ``judge_unavailable: true``.
    """
    criterion_id = (
        criterion.get("id", "<missing-id>")
        if isinstance(criterion, dict)
        else "<bad-criterion>"
    )
    model = judge_model(config)
    if not judge_enabled(config) or not _credential_present(environ):
        return {
            "id": criterion_id,
            "score": 0.0,
            "passed": False,
            "check": criterion.get("check") if isinstance(criterion, dict) else None,
            "judge": "skipped",
            "model": model,
            "rationale": "judge disabled (flag off, model empty, or credential absent)",
        }
    env = environ if environ is not None else os.environ
    try:
        endpoint = str(env.get(ENDPOINT_ENV_VAR, "")).strip()
    except Exception:
        endpoint = ""
    if not endpoint:
        return {
            "id": criterion_id,
            "score": 0.0,
            "passed": False,
            "check": criterion.get("check") if isinstance(criterion, dict) else None,
            "judge": "unavailable",
            "judge_unavailable": True,
            "model": model,
            "rationale": "judge endpoint unconfigured",
        }
    prompt = ""
    check = criterion.get("check") if isinstance(criterion, dict) else None
    if isinstance(check, dict):
        prompt = str(check.get("prompt", check.get("rubric", "")))
    try:
        import socket

        _ = socket.getdefaulttimeout()
        answer = _post_json(
            endpoint,
            {
                "model": model,
                "criterion": criterion_id,
                "prompt": prompt,
                "transcript": transcript,
            },
        )
    except Exception as exc:
        return {
            "id": criterion_id,
            "score": 0.0,
            "passed": False,
            "check": check,
            "judge": "unavailable",
            "judge_unavailable": True,
            "model": model,
            "rationale": f"judge endpoint error: {exc}",
        }
    try:
        score = float(answer.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = min(1.0, max(0.0, score))
    rationale = answer.get("rationale", "")
    return {
        "id": criterion_id,
        "score": score,
        "passed": bool(score >= 0.5),
        "check": check,
        "judge": "scored",
        "model": model,
        "rationale": rationale if isinstance(rationale, str) else str(rationale),
    }

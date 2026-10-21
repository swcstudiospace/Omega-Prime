# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Choose an omp provider profile from usage and the compliance allowlist.

The default allowlist is empty for subscription-via-omp. Anthropic and Muse
subscription OAuth are never allowlisted. API-key profiles are allowlisted
when their environment variable is set. Opt-in flags are ``OMEGA_PRIME_OMP_ALLOW_*``.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from omega_prime.engine.jobs import child_env, flag

# Default model ids. Operators override them with OMEGA_PRIME_OMP_MODEL_<PLAN>.
_DEFAULT_MODELS = {
    "zai": "glm-5.3",
    "xai": "grok-4",
    "anthropic": "claude-sonnet-4-5",
    "muse": "muse-spark-1.2",
    "cursor": "auto",
    "chatgpt": "gpt-6.1-sol",
}

_PROVIDERS = {
    "zai": "zai",
    "xai": "xai",
    "anthropic": "anthropic",
    "muse": "muse-code",
    "cursor": "cursor",
    "chatgpt": "openai-codex",
}

# Stage defaults. Planning runs on the stronger model, execution on the
# faster one: ChatGPT plans on Sol and builds on Luna; Z.ai plans on GLM-5.3
# and builds on GLM-5.3-flash. Operators override them with
# OMEGA_PRIME_OMP_MODEL_<PLAN>_<STAGE>.
STAGES = ("plan", "execute")
_STAGE_MODELS: dict[str, dict[str, str]] = {
    "chatgpt": {"plan": "gpt-6.1-sol", "execute": "gpt-6-luna"},
    "zai": {"plan": "glm-5.3", "execute": "glm-5.3-flash"},
}
# Stage routing tries these plans in order. Z.ai GLM covers ChatGPT when the
# ChatGPT plan is gated off or its quota window is exhausted.
_STAGE_PREFERENCE = ("chatgpt", "zai")


class PlanNotAllowed(Exception):
    """The requested plan, or every plan, is outside the allowlist."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Decision:
    plan: str
    provider: str
    model_id: str
    allowlisted: bool
    auth: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class Quota:
    remaining: float | None
    used: float | None
    percent: float | None
    window: str | None
    resets_at: str | None
    confidence: str


@dataclass(frozen=True)
class Choice:
    decision: Decision
    quota: Quota | None
    stage: str | None = None
    # Plan the stage asked for first, when a fallback plan was used instead.
    fallback_from: str | None = None
    fallback_reason: str | None = None


def _present(env: Mapping[str, str], name: str) -> bool:
    return bool(str(env.get(name, "")).strip())


def _check_stage(stage: str | None) -> None:
    if stage is not None and stage not in STAGES:
        raise PlanNotAllowed(f"unknown stage {stage!r}; expected one of {STAGES}")


def model_id(plan: str, env: Mapping[str, str], stage: str | None = None) -> str:
    """Model for ``plan``, optionally for one stage.

    Order: ``OMEGA_PRIME_OMP_MODEL_<PLAN>_<STAGE>``, then
    ``OMEGA_PRIME_OMP_MODEL_<PLAN>``, then the stage default, then the plan
    default.
    """
    _check_stage(stage)
    if stage is not None:
        name = f"OMEGA_PRIME_OMP_MODEL_{plan.upper()}_{stage.upper()}"
        staged = str(env.get(name, "")).strip()
        if staged:
            return staged
    override = str(env.get(f"OMEGA_PRIME_OMP_MODEL_{plan.upper()}", "")).strip()
    if override:
        return override
    if stage is not None and stage in _STAGE_MODELS.get(plan, {}):
        return _STAGE_MODELS[plan][stage]
    return _DEFAULT_MODELS[plan]


def evaluate_all(
    env: Mapping[str, str], stage: str | None = None
) -> tuple[Decision, ...]:
    _check_stage(stage)
    return tuple(_evaluate(plan, env, stage) for plan in _DEFAULT_MODELS)


def _evaluate(plan: str, env: Mapping[str, str], stage: str | None = None) -> Decision:
    provider = _PROVIDERS[plan]
    model = model_id(plan, env, stage)
    if plan == "zai":
        allowed = flag(env, "OMEGA_PRIME_OMP_ALLOW_ZAI")
        return Decision(
            plan,
            provider,
            model,
            allowed,
            "subscription_opt_in" if allowed else "none",
            "ambiguous",
            (
                "Z.ai via omp is on the allowlist because OMEGA_PRIME_OMP_ALLOW_ZAI=1."
                if allowed
                else (
                    "Z.ai via omp is ambiguous and off the default allowlist. "
                    "ZAI_API_KEY alone does not allow it. Set "
                    "OMEGA_PRIME_OMP_ALLOW_ZAI=1 to opt in."
                )
            ),
        )
    if plan == "xai":
        if _present(env, "XAI_API_KEY"):
            return Decision(
                plan,
                provider,
                model,
                True,
                "api_key",
                "allowed",
                "xAI API-key profile is allowlisted because XAI_API_KEY is set.",
            )
        if flag(env, "OMEGA_PRIME_OMP_ALLOW_XAI"):
            return Decision(
                plan,
                provider,
                model,
                True,
                "subscription_opt_in",
                "ambiguous",
                "xAI subscription via omp is opt-in (OMEGA_PRIME_OMP_ALLOW_XAI=1).",
            )
        return Decision(
            plan,
            provider,
            model,
            False,
            "none",
            "ambiguous",
            "xAI via omp is off the allowlist. Set XAI_API_KEY or "
            "OMEGA_PRIME_OMP_ALLOW_XAI=1.",
        )
    if plan == "anthropic":
        if _present(env, "ANTHROPIC_API_KEY"):
            return Decision(
                plan,
                provider,
                model,
                True,
                "api_key",
                "allowed",
                "Anthropic API-key profile is allowlisted. Subscription OAuth "
                "is not used.",
            )
        return Decision(
            plan,
            provider,
            model,
            False,
            "none",
            "not_allowed",
            "Anthropic subscription OAuth is not allowed. Set ANTHROPIC_API_KEY "
            "for the API-key profile. OMEGA_PRIME_OMP_ALLOW_ANTHROPIC does not "
            "enable subscription OAuth.",
        )
    if plan == "muse":
        if _present(env, "META_API_KEY") or _present(env, "MUSE_API_KEY"):
            return Decision(
                plan,
                provider,
                model,
                True,
                "api_key",
                "allowed",
                "Muse API-key profile is allowlisted. Subscription OAuth is not used.",
            )
        return Decision(
            plan,
            provider,
            model,
            False,
            "none",
            "not_allowed",
            "Muse subscription via omp is not allowed. Set META_API_KEY or "
            "MUSE_API_KEY. OMEGA_PRIME_OMP_ALLOW_MUSE does not enable it.",
        )
    if plan == "chatgpt":
        return _chatgpt(plan, provider, model, env)
    if _present(env, "CURSOR_API_KEY"):
        return Decision(
            plan,
            provider,
            model,
            True,
            "api_key",
            "allowed",
            "Cursor API-key profile is allowlisted because CURSOR_API_KEY is set.",
        )
    if flag(env, "OMEGA_PRIME_OMP_ALLOW_CURSOR"):
        return Decision(
            plan,
            provider,
            model,
            True,
            "subscription_opt_in",
            "ambiguous",
            "Cursor subscription via omp is opt-in (OMEGA_PRIME_OMP_ALLOW_CURSOR=1).",
        )
    return Decision(
        plan,
        provider,
        model,
        False,
        "none",
        "ambiguous",
        "Cursor via omp is off the allowlist. Set CURSOR_API_KEY or "
        "OMEGA_PRIME_OMP_ALLOW_CURSOR=1.",
    )


def _chatgpt(plan: str, provider: str, model: str, env: Mapping[str, str]) -> Decision:
    """ChatGPT plan through omp's ``openai-codex`` login.

    Only the opt-in flag allows it. ``OPENAI_API_KEY`` belongs to the separate
    ``openai`` developer API route, and ``OPENAI_CODEX_OAUTH_TOKEN`` alone is
    a credential, not consent, so neither allows this plan.
    """
    if flag(env, "OMEGA_PRIME_OMP_ALLOW_CHATGPT"):
        return Decision(
            plan,
            provider,
            model,
            True,
            "subscription_opt_in",
            "ambiguous",
            "ChatGPT plan via omp (openai-codex) is opt-in "
            "(OMEGA_PRIME_OMP_ALLOW_CHATGPT=1).",
        )
    return Decision(
        plan,
        provider,
        model,
        False,
        "none",
        "ambiguous",
        "ChatGPT plan via omp (openai-codex) is off the allowlist. "
        "OPENAI_API_KEY and OPENAI_CODEX_OAUTH_TOKEN do not allow it. Set "
        "OMEGA_PRIME_OMP_ALLOW_CHATGPT=1 to opt in.",
    )


def parse_usage(payload: Any) -> dict[str, Quota]:
    """Normalise ``omp usage --json`` into one quota per plan name."""
    found: dict[str, Quota] = {}
    rows = _rows(payload)
    for row in rows:
        plan = _plan_name(row)
        if plan is None or plan not in _DEFAULT_MODELS:
            continue
        found[plan] = _quota(row)
    return found


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    providers = payload.get("providers")
    if isinstance(providers, list):
        return [item for item in providers if isinstance(item, dict)]
    if isinstance(providers, dict):
        rows: list[dict[str, Any]] = []
        for name, body in providers.items():
            if isinstance(body, dict):
                row = dict(body)
                row.setdefault("provider", name)
                rows.append(row)
        return rows
    rows = []
    for name, body in payload.items():
        if isinstance(body, dict) and name in _DEFAULT_MODELS:
            row = dict(body)
            row.setdefault("provider", name)
            rows.append(row)
    return rows


def _plan_name(row: dict[str, Any]) -> str | None:
    raw = row.get("plan") or row.get("provider") or row.get("id") or row.get("name")
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    if text in {"muse-code", "muse_code"}:
        return "muse"
    if text in {"openai-codex", "openai_codex", "codex"}:
        return "chatgpt"
    if text in _DEFAULT_MODELS:
        return text
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _quota(row: dict[str, Any]) -> Quota:
    window = row.get("windows")
    source = row
    if isinstance(window, list) and window and isinstance(window[0], dict):
        source = window[0]
    remaining = _number(
        source.get("remaining")
        if "remaining" in source
        else source.get("remainingCredits")
    )
    used = _number(
        source.get("used") if "used" in source else source.get("usedCredits")
    )
    percent = _number(
        source.get("percent")
        if "percent" in source
        else source.get("percentUsed")
        if "percentUsed" in source
        else source.get("percentage")
    )
    label = source.get("window") or source.get("type") or row.get("window")
    reset = (
        source.get("resets_at") or source.get("resetsAt") or source.get("nextResetTime")
    )
    live = remaining is not None or used is not None or percent is not None
    return Quota(
        remaining=remaining,
        used=used,
        percent=percent,
        window=label if isinstance(label, str) else None,
        resets_at=reset if isinstance(reset, str) else None,
        confidence="live" if live else "unknown",
    )


def _exhausted(quota: Quota | None) -> bool:
    if quota is None:
        return False
    if quota.remaining is not None:
        return quota.remaining <= 0
    return quota.percent is not None and quota.percent >= 100


def select(
    env: Mapping[str, str],
    usage: Mapping[str, Quota] | None = None,
    *,
    requested: str | None = None,
    stage: str | None = None,
    fallback: tuple[str, ...] | None = None,
) -> Choice:
    """Pick one allowlisted plan.

    Without ``stage``: a requested plan is used only when it is allowlisted.
    Otherwise the allowlisted plan with remaining quota wins. An exhausted
    window loses to an unknown window. API-key profiles win ties.

    With ``stage`` (``plan`` or ``execute``): the stage model of each plan is
    used, and plans are tried in order, starting with ``requested`` (default
    ChatGPT), then ``fallback`` (default Z.ai). A plan is skipped when it is
    off the allowlist or its quota window is exhausted. The choice records
    which plan it fell back from.
    """
    quotas = dict(usage or {})
    decisions = evaluate_all(env, stage)
    if stage is not None:
        return _select_stage(decisions, quotas, stage, requested, fallback)
    if requested is not None:
        name = requested.strip().lower()
        match = next((item for item in decisions if item.plan == name), None)
        if match is None:
            raise PlanNotAllowed(f"unknown plan {requested!r}")
        if not match.allowlisted:
            raise PlanNotAllowed(match.reason)
        return Choice(match, quotas.get(match.plan))
    allowed = [item for item in decisions if item.allowlisted]
    if not allowed:
        raise PlanNotAllowed(
            "no coding plan is on the allowlist. The default allowlist is empty. "
            "Set an API key (ANTHROPIC_API_KEY, XAI_API_KEY, META_API_KEY, "
            "MUSE_API_KEY, CURSOR_API_KEY) or an opt-in flag "
            "(OMEGA_PRIME_OMP_ALLOW_ZAI, OMEGA_PRIME_OMP_ALLOW_XAI, "
            "OMEGA_PRIME_OMP_ALLOW_CURSOR, OMEGA_PRIME_OMP_ALLOW_CHATGPT). Anthropic and Muse subscription "
            "OAuth stay off."
        )

    def rank(item: Decision) -> tuple[int, float, int]:
        quota = quotas.get(item.plan)
        auth = 1 if item.auth == "api_key" else 0
        if quota is None:
            return (1, 0.0, auth)
        if quota.remaining is not None:
            if quota.remaining > 0:
                return (2, float(quota.remaining), auth)
            return (0, 0.0, auth)
        if quota.percent is not None:
            if quota.percent >= 100:
                return (0, 0.0, auth)
            return (2, 100.0 - float(quota.percent), auth)
        return (1, 0.0, auth)

    chosen = max(allowed, key=rank)
    return Choice(chosen, quotas.get(chosen.plan))


def _select_stage(
    decisions: tuple[Decision, ...],
    quotas: Mapping[str, Quota],
    stage: str,
    requested: str | None,
    fallback: tuple[str, ...] | None,
) -> Choice:
    first = (requested or _STAGE_PREFERENCE[0]).strip().lower()
    rest = _STAGE_PREFERENCE[1:] if fallback is None else fallback
    order: list[str] = []
    for name in (first, *(item.strip().lower() for item in rest)):
        if name not in order:
            order.append(name)
    by_plan = {item.plan: item for item in decisions}
    skipped: list[str] = []
    for name in order:
        match = by_plan.get(name)
        if match is None:
            raise PlanNotAllowed(f"unknown plan {name!r}")
        if not match.allowlisted:
            skipped.append(f"{name}: off the allowlist")
            continue
        if _exhausted(quotas.get(name)):
            skipped.append(f"{name}: quota window exhausted")
            continue
        if name == first:
            return Choice(match, quotas.get(name), stage)
        return Choice(match, quotas.get(name), stage, first, "; ".join(skipped))
    head = by_plan[first]
    detail = (
        head.reason if not head.allowlisted else f"{first} quota window is exhausted"
    )
    raise PlanNotAllowed(
        f"no plan can run the {stage} stage ({'; '.join(skipped)}). {detail}"
    )


def read_usage(binary: str, env: Mapping[str, str]) -> dict[str, Quota]:
    """Run ``omp usage --json``. A failure yields an empty map, not an HTTP call."""
    try:
        proc = subprocess.run(
            [binary, "usage", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=child_env(env),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    return parse_usage(payload)

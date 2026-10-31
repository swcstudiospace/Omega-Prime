"""Pure merge-gate predicate (standing Omega Prime rules)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GateInput:
    greptile_score: int | None
    greptile_on_head: bool
    ci_green: bool
    mergeable: bool
    draft: bool
    auto_deploy: bool
    repo_allowed: bool
    # True while required checks are queued or running (not yet failed).
    ci_pending: bool = False


@dataclass(frozen=True, slots=True)
class GateResult:
    ok: bool
    reasons: tuple[str, ...]

    @property
    def should_merge(self) -> bool:
        return self.ok


def evaluate_merge_gate(inp: GateInput) -> GateResult:
    reasons: list[str] = []
    if not inp.repo_allowed:
        reasons.append("repo_not_allowlisted")
    if inp.draft:
        reasons.append("draft_held")
    if inp.auto_deploy:
        reasons.append("auto_deploy_held")
    if inp.greptile_score is None:
        reasons.append("greptile_score_missing")
    elif inp.greptile_score < 5:
        reasons.append(f"greptile_score_{inp.greptile_score}")
    if not inp.greptile_on_head:
        reasons.append("greptile_not_on_head")
    if not inp.ci_green:
        reasons.append("ci_pending" if inp.ci_pending else "ci_not_green")
    if not inp.mergeable:
        reasons.append("not_mergeable")
    return GateResult(ok=not reasons, reasons=tuple(reasons))

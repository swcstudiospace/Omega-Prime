from omega_prime.sdlc_events.gate import GateInput, evaluate_merge_gate


def test_gate_opens_only_when_all_green() -> None:
    ok = evaluate_merge_gate(
        GateInput(
            greptile_score=5,
            greptile_on_head=True,
            ci_green=True,
            mergeable=True,
            draft=False,
            auto_deploy=False,
            repo_allowed=True,
        )
    )
    assert ok.should_merge


def test_gate_holds_draft_and_auto_deploy() -> None:
    r = evaluate_merge_gate(
        GateInput(
            greptile_score=5,
            greptile_on_head=True,
            ci_green=True,
            mergeable=True,
            draft=True,
            auto_deploy=True,
            repo_allowed=True,
        )
    )
    assert not r.ok
    assert "draft_held" in r.reasons
    assert "auto_deploy_held" in r.reasons


def test_gate_requires_greptile_on_head() -> None:
    r = evaluate_merge_gate(
        GateInput(
            greptile_score=5,
            greptile_on_head=False,
            ci_green=True,
            mergeable=True,
            draft=False,
            auto_deploy=False,
            repo_allowed=True,
        )
    )
    assert "greptile_not_on_head" in r.reasons

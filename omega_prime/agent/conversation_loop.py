"""One conversation turn: interrupt, budget, one model call, tool round or text, finalize.

Adapted from Hermes ``agent/conversation_loop.py`` (``run_conversation`` and the
iteration in ``_run_conversation_turn``) and ``agent/turn_iteration_prep.py``.
The system prompt is installed once, before the loop, and is not rebuilt after
a tool round. Messages already appended are not rewritten here; compression is
the only sanctioned rewrite, and this loop does not call it.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass
from typing import Any

from omega_prime.agent.budget import IterationBudget
from omega_prime.agent.degraded import guarded_hook
from omega_prime.agent.harness import (
    account_output,
    apply_tool_marks,
    drain_steer,
    emit,
    prepare_speculative,
    restore_tools,
    run_before_model,
    take_leftover_steer,
    wrap_tools,
)
from omega_prime.agent.interrupt import InterruptFlag
from omega_prime.agent.magic_keywords import notices_for_turn
from omega_prime.agent.model import Model
from omega_prime.agent.modes import apply_plan_mode
from omega_prime.agent.prime_hooks import (
    accounting_usage,
    evaluate_autonomous,
    evaluate_goal,
    merge_usage,
    sum_usages,
)
from omega_prime.agent.prompt_builder import build_system_prompt, steer_user_row
from omega_prime.agent.session_lease import SessionLease
from omega_prime.agent.turn_final_response import (
    FinalResponseVerdict,
    finish_text_response,
)
from omega_prime.agent.turn_finalizer import finalize_turn
from omega_prime.agent.turn_tool_round import ToolRoundVerdict, run_tool_round


@dataclass
class Agent:
    """What the loop reads. Not ``AIAgent``: no credential, gateway, or callback parameters.

    ``tools`` is a name → callable map. ``interrupt`` is polled at the start of each
    iteration. ``pending_steer`` is delivered as its own user row after a tool result.
    ``lease`` is held for the turn. A budget the caller omits is per-turn and refilled
    from ``max_iterations``. A budget the caller passes is a session cap and is not reset.

    Omp harness options (all ``None`` by default, keeping the Hermes turn): ``before_model``
    may mutate the outgoing request or stop the turn before it is billed; ``pause_gate``
    parks the loop at both action boundaries; ``output_budget`` caps model-visible
    output chars; ``speculative_tools`` names discard-safe tools to pre-execute.

    Omp mode options: ``plan_mode`` refuses write tools without calling them;
    ``extensions`` is an :class:`ExtensionHooks` whose ``before_model`` hooks run
    before the legacy ``before_model`` hook each iteration.

    Durability options: ``journal`` is a :class:`TurnJournal` that persists
    every appended row; ``run_id`` names the run (a fresh hex id by default).

    Observability: ``tracer`` is a :class:`Tracer` recording one span per
    model call.

    Magic keywords: ``magic_keywords`` is a
    :class:`MagicKeywordSettings` (all on when omitted). A user turn
    containing ``ultrathink``, ``orchestrate``, or ``workflowz`` as
    standalone prose gains that word's notice rows for the turn.

    Substrate surface: ``substrate`` is a :class:`SubstrateSession` (or
    duck-typed equivalent). Each turn opens it once (brief cached after the
    first open), prepends its brief block to the system prompt, and emits
    the prompt plus a turn-end note. Tool calls are reported from the tool
    round. ``None`` keeps the turn fully local.

    Prime hooks: ``prime_hooks`` is a :class:`PrimeHooks` built from the
    registry's ``runtime_bindings`` (or ``None``). ``None`` keeps the exact
    pre-v10 turn: no implicit continuation, no usage accounting, no extra
    result keys.

    Engine: ``force_engine`` sends a scripted or fake model through the omp
    RPC driver when the binary and allowlist are ready. Auto mode otherwise
    leaves those models on the Python loop. ``engine_stage`` (``plan`` or
    ``execute``) picks that stage's model for engine turns; None defers to
    ``OMEGA_PRIME_ENGINE_STAGE``.
    """

    model: Model
    tools: dict[str, Any] | None = None
    max_iterations: int = 90
    budget: IterationBudget | None = None
    interrupt: InterruptFlag | None = None
    pending_steer: str | None = None
    lease: SessionLease | None = None
    before_model: Any | None = None
    pause_gate: Any | None = None
    output_budget: Any | None = None
    speculative_tools: Any | None = None
    plan_mode: bool = False
    extensions: Any | None = None
    journal: Any | None = None
    run_id: str | None = None
    tracer: Any | None = None
    magic_keywords: Any | None = None
    substrate: Any | None = None
    prime_hooks: Any | None = None
    force_engine: bool = False
    engine_stage: str | None = None
    delegate_depth: int = 0
    max_depth: int = 2
    max_children: int = 1
    child_model: Any | None = None
    child_tool_hook: Any | None = None
    _cached_system_prompt: str | None = None
    _turn_budget: IterationBudget | None = None

    def __post_init__(self) -> None:
        if self.tools is None:
            self.tools = {}
        # Only the budget this agent created is per-turn. A caller-supplied budget
        # stays a session cap so spending it still stops later turns.
        if self.budget is None:
            self.budget = IterationBudget(self.max_iterations)
            self._turn_budget = self.budget
        else:
            self._turn_budget = None
        if self.interrupt is None:
            self.interrupt = InterruptFlag()
        if self.lease is None:
            self.lease = SessionLease()


def run_conversation(
    agent: Agent,
    user_message: Any,
    system_message: str | None = None,
    conversation_history: list | None = None,
    task_id: str | None = None,
    *,
    wait: bool = False,
) -> dict:
    """Run one turn. Return ``final_response`` and ``messages``.

    The session lease is taken before the turn and released on the way out,
    including when the turn raises. ``wait`` forwards verbatim into
    ``lease.acquire``: the default fast-fails on a held lease exactly as
    before; heartbeat re-entry opts into atomic waiting.
    """
    lease = agent.lease
    if lease is None:
        lease = SessionLease()
        agent.lease = lease
    lease.acquire(wait=wait)
    try:
        from omega_prime.engine.attach import route_turn

        history = conversation_history if conversation_history is not None else None
        since = len(history) if history is not None else 0
        routed = None
        if not _engine_preempted(agent):
            routed = route_turn(
                agent,
                user_message,
                system_message=system_message,
                conversation_history=history,
                task_id=task_id,
                interrupt=agent.interrupt,
            )
        if routed is not None:
            return _seal_engine_turn(agent, routed, since)
        system_message = _open_substrate(agent, user_message, system_message)
        result = _run_conversation_turn(
            agent,
            user_message,
            system_message=system_message,
            conversation_history=conversation_history,
            task_id=task_id,
        )
        _close_substrate_turn(agent, result)
        return result
    finally:
        lease.release()


def _engine_preempted(agent: Agent) -> bool:
    """True when a shared stop already applies, so omp must not start."""
    interrupt = agent.interrupt
    if interrupt is not None and interrupt.is_set():
        return True
    budget = agent.budget
    if (
        budget is not None
        and budget is not agent._turn_budget
        and budget.remaining <= 0
    ):
        return True
    pause = agent.pause_gate
    return bool(pause is not None and not pause.wait_until_resumed(interrupt))


def _token_usage(raw: Any) -> dict[str, int] | None:
    """Map an omp usage object onto the loop's token counters."""
    if not isinstance(raw, dict):
        return None
    known = {}
    for key in ("total_tokens", "prompt_tokens", "completion_tokens"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            known[key] = value
    if known:
        return known
    prompt = raw.get("input")
    if prompt is None:
        prompt = raw.get("input_tokens")
    completion = raw.get("output")
    if completion is None:
        completion = raw.get("output_tokens")
    mapped: dict[str, int] = {}
    if isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0:
        mapped["prompt_tokens"] = prompt
    if (
        isinstance(completion, int)
        and not isinstance(completion, bool)
        and completion >= 0
    ):
        mapped["completion_tokens"] = completion
    return mapped or None


def _seal_engine_turn(agent: Agent, routed: dict, since: int) -> dict:
    """Journal the engine turn and charge goal and autonomous accounting."""
    if routed.get("interrupted"):
        interrupt = agent.interrupt
        if interrupt is not None:
            routed["interrupt_message"] = getattr(interrupt, "message", None)
            interrupt.clear()
    messages = routed.get("messages")
    rows = messages if isinstance(messages, list) else []
    run_id = _begin_journal(agent)
    _journal_new_rows(agent, run_id, rows, since)
    hooks = getattr(agent, "prime_hooks", None)
    if getattr(hooks, "present", True) is False:
        hooks = None
    if hooks is not None:
        engine = routed.get("engine")
        raw = engine.get("usage") if isinstance(engine, dict) else None
        charged = accounting_usage([_token_usage(raw)])
        goal = evaluate_goal(hooks, agent, charged, turn_id=run_id)
        report = goal.get("report") if isinstance(goal, dict) else None
        if report is not None:
            routed["goal_completion"] = report
        auto = evaluate_autonomous(hooks, agent, routed, turn_id=run_id)
        verdict = auto.get("verdict") if isinstance(auto, dict) else None
        if isinstance(verdict, dict) and verdict.get("action") == "stop":
            routed["autonomous_stop"] = verdict
        if charged is not None:
            routed["usage"] = charged
    budget = agent.budget
    calls = routed.get("api_calls")
    engine = routed.get("engine")
    started = bool(engine.get("started")) if isinstance(engine, dict) else False
    if budget is not None and ((isinstance(calls, int) and calls > 0) or started):
        budget.consume()
    emit(agent, "turn_end", turn_exit_reason=routed.get("turn_exit_reason"))
    _finish_journal(agent, run_id, routed)
    return routed


def _run_conversation_turn(
    agent: Agent,
    user_message: Any,
    *,
    system_message: str | None,
    conversation_history: list | None,
    task_id: str | None,
) -> dict:
    messages: list = conversation_history if conversation_history is not None else []
    emit(agent, "turn_start")
    run_id = _begin_journal(agent)
    since = len(messages)
    _install_system_prompt(agent, messages, system_message)
    messages.append({"role": "user", "content": user_message})
    if isinstance(user_message, str):
        messages.extend(
            notices_for_turn(user_message, agent.tools, agent.magic_keywords)
        )
    _emit_new_rows(agent, messages, since)
    _journal_new_rows(agent, run_id, messages, since)

    api_call_count = 0
    final_response: str | None = None
    interrupted = False
    failed = False
    turn_exit_reason: str | None = None
    budget = agent.budget
    if budget is None:
        budget = IterationBudget(agent.max_iterations)
        agent.budget = budget
        agent._turn_budget = budget
    elif budget is getattr(agent, "_turn_budget", None):
        assert budget is not None
        budget.refill(agent.max_iterations)

    # Prime boundary state. ``None`` hooks keep the exact pre-v10 turn; the
    # block below only reads them. Usage from every successful model call is
    # captured here and charged exactly once per logical turn boundary,
    # separate from the permission to append a continuation. Accounting
    # always runs; continuation is granted only past every precedence stop.
    hooks = getattr(agent, "prime_hooks", None)
    if getattr(hooks, "present", True) is False:
        hooks = None
    pending_usages: list = []
    turn_usage: dict[str, int] | None = None
    goal_report: dict | None = None
    autonomous_stop: dict | None = None
    # Whether goal work was observed in this public run: a fresh read of an
    # active goal at turn start, an active boundary, or a successful
    # goal-lifecycle tool row. Distinguishes a real clear during ongoing
    # work (cancels the other family's continuation) from never-set absence
    # (no veto on independent autonomy). ``prime_status`` alone reports
    # "cleared" for both, so the turn-local observation is authoritative.
    # A startup read failure degrades loudly and vetoes implicit calls for
    # this public run, while later boundaries still accrue paid usage.
    goal_ever_active, goal_startup_degraded = _goal_startup_state(hooks, agent, run_id)
    # Start of this run's own rows in the shared transcript. Refusal and
    # lifecycle scans stay scoped here, never to prior turns' history.
    turn_begin = len(messages)

    original_tools = wrap_tools(agent)
    if agent.plan_mode:
        assert agent.tools is not None
        agent.tools = apply_plan_mode(agent.tools)
    try:
        while api_call_count < agent.max_iterations and budget.remaining > 0:
            if agent.pause_gate is not None and not agent.pause_gate.wait_until_resumed(
                agent.interrupt
            ):
                interrupted = True
                turn_exit_reason = "interrupted_by_user"
                break
            if agent.interrupt is not None and agent.interrupt.is_set():
                interrupted = True
                turn_exit_reason = "interrupted_by_user"
                break
            stop = _run_extension_hooks(agent, messages)
            if stop is None:
                stop = run_before_model(agent, messages, agent.tools)
            if stop is not None:
                turn_exit_reason = str(stop.get("reason", "stopped_before_model"))
                break
            if not budget.consume():
                turn_exit_reason = "budget_exhausted"
                break
            api_call_count += 1
            started = time.monotonic()
            assistant_message = agent.model.complete(messages, original_tools)
            _trace_model_call(agent, started)
            if hooks is not None:
                pending_usages.append(_capture_usage(agent))
            if not isinstance(assistant_message, dict):
                raise TypeError("model.complete must return an assistant message dict")
            if not account_output(agent, assistant_message.get("content")):
                turn_exit_reason = "output_budget_exceeded"
                break
            if _tool_calls(assistant_message):
                # A later tool round supersedes a previous continuation answer;
                # an unfinished tool tail must still receive its visible close.
                final_response = None
                _fold_queued_steer(agent)
                since = len(messages)
                if agent.speculative_tools and not agent.plan_mode:
                    prepare_speculative(
                        agent, original_tools, _tool_calls(assistant_message)
                    )
                verdict: ToolRoundVerdict | FinalResponseVerdict = run_tool_round(
                    agent,
                    assistant_message=assistant_message,
                    messages=messages,
                    task_id=task_id,
                )
                late_steer = drain_steer(agent)
                if late_steer:
                    messages.append(steer_user_row(late_steer))
                apply_tool_marks(agent, messages, since)
                _emit_new_rows(agent, messages, since)
                _journal_new_rows(agent, run_id, messages, since)
                if not account_output(
                    agent, *[_tool_content(row) for row in messages[since:]]
                ):
                    turn_exit_reason = "output_budget_exceeded"
                    break
                if verdict.action == "break":
                    final_response = verdict.final_response
                    turn_exit_reason = verdict.turn_exit_reason
                    break
                continue
            finish_reason = assistant_message.get("finish_reason") or "stop"
            since = len(messages)
            verdict = finish_text_response(
                agent,
                assistant_message=assistant_message,
                messages=messages,
                finish_reason=str(finish_reason),
            )
            _emit_new_rows(agent, messages, since)
            _journal_new_rows(agent, run_id, messages, since)
            if hooks is None:
                final_response = verdict.final_response
                turn_exit_reason = verdict.turn_exit_reason
                break
            # One logical-turn boundary: charge this boundary's usage once to
            # the real goal and driver, then yield to every precedence stop
            # before any continuation. Continuation appends user rows to the
            # same transcript and stays inside this lease, journal run, and
            # cumulative cap/budget. The final response is preserved even when
            # a stop wins; the stop itself is exposed in turn_exit_reason so
            # a limit, pause, clear, stale, or gate outcome can never read as
            # a completed text-response success.
            outcome = _prime_text_boundary(
                agent,
                hooks,
                verdict,
                finish_reason=str(finish_reason),
                interrupted=interrupted,
                failed=failed,
                pending_usages=pending_usages,
                run_id=run_id,
                api_call_count=api_call_count,
                max_iterations=agent.max_iterations,
                budget_remaining=budget.remaining,
                messages=messages,
                turn_begin=turn_begin,
                goal_ever_active=goal_ever_active,
                goal_startup_degraded=goal_startup_degraded,
            )
            turn_usage = merge_usage(turn_usage, outcome["aggregate"])
            pending_usages.clear()
            goal_ever_active = outcome["goal_ever_active"]
            if outcome["report"] is not None:
                goal_report = outcome["report"]
            if outcome["autonomous_stop"] is not None:
                autonomous_stop = outcome["autonomous_stop"]
            if outcome["rows"] is None:
                final_response = verdict.final_response
                turn_exit_reason = outcome["turn_exit_reason"]
                break
            # Keep the accepted answer if a later continuation is vetoed before
            # another answer arrives (interrupt, before-model, output budget).
            final_response = verdict.final_response
            since = len(messages)
            messages.extend(outcome["rows"])
            _emit_new_rows(agent, messages, since)
            _journal_new_rows(agent, run_id, messages, since)
            continue

        if turn_exit_reason is None:
            if api_call_count >= agent.max_iterations:
                turn_exit_reason = (
                    f"max_iterations_reached({api_call_count}/{agent.max_iterations})"
                )
            elif budget.remaining <= 0:
                turn_exit_reason = "budget_exhausted"
            else:
                turn_exit_reason = "stopped"

        _report_leftover_steer(agent)
        if hooks is not None and pending_usages:
            # A tail with no text boundary (tool-row budget/cap exit): charge
            # its successful calls once to the goal and the driver under
            # terminal semantics. The loop's own exit reason stands; no gate
            # runs and no continuation is requested for a turn that cannot
            # continue, but paid work is never left uncounted.
            tail = sum_usages(pending_usages)
            turn_usage = merge_usage(turn_usage, tail)
            charged = accounting_usage(pending_usages)
            pending_usages.clear()
            goal = evaluate_goal(hooks, agent, charged, turn_id=run_id)
            if goal["report"] is not None:
                goal_report = goal["report"]
            tail_stop = _terminal_driver_stop(
                hooks, agent, charged, turn_exit_reason, run_id
            )
            if tail_stop is not None:
                autonomous_stop = tail_stop
        since = len(messages)
        result = finalize_turn(
            agent,
            final_response=final_response,
            messages=messages,
            api_call_count=api_call_count,
            interrupted=interrupted,
            failed=failed,
            turn_exit_reason=turn_exit_reason,
            task_id=task_id,
        )
        if hooks is not None:
            # Unknown usage stays unknown (None), never fabricated.
            result["usage"] = turn_usage
            if goal_report is not None:
                result["goal_completion"] = goal_report
            if autonomous_stop is not None:
                result["autonomous_stop"] = autonomous_stop
        _emit_new_rows(agent, messages, since)
        _journal_new_rows(agent, run_id, messages, since)
        emit(agent, "turn_end", turn_exit_reason=result["turn_exit_reason"])
        _finish_journal(agent, run_id, result)
        return result
    except BaseException as exc:
        if hooks is not None and isinstance(exc, Exception) and pending_usages:
            # Successful calls preceding a later provider failure are accrued
            # to the goal and the driver while the original exception
            # propagates with its type intact. No gate runs for a failed turn;
            # the driver sees terminal semantics. Accounting failures degrade
            # loudly and never replace it.
            with contextlib.suppress(Exception):
                charged = accounting_usage(pending_usages)
                evaluate_goal(hooks, agent, charged, turn_id=run_id)
                _terminal_driver_stop(hooks, agent, charged, type(exc).__name__, run_id)
        raise
    finally:
        restore_tools(agent, original_tools)


def _capture_usage(agent: Agent) -> Any:
    """Snapshot the provider's latest-call usage. Real shapes are dict or None."""
    usage = getattr(agent.model, "last_usage", None)
    if isinstance(usage, dict):
        return dict(usage)
    return usage


def _goal_startup_state(
    hooks: Any, agent: Agent, run_id: str | None = None
) -> tuple[bool, bool]:
    """Fresh read of goal work plus startup-hook health as the run opens.

    Returns ``(active, startup_degraded)``. The read goes through the
    existing redacted guarded-hook mechanism, so a factory failure emits
    one ``prime_degraded`` event instead of vanishing silently; the veto
    is remembered by the caller for this public run. Read-only; never
    accrues. Later boundaries still charge paid usage when a fresh store
    is reachable again.
    """
    factory = getattr(hooks, "goal_factory", None) if hooks is not None else None
    if not callable(factory):
        return (False, False)
    status = guarded_hook(
        agent, "goals", lambda: factory().prime_status(), turn_id=run_id
    )
    if status is None:
        return (False, True)
    return (
        isinstance(status, dict) and status.get("prime_status") == "active",
        False,
    )


def _refusal_reason(messages: list, turn_begin: int) -> str | None:
    """Honest stop reason when the run's most recent tool round was refused.

    Only the tool rows after the last assistant row that carries tool calls
    inside ``messages[turn_begin:]`` are read, so a refusal the model recovered
    from in a later round is not terminal, while a refusal in the final round
    that precedes the final answer still is.
    """
    run = messages[turn_begin:]
    last_round = 0
    for index, row in enumerate(run):
        if (
            isinstance(row, dict)
            and row.get("role") == "assistant"
            and row.get("tool_calls")
        ):
            last_round = index + 1
    for row in run[last_round:]:
        if isinstance(row, dict) and row.get("role") == "tool":
            content = row.get("content")
            # Anchored to the failure wire: refusals arrive as failed rows
            # (``error: ...`` from the dispatcher-raised registry denial),
            # never as successful returned document data that merely mentions
            # the same words.
            if isinstance(content, str) and content.startswith("error:"):
                if "policy forbids" in content:
                    return "policy_refused"
                if "approval required" in content:
                    return "approval_refused"
    return None


def _cap_reason(
    api_call_count: int, max_iterations: int, budget_remaining: int
) -> str | None:
    """Honest stop reason when the run hit a bound, else ``None``.

    The outer iteration cap keeps its own actual reason; only a
    caller-supplied budget exhaustion reads as ``budget_exhausted``.
    Mirrors the loop's post-loop fallback strings exactly.
    """
    if api_call_count >= max_iterations:
        return f"max_iterations_reached({api_call_count}/{max_iterations})"
    if budget_remaining <= 0:
        return "budget_exhausted"
    return None


def _goal_lifecycle_seen(messages: list, turn_begin: int) -> bool:
    """Whether this run successfully executed a goal-lifecycle tool."""
    for row in messages[turn_begin:]:
        if (
            isinstance(row, dict)
            and row.get("role") == "tool"
            and row.get("name")
            in ("goal_set", "goal_pause", "goal_resume", "goal_clear")
        ):
            content = row.get("content")
            if isinstance(content, str) and not content.startswith("error:"):
                return True
    return False


def _goal_stop_reason(status: Any) -> str | None:
    """The honest exit reason for a goal state that vetoes continuation."""
    if not isinstance(status, dict):
        return None
    if status.get("prime_status") == "paused":
        return "goal_paused"
    if status.get("prime_status") == "completed":
        return "goal_completed"
    if status.get("budget_exhausted"):
        return "goal_budget_exhausted"
    if status.get("stale"):
        return "goal_stale"
    return None


def _terminal_driver_stop(
    hooks: Any,
    agent: Agent,
    usage: dict[str, int] | None,
    turn_exit_reason: str | None,
    run_id: str | None,
) -> dict | None:
    """Charge terminal usage to the driver without running its gate.

    The driver sees ``completed: False`` under its existing incomplete-result
    semantics: counters move for paid work, no completion gate runs, and no
    continuation is proposed. Returns the stop verdict to record, or ``None``
    when no run exists to stop. A stopped driver's counters never move; an
    explicit tool stop reports its stable marker.
    """
    auto = evaluate_autonomous(
        hooks,
        agent,
        {"completed": False, "turn_exit_reason": turn_exit_reason, "usage": usage},
        turn_id=run_id,
    )
    auto_verdict = auto["verdict"] if isinstance(auto["verdict"], dict) else None
    if auto["explicit_stop"]:
        return {"action": "stop", "reason": "autonomous_stop"}
    if auto_verdict is not None and auto_verdict.get("action") == "stop":
        return auto_verdict
    return None


def _prime_text_boundary(
    agent: Agent,
    hooks: Any,
    verdict: FinalResponseVerdict,
    *,
    finish_reason: str,
    interrupted: bool,
    failed: bool,
    pending_usages: list,
    run_id: str | None,
    api_call_count: int,
    max_iterations: int,
    budget_remaining: int,
    messages: list,
    turn_begin: int,
    goal_ever_active: bool,
    goal_startup_degraded: bool = False,
) -> dict:
    """Evaluate one logical-turn boundary for goal/autonomous continuation.

    Accounting and continuation permission are separate. Every successful
    model call's usage is charged exactly once to the real goal and driver
    first, including terminal boundaries; only then is continuation
    considered. Returns ``{"aggregate", "report", "autonomous_stop", "rows",
    "turn_exit_reason", "goal_ever_active"}``. ``rows`` is ``None`` when the
    turn ends (yielding to a precedence stop) and a list of user-role
    continuation rows when the same turn continues. Either applicable stop
    wins over any other family's continuation request; any degraded control
    hook (including a failed startup read) stops implicit calls for this
    public run while the normal response stays available. A goal/driver
    limit, pause, clear, stale, gate, refusal, or unfinished finish keeps
    the final response but carries the real stop in turn_exit_reason, so it
    can never read as a completed text-response success. No bound is
    refilled here; the caller stays inside the existing cumulative cap and
    caller budget.
    """
    aggregate = sum_usages(pending_usages)
    charged = accounting_usage(pending_usages)
    goal = evaluate_goal(hooks, agent, charged, turn_id=run_id)
    report = goal["report"]
    if (
        not goal.get("degraded")
        and isinstance(goal.get("status"), dict)
        and goal["status"].get("prime_status") == "active"
    ):
        goal_ever_active = True
    if not goal_ever_active and _goal_lifecycle_seen(messages, turn_begin):
        goal_ever_active = True
    autonomous_stop: dict | None = None
    breakdown = {
        "aggregate": aggregate,
        "report": report,
        "autonomous_stop": None,
        "rows": None,
        "turn_exit_reason": verdict.turn_exit_reason,
        "goal_ever_active": goal_ever_active,
    }
    # Authoritative goal stops are read before the driver is consulted, so a
    # paused/completed/exhausted/stale/cleared ongoing goal never lets a
    # completion gate run. The driver is still charged under its existing
    # incomplete-result semantics (counters move, no gate runs).
    goal_reason = _goal_stop_reason(goal["status"])
    if goal_reason is None and (
        isinstance(goal.get("status"), dict)
        and goal["status"].get("prime_status") == "cleared"
        and goal_ever_active
    ):
        # A real clear during ongoing goal work cancels the other family's
        # continuation for this run. Never-set absence (no work observed)
        # applies no such veto, so independent autonomy still runs.
        goal_reason = "goal_cleared"
    # Terminal accounting: the outer cap, caller budget, interrupt, failure,
    # unfinished finish, or an approval/policy refusal in this run's own
    # failed tool rows. The final response is preserved, but the exit reason
    # names the real stop so genuinely unfinished or refused controlled work
    # never reads as a completed text-response success.
    holder = hooks.autonomous_holder
    controlled = (
        goal_ever_active
        or goal_reason is not None
        or bool(goal["prompt"])
        or (
            holder is not None
            and (holder.get("driver") is not None or holder.get("stop_requested"))
        )
    )
    # Registered-but-idle families do not relabel an ordinary completed answer
    # merely because it consumed the caller's final allowed model call.
    cap_reason = (
        _cap_reason(api_call_count, max_iterations, budget_remaining)
        if controlled
        else None
    )
    finished_text = finish_reason in ("stop", "end_turn", "stop_sequence")
    refusal_reason = _refusal_reason(messages, turn_begin)
    terminal = (
        cap_reason is not None
        or interrupted
        or failed
        or not finished_text
        or refusal_reason is not None
    )
    boundary_reason: str | None
    if cap_reason is not None:
        boundary_reason = cap_reason
    elif goal_reason is not None:
        boundary_reason = goal_reason
    elif refusal_reason is not None:
        boundary_reason = refusal_reason
    elif not finished_text:
        boundary_reason = f"incomplete_response(finish_reason={finish_reason})"
    else:
        boundary_reason = verdict.turn_exit_reason
    turn_result = {
        "completed": not terminal and goal_reason is None,
        "turn_exit_reason": boundary_reason,
        "usage": charged,
    }
    auto = evaluate_autonomous(hooks, agent, turn_result, turn_id=run_id)
    auto_verdict = auto["verdict"] if isinstance(auto["verdict"], dict) else None
    if auto["explicit_stop"] or (
        auto_verdict is not None and auto_verdict.get("action") == "stop"
    ):
        if auto_verdict is not None:
            autonomous_stop = auto_verdict
        else:
            autonomous_stop = {"action": "stop", "reason": "autonomous_stop"}
        breakdown["autonomous_stop"] = autonomous_stop
    if cap_reason is not None:
        # The loop's own bound stands; the driver's terminal stop above is
        # still recorded honestly on the result.
        breakdown["turn_exit_reason"] = cap_reason
        return breakdown
    if goal_reason is not None:
        # An authoritative goal stop wins over any driver continuation or
        # stop; the driver's terminal accounting above is still recorded.
        breakdown["turn_exit_reason"] = goal_reason
        return breakdown
    if terminal:
        # Refusal, unfinished finish, interrupt, or failure: the honest
        # boundary reason stands; the driver's terminal stop above is still
        # recorded honestly on the result.
        breakdown["turn_exit_reason"] = boundary_reason
        return breakdown
    if autonomous_stop is not None:
        # An explicit tool stop or a driver limit/gate stop wins even with an
        # active goal. Stopped outcomes stay sticky and honest on the result:
        # the final response is preserved but the exit reason names the stop.
        breakdown["turn_exit_reason"] = (
            f"autonomous_stop({autonomous_stop.get('reason')})"
        )
        return breakdown
    # A degraded control hook stops implicit calls for this public run. The
    # normal response stays available and the redacted degradation was
    # already emitted; neither family's continuation may spend past an
    # unreadable budget on this run.
    if goal.get("degraded") or auto.get("degraded") or goal_startup_degraded:
        return breakdown
    rows: list = []
    if goal["prompt"]:
        rows.append({"role": "user", "content": goal["prompt"]})
    if (
        auto["present"]
        and not auto["degraded"]
        and auto_verdict is not None
        and auto_verdict.get("action") == "continue"
        and auto_verdict.get("prompt")
    ):
        rows.append({"role": "user", "content": auto_verdict["prompt"]})
    if not rows:
        return breakdown
    breakdown["rows"] = rows
    return breakdown


def _open_substrate(
    agent: Agent, user_message: Any, system_message: str | None
) -> str | None:
    """Open the substrate session; prepend its brief block. Never raises."""
    substrate = getattr(agent, "substrate", None)
    if substrate is None:
        return system_message
    try:
        substrate.open()
        block = substrate.brief_block()
    except Exception:
        return system_message
    if block:
        system_message = f"{block}\n\n{system_message}" if system_message else block
    with contextlib.suppress(Exception):
        substrate.on_prompt(user_message)
    return system_message


def _close_substrate_turn(agent: Agent, result: dict) -> None:
    """Report the turn-end note. Never raises."""
    substrate = getattr(agent, "substrate", None)
    if substrate is None:
        return
    try:
        reason = result.get("turn_exit_reason") if isinstance(result, dict) else None
        substrate.on_turn_end(reason)
    except Exception:
        pass


def _install_system_prompt(
    agent: Agent, messages: list, system_message: str | None
) -> None:
    """Build the system prompt once. A history that already starts with one is reused."""
    if (
        messages
        and isinstance(messages[0], dict)
        and messages[0].get("role") == "system"
    ):
        content = messages[0].get("content")
        if isinstance(content, str) and content:
            agent._cached_system_prompt = content
            return
    prompt = build_system_prompt(agent, system_message)
    row = {"role": "system", "content": prompt}
    if not messages:
        messages.append(row)
    else:
        messages.insert(0, row)


def _trace_model_call(agent: Agent, started: float) -> None:
    """Record one model span. No tracer, no-op."""
    tracer = getattr(agent, "tracer", None)
    if tracer is None:
        return
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)
    fields: dict[str, Any] = {"model": type(agent.model).__name__}
    usage = getattr(agent.model, "last_usage", None)
    if isinstance(usage, dict) and usage:
        counters = {
            key: value
            for key, value in usage.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        if counters:
            fields["usage"] = counters
    tracer.span(
        "model",
        type(agent.model).__name__,
        fields,
        duration_ms=max(0.0, elapsed_ms),
    )


def _begin_journal(agent: Agent) -> str | None:
    """Open the journal run. None when the agent journals nothing."""
    journal = getattr(agent, "journal", None)
    if journal is None:
        return None
    run_id = getattr(agent, "run_id", None)
    if not run_id:
        run_id = uuid.uuid4().hex
        agent.run_id = run_id
    journal.begin_run(run_id)
    return run_id


def _journal_new_rows(
    agent: Agent, run_id: str | None, messages: list, since: int
) -> None:
    """Persist rows appended at ``messages[since:]``. No journal, no-op."""
    journal = getattr(agent, "journal", None)
    if journal is None or run_id is None:
        return
    for row in messages[since:]:
        journal.append(run_id, dict(row) if isinstance(row, dict) else {"row": row})


def _finish_journal(agent: Agent, run_id: str | None, result: dict) -> None:
    """Close the journal run with the result's reason and response."""
    journal = getattr(agent, "journal", None)
    if journal is None or run_id is None:
        return
    response = result.get("final_response", "")
    journal.finish_run(
        run_id, str(result.get("turn_exit_reason", "stopped")), str(response)
    )


def _run_extension_hooks(agent: Agent, messages: list) -> dict | None:
    """Run registered extension hooks; None when none stop the turn."""
    extensions = getattr(agent, "extensions", None)
    if extensions is None:
        return None
    return extensions.run_before_model(messages, agent.tools)


def _tool_calls(message: dict) -> list:
    calls = message.get("tool_calls")
    return calls if isinstance(calls, list) and calls else []


def _emit_new_rows(agent: Agent, messages: list, since: int) -> None:
    """Emit one ``message`` event per row appended at ``messages[since:]``."""
    for row in messages[since:]:
        role = row.get("role") if isinstance(row, dict) else None
        emit(agent, "message", role=role)


def _tool_content(row: Any) -> str:
    if isinstance(row, dict) and row.get("role") == "tool":
        content = row.get("content", "")
        return content if isinstance(content, str) else str(content)
    return ""


def _fold_queued_steer(agent: Agent) -> None:
    """Fold harness-queued steer into the legacy slot for the tool round."""
    if getattr(agent, "_steer_queue", None):
        agent.pending_steer = drain_steer(agent)


def _report_leftover_steer(agent: Agent) -> None:
    """Move undelivered queued steer onto the legacy slot for the finalizer."""
    leftover = take_leftover_steer(agent)
    if leftover:
        queued = "\n".join(part for part in leftover if part.strip())
        legacy = agent.pending_steer
        if legacy:
            queued = f"{queued}\n{legacy}" if queued else str(legacy)
        agent.pending_steer = queued or None


__all__ = ["Agent", "run_conversation"]

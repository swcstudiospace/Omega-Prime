"""Omp harness behavior for the Omega Prime conversation loop.

Ports of ``oh-my-pi/packages/agent/src`` behavior (not types) so one agent
class satisfies both loops:

- ``agent-loop.ts`` turn/message events (``turn_start`` / ``message`` /
  ``turn_end``) and the ``beforeModelCall`` hook, which may mutate the outgoing
  request or stop the turn before it is billed.
- ``live-steering.ts`` queued steering: text injected mid-turn is delivered as
  its own user row, never merged into a tool row (same invariant as the
  phase-2 steer).
- ``pause.ts`` pause gate polled at both action boundaries (before each model
  call and before each tool execution). Parking never aborts: in-flight work
  runs to completion and queued steers deliver after resume. An interrupt
  unwinds a parked wait without releasing the gate.
- ``agent-loop.ts`` tool-result marking: a result flagged ``useless`` is kept
  for compaction but is never an error; errors are never useless.
- ``speculative-execution.ts`` single-shot speculative cache for discard-safe
  tools, committed only on fingerprint match inside the same loop.

No network, no third-party dependencies. Only the conversation loop calls
into this module; ``turn_tool_round.py`` is untouched, so tool wrapping below
adapts its ``agent.tools`` reads without changing its behavior.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any


# ------------------------------------------------------------- events ---
def events_of(agent: Any) -> list:
    """Return the agent's event sink, creating it on first use."""
    events = getattr(agent, "events", None)
    if events is None:
        events = []
        agent.events = events
    return events


def emit(agent: Any, type: str, **payload: Any) -> dict:
    """Append ``{"type": type, ...}`` to the agent's event sink.

    String payload values are stored redacted: events must never carry keys.
    """
    event = {"type": type, **_redact_payload(payload)}
    events_of(agent).append(event)
    return event


def _redact_payload(payload: dict) -> dict:
    from omega_prime.credentials.redact import redact_text

    return {
        key: redact_text(value) if isinstance(value, str) else value
        for key, value in payload.items()
    }


# -------------------------------------------------------------- steer ---
def inject_steer(agent: Any, text: str) -> None:
    """Queue steer text from another thread while a turn is running.

    Delivery happens at the next loop boundary as its own user row. The
    legacy single ``agent.pending_steer`` slot keeps working: queued entries
    drain first, then the legacy slot.
    """
    queue = getattr(agent, "_steer_queue", None)
    if queue is None:
        queue = []
        agent._steer_queue = queue
    queue.append(text)


def drain_steer(agent: Any) -> str | None:
    """Take all queued steer text (queue, then legacy slot) as one string."""
    parts: list[str] = []
    queue = getattr(agent, "_steer_queue", None)
    if queue:
        while queue:
            parts.append(str(queue.pop(0)))
    legacy = getattr(agent, "pending_steer", None)
    if legacy is not None:
        agent.pending_steer = None
        parts.append(str(legacy))
    cleaned = "\n".join(part.strip() for part in parts if str(part).strip())
    return cleaned or None


def take_leftover_steer(agent: Any) -> list[str] | None:
    """Clear undelivered queued steer; the loop reports it on the result."""
    queue = getattr(agent, "_steer_queue", None)
    if not queue:
        return None
    leftover = [str(part) for part in queue]
    del queue[:]
    return leftover or None


# ------------------------------------------------------- before-model ---
def run_before_model(agent: Any, messages: list, tools: Any) -> dict | None:
    """Run ``agent.before_model(messages, tools)`` when set.

    The hook may mutate ``messages`` (or ``tools``) in place to change the
    outgoing request. Returning ``{"stop": True, ...}`` ends the turn without
    a model call; anything else continues. No hook means no stop.
    """
    hook = getattr(agent, "before_model", None)
    if hook is None:
        return None
    decision = hook(messages, tools)
    if isinstance(decision, dict) and decision.get("stop"):
        return decision
    return None


# --------------------------------------------------- result marking ---
def coerce_tool_result(raw: dict) -> tuple[str, bool, bool]:
    """Split a structured tool return into ``(content, is_error, useless)``.

    Errors are never useless: a result that sets both flags keeps the error.
    """
    is_error = bool(raw.get("is_error", False))
    useless = bool(raw.get("useless", False)) and not is_error
    content = raw.get("content", "")
    if content is None:
        content = ""
    elif not isinstance(content, str):
        content = str(content)
    return content, is_error, useless


def is_error_result(row: Any) -> bool:
    """True when a persisted tool row is an error (useless rows are not)."""
    return isinstance(row, dict) and row.get("is_error") is True


def is_useless_result(row: Any) -> bool:
    """True when a persisted tool row was flagged contextually useless."""
    return (
        isinstance(row, dict)
        and row.get("useless") is True
        and row.get("is_error") is not True
    )


def _looks_structured(result: Any) -> bool:
    return isinstance(result, dict) and ("useless" in result or "is_error" in result)


def wrap_tools(agent: Any) -> dict:
    """Swap ``agent.tools`` for harness-aware wrappers; return the original.

    The wrappers translate structured tool returns (``useless`` / ``is_error``
    dicts) to plain content strings while recording one mark per execution in
    call order on ``agent._harness_marks``. Exceptions propagate to the
    existing ``turn_tool_round`` handler and record an error mark. Each wrapper
    also honors the pause gate, which is how the tool-execution boundary parks
    without editing ``turn_tool_round.py``. Callers must restore the original
    with :func:`restore_tools`.
    """
    original = getattr(agent, "tools", None) or {}
    marks: list[tuple[str, bool, bool]] = []
    agent._harness_marks = marks

    def _wrap(name: str, fn: Any) -> Any:
        def _inner(*args: Any, **kwargs: Any) -> Any:
            gate = getattr(agent, "pause_gate", None)
            if gate is not None:
                gate.wait_until_resumed(getattr(agent, "interrupt", None))
            cached = commit_speculative(agent, name, kwargs if kwargs else {})
            if cached is not None:
                content, is_error, useless = cached
                marks.append((name, is_error, useless))
                return content
            try:
                result = fn(*args, **kwargs)
            except Exception:
                marks.append((name, True, False))
                raise
            if _looks_structured(result):
                content, is_error, useless = coerce_tool_result(result)
                marks.append((name, is_error, useless))
                return content
            marks.append((name, False, False))
            return result

        return _inner

    agent.tools = {name: _wrap(name, fn) for name, fn in original.items()}
    return original


def restore_tools(agent: Any, original: dict) -> None:
    """Put back the tool map :func:`wrap_tools` replaced."""
    agent.tools = original


def apply_tool_marks(agent: Any, messages: list, since: int) -> None:
    """Flag tool rows appended at ``messages[since:]`` as error/useful/useless.

    Marks recorded by the wrappers line up with rows in execution order; a row
    with no mark is an unknown-tool failure. Only flag keys are added — row
    content is already final — so append-only transcripts are untouched.
    """
    marks: list[tuple[str, bool, bool]] = getattr(agent, "_harness_marks", None) or []
    for row in messages[since:]:
        if not isinstance(row, dict) or row.get("role") != "tool":
            continue
        name = row.get("name")
        if marks and marks[0][0] == name:
            _, is_error, useless = marks.pop(0)
            if "is_error" not in row:
                row["is_error"] = is_error
            if useless and "useless" not in row:
                row["useless"] = True
        elif "is_error" not in row:
            content = row.get("content")
            row["is_error"] = isinstance(content, str) and content.startswith("error:")


# ---------------------------------------------------------- pause gate ---
class PauseGate:
    """Freeze switch polled by the loop at both action boundaries.

    Adapted from Omp ``pause.ts`` (``AgentPauseGate``): engaging the gate parks
    loops at the next safe point without aborting anything, and queued steers
    deliver normally after resume. ``wait_until_resumed`` releases only its own
    wait on interrupt — the gate stays engaged — so cancelling one turn never
    unfreezes the process.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._paused = False
        self._paused_at: float | None = None
        self._listeners: set[Any] = set()

    @property
    def paused(self) -> bool:
        with self._cond:
            return self._paused

    @property
    def paused_at(self) -> float | None:
        with self._cond:
            return self._paused_at if self._paused else None

    def pause(self) -> bool:
        """Engage the gate. False (and no-op) when already paused."""
        with self._cond:
            if self._paused:
                return False
            self._paused = True
            self._paused_at = time.time()
            listeners = list(self._listeners)
        for listener in listeners:
            listener(True)
        return True

    def resume(self) -> float | None:
        """Release the gate, waking every parked loop. Pause ms, else None."""
        with self._cond:
            if not self._paused:
                return None
            duration_ms = (time.time() - (self._paused_at or time.time())) * 1000.0
            self._paused = False
            self._paused_at = None
            self._cond.notify_all()
            listeners = list(self._listeners)
        for listener in listeners:
            listener(False)
        return duration_ms

    def on_change(self, listener: Any) -> Any:
        """Subscribe to pause/resume transitions; returns an unsubscribe fn."""
        with self._cond:
            self._listeners.add(listener)

        def _off() -> None:
            with self._cond:
                self._listeners.discard(listener)

        return _off

    def wait_until_resumed(self, interrupt: Any = None, poll_s: float = 0.05) -> bool:
        """Park while paused. True when running; False on interrupt (gate kept)."""
        with self._cond:
            while self._paused:
                if interrupt is not None and interrupt.is_set():
                    return False
                self._cond.wait(timeout=poll_s)
            return True


#: Process-wide gate for hosts that freeze every loop at once (Omp ``agentPauseGate``).
default_pause_gate = PauseGate()


# ------------------------------------------------------- speculation ---
def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_call(call: Any) -> tuple[str, dict]:
    raw = call.get("function") if isinstance(call, dict) else None
    function: dict[str, Any] = raw if isinstance(raw, dict) else {}
    name = str(function.get("name") or "")
    return name, _parse_args(function.get("arguments"))


def fingerprint(name: str, args: dict) -> str:
    """Canonical identity of one tool execution (Omp execution fingerprint)."""
    try:
        return json.dumps(
            {"name": name, "args": args},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return f"{name}\x00{args!r}"


class SpeculativeCache:
    """Single-shot results for discard-safe tools, committed on match."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, bool, bool]] = {}
        self._lock = threading.Lock()

    def store(self, fp: str, content: str, is_error: bool, useless: bool) -> None:
        with self._lock:
            self._entries[fp] = (content, is_error, useless)

    def commit(self, fp: str) -> tuple[str, bool, bool] | None:
        with self._lock:
            return self._entries.pop(fp, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def is_speculative_tool(agent: Any, name: str, tools: dict) -> bool:
    """Opt-in per tool: membership in ``agent.speculative_tools`` or a
    ``speculative_safe`` attribute on the function. Nothing speculates by default."""
    spec = getattr(agent, "speculative_tools", None)
    if spec is not None and name in spec:
        return True
    fn = tools.get(name)
    return bool(getattr(fn, "speculative_safe", False))


def prepare_speculative(agent: Any, tools: dict, calls: list) -> SpeculativeCache:
    """Pre-execute discard-safe calls off the dispatch path.

    Runs ahead of ordinary dispatch (in worker threads, so two safe tools
    overlap); ordinary dispatch later commits each result only when its
    fingerprint matches, otherwise the entry is discarded and the tool runs
    fresh. Failures are discarded, never cached.
    """
    cache = SpeculativeCache()
    agent._spec_cache = cache
    todo: list[tuple[str, str, Any, dict]] = []
    seen: set[str] = set()
    for call in calls or []:
        name, args = _parse_call(call)
        if not name or not is_speculative_tool(agent, name, tools):
            continue
        fp = fingerprint(name, args)
        if fp in seen:
            continue
        seen.add(fp)
        fn = tools.get(name)
        if fn is None:
            continue
        todo.append((fp, name, fn, args))
    if not todo:
        return cache

    def _run(
        entry: tuple[str, str, Any, dict],
    ) -> tuple[str, tuple[str, bool, bool] | None]:
        fp, _name, fn, args = entry
        gate = getattr(agent, "pause_gate", None)
        if gate is not None:
            gate.wait_until_resumed(getattr(agent, "interrupt", None))
        try:
            if args:
                result = fn(**args)
            else:
                try:
                    result = fn()
                except TypeError:
                    result = fn()
        except Exception:
            return fp, None
        if _looks_structured(result):
            content, is_error, useless = coerce_tool_result(result)
        elif result is None:
            content, is_error, useless = "", False, False
        elif isinstance(result, str):
            content, is_error, useless = result, False, False
        else:
            content, is_error, useless = str(result), False, False
        return fp, (content, is_error, useless)

    with ThreadPoolExecutor(max_workers=max(1, min(len(todo), 4))) as pool:
        for fp, outcome in pool.map(_run, todo):
            if outcome is not None:
                cache.store(fp, *outcome)
    return cache


def commit_speculative(
    agent: Any, name: str, args: dict
) -> tuple[str, bool, bool] | None:
    """Take the prepared result for ``(name, args)`` or None (discarded)."""
    cache = getattr(agent, "_spec_cache", None)
    if cache is None:
        return None
    if not isinstance(args, dict):
        args = {}
    return cache.commit(fingerprint(name, args))


# --------------------------------------------------- output accounting ---
def account_output(agent: Any, *texts: Any) -> bool:
    """Charge ``texts`` to ``agent.output_budget``. True while within budget."""
    budget = getattr(agent, "output_budget", None)
    if budget is None:
        return True
    ok = True
    for text in texts:
        if text is None:
            chunk = ""
        elif isinstance(text, str):
            chunk = text
        else:
            chunk = str(text)
        if not budget.add(chunk):
            ok = False
    return ok


__all__ = [
    "PauseGate",
    "SpeculativeCache",
    "account_output",
    "apply_tool_marks",
    "coerce_tool_result",
    "commit_speculative",
    "default_pause_gate",
    "drain_steer",
    "emit",
    "events_of",
    "fingerprint",
    "inject_steer",
    "is_error_result",
    "is_speculative_tool",
    "is_useless_result",
    "prepare_speculative",
    "restore_tools",
    "run_before_model",
    "take_leftover_steer",
    "wrap_tools",
]

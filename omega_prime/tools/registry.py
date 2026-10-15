"""Tool registry.

Adapted from Hermes ``tools/registry.py``. A tool is a name, a description, a JSON
schema for its parameters, and a handler. ``dispatch`` always returns a JSON string.
An unknown name becomes a JSON error and does not raise. Handlers return
JSON-serializable objects, which this module encodes, or a JSON string, which is
returned unchanged. A tool marked ``requires_approval`` does not run until the
registry's approval log has approved that name.
"""

from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Callable
from typing import Any


class ToolRegistry:
    """In-process tool map. ``dispatch`` calls the handler and encodes its result.

    ``approval_log`` gates tools registered with ``requires_approval``. ``None``
    leaves every flagged tool unapproved. Unflagged tools are unchanged.
    ``policy`` refuses names the seat policy does not allow, before approvals
    and before the handler. ``audit`` records one verdict per dispatch, and
    ``tracer`` records one tool span (plus a policy span on refusals).
    ``None`` for any of them keeps the historical behavior.
    """

    def __init__(
        self,
        approval_log: Any = None,
        policy: Any = None,
        audit: Any = None,
        tracer: Any = None,
    ) -> None:
        self._tools: dict[str, _Tool] = {}
        self._order: list[str] = []
        self._approval_log = approval_log
        self._policy = policy
        self._audit = audit
        self._tracer = tracer
        # Resource attachment for enabled capability families. Keys are
        # published only by the registering family module itself
        # (``prime_goals`` factory, ``prime_autonomous`` holder,
        # ``prime_heartbeat`` runtime). Empty by default; never grants tools
        # and never bypasses ``dispatch``.
        self.runtime_bindings: dict[str, Any] = {}

    @property
    def policy(self) -> Any:
        """Seat policy, or ``None`` when this registry was built without one."""
        return self._policy

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[..., Any],
        requires_approval: bool = False,
    ) -> None:
        """Store ``handler`` under ``name``. Registering the same name replaces it."""
        if not isinstance(name, str) or not name:
            raise ValueError("tool name must be a non-empty string")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be a JSON schema object")
        if not callable(handler):
            raise ValueError("handler must be callable")
        self._tools[name] = _Tool(
            name,
            description,
            copy.deepcopy(parameters),
            handler,
            bool(requires_approval),
        )
        if name not in self._order:
            self._order.append(name)

    def schemas(self) -> list[dict]:
        """OpenAI-style function schemas, in registration order."""
        return [
            _schema(self._tools[name]) for name in self._order if name in self._tools
        ]

    def approval_required(self, name: str) -> bool:
        """Whether ``name`` is registered and flagged as requiring approval."""
        tool = self._tools.get(name) if isinstance(name, str) else None
        return bool(tool is not None and tool.requires_approval)

    def dispatch(self, name: str, arguments: Any = None) -> str:
        """Run ``name`` and return a JSON string. Unknown names do not raise."""
        from omega_prime.prime.errors import PrimeError

        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            label = name if isinstance(name, str) else type(name).__name__
            self._record(label, "unknown")
            return _dump({"error": f"Unknown tool: {label}"})
        if self._policy is not None and not self._policy.allows_tool(name):
            self._record(name, "denied", reason="policy forbids")
            return _dump({"error": f"policy forbids {name}", "tool": name})
        try:
            if tool.requires_approval and not _has_approval(self._approval_log, name):
                self._record(name, "denied", reason="approval required")
                return _dump({"error": "approval required", "tool": name})
            payload = _call(tool.handler, _arguments(arguments))
        except PrimeError as exc:
            # Typed-boundary failures keep their structured code/reason on the
            # wire (CONN-01): the model sees "code: reason" plus the machine
            # fields, not a flattened traceback. Policy/approval already ran
            # above; decoding never precedes authorization.
            self._record(name, "error", reason=f"{exc.code}: {exc.reason}")
            return _dump(exc.to_dict())
        except Exception as exc:
            self._record(name, "error", reason=f"{type(exc).__name__}: {exc}")
            return _dump({"error": f"{type(exc).__name__}: {exc}"})
        # Growth handlers return a JSON string. Dispatch must return that string,
        # not a second encoding. Any other string is encoded below.
        if isinstance(payload, str):
            try:
                json.loads(payload)
            except json.JSONDecodeError:
                pass
            else:
                self._record(name, "allowed")
                return payload
        try:
            encoded = _dump(payload)
        except (TypeError, ValueError):
            self._record(name, "error", reason="tool result is not JSON-serializable")
            return _dump({"error": "tool result is not JSON-serializable"})
        self._record(name, "allowed")
        return encoded

    def _record(self, tool: str, verdict: str, reason: str | None = None) -> None:
        """Append one audit record, and trace the dispatch, when attached."""
        if self._audit is not None:
            self._audit.append(tool, verdict, reason)
        if self._tracer is not None:
            fields: dict[str, Any] = {"verdict": verdict}
            if reason is not None:
                fields["reason"] = reason
            self._tracer.span("tool", tool, fields)
            if verdict == "denied" and reason == "policy forbids":
                self._tracer.span(
                    "policy",
                    tool,
                    {"verdict": "denied", "reason": f"policy forbids {tool}"},
                )


class _Tool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[..., Any],
        requires_approval: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.requires_approval = requires_approval


def _schema(tool: _Tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": copy.deepcopy(tool.parameters),
        },
    }


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _has_approval(log: Any, name: str) -> bool:
    """True only when ``log`` exists and reports this tool approved."""
    if log is None:
        return False
    is_approved = getattr(log, "is_approved", None)
    if not callable(is_approved):
        return False
    return bool(is_approved(name))


def _arguments(arguments: Any) -> dict:
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        parsed = json.loads(arguments)
        if not isinstance(parsed, dict):
            raise ValueError("arguments must be a JSON object")
        return parsed
    if isinstance(arguments, dict):
        return arguments
    raise ValueError("arguments must be an object")


def _call(handler: Callable[..., Any], arguments: dict) -> Any:
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return handler(**arguments)
    parameters = signature.parameters
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return handler(**arguments)
    accepted = {
        name
        for name, item in parameters.items()
        if item.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    missing = [
        name
        for name, item in parameters.items()
        if name in accepted
        and item.default is inspect.Parameter.empty
        and name not in arguments
    ]
    if missing:
        raise TypeError("missing required arguments: " + ", ".join(missing))
    extra = [name for name in arguments if name not in accepted]
    if extra:
        raise TypeError("unexpected arguments: " + ", ".join(extra))
    return handler(**{name: arguments[name] for name in accepted if name in arguments})


__all__ = ["ToolRegistry"]

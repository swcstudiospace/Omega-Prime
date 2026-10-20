# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""One agent instance: Hermes/Omp turn handling and registry-gated tools."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any, Literal

from omega_prime.agent.conversation_loop import Agent, run_conversation
from omega_prime.agent.degraded import guarded_hook
from omega_prime.agent.harness import emit
from omega_prime.agent.model import Model
from omega_prime.agent.prime_hooks import prime_hooks_from_bindings
from omega_prime.agent.session_lease import SessionLease
from omega_prime.commands import parse_omega_command
from omega_prime.tools.offer import offered_schemas
from omega_prime.tools.registry import ToolRegistry


class _RegisteredModel:
    """Give the provider real tool schemas, while the loop holds dispatchers."""

    def __init__(self, model: Model, registry: ToolRegistry) -> None:
        self.provider = model
        self.registry = registry

    @property
    def last_usage(self) -> Any:
        return getattr(self.provider, "last_usage", None)

    def complete(self, messages: list, tools: Any = None) -> dict:
        names = tools.keys() if isinstance(tools, dict) else ()
        return self.provider.complete(messages, offered_schemas(self.registry, names))


def _dispatcher(registry: ToolRegistry, name: str) -> Any:
    def dispatch(**arguments: Any) -> str:
        result = registry.dispatch(name, arguments)
        payload = json.loads(result)
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return result

    return dispatch


class OmegaPrimeAgent(Agent):
    """Run the existing loop with one provider, tool registry, and transcript.

    The supplied registry remains the authority for approvals and policy. An
    optional roster narrows both offered schemas and executable tools; it never
    grants tools absent from the registry. All existing ``Agent`` loop options
    (pause, steering, extensions, budgets, journal, tracer) are accepted.
    ``session_dir`` is the durable directory this agent offers as the parent of
    an ``RlmHost`` (RLM child records live under it); without it the agent
    cannot be an RLM parent.
    """

    def __init__(
        self,
        model: Model,
        *,
        registry: ToolRegistry,
        roster: Iterable[str] | None = None,
        system_message: str | None = None,
        session_name: str = "default",
        session_dir: str | os.PathLike[str] | None = None,
        **loop_options: Any,
    ) -> None:
        schemas = registry.schemas()
        names = (
            [schema["function"]["name"] for schema in schemas]
            if roster is None
            else [
                schema["function"]["name"]
                for schema in offered_schemas(registry, roster)
            ]
        )
        self.registry = registry
        self.provider_model = model
        self.messages: list[dict] = []
        self.system_message = system_message
        # Stable session identity for named heartbeat re-entry.
        self.session_name = session_name
        self.session_dir: str | None = (
            None if session_dir is None else os.fspath(session_dir)
        )
        # Same-registry composition: the loop consumes the exact resources
        # the enabled families published. Absent bindings mean absent hooks.
        # ToolRegistry always owns runtime_bindings, so read it directly.
        bindings = registry.runtime_bindings
        loop_options["prime_hooks"] = prime_hooks_from_bindings(bindings)
        super().__init__(
            model=_RegisteredModel(model, registry),
            tools={name: _dispatcher(registry, name) for name in names},
            **loop_options,
        )
        # A roster narrows the tool map. None means the full registry, which
        # the omp MCP roster is allowed to serve. Plan mode still stays off
        # the engine.
        self.tool_roster = None if roster is None else tuple(names)
        self.engine_full_tools = roster is None
        # Heartbeat glue (61-02 surface, consumed directly). When a heartbeat
        # binding exists, bind this live runner before starting the
        # registry-owned runtime so beats re-enter this same transcript.
        # Bind/start are ordinary capabilities: their failure degrades loudly
        # through guarded_hook while the foreground conversation stays up.
        # Model/provider/control-flow failures are never wrapped here.
        self._heartbeat_runtime: Any = bindings.get("prime_heartbeat")
        self._heartbeat_runner: Any = None
        if self._heartbeat_runtime is not None:
            self._heartbeat_runner = self._run_heartbeat_beat

            def _attach() -> bool:
                assert self._heartbeat_runtime is not None
                assert self._heartbeat_runner is not None
                self._heartbeat_runtime.bind(
                    self.session_name,
                    self._heartbeat_runner,
                    event_sink=self._make_binding_sink(),
                )
                self._heartbeat_runtime.start()
                return True

            guarded_hook(self, "heartbeat", _attach)

    @classmethod
    def from_prime(
        cls,
        model: dict,
        *,
        registry: ToolRegistry,
        api_key: str | None = None,
        broker: Any = None,
        provider_options: dict | None = None,
        **agent_options: Any,
    ) -> OmegaPrimeAgent:
        """Select real Prime providers explicitly; never silently fall back."""
        from omega_prime.providers.prime import PrimeProviderModel

        provider = PrimeProviderModel(
            model, api_key=api_key, broker=broker, options=provider_options
        )
        return cls(provider, registry=registry, **agent_options)

    def run(
        self, user_message: Any, *, task_id: str | None = None, wait: bool = False
    ) -> dict:
        """Run a turn in this session, under the existing exclusive lease.

        ``wait`` forwards verbatim into the lease acquisition: the default
        fast-fails on a held lease exactly as before; heartbeat re-entry
        opts into atomic waiting.
        """
        if isinstance(user_message, str) and self._serves_omega_command():
            parsed = parse_omega_command(user_message)
            if parsed is not None:
                return self._run_omega_command(user_message.strip(), wait=wait)
        return run_conversation(
            self,
            user_message,
            system_message=self.system_message,
            conversation_history=self.messages,
            task_id=task_id,
            wait=wait,
        )

    def _serves_omega_command(self) -> bool:
        for schema in self.registry.schemas():
            function = schema.get("function")
            if isinstance(function, dict) and function.get("name") == "omega_command":
                return True
        return False

    def _run_omega_command(self, command: str, *, wait: bool) -> dict:
        """Run one slash command on this registry. The model is not called."""
        lease = self.lease
        if lease is None:
            lease = SessionLease()
            self.lease = lease
        lease.acquire(wait=wait)
        try:
            raw = self.registry.dispatch("omega_command", {"command": command})
            text = raw if isinstance(raw, str) else json.dumps(raw)
            self.messages.append({"role": "user", "content": command})
            self.messages.append({"role": "assistant", "content": text})
            return {
                "final_response": text,
                "messages": self.messages,
                "api_call_count": 0,
                "turn_exit_reason": "omega_command",
            }
        finally:
            lease.release()

    def _run_heartbeat_beat(self, prompt: str) -> str:
        """Re-enter this same live session for one scheduled beat.

        Returns the actual ``final_response`` string of the beat's run, which
        is the JSON-serializable callback response the scheduler persists.
        It runs on the owning transcript with opt-in lease waiting, outside
        any bookkeeping lock, so a foreground turn never deadlocks a beat.
        """
        result = self.run(prompt, wait=True)
        return result["final_response"]

    def _make_binding_sink(self) -> Any:
        """Per-binding error sink routed into this session's redacted events.

        The sink is captured together with this binding's runner, so another
        named session can never reroute this session's error events. Delivery
        uses the shared harness emit path, which redacts string payloads.
        """
        agent = self

        def _sink(event: Any = None, **fields: Any) -> None:
            payload = dict(event) if isinstance(event, dict) else dict(fields)
            event_type = payload.pop("type", "prime_degraded")
            if not isinstance(event_type, str):
                event_type = "prime_degraded"
            emit(agent, event_type, **payload)

        return _sink

    def close(self) -> None:
        """Detach this session's heartbeat binding, stopping owned scheduling.

        The conditional unbind compares the expected runner atomically, so
        closing a replaced agent never detaches its replacement or stops
        another live binding. ``stop_if_empty`` folds last-owner shutdown
        into that same atomic step. Handles clear only after the lifecycle
        action succeeds, so a cleanup failure propagates with a retryable
        handle still attached.
        """
        runtime = self._heartbeat_runtime
        runner = self._heartbeat_runner
        if runtime is None or runner is None:
            return
        runtime.unbind(self.session_name, runner=runner, stop_if_empty=True)
        self._heartbeat_runner = None
        self._heartbeat_runtime = None

    def __enter__(self) -> OmegaPrimeAgent:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        self.close()
        return False


__all__ = ["OmegaPrimeAgent"]

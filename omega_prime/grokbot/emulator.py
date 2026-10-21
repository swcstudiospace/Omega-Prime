"""Interactive Grok Bot Turn Emulator and Smoke Test Harness.

Enables developers and CI pipelines to simulate Grok Bot conversational turns,
tool calls, gated approval workflows, and receipt generation locally without
requiring live xAI API keys or cloud infrastructure.

The emulator drives the same runtime the tool host serves: `load_runtime` builds
the roster, seat policy, approval log and registry, and every call goes through
`call_tool_handler` with the scope and audit interceptors, so roster, policy,
approval gates, scopes and audit behave as on the remote host.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp.types import TextContent

from omega_prime.grokbot.audit import GrokBotAuditTracer, default_audit_path
from omega_prime.grokbot.interceptors import AuditInterceptor, ScopeInterceptor
from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.grokbot.security import SCOPE_CALL, SCOPE_READ, Principal
from omega_prime.mcp_server import call_tool_handler, load_runtime
from omega_prime.tools.clarify import clarify


class _EmulatorOperator:
    """Deterministic operator stand-in; this is not a live human route."""

    def ask(self, question: str) -> str:
        return "Emulator stand-in answer: proceed to the operator-selected phase."


_EMULATOR_OPERATOR = _EmulatorOperator()


def _emulator_clarify(question: str) -> dict[str, Any]:
    """Use a fixed stand-in answer for smoke scenarios, never a real operator."""
    return clarify(_EMULATOR_OPERATOR, question)


EMULATOR_CALLER = "emulator"
SMOKE_APPROVER = "emulator-smoke"


@dataclass
class TurnResult:
    scenario: str
    tool_name: str
    arguments: dict[str, Any]
    status: str
    result: Any
    elapsed_ms: float
    error: str | None = None


def _context(principal: Principal) -> Any:
    """The slice of the MCP request context the interceptors read."""
    state = SimpleNamespace(principal=principal, request_id=None)
    return SimpleNamespace(request=SimpleNamespace(state=state))


def _parse_approval(item: str) -> tuple[str, str]:
    tool, _, approver = item.partition(":")
    if not tool or not approver:
        raise ValueError(f"bad --approve {item!r}, want TOOL:APPROVER")
    return tool, approver


class GrokBotTurnEmulator:
    """Emulates Grok Bot conversation turns and tool invocations."""

    def __init__(
        self,
        root: Path | None = None,
        auditor: GrokBotAuditTracer | None = None,
        *,
        home: Path | None = None,
        approvals: Sequence[tuple[str, str]] = (),
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else find_repo_root()
        self.home = home
        self.env = env
        self.approvals = tuple(approvals)
        self.auditor = (
            auditor
            if auditor is not None
            else GrokBotAuditTracer(default_audit_path(self.root))
        )
        self.runtime = load_runtime(self.root, home, approvals=self.approvals, env=env)
        clarify_schema = next(
            schema["function"]
            for schema in self.runtime.registry.schemas()
            if schema.get("function", {}).get("name") == "clarify"
        )
        self.runtime.registry.register(
            "clarify",
            "Ask the emulator's deterministic operator stand-in; not a live human route.",
            clarify_schema["parameters"],
            _emulator_clarify,
        )
        self.registry = self.runtime.registry
        self._principal = Principal(
            EMULATOR_CALLER, frozenset({SCOPE_READ, SCOPE_CALL})
        )
        self._handler = call_tool_handler(
            self.runtime.registry,
            self.runtime.roster,
            interceptors=[ScopeInterceptor(), AuditInterceptor(self.auditor)],
            transport="stdio",
        )

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool invocation through the production call handler."""
        start = time.perf_counter()
        if tool_name not in self.runtime.tool_names:
            error = f"Tool '{tool_name}' not found in active roster"
            elapsed = (time.perf_counter() - start) * 1000
            self.auditor.log_event(
                "tool_call",
                tool_name=tool_name,
                caller=EMULATOR_CALLER,
                status="denied",
                duration_ms=elapsed,
                is_error=True,
                details={
                    "arg_keys": sorted(str(key) for key in arguments or {}),
                    "transport": "stdio",
                    "denial": "not_in_roster",
                    "error": error,
                },
            )
            return {"status": "error", "error": error, "elapsed_ms": elapsed}
        try:
            params = SimpleNamespace(name=tool_name, arguments=arguments or {})
            outcome = asyncio.run(self._handler(_context(self._principal), params))
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": elapsed,
            }
        elapsed = (time.perf_counter() - start) * 1000
        raw_output = "".join(
            part.text for part in outcome.content if isinstance(part, TextContent)
        )
        try:
            parsed_res: Any = json.loads(raw_output)
        except ValueError:
            parsed_res = raw_output
        if not outcome.is_error:
            return {"status": "ok", "result": parsed_res, "elapsed_ms": elapsed}
        message = (
            parsed_res.get("error") if isinstance(parsed_res, dict) else parsed_res
        )
        return {
            "status": "error",
            "result": parsed_res,
            "error": str(message),
            "elapsed_ms": elapsed,
        }

    def _run_scenarios(
        self, scenarios: Sequence[tuple[str, str, dict[str, Any]]]
    ) -> list[TurnResult]:
        results: list[TurnResult] = []
        for desc, tool_name, args in scenarios:
            t0 = time.perf_counter()
            out = self.execute_tool(tool_name, args)
            dur = (time.perf_counter() - t0) * 1000
            is_err = out.get("status") == "error" or "error" in out
            results.append(
                TurnResult(
                    scenario=desc,
                    tool_name=tool_name,
                    arguments=args,
                    status="ok" if not is_err else "error",
                    result=out.get("result"),
                    elapsed_ms=dur,
                    error=out.get("error"),
                )
            )
        return results

    def _approval_probe(self) -> str | None:
        """A gated tool that rejects empty arguments before its body runs."""
        gated = set(self.runtime.gated_tools)
        for schema in self.registry.schemas():
            fn = schema.get("function", {})
            name = fn.get("name", "")
            required = fn.get("parameters", {}).get("required")
            if (
                name in gated
                and required
                and not self.runtime.approval_log.is_approved(name)
            ):
                return str(name)
        return None

    def run_approval_scenario(self) -> list[TurnResult]:
        """Prove a gated tool is refused without approval and passes it with one.

        The probe sends no arguments to a gated tool that declares required
        parameters, so the registry rejects the call before the tool body can run
        and the scenario has no side effect. An empty list means no gated tool
        qualifies in this runtime.
        """
        tool = self._approval_probe()
        if tool is None:
            return []
        results: list[TurnResult] = []

        t0 = time.perf_counter()
        refused = self.execute_tool(tool, {})
        refusal = str(refused.get("error", ""))
        ok = refused.get("status") == "error" and "approval required" in refusal
        results.append(
            TurnResult(
                scenario="Approval-gated tool is refused without approval",
                tool_name=tool,
                arguments={},
                status="ok" if ok else "error",
                result=refused.get("result"),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                error=None if ok else f"expected an approval refusal, got {refused}",
            )
        )

        approved = GrokBotTurnEmulator(
            self.root,
            self.auditor,
            home=self.home,
            approvals=(*self.approvals, (tool, SMOKE_APPROVER)),
            env=self.env,
        )
        t0 = time.perf_counter()
        passed = approved.execute_tool(tool, {})
        text = str(passed.get("error", ""))
        ok = "approval required" not in text and "not found" not in text
        results.append(
            TurnResult(
                scenario="Approval-gated tool passes the approval gate when approved",
                tool_name=tool,
                arguments={},
                status="ok" if ok else "error",
                result=passed.get("result"),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                error=None if ok else f"approval gate still refused: {text}",
            )
        )
        return results

    def run_smoke_suite(self, *, include_approval: bool = False) -> list[TurnResult]:
        """Execute a predefined set of canonical Grok Bot smoke test turns."""
        scenarios: list[tuple[str, str, dict[str, Any]]] = [
            (
                "Read package init",
                "read_file",
                {"path": "__init__.py"},
            ),
            (
                "Inspect TODO list",
                "todo_read",
                {},
            ),
            (
                "Run harmless echo command",
                "run_terminal",
                {"argv": ["echo", "omega-prime-grokbot-emulator"]},
            ),
            (
                "Clarify with deterministic emulator stand-in (not a live human)",
                "clarify",
                {"question": "What is the next phase?"},
            ),
        ]
        results = self._run_scenarios(scenarios)
        if include_approval:
            results.extend(self.run_approval_scenario())
        return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Grok Bot local turn emulator and smoke tester"
    )
    parser.add_argument(
        "--smoke", action="store_true", help="Run automated smoke turn suite"
    )
    parser.add_argument(
        "--tool", type=str, default=None, help="Execute a specific tool call"
    )
    parser.add_argument(
        "--args", type=str, default="{}", help="JSON arguments for tool call"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results in JSON format"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (default: the checkout this package lives in)",
    )
    parser.add_argument(
        "--approve",
        action="append",
        default=[],
        metavar="TOOL:APPROVER",
        help="pre-approve one gated tool (repeatable)",
    )
    args = parser.parse_args(argv)

    try:
        approvals = [_parse_approval(item) for item in args.approve]
        emulator = GrokBotTurnEmulator(args.root, approvals=approvals)
    except ValueError as exc:  # includes RuntimeConfigError
        print(f"omega-prime-grokbot-emulator: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.tool:
        try:
            tool_args = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"Error parsing --args JSON: {exc}", file=sys.stderr)
            sys.exit(1)
        res = emulator.execute_tool(args.tool, tool_args)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Tool: {args.tool}")
            print(f"Status: {res.get('status')}")
            if "error" in res:
                print(f"Error: {res['error']}")
            else:
                print(f"Result:\n{res.get('result')}")
        return

    if args.smoke or not args.tool:
        print("Running Grok Bot Smoke Turn Test Suite...")
        results = emulator.run_smoke_suite(include_approval=True)
        all_ok = True
        for r in results:
            mark = "PASS" if r.status == "ok" else "FAIL"
            if r.status != "ok":
                all_ok = False
            print(f"  [{mark}] {r.scenario} ({r.tool_name}) - {r.elapsed_ms:.1f}ms")
            if r.error:
                print(f"         Error: {r.error}")

        if not all_ok:
            sys.exit(1)
        print("All smoke turn scenarios passed successfully!")


if __name__ == "__main__":
    main()

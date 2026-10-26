"""On-demand operator commands for eventstream."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from omega_prime.credentials.redact import REDACTED, redact_value
from omega_prime.eventstream.config import EventStreamConfig

_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "authorization",
        "auth",
        "credential",
    }
)


def _sanitize_for_emit(value: object) -> object:
    """Redact secret-shaped strings and values under credential key names."""
    if isinstance(value, dict):
        sanitized: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                sanitized[key] = REDACTED
            else:
                sanitized[key] = _sanitize_for_emit(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_emit(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_for_emit(item) for item in value)
    return redact_value(value)


def _emit(value: object) -> None:
    safe_value = _sanitize_for_emit(value)
    serialized = json.dumps(
        safe_value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=lambda item: str(redact_value(str(item))),
    )
    sys.stdout.write(f"{redact_value(serialized)}\n")


async def _admin_operation(operation: str, config: EventStreamConfig) -> object:
    from omega_prime.eventstream.admin import PulsarAdminPort

    admin = PulsarAdminPort()
    try:
        await admin.configure(config)
        if operation == "apply":
            result = await admin.reconcile(dry_run=False)
            return {
                "operation": operation,
                "dry_run": result.dry_run,
                "changes": list(result.changes),
                "actions": [
                    {
                        "method": item.method,
                        "path": item.path,
                        "description": item.description,
                    }
                    for item in result.actions
                ],
            }
        if operation == "dry-run":
            result = await admin.reconcile(dry_run=True)
            return {
                "operation": "apply",
                "dry_run": True,
                "changes": list(result.changes),
                "actions": [
                    {
                        "method": item.method,
                        "path": item.path,
                        "description": item.description,
                    }
                    for item in result.actions
                ],
            }
        return {"operation": operation, "status": await admin.status()}
    finally:
        await admin.close()


async def _tail(topic: str, config: EventStreamConfig) -> None:
    from omega_prime.eventstream.adapters.pulsar import PulsarEventBus

    bus = PulsarEventBus(config)
    reader = None
    try:
        reader = await bus.read(topic, start_at=0)
        while True:
            delivery = await reader.read()
            _emit(
                {
                    "topic": delivery.topic,
                    "key": delivery.key,
                    "event": json.loads(delivery.event.to_bytes()),
                }
            )
    finally:
        try:
            if reader is not None:
                await reader.close()
        finally:
            await bus.close()


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="omega eventstream")
    commands = parser.add_subparsers(dest="operation", required=True)
    apply = commands.add_parser("apply", help="Reconcile eventstream topology")
    apply.add_argument("--dry-run", action="store_true")
    commands.add_parser("status", help="Read eventstream topology status")
    tail = commands.add_parser("tail", help="Read events from an explicit topic")
    tail.add_argument("topic")
    args = parser.parse_args(raw_argv)
    try:
        config = EventStreamConfig.from_env(argv=raw_argv)
        if args.operation == "tail":
            if not args.topic.startswith("persistent://omega/"):
                raise ValueError("tail topic must be a persistent omega topic")
            asyncio.run(_tail(args.topic, config))
        else:
            operation = (
                "dry-run"
                if args.operation == "apply" and args.dry_run
                else args.operation
            )
            _emit(asyncio.run(_admin_operation(operation, config)))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        detail = str(redact_value(f"{type(exc).__name__}: {exc}"))
        sys.stderr.write(f"omega eventstream: {detail}\n")
        return 2

"""CLI: ``python -m omega_prime.ci_runners controller|agent --host H --port P``.

Configuration comes from validated ``OMEGA_CI_*`` settings (secrets by
``*_FILE`` path). ``uvicorn`` is the only serving dependency and is imported
only when a server is actually started (``pip install 'omega-prime[ci-runners]'``).
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from omega_prime.ci_runners.settings import SettingsError

EXTRA_HINT = "install the serving extra: pip install 'omega-prime[ci-runners]'"


class ServeUnavailable(RuntimeError):
    pass


def serve(app: Any, host: str, port: int) -> None:
    try:
        uvicorn = importlib.import_module("uvicorn")
    except ImportError:
        raise ServeUnavailable(f"uvicorn is not installed; {EXTRA_HINT}") from None
    uvicorn.run(app, host=host, port=port, access_log=False, lifespan="on")


def _controller_app(env: Mapping[str, str]) -> Any:
    from omega_prime.ci_runners.app import build_controller_app
    from omega_prime.ci_runners.settings import ControllerSettings

    return build_controller_app(ControllerSettings.from_env(env))


def _agent_app(env: Mapping[str, str]) -> Any:
    try:
        agent = importlib.import_module("omega_prime.ci_runners.agent.app")
    except ImportError:
        raise ServeUnavailable("the runner agent is not part of this build") from None
    return agent.build_agent_app(env)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m omega_prime.ci_runners")
    sub = root.add_subparsers(dest="command", required=True)
    for name, host, port, text in (
        ("controller", "127.0.0.1", 8788, "always-on webhook controller (VPS)"),
        ("agent", "0.0.0.0", 8787, "runner agent inside the Railway Desktop"),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--host", default=host)
        cmd.add_argument("--port", type=int, default=port)
    return root


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    serve_fn: Callable[[Any, str, int], None] = serve,
) -> int:
    args = parser().parse_args(argv)
    environ = os.environ if env is None else env
    build = _controller_app if args.command == "controller" else _agent_app
    try:
        app = build(environ)
        serve_fn(app, args.host, args.port)
    except (SettingsError, ServeUnavailable) as exc:
        print(f"omega-ci {args.command}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

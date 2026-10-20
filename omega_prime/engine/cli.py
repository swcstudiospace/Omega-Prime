# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``omega`` console: ``omega watch <job>``."""

from __future__ import annotations

import argparse
import sys

from omega_prime.engine.watch import main as watch_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omega")
    sub = parser.add_subparsers(dest="command", required=True)
    watch = sub.add_parser("watch", help="Tail one job's omp event feed")
    watch.add_argument("job")
    watch.add_argument("--follow", action="store_true")
    watch.add_argument("--state-dir", default=None)
    watch.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.command == "watch":
        forwarded = [args.job]
        if args.follow:
            forwarded.append("--follow")
        if args.state_dir:
            forwarded.extend(["--state-dir", args.state_dir])
        forwarded.extend(["--timeout", str(args.timeout)])
        return watch_main(forwarded)
    sys.stderr.write("unknown command\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

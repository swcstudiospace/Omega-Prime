#!/usr/bin/env bash
# Operator-started only (nohup or a Herdr workspace named "dashboard"); does not daemonize.
# Runs the checkout that contains this script. Interpreter: OMEGA_PRIME_DASHBOARD_PYTHON,
# else the active virtualenv, else <repo>/.venv, else python3 on PATH.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
if [ -n "${OMEGA_PRIME_DASHBOARD_PYTHON:-}" ]; then
    PY="$OMEGA_PRIME_DASHBOARD_PYTHON"
elif [ -n "${VIRTUAL_ENV:-}" ]; then
    PY="$VIRTUAL_ENV/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="$(command -v python3)"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m omega_prime.dashboard --host 127.0.0.1 --port "${OMEGA_PRIME_DASHBOARD_PORT:-7421}"

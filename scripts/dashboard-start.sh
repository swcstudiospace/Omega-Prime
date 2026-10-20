#!/usr/bin/env bash
# Operator-started only (nohup or a Herdr workspace named "dashboard"); does not daemonize.
set -euo pipefail
cd /workspace/omes-bot-wt/dash
if [ -n "${VIRTUAL_ENV:-}" ]; then
    PY="$VIRTUAL_ENV/bin/python"
else
    PY=/workspace/omes-bot/.venv/bin/python
fi
export PYTHONPATH="/workspace/omes-bot-wt/dash${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m omega_prime.dashboard --host 127.0.0.1 --port "${OMEGA_PRIME_DASHBOARD_PORT:-7421}"

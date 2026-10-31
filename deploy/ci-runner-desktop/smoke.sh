#!/bin/sh
# Build-time smoke check for the Desktop runner agent overlay.
#
# Builds the REAL agent app (settings -> credential broker -> token read ->
# supervisor -> ASGI app) from the image's site directory only, with an
# isolated interpreter (-I -S: no PYTHONPATH, no site-packages), so a module
# missing from the image's COPY set fails the build instead of the first boot.
# The agent token is minted here from secrets.token_hex into a 0600 scratch
# file and deleted afterwards; nothing secret is baked into the image.
#
# Usage: smoke.sh [extra-module ...]   (the image passes "uvicorn")
set -eu

site="${OMEGA_CI_SITE:-/opt/omega-ci/site}"
runner_root="${OMEGA_CI_AGENT_RUNNER_ROOT:-/opt/actions-runner}"
python="${OMEGA_CI_PYTHON:-python3}"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
umask 077
"$python" -I -S -c 'import secrets; print(secrets.token_hex(32))' > "$scratch/agent-token"

env -i PATH="${PATH:-/usr/bin:/bin}" \
    OMEGA_CI_SITE="$site" \
    OMEGA_CI_AGENT_TOKEN_FILE="$scratch/agent-token" \
    OMEGA_CI_CONTROLLER_URL=http://127.0.0.1:9 \
    OMEGA_CI_AGENT_RUNNER_ROOT="$runner_root" \
    OMEGA_CI_AGENT_WORK_ROOT="$scratch/work" \
    "$python" -I -S -c '
import importlib, os, sys
sys.path.insert(0, os.environ["OMEGA_CI_SITE"])
assert sys.version_info >= (3, 11), "Python 3.11+ required"
from omega_prime.ci_runners.agent.app import build_agent_app
app = build_agent_app(os.environ)
assert callable(app)
for name in sys.argv[1:]:
    importlib.import_module(name)
site = os.path.realpath(os.environ["OMEGA_CI_SITE"])
for name, module in sorted(sys.modules.items()):
    if name.split(".")[0] != "omega_prime":
        continue
    origin = os.path.realpath(getattr(module, "__file__", None) or site)
    assert origin.startswith(site + os.sep) or origin == site, name
print("omega-ci smoke: agent app built from", site)
' "$@"

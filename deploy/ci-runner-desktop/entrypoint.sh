#!/bin/sh
# omega-ci Desktop wrapper: start the runner agent in the background, then
# exec the upstream desktop command (Docker CMD) unchanged, so the upstream
# supervisor keeps its PID, signals and exit status.
#
# The agent never sees the desktop PASSWORD. If agent settings are missing it
# exits (logged) and the desktop still starts: the desktop is never held
# hostage by CI configuration.
set -eu

if [ "$#" -eq 0 ]; then
    echo "omega-ci entrypoint: no upstream command given" >&2
    exit 64
fi

OMEGA_CI_SITE="${OMEGA_CI_SITE:-/opt/omega-ci/site}"

env -u PASSWORD PYTHONPATH="$OMEGA_CI_SITE" \
    python3 -m omega_prime.ci_runners agent --host 0.0.0.0 --port 8787 &

exec "$@"

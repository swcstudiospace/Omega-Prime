#!/bin/sh
# omega-ci Desktop wrapper: start the runner agent and the upstream desktop
# command (Docker CMD, unchanged argv and env) side by side, and forward
# SIGTERM/SIGINT to BOTH.
#
# Why not `exec "$@"`: upstream's supervisor only stops the processes it
# launched itself. The agent would never see Railway's SIGTERM, so on every
# redeploy the kernel SIGKILLed it together with every runner it owned. No
# exit callback reached the controller, which then held each dead runner's
# slot until GitHub timed the job out plus the finish deadline (00062: the
# 24 Oct 09:10 AEDT redeploy stalled the pool for ~12 minutes). With the
# signal forwarded, the agent drains: each runner gets SIGTERM, then SIGKILL
# after its grace, and one exit callback per runner frees the slot at once.
# RAILWAY_DEPLOYMENT_DRAINING_SECONDS must cover that grace.
#
# The agent never sees the desktop PASSWORD. If agent settings are missing it
# exits (logged) and the desktop still starts: the desktop is never held
# hostage by CI configuration. The wrapper exits with the upstream status.
set -eu

if [ "$#" -eq 0 ]; then
    echo "omega-ci entrypoint: no upstream command given" >&2
    exit 64
fi

OMEGA_CI_SITE="${OMEGA_CI_SITE:-/opt/omega-ci/site}"

stopping=0
agent_pid=
desktop_pid=

forward() {
    stopping=1
    # Agent first so runner exit callbacks are on their way while the
    # desktop tears down.
    [ -n "$agent_pid" ] && kill -TERM "$agent_pid" 2>/dev/null || true
    [ -n "$desktop_pid" ] && kill -TERM "$desktop_pid" 2>/dev/null || true
}
trap forward TERM INT

env -u PASSWORD PYTHONPATH="$OMEGA_CI_SITE" \
    python3 -m omega_prime.ci_runners agent --host 0.0.0.0 --port 8787 &
agent_pid=$!

"$@" &
desktop_pid=$!

# A trapped signal interrupts `wait` (status > 128) while the child still
# runs; wait again for its real status.
status=0
wait "$desktop_pid" || status=$?
if [ "$stopping" -eq 1 ] && [ "$status" -gt 128 ]; then
    status=0
    wait "$desktop_pid" || status=$?
fi

# The desktop is gone (signal or its own failure): stop the agent and let it
# drain before the container exits.
kill -TERM "$agent_pid" 2>/dev/null || true
wait "$agent_pid" 2>/dev/null || true
exit "$status"

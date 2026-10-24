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
# signal forwarded, the agent drains: idle runners get SIGTERM at once, runners
# mid-job may finish within RAILWAY_DEPLOYMENT_DRAINING_SECONDS minus a stop
# margin, then any left get SIGTERM and SIGKILL after their grace. One exit
# callback per runner frees each slot (see README for the budget).
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
    if [ -n "$agent_pid" ]; then kill -TERM "$agent_pid" 2>/dev/null || true; fi
    if [ -n "$desktop_pid" ]; then kill -TERM "$desktop_pid" 2>/dev/null || true; fi
}
trap forward TERM INT

# reap PID: wait until PID has really exited and set $status to its exit
# status. A trapped signal interrupts `wait` (status > 128) while the child
# may still run, so wait again while it exists. If it exited during the
# interruption the shell has already collected it; one more `wait` returns
# that saved status (127 means there was nothing left to collect).
reap() {
    while :; do
        status=0
        wait "$1" || status=$?
        [ "$status" -gt 128 ] || return 0
        kill -0 "$1" 2>/dev/null || break
    done
    saved=0
    wait "$1" 2>/dev/null || saved=$?
    if [ "$saved" -ne 127 ]; then status=$saved; fi
}

env -u PASSWORD PYTHONPATH="$OMEGA_CI_SITE" \
    python3 -m omega_prime.ci_runners agent --host 0.0.0.0 --port 8787 &
agent_pid=$!

"$@" &
desktop_pid=$!

# A stop that arrived before both PIDs were recorded reached neither child.
if [ "$stopping" -eq 1 ]; then forward; fi

reap "$desktop_pid"
desktop_status=$status

# The desktop is gone (signal or its own failure): stop the agent and let it
# drain before the container exits, even if more stop signals arrive.
kill -TERM "$agent_pid" 2>/dev/null || true
reap "$agent_pid"
exit "$desktop_status"

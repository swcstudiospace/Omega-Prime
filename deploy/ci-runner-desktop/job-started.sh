#!/bin/sh
# omega-ci runner-side pre-job guard (ACTIONS_RUNNER_HOOK_JOB_STARTED).
# Runs as the job identity after GitHub assigns a job and before any workflow
# step. A non-zero exit fails the job, so refused work never runs. Logic,
# tests and the refusal rules: omega_prime/ci_runners/agent/jobhook.py.
# Absolute paths only, and python3 -I (ignores PYTHON* variables and the
# user site) so nothing in the job's environment can redirect it. The policy
# file is written by the root agent at startup (OMEGA_CI_AGENT_REPOS).
set -eu
exec /usr/bin/python3 -I /opt/omega-ci/site/omega_prime/ci_runners/agent/jobhook.py /var/lib/omega-ci/job-policy.json

"""Public-repository fork guard: decide whether a public job may get a runner.

Self-hosted runners on public repositories are a known remote-code-execution
vector: anyone can open a pull request from a fork, and its workflow code would
run on our machine. The pool therefore refuses public repositories unless the
operator lists them in ``OMEGA_CI_PUBLIC_REPOS``, and even then serves a job
only after its workflow run is fetched from GitHub
(``GET /repos/{repo}/actions/runs/{run_id}``) and shown to be:

* in the same repository the webhook named,
* built from that same repository (``head_repository``), not a fork, and
* triggered by an allowed event: ``push``, ``workflow_dispatch`` or a
  same-repository ``pull_request``. ``pull_request_target`` and every other
  trigger are refused.

The ``workflow_job`` webhook alone cannot prove any of this, so the lookup is
mandatory, and every unknown (missing field, lookup error, 404) refuses.
Pure functions only; the controller does the I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ALLOWED_EVENTS = frozenset({"push", "workflow_dispatch", "pull_request"})

# Refusal reasons (the ``reason`` field of ``omega.ci.runner.refused``).
NOT_OPTED_IN = "public_not_opted_in"
LOOKUP_FAILED = "run_lookup_failed"
RUN_NOT_FOUND = "run_not_found"
REPO_MISMATCH = "run_repo_mismatch"
PULL_REQUEST_TARGET = "pull_request_target"
EVENT_NOT_ALLOWED = "event_not_allowed"
FORK = "fork"
HEAD_UNKNOWN = "head_unknown"


def _full_name(obj: Any) -> str | None:
    if not isinstance(obj, Mapping):
        return None
    name = obj.get("full_name")
    return name.lower() if isinstance(name, str) and "/" in name else None


def run_refusal(run: Mapping[str, Any] | None, repo: str) -> str | None:
    """``None`` when the workflow run is trusted, else the refusal reason."""
    if run is None:
        return RUN_NOT_FOUND
    if _full_name(run.get("repository")) != repo.lower():
        return REPO_MISMATCH
    event = run.get("event")
    if event == "pull_request_target":
        return PULL_REQUEST_TARGET
    if not isinstance(event, str) or event not in ALLOWED_EVENTS:
        return EVENT_NOT_ALLOWED
    head = run.get("head_repository")
    head_name = _full_name(head)
    if head_name is None:
        return HEAD_UNKNOWN
    if head_name != repo.lower():
        return FORK
    assert isinstance(head, Mapping)
    fork = head.get("fork")
    if fork is True:
        return FORK
    if fork is not False:
        return HEAD_UNKNOWN
    return None


__all__ = [
    "ALLOWED_EVENTS",
    "EVENT_NOT_ALLOWED",
    "FORK",
    "HEAD_UNKNOWN",
    "LOOKUP_FAILED",
    "NOT_OPTED_IN",
    "PULL_REQUEST_TARGET",
    "REPO_MISMATCH",
    "RUN_NOT_FOUND",
    "run_refusal",
]

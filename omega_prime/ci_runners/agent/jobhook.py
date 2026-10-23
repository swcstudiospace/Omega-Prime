"""Runner-side pre-job guard (``ACTIONS_RUNNER_HOOK_JOB_STARTED``).

The controller's fork guard decides when to *launch* a runner, but a JIT
runner registers into a shared runner group with shared labels: GitHub may
hand it any matching job, including one the controller refused (an approved
fork pull request, say). This hook closes that gap on the runner itself. The
runner executes it after a job is assigned and before any workflow step; a
non-zero exit fails the job, so no user code runs.

A job is refused when:

* the allowlist is missing or empty, or the repository is not on it;
* ``GITHUB_EVENT_NAME`` / ``GITHUB_REPOSITORY`` / ``GITHUB_EVENT_PATH`` are
  missing, or the event payload is unreadable or names another repository;
* the event is ``pull_request_target``;
* the payload carries a pull request whose head repository is a fork, is not
  this repository, or cannot be determined;
* the repository is not explicitly private and the event is not ``push``,
  ``workflow_dispatch`` or ``pull_request``.

Every unknown fails closed. The module is stdlib-only and imports nothing
from ``omega_prime`` so the image runs it as a plain file under
``python3 -I`` (environment-isolated, no ``PYTHONPATH``). The allowlist comes
from a root-owned policy file the agent writes at startup, never from the
job's environment. It carries repository names only, no secrets.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

ALLOWED_PUBLIC_EVENTS = frozenset({"push", "workflow_dispatch", "pull_request"})
MAX_EVENT_BYTES = 32 * 1024 * 1024
POLICY_VERSION = 1

NO_ALLOWLIST = "no_allowlist"
CONTEXT_MISSING = "context_missing"
REPO_NOT_ALLOWED = "repo_not_allowed"
EVENT_UNREADABLE = "event_unreadable"
REPO_MISMATCH = "repo_mismatch"
PULL_REQUEST_TARGET = "pull_request_target"
EVENT_NOT_ALLOWED = "event_not_allowed"
FORK = "fork"
HEAD_UNKNOWN = "head_unknown"


def _name(obj: Any) -> str | None:
    if not isinstance(obj, Mapping):
        return None
    name = obj.get("full_name")
    return name.lower() if isinstance(name, str) and "/" in name else None


def refusal(
    event_name: str | None,
    repository: str | None,
    payload: Any,
    allowed: Collection[str],
) -> str | None:
    """``None`` when the job may run, else why not. Pure; fails closed."""
    repos = {r.lower() for r in allowed if isinstance(r, str)}
    if not repos:
        return NO_ALLOWLIST
    if not event_name or not repository:
        return CONTEXT_MISSING
    repo = repository.lower()
    if repo not in repos:
        return REPO_NOT_ALLOWED
    if not isinstance(payload, Mapping):
        return EVENT_UNREADABLE
    source = payload.get("repository")
    if _name(source) != repo:
        return REPO_MISMATCH
    if event_name == "pull_request_target":
        return PULL_REQUEST_TARGET
    assert isinstance(source, Mapping)
    if source.get("private") is not True and event_name not in ALLOWED_PUBLIC_EVENTS:
        return EVENT_NOT_ALLOWED
    if event_name.startswith("pull_request") or "pull_request" in payload:
        pull = payload.get("pull_request")
        head = pull.get("head") if isinstance(pull, Mapping) else None
        head_repo = head.get("repo") if isinstance(head, Mapping) else None
        head_name = _name(head_repo)
        if head_name is None:
            return HEAD_UNKNOWN
        if head_name != repo:
            return FORK
        assert isinstance(head_repo, Mapping)
        fork = head_repo.get("fork")
        if fork is True:
            return FORK
        if fork is not False:
            return HEAD_UNKNOWN
    return None


def policy_document(repos: Collection[str]) -> dict[str, Any]:
    return {"version": POLICY_VERSION, "repos": sorted({r.lower() for r in repos})}


def load_policy(path: Path) -> frozenset[str]:
    """Allowlisted repositories, or empty (→ every job refused) on any doubt."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return frozenset()
    if not isinstance(raw, dict) or raw.get("version") != POLICY_VERSION:
        return frozenset()
    repos = raw.get("repos")
    if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
        return frozenset()
    return frozenset(r.lower() for r in repos)


def _payload(path: str | None) -> Any:
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_EVENT_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_EVENT_BYTES:
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return None


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    environ = os.environ if env is None else env
    try:
        allowed = load_policy(Path(args[0])) if len(args) == 1 else frozenset()
        reason = refusal(
            environ.get("GITHUB_EVENT_NAME"),
            environ.get("GITHUB_REPOSITORY"),
            _payload(environ.get("GITHUB_EVENT_PATH")),
            allowed,
        )
    except Exception:  # fail closed on anything unexpected
        reason = EVENT_UNREADABLE
    if reason is not None:
        print(
            f"::error::omega-ci job guard refused this job ({reason}). "
            "This self-hosted pool serves allowlisted repositories only, never "
            "pull_request_target or pull requests from forks.",
            file=sys.stderr,
        )
        return 1
    print("omega-ci job guard: ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ALLOWED_PUBLIC_EVENTS",
    "CONTEXT_MISSING",
    "EVENT_NOT_ALLOWED",
    "EVENT_UNREADABLE",
    "FORK",
    "HEAD_UNKNOWN",
    "NO_ALLOWLIST",
    "PULL_REQUEST_TARGET",
    "REPO_MISMATCH",
    "REPO_NOT_ALLOWED",
    "load_policy",
    "main",
    "policy_document",
    "refusal",
]

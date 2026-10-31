"""Runner-side pre-job guard (``ACTIONS_RUNNER_HOOK_JOB_STARTED``).

The controller's fork guard decides when to *launch* a runner, but a JIT
runner registers into a shared runner group with shared labels: GitHub may
hand it any matching job, including one the controller refused (an approved
fork pull request, say). This hook closes that gap on the runner itself. The
runner executes it after a job is assigned and before any workflow step; a
non-zero exit fails the job, so no user code runs.

A job is refused when:

* the allowlist is missing or empty, or the repository is not on it (an
  ``owner/*`` entry admits only repositories that are *not* themselves GitHub
  forks: an org fork of an upstream project carries upstream's workflows, so
  it runs only when listed by its exact name);
* ``GITHUB_EVENT_NAME`` / ``GITHUB_REPOSITORY`` / ``GITHUB_EVENT_PATH`` are
  missing, or the event payload is unreadable or names another repository;
* the event is ``pull_request_target``;
* the payload carries a pull request whose head repository is a fork, is not
  this repository, or cannot be determined;
* the repository is not explicitly private and the event is not ``push``,
  ``workflow_dispatch``, ``pull_request``, or ``schedule`` on the
  repository's default branch (``GITHUB_REF`` is ``refs/heads/<default>``).

Every unknown fails closed. The module is stdlib-only and imports nothing
from ``omega_prime`` so the image runs it as a plain file under
``python3 -I`` (environment-isolated, no ``PYTHONPATH``). The allowlist comes
from a root-owned policy file the agent writes at startup, never from the
job's environment. It carries repository names only, no secrets.

This module is also the one source of truth for repository allowlists
(``OMEGA_CI_REPOS``, ``OMEGA_CI_PUBLIC_REPOS``, ``OMEGA_CI_AGENT_REPOS``): the
controller's settings, webhook admission, demand replay and fork guard all
match through :func:`parse_repo_patterns`, :func:`repo_allowed` and
:func:`patterns_cover` below, so the controller and the runner can never
disagree about what an entry means. An entry is either ``owner/repo`` or the
owner wildcard ``owner/*``; there is no bare ``*`` and no partial glob.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

ALLOWED_PUBLIC_EVENTS = frozenset(
    {"push", "workflow_dispatch", "pull_request", "schedule"}
)
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
FORK_NOT_LISTED = "fork_repo_not_listed"

_OWNER = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
_EXACT_RE = re.compile(rf"^{_OWNER}/[A-Za-z0-9._-]{{1,100}}$")
_WILDCARD_RE = re.compile(rf"^{_OWNER}/\*$")
WILDCARD = "*"


class RepoPatternError(ValueError):
    """An allowlist entry is neither ``owner/repo`` nor ``owner/*``."""


def _is_repo(name: str) -> bool:
    return bool(_EXACT_RE.match(name)) and not name.endswith(("/.", "/.."))


def is_repo_pattern(entry: str) -> bool:
    """``owner/repo`` or ``owner/*``. Bare ``*``, ``*/x``, ``owner/foo*``,
    ``owner/**`` and anything with other characters are not patterns."""
    return _is_repo(entry) or bool(_WILDCARD_RE.match(entry))


def parse_repo_patterns(entries: Iterable[str]) -> frozenset[str]:
    """Validated, lower-cased allowlist entries. Raises on any bad entry."""
    out: set[str] = set()
    for raw in entries:
        entry = raw.strip() if isinstance(raw, str) else ""
        if not is_repo_pattern(entry):
            raise RepoPatternError("allowlist entries are owner/repo or owner/*")
        out.add(entry.lower())
    return frozenset(out)


def _owner_wildcard(owner: str) -> str:
    return f"{owner}/{WILDCARD}"


def repo_allowed(
    repo: str | None, patterns: Collection[str], *, fork: bool | None
) -> bool:
    """Whether ``repo`` (``owner/name``) is on the allowlist ``patterns``.

    Case-insensitive. An exact ``owner/repo`` entry always matches. An
    ``owner/*`` entry matches any repository of that owner that is explicitly
    *not* a GitHub fork (``fork is False``): a fork, or a repository whose
    fork status is unknown, needs its exact entry. Invalid entries never
    match, and an empty allowlist matches nothing. Fails closed.
    """
    if not isinstance(repo, str):
        return False
    name = repo.lower()
    if not _is_repo(name):
        return False
    entries = {p.lower() for p in patterns if isinstance(p, str)}
    if name in entries:
        return True
    owner = name.split("/", 1)[0]
    return fork is False and _owner_wildcard(owner) in entries


def patterns_cover(subset: Collection[str], superset: Collection[str]) -> bool:
    """Every entry of ``subset`` is admitted by ``superset``: an exact entry
    by the same entry or its owner's wildcard, a wildcard only by the same
    wildcard. An empty ``superset`` covers nothing (only an empty subset)."""
    sup = {p.lower() for p in superset}
    for entry in (p.lower() for p in subset):
        if entry in sup:
            continue
        if _is_repo(entry) and _owner_wildcard(entry.split("/", 1)[0]) in sup:
            continue
        return False
    return True


def _name(obj: Any) -> str | None:
    if not isinstance(obj, Mapping):
        return None
    name = obj.get("full_name")
    return name.lower() if isinstance(name, str) and "/" in name else None


def _branch_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if any(ch in value for ch in "\n\r\x00"):
        return None
    return value


def trusted_schedule(source: Mapping[str, Any], ref: str | None) -> bool:
    """A public ``schedule`` event is the default-branch workflow.

    GitHub sets ``GITHUB_REF`` to ``refs/heads/<default_branch>`` and puts
    ``default_branch`` on the repository object. Anything else is refused:
    a missing branch, a missing ref, or a ref for some other branch.
    """
    default = _branch_name(source.get("default_branch"))
    if default is None or not isinstance(ref, str):
        return False
    return ref == f"refs/heads/{default}"


def refusal(
    event_name: str | None,
    repository: str | None,
    payload: Any,
    allowed: Collection[str],
    *,
    ref: str | None = None,
) -> str | None:
    """``None`` when the job may run, else why not. Pure; fails closed."""
    repos = {r.lower() for r in allowed if isinstance(r, str) and is_repo_pattern(r)}
    if not repos:
        return NO_ALLOWLIST
    if not event_name or not repository:
        return CONTEXT_MISSING
    repo = repository.lower()
    if not repo_allowed(repo, repos, fork=False):  # name only; fork checked below
        return REPO_NOT_ALLOWED
    if not isinstance(payload, Mapping):
        return EVENT_UNREADABLE
    source = payload.get("repository")
    if _name(source) != repo:
        return REPO_MISMATCH
    assert isinstance(source, Mapping)
    fork_flag = source.get("fork")
    if not repo_allowed(
        repo, repos, fork=fork_flag if isinstance(fork_flag, bool) else None
    ):
        return FORK_NOT_LISTED
    if event_name == "pull_request_target":
        return PULL_REQUEST_TARGET
    if source.get("private") is not True and event_name not in ALLOWED_PUBLIC_EVENTS:
        return EVENT_NOT_ALLOWED
    if (
        event_name == "schedule"
        and source.get("private") is not True
        and not trusted_schedule(source, ref)
    ):
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
    return {"version": POLICY_VERSION, "repos": sorted(parse_repo_patterns(repos))}


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
    try:
        return parse_repo_patterns(repos)
    except RepoPatternError:
        return frozenset()  # one bad entry voids the whole policy


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
            ref=environ.get("GITHUB_REF"),
        )
    except Exception:  # fail closed on anything unexpected
        reason = EVENT_UNREADABLE
    if reason is not None:
        print(
            f"::error::omega-ci job guard refused this job ({reason}). "
            "This self-hosted pool serves allowlisted repositories only (an "
            "owner/* entry skips repositories that are forks), never "
            "pull_request_target or pull requests from forks.",
            file=sys.stderr,
        )
        return 1
    print("omega-ci job guard: ok", file=sys.stderr)
    add_user_paths(environ)
    return 0


#: Per-job bin dirs under the runner's own HOME (its private work dir). The
#: image's tool trees are read-only, so ``pip install`` on a tool-cache Python
#: falls back to ``--user`` (scripts in ~/.local/bin) and ``cargo install``
#: writes to ~/.cargo/bin. GitHub-hosted runners have them on PATH; so do we.
USER_BIN_DIRS = (".local/bin", ".cargo/bin")


def add_user_paths(environ: Mapping[str, str]) -> bool:
    """Append the job's per-HOME bin dirs to ``GITHUB_PATH`` (best effort).

    Runs only after the guard allowed the job. Never fails the job: the guard
    verdict is what matters, and a missing PATH entry only affects tools a
    job installs itself.
    """
    home, target = environ.get("HOME", ""), environ.get("GITHUB_PATH", "")
    if not (home.startswith("/") and target.startswith("/")):
        return False
    try:
        with open(target, "a", encoding="utf-8") as fh:
            for rel in USER_BIN_DIRS:
                fh.write(f"{home.rstrip('/')}/{rel}\n")
    except OSError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ALLOWED_PUBLIC_EVENTS",
    "CONTEXT_MISSING",
    "EVENT_NOT_ALLOWED",
    "EVENT_UNREADABLE",
    "FORK",
    "FORK_NOT_LISTED",
    "HEAD_UNKNOWN",
    "NO_ALLOWLIST",
    "PULL_REQUEST_TARGET",
    "REPO_MISMATCH",
    "REPO_NOT_ALLOWED",
    "USER_BIN_DIRS",
    "WILDCARD",
    "RepoPatternError",
    "add_user_paths",
    "is_repo_pattern",
    "load_policy",
    "main",
    "parse_repo_patterns",
    "patterns_cover",
    "policy_document",
    "refusal",
    "repo_allowed",
    "trusted_schedule",
]

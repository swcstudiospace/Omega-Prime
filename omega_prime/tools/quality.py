"""Quality pack: desk quality tools as an Omega Prime registry family.

Ports `services/desk-gateway/src/desk_gateway/tools/quality.py`: gate runs,
Greptile reviews, receipt approvals, waivers, contract acks, supply-chain
checks, and secret scans. Approvals and waivers are stamped and written
under the root for the operator to commit — Omega Prime never pushes. Greptile,
the command runner, and VCS are injected seams; tests use fakes only.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from omega_prime.credentials.redact import redact_text
from omega_prime.receipts import (
    ReceiptError,
    append_execution,
    command_digest,
    load_executions,
    validate_receipt,
)
from omega_prime.sandbox.policy_gen import digest_file
from omega_prime.sandbox.runner import SandboxRunner, SandboxUnavailable
from omega_prime.tools.registry import ToolRegistry

QUALITY_TOOL_NAMES = (
    "qua_gates_run",
    "qua_greptile_review",
    "qua_receipt_approve",
    "qua_waiver_record",
    "qua_contract_ack",
    "qua_contract_ack_status",
    "qua_supply_chain_check",
    "qua_secret_scan",
)

LOCKFILES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "Package.resolved",
    "gradle.lockfile",
    "requirements.txt",
    "deno.lock",
)
MANIFESTS = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "Package.swift",
    "build.gradle.kts",
    "build.gradle",
    "deno.json",
)


def _error(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"error": f"{code}: {reason}", **extra}


def _default_run(
    argv: list[str], timeout: float = 120, cwd: str | None = None
) -> dict[str, Any]:
    try:
        run = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": 127, "stdout": "", "stderr": str(exc)}
    return {"exit_code": run.returncode, "stdout": run.stdout, "stderr": run.stderr}


def _coerce_suites(suites: Any) -> list[tuple[str, list[str], int | float]]:
    """Normalize ``[{name, argv, timeout}]`` into runner tuples."""
    if not isinstance(suites, list) or not suites:
        raise ValueError("suites must be a non-empty list of {name, argv, timeout}")
    selected: list[tuple[str, list[str], int | float]] = []
    for index, item in enumerate(suites):
        if not isinstance(item, dict):
            raise ValueError(f"suites[{index}] must be an object")
        name = item.get("name")
        argv = item.get("argv")
        timeout = item.get("timeout", 120)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"suites[{index}] needs a name")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(part, str) for part in argv)
        ):
            raise ValueError(f"suites[{index}] needs an argv of strings")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or timeout <= 0
        ):
            raise ValueError(f"suites[{index}] timeout must be a positive number")
        selected.append((name, list(argv), timeout))
    return selected


def _omega_suites() -> list[tuple[str, list[str], int | float]]:
    """The three Omega Prime commands today's tree already runs."""
    return [
        ("suite", ["python3", "-m", "pytest", "omega_prime/tests", "-q"], 600),
        (
            "evals",
            [
                "python3",
                "-m",
                "omega_prime.evals.runner",
                "omega_prime/evals/cases",
            ],
            300,
        ),
        (
            "assemble",
            ["bash", "omega_prime/scripts/assemble-prompts.sh", "--check"],
            120,
        ),
    ]


def _discover_suites(
    root: str, suites: list | None
) -> list[tuple[str, list[str], int | float]]:
    """Pick suites: argument, desk file, Omega tree, then a pytest repo."""
    if suites is not None:
        return _coerce_suites(suites)
    base = Path(root)
    declared = base / "omega-desk-gates.json"
    if declared.is_file():
        return _coerce_suites(json.loads(declared.read_text(encoding="utf-8")))
    if (base / "omega_prime" / "tests").exists():
        return _omega_suites()
    if (base / "pyproject.toml").is_file() or (base / "tests").exists():
        return [("pytest", ["python3", "-m", "pytest", "-q"], 600)]
    raise ValueError("no suites configured for this repo")


def _git_vcs(root: str):
    def diff_names(base: str, head: str) -> list[str]:
        run = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return run.stdout.splitlines() if run.returncode == 0 else []

    def diff(base: str, head: str, paths: list[str]) -> str:
        run = subprocess.run(
            ["git", "diff", f"{base}...{head}", "--", *paths],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return run.stdout if run.returncode == 0 else ""

    return {"diff_names": diff_names, "diff": diff}


SANDBOX_SCOPES = ("gates", "gates+execute")
DEFAULT_SANDBOX_POLICY = "omega_prime/hosting/openshell/sandbox-policy.yaml"


def _sandbox_cfg(ctx: QualityContext) -> dict[str, Any] | None:
    """Sandbox routing for gates_run. None means disabled (today's path).

    Enabled only for an explicit ``{"enabled": True}`` node with a scope
    in {gates, gates+execute} (both cover verification gates; execute-path
    sandboxing stays out of scope). Anything else — including a bogus
    scope — disables sandboxing rather than silently enabling it.
    """
    sandbox = getattr(ctx, "sandbox", None)
    if not isinstance(sandbox, dict) or sandbox.get("enabled") is not True:
        return None
    scope = sandbox.get("scope", "gates")
    if scope not in SANDBOX_SCOPES:
        return None
    policy = sandbox.get("policy") or DEFAULT_SANDBOX_POLICY
    return {"policy": policy, "scope": scope}


def _expected_policy_digest(ctx: QualityContext) -> str | None:
    """Policy digest for the receipt_approve cross-check (None when off)."""
    cfg = _sandbox_cfg(ctx)
    if cfg is None:
        return None
    try:
        return digest_file(cfg["policy"])
    except OSError:
        return None


class AckStore:
    """JSON contract-acknowledgement ledger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.acks: dict[str, dict] = {}
        self._load()

    def record(self, change_id: str, bot: str, ack: bool, note: str) -> dict:
        """Append one acknowledgement and return the change entry."""
        entry = self.acks.setdefault(change_id, {"acknowledgements": []})
        entry["acknowledgements"].append({"bot": bot, "ack": ack, "note": note})
        self._save()
        return entry

    def _load(self) -> None:
        if not self.path.exists():
            self.acks = {}
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        acks = data.get("acks") if isinstance(data, dict) else None
        self.acks = acks if isinstance(acks, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"acks": self.acks}, ensure_ascii=False, indent=2) + "\n"
        self.path.write_text(text, encoding="utf-8")


@dataclass
class QualityContext:
    """Everything the quality tools need. Clients default to unconfigured."""

    root: str | Path = "."
    run: Any = None
    greptile: Any = None
    acks: AckStore | None = None
    vcs: Any = None
    repo_slug: str = "swcstudiospace/omega-prime"
    bot_id: str = "bot-00-omega-prime"
    # Sandbox routing (Phase 83, SAND-02). None/empty means disabled: gates
    # run exactly as today. Enable with {"enabled": True, "policy": <path>,
    # "scope": "gates"} (the config sandbox node shape).
    sandbox: Any = None
    # Transport for sandboxed runs: callable(argv, timeout, cwd). Defaults
    # to direct subprocess execution when sandboxing is enabled.
    sandbox_transport: Any = None


class QualityClient:
    """Desk quality tools bound to one context."""

    def __init__(self, ctx: QualityContext) -> None:
        self.ctx = ctx

    def gates_run(
        self, repo: str | None = None, suites: list | None = None
    ) -> dict[str, Any]:
        """Run the target repo's suites and report each runner's exit code."""
        run = self.ctx.run or _default_run
        root = str(self.ctx.root if repo is None else repo)
        selected = _discover_suites(root, suites)
        sandbox_cfg = _sandbox_cfg(self.ctx)
        gates: dict[str, Any] = {}
        for name, argv, timeout in selected:
            if sandbox_cfg is not None:
                gates[name] = self._sandboxed_gate(
                    list(argv), timeout, root, sandbox_cfg
                )
                continue
            try:
                result = (
                    run(argv, timeout, root) if _takes_cwd(run) else run(argv, timeout)
                )
            except TypeError:
                result = run(argv, timeout)
            if not isinstance(result, dict):
                result = {"exit_code": 127, "stdout": "", "stderr": "no object"}
            gates[name] = {
                "exit_code": result.get("exit_code", 127),
                "output": ((result.get("stdout") or "") + (result.get("stderr") or ""))[
                    -2500:
                ],
            }
        return {"ok": all(g["exit_code"] == 0 for g in gates.values()), "gates": gates}

    def _sandboxed_gate(
        self, argv: list[str], timeout: int | float, root: str, cfg: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one gate argv inside a sandbox; degrade explicitly when absent.

        Success entries carry the additive receipt binding (argv,
        argv_sha256, sandbox block) and append the execution to the command
        log. Enabled-but-absent entries carry ``degraded`` naming
        ``sandbox_unavailable: <cause>``, report ``ok False`` upstream via
        exit 127, and never execute the argv.
        """
        record = list(argv)
        argv_sha256 = command_digest(record)
        try:
            policy_sha256: str | None = digest_file(cfg["policy"])
        except OSError:
            policy_sha256 = None
        runner = SandboxRunner(cfg["policy"], transport=self.ctx.sandbox_transport)
        ok, cause = runner.available()
        if not ok:
            return {
                "exit_code": 127,
                "output": "",
                "argv": record,
                "argv_sha256": argv_sha256,
                "sandbox": {
                    "enabled": True,
                    "policy_sha256": policy_sha256,
                    "degraded": f"sandbox_unavailable: {cause}",
                },
            }
        try:
            result = runner.run(record, timeout, root)
        except SandboxUnavailable as exc:
            return {
                "exit_code": 127,
                "output": "",
                "argv": record,
                "argv_sha256": argv_sha256,
                "sandbox": {
                    "enabled": True,
                    "policy_sha256": policy_sha256,
                    "degraded": str(exc),
                },
            }
        append_execution(
            record,
            result.exit_code,
            result.output,
            cwd=root,
            policy_sha256=result.policy_sha256,
        )
        return {
            "exit_code": result.exit_code,
            "output": result.output,
            "argv": record,
            "argv_sha256": result.argv_sha256,
            "sandbox": {
                "enabled": True,
                "policy_sha256": result.policy_sha256,
                "degraded": None,
            },
        }

    def greptile_review(
        self,
        action: str,
        pr_number: int | None = None,
        review_id: str | None = None,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Trigger, get, or list comments of a Greptile review."""
        if action not in ("trigger", "get", "comments"):
            raise ValueError("action must be trigger, get, or comments")
        if self.ctx.greptile is None:
            return _error(
                "not_configured",
                "Greptile is not configured; merge claim stays blocked",
            )
        target = repo or self.ctx.repo_slug
        if action == "trigger":
            if not isinstance(pr_number, int) or isinstance(pr_number, bool):
                raise ValueError("trigger needs an integer pr_number")
            result = self.ctx.greptile.trigger(target, pr_number)
            note = "a successful trigger means the review was queued, not that analysis finished"
        elif action == "get":
            if not review_id:
                return _error("invalid_args", "get needs review_id")
            result = self.ctx.greptile.get(review_id)
            note = None
        else:
            if not isinstance(pr_number, int) or isinstance(pr_number, bool):
                raise ValueError("comments needs an integer pr_number")
            result = self.ctx.greptile.comments(target, pr_number)
            note = "comments with addressed=false block the merge claim"
        if not isinstance(result, dict) or result.get("error"):
            reason = (
                result.get("reason", "greptile failed")
                if isinstance(result, dict)
                else "greptile failed"
            )
            code = (
                result.get("error", "upstream_error")
                if isinstance(result, dict)
                else "upstream_error"
            )
            return _error(str(code), str(reason))
        return {
            "ok": True,
            "action": action,
            "result": result.get("body"),
            "note": note,
        }

    def receipt_approve(
        self, receipt_path: str, note: str | None = None, approver: str | None = None
    ) -> dict[str, Any]:
        """Validate against the command log, stamp, and write one receipt. No push."""
        if not _repo_relative(receipt_path):
            return _error("invalid_args", "receipt_path must be repo-relative")
        target = Path(self.ctx.root) / receipt_path
        if not target.is_file():
            return _error("not_found", f"{receipt_path} is not under this root")
        try:
            receipt = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            return _error("invalid_receipt", "receipt is not valid JSON")
        if not receipt.get("bot"):
            return _error("invalid_receipt", "receipt names no authoring bot")
        if approver is not None and not isinstance(approver, str):
            return _error("invalid_args", "approver must be a string")
        identity = approver.strip() if isinstance(approver, str) else ""
        if not identity:
            if receipt.get("bot") == self.ctx.bot_id:
                return _error(
                    "self_approval",
                    "QUALITY cannot approve a QUALITY receipt",
                )
            stamp = self.ctx.bot_id
        elif identity == receipt.get("bot") or identity == self.ctx.bot_id:
            return _error("self_approval", "QUALITY cannot approve a QUALITY receipt")
        else:
            stamp = identity
        try:
            validate_receipt(
                receipt,
                executions=load_executions(),
                expected_policy_sha256=_expected_policy_digest(self.ctx),
            )
        except ReceiptError as exc:
            return _error(
                "gate_failed",
                "the receipt does not pass before stamping",
                problems=str(exc).splitlines(),
            )
        receipt["approved_by"] = stamp
        receipt["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if note:
            receipt["approval_note"] = redact_text(note)
        target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "receipt_path": receipt_path,
            "approved_by": stamp,
            "pushed": False,
            "reason": "Omega Prime never pushes; the operator commits this stamp",
        }

    def waiver_record(
        self, pr_number: int, comment_ids: list[str], reason: str, branch: str = "main"
    ) -> dict[str, Any]:
        """Write a Greptile waiver record under root. No push."""
        if isinstance(pr_number, bool) or not isinstance(pr_number, int):
            raise ValueError("pr_number must be an integer")
        if not isinstance(comment_ids, list) or not comment_ids:
            raise ValueError("comment_ids must be a non-empty list")
        name = (
            f".receipts/quality/greptile-waiver-pr{pr_number}-{int(time.time())}.json"
        )
        waiver = {
            "task_id": f"greptile-waiver-pr{pr_number}",
            "bot": self.ctx.bot_id,
            "pr_number": pr_number,
            "waived_comment_ids": list(comment_ids),
            "reason": redact_text(reason),
            "branch": branch,
            "unverified": [
                "waiver recorded locally; Greptile status at the branch tip was not re-fetched here"
            ],
            "approved_by": "",
        }
        target = Path(self.ctx.root) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(waiver, indent=2) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "receipt_path": name,
            "pushed": False,
            "reason": "Omega Prime never pushes; the operator commits this waiver",
        }

    def contract_ack(self, change_id: str, ack: bool, note: str) -> dict[str, Any]:
        """Record one consumer ack. Rejections must name the blocker."""
        if not isinstance(change_id, str) or change_id == "":
            raise ValueError("change_id must be a non-empty string")
        if not ack and len(note or "") < 20:
            return _error(
                "invalid_args",
                "a rejection must name the blocker (note of at least 20 characters)",
            )
        if self.ctx.acks is None:
            return _error("not_configured", "ack store is missing")
        entry = self.ctx.acks.record(
            change_id, self.ctx.bot_id, bool(ack), redact_text(note or "")
        )
        return {
            "ok": True,
            "change_id": change_id,
            "acknowledgements": entry["acknowledgements"],
        }

    def contract_ack_status(self, change_id: str) -> dict[str, Any]:
        """Ack coverage for one change against its required consumers."""
        if not isinstance(change_id, str) or change_id == "":
            raise ValueError("change_id must be a non-empty string")
        entry = (self.ctx.acks.acks.get(change_id) if self.ctx.acks else None) or {
            "acknowledgements": []
        }
        required: list[str] = []
        for suffix in (".yaml", ".yml", ".json"):
            proposal = (
                Path(self.ctx.root) / "contracts" / "changes" / f"{change_id}{suffix}"
            )
            if proposal.is_file():
                try:
                    text = proposal.read_text(encoding="utf-8")
                except OSError:
                    break
                if suffix == ".json":
                    try:
                        required = list(
                            (json.loads(text) or {}).get("consumers_required") or []
                        )
                    except ValueError:
                        required = []
                else:
                    required = _yaml_consumers(text)
                break
        acked = {a["bot"] for a in entry["acknowledgements"] if a.get("ack")}
        rejected = [a for a in entry["acknowledgements"] if not a.get("ack")]
        return {
            "change_id": change_id,
            "consumers_required": required,
            "acknowledged": sorted(acked),
            "missing": sorted(set(required) - acked),
            "rejected": rejected,
            "complete": bool(required) and set(required) <= acked and not rejected,
        }

    def supply_chain_check(self, base_ref: str, head_ref: str) -> dict[str, Any]:
        """Lockfile/manifest changes with unpinned and git-dependency flags."""
        vcs = self.ctx.vcs or _git_vcs(str(self.ctx.root))
        names = vcs["diff_names"](base_ref, head_ref)
        lockfiles = [n for n in names if n.split("/")[-1] in LOCKFILES]
        manifests = [n for n in names if n.split("/")[-1] in MANIFESTS]
        diff = (
            vcs["diff"](base_ref, head_ref, lockfiles + manifests)
            if (lockfiles or manifests)
            else ""
        )
        added = [
            line[1:]
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        unpinned = [
            line.strip()
            for line in added
            if any(tok in line for tok in ('"latest"', "@latest", ": latest", "*"))
        ][:20]
        git_deps = [
            line.strip() for line in added if "git+" in line or "github:" in line
        ][:20]
        return {
            "lockfiles_changed": lockfiles,
            "manifests_changed": manifests,
            "unpinned": unpinned,
            "git_dependencies": git_deps,
            "verdict": "review" if (lockfiles or manifests) else "no dependency change",
            "unverified": [
                "licence and CVE lookup is not wired; run the security-supply-chain skill's audit commands"
            ],
        }

    def secret_scan(self, paths: list[str] | None = None) -> dict[str, Any]:
        """Secret-shape scan of given files (default: tree minus generated dirs, max 2000)."""
        root = Path(self.ctx.root)
        truncated = False
        if paths is None:
            candidates = [
                p
                for p in sorted(root.rglob("*"))
                if p.is_file() and not _generated(p.relative_to(root))
            ]
            truncated = len(candidates) > 2000
            targets = [str(p.relative_to(root)) for p in candidates[:2000]]
        else:
            if not isinstance(paths, list) or any(not _repo_relative(p) for p in paths):
                return _error("invalid_args", "paths must be repo-relative")
            targets = [p for p in paths if (root / p).is_file()]
        findings: list[dict[str, Any]] = []
        for rel in targets:
            try:
                text = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if redact_text(line) != line:
                    findings.append({"path": rel, "line": number})
                    if len(findings) >= 50:
                        break
            if len(findings) >= 50:
                break
        outcome: dict[str, Any] = {
            "ok": not findings and not truncated,
            "gate": "G-3",
            "files": len(targets),
            "findings": findings,
            "truncated": truncated,
        }
        if truncated:
            outcome["note"] = (
                "default scan skips generated dirs and stops at 2000 files; "
                "pass paths to cover the rest"
            )
        return outcome


def _yaml_consumers(text: str) -> list[str]:
    """`consumers_required` block list from a fixed-shape proposal.

    No PyYAML in Omega Prime: this reads only the `  - item` block the
    systems emitter writes (plain or double-quoted scalars).
    """
    consumers: list[str] = []
    in_block = False
    for line in text.splitlines():
        if not in_block:
            if line.strip() == "consumers_required:":
                in_block = True
            continue
        stripped = line.strip()
        if stripped == "":
            continue
        if not stripped.startswith("- "):
            break
        item = stripped[2:].strip()
        if len(item) >= 2 and item.startswith('"') and item.endswith('"'):
            with contextlib.suppress(ValueError):
                item = json.loads(item)
        consumers.append(item)
    return consumers


def _takes_cwd(run: Any) -> bool:
    try:
        import inspect

        return len(inspect.signature(run).parameters) >= 3
    except (TypeError, ValueError):
        return False


_GENERATED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".eggs",
    }
)


def _generated(relative: Path) -> bool:
    """True when a repo-relative path lives under a generated directory."""
    return any(
        part in _GENERATED_PARTS or part.endswith(".egg-info")
        for part in relative.parts
    )


def _repo_relative(path: Any) -> bool:
    if not isinstance(path, str) or path == "":
        return False
    candidate = PurePosixPath(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def register_quality_tools(registry: ToolRegistry, client: QualityClient) -> list[str]:
    """Register the 8 quality tools. Greptile/approve/waiver/ack need approval."""

    def _wrap(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}

    handlers: dict[str, Callable[..., Any]] = {
        "qua_gates_run": lambda repo=None, suites=None: _wrap(
            client.gates_run, repo=repo, suites=suites
        ),
        "qua_greptile_review": lambda action, pr_number=None, review_id=None, repo=None: (
            _wrap(
                client.greptile_review,
                action,
                pr_number=pr_number,
                review_id=review_id,
                repo=repo,
            )
        ),
        "qua_receipt_approve": lambda receipt_path, note=None, approver=None: _wrap(
            client.receipt_approve, receipt_path, note=note, approver=approver
        ),
        "qua_waiver_record": lambda pr_number, comment_ids, reason, branch="main": (
            _wrap(client.waiver_record, pr_number, comment_ids, reason, branch=branch)
        ),
        "qua_contract_ack": lambda change_id, ack, note: _wrap(
            client.contract_ack, change_id, ack, note
        ),
        "qua_contract_ack_status": lambda change_id: _wrap(
            client.contract_ack_status, change_id
        ),
        "qua_supply_chain_check": lambda base_ref, head_ref: _wrap(
            client.supply_chain_check, base_ref, head_ref
        ),
        "qua_secret_scan": lambda paths=None: _wrap(client.secret_scan, paths),
    }
    if set(handlers) != set(QUALITY_TOOL_NAMES):
        raise RuntimeError("Quality tool handlers drifted from QUALITY_TOOL_NAMES")
    approvals = {
        "qua_greptile_review",
        "qua_receipt_approve",
        "qua_waiver_record",
        "qua_contract_ack",
    }
    for name in QUALITY_TOOL_NAMES:
        description, parameters = _SCHEMAS[name]
        registry.register(
            name,
            description,
            parameters,
            handlers[name],
            requires_approval=(name in approvals),
        )
    return list(QUALITY_TOOL_NAMES)


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "qua_gates_run": (
        "Run the target repo's gates and report each suite's real exit code. Read-only.",
        _object(
            {
                "repo": _string(
                    "Repository root to test. Defaults to the quality work root."
                ),
                "suites": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "argv": {"type": "array", "items": {"type": "string"}},
                            "timeout": {"type": "number"},
                        },
                        "required": ["name", "argv"],
                    },
                    "description": "Suites to run. Each item is {name, argv, timeout}.",
                },
            },
            [],
        ),
    ),
    "qua_greptile_review": (
        "Trigger, get, or list Greptile review comments. Requires approval.",
        _object(
            {
                "action": _string("trigger, get, or comments."),
                "pr_number": {"type": "integer"},
                "review_id": _string("Review id."),
                "repo": _string("Repo slug."),
            },
            ["action"],
        ),
    ),
    "qua_receipt_approve": (
        "Validate, stamp, and write one receipt. Requires approval.",
        _object(
            {
                "receipt_path": _string("Repo-relative path."),
                "note": _string("Approval note."),
                "approver": _string(
                    "Operator identity that stamps the receipt. "
                    "The authoring bot and this seat's bot id are refused."
                ),
            },
            ["receipt_path"],
        ),
    ),
    "qua_waiver_record": (
        "Write a Greptile waiver record. Requires approval.",
        _object(
            {
                "pr_number": {"type": "integer"},
                "comment_ids": {"type": "array", "items": {"type": "string"}},
                "reason": _string("Waiver reason."),
                "branch": _string("Branch."),
            },
            ["pr_number", "comment_ids", "reason"],
        ),
    ),
    "qua_contract_ack": (
        "Record one consumer ack. Requires approval.",
        _object(
            {
                "change_id": _string("Change id."),
                "ack": {"type": "boolean"},
                "note": _string("Note (20+ chars when rejecting)."),
            },
            ["change_id", "ack", "note"],
        ),
    ),
    "qua_contract_ack_status": (
        "Ack coverage for one change. Read-only.",
        _object({"change_id": _string("Change id.")}, ["change_id"]),
    ),
    "qua_supply_chain_check": (
        "Lockfile/manifest change flags. Read-only.",
        _object(
            {"base_ref": _string("Base ref."), "head_ref": _string("Head ref.")},
            ["base_ref", "head_ref"],
        ),
    ),
    "qua_secret_scan": (
        "Secret-shape scan of files. Read-only.",
        _object({"paths": {"type": "array", "items": {"type": "string"}}}, []),
    ),
}


__all__ = [
    "QUALITY_TOOL_NAMES",
    "AckStore",
    "QualityClient",
    "QualityContext",
    "register_quality_tools",
]

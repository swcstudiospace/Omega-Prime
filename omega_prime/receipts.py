"""Verification receipts for the Omega Prime seat.

A completion claim cites a command and its exit code. A destructive operation
also cites a recorded approval. Captured executions live outside the source
tree; a receipt checked against that log must cite commands that actually ran.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

BYPASS_MARKERS = ("--no-verify", "--skip-checks", "|| true")


class ReceiptError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def command_log_path() -> Path:
    """JSONL execution log: explicit path, else state dir, else the home dir.

    A candidate inside this repository is skipped. The log is never written
    into the source tree.
    """
    repo = Path(__file__).resolve().parents[1]
    candidates: list[Path] = []
    explicit = os.environ.get("OMEGA_PRIME_COMMAND_LOG", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    state = os.environ.get("OMEGA_PRIME_STATE_DIR", "").strip()
    if state:
        candidates.append(Path(state) / "command-log.jsonl")
    candidates.append(Path.home() / ".omega-prime" / "command-log.jsonl")
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved == repo or repo in resolved.parents:
            continue
        return resolved
    raise OSError("refusing to write the command log inside the repo tree")


def command_digest(cmd: Any) -> str:
    """Canonical SHA-256 hex of one cited command (argv-tamper binding).

    The digest runs over the JSON encoding of the command exactly as
    recorded, so a list argv and its joined-string form digest
    differently — callers must cite the recorded shape verbatim.
    """
    return hashlib.sha256(
        json.dumps(cmd, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def append_execution(
    cmd: Any,
    exit_code: int,
    output_tail: str,
    *,
    cwd: str,
    policy_sha256: str | None = None,
) -> None:
    """Append one captured command as a single JSON line.

    ``policy_sha256`` is the digest of the sandbox policy file the command
    ran under; recorded only for sandboxed executions (flag-off records
    keep today's exact shape).
    """
    path = command_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "cmd": cmd,
        "exit_code": exit_code,
        "output_tail": output_tail,
        "cwd": str(cwd),
    }
    if policy_sha256 is not None:
        record["policy_sha256"] = policy_sha256
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_executions() -> list[Any]:
    """Read the command log. A missing file is an empty capture list."""
    path = command_log_path()
    if not path.is_file():
        return []
    rows: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            rows.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return rows


def _exit_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    return left == right


def _check_sandbox_binding(
    errors: list[str],
    index: int,
    command: dict,
    matched_record: dict | None,
    expected_policy_sha256: str | None,
) -> None:
    """Tamper-evidence for one sandboxed receipt command (additive).

    Commands without sandbox fields pass untouched. A cited ``argv_sha256``
    must equal the digest of the cited ``cmd``; a cited
    ``sandbox_policy_sha256`` must equal the caller's expected digest and,
    when a captured record matched, the record's ``policy_sha256``.
    """
    cited_argv = command.get("argv_sha256")
    cited_policy = command.get("sandbox_policy_sha256")
    if cited_argv is None and cited_policy is None:
        return
    if cited_argv is not None and (
        not isinstance(cited_argv, str)
        or command_digest(command.get("cmd")) != cited_argv
    ):
        errors.append(f"commands[{index}] argv_sha256 does not match the cited cmd")
    if cited_policy is not None:
        if not isinstance(cited_policy, str):
            errors.append(
                f"commands[{index}] sandbox_policy_sha256 is not a digest string"
            )
        else:
            if (
                expected_policy_sha256 is not None
                and cited_policy != expected_policy_sha256
            ):
                errors.append(
                    f"commands[{index}] sandbox_policy_sha256 "
                    "does not match the expected policy"
                )
            if matched_record is not None:
                captured_policy = matched_record.get("policy_sha256")
                if not isinstance(captured_policy, str):
                    errors.append(
                        f"commands[{index}] cites a sandbox binding "
                        "with no captured policy digest"
                    )
                elif cited_policy != captured_policy:
                    errors.append(
                        f"commands[{index}] sandbox_policy_sha256 "
                        "does not match the captured execution"
                    )


def _select_policy_bound(
    candidates: list[dict], expected_policy_sha256: str | None
) -> dict | None:
    """Pick the captured record a receipt command binds to.

    Without an expected policy digest the first same command+exit record
    wins (historical behavior). With one, records whose ``policy_sha256``
    equals the expected digest win; digest-less records are only a
    fallback when no policy-bound record exists, and records bound to a
    *different* digest never match — an old run under policy A must not
    approve a receipt expecting policy B.
    """
    if not candidates:
        return None
    if expected_policy_sha256 is None:
        return candidates[0]
    for record in candidates:
        if record.get("policy_sha256") == expected_policy_sha256:
            return record
    for record in candidates:
        if "policy_sha256" not in record or record.get("policy_sha256") is None:
            return record
    return None


def validate_receipt(
    receipt: dict,
    executions: list | None = None,
    expected_policy_sha256: str | None = None,
) -> None:
    """Raise ReceiptError listing every problem. Return when the receipt holds.

    ``executions=None`` keeps the structural checks only. A list requires every
    ``commands[]`` entry to equal a captured record on both ``cmd`` and
    ``exit_code``. ``expects_failure`` is still judged on the receipt's own
    exit code.

    Sandbox binding (additive): a command citing ``argv_sha256`` must digest
    to it over the cited ``cmd`` exactly as recorded, or verification
    rejects it naming the command index. A command citing
    ``sandbox_policy_sha256`` must equal ``expected_policy_sha256`` when
    the caller passes one, and must equal the matched captured record's
    ``policy_sha256`` when executions are provided. Receipts without
    sandbox fields validate exactly as before.
    """
    errors: list[str] = []
    commands = receipt.get("commands")
    if not isinstance(commands, list):
        errors.append("commands must be a list")
        commands = []
    claims = receipt.get("claims")
    if not isinstance(claims, list):
        errors.append("claims must be a list")
        claims = []
    if not isinstance(receipt.get("unverified"), list):
        errors.append("unverified must be a list")

    for index, claim in enumerate(claims):
        if not isinstance(claim, dict) or not str(claim.get("claim", "")).strip():
            errors.append(f"claims[{index}] missing claim text")
            continue
        evidence = claim.get("evidence_command_index")
        if isinstance(evidence, bool) or not isinstance(evidence, int):
            errors.append(f"claims[{index}] has no command")
            continue
        if evidence < 0 or evidence >= len(commands):
            errors.append(f"claims[{index}] has no command")
            continue
        command = commands[evidence]
        if not isinstance(command, dict):
            errors.append(f"claims[{index}] cites a command that is not an object")
            continue
        if not str(command.get("cmd", "")).strip() or "exit_code" not in command:
            errors.append(f"claims[{index}] cites a command with no cmd or exit_code")
            continue
        exit_code = command["exit_code"]
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            errors.append(f"claims[{index}] cites a command with no exit_code")
            continue
        expects_failure = bool(claim.get("expects_failure", False))
        if expects_failure and exit_code == 0:
            errors.append(f"claims[{index}] expects_failure on a passing command")
        if not expects_failure and exit_code != 0:
            errors.append(f"claims[{index}] cites a failing command")
        blob = f"{command.get('cmd', '')} {command.get('output_tail', '')}"
        if any(marker in blob for marker in BYPASS_MARKERS):
            errors.append(f"claims[{index}] cites a bypassed command")

    if receipt.get("destructive") is True:
        approvals = receipt.get("approvals")
        if not isinstance(approvals, list) or not approvals:
            errors.append("destructive operation without recorded approval")
        else:
            for index, approval in enumerate(approvals):
                if (
                    not isinstance(approval, dict)
                    or not str(approval.get("approved_by", "")).strip()
                ):
                    errors.append(f"approvals[{index}] missing approved_by")
                elif str(approval.get("approved_by")) == str(receipt.get("bot", "")):
                    errors.append(f"approvals[{index}] approved_by is the bot itself")

    if executions is not None:
        captured = executions if isinstance(executions, list) else []
        if not isinstance(executions, list):
            errors.append("executions must be a list")
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                errors.append(f"commands[{index}] was not captured")
                continue
            cmd = command.get("cmd")
            exit_code = command.get("exit_code")
            cmd_candidates: list[dict] = [
                record
                for record in captured
                if isinstance(record, dict) and record.get("cmd") == cmd
            ]
            if not cmd_candidates:
                errors.append(f"commands[{index}] was not captured")
                continue
            exit_candidates: list[dict] = [
                record
                for record in cmd_candidates
                if _exit_equal(record.get("exit_code"), exit_code)
            ]
            if not exit_candidates:
                errors.append(
                    f"commands[{index}] exit_code does not match a captured execution"
                )
                continue
            matched_record = _select_policy_bound(
                exit_candidates, expected_policy_sha256
            )
            if matched_record is None:
                errors.append(
                    f"commands[{index}] matches only executions bound to "
                    "a different policy digest"
                )
                continue
            _check_sandbox_binding(
                errors, index, command, matched_record, expected_policy_sha256
            )
    else:
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                continue
            _check_sandbox_binding(errors, index, command, None, expected_policy_sha256)

    if errors:
        raise ReceiptError(errors)

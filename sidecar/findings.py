"""Purple-team sidecar findings pipeline with dedupe (GAP-12/13).

Append-only NDJSON store under ``sidecar/reports/findings.ndjson``. Each
finding is keyed by ``dedupe_hash = sha256(canonical(case_id +
normalized gateway response))``: replaying identical content appends no
new row but increments ``occurrence_count`` on the existing row, so
replays are counted without rewriting history (threat T-79-09).

:func:`export_work_items` emits file-based tracked-work-item rows under
``sidecar/reports/work-items.json`` (convertible to issues later; no
network, no external tracker API calls). :func:`check_drift` compares
``sidecar/VERSION`` and the adapter tool surface against
``contract/bot-interface.json``; any mismatch implies DEGRADED with the
disagreeing pair named — never a silent pass (threat T-79-10).

Reports record normalized response digests and verdicts; raw payload
text stays in the versioned case file (threat T-79-11).

Stdlib-only. Never imports ``omega_prime.redteam`` or ``redteam/``.
The adapter surface is read via ``ast`` parsing (no import, no side
effects) so drift detection cannot execute adapter code.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sidecar.state import reports_dir, utc_now_rfc3339  # noqa: E402

FINDINGS_FILENAME = "findings.ndjson"
WORK_ITEMS_FILENAME = "work-items.json"
FINDING_SCHEMA = "finding/1"

VERSION_FILENAME = "VERSION"
CONTRACT_FILENAME = "bot-interface.json"
ADAPTER_RELATIVE = Path("omega_prime") / "tools" / "redteam.py"
ADAPTER_CONSTANT = "REDTEAM_TOOL_NAMES"

_WHITESPACE_RE = re.compile(r"\s+")


def findings_path(directory: str | Path | None = None) -> Path:
    """Full path of the append-only findings store."""
    base = Path(directory) if directory is not None else reports_dir()
    return base / FINDINGS_FILENAME


def work_items_path(directory: str | Path | None = None) -> Path:
    """Full path of the file-based work-item export."""
    base = Path(directory) if directory is not None else reports_dir()
    return base / WORK_ITEMS_FILENAME


def normalize_response(response: str) -> str:
    """Canonical form of a gateway response for hashing.

    Strips leading/trailing whitespace and collapses every internal
    whitespace run to a single space. The normalized text (not the raw
    payload) is what binds a finding's identity.
    """
    if not isinstance(response, str):
        raise ValueError("gateway response must be a string")
    return _WHITESPACE_RE.sub(" ", response.strip())


def dedupe_hash(case_id: str, response: str) -> str:
    """Hex identity binding ``case_id`` to the normalized response.

    ``sha256(case_id + "\\x00" + normalized_response)``: the NUL separator
    keeps ``("ab", "c")`` and ``("a", "b\\x00c")``-style collisions
    distinct at the join boundary.
    """
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id must be a non-empty string")
    canonical = case_id + "\x00" + normalize_response(response)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_findings(
    directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read all findings rows (empty list when the store does not exist)."""
    path = findings_path(directory)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: corrupt findings row ({exc})") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{lineno}: corrupt findings row")
        rows.append(row)
    return rows


def _write_rows_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(path)


def record_finding(
    case_id: str,
    klass: str,
    severity: str,
    observed_detection: str,
    verdict: str,
    response: str,
    directory: str | Path | None = None,
) -> dict[str, Any]:
    """Record one finding; replay of identical content dedupes.

    Returns the stored row. A replay (same ``dedupe_hash``) appends no
    new row: it increments ``occurrence_count`` and refreshes
    ``last_seen``, preserving the original ``ts``.
    """
    for name, value in (
        ("case_id", case_id),
        ("class", klass),
        ("severity", severity),
        ("observed_detection", observed_detection),
        ("verdict", verdict),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    digest = dedupe_hash(case_id, response)
    path = findings_path(directory)
    rows = load_findings(directory)
    now = utc_now_rfc3339()
    for row in rows:
        if row.get("dedupe_hash") == digest:
            row["occurrence_count"] = int(row.get("occurrence_count", 1)) + 1
            row["last_seen"] = now
            _write_rows_atomic(path, rows)
            return row
    new_row: dict[str, Any] = {
        "case_id": case_id,
        "class": klass,
        "severity": severity,
        "observed_detection": observed_detection,
        "verdict": verdict,
        "dedupe_hash": digest,
        "occurrence_count": 1,
        "ts": now,
        "last_seen": now,
    }
    rows.append(new_row)
    _write_rows_atomic(path, rows)
    return new_row


def render_findings_jsonl(rows: list[dict[str, Any]]) -> str:
    """Render ``finding/1`` JSON Lines. Empty input is one trailing newline.

    Each object carries only ``schema``, ``case_id``, ``severity``,
    ``dedupe_hash``, and ``synced`` (true when ``issue_url`` is a
    non-empty string). Raw gateway text is never included.
    """
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("finding row must be an object")
        issue_url = row.get("issue_url")
        payload = {
            "schema": FINDING_SCHEMA,
            "case_id": row["case_id"] if isinstance(row.get("case_id"), str) else "",
            "severity": row["severity"] if isinstance(row.get("severity"), str) else "",
            "dedupe_hash": (
                row["dedupe_hash"] if isinstance(row.get("dedupe_hash"), str) else ""
            ),
            "synced": isinstance(issue_url, str) and issue_url != "",
        }
        lines.append(json.dumps(payload, sort_keys=True))
    if not lines:
        return "\n"
    return "".join(line + "\n" for line in lines)


def _write_jsonl_stream(text: str, jsonl_path: Path) -> None:
    """Write ``text`` into a FIFO or device. Refuses a swapped regular file."""
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOCTTY", 0)
    fd = os.open(jsonl_path, flags)
    try:
        opened_mode = os.fstat(fd).st_mode
        if not (
            stat.S_ISFIFO(opened_mode)
            or stat.S_ISCHR(opened_mode)
            or stat.S_ISBLK(opened_mode)
            or stat.S_ISSOCK(opened_mode)
        ):
            raise OSError("refusing to write: target is not a stream")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()


def _write_jsonl_atomic(text: str, jsonl_path: Path) -> None:
    """Atomic temp + ``os.replace``. The temp is removed if replace fails."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    parent = jsonl_path.parent
    temp_name: str | None = None
    try:
        for _attempt in range(100):
            candidate = str(parent / f".findings-{secrets.token_hex(4)}.tmp")
            try:
                fd = os.open(candidate, flags, 0o666)
            except FileExistsError:
                continue
            temp_name = candidate
            break
        else:
            raise OSError("cannot create a unique temporary file")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, jsonl_path)
        temp_name = None
    finally:
        if temp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)


def export_findings_jsonl(rows: list[dict[str, Any]], destination: Path) -> None:
    """Write ``finding/1`` JSON Lines at ``destination``.

    A missing path or a regular file is replaced atomically. A FIFO,
    character device, block device, or socket is written in place. A
    symlink leaf whose target is missing, a regular file, or a directory
    is refused. A symlink to a non-regular target is written through.
    """
    destination = Path(destination)
    try:
        leaf_mode: int | None = os.lstat(destination).st_mode
    except FileNotFoundError:
        leaf_mode = None
    direct = False
    if leaf_mode is not None and stat.S_ISLNK(leaf_mode):
        try:
            target_mode = os.stat(destination).st_mode
        except OSError:
            target_mode = None
        if (
            target_mode is None
            or stat.S_ISREG(target_mode)
            or stat.S_ISDIR(target_mode)
        ):
            raise OSError("refusing to write through a symlink")
        direct = True
    elif leaf_mode is not None and not (
        stat.S_ISREG(leaf_mode) or stat.S_ISDIR(leaf_mode)
    ):
        direct = True
    text = render_findings_jsonl(rows)
    if direct:
        _write_jsonl_stream(text, destination)
    else:
        _write_jsonl_atomic(text, destination)


def export_work_items(
    directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Export one tracked-work-item row per deduped finding to a JSON file.

    File-based only (no network, no issue-tracker calls). Export never
    mutates the findings history. Returns the exported rows.
    """
    items: list[dict[str, Any]] = []
    for row in load_findings(directory):
        items.append(
            {
                "title": (
                    f"[{row.get('severity')}] {row.get('case_id')}: "
                    f"{row.get('observed_detection')} "
                    f"({row.get('verdict')})"
                ),
                "severity": row.get("severity"),
                "case_id": row.get("case_id"),
                "dedupe_hash": row.get("dedupe_hash"),
                "occurrence_count": row.get("occurrence_count", 1),
                "observed_detection": row.get("observed_detection"),
                "verdict": row.get("verdict"),
            }
        )
    path = work_items_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return items


def _read_version(repo_root: Path) -> str:
    path = repo_root / "sidecar" / VERSION_FILENAME
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot read {path} ({exc})") from exc
    if not text:
        raise ValueError(f"{path} is empty")
    return text


def _read_contract(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "sidecar" / "contract" / CONTRACT_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path} ({exc})") from exc
    try:
        contract = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{path} is not JSON ({exc})") from exc
    if not isinstance(contract, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return contract


def _read_adapter_tool_names(repo_root: Path) -> list[str]:
    """Parse ``REDTEAM_TOOL_NAMES`` from the adapter source via ``ast``.

    The adapter is never imported: parsing cannot execute its code.
    """
    path = repo_root / ADAPTER_RELATIVE
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read adapter {path} ({exc})") from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"adapter {path} does not parse ({exc})") from exc
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == ADAPTER_CONSTANT
            and isinstance(node.value, (ast.Tuple, ast.List))
        ):
            names: list[str] = []
            for elt in node.value.elts:
                if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
                    raise ValueError(
                        f"adapter {path}: {ADAPTER_CONSTANT} holds non-string entries"
                    )
                names.append(elt.value)
            return names
    raise ValueError(f"adapter {path}: {ADAPTER_CONSTANT} not found")


def check_drift(
    repo_root: str | Path | None = None,
) -> tuple[bool, str]:
    """Compare VERSION + adapter surface against the contract baseline.

    Returns ``(True, detail)`` when everything agrees, otherwise
    ``(False, detail)`` where ``detail`` names the disagreeing pair.
    Unreadable inputs fail closed (drift) rather than passing silently.
    """
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    try:
        version = _read_version(root)
        contract = _read_contract(root)
        adapter_names = _read_adapter_tool_names(root)
    except ValueError as exc:
        return False, f"drift-check unreadable input: {exc}"
    contract_version = contract.get("harness_version")
    if version != contract_version:
        return (
            False,
            f"harness_version drift: sidecar/VERSION {version!r} != "
            f"contract/bot-interface.json harness_version "
            f"{contract_version!r}",
        )
    contract_names = contract.get("tool_names")
    if sorted(adapter_names) != sorted(contract_names or []):
        missing = sorted(set(contract_names or []) - set(adapter_names))
        extra = sorted(set(adapter_names) - set(contract_names or []))
        return (
            False,
            f"tool-surface drift: {ADAPTER_RELATIVE} "
            f"{sorted(adapter_names)} != contract/bot-interface.json "
            f"tool_names {sorted(contract_names or [])} "
            f"(missing {missing}; extra {extra})",
        )
    return (
        True,
        f"no drift: sidecar/VERSION {version!r} and "
        f"{len(adapter_names)} adapter tools agree with the contract",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``findings.py export [--reports-dir DIR]`` / ``drift-check``."""
    parser = argparse.ArgumentParser(
        description="Purple-team sidecar findings pipeline (offline)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("--reports-dir", default=None)
    sub.add_parser("drift-check")
    args = parser.parse_args(argv)
    if args.command == "export":
        items = export_work_items(args.reports_dir)
        print(f"exported {len(items)} work items")
        return 0
    ok, detail = check_drift()
    print(("OK" if ok else "DEGRADED") + f": {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "FINDING_SCHEMA",
    "check_drift",
    "dedupe_hash",
    "export_findings_jsonl",
    "export_work_items",
    "findings_path",
    "load_findings",
    "main",
    "normalize_response",
    "record_finding",
    "render_findings_jsonl",
    "work_items_path",
]

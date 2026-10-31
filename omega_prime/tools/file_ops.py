"""Rooted read, write, and unified-diff patch.

Adapted from Hermes ``tools/file_operations.py`` and ``tools/patch_parser.py``.
Paths that resolve outside the root are errors. A unified diff is applied to one
file, and a hunk whose context does not match does not write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PathError(ValueError):
    """The path is empty, or it resolves outside the workspace root."""


class PatchError(ValueError):
    """The diff is not one unified diff this applier can apply."""


class PatchMismatch(PatchError):
    """Hunk context is not present at the location the diff names."""


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class FileWorkspace:
    """File tools bound to one directory. The root is resolved once.

    ``policy`` is a seat policy (or None). When set, the three writers refuse
    a read-only path before reading or writing a byte. ``tracer`` records a
    policy span for each refusal.
    """

    def __init__(
        self, root: str | Path, *, policy: Any = None, tracer: Any = None
    ) -> None:
        self.root = require_directory(root)
        self._policy = policy
        self._tracer = tracer

    def read_file(self, path: str) -> dict[str, Any]:
        """Return the UTF-8 contents of ``path``. The file is not created."""
        try:
            target = resolve_inside(self.root, path)
        except PathError as exc:
            return {"error": str(exc)}
        relative = _relative(self.root, target)
        if not target.is_file():
            return {"error": f"file not found: {relative}"}
        try:
            text = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return {"error": f"file is not utf-8 text: {relative}"}
        except OSError as exc:
            return {"error": f"could not read {relative}: {exc}"}
        return {"path": relative, "content": text}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Replace ``path`` with UTF-8 ``content``. Parents are created inside the root."""
        if not isinstance(content, str):
            return {"error": "content must be a string"}
        try:
            target = resolve_inside(self.root, path)
        except PathError as exc:
            return {"error": str(exc)}
        if (refused := self._refuse_write(target)) is not None:
            return refused
        if target == self.root or (target.exists() and not target.is_file()):
            return {"error": f"not a file: {path}"}
        data = content.encode("utf-8")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as exc:
            return {"error": f"could not write {path}: {exc}"}
        return {"path": _relative(self.root, target), "bytes_written": len(data)}

    def patch_file(self, path: str, diff: str) -> dict[str, Any]:
        """Apply ``diff`` to one file. A context mismatch leaves the bytes untouched."""
        if not isinstance(diff, str):
            return {"error": "diff must be a string"}
        try:
            target = resolve_inside(self.root, path)
        except PathError as exc:
            return {"error": str(exc)}
        if (refused := self._refuse_write(target)) is not None:
            return refused
        relative = _relative(self.root, target)
        if not target.is_file():
            return {"error": f"file not found: {relative}"}
        original = target.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": f"file is not utf-8 text: {relative}"}
        try:
            updated = apply_unified_diff(text, diff)
        except PatchMismatch:
            return {"error": "hunk context does not match", "path": relative}
        except PatchError as exc:
            return {"error": str(exc), "path": relative}
        new_bytes = updated.encode("utf-8")
        if new_bytes != original:
            target.write_bytes(new_bytes)
        return {"path": relative, "applied": True}

    def edit_file(self, path: str, diff: str) -> dict[str, Any]:
        """Apply ``diff`` exactly, else by repair. Failure leaves bytes untouched."""
        if not isinstance(diff, str):
            return {"error": "diff must be a string"}
        try:
            target = resolve_inside(self.root, path)
        except PathError as exc:
            return {"error": str(exc)}
        if (refused := self._refuse_write(target)) is not None:
            return refused
        relative = _relative(self.root, target)
        if not target.is_file():
            return {"error": f"file not found: {relative}"}
        original = target.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": f"file is not utf-8 text: {relative}"}
        from omega_prime.tools.edit_pipeline import apply_edit

        try:
            updated, method = apply_edit(text, diff)
        except PatchMismatch:
            return {"error": "hunk context does not match", "path": relative}
        except PatchError as exc:
            return {"error": str(exc), "path": relative}
        new_bytes = updated.encode("utf-8")
        if new_bytes != original:
            target.write_bytes(new_bytes)
        return {"path": relative, "applied": True, "method": method}

    def _refuse_write(self, target: Path) -> dict[str, Any] | None:
        """Refusal when the policy marks this jailed path read-only."""
        if self._policy is None:
            return None
        relative = _relative(self.root, target)
        if self._policy.allows_write(relative):
            return None
        if self._tracer is not None:
            self._tracer.span(
                "policy",
                relative,
                {"verdict": "denied", "reason": f"policy forbids writing {relative}"},
            )
        return {"error": f"policy forbids writing {relative}"}


def require_directory(root: str | Path) -> Path:
    """Resolve ``root`` and require it to be a directory."""
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise PathError(f"tool root is not a directory: {resolved}")
    return resolved


def resolve_inside(root: str | Path, user_path: str) -> Path:
    """Absolute path under ``root``. Absolute inputs are not joined onto the root.

    ``Path.joinpath`` drops the left side when the right side is absolute, so an
    absolute path is checked on its own and a relative path is joined first.
    ``resolve`` follows symlinks; a link that lands outside the root is an error.
    """
    if not isinstance(user_path, str) or user_path == "" or "\x00" in user_path:
        raise PathError("path must be a non-empty string")
    base = Path(root).resolve()
    raw = Path(user_path)
    candidate = raw if raw.is_absolute() else base / raw
    resolved = candidate.resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise PathError(f"path escapes the root: {user_path}") from None
    return resolved


def contains(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` is ``root`` or a path under it."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def apply_unified_diff(original: str, diff: str) -> str:
    """Return the patched text. Raise ``PatchMismatch`` before producing a partial result.

    The caller writes the file only after this returns, so a mismatch leaves the
    on-disk bytes untouched. Every hunk is applied to the same text.
    """
    if not isinstance(diff, str) or diff.strip() == "":
        raise PatchError("diff must be a non-empty unified diff")
    minus_headers, plus_headers = _headers_outside_hunks(diff)
    if minus_headers > 1 or plus_headers > 1:
        raise PatchError("patch_file applies a unified diff to one file")
    file_lines = _split_keep(original)
    shift = 0
    for hunk in _parse_hunks(diff):
        idx = (
            hunk.old_start + shift
            if hunk.old_count == 0
            else hunk.old_start - 1 + shift
        )
        if idx < 0 or idx > len(file_lines):
            raise PatchMismatch("hunk context does not match")
        out: list[str] = []
        consumed = 0
        for kind, text, no_newline in hunk.lines:
            if kind in {" ", "-"}:
                pos = idx + consumed
                if pos >= len(file_lines) or _line_text(file_lines[pos]) != text:
                    raise PatchMismatch("hunk context does not match")
                if kind == " ":
                    out.append(file_lines[pos])
                consumed += 1
            elif kind == "+":
                out.append(text if no_newline else text + "\n")
            else:
                raise PatchError("malformed unified diff")
        file_lines = file_lines[:idx] + out + file_lines[idx + consumed :]
        shift += len(out) - consumed
    return "".join(file_lines)


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    lines: list[tuple[str, str, bool]]


def _headers_outside_hunks(diff: str) -> tuple[int, int]:
    """Count ``---`` and ``+++`` file headers, skipping hunk bodies.

    A removed line ``-- comment`` is encoded ``--- comment``, and an added line
    ``++ note`` is encoded ``+++ note``. The ``@@`` counts say how many body
    lines follow, so those are not a second file.
    """
    lines = diff.splitlines()
    index = 0
    minus = 0
    plus = 0
    while index < len(lines):
        header = _HUNK_HEADER.match(lines[index])
        if header is not None:
            index = _skip_hunk_body(lines, index + 1, header)
            continue
        if lines[index].startswith("--- "):
            minus += 1
        elif lines[index].startswith("+++ "):
            plus += 1
        index += 1
    return minus, plus


def _skip_hunk_body(lines: list[str], index: int, header: re.Match[str]) -> int:
    """Advance past the body whose size is declared by ``header``."""
    old_count = int(header.group(2)) if header.group(2) is not None else 1
    new_count = int(header.group(4)) if header.group(4) is not None else 1
    old_seen = 0
    new_seen = 0
    while index < len(lines) and (old_seen < old_count or new_seen < new_count):
        line = lines[index]
        if line.startswith("@@"):
            break
        index += 1
        if line.startswith("\\"):
            continue
        if line == "" or line.startswith(" "):
            old_seen += 1
            new_seen += 1
        elif line.startswith("-"):
            old_seen += 1
        elif line.startswith("+"):
            new_seen += 1
        else:
            break
    return index


def _parse_hunks(diff: str) -> list[_Hunk]:
    lines = diff.splitlines()
    hunks: list[_Hunk] = []
    index = 0
    while index < len(lines):
        header = _HUNK_HEADER.match(lines[index])
        if header is None:
            index += 1
            continue
        old_start = int(header.group(1))
        old_count = int(header.group(2)) if header.group(2) is not None else 1
        new_count = int(header.group(4)) if header.group(4) is not None else 1
        index += 1
        body: list[tuple[str, str]] = []
        while index < len(lines) and not lines[index].startswith("@@"):
            line = lines[index]
            index += 1
            if line.startswith("\\"):
                body.append(("\\", line[1:].strip()))
                continue
            if line == "":
                body.append((" ", ""))
                continue
            prefix, text = line[0], line[1:]
            if prefix not in " +-":
                raise PatchError(f"malformed unified diff line: {line!r}")
            body.append((prefix, text))
        flagged = _newline_flags(body)
        old_lines = sum(1 for kind, _, _ in flagged if kind in {" ", "-"})
        new_lines = sum(1 for kind, _, _ in flagged if kind in {" ", "+"})
        if old_lines != old_count or new_lines != new_count:
            raise PatchError("malformed unified diff: hunk line count does not match")
        hunks.append(_Hunk(old_start, old_count, flagged))
    if not hunks:
        raise PatchError("unified diff contains no hunks")
    return hunks


def _newline_flags(body: list[tuple[str, str]]) -> list[tuple[str, str, bool]]:
    flagged: list[tuple[str, str, bool]] = []
    index = 0
    while index < len(body):
        kind, text = body[index]
        if kind == "\\":
            raise PatchError("malformed unified diff: newline marker without a line")
        no_newline = index + 1 < len(body) and body[index + 1][0] == "\\"
        flagged.append((kind, text, no_newline))
        index += 2 if no_newline else 1
    return flagged


def _split_keep(text: str) -> list[str]:
    if text == "":
        return []
    return text.splitlines(keepends=True)


def _line_text(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


def _relative(root: Path, target: Path) -> str:
    return target.resolve().relative_to(root.resolve()).as_posix()


__all__ = [
    "FileWorkspace",
    "PatchError",
    "PatchMismatch",
    "PathError",
    "apply_unified_diff",
    "contains",
    "require_directory",
    "resolve_inside",
]

"""MEMORY.md and USER.md, joined by a section delimiter.

Character budgets are 2200 for memory and 1375 for the user profile. An entry
that contains ``§`` is refused. Nothing else is scanned in this phase.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ENTRY_DELIMITER = "\n§\n"
MEMORY_CHAR_LIMIT = 2200
USER_CHAR_LIMIT = 1375

_FILES = {"memory": "MEMORY.md", "user": "USER.md"}
_LIMITS = {"memory": MEMORY_CHAR_LIMIT, "user": USER_CHAR_LIMIT}
_TITLES = {
    "memory": "MEMORY (your personal notes)",
    "user": "USER PROFILE (who the user is)",
}
_BLOCK_TAGS = {
    "memory": "memory_notes",
    "user": "user_profile",
}


class MemoryStore:
    """One directory, two files. A new store reloads what an earlier store wrote."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.memory_entries: list[str] = []
        self.user_entries: list[str] = []
        self.load_from_disk()

    def add(self, target: str, content: str) -> dict[str, Any]:
        """Append a stripped entry. A duplicate is success and does not append."""
        cleaned, error = _clean_entry(content)
        if error is not None:
            return error
        assert cleaned is not None
        if target_error := _target_error(target):
            return target_error
        entries, read_error = self._read(target)
        if read_error is not None:
            return read_error
        if cleaned in entries:
            self._set(target, entries)
            return {
                "success": True,
                "target": target,
                "message": "Entry already exists (no duplicate added).",
            }
        updated = [*entries, cleaned]
        if _length(updated) > _LIMITS[target]:
            return {
                "success": False,
                "target": target,
                "error": (
                    f"Adding this entry would exceed the {_LIMITS[target]} character limit."
                ),
            }
        self._set(target, updated)
        self._write(target, updated)
        return {"success": True, "target": target, "message": "Entry added."}

    def replace(self, target: str, old_text: str, new_content: str) -> dict[str, Any]:
        """Replace one entry. Exact text wins over a substring. Two distinct hits do not write."""
        cleaned, error = _clean_entry(new_content)
        if error is not None:
            return error
        assert cleaned is not None
        if target_error := _target_error(target):
            return target_error
        entries, read_error = self._read(target)
        if read_error is not None:
            return read_error
        index, locate_error = _locate(entries, old_text)
        if locate_error is not None:
            return locate_error
        assert index is not None
        updated = [*entries[:index], cleaned, *entries[index + 1 :]]
        if _length(updated) > _LIMITS[target]:
            return {
                "success": False,
                "target": target,
                "error": (
                    f"Replacement would exceed the {_LIMITS[target]} character limit."
                ),
            }
        self._set(target, updated)
        self._write(target, updated)
        return {"success": True, "target": target, "message": "Entry replaced."}

    def remove(self, target: str, old_text: str) -> dict[str, Any]:
        """Remove the one entry ``old_text`` selects. A bad match leaves the file unchanged."""
        if target_error := _target_error(target):
            return target_error
        entries, read_error = self._read(target)
        if read_error is not None:
            return read_error
        index, locate_error = _locate(entries, old_text)
        if locate_error is not None:
            return locate_error
        assert index is not None
        updated = entries[:index] + entries[index + 1 :]
        self._set(target, updated)
        self._write(target, updated)
        return {"success": True, "target": target, "message": "Entry removed."}

    def load_from_disk(self) -> None:
        """Reload both files. Missing files are empty lists. An unreadable file is left untouched."""
        memory, _memory_error = self._read("memory")
        user, _user_error = self._read("user")
        self.memory_entries = memory
        self.user_entries = user

    def render(self) -> str:
        """Both blocks, in memory then user order. Empty targets are omitted."""
        blocks = [self._block(target) for target in ("memory", "user")]
        return "\n\n".join(block for block in blocks if block)

    def _block(self, target: str) -> str:
        entries = self._entries(target)
        if not entries:
            return ""
        tag = _BLOCK_TAGS[target]
        close = f"</{tag}>"
        joined = ENTRY_DELIMITER.join(
            entry.replace(close, f"&lt;/{tag}&gt;") for entry in entries
        )
        return f'<{tag} title="{_TITLES[target]}">\n{joined}\n{close}'

    def _entries(self, target: str) -> list[str]:
        return self.user_entries if target == "user" else self.memory_entries

    def _set(self, target: str, entries: list[str]) -> None:
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _path(self, target: str) -> Path:
        return self.directory / _FILES[target]

    def _read(self, target: str) -> tuple[list[str], dict[str, Any] | None]:
        """Entries, or an error when the file exists but cannot be read.

        Treating an unreadable file as empty and then saving would wipe it.
        """
        path = self._path(target)
        if not path.exists():
            return [], None
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], {
                "success": False,
                "error": f"Refusing to write {path.name}: the file could not be read.",
            }
        seen: list[str] = []
        for entry in raw.split(ENTRY_DELIMITER):
            entry = entry.strip()
            if entry and entry not in seen:
                seen.append(entry)
        return seen, None

    def _write(self, target: str, entries: list[str]) -> None:
        path = self._path(target)
        path.write_bytes(ENTRY_DELIMITER.join(entries).encode("utf-8"))


def _clean_entry(content: str) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(content, str):
        return None, {"success": False, "error": "Content must be a string."}
    if "§" in content:
        return None, {
            "success": False,
            "error": "Entry contains the delimiter character §.",
        }
    cleaned = content.strip()
    if cleaned == "":
        return None, {"success": False, "error": "Content cannot be empty."}
    if "§" in cleaned:
        return None, {
            "success": False,
            "error": "Entry contains the delimiter character §.",
        }
    return cleaned, None


def _target_error(target: str) -> dict[str, Any] | None:
    if target not in _FILES:
        return {
            "success": False,
            "error": f"Unknown target '{target}'. Use memory or user.",
        }
    return None


def _length(entries: list[str]) -> int:
    return len(ENTRY_DELIMITER.join(entries))


def _locate(
    entries: list[str], old_text: str
) -> tuple[int | None, dict[str, Any] | None]:
    if not isinstance(old_text, str) or old_text.strip() == "":
        return None, {"success": False, "error": "old_text cannot be empty."}
    needle = old_text.strip()
    exact = [index for index, entry in enumerate(entries) if entry == needle]
    matches = (
        exact
        if exact
        else [index for index, entry in enumerate(entries) if needle in entry]
    )
    distinct = {entries[index] for index in matches}
    if len(distinct) > 1:
        return None, {
            "success": False,
            "error": f"Multiple entries matched '{needle}'.",
        }
    if not matches:
        return None, {"success": False, "error": f"No entry matched '{needle}'."}
    return matches[0], None


__all__ = ["ENTRY_DELIMITER", "MEMORY_CHAR_LIMIT", "USER_CHAR_LIMIT", "MemoryStore"]

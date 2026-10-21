"""Create, replace, patch, and read one SKILL.md under a caller-supplied root.

``skills_root`` is keyword-only. The model never sends it. ``register_growth_tools``
binds a closure around the directory and that closure is what the registry calls.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

from omega_prime.skills_runtime.guards import (
    outside_root,
    parse_frontmatter,
    skill_directory,
    validate_category,
    validate_content_size,
    validate_frontmatter,
    validate_name,
)


def skill_manage(
    action: str,
    name: str,
    content: str | None = None,
    category: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    *,
    skills_root: str | Path,
) -> str:
    """Return a JSON string. A failed guard does not write.

    Every save holds the root's ``<root>.lock`` writer lock, the lock the
    agent-data migration holds while it moves the folder. A save that
    waited out a move finds the root gone and follows the ``<root>.moved``
    pointer the migration left (one hop), so it lands in the new folder.
    """
    if action not in ("create", "edit", "patch"):
        return _error(f"Unknown action '{action}'. Use create, edit, or patch.")
    root = Path(skills_root)
    with _root_lock(root):
        moved = _moved_to(root)
    if moved is not None:
        root = moved
    with _root_lock(root):
        if action == "create":
            return _create(name, content, category, root)
        if action == "edit":
            return _edit(name, content, root)
        return _patch(name, old_string, new_string, root)


_LOCK_SUFFIX = ".lock"
_SHIPPED_ROOT = Path(__file__).resolve().parents[1] / "skills"


def _same(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


_MOVED_SUFFIX = ".moved"  # agent_state.MOVED_SUFFIX


@contextlib.contextmanager
def _root_lock(root: Path) -> Iterator[None]:
    """Exclusive ``flock`` on ``<root>.lock`` for one save.

    The shipped install folder (``omega_prime/skills``) is never moved by
    the migration, so it gets no sidecar (the checkout stays clean). Also
    unlocked when the sidecar cannot be created (a read-only parent): the
    migration cannot move a root whose parent it cannot write either.
    """
    if _same(root, _SHIPPED_ROOT):
        yield
        return
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    lock = root.with_name(root.name + _LOCK_SUFFIX)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock, flags)
    except FileNotFoundError:
        try:
            fd = os.open(lock, flags | os.O_CREAT, 0o600)
        except OSError:
            yield
            return
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _moved_to(root: Path) -> Path | None:
    """The folder a migration moved ``root`` to, when ``root`` is gone."""
    if root.is_dir():
        return None
    pointer = root.with_name(root.name + _MOVED_SUFFIX)
    try:
        if pointer.is_symlink():
            return None
        target = Path(pointer.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return None
    if target.is_absolute() and target.is_dir() and not target.is_symlink():
        return target
    return None


def skill_view(name: str, *, skills_root: str | Path) -> str:
    """Return JSON whose ``content`` is the SKILL.md text. Each call reads the file."""
    root, error = _root(skills_root)
    if error is not None:
        return error
    assert root is not None
    if name_error := validate_name(name):
        return _error(name_error)
    matches = _find(root, name)
    if not matches:
        return _error(f"Skill '{name}' not found.")
    if len(matches) > 1:
        return _error(f"Skill '{name}' is ambiguous.")
    skill_md = matches[0]
    if outside_root(root, skill_md) or _symlink_leaves(root, skill_md):
        return _error("Skill path leaves the skills root.")
    try:
        data = skill_md.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _error(f"Failed to read skill '{name}': {exc}")
    return _ok(name=name, path=str(skill_md), content=text)


def _create(
    name: str, content: str | None, category: str | None, skills_root: str | Path
) -> str:
    root, error = _root(skills_root)
    if error is not None:
        return error
    assert root is not None
    if guard := _prepare(name, category, content, new_skill=True):
        return guard
    assert content is not None
    if _find(root, name):
        return _error(f"A skill named '{name}' already exists.")
    content, refused = _normalize(name, content)
    if refused is not None:
        return refused
    assert content is not None
    skill_dir = skill_directory(root, name, category)
    if outside_root(root, skill_dir) or _symlink_leaves(root, skill_dir):
        return _error("Skill directory escapes the skills root.")
    if dir_error := _existing_dir(skill_dir):
        return _error(dir_error)
    created: list[Path] = []
    try:
        if not skill_dir.exists():
            skill_dir.mkdir(parents=True, exist_ok=False)
            created.append(skill_dir)
            parent = skill_dir.parent
            if parent != root and parent not in created:
                created.append(parent)
        skill_md = skill_dir / "SKILL.md"
        _write_text(skill_md, content)
    except OSError as exc:
        _cleanup(created, root)
        return _error(f"Cannot create skill '{name}': {exc}")
    return _ok(message=f"Skill '{name}' created.", path=str(skill_md), name=name)


def _edit(name: str, content: str | None, skills_root: str | Path) -> str:
    root, error = _root(skills_root)
    if error is not None:
        return error
    assert root is not None
    if guard := _prepare(name, None, content, new_skill=False):
        return guard
    assert content is not None
    skill_md, found = _one(root, name)
    if found is not None:
        return found
    assert skill_md is not None
    if outside_root(root, skill_md) or _symlink_leaves(root, skill_md):
        return _error("Skill path leaves the skills root.")
    content, refused = _normalize(name, content)
    if refused is not None:
        return refused
    assert content is not None
    try:
        _write_text(skill_md, content)
    except OSError as exc:
        return _error(f"Cannot edit skill '{name}': {exc}")
    return _ok(message=f"Skill '{name}' updated.", path=str(skill_md), name=name)


def _patch(
    name: str,
    old_string: str | None,
    new_string: str | None,
    skills_root: str | Path,
) -> str:
    root, error = _root(skills_root)
    if error is not None:
        return error
    assert root is not None
    if name_error := validate_name(name):
        return _error(name_error)
    if not isinstance(old_string, str) or old_string == "":
        return _error("old_string is required.")
    if not isinstance(new_string, str):
        return _error("new_string is required.")
    skill_md, found = _one(root, name)
    if found is not None:
        return found
    assert skill_md is not None
    if outside_root(root, skill_md) or _symlink_leaves(root, skill_md):
        return _error("Skill path leaves the skills root.")
    try:
        original = skill_md.read_bytes()
        text = original.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _error(f"Failed to read skill '{name}': {exc}")
    count = text.count(old_string)
    if count != 1:
        return _error(
            f"old_string matched {count} times. Patch needs one exact match and left the file unchanged."
        )
    updated = text.replace(old_string, new_string, 1)
    if guard := validate_content_size(updated) or validate_frontmatter(
        updated, new_skill=False
    ):
        return _error(guard)
    normalized, refused = _normalize(name, updated)
    if refused is not None or normalized is None:
        return refused or _error("Content is required.")
    updated = normalized
    if updated.encode("utf-8") != original:
        try:
            _write_text(skill_md, updated)
        except OSError as exc:
            return _error(f"Cannot patch skill '{name}': {exc}")
    return _ok(message=f"Patched skill '{name}'.", path=str(skill_md), name=name)


def _exact_final_close(stripped: str, open_tag: str, close: str) -> bool:
    """Match the generated-command load rule for one skill envelope."""
    tag_name = close[2:-1]
    matches = list(re.finditer(rf"</{re.escape(tag_name)}\s*>", stripped))
    if len(matches) != 1:
        return False
    match = matches[0]
    return (
        stripped.startswith(open_tag)
        and match.group(0) == close
        and match.end() == len(stripped)
    )


# An opening skill tag at the start of the body. ``<skills>`` and a prose
# placeholder such as ``/omega-<skill>`` do not match: the next character
# after ``skill`` has to be whitespace, ``>``, or the end of an empty tag.
_SKILL_OPEN_AT_START = re.compile(r"<skill(?:\s|>|/>)")
# A closing tag the loader treats as the end of the envelope, including
# ``</skill >``. A mention of ``<skill>`` does not match.
_SKILL_CLOSE = re.compile(r"</skill\s*>")


def _attempted_skill_wrapper(body: str) -> bool:
    """True when the body is trying to be a skill envelope and is not one.

    A real ``</skill>`` close, or a ``<skill>`` / ``<skill ...>`` open tag
    that begins the body, is an attempted wrapper. Ordinary text such as
    ``<skills>`` or ``/omega-<skill>`` is not, and can be wrapped.
    """
    if _SKILL_CLOSE.search(body):
        return True
    return _SKILL_OPEN_AT_START.match(body.strip()) is not None


def _normalize(name: str, content: str) -> tuple[str | None, str | None]:
    """Return text to store, or the JSON error and no text.

    A body that already meets the load rule is returned unchanged. A body
    that tries to be a skill envelope and does not meet that rule is refused.
    Any other body is wrapped, leaving the frontmatter and the body bytes
    (including indentation) as they were.
    """
    _mapping, body, error = parse_frontmatter(content)
    if error is not None or body is None:
        return None, _error(error or "Content is required.")
    stripped = body.strip()
    open_tag = f'<skill name="{name}" version="1">'
    if _exact_final_close(stripped, open_tag, "</skill>"):
        return content, None
    if _attempted_skill_wrapper(body):
        return None, _error(f"Skill body is not a valid envelope for '{name}'.")
    text = content.lstrip("\ufeff")
    prefix = content[: len(content) - len(text) + (len(text) - len(body))]
    wrapped = f'\n<skill name="{name}" version="1">\n{body}\n</skill>\n'
    stored = prefix + wrapped
    if guard := validate_content_size(stored):
        return None, _error(guard)
    return stored, None


def _prepare(
    name: str, category: str | None, content: str | None, *, new_skill: bool
) -> str | None:
    if name_error := validate_name(name):
        return _error(name_error)
    if category_error := validate_category(category):
        return _error(category_error)
    if content is None:
        return _error("Content is required.")
    if guard := validate_content_size(content) or validate_frontmatter(
        content, new_skill=new_skill
    ):
        return _error(guard)
    return None


def _root(skills_root: str | Path) -> tuple[Path | None, str | None]:
    root = Path(skills_root)
    if not root.is_dir():
        return None, _error("skills_root is not a directory.")
    return root.resolve(), None


def _find(root: Path, name: str) -> list[Path]:
    matches: list[Path] = []
    if not root.is_dir():
        return matches
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [item for item in dirnames if not (current / item).is_symlink()]
        if "SKILL.md" not in filenames or current.name != name:
            continue
        skill_md = current / "SKILL.md"
        if not outside_root(root, skill_md):
            matches.append(skill_md)
    return matches


def _one(root: Path, name: str) -> tuple[Path | None, str | None]:
    matches = _find(root, name)
    if not matches:
        return None, _error(f"Skill '{name}' not found.")
    if len(matches) > 1:
        return None, _error(f"Skill '{name}' is ambiguous.")
    return matches[0], None


def _existing_dir(skill_dir: Path) -> str | None:
    if skill_dir.is_symlink():
        return f"Cannot create skill: {skill_dir} exists and is not an empty directory."
    if not skill_dir.exists():
        return None
    if not skill_dir.is_dir():
        return f"Cannot create skill: {skill_dir} exists and is not an empty directory."
    try:
        occupied = any(skill_dir.iterdir())
    except OSError:
        return f"Cannot create skill: {skill_dir} exists and is not an empty directory."
    if occupied:
        return f"Cannot create skill: {skill_dir} exists and is not an empty directory."
    return None


def _symlink_leaves(root: Path, path: Path) -> bool:
    """True when ``path`` or an ancestor symlink resolves outside ``root``."""
    cursor = root
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    for part in parts:
        cursor = cursor / part
        if not cursor.is_symlink():
            continue
        try:
            cursor.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            return True
    if path.is_symlink():
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            return True
    return False


def _write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(text.encode("utf-8"))
    try:
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _cleanup(created: list[Path], root: Path) -> None:
    for path in created:
        if path == root or not path.is_dir() or path.is_symlink():
            continue
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        except OSError:
            continue


def _ok(**fields: object) -> str:
    return json.dumps({"success": True, **fields}, ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"success": False, "error": message}, ensure_ascii=False)


__all__ = ["skill_manage", "skill_view"]

"""Load ``{home}/plugins/<name>/plugin.py`` and register its tools.

The exact ``OMEGA_PRIME_PLUGINS=1`` predicate is exposed for the host to check
against its supplied environment snapshot before scanning this directory.
Loaded plugins are trusted local Python, not sandboxed or approval-gated;
registration can replace handlers and approval metadata in the registry.
Resolved ``plugin.py`` paths outside ``home`` are skipped, including symlinks
that leave ``home``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def plugins_enabled(env: Mapping[str, str]) -> bool:
    """Return whether the supplied environment snapshot opts plugins in."""
    return env.get("OMEGA_PRIME_PLUGINS") == "1"


def load_plugins(home: str | Path, registry: Any) -> list[str]:
    """Call each plugin's ``register(registry)`` and return the names it added.

    Names are in registration order. A directory whose ``plugin.py`` defines no
    ``register`` callable is skipped. An exception from ``register`` propagates.
    """
    root = Path(home).resolve()
    plugins_dir = root / "plugins"
    if not plugins_dir.is_dir():
        return []
    loaded: list[str] = []
    for entry in sorted(plugins_dir.iterdir(), key=lambda path: path.name):
        if _NAME.fullmatch(entry.name) is None:
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        plugin_file = entry / "plugin.py"
        try:
            resolved = plugin_file.resolve()
        except OSError:
            continue
        if not _inside(root, resolved) or not resolved.is_file():
            continue
        module = _load_module(entry.name, resolved)
        register = getattr(module, "register", None)
        if not callable(register):
            continue
        before = _tool_names(registry)
        register(registry)
        loaded.extend(_added(before, _tool_names(registry)))
    return loaded


def _inside(home: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(home)
    except (OSError, ValueError):
        return False
    return True


def _load_module(name: str, path: Path) -> Any:
    safe = re.sub(r"[^a-z0-9_]", "_", name)
    module_name = f"_omega_prime_plugin_{safe}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _tool_names(registry: Any) -> list[str]:
    names: list[str] = []
    for schema in registry.schemas():
        function = schema.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.append(function["name"])
    return names


def _added(before: list[str], after: list[str]) -> list[str]:
    seen = set(before)
    added: list[str] = []
    for name in after:
        if name not in seen:
            added.append(name)
            seen.add(name)
    return added


__all__ = ["load_plugins", "plugins_enabled"]

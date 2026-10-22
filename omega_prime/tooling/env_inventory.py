"""Source-only inventory of reviewed Python environment settings.

The collector parses checked-in source without importing it or evaluating values.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Fields Python 3.12 added to existing node types. Python 3.11 does not have
# them, so its dump gets them back as the empty value 3.12 prints.
_PY312_ADDED_FIELDS: dict[str, tuple[str, ...]] = {
    "FunctionDef": ("type_params",),
    "AsyncFunctionDef": ("type_params",),
    "ClassDef": ("type_params",),
}


def _dump_312_shape(node: object) -> str:
    """``ast.dump(node)`` as Python 3.12 prints it, on any older interpreter.

    Same rules as the 3.11/3.12 ``ast.dump`` (fields in order, an unset field or
    a ``None`` that equals the class default is left out; every field is
    printed with its name), plus the 3.12 fields from ``_PY312_ADDED_FIELDS`` as ``[]``.
    """
    if isinstance(node, ast.AST):
        cls = type(node)
        args: list[str] = []
        added = _PY312_ADDED_FIELDS.get(cls.__name__, ())
        fields = [*node._fields, *(name for name in added if name not in node._fields)]
        for name in fields:
            try:
                value = getattr(node, name)
            except AttributeError:
                if name not in added:
                    continue
                value = []
            if value is None and getattr(cls, name, ...) is None:
                continue
            args.append(f"{name}={_dump_312_shape(value)}")
        return f"{cls.__name__}({', '.join(args)})"
    if isinstance(node, list):
        return "[" + ", ".join(_dump_312_shape(item) for item in node) + "]"
    return repr(node)


def ast_source_digest(node: ast.AST) -> str:
    """SHA-256 of a node's AST dump, identical on Python 3.11 through 3.14.

    Committed policy digests use the Python 3.12 dump. Python 3.13 started
    omitting empty fields from ``ast.dump``; ``show_empty`` restores the 3.12
    shape there. Python 3.11 lacks fields 3.12 added (``type_params``), so it
    uses ``_dump_312_shape``, which prints them empty the way 3.12 does.
    """
    if sys.version_info >= (3, 13):
        dumped = ast.dump(node, include_attributes=False, show_empty=True)
    elif sys.version_info >= (3, 12):
        dumped = ast.dump(node, include_attributes=False)
    else:
        dumped = _dump_312_shape(node)
    return hashlib.sha256(dumped.encode()).hexdigest()


_POLICY = "omega_prime/tooling/env_inventory_policy.json"
_EXCLUSION_FILES = frozenset(
    {"omega_prime/scripts/ci_receipt.py", "omega_prime/grokbot/local_validation.py"}
)


@dataclass(frozen=True)
class Finding:
    name: str
    path: str
    line: int
    symbol: str


class InventoryError(Exception):
    def __init__(self, path: str, line: int, name: str, error_class: str):
        self.path, self.line, self.name, self.error_class = (
            path,
            line,
            name,
            error_class,
        )
        super().__init__(error_class)


def _name(node: ast.AST, constants: dict[str, Any]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        value = constants.get(node.id)
        return value if isinstance(value, str) else None
    return None


def _constant(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, tuple, list)):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        values = [_constant(value) for value in node.elts]
        if all(isinstance(value, (str, tuple, list, dict)) for value in values):
            return tuple(values)
    if isinstance(node, ast.Dict):
        pairs: list[tuple[Any, Any]] = []
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                return None
            pairs.append((_constant(key), _constant(value)))
        if all(
            isinstance(key, str) and isinstance(value, (str, tuple, list))
            for key, value in pairs
        ):
            return dict(pairs)
    return None


def _func(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _unparse(node: ast.AST) -> str:
    return ast.unparse(node) if hasattr(ast, "unparse") else ""


class _FileCollector:
    def __init__(
        self,
        rel: str,
        tree: ast.Module,
        policy: dict[str, Any],
        imported_constants: dict[str, Any] | None = None,
    ):
        self.rel, self.tree, self.policy = rel, tree, policy
        self.constants: dict[str, Any] = dict(imported_constants or {})
        self.scoped_constants: dict[ast.AST, dict[str, Any]] = {}
        self.os_aliases: set[str] = set()
        self.scoped_os_aliases: dict[ast.AST, set[str]] = {}
        self.getenv_aliases: set[str] = set()
        self.scoped_getenv_aliases: dict[ast.AST, set[str]] = {}
        self.env_aliases: set[str] = set()
        self.scoped_env_aliases: dict[ast.AST, set[str]] = {}
        self.attr_env: set[str] = set()
        self.scoped_attr_env: dict[ast.AST, set[str]] = {}
        self.class_attr_env: dict[ast.ClassDef, set[str]] = {}
        self.loop_constants: dict[str, tuple[str, ...]] = {}
        self.scoped_loop_constants: dict[ast.AST, dict[str, tuple[str, ...]]] = {}
        self.findings: list[Finding] = []
        self.helper_policy = (
            policy.get("helpers", {})
            if isinstance(policy.get("helpers", {}), dict)
            else {}
        )
        self.used_helpers: set[str] = set()
        self.used_dynamic: set[str] = set()
        self.used_supplements: set[str] = set()
        self.used_exclusions: set[str] = set()
        self.used_token_selectors: set[str] = set()
        self.errors: list[InventoryError] = []
        self._parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self._parents[child] = parent
        self._collect_constants()
        self._collect_loop_constants()
        self._collect_aliases()

    def _constant_for(
        self,
        node: ast.AST,
        context: ast.AST | None = None,
        seen: frozenset[str] = frozenset(),
    ) -> Any:
        if isinstance(node, ast.Name):
            if node.id in seen:
                return None
            next_seen = seen | {node.id}
            scope = self._scope(context or node)
            known = (
                self.scoped_constants.get(scope, {}).get(
                    node.id, self.constants.get(node.id)
                )
                if scope is not None
                else self.constants.get(node.id)
            )
            if known is not None:
                return known
            for candidate in ast.walk(self.tree):
                if (
                    isinstance(candidate, ast.comprehension)
                    and isinstance(candidate.target, ast.Name)
                    and candidate.target.id == node.id
                    and self._scope(candidate) is scope
                ):
                    expanded = self._constant_for(candidate.iter, candidate, next_seen)
                    if isinstance(expanded, (tuple, list)) and all(
                        isinstance(item, str) for item in expanded
                    ):
                        return tuple(expanded)
            for assignment in ast.walk(self.tree):
                if (
                    not isinstance(assignment, (ast.Assign, ast.AnnAssign))
                    or assignment.value is None
                    or self._scope(assignment) is not scope
                ):
                    continue
                targets = (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
                if any(
                    isinstance(target, ast.Name) and target.id == node.id
                    for target in targets
                ):
                    return self._constant_for(assignment.value, assignment, next_seen)
            return None
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, (str, tuple, list)) else None
        if isinstance(node, (ast.Tuple, ast.List)):
            values = [self._constant_for(item, context, seen) for item in node.elts]
            return tuple(values) if all(value is not None for value in values) else None
        if isinstance(node, ast.Dict):
            pairs: list[tuple[Any, Any]] = []
            for key, value in zip(node.keys, node.values, strict=True):
                if key is None:
                    return None
                pairs.append(
                    (
                        self._constant_for(key, context, seen),
                        self._constant_for(value, context, seen),
                    )
                )
            return (
                dict(pairs)
                if all(k is not None and v is not None for k, v in pairs)
                else None
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "split"
            and not node.args
        ):
            value = self._constant_for(node.func.value, context, seen)
            return tuple(value.split()) if isinstance(value, str) else None
        return None

    def _collect_constants(self) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            value_constant = _constant(node.value)
            if value_constant is None:
                continue
            scope = self._scope(node)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    if scope is None:
                        self.constants[target.id] = value_constant
                    else:
                        self.scoped_constants.setdefault(scope, {})[target.id] = (
                            value_constant
                        )

    def _is_os_environ(self, node: ast.AST, context: ast.AST | None = None) -> bool:
        if not (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
        ):
            return False
        return self._visible(
            node.value.id, context or node, self.os_aliases, self.scoped_os_aliases
        )

    def _is_os_getenv(self, node: ast.AST, context: ast.AST | None = None) -> bool:
        if not (
            isinstance(node, ast.Attribute)
            and node.attr == "getenv"
            and isinstance(node.value, ast.Name)
        ):
            return False
        return self._visible(
            node.value.id, context or node, self.os_aliases, self.scoped_os_aliases
        )

    def _scope(self, node: ast.AST) -> ast.AST | None:
        scope: ast.AST | None = node
        while scope is not None and not isinstance(
            scope, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            scope = self._parents.get(scope)
        return scope

    def _class_scope(self, node: ast.AST) -> ast.ClassDef | None:
        scope: ast.AST | None = node
        while scope is not None and not isinstance(scope, ast.ClassDef):
            scope = self._parents.get(scope)
        return scope

    def _is_helper_scope(self, node: ast.AST) -> bool:
        scope = self._scope(node)
        return isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            f"{self.rel}:{scope.name}" in self.helper_policy
        )

    def _visible(
        self,
        name: str,
        node: ast.AST,
        module_names: set[str],
        scoped: dict[ast.AST, set[str]],
    ) -> bool:
        if name in module_names:
            return True
        scope = self._scope(node)
        return scope is not None and name in scoped.get(scope, set())

    def _is_environment_getenv(
        self, node: ast.AST, context: ast.AST | None = None
    ) -> bool:
        return self._is_os_getenv(node, context) or (
            isinstance(node, ast.Name)
            and self._visible(
                node.id,
                context or node,
                self.getenv_aliases,
                self.scoped_getenv_aliases,
            )
        )

    def _annotated_env(self, node: ast.arg) -> bool:
        annotation = _unparse(node.annotation) if node.annotation is not None else ""
        words = set(re.findall(r"[A-Za-z_]\w*", annotation))
        return node.arg in {"env", "environ", "environment"} and bool(
            words & {"Mapping", "MutableMapping", "dict", "Environ"}
        )

    def _collect_loop_constants(self) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.For, ast.AsyncFor)) or not isinstance(
                node.target, ast.Name
            ):
                continue
            scope = self._scope(node)
            values = self._finite(node.iter, node)
            if values is not None:
                if scope is None:
                    self.loop_constants[node.target.id] = values
                else:
                    self.scoped_loop_constants.setdefault(scope, {})[node.target.id] = (
                        values
                    )

    def _finite(
        self, node: ast.AST, context: ast.AST | None = None
    ) -> tuple[str, ...] | None:
        if isinstance(node, ast.Name):
            value = self._constant_for(node, context)
            return (
                tuple(value)
                if isinstance(value, (tuple, list))
                and all(isinstance(item, str) for item in value)
                else None
            )
        if isinstance(node, (ast.Tuple, ast.List)):
            values: list[str] = []
            for element in node.elts:
                if isinstance(element, ast.Starred):
                    expanded = self._finite(element.value, context)
                    if expanded is None:
                        return None
                    values.extend(expanded)
                elif isinstance(element, ast.Constant) and isinstance(
                    element.value, str
                ):
                    values.append(element.value)
                else:
                    return None
            return tuple(values)
        return None

    def _collect_aliases(self) -> None:
        for node in ast.walk(self.tree):
            scope = self._scope(node)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "os":
                        (
                            self.os_aliases
                            if scope is None
                            else self.scoped_os_aliases.setdefault(scope, set())
                        ).add(alias.asname or "os")
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                for alias in node.names:
                    if alias.name not in {"environ", "getenv"}:
                        continue
                    destination = alias.asname or alias.name
                    if alias.name == "environ":
                        (
                            self.env_aliases
                            if scope is None
                            else self.scoped_env_aliases.setdefault(scope, set())
                        ).add(destination)
                    else:
                        (
                            self.getenv_aliases
                            if scope is None
                            else self.scoped_getenv_aliases.setdefault(scope, set())
                        ).add(destination)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ):
                    if self._annotated_env(arg):
                        self.scoped_env_aliases.setdefault(node, set()).add(arg.arg)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if value is None:
                    continue
                source = (
                    self._is_os_environ(value, node)
                    or (
                        isinstance(value, ast.Name)
                        and self._visible(
                            value.id, node, self.env_aliases, self.scoped_env_aliases
                        )
                    )
                    or self._is_receiver(value, node)
                )
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if source:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            (
                                self.env_aliases
                                if scope is None
                                else self.scoped_env_aliases.setdefault(scope, set())
                            ).add(target.id)
                        elif isinstance(target, ast.Attribute) and target.attr in {
                            "env",
                            "_env",
                        }:
                            attribute = _unparse(target)
                            if scope is None:
                                self.attr_env.add(attribute)
                            else:
                                self.scoped_attr_env.setdefault(scope, set()).add(
                                    attribute
                                )
                            class_scope = self._class_scope(node)
                            if class_scope is not None:
                                self.class_attr_env.setdefault(class_scope, set()).add(
                                    attribute
                                )

    def _is_environment_snapshot(
        self, node: ast.AST, context: ast.AST | None = None
    ) -> bool:
        at = context or node
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "copy"
            and self._is_receiver(node.func.value, at)
        ):
            return True
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
            and len(node.args) == 1
            and self._is_receiver(node.args[0], at)
        )

    def _is_receiver(self, node: ast.AST, context: ast.AST | None = None) -> bool:
        at = context or node
        if self._is_os_environ(node, at) or self._is_environment_snapshot(node, at):
            return True
        if isinstance(node, ast.IfExp):
            return self._is_receiver(node.body, at) or self._is_receiver(
                node.orelse, at
            )
        if isinstance(node, ast.Name):
            return self._visible(node.id, at, self.env_aliases, self.scoped_env_aliases)
        if isinstance(node, ast.Attribute):
            scope = self._scope(at)
            class_scope = self._class_scope(at)
            attribute = _unparse(node)
            return (
                attribute in self.attr_env
                or (
                    scope is not None
                    and attribute in self.scoped_attr_env.get(scope, set())
                )
                or (
                    class_scope is not None
                    and attribute in self.class_attr_env.get(class_scope, set())
                )
            )
        return False

    def _site(self, node: ast.AST, symbol: str) -> str:
        return f"{self.rel}:{getattr(node, 'lineno', 1)}:{symbol}"

    def _error(self, node: ast.AST, name: str, kind: str) -> None:
        self.errors.append(
            InventoryError(self.rel, getattr(node, "lineno", 1), name, kind)
        )

    def _add(self, node: ast.AST, name: str, symbol: str) -> None:
        if not name or "\n" in name or "\r" in name or "`" in name:
            self._error(node, "<dynamic>", "InvalidEnvironmentName")
            return
        self.findings.append(
            Finding(name, self.rel, getattr(node, "lineno", 1), symbol)
        )

    def _policy_declaration(self, collection: str, site: str) -> dict[str, Any] | None:
        rows = self.policy.get(collection, [])
        if not isinstance(rows, list):
            self._error(self.tree, "<policy>", "InvalidPolicy")
            return None
        matches = [
            row for row in rows if isinstance(row, dict) and row.get("site") == site
        ]
        if len(matches) > 1:
            self._error(self.tree, "<policy>", "DuplicatePolicySite")
            return None
        return matches[0] if matches else None

    def _function_metadata(self, node: ast.AST) -> tuple[str, str]:
        scope = self._scope(node)
        symbol = (
            scope.name
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
            else ""
        )
        digest = ast_source_digest(scope) if scope else ""
        return symbol, digest

    def _excluded(
        self, node: ast.AST, kind: str, names: list[str] | None = None
    ) -> bool:
        site = f"{self.rel}:{getattr(node, 'lineno', 1)}:{kind}"
        row = self._policy_declaration("exclusions", site)
        if row is None:
            return False
        self.used_exclusions.add(site)
        symbol, digest = self._function_metadata(node)
        expected = row.get("names")
        reason = row.get("reason")
        if (
            row.get("symbol") != symbol
            or row.get("source_sha256") != digest
            or not isinstance(expected, list)
            or not expected
            or any(not isinstance(name, str) or not name for name in expected)
            or len(expected) != len(set(expected))
            or not isinstance(reason, str)
            or len(reason.strip()) < 12
            or (names is not None and set(expected) != set(names))
        ):
            self._error(node, "<policy>", "InvalidExclusion")
            return True
        return True

    def _dynamic(self, node: ast.AST, symbol: str) -> bool:
        site = self._site(node, symbol)
        row = self._policy_declaration("dynamic", site)
        supplemental = (
            self._policy_declaration("supplements", site) if row is None else None
        )
        policy_row = row or supplemental
        if policy_row is None:
            self._error(node, "<dynamic>", "UnreviewedDynamicRead")
            return True
        if row is not None:
            self.used_dynamic.add(site)
        else:
            self.used_supplements.add(site)
        function, digest = self._function_metadata(node)
        expected = policy_row.get("names")
        reason = policy_row.get("reason")
        if (
            policy_row.get("symbol") != function
            or policy_row.get("source_sha256") != digest
        ):
            self._error(node, "<dynamic>", "DynamicSourceChanged")
            return True
        if not isinstance(reason, str) or len(reason.strip()) < 12:
            self._error(node, "<dynamic>", "InvalidDynamicPolicy")
            return True
        if row is not None:
            if (
                not isinstance(expected, list)
                or not expected
                or any(not isinstance(item, str) or not item for item in expected)
                or len(expected) != len(set(expected))
            ):
                self._error(node, "<dynamic>", "InvalidDynamicPolicy")
                return True
            for name in expected:
                self._add(node, name, symbol)
        elif (
            supplemental is None
            or supplemental.get("classification") != "excluded"
            or "names" in supplemental
        ):
            self._error(node, "<dynamic>", "InvalidSupplementPolicy")
        return True

    def _read_name(self, node: ast.AST, symbol: str) -> bool:
        metadata = self.policy.get("metadata", {})
        selectors = (
            metadata.get("token_selectors", []) if isinstance(metadata, dict) else []
        )
        selector_sites = (
            {
                row.get("site"): row
                for row in selectors
                if isinstance(row, dict) and isinstance(row.get("site"), str)
            }
            if isinstance(selectors, list)
            else {}
        )
        selector_kind = None
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "token_env"
            and isinstance(node.value, ast.Name)
            and node.value.id == "args"
        ):
            selector_kind = "args.token_env"
        elif isinstance(node, ast.Name) and node.id in {"token_env", "env_name"}:
            selector_kind = node.id
        if selector_kind is not None:
            site = f"{self.rel}:{getattr(node, 'lineno', 1)}:{selector_kind}"
            row = selector_sites.get(site)
            if row is not None:
                self.used_token_selectors.add(site)
            reason = row.get("reason") if row else None
            symbol_name, digest = self._function_metadata(node)
            if (
                row is None
                or not isinstance(reason, str)
                or len(reason.strip()) < 12
                or row.get("symbol") != symbol_name
                or row.get("source_sha256") != digest
                or not isinstance(row.get("source_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"])
            ):
                self._error(node, "<dynamic>", "TokenSelectorSourceChanged")
            return True
        scope = self._scope(node)
        loops = self.scoped_loop_constants.get(scope, {}) if scope is not None else {}
        if isinstance(node, ast.Name) and (
            node.id in loops or node.id in self.loop_constants
        ):
            for name in loops.get(node.id, self.loop_constants.get(node.id, ())):
                self._add(node, name, symbol)
            return True
        if isinstance(node, ast.Name):
            for assignment in ast.walk(self.tree):
                if (
                    not isinstance(assignment, (ast.Assign, ast.AnnAssign))
                    or assignment.value is None
                ):
                    continue
                if self._scope(assignment) is not scope:
                    continue
                targets = (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
                if not any(
                    isinstance(target, ast.Name) and target.id == node.id
                    for target in targets
                ):
                    continue
                value = assignment.value
                if isinstance(value, ast.JoinedStr):
                    formats = [
                        part
                        for part in value.values
                        if isinstance(part, ast.FormattedValue)
                    ]
                    if len(formats) == 1 and isinstance(formats[0].value, ast.Name):
                        bases = loops.get(
                            formats[0].value.id,
                            self.loop_constants.get(formats[0].value.id),
                        )
                        suffix = "".join(
                            part.value
                            for part in value.values
                            if isinstance(part, ast.Constant)
                            and isinstance(part.value, str)
                        )
                        if bases and suffix:
                            for base_name in bases:
                                self._add(node, base_name + suffix, symbol)
                            return True
        literal_name = _name(
            node,
            {
                **self.constants,
                **(self.scoped_constants.get(scope, {}) if scope is not None else {}),
            },
        )
        if literal_name is not None:
            self._add(node, literal_name, symbol)
            return True
        return self._dynamic(node, symbol)

    def collect(self) -> list[Finding]:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Call, ast.Subscript)) and self._is_helper_scope(
                node
            ):
                continue
            if isinstance(node, ast.Call):
                fn = node.func
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr == "items"
                    and self._is_receiver(fn.value, node)
                ):
                    self._dynamic(node, "environment.items")
                    continue
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr == "get"
                    and self._is_os_environ(fn.value, node)
                ):
                    arg = node.args[0] if node.args else None
                    value = self._constant_for(arg, node) if arg is not None else None
                    names = (
                        [value]
                        if isinstance(value, str)
                        else list(value)
                        if isinstance(value, (tuple, list))
                        else None
                    )
                    if self._excluded(node, "os.environ.get", names):
                        continue
                    if self.rel in _EXCLUSION_FILES:
                        self._error(node, "<policy>", "UnreviewedExclusion")
                        continue
                if self._is_environment_getenv(fn, node):
                    if self.rel in _EXCLUSION_FILES:
                        self._error(node, "<policy>", "UnreviewedExclusion")
                    elif node.args:
                        self._read_name(node.args[0], "os.getenv")
                    else:
                        self._error(node, "<dynamic>", "MissingEnvironmentName")
                    continue
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr in {"get", "__getitem__"}
                    and self._is_receiver(fn.value, node)
                ):
                    arg = node.args[0] if node.args else None
                    if arg is None:
                        self._error(node, "<dynamic>", "MissingEnvironmentName")
                    elif self._is_os_environ(fn.value, node):
                        value = self._constant_for(arg, node)
                        names = (
                            [value]
                            if isinstance(value, str)
                            else list(value)
                            if isinstance(value, (tuple, list))
                            else None
                        )
                        if not self._excluded(node, "os.environ.get", names):
                            self._read_name(arg, "mapping.get")
                    else:
                        self._read_name(arg, "mapping.get")
            elif (
                isinstance(node, ast.Subscript)
                and self._is_receiver(node.value, node)
                and isinstance(node.ctx, ast.Load)
            ):
                if self._is_os_environ(node.value, node):
                    value = self._constant_for(node.slice, node)
                    names = (
                        [value]
                        if isinstance(value, str)
                        else list(value)
                        if isinstance(value, (tuple, list))
                        else None
                    )
                    if self._excluded(node, "os.environ.subscription", names):
                        continue
                    if self.rel in _EXCLUSION_FILES:
                        self._error(node, "<policy>", "UnreviewedExclusion")
                        continue
                self._read_name(node.slice, "mapping.subscription")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if self.rel == "omega_prime/commands/workflows.py" and any(
                    isinstance(target, ast.Name) and target.id == "_CONNECTORS"
                    for target in targets
                ):
                    declared = self.constants.get("_CONNECTORS")
                    if isinstance(declared, tuple):
                        for connector in declared:
                            if not isinstance(connector, dict):
                                continue
                            names = connector.get("env", ())
                            if isinstance(names, (tuple, list)):
                                for name in names:
                                    if isinstance(name, str):
                                        self._add(node, name, "_CONNECTORS.env")
                if self.rel.startswith("omega_prime/providers/") and any(
                    isinstance(target, ast.Name) and target.id == "_ENV_VARS"
                    for target in targets
                ):
                    declared = _constant(node.value) if node.value is not None else None
                    if isinstance(declared, dict):
                        for provider_names in declared.values():
                            values = (
                                provider_names
                                if isinstance(provider_names, (tuple, list))
                                else (provider_names,)
                            )
                            for name in values:
                                if isinstance(name, str):
                                    self._add(node, name, "_ENV_VARS")
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = child.value
                    targets = (
                        child.targets
                        if isinstance(child, ast.Assign)
                        else [child.target]
                    )
                    if not any(
                        isinstance(target, ast.Name) and target.id == "env_vars"
                        for target in targets
                    ):
                        continue
                    class_env_names = (
                        self.constants.get(value.id)
                        if isinstance(value, ast.Name)
                        else _constant(value)
                        if value is not None
                        else None
                    )
                    if isinstance(class_env_names, dict):
                        class_env_names = tuple(class_env_names.values())
                    if isinstance(class_env_names, tuple):
                        for name in class_env_names:
                            if isinstance(name, str):
                                self._add(child, name, f"{node.name}.env_vars")
        self._collect_helpers()
        return self.findings

    def _collect_helpers(self) -> None:
        helpers = self.helper_policy
        aliases: dict[str, str] = {}
        module = self.rel[:-3].replace("/", ".")
        package = module.rpartition(".")[0]
        for imported in ast.walk(self.tree):
            if not isinstance(imported, ast.ImportFrom):
                continue
            target_module = imported.module or ""
            if imported.level:
                try:
                    target_module = importlib.util.resolve_name(
                        "." * imported.level + target_module, package
                    )
                except ImportError:
                    continue
            target_path = f"{target_module.replace('.', '/')}.py"
            for alias in imported.names:
                aliases[alias.asname or alias.name] = f"{target_path}:{alias.name}"
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            fn = node.func.id
            key = aliases.get(fn, f"{self.rel}:{fn}")
            config = helpers.get(key)
            if not isinstance(config, dict):
                continue
            if self._is_helper_scope(node):
                self.used_helpers.add(key)
                continue
            position, env_arg = config.get("name_arg"), config.get("env_arg")
            if (
                not isinstance(position, int)
                or isinstance(position, bool)
                or position < 0
                or not isinstance(env_arg, int)
                or isinstance(env_arg, bool)
                or env_arg < 0
            ):
                self._error(node, "<policy>", "InvalidHelperPolicy")
                continue
            if (
                position >= len(node.args)
                or env_arg >= len(node.args)
                or not self._is_receiver(node.args[env_arg], node)
            ):
                continue
            self.used_helpers.add(key)
            value = self._constant_for(node.args[position], node)
            names = (
                [value]
                if isinstance(value, str)
                else list(value)
                if isinstance(value, (tuple, list))
                and all(isinstance(item, str) for item in value)
                else []
            )
            if names:
                for name in names:
                    self._add(node, name, f"helper:{key}")
            else:
                self._read_name(node.args[position], f"helper:{key}")
            suffixes = config.get("suffixes", [])
            if isinstance(suffixes, list):
                for name in names:
                    for suffix in suffixes:
                        if isinstance(suffix, str):
                            self._add(node, f"{name}{suffix}", f"helper:{key}")


def _source_files(root: Path) -> list[Path]:
    base = root / "omega_prime"
    found = []
    for path in base.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in f"/{rel}/" or rel.startswith("omega_prime/tests/"):
            continue
        if any(part in {".git", "__pycache__", "node_modules"} for part in path.parts):
            continue
        # Do not follow submodules or vendored code.
        if any(
            part in {"vendor", "vendored", "prime-agent"}
            for part in path.relative_to(base).parts
        ):
            continue
        found.append(path)
    return sorted(found)


def _purpose_for(name: str, purposes: dict[str, Any]) -> str | None:
    purpose = purposes.get(name)
    if isinstance(purpose, str):
        return purpose
    if name.endswith("_FILE"):
        base = name[:-5]
        base_purpose = purposes.get(base)
        if isinstance(base_purpose, str):
            return f"Names a protected file supplying {base}: {base_purpose}"
    return None


def collect(root: Path, policy: dict[str, Any]) -> list[Finding]:
    if not isinstance(policy, dict):
        raise InventoryError(_POLICY, 1, "<policy>", "InvalidPolicy")
    policy_rows: dict[str, list[dict[str, Any]]] = {}
    for collection in ("dynamic", "supplements", "exclusions"):
        rows = policy.get(collection, [])
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("site"), str)
            for row in rows
        ):
            raise InventoryError(_POLICY, 1, "<policy>", "InvalidPolicy")
        if len({row["site"] for row in rows}) != len(rows):
            raise InventoryError(_POLICY, 1, "<policy>", "DuplicatePolicySite")
        policy_rows[collection] = rows
    helpers_policy = policy.get("helpers", {})
    metadata = policy.get("metadata", {})
    if not isinstance(helpers_policy, dict) or not isinstance(metadata, dict):
        raise InventoryError(_POLICY, 1, "<policy>", "InvalidPolicy")
    selectors = metadata.get("token_selectors", [])
    if not isinstance(selectors, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("site"), str)
        for row in selectors
    ):
        raise InventoryError(_POLICY, 1, "<policy>", "InvalidPolicy")
    if len({row["site"] for row in selectors}) != len(selectors):
        raise InventoryError(_POLICY, 1, "<policy>", "DuplicatePolicySite")
    findings: list[Finding] = []
    used_dynamic: set[str] = set()
    used_exclusions: set[str] = set()
    used_supplements: set[str] = set()
    used_helpers: set[str] = set()
    used_token_selectors: set[str] = set()
    errors: list[InventoryError] = []
    parsed: list[tuple[Path, str, ast.Module]] = []
    module_constants: dict[str, dict[str, Any]] = {}
    for path in _source_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, UnicodeError, SyntaxError):
            errors.append(InventoryError(rel, 1, "<source>", "SourceParseError"))
            continue
        parsed.append((path, rel, tree))
        module = rel[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        constants: dict[str, Any] = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                value = _constant(node.value)
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if value is not None:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            constants[target.id] = value
        module_constants[module] = constants
    helper_definitions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {
        name: [] for name in helpers_policy if isinstance(name, str)
    }
    for _, rel, tree in parsed:
        for function_node in ast.walk(tree):
            if isinstance(function_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f"{rel}:{function_node.name}"
                if key in helper_definitions:
                    helper_definitions[key].append(function_node)
    for helper, config in helpers_policy.items():
        if (
            not isinstance(helper, str)
            or ":" not in helper
            or not isinstance(config, dict)
            or not isinstance(config.get("name_arg"), int)
            or isinstance(config.get("name_arg"), bool)
            or config.get("name_arg", -1) < 0
            or not isinstance(config.get("env_arg"), int)
            or isinstance(config.get("env_arg"), bool)
            or config.get("env_arg", -1) < 0
            or not isinstance(config.get("source_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", config["source_sha256"])
            or not isinstance(config.get("suffixes", []), list)
            or any(not isinstance(suffix, str) for suffix in config.get("suffixes", []))
        ):
            errors.append(
                InventoryError(_POLICY, 1, str(helper), "InvalidHelperPolicy")
            )
        else:
            definitions = helper_definitions.get(helper, [])
            if (
                len(definitions) != 1
                or ast_source_digest(definitions[0]) != config["source_sha256"]
            ):
                errors.append(
                    InventoryError(_POLICY, 1, helper, "InvalidHelperDefinition")
                )
    for _path, rel, tree in parsed:
        module = rel[:-3].replace("/", ".")
        package = (
            module[: -len(".__init__")]
            if module.endswith(".__init__")
            else module.rpartition(".")[0]
        )
        imported: dict[str, Any] = {}
        for import_node in ast.walk(tree):
            if not isinstance(import_node, ast.ImportFrom):
                continue
            target_module = import_node.module or ""
            if import_node.level:
                try:
                    target_module = importlib.util.resolve_name(
                        "." * import_node.level + target_module, package
                    )
                except ImportError:
                    continue
            for alias in import_node.names:
                value = module_constants.get(target_module, {}).get(alias.name)
                if value is not None:
                    imported[alias.asname or alias.name] = value
        result = _FileCollector(rel, tree, policy, imported)
        findings.extend(result.collect())
        errors.extend(result.errors)
        used_dynamic.update(result.used_dynamic)
        used_exclusions.update(result.used_exclusions)
        used_supplements.update(result.used_supplements)
        used_helpers.update(result.used_helpers)
        used_token_selectors.update(result.used_token_selectors)
    _validate_exclusions(root, policy, used_exclusions, errors)
    expected_dynamic = {row.get("site") for row in policy.get("dynamic", [])}
    expected_selectors = {
        row.get("site")
        for row in policy.get("metadata", {}).get("token_selectors", [])
        if isinstance(row, dict)
    }
    for site in sorted(
        site
        for site in expected_selectors
        if isinstance(site, str) and site not in used_token_selectors
    ):
        errors.append(
            InventoryError(
                str(site).split(":", 1)[0], 1, "<policy>", "StaleTokenSelector"
            )
        )
    expected_exclusions = {row.get("site") for row in policy.get("exclusions", [])}
    expected_supplements = {row.get("site") for row in policy.get("supplements", [])}
    for site in sorted(expected_dynamic - used_dynamic):
        errors.append(
            InventoryError(site.split(":", 1)[0], 1, "<policy>", "StaleDynamicPolicy")
        )
    for site in sorted(expected_exclusions - used_exclusions):
        errors.append(
            InventoryError(site.split(":", 1)[0], 1, "<policy>", "StaleExclusionPolicy")
        )
    for site in sorted(expected_supplements - used_supplements):
        errors.append(
            InventoryError(
                site.split(":", 1)[0], 1, "<policy>", "StaleSupplementPolicy"
            )
        )
    for helper in sorted(set(policy.get("helpers", {})) - used_helpers):
        errors.append(InventoryError(_POLICY, 1, helper, "StaleHelperPolicy"))
    if errors:
        raise sorted(errors, key=lambda e: (e.path, e.line, e.name, e.error_class))[0]
    names = {finding.name for finding in findings}
    purposes = policy.get("purposes", {})
    if not isinstance(purposes, dict):
        raise InventoryError(_POLICY, 1, "<policy>", "InvalidPurposeMap")
    for name in sorted(names):
        purpose = _purpose_for(name, purposes)
        if (
            not isinstance(purpose, str)
            or len(purpose.strip()) < 12
            or re.search(r"placeholder|todo|tbd|unknown", purpose, re.I)
        ):
            source = min(
                (item for item in findings if item.name == name),
                key=lambda item: (item.path, item.line),
            )
            raise InventoryError(source.path, source.line, name, "MissingPurpose")
    for name in sorted(set(purposes) - names):
        raise InventoryError(_POLICY, 1, name, "StalePurpose")
    return sorted(
        findings, key=lambda item: (item.name, item.path, item.line, item.symbol)
    )


def _validate_exclusions(
    root: Path,
    policy: dict[str, Any],
    used: set[str],
    errors: list[InventoryError],
) -> None:
    rows = policy.get("exclusions", [])
    if not isinstance(rows, list):
        errors.append(InventoryError(_POLICY, 1, "<policy>", "InvalidExclusion"))
        return
    for row in rows:
        if not isinstance(row, dict):
            errors.append(InventoryError(_POLICY, 1, "<policy>", "InvalidExclusion"))
            continue
        site = row.get("site")
        reason = row.get("reason")
        parts = site.split(":") if isinstance(site, str) else []
        if (
            len(parts) != 3
            or parts[0] not in _EXCLUSION_FILES
            or not parts[1].isdigit()
            or not isinstance(reason, str)
            or len(reason.strip()) < 12
            or not isinstance(row.get("symbol"), str)
            or not isinstance(row.get("source_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row.get("source_sha256", ""))
            or not isinstance(row.get("names"), list)
            or not row["names"]
            or any(not isinstance(name, str) or not name for name in row["names"])
            or len(row["names"]) != len(set(row["names"]))
            or (
                parts[0] == "omega_prime/grokbot/local_validation.py"
                and (
                    len(row["names"]) != 1
                    or any(not name.startswith("READINESS_") for name in row["names"])
                )
            )
        ):
            errors.append(InventoryError(_POLICY, 1, "<policy>", "InvalidExclusion"))
            continue
        if site not in used:
            errors.append(
                InventoryError(
                    parts[0], int(parts[1]), "<policy>", "StaleExclusionPolicy"
                )
            )


def _load_policy(root: Path) -> dict[str, Any]:
    path = root / _POLICY
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise InventoryError(_POLICY, 1, "<policy>", "PolicyReadError") from None
    if not isinstance(policy, dict):
        raise InventoryError(_POLICY, 1, "<policy>", "InvalidPolicy")
    return policy


def _purpose_map(policy: dict[str, Any], names: set[str]) -> dict[str, str]:
    purposes = policy.get("purposes", {})
    if not isinstance(purposes, dict):
        raise InventoryError(_POLICY, 1, "<policy>", "InvalidPurposeMap")
    rendered: dict[str, str] = {}
    for name in sorted(names):
        purpose = _purpose_for(name, purposes)
        if (
            not isinstance(purpose, str)
            or len(purpose.strip()) < 12
            or re.search(r"placeholder|todo|tbd|unknown", purpose, re.I)
        ):
            raise InventoryError(_POLICY, 1, name, "MissingPurpose")
        rendered[name] = purpose
    for name in sorted(set(purposes) - names):
        raise InventoryError(_POLICY, 1, name, "StalePurpose")
    return rendered


def render(root: Path) -> str:
    policy = _load_policy(root)
    findings = collect(root, policy)
    names = {finding.name for finding in findings}
    purposes = _purpose_map(policy, names)
    lines = [
        "# Configuration inventory",
        "",
        "This inventory covers names proven from production Python source only.",
        "It does not cover shell, YAML, interpolation-only inputs, arbitrary plugins, or embedded child-program code.",
        "A listed name does not imply that the setting is enabled or configured.",
        "The table records names and purposes only.",
        "",
        "| Variable | Purpose |",
        "| --- | --- |",
    ]
    for name in sorted(names):
        lines.append(f"| `{name}` | {purposes[name].replace('|', chr(92) + '|')} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omega-prime-env-inventory")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = (args.root or Path(__file__).resolve().parents[2]).resolve()
    out = args.out or root / "docs" / "configuration.md"
    try:
        fresh = render(root)
        if args.check:
            current = out.read_text(encoding="utf-8")
            if current.replace("\r\n", "\n") != fresh:
                raise InventoryError(out.as_posix(), 1, "<output>", "OutputDrift")
            return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(fresh, encoding="utf-8", newline="\n")
        return 0
    except InventoryError as exc:
        print(
            f"env_inventory: {exc.path}:{exc.line}: {exc.name}: {exc.error_class}",
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            f"env_inventory: {out.as_posix()}:1: <output>: OutputIOError",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

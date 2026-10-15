# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Notion Task Management Dashboard schema contract.

The contract is the Tasks database shape: a title, a status, and two
optional properties. It stores no workspace, page, or database id.
"""

from __future__ import annotations

from typing import Any

CONTRACT_VERSION = 1

REQUIRED_PROPERTIES: tuple[tuple[str, str], ...] = (
    ("Name", "title"),
    ("Status", "status"),
)
REQUIRED_STATUS: tuple[str, ...] = (
    "Not started",
    "Up next",
    "In progress",
    "Done",
)
OPTIONAL_PROPERTIES: tuple[tuple[str, str], ...] = (
    ("Due date", "date"),
    ("Project", "relation"),
)


def schema_gaps(database: dict[str, Any]) -> list[str]:
    """Names of required pieces missing or typed wrong. Empty means ready.

    A present optional property with the wrong type is a gap. An absent
    optional property is not.
    """
    props = database.get("properties")
    if not isinstance(props, dict):
        return ["properties"]
    gaps: list[str] = []
    for name, kind in REQUIRED_PROPERTIES:
        if _type_of(props.get(name)) != kind:
            gaps.append(name)
    if "Status" not in gaps:
        options = _status_names(props.get("Status"))
        for option in REQUIRED_STATUS:
            if option not in options:
                gaps.append(f"Status option {option}")
    for name, kind in OPTIONAL_PROPERTIES:
        prop = props.get(name)
        if prop is None:
            continue
        if _type_of(prop) != kind:
            gaps.append(name)
    return gaps


def _type_of(prop: Any) -> str:
    if not isinstance(prop, dict):
        return ""
    kind = prop.get("type")
    return kind if isinstance(kind, str) else ""


def _status_names(prop: Any) -> set[str]:
    if not isinstance(prop, dict):
        return set()
    status = prop.get("status")
    if not isinstance(status, dict):
        return set()
    options = status.get("options")
    if not isinstance(options, list):
        return set()
    names: set[str] = set()
    for option in options:
        if isinstance(option, dict) and isinstance(option.get("name"), str):
            names.add(option["name"])
    return names

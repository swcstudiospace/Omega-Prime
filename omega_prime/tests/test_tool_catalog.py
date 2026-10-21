"""Phase 45: the tool catalog renders every rostered tool and stays current."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from omega_prime.mcp_server import roster_names
from omega_prime.tooling.catalog import (
    FAMILIES,
    full_inventory_registry,
    main,
    render,
)

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent

_HEADING = re.compile(r"^## (\S+)\s*$", re.MULTILINE)


def _roster() -> list[str]:
    path = OMEGA_PRIME / "contracts" / "tool-rosters" / "omega-prime.yaml"
    return roster_names(path.read_text(encoding="utf-8"))


def test_exec_job_contracts_align_across_registry_roster_policy_and_catalog(
    tmp_path: Path,
) -> None:
    expected: dict[str, Any] = {
        "exec_job_start": {
            "name": "exec_job_start",
            "description": (
                "Start a screened argv process in the configured work directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["argv"],
            },
        },
        "exec_job_poll": {
            "name": "exec_job_poll",
            "description": "Check whether a process-local argv job has finished.",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
        "exec_job_wait": {
            "name": "exec_job_wait",
            "description": "Wait up to 30 seconds for a process-local argv job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "timeout": {"type": "number", "default": 1.0},
                },
                "required": ["job_id"],
            },
        },
        "exec_job_kill": {
            "name": "exec_job_kill",
            "description": "Terminate a process-local argv job.",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    }
    names = list(expected)
    roster = _roster()
    policy_path = OMEGA_PRIME / "contracts" / "policies" / "omega-prime.json"
    policy_allow = json.loads(policy_path.read_text(encoding="utf-8"))["tools"]["allow"]
    registry = full_inventory_registry(ROOT, tmp_path)
    schemas = {
        schema["function"]["name"]: schema["function"] for schema in registry.schemas()
    }
    family_names = [name for _, tools in FAMILIES for name in tools]
    catalog = render(ROOT, tmp_path)

    assert set(policy_allow) == set(roster)
    assert len(policy_allow) == len(set(policy_allow))
    assert set(family_names) == set(roster)
    assert len(family_names) == len(set(family_names))
    assert set(schemas) == set(roster)
    assert {name: schemas[name] for name in names} == expected
    assert all(registry.approval_required(name) for name in names)
    assert "pin_bump_apply" not in roster
    assert "pin_bump_apply" not in policy_allow
    assert "pin_bump_apply" not in schemas
    assert "pin_bump_apply" not in catalog
    assert _HEADING.findall(catalog) == roster
    for name, schema in expected.items():
        required = ", ".join(schema["parameters"]["required"])
        section = catalog.split(f"## {name}\n", 1)[1].split("\n## ", 1)[0]
        assert "- Family: Exec jobs" in section
        assert "- Approval: required" in section
        assert f"- Required params: {required}" in section
        assert schema["description"] in section


def test_render_preserves_roster_order_exactly_once(tmp_path: Path) -> None:
    text = render(ROOT, tmp_path)
    headings = _HEADING.findall(text)
    assert headings == _roster()


def test_check_passes_on_committed_file_and_fails_on_drift(tmp_path: Path) -> None:
    assert main(["--check"]) == 0
    stale = tmp_path / "catalog.md"
    stale.write_text("# Tool catalog\n\nstale\n", encoding="utf-8")
    assert main(["--check", "--out", str(stale)]) == 1


def test_unknown_rostered_definition_refuses_catalog_without_overwriting_output(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixture-root"
    roster = fixture_root / "omega_prime/contracts/tool-rosters/omega-prime.yaml"
    roster.parent.mkdir(parents=True)
    unknown = "catalog_fixture_tool_with_no_registered_definition"
    roster.write_text(f"tools:\n  - {unknown}\n", encoding="utf-8")
    output = tmp_path / "catalog.md"
    output.write_bytes(b"# Maintained output sentinel\n\x00")
    output.chmod(0o640)
    before = (output.read_bytes(), output.stat())

    # Real registry/schema construction; no fake schema or registry echo.
    with pytest.raises(SystemExit) as caught:
        main(["--root", str(fixture_root), "--out", str(output)])

    assert unknown in str(caught.value)
    after = output.stat()
    assert output.read_bytes() == before[0]
    assert (after.st_mode, after.st_mtime_ns, after.st_ino) == (
        before[1].st_mode,
        before[1].st_mtime_ns,
        before[1].st_ino,
    )

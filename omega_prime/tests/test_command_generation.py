# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Generated /omega-<name> commands: parser, naming rule, collisions, dispatch.

Expected values come from the checked-in skills, routines and curated
catalog, never from the generated output under test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import omega_prime.commands as commands
from omega_prime.commands import (
    COMMANDS,
    command_catalog,
    execute_command,
    parse_omega_command,
)
from omega_prime.commands.generated import GenerationError, generate_commands
from omega_prime.commands.workflows import run_workflow
from omega_prime.mcp_server import default_registry
from omega_prime.skills_runtime.manager import skill_manage
from omega_prime.tests.test_omega_commands import RecordingRegistry

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent
SKILLS = OMEGA_PRIME / "skills"
ROUTINES = OMEGA_PRIME / "routines"
CURATED = [spec.name for spec in COMMANDS]

# The locked collision outcomes (CONTEXT collision rule).
SUFFIXED = {
    ("skill", "python"): "omega-python-skill",
    ("routine", "onboard"): "omega-onboard-routine",
    ("routine", "connectors"): "omega-connectors-routine",
    ("skill", "ultrathink"): "omega-ultrathink-skill",
    ("routine", "ultrathink"): "omega-ultrathink-routine",
}
WORKFLOW_ROUTINES = {
    "omega-onboard-routine": "onboard",
    "omega-python-clean": "python-clean",
    "omega-connectors-routine": "connectors",
}
TEXT_ROUTINES = {
    "omega-desk-lead": "desk-lead",
    "omega-nightly": "nightly",
    "omega-sweep": "sweep",
    "omega-ultrathink-routine": "ultrathink",
}


def _expected() -> dict[str, tuple[str, str]]:
    """Generated name -> (kind, stem), derived from the checked-in files."""
    skills = sorted(path.parent.name for path in SKILLS.glob("*/SKILL.md"))
    routines = sorted(path.stem for path in ROUTINES.glob("*.md"))
    assert len(skills) == 27, skills
    assert len(routines) == 7, routines
    expected: dict[str, tuple[str, str]] = {}
    for kind, stems in (("skill", skills), ("routine", routines)):
        for stem in stems:
            name = SUFFIXED.get((kind, stem), "omega-" + stem.removeprefix("omega-"))
            expected[name] = (kind, stem)
    return expected


def _body_after_frontmatter(text: str) -> str:
    lines = text.splitlines(keepends=True)
    assert lines[0].strip() == "---"
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[index + 1 :])
    raise AssertionError("frontmatter is not closed")


def _skill(root: Path, folder: str, description: str = "Use it.") -> Path:
    path = root / "omega_prime" / "skills" / folder / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {folder}\ndescription: {description}\n---\n\n"
        f'<skill name="{folder}" version="1">\nBody of {folder}.\n</skill>\n',
        encoding="utf-8",
    )
    return path


def _routine(root: Path, stem: str) -> Path:
    path = root / "omega_prime" / "routines" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<routine name="{stem}" version="1">\n'
        f"<summary>Run the {stem} steps.</summary>\n"
        f"Run the {stem} steps.\n</routine>\n",
        encoding="utf-8",
    )
    return path


def test_parser_accepts_multi_segment_whole_messages() -> None:
    assert parse_omega_command("/omega-code-review") == ("omega-code-review", "")
    assert parse_omega_command("/omega-python-clean x") == ("omega-python-clean", "x")
    assert parse_omega_command("/OMEGA-Desk-Lead") == ("omega-desk-lead", "")
    assert parse_omega_command("  /omega-desk-lead  run it  ") == (
        "omega-desk-lead",
        "run it",
    )
    assert parse_omega_command("/omega-help") == ("omega-help", "")
    assert parse_omega_command("/omega-recall ships") == ("omega-recall", "ships")


@pytest.mark.parametrize(
    "text",
    [
        "/omega-",
        "/omega--x",
        "/omega-x-",
        "/omega-a_b",
        "/omega-../x",
        "please run /omega-help",
        "run /omega-code-review now",
    ],
)
def test_parser_rejects_malformed_names_and_prose(text: str) -> None:
    assert parse_omega_command(text) is None
    body = execute_command(text, RecordingRegistry(), {})
    assert body["ok"] is False
    assert body["error"] == "not_a_command"


def test_help_lists_curated_then_generated(tmp_path: Path) -> None:
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    body = json.loads(registry.dispatch("omega_command", {"command": "/omega-help"}))
    assert body["ok"] is True, body
    names = [row["name"] for row in body["result"]["commands"]]
    expected = _expected()
    assert len(names) == 58
    assert names[:24] == CURATED
    assert names[24:] == sorted(expected)
    suffixed = [name for name in names[24:] if name.endswith(("-skill", "-routine"))]
    assert sorted(suffixed) == sorted(SUFFIXED.values())
    assert "omega-commands" in names
    assert "omega-omega-commands" not in names
    for row in body["result"]["commands"]:
        assert row["summary"].strip(), row
        assert row["usage"].startswith(f"/{row['name']}"), row


def test_generated_entries_carry_kind_source_and_description() -> None:
    generated = generate_commands(ROOT, CURATED)
    expected = _expected()
    assert [spec.name for spec in generated] == sorted(expected)
    for spec in generated:
        kind, stem = expected[spec.name]
        assert spec.kind == kind
        if kind == "skill":
            assert spec.source == f"omega_prime/skills/{stem}/SKILL.md"
        else:
            assert spec.source == f"omega_prime/routines/{stem}.md"
        assert (ROOT / spec.source).is_file()
        assert spec.summary.strip()
        assert spec.needs_args is False


def test_two_renders_are_byte_identical_in_separate_processes(tmp_path: Path) -> None:
    script = (
        "import dataclasses, json\n"
        "from omega_prime.commands import COMMANDS\n"
        "from omega_prime.commands.generated import CATALOG_ROOT, generate_commands\n"
        "specs = generate_commands(CATALOG_ROOT, [c.name for c in COMMANDS])\n"
        "print(json.dumps([dataclasses.asdict(s) for s in specs], sort_keys=True))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    renders = []
    for _ in range(2):
        done = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        renders.append(done.stdout)
    assert renders[0] == renders[1]
    rows = json.loads(renders[0])
    assert len(rows) == 34
    assert [row["name"] for row in rows] == sorted(row["name"] for row in rows)


def test_collision_rule_in_a_tmp_tree(tmp_path: Path) -> None:
    _skill(tmp_path, "omega-help")
    _skill(tmp_path, "omega-tools")
    _skill(tmp_path, "python")
    _skill(tmp_path, "ultrathink")
    _routine(tmp_path, "ultrathink")
    _routine(tmp_path, "onboard")
    _routine(tmp_path, "desk-lead")
    names = {spec.name: spec.source for spec in generate_commands(tmp_path, CURATED)}
    assert names == {
        "omega-help-skill": "omega_prime/skills/omega-help/SKILL.md",
        "omega-tools": "omega_prime/skills/omega-tools/SKILL.md",
        "omega-python-skill": "omega_prime/skills/python/SKILL.md",
        "omega-ultrathink-skill": "omega_prime/skills/ultrathink/SKILL.md",
        "omega-ultrathink-routine": "omega_prime/routines/ultrathink.md",
        "omega-onboard-routine": "omega_prime/routines/onboard.md",
        "omega-desk-lead": "omega_prime/routines/desk-lead.md",
    }


@pytest.mark.parametrize(
    ("first", "second"),
    [("python", "python-skill"), ("foo", "omega-foo")],
)
def test_leftover_collision_aborts_naming_both_sources(
    tmp_path: Path, first: str, second: str
) -> None:
    _skill(tmp_path, first)
    _skill(tmp_path, second)
    with pytest.raises(GenerationError) as caught:
        generate_commands(tmp_path, CURATED)
    message = str(caught.value)
    assert f"omega_prime/skills/{first}/SKILL.md" in message
    assert f"omega_prime/skills/{second}/SKILL.md" in message


@pytest.mark.parametrize("folder", ["Bad_Name", "x" * 59, "omega-"])
def test_invalid_name_aborts_naming_its_source(tmp_path: Path, folder: str) -> None:
    _skill(tmp_path, "alpha")
    _skill(tmp_path, folder)
    with pytest.raises(GenerationError) as caught:
        generate_commands(tmp_path, CURATED)
    assert f"omega_prime/skills/{folder}/SKILL.md" in str(caught.value)


def test_longest_valid_name_is_accepted(tmp_path: Path) -> None:
    _skill(tmp_path, "x" * 58)
    names = [spec.name for spec in generate_commands(tmp_path, CURATED)]
    assert names == ["omega-" + "x" * 58]
    assert len(names[0]) == 64


def test_generation_conflict_is_a_failed_command_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skill(tmp_path, "python")
    _skill(tmp_path, "python-skill")
    monkeypatch.setattr(commands, "_CATALOG_ROOT", tmp_path)
    registry = RecordingRegistry([{"seats": []}])
    help_body = execute_command("/omega-help", registry, {})
    assert help_body["ok"] is False
    assert help_body["error"] == "catalog_generation_failed"
    listed = [row["name"] for row in help_body["result"]["commands"]]
    assert listed == CURATED
    generated = execute_command("/omega-python-skill", registry, {})
    assert generated["ok"] is False
    assert generated["error"] == "catalog_generation_failed"
    assert registry.calls == []
    roster = execute_command("/omega-roster", registry, {})
    assert roster["ok"] is True
    assert [call[0] for call in registry.calls] == ["lead_roster_status"]


def test_every_generated_skill_returns_its_instructions() -> None:
    skills = {
        name: stem for name, (kind, stem) in _expected().items() if kind == "skill"
    }
    assert len(skills) == 27
    for name, stem in skills.items():
        registry = RecordingRegistry()
        body = execute_command(f"/{name} focus on tests", registry, {})
        assert body["ok"] is True, body
        assert body["command"] == name
        result = body["result"]
        source = f"omega_prime/skills/{stem}/SKILL.md"
        text = (ROOT / source).read_text(encoding="utf-8")
        assert result["kind"] == "skill"
        assert result["source"] == source
        assert result["description"].strip()
        assert result["instructions"] == _body_after_frontmatter(text)
        assert result["args"] == "focus on tests"
        assert registry.calls == []


def test_skill_dispatch_through_the_omega_command_tool(tmp_path: Path) -> None:
    registry = default_registry(
        ROOT,
        tmp_path / "home",
        env={"OMEGA_PRIME_STATE_DIR": str(tmp_path / "state")},
    )
    raw = registry.dispatch("omega_command", {"command": "/omega-code-review"})
    body = json.loads(raw)
    text = (SKILLS / "code-review" / "SKILL.md").read_text(encoding="utf-8")
    assert body["ok"] is True, body
    assert body["command"] == "omega-code-review"
    assert body["result"]["kind"] == "skill"
    assert body["result"]["instructions"] == _body_after_frontmatter(text)


@pytest.mark.parametrize(("name", "workflow"), sorted(WORKFLOW_ROUTINES.items()))
def test_workflow_routines_run_their_workflow(name: str, workflow: str) -> None:
    generated = RecordingRegistry()
    body = execute_command(f"/{name}", generated, {})
    direct = RecordingRegistry()
    expected = run_workflow(workflow, direct, {})
    assert body["command"] == name
    assert body["ok"] is expected["ok"]
    assert body["result"] == expected
    assert generated.calls == direct.calls


@pytest.mark.parametrize(
    ("name", "curated"),
    [
        ("omega-onboard-routine", "omega-onboard"),
        ("omega-python-clean", "omega-python"),
    ],
)
def test_workflow_routines_match_their_curated_twin(name: str, curated: str) -> None:
    generated = RecordingRegistry()
    twin = RecordingRegistry()
    body = execute_command(f"/{name}", generated, {})
    expected = execute_command(f"/{curated}", twin, {})
    assert generated.calls == twin.calls
    assert body["ok"] is expected["ok"]
    assert body["result"] == expected["result"]


@pytest.mark.parametrize(("name", "stem"), sorted(TEXT_ROUTINES.items()))
def test_other_routines_load_their_text_without_dispatch(name: str, stem: str) -> None:
    registry = RecordingRegistry()
    body = execute_command(f"/{name} today", registry, {})
    source = f"omega_prime/routines/{stem}.md"
    assert body["ok"] is True, body
    assert body["result"]["kind"] == "routine"
    assert body["result"]["source"] == source
    assert body["result"]["instructions"] == (ROOT / source).read_text(encoding="utf-8")
    assert body["result"]["args"] == "today"
    assert registry.calls == []


def test_args_are_never_used_as_a_path() -> None:
    registry = RecordingRegistry()
    body = execute_command("/omega-code-review ../../etc/passwd", registry, {})
    text = (SKILLS / "code-review" / "SKILL.md").read_text(encoding="utf-8")
    assert body["ok"] is True, body
    assert body["result"]["source"] == "omega_prime/skills/code-review/SKILL.md"
    assert body["result"]["instructions"] == _body_after_frontmatter(text)
    assert body["result"]["args"] == "../../etc/passwd"
    routine = execute_command("/omega-desk-lead /etc/passwd", registry, {})
    assert routine["ok"] is True, routine
    assert routine["result"]["source"] == "omega_prime/routines/desk-lead.md"
    assert registry.calls == []


def test_source_deleted_after_generation_is_source_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = _skill(tmp_path, "alpha")
    routine = _routine(tmp_path, "onboard")
    monkeypatch.setattr(commands, "_CATALOG_ROOT", tmp_path)
    registry = RecordingRegistry()
    first = execute_command("/omega-alpha", registry, {})
    assert first["ok"] is True, first
    assert first["result"]["instructions"] == (
        '\n<skill name="alpha" version="1">\nBody of alpha.\n</skill>\n'
    )
    skill.unlink()
    routine.unlink()
    gone = execute_command("/omega-alpha", registry, {})
    assert gone["ok"] is False
    assert gone["error"] == "source_missing"
    assert gone["result"]["source"] == "omega_prime/skills/alpha/SKILL.md"
    workflow = execute_command("/omega-onboard-routine", registry, {})
    assert workflow["ok"] is False
    assert workflow["error"] == "source_missing"
    assert registry.calls == []


def test_skill_manage_plain_body_loads_and_broken_envelope_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = tmp_path / "omega_prime" / "skills"
    skills.mkdir(parents=True)
    monkeypatch.setattr(commands, "_CATALOG_ROOT", tmp_path)
    plain = "---\nname: alpha\ndescription: Use it.\n---\n\nPlain steps.\n"
    created = json.loads(
        skill_manage("create", "alpha", content=plain, skills_root=skills)
    )
    assert created["success"] is True
    registry = RecordingRegistry()
    loaded = execute_command("/omega-alpha", registry, {})
    assert loaded["ok"] is True, loaded
    instructions = loaded["result"]["instructions"]
    assert '<skill name="alpha" version="1">' in instructions
    assert instructions.rstrip().endswith("</skill>")

    skill_md = skills / "alpha" / "SKILL.md"
    valid = (
        "---\nname: alpha\ndescription: Use it.\n---\n\n"
        '<skill name="alpha" version="1">\nKeep.\n</skill>\n'
    )
    rewritten = json.loads(
        skill_manage("edit", "alpha", content=valid, skills_root=skills)
    )
    assert rewritten["success"] is True
    original = skill_md.read_bytes()
    assert original == valid.encode("utf-8")
    broken = (
        "---\nname: alpha\ndescription: Use it.\n---\n<skill></skill>\nstill\n</skill>"
    )
    refused = json.loads(
        skill_manage("edit", "alpha", content=broken, skills_root=skills)
    )
    assert refused["success"] is False
    assert skill_md.read_bytes() == original


def test_skill_manage_wraps_prose_mentions_and_keeps_indentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = tmp_path / "omega_prime" / "skills"
    skills.mkdir(parents=True)
    monkeypatch.setattr(commands, "_CATALOG_ROOT", tmp_path)
    plain = (
        "---\nname: alpha\ndescription: Use it.\n---\n"
        "<skills>list</skills>\n"
        "Run /omega-<skill> for one skill.\n"
        "    indented example\n"
        "    still indented\n"
    )
    created = json.loads(
        skill_manage("create", "alpha", content=plain, skills_root=skills)
    )
    assert created["success"] is True, created
    text = (skills / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "<skills>list</skills>" in text
    assert "/omega-<skill>" in text
    assert "\n    indented example\n    still indented\n" in text
    registry = RecordingRegistry()
    loaded = execute_command("/omega-alpha", registry, {})
    assert loaded["ok"] is True, loaded
    instructions = loaded["result"]["instructions"]
    assert instructions.strip().startswith('<skill name="alpha" version="1">')
    assert instructions.rstrip().endswith("</skill>")
    assert "/omega-<skill>" in instructions


def test_skill_manage_refuses_a_closing_skill_tag_in_prose(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    plain = (
        "---\nname: alpha\ndescription: Use it.\n---\n"
        "\nThe loader stops at </skill> even inside a sentence.\n"
    )
    refused = json.loads(
        skill_manage("create", "alpha", content=plain, skills_root=skills)
    )
    assert refused["success"] is False
    assert not (skills / "alpha").exists()


@pytest.mark.parametrize(
    "body",
    [
        '<skill name="alpha" version="1">\nbreak\n</skill>\ninjected\n</skill>\n',
        '<skill name="alpha" version="1">\nbreak\n</skill >\ninjected\n</skill>\n',
    ],
)
def test_skill_early_close_is_source_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    skill = _skill(tmp_path, "alpha")
    skill.write_text(
        "---\nname: alpha\ndescription: Use it.\n---\n\n" + body,
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "_CATALOG_ROOT", tmp_path)
    registry = RecordingRegistry()
    result = execute_command("/omega-alpha", registry, {})
    assert result["ok"] is False
    assert result["error"] == "source_invalid"


def test_routine_early_close_is_source_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routine = _routine(tmp_path, "desk-lead")
    routine.write_text(
        '<routine name="desk-lead" version="1">\n'
        "<summary>Run the desk-lead steps.</summary>\n"
        "</routine>\ninjected\n</routine>\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "_CATALOG_ROOT", tmp_path)
    registry = RecordingRegistry()
    result = execute_command("/omega-desk-lead", registry, {})
    assert result["ok"] is False
    assert result["error"] == "source_invalid"


def test_unknown_command_lists_every_name_and_does_not_dispatch() -> None:
    registry = RecordingRegistry()
    body = execute_command("/omega-nope", registry, {})
    assert body["ok"] is False
    assert body["error"] == "unknown_command"
    assert body["commands"] == CURATED + sorted(_expected())
    assert registry.calls == []
    near = execute_command("/omega-desk-leads", registry, {})
    assert near["error"] == "unknown_command"
    assert registry.calls == []


def test_command_catalog_matches_help_order() -> None:
    names = [row["name"] for row in command_catalog()]
    assert names == CURATED + sorted(_expected())

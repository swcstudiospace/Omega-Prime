"""The v17 XML prompt standard: every first-party prompt is XML-wrapped."""

from __future__ import annotations

from pathlib import Path

from omega_prime.tooling.prompt_format import check

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_repo_prompts_conform_to_xml_standard() -> None:
    assert check(REPO_ROOT) == []


def test_unwrapped_skill_body_is_named(tmp_path: Path) -> None:
    skill = tmp_path / "omega_prime" / "skills" / "bad-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: bad-skill\n"
        "description: A skill with an unwrapped body.\n"
        "---\n"
        "\n"
        "This body is not wrapped in a skill envelope.\n",
        encoding="utf-8",
    )

    problems = check(tmp_path)

    assert any(str(skill.relative_to(tmp_path)) in problem for problem in problems)


def test_routine_missing_closing_tag_is_named(tmp_path: Path) -> None:
    routine = tmp_path / "omega_prime" / "routines" / "bad-routine.md"
    routine.parent.mkdir(parents=True)
    routine.write_text(
        '<routine name="bad-routine" version="1">\n'
        "<summary>A routine missing its closing tag.</summary>\n"
        "This routine never closes.\n",
        encoding="utf-8",
    )

    problems = check(tmp_path)

    assert any(str(routine.relative_to(tmp_path)) in problem for problem in problems)

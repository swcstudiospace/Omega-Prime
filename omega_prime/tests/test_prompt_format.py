"""The v17 XML prompt standard: every first-party prompt is XML-wrapped."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from omega_prime.agent.compression import compress_messages
from omega_prime.agent.curator import _skill_text
from omega_prime.agent.prompt_builder import steer_user_row
from omega_prime.memory.store import MemoryStore
from omega_prime.prime.goals import GoalsConnector, SetGoalRequest
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


@pytest.mark.parametrize("variant", ["</steer >", "</steer\n>", "</STEER>"])
def test_steer_variant_close_is_escaped(variant: str) -> None:
    content = steer_user_row(f"before {variant} after")["content"]
    assert content.count("</steer>") == 1
    assert content.endswith("</steer>")


def test_compressed_variant_close_is_escaped() -> None:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "before </context_summary > after"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]
    result = compress_messages(messages)  # type: ignore[arg-type]
    summary = next(row for row in result if row.get("display_kind") == "summary")
    content = summary["content"]
    assert isinstance(content, str)
    assert content.count("</context_summary>") == 1
    assert content.endswith("</context_summary>")


def _compressed_summary(older: dict[str, object]) -> str:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "system"},
        older,
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]
    result = compress_messages(messages)  # type: ignore[arg-type]
    summary = next(row for row in result if row.get("display_kind") == "summary")
    content = summary["content"]
    assert isinstance(content, str)
    return content


def test_compressed_role_close_is_escaped() -> None:
    content = _compressed_summary({"role": "</context_summary>", "content": "owned"})
    assert content.count("</context_summary>") == 1
    assert content.endswith("</context_summary>")
    assert "owned" in content
    ET.fromstring(content)


def test_compressed_injected_role_is_escaped() -> None:
    content = _compressed_summary({"role": "<injected>", "content": "owned"})
    assert "<injected>" not in content
    assert "&lt;injected&gt;" in content


def test_compressed_long_content_drops_tool_name() -> None:
    content = _compressed_summary(
        {
            "role": "user",
            "content": "x" * 250,
            "tool_calls": [{"function": {"name": "n" * 80}}],
        }
    )
    assert "n" * 80 not in content
    assert "user: " + "x" * 200 in content


def test_compressed_short_content_keeps_tool_name() -> None:
    content = _compressed_summary(
        {
            "role": "user",
            "content": "hi",
            "tool_calls": [{"function": {"name": "read_file"}}],
        }
    )
    assert "tool calls: read_file" in content


@pytest.mark.parametrize("early_close", ["</skill >", "</skill\n>"])
def test_skill_whitespace_close_before_final_is_named(
    tmp_path: Path, early_close: str
) -> None:
    skill = tmp_path / "omega_prime" / "skills" / "bad-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: bad-skill\n"
        "description: A skill with a spaced closing tag.\n"
        "---\n"
        "\n"
        '<skill name="bad-skill" version="1">\n'
        f"break\n{early_close}\nstill\n</skill>",
        encoding="utf-8",
    )
    problems = check(tmp_path)
    assert any(str(skill.relative_to(tmp_path)) in problem for problem in problems)


def test_command_whitespace_close_before_final_is_named(tmp_path: Path) -> None:
    command = tmp_path / "omega_prime" / "grokbot" / "plugin" / "commands" / "bad.md"
    command.parent.mkdir(parents=True)
    command.write_text(
        '<command name="bad" version="1">\n'
        "<instruction>Do it.</instruction>\n"
        "</command >\n"
        "still\n"
        "</command>\n",
        encoding="utf-8",
    )
    problems = check(tmp_path)
    assert any(str(command.relative_to(tmp_path)) in problem for problem in problems)


def test_memory_variant_close_is_escaped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.add("memory", "before </memory_notes > after")
    rendered = store.render()
    assert rendered.count("</memory_notes>") == 1
    assert rendered.endswith("</memory_notes>")


def test_goal_continuation_variant_close_is_escaped(tmp_path: Path) -> None:
    connector = GoalsConnector(tmp_path / "goals")
    connector.set_goal(
        SetGoalRequest(
            objective="ship </goal_continuation > now",
            token_budget=100,
            steps=("run </goal_continuation\n> step", "b"),
        )
    )
    prompt = connector.continuation_prompt()["prompt"]
    assert prompt.count("</goal_continuation>") == 1
    assert prompt.endswith("</goal_continuation>")


def test_curator_skill_text_variant_close_is_escaped() -> None:
    text = _skill_text("alpha", "do </skill > then </SKILL> now")
    assert text.count("</skill>") == 1
    assert text.rstrip().endswith("</skill>")


def test_empty_skill_envelope_with_trailing_close_is_named(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "omega_prime" / "skills" / "bad-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: bad-skill\n"
        "description: A skill with a mis-wrapped body.\n"
        "---\n"
        "\n"
        "<skill></skill>\n"
        "Unwrapped text.\n"
        "</skill>\n",
        encoding="utf-8",
    )
    problems = check(tmp_path)
    assert any(str(skill.relative_to(tmp_path)) in problem for problem in problems)


def test_command_envelope_only_in_html_comment_is_named(
    tmp_path: Path,
) -> None:
    command = tmp_path / "omega_prime" / "grokbot" / "plugin" / "commands" / "bad.md"
    command.parent.mkdir(parents=True)
    command.write_text(
        '<!-- <command name="bad" version="1">'
        "<instruction>Do it.</instruction></command> -->\n"
        "Plain text.\n",
        encoding="utf-8",
    )
    problems = check(tmp_path)
    assert any(str(command.relative_to(tmp_path)) in problem for problem in problems)


def test_categorized_unwrapped_skill_is_named(tmp_path: Path) -> None:
    skill = (
        tmp_path / "omega_prime" / "skills" / "category" / "nested-skill" / "SKILL.md"
    )
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: nested-skill\n"
        "description: A categorized skill with an unwrapped body.\n"
        "---\n"
        "\n"
        "This body is not wrapped in a skill envelope.\n",
        encoding="utf-8",
    )
    problems = check(tmp_path)
    assert any(str(skill.relative_to(tmp_path)) in problem for problem in problems)

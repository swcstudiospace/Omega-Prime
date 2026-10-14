"""Check that every first-party prompt follows the v17 XML prompt standard.

Every prompt this application authors is wrapped in an XML envelope carrying
``version="1"``: the seat prompt and shared core directives, the assembled
prompt, skill bodies (``<skill>``), routines (``<routine>``), generated slash
commands (``<command>``) and the runtime builders (system prompt, steer rows,
context summaries, memory blocks and advisor notes).

Exclusions: the paths and surfaces in ``EXCLUDED_FROM_STANDARD`` are not
application prompts for this standard and are never checked here.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Not application prompts for this standard:
# - ``omega_prime/redteam/pack/`` is vendored byte-verbatim upstream content,
#   pinned by the provenance gate, and keeps its upstream bytes.
# - ``omega_prime/grokbot/templates/OMEGA_PRIME.md`` is an external (Grok)
#   import format; it carries no prompt body of this application's.
# - ``omega_prime/evals/cases/`` is eval case data, not a prompt.
# - Tool description strings are tool metadata, not application prompts.
EXCLUDED_FROM_STANDARD: tuple[str, ...] = (
    "omega_prime/redteam/pack/",
    "omega_prime/grokbot/templates/OMEGA_PRIME.md",
    "omega_prime/evals/cases/",
    "tool description strings",
)

_SEAT_PROMPTS: tuple[tuple[str, str], ...] = (
    ("omega_prime/prompts/bot-00-omega-prime.xml", "agent"),
    ("omega_prime/prompts/_shared/core-directives.xml", "core_directives"),
)
_ASSEMBLED = "prompts-assembled/OMEGA_PRIME.xml"
_ASSEMBLED_PATH = f"omega_prime/{_ASSEMBLED}"
_SKILLS_DIR = "omega_prime/skills"
_ROUTINES_DIR = "omega_prime/routines"
_COMMANDS_DIR = "omega_prime/grokbot/plugin/commands"

_XML_DECLARATION_RE = re.compile(r"<\?xml.*?\?>", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_ASSEMBLED_PLACEHOLDERS: tuple[str, ...] = (
    "{{DEFAULT_BRANCH}}",
    "{{BOT_ID}}",
    "{{ROSTER_VERSION}}",
)


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _read(root: Path, relative: str, problems: list[str]) -> str | None:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"{relative}: cannot read file: {exc}")
        return None
    except UnicodeDecodeError as exc:
        problems.append(f"{relative}: not valid UTF-8: {exc}")
        return None


def _seat_prompt_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for relative, expected_root in _SEAT_PROMPTS:
        path = root / relative
        if not path.is_file():
            problems.append(f"{relative}: file is missing")
            continue
        try:
            element = ET.parse(path).getroot()
        except ET.ParseError as exc:
            problems.append(f"{relative}: does not parse as strict XML: {exc}")
            continue
        except OSError as exc:
            problems.append(f"{relative}: cannot read file: {exc}")
            continue
        if element.tag != expected_root:
            problems.append(
                f"{relative}: root element must be <{expected_root}>, "
                f"found <{element.tag}>"
            )
    return problems


def _strip_assembled_declarations(text: str) -> str:
    body = text
    leading = _XML_DECLARATION_RE.match(body)
    if leading is not None:
        body = body[leading.end() :]
    # Find the first root tag, skipping comments before it.
    cursor = body
    while True:
        cursor = cursor.lstrip()
        if cursor.startswith("<!--"):
            end = cursor.find("-->")
            if end == -1:
                return body
            cursor = cursor[end + 3 :]
            continue
        break
    root_match = re.match(r"<([A-Za-z_][\w.:-]*)", cursor)
    if root_match is None:
        return body
    close = f"</{root_match.group(1)}>"
    close_index = body.find(close)
    if close_index == -1:
        return body
    after = body[close_index + len(close) :]
    stripped_after = after.lstrip()
    if stripped_after.startswith("<?xml"):
        declaration = _XML_DECLARATION_RE.match(stripped_after)
        if declaration is not None:
            body = (
                body[: close_index + len(close)] + stripped_after[declaration.end() :]
            )
    return body


def _assembled_problems(root: Path) -> list[str]:
    problems: list[str] = []
    text = _read(root, _ASSEMBLED_PATH, problems)
    if text is None:
        return problems
    for placeholder in _ASSEMBLED_PLACEHOLDERS:
        if placeholder in text:
            problems.append(
                f"{_ASSEMBLED_PATH}: unfilled placeholder remains: {placeholder}"
            )
    # The assembled file concatenates two documents, each with its own
    # declaration; strip only a declaration at the file start and one
    # immediately after the first root's closing tag before wrapping.
    body = _strip_assembled_declarations(text)
    try:
        element = ET.fromstring(f"<assembled>{body}</assembled>")
    except ET.ParseError as exc:
        problems.append(f"{_ASSEMBLED_PATH}: does not parse as XML: {exc}")
        return problems
    roots = [child.tag for child in element]
    if roots[:2] != ["core_directives", "agent"]:
        problems.append(
            f"{_ASSEMBLED_PATH}: first two element roots must be "
            f"<core_directives> then <agent>, found {roots[:2]}"
        )
    return problems


def _envelope_problems(
    display: str, stripped: str, expected_open: str, close: str, kind: str
) -> list[str]:
    problems: list[str] = []
    if not stripped.startswith(expected_open):
        problems.append(f"{display}: {kind} must start with {expected_open}")
    if stripped.count(expected_open) != 1:
        problems.append(f"{display}: {kind} must contain {expected_open} exactly once")
    name = close[2:-1]
    match = re.search(rf"</{re.escape(name)}\s*>", stripped)
    if match is None:
        problems.append(f"{display}: {kind} must end with {close}")
    elif match.group(0) != close or match.end() != len(stripped):
        problems.append(f"{display}: {kind} first {close} must be the final close")
    return problems


def _skill_problems(root: Path) -> list[str]:
    from omega_prime.skills_runtime.guards import parse_frontmatter

    problems: list[str] = []
    directory = root / _SKILLS_DIR
    if not directory.is_dir():
        return [f"{_SKILLS_DIR}: directory is missing"]
    for path in sorted(directory.rglob("SKILL.md")):
        display = _display(root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{display}: cannot read file: {exc}")
            continue
        _mapping, body, error = parse_frontmatter(text)
        if error is not None or body is None:
            problems.append(f"{display}: frontmatter does not parse: {error}")
            continue
        folder = path.parent.name
        stripped = body.strip()
        expected_open = f'<skill name="{folder}" version="1">'
        problems.extend(
            _envelope_problems(
                display, stripped, expected_open, "</skill>", "skill body"
            )
        )
    return problems


def _routine_problems(root: Path) -> list[str]:
    problems: list[str] = []
    directory = root / _ROUTINES_DIR
    if not directory.is_dir():
        return [f"{_ROUTINES_DIR}: directory is missing"]
    for path in sorted(directory.glob("*.md")):
        display = _display(root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{display}: cannot read file: {exc}")
            continue
        stripped = text.strip()
        expected_open = f'<routine name="{path.stem}" version="1">'
        problems.extend(
            _envelope_problems(
                display, stripped, expected_open, "</routine>", "routine"
            )
        )
        summaries = list(_SUMMARY_RE.finditer(stripped))
        if len(summaries) != 1:
            problems.append(
                f"{display}: routine must contain exactly one <summary>...</summary>"
            )
        else:
            summary = summaries[0]
            if not summary.group(1).strip():
                problems.append(f"{display}: routine summary must not be empty")
            open_end = len(expected_open) if stripped.startswith(expected_open) else 0
            close_index = stripped.find("</routine>")
            if summary.start() < open_end or (
                close_index != -1 and summary.end() > close_index
            ):
                problems.append(
                    f"{display}: routine summary must be inside the routine envelope"
                )
    return problems


def _command_problems(root: Path) -> list[str]:
    problems: list[str] = []
    directory = root / _COMMANDS_DIR
    if not directory.is_dir():
        return [f"{_COMMANDS_DIR}: directory is missing"]
    for path in sorted(directory.glob("*.md")):
        display = _display(root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{display}: cannot read file: {exc}")
            continue
        cleaned = _HTML_COMMENT_RE.sub("", text)
        stripped = cleaned.strip()
        expected_open = f'<command name="{path.stem}" version="1">'
        if expected_open not in cleaned:
            problems.append(f"{display}: command must contain {expected_open}")
            continue
        if cleaned.count(expected_open) != 1:
            problems.append(
                f"{display}: command must contain {expected_open} exactly once"
            )
        command_close = "</command>"
        match = re.search(r"</command\s*>", stripped)
        if match is None:
            problems.append(f"{display}: command must end with {command_close}")
        elif match.group(0) != command_close or match.end() != len(stripped):
            problems.append(
                f"{display}: command first {command_close} must be the final close"
            )
        open_index = cleaned.find(expected_open)
        close_index = cleaned.find("</command>")
        inner = (
            cleaned[open_index + len(expected_open) : close_index]
            if close_index != -1
            else ""
        )
        if "<instruction>" not in inner:
            problems.append(
                f"{display}: command must contain <instruction> inside {expected_open}"
            )
    return problems


def _runtime_problems() -> list[str]:
    problems: list[str] = []

    try:
        from omega_prime.agent.prompt_builder import (
            build_system_prompt,
            steer_user_row,
        )

        system_prompt = build_system_prompt()
        if not system_prompt.startswith('<omega_prime_system version="1">'):
            problems.append(
                "runtime build_system_prompt: system prompt must start with "
                '<omega_prime_system version="1">'
            )
        if not system_prompt.endswith("</omega_prime_system>"):
            problems.append(
                "runtime build_system_prompt: system prompt must end with "
                "</omega_prime_system>"
            )
        steer_content = steer_user_row("x")["content"]
        if not steer_content.startswith("<steer "):
            problems.append("runtime steer_user_row: content must start with <steer ")
        if not steer_content.endswith("</steer>"):
            problems.append("runtime steer_user_row: content must end with </steer>")
    except Exception as exc:
        problems.append(
            f"runtime prompt_builder probe failed: {type(exc).__name__}: {exc}"
        )

    try:
        from omega_prime.agent.compression import compress_messages

        messages: list[dict[str, str]] = [
            {"role": "system", "content": "system probe"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
            {"role": "assistant", "content": "four"},
        ]
        result = compress_messages(messages)
        summary = next(
            (
                row
                for row in result
                if isinstance(row, dict) and row.get("display_kind") == "summary"
            ),
            None,
        )
        if summary is None:
            problems.append(
                "runtime compress_messages: no summary row in compressed history"
            )
        else:
            content = summary.get("content")
            if not isinstance(content, str) or not content.startswith(
                '<context_summary version="1">'
            ):
                problems.append(
                    "runtime compress_messages: summary content must start with "
                    '<context_summary version="1">'
                )
            elif not content.endswith("</context_summary>"):
                problems.append(
                    "runtime compress_messages: summary content must end with "
                    "</context_summary>"
                )
    except Exception as exc:
        problems.append(
            f"runtime compress_messages probe failed: {type(exc).__name__}: {exc}"
        )

    try:
        from omega_prime.memory.store import MemoryStore

        with tempfile.TemporaryDirectory(prefix="omega-prime-prompt-format-") as tmp:
            store = MemoryStore(Path(tmp))
            store.add("memory", "prompt format probe")
            rendered = store.render()
        if not rendered.startswith("<memory_notes "):
            problems.append(
                "runtime MemoryStore.render: text must start with <memory_notes "
            )
        if not rendered.endswith("</memory_notes>"):
            problems.append(
                "runtime MemoryStore.render: text must end with </memory_notes>"
            )
    except Exception as exc:
        problems.append(
            f"runtime MemoryStore probe failed: {type(exc).__name__}: {exc}"
        )

    try:
        from omega_prime.learning.advisor import format_advisories

        rendered_advisories = format_advisories([{"note": "x"}])
        if not rendered_advisories.startswith("<advisory"):
            problems.append("runtime format_advisories: text must start with <advisory")
        if not rendered_advisories.endswith("</advisory>"):
            problems.append("runtime format_advisories: text must end with </advisory>")
    except Exception as exc:
        problems.append(
            f"runtime format_advisories probe failed: {type(exc).__name__}: {exc}"
        )
    try:
        from omega_prime.agent.goals import PrimeGoalStore

        with tempfile.TemporaryDirectory(prefix="omega-prime-prompt-format-") as tmp:
            goal_store = PrimeGoalStore(Path(tmp) / "goals")
            goal_store.set_objective("prompt format probe")
            goal_store.add_step("probe step")
            goal_prompt = goal_store.continuation_prompt()
        if goal_prompt is None or not goal_prompt.startswith(
            '<goal_continuation version="1">'
        ):
            problems.append(
                "runtime PrimeGoalStore.continuation_prompt: text must start with "
                '<goal_continuation version="1">'
            )
        elif not goal_prompt.endswith("</goal_continuation>"):
            problems.append(
                "runtime PrimeGoalStore.continuation_prompt: text must end with "
                "</goal_continuation>"
            )
    except Exception as exc:
        problems.append(
            f"runtime PrimeGoalStore probe failed: {type(exc).__name__}: {exc}"
        )

    try:
        from omega_prime.agent.autonomous import AutonomousDriver

        verdict = AutonomousDriver(root=Path(tempfile.gettempdir())).after_turn(
            {"completed": True}
        )
        autonomous_prompt = verdict.get("prompt")
        if not isinstance(autonomous_prompt, str) or not autonomous_prompt.startswith(
            '<autonomous_continuation version="1">'
        ):
            problems.append(
                "runtime AutonomousDriver.after_turn: prompt must start with "
                '<autonomous_continuation version="1">'
            )
        elif not autonomous_prompt.endswith("</autonomous_continuation>"):
            problems.append(
                "runtime AutonomousDriver.after_turn: prompt must end with "
                "</autonomous_continuation>"
            )
    except Exception as exc:
        problems.append(
            f"runtime AutonomousDriver probe failed: {type(exc).__name__}: {exc}"
        )

    return problems


def check(root: Path) -> list[str]:
    """Return prompt-format problems under ``root``. Empty means pass."""
    root = Path(root)
    problems: list[str] = []
    problems.extend(_seat_prompt_problems(root))
    problems.extend(_assembled_problems(root))
    problems.extend(_skill_problems(root))
    problems.extend(_routine_problems(root))
    problems.extend(_command_problems(root))
    problems.extend(_runtime_problems())
    return problems


def main(argv: list[str] | None = None) -> int:
    """Check the XML prompt standard. Exit 0 on success, 1 on problems."""
    parser = argparse.ArgumentParser(prog="omega-prime-prompt-format")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--check", action="store_true", required=True)
    args = parser.parse_args(argv)
    root = (args.root or Path(__file__).resolve().parents[2]).resolve()
    problems = check(root)
    if problems:
        for problem in problems:
            print(f"prompt_format --check: {problem}", file=sys.stderr)
        return 1
    print("prompt_format --check: all prompts conform to the XML standard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EXCLUDED_FROM_STANDARD", "check", "main"]

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
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")


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


def _assembled_problems(root: Path) -> list[str]:
    problems: list[str] = []
    text = _read(root, _ASSEMBLED_PATH, problems)
    if text is None:
        return problems
    placeholder = _PLACEHOLDER_RE.search(text)
    if placeholder is not None:
        problems.append(
            f"{_ASSEMBLED_PATH}: unfilled placeholder remains: {placeholder.group(0)}"
        )
    # The assembled file concatenates two documents, each with its own
    # declaration; strip every declaration before wrapping in <assembled>.
    body = _XML_DECLARATION_RE.sub("", text)
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


def _skill_problems(root: Path) -> list[str]:
    from omega_prime.skills_runtime.guards import parse_frontmatter

    problems: list[str] = []
    directory = root / _SKILLS_DIR
    if not directory.is_dir():
        return [f"{_SKILLS_DIR}: directory is missing"]
    for path in sorted(directory.glob("*/SKILL.md")):
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
        if not stripped.startswith(expected_open):
            problems.append(f"{display}: skill body must start with {expected_open}")
        if not stripped.endswith("</skill>"):
            problems.append(f"{display}: skill body must end with </skill>")
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
        if not stripped.startswith(expected_open):
            problems.append(f"{display}: routine must start with {expected_open}")
        if "<summary>" not in stripped or "</summary>" not in stripped:
            problems.append(f"{display}: routine must contain <summary> and </summary>")
        if not stripped.endswith("</routine>"):
            problems.append(f"{display}: routine must end with </routine>")
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
        expected_open = f'<command name="{path.stem}" version="1">'
        if expected_open not in text:
            problems.append(f"{display}: command must contain {expected_open}")
        if not text.strip().endswith("</command>"):
            problems.append(f"{display}: command must end with </command>")
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
    except Exception as exc:
        problems.append(
            f"runtime MemoryStore probe failed: {type(exc).__name__}: {exc}"
        )

    try:
        from omega_prime.learning.advisor import format_advisories

        rendered_advisories = format_advisories([{"note": "x"}])
        if not rendered_advisories.startswith("<advisory"):
            problems.append("runtime format_advisories: text must start with <advisory")
    except Exception as exc:
        problems.append(
            f"runtime format_advisories probe failed: {type(exc).__name__}: {exc}"
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

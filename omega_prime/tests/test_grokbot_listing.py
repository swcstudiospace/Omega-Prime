"""The Grok Bot listing package matches its sources and its claims stay within their evidence."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LISTING = ROOT / "omega_prime" / "grokbot" / "listing"
PROMPTS = LISTING / "prompts"
INSTALL = PROMPTS / "install.md"
FOLLOW = PROMPTS / "follow-up"
INDEX = FOLLOW / "INDEX.md"
README = LISTING / "README.md"
SETUP = ROOT / "omega_prime" / "grokbot" / "SETUP.md"
COMMANDS = ROOT / "omega_prime" / "grokbot" / "plugin" / "commands"

ROOT_LICENSE_SHA256 = "6c944d7a32f9355777b7f4c70243a01bf2ea55c429a69599f53c4595d6b4b51a"
SETUP_TASK_OPEN = '<task name="setup-omega-prime" version="1">'
SETUP_TASK_CLOSE = "</task>"
PLUGIN_PLACEHOLDER = "${CURSOR_PLUGIN_ROOT}"
ON_BOT_ROOT = "/workspace/omega-prime"
DOES_NOT_PROVE_HEADING = "## What this does not prove"
INDEX_HEADER = "| Order | Prompt | Sends | Use it when |"
README_HEADER = "| Claim | Evidence level | Source |"

# Pinned mapping: each follow-up prompt sends exactly this command.
EXPECTED_FOLLOW_UP: dict[str, str] = {
    "01-verify-install.md": "/omega-roster",
    "02-help-tour.md": "/omega-help",
    "03-health-check.md": "/omega-doctor",
    "04-first-real-task-code-review.md": "/omega-code-review",
    "05-troubleshooting.md": "/omega-doctor",
}

ALLOWED_LEVELS = frozenset(
    {
        "local proof",
        "optional capability",
        "unproven external",
        "CONFIRMED",
        "PARTIAL",
        "UNPROVEN",
    }
)

LINK = re.compile(r"\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
COMMAND_NAME = re.compile(r"(?<![\w/.-])/omega-[a-z0-9][a-z0-9-]*")
TEXT_FENCE = re.compile(r"^```text\n(.*?)\n```$", re.MULTILINE | re.DOTALL)
SECRET_ASSIGN = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S")
EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf]")
FORBIDDEN_CLAIMS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"connected bot",
        r"connected to a bot",
        r"published listing",
        r"marketplace listing live",
        r"live on grok",
        r"successfully (?:imported|attached|connected)",
        r"\b\d+(?:\.\d+)?\s*(?:ms|sec|secs|seconds|minutes|min)\b",
        r"\b\d+(?:\.\d+)?\s*%",
        r"!\[[^\]]*\]\([^)]*screenshot",
    )
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _listing_markdown() -> list[Path]:
    """Every markdown file in the listing folder, recursively."""
    return sorted(LISTING.rglob("*.md"))


def _follow_up_prompts() -> list[Path]:
    """The follow-up prompt files; the index is not a prompt."""
    return sorted(path for path in FOLLOW.glob("*.md") if path.name != "INDEX.md")


def _text_fences(text: str) -> list[str]:
    """Bodies of every ```text fence in a markdown file."""
    return TEXT_FENCE.findall(text)


def _command_names(text: str) -> set[str]:
    """Every /omega-* name in a text, without trailing hyphens."""
    return {name.rstrip("-") for name in COMMAND_NAME.findall(text)}


def _slug(heading: str) -> str:
    """GitHub-style anchor for a heading."""
    kept = re.sub(r"[^a-z0-9 -]", "", heading.strip().lower())
    return kept.replace(" ", "-")


def _does_not_prove_bullets(text: str) -> list[str]:
    """Bullet lines under the 'What this does not prove' heading."""
    lines = text.splitlines()
    if DOES_NOT_PROVE_HEADING not in lines:
        return []
    bullets: list[str] = []
    for line in lines[lines.index(DOES_NOT_PROVE_HEADING) + 1 :]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            bullets.append(line)
    return bullets


def _broken_link(source: Path, target: str) -> str | None:
    """Describe a relative link that does not resolve, or None when it does."""
    if "://" in target or target.startswith("mailto:"):
        return None
    path_part, _, anchor = target.partition("#")
    dest = (source.parent / path_part) if path_part else source
    where = source.relative_to(ROOT)
    if not dest.exists():
        return f"{where}: target does not exist: {target}"
    if anchor and dest.suffix == ".md":
        headings = {_slug(heading) for heading in HEADING.findall(_read(dest))}
        if anchor.lower() not in headings:
            return f"{where}: anchor not found: {target}"
    return None


def test_install_prompt_is_verbatim_setup_section_one() -> None:
    """The install prompt's only text fence equals the SETUP.md section 1 task block."""
    setup = _read(SETUP)
    assert setup.count(SETUP_TASK_OPEN) == 1
    start = setup.index(SETUP_TASK_OPEN)
    end = setup.index(SETUP_TASK_CLOSE, start) + len(SETUP_TASK_CLOSE)
    block = setup[start:end]
    install = _read(INSTALL)
    assert install.count("```text") == 1
    fences = _text_fences(install)
    assert len(fences) == 1
    assert fences[0] == block


def test_install_prompt_stdio_command_matches_mcp_json() -> None:
    """The install prompt's stdio command is the mcp.json entry with the Bot path."""
    servers = json.loads(_read(ROOT / "mcp.json"))["mcpServers"]
    assert list(servers) == ["omega-prime"]
    entry = servers["omega-prime"]
    declared = " ".join([entry["command"], *entry["args"]])
    assert PLUGIN_PLACEHOLDER in declared
    on_bot = declared.replace(PLUGIN_PLACEHOLDER, ON_BOT_ROOT)
    assert f"running exactly:\n{on_bot}\n" in _read(INSTALL)


def test_every_omega_command_name_has_a_command_file() -> None:
    """Every /omega-* name in the listing has a file in plugin/commands/."""
    for path in _listing_markdown():
        for name in sorted(_command_names(_read(path))):
            assert (COMMANDS / f"{name[1:]}.md").is_file(), f"{path.name}: {name}"
    sent: set[str] = set()
    for prompt in _follow_up_prompts():
        sent |= _command_names(_read(prompt))
    assert sent == set(EXPECTED_FOLLOW_UP.values())


def test_each_follow_up_prompt_sends_one_command_and_lists_limits() -> None:
    """Each follow-up prompt sends one command as its whole message and lists limits."""
    prompts = _follow_up_prompts()
    assert len(prompts) == 5
    for prompt in prompts:
        text = _read(prompt)
        fences = _text_fences(text)
        assert len(fences) == 1, prompt.name
        assert sum(line.startswith("```") for line in text.splitlines()) == 2, (
            prompt.name
        )
        body = fences[0].strip()
        assert "\n" not in body and body.startswith("/omega-"), prompt.name
        command = body.split()[0]
        assert (COMMANDS / f"{command[1:]}.md").is_file(), prompt.name
        assert len(_does_not_prove_bullets(text)) >= 2, prompt.name
        assert "UNPROVEN" in text, prompt.name


def test_follow_up_index_lists_each_prompt_exactly_once() -> None:
    """The index links each follow-up prompt once and has the table header."""
    names = {prompt.name for prompt in _follow_up_prompts()}
    assert len(names) == 5
    index = _read(INDEX)
    assert INDEX_HEADER in index
    linked = [target for target in LINK.findall(index) if Path(target).name in names]
    assert all(target in names for target in linked)
    assert Counter(linked) == Counter(names)


def test_follow_up_commands_match_pinned_mapping() -> None:
    """Each prompt sends its pinned command, so a silent swap fails."""
    assert {prompt.name for prompt in _follow_up_prompts()} == set(EXPECTED_FOLLOW_UP)
    for name, command in EXPECTED_FOLLOW_UP.items():
        fences = _text_fences(_read(FOLLOW / name))
        assert len(fences) == 1, name
        assert fences[0].split()[0] == command, name


def test_root_license_is_pinned_and_matches_git_head() -> None:
    """The root LICENSE is the pinned file and equals the committed HEAD copy."""
    working = (ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(working).hexdigest() == ROOT_LICENSE_SHA256
    head = subprocess.run(
        ["git", "show", "HEAD:LICENSE"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    assert head == working


def test_license_notice_names_license_holder_and_root_file() -> None:
    """The listing licence notice names AGPL-3.0-only, the holder and the root file."""
    notice = _read(LISTING / "LICENSE.md")
    for needle in (
        "AGPL-3.0-only",
        "Copyright (C) 2026 Spectrum Web Co",
        "../../../LICENSE",
    ):
        assert needle in notice, needle
    assert (LISTING / "../../../LICENSE").resolve() == (ROOT / "LICENSE").resolve()


def test_changelog_has_one_v20_entry_and_links_root_history() -> None:
    """The listing changelog has exactly one version heading and links the root history."""
    text = _read(LISTING / "CHANGELOG.md")
    headings = [line for line in text.splitlines() if line.startswith("## [")]
    assert headings == ["## [v20] - Unreleased"]
    assert "../../../CHANGELOG.md" in text


def test_listing_claims_stay_unproven_and_emoji_free() -> None:
    """Every listing file says live import is UNPROVEN and makes no forbidden claim."""
    for path in _listing_markdown():
        text = _read(path)
        assert "UNPROVEN" in text, path.name
        for pattern in FORBIDDEN_CLAIMS:
            assert pattern.search(text) is None, f"{path.name}: {pattern.pattern}"
        assert EMOJI.search(text) is None, path.name


def test_listing_files_do_not_duplicate_install_block_or_secrets() -> None:
    """The install block appears only in install.md, and no file assigns a secret."""
    for path in _listing_markdown():
        text = _read(path)
        if path != INSTALL:
            assert '<task name="setup-omega-prime"' not in text, path.name
        assert SECRET_ASSIGN.search(text) is None, path.name


def test_listing_relative_links_and_anchors_resolve() -> None:
    """Every relative link and anchor in every listing file resolves."""
    problems: list[str] = []
    for path in _listing_markdown():
        for target in LINK.findall(_read(path)):
            problem = _broken_link(path, target)
            if problem is not None:
                problems.append(problem)
    assert problems == []


def test_readme_claims_table_cites_levels_and_sources() -> None:
    """Each README claim row has an allowed evidence level and a relative source link."""
    lines = _read(README).splitlines()
    assert README_HEADER in lines
    rows: list[str] = []
    for line in lines[lines.index(README_HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    assert len(rows) >= 8
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert len(cells) == 3, row
        assert cells[1].replace("`", "") in ALLOWED_LEVELS, row
        relative = [t for t in LINK.findall(cells[2]) if "://" not in t]
        assert relative, row

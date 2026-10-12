"""Documentation navigation resolves, and the setup prompt runs the declared server."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import unquote

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent
DOCS = ROOT / "docs"

_LINK = re.compile(r"\[[^\]]*\]\(([^)#]+\.md)\)")

# Pages a reader opens from GitHub or GitBook: every docs page plus the
# repository-root Markdown files that link into them.
_PAGES = sorted(DOCS.rglob("*.md")) + sorted(ROOT.glob("*.md"))
_FENCE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})([^\n]*)\n(.*?)^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL
)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_TARGETS = (
    re.compile(r"\]\(\s*<?([^)\s>]+)>?[^)]*\)"),  # inline links and images
    re.compile(r"^[ \t]*\[[^\]]+\]:[ \t]*<?(\S+?)>?(?:[ \t].*)?$", re.MULTILINE),
    re.compile(r"""\b(?:src|href)=["']([^"']+)["']"""),  # raw HTML
)
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t#]*$", re.MULTILINE)
_HTML_ANCHOR = re.compile(r"""<a\s+(?:id|name)=["']([^"']+)["']""")


def _prose(text: str) -> str:
    """The rendered prose: fenced blocks dropped, inline code neutralized."""
    return _INLINE_CODE.sub("code", _FENCE.sub("", text))


def _slug(heading: str) -> str:
    """GitHub's heading anchor: rendered text, lowercased, punctuation dropped."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "")
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def _anchors(text: str) -> set[str]:
    """Heading anchors (with GitHub's -1, -2 duplicate suffixes) and HTML ids."""
    body = _FENCE.sub("", text)
    anchors = {match.lower() for match in _HTML_ANCHOR.findall(body)}
    seen: dict[str, int] = {}
    for heading in _HEADING.findall(body):
        slug = _slug(heading)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _broken_links(page: Path, text: str) -> list[str]:
    """Relative link, image and anchor targets in ``text`` that do not resolve.

    ``text`` is the page's content, so a same-page anchor is checked against
    it rather than against the file on disk.
    """
    prose = _prose(text)
    broken = []
    for pattern in _TARGETS:
        for target in pattern.findall(prose):
            if "://" in target or target.startswith(("mailto:", "tel:")):
                continue
            path_part, _, anchor = target.partition("#")
            resolved = (page.parent / unquote(path_part)) if path_part else page
            if not resolved.exists():
                broken.append(f"{target} (no file {resolved})")
                continue
            if not anchor or resolved.suffix != ".md":
                continue
            source = resolved.read_text(encoding="utf-8") if path_part else text
            if unquote(anchor).lower() not in _anchors(source):
                broken.append(f"{target} (no heading anchor #{anchor})")
    return broken


_PY_MODULE = re.compile(r"(?:^|[\s/])python[\d.]*\s+-m\s+(omega_prime(?:\.\w+)+)")
_SCRIPT = re.compile(r"(?:^|\s)(?:bash|sh)\s+(omega_prime/\S+\.sh)\b")


def _unrunnable_demo_commands(text: str) -> list[str]:
    """``python -m omega_prime.*`` modules and repo scripts in shell fences
    that a reader running the block from the repository root could not start."""
    missing = []
    for match in _FENCE.finditer(text):
        if match.group(2).strip().split(" ")[0] not in (
            "bash",
            "sh",
            "shell",
            "console",
        ):
            continue
        block = match.group(3)
        for module in _PY_MODULE.findall(block):
            if importlib.util.find_spec(module) is None:
                missing.append(f"python -m {module}")
        for script in _SCRIPT.findall(block):
            if not (ROOT / script).is_file():
                missing.append(f"bash {script}")
    return missing


def test_every_docs_page_opens_with_a_top_level_heading():
    for page in sorted(DOCS.rglob("*.md")):
        first = next(
            (
                line
                for line in page.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ),
            "",
        )
        assert first.startswith("# "), f"{page.relative_to(ROOT)} opens with {first!r}"


def test_relative_links_images_and_anchors_resolve():
    """A reader following any relative link, image or heading anchor lands on it."""
    broken = {
        str(page.relative_to(ROOT)): found
        for page in _PAGES
        if (found := _broken_links(page, page.read_text(encoding="utf-8")))
    }
    assert not broken, broken


def test_link_check_rejects_a_missing_anchor_file_and_image():
    """The resolver itself fails closed, so the docs-wide check cannot pass vacuously."""
    page = DOCS / "marketing.md"
    text = page.read_text(encoding="utf-8")
    assert _broken_links(page, text) == []
    assert _broken_links(page, text + "\n[x](grok-bot-native.md#no-such-row)\n")
    assert _broken_links(page, text + "\n[x](#no-such-section)\n")
    assert _broken_links(page, text + "\n[x](no-such-page.md)\n")
    assert _broken_links(page, text + "\n![x](../assets/no-such-art.svg)\n")
    assert _broken_links(page, text + '\n<img src="../assets/no-such-art.png">\n')
    # A link quoted inside a code block is not a link.
    assert _broken_links(page, text + "\n```text\n[x](no-such-page.md)\n```\n") == []


def test_documented_shell_commands_name_runnable_modules_and_scripts():
    """Shell blocks a reader copies run real ``omega_prime`` modules and scripts."""
    missing = {
        str(page.relative_to(ROOT)): found
        for page in _PAGES
        if (found := _unrunnable_demo_commands(page.read_text(encoding="utf-8")))
    }
    assert not missing, missing
    demo = (DOCS / "marketing.md").read_text(encoding="utf-8")
    assert _unrunnable_demo_commands(demo) == []
    renamed = demo.replace("omega_prime.tooling.catalog", "omega_prime.tooling.katalog")
    assert _unrunnable_demo_commands(renamed) == [
        "python -m omega_prime.tooling.katalog"
    ]
    moved = demo.replace("scripts/grokbot-1click.sh", "scripts/grokbot-2click.sh")
    assert "bash omega_prime/scripts/grokbot-2click.sh" in _unrunnable_demo_commands(
        moved
    )


def test_summary_links_resolve_exactly_once():
    summary = (DOCS / "SUMMARY.md").read_text(encoding="utf-8")
    links = _LINK.findall(summary)
    assert len(links) == len(set(links)), "a page is listed twice"
    for link in links:
        assert (DOCS / link).is_file(), f"SUMMARY.md links missing {link}"
    pages = {
        str(p.relative_to(DOCS)) for p in DOCS.rglob("*.md") if p.name != "SUMMARY.md"
    }
    assert set(links) == pages, "SUMMARY.md and docs/ disagree"


def test_setup_prompts_run_the_declared_stdio_command():
    """The Bot-driven prompt and the plugin route must start the same server.

    The copy-paste setup prompt is the primary import path: the Bot registers
    whatever command the docs give it. If ``mcp.json`` changes and the prompt
    does not, the Bot silently runs a different server than the plugin route.
    """
    (server,) = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))[
        "mcpServers"
    ].values()
    declared = " ".join([server["command"], *server["args"]])
    assert "${CURSOR_PLUGIN_ROOT}" in declared
    on_bot = declared.replace("${CURSOR_PLUGIN_ROOT}", "/workspace/omega-prime")
    for doc in ("omega_prime/grokbot/SETUP.md", "README.md"):
        assert on_bot in (ROOT / doc).read_text(encoding="utf-8"), doc
    for doc in ("omega_prime/grokbot/SETUP.md", "docs/grok-bot-native.md"):
        assert declared in (ROOT / doc).read_text(encoding="utf-8"), doc

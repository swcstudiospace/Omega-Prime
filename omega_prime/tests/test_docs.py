"""Documentation navigation resolves, and the setup prompt runs the declared server."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

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
_FRONT_MATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*\n", re.DOTALL)
_ATX = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", re.MULTILINE)
# A paragraph line underlined with = or -- is a heading too; a list item,
# quote, table row or ATX heading above a rule is not.
_SETEXT = re.compile(
    r"^ {0,3}(?![#>|]|[-*+][ \t]|\d+[.)][ \t])(\S[^\n]*?)[ \t]*\n {0,3}(?:=+|-{2,})[ \t]*$",
    re.MULTILINE,
)
_HTML_ANCHOR = re.compile(
    r"""<a\b[^>]*?\s(?:id|name)\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)
# External, protocol-relative, mailto:, data: and other scheme targets.
_SCHEME = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")


def _prose(text: str) -> str:
    """The rendered prose: fenced blocks dropped, inline code neutralized."""
    return _INLINE_CODE.sub("code", _FENCE.sub("", text))


def _slug(heading: str) -> str:
    """GitHub's heading anchor: rendered text, lowercased, punctuation dropped."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "")
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def _anchors(text: str) -> tuple[set[str], set[str]]:
    """GitHub heading slugs (with -1, -2 duplicate suffixes) and HTML anchor ids."""
    body = _FENCE.sub("", _FRONT_MATTER.sub("", text, count=1))
    headings = sorted(
        [(match.start(), match.group(1)) for match in _ATX.finditer(body)]
        + [(match.start(), match.group(1)) for match in _SETEXT.finditer(body)]
    )
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    for _, heading in headings:
        slug = _slug(heading)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slugs.add(slug if count == 0 else f"{slug}-{count}")
    return slugs, set(_HTML_ANCHOR.findall(body))


def _broken_links(page: Path, text: str) -> list[str]:
    """Relative link, image and anchor targets in ``text`` that do not resolve.

    ``text`` is the page's content, so a same-page anchor is checked against
    it rather than against the file on disk. Heading slugs match without
    regard to case; HTML ids match exactly, as browsers compare them.
    """
    prose = _prose(text)
    broken = []
    for pattern in _TARGETS:
        for target in pattern.findall(prose):
            if _SCHEME.match(target):
                continue
            path_part, _, anchor = target.partition("#")
            path_part = path_part.split("?", 1)[0]
            resolved = (page.parent / unquote(path_part)) if path_part else page
            if not resolved.exists():
                broken.append(f"{target} (no file {resolved})")
                continue
            if not anchor or resolved.suffix != ".md":
                continue
            source = resolved.read_text(encoding="utf-8") if path_part else text
            slugs, ids = _anchors(source)
            wanted = unquote(anchor)
            if wanted not in ids and wanted.lower() not in slugs:
                broken.append(f"{target} (no heading anchor #{anchor})")
    return broken


_SHELL_FENCES = {"bash", "sh", "shell", "zsh", "console", "shell-session", "sh-session"}
_PY_MODULE = re.compile(r"(?:^|[\s/])python[\d.]*\s+-m\s+(omega_prime(?:\.\w+)+)")
_SCRIPT = re.compile(r"""(?:^|\s)(?:bash|sh|zsh)\s+["']?(omega_prime/[^\s"']+\.sh)\b""")
_COMMENT = re.compile(r"(?:^|[ \t])#.*$", re.MULTILINE)


def _module_exists(module: str) -> bool:
    """``python -m module`` can start it: a module, or a package with ``__main__``."""
    try:
        spec = importlib.util.find_spec(module)
        if spec is not None and spec.submodule_search_locations is not None:
            spec = importlib.util.find_spec(f"{module}.__main__")
    except ModuleNotFoundError:  # a parent package is missing too
        return False
    return spec is not None


def _unrunnable_demo_commands(text: str) -> list[str]:
    """``python -m omega_prime.*`` modules and repo scripts in shell fences
    that a reader running the block from the repository root could not start.

    Continued lines are joined and comments dropped first, as the shell reads
    them, so a command split over lines is checked and a commented-out one is not.
    """
    missing = []
    for match in _FENCE.finditer(text):
        info = match.group(2).split()
        if not info or info[0] not in _SHELL_FENCES:
            continue
        block = _COMMENT.sub("", re.sub(r"\\\n", " ", match.group(3)))
        for module in _PY_MODULE.findall(block):
            if not _module_exists(module):
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


def test_link_check_flags_missing_targets_and_accepts_valid_forms():
    """The resolver fails closed on a missing target, so the docs-wide check
    cannot pass vacuously, and accepts every form a reader's link can take."""
    page = DOCS / "marketing.md"
    text = page.read_text(encoding="utf-8")
    assert _broken_links(page, text) == []
    for missing in (
        "[x](grok-bot-native.md#no-such-row)",
        "[x](#no-such-section)",
        "[x](no-such-page.md)",
        "![x](../assets/no-such-art.svg)",
        '<img src="../assets/no-such-art.png">',
    ):
        assert _broken_links(page, f"{text}\n{missing}\n"), missing
    extra = (
        "\n\nSetext title\n------------\n\n   ### Indented title\n\n"
        '<a class="k" id="Mixed-Case"></a>\n'
    )
    for valid in (
        "[x](marketing.md?plain=1)",
        "![x](data:image/png;base64,AAAA)",
        "[x](#setext-title)",
        "[x](#indented-title)",
        "[x](#Mixed-Case)",
    ):
        assert _broken_links(page, f"{text}{extra}\n{valid}\n") == [], valid
    # An HTML id is case-sensitive, unlike a heading slug.
    assert _broken_links(page, f"{text}{extra}\n[x](#mixed-case)\n")
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


@pytest.mark.parametrize(
    ("block", "missing"),
    [
        ("```bash\n# python -m omega_prime.no_such\n```", []),
        (
            "```bash\n.venv/bin/python \\\n  -m omega_prime.no_such --json\n```",
            ["python -m omega_prime.no_such"],
        ),
        (
            "```zsh\npython3 -m omega_prime.no_such.deeper\n```",
            ["python -m omega_prime.no_such.deeper"],
        ),
        (
            '```bash\nbash "omega_prime/scripts/no-such.sh"\n```',
            ["bash omega_prime/scripts/no-such.sh"],
        ),
        ("```text\npython -m omega_prime.no_such\n```", []),
        # A package runs under -m only through its __main__ module.
        (
            "```bash\npython -m omega_prime.tooling\n```",
            ["python -m omega_prime.tooling"],
        ),
    ],
)
def test_command_check_reads_a_block_as_the_shell_would(
    block: str, missing: list[str]
) -> None:
    assert _unrunnable_demo_commands(block) == missing


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
    on_bot = declared.replace("${CURSOR_PLUGIN_ROOT}", "/workspace/omes-bot")
    for doc in (
        "omega_prime/grokbot/SETUP.md",
        "omega_prime/grokbot/templates/OMEGA_PRIME.md",
        "README.md",
        "docs/marketing.md",
    ):
        assert on_bot in (ROOT / doc).read_text(encoding="utf-8"), doc
    for doc in ("omega_prime/grokbot/SETUP.md", "docs/grok-bot-native.md"):
        assert declared in (ROOT / doc).read_text(encoding="utf-8"), doc

"""Documentation navigation resolves, and the setup prompt runs the declared server."""

from __future__ import annotations

import json
import re
from pathlib import Path

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent
DOCS = ROOT / "docs"

_LINK = re.compile(r"\[[^\]]*\]\(([^)#]+\.md)\)")


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

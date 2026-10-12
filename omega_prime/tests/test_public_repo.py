"""Consumer-facing documentation links and self-contained SVG assets."""

from __future__ import annotations

import re
from pathlib import Path
from xml.dom import minidom

ROOT = Path(__file__).resolve().parents[2]
_LINK = re.compile(r"\[[^\]]*\]\(([^)#]+?)(?:#[^)]*)?\)")


def test_readme_links_resolve() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = [match for match in _LINK.findall(text) if "://" not in match]
    assert targets, "README names no relative links"
    for target in targets:
        assert (ROOT / target).exists(), f"README links missing {target}"


def test_svgs_valid_and_self_contained() -> None:
    for name in ("assets/icon.svg", "assets/banner.svg", "assets/social-card.svg"):
        raw = (ROOT / name).read_text(encoding="utf-8")
        root = minidom.parseString(raw).documentElement
        assert root is not None
        assert root.tagName == "svg"
        assert root.getAttribute("width") and root.getAttribute("height")
        assert not root.getElementsByTagName("script")
        assert "href" not in raw and "xlink" not in raw
        without_ns = raw.replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert "http://" not in without_ns and "https://" not in without_ns
    # assets/icon.svg stays byte-identical to the brand source and has no <title>.
    for name in ("assets/banner.svg", "assets/social-card.svg"):
        root = minidom.parse(str(ROOT / name)).documentElement
        assert root is not None
        title = root.getElementsByTagName("title")
        assert title, f"{name} has no <title> for assistive tech"

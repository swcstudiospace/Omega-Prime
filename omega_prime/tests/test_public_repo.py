"""Consumer-facing documentation links and self-contained SVG assets."""

from __future__ import annotations

import re
import struct
from pathlib import Path
from xml.dom import minidom

ROOT = Path(__file__).resolve().parents[2]
_LINK = re.compile(r"\[[^\]]*\]\(([^)#]+?)(?:#[^)]*)?\)")
_SVGS = sorted((ROOT / "assets").glob("*.svg"))


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
    # assets/icon.svg carries no <title>, so the title check covers banner and card.
    for name in ("assets/banner.svg", "assets/social-card.svg"):
        root = minidom.parse(str(ROOT / name)).documentElement
        assert root is not None
        title = root.getElementsByTagName("title")
        assert title, f"{name} has no <title> for assistive tech"


def test_svg_canvas_matches_its_viewbox() -> None:
    """Declared pixel size and viewBox agree, so no renderer stretches the art."""
    assert _SVGS
    for path in _SVGS:
        root = minidom.parse(str(path)).documentElement
        assert root is not None
        box = [float(part) for part in root.getAttribute("viewBox").split()]
        assert len(box) == 4, f"{path.name} has no four-number viewBox"
        size = [float(root.getAttribute("width")), float(root.getAttribute("height"))]
        assert box[:2] == [0, 0] and box[2:] == size, f"{path.name}: {box} vs {size}"


def test_svg_accessible_name_references_resolve() -> None:
    """Every ``aria-labelledby`` id names an element with text a reader hears."""
    for path in _SVGS:
        root = minidom.parse(str(path)).documentElement
        assert root is not None
        ids = root.getAttribute("aria-labelledby").split()
        if not ids:
            continue
        by_id = {
            node.getAttribute("id"): node
            for node in root.getElementsByTagName("*")
            if node.getAttribute("id")
        }
        for ref in ids:
            assert ref in by_id, f"{path.name}: aria-labelledby names missing #{ref}"
            spoken = "".join(
                child.data for child in by_id[ref].childNodes if child.nodeType == 3
            )
            assert spoken.strip(), f"{path.name}: #{ref} is empty"


def _rgb(colour: str) -> tuple[float, float, float]:
    hexa = colour.strip().lstrip("#")
    if len(hexa) == 3:
        hexa = "".join(ch * 2 for ch in hexa)
    assert re.fullmatch(r"[0-9a-fA-F]{6}", hexa), f"not a solid colour: {colour}"
    r, g, b = (int(hexa[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _paint(doc: minidom.Element, fill: str) -> list[tuple[float, float, float]]:
    """Solid colours a fill can show: the colour itself or each gradient stop."""
    ref = re.fullmatch(r"url\(#([^)]+)\)", fill.strip())
    if not ref:
        return [_rgb(fill)]
    for node in doc.getElementsByTagName("*"):
        if node.getAttribute("id") == ref.group(1):
            stops = node.getElementsByTagName("stop")
            return [_rgb(stop.getAttribute("stop-color")) for stop in stops]
    raise AssertionError(f"fill {fill} names no element")


def _text_contrast_failures(raw: str, minimum: float = 4.5) -> list[str]:
    """Text below WCAG AA contrast against any colour of the full-canvas ground.

    The ground is every full-canvas filled rect: gradient stops are the base
    colours, and a translucent pattern overlay is composited over each base so
    the lightest (worst case for light text) background is also checked.
    """
    root = minidom.parseString(raw).documentElement
    assert root is not None
    width, height = root.getAttribute("width"), root.getAttribute("height")
    bases: list[tuple[float, float, float]] = []
    overlays: list[tuple[tuple[float, float, float], float]] = []
    for rect in root.getElementsByTagName("rect"):
        full = (
            rect.getAttribute("width") == width
            and rect.getAttribute("height") == height
        )
        fill = rect.getAttribute("fill")
        if not full or not fill or fill == "none":
            continue
        ref = re.fullmatch(r"url\(#([^)]+)\)", fill)
        pattern = next(
            (
                node
                for node in root.getElementsByTagName("pattern")
                if ref and node.getAttribute("id") == ref.group(1)
            ),
            None,
        )
        if pattern is None:
            bases.extend(_paint(root, fill))
            continue
        for shape in pattern.getElementsByTagName("*"):
            if shape.getAttribute("fill"):
                opacity = float(shape.getAttribute("opacity") or 1)
                overlays.append((_rgb(shape.getAttribute("fill")), opacity))
    assert bases, "no full-canvas ground to measure text against"
    grounds = list(bases)
    for base in bases:
        for colour, alpha in overlays:
            grounds.append(
                (
                    base[0] * (1 - alpha) + colour[0] * alpha,
                    base[1] * (1 - alpha) + colour[1] * alpha,
                    base[2] * (1 - alpha) + colour[2] * alpha,
                )
            )
    failures = []
    for text in root.getElementsByTagName("text"):
        label = "".join(c.data for c in text.childNodes if c.nodeType == 3).strip()
        fill = text.getAttribute("fill")
        assert fill, f"text {label!r} has no explicit fill"
        worst = min(_contrast(f, g) for f in _paint(root, fill) for g in grounds)
        if worst < minimum:
            failures.append(f"{label!r} {worst:.2f}:1")
    return failures


def test_svg_text_meets_wcag_aa_contrast_on_its_ground() -> None:
    for path in _SVGS:
        raw = path.read_text(encoding="utf-8")
        assert _text_contrast_failures(raw) == [], path.name


def test_contrast_check_flags_dim_text() -> None:
    """The measurement fails closed: a dimmed line on the real banner is caught."""
    raw = (ROOT / "assets" / "banner.svg").read_text(encoding="utf-8")
    root = minidom.parseString(raw).documentElement
    assert root is not None
    last = root.getElementsByTagName("text")[-1]
    last.setAttribute("fill", "#4b5260")
    assert _text_contrast_failures(root.toxml())


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", path.name
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_png_rasters_match_their_svg_source_canvas() -> None:
    """A committed raster has an SVG source and was rendered at that canvas size,
    so a resized source with a stale raster fails."""
    rasters = sorted((ROOT / "assets").glob("*.png"))
    assert rasters
    for raster in rasters:
        source = raster.with_suffix(".svg")
        assert source.is_file(), f"{raster.name} has no SVG source"
        root = minidom.parse(str(source)).documentElement
        assert root is not None
        declared = (int(root.getAttribute("width")), int(root.getAttribute("height")))
        assert _png_size(raster) == declared, raster.name

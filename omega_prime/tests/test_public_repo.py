"""Consumer-facing documentation links and self-contained SVG assets."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
import sys
import zlib
from itertools import pairwise
from pathlib import Path
from xml.dom import minidom

import pytest

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


def _length(value: str, default: float = 0.0) -> float:
    """The first number of an SVG length or coordinate list, ignoring a px unit."""
    number = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
    return float(number.group(1)) if number else default


def _canvas(root: minidom.Element, name: str) -> tuple[float, float]:
    """The declared canvas size in pixels; a percentage or other unit fails."""
    size = []
    for attribute in ("width", "height"):
        value = root.getAttribute(attribute).strip()
        assert re.fullmatch(r"\d+(?:\.\d+)?(?:px)?", value), (
            f"{name}: {attribute}={value!r} is not a pixel size"
        )
        size.append(_length(value))
    return size[0], size[1]


def test_svg_canvas_matches_its_viewbox() -> None:
    """Declared pixel size and viewBox agree, so no renderer stretches the art."""
    assert _SVGS
    for path in _SVGS:
        root = minidom.parse(str(path)).documentElement
        assert root is not None
        parts = [p for p in re.split(r"[\s,]+", root.getAttribute("viewBox")) if p]
        assert len(parts) == 4, f"{path.name} has no four-number viewBox"
        box = [float(part) for part in parts]
        size = list(_canvas(root, path.name))
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


Rgb = tuple[float, float, float]

# The CSS basic colour keywords; any other keyword fails, naming the value.
_KEYWORDS = {
    "black": "#000000",
    "silver": "#c0c0c0",
    "gray": "#808080",
    "grey": "#808080",
    "white": "#ffffff",
    "maroon": "#800000",
    "red": "#ff0000",
    "purple": "#800080",
    "fuchsia": "#ff00ff",
    "green": "#008000",
    "lime": "#00ff00",
    "olive": "#808000",
    "yellow": "#ffff00",
    "navy": "#000080",
    "blue": "#0000ff",
    "teal": "#008080",
    "aqua": "#00ffff",
}
_URL = re.compile(r"url\(\s*#([^)\s]+)\s*\)")
_PROPERTIES = ("fill", "fill-opacity", "opacity", "color", "stop-color", "stop-opacity")
_SHAPES = ("circle", "ellipse", "rect", "path", "polygon", "polyline", "line")


def _fraction(value: str) -> float:
    value = value.strip()
    return float(value[:-1]) / 100 if value.endswith("%") else float(value)


def _colour(value: str) -> tuple[Rgb, float]:
    """An sRGB colour and its alpha, from hex, ``rgb()``/``rgba()`` or a keyword."""
    text = value.strip().lower()
    text = _KEYWORDS.get(text, text)
    if text.startswith("#"):
        digits = text[1:]
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits)
        assert re.fullmatch(r"[0-9a-f]{6}(?:[0-9a-f]{2})?", digits), (
            f"unsupported colour {value!r}"
        )
        parts = [int(digits[i : i + 2], 16) / 255 for i in range(0, len(digits), 2)]
        return (parts[0], parts[1], parts[2]), (parts[3] if len(parts) == 4 else 1.0)
    function = re.fullmatch(r"rgba?\(([^)]*)\)", text)
    assert function, f"unsupported colour {value!r}"
    args = [arg for arg in re.split(r"[\s,/]+", function.group(1)) if arg]
    assert len(args) in (3, 4), f"unsupported colour {value!r}"
    r, g, b = (
        _fraction(arg) if arg.endswith("%") else float(arg) / 255 for arg in args[:3]
    )
    return (r, g, b), (_fraction(args[3]) if len(args) == 4 else 1.0)


def _luminance(rgb: Rgb) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: Rgb, b: Rgb) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _over(top: Rgb, alpha: float, bottom: Rgb) -> Rgb:
    """``top`` at ``alpha`` composited over an opaque ``bottom``."""
    return (
        top[0] * alpha + bottom[0] * (1 - alpha),
        top[1] * alpha + bottom[1] * (1 - alpha),
        top[2] * alpha + bottom[2] * (1 - alpha),
    )


def _props(node: minidom.Element) -> dict[str, str]:
    """Paint properties set on ``node``; inline ``style`` wins over attributes."""
    props = {
        name: node.getAttribute(name) for name in _PROPERTIES if node.hasAttribute(name)
    }
    for declaration in node.getAttribute("style").split(";"):
        name, _, value = declaration.partition(":")
        if name.strip() in _PROPERTIES and value.strip():
            props[name.strip()] = value.strip()
    return props


def _lineage(node: minidom.Element) -> list[minidom.Element]:
    """``node`` and its element ancestors, innermost first."""
    chain: list[minidom.Element] = []
    current: object = node
    while isinstance(current, minidom.Element):
        chain.append(current)
        current = current.parentNode
    return chain


def _by_id(root: minidom.Element, ref: str) -> minidom.Element:
    for node in root.getElementsByTagName("*"):
        if node.getAttribute("id") == ref:
            return node
    raise AssertionError(f"url(#{ref}) names no element")


def _paint(root: minidom.Element, fill: str) -> list[tuple[Rgb, float]]:
    """Colours a fill can show, with alpha: the colour itself, or each gradient
    stop plus points between neighbouring stops, where a blend of two hues can
    be darker or lighter than either end."""
    ref = _URL.fullmatch(fill.strip())
    if not ref:
        return [_colour(fill)]
    gradient = _by_id(root, ref.group(1))
    assert gradient.tagName.endswith("Gradient"), f"{fill} is not a gradient"
    stops = []
    for stop in gradient.getElementsByTagName("stop"):
        props = _props(stop)
        colour, alpha = _colour(props.get("stop-color", "#000000"))
        stops.append((colour, alpha * _fraction(props.get("stop-opacity", "1"))))
    assert stops, f"{fill} has no stops"
    samples = [stops[0]]
    for (start, start_alpha), (end, end_alpha) in pairwise(stops):
        for t in (0.25, 0.5, 0.75, 1.0):
            blend = start_alpha + (end_alpha - start_alpha) * t
            samples.append((_over(end, t, start), blend))
    return samples


def _fill_paint(
    root: minidom.Element, node: minidom.Element
) -> list[tuple[Rgb, float]]:
    """What ``node`` paints: its inherited fill (black when unset, ``currentColor``
    resolved), scaled by the inherited fill-opacity and every ancestor's opacity."""
    chain = [_props(element) for element in _lineage(node)]

    def inherited(name: str, default: str) -> str:
        return next((props[name] for props in chain if name in props), default)

    fill = inherited("fill", "#000000")
    if fill.lower() == "currentcolor":
        fill = inherited("color", "#000000")
    if fill == "none":
        return []
    alpha = _fraction(inherited("fill-opacity", "1"))
    for props in chain:
        alpha *= _fraction(props.get("opacity", "1"))
    return [(colour, own * alpha) for colour, own in _paint(root, fill)]


def _to_root(node: minidom.Element, x: float, y: float) -> tuple[float, float]:
    """A point in ``node``'s user space, mapped to canvas coordinates through the
    translate and scale transforms of the node and its ancestors."""
    for element in _lineage(node):
        steps = re.findall(r"(\w+)\s*\(([^)]*)\)", element.getAttribute("transform"))
        for kind, args in reversed(steps):
            values = [float(arg) for arg in re.split(r"[\s,]+", args.strip()) if arg]
            if kind == "translate":
                x, y = x + values[0], y + (values[1] if len(values) > 1 else 0.0)
            elif kind == "scale":
                x, y = x * values[0], y * (values[1] if len(values) > 1 else values[0])
            else:
                raise AssertionError(f"a {kind}() transform is not modelled")
    return x, y


def _grounds(root: minidom.Element) -> list[Rgb]:
    """Colours of the full-canvas ground: each base colour alone and under every
    translucent pattern overlay, so the lightest variant (the worst case for
    light text) is measured too."""
    canvas = _canvas(root, "svg")
    bases: list[Rgb] = []
    overlays: list[tuple[Rgb, float]] = []
    for rect in root.getElementsByTagName("rect"):
        fill = _props(rect).get("fill", "none")
        size = (
            _length(rect.getAttribute("width"), -1),
            _length(rect.getAttribute("height"), -1),
        )
        if size != canvas or fill == "none":
            continue
        ref = _URL.fullmatch(fill)
        target = _by_id(root, ref.group(1)) if ref else None
        if target is None or target.tagName != "pattern":
            bases.extend(colour for colour, _ in _paint(root, fill))
            continue
        for shape in target.getElementsByTagName("*"):
            if shape.tagName in _SHAPES:
                overlays.extend(_fill_paint(root, shape))
    assert bases, "no full-canvas ground to measure text against"
    return bases + [
        _over(colour, alpha, base) for base in bases for colour, alpha in overlays
    ]


def _under(
    root: minidom.Element, run: minidom.Element, grounds: list[Rgb]
) -> list[Rgb]:
    """Colours under a text run's anchor point: the topmost filled rect painted
    before the run whose box contains the point, composited over the ground,
    or the ground itself when no rect does."""
    anchor = next(
        (
            node
            for node in _lineage(run)
            if node.tagName in ("text", "tspan") and node.hasAttribute("x")
        ),
        run,
    )
    point = _to_root(
        anchor, _length(anchor.getAttribute("x")), _length(anchor.getAttribute("y"))
    )
    canvas = _canvas(root, "svg")
    under = grounds
    for node in root.getElementsByTagName("*"):
        if node is run:
            break
        if node.tagName != "rect":
            continue
        x, y = _length(node.getAttribute("x")), _length(node.getAttribute("y"))
        width = _length(node.getAttribute("width"))
        height = _length(node.getAttribute("height"))
        if (width, height) == canvas:
            continue  # part of the ground
        (x0, y0), (x1, y1) = _to_root(node, x, y), _to_root(node, x + width, y + height)
        inside = min(x0, x1) <= point[0] <= max(x0, x1) and (
            min(y0, y1) <= point[1] <= max(y0, y1)
        )
        paint = _fill_paint(root, node) if inside else []
        if paint:
            under = [
                _over(c, alpha, ground) for ground in grounds for c, alpha in paint
            ]
    return under


def _text_contrast_failures(raw: str, minimum: float = 4.5) -> list[str]:
    """Text runs below WCAG AA contrast against what lies under them.

    Glyph colour follows SVG paint inheritance, including fill-opacity,
    ancestor opacity and currentColor. Under a run lies the topmost filled
    rect painted before it that contains its anchor point, or else the
    full-canvas ground. Only the anchor point is placed, not the glyph
    extents, and only translate and scale transforms are modelled; anything
    else fails, naming it, rather than measuring the wrong pixels.
    """
    root = minidom.parseString(raw).documentElement
    assert root is not None
    grounds = _grounds(root)
    failures = []
    for run in root.getElementsByTagName("*"):
        label = "".join(c.data for c in run.childNodes if c.nodeType == 3).strip()
        if run.tagName not in ("text", "tspan") or not label:
            continue
        under = _under(root, run, grounds)
        ratios = [
            _contrast(_over(colour, alpha, below), below)
            for colour, alpha in _fill_paint(root, run)
            for below in under
        ]
        if ratios and min(ratios) < minimum:
            failures.append(f"{label!r} {min(ratios):.2f}:1")
    return failures


def test_svg_text_meets_wcag_aa_contrast_on_its_ground() -> None:
    for path in _SVGS:
        raw = path.read_text(encoding="utf-8")
        assert _text_contrast_failures(raw) == [], path.name


_PROBE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120"'
    ' viewBox="0 0 400 120"><rect width="400" height="120" fill="#0d1117"/>'
    "{}</svg>"
)
_LIGHT_BADGE = '<rect x="10" y="10" width="300" height="50" fill="#f6f8fa"/>'


@pytest.mark.parametrize(
    ("body", "readable"),
    [
        ('<text x="20" y="40" fill="#e6edf3">light on dark</text>', True),
        ('<text x="20" y="40" fill="#4b5260">dim on dark</text>', False),
        ('<text x="20" y="40" fill="white">keyword colour</text>', True),
        ('<g fill="#4b5260"><text x="20" y="40">inherited dim</text></g>', False),
        ('<text x="20" y="40" fill="#e6edf3" fill-opacity="0.2">faint</text>', False),
        ('<text x="20" y="40" fill="#e6edf333">alpha hex</text>', False),
        ('<g opacity="0.2"><text x="20" y="40" fill="#e6edf3">faded</text></g>', False),
        (
            '<text x="20" y="40" fill="#e6edf3"><tspan fill="#4b5260">dim</tspan></text>',
            False,
        ),
        (
            _LIGHT_BADGE + '<text x="20" y="40" fill="#e6edf3">light on light</text>',
            False,
        ),
        (
            _LIGHT_BADGE + '<text x="20" y="40" fill="#24292f">dark on light</text>',
            True,
        ),
        (
            '<g transform="translate(0,60)">'
            + _LIGHT_BADGE
            + '</g><text x="20" y="100" fill="#24292f">dark on a moved badge</text>',
            True,
        ),
    ],
)
def test_contrast_measures_each_run_against_what_lies_under_it(
    body: str, readable: bool
) -> None:
    """The guard follows SVG paint rules, so a faint run, an inherited colour or
    text on its own backdrop is never judged against the wrong colours."""
    assert (_text_contrast_failures(_PROBE.format(body)) == []) is readable


def test_contrast_refuses_a_transform_it_cannot_place() -> None:
    body = '<g transform="rotate(10)"><text x="20" y="40" fill="#e6edf3">t</text></g>'
    with pytest.raises(AssertionError, match="rotate"):
        _text_contrast_failures(_PROBE.format(body))


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", path.name
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _png_texts(data: bytes) -> dict[bytes, list[bytes]]:
    """Every ``tEXt`` chunk of a PNG, keyword to texts, CRC checked."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    texts: dict[bytes, list[bytes]] = {}
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(kind + body), f"bad CRC on {kind!r}"
        if kind == b"tEXt":
            keyword, _, text = body.partition(b"\x00")
            texts.setdefault(keyword, []).append(text)
        pos += 12 + length
    return texts


def test_png_rasters_were_rendered_from_their_current_svg() -> None:
    """A committed raster has an SVG source, its canvas size, and the digest of
    that exact SVG, so editing the art without re-rendering fails."""
    rasters = sorted((ROOT / "assets").glob("*.png"))
    assert rasters
    for raster in rasters:
        source = raster.with_suffix(".svg")
        assert source.is_file(), f"{raster.name} has no SVG source"
        root = minidom.parse(str(source)).documentElement
        assert root is not None
        assert _png_size(raster) == _canvas(root, source.name), raster.name
        digest = hashlib.sha256(source.read_bytes()).hexdigest().encode()
        assert _png_texts(raster.read_bytes()).get(b"omega-source-sha256") == [
            digest
        ], (
            f"{raster.name} was not rendered from the current {source.name}; run "
            f"python3 omega_prime/scripts/render_raster.py assets/{source.name}"
        )


_FAKE_CAIROSVG = """#!{python}
import struct, sys, zlib
if "--fail" in open(sys.argv[1]).read():
    sys.exit(3)
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
png = b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", ihdr)
png += chunk(b"IDAT", zlib.compress(b"\\x00\\x00\\x00\\x00")) + chunk(b"IEND", b"")
open(sys.argv[sys.argv.index("-o") + 1], "wb").write(png)
"""


def test_render_script_stamps_the_svg_digest_and_keeps_renderer_failures(
    tmp_path: Path,
) -> None:
    """The render script records the SVG it rendered, replaces an older record
    on re-render, and passes a renderer failure through unstamped."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "cairosvg"
    fake.write_text(_FAKE_CAIROSVG.format(python=sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    script = ROOT / "omega_prime" / "scripts" / "render_raster.py"
    svg, png = tmp_path / "card.svg", tmp_path / "card.png"

    def render() -> int:
        argv = [sys.executable, str(script), str(svg)]
        return subprocess.run(argv, env=env, check=False).returncode

    for art in ("<svg>first</svg>", "<svg>second</svg>"):
        svg.write_text(art, encoding="utf-8")
        assert render() == 0
        digest = hashlib.sha256(art.encode()).hexdigest().encode()
        assert _png_texts(png.read_bytes())[b"omega-source-sha256"] == [digest]

    svg.write_text("<svg>--fail</svg>", encoding="utf-8")
    assert render() == 3

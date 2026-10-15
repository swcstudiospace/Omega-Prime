"""Grok Bot listing icons are rendered from the brand SVG and stay in step with it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.dom import minidom

import pytest

from omega_prime.tests.test_public_repo import (
    _png_size,
    _png_texts,
    _text_contrast_failures,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "omega_prime" / "scripts" / "render_raster.py"

# Logs the renderer argv to $FAKE_CAIROSVG_LOG, then writes a PNG whose IHDR
# size comes from --output-width/--output-height (1 by 1 when absent).
_LOGGING_CAIROSVG = """#!{python}
import json, os, struct, sys, zlib
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
argv = sys.argv[1:]
width = int(argv[argv.index("--output-width") + 1]) if "--output-width" in argv else 1
height = int(argv[argv.index("--output-height") + 1]) if "--output-height" in argv else 1
with open(os.environ["FAKE_CAIROSVG_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(argv) + "\\n")
ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
png = b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", ihdr)
png += chunk(b"IDAT", zlib.compress(b"\\x00\\x00\\x00\\x00")) + chunk(b"IEND", b"")
open(argv[argv.index("-o") + 1], "wb").write(png)
"""


def _fake_renderer_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "cairosvg"
    fake.write_text(_LOGGING_CAIROSVG.format(python=sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "renderer.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_CAIROSVG_LOG": str(log),
    }
    return env, log


def test_render_raster_size_and_out_pass_through_and_stamp_the_source(
    tmp_path: Path,
) -> None:
    env, log = _fake_renderer_env(tmp_path)
    svg = tmp_path / "card.svg"
    svg.write_text("<svg>logged</svg>", encoding="utf-8")
    out = tmp_path / "icons" / "card-16.png"
    argv = [sys.executable, str(SCRIPT), "--size", "16", "--out", str(out), str(svg)]

    result = subprocess.run(argv, env=env, check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        [str(svg), "-o", str(out), "--output-width", "16", "--output-height", "16"]
    ]
    assert "-u" not in calls[0] and "--unsafe" not in calls[0]
    assert _png_size(out) == (16, 16)
    digest = hashlib.sha256(svg.read_bytes()).hexdigest().encode()
    assert _png_texts(out.read_bytes())[b"omega-source-sha256"] == [digest]


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--size", "0", "x.svg"],
        ["--size", "abc", "x.svg"],
        ["--out"],
        ["card.txt"],
    ],
)
def test_render_raster_usage_errors_exit_2(tmp_path: Path, args: list[str]) -> None:
    env, log = _fake_renderer_env(tmp_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stderr.startswith("usage: render_raster.py")
    assert not log.exists(), "a usage error must not reach the renderer"


ICON_SIZES: tuple[int, ...] = (16, 32, 64, 128, 192, 256, 512)
ICON_DIR = ROOT / "omega_prime" / "grokbot" / "listing" / "icons"
ICON_SOURCE = ROOT / "assets" / "icon.svg"


def _icon_failures(icon_dir: Path, source: Path) -> list[str]:
    """Every way the icon set disagrees with ``source``; empty when it matches."""
    digest = hashlib.sha256(source.read_bytes()).hexdigest().encode()
    failures: list[str] = []
    for size in ICON_SIZES:
        name = f"icon-{size}.png"
        path = icon_dir / name
        if not path.is_file():
            failures.append(f"{name} is missing")
        elif _png_size(path) != (size, size):
            failures.append(f"{name} is not {size} by {size}")
        elif _png_texts(path.read_bytes()).get(b"omega-source-sha256") != [digest]:
            failures.append(
                f"{name} was not rendered from the current icon.svg; run "
                f"python3 omega_prime/scripts/render_raster.py --size {size} "
                f"--out omega_prime/grokbot/listing/icons/{name} assets/icon.svg"
            )
    expected = {f"icon-{size}.png" for size in ICON_SIZES}
    for extra in sorted(p.name for p in icon_dir.iterdir()):
        if extra not in expected:
            failures.append(f"unexpected file {extra}")
    return failures


def test_icon_rasters_match_the_current_brand_svg() -> None:
    assert _icon_failures(ICON_DIR, ICON_SOURCE) == []


def test_stale_icon_raster_is_reported(tmp_path: Path) -> None:
    icons = tmp_path / "icons"
    icons.mkdir()
    for path in ICON_DIR.iterdir():
        (icons / path.name).write_bytes(path.read_bytes())
    edited = tmp_path / "icon.svg"
    edited.write_bytes(ICON_SOURCE.read_bytes() + b"<!-- edited -->")

    failures = _icon_failures(icons, edited)

    assert len(failures) == len(ICON_SIZES)
    assert failures[0].startswith(
        "icon-16.png was not rendered from the current icon.svg"
    )


def test_icon_check_flags_missing_and_extra_files(tmp_path: Path) -> None:
    icons = tmp_path / "icons"
    icons.mkdir()
    for path in ICON_DIR.iterdir():
        (icons / path.name).write_bytes(path.read_bytes())
    (icons / "icon-32.png").unlink()
    (icons / "icon-48.png").write_bytes(b"")

    assert _icon_failures(icons, ICON_SOURCE) == [
        "icon-32.png is missing",
        "unexpected file icon-48.png",
    ]


BANNER_SVG = ROOT / "omega_prime" / "grokbot" / "listing" / "banner.svg"
BANNER_PNG = BANNER_SVG.with_suffix(".png")
PHASE78_BANNER = ROOT / "assets" / "banner.svg"
_BANNER_TEXT = [
    "Omega Prime",
    "One Grok programming bot.",
    "Local proof: MCP tool host and readiness report",
    "Evidence levels: local proof, optional capability, unproven external",
]
_LIVE_CLAIM = re.compile(
    r"\b(connect\w*|attach\w*|live|works with|running)\b", re.IGNORECASE
)


def _text_runs(raw: str) -> list[tuple[str, str]]:
    """(fill, text) of each text or tspan element with visible text, in order."""
    root = minidom.parseString(raw).documentElement
    assert root is not None
    runs = []
    for node in root.getElementsByTagName("*"):
        label = "".join(c.data for c in node.childNodes if c.nodeType == 3).strip()
        if node.tagName in ("text", "tspan") and label:
            runs.append((node.getAttribute("fill"), label))
    return runs


def _desc_text(raw: str) -> str:
    root = minidom.parseString(raw).documentElement
    assert root is not None
    return " ".join(
        "".join(c.data for c in node.childNodes if c.nodeType == 3)
        for node in root.getElementsByTagName("desc")
    )


def test_listing_banner_text_contrast_matches_phase78() -> None:
    raw = BANNER_SVG.read_text(encoding="utf-8")
    assert _text_contrast_failures(raw) == []
    listing = {fill for fill, _ in _text_runs(raw)}
    phase78 = {
        fill for fill, _ in _text_runs(PHASE78_BANNER.read_text(encoding="utf-8"))
    }
    assert listing
    assert listing <= phase78


def test_listing_banner_names_title_tagline_and_evidence_levels() -> None:
    raw = BANNER_SVG.read_text(encoding="utf-8")
    texts = [text for _, text in _text_runs(raw)]
    assert texts == _BANNER_TEXT
    assert "grok bot import" not in " ".join([*texts, _desc_text(raw)]).lower()
    assert texts[2].startswith("Local proof: ")
    legend = texts[3].lower()
    for level in ("local proof", "optional capability", "unproven external"):
        assert level in legend


def test_listing_banner_makes_no_live_grok_claim() -> None:
    raw = BANNER_SVG.read_text(encoding="utf-8")
    joined = "\n".join([text for _, text in _text_runs(raw)] + [_desc_text(raw)])
    assert _LIVE_CLAIM.search(joined) is None


def test_listing_banner_png_is_stamped_from_its_svg() -> None:
    assert _png_size(BANNER_PNG) == (1200, 400)
    digest = hashlib.sha256(BANNER_SVG.read_bytes()).hexdigest().encode()
    assert _png_texts(BANNER_PNG.read_bytes()).get(b"omega-source-sha256") == [digest]


def test_listing_banner_is_self_contained() -> None:
    raw = BANNER_SVG.read_text(encoding="utf-8")
    # The SVG namespace identifier is not a reference; strip it as the asset check does.
    without_ns = raw.replace('xmlns="http://www.w3.org/2000/svg"', "")
    for token in ("href", "xlink", "http://", "https://", "<script"):
        assert token not in without_ns, token

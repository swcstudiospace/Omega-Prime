"""Grok Bot listing icons are rendered from the brand SVG and stay in step with it."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omega_prime.tests.test_public_repo import _png_size, _png_texts

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

"""Render an SVG asset to its PNG and record which SVG the PNG came from.

usage: python3 omega_prime/scripts/render_raster.py [--size N] [--out PATH] <asset.svg>

Runs CairoSVG (``cairosvg`` on PATH) to write the PNG, then adds one ``tEXt``
chunk, ``omega-source-sha256``, holding the SHA-256 of the input SVG bytes.
``omega_prime/tests/test_public_repo.py`` fails when an SVG no longer matches
the digest its PNG carries, so edited art cannot ship with a stale raster.

Options:
  --size N    render a square N by N pixel PNG (passed to CairoSVG as
              ``--output-width N --output-height N``); N is a positive integer.
  --out PATH  write the PNG to PATH (its parent directory is created) instead
              of the sibling ``.png`` of the SVG.

The digest is always taken from the input SVG, never from the output file.
CairoSVG is never run with ``-u``/``--unsafe``.

Exit status: 0 when the PNG is written and stamped; 2 for a usage error or
no ``cairosvg`` on PATH; otherwise CairoSVG's own non-zero status, with the
previous PNG left as CairoSVG left it.
"""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

KEYWORD = b"omega-source-sha256"
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data)
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def stamp(png: bytes, digest: str) -> bytes:
    """``png`` with exactly one source-digest text chunk, placed before IEND."""
    if not png.startswith(_SIGNATURE):
        raise ValueError("not a PNG file")
    parts = [png[: len(_SIGNATURE)]]
    pos = len(_SIGNATURE)
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind = png[pos + 4 : pos + 8]
        data = png[pos + 8 : pos + 8 + length]
        end = pos + 12 + length
        if kind == b"IEND":
            parts.append(_chunk(b"tEXt", KEYWORD + b"\x00" + digest.encode("ascii")))
        if not (kind == b"tEXt" and data.partition(b"\x00")[0] == KEYWORD):
            parts.append(png[pos:end])
        pos = end
    return b"".join(parts)


def _usage() -> int:
    print(
        "usage: render_raster.py [--size N] [--out PATH] <asset.svg>",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str]) -> int:
    size: int | None = None
    out: str | None = None
    positional: list[str] = []
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--size", "--out"):
            if i + 1 >= len(args):
                return _usage()
            value = args[i + 1]
            if arg == "--out":
                out = value
            else:
                if not (value.isascii() and value.isdigit()) or int(value) < 1:
                    return _usage()
                size = int(value)
            i += 2
        elif arg.startswith("-"):
            return _usage()
        else:
            positional.append(arg)
            i += 1
    if len(positional) != 1 or not positional[0].endswith(".svg"):
        return _usage()
    renderer = shutil.which("cairosvg")
    if renderer is None:
        print("render_raster: cairosvg is not on PATH", file=sys.stderr)
        return 2
    svg = Path(positional[0])
    if out is None:
        png = svg.with_suffix(".png")
    else:
        png = Path(out)
        png.parent.mkdir(parents=True, exist_ok=True)
    command = [renderer, str(svg), "-o", str(png)]
    if size is not None:
        command += ["--output-width", str(size), "--output-height", str(size)]
    rendered = subprocess.run(command, check=False)
    if rendered.returncode != 0:
        return rendered.returncode
    digest = hashlib.sha256(svg.read_bytes()).hexdigest()
    png.write_bytes(stamp(png.read_bytes(), digest))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

# Phase 97 Plan 01 Summary: Icon rasters and render options

## Files

Changed in this pass:
- `omega_prime/grokbot/listing/icons/icon-16.png`, `icon-32.png`, `icon-64.png`, `icon-128.png`, `icon-192.png`, `icon-256.png`, `icon-512.png` (re-rendered through `render_raster.py`; no PNG byte hand-edited)
- `.planning/phases/97-brand-assets/97-01-SUMMARY.md` (this file)

Already on disk when this pass began, verified by reading, not rewritten:
- `omega_prime/scripts/render_raster.py` holds the `--size N` / `--out PATH` grammar, `--output-width`/`--output-height` argv, the usage exit 2, and the digest taken from the input SVG. It contains no `-u` or `--unsafe`.
- `omega_prime/tests/test_grokbot_listing_assets.py` holds the render tests, the usage-error test, `_icon_failures`, and the three icon tests. The same module also holds the banner tests, which belong to plan 97-02; those were not touched.

## Render commands and exit codes

`command -v cairosvg` → `/usr/local/bin/cairosvg` (present).

From the repository root:

| Command | Exit |
|---|---|
| `python3 omega_prime/scripts/render_raster.py --size 16 --out omega_prime/grokbot/listing/icons/icon-16.png assets/icon.svg` | 0 |
| `... --size 32 --out omega_prime/grokbot/listing/icons/icon-32.png assets/icon.svg` | 0 |
| `... --size 64 --out omega_prime/grokbot/listing/icons/icon-64.png assets/icon.svg` | 0 |
| `... --size 128 --out omega_prime/grokbot/listing/icons/icon-128.png assets/icon.svg` | 0 |
| `... --size 192 --out omega_prime/grokbot/listing/icons/icon-192.png assets/icon.svg` | 0 |
| `... --size 256 --out omega_prime/grokbot/listing/icons/icon-256.png assets/icon.svg` | 0 |
| `... --size 512 --out omega_prime/grokbot/listing/icons/icon-512.png assets/icon.svg` | 0 |

No command exited non-zero, so the loop never stopped early.

## Read-only checks of the output (not gates)

- `icons/` holds exactly the seven expected files.
- IHDR of each file is N by N for its N.
- Each file has one `tEXt` chunk, `omega-source-sha256`, equal to `e023a819520740e3899c1c70ab7bc9bfcc5e43c7b8a833c397dafb248f62ae5b`, which is `sha256sum assets/icon.svg`.

## Not run

pytest, ruff, mypy, LSP, and formatters were not run, per the assignment. The orchestrator owns the acceptance checks.

## Remaining

- Orchestrator gates: pytest on `test_grokbot_listing_assets.py` and `test_public_repo.py`, ruff, mypy.
- Nothing staged or committed.

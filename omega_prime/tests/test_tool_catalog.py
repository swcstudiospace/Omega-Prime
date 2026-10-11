"""Phase 45: the tool catalog renders every rostered tool and stays current."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omega_prime.mcp_server import roster_names
from omega_prime.tooling.catalog import main, render

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent

_HEADING = re.compile(r"^## (\S+)\s*$", re.MULTILINE)


def _roster() -> list[str]:
    path = OMEGA_PRIME / "contracts" / "tool-rosters" / "omega-prime.yaml"
    return roster_names(path.read_text(encoding="utf-8"))


def test_render_preserves_roster_order_exactly_once(tmp_path: Path) -> None:
    text = render(ROOT, tmp_path)
    headings = _HEADING.findall(text)
    assert headings == _roster()


def test_check_passes_on_committed_file_and_fails_on_drift(tmp_path: Path) -> None:
    assert main(["--check"]) == 0
    stale = tmp_path / "catalog.md"
    stale.write_text("# Tool catalog\n\nstale\n", encoding="utf-8")
    assert main(["--check", "--out", str(stale)]) == 1


def test_unknown_rostered_definition_refuses_catalog_without_overwriting_output(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixture-root"
    roster = fixture_root / "omega_prime/contracts/tool-rosters/omega-prime.yaml"
    roster.parent.mkdir(parents=True)
    unknown = "catalog_fixture_tool_with_no_registered_definition"
    roster.write_text(f"tools:\n  - {unknown}\n", encoding="utf-8")
    output = tmp_path / "catalog.md"
    output.write_bytes(b"# Maintained output sentinel\n\x00")
    output.chmod(0o640)
    before = (output.read_bytes(), output.stat())

    # Real registry/schema construction; no fake schema or registry echo.
    with pytest.raises(SystemExit) as caught:
        main(["--root", str(fixture_root), "--out", str(output)])

    assert unknown in str(caught.value)
    after = output.stat()
    assert output.read_bytes() == before[0]
    assert (after.st_mode, after.st_mtime_ns, after.st_ino) == (
        before[1].st_mode,
        before[1].st_mtime_ns,
        before[1].st_ino,
    )

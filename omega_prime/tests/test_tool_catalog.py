"""Phase 45: the tool catalog renders every rostered tool and stays current."""

from __future__ import annotations

import re
from pathlib import Path

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

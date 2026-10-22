"""The receipts this branch commits must pass the receipt validator.

Structural checks only (no captured executions): every claim cites an
existing command, and a claim about a failing command is marked
``expects_failure``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.receipts import validate_receipt

ROOT = Path(__file__).resolve().parents[2]
RECEIPTS = ROOT / ".receipts" / "bot-00-omega-prime"


@pytest.mark.parametrize(
    "name",
    [
        "phase-103-ultrathink-integration-and-cleanup.json",
        "pr-46-greptile-round-1.json",
    ],
)
def test_phase_103_receipts_validate(name: str) -> None:
    receipt = json.loads((RECEIPTS / name).read_text(encoding="utf-8"))
    validate_receipt(receipt)

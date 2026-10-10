# SPDX-License-Identifier: AGPL-3.0-only
"""Prime diagnostic gating regressions (Phase 75-07, T-75-04-03).

Consumer-visible proof that complete refusal diagnostics reach the shared
full-input redaction+strong gate before any clip, so a low-entropy prefix
window cannot publish while the full candidate trips the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omega_prime.config import PRIME_FAMILIES
from omega_prime.grokbot import local_validation as lv
from omega_prime.grokbot import readiness_prime as prime
from omega_prime.grokbot.audit import GrokBotAuditTracer, redact_sensitive
from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets

ROOT = Path(__file__).resolve().parents[2]

# Parent ACTUAL repro shape: low-entropy repeat prefix plus a high-entropy
# tail. The prefix alone is scanner-clean (4 distinct chars); the full
# candidate trips the unchanged strong gate.
_CANDIDATE = (
    "bA3c" * 100
    + ("aB0dE2fG4hI6jK8mN0pQ2rS4tU6vW8xY0zA2bC4dE6fG8hI0jK2lM4nO6pQ8rS0tU") * 10
)


def _prime_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> lv.ReadinessProbe:
    """Real ON-runtime probe: production emulator, targeted approvals."""
    for family in PRIME_FAMILIES:
        monkeypatch.setenv(f"OMEGA_PRIME_PRIME_{family.upper()}_ENABLED", "1")
    layout = lv.LocalLayout(
        source=ROOT,
        base=tmp_path,
        install=tmp_path,
        home=tmp_path,
        work=tmp_path,
        state=tmp_path,
        audit=tmp_path,
        command_log=tmp_path / "log.jsonl",
        env={},
    )
    probe = lv.ReadinessProbe(layout, include_prime=True)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    from omega_prime.grokbot.emulator import GrokBotTurnEmulator

    emulator = GrokBotTurnEmulator(
        root=ROOT,
        auditor=GrokBotAuditTracer(tmp_path / "audit.jsonl"),
        home=home,
        approvals=tuple(lv.TARGETED_LOCAL_APPROVALS),
        env={},
    )
    assert "rlm_rename" in emulator.runtime.tool_names
    probe.attach(emulator)
    return probe


def test_prime_refusal_withholds_low_entropy_prefix_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real rlm_rename refusal echoes the full candidate but publishes withheld."""
    probe = _prime_probe(tmp_path, monkeypatch)
    text = prime._expect_refusal(
        probe,
        "owned-prime-refusal",
        "rlm_rename",
        {"target": _CANDIDATE, "name": "owned-negative-name"},
        ("No direct RLM subagent",),
    )
    # Real negative dispatch, not a forwarded mock: the handler echoes.
    assert _CANDIDATE in text
    assert "No direct RLM subagent" in text
    # Genuine gap, not authorization; no false success path taken.
    assert "approval required" not in text
    assert "policy forbids" not in text
    # Audit-clean premise, strong gate rejects the FULL diagnostic.
    assert redact_sensitive(text) == text
    with pytest.raises(SecretLeakError):
        assert_no_secrets(text, "probe")
    # The low-entropy prefix window alone would have passed: this is the trap
    # the removed producer windows fell into.
    assert_no_secrets(f"evidence {_CANDIDATE[:40]} end", "probe")
    # Consumer-visible publication: control-only withheld, no fragment leaks.
    assert len(probe.checks) == 1
    row = probe.checks[0]
    assert row["name"] == "owned-prime-refusal"
    assert row["tools"] == []
    detail = row["detail"]
    assert detail == lv._CURATE_WITHHELD
    assert _CANDIDATE[:40] not in detail
    assert _CANDIDATE[-40:] not in detail
    assert _CANDIDATE not in detail
    assert _CANDIDATE[:12] not in json.dumps(probe.checks)
    # Final whole-report scanner accepts what was published.
    assert_no_secrets(json.dumps(probe.checks), "readiness-report")


def test_prime_trailing_secret_preserves_prior_and_withholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic ending in secret withholds without touching prior output."""
    probe = _prime_probe(tmp_path, monkeypatch)
    probe.record("owned-prior-safe", (), "prior safe evidence")
    prior = probe.checks[-1]["detail"]
    assert "prior safe evidence" in prior
    token = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY2zZ4"
    trailing = "owned diagnostic " + "q " * 40 + token
    assert redact_sensitive(trailing) == trailing
    with pytest.raises(SecretLeakError):
        assert_no_secrets(trailing, "probe")
    probe.blocked(
        "owned-trailing",
        ("rlm_rename",),
        ("existing rlm child id (requires configured run_child)",),
        f"no child exists on the unconfigured host: {trailing}",
    )
    # Prior artifact preserved byte-for-byte; new row withholds.
    assert probe.checks[0]["detail"] == prior
    detail = probe.checks[-1]["detail"]
    assert detail == lv._CURATE_WITHHELD
    assert token not in detail
    assert token[:12] not in detail
    assert token[-12:] not in detail
    assert token not in json.dumps(probe.checks)
    assert_no_secrets(json.dumps(probe.checks), "readiness-report")

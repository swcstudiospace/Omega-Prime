# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""``/omega-redteam-triage-apply``: preview and apply of PROPOSAL verdicts.

Hermetic: the review queue lives in ``tmp_path``, the triage script is a
fixture copy, and both ``git status`` and the UTC date are stubbed. The
real repository script is never touched. No sockets open.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from omega_prime.commands import (
    COMMANDS,
    command_catalog,
    execute_command,
)
from omega_prime.commands import (
    redteam_triage_apply as triage_apply,
)

REPORTS_ENV_VAR = "OMEGA_PRIME_SIDECAR_REPORTS_DIR"

DIGEST_A = "ab" * 32
DIGEST_B = "cd" * 32
DIGEST_C = "12" * 32
UNKNOWN = "ef" * 32
STAMP = "2026-10-13"

EMPTY_SCRIPT = "TRIAGED: dict[str, tuple[str, str]] = {\n}\n"


def _harness(
    monkeypatch: Any, tmp_path: Path, script_text: str = EMPTY_SCRIPT
) -> tuple[dict[str, str], Path, Path]:
    """Isolated queue dir, fixture script, and stubbed git/date."""
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "redteam-audit-triage.py"
    script.write_text(script_text, encoding="utf-8")
    monkeypatch.setattr(triage_apply, "SCRIPT_PATH_OVERRIDE", script)
    monkeypatch.setattr(triage_apply, "_git_status_porcelain", lambda _s: "")
    monkeypatch.setattr(triage_apply, "_today_utc", lambda: STAMP)
    monkeypatch.setenv(REPORTS_ENV_VAR, str(reports))
    return {REPORTS_ENV_VAR: str(reports)}, reports / "review-queue.ndjson", script


def _seed(
    queue: Path, digest: str, rationale: str, ts: str = "2026-10-13T00:00:00+00:00"
) -> None:
    with queue.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "dedupe_hash": digest,
                    "rationale": rationale,
                    "ts": ts,
                    "proposer": "operator",
                },
                sort_keys=True,
            )
            + "\n"
        )


def _load_triaged(script: Path) -> dict[str, tuple[str, str]]:
    spec = importlib.util.spec_from_file_location("triage_fixture", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.TRIAGED)


def test_catalog_registers_triage_apply_with_exact_usage() -> None:
    by_name = {spec.name: spec for spec in COMMANDS}
    assert by_name[triage_apply.NAME].usage == triage_apply.USAGE
    assert by_name[triage_apply.NAME].needs_args is True
    catalog = {row["name"]: row for row in command_catalog()}
    assert catalog[triage_apply.NAME]["usage"] == triage_apply.USAGE


def test_preview_renders_diff_without_mutating(
    tmp_path: Path, monkeypatch: Any
) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_A, "dev-only transitive; loopback sidecar never loads it.")
    before = script.read_bytes()
    result = triage_apply.run(f"--hash {DIGEST_A}", env)
    assert result["preview_only"] is True
    assert f"+    {json.dumps(DIGEST_A)}:" in result["diff"]
    assert STAMP in result["diff"]
    assert result["queue"] == str(queue)
    assert result["proposer"] == "operator"
    assert script.read_bytes() == before


def test_preview_diff_matches_applied_result(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_A, "first rationale")
    preview = triage_apply.run(f"--hash {DIGEST_A}", env)
    applied = triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert applied == {
        "applied": True,
        "key": DIGEST_A,
        "date": STAMP,
        "self_check": "pass",
    }
    # The previewed addition is exactly the line the apply inserted.
    added = [line for line in preview["diff"].splitlines() if line.startswith("+    ")]
    assert len(added) == 1
    assert added[0][1:] in script.read_text(encoding="utf-8")


def test_apply_inserts_verbatim_rationale_and_refuses_reapply(
    tmp_path: Path, monkeypatch: Any
) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    rationale = "dev-only transitive; loopback sidecar never loads it."
    _seed(queue, DIGEST_A, rationale)
    result = triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert result["self_check"] == "pass"
    assert _load_triaged(script) == {DIGEST_A: (STAMP, rationale)}
    with pytest.raises(triage_apply.Error) as excinfo:
        triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert excinfo.value.error == "already_triaged"
    text = script.read_text(encoding="utf-8")
    assert text.count(DIGEST_A) == 1


def test_newest_proposal_wins(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_A, "stale rationale", "2026-10-12T00:00:00+00:00")
    _seed(queue, DIGEST_A, "current rationale", "2026-10-13T00:00:00+00:00")
    result = triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert result["self_check"] == "pass"
    assert _load_triaged(script)[DIGEST_A][1] == "current rationale"


def test_rationale_with_quotes_round_trips(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    rationale = 'says "dev-only" and it\'s loopback-only'
    _seed(queue, DIGEST_A, rationale)
    result = triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert result["self_check"] == "pass"
    assert _load_triaged(script) == {DIGEST_A: (STAMP, rationale)}


def test_apply_keeps_alphabetical_key_order(tmp_path: Path, monkeypatch: Any) -> None:
    seeded = (
        "TRIAGED: dict[str, tuple[str, str]] = {\n"
        f'    {json.dumps(DIGEST_B)}: ("2026-10-12", "second"),\n'
        f'    {json.dumps("f0" * 32)}: ("2026-10-12", "third"),\n'
        "}\n"
    )
    env, queue, script = _harness(monkeypatch, tmp_path, seeded)
    _seed(queue, DIGEST_A, "first")
    result = triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert result["self_check"] == "pass"
    triaged = _load_triaged(script)
    assert list(triaged) == sorted(triaged)
    assert set(triaged) == {DIGEST_A, DIGEST_B, "f0" * 32}


def test_untriage_preview_and_apply(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_A, "approved earlier")
    triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    before = script.read_bytes()
    preview = triage_apply.run(f"--hash {DIGEST_A} --untriage", env)
    assert preview["preview_only"] is True
    assert f"-    {json.dumps(DIGEST_A)}:" in preview["diff"]
    assert script.read_bytes() == before
    removed = triage_apply.run(f"--hash {DIGEST_A} --untriage --apply", env)
    assert removed["applied"] is True
    assert removed["untriaged"] is True
    assert removed["self_check"] == "pass"
    assert _load_triaged(script) == {}
    with pytest.raises(triage_apply.Error) as excinfo:
        triage_apply.run(f"--hash {DIGEST_A} --untriage --apply", env)
    assert excinfo.value.error == "not_triaged"


def test_no_proposal_refusals(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    before = script.read_bytes()
    with pytest.raises(triage_apply.Error) as excinfo:
        triage_apply.run(f"--hash {UNKNOWN} --apply", env)
    assert excinfo.value.error == "no_proposal"
    assert script.read_bytes() == before
    # Empty queue file is a refusal, not a crash.
    queue.write_text("", encoding="utf-8")
    with pytest.raises(triage_apply.Error) as excinfo:
        triage_apply.run(f"--hash {UNKNOWN}", env)
    assert excinfo.value.error == "no_proposal"


def test_dirty_script_refuses_without_writes(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_A, "needs approval")
    monkeypatch.setattr(
        triage_apply,
        "_git_status_porcelain",
        lambda _s: " M scripts/redteam-audit-triage.py\n",
    )
    before = script.read_bytes()
    with pytest.raises(triage_apply.Error) as excinfo:
        triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert excinfo.value.error == "dirty_script"
    assert script.read_bytes() == before
    with pytest.raises(triage_apply.Error) as excinfo:
        triage_apply.run(f"--hash {DIGEST_A}", env)
    assert excinfo.value.error == "dirty_script"
    assert script.read_bytes() == before


def test_bad_flags_and_hashes(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_A, "rationale")
    before = script.read_bytes()
    bad = [
        "",
        "--hash",
        f"--hash {DIGEST_A.upper()}",
        f"--hash {'ab' * 31}",
        f"--hash {DIGEST_A} --bogus",
        f"--hash {DIGEST_A} --hash {DIGEST_B}",
        f"--apply --hash {DIGEST_A} extra",
    ]
    for args in bad:
        with pytest.raises(triage_apply.Error) as excinfo:
            triage_apply.run(args, env)
        assert excinfo.value.error == "bad_args"
        assert excinfo.value.result["usage"] == triage_apply.USAGE
    assert script.read_bytes() == before


def test_self_check_failure_rolls_back(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, script = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_C, "rationale")

    def _broken(_script: Path) -> None:
        raise ValueError("simulated corrupt post-edit import")

    monkeypatch.setattr(triage_apply, "_self_check", _broken)
    before = script.read_bytes()
    with pytest.raises(triage_apply.Error) as excinfo:
        triage_apply.run(f"--hash {DIGEST_C} --apply", env)
    assert excinfo.value.error == "self_check_failed"
    assert excinfo.value.result["self_check"] == "fail"
    assert script.read_bytes() == before


def test_applies_to_real_script_shape(tmp_path: Path, monkeypatch: Any) -> None:
    from pathlib import Path as _Path

    real = _Path("scripts/redteam-audit-triage.py").read_text(encoding="utf-8")
    env, queue, script = _harness(monkeypatch, tmp_path, real)
    _seed(queue, DIGEST_A, "dev-only transitive; loopback sidecar never loads it.")
    preview = triage_apply.run(f"--hash {DIGEST_A}", env)
    assert preview["preview_only"] is True
    applied = triage_apply.run(f"--hash {DIGEST_A} --apply", env)
    assert applied["self_check"] == "pass"
    assert _load_triaged(script)[DIGEST_A][1].startswith("dev-only")


def test_execute_command_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    env, queue, _ = _harness(monkeypatch, tmp_path)
    _seed(queue, DIGEST_A, "via chat")
    body = execute_command(
        f"/omega-redteam-triage-apply --hash {DIGEST_A}", object(), env
    )
    assert body["ok"] is True
    assert body["result"]["preview_only"] is True
    body = execute_command(
        f"/omega-redteam-triage-apply --hash {DIGEST_A} --apply", object(), env
    )
    assert body["ok"] is True
    assert body["result"]["self_check"] == "pass"
    body = execute_command(
        f"/omega-redteam-triage-apply --hash {UNKNOWN} --apply", object(), env
    )
    assert body["ok"] is False
    assert body["error"] == "no_proposal"

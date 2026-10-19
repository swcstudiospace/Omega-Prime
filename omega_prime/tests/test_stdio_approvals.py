# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Human grants for gated tools on the stdio route.

The file is read at startup. The process never writes it. The bot cannot
name itself as the approver. lead_doctor check stays a read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from omega_prime.grokbot import oneclick
from omega_prime.grokbot.approvals_file import (
    BOT_ID,
    load_approvals_file,
    resolve_approvals_path,
)
from omega_prime.grokbot.oneclick import main
from omega_prime.mcp_server import default_registry, load_runtime

ROOT = Path(__file__).resolve().parents[2]


def _grant(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_loader_refuses_the_bot_and_a_file_inside_the_repo(tmp_path: Path) -> None:
    outside = _grant(tmp_path / "state" / "approvals", f"lead_doctor:{BOT_ID}\n")
    with pytest.raises(ValueError, match="person"):
        load_approvals_file(outside, repo_root=tmp_path / "repo")

    repo = tmp_path / "repo"
    inside = _grant(repo / "approvals", "lead_intake_next:operator\n")
    with pytest.raises(ValueError, match="outside the repository"):
        load_approvals_file(inside, repo_root=repo)


def test_loader_refuses_a_world_readable_file_and_never_writes(tmp_path: Path) -> None:
    path = _grant(
        tmp_path / "state" / "approvals",
        "lead_intake_next:operator\n",
        mode=0o644,
    )
    with pytest.raises(ValueError, match="chmod 600"):
        load_approvals_file(path, repo_root=tmp_path / "repo")
    source = Path(load_approvals_file.__code__.co_filename).read_text(encoding="utf-8")
    assert "write_text" not in source
    assert "atomic_write" not in source
    assert "O_WRONLY" not in source


def test_file_grant_allows_a_gated_tool_and_check_does_not_need_one(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    path = _grant(state / "approvals", "lead_intake_next:operator\n# note\n")
    grants = load_approvals_file(path, repo_root=ROOT)
    assert grants == [("lead_intake_next", "operator")]
    env = {"OMEGA_PRIME_STATE_DIR": str(state)}
    runtime = load_runtime(
        ROOT, tmp_path / "home", approvals=grants, env=env, no_roster=True
    )
    claimed = json.loads(runtime.registry.dispatch("lead_intake_next", {}))
    assert claimed.get("error") != "approval required", claimed
    doctor = json.loads(
        default_registry(ROOT, tmp_path / "home", env=env).dispatch(
            "lead_doctor", {"action": "check"}
        )
    )
    assert doctor.get("error") != "approval required", doctor
    blocked = json.loads(
        default_registry(ROOT, tmp_path / "home", env=env).dispatch(
            "lead_doctor", {"action": "install_prompt"}
        )
    )
    assert blocked["error"] == "approval required"


def test_stdio_launcher_forwards_the_file_and_does_not_create_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    path = _grant(state / "approvals", "x_post:operator\n")
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(state))
    monkeypatch.delenv("OMEGA_PRIME_APPROVALS_FILE", raising=False)
    seen: list[list[str]] = []

    def fake_call(cmd: list[str]) -> int:
        seen.append(cmd)
        return 0

    monkeypatch.setattr(oneclick.subprocess, "call", fake_call)
    assert main(["--skip-doctor"]) == 0
    assert seen[0][seen[0].index("--approvals-file") + 1] == str(path)
    assert "--approve" not in seen[0]
    assert resolve_approvals_path() == path

    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path / "empty"))
    seen.clear()
    assert main(["--skip-doctor"]) == 0
    assert "--approvals-file" not in seen[0]
    assert not (tmp_path / "empty" / "approvals").exists()


def test_self_approver_names_are_refused(tmp_path: Path) -> None:
    for approver in ("assistant", "model", "self", "Bot-00-Omega-Prime"):
        path = _grant(tmp_path / approver / "approvals", f"x_post:{approver}\n")
        with pytest.raises(ValueError, match="person"):
            load_approvals_file(path, repo_root=tmp_path / "repo")

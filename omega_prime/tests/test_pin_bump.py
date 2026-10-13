# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Hermetic tests for the redteam pin-bump CLI (PLAN-G1).

Every case runs against tmp fixture trees with a fake ``run()`` runner —
no network, no real ``git``/``npm``/``node``/``gh``, no live checkout
state. The bump is driven through :func:`pin_bump.bump` (plus one
no-subprocess path through :func:`pin_bump.main`); the real
``sidecar.provenance`` checks run against the fixture root, which is
exactly what the CLI does in production.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from omega_prime.grokbot import pin_bump

PIN_OLD = "a" * 40
PIN_NEW = "b" * 40
PIN_TAG = "c" * 40
PIN_DECOY = "d" * 40

TOOLS = ["tempest_status", "tempest_report"]


def _cp(cmd: list[str], code: int, out: str = "", err: str = ""):
    return subprocess.CompletedProcess(cmd, code, out, err)


class FakeRun:
    """Canned git/npm/node/gh runner. Records every command."""

    def __init__(
        self,
        *,
        ls_remote: str = "",
        node_ok: bool = True,
        node_missing: bool = False,
        npm_doc: dict | None = None,
        npm_raw: str | None = None,
        npm_missing: bool = False,
        gh_missing: bool = False,
        fail_prefixes: tuple[tuple[str, ...], ...] = (),
    ):
        self.ls_remote = ls_remote
        self.node_ok = node_ok
        self.node_missing = node_missing
        self.npm_doc = npm_doc if npm_doc is not None else {"vulnerabilities": {}}
        self.npm_raw = npm_raw
        self.npm_missing = npm_missing
        self.gh_missing = gh_missing
        self.fail_prefixes = fail_prefixes
        self.cmds: list[tuple[list[str], object]] = []

    def __call__(self, cmd: list[str], cwd=None):
        cmd = list(cmd)
        self.cmds.append((cmd, cwd))
        if cmd[:2] == ["git", "ls-remote"]:
            return _cp(cmd, 0, self.ls_remote)
        if cmd[0] == "node":
            if self.node_missing:
                raise FileNotFoundError("No such file or directory: 'node'")
            return _cp(cmd, 0 if self.node_ok else 1, "v22.0.0\n")
        if cmd[0] == "npm":
            if self.npm_missing:
                raise FileNotFoundError("No such file or directory: 'npm'")
            raw = self.npm_raw if self.npm_raw is not None else json.dumps(self.npm_doc)
            code = 0 if self.npm_doc == {"vulnerabilities": {}} else 1
            return _cp(cmd, code, raw)
        if cmd[0] == "gh":
            if self.gh_missing:
                raise FileNotFoundError("No such file or directory: 'gh'")
            if cmd[1:3] == ["pr", "create"]:
                return _cp(cmd, 0, "https://example.test/pr/1\n")
            return _cp(cmd, 0, "gh version 2.0.0\n")
        if cmd[0] == "git":
            for prefix in self.fail_prefixes:
                if tuple(cmd[: len(prefix)]) == prefix:
                    return _cp(cmd, 1, "", "boom")
            return _cp(cmd, 0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    def kinds(self) -> list[list[str]]:
        return [cmd for cmd, _ in self.cmds]


def _audit(*entries: tuple[str, str, str]) -> dict:
    """npm audit doc from (package, severity, advisory-key) rows."""
    vulns = {}
    for package, severity, key in entries:
        vulns[package] = {"severity": severity, "via": [key], "range": ">=1.0.0"}
    return {"vulnerabilities": vulns}


def _write_fixture_root(
    tmp_path: Path,
    *,
    vendor_pin: str = PIN_OLD,
    upstream_skill: bytes = b"# skill v2\n",
    vendored_skill: bytes = b"# skill v1\n",
    upstream_avatar: bytes = b"\x89PNG-new",
    vendored_avatar: bytes = b"\x89PNG-old",
    upstream_tools: list[str] | None = None,
    extra_upstream: dict[str, bytes] | None = None,
    extra_vendored: dict[str, bytes] | None = None,
    with_saved_old_audit: bool = False,
    old_audit_doc: dict | None = None,
) -> Path:
    """Build a fake repo root with diverging pack, pins, and surface."""
    tools = TOOLS if upstream_tools is None else upstream_tools
    (tmp_path / "redteam" / "grokbot" / "skills" / "deep").mkdir(parents=True)
    (tmp_path / "redteam" / "grokbot" / "skills" / "deep" / "SKILL.md").write_bytes(
        upstream_skill
    )
    (tmp_path / "redteam" / "grokbot" / "avatars").mkdir(parents=True, exist_ok=True)
    (tmp_path / "redteam" / "grokbot" / "avatars" / "a.png").write_bytes(
        upstream_avatar
    )
    (tmp_path / "redteam" / "grokbot" / "manifest.json").write_text('{"v": 1}\n')
    for rel, data in (extra_upstream or {}).items():
        target = tmp_path / "redteam" / "grokbot" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    pack = tmp_path / "omega_prime" / "redteam" / "pack"
    (pack / "skills" / "deep").mkdir(parents=True)
    (pack / "skills" / "deep" / "SKILL.md").write_bytes(vendored_skill)
    (pack / "avatars").mkdir(parents=True, exist_ok=True)
    (pack / "avatars" / "a.png").write_bytes(vendored_avatar)
    (pack / "manifest.json").write_text('{"v": 1}\n')
    (pack / "LICENSE").write_text("vendored MIT notice\n")
    for rel, data in (extra_vendored or {}).items():
        target = pack / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    (tmp_path / "VENDOR.md").write_text(
        "# Vendored behavior\n\n"
        "## Other pack\n\n"
        f"- Commit: `{PIN_DECOY}`\n\n"
        "## T3MP3ST red team pack\n\n"
        "- Remote: http://127.0.0.1/purple-team.git\n"
        f"- Commit: `{vendor_pin}`\n",
        encoding="utf-8",
    )
    init = tmp_path / "omega_prime" / "redteam"
    (init / "__init__.py").write_text(
        f'"""Vendored pack.\n\nProvenance\n    Upstream commit: {vendor_pin}\n"""\n',
        encoding="utf-8",
    )
    src = tmp_path / "redteam" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "mcp-server.ts").write_text(
        "".join(f"    name: '{name}',\n" for name in tools), encoding="utf-8"
    )
    tools_dir = tmp_path / "omega_prime" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "redteam.py").write_text(
        "REDTEAM_TOOL_NAMES = (\n"
        + "".join(f'    "{name}",\n' for name in TOOLS)
        + ")\n",
        encoding="utf-8",
    )
    contract = tmp_path / "sidecar" / "contract"
    contract.mkdir(parents=True, exist_ok=True)
    (contract / "bot-interface.json").write_text(
        json.dumps({"tool_names": TOOLS}), encoding="utf-8"
    )
    if with_saved_old_audit:
        audit_dir = tmp_path / "omega_prime" / "tests" / "data"
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / f"redteam-audit-{PIN_OLD}.json").write_text(
            json.dumps(old_audit_doc if old_audit_doc is not None else _audit()),
            encoding="utf-8",
        )
    return tmp_path


def _pack(root: Path) -> Path:
    return root / "omega_prime" / "redteam" / "pack"


# Happy paths


def test_happy_sha_green_improved(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run)
    out = capsys.readouterr().out
    assert rc == 0
    # Pack re-vendored byte-identical, LICENSE preserved, binary copied.
    assert (
        _pack(root) / "skills" / "deep" / "SKILL.md"
    ).read_bytes() == b"# skill v2\n"
    assert (_pack(root) / "LICENSE").read_text() == "vendored MIT notice\n"
    assert (_pack(root) / "avatars" / "a.png").read_bytes() == b"\x89PNG-new"
    # Both pin records rewritten; the decoy Commit line untouched.
    vendor = (root / "VENDOR.md").read_text()
    assert f"- Commit: `{PIN_NEW}`" in vendor
    assert f"- Commit: `{PIN_DECOY}`" in vendor
    init = (root / "omega_prime" / "redteam" / "__init__.py").read_text()
    assert f"Upstream commit: {PIN_NEW}" in init
    # Verdict quoted verbatim; report footer carries it.
    assert "improved" in out
    report = (root / "report.md").read_text()
    assert "Verdict: improved" in report
    # Provenance actually green afterwards.
    from sidecar import provenance

    assert provenance.cmd_check(root, gitlink_override=PIN_NEW) == 0
    assert provenance.cmd_pack_diff(root) == 0


def test_already_identical_pack(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        upstream_skill=b"# same\n",
        vendored_skill=b"# same\n",
        upstream_avatar=b"\x89PNG-same",
        vendored_avatar=b"\x89PNG-same",
        with_saved_old_audit=True,
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun())
    out = capsys.readouterr().out
    assert rc == 0
    assert "improved" in out or "unchanged" in out


def test_skip_audit_with_node_absent(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=FakeRun(node_missing=True), skip_audit=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "audit-skipped" in out
    assert "Verdict:" not in out
    assert not (root / "report.md").exists()


def test_tag_resolved_via_ls_remote(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun(ls_remote=f"{PIN_TAG}\trefs/tags/v1.4\n")
    rc = pin_bump.bump("v1.4", root=root, run=run, skip_audit=True)
    capsys.readouterr()
    assert rc == 0
    assert f"- Commit: `{PIN_TAG}`" in (root / "VENDOR.md").read_text()
    assert run.cmds[0][0] == ["git", "ls-remote", "origin", "v1.4"]


def test_explicit_old_audit_without_saved_artifact(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(
        json.dumps(_audit(("left-pad", "high", "GHSA-old-0001"))), encoding="utf-8"
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), old_audit=str(old_audit))
    out = capsys.readouterr().out
    assert rc == 0
    assert "improved" in out


def test_json_mode_reports_pin_verdict_report(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), json_mode=True)
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(
        [line for line in out.splitlines() if line.startswith("{")][-1]
    )
    assert payload == {
        "pin": PIN_NEW,
        "verdict": "improved",
        "report": str(root / "report.md"),
    }


# Failure paths


def test_regressed_untriaged_high_exits_1_and_pushes_nothing(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(),
    )
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run)
    out = capsys.readouterr().out
    assert rc == 1
    assert "GHSA-new-0009" in out
    verbs = [cmd for cmd, _ in run.cmds]
    assert ["git", "push", "-u", "origin", f"pin-bump-{PIN_NEW[:7]}"] not in verbs
    assert not any(cmd[:2] == ["git", "commit"] for cmd in verbs)
    assert not any(cmd[:2] == ["gh", "pr"] for cmd in verbs)


def test_node_absent_without_skip_audit_exits_3(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(node_missing=True))
    err = capsys.readouterr().err
    assert rc == 3
    assert "node" in err
    assert "pin-moved" in err and "pack-revendored" in err and "provenance-green" in err


def test_npm_absent_exits_3(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(npm_missing=True))
    err = capsys.readouterr().err
    assert rc == 3
    assert "npm" in err


def test_bad_ref_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump("main", root=root, run=run)
    err = capsys.readouterr().err
    assert rc == 2
    assert "bad ref" in err
    assert run.cmds == []


def test_submodule_missing_exits_3(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    import shutil

    shutil.rmtree(root / "redteam")
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), skip_audit=True)
    err = capsys.readouterr().err
    assert rc == 3
    assert "submodule unreachable" in err


def test_unresolvable_tag_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    rc = pin_bump.bump("v9.9", root=root, run=FakeRun(ls_remote=""))
    err = capsys.readouterr().err
    assert rc == 2
    assert "unresolvable tag" in err


def test_pack_diff_still_diverged_after_2_passes(tmp_path, capsys, monkeypatch):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
    )
    monkeypatch.setattr(
        pin_bump.provenance,
        "cmd_pack_diff",
        lambda root, vendored_dir=None, upstream_dir=None: 1,
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun())
    out = capsys.readouterr().out
    assert rc == 1
    assert "2 passes" in out


def test_open_pr_without_gh_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=FakeRun(gh_missing=True), skip_audit=True, open_pr=True
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "gh" in err


def test_missing_old_audit_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)  # no saved artifact, no --old-audit
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun())
    err = capsys.readouterr().err
    assert rc == 2
    assert "--old-audit" in err


def test_surface_drift_reports_and_stops(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, upstream_tools=["tempest_status", "tempest_brand_new"]
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), skip_audit=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "DRIFT" in out


# Vendoring correctness


def test_extra_and_missing_upstream_converge(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        extra_upstream={"skills/new/SKILL.md": b"# new upstream file\n"},
        extra_vendored={"skills/stale/SKILL.md": b"# gone upstream\n"},
        with_saved_old_audit=True,
    )
    from sidecar import provenance

    before = provenance.diff_pack_dirs(_pack(root), root / "redteam" / "grokbot")
    assert any(line.startswith("EXTRA-UPSTREAM") for line in before)
    assert any(line.startswith("MISSING-UPSTREAM") for line in before)
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun())
    capsys.readouterr()
    assert rc == 0
    assert (_pack(root) / "skills" / "new" / "SKILL.md").read_bytes() == (
        b"# new upstream file\n"
    )
    assert not (_pack(root) / "skills" / "stale" / "SKILL.md").exists()
    assert provenance.cmd_pack_diff(root) == 0


# --open-pr and CLI surface


def test_open_pr_commits_bump_set_with_verdict(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, open_pr=True)
    out = capsys.readouterr().out
    assert rc == 0
    branch = f"pin-bump-{PIN_NEW[:7]}"
    verbs = [cmd for cmd, _ in run.cmds]
    assert ["git", "checkout", "-b", branch] in verbs
    assert [
        "git",
        "add",
        "redteam",
        "VENDOR.md",
        "omega_prime/redteam/__init__.py",
        "omega_prime/redteam/pack",
    ] in verbs
    assert any(cmd[:2] == ["git", "commit"] for cmd in verbs)
    assert ["git", "push", "-u", "origin", branch] in verbs
    pr = next(cmd for cmd in verbs if cmd[:2] == ["gh", "pr"])
    body = pr[pr.index("--body") + 1]
    assert "improved" in body
    assert "report.md" in body
    assert "https://example.test/pr/1" in out


def test_main_bad_ref_needs_no_subprocess(tmp_path, capsys):
    rc = pin_bump.main(["--to", "not-a-ref!", "--root", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "bad ref" in err


def test_main_requires_to():
    with pytest.raises(SystemExit) as exc:
        pin_bump.main([])
    assert exc.value.code == 2


def test_main_help_documents_exit_codes(capsys):
    with pytest.raises(SystemExit) as exc:
        pin_bump.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "exit codes" in out
    assert "0 = bump complete" in out

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

import base64
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from omega_prime.cron import scheduler as sched_mod
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
        origin_url: str = "https://github.com/example/purple-team.git",
        gh_api_content: str | None = None,
        gh_api_fail: bool = False,
        upstream_npm_doc: dict | None = None,
        git_log_vendor: str = "",
        git_show_vendor: str = "",
        npm_docs: list[dict] | None = None,
        git_show_package: str = "",
    ):
        self.ls_remote = ls_remote
        self.node_ok = node_ok
        self.node_missing = node_missing
        self.npm_doc = npm_doc if npm_doc is not None else {"vulnerabilities": {}}
        self.npm_raw = npm_raw
        self.npm_missing = npm_missing
        self.gh_missing = gh_missing
        self.fail_prefixes = fail_prefixes
        self.origin_url = origin_url
        self.gh_api_content = (
            gh_api_content
            if gh_api_content is not None
            else base64.b64encode(b'{"lockfileVersion": 3}').decode("ascii")
        )
        self.gh_api_fail = gh_api_fail
        self.upstream_npm_doc = upstream_npm_doc
        self.npm_docs = npm_docs
        self.npm_seq_calls = 0
        self.git_log_vendor = git_log_vendor
        self.git_show_vendor = git_show_vendor
        self.git_show_package = git_show_package
        self.npm_calls = 0
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
            # The auto-detect probe audits upstream HEAD before the local
            # pin audit, so the first npm call answers with the upstream
            # document when one is configured.
            self.npm_calls += 1
            if self.npm_calls == 1 and self.upstream_npm_doc is not None:
                return _cp(cmd, 1, json.dumps(self.upstream_npm_doc))
            if self.npm_docs:
                self.npm_seq_calls += 1
                seq_doc = self.npm_docs[
                    min(self.npm_seq_calls - 1, len(self.npm_docs) - 1)
                ]
                seq_raw = json.dumps(seq_doc)
                return _cp(cmd, 0 if seq_doc == {"vulnerabilities": {}} else 1, seq_raw)
            raw = self.npm_raw if self.npm_raw is not None else json.dumps(self.npm_doc)
            code = 0 if self.npm_doc == {"vulnerabilities": {}} else 1
            return _cp(cmd, code, raw)
        if cmd[0] == "gh":
            if self.gh_missing:
                raise FileNotFoundError("No such file or directory: 'gh'")
            if cmd[1:3] == ["pr", "create"]:
                return _cp(cmd, 0, "https://example.test/pr/1\n")
            if cmd[1] == "api":
                if self.gh_api_fail:
                    return _cp(cmd, 1, "", "boom")
                return _cp(cmd, 0, self.gh_api_content + "\n")
            return _cp(cmd, 0, "gh version 2.0.0\n")
        if cmd[0] == "git":
            if cmd[1:3] == ["config", "--get"]:
                return _cp(cmd, 0, self.origin_url + "\n")
            for prefix in self.fail_prefixes:
                if tuple(cmd[: len(prefix)]) == prefix:
                    return _cp(cmd, 1, "", "boom")
            if cmd[1:2] == ["log"]:
                return _cp(cmd, 0, self.git_log_vendor)
            if cmd[1:2] == ["show"]:
                target = cmd[-1] if len(cmd) > 2 else ""
                if target.endswith("VENDOR.md"):
                    return _cp(cmd, 0, self.git_show_vendor)
                if target.endswith("package.json"):
                    return _cp(cmd, 0, self.git_show_package)
                return _cp(cmd, 0, "")
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


def test_json_mode_schema_report_written_stdout_clean(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), json_mode=True)
    captured = capsys.readouterr()
    assert rc == 0
    # stdout carries exactly one JSON object — nothing else.
    assert len(captured.out.strip().splitlines()) == 1
    payload = json.loads(captured.out)
    assert set(payload) == {"verdict", "rows", "semver", "bump", "exit_code"}
    assert payload["verdict"] == "improved"
    assert payload["exit_code"] == 0
    assert payload["rows"] == [
        {
            "key": "GHSA-old-0001",
            "package": "left-pad",
            "old": ">=1.0.0",
            "new": "",
            "label": None,
        }
    ]
    assert payload["semver"] is None
    assert payload["bump"] is None
    # Normal mode still writes the markdown report.
    assert "Verdict: improved" in (root / "report.md").read_text()


def test_json_mode_keys_sorted_deterministically(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), json_mode=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert out == json.dumps(json.loads(out), sort_keys=True) + "\n"
    key_order = ["bump", "exit_code", "rows", "semver", "verdict"]
    positions = [out.index(f'"{key}"') for key in key_order]
    assert positions == sorted(positions)


def test_json_mode_semver_and_bump_commit(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        json_mode=True,
        semver_check=True,
        bump_commit=True,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert len(captured.out.strip().splitlines()) == 1
    payload = json.loads(captured.out)
    assert payload["verdict"] == "improved"
    assert payload["rows"] == [
        {
            "key": "GHSA-fix-0001",
            "package": "left-pad",
            "old": "1.2.3",
            "new": "1.4.0",
            "label": "compatible",
        }
    ]
    assert payload["semver"] == {"GHSA-fix-0001": "compatible"}
    assert payload["bump"] == {
        "applied": True,
        "level": "minor",
        "old": "1.2.3",
        "new": "1.3.0",
        "reason": None,
    }
    assert json.loads(pkg.read_text())["version"] == "1.3.0"
    assert (root / "report.md").exists()


def test_json_mode_warnings_go_to_stderr(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=FakeRun(), json_mode=True, bump_commit=True
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["exit_code"] == 0
    assert payload["bump"] is not None and payload["bump"]["applied"] is False
    assert "--semver-check" in payload["bump"]["reason"]
    assert "--bump-commit skipped" in captured.err
    assert "WARNING" not in captured.out


def test_json_mode_regressed_exit_code_1(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    run = FakeRun(npm_doc=_audit(("left-pad", "moderate", "GHSA-mod-1")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, json_mode=True)
    captured = capsys.readouterr()
    assert rc == 1
    payload = json.loads(captured.out)
    assert payload == {
        "verdict": "regressed",
        "rows": [],
        "semver": None,
        "bump": None,
        "exit_code": 1,
    }
    assert (root / "report.md").exists()


def test_json_mode_audit_only_no_report(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=FakeRun(), json_mode=True, audit_only=True
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert len(captured.out.strip().splitlines()) == 1
    payload = json.loads(captured.out)
    assert payload["verdict"] == "improved"
    assert payload["exit_code"] == 0
    assert payload["rows"] == [
        {
            "key": "GHSA-old-0001",
            "package": "left-pad",
            "old": ">=1.0.0",
            "new": "",
            "label": None,
        }
    ]
    assert not (root / "report.md").exists()


def test_json_mode_audit_only_semver_and_bump_suppressed(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        json_mode=True,
        audit_only=True,
        semver_check=True,
        bump_commit=True,
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["semver"] == {"GHSA-fix-0001": "compatible"}
    assert payload["bump"] == {
        "applied": False,
        "level": "minor",
        "old": "1.2.3",
        "new": "1.3.0",
        "reason": "audit-only (no files changed)",
    }
    # Suppressed: the package version is untouched and no report is written.
    assert (
        json.loads((root / "redteam" / "package.json").read_text())["version"]
        == "1.2.3"
    )
    assert not (root / "report.md").exists()


def test_json_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--json"])
    assert args.json_mode is True
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.json_mode is False


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


# --auto-detect-upstream-fix (batch-04 H5)


def test_auto_detect_skips_bump_when_upstream_still_vulnerable(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun(
        upstream_npm_doc=_audit(("left-pad", "high", "GHSA-xxxx-yyyy")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, auto_detect_upstream_fix=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to fix yet" in out
    assert "GHSA-xxxx-yyyy" in out
    # No local mutation: the pin records still point at the old pin and
    # git never fetched, checked out, or staged.
    vendor = (root / "VENDOR.md").read_text()
    assert f"- Commit: `{PIN_OLD}`" in vendor
    assert f"- Commit: `{PIN_NEW}`" not in vendor
    verbs = run.kinds()
    assert not any(cmd[:2] == ["git", "fetch"] for cmd in verbs)
    assert not any(cmd[:2] == ["git", "checkout"] for cmd in verbs)
    assert not any(cmd[:2] == ["git", "add"] for cmd in verbs)
    assert not (root / "report.md").exists()


def test_auto_detect_proceeds_when_upstream_clean(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    run = FakeRun(upstream_npm_doc=_audit())
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, auto_detect_upstream_fix=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "proceeding with bump" in out
    assert "unchanged" in out
    vendor = (root / "VENDOR.md").read_text()
    assert f"- Commit: `{PIN_NEW}`" in vendor


def test_auto_detect_inconclusive_proceeds(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    run = FakeRun(gh_api_fail=True, upstream_npm_doc=_audit())
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, auto_detect_upstream_fix=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "inconclusive" in out
    assert "proceeding with bump" in out
    vendor = (root / "VENDOR.md").read_text()
    assert f"- Commit: `{PIN_NEW}`" in vendor


def test_auto_detect_flag_parses():
    args = pin_bump.build_parser().parse_args(
        ["--to", PIN_NEW, "--auto-detect-upstream-fix"]
    )
    assert args.auto_detect_upstream_fix is True
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert args.auto_detect_upstream_fix is False


def test_parse_github_repo_shapes():
    assert pin_bump._parse_github_repo("https://github.com/acme/purple-team.git") == (
        "acme",
        "purple-team",
    )
    assert pin_bump._parse_github_repo("git@github.com:acme/purple-team.git") == (
        "acme",
        "purple-team",
    )
    assert pin_bump._parse_github_repo("https://example.test/acme/repo.git") is None
    assert pin_bump._parse_github_repo("") is None


# --dry-run mode (batch-05) -----------------------------------------------------


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` by relative path (zero-mutation proof)."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _mutating_verbs(cmds: list[list[str]]) -> list[list[str]]:
    """Commands that would change files, branches, or remote state."""
    mutating = []
    for cmd in cmds:
        if (
            cmd[:2] == ["git", "fetch"]
            or cmd[:2] == ["git", "checkout"]
            or cmd[:2] == ["git", "add"]
            or cmd[:2] == ["git", "commit"]
            or cmd[:2] == ["git", "push"]
            or cmd[:3] == ["gh", "pr", "create"]
        ):
            mutating.append(cmd)
    return mutating


def test_dry_run_success_mutates_nothing_and_reports(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"[dry-run] would move pin {PIN_OLD[:7]} -> {PIN_NEW}" in out
    assert "[dry-run] would re-vendor pack: copy 2, remove 0" in out
    assert f"[dry-run] would update VENDOR.md pin {PIN_OLD[:7]} ->" in out
    assert PIN_NEW[:7] in out
    assert "[dry-run] would update omega_prime/redteam/__init__.py" in out
    assert "[dry-run] audit verdict: unchanged" in out
    assert f"[dry-run] bump to {PIN_NEW[:7]} would succeed" in out
    assert _snapshot(root) == before
    assert not (root / "report.md").exists()
    assert _mutating_verbs(run.kinds()) == []


def test_dry_run_tag_resolves_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun(ls_remote=f"{PIN_TAG}\trefs/tags/v1.4\n")
    rc = pin_bump.bump("v1.4", root=root, run=run, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"[dry-run] would move pin {PIN_OLD[:7]} -> {PIN_TAG}" in out
    assert run.kinds()[0] == ["git", "ls-remote", "origin", "v1.4"]
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_dry_run_regressed_moderate_exits_1_mutates_nothing(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun(npm_doc=_audit(("left-pad", "moderate", "GHSA-mod-1")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "[dry-run] audit verdict: regressed" in out
    assert _snapshot(root) == before
    assert not (root / "report.md").exists()


def test_dry_run_untriaged_high_exits_1_mutates_nothing(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-evil-1")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "[dry-run] pin-bump FAILED: untriaged high/critical" in out
    assert _snapshot(root) == before


def test_dry_run_bad_ref_exits_2_without_subprocess(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump("not-a-ref!", root=root, run=run, dry_run=True)
    assert rc == 2
    assert "bad ref" in capsys.readouterr().err
    assert run.cmds == []


def test_dry_run_missing_submodule_exits_3(tmp_path, capsys):
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=tmp_path, run=run, dry_run=True)
    assert rc == 3
    assert "submodule unreachable" in capsys.readouterr().err
    assert run.cmds == []


def test_dry_run_node_absent_exits_3(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun(node_missing=True)
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True)
    assert rc == 3
    assert "node" in capsys.readouterr().err
    assert _snapshot(root) == before


def test_dry_run_missing_old_audit_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)  # no saved artifact, no --old-audit
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True)
    assert rc == 2
    assert "--old-audit" in capsys.readouterr().err
    assert _snapshot(root) == before


def test_dry_run_skip_audit_exits_0_without_artifact(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)  # no saved artifact needed
    before = _snapshot(root)
    run = FakeRun(node_missing=True)  # audit skipped, node never probed
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, skip_audit=True, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] audit skipped (--skip-audit; no verdict)" in out
    assert f"[dry-run] bump to {PIN_NEW[:7]} would succeed" in out
    assert _snapshot(root) == before
    assert not (root / "report.md").exists()
    assert run.cmds == []


def test_dry_run_open_pr_only_prints(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, open_pr=True, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"[dry-run] would open PR from branch pin-bump-{PIN_NEW[:7]}" in out
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_dry_run_surface_drift_exits_1_mutates_nothing(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, upstream_tools=["renamed_tool"], with_saved_old_audit=True
    )
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True)
    assert rc == 1
    assert "DRIFT" in capsys.readouterr().out
    assert _snapshot(root) == before


def test_dry_run_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--dry-run"])
    assert args.dry_run is True


def test_revendor_preview_matches_pack_counts(tmp_path):
    root = _write_fixture_root(tmp_path)
    preview = pin_bump.revendor_preview(
        root / "redteam" / "grokbot", root / "omega_prime" / "redteam" / "pack"
    )
    assert preview == (2, 0)
    before = _snapshot(root)
    assert (
        pin_bump.revendor_preview(
            root / "redteam" / "grokbot", root / "omega_prime" / "redteam" / "pack"
        )
        == preview
    )
    assert _snapshot(root) == before


# --interactive mode ----------------------------------------------------------


def _tty_stdin(*answers: str):
    """TTY stdin fake answering each prompt with one line."""
    import io

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    return _Tty("".join(a + "\n" for a in answers))


def test_interactive_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--interactive"])
    assert args.interactive is True
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.interactive is False


def test_interactive_accept_all_proceeds(tmp_path, capsys, monkeypatch):
    import sys

    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    monkeypatch.setattr(sys, "stdin", _tty_stdin("y", "y", "y", "y"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), interactive=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("pin-bump confirm") == 4
    assert "ref resolved" in out
    assert "pack re-vendor" in out
    assert "audit verdict" in out
    assert "delta verdict" in out
    assert f"- Commit: `{PIN_NEW}`" in (root / "VENDOR.md").read_text()
    assert "Verdict: improved" in (root / "report.md").read_text()


def test_interactive_decline_at_ref_resolved(tmp_path, capsys, monkeypatch):
    import sys

    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun()
    monkeypatch.setattr(sys, "stdin", _tty_stdin("n"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, interactive=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "declined [ref resolved]" in out
    assert _snapshot(root) == before
    assert run.cmds == []
    assert not (root / "report.md").exists()


def test_interactive_decline_at_pack_revendor(tmp_path, capsys, monkeypatch):
    import sys

    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun()
    monkeypatch.setattr(sys, "stdin", _tty_stdin("y", "n"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, interactive=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "declined [pack re-vendor]" in out
    assert (
        _pack(root) / "skills" / "deep" / "SKILL.md"
    ).read_bytes() == b"# skill v1\n"
    assert _snapshot(root) == before
    assert not (root / "report.md").exists()


def test_interactive_decline_at_audit_verdict(tmp_path, capsys, monkeypatch):
    import sys

    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    monkeypatch.setattr(sys, "stdin", _tty_stdin("y", "y", "n"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), interactive=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "declined [audit verdict]" in out
    assert not (root / "report.md").exists()


def test_interactive_decline_at_delta_verdict(tmp_path, capsys, monkeypatch):
    import sys

    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    monkeypatch.setattr(sys, "stdin", _tty_stdin("y", "y", "y", "n"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), interactive=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "declined [delta verdict]" in out
    assert (root / "report.md").exists()


def test_interactive_non_tty_auto_declines(tmp_path, capsys, monkeypatch):
    import io
    import sys

    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun()
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    assert sys.stdin.isatty() is False
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, interactive=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "non-TTY stdin (auto-decline)" in out
    assert _snapshot(root) == before
    assert not (root / "report.md").exists()


def test_interactive_dry_run_accept_all(tmp_path, capsys, monkeypatch):
    import sys

    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    monkeypatch.setattr(sys, "stdin", _tty_stdin("y", "y", "y", "y"))
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=FakeRun(), dry_run=True, interactive=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("pin-bump confirm") == 4
    assert f"[dry-run] bump to {PIN_NEW[:7]} would succeed" in out
    assert _snapshot(root) == before
    assert not (root / "report.md").exists()


def test_non_interactive_never_prompts(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun())
    out = capsys.readouterr().out
    assert rc == 0
    assert "pin-bump confirm" not in out
    assert "Proceed? [y/n]" not in out


def _lock_audit(specs: dict[str, str], *entries: tuple[str, str, str]) -> dict:
    """Lockfile-shaped audit doc: full package specs plus advisory records.

    ``specs`` maps package name -> locked version; ``entries`` are
    (advisory-key, severity, package) rows attached as lock entry
    records (plus a top-level legacy ``advisories`` map so the triage
    helper accepts the shape). Unlike ``_versioned_audit`` (keyed by
    package, one advisory per package), this shape keeps per-package
    specs for fixed advisories, so ``--semver-check`` can classify them
    ``compatible``/``major`` on an ``improved`` verdict.
    """
    by_package: dict[str, list[dict]] = {}
    top_advisories: dict[str, dict] = {}
    for key, severity, package in entries:
        record = {
            "url": f"https://github.com/advisories/{key}",
            "severity": severity,
            "module_name": package,
        }
        by_package.setdefault(package, []).append(record)
        top_advisories[key] = dict(record)
    packages = {
        f"node_modules/{package}": {
            "version": version,
            "advisories": by_package.get(package, []),
        }
        for package, version in specs.items()
    }
    return {
        "lockfileVersion": 3,
        "packages": packages,
        "advisories": top_advisories,
    }


# --semver-check mode (H11) ---------------------------------------------------


def _versioned_audit(*entries: tuple[str, str, str, str]) -> dict:
    """npm audit doc from (package, severity, advisory-key, spec) rows."""
    vulns = {}
    for package, severity, key, spec in entries:
        vulns[package] = {"severity": severity, "via": [key], "range": spec}
    return {"vulnerabilities": vulns}


def test_semver_check_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--semver-check"])
    assert args.semver_check is True
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.semver_check is False


def test_parse_version_shapes():
    assert pin_bump._parse_version("^1.2.3") == (1, 2, 3)
    assert pin_bump._parse_version("~1.2.3") == (1, 2, 3)
    assert pin_bump._parse_version(">=1.0.0 <2.0.0") == (1, 0, 0)
    assert pin_bump._parse_version("v2.1.0") == (2, 1, 0)
    assert pin_bump._parse_version("1.2") == (1, 2, 0)
    assert pin_bump._parse_version("") is None
    assert pin_bump._parse_version("latest") is None


def test_classify_semver_fixes_labels():
    old_findings = {
        "GHSA-minor": ("high", "left-pad", "1.2.3"),
        "GHSA-major": ("high", "right-pad", "^1.0.0"),
        "GHSA-gone": ("high", "vanished", "3.0.0"),
        "GHSA-mystery": ("high", "weird", "latest"),
    }
    old_packages = {
        "left-pad": "1.2.3",
        "right-pad": "^1.0.0",
        "vanished": "3.0.0",
        "weird": "latest",
    }
    new_packages = {"left-pad": "1.4.0", "right-pad": "2.0.0", "weird": "x"}
    rows = pin_bump.classify_semver_fixes(
        old_findings,
        old_packages,
        new_packages,
        ["GHSA-minor", "GHSA-major", "GHSA-gone", "GHSA-mystery"],
    )
    assert [row[4] for row in rows] == [
        "compatible",
        "major",
        "removed",
        "unknown",
    ]
    section = pin_bump.render_semver_section(rows)
    assert "## Semver check" in section
    assert "semver-compatible" in section
    assert "MAJOR bump" in section
    assert "WARNING" in section


def test_semver_check_compatible_fix_regressed_report(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    run = FakeRun(
        npm_doc=_versioned_audit(("left-pad", "moderate", "GHSA-new-0002", "1.4.0"))
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True)
    out = capsys.readouterr().out
    # The still-present moderate advisory keeps the delta regressed, but the
    # fixed high advisory is classified from the old/new specs.
    assert rc == 1
    report = (root / "report.md").read_text()
    assert "## Semver check" in report
    assert "`GHSA-old-0001` (package `left-pad`): 1.2.3 -> 1.4.0" in report
    assert "semver-compatible" in report
    assert "WARNING" not in report
    assert "semver compatible" in out


def test_semver_check_major_fix_warns(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    run = FakeRun(
        npm_doc=_versioned_audit(("left-pad", "moderate", "GHSA-new-0002", "2.0.0"))
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True)
    out = capsys.readouterr().out
    assert rc == 1
    report = (root / "report.md").read_text()
    assert "MAJOR bump" in report
    assert "WARNING" in report
    assert "WARNING" in out


def test_semver_check_removed_dep_improved(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), semver_check=True)
    out = capsys.readouterr().out
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "Verdict: improved" in report
    assert "dependency removed" in report
    assert "WARNING" not in report
    assert "semver removed" in out


def test_semver_check_off_by_default(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun())
    capsys.readouterr()
    assert rc == 0
    assert "Semver check" not in (root / "report.md").read_text()


def test_semver_check_with_skip_audit_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, skip_audit=True, semver_check=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "--semver-check needs audit data" in err
    assert run.cmds == []


def test_semver_check_dry_run_prints_without_report(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    before = _snapshot(root)
    run = FakeRun(
        npm_doc=_versioned_audit(("left-pad", "moderate", "GHSA-new-0002", "1.4.0"))
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True, semver_check=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "[dry-run] semver compatible" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before


# --bump-commit mode (G9) -------------------------------------------------------


def _bump_commit_old_doc() -> dict:
    """Old audit yielding verdict ``improved`` with one compatible fix.

    ``GHSA-fix-0001`` (high, left-pad, 1.2.3) vanishes while ``left-pad``
    stays in the new tree under the shared drift key, so the delta marks
    the fix ``fixed`` instead of ``removed``. ``GHSA-shared-0002`` drifts
    high -> moderate (downward, triage-green) to carry the new spec.
    """
    return {
        "vulnerabilities": {
            "left-pad": {
                "severity": "high",
                "via": ["GHSA-fix-0001"],
                "range": "1.2.3",
            },
            "right-pad": {
                "severity": "high",
                "via": ["GHSA-shared-0002"],
                "range": "9.9.9",
            },
        }
    }


def _bump_commit_new_doc(new_spec: str) -> dict:
    return {
        "vulnerabilities": {
            "left-pad": {
                "severity": "moderate",
                "via": ["GHSA-shared-0002"],
                "range": new_spec,
            },
        }
    }


def _write_package_json(root: Path, version: str = "1.2.3") -> Path:
    pkg = root / "redteam" / "package.json"
    pkg.write_text(
        json.dumps({"name": "t3mp3st-test", "version": version}) + "\n",
        encoding="utf-8",
    )
    return pkg


def test_bump_commit_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--bump-commit"])
    assert args.bump_commit is True
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.bump_commit is False


def test_bump_package_version_shapes():
    assert pin_bump._bump_package_version("1.2.3", "patch") == "1.2.4"
    assert pin_bump._bump_package_version("1.2.3", "minor") == "1.3.0"
    assert pin_bump._bump_package_version("1.2.3-rc.1", "patch") == "1.2.4-rc.1"
    assert pin_bump._bump_package_version("1.2.3-rc.1", "minor") == "1.3.0-rc.1"
    assert pin_bump._bump_package_version("latest", "patch") is None
    assert pin_bump._bump_package_version("", "minor") is None


def test_bump_level_for_rows_minor_vs_patch():
    minor_rows = [("k", "left-pad", "1.2.3", "1.4.0", "compatible")]
    assert pin_bump._bump_level_for_rows(minor_rows) == "minor"
    patch_rows = [("k", "left-pad", "1.2.3", "1.2.4", "compatible")]
    assert pin_bump._bump_level_for_rows(patch_rows) == "patch"
    removed_rows = [("k", "left-pad", "1.2.3", "", "removed")]
    assert pin_bump._bump_level_for_rows(removed_rows) == "patch"


def test_bump_commit_gate_shapes():
    rows = [("k", "left-pad", "1.2.3", "1.4.0", "compatible")]
    assert (
        pin_bump._bump_commit_gate(semver_check=True, verdict="improved", rows=rows)
        is None
    )
    no_semver = pin_bump._bump_commit_gate(
        semver_check=False, verdict="improved", rows=rows
    )
    assert no_semver is not None and "requires --semver-check" in no_semver
    regressed = pin_bump._bump_commit_gate(
        semver_check=True, verdict="regressed", rows=rows
    )
    assert regressed is not None and "not 'improved'" in regressed
    no_rows = pin_bump._bump_commit_gate(semver_check=True, verdict="improved", rows=[])
    assert no_rows is not None and "no fixed advisories" in no_rows
    major_rows = [("k", "left-pad", "1.2.3", "2.0.0", "major")]
    major = pin_bump._bump_commit_gate(
        semver_check=True, verdict="improved", rows=major_rows
    )
    assert major is not None and "major" in major
    unknown_rows = [("k", "left-pad", "latest", "1.4.0", "unknown")]
    unknown = pin_bump._bump_commit_gate(
        semver_check=True, verdict="improved", rows=unknown_rows
    )
    assert unknown is not None and "unknown" in unknown
    removed_rows = [("k", "left-pad", "1.2.3", "", "removed")]
    removed = pin_bump._bump_commit_gate(
        semver_check=True, verdict="improved", rows=removed_rows
    )
    assert removed is not None and "no compatible fixes" in removed


def test_bump_commit_improved_compatible_bumps_minor(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True, bump_commit=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Verdict: improved" in (root / "report.md").read_text()
    assert json.loads(pkg.read_text())["version"] == "1.3.0"
    assert "pin-bump version bump (minor): 1.2.3 -> 1.3.0" in out


def test_bump_commit_patch_fix_bumps_patch(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.2.4"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True, bump_commit=True)
    capsys.readouterr()
    assert rc == 0
    assert json.loads(pkg.read_text())["version"] == "1.2.4"


def test_bump_commit_major_fix_skips_with_warning(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("2.0.0"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True, bump_commit=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Verdict: improved" in (root / "report.md").read_text()
    assert json.loads(pkg.read_text())["version"] == "1.2.3"
    assert "WARNING: --bump-commit skipped" in out
    assert "major" in out


def test_bump_commit_missing_package_json_skips(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True, bump_commit=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Verdict: improved" in (root / "report.md").read_text()
    assert "WARNING: --bump-commit skipped" in out
    assert "absent" in out


def test_bump_commit_malformed_package_json_skips(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    (root / "redteam" / "package.json").write_text("{not json", encoding="utf-8")
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True, bump_commit=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARNING: --bump-commit skipped" in out


def test_bump_commit_without_semver_check_skips(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, bump_commit=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(pkg.read_text())["version"] == "1.2.3"
    assert "WARNING: --bump-commit skipped" in out
    assert "--semver-check" in out


def test_bump_commit_off_by_default(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, semver_check=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(pkg.read_text())["version"] == "1.2.3"
    assert "version bump" not in out


def test_bump_commit_dry_run_prints_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    before = _snapshot(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, dry_run=True, semver_check=True, bump_commit=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] would bump version 1.2.3 -> 1.3.0" in out
    assert "no files changed" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert json.loads(pkg.read_text())["version"] == "1.2.3"


def test_bump_commit_dry_run_major_skips(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    _write_package_json(root)
    before = _snapshot(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("2.0.0"))
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, dry_run=True, semver_check=True, bump_commit=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] pin-bump WARNING: --bump-commit skipped" in out
    assert _snapshot(root) == before


def test_bump_commit_open_pr_notes_version_in_body(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    _write_package_json(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        semver_check=True,
        bump_commit=True,
        open_pr=True,
    )
    capsys.readouterr()
    assert rc == 0
    commits = [cmd for cmd in run.kinds() if cmd[:2] == ["git", "commit"]]
    assert len(commits) == 1
    assert "Version bump: 1.2.3 -> 1.3.0 (redteam/package.json)." in commits[0][-1]


# --audit-only mode (I9) --------------------------------------------------------


def test_audit_only_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--audit-only"])
    assert args.audit_only is True
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.audit_only is False


def test_audit_only_improved_prints_report_and_mutates_nothing(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[audit-only] audit verdict: improved" in out
    assert "Verdict: improved" in out
    assert "no files changed" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_audit_only_regressed_exits_1_mutates_nothing(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    before = _snapshot(root)
    run = FakeRun(npm_doc=_audit(("left-pad", "moderate", "GHSA-new-0002")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "[audit-only] audit verdict: regressed" in out
    assert "Verdict: regressed" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_audit_only_untriaged_high_exits_1_mutates_nothing(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(),
    )
    before = _snapshot(root)
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "GHSA-new-0009" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_audit_only_semver_check_prints_classification(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    before = _snapshot(root)
    run = FakeRun(
        npm_doc=_versioned_audit(("left-pad", "moderate", "GHSA-new-0002", "1.4.0"))
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, semver_check=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "[audit-only] semver compatible" in out
    assert "## Semver check" in out
    assert "`GHSA-old-0001` (package `left-pad`): 1.2.3 -> 1.4.0" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before


def test_audit_only_bump_commit_suppressed_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    pkg = _write_package_json(root)
    before = _snapshot(root)
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        audit_only=True,
        semver_check=True,
        bump_commit=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[audit-only] would bump version 1.2.3 -> 1.3.0" in out
    assert "no files changed" in out
    assert json.loads(pkg.read_text())["version"] == "1.2.3"
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_audit_only_open_pr_exits_2_without_subprocess(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, open_pr=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "--open-pr" in err
    assert run.cmds == []


def test_audit_only_skip_audit_exits_2_without_subprocess(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, skip_audit=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "--skip-audit" in err
    assert run.cmds == []


def test_audit_only_missing_old_audit_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)  # no saved artifact, no --old-audit
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "--old-audit" in err
    assert _snapshot(root) == before


def test_audit_only_dry_run_composes_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[audit-only] audit verdict: improved" in out
    assert "Verdict: improved" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_audit_only_node_absent_exits_3(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun(node_missing=True)
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True)
    err = capsys.readouterr().err
    assert rc == 3
    assert "node" in err
    assert _snapshot(root) == before


# --pin targeted mode (K9) -------------------------------------------------------


def test_pin_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--pin", "left-pad"])
    assert args.pin == ["left-pad"]
    repeated = pin_bump.build_parser().parse_args(
        ["--to", PIN_NEW, "--pin", "left-pad", "--pin", "GHSA-old-0001"]
    )
    assert repeated.pin == ["left-pad", "GHSA-old-0001"]
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.pin is None


def test_pin_match_shapes():
    assert pin_bump._pin_matches("GHSA-old-0001", "GHSA-old-0001", "left-pad")
    assert pin_bump._pin_matches("left", "GHSA-old-0001", "left-pad")
    assert pin_bump._pin_matches("PAD", "GHSA-old-0001", "left-pad")
    assert pin_bump._pin_matches("ghsa-old", "GHSA-old-0001", "left-pad")
    assert not pin_bump._pin_matches("evil", "GHSA-old-0001", "left-pad")
    assert not pin_bump._pin_matches("left-pad", "GHSA-new-0009", "evil")


def test_pin_scoped_improved_despite_out_of_scope_new(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["left-pad"])
    out = capsys.readouterr().out
    # Unpinned this delta is regressed (a new high appears); pinned to
    # left-pad the fixed advisory carries the verdict instead.
    assert rc == 0
    assert "out of scope for --pin" in out
    report = (root / "report.md").read_text()
    assert "Verdict: improved" in report
    assert "Out of scope for --pin" in report
    assert "GHSA-new-0009" in report
    assert "GHSA-old-0001" in report
    # The file path still ran whole-tree so provenance is green.
    assert f"- Commit: `{PIN_NEW}`" in (root / "VENDOR.md").read_text()
    assert (
        _pack(root) / "skills" / "deep" / "SKILL.md"
    ).read_bytes() == b"# skill v2\n"


def test_pin_match_by_advisory_id(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["GHSA-old-0001"])
    capsys.readouterr()
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "Verdict: improved" in report
    assert "Out of scope for --pin" in report


def test_pin_in_scope_untriaged_still_blocks(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, with_saved_old_audit=True, old_audit_doc=_audit()
    )
    run = FakeRun(npm_doc=_audit(("left-pad", "high", "GHSA-new-0001")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["left-pad"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "GHSA-new-0001" in out


def test_pin_in_scope_new_moderate_regresses(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, with_saved_old_audit=True, old_audit_doc=_audit()
    )
    run = FakeRun(npm_doc=_audit(("left-pad", "moderate", "GHSA-new-0002")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["left-pad"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "regressed" in out
    assert "Verdict: regressed" in (root / "report.md").read_text()


def test_pin_no_match_exits_2_listing_found(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["no-such-pkg"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--pin matched no advisories" in err
    assert "GHSA-old-0001" in err
    assert "left-pad" in err


def test_pin_no_match_both_clean_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["left-pad"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--pin matched no advisories" in err


def test_pin_repeatable_union_covers_both(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(
            ("left-pad", "high", "GHSA-old-0001"),
            ("wibble", "high", "GHSA-old-0002"),
        ),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["left-pad", "wibble"])
    capsys.readouterr()
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "Verdict: improved" in report
    assert "Out of scope for --pin" in report
    assert "0 other advisories" in report


def test_pin_single_leaves_other_fixed_out_of_scope(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(
            ("left-pad", "high", "GHSA-old-0001"),
            ("wibble", "high", "GHSA-old-0002"),
        ),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin=["left-pad"])
    capsys.readouterr()
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "Verdict: improved" in report
    assert "Out of scope for --pin" in report
    assert "GHSA-old-0002" in report


def test_pin_skip_audit_exits_2_without_subprocess(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, skip_audit=True, pin=["left-pad"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--pin" in err
    assert run.cmds == []


def test_pin_audit_only_scoped_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    before = _snapshot(root)
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, pin=["left-pad"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[audit-only] audit verdict: improved" in out
    assert "out of scope for --pin" in out
    assert "GHSA-new-0009" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_pin_dry_run_scoped_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    before = _snapshot(root)
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, dry_run=True, pin=["left-pad"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] audit verdict: improved" in out
    assert "out of scope for --pin" in out
    assert "GHSA-new-0009" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_pin_json_mode_pinned_keys(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun(npm_doc=_audit(("evil", "high", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, json_mode=True, pin=["left-pad"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["verdict"] == "improved"
    assert payload["pinned"] == ["left-pad"]
    assert payload["out_of_scope"] == [
        {"key": "GHSA-new-0009", "package": "evil", "status": "new"}
    ]
    assert [row["key"] for row in payload["rows"]] == ["GHSA-old-0001"]
    assert "out of scope for --pin" in captured.err
    assert "Verdict: improved" in (root / "report.md").read_text()


def test_pin_semver_check_scoped_to_fixed(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(
            ("left-pad", "high", "GHSA-old-0001", "1.2.3"),
            ("wibble", "high", "GHSA-old-0002", "2.0.0"),
        ),
    )
    run = FakeRun(
        npm_doc=_versioned_audit(
            ("left-pad", "moderate", "GHSA-new-0003", "1.4.0"),
            ("wibble", "moderate", "GHSA-new-0004", "2.1.0"),
        )
    )
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, semver_check=True, pin=["GHSA-old-0001"]
    )
    out = capsys.readouterr().out
    # Unpinned this delta is regressed (two new moderate advisories); pinned
    # to the one compatible fix, the verdict is driven by that key alone.
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "Verdict: improved" in report
    assert "Out of scope for --pin" in report
    assert "`GHSA-old-0001` (package `left-pad`): 1.2.3 -> 1.4.0" in report
    assert "GHSA-old-0002" in report
    assert "semver compatible" in out


def test_unpinned_regressed_unchanged_by_pin_feature(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun(npm_doc=_audit(("evil", "moderate", "GHSA-new-0009")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run)
    capsys.readouterr()
    assert rc == 1
    report = (root / "report.md").read_text()
    assert "Verdict: regressed" in report
    assert "Out of scope for --pin" not in report


def test_pin_main_plumbing(tmp_path, capsys, monkeypatch):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    monkeypatch.setattr(pin_bump, "_default_run", run)
    rc = pin_bump.main(
        [
            "--to",
            PIN_NEW,
            "--root",
            str(root),
            "--pin",
            "left-pad",
            "--audit-only",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[audit-only] audit verdict: improved" in out


# --schedule mode (L9) ----------------------------------------------------------


def test_schedule_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert args.schedule is None
    assert args.schedule_name == "pin-bump"
    args = pin_bump.build_parser().parse_args(
        ["--to", PIN_NEW, "--schedule", "0 6 * * *", "--schedule-name", "nightly"]
    )
    assert args.schedule == "0 6 * * *"
    assert args.schedule_name == "nightly"


def test_schedule_invalid_cron_exits_2_without_registering(tmp_path, capsys):
    for bad in [
        "not-a-cron",
        "0 6 * *",  # four fields
        "* * * * * *",  # six fields
        "61 * * * *",  # minute out of range
        "0 24 * * *",  # hour out of range
        "0 6 0 * *",  # day-of-month out of range
        "0 6 * 13 *",  # month out of range
        "0 6 * * 8",  # day-of-week out of range
        "noon * * * *",  # names rejected
        "@daily",  # shorthands rejected
        "",
    ]:
        run = FakeRun()
        rc = pin_bump.bump(
            PIN_NEW, root=tmp_path, run=run, schedule=bad, schedule_name="nightly"
        )
        err = capsys.readouterr().err
        assert rc == 2, bad
        assert "--schedule" in err, bad
        assert run.cmds == [], bad
    assert not (tmp_path / "cron" / "jobs.json").exists()


def test_schedule_empty_name_exits_2_without_registering(tmp_path, capsys):
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW, root=tmp_path, run=run, schedule="0 6 * * *", schedule_name="  "
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "--schedule-name" in err
    assert run.cmds == []
    assert not (tmp_path / "cron" / "jobs.json").exists()


def test_schedule_registers_job_with_argv_echo(tmp_path, capsys):
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW,
        root=tmp_path,
        run=run,
        semver_check=True,
        pin=["left-pad"],
        schedule="0 6 * * *",
        schedule_name="nightly",
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert run.cmds == []
    assert "'nightly' (0 6 * * *)" in out
    store = sched_mod.JobStore(tmp_path / "cron" / "jobs.json")
    jobs = sched_mod.list_pin_bump_schedules(store)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["kind"] == sched_mod.PIN_BUMP_SCHEDULE_KIND
    assert job["schedule_name"] == "nightly"
    assert job["cron_expr"] == "0 6 * * *"
    assert job["last_verdict"] is None
    assert job["last_run"] is None
    config = job["pin_bump_config"]
    assert config["to"] == PIN_NEW
    assert config["semver_check"] is True
    assert config["pin"] == ["left-pad"]


def test_schedule_duplicate_name_replaces(tmp_path, capsys):
    run = FakeRun()
    first = pin_bump.bump(
        PIN_NEW, root=tmp_path, run=run, schedule="0 6 * * *", schedule_name="nightly"
    )
    capsys.readouterr()
    second = pin_bump.bump(
        PIN_NEW, root=tmp_path, run=run, schedule="30 7 * * *", schedule_name="nightly"
    )
    capsys.readouterr()
    assert first == 0 and second == 0
    store = sched_mod.JobStore(tmp_path / "cron" / "jobs.json")
    jobs = sched_mod.list_pin_bump_schedules(store)
    assert len(jobs) == 1
    assert jobs[0]["cron_expr"] == "30 7 * * *"


def test_schedule_distinct_names_coexist(tmp_path, capsys):
    run = FakeRun()
    pin_bump.bump(
        PIN_NEW, root=tmp_path, run=run, schedule="0 6 * * *", schedule_name="a"
    )
    capsys.readouterr()
    pin_bump.bump(
        PIN_NEW, root=tmp_path, run=run, schedule="0 6 * * *", schedule_name="b"
    )
    capsys.readouterr()
    store = sched_mod.JobStore(tmp_path / "cron" / "jobs.json")
    assert len(sched_mod.list_pin_bump_schedules(store)) == 2


def test_schedule_main_plumbing_default_name(tmp_path, capsys):
    rc = pin_bump.main(
        ["--to", PIN_NEW, "--root", str(tmp_path), "--schedule", "*/15 * * * *"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "'pin-bump' (*/15 * * * *)" in out
    store = sched_mod.JobStore(tmp_path / "cron" / "jobs.json")
    jobs = sched_mod.list_pin_bump_schedules(store)
    assert [job["schedule_name"] for job in jobs] == ["pin-bump"]


def test_validate_pin_bump_cron_shapes():
    assert sched_mod.validate_pin_bump_cron("0 6 * * *") == "0 6 * * *"
    assert sched_mod.validate_pin_bump_cron("  */15  9-17  *  *  1-5  ") == (
        "*/15 9-17 * * 1-5"
    )
    assert sched_mod.validate_pin_bump_cron("0 0 * * 7") == "0 0 * * 7"
    assert sched_mod.validate_pin_bump_cron("5/15 0 * * *") == "5/15 0 * * *"
    assert sched_mod.validate_pin_bump_cron("0,30 6,18 1,15 * *") == (
        "0,30 6,18 1,15 * *"
    )
    for bad in [
        "",
        "* * * *",
        "* * * * * *",
        "*/0 * * * *",
        "/5 * * * *",
        "5-2 * * * *",
        "mon * * * *",
        "@hourly",
        "1.5 * * * *",
    ]:
        with pytest.raises(ValueError):
            sched_mod.validate_pin_bump_cron(bad)


def test_pin_bump_next_due_shapes():
    # Every-minute cadence fires at the next minute boundary, strictly after.
    assert sched_mod.pin_bump_next_due("* * * * *", 1000.0) == 1020.0
    assert sched_mod.pin_bump_next_due("* * * * *", 1020.0) == 1080.0
    # Daily 06:00 UTC lands on 06:00, and an exact hit rolls to tomorrow.
    day = 1700006400.0  # 2023-11-15 00:00:00 UTC
    six_am = day + 6 * 3600
    assert sched_mod.pin_bump_next_due("0 6 * * *", day) == six_am
    assert sched_mod.pin_bump_next_due("0 6 * * *", six_am) == six_am + 86400
    # Feb 30 never fires: fail-closed instead of a silent dead schedule.
    with pytest.raises(ValueError):
        sched_mod.pin_bump_next_due("0 0 30 2 *", day)
    with pytest.raises(ValueError):
        sched_mod.pin_bump_next_due("bogus", day)


def _bump_recorder(
    calls: list[object], *, record: str | None = None
) -> Callable[[dict], int]:
    """Tick bump seam: record the job (or one field) and return exit 0."""

    def _bump(job: dict) -> int:
        calls.append(job[record] if record else job)
        return 0

    return _bump


def _due_store(tmp_path, config, now=1000.0, cron="* * * * *", name="nightly"):
    store = sched_mod.JobStore(tmp_path / "cron" / "jobs.json")
    sched_mod.schedule_pin_bump(
        store, cron_expr=cron, name=name, config=config, now=now
    )
    return store


def test_tick_improved_compatible_opens_bump_path(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW, "semver_check": True})
    calls: list[object] = []
    ran = sched_mod.tick_pin_bump_schedules(
        store,
        1020.0,
        audit=lambda job: {"verdict": "improved", "semver_compatible": True},
        bump=_bump_recorder(calls, record="schedule_name"),
    )
    assert len(ran) == 1
    assert calls == ["nightly"]
    job = ran[0]
    assert job["last_verdict"] == "improved"
    assert job["last_run"] == 1020.0
    assert job["last_result"]["action"] == "bump-pr"
    assert job["pin_bump_digest"][-1]["action"] == "bump-pr"
    assert job["due_at"] == 1080.0


def test_tick_regressed_records_digest_and_skips_bump(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW})
    calls: list[object] = []
    ran = sched_mod.tick_pin_bump_schedules(
        store,
        1020.0,
        audit=lambda job: {"verdict": "regressed"},
        bump=_bump_recorder(calls),
    )
    assert len(ran) == 1
    assert calls == []
    job = ran[0]
    assert job["last_verdict"] == "regressed"
    assert job["last_run"] == 1020.0
    assert job["last_result"] == {
        "verdict": "regressed",
        "action": "none",
        "detail": None,
    }
    assert job["due_at"] == 1080.0


def test_tick_improved_major_with_semver_check_skips_bump(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW, "semver_check": True})
    calls: list[object] = []
    ran = sched_mod.tick_pin_bump_schedules(
        store,
        1020.0,
        audit=lambda job: {"verdict": "improved", "semver_compatible": False},
        bump=_bump_recorder(calls),
    )
    assert len(ran) == 1
    assert calls == []
    assert ran[0]["last_result"]["action"] == "none"
    assert ran[0]["pin_bump_digest"][-1]["semver_compatible"] is False


def test_tick_improved_without_semver_check_opens_bump_path(tmp_path):
    # No --semver-check in the stored config: an improved verdict alone
    # opens the PR path even when the seam reports incompatible fixes.
    store = _due_store(tmp_path, {"to": PIN_NEW, "semver_check": False})
    calls: list[object] = []
    ran = sched_mod.tick_pin_bump_schedules(
        store,
        1020.0,
        audit=lambda job: {"verdict": "improved", "semver_compatible": False},
        bump=_bump_recorder(calls),
    )
    assert len(ran) == 1
    assert len(calls) == 1
    assert ran[0]["last_result"]["action"] == "bump-pr"


def test_tick_audit_failure_fail_closed(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW})
    calls: list[object] = []

    def _boom(job):
        raise RuntimeError("audit exploded")

    ran = sched_mod.tick_pin_bump_schedules(
        store, 1020.0, audit=_boom, bump=_bump_recorder(calls)
    )
    assert len(ran) == 1
    assert calls == []
    assert ran[0]["last_verdict"] == "error"
    assert ran[0]["last_result"]["action"] == "none"


def test_tick_bump_failure_recorded_not_raised(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW})

    def _boom(job):
        raise RuntimeError("pr exploded")

    ran = sched_mod.tick_pin_bump_schedules(
        store,
        1020.0,
        audit=lambda job: {"verdict": "improved"},
        bump=_boom,
    )
    assert len(ran) == 1
    assert ran[0]["last_result"]["action"] == "bump-pr-failed"
    assert "pr exploded" in str(ran[0]["last_result"]["detail"])


def test_tick_not_due_skips(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW}, now=1000.0)
    calls: list[object] = []
    ran = sched_mod.tick_pin_bump_schedules(
        store,
        1000.0,  # before the 1020.0 due time
        audit=lambda job: {"verdict": "improved"},
        bump=_bump_recorder(calls),
    )
    assert ran == []
    assert calls == []
    # A second tick at the same stamp never double-runs.
    ran = sched_mod.tick_pin_bump_schedules(
        store,
        1020.0,
        audit=lambda job: {"verdict": "improved"},
        bump=_bump_recorder(calls),
    )
    assert len(ran) == 1
    reran = sched_mod.tick_pin_bump_schedules(
        store,
        1020.0,
        audit=lambda job: {"verdict": "improved"},
        bump=_bump_recorder(calls),
    )
    assert reran == []
    assert len(calls) == 1


def test_tick_leaves_other_kinds_alone(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW})
    due = sched_mod.pin_bump_next_due("* * * * *", 1000.0)
    other_id = store.schedule("plain prompt", due)
    ran = sched_mod.tick_pin_bump_schedules(
        store,
        due,
        audit=lambda job: {"verdict": "improved"},
        bump=lambda job: 0,
    )
    assert [job["schedule_name"] for job in ran] == ["nightly"]
    other = next(job for job in store.jobs if job["id"] == other_id)
    assert other["last_ran_at"] is None


def test_generic_tick_skips_pin_bump_schedules(tmp_path):
    store = _due_store(tmp_path, {"to": PIN_NEW})
    calls: list[object] = []
    ran = store.tick(
        1020.0, calls.append, skip_kinds=(sched_mod.PIN_BUMP_SCHEDULE_KIND,)
    )
    assert ran == []
    assert calls == []
    job = sched_mod.list_pin_bump_schedules(store)[0]
    assert job.get("completed") is not True
    assert job["last_ran_at"] is None
    # The legacy single-kind spelling keeps working alongside the new one.
    ran = store.tick(1020.0, calls.append, skip_kind="desk_lead_pass")
    assert [j["schedule_name"] for j in ran] == ["nightly"]


def test_audit_scheduled_job_improved_compatible(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(
            ("left-pad", "high", "GHSA-old-0001", "1.2.3"),
            ("wibble", "high", "GHSA-old-0002", "2.0.0"),
        ),
    )
    run = FakeRun(
        npm_doc=_versioned_audit(
            ("left-pad", "moderate", "GHSA-new-0003", "1.4.0"),
            ("wibble", "moderate", "GHSA-new-0004", "2.1.0"),
        )
    )
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = pin_bump.audit_scheduled_job(
        {
            "to": PIN_NEW,
            "semver_check": True,
            "pin": ["GHSA-old-0001"],
        },
        root=root,
        run=run,
    )
    capsys.readouterr()
    assert result["verdict"] == "improved"
    assert result["semver_compatible"] is True
    assert result["exit_code"] == 0
    # Audit-only semantics: no file under the repo root changed.
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_audit_scheduled_job_major_not_compatible(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(
            ("left-pad", "high", "GHSA-old-0001", "1.2.3"),
        ),
    )
    run = FakeRun(
        npm_doc=_versioned_audit(
            ("left-pad", "low", "GHSA-new-0003", "2.0.0"),
        )
    )
    result = pin_bump.audit_scheduled_job(
        {"to": PIN_NEW, "semver_check": True, "pin": ["GHSA-old-0001"]},
        root=root,
        run=run,
    )
    capsys.readouterr()
    assert result["verdict"] == "improved"
    assert result["semver_compatible"] is False


def test_audit_scheduled_job_broken_config_fail_closed(tmp_path):
    assert pin_bump.audit_scheduled_job({}, root=tmp_path)["verdict"] == "error"
    assert pin_bump.audit_scheduled_job(None, root=tmp_path)["verdict"] == "error"


def test_apply_scheduled_bump_forces_open_pr(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    # Stored config never asked for --open-pr; the scheduled apply opens
    # the normal bump PR path anyway.
    rc = pin_bump.apply_scheduled_bump({"to": PIN_NEW}, root=root, run=run)
    out = capsys.readouterr().out
    assert rc == 0
    verbs = [cmd for cmd, _ in run.cmds]
    assert ["git", "checkout", "-b", f"pin-bump-{PIN_NEW[:7]}"] in verbs
    assert any(cmd[:2] == ["gh", "pr"] for cmd in verbs)
    assert "https://example.test/pr/1" in out


# --rollback mode (M9) ----------------------------------------------------------


def _write_rollback_root(
    tmp_path: Path, *, old: str = PIN_OLD, new: str = PIN_NEW
) -> Path:
    """A root as if a bump ``old -> new`` just landed: pins at ``new`` and
    a delta report whose ``Old:`` label names the saved pre-bump audit
    (the record a rollback reads)."""
    root = _write_fixture_root(tmp_path, vendor_pin=new)
    (root / "report.md").write_text(
        "# redteam audit delta\n"
        "\n"
        f"Old: `omega_prime/tests/data/redteam-audit-{old}.json`\n"
        "New: `new-pin npm audit`\n"
        "\n"
        "## Advisories new\n"
        "\n"
        "_None._\n"
        "\n"
        "Verdict: improved\n",
        encoding="utf-8",
    )
    return root


def _roe_artifact_doc(*, until: str) -> dict:
    """Minimal schema-valid RoE document with the window ending at ``until``."""
    return {
        "schema_version": 1,
        "operator": "tester",
        "authorized_targets": [{"host": "example.test", "tools": ["tempest_status"]}],
        "effective_from": "2020-01-01T00:00:00Z",
        "effective_until": until,
        "case_classes": ["jailbreak"],
    }


def _write_roe_artifact(tmp_path: Path, name: str, *, until: str) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(_roe_artifact_doc(until=until)), encoding="utf-8")
    return path


def test_rollback_flag_parses():
    args = pin_bump.build_parser().parse_args(["--rollback"])
    assert args.rollback is True
    assert args.to is None
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.rollback is False
    assert default.to == PIN_NEW


def test_rollback_with_to_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        pin_bump.main(["--rollback", "--to", PIN_NEW, "--root", str(tmp_path)])
    assert exc.value.code == 2
    assert "drop --to" in capsys.readouterr().err


def test_rollback_main_still_requires_to(tmp_path):
    # Non-rollback runs keep the required---to behavior (SystemExit 2).
    with pytest.raises(SystemExit) as exc:
        pin_bump.main(["--root", str(tmp_path)])
    assert exc.value.code == 2


def test_rollback_no_record_exits_2_lists_locations(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)  # no report.md, empty git log
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(None, root=root, run=run, rollback=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "no prior bump record" in err
    assert "report.md" in err
    assert "VENDOR.md" in err
    assert ["git", "log", "--format=%H", "-n", "1", "--", "VENDOR.md"] in run.kinds()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_rollback_happy_path_report_record(tmp_path, capsys):
    root = _write_rollback_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(None, root=root, run=run, rollback=True)
    out = capsys.readouterr().out
    assert rc == 0
    verbs = [cmd for cmd, _ in run.cmds]
    # The old lockfile entry is restored via the submodule move.
    assert ["git", "fetch", "origin"] in verbs
    assert ["git", "checkout", PIN_OLD] in verbs
    assert ["git", "add", "redteam"] in verbs
    # Pin records point at the previous version again.
    assert f"- Commit: `{PIN_OLD}`" in (root / "VENDOR.md").read_text()
    init_text = (root / "omega_prime" / "redteam" / "__init__.py").read_text()
    assert f"Upstream commit: {PIN_OLD}" in init_text
    assert PIN_NEW not in (root / "VENDOR.md").read_text()
    # The vendored pack mirrors the restored pin; the bump record survives.
    assert (
        _pack(root).joinpath("skills", "deep", "SKILL.md").read_bytes()
        == b"# skill v2\n"
    )
    assert f"redteam-audit-{PIN_OLD}.json" in (root / "report.md").read_text()
    # A revert PR opens on its own branch.
    branch = f"pin-rollback-{PIN_OLD[:7]}"
    assert ["git", "checkout", "-b", branch] in verbs
    assert [
        "git",
        "add",
        "redteam",
        "VENDOR.md",
        "omega_prime/redteam/__init__.py",
        "omega_prime/redteam/pack",
    ] in verbs
    pr = next(cmd for cmd in verbs if cmd[:2] == ["gh", "pr"])
    title = pr[pr.index("--title") + 1]
    body = pr[pr.index("--body") + 1]
    assert PIN_OLD[:7] in title
    assert "Revert" in body
    assert PIN_NEW[:7] in body and PIN_OLD[:7] in body
    assert "Old audit label" in body  # the revert cites its source record
    assert "https://example.test/pr/1" in out
    assert f"rollback to {PIN_OLD[:7]} complete" in out


def test_rollback_history_fallback_without_report(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, vendor_pin=PIN_NEW)  # no report.md
    prior_vendor = (
        "# Vendored behavior\n\n"
        "## T3MP3ST red team pack\n\n"
        "- Remote: http://127.0.0.1/purple-team.git\n"
        f"- Commit: `{PIN_OLD}`\n"
    )
    run = FakeRun(git_log_vendor=f"{'e' * 40}\n", git_show_vendor=prior_vendor)
    rc = pin_bump.bump(None, root=root, run=run, rollback=True)
    capsys.readouterr()
    assert rc == 0
    assert f"- Commit: `{PIN_OLD}`" in (root / "VENDOR.md").read_text()
    assert ["git", "checkout", PIN_OLD] in run.kinds()


def test_rollback_already_current_exits_2(tmp_path, capsys):
    root = _write_rollback_root(tmp_path, old=PIN_OLD, new=PIN_OLD)
    before = _snapshot(root)
    rc = pin_bump.bump(None, root=root, run=FakeRun(), rollback=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "nothing to roll back" in err
    assert _snapshot(root) == before


def test_rollback_restores_package_json_version(tmp_path, capsys):
    root = _write_rollback_root(tmp_path)
    pkg = root / "redteam" / "package.json"
    pkg.write_text('{"name": "redteam", "version": "1.3.0"}\n', encoding="utf-8")
    run = FakeRun(
        git_show_package='{"name": "redteam", "version": "1.2.3"}\n',
    )
    rc = pin_bump.bump(None, root=root, run=run, rollback=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(pkg.read_text())["version"] == "1.2.3"
    assert "version restore" in out
    assert "1.3.0 -> 1.2.3" in out
    verbs = [cmd for cmd, _ in run.cmds]
    pr = next(cmd for cmd in verbs if cmd[:2] == ["gh", "pr"])
    assert "Version restore: 1.3.0 -> 1.2.3" in pr[pr.index("--body") + 1]


def test_rollback_package_json_already_matches(tmp_path, capsys):
    root = _write_rollback_root(tmp_path)
    pkg = root / "redteam" / "package.json"
    pkg.write_text('{"name": "redteam", "version": "1.2.3"}\n', encoding="utf-8")
    run = FakeRun(
        git_show_package='{"name": "redteam", "version": "1.2.3"}\n',
    )
    rc = pin_bump.bump(None, root=root, run=run, rollback=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(pkg.read_text())["version"] == "1.2.3"
    assert "version restore" not in out


def test_rollback_dry_run_plan_mutates_nothing(tmp_path, capsys):
    root = _write_rollback_root(tmp_path)
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(None, root=root, run=run, rollback=True, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"[dry-run] would roll back pin {PIN_NEW[:7]} -> {PIN_OLD[:7]}" in out
    assert "[dry-run] would re-vendor pack: copy 2, remove 0" in out
    assert f"[dry-run] would update VENDOR.md pin {PIN_NEW[:7]} ->" in out
    assert "[dry-run] would update omega_prime/redteam/__init__.py" in out
    assert (
        "[dry-run] would open revert PR from branch "
        f"pin-rollback-{PIN_OLD[:7]} (skipped)" in out
    )
    assert f"[dry-run] rollback to {PIN_OLD[:7]} would succeed" in out
    assert _snapshot(root) == before
    assert not any(cmd[:2] == ["gh", "pr"] for cmd in run.kinds())
    assert _mutating_verbs(run.kinds()) == []


def test_rollback_dry_run_no_record(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    before = _snapshot(root)
    rc = pin_bump.bump(None, root=root, run=FakeRun(), rollback=True, dry_run=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "no prior bump record" in err
    assert _snapshot(root) == before


def test_rollback_roe_expired_blocks_before_lookup(tmp_path, capsys):
    # The gate runs before the bump-record lookup: this root has no
    # report.md, so a record-first flow would exit 2, not 1.
    root = _write_fixture_root(tmp_path)
    artifact = _write_roe_artifact(tmp_path, "roe.json", until="2020-06-01T00:00:00Z")
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(
        None,
        root=root,
        run=run,
        rollback=True,
        job={"context": {"roe_artifact": str(artifact)}},
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "rollback blocked" in out
    assert "expired" in out
    assert _snapshot(root) == before
    assert run.cmds == []


def test_rollback_roe_revoked_blocks(tmp_path, capsys):
    from omega_prime.redteam import roe_audit

    root = _write_rollback_root(tmp_path)
    artifact = _write_roe_artifact(tmp_path, "roe.json", until="2099-01-01T00:00:00Z")
    log_path = tmp_path / "roe-audit.jsonl"
    assert roe_audit.log_revoked(log_path, str(artifact), "tester") is not None
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(
        None,
        root=root,
        run=run,
        rollback=True,
        job={
            "context": {
                "roe_artifact": str(artifact),
                "roe_audit_log": str(log_path),
            }
        },
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "rollback blocked" in out
    assert "revoked" in out
    assert _snapshot(root) == before
    assert run.cmds == []


def test_rollback_roe_valid_proceeds(tmp_path, capsys):
    root = _write_rollback_root(tmp_path)
    artifact = _write_roe_artifact(tmp_path, "roe.json", until="2099-01-01T00:00:00Z")
    run = FakeRun()
    rc = pin_bump.bump(
        None,
        root=root,
        run=run,
        rollback=True,
        job={"context": {"roe_artifact": str(artifact)}},
    )
    capsys.readouterr()
    assert rc == 0
    assert f"- Commit: `{PIN_OLD}`" in (root / "VENDOR.md").read_text()


def test_rollback_roe_job_level_and_dict_forms(tmp_path, capsys):
    first = _write_rollback_root(tmp_path / "first")
    artifact = _write_roe_artifact(tmp_path, "roe.json", until="2099-01-01T00:00:00Z")
    rc = pin_bump.bump(
        None,
        root=first,
        run=FakeRun(),
        rollback=True,
        job={"roe_artifact": str(artifact)},
    )
    capsys.readouterr()
    assert rc == 0
    second = _write_rollback_root(tmp_path / "second")
    inline = _roe_artifact_doc(until="2099-01-01T00:00:00Z")
    inline["artifact_id"] = "inline-grant"
    rc = pin_bump.bump(
        None,
        root=second,
        run=FakeRun(),
        rollback=True,
        job={"context": {"roe_artifact": inline}},
    )
    capsys.readouterr()
    assert rc == 0
    assert f"- Commit: `{PIN_OLD}`" in (second / "VENDOR.md").read_text()


def test_rollback_gh_missing_exits_2_after_local_restore(tmp_path, capsys):
    root = _write_rollback_root(tmp_path)
    run = FakeRun(gh_missing=True)
    rc = pin_bump.bump(None, root=root, run=run, rollback=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "gh" in err
    # The local revert already landed; only the PR step failed.
    assert f"- Commit: `{PIN_OLD}`" in (root / "VENDOR.md").read_text()
    assert not any(cmd[:2] == ["gh", "pr"] for cmd in run.kinds())


def test_rollback_main_plumbing(tmp_path, capsys, monkeypatch):
    root = _write_rollback_root(tmp_path)
    run = FakeRun()
    monkeypatch.setattr(pin_bump, "_default_run", run)
    rc = pin_bump.main(["--rollback", "--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"rollback to {PIN_OLD[:7]} complete" in out
    assert ["git", "checkout", PIN_OLD] in run.kinds()


def test_rollback_conflicts_parsing(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        pin_bump.main(["--rollback", "--audit-only", "--root", str(tmp_path)])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        pin_bump.main(
            ["--rollback", "--schedule", "0 6 * * *", "--root", str(tmp_path)]
        )
    assert exc.value.code == 2


def _cvss_audit() -> dict:
    """Old audit whose via objects carry title/url/CVSS detail."""
    return {
        "vulnerabilities": {
            "left-pad": {
                "severity": "high",
                "via": [
                    "GHSA-old-0001",
                    {
                        "name": "left-pad",
                        "title": "Prototype pollution in left-pad",
                        "url": "https://github.com/advisories/GHSA-old-0001",
                        "severity": "high",
                        "cvss": {"score": 7.5},
                        "range": ">=1.0.0",
                    },
                ],
                "range": "1.2.3",
            }
        }
    }


def test_explain_flag_parses():
    args = pin_bump.build_parser().parse_args(
        ["--to", PIN_NEW, "--explain", "left-pad"]
    )
    assert args.explain == "left-pad"
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.explain is None


def test_explain_match_key_shapes():
    old = {"GHSA-old-0001": ("high", "left-pad", "1.2.3")}
    new: dict = {}
    key, matches = pin_bump._explain_match_key("GHSA-OLD-0001", old, new)
    assert (key, matches) == ("GHSA-old-0001", ["GHSA-old-0001"])
    key, matches = pin_bump._explain_match_key("left-pad", old, new)
    assert (key, matches) == ("GHSA-old-0001", ["GHSA-old-0001"])
    assert pin_bump._explain_match_key("nope", old, new) == (None, [])
    assert pin_bump._explain_match_key("nope", {}, {}) == (None, [])


def test_explain_audit_only_fixed_advisory(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, audit_only=True, explain="GHSA-old-0001"
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Explain: `GHSA-old-0001`" in out
    assert "### WHAT" in out
    assert "- Package: `left-pad`" in out
    assert "1.2.3 -> gone (dependency removed)" in out
    assert "### WHY" in out
    assert "this bump fixes it" in out
    assert "### FIX" in out
    assert f"Pin bump {PIN_OLD[:7]} -> {PIN_NEW[:7]} fixes `GHSA-old-0001`" in out
    assert "Semver: dependency removed (no bump needed)" in out
    assert "### ROLLBACK" in out
    assert "python -m omega_prime.grokbot.pin_bump --rollback" in out
    assert "[audit-only] audit verdict: improved" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_explain_audit_only_new_advisory_by_package(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    before = _snapshot(root)
    run = FakeRun(npm_doc=_audit(("left-pad", "moderate", "GHSA-new-0002")))
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, explain="left-pad")
    out = capsys.readouterr().out
    assert rc == 1
    assert "## Explain: `GHSA-new-0002`" in out
    assert "this bump introduces it" in out
    assert "Semver: n/a (not a fixed advisory" in out
    assert _snapshot(root) == before


def test_explain_audit_only_cvss_and_title(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, with_saved_old_audit=True, old_audit_doc=_cvss_audit()
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, explain="left-pad")
    out = capsys.readouterr().out
    assert rc == 0
    assert "- CVSS: 7.5" in out
    assert "Prototype pollution in left-pad" in out
    assert "https://github.com/advisories/GHSA-old-0001" in out


def test_explain_not_found_exits_2_listing_present(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, explain="nope")
    captured = capsys.readouterr()
    assert rc == 2
    assert "--explain matched no advisories" in captured.err
    assert "GHSA-old-0001" in captured.err
    assert "## Explain:" not in captured.out
    assert _snapshot(root) == before


def test_explain_not_found_both_clean(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, with_saved_old_audit=True)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, explain="nope")
    err = capsys.readouterr().err
    assert rc == 2
    assert "both clean" in err


def test_explain_json_mode_adds_object(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_cvss_audit(),
    )
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=FakeRun(),
        audit_only=True,
        json_mode=True,
        explain="left-pad",
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert len(captured.out.strip().splitlines()) == 1
    payload = json.loads(captured.out)
    assert payload["explain"]["key"] == "GHSA-old-0001"
    assert payload["explain"]["package"] == "left-pad"
    assert payload["explain"]["status"] == "fixed"
    assert payload["explain"]["spec_old"] == "1.2.3"
    assert payload["explain"]["spec_new"] == ""
    assert payload["explain"]["cvss"] == "7.5"
    assert payload["explain"]["title"] == "Prototype pollution in left-pad"
    assert payload["explain"]["semver"] == "removed"
    assert payload["explain"]["rollback"] == (
        "python -m omega_prime.grokbot.pin_bump --rollback"
    )
    assert "## Explain:" not in captured.out
    assert "## Explain: `GHSA-old-0001`" in captured.err


def test_explain_normal_mode_writes_report_and_explains(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_bump_commit_old_doc(),
    )
    run = FakeRun(npm_doc=_bump_commit_new_doc("1.4.0"))
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, semver_check=True, explain="GHSA-fix-0001"
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Explain: `GHSA-fix-0001`" in out
    assert "1.2.3 -> 1.4.0" in out
    assert "Semver: semver-compatible" in out
    assert "Verdict: improved" in (root / "report.md").read_text()


def test_explain_main_plumbing(tmp_path, capsys, monkeypatch):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    monkeypatch.setattr(pin_bump, "_default_run", run)
    rc = pin_bump.main(
        [
            "--to",
            PIN_NEW,
            "--root",
            str(root),
            "--audit-only",
            "--explain",
            "left-pad",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Explain: `GHSA-old-0001`" in out


def test_explain_conflicts(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, skip_audit=True, explain="x")
    assert rc == 2
    assert "--explain needs audit data" in capsys.readouterr().err
    assert run.cmds == []
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, audit_only=True, explain="  ")
    assert rc == 2
    assert "--explain requires a non-empty" in capsys.readouterr().err
    rc = pin_bump.bump(None, root=root, run=run, rollback=True, explain="x")
    assert rc == 2
    assert "--rollback" in capsys.readouterr().err
    assert run.cmds == []
    with pytest.raises(SystemExit) as exc:
        pin_bump.main(["--rollback", "--explain", "x", "--root", str(tmp_path)])
    assert exc.value.code == 2


# --verify mode (O9) -----------------------------------------------------------


def test_verify_flag_parses():
    args = pin_bump.build_parser().parse_args(["--to", PIN_NEW, "--verify"])
    assert args.verify is True
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.verify is False


def test_verify_with_skip_audit_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, skip_audit=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "--verify" in err and "--skip-audit" in err
    assert run.cmds == []


def test_verify_main_plumbing_skip_audit_conflict(tmp_path, capsys):
    rc = pin_bump.main(
        ["--to", PIN_NEW, "--root", str(tmp_path), "--verify", "--skip-audit"]
    )
    assert rc == 2
    assert "--verify" in capsys.readouterr().err


def test_verify_happy_passes_and_opens_pr(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, open_pr=True)
    out = capsys.readouterr().out
    assert rc == 0
    # Main audit + verification audit: exactly two npm audits.
    assert run.npm_calls == 2
    assert "verification green" in out
    assert f"- Commit: `{PIN_NEW}`" in (root / "VENDOR.md").read_text()
    assert "Verdict: improved" in (root / "report.md").read_text()
    assert "https://example.test/pr/1" in out


def test_verify_failing_untriaged_rolls_back_exit_4(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun(
        npm_docs=[
            {"vulnerabilities": {}},
            _audit(("evil", "critical", "GHSA-new-0009")),
        ]
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, open_pr=True)
    out = capsys.readouterr().out
    assert rc == 4
    assert run.npm_calls == 2
    # Verification output carried on exit 4.
    assert "verification FAILED" in out
    assert "GHSA-new-0009" in out
    # Bump rolled back locally: pins restored, report removed, no PR.
    vendor = (root / "VENDOR.md").read_text()
    assert f"- Commit: `{PIN_OLD}`" in vendor
    assert f"- Commit: `{PIN_NEW}`" not in vendor
    init = (root / "omega_prime" / "redteam" / "__init__.py").read_text()
    assert f"Upstream commit: {PIN_OLD}" in init
    assert not (root / "report.md").exists()
    verbs = run.kinds()
    assert not any(cmd[:2] == ["gh", "pr"] for cmd in verbs)
    assert not any(cmd[:2] == ["git", "push"] for cmd in verbs)
    assert not any(cmd[:2] == ["git", "commit"] for cmd in verbs)


def test_verify_failing_verdict_rolls_back_exit_4(tmp_path, capsys):
    # Second audit is triage-clean (moderate only) but regresses the delta.
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun(
        npm_docs=[
            {"vulnerabilities": {}},
            _audit(("evil", "moderate", "GHSA-new-0009")),
        ]
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True)
    out = capsys.readouterr().out
    assert rc == 4
    assert "verification FAILED" in out
    assert "'regressed'" in out
    assert f"- Commit: `{PIN_OLD}`" in (root / "VENDOR.md").read_text()
    assert not (root / "report.md").exists()


def test_verify_failing_restores_package_json(tmp_path, capsys):
    # Lockfile shape: the bump audit sees an improved verdict with one
    # compatible fix (so --bump-commit applies), then verification trips on
    # an untriaged high and the version bump rolls back with the pins.
    old_doc = _lock_audit(
        {"left-pad": "1.2.3"},
        ("GHSA-old-0001", "high", "left-pad"),
        ("GHSA-persist-0002", "moderate", "left-pad"),
    )
    root = _write_fixture_root(
        tmp_path, with_saved_old_audit=True, old_audit_doc=old_doc
    )
    pkg = _write_package_json(root, "1.2.3")
    run = FakeRun(
        npm_docs=[
            _lock_audit(
                {"left-pad": "1.4.0"},
                ("GHSA-persist-0002", "moderate", "left-pad"),
            ),
            _audit(("evil", "high", "GHSA-new-0009")),
        ]
    )
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        verify=True,
        semver_check=True,
        bump_commit=True,
    )
    out = capsys.readouterr().out
    assert rc == 4
    assert "version bump (minor): 1.2.3 -> 1.3.0" in out
    assert "verification FAILED" in out
    assert json.loads(pkg.read_text())["version"] == "1.2.3"
    assert f"- Commit: `{PIN_OLD}`" in (root / "VENDOR.md").read_text()
    assert not (root / "report.md").exists()


def test_verify_dry_run_prints_without_double_audit(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    before = _snapshot(root)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "would re-run verification audit" in out
    assert "no double audit" in out
    # Exactly one audit ran: verification never re-audits in dry-run.
    assert run.npm_calls == 1
    assert _snapshot(root) == before
    assert not (root / "report.md").exists()


def test_verify_semver_holds_passes(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_versioned_audit(("left-pad", "high", "GHSA-old-0001", "1.2.3")),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, semver_check=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert run.npm_calls == 2
    assert "semver holds" in out
    assert "Verdict: improved" in (root / "report.md").read_text()


def test_verify_semver_changed_fails_exit_4(tmp_path, capsys):
    # Both audits see an improved verdict, but the re-audit classifies the
    # same fix as major instead of compatible: the classification no longer
    # holds, so verification fails and the bump rolls back.
    old_doc = _lock_audit(
        {"left-pad": "1.2.3"},
        ("GHSA-old-0001", "high", "left-pad"),
        ("GHSA-persist-0002", "moderate", "left-pad"),
    )
    root = _write_fixture_root(
        tmp_path, with_saved_old_audit=True, old_audit_doc=old_doc
    )
    run = FakeRun(
        npm_docs=[
            _lock_audit(
                {"left-pad": "1.4.0"},
                ("GHSA-persist-0002", "moderate", "left-pad"),
            ),
            _lock_audit(
                {"left-pad": "2.0.0"},
                ("GHSA-persist-0002", "moderate", "left-pad"),
            ),
        ]
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, semver_check=True)
    out = capsys.readouterr().out
    assert rc == 4
    assert "semver classification changed" in out
    assert f"- Commit: `{PIN_OLD}`" in (root / "VENDOR.md").read_text()
    assert not (root / "report.md").exists()


def test_verify_json_mode_object_on_success(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), verify=True, json_mode=True)
    captured = capsys.readouterr()
    assert rc == 0
    assert len(captured.out.strip().splitlines()) == 1
    payload = json.loads(captured.out)
    assert payload["verify"] == {
        "ran": True,
        "verdict": "improved",
        "passed": True,
    }
    assert payload["exit_code"] == 0


def test_verify_json_mode_object_on_failure(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun(
        npm_docs=[
            {"vulnerabilities": {}},
            _audit(("evil", "high", "GHSA-new-0009")),
        ]
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, json_mode=True)
    captured = capsys.readouterr()
    assert rc == 4
    assert len(captured.out.strip().splitlines()) == 1
    payload = json.loads(captured.out)
    assert payload["verify"]["ran"] is True
    assert payload["verify"]["passed"] is False
    assert payload["exit_code"] == 4


def test_verify_json_mode_absent_without_flag(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(PIN_NEW, root=root, run=FakeRun(), json_mode=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "verify" not in json.loads(out)


def test_verify_json_mode_dry_run_unrun(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=FakeRun(), verify=True, json_mode=True, dry_run=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["verify"] == {"ran": False, "verdict": None, "passed": None}


def test_verify_audit_only_passes_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    # Current checkout already carries the fix: both the audit-only pass
    # and the verification re-audit see the improved delta.
    before = _snapshot(root)
    run = FakeRun(npm_doc={"vulnerabilities": {}})
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, verify=True, audit_only=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert run.npm_calls == 2
    assert "verification green" in out
    assert _mutating_verbs(run.kinds()) == []
    assert _snapshot(root) == before


# --from-audit mode (P9) --------------------------------------------------------


def test_from_audit_flag_parses():
    args = pin_bump.build_parser().parse_args(
        ["--to", PIN_NEW, "--from-audit", "new.json", "--old-audit", "old.json"]
    )
    assert args.from_audit == "new.json"
    assert args.old_audit == "old.json"
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.from_audit is None


def test_from_audit_happy_path_skips_reaudit(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(
        json.dumps(_audit(("left-pad", "high", "GHSA-old-0001"))), encoding="utf-8"
    )
    new_audit = tmp_path / "new.json"
    new_audit.write_text(json.dumps(_audit()), encoding="utf-8")
    # node/npm both absent: the saved new audit must be used as-is, so no
    # re-audit subprocess is ever attempted.
    run = FakeRun(node_missing=True, npm_missing=True)
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        from_audit=str(new_audit),
        old_audit=str(old_audit),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "no re-audit" in out
    assert "Verdict: improved" in (root / "report.md").read_text()
    assert f"- Commit: `{PIN_NEW}`" in (root / "VENDOR.md").read_text()
    assert not any(cmd[0] in ("node", "npm") for cmd in run.kinds())


def test_from_audit_missing_file_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(json.dumps(_audit()), encoding="utf-8")
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        from_audit=str(tmp_path / "absent.json"),
        old_audit=str(old_audit),
    )
    assert rc == 2
    assert "--from-audit not found" in capsys.readouterr().err


def test_from_audit_unparseable_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(json.dumps(_audit()), encoding="utf-8")
    new_audit = tmp_path / "new.json"
    new_audit.write_text("{not json", encoding="utf-8")
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        from_audit=str(new_audit),
        old_audit=str(old_audit),
    )
    assert rc == 2
    assert "cannot parse --from-audit" in capsys.readouterr().err


def test_from_audit_skip_audit_conflict_exits_2(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, from_audit="new.json", skip_audit=True
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "--from-audit" in err and "--skip-audit" in err
    assert run.cmds == []


def test_from_audit_needs_old_audit(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)  # no saved artifact, no --old-audit
    new_audit = tmp_path / "new.json"
    new_audit.write_text(json.dumps(_audit()), encoding="utf-8")
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, from_audit=str(new_audit))
    err = capsys.readouterr().err
    assert rc == 2
    assert "--old-audit" in err
    assert run.cmds == []


def test_from_audit_dry_run_no_mutation_no_audit(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(
        json.dumps(_audit(("left-pad", "high", "GHSA-old-0001"))), encoding="utf-8"
    )
    new_audit = tmp_path / "new.json"
    new_audit.write_text(json.dumps(_audit()), encoding="utf-8")
    before = _snapshot(root)
    run = FakeRun(node_missing=True, npm_missing=True)
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        from_audit=str(new_audit),
        old_audit=str(old_audit),
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "audit verdict: improved" in out
    assert not (root / "report.md").exists()
    assert not any(cmd[0] in ("node", "npm") for cmd in run.kinds())
    assert _mutating_verbs(run.kinds()) == []
    assert _snapshot(root) == before


def test_from_audit_audit_only_no_mutation_no_audit(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(
        json.dumps(_audit(("left-pad", "high", "GHSA-old-0001"))), encoding="utf-8"
    )
    new_audit = tmp_path / "new.json"
    new_audit.write_text(json.dumps(_audit()), encoding="utf-8")
    before = _snapshot(root)
    run = FakeRun(node_missing=True, npm_missing=True)
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        from_audit=str(new_audit),
        old_audit=str(old_audit),
        audit_only=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[audit-only] audit verdict: improved" in out
    assert not any(cmd[0] in ("node", "npm") for cmd in run.kinds())
    assert _mutating_verbs(run.kinds()) == []
    assert _snapshot(root) == before


def test_from_audit_semver_json_and_bump_commit(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    _write_package_json(root)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(json.dumps(_bump_commit_old_doc()), encoding="utf-8")
    new_audit = tmp_path / "new.json"
    new_audit.write_text(json.dumps(_bump_commit_new_doc("1.3.0")), encoding="utf-8")
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        from_audit=str(new_audit),
        old_audit=str(old_audit),
        semver_check=True,
        bump_commit=True,
        json_mode=True,
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["verdict"] == "improved"
    assert payload["semver"] == {"GHSA-fix-0001": "compatible"}
    assert payload["bump"]["applied"] is True
    assert payload["bump"]["new"] == "1.3.0"
    assert "semver compatible" in captured.err
    assert "## Semver check" in (root / "report.md").read_text()
    assert json.loads((root / "redteam" / "package.json").read_text())["version"] == (
        "1.3.0"
    )
    assert not any(cmd[0] in ("node", "npm") for cmd in run.kinds())


def test_from_audit_main_plumbing(tmp_path, capsys, monkeypatch):
    root = _write_fixture_root(tmp_path)
    old_audit = tmp_path / "old.json"
    old_audit.write_text(
        json.dumps(_audit(("left-pad", "high", "GHSA-old-0001"))), encoding="utf-8"
    )
    new_audit = tmp_path / "new.json"
    new_audit.write_text(json.dumps(_audit()), encoding="utf-8")
    run = FakeRun(node_missing=True, npm_missing=True)
    monkeypatch.setattr(pin_bump, "_default_run", run)
    rc = pin_bump.main(
        [
            "--to",
            PIN_NEW,
            "--root",
            str(root),
            "--from-audit",
            str(new_audit),
            "--old-audit",
            str(old_audit),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Verdict: improved" in (root / "report.md").read_text()
    assert not any(cmd[0] in ("node", "npm") for cmd in run.kinds())


def test_from_audit_main_plumbing_skip_audit_conflict(tmp_path, capsys):
    rc = pin_bump.main(
        [
            "--to",
            PIN_NEW,
            "--root",
            str(tmp_path),
            "--from-audit",
            "new.json",
            "--old-audit",
            "old.json",
            "--skip-audit",
        ]
    )
    assert rc == 2
    assert "--from-audit" in capsys.readouterr().err


# --pin-note mode (Q9) ---------------------------------------------------------


def test_pin_note_flag_parses():
    args = pin_bump.build_parser().parse_args(
        ["--to", PIN_NEW, "--pin-note", "left-pad=waived: downstream only"]
    )
    assert args.pin_note == ["left-pad=waived: downstream only"]
    default = pin_bump.build_parser().parse_args(["--to", PIN_NEW])
    assert default.pin_note is None


def test_parse_pin_note_shapes():
    notes, error = pin_bump._parse_pin_notes(["GHSA-old-0001=waived"])
    assert error is None
    assert notes == [("GHSA-old-0001", "waived")]
    # Split on the first "=" only; surrounding whitespace is stripped.
    notes, error = pin_bump._parse_pin_notes([" left-pad = needs review = soon "])
    assert error is None
    assert notes == [("left-pad", "needs review = soon")]
    notes, error = pin_bump._parse_pin_notes(["missing-equals"])
    assert notes is None
    assert error is not None and "SELECTOR=TEXT" in error
    notes, error = pin_bump._parse_pin_notes(["=empty selector"])
    assert notes is None
    assert error is not None and "non-empty" in error


def test_render_operator_notes_section_shapes():
    old = {"GHSA-old-0001": ("high", "left-pad", "1.2.3")}
    section = pin_bump.render_operator_notes_section(
        [("left-pad", "a | b"), ("nope", "later")], old, {}
    )
    assert section.startswith("## Operator notes")
    assert "| Advisory | Note |" in section
    assert "| `GHSA-old-0001` | a \\| b |" in section
    assert "| `nope` (no matching advisory) | later |" in section


def test_pin_note_matched_renders_in_report(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, pin_note=["left-pad=waived: downstream only"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "## Operator notes" in report
    assert "| Advisory | Note |" in report
    assert "`GHSA-old-0001`" in report
    assert "waived: downstream only" in report
    assert "operator notes" in out


def test_pin_note_unmatched_flagged(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin_note=["nope=check later"])
    capsys.readouterr()
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "`nope` (no matching advisory)" in report
    assert "check later" in report


def test_pin_note_case_insensitive_and_repeatable(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        pin_note=["LEFT-PAD=first note", "ghsa-old=second note"],
    )
    capsys.readouterr()
    assert rc == 0
    report = (root / "report.md").read_text()
    assert report.index("first note") < report.index("second note")
    assert report.count("`GHSA-old-0001`") >= 2


def test_pin_note_malformed_exits_2_without_subprocess(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, pin_note=["missing-equals"])
    assert rc == 2
    assert "SELECTOR=TEXT" in capsys.readouterr().err
    assert run.cmds == []


def test_pin_note_conflicts(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, skip_audit=True, pin_note=["x=y"])
    assert rc == 2
    assert "--pin-note needs audit data" in capsys.readouterr().err
    assert run.cmds == []
    rc = pin_bump.bump(None, root=root, run=run, rollback=True, pin_note=["x=y"])
    assert rc == 2
    assert "--rollback" in capsys.readouterr().err
    assert run.cmds == []


def test_pin_note_json_mode_echo(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        json_mode=True,
        pin_note=["left-pad=waived", "nope=later"],
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["notes"] == [
        {"selector": "left-pad", "note": "waived", "advisories": ["GHSA-old-0001"]},
        {"selector": "nope", "note": "later", "advisories": []},
    ]
    assert "## Operator notes" in (root / "report.md").read_text()


def test_pin_note_json_absent_without_flag(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    rc = pin_bump.bump(PIN_NEW, root=root, run=run, json_mode=True)
    captured = capsys.readouterr()
    assert rc == 0
    assert "notes" not in json.loads(captured.out)
    assert "Operator notes" not in (root / "report.md").read_text()


def test_pin_note_audit_only_prints_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, audit_only=True, pin_note=["left-pad=waived"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Operator notes" in out
    assert "waived" in out
    assert not (root / "report.md").exists()
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert _mutating_verbs(run.kinds()) == []


def test_pin_note_dry_run_prints_without_mutation(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    before = _snapshot(root)
    rc = pin_bump.bump(
        PIN_NEW, root=root, run=run, dry_run=True, pin_note=["GHSA-old-0001=seen"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Operator notes" in out
    assert "seen" in out
    assert not (root / "report.md").exists()
    assert _snapshot(root) == before
    assert _mutating_verbs(run.kinds()) == []


def test_pin_note_composes_with_pin(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(
            ("left-pad", "high", "GHSA-old-0001"),
            ("wibble", "high", "GHSA-old-0002"),
        ),
    )
    run = FakeRun()
    rc = pin_bump.bump(
        PIN_NEW,
        root=root,
        run=run,
        pin=["left-pad"],
        pin_note=["wibble=other fix noted"],
    )
    capsys.readouterr()
    assert rc == 0
    report = (root / "report.md").read_text()
    assert "Out of scope for --pin" in report
    assert "## Operator notes" in report
    assert "`GHSA-old-0002`" in report
    assert "other fix noted" in report


def test_pin_note_main_plumbing(tmp_path, capsys, monkeypatch):
    root = _write_fixture_root(
        tmp_path,
        with_saved_old_audit=True,
        old_audit_doc=_audit(("left-pad", "high", "GHSA-old-0001")),
    )
    run = FakeRun()
    monkeypatch.setattr(pin_bump, "_default_run", run)
    rc = pin_bump.main(
        [
            "--to",
            PIN_NEW,
            "--root",
            str(root),
            "--pin-note",
            "left-pad=waived",
            "--audit-only",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Operator notes" in out

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
        origin_url: str = "https://github.com/example/purple-team.git",
        gh_api_content: str | None = None,
        gh_api_fail: bool = False,
        upstream_npm_doc: dict | None = None,
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

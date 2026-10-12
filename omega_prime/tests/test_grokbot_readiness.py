# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Isolation-effect, sentinel, canary, and CLI-behavior tests.

Expectations are derived from real files (roster text, export output),
never hardcoded counts, source wording, or default wiring.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime.grokbot import local_validation as lv
from omega_prime.grokbot import readiness as cli
from omega_prime.grokbot.audit import redact_sensitive
from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets
from omega_prime.tooling.roster import load_roster_names

ROOT = Path(__file__).resolve().parents[2]

_SENTINELS = {
    "SUBSTRATE_TOKEN": "live-substrate-sentinel-abc123",
    "SUBSTRATE_TOKEN_GROK_BOT": "live-substrate-grok-sentinel-abc123",
    "MCP_AUTH_TOKEN": "live-mcp-sentinel-abc123",
    "XAI_API_KEY": "live-xai-sentinel-abc123",
    "OMEGA_PRIME_DESK_BUS_URL": "https://bus.example.invalid/sentinel",
    "OMEGA_PRIME_DESK_DOCS_INDEX": "https://docs.example.invalid/sentinel",
    "OMEGA_PRIME_DESK_NOTIFY_URL": "https://notify.example.invalid/sentinel",
    "OMEGA_PRIME_DESK_CHILD_MODEL": "sentinel-model",
    "ULTRATHINK_ROOT": "/sentinel/ultrathink",
    "OMEGA_PRIME_WORK_ROOT": "/sentinel/work",
    "OMEGA_PRIME_STATE_DIR": "/sentinel/state",
    "OMEGA_PRIME_AUDIT_LOG": "/sentinel/audit.jsonl",
    "OMEGA_PRIME_COMMAND_LOG": "/sentinel/commands.jsonl",
    "OMEGA_PRIME_PRIME_RLM_ENABLED": "1",
    "OMEGA_PRIME_PRIME_KERNEL_ENABLED": "1",
}


def _prime_selector_names() -> tuple[str, ...]:
    from omega_prime.config import PRIME_FAMILIES

    return tuple(f"OMEGA_PRIME_PRIME_{name.upper()}_ENABLED" for name in PRIME_FAMILIES)


_BOOLEAN_SELECTORS = (
    "OMEGA_PRIME_PRIME_RLM_ENABLED",
    "OMEGA_PRIME_PRIME_KERNEL_ENABLED",
)
"""Parent feature flags set to realistic values; asserted absent by key, never
by text (their value ``1`` legitimately recurs in fresh env/JSON output)."""

_REDIRECTED_ROOTS = (
    "ULTRATHINK_ROOT",
    "OMEGA_PRIME_WORK_ROOT",
    "OMEGA_PRIME_STATE_DIR",
    "OMEGA_PRIME_AUDIT_LOG",
    "OMEGA_PRIME_COMMAND_LOG",
)
"""Keys the child deliberately redefines to fresh owned roots; asserted by
containment under the disposable base and inequality with the caller value."""


@pytest.fixture
def sentinel_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Parent environment full of live-looking values the child must not see.

    Returns only genuine text canaries (credential/endpoint shapes); boolean
    selectors and redirected roots are set in the parent but checked by key
    and containment rules instead.
    """
    for key, value in _SENTINELS.items():
        monkeypatch.setenv(key, value)
    return {
        key: value for key, value in _SENTINELS.items() if key not in _BOOLEAN_SELECTORS
    }


def test_layout_roots_under_base_and_env_restored(
    sentinel_env: dict[str, str],
) -> None:
    before = dict(os.environ)
    with lv.isolated_layout(ROOT) as layout:
        assert layout.base != layout.source
        for path in (
            layout.install,
            layout.home,
            layout.work,
            layout.state,
            layout.audit,
        ):
            assert path.is_relative_to(layout.base), path
        assert layout.command_log.parent.is_relative_to(layout.base)
        assert layout.source == ROOT
        for value in layout.env.values():
            for sentinel in sentinel_env.values():
                assert sentinel not in value
    assert dict(os.environ) == before
    assert not layout.base.exists()


def test_child_env_omits_secrets_and_redirects_roots(
    sentinel_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/sentinel/path")
    with lv.isolated_layout(ROOT) as layout:
        env = layout.env
        for key in (*sentinel_env, *_prime_selector_names()):
            if key in _REDIRECTED_ROOTS:
                assert env[key] != _SENTINELS[key], key
                continue
            assert key not in env, key
        for value in env.values():
            for sentinel in sentinel_env.values():
                assert sentinel not in value
        assert env["PATH"] != "/sentinel/path"
        assert str(Path(sys.executable).parent) in env["PATH"].split(os.pathsep)
        for key in (
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "XDG_RUNTIME_DIR",
            "TMPDIR",
            "OMEGA_PRIME_WORK_ROOT",
            "OMEGA_PRIME_STATE_DIR",
            "OMEGA_PRIME_AUDIT_LOG",
            "OMEGA_PRIME_COMMAND_LOG",
            "ULTRATHINK_ROOT",
        ):
            assert Path(env[key]).is_relative_to(layout.base), key
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert env["PYTHONPATH"] == str(layout.install)


def test_loopback_spawn_inherits_only_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production loopback host observes no caller-only keys.

    Invokes the real ``lv._run_loopback`` with a genuinely attached
    ``GrokBotTurnEmulator``. The disposable ``mcp_server.py`` copy carries a
    startup observer, placed after its future import, that records Boolean
    presence (never values) of caller-only credential/endpoint keys to an
    owned WORK marker selected by an explicit fixture variable. The marker
    channel is also present in the caller environment so that a reversion to
    ambient inheritance would still run the observer and record the leaked
    keys instead of merely missing the marker. Only key names cross into the
    disposable copy; no secret value is passed in, printed, or compared.
    """
    from omega_prime.grokbot.emulator import GrokBotTurnEmulator

    caller_keys = (
        "XAI_API_KEY",
        "OMEGA_PRIME_DESK_BUS_URL",
        "OMEGA_READINESS_CALLER_ONLY_PROBE",
    )
    monkeypatch.setenv("XAI_API_KEY", "owned-caller-only-credential")
    monkeypatch.setenv("OMEGA_PRIME_DESK_BUS_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("OMEGA_READINESS_CALLER_ONLY_PROBE", "caller-only")
    assert os.environ.get("XAI_API_KEY"), "caller credential precondition"
    assert os.environ.get("OMEGA_PRIME_DESK_BUS_URL"), "caller endpoint precondition"
    assert os.environ.get("OMEGA_READINESS_CALLER_ONLY_PROBE")
    # Keep the expected parent/child registry OFF while retaining only the
    # caller credential/endpoint/probe contamination this boundary exercises.
    from omega_prime.config import PRIME_FAMILIES

    for _family in PRIME_FAMILIES:
        monkeypatch.setenv(f"OMEGA_PRIME_PRIME_{_family.upper()}_ENABLED", "0")
    monkeypatch.setenv("OMEGA_PRIME_REDTEAM_ENABLED", "0")
    with lv.isolated_layout(ROOT) as layout:
        marker = layout.work / "loopback-env-marker.json"
        layout.env["OMEGA_READINESS_LOOPBACK_MARKER"] = str(marker)
        monkeypatch.setenv("OMEGA_READINESS_LOOPBACK_MARKER", str(marker))
        # Hold the owned bootstrap/roots constant in both implementations;
        # only the caller-only keys distinguish ambient inheritance.
        assert all(key not in layout.env for key in caller_keys)
        for key, value in layout.env.items():
            monkeypatch.setenv(key, value)
        server_copy = layout.install / "omega_prime" / "mcp_server.py"
        anchor = "from __future__ import annotations\n"
        text = server_copy.read_text(encoding="utf-8")
        assert anchor in text, "disposable server copy missing future-import anchor"
        observer = (
            "try:\n"
            "    import json as _readiness_loopback_json\n"
            "    import os as _readiness_loopback_os\n"
            "    _readiness_loopback_marker = _readiness_loopback_os.environ.get(\n"
            '        "OMEGA_READINESS_LOOPBACK_MARKER"\n'
            "    )\n"
            "    if _readiness_loopback_marker:\n"
            "        _readiness_loopback_keys = (\n"
            '            "XAI_API_KEY",\n'
            '            "OMEGA_PRIME_DESK_BUS_URL",\n'
            '            "OMEGA_READINESS_CALLER_ONLY_PROBE",\n'
            "        )\n"
            "        _readiness_loopback_seen = {\n"
            "            _readiness_loopback_key: (\n"
            "                _readiness_loopback_key in _readiness_loopback_os.environ\n"
            "            )\n"
            "            for _readiness_loopback_key in _readiness_loopback_keys\n"
            "        }\n"
            "        with open(\n"
            '            _readiness_loopback_marker, "w", encoding="utf-8"\n'
            "        ) as _readiness_loopback_fh:\n"
            "            _readiness_loopback_fh.write(\n"
            "                _readiness_loopback_json.dumps(\n"
            "                    _readiness_loopback_seen, sort_keys=True\n"
            "                )\n"
            "            )\n"
            "except Exception:\n"
            "    pass\n"
        )
        server_copy.write_text(
            text.replace(anchor, anchor + observer, 1), encoding="utf-8"
        )
        emulator = GrokBotTurnEmulator(
            root=layout.install,
            home=layout.home,
            env=dict(layout.env),
            approvals=lv.TARGETED_LOCAL_APPROVALS,
        )
        probe = lv.ReadinessProbe(layout)
        probe.attach(emulator)
        lv._run_loopback(probe)
        assert marker.is_file(), "child observer never ran in the owned host"
        payload = json.loads(marker.read_text(encoding="utf-8"))
        assert payload == {key: False for key in caller_keys}, payload
        names = [check["name"] for check in probe.checks]
        for expected in (
            "loopback:manifest-agrees",
            "loopback:audit-sanitized",
            "loopback:clean-shutdown",
        ):
            assert expected in names, expected


def test_preimported_production_does_not_leak(
    sentinel_env: dict[str, str],
) -> None:
    import omega_prime.mcp_server  # noqa: F401 - observational pre-import case

    with lv.isolated_layout(ROOT) as layout:
        for value in layout.env.values():
            for sentinel in sentinel_env.values():
                assert sentinel not in value
        assert not (layout.install / lv.FIXTURE_NAME).exists()


def test_export_keeps_code_excludes_runtime_data() -> None:
    with lv.isolated_layout(ROOT) as layout:
        install = layout.install
        assert (install / "omega_prime" / "memory" / "store.py").is_file()
        assert (install / "omega_prime" / "session" / "persist.py").is_file()
        assert (install / "omega_prime" / "memory" / "manager.py").is_file()
        assert (install / "omega_prime" / "contracts" / "tool-rosters").is_dir()
        assert (
            install / "omega_prime" / "prompts" / "bot-00-omega-prime.xml"
        ).is_file()
        assert not (install / "prime-agent").exists()
        assert list(install.rglob("*.db")) == []
        assert list(install.rglob("*.sqlite*")) == []
        assert not (install / "omega_prime" / "state").exists()
        assert list(install.rglob("jobs.json")) == []
        assert list(install.rglob("intake.json")) == []
        for scope in ("memory", "session"):
            leftovers = [
                p
                for p in (install / "omega_prime" / scope).rglob("*")
                if p.is_file() and p.suffix not in (".py", ".pyc")
            ]
            assert leftovers == [], leftovers
        assert all(
            p.suffix == ".py"
            for p in (install / "omega_prime" / "skills_runtime").rglob("*")
            if p.is_file()
        )


def test_exported_memory_session_code_imports() -> None:
    with lv.isolated_layout(ROOT) as layout:
        code = (
            "import omega_prime.memory.store, omega_prime.memory.manager, "
            "omega_prime.session.persist, omega_prime.session.search"
        )
        env = dict(layout.env)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(layout.work),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr.decode()[-400:]


def test_reject_symlink_escape_and_mutable(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "omega_prime").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    mutable = src / "sessions.db"
    mutable.write_text("mutable")
    escape = src / "omega_prime" / "escape.py"
    escape.symlink_to(outside)
    with pytest.raises(lv.IsolationError):
        lv._reject_symlink(escape, src)
    escape.unlink()
    backed = src / "omega_prime" / "backed.db"
    backed.symlink_to(mutable)
    with pytest.raises(lv.IsolationError):
        lv._reject_symlink(backed, src)


def test_schedules_empty_and_source_untouched() -> None:
    scheduler = ROOT / "omega_prime" / "cron" / "scheduler.py"
    before = scheduler.stat().st_mtime_ns
    with lv.isolated_layout(ROOT) as layout:
        assert list(layout.install.rglob("jobs.json")) == []
        assert list(layout.install.rglob("*.schedule")) == []
    assert scheduler.stat().st_mtime_ns == before


def test_cleanup_on_error() -> None:
    base: Path | None = None
    with pytest.raises(RuntimeError), lv.isolated_layout(ROOT) as layout:
        base = layout.base
        assert base.is_dir()
        raise RuntimeError("boom")
    assert base is not None
    assert not base.exists()


def _minimal_source(root: Path) -> None:
    package = root / "omega_prime"
    roster_dir = package / "contracts" / "tool-rosters"
    roster_dir.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (roster_dir / "omega-prime.yaml").write_text("tools:\n  - a-tool\n")


def _bases() -> set[Path]:
    return set(Path("/tmp").glob("omega-readiness-*"))


def test_export_failure_leaves_no_base(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _minimal_source(src)
    (src / "omega_prime" / "escape.py").symlink_to("/etc/hostname")
    before = _bases()
    with pytest.raises(lv.IsolationError), lv.isolated_layout(src):
        pass
    assert _bases() == before


def test_public_export_boundaries_on_real_tree() -> None:
    """Actual export: canonical policy and tracked inputs ship; nothing else."""
    with lv.isolated_layout(ROOT) as layout:
        install = layout.install
        assert (
            install / "omega_prime" / "contracts" / "policies" / "omega-prime.json"
        ).is_file()
        assert (install / "omega_prime" / "skills" / "python" / "SKILL.md").is_file()
        assert (install / "omega_prime" / "commands" / "seeds" / "memory.txt").is_file()
        assert (
            install / "omega_prime" / "prompts" / "bot-00-omega-prime.xml"
        ).is_file()
        assert list(install.rglob(".env")) == []
        assert list(install.rglob("*.pem")) == []
        assert list(install.rglob("*.key")) == []


def test_nested_export_uses_trusted_inventory() -> None:
    """Nested VCS-less re-export cannot promote a learned skill to shipped.

    Seeds the learned skill inside the owned outer install (never the real
    repository) and proves the inner export drops it while inventoried data
    still ships.
    """
    with lv.isolated_layout(ROOT) as outer:
        install = outer.install
        inventory = json.loads(
            (install / ".omega-readiness-inventory.json").read_text(encoding="utf-8")
        )
        assert "omega_prime/contracts/tool-rosters/omega-prime.yaml" in inventory
        seed = install / "omega_prime" / "skills" / "local-unshipped"
        seed.mkdir(parents=True)
        (seed / "SKILL.md").write_text("# learned\n", encoding="utf-8")
        (seed / "helper.py").write_text("# learned helper\n", encoding="utf-8")
        fresh_code = install / "omega_prime" / "tools" / "_owned_new_canary.py"
        fresh_code.write_text("# newly implemented product code\n", encoding="utf-8")
        with lv.isolated_layout(install) as inner:
            assert not (
                inner.install
                / "omega_prime"
                / "skills"
                / "local-unshipped"
                / "SKILL.md"
            ).exists()
            assert not (
                inner.install
                / "omega_prime"
                / "skills"
                / "local-unshipped"
                / "helper.py"
            ).exists()
            assert (
                inner.install / "omega_prime" / "tools" / "_owned_new_canary.py"
            ).is_file()
            assert (
                inner.install
                / "omega_prime"
                / "contracts"
                / "tool-rosters"
                / "omega-prime.yaml"
            ).is_file()
            assert (
                inner.install
                / "omega_prime"
                / "contracts"
                / "policies"
                / "omega-prime.json"
            ).is_file()


def test_nested_prime_symlink_rejected(tmp_path: Path) -> None:
    src = tmp_path / "src"
    nested = src / "prime-agent" / "prime-agent-runtime" / "src" / "nested"
    nested.mkdir(parents=True)
    (nested / "ok.rs").write_text("")
    (nested / "escape.rs").symlink_to("/etc/hostname")
    with pytest.raises(lv.IsolationError):
        lv._validate_prime_tree(
            "prime-agent/prime-agent-runtime/src",
            src / "prime-agent" / "prime-agent-runtime" / "src",
            src,
        )
    (nested / "escape.rs").unlink()
    lv._validate_prime_tree(
        "prime-agent/prime-agent-runtime/src",
        src / "prime-agent" / "prime-agent-runtime" / "src",
        src,
    )


def test_accounting_gap_is_failed() -> None:
    rows, problems = lv.account_roster(
        canonical=["a-tool", "b-tool"],
        offered=["a-tool", "b-tool"],
        evidence={"a-tool": ["check:x"]},
        disabled={},
        blocked={},
        family_of=lambda name: "Fam",
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["a-tool"]["status"] == "exercised"
    assert by_name["b-tool"]["status"] == "failed"
    assert problems, "an offered-but-unexercised tool must be a problem"


def test_accounting_extra_offered_is_problem() -> None:
    _rows, problems = lv.account_roster(
        canonical=["a-tool"],
        offered=["a-tool", "zzz-extra"],
        evidence={"a-tool": ["check:x"]},
        disabled={},
        blocked={},
        family_of=lambda name: "Fam",
    )
    assert any("zzz-extra" in problem for problem in problems)


def test_accounting_disabled_and_unmapped() -> None:
    rows, problems = lv.account_roster(
        canonical=["a-tool", "b-tool"],
        offered=["a-tool"],
        evidence={"a-tool": ["check:x"]},
        disabled={"b-tool": "prime.rlm.enabled=false verified absent"},
        blocked={},
        family_of=lambda name: None if name == "a-tool" else "Fam",
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["b-tool"]["status"] == "feature_disabled"
    assert by_name["b-tool"]["prerequisites"] == []
    assert any("unmapped" in problem for problem in problems)


def test_accounting_blocked_preserves_prerequisites() -> None:
    rows, problems = lv.account_roster(
        canonical=["a-tool", "b-tool", "c-tool"],
        offered=["a-tool", "b-tool", "c-tool"],
        evidence={"a-tool": ["check:x"], "b-tool": ["check:z"]},
        disabled={},
        blocked={"b-tool": (["check:y"], ["real prerequisite"])},
        family_of=lambda name: "Fam",
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["a-tool"]["status"] == "exercised"
    assert by_name["b-tool"]["status"] == "exercised"
    assert by_name["c-tool"]["status"] == "failed"
    rows, problems = lv.account_roster(
        canonical=["a-tool", "b-tool"],
        offered=["a-tool", "b-tool"],
        evidence={"a-tool": ["check:x"]},
        disabled={},
        blocked={"b-tool": (["check:y"], ["real prerequisite"])},
        family_of=lambda name: "Fam",
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["b-tool"]["status"] == "blocked"
    assert by_name["b-tool"]["evidence"] == ["check:y"]
    assert by_name["b-tool"]["prerequisites"] == ["real prerequisite"]
    assert problems == []


def _redteam_family(name: str) -> str:
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    return "Red Team" if name in set(REDTEAM_TOOL_NAMES) else "Fam"


@pytest.mark.parametrize("prime_sim_present", [False, True])
def test_redteam_disabled_absent_is_feature_disabled(prime_sim_present: bool) -> None:
    """Off-flag plus genuine absence reads feature_disabled in both profiles.

    The parametrized extra exercised tool stands in for the include-prime
    main runtime offering more than the local-only one; Red Team stays off
    and absent in both compositions.
    """
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    redteam = list(REDTEAM_TOOL_NAMES)
    extra = ["extra-prime-sim"] if prime_sim_present else []
    canonical = [*redteam, "sys_cache", *extra]
    offered = ["sys_cache", *extra]
    evidence = {name: ["check:x"] for name in ["sys_cache", *extra]}
    config = {"redteam": {"enabled": False}}
    disabled = lv._redteam_disabled_evidence(config, offered)
    rows, problems = lv.account_roster(
        canonical=canonical,
        offered=offered,
        evidence=evidence,
        disabled=disabled,
        blocked={},
        family_of=_redteam_family,
    )
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == set(canonical)
    for name in redteam:
        row = by_name[name]
        assert row["status"] == "feature_disabled"
        assert row["evidence"], name
        assert any(
            "redteam" in entry and "absent" in entry for entry in row["evidence"]
        ), name
        assert row["prerequisites"] == []
    assert problems == []


def test_redteam_enabled_missing_stays_failed() -> None:
    """An enabled-but-missing Red Team tool is a failure, never disabled."""
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    redteam = list(REDTEAM_TOOL_NAMES)
    canonical = [*redteam, "sys_cache"]
    offered = ["sys_cache"]
    config = {"redteam": {"enabled": True}}
    disabled = lv._redteam_disabled_evidence(config, offered)
    rows, problems = lv.account_roster(
        canonical=canonical,
        offered=offered,
        evidence={"sys_cache": ["check:x"]},
        disabled=disabled,
        blocked={},
        family_of=_redteam_family,
    )
    by_name = {row["name"]: row for row in rows}
    for name in redteam:
        assert by_name[name]["status"] == "failed", name
    assert any("absent-from-offered" in problem for problem in problems)


def test_redteam_offered_while_disabled_is_not_hidden() -> None:
    """A disabled-flagged tool that is offered must still fail when unexercised."""
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    sample = next(iter(REDTEAM_TOOL_NAMES))
    canonical = [sample, "sys_cache"]
    offered = [sample, "sys_cache"]
    config = {"redteam": {"enabled": False}}
    disabled = lv._redteam_disabled_evidence(config, offered)
    rows, problems = lv.account_roster(
        canonical=canonical,
        offered=offered,
        evidence={"sys_cache": ["check:x"]},
        disabled=disabled,
        blocked={},
        family_of=_redteam_family,
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name[sample]["status"] == "failed"
    assert problems, "an offered-but-unexercised tool must be a problem"
    assert any(sample in problem for problem in problems)


def test_unexplained_absence_remains_failed() -> None:
    """A non-Red-Team absence with no evidence stays failed beside disabled rows."""
    from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

    sample = next(iter(REDTEAM_TOOL_NAMES))
    canonical = [sample, "ordinary-tool", "sys_cache"]
    offered = ["sys_cache"]
    config = {"redteam": {"enabled": False}}
    disabled = lv._redteam_disabled_evidence(config, offered)
    rows, problems = lv.account_roster(
        canonical=canonical,
        offered=offered,
        evidence={"sys_cache": ["check:x"]},
        disabled=disabled,
        blocked={},
        family_of=_redteam_family,
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name[sample]["status"] == "feature_disabled"
    assert by_name["ordinary-tool"]["status"] == "failed"
    assert any("ordinary-tool" in problem for problem in problems)


def test_publication_canaries_blocked_from_evidence() -> None:
    recognized = "xai-" + "aB3dE5fG7hJ9kL2mN4pQ6"
    assignment = "api_secret=Sup3rS3cretValue99"
    cross_tail = "x" * 186 + "omk_" + "qW8eR4tY2uI6oP0aS3dF"
    for canary in (recognized, assignment):
        assert redact_sensitive(f"evidence {canary} end") != f"evidence {canary} end"
        out = lv._curate(f"evidence {canary} end", redact_sensitive)
        assert canary not in out
    out = lv._curate(f"prefix {cross_tail}", redact_sensitive, limit=200)
    assert "omk_" not in out
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBmock\n-----END RSA PRIVATE KEY-----"
    with pytest.raises(SecretLeakError):
        assert_no_secrets(f"bundle {pem}", "probe")
    with pytest.raises(SecretLeakError):
        assert_no_secrets(f"bundle {recognized}", "probe")


_SPANNING_TOKEN = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY2zZ4"
"""Scanner-only generic token: audit-clean, high-entropy, mixed classes."""


def _spanning_diagnostic() -> tuple[str, str]:
    """A diagnostic whose generic token spans the 200-char clip window.

    Padding uses ``q `` pairs (never a 32+ token-alphabet run) so the only
    scanner candidate is the token itself; ``x``-style padding would merge
    and dilute its entropy below the gate threshold.
    """
    full = "diagnostic " + "q " * 87 + _SPANNING_TOKEN + " tail"
    assert full.index(_SPANNING_TOKEN) < 200 < full.index(_SPANNING_TOKEN) + 32
    return full, _SPANNING_TOKEN


def test_curate_withholds_spanning_generic_token() -> None:
    """A scanner-only token split by the clip window never publishes."""
    full, token = _spanning_diagnostic()
    assert redact_sensitive(full) == full
    with pytest.raises(SecretLeakError):
        assert_no_secrets(full, "probe")
    out = lv._curate(full, redact_sensitive)
    assert token not in out
    assert token[:12] not in out
    assert token[-12:] not in out


def test_probe_call_withholds_spanning_secret(tmp_path: Path) -> None:
    """The probe failure path gates the full reason before curating."""
    import types

    full, token = _spanning_diagnostic()
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
    probe = lv.ReadinessProbe(layout)
    probe.attach(
        types.SimpleNamespace(
            execute_tool=lambda name, args: {"status": "error", "error": full},
            registry=types.SimpleNamespace(),
        )
    )
    with pytest.raises(lv.ReadinessFailure) as excinfo:
        probe.call("write_file", {"path": "x", "content": "y"})
    assert token[:12] not in str(excinfo.value)


def test_alias_to_excluded_state_is_rejected(tmp_path: Path) -> None:
    """An alias to a pruned state target is refused before any copy."""
    src = tmp_path / "src"
    _minimal_source(src)
    (src / "omega_prime" / "tools").mkdir(parents=True)
    (src / "omega_prime" / "state").mkdir(parents=True)
    (src / "omega_prime" / "state" / "owned-canary.py").write_text("# owned\n")
    (src / "omega_prime" / "tools" / "owned-alias.py").symlink_to(
        "../state/owned-canary.py"
    )
    before = _bases()
    with pytest.raises(lv.IsolationError), lv.isolated_layout(src):
        pass
    assert _bases() == before
    assert (src / "omega_prime" / "state" / "owned-canary.py").is_file()
    assert (src / "omega_prime" / "tools" / "owned-alias.py").is_symlink()


def test_alias_to_learned_helper_is_rejected(tmp_path: Path) -> None:
    """An alias into unvetted learned skill code cannot bypass membership."""
    src = tmp_path / "src"
    _minimal_source(src)
    learned = src / "omega_prime" / "skills" / "owned-learned"
    learned.mkdir(parents=True)
    (learned / "helper.py").write_text("# owned learned helper\n")
    (src / "omega_prime" / "tools").mkdir(parents=True)
    (src / "omega_prime" / "tools" / "owned-helper-alias.py").symlink_to(
        "../skills/owned-learned/helper.py"
    )
    before = _bases()
    with pytest.raises(lv.IsolationError), lv.isolated_layout(src):
        pass
    assert _bases() == before
    assert (learned / "helper.py").is_file()


def test_safe_product_alias_still_copies(tmp_path: Path) -> None:
    """Fresh product Python stays exportable, including via safe aliases."""
    src = tmp_path / "src"
    _minimal_source(src)
    tools = src / "omega_prime" / "tools"
    tools.mkdir(parents=True)
    (tools / "_owned_new.py").write_text("# newly implemented product code\n")
    (tools / "safe-alias.py").symlink_to("_owned_new.py")
    with lv.isolated_layout(src) as layout:
        assert (layout.install / "omega_prime" / "tools" / "_owned_new.py").is_file()
        alias = layout.install / "omega_prime" / "tools" / "safe-alias.py"
        assert alias.is_file() and not alias.is_symlink()
        assert alias.read_text(encoding="utf-8") == "# newly implemented product code\n"


@pytest.mark.parametrize("inventory_body", [None, "not json{{"])
def test_unvetted_learned_refused_without_provenance(
    tmp_path: Path, inventory_body: str | None
) -> None:
    """Missing/malformed inventory refuses learned code/data, not new code."""
    src = tmp_path / "src"
    _minimal_source(src)
    learned = src / "omega_prime" / "skills" / "owned-learned"
    learned.mkdir(parents=True)
    (learned / "SKILL.md").write_text("# owned learned\n")
    (learned / "helper.py").write_text("# owned learned helper\n")
    tools = src / "omega_prime" / "tools"
    tools.mkdir(parents=True)
    (tools / "_owned_new.py").write_text("# newly implemented product code\n")
    if inventory_body is not None:
        (src / ".omega-readiness-inventory.json").write_text(inventory_body)
    with lv.isolated_layout(src) as layout:
        assert not (
            layout.install / "omega_prime" / "skills" / "owned-learned" / "SKILL.md"
        ).exists()
        assert not (
            layout.install / "omega_prime" / "skills" / "owned-learned" / "helper.py"
        ).exists()
        assert (layout.install / "omega_prime" / "tools" / "_owned_new.py").is_file()
        assert (layout.install / "omega_prime" / "__init__.py").is_file()


def test_prime_private_regular_file_omitted(tmp_path: Path) -> None:
    """Optional Prime trees filter per entry; private files never enter."""
    src = tmp_path / "src"
    _minimal_source(src)
    stock = src / "prime-agent" / "skills" / "owned-stock"
    stock.mkdir(parents=True)
    (stock / "SKILL.md").write_text("# owned stock skill\n")
    private = src / "prime-agent" / "skills" / "owned-private"
    private.mkdir(parents=True)
    (private / ".env").write_text("OWNED_SECRET=1\n")
    (src / ".omega-readiness-inventory.json").write_text(
        json.dumps(["prime-agent/skills/owned-stock/SKILL.md"])
    )
    with lv.isolated_layout(src, include_prime=True) as layout:
        assert (
            layout.install / "prime-agent" / "skills" / "owned-stock" / "SKILL.md"
        ).is_file()
        assert not (
            layout.install / "prime-agent" / "skills" / "owned-private" / ".env"
        ).exists()
        assert not (
            layout.install / "prime-agent" / "skills" / "owned-private"
        ).exists()
        assert set(layout.prime_missing) == {
            "prime-agent/Cargo.toml",
            "prime-agent/LICENSE",
            "prime-agent/target/debug/libomega_prime_prime.so",
            "prime-agent/prime-agent-runtime/src",
        }


def test_regular_credential_python_omitted_and_uninventoried(
    tmp_path: Path,
) -> None:
    """Fresh credential-named regular Python fails closed without membership."""
    src = tmp_path / "src"
    _minimal_source(src)
    tools = src / "omega_prime" / "tools"
    tools.mkdir(parents=True)
    (tools / "owned_credentials.py").write_text("# owned credential canary\n")
    (tools / "_owned_new.py").write_text("# newly implemented product code\n")
    with lv.isolated_layout(src) as layout:
        assert not (
            layout.install / "omega_prime" / "tools" / "owned_credentials.py"
        ).exists()
        assert (layout.install / "omega_prime" / "tools" / "_owned_new.py").is_file()
        inventory = json.loads(
            (layout.install / ".omega-readiness-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        assert "omega_prime/tools/owned_credentials.py" not in inventory
        assert "omega_prime/tools/_owned_new.py" in inventory


def test_excluded_ancestor_python_omitted_and_uninventoried(
    tmp_path: Path,
) -> None:
    """Python beneath an excluded .planning ancestor never exports."""
    src = tmp_path / "src"
    _minimal_source(src)
    nested = src / "omega_prime" / "tools" / ".planning"
    nested.mkdir(parents=True)
    (nested / "owned.py").write_text("# owned excluded-ancestor canary\n")
    (src / "omega_prime" / "tools" / "_owned_new.py").write_text(
        "# newly implemented product code\n"
    )
    with lv.isolated_layout(src) as layout:
        assert not (
            layout.install / "omega_prime" / "tools" / ".planning" / "owned.py"
        ).exists()
        assert not (layout.install / "omega_prime" / "tools" / ".planning").exists()
        assert (layout.install / "omega_prime" / "tools" / "_owned_new.py").is_file()
        inventory = json.loads(
            (layout.install / ".omega-readiness-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        assert "omega_prime/tools/.planning/owned.py" not in inventory


def test_nested_export_cannot_bless_unsafe_regulars() -> None:
    """A nested re-export drops planted credential/excluded-ancestor Python."""
    with lv.isolated_layout(ROOT) as outer:
        install = outer.install
        outer_inventory = json.loads(
            (install / ".omega-readiness-inventory.json").read_text(encoding="utf-8")
        )
        assert "omega_prime/grokbot/secret_guard.py" in outer_inventory
        (install / "omega_prime" / "tools" / "owned_credentials.py").write_text(
            "# planted\n", encoding="utf-8"
        )
        planted_dir = install / "omega_prime" / "tools" / ".planning"
        planted_dir.mkdir(parents=True, exist_ok=True)
        (planted_dir / "owned.py").write_text("# planted\n", encoding="utf-8")
        with lv.isolated_layout(install) as inner:
            assert not (
                inner.install / "omega_prime" / "tools" / "owned_credentials.py"
            ).exists()
            assert not (
                inner.install / "omega_prime" / "tools" / ".planning" / "owned.py"
            ).exists()
            inner_inventory = json.loads(
                (inner.install / ".omega-readiness-inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            assert "omega_prime/tools/owned_credentials.py" not in inner_inventory
            assert "omega_prime/tools/.planning/owned.py" not in inner_inventory
            assert (
                inner.install / "omega_prime" / "grokbot" / "secret_guard.py"
            ).is_file()
            assert "omega_prime/grokbot/secret_guard.py" in inner_inventory


def test_legitimate_private_named_product_modules_survive() -> None:
    """Vetted private-named stock modules still ship with real code and tests."""
    expected = (
        "omega_prime/grokbot/secret_guard.py",
        "omega_prime/grokbot/tokens.py",
        "omega_prime/tests/test_credentials.py",
        "omega_prime/tests/test_grokbot_secret_guard.py",
        "omega_prime/tests/test_grokbot_tokens.py",
    )
    with lv.isolated_layout(ROOT) as layout:
        inventory = json.loads(
            (layout.install / ".omega-readiness-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        for rel in expected:
            assert (layout.install / rel).is_file(), rel
            assert rel in inventory, rel


def test_failed_scenario_module_records_failed_row(tmp_path: Path) -> None:
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
    probe = lv.ReadinessProbe(layout)
    lv._run_scenario_module(probe, "omega_prime.grokbot.readiness_nonexistent_xyz")
    assert probe.checks and probe.checks[-1]["status"] == "failed"

    import types

    module = types.ModuleType("omega_prime.grokbot.readiness_fake_xyz")

    def _run(_probe: lv.ReadinessProbe) -> None:
        raise lv.ReadinessFailure("synthetic eligible failure")

    module.run = _run  # type: ignore[attr-defined]
    sys.modules[module.__name__] = module
    try:
        lv._run_scenario_module(probe, module.__name__)
    finally:
        del sys.modules[module.__name__]
    assert probe.checks[-1]["status"] == "failed"
    assert "synthetic eligible failure" in probe.checks[-1]["detail"]


def test_validate_report_rejects_gaps() -> None:
    good: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "scope": "isolated-local",
        "checks": [],
        "tools": [
            {
                "name": "a-tool",
                "family": "Fam",
                "status": "exercised",
                "evidence": ["check:x"],
                "prerequisites": [],
            }
        ],
        "summary": {},
        "limitations": [],
    }
    assert cli.validate_report(good, ["a-tool"])["ok"] is True
    with pytest.raises(ValueError):
        cli.validate_report(dict(good, tools=[]), ["a-tool"])
    dup = dict(good, tools=[good["tools"][0], good["tools"][0]])
    with pytest.raises(ValueError):
        cli.validate_report(dup, ["a-tool"])
    with pytest.raises(ValueError):
        cli.validate_report(dict(good, scope="elsewhere"), ["a-tool"])


def test_validate_report_rejects_malformed_summary_and_checks() -> None:
    """Child-derived summary/checks shapes fail validation, never crash."""
    tool = {
        "name": "a-tool",
        "family": "Fam",
        "status": "exercised",
        "evidence": ["check:x"],
        "prerequisites": [],
    }
    good: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "scope": "isolated-local",
        "checks": [],
        "tools": [tool],
        "summary": {},
        "limitations": [],
    }
    for bad_summary in ([], "total", None, 0):
        with pytest.raises(ValueError):
            cli.validate_report(dict(good, summary=bad_summary), ["a-tool"])
    for bad_checks in ({}, "none", None, ["ok"], [None], [[]]):
        with pytest.raises(ValueError):
            cli.validate_report(dict(good, checks=bad_checks), ["a-tool"])


def test_cli_rejects_bad_root(tmp_path: Path) -> None:
    assert cli.main(["--root", str(tmp_path / "nope"), "--json"]) == 2


def test_missing_prime_resources_become_named_prerequisites(tmp_path: Path) -> None:
    """Owned fixture without prime-agent: absence is named, never a failure."""
    src = tmp_path / "src"
    _minimal_source(src)
    with lv.isolated_layout(src, include_prime=True) as layout:
        assert set(layout.prime_missing) == {
            "prime-agent/Cargo.toml",
            "prime-agent/LICENSE",
            "prime-agent/target/debug/libomega_prime_prime.so",
            "prime-agent/prime-agent-runtime/src",
            "prime-agent/skills",
        }
        assert (layout.install / ".omega-readiness-inventory.json").is_file()


def test_cli_full_local_profile(sentinel_env: dict[str, str], tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "omega_prime.grokbot.readiness",
            "--root",
            str(ROOT),
            "--json",
            "--output",
            str(report_path),
        ],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr.decode()[-500:]
    text = completed.stdout.decode("utf-8")
    report = json.loads(text)
    assert report["schema_version"] == 1
    assert report["scope"] == "isolated-local"
    canonical = load_roster_names(ROOT / cli.ROSTER_REL)
    assert sorted(row["name"] for row in report["tools"]) == sorted(canonical)
    assert len({row["name"] for row in report["tools"]}) == len(canonical)
    for sentinel in sentinel_env.values():
        assert sentinel not in text
    assert report["ok"] is True
    assert [c for c in report["checks"] if c.get("status") == "failed"] == []
    assert [r for r in report["tools"] if r.get("status") == "failed"] == []
    for row in report["tools"]:
        if row.get("status") == "blocked":
            assert row.get("prerequisites"), row.get("name")
    names = {check["name"] for check in report["checks"]}
    assert "prime-off:absent-and-refused" in names
    for expected in (
        "loopback:manifest-agrees",
        "loopback:sse:file-roundtrip",
        "loopback:http:file-roundtrip",
        "loopback:sse:approval-lifecycle",
        "loopback:http:approval-lifecycle",
        "loopback:audit-sanitized",
        "loopback:clean-shutdown",
    ):
        assert expected in names, expected
    assert report_path.is_file()
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_cli_negative_owned_read_file_failure(
    sentinel_env: dict[str, str], tmp_path: Path
) -> None:
    """Real CLI failure propagation for an eligible owned read_file error.

    The owned export's copied ``readiness_local`` scenario creates an owned
    directory under disposable WORK and dispatches the real
    ``probe.call('read_file', {'path': owned-relative-path})`` against it, so
    production file dispatch fails (a directory is not a readable file). The
    actual readiness CLI runs in a real subprocess against the owned install.
    """
    owned_report = tmp_path / "negative-report.json"
    root_scenario = ROOT / "omega_prime" / "grokbot" / "readiness_local.py"
    root_bytes_before = root_scenario.read_bytes()
    with lv.isolated_layout(ROOT) as layout:
        owned_install = layout.install
        assert owned_install.is_relative_to(layout.base)
        assert owned_install != ROOT
        scenario = owned_install / "omega_prime" / "grokbot" / "readiness_local.py"
        original = scenario.read_text(encoding="utf-8")
        fault = (
            "    from pathlib import Path as _negative_path\n"
            '    _negative_dir = _negative_path(probe.layout.work) / "readiness-75-05" / "negative-is-dir-75-05"\n'
            "    _negative_dir.mkdir(parents=True, exist_ok=True)\n"
            '    probe.call("read_file", {"path": "readiness-75-05/negative-is-dir-75-05"})\n'
        )
        mutated = original.replace("    _files(probe)", fault + "    _files(probe)", 1)
        scenario.write_text(mutated, encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "omega_prime.grokbot.readiness",
                "--root",
                str(owned_install),
                "--json",
                "--output",
                str(owned_report),
            ],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=600,
        )
        assert completed.returncode == 1, completed.stderr.decode()[-500:]
        text = completed.stdout.decode("utf-8")
        report = json.loads(text)
        assert report["schema_version"] == 1
        assert report["scope"] == "isolated-local"
        canonical = load_roster_names(owned_install / cli.ROSTER_REL)
        assert sorted(row["name"] for row in report["tools"]) == sorted(canonical)
        assert len({row["name"] for row in report["tools"]}) == len(canonical)
        failed_checks = [c for c in report["checks"] if c.get("status") == "failed"]
        assert failed_checks
        failed_tools = [r for r in report["tools"] if r.get("status") == "failed"]
        assert failed_tools
        assert report["ok"] is False
        assert any(
            "read_file" in str(check.get("detail", "")) for check in failed_checks
        )
        by_name = {row["name"]: row for row in report["tools"]}
        # Later successful transport/workflow calls may prove this tool
        # exercised; they must never erase the retained failed check or exit1.
        assert by_name["read_file"]["status"] in ("failed", "exercised")
        assert by_name["read_file"]["evidence"]
        assert by_name["read_file"]["status"] not in ("blocked", "feature_disabled")
        for row in report["tools"]:
            if row.get("status") == "blocked":
                assert row.get("prerequisites"), row.get("name")
        assert owned_report.is_file()
        assert stat.S_IMODE(owned_report.stat().st_mode) == 0o600
        assert json.loads(owned_report.read_text(encoding="utf-8")) == report
        for sentinel in sentinel_env.values():
            assert sentinel not in text
        assert root_scenario.read_bytes() == root_bytes_before
    assert not layout.base.exists()
    assert root_scenario.read_bytes() == root_bytes_before

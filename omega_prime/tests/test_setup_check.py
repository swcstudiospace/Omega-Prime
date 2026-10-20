"""Phase 37: setup smoke check passes and trips per check."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from omega_prime import setup_check
from omega_prime.setup_check import (
    CHECKS,
    check_plugin,
    check_substrate,
    check_template,
    main,
    run_checks,
)

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


@pytest.fixture(autouse=True)
def _isolated_settings_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMEGA_PRIME_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("OMEGA_PRIME_STATE_DIR", str(tmp_path / "state"))


def _mirror(root: Path) -> Path:
    """Copy the repo's omega_prime/ tree into a fixture root."""
    dest = root / "omega_prime"
    shutil.copytree(OMEGA_PRIME, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return root


# Repo-level generator inputs and outputs that `_mirror` leaves out.
_PLUGIN_ROOT_FILES = ("pyproject.toml", "mcp.json", ".cursor-plugin")


def _plugin_tree(root: Path) -> Path:
    """`_mirror` plus the root files the plugin generator reads and writes."""
    _mirror(root)
    for rel in _PLUGIN_ROOT_FILES:
        source = ROOT / rel
        if source.is_dir():
            shutil.copytree(source, root / rel)
        else:
            shutil.copy2(source, root / rel)
    return root


def test_checks_pass_on_repo_root(monkeypatch, tmp_path: Path):
    # The required prime-agent submodule is external checkout state. Its
    # missing-check behavior is tested separately; keep this repo smoke test
    # independent of whether the test runner initialized submodules.
    monkeypatch.setattr(
        setup_check,
        "CHECKS",
        tuple(
            (
                name,
                (lambda _root: (True, "submodule fixture"))
                if name == "submodules"
                else check,
            )
            for name, check in CHECKS
        ),
    )
    from omega_prime.substrate.client import SubstrateClient

    def no_network(*args, **kwargs):
        raise AssertionError("setup diagnostics must not contact substrate")

    monkeypatch.setattr(SubstrateClient, "_mcp_post", no_network)
    for name in (
        "SUBSTRATE_URL",
        "SUBSTRATE_TOKEN",
        "SUBSTRATE_TOKEN_GROK_BOT",
        "HINDSIGHT_URL",
        "HINDSIGHT_API_KEY",
        "HINDSIGHT_API_TOKEN",
        "ULTRATHINK_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUBSTRATE_URL", "https://substrate.example")
    report = run_checks(ROOT)
    assert report["passed"] is True
    assert report["roster"]["ok"] is True
    assert report["template"]["ok"] is True
    assert report["assembly"]["ok"] is True
    assert report["plugin"]["ok"] is True
    assert "up to date" in report["plugin"]["detail"]
    assert report["registry"]["ok"] is True
    assert report["ultrathink"]["ok"] is None
    assert report["substrate"]["ok"] is True
    assert "configured-only" in report["substrate"]["detail"]
    assert "reachability not checked" in report["substrate"]["detail"]
    assert "anonymous mode" in report["substrate"]["detail"]
    assert main(["--root", str(ROOT)]) == 0


def test_each_failure_trips_its_check(tmp_path: Path, monkeypatch):
    fixture = _mirror(tmp_path / "bad")

    roster_path = fixture / "omega_prime" / "grokbot" / "rosters" / "default.json"
    roster_path.write_text("{broken", encoding="utf-8")
    report = run_checks(fixture)
    assert report["passed"] is False
    assert report["roster"]["ok"] is False

    roster = {
        "bot_id": "x",
        "default_branch": "main",
        "roster_version": "1",
        "skills_dir": "skills",
        "prompts_dir": "prompts",
        "routines_dir": "routines",
        "tool_roster": "contracts/tool-rosters/omega-prime.yaml",
    }
    roster_path.write_text(json.dumps(roster), encoding="utf-8")
    template = fixture / "omega_prime" / "grokbot" / "templates" / "OMEGA_PRIME.md"
    text = template.read_text(encoding="utf-8")
    assert "## Enabled skills\n" in text
    assert "## Routines\n" in text
    template.write_text(
        text.replace(
            "## Enabled skills\n",
            "## Enabled skills\n\n- ghost-skill\n",
            1,
        ),
        encoding="utf-8",
    )
    assert run_checks(fixture)["template"]["ok"] is False

    shutil.rmtree(fixture / "omega_prime")
    _mirror(fixture)
    (fixture / "omega_prime" / "prompts-assembled" / "OMEGA_PRIME.xml").write_text(
        "stale", encoding="utf-8"
    )
    assert run_checks(fixture)["assembly"]["ok"] is False

    shutil.rmtree(fixture / "omega_prime")
    _mirror(fixture)
    (
        fixture / "omega_prime" / "contracts" / "tool-rosters" / "omega-prime.yaml"
    ).write_text("version: 1\ntools:\n", encoding="utf-8")
    assert run_checks(fixture)["registry"]["ok"] is False

    monkeypatch.setenv("ULTRATHINK_ROOT", str(tmp_path / "nowhere"))
    assert run_checks(fixture)["ultrathink"]["ok"] is False

    monkeypatch.setenv("SUBSTRATE_URL", "gopher://x")
    assert run_checks(fixture)["substrate"]["ok"] is False
    monkeypatch.delenv("SUBSTRATE_URL")
    monkeypatch.setenv("SUBSTRATE_TOKEN", "fake-value")
    substrate = run_checks(fixture)["substrate"]
    assert substrate["ok"] is False
    assert "SUBSTRATE_URL" in substrate["detail"]
    assert "fake-value" not in substrate["detail"]


def test_missing_substrate_url_fails_with_actionable_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUBSTRATE_URL", raising=False)
    monkeypatch.delenv("SUBSTRATE_TOKEN", raising=False)
    monkeypatch.delenv("SUBSTRATE_TOKEN_GROK_BOT", raising=False)
    ok, detail = check_substrate(tmp_path)
    assert ok is False
    assert "SUBSTRATE_URL" in detail
    assert "process environment or settings file" in detail


def test_run_checks_uses_settings_file_for_ultrathink_and_substrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _mirror(tmp_path / "configured")
    checkout = tmp_path / "ultrathink"
    executable = checkout / "bin" / "ultrathink-ship"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "ULTRATHINK_ROOT": str(checkout),
                "SUBSTRATE_URL": "https://substrate.example",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OMEGA_PRIME_SETTINGS_FILE", str(settings_file))
    monkeypatch.delenv("ULTRATHINK_ROOT", raising=False)
    monkeypatch.delenv("SUBSTRATE_URL", raising=False)

    original_load_settings = setup_check.load_settings
    load_calls = 0

    def tracked_load_settings(process_env):
        nonlocal load_calls
        load_calls += 1
        return original_load_settings(process_env)

    monkeypatch.setattr(setup_check, "load_settings", tracked_load_settings)
    report = run_checks(fixture)

    assert load_calls == 1
    assert report["ultrathink"]["ok"] is True
    assert str(checkout) in report["ultrathink"]["detail"]
    assert report["substrate"]["ok"] is True
    assert "configured-only" in report["substrate"]["detail"]


def test_run_checks_reports_settings_file_invalid_ultrathink_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _mirror(tmp_path / "invalid-configured")
    checkout = tmp_path / "missing-ultrathink"
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"ULTRATHINK_ROOT": str(checkout)}), encoding="utf-8"
    )
    monkeypatch.setenv("OMEGA_PRIME_SETTINGS_FILE", str(settings_file))
    monkeypatch.delenv("ULTRATHINK_ROOT", raising=False)

    report = run_checks(fixture)

    assert report["ultrathink"]["ok"] is False
    assert str(checkout) in report["ultrathink"]["detail"]
    assert "root not set" not in report["ultrathink"]["detail"]


def test_run_checks_contains_settings_load_error_and_runs_every_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = tuple(name for name, _ in CHECKS)
    monkeypatch.setattr(
        setup_check,
        "CHECKS",
        tuple((name, lambda _root: (True, "ran")) for name in names),
    )
    monkeypatch.setattr(
        setup_check,
        "load_settings",
        lambda _env: (_ for _ in ()).throw(ValueError("bad optional settings path")),
    )
    monkeypatch.delenv("ULTRATHINK_ROOT", raising=False)

    report = run_checks(tmp_path)

    assert set(report) == {*names, "passed"}
    assert report["passed"] is False
    assert report["substrate"]["ok"] is False
    assert "settings load failed: ValueError" in report["substrate"]["detail"]
    assert report["roster"] == {"ok": True, "detail": "ran"}
    assert report["ultrathink"]["ok"] is None


def test_template_row_reports_unlisted_skills_like_the_doctor(tmp_path: Path) -> None:
    """IN-04: the row covers both directions, not just template gaps."""
    fixture = _mirror(tmp_path / "unlisted")
    skill = fixture / "omega_prime" / "skills" / "zz-unlisted"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# zz-unlisted\n", encoding="utf-8")
    ok, detail = check_template(fixture)
    assert ok is False
    assert "zz-unlisted" in detail


def test_plugin_row_is_function_backed() -> None:
    assert ("plugin", check_plugin) in CHECKS


def test_plugin_check_runs_in_process(tmp_path: Path, monkeypatch) -> None:
    # No `.git` in the fixture, so the generator's optional `git ls-files`
    # never runs; any spawn here would be the row shelling out.
    fixture = _plugin_tree(tmp_path / "clean")

    def no_spawn(*args, **kwargs):
        raise AssertionError("setup_check plugin row must not spawn a process")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    ok, detail = check_plugin(fixture)
    assert ok is True, detail
    assert "up to date" in detail


COMMANDS = "omega_prime/grokbot/plugin/commands"


def _write(rel: str, text: str) -> Callable[[Path], object]:
    return lambda root: (root / rel).write_text(text, encoding="utf-8")


def _remove(rel: str) -> Callable[[Path], object]:
    return lambda root: (root / rel).unlink()


@pytest.mark.parametrize(
    ("drift", "expected"),
    [
        pytest.param(
            _write(f"{COMMANDS}/omega-help.md", "hand edited\n"),
            f"{COMMANDS}/omega-help.md is stale",
            id="edited-command",
        ),
        pytest.param(
            _write(f"{COMMANDS}/omega-x.md", "orphan\n"),
            f"{COMMANDS}/omega-x.md is an orphan",
            id="orphan-command",
        ),
        pytest.param(
            _remove("mcp.json"),
            "mcp.json is missing",
            id="missing-mcp-json",
        ),
        pytest.param(
            _write(".cursor-plugin/plugin.json", "{}\n"),
            ".cursor-plugin/plugin.json is stale",
            id="stale-manifest",
        ),
        pytest.param(
            _remove("pyproject.toml"),
            "plugin generation failed: pyproject.toml",
            id="generation-failure",
        ),
    ],
)
def test_plugin_drift_fails_the_plugin_row(
    tmp_path: Path, drift: Callable[[Path], object], expected: str
) -> None:
    fixture = _plugin_tree(tmp_path / "drift")
    drift(fixture)
    ok, detail = check_plugin(fixture)
    assert ok is False
    assert expected in detail


def test_plugin_drift_fails_setup_check(tmp_path: Path, capsys) -> None:
    fixture = _plugin_tree(tmp_path / "drift")
    _write(f"{COMMANDS}/omega-help.md", "hand edited\n")(fixture)
    report = run_checks(fixture)
    assert report["plugin"]["ok"] is False
    detail = report["plugin"]["detail"]
    assert "rerun python -m omega_prime.tooling.plugin --write" in detail
    assert report["passed"] is False
    assert main(["--root", str(fixture)]) == 1
    assert "[FAIL] plugin: plugin set is stale" in capsys.readouterr().out

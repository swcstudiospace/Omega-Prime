"""Phase 37: setup smoke check passes and trips per check."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from omega_prime.setup_check import CHECKS, check_plugin, main, run_checks

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


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


def test_checks_pass_on_repo_root(monkeypatch):
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
    report = run_checks(ROOT)
    assert report["passed"] is True
    assert report["roster"]["ok"] is True
    assert report["template"]["ok"] is True
    assert report["assembly"]["ok"] is True
    assert report["plugin"]["ok"] is True
    assert "up to date" in report["plugin"]["detail"]
    assert report["registry"]["ok"] is True
    assert report["ultrathink"]["ok"] is None
    assert report["substrate"]["ok"] is None
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
    assert substrate["ok"] is True
    assert "fake-value" not in substrate["detail"]
    assert "SUBSTRATE_TOKEN" in substrate["detail"]


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

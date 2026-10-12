"""Tests for the Grok Bot template lint (template and disk agree both ways)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from omega_prime.grokbot.manifest import TemplateLint, find_repo_root, lint_template

TEMPLATE = """# Test

## Name

Test

## Enabled skills

- alpha
- beta
- ../escape

## Routines

- one — does the first thing — with a second dash
- two
- three — also missing
"""


def _tree(base: Path, *, template: str = TEMPLATE) -> Path:
    pkg = base / "omega_prime"
    (pkg / "grokbot" / "templates").mkdir(parents=True)
    (pkg / "grokbot" / "templates" / "OMEGA_PRIME.md").write_text(
        template, encoding="utf-8"
    )
    (pkg / "skills" / "alpha").mkdir(parents=True)
    (pkg / "skills" / "alpha" / "SKILL.md").write_text("# alpha\n", encoding="utf-8")
    (pkg / "routines").mkdir()
    (pkg / "routines" / "one.md").write_text("# one\n", encoding="utf-8")
    return base


def test_missing_skills_and_routines_are_listed(tmp_path: Path):
    lint = lint_template(_tree(tmp_path))
    assert lint == TemplateLint(
        missing_skills=["beta", "../escape"], missing_routines=["two", "three"]
    )
    assert lint.ok is False


def test_routine_name_is_the_text_before_the_first_em_dash(tmp_path: Path):
    lint = lint_template(_tree(tmp_path))
    assert "one" not in lint.missing_routines
    assert not any("—" in name for name in lint.missing_routines)


def test_directory_without_skill_file_is_missing(tmp_path: Path):
    root = _tree(tmp_path)
    (root / "omega_prime" / "skills" / "beta").mkdir()
    assert "beta" in lint_template(root).missing_skills


COMPLETE = "# T\n\n## Enabled skills\n\n- alpha\n\n## Routines\n\n- one — first\n"


def test_complete_tree_is_ok(tmp_path: Path):
    root = _tree(tmp_path, template=COMPLETE)
    lint = lint_template(root)
    assert lint.ok is True
    assert lint.missing_skills == []
    assert lint.missing_routines == []
    assert lint.unlisted_skills == []
    assert lint.unlisted_routines == []


def test_skill_and_routine_on_disk_but_not_listed_are_unlisted(tmp_path: Path):
    root = _tree(tmp_path, template=COMPLETE)
    pkg = root / "omega_prime"
    (pkg / "skills" / "gamma").mkdir()
    (pkg / "skills" / "gamma" / "SKILL.md").write_text("# gamma\n", encoding="utf-8")
    (pkg / "routines" / "four.md").write_text("# four\n", encoding="utf-8")
    lint = lint_template(root)
    assert lint == TemplateLint(unlisted_skills=["gamma"], unlisted_routines=["four"])
    assert lint.ok is False


def test_both_directions_are_reported_together(tmp_path: Path):
    root = _tree(tmp_path)
    (root / "omega_prime" / "skills" / "gamma").mkdir()
    (root / "omega_prime" / "skills" / "gamma" / "SKILL.md").write_text(
        "# gamma\n", encoding="utf-8"
    )
    lint = lint_template(root)
    assert lint.missing_skills == ["beta", "../escape"]
    assert lint.missing_routines == ["two", "three"]
    assert lint.unlisted_skills == ["gamma"]
    assert lint.ok is False


def test_empty_directory_and_non_routine_files_are_not_unlisted(tmp_path: Path):
    root = _tree(tmp_path, template=COMPLETE)
    pkg = root / "omega_prime"
    (pkg / "skills" / "delta").mkdir()
    (pkg / "routines" / "helper.py").write_text("", encoding="utf-8")
    lint = lint_template(root)
    assert lint.unlisted_skills == []
    assert lint.unlisted_routines == []
    assert lint.ok is True


@pytest.mark.skipif(shutil.which("git") is None, reason="needs a git client")
def test_untracked_skill_in_a_checkout_is_not_unlisted(tmp_path: Path):
    root = _tree(tmp_path, template=COMPLETE)
    for argv in (["init", "-q"], ["add", "omega_prime"]):
        subprocess.run(["git", "-C", str(root), *argv], check=True)
    learned = root / "omega_prime" / "skills" / "learned"
    learned.mkdir()
    (learned / "SKILL.md").write_text("# learned\n", encoding="utf-8")
    lint = lint_template(root)
    assert lint.unlisted_skills == []
    assert lint.ok is True


def test_missing_template_is_ok_and_empty(tmp_path: Path):
    lint = lint_template(tmp_path)
    assert lint.ok is True


def test_shipped_template_matches_checked_in_skills_and_routines():
    lint = lint_template(find_repo_root())
    assert lint == TemplateLint()
    assert lint.ok is True

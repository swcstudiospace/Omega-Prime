"""Phase 77.1: the generated plugin manifest set, its drift check and verifier.

Expected values come from the checked-in sources (skills, routines, curated
``COMMANDS``, ``pyproject.toml`` and the RESEARCH collision table), never from
the generated output under test.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from omega_prime.commands import COMMANDS
from omega_prime.commands.generated import checked_in_routines, checked_in_skills
from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets
from omega_prime.tooling import plugin
from omega_prime.tooling.plugin import (
    COMMANDS_DIR,
    HTTPS_EXAMPLE,
    HTTPS_TOKEN_VAR,
    MARKETPLACE,
    MCP_JSON,
    PLUGIN_MANIFEST,
    PluginEnvError,
    PluginError,
    main,
    render,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = "omega_prime/grokbot/templates/OMEGA_PRIME.md"
LAUNCHER = "omega_prime/scripts/grokbot-1click.sh"
ROSTER = "omega_prime/contracts/tool-rosters/omega-prime.yaml"
SKILLS = checked_in_skills(ROOT)
ROUTINES = checked_in_routines(ROOT)

# RESEARCH R-2: the 12 curated names, in catalog order.
CURATED = [
    "omega-help",
    "omega-doctor",
    "omega-roster",
    "omega-recall",
    "omega-retain",
    "omega-gates",
    "omega-python",
    "omega-delegate",
    "omega-workflow",
    "omega-onboard",
    "omega-connectors",
    "omega-desk",
]
# RESEARCH §Generation rule, applied table: exactly these take a suffix.
SUFFIXED = {
    ("skill", "python"): "omega-python-skill",
    ("routine", "onboard"): "omega-onboard-routine",
    ("routine", "connectors"): "omega-connectors-routine",
    ("skill", "ultrathink"): "omega-ultrathink-skill",
    ("routine", "ultrathink"): "omega-ultrathink-routine",
}
EXPECTED_ARGS = [
    "${CURSOR_PLUGIN_ROOT}/omega_prime/scripts/grokbot-1click.sh",
    "--bootstrap",
    "--transport",
    "stdio",
]
PLACEHOLDER = re.compile(r"^Bearer \$\{(env:)?[A-Z][A-Z0-9_]*\}$")
KEBAB = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
COMPONENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A bare high-entropy token the unchanged gate must catch; assembled at runtime.
TOKEN = "Zq7Xk2Lm" + "9Pw4Rt8V" + "b6Nc3Hd5" + "Jf1Gs0Ya" + "Ue7Ti"
BEARER_40 = "Bearer " + "a1B2c3D4" * 5


# --- helpers ----------------------------------------------------------------


def _frontmatter(text: str) -> dict[str, Any]:
    lines = text.splitlines(keepends=True)
    assert lines[0].strip() == "---"
    end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    mapping = yaml.safe_load("".join(lines[1:end]))
    assert isinstance(mapping, dict)
    return mapping


def _expected_names() -> set[str]:
    names = set(CURATED)
    for kind, stems in (("skill", SKILLS), ("routine", ROUTINES)):
        for stem in stems:
            base = "omega-" + stem.removeprefix("omega-")
            names.add(SUFFIXED.get((kind, stem), base))
    return names


def _copy_tree(tmp_path: Path) -> Path:
    """The checked-in inputs plus the committed outputs, without `.git`."""
    tree = tmp_path / "tree"
    files = [
        "pyproject.toml",
        MCP_JSON,
        MARKETPLACE,
        PLUGIN_MANIFEST,
        HTTPS_EXAMPLE,
        LAUNCHER,
        TEMPLATE,
        *(f"omega_prime/skills/{name}/SKILL.md" for name in SKILLS),
        *(f"omega_prime/routines/{name}.md" for name in ROUTINES),
        *(f"{COMMANDS_DIR}/{p.name}" for p in (ROOT / COMMANDS_DIR).glob("*.md")),
    ]
    for rel in files:
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    return tree


def _snapshot(tree: Path) -> dict[str, tuple[bytes, int, int, int]]:
    result: dict[str, tuple[bytes, int, int, int]] = {}
    for path in sorted(tree.rglob("*")):
        if path.is_file():
            info = path.stat()
            result[path.relative_to(tree).as_posix()] = (
                path.read_bytes(),
                info.st_mode,
                info.st_mtime_ns,
                info.st_ino,
            )
    return result


def _edit(tree: Path, rel: str, edit: Callable[[str], str]) -> None:
    path = tree / rel
    before = path.read_text(encoding="utf-8")
    after = edit(before)
    assert after != before, f"fixture edit did not change {rel}"
    path.write_text(after, encoding="utf-8")


def _edit_json(tree: Path, rel: str, edit: Callable[[dict[str, Any]], None]) -> None:
    def apply(text: str) -> str:
        document = json.loads(text)
        edit(document)
        return json.dumps(document, indent=2) + "\n"

    _edit(tree, rel, apply)


def _add_skill(tree: Path, folder: str) -> None:
    skill = tree / "omega_prime" / "skills" / folder / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        f"---\nname: {folder}\ndescription: Fixture skill {folder}.\n---\n"
        "\n# Fixture\n",
        encoding="utf-8",
    )
    _list_in_template(tree, "skills", folder)


def _list_in_template(tree: Path, section: str, entry: str) -> None:
    heading = {"skills": "## Enabled skills\n", "routines": "## Routines\n"}[section]
    _edit(
        tree, TEMPLATE, lambda text: text.replace(heading, f"{heading}\n- {entry}\n", 1)
    )


def _unlist_in_template(tree: Path, entry: str) -> None:
    def drop(text: str) -> str:
        kept = [
            line
            for line in text.splitlines(keepends=True)
            if line.strip().split(" — ", 1)[0] != f"- {entry}"
        ]
        return "".join(kept)

    _edit(tree, TEMPLATE, drop)


def _set_skill_line(tree: Path, folder: str, key: str, line: str) -> None:
    rel = f"omega_prime/skills/{folder}/SKILL.md"
    pattern = re.compile(rf"^{key}:.*$", re.MULTILINE)
    _edit(tree, rel, lambda text: pattern.sub(line, text, count=1))


def _server(document: dict[str, Any]) -> dict[str, Any]:
    return document["mcpServers"]["omega-prime"]


# --- manifest schema (R01, R02, R03) ----------------------------------------


def test_plugin_manifest_has_required_fields_and_existing_relative_paths() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    text = (ROOT / PLUGIN_MANIFEST).read_text(encoding="utf-8")
    manifest = json.loads(text)
    assert manifest["name"] == "omega-prime"
    assert KEBAB.fullmatch(manifest["name"])
    assert manifest["version"] == pyproject["project"]["version"]
    assert manifest["repository"] == pyproject["project"]["urls"]["Repository"]
    assert manifest["repository"] == "https://github.com/swcstudiospace/omes-bot"
    assert manifest["skills"] == "omega_prime/skills"
    assert manifest["commands"] == COMMANDS_DIR
    assert manifest["mcpServers"] == "mcp.json"
    for key in ("skills", "commands", "mcpServers"):
        value = manifest[key]
        assert not value.startswith("/") and ".." not in Path(value).parts
        assert (ROOT / value).exists(), key
    assert "mcp.https.example" not in text


def test_marketplace_lists_the_repo_root_plugin() -> None:
    marketplace = json.loads((ROOT / MARKETPLACE).read_text(encoding="utf-8"))
    assert KEBAB.fullmatch(marketplace["name"])
    assert marketplace["owner"]["name"].strip()
    assert marketplace["plugins"] == [
        {
            "name": "omega-prime",
            "source": "./",
            "description": marketplace["plugins"][0]["description"],
        }
    ]
    assert marketplace["plugins"][0]["description"].strip()


def test_mcp_json_declares_exactly_the_stdio_launcher() -> None:
    text = (ROOT / MCP_JSON).read_text(encoding="utf-8")
    document = json.loads(text)
    assert list(document) == ["mcpServers"]
    assert list(document["mcpServers"]) == ["omega-prime"]
    assert _server(document) == {
        "type": "stdio",
        "command": "bash",
        "args": EXPECTED_ARGS,
    }
    # The real launcher, not the scripts/ symlink.
    assert (ROOT / LAUNCHER).is_file() and not (ROOT / LAUNCHER).is_symlink()
    assert_no_secrets(text, name=MCP_JSON)


def test_https_example_is_https_mcp_with_only_placeholders() -> None:
    text = (ROOT / HTTPS_EXAMPLE).read_text(encoding="utf-8")
    document = json.loads(text)
    assert list(document["mcpServers"]) == ["omega-prime"]
    server = _server(document)
    assert set(server) == {"url", "headers"}
    assert server["url"].startswith("https://")
    assert server["url"].endswith("/mcp")
    assert list(server["headers"]) == ["Authorization"]
    value = server["headers"]["Authorization"]
    assert PLACEHOLDER.fullmatch(value)
    dollars = text.count("$")
    placeholders = re.findall(r"\$\{(?:env:)?[A-Z][A-Z0-9_]*\}", text)
    assert dollars == len(placeholders) == 1
    # Validated by shape above, then sentinel-substituted for the unchanged gate.
    member = f'"Authorization": {json.dumps(value)}'
    assert text.count(member) == 1
    assert_no_secrets(text.replace(member, plugin.HEADER_SENTINEL), name=HTTPS_EXAMPLE)
    assert plugin.secret_problems(HTTPS_EXAMPLE, text) == []


def test_https_example_rejects_a_foreign_placeholder_variable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """N-01: only the documented token variable is sentinel-masked."""
    tree = _copy_tree(tmp_path)
    foreign = "Bearer ${AKIAIOSFODNN7EXAMPLE}"
    _edit_json(
        tree,
        HTTPS_EXAMPLE,
        lambda doc: _server(doc)["headers"].update(Authorization=foreign),
    )
    assert main(["--root", str(tree), "--check"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert HTTPS_EXAMPLE in captured.err
    assert "AKIAIOSFODNN7EXAMPLE" not in captured.out + captured.err


def test_https_example_accepts_the_env_form_of_the_token_variable() -> None:
    """N-01 guard: pinning the variable must not reject its `env:` form."""
    text = (ROOT / HTTPS_EXAMPLE).read_text(encoding="utf-8")
    plain = f"Bearer ${{{HTTPS_TOKEN_VAR}}}"
    env_form = text.replace(plain, f"Bearer ${{env:{HTTPS_TOKEN_VAR}}}", 1)
    assert env_form != text
    assert plugin.secret_problems(HTTPS_EXAMPLE, env_form) == []


def test_injected_token_is_caught_by_the_unchanged_gate() -> None:
    with pytest.raises(SecretLeakError) as caught:
        assert_no_secrets(f"description: see {TOKEN}", name="fixture")
    assert TOKEN not in str(caught.value)


def test_every_rendered_text_passes_the_secret_gate_as_str() -> None:
    files = render(ROOT)
    for path, text in files.items():
        assert isinstance(text, str)
        if path == HTTPS_EXAMPLE:
            assert plugin.secret_problems(path, text) == []
        else:
            assert_no_secrets(text, name=path)


# --- frontmatter (R05, P-15) ------------------------------------------------


def test_every_checked_in_skill_has_platform_valid_frontmatter() -> None:
    assert len(SKILLS) == 27
    for folder in SKILLS:
        mapping = _frontmatter(
            (ROOT / "omega_prime" / "skills" / folder / "SKILL.md").read_text(
                encoding="utf-8"
            )
        )
        assert mapping["name"] == folder
        assert COMPONENT.fullmatch(folder) and len(folder) <= 64
        assert isinstance(mapping["description"], str)
        assert 1 <= len(mapping["description"].strip()) <= 1024


def test_every_command_file_routes_to_omega_command_with_its_exact_text() -> None:
    files = sorted((ROOT / COMMANDS_DIR).glob("*.md"))
    assert len(files) == 46
    for path in files:
        text = path.read_text(encoding="utf-8")
        mapping = _frontmatter(text)
        assert set(mapping) == {"name", "description"}
        assert mapping["name"] == path.stem
        assert isinstance(mapping["description"], str) and mapping["description"]
        body = text.split("\n---\n", 1)[1]
        assert "`omega_command`" in body
        assert "`omega-prime`" in body
        assert f"`/{path.stem}`" in body


# --- catalog (R08, R09) -----------------------------------------------------


def test_catalog_has_46_entries_with_exactly_the_five_suffixed_names() -> None:
    assert [spec.name for spec in COMMANDS] == CURATED
    assert len(ROUTINES) == 7
    names = {spec.name for spec in plugin.catalog(ROOT)}
    assert len(names) == 46 == len(CURATED) + len(SKILLS) + len(ROUTINES)
    assert names == _expected_names()
    generated = names - set(CURATED)
    suffixed = {name for name in generated if name.endswith(("-skill", "-routine"))}
    assert suffixed == set(SUFFIXED.values())
    assert "omega-commands" in names and "omega-omega-commands" not in names
    on_disk = {path.stem for path in (ROOT / COMMANDS_DIR).glob("*.md")}
    assert on_disk == _expected_names()


def test_two_renders_are_byte_identical_and_sorted() -> None:
    first = render(ROOT)
    second = render(ROOT)
    assert first == second
    assert list(first) == sorted(first)
    digest = hashlib.sha256(
        json.dumps(first, sort_keys=True).encode("utf-8")
    ).hexdigest()
    code = (
        "import hashlib, json, sys\n"
        "from omega_prime.tooling.plugin import render\n"
        "files = render(sys.argv[1])\n"
        "text = json.dumps(files, sort_keys=True).encode('utf-8')\n"
        "print(hashlib.sha256(text).hexdigest())\n"
    )
    other = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert other.stdout.strip() == digest


def test_roster_keeps_the_single_omega_command_surface() -> None:
    assert not any(path.startswith("omega_prime/contracts/") for path in render(ROOT))
    assert re.search(r"^\s*-\s*omega_command\s*$", (ROOT / ROSTER).read_text(), re.M)


# --- check and write (R10, R13) ---------------------------------------------


def test_check_passes_on_the_committed_tree(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check"]) == 0
    captured = capsys.readouterr()
    assert "up to date" in captured.out
    assert captured.err == ""


def test_check_passes_on_a_clean_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _copy_tree(tmp_path)
    assert main(["--root", str(tree), "--check"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_check_passes_when_a_checkout_materializes_crlf(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """WR-05: CRLF from a `text=auto` checkout equals LF; `--check` converges."""
    tree = _copy_tree(tmp_path)
    for rel in (MCP_JSON, f"{COMMANDS_DIR}/omega-help.md"):
        path = tree / rel
        text = path.read_text(encoding="utf-8")
        assert "\r\n" not in text
        path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert main(["--root", str(tree), "--check"]) == 0
    assert "up to date" in capsys.readouterr().out


def _modified_file(tree: Path) -> None:
    _edit(tree, MCP_JSON, lambda text: text + "\n")


def _modified_command(tree: Path) -> None:
    _edit(tree, f"{COMMANDS_DIR}/omega-help.md", lambda text: text + "extra\n")


def _orphan(tree: Path) -> None:
    (tree / COMMANDS_DIR / "omega-orphan.md").write_text("orphan\n", encoding="utf-8")


def _unowned(tree: Path) -> None:
    (tree / COMMANDS_DIR / "notes.txt").write_text("notes\n", encoding="utf-8")


def _new_skill(tree: Path) -> None:
    _add_skill(tree, "new-skill")


def _yaml_invalid(tree: Path) -> None:
    _set_skill_line(
        tree,
        "code-review",
        "description",
        "description: Review work: an unquoted colon",
    )


def _name_mismatch(tree: Path) -> None:
    _set_skill_line(tree, "code-review", "name", "name: review-code")


def _empty_description(tree: Path) -> None:
    _set_skill_line(tree, "code-review", "description", "description:")


def _collision(tree: Path) -> None:
    _add_skill(tree, "python-skill")


def _invalid_name(tree: Path) -> None:
    _add_skill(tree, "Bad_Name")


def _template_missing_skill(tree: Path) -> None:
    _list_in_template(tree, "skills", "ghost-skill")


def _template_missing_routine(tree: Path) -> None:
    _list_in_template(tree, "routines", "ghost-routine")


def _template_unlisted_skill(tree: Path) -> None:
    _unlist_in_template(tree, "code-review")


def _template_unlisted_routine(tree: Path) -> None:
    _unlist_in_template(tree, "sweep")


def _no_routines(tree: Path) -> None:
    for path in (tree / "omega_prime" / "routines").glob("*.md"):
        path.unlink()


def _no_skills(tree: Path) -> None:
    shutil.rmtree(tree / "omega_prime" / "skills")
    (tree / "omega_prime" / "skills").mkdir()


def _secret_in_skill(tree: Path) -> None:
    _set_skill_line(
        tree, "code-review", "description", f"description: Review work. Ref {TOKEN}"
    )


def _secret_in_mcp_json(tree: Path) -> None:
    _edit_json(
        tree, MCP_JSON, lambda doc: _server(doc).update(env={"API_TOKEN": TOKEN})
    )


def _https_literal_token(tree: Path) -> None:
    _edit_json(
        tree,
        HTTPS_EXAMPLE,
        lambda doc: _server(doc)["headers"].update(Authorization=f"Bearer {TOKEN}"),
    )


def _https_bearer_40(tree: Path) -> None:
    _edit_json(
        tree,
        HTTPS_EXAMPLE,
        lambda doc: _server(doc)["headers"].update(Authorization=BEARER_40),
    )


def _https_plain_http(tree: Path) -> None:
    _edit_json(
        tree,
        HTTPS_EXAMPLE,
        lambda doc: _server(doc).update(url="http://omega.example.com/mcp"),
    )


def _https_sse_path(tree: Path) -> None:
    _edit_json(
        tree,
        HTTPS_EXAMPLE,
        lambda doc: _server(doc).update(url="https://omega.example.com/sse"),
    )


def _https_extra_server(tree: Path) -> None:
    _edit_json(
        tree,
        HTTPS_EXAMPLE,
        lambda doc: doc["mcpServers"].update(other={"url": "https://x.example/mcp"}),
    )


SKILL_SOURCE = "omega_prime/skills/code-review/SKILL.md"

DRIFT_CASES: list[tuple[str, Callable[[Path], None], list[str], str | None, int]] = [
    # `--check` exit 1: drift `--write` repairs. Exit 3: content it cannot.
    ("modified-file", _modified_file, [MCP_JSON], None, 1),
    ("modified-command", _modified_command, [f"{COMMANDS_DIR}/omega-help.md"], None, 1),
    ("orphan-command", _orphan, [f"{COMMANDS_DIR}/omega-orphan.md"], None, 1),
    ("unowned-command", _unowned, [f"{COMMANDS_DIR}/notes.txt"], None, 3),
    (
        "unregenerated-skill",
        _new_skill,
        [f"{COMMANDS_DIR}/omega-new-skill.md"],
        None,
        1,
    ),
    ("yaml-invalid-skill", _yaml_invalid, [SKILL_SOURCE], None, 3),
    ("name-mismatched-skill", _name_mismatch, [SKILL_SOURCE], None, 3),
    ("empty-description", _empty_description, [SKILL_SOURCE], None, 3),
    (
        "collision",
        _collision,
        [
            "omega_prime/skills/python/SKILL.md",
            "omega_prime/skills/python-skill/SKILL.md",
        ],
        None,
        3,
    ),
    ("invalid-name", _invalid_name, ["omega_prime/skills/Bad_Name/SKILL.md"], None, 3),
    (
        "template-missing-skill",
        _template_missing_skill,
        [TEMPLATE, "ghost-skill"],
        None,
        3,
    ),
    (
        "template-missing-routine",
        _template_missing_routine,
        [TEMPLATE, "ghost-routine"],
        None,
        3,
    ),
    (
        "template-unlisted-skill",
        _template_unlisted_skill,
        [TEMPLATE, SKILL_SOURCE],
        None,
        3,
    ),
    (
        "template-unlisted-routine",
        _template_unlisted_routine,
        [TEMPLATE, "omega_prime/routines/sweep.md"],
        None,
        3,
    ),
    ("no-routines", _no_routines, ["omega_prime/routines: no checked-in"], None, 3),
    ("no-skills", _no_skills, ["omega_prime/skills: no checked-in"], None, 3),
    (
        "secret-in-skill",
        _secret_in_skill,
        [f"{COMMANDS_DIR}/omega-code-review.md"],
        TOKEN,
        3,
    ),
    ("secret-in-mcp-json", _secret_in_mcp_json, [MCP_JSON], TOKEN, 3),
    ("https-literal-token", _https_literal_token, [HTTPS_EXAMPLE], TOKEN, 3),
    ("https-bearer-40", _https_bearer_40, [HTTPS_EXAMPLE], BEARER_40[7:], 3),
    ("https-plain-http", _https_plain_http, [HTTPS_EXAMPLE], None, 3),
    ("https-sse-path", _https_sse_path, [HTTPS_EXAMPLE], None, 3),
    ("https-extra-server", _https_extra_server, [HTTPS_EXAMPLE], None, 3),
]


@pytest.mark.parametrize(
    ("mutate", "paths", "secret", "code"),
    [pytest.param(m, p, s, c, id=i) for i, m, p, s, c in DRIFT_CASES],
)
def test_each_drift_class_fails_check_naming_paths_and_writing_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutate: Callable[[Path], None],
    paths: list[str],
    secret: str | None,
    code: int,
) -> None:
    tree = _copy_tree(tmp_path)
    mutate(tree)
    before = _snapshot(tree)
    capsys.readouterr()

    assert main(["--root", str(tree), "--check"]) == code

    captured = capsys.readouterr()
    assert captured.out == ""
    for path in paths:
        assert path in captured.err
    if secret is not None:
        assert secret not in captured.out + captured.err
    assert _snapshot(tree) == before


def test_render_aborts_on_a_collision_naming_both_sources(tmp_path: Path) -> None:
    tree = _copy_tree(tmp_path)
    _collision(tree)
    with pytest.raises(PluginError) as caught:
        render(tree)
    message = str(caught.value)
    assert "omega_prime/skills/python/SKILL.md" in message
    assert "omega_prime/skills/python-skill/SKILL.md" in message


def test_a_curated_base_from_a_skill_takes_the_skill_suffix(tmp_path: Path) -> None:
    tree = _copy_tree(tmp_path)
    _add_skill(tree, "omega-help")
    files = render(tree)
    assert f"{COMMANDS_DIR}/omega-help-skill.md" in files
    assert f"{COMMANDS_DIR}/omega-help.md" in files
    assert "curated catalog" in files[f"{COMMANDS_DIR}/omega-help.md"]


def test_failed_write_leaves_outputs_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _copy_tree(tmp_path)
    _modified_file(tree)
    _collision(tree)
    before = _snapshot(tree)

    assert main(["--root", str(tree), "--write"]) == 3

    assert "nothing written" in capsys.readouterr().err
    assert _snapshot(tree) == before


def test_write_regenerates_and_removes_only_owned_orphans(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _copy_tree(tmp_path)
    _modified_file(tree)
    (tree / COMMANDS_DIR / "omega-desk.md").unlink()
    _orphan(tree)
    _unowned(tree)

    assert main(["--root", str(tree), "--write"]) == 1
    err = capsys.readouterr().err
    assert f"{COMMANDS_DIR}/notes.txt" in err

    assert not (tree / COMMANDS_DIR / "omega-orphan.md").exists()
    assert (tree / COMMANDS_DIR / "notes.txt").read_text(encoding="utf-8") == "notes\n"
    for rel in (MCP_JSON, f"{COMMANDS_DIR}/omega-desk.md"):
        assert (tree / rel).read_bytes() == (ROOT / rel).read_bytes()
        assert stat.S_IMODE((tree / rel).stat().st_mode) == 0o644

    (tree / COMMANDS_DIR / "notes.txt").unlink()
    assert main(["--root", str(tree), "--check"]) == 0


def test_absent_pyyaml_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(PluginEnvError) as caught:
        render(ROOT)
    assert "PyYAML" in str(caught.value)


def test_absent_pyyaml_check_exits_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-03: missing PyYAML is environment breakage (exit 4), not drift."""
    tree = _copy_tree(tmp_path)
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert main(["--root", str(tree), "--check"]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PyYAML" in captured.err


def test_check_crash_exits_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-03: an interpreter/import crash is environment breakage (exit 4)."""
    tree = _copy_tree(tmp_path)

    def _boom(root: Path) -> dict[str, str]:
        raise ImportError("hostile import crash")

    monkeypatch.setattr(plugin, "render", _boom)
    assert main(["--root", str(tree), "--check"]) == 4
    assert capsys.readouterr().out == ""


# --- verify (D-02) ----------------------------------------------------------


STUB_HOST = r"""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "stub-argv.json"), "w") as fh:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, fh)
with open(os.path.join(here, "stub-mode")) as fh:
    mode = fh.read().strip()
with open(os.path.join(here, "stub-names.json")) as fh:
    names = json.load(fh)
if mode == "banner":
    sys.stdout.write("starting omega prime\n")
    sys.stdout.flush()
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "stub", "version": "1"}}
    else:
        shown = names[:-1] if mode == "short" else names
        payload = {"ok": True, "command": "omega-help",
                   "result": {"commands": [{"name": n} for n in shown]}}
        result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
"""


def _stub_launcher(tree: Path, mode: str) -> Path:
    scripts = tree / "omega_prime" / "scripts"
    (scripts / "stub_host.py").write_text(STUB_HOST, encoding="utf-8")
    (scripts / "stub-mode").write_text(mode, encoding="utf-8")
    names = sorted(spec.name for spec in plugin.catalog(tree))
    (scripts / "stub-names.json").write_text(json.dumps(names), encoding="utf-8")
    (scripts / "grokbot-1click.sh").write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{sys.executable}" "$(dirname "${{BASH_SOURCE[0]}}")/stub_host.py" '
        '"${BASH_SOURCE[0]}" "$@"\n',
        encoding="utf-8",
    )
    return scripts


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_stdio_verifier_spawns_exactly_the_declared_command(tmp_path: Path) -> None:
    tree = _copy_tree(tmp_path)
    scripts = _stub_launcher(tree, "ok")

    ok, detail = plugin.verify_stdio(tree.resolve())

    assert ok, detail
    seen = json.loads((scripts / "stub-argv.json").read_text(encoding="utf-8"))
    assert seen["argv"] == [
        str(tree.resolve() / LAUNCHER),
        *EXPECTED_ARGS[1:],
    ]
    assert Path(seen["cwd"]).resolve() != tree.resolve()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    ("mode", "reason"),
    [("banner", "not JSON-RPC"), ("short", "listed")],
)
def test_stdio_verifier_rejects_stdout_noise_and_a_short_catalog(
    tmp_path: Path, mode: str, reason: str
) -> None:
    tree = _copy_tree(tmp_path)
    _stub_launcher(tree, mode)

    ok, detail = plugin.verify_stdio(tree.resolve())

    assert not ok
    assert reason in detail


@pytest.mark.parametrize("json_report", [False, True])
def test_verify_names_each_failed_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], json_report: bool
) -> None:
    tree = _copy_tree(tmp_path)
    _secret_in_mcp_json(tree)
    _https_plain_http(tree)
    argv = ["--root", str(tree), "--verify", *(["--json"] if json_report else [])]

    assert main(argv) == 1

    captured = capsys.readouterr()
    assert "stdio entry FAILED" in captured.err
    assert "https entry FAILED" in captured.err
    assert TOKEN not in captured.out + captured.err
    if json_report:
        report = json.loads(captured.out)
        assert report["ok"] is False
        assert report["failed"] == ["stdio", "https"]
    else:
        assert captured.out == ""


@pytest.mark.parametrize(
    ("results", "code", "failed"),
    [
        ((True, True), 0, []),
        ((True, False), 1, ["https"]),
        ((False, True), 1, ["stdio"]),
    ],
)
def test_verify_exit_follows_both_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    results: tuple[bool, bool],
    code: int,
    failed: list[str],
) -> None:
    stdio_ok, https_ok = results
    monkeypatch.setattr(
        plugin,
        "VERIFIERS",
        (
            ("stdio", lambda root: (stdio_ok, "stdio detail")),
            ("https", lambda root: (https_ok, "https detail")),
        ),
    )

    assert main(["--verify", "--json"]) == code

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["failed"] == failed
    assert report["ok"] is (code == 0)
    for name in failed:
        assert f"{name} entry FAILED" in captured.err

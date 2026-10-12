"""Tests for Grok Bot Manifest Generator."""

import json
import shlex
import shutil
from pathlib import Path

import pytest

import omega_prime
from omega_prime.config import PRIME_FAMILIES
from omega_prime.grokbot.manifest import (
    find_repo_root,
    generate_manifest,
    get_assembled_prompt,
    lint_template,
    main,
    manifest_digest,
    parse_template_sections,
    served_tools,
)
from omega_prime.mcp_server import SERVER_VERSION, RuntimeConfigError, load_runtime


def test_find_repo_root():
    root = find_repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "omega_prime").is_dir()


def test_parse_template_sections(tmp_path: Path):
    sample_md = tmp_path / "test.md"
    sample_md.write_text(
        "# Header\n"
        "Some top description\n\n"
        "## Enabled Skills\n"
        "- skill-alpha\n"
        "- skill-beta\n\n"
        "## Routines\n"
        "- routine-one\n",
        encoding="utf-8",
    )
    data = parse_template_sections(sample_md)
    assert data["skills"] == ["skill-alpha", "skill-beta"]
    assert data["routines"] == ["routine-one"]


def test_generate_manifest_defaults():
    root = find_repo_root()
    manifest = generate_manifest(
        root, transport="sse", host_url="https://grok.internal:8000/sse"
    )
    assert manifest["manifest_version"] == "1.0.0"
    assert manifest["bot"]["name"] == "Omega Prime"
    assert len(manifest["bot"]["skills"]) >= 20
    assert manifest["mcp_server"]["transport"] == "sse"
    assert manifest["mcp_server"]["url"] == "https://grok.internal:8000/sse"
    assert manifest["mcp_server"]["auth"]["type"] == "none"
    assert manifest["mcp_server"]["rostered_tool_count"] > 100
    assert len(manifest["mcp_server"]["tools"]) > 100
    assert manifest["capabilities"]["programming_desk"] is True


def test_generate_manifest_with_token():
    root = find_repo_root()
    manifest = generate_manifest(root, auth_token="secret-grok-token")
    assert manifest["mcp_server"]["auth"]["type"] == "bearer"
    assert manifest["mcp_server"]["auth"]["token_env"] == "MCP_AUTH_TOKEN"


def test_get_assembled_prompt():
    prompt = get_assembled_prompt()
    assert "<core_directives" in prompt or len(prompt) > 500


# --- phase 62-03: truthful manifest ---------------------------------------

_PRIME_FLAGS = tuple(
    f"OMEGA_PRIME_PRIME_{family.upper()}_ENABLED" for family in PRIME_FAMILIES
)
_SECRET = "tok-SECRET-0123456789abcdef"


def make_tree(base: Path) -> Path:
    """A minimal repo copy: roster, policy, template, prompt, skills, routines."""
    src = find_repo_root() / "omega_prime"
    dst = base / "repo" / "omega_prime"
    ignore = shutil.ignore_patterns("__pycache__")
    for rel in ("contracts", "skills", "prompts-assembled", "grokbot/templates"):
        shutil.copytree(src / rel, dst / rel, ignore=ignore)
    (dst / "routines").mkdir()
    for routine in (src / "routines").glob("*.md"):
        shutil.copy2(routine, dst / "routines" / routine.name)
    return base / "repo"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PRIME_FLAGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def tree(tmp_path: Path, clean_env: None) -> Path:
    return make_tree(tmp_path)


def test_tools_are_exactly_what_the_host_serves(tree: Path, tmp_path: Path):
    manifest = generate_manifest(tree, home=tmp_path, env={})
    runtime = load_runtime(tree, tmp_path, env={})
    server = manifest["mcp_server"]
    assert server["tools"] == runtime.tool_names
    assert server["rostered_tool_count"] == len(runtime.tool_names)
    assert server["approval_required"] == runtime.gated_tools
    assert server["approval_required"]
    assert set(server["approval_required"]) <= set(server["tools"])
    assert served_tools(tree, tmp_path, {}) == (
        runtime.tool_names,
        runtime.gated_tools,
    )


def test_missing_roster_fails_closed(tree: Path, tmp_path: Path):
    (tree / "omega_prime/contracts/tool-rosters/omega-prime.yaml").unlink()
    with pytest.raises(RuntimeConfigError):
        generate_manifest(tree, home=tmp_path, env={})


def test_version_fields(tree: Path, tmp_path: Path):
    bot = generate_manifest(tree, home=tmp_path, env={})["bot"]
    assert bot["version"] == SERVER_VERSION
    assert bot["package_version"] == omega_prime.__version__


def test_prime_agent_follows_config(tree: Path, tmp_path: Path):
    off = generate_manifest(tree, home=tmp_path, env={})["capabilities"]
    assert off["prime_agent"] is False
    assert set(off["prime_families"]) == set(PRIME_FAMILIES)
    assert not any(off["prime_families"].values())
    assert off["transports"] == ["stdio", "sse", "streamable-http"]
    assert off["programming_desk"] is True

    on = generate_manifest(
        tree, home=tmp_path, env={"OMEGA_PRIME_PRIME_GOALS_ENABLED": "1"}
    )["capabilities"]
    assert on["prime_agent"] is True
    assert on["prime_families"]["goals"] is True
    assert on["prime_families"]["rlm"] is False


def test_prime_flag_in_process_env_adds_tools(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    before = generate_manifest(tree, home=tmp_path)
    monkeypatch.setenv("OMEGA_PRIME_PRIME_GOALS_ENABLED", "1")
    after = generate_manifest(tree, home=tmp_path)
    assert after["capabilities"]["prime_agent"] is True
    assert "goal_set" not in before["mcp_server"]["tools"]
    assert "goal_set" in after["mcp_server"]["tools"]
    assert "goal_set" in after["mcp_server"]["approval_required"]
    assert after["mcp_server"]["rostered_tool_count"] == len(
        after["mcp_server"]["tools"]
    )


def test_substrate_telemetry_needs_url_and_token(tree: Path, tmp_path: Path):
    def telemetry(env: dict[str, str]) -> bool:
        manifest = generate_manifest(tree, home=tmp_path, env=env)
        return manifest["capabilities"]["substrate_telemetry"]

    assert telemetry({}) is False
    assert telemetry({"SUBSTRATE_URL": "http://127.0.0.1:1"}) is False
    assert telemetry({"SUBSTRATE_TOKEN": "t"}) is False
    assert telemetry({"SUBSTRATE_URL": "http://127.0.0.1:1", "SUBSTRATE_TOKEN": "t"})
    assert telemetry(
        {"SUBSTRATE_URL": "http://127.0.0.1:1", "SUBSTRATE_TOKEN_GROK_BOT": "t"}
    )


@pytest.mark.parametrize(
    "url",
    ["http://0.0.0.0:8000/sse", "http://[::]:8000/sse", "http://:8000/sse"],
)
def test_wildcard_host_is_rewritten_with_note(url: str, tree: Path, tmp_path: Path):
    server = generate_manifest(tree, host_url=url, home=tmp_path, env={})["mcp_server"]
    assert server["url"] == "http://127.0.0.1:8000/sse"
    assert server["url_note"] == (
        "wildcard bind rewritten to loopback; pass --public-url for remote clients"
    )
    assert server["endpoints"] == {
        "sse": "http://127.0.0.1:8000/sse",
        "mcp": "http://127.0.0.1:8000/mcp",
        "healthz": "http://127.0.0.1:8000/healthz",
        "readyz": "http://127.0.0.1:8000/readyz",
    }


def test_public_url_wins_and_composes_sse(tree: Path, tmp_path: Path):
    server = generate_manifest(
        tree,
        host_url="http://0.0.0.0:8000/sse",
        public_url="https://grok.example.com/",
        home=tmp_path,
        env={},
    )["mcp_server"]
    assert server["url"] == "https://grok.example.com/sse"
    assert "url_note" not in server
    assert server["endpoints"]["mcp"] == "https://grok.example.com/mcp"
    assert server["endpoints"]["healthz"] == "https://grok.example.com/healthz"
    assert server["endpoints"]["readyz"] == "https://grok.example.com/readyz"


def test_stdio_transport_declares_the_launcher_and_no_endpoints(
    tree: Path, tmp_path: Path
):
    server = generate_manifest(tree, transport="stdio", home=tmp_path, env={})[
        "mcp_server"
    ]
    assert server["url"] is None
    assert server["endpoints"] == {}
    argv = shlex.split(server["command"])
    assert argv[0] == "bash"
    script = Path(argv[1])
    assert not script.is_absolute()
    launcher = find_repo_root() / script
    assert launcher.is_file()
    assert not launcher.is_symlink()
    assert argv[2:] == ["--bootstrap", "--transport", "stdio"]


def test_auth_type_and_token_is_never_written(tree: Path, tmp_path: Path):
    manifest = generate_manifest(
        tree,
        host_url="http://127.0.0.1:9000/sse",
        auth_token=_SECRET,
        home=tmp_path,
        env={},
    )
    assert manifest["mcp_server"]["auth"] == {
        "type": "bearer",
        "token_env": "MCP_AUTH_TOKEN",
        "scopes": ["read", "call"],
    }
    assert _SECRET not in json.dumps(manifest)

    forced = generate_manifest(tree, auth_enabled=True, home=tmp_path, env={})
    assert forced["mcp_server"]["auth"]["type"] == "bearer"
    disabled = generate_manifest(
        tree, auth_token=_SECRET, auth_enabled=False, home=tmp_path, env={}
    )
    assert disabled["mcp_server"]["auth"]["type"] == "none"
    assert _SECRET not in json.dumps(disabled)


def test_digest_is_stable_and_tracks_template(tree: Path, tmp_path: Path):
    first = generate_manifest(tree, home=tmp_path, env={})
    second = generate_manifest(tree, home=tmp_path, env={})
    assert first == second
    assert first["digest"] == manifest_digest(first)
    assert len(first["digest"]) == 64

    template = tree / "omega_prime/grokbot/templates/OMEGA_PRIME.md"
    text = template.read_text(encoding="utf-8")
    template.write_text(
        text.replace("- android\n", "- android\n- brand-new-skill\n", 1),
        encoding="utf-8",
    )
    changed = generate_manifest(tree, home=tmp_path, env={})
    assert changed["digest"] != first["digest"]
    assert changed["bot"]["integrity"]["missing_skills"] == ["brand-new-skill"]
    assert changed["bot"]["integrity"]["ok"] is False


def test_integrity_reports_a_skill_the_template_omits(tree: Path, tmp_path: Path):
    skill = tree / "omega_prime/skills/zz-unlisted"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# zz-unlisted\n", encoding="utf-8")
    (tree / "omega_prime/routines/zz-unlisted.md").write_text(
        "# zz-unlisted\n", encoding="utf-8"
    )
    integrity = generate_manifest(tree, home=tmp_path, env={})["bot"]["integrity"]
    assert integrity["missing_skills"] == []
    assert integrity["missing_routines"] == []
    assert "zz-unlisted" in integrity["unlisted_skills"]
    assert "zz-unlisted" in integrity["unlisted_routines"]
    assert integrity["ok"] is False


def test_shipped_template_lints_clean():
    lint = lint_template(find_repo_root())
    assert lint.ok is True
    assert lint.missing_skills == []
    assert lint.missing_routines == []
    assert lint.unlisted_skills == []
    assert lint.unlisted_routines == []
    manifest_integrity = generate_manifest(find_repo_root())["bot"]["integrity"]
    assert manifest_integrity == {
        "missing_skills": [],
        "missing_routines": [],
        "unlisted_skills": [],
        "unlisted_routines": [],
        "ok": True,
    }


def test_main_writes_public_manifest_without_token(
    tree: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    out = tmp_path / "out" / "manifest.json"
    code = main(
        [
            "--root",
            str(tree),
            "--home",
            str(tmp_path),
            "--public-url",
            "https://grok.example.com",
            "--token",
            _SECRET,
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "visible to other users" in captured.err
    assert _SECRET not in captured.out + captured.err
    data = json.loads(out.read_text(encoding="utf-8"))
    assert _SECRET not in out.read_text(encoding="utf-8")
    assert data["mcp_server"]["url"] == "https://grok.example.com/sse"
    assert data["mcp_server"]["auth"]["type"] == "bearer"
    assert out.stat().st_mode & 0o777 == 0o644


def test_main_reports_config_error(
    tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    (tree / "omega_prime/contracts/tool-rosters/omega-prime.yaml").unlink()
    code = main(["--root", str(tree), "--home", str(tmp_path)])
    assert code == 2
    assert "omega-prime-grokbot-manifest:" in capsys.readouterr().err

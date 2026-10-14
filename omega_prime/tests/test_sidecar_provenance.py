"""Hermetic tests for the sidecar provenance checker.

Every case runs against tmp fixtures via the ``--root`` / ``--gitlink``
overrides — nothing here depends on the live checkout state, the network,
or the current pin. The checker itself is exercised through :func:`main`
return codes (0 agree, 1 drift, 2 environment) plus the pure parsers.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from sidecar import provenance
from sidecar.provenance import main

PIN_A = "831a6b0ee09f12c0f069f51d62b2f0945d505980"
PIN_B = "0e2411c0df59fce8c56dc03a5f1c9afffcb0d746"


def _write_fixture_root(
    tmp_path: Path,
    *,
    vendor_pin: str | None = PIN_A,
    docstring_pins: list[str] | None = None,
    upstream_tools: list[str] | None = None,
    adapter_tools: list[str] | None = None,
    contract_tools: list[str] | None = None,
    pack_pairs: dict[str, tuple[str, str | None]] | None = None,
    raw_upstream_surface: str | None = None,
) -> Path:
    """Build a fake repo root exercising every provenance input."""
    docstring_pins = [PIN_A] if docstring_pins is None else docstring_pins
    tools = [
        "tempest_status",
        "tempest_mission_start",
        "tempest_mission_stop",
        "tempest_report",
        "tempest_operators_list",
        "tempest_operator_task",
        "tempest_findings",
        "tempest_providers",
        "tempest_purple_synthesize",
        "tempest_hats_list",
        "tempest_hat_dispatch",
    ]
    upstream_tools = tools if upstream_tools is None else upstream_tools
    adapter_tools = tools if adapter_tools is None else adapter_tools
    contract_tools = tools if contract_tools is None else contract_tools

    vendor_line = f"- Commit: `{vendor_pin}`\n" if vendor_pin else "- Commit: TBD\n"
    (tmp_path / "VENDOR.md").write_text(
        "# Vendored behavior\n\n## T3MP3ST red team pack\n\n"
        "- Remote: http://127.0.0.1/purple-team.git\n"
        f"{vendor_line}",
        encoding="utf-8",
    )
    init = tmp_path / "omega_prime" / "redteam"
    init.mkdir(parents=True)
    docstring_block = "\n".join(f"    Upstream commit: {p}" for p in docstring_pins)
    (init / "__init__.py").write_text(
        f'"""Vendored pack.\n\nProvenance\n{docstring_block}\n"""\n',
        encoding="utf-8",
    )
    tools_dir = tmp_path / "omega_prime" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "redteam.py").write_text(
        "REDTEAM_TOOL_NAMES = (\n"
        + "".join(f'    "{name}",\n' for name in adapter_tools)
        + ")\n",
        encoding="utf-8",
    )
    src = tmp_path / "redteam" / "src"
    src.mkdir(parents=True)
    if raw_upstream_surface is not None:
        (src / "mcp-server.ts").write_text(raw_upstream_surface, encoding="utf-8")
    else:
        (src / "mcp-server.ts").write_text(
            "".join(f"    name: '{name}',\n" for name in upstream_tools),
            encoding="utf-8",
        )
    contract_dir = tmp_path / "sidecar" / "contract"
    contract_dir.mkdir(parents=True)
    (contract_dir / "bot-interface.json").write_text(
        json.dumps({"schema_version": 1, "tool_names": contract_tools}),
        encoding="utf-8",
    )
    pack_pairs = {} if pack_pairs is None else pack_pairs
    for rel, (vendored_text, upstream_text) in pack_pairs.items():
        vendored_path = tmp_path / "omega_prime" / "redteam" / "pack" / rel
        vendored_path.parent.mkdir(parents=True, exist_ok=True)
        vendored_path.write_text(vendored_text, encoding="utf-8")
        if upstream_text is not None:
            upstream_path = tmp_path / "redteam" / "grokbot" / rel
            upstream_path.parent.mkdir(parents=True, exist_ok=True)
            upstream_path.write_text(upstream_text, encoding="utf-8")
    (tmp_path / "omega_prime" / "redteam" / "pack").mkdir(parents=True, exist_ok=True)
    (tmp_path / "redteam" / "grokbot").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _run(capsys, *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ------------------------------------------------------------------ check


def test_check_green_when_all_three_agree(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    code, out, _ = _run(capsys, "--root", str(root), "check", "--gitlink", PIN_A)
    assert code == 0
    assert "check OK" in out


def test_check_names_gitlink_vs_vendor_pair(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, vendor_pin=PIN_B)
    code, out, _ = _run(capsys, "--root", str(root), "check", "--gitlink", PIN_A)
    assert code == 1
    assert "gitlink-vs-VENDOR.md" in out


def test_check_names_vendor_vs_docstring_pair(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, docstring_pins=[PIN_B])
    code, out, _ = _run(capsys, "--root", str(root), "check", "--gitlink", PIN_A)
    assert code == 1
    assert "gitlink-vs-docstrings" in out
    assert "VENDOR.md-vs-docstrings" in out


def test_check_missing_vendor_pin_fails(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, vendor_pin=None)
    code, out, _ = _run(capsys, "--root", str(root), "check", "--gitlink", PIN_A)
    assert code == 1
    assert "no `Commit:` pin" in out


def test_check_missing_docstring_pin_fails(tmp_path, capsys):
    root = _write_fixture_root(tmp_path, docstring_pins=[])
    code, out, _ = _run(capsys, "--root", str(root), "check", "--gitlink", PIN_A)
    assert code == 1
    assert "no `Upstream commit:` pin" in out


def test_check_missing_vendor_file_is_environment_error(tmp_path, capsys):
    code, _, err = _run(capsys, "--root", str(tmp_path), "check", "--gitlink", PIN_A)
    assert code == 2
    assert "missing" in err


def test_read_gitlink_prefers_staged_index_over_head(monkeypatch):
    """A staged bump (``git add redteam``) verifies before commit."""
    calls: list[list[str]] = []

    class _Proc:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def _fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["git", "ls-files"]:
            return _Proc(f"160000 {PIN_B} 0\tredteam\n")
        return _Proc(f"160000 {PIN_A} 0\tredteam\n")

    monkeypatch.setattr(provenance.subprocess, "run", _fake_run)
    assert provenance.read_gitlink(Path("/repo")) == PIN_B
    assert calls[0][:3] == ["git", "ls-files", "-s"]


def test_read_gitlink_falls_back_to_head_when_nothing_staged(monkeypatch):
    class _Proc:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def _fake_run(argv, **kwargs):
        if argv[:2] == ["git", "ls-files"]:
            return _Proc("")
        return _Proc(f"160000 {PIN_A} 0\tredteam\n")

    monkeypatch.setattr(provenance.subprocess, "run", _fake_run)
    assert provenance.read_gitlink(Path("/repo")) == PIN_A


# -------------------------------------------------------------- pack-diff


def test_pack_diff_green_on_identical_trees(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        pack_pairs={
            "skills/tempest-status/SKILL.md": ("# status", "# status"),
            "rosters/redteam.json": ('{"team": 1}', '{"team": 1}'),
        },
    )
    code, out, _ = _run(capsys, "--root", str(root), "pack-diff")
    assert code == 0
    assert "pack-diff OK" in out


def test_pack_diff_lists_diverged_files(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        pack_pairs={"templates/RED.md": ("# red v1", "# red v2")},
    )
    code, out, _ = _run(capsys, "--root", str(root), "pack-diff")
    assert code == 1
    assert "DIVERGED templates/RED.md" in out


def test_pack_diff_lists_missing_and_extra(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path,
        pack_pairs={
            "skills/orphan/SKILL.md": ("# orphan", None),
        },
    )
    (root / "redteam" / "grokbot" / "skills" / "undeclared").mkdir(parents=True)
    (root / "redteam" / "grokbot" / "skills" / "undeclared" / "SKILL.md").write_text(
        "# undeclared", encoding="utf-8"
    )
    code, out, _ = _run(capsys, "--root", str(root), "pack-diff")
    assert code == 1
    assert "MISSING-UPSTREAM skills/orphan/SKILL.md" in out
    assert "EXTRA-UPSTREAM skills/undeclared/SKILL.md" in out


def test_pack_diff_skips_license_and_non_doc_files(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    pack = root / "omega_prime" / "redteam" / "pack"
    (pack / "LICENSE").write_text("MIT notice", encoding="utf-8")
    (pack / "avatars").mkdir(exist_ok=True)
    (pack / "avatars" / "commander.png").write_bytes(b"\x89PNG")
    code, _, _ = _run(capsys, "--root", str(root), "pack-diff")
    assert code == 0


# ---------------------------------------------------------------- surface


def test_surface_green_when_all_three_agree(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    code, out, _ = _run(capsys, "--root", str(root), "surface")
    assert code == 0
    assert "surface OK" in out and "11 tool names" in out


def test_surface_names_upstream_vs_adapter_drift(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, upstream_tools=["tempest_status", "tempest_brand_new"]
    )
    code, out, _ = _run(capsys, "--root", str(root), "surface")
    assert code == 1
    assert "upstream-vs-adapter" in out


def test_surface_names_adapter_vs_contract_drift(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, contract_tools=["tempest_status", "tempest_other"]
    )
    code, out, _ = _run(capsys, "--root", str(root), "surface")
    assert code == 1
    assert "adapter-vs-contract" in out


def test_surface_unparseable_upstream_fails_with_excerpt(tmp_path, capsys):
    root = _write_fixture_root(
        tmp_path, raw_upstream_surface="// no tool declarations here\n"
    )
    code, out, _ = _run(capsys, "--root", str(root), "surface")
    assert code == 1
    assert "unparseable upstream tool surface" in out
    assert "no tool declarations here" in out


def test_surface_unparseable_adapter_fails_loudly(tmp_path, capsys):
    root = _write_fixture_root(tmp_path)
    (root / "omega_prime" / "tools" / "redteam.py").write_text(
        "# no tool names here\n", encoding="utf-8"
    )
    code, out, _ = _run(capsys, "--root", str(root), "surface")
    assert code == 1
    assert "unparseable adapter tool surface" in out


# ------------------------------------------------------------ stdlib-only


def test_provenance_module_is_stdlib_only():
    source = Path(provenance.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), (
        f"non-stdlib imports: {sorted(imported - set(sys.stdlib_module_names))}"
    )
    assert "import omega_prime" not in source
    assert "import redteam" not in source
    assert "from omega_prime" not in source
    assert "from redteam" not in source


def test_adapter_parsed_without_import():
    source = Path("omega_prime/tools/redteam.py").read_text(encoding="utf-8")
    names = provenance.parse_adapter_tool_names(source)
    assert len(names) == 14
    assert names[0] == "tempest_status"
    assert names[-1] == "tempest_hat_dispatch"


def test_parse_vendor_pin_scopes_to_red_team_section():
    """VENDOR.md carries one Commit pin per vendored component; the parser
    must take the red-team section's pin, not the first pin in the file."""
    text = (
        "# Vendored behavior\n\n## Hermes Agent\n\n"
        f"- Commit: `{PIN_B}`\n\n"
        "## T3MP3ST red team pack\n\n"
        f"- Commit: `{PIN_A}`\n"
    )
    assert provenance.parse_vendor_pin(text) == PIN_A

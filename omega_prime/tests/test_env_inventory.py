from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omega_prime.tooling.env_inventory import (
    InventoryError,
    _purpose_map,
    ast_source_digest,
    collect,
    render,
)

ROOT = Path(__file__).resolve().parents[2]


def _tree(tmp_path: Path, source: str, *, policy: dict | None = None) -> Path:
    root = tmp_path
    source_path = root / "omega_prime" / "consumer.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    base: dict[str, Any] = {
        "purposes": {},
        "helpers": {},
        "dynamic": [],
        "supplements": [],
        "exclusions": [],
    }
    if policy:
        base.update(policy)
    tooling = root / "omega_prime" / "tooling"
    tooling.mkdir(exist_ok=True)
    (tooling / "env_inventory_policy.json").write_text(
        json.dumps(base), encoding="utf-8"
    )
    return root


def _helper_entry(
    source: str,
    symbol: str,
    *,
    name_arg: int = 1,
    env_arg: int = 0,
    suffixes: list[str] | None = None,
) -> dict:
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == symbol
    )
    digest = ast_source_digest(function)
    return {
        f"omega_prime/consumer.py:{symbol}": {
            "name_arg": name_arg,
            "env_arg": env_arg,
            "source_sha256": digest,
            "suffixes": suffixes or [],
        }
    }


def _collect(root: Path, *, policy: dict | None = None):
    raw = json.loads(
        (root / "omega_prime/tooling/env_inventory_policy.json").read_text()
    )
    if policy:
        raw.update(policy)
    return collect(root, raw)


def test_aliases_constants_injected_helpers_and_provenance_are_sorted(
    tmp_path: Path,
) -> None:
    source = """
import os
from typing import Mapping
NAME = "OMEGA_TEST_DIRECT"
ALIAS = "OMEGA_TEST_ALIAS"
UNUSED = "OMEGA_NOT_READ"
KEYS = ("OMEGA_TEST_ONE", "OMEGA_TEST_TWO")
class Provider:
    env_vars = ("OMEGA_TEST_PROVIDER",)
def flag(env, name):
    return bool(name) and bool(env)
def configured(env: Mapping[str, str]):
    local = env
    direct = os.environ
    a = direct.get(NAME)
    b = local[ALIAS]
    c = flag(env, "OMEGA_TEST_HELPER")
    for key in KEYS:
        direct.get(key)
"""
    root = _tree(
        tmp_path,
        source,
        policy={
            "helpers": _helper_entry(source, "flag"),
            "purposes": {
                name: f"Purpose for the reviewed {name} setting."
                for name in (
                    "OMEGA_TEST_DIRECT",
                    "OMEGA_TEST_ALIAS",
                    "OMEGA_TEST_HELPER",
                    "OMEGA_TEST_ONE",
                    "OMEGA_TEST_TWO",
                    "OMEGA_TEST_PROVIDER",
                )
            },
        },
    )
    findings = _collect(root)
    assert [item.name for item in findings] == sorted(
        [
            "OMEGA_TEST_DIRECT",
            "OMEGA_TEST_ALIAS",
            "OMEGA_TEST_HELPER",
            "OMEGA_TEST_ONE",
            "OMEGA_TEST_TWO",
            "OMEGA_TEST_PROVIDER",
        ]
    )
    assert all(
        item.path == "omega_prime/consumer.py" and item.line > 0 for item in findings
    )
    assert len({(item.name, item.path, item.line) for item in findings}) == len(
        findings
    )


def test_environment_snapshots_from_copy_and_dict_are_inventoried(
    tmp_path: Path,
) -> None:
    root = _tree(
        tmp_path,
        """
import os
copy_snapshot = os.environ.copy()
dict_snapshot = dict(os.environ)
copy_snapshot.get("OMEGA_COPY_GET")
copy_snapshot["OMEGA_COPY_ITEM"]
dict_snapshot.get("OMEGA_DICT_GET")
dict_snapshot["OMEGA_DICT_ITEM"]
""",
        policy={
            "purposes": {
                name: f"Reads the {name} snapshot setting."
                for name in (
                    "OMEGA_COPY_GET",
                    "OMEGA_COPY_ITEM",
                    "OMEGA_DICT_GET",
                    "OMEGA_DICT_ITEM",
                )
            }
        },
    )

    assert [item.name for item in _collect(root)] == [
        "OMEGA_COPY_GET",
        "OMEGA_COPY_ITEM",
        "OMEGA_DICT_GET",
        "OMEGA_DICT_ITEM",
    ]


def test_environment_snapshots_follow_environment_aliases_and_nested_copies(
    tmp_path: Path,
) -> None:
    root = _tree(
        tmp_path,
        """
import os as operating_system
from os import environ as imported_environment
environment_alias = operating_system.environ
copied_alias = environment_alias.copy()
nested_snapshot = dict(copied_alias.copy())
imported_snapshot = dict(imported_environment)
copied_alias.get("OMEGA_ALIAS_COPY")
nested_snapshot["OMEGA_NESTED_DICT"]
imported_snapshot.get("OMEGA_IMPORTED_DICT")
""",
        policy={
            "purposes": {
                name: f"Reads the {name} snapshot setting."
                for name in (
                    "OMEGA_ALIAS_COPY",
                    "OMEGA_NESTED_DICT",
                    "OMEGA_IMPORTED_DICT",
                )
            }
        },
    )

    assert [item.name for item in _collect(root)] == [
        "OMEGA_ALIAS_COPY",
        "OMEGA_IMPORTED_DICT",
        "OMEGA_NESTED_DICT",
    ]


def test_ordinary_mappings_json_writes_constants_comments_fixtures_and_child_code_are_ignored(
    tmp_path: Path,
) -> None:
    root = _tree(
        tmp_path,
        '''
import json
import os
UNUSED = "OMEGA_UNUSED"
# os.environ["OMEGA_COMMENT"]
"""os.environ.get('OMEGA_DOCSTRING')"""
def run(env):
    payload = json.loads("{\\"OMEGA_FIXTURE\\": \\"x\\"}")
    payload.get("OMEGA_JSON_FIELD")
    ordinary = {"OMEGA_DICT_FIELD": "x"}
    ordinary.get("OMEGA_DICT_FIELD")
    os.environ["OMEGA_WRITE"] = "secret"
    env["OMEGA_CHILD_CODE"] = "os.environ.get('OMEGA_EMBEDDED')"
''',
    )
    assert _collect(root) == []


def test_unreviewed_literal_read_fails_with_safe_location(tmp_path: Path) -> None:
    root = _tree(tmp_path, 'import os\nVALUE = os.environ.get("OMEGA_SECRETISH")\n')
    with pytest.raises(InventoryError) as caught:
        render(root)
    assert caught.value.path == "omega_prime/consumer.py"
    assert caught.value.line == 2
    assert caught.value.name == "OMEGA_SECRETISH"
    assert (
        caught.value.error_class == "MissingPurpose"
        or caught.value.error_class == "UnreviewedDynamicRead"
    )


def test_unreviewed_dynamic_read_fails_closed(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        "from typing import Mapping\ndef get(env: Mapping[str, str]):\n    return env.get(make_name())\n",
    )
    with pytest.raises(InventoryError) as caught:
        _collect(root)
    assert caught.value.path == "omega_prime/consumer.py"
    assert caught.value.line == 3
    assert caught.value.error_class == "UnreviewedDynamicRead"


def test_dynamic_declaration_stale_entries_and_missing_purposes_fail(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, 'import os\nVALUE = os.environ.get("OMEGA_POLICY")\n')
    with pytest.raises(InventoryError):
        _purpose_map(
            {"purposes": {"OMEGA_OLD": "An obsolete purpose description."}},
            {"OMEGA_POLICY"},
        )
    with pytest.raises(InventoryError):
        _purpose_map({"purposes": {"OMEGA_POLICY": "placeholder"}}, {"OMEGA_POLICY"})
    with pytest.raises(InventoryError) as caught:
        _collect(
            root,
            policy={
                "dynamic": [
                    {
                        "site": "omega_prime/missing.py:1:missing",
                        "names": ["OMEGA_FAKE"],
                    }
                ]
            },
        )
    assert caught.value.error_class == "StaleDynamicPolicy"


def test_token_selector_is_reviewed_but_never_emitted_as_an_inventory_name(
    tmp_path: Path,
) -> None:
    import ast

    source = "import os\n\ndef read(args):\n    return os.environ.get(args.token_env)\n"
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    )
    digest = ast_source_digest(function)
    policy = {
        "metadata": {
            "token_selectors": [
                {
                    "site": "omega_prime/consumer.py:4:args.token_env",
                    "symbol": "read",
                    "source_sha256": digest,
                    "reason": "The CLI selector chooses a caller-named environment key.",
                }
            ]
        }
    }
    root = _tree(tmp_path, source, policy=policy)
    assert _collect(root) == []
    assert "| Variable | Purpose |" in render(root)
    assert "token_env" not in render(root)
    changed = source.replace("args.token_env)", "args.token_env) or 'ignored'")
    (root / "omega_prime/consumer.py").write_text(changed, encoding="utf-8")
    with pytest.raises(InventoryError) as caught:
        _collect(root)
    assert caught.value.error_class == "TokenSelectorSourceChanged"


def test_renderer_is_sorted_deduplicated_and_contains_no_values_or_provenance(
    tmp_path: Path,
) -> None:
    root = _tree(
        tmp_path,
        'import os\nA = "OMEGA_ZED"\nB = "OMEGA_ALPHA"\nos.environ.get(A, "do-not-render")\nos.environ.get(B, "credential-value")\nos.environ.get(A)\n',
        policy={
            "purposes": {
                "OMEGA_ALPHA": "Selects the alpha configuration mode.",
                "OMEGA_ZED": "Selects the zed configuration mode.",
            }
        },
    )
    rendered = render(root)
    assert rendered == render(root)
    assert rendered.index("OMEGA_ALPHA") < rendered.index("OMEGA_ZED")
    assert rendered.count("OMEGA_ZED") == 1
    assert "do-not-render" not in rendered and "credential-value" not in rendered
    assert "consumer.py" not in rendered and "| Variable | Purpose |" in rendered


def test_file_selector_purpose_derives_from_base_setting(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        'import os\nos.environ.get("OMEGA_TEST_CREDENTIAL")\nos.environ.get("OMEGA_TEST_CREDENTIAL_FILE")\n',
        policy={
            "purposes": {
                "OMEGA_TEST_CREDENTIAL": "Authenticates the reviewed test provider."
            }
        },
    )
    names = {finding.name for finding in _collect(root)}
    purposes = _purpose_map(
        json.loads(
            (root / "omega_prime/tooling/env_inventory_policy.json").read_text()
        ),
        names,
    )
    assert purposes["OMEGA_TEST_CREDENTIAL_FILE"] == (
        "Names a protected file supplying OMEGA_TEST_CREDENTIAL: "
        "Authenticates the reviewed test provider."
    )


def test_cli_check_is_read_only_and_accepts_crlf(tmp_path: Path) -> None:
    source = 'import os\nos.environ.get("OMEGA_CLI")\n'
    root = _tree(
        tmp_path,
        source,
        policy={"purposes": {"OMEGA_CLI": "Selects the command line mode."}},
    )
    out = root / "generated.md"
    expected = render(root)
    out.write_bytes(expected.replace("\n", "\r\n").encode())
    before = out.read_bytes()
    command = [
        sys.executable,
        "-m",
        "omega_prime.tooling.env_inventory",
        "--root",
        str(root),
        "--out",
        str(out),
        "--check",
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert out.read_bytes() == before
    out.unlink()
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
    assert not out.exists()
    assert "OMEGA_CLI" not in completed.stderr or "OutputDrift" in completed.stderr


def test_test_fixtures_are_not_scanned_as_production_sources(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        'import os\nos.environ.get("OMEGA_SOURCE")\n',
        policy={"purposes": {"OMEGA_SOURCE": "Selects a source fixture setting."}},
    )
    fixture = root / "omega_prime" / "tests"
    fixture.mkdir()
    (fixture / "test_fake.py").write_text(
        'import os\nos.environ.get("OMEGA_FIXTURE_ONLY")\n'
    )
    assert [item.name for item in _collect(root)] == ["OMEGA_SOURCE"]


def test_scope_facts_do_not_leak_between_functions(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        """
import os
from typing import Mapping
KEYS = ("OMEGA_SCOPE_ONE", "OMEGA_SCOPE_TWO")
def real(env: Mapping[str, str]):
    for key in KEYS:
        env.get(key)
def ordinary(env):
    env.get("OMEGA_FALSE_POSITIVE")
""",
        policy={
            "purposes": {
                "OMEGA_SCOPE_ONE": "Selects the first scoped test setting.",
                "OMEGA_SCOPE_TWO": "Selects the second scoped test setting.",
            }
        },
    )
    assert [item.name for item in _collect(root)] == [
        "OMEGA_SCOPE_ONE",
        "OMEGA_SCOPE_TWO",
    ]


def test_helper_policy_requires_a_unique_definition_and_proven_call(
    tmp_path: Path,
) -> None:
    source = """
from typing import Mapping
def flag(env, name):
    return True
def read(env: Mapping[str, str]):
    return flag(env, "OMEGA_HELPER")
"""
    root = _tree(
        tmp_path,
        source,
        policy={
            "helpers": _helper_entry(source, "flag"),
            "purposes": {
                "OMEGA_HELPER": "Selects the helper-derived inventory setting."
            },
        },
    )
    assert [item.name for item in _collect(root)] == ["OMEGA_HELPER"]
    changed = source.replace("return True", "return False")
    (root / "omega_prime/consumer.py").write_text(changed, encoding="utf-8")
    with pytest.raises(InventoryError) as caught:
        _collect(root)
    assert caught.value.error_class == "InvalidHelperDefinition"
    (root / "omega_prime/consumer.py").write_text(
        source + "\ndef flag(env, name):\n    return False\n", encoding="utf-8"
    )
    with pytest.raises(InventoryError) as caught:
        _collect(root)
    assert caught.value.error_class == "InvalidHelperDefinition"


def test_function_local_os_alias_does_not_escape_its_scope(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        """
def read():
    import os as local_os
    return local_os.environ.get("OMEGA_LOCAL")
def unrelated():
    return local_os.environ.get("OMEGA_NOT_LOCAL")
""",
        policy={
            "purposes": {
                "OMEGA_LOCAL": "Selects the function-local environment setting."
            }
        },
    )
    assert [item.name for item in _collect(root)] == ["OMEGA_LOCAL"]


def test_environment_attribute_is_shared_across_class_methods(tmp_path: Path) -> None:
    source = """
import os
from typing import Mapping
class Client:
    def __init__(self, env: Mapping[str, str] | None = None):
        self._env = env if env is not None else os.environ
    def fetch(self):
        return self._env.get("OMEGA_CLASS_ENV")
"""
    root = _tree(
        tmp_path,
        source,
        policy={
            "purposes": {
                "OMEGA_CLASS_ENV": "Selects the setting read by the client method."
            }
        },
    )
    assert [item.name for item in _collect(root)] == ["OMEGA_CLASS_ENV"]


def test_dynamic_inventory_site_is_bound_to_exact_function_source(
    tmp_path: Path,
) -> None:
    import ast

    source = "from typing import Mapping\ndef get(env: Mapping[str, str]):\n    return env.get(make_name())\n"
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    )
    digest = ast_source_digest(function)
    policy = {
        "purposes": {"OMEGA_DYNAMIC": "Selects a reviewed dynamic test setting."},
        "dynamic": [
            {
                "site": "omega_prime/consumer.py:3:mapping.get",
                "symbol": "get",
                "source_sha256": digest,
                "names": ["OMEGA_DYNAMIC"],
                "reason": "Maps this finite reviewed test decoder branch to its setting name.",
            }
        ],
    }
    root = _tree(tmp_path, source, policy=policy)
    assert [item.name for item in _collect(root)] == ["OMEGA_DYNAMIC"]
    changed = source.replace("make_name()", "other_name()")
    (root / "omega_prime/consumer.py").write_text(changed, encoding="utf-8")
    with pytest.raises(InventoryError) as caught:
        _collect(root)
    assert caught.value.error_class == "DynamicSourceChanged"


def test_source_bound_supplements_and_exact_ci_exclusions(tmp_path: Path) -> None:
    import ast

    source = "import os\ndef receipt():\n    return os.environ['CI_TEST_KEY']\n"
    ci_tree = ast.parse(source)
    function = next(
        node for node in ast.walk(ci_tree) if isinstance(node, ast.FunctionDef)
    )
    digest = ast_source_digest(function)
    root = _tree(
        tmp_path,
        "pass\n",
        policy={
            "exclusions": [
                {
                    "site": "omega_prime/scripts/ci_receipt.py:3:os.environ.subscription",
                    "symbol": "receipt",
                    "source_sha256": digest,
                    "names": ["CI_TEST_KEY"],
                    "reason": "Reads this exact key exclusively for a synthetic CI receipt protocol.",
                }
            ],
        },
    )
    ci = root / "omega_prime/scripts/ci_receipt.py"
    ci.parent.mkdir(parents=True)
    ci.write_text(source, encoding="utf-8")
    assert (
        collect(
            root,
            json.loads(
                (root / "omega_prime/tooling/env_inventory_policy.json").read_text()
            ),
        )
        == []
    )
    ci.write_text(source.replace("CI_TEST_KEY", "CI_OTHER_KEY"), encoding="utf-8")
    with pytest.raises(InventoryError) as caught:
        collect(
            root,
            json.loads(
                (root / "omega_prime/tooling/env_inventory_policy.json").read_text()
            ),
        )
    assert caught.value.error_class == "InvalidExclusion"

    dynamic = "from typing import Mapping\ndef read(env: Mapping[str, str]):\n    return env.items()\n"
    dynamic_tree = ast.parse(dynamic)
    dynamic_function = next(
        node for node in ast.walk(dynamic_tree) if isinstance(node, ast.FunctionDef)
    )
    dynamic_digest = ast_source_digest(dynamic_function)
    root = _tree(
        tmp_path / "supplement-case",
        dynamic,
        policy={
            "supplements": [
                {
                    "site": "omega_prime/consumer.py:3:environment.items",
                    "symbol": "read",
                    "source_sha256": dynamic_digest,
                    "classification": "excluded",
                    "reason": "This exact dynamic consumer is isolated child-fixture transport, not a setting.",
                }
            ],
        },
    )
    assert _collect(root) == []
    (root / "omega_prime/consumer.py").write_text(
        dynamic.replace("return env.items()", "return list(env.items())"),
        encoding="utf-8",
    )
    with pytest.raises(InventoryError) as caught:
        _collect(root)
    assert caught.value.error_class == "DynamicSourceChanged"


def test_production_checkout_is_collectable() -> None:
    policy = json.loads(
        (ROOT / "omega_prime/tooling/env_inventory_policy.json").read_text()
    )
    findings = collect(ROOT, policy)
    assert findings
    purposes = _purpose_map(policy, {item.name for item in findings})
    assert all(item.name in purposes for item in findings)


def test_committed_inventory_check_contract() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(ROOT), env.get("PYTHONPATH", "")) if item
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "omega_prime.tooling.env_inventory",
            "--root",
            ".",
            "--out",
            "docs/configuration.md",
            "--check",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_provider_env_vars_mapping_is_collected(tmp_path: Path) -> None:
    root = tmp_path
    provider = root / "omega_prime/providers"
    provider.mkdir(parents=True)
    (provider / "prime.py").write_text(
        '_ENV_VARS = {"provider": ("OMEGA_PROVIDER_TOKEN",)}\n'
    )
    (root / "omega_prime/tooling").mkdir()
    policy_path = root / "omega_prime/tooling/env_inventory_policy.json"
    policy_path.write_text("{}")
    policy = {
        "purposes": {
            "OMEGA_PROVIDER_TOKEN": "Provides the provider credential to the adapter."
        }
    }
    assert [item.name for item in collect(root, policy)] == ["OMEGA_PROVIDER_TOKEN"]

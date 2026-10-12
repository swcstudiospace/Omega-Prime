# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Generate the Cursor plugin and MCP manifest set from checked-in sources.

`python -m omega_prime.tooling.plugin --write` renders, from the curated
commands plus the checked-in skills and routines:

- `.cursor-plugin/marketplace.json`: one plugin, `omega-prime`, at the repo root;
- `.cursor-plugin/plugin.json`: version and repository from `pyproject.toml`;
- `mcp.json`: exactly one stdio server, `omega-prime`, launched with `bash`;
- `omega_prime/grokbot/plugin/commands/omega-*.md`: one per catalog entry;
- `omega_prime/grokbot/plugin/mcp.https.example.json`: the documented HTTPS
  alternative, which `plugin.json` never references.

`--check` exits 1 when committed files merely differ from a fresh render
(stale, missing or owned-orphan files that `--write` repairs); 3 when the
content itself is invalid (a skill fails the platform's YAML frontmatter
rules, names collide, the template and disk disagree, generated text looks
like a secret, or a committed file is otherwise not what `--write` repairs);
and 4 when the generator cannot run in this interpreter (a missing PyYAML or
an import crash). `--verify` spawns the declared stdio command and a loopback
HTTPS host and exits 0 only when both entries answer. No network.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from omega_prime.commands import COMMANDS
from omega_prime.commands.generated import (
    CommandSpec,
    GenerationError,
    checked_in_routines,
    checked_in_skills,
    generate_commands,
)
from omega_prime.grokbot.manifest import STDIO_COMMAND, lint_template
from omega_prime.grokbot.secret_guard import SecretLeakError, assert_no_secrets
from omega_prime.tooling.fs import atomic_write_text

GENERATOR = "python -m omega_prime.tooling.plugin"

PLUGIN_NAME = "omega-prime"
MARKETPLACE_NAME = "omega-prime-marketplace"
SERVER_NAME = "omega-prime"
TOOL_NAME = "omega_command"
PLUGIN_ROOT_VAR = "${CURSOR_PLUGIN_ROOT}"

MARKETPLACE = ".cursor-plugin/marketplace.json"
PLUGIN_MANIFEST = ".cursor-plugin/plugin.json"
MCP_JSON = "mcp.json"
COMMANDS_DIR = "omega_prime/grokbot/plugin/commands"
HTTPS_EXAMPLE = "omega_prime/grokbot/plugin/mcp.https.example.json"
SKILLS_DIR = "omega_prime/skills"
ROUTINES_DIR = "omega_prime/routines"
TEMPLATE = "omega_prime/grokbot/templates/OMEGA_PRIME.md"
PYPROJECT = "pyproject.toml"

HTTPS_EXAMPLE_URL = "https://omega.example.com/mcp"
HTTPS_TOKEN_VAR = "OMEGA_PRIME_MCP_TOKEN"
HTTPS_PATH = "/mcp"

FILE_MODE = 0o644
MAX_DESCRIPTION = 1024
MAX_NAME = 64

# Cursor loads these extensions from a commands directory.
COMMAND_EXTENSIONS = (".md", ".mdc", ".markdown", ".txt")

# Plugin and marketplace names: lowercase kebab-case, alphanumerics, hyphens
# and periods, starting and ending alphanumeric (cursor.com plugin reference).
PLUGIN_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
# Agent Skills and command names: lowercase letters, digits, single hyphens.
COMPONENT_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# The only accepted Authorization value in the HTTPS example (AS-07).
HEADER_PLACEHOLDER_RE = re.compile(r"Bearer \$\{(?:env:)?[A-Z][A-Z0-9_]*\}")
# Every `$` in the HTTPS example must open one of these placeholders.
PLACEHOLDER_RE = re.compile(r"\$\{(?:env:)?[A-Z][A-Z0-9_]*\}")
# Fixed stand-in for the one validated header member, scanned instead of it.
HEADER_SENTINEL = '"validated-header-placeholder": "VALIDATED_PLACEHOLDER"'

_FIRST_REPLY_TIMEOUT = 300.0
_REPLY_TIMEOUT = 120.0
_SHUTDOWN_TIMEOUT = 15.0

_YAML_INDICATORS = frozenset("-?:,[]{}#&*!|>'\"%@`")
_YAML_KEYWORDS = frozenset(
    {"true", "false", "yes", "no", "on", "off", "y", "n", "null", "~"}
)
_NUMBER_RE = re.compile(r"[-+]?(?:\.?[0-9][0-9_.eE+-]*|\.inf|\.nan)", re.IGNORECASE)


class PluginError(ValueError):
    """Generation aborted. Each problem names its source path, never a value."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


class PluginEnvError(PluginError):
    """The generator cannot run in this interpreter (missing PyYAML, and kin).

    A subclass of `PluginError` so callers that only distinguish failure from
    success keep working; `--check` maps it to its own environment exit.
    """


# --- catalog ----------------------------------------------------------------


def catalog(root: Path) -> tuple[CommandSpec, ...]:
    """Curated commands, then generated ones sorted by name.

    Empty or absent skill or routine inputs abort instead of producing an
    empty catalog. Raises `GenerationError` naming the offending source.
    """
    if not checked_in_skills(root):
        raise GenerationError(
            f"{SKILLS_DIR}: no checked-in skills; refusing to emit an empty catalog"
        )
    if not checked_in_routines(root):
        raise GenerationError(
            f"{ROUTINES_DIR}: no checked-in routines; refusing to emit an empty catalog"
        )
    curated = tuple(COMMANDS)
    generated = generate_commands(root, [spec.name for spec in curated])
    return (*curated, *generated)


# --- rendering --------------------------------------------------------------


def render(root: str | Path) -> dict[str, str]:
    """Render every generated file as `{repo-relative path: text}`, sorted.

    Pure apart from reading the checked-in sources under `root`. Raises
    `PluginError` listing every problem found; nothing is written.
    """
    repo = Path(root)
    problems: list[str] = []
    project = _project(repo, problems)
    problems.extend(skill_conformance(repo))
    problems.extend(template_problems(repo))
    try:
        specs = catalog(repo)
    except GenerationError as exc:
        problems.append(str(exc))
        specs = ()
    launcher = _launcher()
    if not (repo / launcher).is_file():
        problems.append(f"{launcher}: the declared stdio launcher is missing")
    if problems:
        raise PluginError(problems)

    files: dict[str, str] = {
        MARKETPLACE: _json(_marketplace(project)),
        PLUGIN_MANIFEST: _json(_plugin_manifest(project)),
        MCP_JSON: _json(_mcp_json()),
        HTTPS_EXAMPLE: _json(_https_example()),
    }
    for spec in specs:
        files[f"{COMMANDS_DIR}/{spec.name}.md"] = _command_file(spec)

    problems.extend(_rendered_problems(repo, files))
    if problems:
        raise PluginError(problems)
    return dict(sorted(files.items()))


def _project(repo: Path, problems: list[str]) -> dict[str, Any]:
    try:
        data = tomllib.loads((repo / PYPROJECT).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        problems.append(f"{PYPROJECT}: cannot read ({type(exc).__name__})")
        return {}
    project = data.get("project")
    if not isinstance(project, dict):
        problems.append(f"{PYPROJECT}: has no [project] table")
        return {}
    raw_urls = project.get("urls")
    # An absent or malformed [project.urls] leaves repository/homepage unset,
    # which the loop below reports as a pyproject problem.
    urls: dict[str, Any] = raw_urls if isinstance(raw_urls, dict) else {}
    authors = project.get("authors")
    license_ = project.get("license")
    found = {
        "version": project.get("version"),
        "description": project.get("description"),
        "repository": urls.get("Repository"),
        "homepage": urls.get("Homepage"),
        "author": (
            authors[0].get("name")
            if isinstance(authors, list) and authors and isinstance(authors[0], dict)
            else None
        ),
        "license": license_.get("text") if isinstance(license_, dict) else license_,
    }
    for key, value in found.items():
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{PYPROJECT}: project {key} is missing")
    keywords = project.get("keywords", [])
    if not isinstance(keywords, list) or not all(
        isinstance(item, str) for item in keywords
    ):
        problems.append(f"{PYPROJECT}: project keywords must be a list of strings")
        keywords = []
    found["keywords"] = list(keywords)
    return found


def _launcher() -> str:
    parts = shlex.split(STDIO_COMMAND)
    return parts[1]


def stdio_server() -> dict[str, Any]:
    """The declared stdio server: `manifest.STDIO_COMMAND` under the plugin root."""
    command, launcher, *rest = shlex.split(STDIO_COMMAND)
    return {
        "type": "stdio",
        "command": command,
        "args": [f"{PLUGIN_ROOT_VAR}/{launcher}", *rest],
    }


def _marketplace(project: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": MARKETPLACE_NAME,
        "owner": {"name": project["author"]},
        "metadata": {
            "description": project["description"],
            "version": project["version"],
        },
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": "./",
                "description": project["description"],
            }
        ],
    }


def _plugin_manifest(project: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "version": project["version"],
        "description": project["description"],
        "author": {"name": project["author"]},
        "homepage": project["homepage"],
        "repository": project["repository"],
        "license": project["license"],
        "keywords": project["keywords"],
        "skills": SKILLS_DIR,
        "commands": COMMANDS_DIR,
        "mcpServers": MCP_JSON,
    }


def _mcp_json() -> dict[str, Any]:
    return {"mcpServers": {SERVER_NAME: stdio_server()}}


def _https_example() -> dict[str, Any]:
    return {
        "mcpServers": {
            SERVER_NAME: {
                "url": HTTPS_EXAMPLE_URL,
                "headers": {"Authorization": f"Bearer ${{{HTTPS_TOKEN_VAR}}}"},
            }
        }
    }


def _json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _yaml_scalar(value: str) -> str:
    """`value` as a YAML scalar: plain when unambiguous, else double-quoted."""
    plain = (
        bool(value)
        and value == value.strip()
        and value.isprintable()
        and value[0] not in _YAML_INDICATORS
        and ": " not in value
        and " #" not in value
        and not value.endswith(":")
        and value.lower() not in _YAML_KEYWORDS
        and _NUMBER_RE.fullmatch(value) is None
    )
    if plain:
        return value
    # A JSON string is a valid YAML double-quoted scalar.
    return json.dumps(value, ensure_ascii=not value.isprintable())


def _command_file(spec: CommandSpec) -> str:
    # The kind, not the source path: a long path run trips the secret gate's
    # bare-token heuristic, and the gate is never loosened.
    origin = {
        "curated": "the curated catalog",
        "skill": "a checked-in skill",
        "routine": "a checked-in routine",
    }[spec.kind]
    return (
        "---\n"
        f"name: {spec.name}\n"
        f"description: {_yaml_scalar(spec.summary)}\n"
        "---\n"
        "\n"
        f"<!-- DO NOT EDIT — generated by `{GENERATOR}` from {origin}. "
        "Rebuild with `--write`; `--check` fails when this file drifts. -->\n"
        "\n"
        f"Call the `{TOOL_NAME}` tool of the `{SERVER_NAME}` MCP server with "
        f"`command` set to `/{spec.name}`, followed by a space and the user's "
        "arguments when there are any. Do not reimplement the steps; report "
        "the tool result.\n"
    )


# --- validation -------------------------------------------------------------


def _yaml() -> Any:
    try:
        import yaml
    except ImportError:
        raise PluginEnvError(
            [
                "PyYAML is required for the frontmatter conformance check; "
                "install requirements-lock.txt"
            ]
        ) from None
    return yaml


def _frontmatter_block(text: str) -> str | None:
    lines = text.lstrip("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:index])
    return None


def _frontmatter_problems(source: str, text: str, expected_name: str) -> list[str]:
    """Platform rules: YAML mapping, `name` equals `expected_name`, description."""
    block = _frontmatter_block(text)
    if block is None:
        return [f"{source}: has no closed YAML frontmatter"]
    yaml = _yaml()
    try:
        mapping = yaml.safe_load(block)
    except yaml.YAMLError:
        return [f"{source}: frontmatter is not valid YAML"]
    if not isinstance(mapping, dict):
        return [f"{source}: frontmatter is not a YAML mapping"]
    problems: list[str] = []
    name = mapping.get("name")
    if not isinstance(name, str) or name != expected_name:
        problems.append(f"{source}: frontmatter name must equal {expected_name!r}")
    elif COMPONENT_NAME_RE.fullmatch(name) is None or len(name) > MAX_NAME:
        problems.append(f"{source}: frontmatter name is not lowercase kebab-case")
    description = mapping.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append(f"{source}: frontmatter has no description")
    elif len(description) > MAX_DESCRIPTION:
        problems.append(
            f"{source}: description is longer than {MAX_DESCRIPTION} characters"
        )
    return problems


def skill_conformance(root: Path) -> list[str]:
    """Every checked-in skill passes `yaml.safe_load` with `name` == folder."""
    problems: list[str] = []
    for folder in checked_in_skills(root):
        source = f"{SKILLS_DIR}/{folder}/SKILL.md"
        try:
            text = (root / source).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{source}: cannot read ({type(exc).__name__})")
            continue
        problems.extend(_frontmatter_problems(source, text, folder))
    return problems


def template_problems(root: Path) -> list[str]:
    """Template and disk disagreements, both directions (`lint_template`)."""
    lint = lint_template(root)
    problems = [
        f"{TEMPLATE}: lists skill {name!r} with no {SKILLS_DIR}/{name}/SKILL.md"
        for name in lint.missing_skills
    ]
    problems += [
        f"{TEMPLATE}: lists routine {name!r} with no {ROUTINES_DIR}/{name}.md"
        for name in lint.missing_routines
    ]
    problems += [
        f"{TEMPLATE}: does not list checked-in skill {SKILLS_DIR}/{name}/SKILL.md"
        for name in lint.unlisted_skills
    ]
    problems += [
        f"{TEMPLATE}: does not list checked-in routine {ROUTINES_DIR}/{name}.md"
        for name in lint.unlisted_routines
    ]
    return problems


def secret_problems(path: str, text: str) -> list[str]:
    """Run the unchanged secret gate over `text` (a `str`, never a `Path`).

    The HTTPS example's one Authorization member is validated by exact
    placeholder shape first and replaced by a fixed sentinel before the scan;
    an example that fails the shape check is not scanned and fails instead.
    """
    scanned = text
    if path == HTTPS_EXAMPLE:
        problems = https_example_problems(text)
        if problems:
            return problems
        document = json.loads(text)
        value = document["mcpServers"][SERVER_NAME]["headers"]["Authorization"]
        member = f"{json.dumps('Authorization')}: {json.dumps(value)}"
        if text.count(member) != 1:
            return [f"{path}: the Authorization header is not in canonical form"]
        scanned = text.replace(member, HEADER_SENTINEL)
    try:
        assert_no_secrets(scanned, name=path)
    except SecretLeakError as exc:
        return [str(exc)]
    return []


def mcp_json_problems(text: str) -> list[str]:
    """`mcp.json` is exactly the one declared stdio server, nothing else."""
    try:
        document = json.loads(text)
    except ValueError:
        return [f"{MCP_JSON}: is not valid JSON"]
    if document != _mcp_json():
        return [
            f"{MCP_JSON}: must declare exactly the {SERVER_NAME} stdio server "
            "(bash plus the launcher arguments; no env, cwd, url or headers)"
        ]
    return []


def https_example_problems(text: str) -> list[str]:
    """Shape rules for the HTTPS example. Messages never quote a value."""
    path = HTTPS_EXAMPLE
    try:
        document = json.loads(text)
    except ValueError:
        return [f"{path}: is not valid JSON"]
    servers = document.get("mcpServers") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != {"mcpServers"}
        or not isinstance(servers, dict)
        or list(servers) != [SERVER_NAME]
    ):
        return [f"{path}: must declare exactly the {SERVER_NAME} server"]
    server = servers[SERVER_NAME]
    if not isinstance(server, dict) or set(server) != {"url", "headers"}:
        return [f"{path}: the server must carry only url and headers"]
    problems: list[str] = []
    url = server["url"]
    parts = urlsplit(url) if isinstance(url, str) else None
    if (
        parts is None
        or parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path != HTTPS_PATH
        or parts.query
        or parts.fragment
    ):
        problems.append(f"{path}: url must be https://<host>{HTTPS_PATH}")
    headers = server["headers"]
    if not isinstance(headers, dict) or list(headers) != ["Authorization"]:
        problems.append(f"{path}: headers must hold only Authorization")
    else:
        value = headers["Authorization"]
        if not isinstance(value, str) or HEADER_PLACEHOLDER_RE.fullmatch(value) is None:
            problems.append(
                f"{path}: the Authorization header is not an exact "
                "Bearer ${VAR} placeholder"
            )
    if text.count("$") != sum(
        match.group(0).count("$") for match in PLACEHOLDER_RE.finditer(text)
    ):
        problems.append(f"{path}: holds a `$` outside a ${{VAR}} placeholder")
    return problems


def plugin_manifest_problems(root: Path, text: str) -> list[str]:
    """Kebab name, relative existing component paths, no HTTPS example."""
    try:
        document = json.loads(text)
    except ValueError:
        return [f"{PLUGIN_MANIFEST}: is not valid JSON"]
    if not isinstance(document, dict):
        return [f"{PLUGIN_MANIFEST}: is not a JSON object"]
    problems: list[str] = []
    name = document.get("name")
    if not isinstance(name, str) or PLUGIN_NAME_RE.fullmatch(name) is None:
        problems.append(f"{PLUGIN_MANIFEST}: name is not lowercase kebab-case")
    for key in ("skills", "commands", "mcpServers"):
        value = document.get(key)
        if not isinstance(value, str) or not relative_path_ok(value):
            problems.append(
                f"{PLUGIN_MANIFEST}: {key} must be a relative path without '..'"
            )
        elif not (root / value).exists():
            problems.append(f"{PLUGIN_MANIFEST}: {key} path {value} does not exist")
    if PurePosixPath(HTTPS_EXAMPLE).name in text:
        problems.append(f"{PLUGIN_MANIFEST}: must not reference the HTTPS example")
    return problems


def relative_path_ok(value: str) -> bool:
    """True for a non-empty POSIX path that is relative and has no `..`."""
    if not value or value.startswith(("/", "~")) or "\\" in value:
        return False
    return ".." not in PurePosixPath(value).parts


def _rendered_problems(root: Path, files: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    for path, text in files.items():
        problems.extend(secret_problems(path, text))
        if path.startswith(f"{COMMANDS_DIR}/"):
            stem = PurePosixPath(path).stem
            problems.extend(_frontmatter_problems(path, text, stem))
    problems.extend(mcp_json_problems(files[MCP_JSON]))
    manifest = json.loads(files[PLUGIN_MANIFEST])
    for key in ("skills", "commands", "mcpServers"):
        if not relative_path_ok(manifest[key]):
            problems.append(f"{PLUGIN_MANIFEST}: {key} is not a relative path")
    if not (root / SKILLS_DIR).is_dir():
        problems.append(f"{SKILLS_DIR}: the skills directory is missing")
    if PurePosixPath(HTTPS_EXAMPLE).name in files[PLUGIN_MANIFEST]:
        problems.append(f"{PLUGIN_MANIFEST}: must not reference the HTTPS example")
    return problems


# --- drift ------------------------------------------------------------------


def _command_files(root: Path) -> list[str]:
    directory = root / COMMANDS_DIR
    if not directory.is_dir():
        return []
    return sorted(
        f"{COMMANDS_DIR}/{path.name}"
        for path in directory.iterdir()
        if path.suffix in COMMAND_EXTENSIONS and not path.is_dir()
    )


def _owned_orphan(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith("omega-") and name.endswith(".md")


def drift(root: Path, fresh: Mapping[str, str]) -> list[str]:
    """Committed-tree problems against a fresh render. Never echoes content."""
    problems: list[str] = []
    for path, text in fresh.items():
        target = root / path
        try:
            current = target.read_bytes()
        except FileNotFoundError:
            problems.append(f"{path} is missing; rerun with --write")
            continue
        except OSError as exc:
            problems.append(f"{path}: cannot read ({type(exc).__name__})")
            continue
        if current != text.encode("utf-8"):
            problems.append(f"{path} is stale; rerun with --write")
        try:
            committed = current.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path}: is not UTF-8")
            continue
        problems.extend(secret_problems(path, committed))
        if path == MCP_JSON:
            problems.extend(mcp_json_problems(committed))
        elif path == PLUGIN_MANIFEST:
            problems.extend(plugin_manifest_problems(root, committed))
    for path in _command_files(root):
        if path in fresh:
            continue
        if _owned_orphan(path):
            problems.append(
                f"{path} is an orphan with no catalog entry; rerun with --write"
            )
        else:
            problems.append(
                f"{path} is not a generated command file; remove it by hand"
            )
    return problems


# Reports `--write` repairs, by construction site in `drift()`: a missing or
# stale generated file, or an owned orphan with no catalog entry. Anything
# else (unreadable or non-UTF-8 bytes, a secret or shape problem in a
# committed file, a foreign command file) needs the operator's hand.
_WRITE_FIXABLE_TAILS = (
    " is missing; rerun with --write",
    " is stale; rerun with --write",
    " is an orphan with no catalog entry; rerun with --write",
)


def has_content_problems(problems: Iterable[str]) -> bool:
    """True when `drift()` problems include content `--write` cannot repair."""
    return any(not problem.endswith(_WRITE_FIXABLE_TAILS) for problem in problems)


def write(root: Path, fresh: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Write changed files (mode 0644) and remove owned orphans only."""
    written: list[str] = []
    for path, text in fresh.items():
        target = root / path
        try:
            current = target.read_bytes()
        except OSError:
            current = None
        if current == text.encode("utf-8"):
            if (target.stat().st_mode & 0o777) != FILE_MODE:
                target.chmod(FILE_MODE)
            continue
        # Create parents with the default umask, not the private 0700 mode.
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text, mode=FILE_MODE)
        written.append(path)
    removed: list[str] = []
    for path in _command_files(root):
        if path not in fresh and _owned_orphan(path):
            (root / path).unlink()
            removed.append(path)
    return written, removed


# --- verify -----------------------------------------------------------------


class _ProtocolError(RuntimeError):
    """The stdio entry broke the JSON-RPC contract. Messages carry no output."""


class _StdioClient:
    """Minimal newline-delimited JSON-RPC 2.0 client over a child's stdio."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdin is not None and proc.stdout is not None
        self._proc = proc
        self._stdin = proc.stdin
        self._stdout = proc.stdout
        self._pending = b""
        self._next_id = 1

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    def request(
        self, method: str, params: Mapping[str, Any], timeout: float
    ) -> dict[str, Any]:
        ident = self._next_id
        self._next_id += 1
        self._send(
            {"jsonrpc": "2.0", "id": ident, "method": method, "params": dict(params)}
        )
        deadline = time.monotonic() + timeout
        while True:
            reply = self._read_message(deadline, method)
            if reply.get("id") != ident:
                continue
            if "error" in reply:
                error = reply["error"]
                code = error.get("code") if isinstance(error, dict) else None
                raise _ProtocolError(f"{method} returned JSON-RPC error {code}")
            result = reply.get("result")
            if not isinstance(result, dict):
                raise _ProtocolError(f"{method} returned no result object")
            return result

    def _send(self, message: Mapping[str, Any]) -> None:
        try:
            self._stdin.write(json.dumps(message).encode("utf-8") + b"\n")
            self._stdin.flush()
        except OSError:
            raise _ProtocolError("the stdio entry closed its stdin") from None

    def _read_message(self, deadline: float, method: str) -> dict[str, Any]:
        while b"\n" not in self._pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _ProtocolError(f"no reply to {method} before the deadline")
            readable, _, _ = select.select([self._stdout], [], [], remaining)
            if not readable:
                continue
            chunk = os.read(self._stdout.fileno(), 65536)
            if not chunk:
                code = self._proc.poll()
                raise _ProtocolError(
                    f"the stdio entry closed stdout before replying to {method}"
                    + (f" (exit {code})" if code is not None else "")
                )
            self._pending += chunk
        line, _, self._pending = self._pending.partition(b"\n")
        try:
            message = json.loads(line)
        except ValueError:
            raise _ProtocolError("stdout carried a line that is not JSON-RPC") from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise _ProtocolError("stdout carried a line that is not JSON-RPC 2.0")
        return message


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.stdin is not None:
        with contextlib.suppress(OSError):
            proc.stdin.close()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_SHUTDOWN_TIMEOUT)
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, sig)
        try:
            proc.wait(timeout=_SHUTDOWN_TIMEOUT)
            return
        except subprocess.TimeoutExpired:
            continue


def _diagnostic(log: Path) -> str:
    """The child's stderr tail, redacted and secret-gated before publication."""
    from omega_prime.grokbot.oneclick import _bounded_diagnostic

    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _bounded_diagnostic(text, ()).strip()


def verify_stdio(root: Path) -> tuple[bool, str]:
    """Spawn the declared `mcp.json` command; `initialize` plus `/omega-help`."""
    try:
        text = (root / MCP_JSON).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"cannot read {MCP_JSON} ({type(exc).__name__})"
    problems = mcp_json_problems(text)
    if problems:
        return False, "; ".join(problems)
    try:
        expected = sorted(spec.name for spec in catalog(root))
    except GenerationError as exc:
        return False, str(exc)
    server = json.loads(text)["mcpServers"][SERVER_NAME]
    argv = [
        str(part).replace(PLUGIN_ROOT_VAR, str(root))
        for part in (server["command"], *server["args"])
    ]
    with tempfile.TemporaryDirectory(prefix="omega-prime-plugin-verify-") as tmp:
        log = Path(tmp) / "stdio-entry.log"
        with log.open("wb") as stderr:
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=tmp,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    start_new_session=True,
                )
            except OSError as exc:
                name = type(exc).__name__
                return False, f"cannot spawn the declared command ({name})"
            try:
                reason = _stdio_exchange(_StdioClient(proc), expected)
            finally:
                _stop(proc)
        if reason is None:
            count = len(expected)
            return True, f"initialize and /omega-help answered ({count} commands)"
        tail = _diagnostic(log)
        return False, reason + (f"\n{tail}" if tail else "")


def _stdio_exchange(client: _StdioClient, expected: list[str]) -> str | None:
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "omega-prime-plugin-verify", "version": "1"},
            },
            _FIRST_REPLY_TIMEOUT,
        )
        capabilities = initialized.get("capabilities")
        if not isinstance(capabilities, dict) or "tools" not in capabilities:
            return "initialize reported no tools capability"
        client.notify("notifications/initialized", {})
        result = client.request(
            "tools/call",
            {"name": TOOL_NAME, "arguments": {"command": "/omega-help"}},
            _REPLY_TIMEOUT,
        )
    except _ProtocolError as exc:
        return str(exc)
    if result.get("isError") is True:
        return f"tools/call {TOOL_NAME} /omega-help returned an error"
    try:
        payload = json.loads(result["content"][0]["text"])
        names = sorted(entry["name"] for entry in payload["result"]["commands"])
    except (KeyError, IndexError, TypeError, ValueError):
        return f"tools/call {TOOL_NAME} /omega-help returned an unexpected shape"
    if payload.get("ok") is not True:
        return f"/omega-help failed ({payload.get('error')})"
    if names != expected:
        return (
            f"/omega-help listed {len(names)} commands; the catalog has {len(expected)}"
        )
    return None


def verify_https(root: Path) -> tuple[bool, str]:
    """Validate the HTTPS example, then verify a loopback bearer host at `/mcp`.

    The loopback host is `oneclick.run_self_test`: an isolated install served
    with `--transport sse` and a minted 0600 token, checked by
    `verify.run_verification(transports=("sse", "http"), require_auth=True)`.
    Its report goes to stderr. A public host is not provable here.
    """
    try:
        text = (root / HTTPS_EXAMPLE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"cannot read {HTTPS_EXAMPLE} ({type(exc).__name__})"
    problems = secret_problems(HTTPS_EXAMPLE, text)
    if problems:
        return False, "; ".join(problems)
    from omega_prime.grokbot.oneclick import run_self_test

    with contextlib.redirect_stdout(sys.stderr):
        code = run_self_test(root)
    if code != 0:
        return False, f"loopback bearer host verification exited {code}"
    return True, f"example valid; loopback bearer host answered at {HTTPS_PATH}"


VERIFIERS = (("stdio", verify_stdio), ("https", verify_https))


def verify(root: Path, *, json_report: bool = False) -> int:
    """Run both entries. Exit 0 only when both resolve.

    Human output goes to stderr. With `json_report` one JSON object goes to
    stdout and nothing else does.
    """
    entries: dict[str, dict[str, Any]] = {}
    for name, check in VERIFIERS:
        print(f"plugin --verify: checking the {name} entry", file=sys.stderr)
        ok, detail = check(root)
        entries[name] = {"ok": ok, "detail": detail}
        state = "ok" if ok else "FAILED"
        print(f"plugin --verify: {name} entry {state}: {detail}", file=sys.stderr)
    failed = [name for name, entry in entries.items() if not entry["ok"]]
    if json_report:
        report = {"ok": not failed, "failed": failed, "entries": entries}
        print(json.dumps(report, sort_keys=True), flush=True)
    if failed:
        print(f"plugin --verify: failed entries: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("plugin --verify: both entries resolve", file=sys.stderr)
    return 0


# --- CLI --------------------------------------------------------------------


def _report(problems: Iterable[str], prefix: str) -> None:
    for problem in problems:
        print(f"{prefix}: {problem}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Check, write or verify the generated plugin set.

    Exit 0 when the tree is up to date (or `--write`/`--verify` succeeded).
    `--check` exits 1 when committed files merely differ from a fresh render
    (stale, missing or owned-orphan files that `--write` repairs); 3 when the
    content itself is invalid (a `--write` run would fail the same way); and 4
    when the generator cannot run in this interpreter (a missing PyYAML or an
    import crash). Usage errors exit 2 via argparse. Diagnostics go to stderr;
    stdout stays empty outside `--verify --json`.
    """
    parser = argparse.ArgumentParser(prog="omega-prime-plugin")
    parser.add_argument("--root", type=Path, default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--json", action="store_true", help="with --verify only")
    args = parser.parse_args(argv)
    if args.json and not args.verify:
        parser.error("--json requires --verify")
    root = (args.root or Path(__file__).resolve().parents[2]).resolve()

    if args.verify:
        return verify(root, json_report=args.json)

    label = "plugin --check" if args.check else "plugin --write"
    try:
        fresh = render(root)
    except PluginEnvError as exc:
        _report(exc.problems, label)
        print(f"{label}: environment breakage; nothing written", file=sys.stderr)
        return 4
    except PluginError as exc:
        _report(exc.problems, label)
        print(f"{label}: generation failed; nothing written", file=sys.stderr)
        return 3
    except Exception:
        traceback.print_exc()
        return 4
    if args.check:
        try:
            problems = drift(root, fresh)
        except Exception:
            traceback.print_exc()
            return 4
        if problems:
            _report(problems, label)
            if has_content_problems(problems):
                print(
                    f"{label}: content invalid; fix the named source files, "
                    "then rerun --check (--write cannot repair content errors)",
                    file=sys.stderr,
                )
                return 3
            return 1
        print(f"{label}: {len(fresh)} generated files up to date")
        return 0
    written, removed = write(root, fresh)
    unowned = [path for path in _command_files(root) if path not in fresh]
    print(
        f"{label}: wrote {len(written)} of {len(fresh)} files, "
        f"removed {len(removed)} orphans"
    )
    if unowned:
        _report(
            (
                f"{path} is not a generated command file; remove it by hand"
                for path in unowned
            ),
            label,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMMANDS_DIR",
    "HTTPS_EXAMPLE",
    "MARKETPLACE",
    "MCP_JSON",
    "PLUGIN_MANIFEST",
    "PluginEnvError",
    "PluginError",
    "catalog",
    "drift",
    "has_content_problems",
    "main",
    "mcp_json_problems",
    "plugin_manifest_problems",
    "relative_path_ok",
    "render",
    "secret_problems",
    "skill_conformance",
    "stdio_server",
    "template_problems",
    "verify",
    "verify_https",
    "verify_stdio",
    "write",
]

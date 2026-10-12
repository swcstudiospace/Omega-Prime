"""Declared-command stdio proof from a fresh-clone layout (GROK-03 SC5, D-02).

The tree is ``isolated_layout(REPO)`` with every ``git ls-files`` entry of the
working tree overlaid on ``layout.install``: the root plugin set (``mcp.json``,
``.cursor-plugin/``, ``pyproject.toml``, ``requirements-lock.txt``,
``.gitmodules``) and the generated ``omega_prime/grokbot/plugin/`` tree arrive
with their working-tree bytes, so uncommitted edits to tracked generator output
are proven too. Gitlinks become empty directories and there is no ``.git``, so
the launcher stamps ``prime_agent_commit`` as ``none`` and never fetches.

Warm venv: a fresh ``--without-pip`` venv at ``ROOT/.venv`` whose single
``.pth`` lists the overlay root, then the running interpreter's site-packages.
The real ``.venv`` is only read as a path entry and is never modified, and its
editable-install finder is not loaded, so ``omega_prime`` always imports from
the overlay. The stamp is the launcher's own ``STAMP_CODE``, read from the
overlaid script and run with the same base interpreter the launcher selects (a
private ``python3.12`` shim first on ``PATH``). A matching stamp means no pip,
no network and no rebuild. No case here installs over the network; the cold
install is the operator observation in ``docs/grok-bot-native.md`` (Bootstrap
modes).

The spawned argv is exactly the overlay's ``mcp.json`` ``command`` plus
``args`` with ``${CURSOR_PLUGIN_ROOT}`` substituted. ``initialize`` is written
to stdin at spawn, before the bootstrap finishes. Stdout is read raw and
unfiltered, and every byte must be newline-delimited JSON-RPC 2.0.

LOCAL-PROOF pointer (parent-executed; this module runs none of it):
  1. ``.venv/bin/python -m omega_prime.tooling.plugin --check``: exit 0.
  2. ``.venv/bin/python -m omega_prime.tooling.catalog --check``: exit 0.
  3. ``.venv/bin/python -m pytest omega_prime/tests/test_grokbot_plugin_stdio.py
     -q -rA``: every case passes.
  4. Second-run no-op: in a tmp fresh-clone tree (never the developer
     checkout), run ``bash omega_prime/scripts/grokbot-1click.sh
     --bootstrap-only`` twice. Pass: stamp bytes and mtime unchanged, "stamp
     matches" on stderr, and no pip call.
  5. Failure classes: one observed exit each for 10 missing_prerequisite, 11
     install_failed, 12 catalog_drift and 13 host_unreachable. Each prints one
     JSON class line on stderr and leaves stdout empty.
  6. Loopback HTTPS entry: ``plugin --verify`` in a tmp tree runs the bearer
     self-test at ``/mcp`` with 0 failures, and bearer is required.
  7. ``.venv/bin/python -m omega_prime.setup_check --root .``: exit 0.
  8. The full CONTRIBUTING gate list is green, with tests at or above the 2352
     baseline plus the new tests, and evals at 26.
Live Grok Bot or Cursor import, attachment, palette entries and timing stay
human UAT. Nothing here is a live-attachment claim.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import sysconfig
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from omega_prime.grokbot.local_validation import LocalLayout, isolated_layout
from omega_prime.grokbot.manifest import find_repo_root
from omega_prime.tooling.plugin import (
    COMMANDS_DIR,
    HTTPS_EXAMPLE,
    MARKETPLACE,
    MCP_JSON,
    PLUGIN_MANIFEST,
    PLUGIN_ROOT_VAR,
    PYPROJECT,
    SERVER_NAME,
    TOOL_NAME,
    catalog,
)

REPO = find_repo_root()
LAUNCHER_REL = "omega_prime/scripts/grokbot-1click.sh"
LOCK_REL = "requirements-lock.txt"
ROOT_PLUGIN_SET = (
    MCP_JSON,
    MARKETPLACE,
    PLUGIN_MANIFEST,
    PYPROJECT,
    LOCK_REL,
    HTTPS_EXAMPLE,
)
EXPECTED_COMMANDS = 46
SKILL_COMMAND = "omega-code-review"
GITLINK, SYMLINK = "160000", "120000"
FIRST_REPLY_TIMEOUT = 300.0
REPLY_TIMEOUT = 120.0
SHUTDOWN_TIMEOUT = 15.0
WARM_LOG = "environment is up to date (stamp matches)"
BUILD_LOG = "building a fresh environment"
INITIALIZE_ID = 1
INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": INITIALIZE_ID,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "declared-command-stdio-proof", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}


# --- fresh-clone layout -----------------------------------------------------


def _tracked(repo: Path) -> dict[str, str]:
    """Tracked path -> git mode, from the index (``git ls-files --stage``)."""
    git = shutil.which("git")
    assert git is not None, "git is required to build the fresh-clone overlay"
    out = subprocess.run(
        [git, "-C", str(repo), "ls-files", "-z", "--stage"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    ).stdout
    entries: dict[str, str] = {}
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        entries[os.fsdecode(path)] = meta.split(b" ", 1)[0].decode()
    return entries


def _clear(dest: Path) -> None:
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)


def _overlay(repo: Path, install: Path, tracked: Mapping[str, str]) -> list[str]:
    """Copy tracked working-tree entries onto ``install``; return copied files.

    Worktree bytes win over HEAD. A path deleted in the worktree is skipped, a
    gitlink becomes an empty directory, and a symlink keeps its target.
    """
    copied: list[str] = []
    for rel, mode in sorted(tracked.items()):
        src, dest = repo / rel, install / rel
        if mode == GITLINK:
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if mode == SYMLINK:
            if not src.is_symlink():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            _clear(dest)
            dest.symlink_to(os.readlink(src))
            continue
        if src.is_symlink() or not src.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.is_dir():
            _clear(dest)
        shutil.copy2(src, dest)
        copied.append(rel)
    return copied


def _base_interpreter() -> Path:
    """The interpreter the real ``.venv`` was built from (same ABI as its deps)."""
    return Path(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))


def _stamp_code(launcher: Path) -> str:
    """The launcher's own ``STAMP_CODE`` (a single-quoted bash literal)."""
    text = launcher.read_text(encoding="utf-8")
    match = re.search(r"^STAMP_CODE='([^']*)'$", text, re.MULTILINE)
    assert match is not None, f"STAMP_CODE not found in {LAUNCHER_REL}"
    return match.group(1)


@dataclass
class FreshClone:
    layout: LocalLayout
    root: Path
    bin: Path
    tracked: dict[str, str]
    copied: list[str] = field(default_factory=list)

    @property
    def shim(self) -> Path:
        return self.bin / "python3.12"

    @property
    def venv(self) -> Path:
        return self.root / ".venv"

    @property
    def stamp(self) -> Path:
        return self.root / ".venv.omega-prime-bootstrap.json"

    @property
    def build(self) -> Path:
        return self.root / ".venv.build"

    def env(self) -> dict[str, str]:
        """The layout's explicit env. The shim comes first on ``PATH``, so the
        launcher's ``python3.12`` candidate is the base interpreter, and the
        overlay is importable only through ``ROOT/.venv``."""
        env = dict(self.layout.env)
        env.pop("PYTHONPATH", None)
        env["PATH"] = os.pathsep.join([str(self.bin), "/usr/bin", "/bin"])
        return env

    def declared(self) -> tuple[list[str], list[str]]:
        """``(raw, substituted)`` command plus args from the overlay's mcp.json."""
        document = json.loads((self.root / MCP_JSON).read_text(encoding="utf-8"))
        server = document["mcpServers"][SERVER_NAME]
        assert server.get("type") == "stdio"
        raw = [str(part) for part in (server["command"], *server["args"])]
        argv = [part.replace(PLUGIN_ROOT_VAR, str(self.root)) for part in raw]
        return raw, argv

    def launcher_stamp(self) -> bytes:
        """What the launcher computes as ``want`` for this tree (no git: none)."""
        code = _stamp_code(self.root / LAUNCHER_REL)
        return subprocess.run(
            [str(self.shim), "-c", code, str(self.root), "none"],
            cwd=self.layout.base,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        ).stdout


def _fresh_clone(layout: LocalLayout) -> FreshClone:
    root = layout.install.resolve()
    for directory in (layout.base / "tmp", layout.audit / "run"):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    tracked = _tracked(REPO)
    copied = _overlay(REPO, root, tracked)
    bindir = layout.base / "bin"
    bindir.mkdir()
    tree = FreshClone(
        layout=layout, root=root, bin=bindir, tracked=tracked, copied=copied
    )
    tree.shim.symlink_to(_base_interpreter())
    for tool in ("bash", "git", "flock"):
        found = shutil.which(tool, path=tree.env()["PATH"])
        assert found is not None, f"{tool} is required by the declared command"
    usable = subprocess.run(
        [str(tree.shim), "-c", "import venv, ensurepip"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    assert usable.returncode == 0, (
        "the base interpreter lacks venv/ensurepip; the launcher would exit 10"
    )
    _warm_venv(tree)
    return tree


def _warm_venv(tree: FreshClone) -> None:
    """A no-network venv at ``ROOT/.venv`` plus the launcher-computed stamp."""
    subprocess.run(
        [str(_base_interpreter()), "-m", "venv", "--without-pip", str(tree.venv)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    query = "import sys, sysconfig; print(sysconfig.get_path('purelib'));"
    query += " print('%d.%d' % sys.version_info[:2])"
    out = subprocess.run(
        [str(tree.venv / "bin" / "python"), "-I", "-c", query],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    purelib, version = out.strip().splitlines()
    assert version == f"{sys.version_info[0]}.{sys.version_info[1]}", (
        "run this module with the interpreter of the real .venv (.venv/bin/python)"
    )
    deps: list[str] = []
    for key in ("purelib", "platlib"):
        path = sysconfig.get_path(key)
        if path not in deps:
            deps.append(path)
    entries = [str(tree.root), *deps]
    Path(purelib, "omega_prime_fresh_clone.pth").write_text(
        "".join(f"{entry}\n" for entry in entries), encoding="utf-8"
    )
    tree.stamp.write_bytes(tree.launcher_stamp())
    tree.stamp.chmod(0o600)


@pytest.fixture(scope="module")
def fresh_clone() -> Iterator[FreshClone]:
    with isolated_layout(REPO) as layout:
        yield _fresh_clone(layout)


# --- raw JSON-RPC session ---------------------------------------------------


class StdoutClosed(Exception):
    """The spawned command closed stdout before the awaited reply."""


def _jsonrpc(line: bytes) -> dict[str, Any] | None:
    try:
        message = json.loads(line)
    except ValueError:
        return None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return None
    return message


class Session:
    """The declared command under raw newline-delimited JSON-RPC.

    Every stdout byte lands in ``raw`` unfiltered; a non-JSON-RPC line fails
    the test the moment it is read.
    """

    def __init__(self, argv: list[str], *, env: Mapping[str, str], cwd: Path) -> None:
        self.argv = argv
        self.log = cwd / f"stderr-{uuid.uuid4().hex}.log"
        self.raw = bytearray()
        self._offset = 0
        self._eof = False
        self._closed = False
        with self.log.open("wb") as stderr:
            self.proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                start_new_session=True,
            )

    def stderr(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def send(self, message: Mapping[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def _next_line(self, deadline: float) -> bytes | None:
        assert self.proc.stdout is not None
        while True:
            end = self.raw.find(b"\n", self._offset)
            if end >= 0:
                line = bytes(self.raw[self._offset : end])
                self._offset = end + 1
                return line
            if self._eof:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(f"no reply before the deadline\n{self.stderr()[-4000:]}")
            readable, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not readable:
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                self._eof = True
                continue
            self.raw += chunk

    def reply(self, ident: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            line = self._next_line(deadline)
            if line is None:
                code = self.proc.poll()
                raise StdoutClosed(
                    f"stdout closed before reply {ident} (exit {code})\n"
                    + self.stderr()[-4000:]
                )
            message = _jsonrpc(line)
            assert message is not None, f"stdout carried non-JSON-RPC {line[:200]!r}"
            if message.get("id") != ident or "method" in message:
                continue
            assert "error" not in message, message["error"]
            result = message["result"]
            assert isinstance(result, dict)
            return result

    def request(
        self, ident: int, method: str, params: Mapping[str, Any], timeout: float
    ) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        return self.reply(ident, timeout)

    def command(self, ident: int, text: str) -> dict[str, Any]:
        """``tools/call omega_command``; the parsed command payload."""
        result = self.request(
            ident,
            "tools/call",
            {"name": TOOL_NAME, "arguments": {"command": text}},
            REPLY_TIMEOUT,
        )
        assert result.get("isError") is not True, result
        payload = json.loads(result["content"][0]["text"])
        assert isinstance(payload, dict)
        return payload

    def close(self) -> int | None:
        """EOF on stdin, drain stdout to EOF, then reap the whole group."""
        proc = self.proc
        if self._closed:
            return proc.returncode
        self._closed = True
        if proc.stdin is not None:
            with contextlib.suppress(OSError):
                proc.stdin.close()
        assert proc.stdout is not None
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT
        while not self._eof and (remaining := deadline - time.monotonic()) > 0:
            readable, _, _ = select.select([proc.stdout], [], [], remaining)
            if readable:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if chunk:
                    self.raw += chunk
                else:
                    self._eof = True
        try:
            proc.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, sig)
                try:
                    proc.wait(timeout=SHUTDOWN_TIMEOUT)
                    break
                except subprocess.TimeoutExpired:
                    continue
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.stdout.close()
        return proc.returncode


def _assert_protocol_only(raw: bytes) -> None:
    """Every stdout byte is newline-delimited JSON-RPC 2.0; zero other lines."""
    assert not raw or raw.endswith(b"\n"), f"unterminated stdout tail {raw[-200:]!r}"
    lines = raw.split(b"\n")[:-1]
    stray = [line for line in lines if _jsonrpc(line) is None]
    assert len(stray) == 0, (
        f"{len(stray)} non-JSON-RPC stdout line(s); first {stray[0][:200]!r}"
    )


def _handshake(session: Session, timeout: float) -> None:
    """``initialize`` was already written at spawn; await it, then initialized."""
    initialized = session.reply(INITIALIZE_ID, timeout)
    assert "tools" in initialized["capabilities"]
    session.send(INITIALIZED)


def _assert_help(tree: FreshClone, payload: Mapping[str, Any]) -> None:
    assert payload["ok"] is True, payload
    names = [entry["name"] for entry in payload["result"]["commands"]]
    assert len(names) == EXPECTED_COMMANDS
    files = sorted(path.stem for path in (tree.root / COMMANDS_DIR).iterdir())
    assert sorted(names) == files
    assert sorted(names) == sorted(spec.name for spec in catalog(tree.root))


def _spawn(tree: FreshClone, argv: list[str], env: Mapping[str, str]) -> Session:
    cwd = tree.layout.base / f"cwd-{uuid.uuid4().hex}"
    cwd.mkdir()
    session = Session(argv, env=env, cwd=cwd)
    session.send(INITIALIZE)
    return session


# --- tests ------------------------------------------------------------------


def test_overlay_is_the_tracked_working_tree(fresh_clone: FreshClone) -> None:
    """Provenance (T-77.1-18): tracked bytes, root plugin set, own warm venv."""
    tree = fresh_clone
    for rel in (*ROOT_PLUGIN_SET, LAUNCHER_REL):
        assert tree.tracked.get(rel) in ("100644", "100755"), f"{rel} is untracked"
        assert rel in tree.copied, f"{rel} missing from the overlay"
    commands = [rel for rel in tree.copied if rel.startswith(f"{COMMANDS_DIR}/")]
    assert len(commands) == EXPECTED_COMMANDS
    for rel in tree.copied:
        source = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
        copy = hashlib.sha256((tree.root / rel).read_bytes()).hexdigest()
        assert source == copy, f"{rel} differs from the working tree"
    assert not (tree.root / ".git").exists()
    assert not tree.venv.is_symlink()
    assert tree.venv.resolve() != (REPO / ".venv").resolve()
    assert (tree.venv / "pyvenv.cfg").is_file()
    assert tree.stamp.read_bytes() == tree.launcher_stamp()


def test_declared_command_answers_help_and_a_generated_skill(
    fresh_clone: FreshClone,
) -> None:
    """R22 + R24: early initialize, /omega-help (46) and /omega-code-review."""
    tree = fresh_clone
    raw, argv = tree.declared()
    assert sum(part.count(PLUGIN_ROOT_VAR) for part in raw) == 1
    assert all("${" not in part for part in argv)
    stamp_before = tree.stamp.read_bytes()
    mtime_before = tree.stamp.stat().st_mtime_ns
    expected_skill = {spec.name: spec for spec in catalog(tree.root)}[SKILL_COMMAND]
    assert expected_skill.kind == "skill"

    session = _spawn(tree, argv, tree.env())
    try:
        assert session.proc.args == argv
        _handshake(session, FIRST_REPLY_TIMEOUT)
        _assert_help(tree, session.command(2, "/omega-help"))
        skill = session.command(3, f"/{SKILL_COMMAND}")
        assert skill["ok"] is True, skill
        assert skill["command"] == SKILL_COMMAND
        assert skill["result"]["kind"] == "skill"
        assert skill["result"]["source"] == expected_skill.source
        assert (tree.root / expected_skill.source).is_file()
        assert skill["result"]["instructions"].strip()
    finally:
        session.close()

    _assert_protocol_only(bytes(session.raw))
    stderr = session.stderr()
    assert WARM_LOG in stderr
    assert BUILD_LOG not in stderr
    assert tree.stamp.read_bytes() == stamp_before
    assert tree.stamp.stat().st_mtime_ns == mtime_before
    assert not tree.build.exists()


def test_declared_command_serves_the_work_root_flag(fresh_clone: FreshClone) -> None:
    """WR-01: ``--work-root`` as a flag, not the env var, selects the workspace."""
    tree = fresh_clone
    token = uuid.uuid4().hex
    work = tree.layout.base / f"flag-work-{token}"
    work.mkdir()
    expected = {"path": f"owned-proof-{token}.txt", "content": f"owned {token}\n"}
    (work / expected["path"]).write_text(expected["content"], encoding="utf-8")
    env = tree.env()
    env.pop("OMEGA_PRIME_WORK_ROOT", None)
    _, declared = tree.declared()
    argv = [*declared, "--work-root", str(work)]

    session = _spawn(tree, argv, env)
    try:
        assert session.argv[: len(declared)] == declared
        assert session.proc.args == argv
        _handshake(session, FIRST_REPLY_TIMEOUT)
        result = session.request(
            2,
            "tools/call",
            {"name": "read_file", "arguments": {"path": expected["path"]}},
            REPLY_TIMEOUT,
        )
        assert result.get("isError") is not True, result
        assert len(result["content"]) == 1
        assert json.loads(result["content"][0]["text"]) == expected
    finally:
        session.close()

    _assert_protocol_only(bytes(session.raw))

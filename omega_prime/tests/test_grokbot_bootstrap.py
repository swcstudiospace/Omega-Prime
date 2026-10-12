"""Shell bootstrap of ``omega_prime/scripts/grokbot-1click.sh`` (D-09, D-04).

Hermetic: every run gets a private ``PATH`` holding stub interpreters, a stub
``git`` and only the coreutils the launcher needs. The stub venv interpreter
records pip, probe, plugin and oneclick calls and writes noise to stdout and
drains stdin, so a missing fd swap or a child reading the protocol stream shows
up as stray stdout or a lost frame. Nothing touches the network; the real cold
install is an opt-in slow observation kept out of this file.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from omega_prime.grokbot.manifest import find_repo_root

REPO = find_repo_root()
SCRIPT_REL = Path("omega_prime") / "scripts" / "grokbot-1click.sh"
BASH = shutil.which("bash")
TOOLS = (
    "cat",
    "chmod",
    "cp",
    "dirname",
    "flock",
    "mkdir",
    "mv",
    "readlink",
    "rm",
    "sleep",
)
FRAME = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
REPLY = b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
TIMEOUT = 60

_RECORD = """record() { local IFS=$'\\t'; printf '%s\\n' "$*" >> "$STUB_LOG"; }
"""

_BASE_PY = """if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    record venv "$3"
    cat > /dev/null
    echo "venv stdout noise"
    [ "${STUB_VENV_EXIT:-0}" = 0 ] || exit "$STUB_VENV_EXIT"
    mkdir -p "$3/bin"
    cp "$STUB_VENV_PYTHON" "$3/bin/python"
    chmod 755 "$3/bin/python"
    exit 0
fi
exec "$STUB_REAL_PY" "$@"
"""

_OLD_PY = """if [ "$1" = "-c" ]; then echo 3.10.14; exit 0; fi
exit 1
"""

_VENV_PY = """if [ "$1" = "-I" ] && [ "$2" = "-c" ]; then
    record probe "$0"
    exit "${STUB_PROBE_EXIT:-0}"
fi
if [ "$1" = "-m" ]; then
    module="$2"
    shift 2
    case "$module" in
        pip)
            record pip "$@"
            cat > /dev/null
            echo "Collecting stub (pip stdout noise)"
            [ -z "${STUB_PIP_STARTED:-}" ] || : > "$STUB_PIP_STARTED"
            sleep "${STUB_PIP_SLEEP:-0}"
            exit "${STUB_PIP_EXIT:-0}" ;;
        omega_prime.tooling.plugin)
            record plugin "$@"
            cat > /dev/null
            case " $* " in
                *" --check "*)
                    echo "plugin --check stdout noise"
                    [ "${STUB_CHECK_EXIT:-0}" = 0 ] || echo "stale: mcp.json" >&2
                    exit "${STUB_CHECK_EXIT:-0}" ;;
                *" --verify "*)
                    echo '{"report":"verify"}'
                    exit "${STUB_VERIFY_EXIT:-0}" ;;
            esac
            exit 2 ;;
        omega_prime.grokbot.oneclick)
            record oneclick "$@"
            record env "${OMEGA_PRIME_STATE_DIR:-}" "${OMEGA_PRIME_WORK_ROOT:-}"
            if IFS= read -r line; then
                printf '%s\\n' '{"jsonrpc":"2.0","id":1,"result":{}}'
            fi
            exit 0 ;;
    esac
fi
exec "$STUB_REAL_PY" "$@"
"""

_GIT = """record git "$@"
cat > /dev/null
echo "git stdout noise"
exit "${STUB_GIT_EXIT:-0}"
"""


@dataclass
class Tree:
    tmp: Path
    root: Path
    bin: Path
    home: Path
    log: Path
    venv_stub: Path

    @property
    def script(self) -> Path:
        return self.root / SCRIPT_REL

    @property
    def symlink(self) -> Path:
        return self.root / "scripts" / "grokbot-1click.sh"

    @property
    def venv(self) -> Path:
        return self.root / ".venv"

    @property
    def stamp(self) -> Path:
        return self.root / ".venv.omega-prime-bootstrap.json"

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.home),
            "LC_ALL": "C",
            "STUB_LOG": str(self.log),
            "STUB_REAL_PY": sys.executable,
            "STUB_VENV_PYTHON": str(self.venv_stub),
        }
        env.update(extra)
        return env

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        text = self.log.read_text(encoding="utf-8")
        return [line.split("\t") for line in text.splitlines()]

    def calls_of(self, kind: str) -> list[list[str]]:
        return [call[1:] for call in self.calls() if call[0] == kind]

    def clear_log(self) -> None:
        self.log.unlink(missing_ok=True)


def _stub(path: Path, body: str) -> Path:
    assert BASH is not None
    path.write_text(f"#!{BASH}\n{_RECORD}{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _tree(
    tmp_path: Path,
    *,
    python: str | None = "ok",
    git: bool = True,
    checkout: bool = False,
    prime_agent: bool = False,
) -> Tree:
    assert BASH is not None, "bash is required"
    root = tmp_path / "repo"
    script = root / SCRIPT_REL
    script.parent.mkdir(parents=True)
    shutil.copy2(REPO / SCRIPT_REL, script)
    script.chmod(0o755)
    (root / "scripts").mkdir()
    (root / "scripts" / "grokbot-1click.sh").symlink_to(Path("..") / SCRIPT_REL)
    (root / "pyproject.toml").write_text('[project]\nname = "stub"\n', encoding="utf-8")
    (root / "requirements-lock.txt").write_text("stub==1.0\n", encoding="utf-8")
    (root / "redteam").mkdir()
    if checkout:
        (root / ".git").mkdir()
    if prime_agent:
        (root / "prime-agent").mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in TOOLS:
        found = shutil.which(tool)
        assert found is not None, f"{tool} is required by the launcher"
        (bindir / tool).symlink_to(found)
    if python == "ok":
        _stub(bindir / "python3", _BASE_PY)
    elif python == "old":
        _stub(bindir / "python3", _OLD_PY)
    if git:
        _stub(bindir / "git", _GIT)
    venv_stub = _stub(tmp_path / "stub-venv-python", _VENV_PY)
    home = tmp_path / "home"
    home.mkdir()
    return Tree(
        tmp_path, root.resolve(), bindir, home, tmp_path / "calls.log", venv_stub
    )


def _run(
    tree: Tree,
    *args: str,
    script: Path | str | None = None,
    cwd: Path | None = None,
    stdin: bytes = b"",
    extra_env: dict[str, str] | None = None,
    **env: str,
) -> subprocess.CompletedProcess[bytes]:
    assert BASH is not None
    return subprocess.run(
        [BASH, str(script or tree.script), *args],
        cwd=cwd or tree.tmp,
        env=tree.env(**(extra_env or {}), **env),
        input=stdin,
        capture_output=True,
        timeout=TIMEOUT,
        check=False,
    )


def _class_line(stderr: bytes) -> dict[str, object]:
    found = []
    for line in stderr.decode("utf-8", "replace").splitlines():
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and "class" in payload:
            found.append(payload)
    assert len(found) == 1, stderr.decode("utf-8", "replace")
    return found[0]


def _assert_failed(
    proc: subprocess.CompletedProcess[bytes], code: int, klass: str
) -> dict[str, object]:
    assert proc.returncode == code, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    line = _class_line(proc.stderr)
    assert line["class"] == klass
    assert line["exit"] == code
    return line


def _pip_lock_installs(tree: Tree) -> list[list[str]]:
    return [call for call in tree.calls_of("pip") if "-r" in call]


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


@pytest.mark.parametrize(
    ("python", "git", "missing"),
    [
        (None, True, "python3>=3.11"),
        ("old", True, "python3>=3.11"),
        ("ok", False, "git"),
    ],
)
def test_missing_prerequisite_exits_10_before_any_install(
    tmp_path: Path, python: str | None, git: bool, missing: str
) -> None:
    tree = _tree(tmp_path, python=python, git=git)
    proc = _run(tree, "--bootstrap", "--transport", "stdio")
    line = _assert_failed(proc, 10, "missing_prerequisite")
    assert isinstance(line["missing"], list)
    assert missing in line["missing"]
    assert not tree.stamp.exists()
    assert not tree.venv.exists()
    assert tree.calls_of("pip") == [] and tree.calls_of("oneclick") == []


@pytest.mark.parametrize(
    "failure",
    [{"STUB_PIP_EXIT": "1"}, {"STUB_PROBE_EXIT": "1"}, {"STUB_VENV_EXIT": "1"}],
    ids=["pip", "probe", "venv"],
)
def test_install_failure_exits_11_with_no_stamp_and_no_half_built_venv(
    tmp_path: Path, failure: dict[str, str]
) -> None:
    tree = _tree(tmp_path)
    proc = _run(tree, "--bootstrap", "--transport", "stdio", extra_env=failure)
    _assert_failed(proc, 11, "install_failed")
    assert not tree.stamp.exists()
    assert not tree.venv.exists()
    assert not (tree.root / ".venv.build").exists()
    assert tree.calls_of("oneclick") == []


def test_failed_rebuild_keeps_the_previous_venv(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    assert _run(tree, "--bootstrap-only").returncode == 0
    marker = tree.venv / "previous-marker"
    marker.write_text("kept\n", encoding="utf-8")
    before = tree.stamp.read_bytes()
    (tree.root / "requirements-lock.txt").write_text("stub==2.0\n", encoding="utf-8")

    proc = _run(tree, "--bootstrap-only", STUB_PIP_EXIT="1")

    _assert_failed(proc, 11, "install_failed")
    assert marker.read_text(encoding="utf-8") == "kept\n"
    assert tree.stamp.read_bytes() == before
    assert not (tree.root / ".venv.build").exists()


def _operator_venv(tree: Tree) -> dict[str, tuple[bytes, int]]:
    """Hand-made ``.venv`` with no bootstrap stamp; returns its file snapshot."""
    (tree.venv / "bin").mkdir(parents=True)
    shutil.copy2(tree.venv_stub, tree.venv / "bin" / "python")
    (tree.venv / "dev-extras-sentinel").write_text("mine\n", encoding="utf-8")
    return _tree_files(tree.venv)


def _tree_files(top: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(top)): (path.read_bytes(), path.stat().st_mode)
        for path in sorted(top.rglob("*"))
        if path.is_file()
    }


def test_unstamped_operator_venv_is_used_unchanged(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    before = _operator_venv(tree)

    proc = _run(tree, "--bootstrap", "--transport", "stdio")

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    assert b"operator-managed .venv" in proc.stderr
    assert _tree_files(tree.venv) == before
    assert tree.calls_of("pip") == [] and tree.calls_of("venv") == []
    assert tree.calls_of("probe") == [[str(tree.venv / "bin" / "python")]]
    assert tree.calls_of("plugin") == [["--root", str(tree.root), "--check"]]
    assert len(tree.calls_of("oneclick")) == 1
    # Ownership stays with the operator: the bootstrap never stamps it.
    assert not tree.stamp.exists()
    assert not (tree.root / ".venv.build").exists()


def test_unstamped_operator_venv_failing_the_probe_exits_11_untouched(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    before = _operator_venv(tree)

    proc = _run(tree, "--bootstrap", "--transport", "stdio", STUB_PROBE_EXIT="1")

    line = _assert_failed(proc, 11, "install_failed")
    assert line["step"] == "operator_venv"
    assert str(tree.venv).encode() in proc.stderr
    assert _tree_files(tree.venv) == before
    assert tree.calls_of("pip") == [] and tree.calls_of("venv") == []
    assert tree.calls_of("oneclick") == []
    assert not tree.stamp.exists()


def test_owned_venv_with_an_interrupted_swap_stamp_is_rebuilt(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    assert _run(tree, "--bootstrap-only").returncode == 0
    marker = tree.venv / "previous-marker"
    marker.write_text("old\n", encoding="utf-8")
    # A start killed between the ownership placeholder and the final stamp.
    tree.stamp.write_text('{"owner":"omega-prime-bootstrap"}\n', encoding="utf-8")
    tree.clear_log()

    proc = _run(tree, "--bootstrap-only")

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    assert len(_pip_lock_installs(tree)) == 1
    assert not marker.exists()
    assert json.loads(tree.stamp.read_text(encoding="utf-8"))["lock_sha256"]


def test_catalog_drift_exits_12_naming_stale_paths_before_serving(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    proc = _run(tree, "--bootstrap", "--transport", "stdio", STUB_CHECK_EXIT="1")
    _assert_failed(proc, 12, "catalog_drift")
    assert b"stale: mcp.json" in proc.stderr
    assert tree.calls_of("plugin") == [["--root", str(tree.root), "--check"]]
    assert tree.calls_of("oneclick") == []


@pytest.mark.parametrize(("verifier", "expected"), [("0", 0), ("1", 13), ("7", 13)])
def test_verify_mode_delegates_to_the_plugin_verifier(
    tmp_path: Path, verifier: str, expected: int
) -> None:
    tree = _tree(tmp_path)
    proc = _run(tree, "--verify", STUB_VERIFY_EXIT=verifier)
    if expected == 0:
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
        assert proc.stdout == b""
    else:
        line = _assert_failed(proc, 13, "host_unreachable")
        assert line["verifier_exit"] == int(verifier)
    plugin = tree.calls_of("plugin")
    assert plugin[-1] == ["--root", str(tree.root), "--verify"]
    # The shell opens no JSON-RPC exchange of its own: the host is never started.
    assert tree.calls_of("oneclick") == []


def test_verify_json_report_is_the_only_stdout(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    proc = _run(tree, "--verify", "--json")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b'{"report":"verify"}\n'
    assert tree.calls_of("plugin")[-1] == [
        "--root",
        str(tree.root),
        "--verify",
        "--json",
    ]


def test_warm_start_skips_install_and_a_changed_lock_rebuilds(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    assert _run(tree, "--bootstrap-only").returncode == 0
    assert stat.S_IMODE(tree.stamp.stat().st_mode) == 0o600
    first = _snapshot(tree.stamp)
    time.sleep(0.05)
    tree.clear_log()

    warm = _run(tree, "--bootstrap-only")
    assert warm.returncode == 0, warm.stderr.decode("utf-8", "replace")
    assert warm.stdout == b""
    assert tree.calls_of("pip") == [] and tree.calls_of("venv") == []
    assert tree.calls_of("probe") == [[str(tree.venv / "bin" / "python")]]
    assert _snapshot(tree.stamp) == first

    marker = tree.venv / "previous-marker"
    marker.write_text("old\n", encoding="utf-8")
    (tree.root / "requirements-lock.txt").write_text("stub==2.0\n", encoding="utf-8")
    tree.clear_log()

    rebuilt = _run(tree, "--bootstrap-only")
    assert rebuilt.returncode == 0, rebuilt.stderr.decode("utf-8", "replace")
    assert rebuilt.stdout == b""
    assert len(_pip_lock_installs(tree)) == 1
    assert ["install", "--no-deps", "-e", str(tree.root)] in tree.calls_of("pip")
    assert tree.calls_of("venv") == [[str(tree.root / ".venv.build")]]
    assert not marker.exists()
    assert (tree.venv / "bin" / "python").is_file()
    assert tree.stamp.read_bytes() != first[0]
    assert stat.S_IMODE(tree.stamp.stat().st_mode) == 0o600
    assert sorted(p.name for p in tree.root.glob(".venv*")) == [
        ".venv",
        ".venv.in-use.lock",
        ".venv.lock",
        ".venv.omega-prime-bootstrap.json",
    ]


def test_killed_first_start_leaves_no_stamp_and_the_next_start_completes(
    tmp_path: Path,
) -> None:
    assert BASH is not None
    tree = _tree(tmp_path)
    started = tmp_path / "pip-started"
    proc = subprocess.Popen(
        [BASH, str(tree.script), "--bootstrap-only"],
        cwd=tree.tmp,
        env=tree.env(STUB_PIP_SLEEP="30", STUB_PIP_STARTED=str(started)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + TIMEOUT
        while not started.exists():
            assert proc.poll() is None, "bootstrap ended before pip started"
            assert time.monotonic() < deadline, "pip never started"
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=TIMEOUT)
        assert proc.stdout is not None
        assert proc.stdout.read() == b""
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=TIMEOUT)
        if proc.stdout is not None:
            proc.stdout.close()
    assert not tree.stamp.exists()
    assert not tree.venv.exists()

    resumed = _run(tree, "--bootstrap-only")
    assert resumed.returncode == 0, resumed.stderr.decode("utf-8", "replace")
    assert resumed.stdout == b""
    assert tree.stamp.is_file()
    assert (tree.venv / "bin" / "python").is_file()
    assert not (tree.root / ".venv.build").exists()


def _hold_in_use_shared(path: Path) -> object:
    """Hold a shared flock like a serving launcher; the caller must close it."""
    fcntl = pytest.importorskip("fcntl")
    handle = open(path, "a", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
    return handle


def _release_in_use(holder: object) -> None:
    fcntl = pytest.importorskip("fcntl")
    assert hasattr(holder, "fileno") and hasattr(holder, "close")
    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    holder.close()  # type: ignore[attr-defined]


def test_stale_stamp_rebuild_refuses_while_a_server_is_running(
    tmp_path: Path,
) -> None:
    """WR-02: a rebuild never swaps the venv a running server serves from."""
    tree = _tree(tmp_path)
    assert _run(tree, "--bootstrap-only").returncode == 0
    marker = tree.venv / "previous-marker"
    marker.write_text("old\n", encoding="utf-8")
    (tree.root / "requirements-lock.txt").write_text("stub==2.0\n", encoding="utf-8")
    before = _tree_files(tree.venv)
    holder = _hold_in_use_shared(tree.root / ".venv.in-use.lock")
    try:
        proc = _run(tree, "--bootstrap-only")
    finally:
        _release_in_use(holder)
    line = _assert_failed(proc, 11, "install_failed")
    assert line["step"] == "venv_in_use"
    assert proc.stdout == b""
    assert b"stop running Omega Prime servers" in proc.stderr
    assert _tree_files(tree.venv) == before
    assert marker.is_file()
    assert not (tree.root / ".venv.build").exists()
    assert list(tree.root.glob(".venv.old.*")) == []


def test_warm_start_is_not_blocked_by_a_running_server(tmp_path: Path) -> None:
    """WR-02: a matching stamp serves warm while a server holds the lock."""
    tree = _tree(tmp_path)
    assert _run(tree, "--bootstrap-only").returncode == 0
    tree.clear_log()
    holder = _hold_in_use_shared(tree.root / ".venv.in-use.lock")
    try:
        proc = _run(tree, "--bootstrap-only")
    finally:
        _release_in_use(holder)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    assert tree.calls_of("pip") == [] and tree.calls_of("venv") == []


def test_plain_serve_is_not_blocked_by_a_running_server(tmp_path: Path) -> None:
    """IN-05: the non-bootstrap serve path shares the lock, never races swap."""
    tree = _tree(tmp_path)
    assert _run(tree, "--bootstrap-only").returncode == 0
    holder = _hold_in_use_shared(tree.root / ".venv.in-use.lock")
    try:
        proc = _run(tree, "--transport", "stdio", stdin=FRAME)
    finally:
        _release_in_use(holder)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == REPLY


def test_concurrent_cold_starts_serialize_on_one_install(tmp_path: Path) -> None:
    assert BASH is not None
    tree = _tree(tmp_path)
    procs = [
        subprocess.Popen(
            [BASH, str(tree.script), "--bootstrap", "--transport", "stdio"],
            cwd=tree.tmp,
            env=tree.env(STUB_PIP_SLEEP="1"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for _ in range(2)
    ]
    try:
        results = [proc.communicate(timeout=TIMEOUT) for proc in procs]
    finally:
        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=TIMEOUT)
    for proc, (out, err) in zip(procs, results, strict=True):
        assert proc.returncode == 0, err.decode("utf-8", "replace")
        assert out == b""
    assert len(_pip_lock_installs(tree)) == 1
    assert len(tree.calls_of("venv")) == 1
    assert len(tree.calls_of("oneclick")) == 2
    assert tree.stamp.is_file()


def test_bootstrap_ignores_hostile_modules_in_the_callers_cwd(
    tmp_path: Path,
) -> None:
    """WR-01: a hostile launch directory never shadows bootstrap imports."""
    tree = _tree(tmp_path)
    hostile = tmp_path / "hostile-cwd"
    hostile.mkdir()
    marker = hostile / "imported.txt"
    (hostile / "json.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "raise ImportError('hostile json')\n",
        encoding="utf-8",
    )
    for package in ("venv", "pip"):
        shadow = hostile / package
        shadow.mkdir()
        (shadow / "__init__.py").write_text(
            f"raise ImportError('hostile {package}')\n", encoding="utf-8"
        )
    shadow_pkg = hostile / "omega_prime"
    shadow_pkg.mkdir()
    (shadow_pkg / "__init__.py").write_text(
        "raise ImportError('hostile omega_prime')\n", encoding="utf-8"
    )
    before = sorted(p.name for p in hostile.iterdir())

    proc = _run(tree, "--bootstrap-only", cwd=hostile)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    assert not marker.exists()
    assert tree.stamp.is_file()
    assert sorted(p.name for p in hostile.iterdir()) == before

@pytest.mark.parametrize("via", ["script", "symlink", "relative-symlink"])
def test_root_comes_from_the_script_location_not_the_cwd(
    tmp_path: Path, via: str
) -> None:
    tree = _tree(tmp_path)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "pyproject.toml").write_text(
        '[project]\nname = "decoy"\n', encoding="utf-8"
    )
    (decoy / "requirements-lock.txt").write_text("decoy==1.0\n", encoding="utf-8")
    before = sorted(p.name for p in decoy.iterdir())
    choices: dict[str, Path | str] = {
        "script": tree.script,
        "symlink": tree.symlink,
        "relative-symlink": os.path.relpath(tree.symlink, decoy),
    }
    script = choices[via]

    proc = _run(tree, "--bootstrap", "--transport", "stdio", script=script, cwd=decoy)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    oneclick = tree.calls_of("oneclick")
    assert len(oneclick) == 1
    argv = oneclick[0]
    assert argv[argv.index("--root") + 1] == str(tree.root)
    assert argv[-2:] == ["--transport", "stdio"]
    assert "--skip-doctor" in argv
    assert tree.calls_of("plugin")[0] == ["--root", str(tree.root), "--check"]
    assert tree.stamp.is_file()
    assert sorted(p.name for p in decoy.iterdir()) == before


@pytest.mark.parametrize("warm", [False, True], ids=["cold", "warm"])
def test_served_stdout_is_protocol_only_and_an_early_frame_is_answered(
    tmp_path: Path, warm: bool
) -> None:
    tree = _tree(tmp_path, checkout=True, prime_agent=True)
    if warm:
        assert _run(tree, "--bootstrap-only").returncode == 0
        tree.clear_log()

    proc = _run(tree, "--bootstrap", "--transport", "stdio", stdin=FRAME)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    # Bootstrap children print noise and drain stdin; none of it may reach the
    # protocol stream or swallow the frame written at spawn.
    assert proc.stdout == REPLY
    assert len(tree.calls_of("oneclick")) == 1
    assert (tree.calls_of("pip") == []) is warm


@pytest.mark.parametrize(
    ("checkout", "git_exit"),
    [(True, "0"), (True, "1"), (False, "0")],
    ids=["checkout", "checkout-git-fails", "not-a-checkout"],
)
def test_only_prime_agent_is_initialized_and_redteam_never_touched(
    tmp_path: Path, checkout: bool, git_exit: str
) -> None:
    tree = _tree(tmp_path, checkout=checkout, prime_agent=True)
    proc = _run(tree, "--bootstrap-only", STUB_GIT_EXIT=git_exit)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    git_calls = tree.calls_of("git")
    if checkout:
        assert git_calls == [
            ["-C", str(tree.root), "submodule", "update", "--init", "prime-agent"]
        ]
    else:
        assert git_calls == []
    if not checkout or git_exit != "0":
        assert b"warning" in proc.stderr.lower()
    assert all("redteam" not in arg for call in tree.calls() for arg in call)
    assert list((tree.root / "redteam").iterdir()) == []
    assert tree.stamp.is_file()


def test_state_defaults_are_exported_only_when_unset(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    assert _run(tree, "--bootstrap", "--transport", "stdio").returncode == 0
    state, work = tree.calls_of("env")[-1]
    assert state == str(tree.home / ".local" / "state" / "omega-prime")
    assert work in {"/workspace", str(tree.home)}

    tree.clear_log()
    mine = tmp_path / "mine"
    proc = _run(
        tree,
        "--bootstrap",
        "--transport",
        "stdio",
        OMEGA_PRIME_STATE_DIR=str(mine / "state"),
        OMEGA_PRIME_WORK_ROOT=str(mine / "work"),
    )
    assert proc.returncode == 0
    assert tree.calls_of("env")[-1] == [str(mine / "state"), str(mine / "work")]


@pytest.mark.parametrize(
    "args",
    [
        ("--bootstrap-only", "--verify"),
        ("--bootstrap-only", "--json"),
        ("--verify", "--port", "1"),
    ],
)
def test_conflicting_launcher_flags_are_usage_errors(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    tree = _tree(tmp_path)
    proc = _run(tree, *args)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert tree.calls() == []
    assert not tree.venv.exists()

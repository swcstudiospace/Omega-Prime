"""00058: per-job databases and self-check cleanup on the omega-ci runner image.

``omega-ci-db`` gives each PostgreSQL / GreptimeDB its own free loopback
ports and data dir, so parallel jobs on one Desktop cannot collide, and
``toolchain-check.sh`` stops every database it started on ANY exit. These
tests drive the real scripts with stub database binaries (the image build
runs them against the real ones).
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DESKTOP = REPO / "deploy" / "ci-runner-desktop"
HELPER = DESKTOP / "omega-ci-db"
CHECK = DESKTOP / "toolchain-check.sh"

PG_CTL = r"""#!/bin/sh
# Stub pg_ctl: "start" runs a detached sleeper and records it as postmaster.
log="$STUB_LOG"
case "$1" in --version) echo "pg_ctl (PostgreSQL) 16.9"; exit 0 ;; esac
data=""; action=""; opts=""
while [ $# -gt 0 ]; do
    case "$1" in
        -D) data="$2"; shift 2 ;;
        -o) opts="$2"; shift 2 ;;
        -l|-t|-m) shift 2 ;;
        -w) shift ;;
        start|stop) action="$1"; shift ;;
        *) shift ;;
    esac
done
echo "pg_ctl $action $opts" >> "$log"
if [ "$action" = start ]; then
    # Like postgres, the sleeper handles SIGQUIT itself (a background job
    # of a non-interactive shell starts with SIGQUIT ignored).
    python3 -c 'import signal, time; signal.signal(signal.SIGQUIT, signal.SIG_DFL); time.sleep(600)' \
        </dev/null >/dev/null 2>&1 &
    echo "$!" > "$data/postmaster.pid"
    echo "pid $!" >> "$log"
    exit 0
fi
if [ "$action" = stop ]; then
    [ -n "${STUB_PG_CTL_STOP_BROKEN:-}" ] && exit 1
    pid="$(head -n 1 "$data/postmaster.pid")"
    kill "$pid"; while kill -0 "$pid" 2>/dev/null; do sleep 0.05; done
    rm -f "$data/postmaster.pid"
fi
"""

INITDB = r"""#!/bin/sh
case "$1" in --version) echo "initdb (PostgreSQL) 16.9"; exit 0 ;; esac
while [ $# -gt 0 ]; do [ "$1" = -D ] && mkdir -p "$2"; shift; done
"""

PSQL = r"""#!/bin/sh
[ -n "${STUB_PSQL_SLEEP:-}" ] && sleep "$STUB_PSQL_SLEEP"
[ -n "${STUB_PSQL_FAIL:-}" ] && { echo "stub psql: simulated failure" >&2; exit 2; }
echo omega-ci-pg-ok
"""

# Stub greptime: a real HTTP server on --http-addr; /health is OK, SQL fails
# unless STUB_GT_SQL_OK is set. exec, so the helper's recorded pid is it.
GREPTIME = r"""#!/bin/sh
case "$1" in --version) echo "version: 0.14.4"; exit 0 ;; esac
echo "greptime $*" >> "$STUB_LOG"
http=""
while [ $# -gt 0 ]; do [ "$1" = --http-addr ] && http="$2"; shift; done
exec python3 -c '
import http.server, os, sys
host, port = sys.argv[1].rsplit(":", 1)
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, body):
        self.send_response(code); self.end_headers(); self.wfile.write(body)
    def do_GET(self): self.reply(200, b"{}")
    def do_POST(self):
        ok = os.environ.get("STUB_GT_SQL_OK")
        self.reply(200 if ok else 500, b"{\"rows\":[[1]]}" if ok else b"boom")
http.server.HTTPServer((host, int(port)), H).serve_forever()
' "$http"
"""


@pytest.fixture
def stubs(tmp_path: Path) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "pg_ctl": PG_CTL,
        "initdb": INITDB,
        "psql": PSQL,
        "greptime": GREPTIME,
    }.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    versions = tmp_path / "versions"
    versions.write_text("PG_MAJOR=16\nGREPTIME=0.14.4\n")
    (tmp_path / "home").mkdir()
    return {
        "PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "home"),
        "STUB_LOG": str(tmp_path / "stub.log"),
        "OMEGA_CI_DB": str(HELPER),
        "OMEGA_CI_TOOLCHAIN_VERSIONS": str(versions),
        "OMEGA_CI_DB_WAIT": "20",
    }


def pids(env: dict[str, str]) -> list[int]:
    log = Path(env["STUB_LOG"])
    if not log.exists():
        return []
    return [
        int(x.split()[1]) for x in log.read_text().splitlines() if x.startswith("pid ")
    ]


def greptime_pids(home: Path) -> list[int]:
    return [int(p.read_text()) for p in home.rglob("pid")]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill 0; check /proc state too.
    stat = Path(f"/proc/{pid}/stat")
    return stat.exists() and stat.read_text().split()[2] != "Z"


def wait_dead(pid: int, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def run(
    args: list[str], env: dict[str, str], **kw: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=120, check=False, **kw
    )


# --- toolchain-check.sh cleanup (Greptile P2: failed checks leave databases running)


@pytest.mark.parametrize("pg_ctl_stop_broken", [False, True])
def test_failed_postgres_check_stops_the_clusters(
    stubs: dict[str, str], pg_ctl_stop_broken: bool
) -> None:
    env = {**stubs, "STUB_PSQL_FAIL": "1"}
    if pg_ctl_stop_broken:
        env["STUB_PG_CTL_STOP_BROKEN"] = "1"  # the helper must still stop it
    done = run(["/bin/sh", str(CHECK), "--only", "postgres"], env)
    assert done.returncode != 0
    assert "toolchain-check: FAIL postgres query" in done.stderr
    started = pids(env)
    assert len(started) == 2, done.stdout + done.stderr  # pg-a and pg-b
    for pid in started:
        assert wait_dead(pid), f"postgres stub {pid} left running"
    assert list(Path(env["HOME"]).iterdir()) == []  # scratch deleted after the stop


def test_failed_greptime_check_stops_the_instances(stubs: dict[str, str]) -> None:
    home = Path(stubs["HOME"])
    proc = subprocess.Popen(
        ["/bin/sh", str(CHECK), "--only", "greptime"],
        env=stubs,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    seen: set[int] = set()
    while proc.poll() is None:
        seen.update(greptime_pids(home))
        time.sleep(0.02)
    _, err = proc.communicate()
    assert proc.returncode != 0
    assert "toolchain-check: FAIL greptime query" in err
    started = sorted(seen)
    assert started, err
    for pid in started:
        assert wait_dead(pid), f"greptime stub {pid} left running"
    assert list(home.iterdir()) == []


def test_term_signal_still_stops_the_clusters(stubs: dict[str, str]) -> None:
    env = {**stubs, "STUB_PSQL_SLEEP": "1"}
    proc = subprocess.Popen(
        ["/bin/sh", str(CHECK), "--only", "postgres"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    end = time.monotonic() + 30
    while len(pids(env)) < 2 and time.monotonic() < end:
        time.sleep(0.02)
    assert len(pids(env)) == 2
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=60)
    assert proc.returncode == 143
    for pid in pids(env):
        assert wait_dead(pid), f"postgres stub {pid} left running after SIGTERM"


def test_passing_check_runs_two_instances_on_distinct_ports(
    stubs: dict[str, str],
) -> None:
    env = {**stubs, "STUB_GT_SQL_OK": "1"}
    done = run(["/bin/sh", str(CHECK), "--only", "postgres,greptime"], env)
    assert done.returncode == 0, done.stderr
    assert "sections [postgres,greptime] present and usable" in done.stdout
    log = Path(env["STUB_LOG"]).read_text()
    pg_ports = [
        line.split("-p ")[1].split()[0]
        for line in log.splitlines()
        if "pg_ctl start" in line
    ]
    assert len(set(pg_ports)) == 2
    gt_lines = [line for line in log.splitlines() if line.startswith("greptime ")]
    assert len(gt_lines) == 2
    all_ports = []
    for line in gt_lines:
        words = line.split()
        for flag in (
            "--http-addr",
            "--rpc-bind-addr",
            "--mysql-addr",
            "--postgres-addr",
        ):
            addr = words[words.index(flag) + 1]
            assert addr.startswith("127.0.0.1:")
            all_ports.append(addr)
    assert len(set(all_ports)) == 8  # 4 listeners x 2 instances, none shared
    for pid in pids(env):
        assert wait_dead(pid)


# --- omega-ci-db helper (Greptile P2: parallel jobs fight over ports) --------------


def helper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run(["/bin/sh", str(HELPER), *args], env)


def kv(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_helper_exports_to_github_env_and_stops(
    stubs: dict[str, str], tmp_path: Path
) -> None:
    github_env = tmp_path / "github_env"
    github_env.write_text("")
    env = {**stubs, "RUNNER_TEMP": str(tmp_path / "rt"), "GITHUB_ENV": str(github_env)}
    first = helper(env, "start", "postgres")
    assert first.returncode == 0, first.stderr
    vals = kv(first.stdout)
    assert vals["PGHOST"] == "127.0.0.1"
    assert (
        vals["DATABASE_URL"]
        == f"postgresql://postgres@127.0.0.1:{vals['PGPORT']}/postgres"
    )
    assert kv(github_env.read_text()) == vals
    # Data and socket dir live under RUNNER_TEMP, never a shared path.
    assert (tmp_path / "rt" / "omega-ci-db" / "postgres" / "data").is_dir()
    assert (
        f"-k {tmp_path / 'rt' / 'omega-ci-db' / 'postgres'}"
        in Path(env["STUB_LOG"]).read_text()
    )

    second = helper(env, "start", "postgres", "--name", "other", "--no-export")
    assert second.returncode == 0, second.stderr
    assert kv(second.stdout)["PGPORT"] != vals["PGPORT"]
    assert kv(github_env.read_text()) == vals  # --no-export left GITHUB_ENV alone
    assert kv(helper(env, "env", "other").stdout) == kv(second.stdout)

    dup = helper(env, "start", "postgres")
    assert dup.returncode != 0 and "already running" in dup.stderr

    stop = helper(env, "stop", "--all")
    assert stop.returncode == 0, stop.stderr
    for pid in pids(env):
        assert wait_dead(pid)
    assert helper(env, "stop", "--all").returncode == 0  # idempotent
    # A stopped name can be started again.
    again = helper(env, "start", "postgres", "--no-export")
    assert again.returncode == 0, again.stderr
    assert helper(env, "stop", "postgres").returncode == 0


def test_helper_rejects_bad_input(stubs: dict[str, str], tmp_path: Path) -> None:
    env = {**stubs, "RUNNER_TEMP": str(tmp_path / "rt")}
    for args in (
        ("start", "mysql"),
        ("start", "postgres", "--name", "../escape"),
        ("start", "postgres", "--name", ".hidden"),
        ("start", "postgres", "--bogus"),
        ("stop",),
        ("env", "nothing-here"),
        (),
    ):
        done = helper(env, *args)
        assert done.returncode != 0, args
        assert "omega-ci-db:" in done.stderr, args
    assert not (tmp_path / "escape").exists()


def test_helper_retries_when_a_port_is_taken(
    stubs: dict[str, str], tmp_path: Path
) -> None:
    # The first greptime start "loses the race" and dies; the helper must
    # pick a fresh port set and come up on the second try.
    flag = tmp_path / "first-try"
    bindir = Path(stubs["PATH"].split(":")[0])
    real = (bindir / "greptime").read_text()
    (bindir / "greptime").write_text(
        real.replace(
            'echo "greptime $*" >> "$STUB_LOG"',
            f'echo "greptime $*" >> "$STUB_LOG"\n'
            f'[ -e {flag} ] || {{ touch {flag}; echo "Address in use" >&2; exit 1; }}',
        )
    )
    env = {**stubs, "RUNNER_TEMP": str(tmp_path / "rt")}
    done = helper(env, "start", "greptime", "--no-export")
    assert done.returncode == 0, done.stderr
    assert (
        len(
            [
                x
                for x in Path(env["STUB_LOG"]).read_text().splitlines()
                if x.startswith("greptime ")
            ]
        )
        == 2
    )
    assert helper(env, "stop", "greptime").returncode == 0

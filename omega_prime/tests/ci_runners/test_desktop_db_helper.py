"""00058: per-job databases and self-check cleanup on the omega-ci runner image.

``omega-ci-db`` gives each PostgreSQL / GreptimeDB its own free loopback
ports and data dir, so parallel jobs on one Desktop cannot collide, and
``toolchain-check.sh`` stops every database it started on ANY exit. These
tests drive the real scripts with stub database binaries, a fake port tool
and a fake curl, and open no sockets (the image build runs them against the
real ones).
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

# No sockets anywhere in these tests (hermetic-tests rule): ports are plain
# numbers handed out by a fake omega-ci-ports, and "holding" a port is an
# owner file $STUB_PORTS/owner-PORT naming the holder's pid, claimed with
# O_EXCL so a second holder fails like a real bind would. The fake curl
# answers only while that owner is alive. The image build runs the real
# omega-ci-ports, curl and databases (toolchain-check.sh).

PORTS = r"""#!/bin/sh
# Fake omega-ci-ports: "free N" hands out fresh numbers from a counter;
# "owns PID PORT" reads the owner file.
dir="$STUB_PORTS"
case "$1" in
    free)
        exec 8>"$dir/.counter.lock"; flock 8
        next="$(cat "$dir/counter" 2>/dev/null || echo 20000)"
        out=""; i=0
        while [ "$i" -lt "$2" ]; do
            next=$(( next + 1 )); out="$out${out:+ }$next"; i=$(( i + 1 ))
        done
        echo "$next" >"$dir/counter"
        echo "$out"
        ;;
    owns) [ "$(cat "$dir/owner-$3" 2>/dev/null)" = "$2" ] ;;
    *) exit 2 ;;
esac
"""

# hold PORT... : claim each port for this process (fails if already held).
HOLD = r"""
import os, sys
def hold(ports, pid):
    for p in ports:
        fd = os.open(os.path.join(os.environ["STUB_PORTS"], f"owner-{p}"),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.write(fd, str(pid).encode()); os.close(fd)
"""

PG_CTL = r"""#!/bin/sh
# Stub pg_ctl: "start" runs a detached sleeper that HOLDS the -p port (like a
# real postmaster, it fails if the port is taken) and records it as postmaster.
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
    port="$(echo "$opts" | sed 's/.*-p \([0-9]*\).*/\1/')"
    # The child detaches (setsid), moves into its data dir and claims the
    # port, and only THEN publishes postmaster.pid (atomic rename); the
    # parent exits once that file exists. So the pid a test sees is already
    # a detached postmaster in its own session, never a half-started child.
    python3 -c '__HOLD__
import signal, time
data = sys.argv[2]
r, w = os.pipe()
if os.fork():
    os.close(w)
    ok = os.read(r, 1) == b"1"
    sys.exit(0 if ok else 1)
os.close(r)
os.setsid()  # detached from the caller, like a postmaster pg_ctl started
os.chdir(data)  # a real postmaster runs with its data dir as cwd
signal.signal(signal.SIGQUIT, signal.SIG_DFL)  # postgres handles SIGQUIT
try:
    hold([sys.argv[1]], os.getpid())
except FileExistsError:
    os.write(w, b"0"); sys.exit(1)
with open("postmaster.pid.tmp", "w") as fh:
    fh.write(f"{os.getpid()}\n")
os.rename("postmaster.pid.tmp", "postmaster.pid")
os.write(w, b"1"); os.close(w)
for fd in (0, 1, 2):
    os.close(fd)
time.sleep(600)
' "$port" "$data" </dev/null >/dev/null 2>&1 || exit 1
    echo "pid $(head -n 1 "$data/postmaster.pid")" >> "$log"
    # Server up, pg_ctl -w still "waiting": lets a test interrupt the start.
    [ -n "${STUB_PG_CTL_START_SLEEP:-}" ] && sleep "$STUB_PG_CTL_START_SLEEP"
    exit 0
fi
if [ "$action" = stop ]; then
    [ -n "${STUB_PG_CTL_STOP_BROKEN:-}" ] && exit 1
    pid="$(head -n 1 "$data/postmaster.pid")"
    kill "$pid"; while kill -0 "$pid" 2>/dev/null; do sleep 0.05; done
    rm -f "$data/postmaster.pid"
fi
""".replace("__HOLD__", HOLD)

INITDB = r"""#!/bin/sh
case "$1" in --version) echo "initdb (PostgreSQL) 16.9"; exit 0 ;; esac
[ -n "${STUB_INITDB_SLEEP:-}" ] && sleep "$STUB_INITDB_SLEEP"
while [ $# -gt 0 ]; do [ "$1" = -D ] && mkdir -p "$2"; shift; done
"""

PSQL = r"""#!/bin/sh
[ -n "${STUB_PSQL_SLEEP:-}" ] && sleep "$STUB_PSQL_SLEEP"
[ -n "${STUB_PSQL_FAIL:-}" ] && { echo "stub psql: simulated failure" >&2; exit 2; }
echo omega-ci-pg-ok
"""

# Stub greptime: HOLDS its http, rpc, mysql and postgres ports (owner files)
# and sleeps. exec, so the helper's recorded pid is it.
GREPTIME = r"""#!/bin/sh
case "$1" in --version) echo "version: 0.14.4"; exit 0 ;; esac
echo "greptime $*" >> "$STUB_LOG"
ports=""
while [ $# -gt 0 ]; do
    case "$1" in
        --http-addr|--rpc-bind-addr|--mysql-addr|--postgres-addr) ports="$ports ${2##*:}" ;;
    esac
    shift
done
exec python3 -c '__HOLD__
import time
hold(sys.argv[1:], os.getpid())
time.sleep(600)
' $ports
""".replace("__HOLD__", HOLD)

# Fake curl for greptime: /health is OK and SQL answers (only with
# STUB_GT_SQL_OK) while the port's owner is alive.
CURL = r"""#!/bin/sh
url=""
for a in "$@"; do case "$a" in http://*) url="$a" ;; esac; done
hostport="${url#http://}"; hostport="${hostport%%/*}"; port="${hostport##*:}"
owner="$(cat "$STUB_PORTS/owner-$port" 2>/dev/null)"
[ -n "$owner" ] && kill -0 "$owner" 2>/dev/null || { echo "curl: (7) connection refused" >&2; exit 7; }
case "$url" in
    */health) echo "{}" ;;
    */v1/sql)
        [ -n "${STUB_GT_SQL_OK:-}" ] || { echo "curl: (22) 500" >&2; exit 22; }
        echo '{"rows":[[1]]}' ;;
    *) exit 22 ;;
esac
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
        "curl": CURL,
        "omega-ci-ports": PORTS,
    }.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    versions = tmp_path / "versions"
    versions.write_text("PG_MAJOR=16\nGREPTIME=0.14.4\n")
    (tmp_path / "home").mkdir()
    (tmp_path / "ports").mkdir()
    return {
        "PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "home"),
        "STUB_LOG": str(tmp_path / "stub.log"),
        "STUB_PORTS": str(tmp_path / "ports"),
        "OMEGA_CI_PORTS": str(bindir / "omega-ci-ports"),
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


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=120, check=False
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
    leaked: list[int] = []
    deadline = time.monotonic() + 90
    try:
        while proc.poll() is None:
            if time.monotonic() > deadline:
                pytest.fail("toolchain-check.sh hung (no exit within 90s)")
            seen.update(greptime_pids(home))
            time.sleep(0.02)
        _, err = proc.communicate(timeout=30)
        # Judge the script's own cleanup BEFORE the fallback teardown below,
        # which would otherwise kill a leaked server and hide the leak.
        leaked = [pid for pid in sorted(seen) if not wait_dead(pid)]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        for pid in seen | set(greptime_pids(home)):
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
    assert proc.returncode != 0
    assert "toolchain-check: FAIL greptime query" in err
    assert seen, err
    assert not leaked, f"greptime stubs {leaked} left running"
    assert list(home.iterdir()) == []


def test_interrupted_postgres_start_is_still_stopped(
    stubs: dict[str, str], tmp_path: Path
) -> None:
    # Greptile P1: the helper dies while pg_ctl -w waits, after the server is
    # already up. stop --all must still find and stop that server, and a new
    # start of the same name must refuse instead of deleting its data dir.
    root = tmp_path / "dbroot"
    env = {**stubs, "OMEGA_CI_DB_ROOT": str(root), "STUB_PG_CTL_START_SLEEP": "30"}
    proc = subprocess.Popen(
        ["/bin/sh", str(HELPER), "start", "postgres", "--no-export"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    started: list[int] = []
    try:
        end = time.monotonic() + 30
        while not (started := pids(env)) and time.monotonic() < end:
            time.sleep(0.02)
        assert len(started) == 1
        os.killpg(proc.pid, signal.SIGKILL)  # helper and its pg_ctl, not the server
        proc.wait(timeout=10)
        server = started[0]
        assert alive(server)
        again = run(["/bin/sh", str(HELPER), "start", "postgres", "--no-export"], env)
        assert again.returncode != 0
        assert "already running" in again.stderr
        assert (root / "postgres" / "data").is_dir()
        stopped = run(["/bin/sh", str(HELPER), "stop", "--all"], env)
        assert stopped.returncode == 0, stopped.stderr
        assert wait_dead(server), f"postgres stub {server} left running"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        for pid in started:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)


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
    # Data lives under RUNNER_TEMP; no Unix socket at all (TCP only), so a
    # deep RUNNER_TEMP cannot overflow sun_path and /tmp sockets cannot clash.
    assert (tmp_path / "rt" / "omega-ci-db" / "postgres" / "data").is_dir()
    log = Path(env["STUB_LOG"]).read_text()
    assert "-c unix_socket_directories=''" in log
    assert " -k " not in log

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


def test_overlapping_start_of_one_name_is_refused(
    stubs: dict[str, str], tmp_path: Path
) -> None:
    # The first start is still in initdb (no postmaster.pid yet); a second
    # start of the same name must refuse, not delete the first one's files.
    env = {**stubs, "RUNNER_TEMP": str(tmp_path / "rt"), "STUB_INITDB_SLEEP": "2"}
    first = subprocess.Popen(
        ["/bin/sh", str(HELPER), "start", "postgres", "--no-export"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        data = tmp_path / "rt" / "omega-ci-db" / "postgres"
        end = time.monotonic() + 10
        while not data.exists() and time.monotonic() < end:
            time.sleep(0.02)
        second = helper(env, "start", "postgres", "--no-export")
        assert second.returncode != 0
        assert "already being started" in second.stderr
        out, err = first.communicate(timeout=60)
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
    assert first.returncode == 0, err
    assert kv(out)["PGPORT"]
    assert helper(env, "stop", "postgres").returncode == 0
    for pid in pids(env):
        assert wait_dead(pid)


def test_stop_never_signals_a_reused_pid(stubs: dict[str, str], tmp_path: Path) -> None:
    # The database died and the kernel gave its PID to another job's process:
    # same PID, different start time. stop must leave that process alone.
    env = {**stubs, "RUNNER_TEMP": str(tmp_path / "rt")}
    bystander = subprocess.Popen(["sleep", "60"])
    try:
        inst = tmp_path / "rt" / "omega-ci-db" / "greptime"
        inst.mkdir(parents=True)
        (inst / "kind").write_text("greptime\n")
        (inst / "pid").write_text(f"{bystander.pid}\n")
        (inst / "start").write_text("1\n")  # not the bystander's start time
        (inst / "env").write_text("GREPTIME_HTTP_ADDR=127.0.0.1:1\n")
        done = helper(env, "stop", "--all")
        assert done.returncode == 0, done.stderr
        time.sleep(0.3)
        assert bystander.poll() is None, "stop signalled a process it did not start"
        # And a fresh start of that name is not blocked by the stale record.
        again = helper(env, "start", "greptime", "--no-export")
        assert again.returncode == 0, again.stderr
        assert helper(env, "stop", "greptime").returncode == 0
    finally:
        bystander.kill()
        bystander.wait()


@pytest.mark.parametrize("only", ["postgress", "postgres,gretime", ",postgres"])
def test_unknown_only_section_is_rejected(stubs: dict[str, str], only: str) -> None:
    done = run(["/bin/sh", str(CHECK), "--only", only], stubs)
    assert done.returncode == 2
    assert "unknown section" in done.stderr
    assert "present and usable" not in done.stdout
    assert pids(stubs) == []


def test_postgres_stop_never_signals_a_reused_pid(
    stubs: dict[str, str], tmp_path: Path
) -> None:
    # A stale postmaster.pid naming another job's process: it is not running
    # in this instance's data dir, so stop must leave it alone.
    env = {**stubs, "RUNNER_TEMP": str(tmp_path / "rt")}
    bystander = subprocess.Popen(["sleep", "60"], cwd=tmp_path)
    try:
        inst = tmp_path / "rt" / "omega-ci-db" / "postgres"
        (inst / "data").mkdir(parents=True)
        (inst / "kind").write_text("postgres\n")
        (inst / "data" / "postmaster.pid").write_text(f"{bystander.pid}\n")
        done = helper(env, "stop", "--all")
        assert done.returncode == 0, done.stderr
        time.sleep(0.3)
        assert bystander.poll() is None, "stop signalled a process it did not start"
    finally:
        bystander.kill()
        bystander.wait()

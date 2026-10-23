#!/bin/sh
# Toolchain self-check for the omega-ci runner image (00056).
#
# Asserts every tool the image promises is present at its pinned version and
# usable WITHOUT root, from the job's own environment. The build runs it as the
# job identity; it can be re-run on a live Desktop the same way:
#
#   /opt/omega-ci/toolchain-check.sh --as-job 1500 1500   (as root)
#   /opt/omega-ci/toolchain-check.sh                      (as any user)
#
# Pinned versions: /opt/omega-ci/toolchain-versions (written from the
# Dockerfile ARGs). Job environment: /opt/actions-runner/.env (runner-env).
# Exits non-zero on the first failure, naming the tool.
set -eu

versions="${OMEGA_CI_TOOLCHAIN_VERSIONS:-/opt/omega-ci/toolchain-versions}"
runner_env="${OMEGA_CI_RUNNER_ENV:-/opt/actions-runner/.env}"

if [ "${1:-}" = "--as-job" ]; then
    uid="$2"; gid="$3"
    home="$(mktemp -d)"
    chown "$uid:$gid" "$home"
    trap 'rm -rf "$home"' EXIT
    # The job's view: only what the agent passes (PATH, HOME) plus runner-env.
    set -- env -i HOME="$home" PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin TMPDIR="$home"
    while IFS= read -r line; do
        case "$line" in ''|'#'*) ;; *) set -- "$@" "$line" ;; esac
    done < "$runner_env"
    "$@" setpriv --reuid="$uid" --regid="$gid" --clear-groups /bin/sh "$0"
    exit $?
fi

fail() { echo "toolchain-check: FAIL $*" >&2; exit 1; }
want() { grep -E "^$1=" "$versions" | head -n 1 | cut -d= -f2-; }
# Version a hash-locked venv pins for its own tool (requirements/<tool>.txt).
locked() { grep -E "^$1==" "${OMEGA_CI_REQUIREMENTS:-/opt/omega-ci/requirements}/$1.txt" | head -n 1 | sed 's/^[^=]*==\([^ ]*\).*/\1/'; }
# check NAME EXPECTED CMD...: CMD's output must contain EXPECTED.
check() {
    name="$1"; expected="$2"; shift 2
    out="$("$@" 2>&1)" || fail "$name: '$*' exited non-zero: $(echo "$out" | tail -n 3)"
    case "$out" in *"$expected"*) ;; *) fail "$name: want '$expected', got: $(echo "$out" | head -n 2)" ;; esac
    echo "ok  $name  $(echo "$out" | grep -F -m 1 "$expected" | cut -c1-100)"
}
# Shared trees must not be writable by the job (no cross-job tampering).
readonly_tree() { [ -w "$1" ] && fail "$1 is writable by uid $(id -u)"; echo "ok  read-only  $1"; }

[ "$(id -u)" != 0 ] || echo "toolchain-check: note: running as root; use --as-job to prove non-root use" >&2
[ -r "$versions" ] || fail "missing $versions"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

# --- base CLIs --------------------------------------------------------------
gitv="$(git --version | awk '{print $3}')"
python3 -c 'import sys; v=tuple(map(int, sys.argv[1].split(".")[:2])); sys.exit(v < (2, 18))' "$gitv" \
    || fail "git $gitv < 2.18 (actions/checkout needs 2.18+)"
echo "ok  git  $gitv"
check gh "gh version $(want GH)" gh --version
for tool in jq make gcc g++ zip unzip xz rsync age pipx curl file pkg-config; do
    command -v "$tool" >/dev/null || fail "$tool not on PATH"
done
echo "ok  jq make gcc g++ zip unzip xz rsync age pipx curl file pkg-config"
check python3-venv "venv-ok" sh -c "python3 -m venv '$scratch/venv' && '$scratch/venv/bin/python' -m pip --version >/dev/null && echo venv-ok"

# --- hosted tool cache (setup-python / setup-node cache hits) ----------------
tc="${RUNNER_TOOL_CACHE:?RUNNER_TOOL_CACHE unset in the job env}"
[ "${AGENT_TOOLSDIRECTORY:-}" = "$tc" ] || fail "AGENT_TOOLSDIRECTORY != RUNNER_TOOL_CACHE"
for key in PY310 PY312; do
    v="$(want "$key")"
    [ -f "$tc/Python/$v/x64.complete" ] || fail "Python $v: no x64.complete marker"
    check "Python $v" "Python $v" "$tc/Python/$v/x64/python" --version
    check "Python $v pip" "pip " "$tc/Python/$v/x64/bin/python3" -m pip --version
done
for key in NODE22 NODE24; do
    v="$(want "$key")"
    [ -f "$tc/node/$v/x64.complete" ] || fail "node $v: no x64.complete marker"
    check "node $v" "v$v" "$tc/node/$v/x64/bin/node" --version
    # setup-node puts the version's bin dir on PATH; npm's shebang needs it.
    check "npm (node $v)" "." env PATH="$tc/node/$v/x64/bin:$PATH" npm --version
done
readonly_tree "$tc"
readonly_tree "$tc/Python"
readonly_tree "$tc/node"

# --- .NET + Android ---------------------------------------------------------
check "dotnet sdk" "$(want DOTNET_SDK)" dotnet --list-sdks
check "dotnet workload" "maui-android" dotnet workload list
check "java 17" 'version "17.' java -version
check "javac 17" "javac 17." javac -version
[ -f "$ANDROID_HOME/platforms/android-$(want ANDROID_API)/android.jar" ] || fail "android-$(want ANDROID_API) platform missing"
[ -x "$ANDROID_HOME/build-tools/$(want ANDROID_BUILD_TOOLS)/aapt2" ] || fail "build-tools $(want ANDROID_BUILD_TOOLS) missing"
check "adb" "Android Debug Bridge" adb version
echo "ok  android  platforms;android-$(want ANDROID_API) build-tools;$(want ANDROID_BUILD_TOOLS)"
readonly_tree "$DOTNET_ROOT"
readonly_tree "$ANDROID_HOME"

# --- PostgreSQL 16: a real throwaway cluster as this user --------------------
pg="$(want PG_MAJOR)"
check initdb "(PostgreSQL) $pg." initdb --version
check pg_ctl "(PostgreSQL) $pg." pg_ctl --version
initdb -D "$scratch/pg" -U postgres --auth=trust -E UTF8 >/dev/null
pg_ctl -D "$scratch/pg" -l "$scratch/pg.log" -w -o "-k $scratch -c listen_addresses=''" start >/dev/null \
    || fail "postgres did not start: $(tail -n 5 "$scratch/pg.log")"
check "postgres query" "omega-ci-pg-ok" psql -h "$scratch" -U postgres -Atc "select 'omega-ci-pg-ok'"
pg_ctl -D "$scratch/pg" -m fast -w stop >/dev/null

# --- GreptimeDB ---------------------------------------------------------------
check greptime "$(want GREPTIME)" greptime --version
# A real standalone instance as this user (data and logs under scratch).
gt="$scratch/greptime"
greptime standalone start --http-addr 127.0.0.1:4000 --data-home "$gt" --log-dir "$gt/logs" \
    > "$scratch/greptime.out" 2>&1 &
gt_pid=$!
up=""
for _ in $(seq 60); do
    curl -fsS http://127.0.0.1:4000/health >/dev/null 2>&1 && { up=1; break; }
    kill -0 "$gt_pid" 2>/dev/null || break
    sleep 1
done
[ -n "$up" ] || fail "greptime standalone did not come up: $(tail -n 3 "$scratch/greptime.out")"
check "greptime query" '"rows":[[1]]' curl -fsS -X POST http://127.0.0.1:4000/v1/sql --data-urlencode "sql=SELECT 1"
kill "$gt_pid" && wait "$gt_pid" 2>/dev/null || true

# --- containers (CLI only: Railway gives no user namespaces to run them) -----
check podman "podman version" podman --version
check podman-compose "$(locked podman-compose)" podman-compose --version
printf 'services:\n  app:\n    image: docker.io/library/busybox:1\n    profiles: [extra]\n' > "$scratch/compose.yaml"
check "podman-compose config" "app" podman-compose --profile extra -f "$scratch/compose.yaml" config --services

# --- security scanners ----------------------------------------------------------
check gitleaks "$(want GITLEAKS)" gitleaks version
check trivy "Version: $(want TRIVY)" trivy --version
check semgrep "$(locked semgrep)" semgrep --version
check bandit "bandit $(locked bandit)" bandit --version

# --- Rust: compile and run a crate as this user ------------------------------
check rustc "rustc $(want RUST)" rustc --version
check cargo "cargo $(want RUST)" cargo --version
check rustfmt "rustfmt" rustfmt --version
check clippy "clippy" cargo clippy --version
readonly_tree "$RUSTUP_HOME"
(cd "$scratch" && cargo new --vcs none -q hello && cd hello && cargo build -q --offline) \
    || fail "cargo build of a hello crate failed"
check "cargo run" "Hello, world!" "$scratch/hello/target/debug/hello"

echo "toolchain-check: all tools present and usable as uid $(id -u)"

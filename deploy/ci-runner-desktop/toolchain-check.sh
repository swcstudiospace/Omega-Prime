#!/bin/sh
# Toolchain self-check for the omega-ci runner image (00056, 00058).
#
# Asserts every tool the image promises is present at its pinned version and
# usable WITHOUT root, from the job's own environment. The build runs it as the
# job identity; it can be re-run on a live Desktop the same way:
#
#   /opt/omega-ci/toolchain-check.sh --as-job 1500 1500   (as root)
#   /opt/omega-ci/toolchain-check.sh                      (as any user)
#   /opt/omega-ci/toolchain-check.sh --only postgres,greptime
#
# Sections (for --only): base toolcache dotnet android-build postgres
# greptime containers scanners rust. Default: all of them.
#
# Pinned versions: /opt/omega-ci/toolchain-versions (written from the
# Dockerfile ARGs). Job environment: /opt/actions-runner/.env (runner-env).
# Exits non-zero on the first failure, naming the tool. On ANY exit (failure,
# set -e, or HUP/INT/TERM) the databases it started are stopped and waited
# for before their files are deleted, so a failed live re-run leaves no
# PostgreSQL/GreptimeDB holding ports or memory.
set -eu

versions="${OMEGA_CI_TOOLCHAIN_VERSIONS:-/opt/omega-ci/toolchain-versions}"
runner_env="${OMEGA_CI_RUNNER_ENV:-/opt/actions-runner/.env}"
# Per-job database helper (shipped as /usr/local/bin/omega-ci-db).
db_helper="${OMEGA_CI_DB:-omega-ci-db}"

if [ "${1:-}" = "--as-job" ]; then
    uid="$2"; gid="$3"; shift 3
    home="$(mktemp -d)"
    chown "$uid:$gid" "$home"
    trap 'rm -rf "$home"' EXIT
    trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
    rest="$*"
    # The job's view: only what the agent passes (PATH, HOME) plus runner-env.
    set -- env -i HOME="$home" PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin TMPDIR="$home"
    while IFS= read -r line; do
        case "$line" in ''|'#'*) ;; *) set -- "$@" "$line" ;; esac
    done < "$runner_env"
    # $rest holds only --only and its comma list (no spaces), so split it.
    # shellcheck disable=SC2086
    "$@" setpriv --reuid="$uid" --regid="$gid" --clear-groups /bin/sh "$0" $rest
    exit $?
fi

only="all"
if [ "${1:-}" = "--only" ]; then
    [ -n "${2:-}" ] || { echo "toolchain-check: --only needs a comma list" >&2; exit 2; }
    only="$2"
fi
# Reject unknown section names up front: a typo must not "pass" by running
# nothing (or by silently skipping one check of a mixed list).
sections="base toolcache dotnet android-build postgres greptime containers scanners rust"
old_ifs="$IFS"; IFS=,; set -f
for want_section in $only; do
    case "$want_section" in ''|*[!a-z-]*) known=no ;; *) known=yes ;; esac
    case "$known: all $sections " in
        "yes:"*" $want_section "*) ;;
        *) echo "toolchain-check: unknown section '$want_section' (known: all $sections)" >&2; exit 2 ;;
    esac
done
IFS="$old_ifs"; set +f
# section NAME: true when NAME should run (prints a header).
section() {
    case ",$only," in *",all,"*|*",$1,"*) echo "== $1"; return 0 ;; esac
    return 1
}

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
db_root="$scratch/db"
# Stop every database this run started, wait for it, THEN delete the files.
cleanup() {
    rc=$?
    trap - EXIT
    if [ -d "$db_root" ]; then
        OMEGA_CI_DB_ROOT="$db_root" "$db_helper" stop --all >/dev/null 2>&1 \
            || echo "toolchain-check: warning: a database in $db_root did not stop" >&2
    fi
    if [ -d "$scratch/android" ]; then
        dotnet build-server shutdown >/dev/null 2>&1 || true
    fi
    rm -rf "$scratch"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
db() { OMEGA_CI_DB_ROOT="$db_root" "$db_helper" "$@"; }
# dbenv NAME KEY: one value from a running instance's env.
dbenv() { db env "$1" | grep -E "^$2=" | head -n 1 | cut -d= -f2-; }

# --- base CLIs --------------------------------------------------------------
if section base; then
gitv="$(git --version | awk '{print $3}')"
python3 -c 'import sys; v=tuple(map(int, sys.argv[1].split(".")[:2])); sys.exit(v < (2, 18))' "$gitv" \
    || fail "git $gitv < 2.18 (actions/checkout needs 2.18+)"
echo "ok  git  $gitv"
check gh "gh version $(want GH)" gh --version
for tool in jq make gcc g++ zip unzip xz rsync age pipx curl file pkg-config; do
    command -v "$tool" >/dev/null || fail "$tool not on PATH"
done
echo "ok  jq make gcc g++ zip unzip xz rsync age pipx curl file pkg-config"
check ripgrep "ripgrep " rg --version
check ffmpeg "ffmpeg version" ffmpeg -hide_banner -version
check ffprobe "ffprobe version" ffprobe -hide_banner -version
check python3-venv "venv-ok" sh -c "python3 -m venv '$scratch/venv' && '$scratch/venv/bin/python' -m pip --version >/dev/null && echo venv-ok"
command -v "$db_helper" >/dev/null || fail "$db_helper (per-job database helper) not on PATH"
echo "ok  omega-ci-db  $(command -v "$db_helper")"
fi

# --- hosted tool cache (setup-python / setup-node cache hits) ----------------
if section toolcache; then
tc="${RUNNER_TOOL_CACHE:?RUNNER_TOOL_CACHE unset in the job env}"
[ "${AGENT_TOOLSDIRECTORY:-}" = "$tc" ] || fail "AGENT_TOOLSDIRECTORY != RUNNER_TOOL_CACHE"
for key in PY310 PY312 PY313 PY314; do
    v="$(want "$key")"
    [ -f "$tc/Python/$v/x64.complete" ] || fail "Python $v: no x64.complete marker"
    check "Python $v" "Python $v" "$tc/Python/$v/x64/python" --version
    check "Python $v pip" "pip " "$tc/Python/$v/x64/bin/python3" -m pip --version
    # `pip` (not only pip3) must resolve to this interpreter once setup-python
    # puts bin/ first on PATH, or it falls through to the system pip.
    check "Python $v bin/pip" "python ${v%.*}" "$tc/Python/$v/x64/bin/pip" --version
done
v="$(want CODEQL)"
[ -f "$tc/CodeQL/$v/x64.complete" ] || fail "CodeQL $v: no x64.complete marker"
check "CodeQL $v" "$v" "$tc/CodeQL/$v/x64/codeql/codeql" version --format=terse
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
readonly_tree "$tc/CodeQL"
fi

# --- .NET + Android ---------------------------------------------------------
# Android packages are fixed archives (sha256-pinned in the Dockerfile); each
# carries its own source.properties, so the revision is asserted exactly.
pkgrev() { grep -E '^Pkg.Revision=' "$1/source.properties" | head -n 1 | cut -d= -f2; }
if section dotnet; then
check "dotnet sdk" "$(want DOTNET_SDK)" dotnet --list-sdks
check "dotnet workload" "maui-android" dotnet workload list
check "java 17" 'version "17.' java -version
check "javac 17" "javac 17." javac -version
plat="$ANDROID_HOME/platforms/android-$(want ANDROID_API)"
bt="$ANDROID_HOME/build-tools/$(want ANDROID_BUILD_TOOLS)"
[ -f "$plat/android.jar" ] || fail "android-$(want ANDROID_API) platform missing"
[ "$(pkgrev "$plat")" = "$(want ANDROID_PLATFORM_REV)" ] \
    || fail "platforms;android-$(want ANDROID_API): revision $(pkgrev "$plat"), want $(want ANDROID_PLATFORM_REV)"
[ -x "$bt/aapt2" ] || fail "build-tools $(want ANDROID_BUILD_TOOLS) missing"
[ "$(pkgrev "$bt")" = "$(want ANDROID_BUILD_TOOLS)" ] \
    || fail "build-tools: revision $(pkgrev "$bt"), want $(want ANDROID_BUILD_TOOLS)"
[ "$(pkgrev "$ANDROID_HOME/platform-tools")" = "$(want ANDROID_PLATFORM_TOOLS)" ] \
    || fail "platform-tools: revision $(pkgrev "$ANDROID_HOME/platform-tools"), want $(want ANDROID_PLATFORM_TOOLS)"
check "adb" "Version $(want ANDROID_PLATFORM_TOOLS)" adb version
echo "ok  android  platforms;android-$(want ANDROID_API) r$(want ANDROID_PLATFORM_REV) build-tools;$(want ANDROID_BUILD_TOOLS) platform-tools;$(want ANDROID_PLATFORM_TOOLS)"
[ -f "$DOTNET_ROOT/library-packs/microsoft.net.illink.tasks.$(want ILLINK_TASKS).nupkg" ] \
    || fail "Microsoft.NET.ILLink.Tasks $(want ILLINK_TASKS) missing from $DOTNET_ROOT/library-packs"
echo "ok  illink-tasks  $(want ILLINK_TASKS) (offline, library-packs)"
readonly_tree "$DOTNET_ROOT"
readonly_tree "$ANDROID_HOME"
fi

# A real net10.0-android build as this user, OFFLINE: the only package
# source is the SDK's own library-packs (nuget.config clears nuget.org), so
# a missing workload pack, SDK package or a write into the read-only SDK
# trees fails here instead of in a job. Everything lands under scratch.
if section android-build; then
app="$scratch/android"
mkdir -p "$app"
export NUGET_PACKAGES="$scratch/nuget" DOTNET_CLI_HOME="$scratch/dotnet-home" \
    DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=1 MSBUILDDISABLENODEREUSE=1 DOTNET_CLI_USE_MSBUILD_SERVER=0
dotnet new android -o "$app/src" -n OmegaCiCheck >"$app/new.log" 2>&1 \
    || fail "dotnet new android: $(tail -n 5 "$app/new.log")"
printf '%s\n' '<?xml version="1.0" encoding="utf-8"?>' \
    '<configuration><packageSources><clear /></packageSources></configuration>' > "$app/src/nuget.config"
dotnet build "$app/src" -c Debug -nologo -nodeReuse:false -p:UseSharedCompilation=false \
    -p:AndroidPackageFormat=apk -o "$app/out" >"$app/build.log" 2>&1 \
    || fail "net10.0-android build as uid $(id -u): $(grep -E 'error|Error' "$app/build.log" | tail -n 5)"
apk=""
for f in "$app/out"/*-Signed.apk; do [ -f "$f" ] && { apk="$f"; break; }; done
[ -s "$apk" ] || fail "net10.0-android build produced no signed apk"
unzip -l "$apk" | grep -q 'AndroidManifest.xml' || fail "apk has no AndroidManifest.xml"
echo "ok  net10.0-android build  $(basename "$apk") ($(wc -c < "$apk") bytes, offline, uid $(id -u))"
fi

# --- PostgreSQL 16: two throwaway clusters side by side, as this user ---------
# Through omega-ci-db, exactly as jobs do: each gets its own free port, so
# parallel jobs on one Desktop cannot collide.
if section postgres; then
pg="$(want PG_MAJOR)"
check initdb "(PostgreSQL) $pg." initdb --version
check pg_ctl "(PostgreSQL) $pg." pg_ctl --version
for n in pg-a pg-b; do
    db start postgres --name "$n" --no-export >/dev/null || fail "postgres $n did not start"
done
pa="$(dbenv pg-a PGPORT)"; pb="$(dbenv pg-b PGPORT)"
[ -n "$pa" ] && [ "$pa" != "$pb" ] || fail "postgres instances share a port ($pa/$pb)"
for n in pg-a pg-b; do
    check "postgres query ($n, port $(dbenv "$n" PGPORT))" "omega-ci-pg-ok" \
        psql -h 127.0.0.1 -p "$(dbenv "$n" PGPORT)" -U postgres -Atc "select 'omega-ci-pg-ok'"
done
db stop pg-a >/dev/null 2>&1 || fail "postgres pg-a did not stop"
db stop pg-b >/dev/null 2>&1 || fail "postgres pg-b did not stop"
fi

# --- GreptimeDB: two standalone instances side by side ------------------------
if section greptime; then
check greptime "$(want GREPTIME)" greptime --version
for n in gt-a gt-b; do
    db start greptime --name "$n" --no-export >/dev/null || fail "greptime $n did not come up"
done
ga="$(dbenv gt-a GREPTIME_HTTP_URL)"; gb="$(dbenv gt-b GREPTIME_HTTP_URL)"
[ -n "$ga" ] && [ "$ga" != "$gb" ] || fail "greptime instances share a port ($ga/$gb)"
for n in gt-a gt-b; do
    url="$(dbenv "$n" GREPTIME_HTTP_URL)"
    check "greptime query ($n, $url)" '"rows":[[1]]' curl -fsS -X POST "$url/v1/sql" --data-urlencode "sql=SELECT 1"
done
db stop gt-a >/dev/null 2>&1 || fail "greptime gt-a did not stop"
db stop gt-b >/dev/null 2>&1 || fail "greptime gt-b did not stop"
fi

# --- containers (CLI only: Railway gives no user namespaces to run them) -----
if section containers; then
check podman "podman version" podman --version
check podman-compose "$(locked podman-compose)" podman-compose --version
printf 'services:\n  app:\n    image: docker.io/library/busybox:1\n    profiles: [extra]\n' > "$scratch/compose.yaml"
check "podman-compose config" "app" podman-compose --profile extra -f "$scratch/compose.yaml" config --services
fi

# --- security scanners ----------------------------------------------------------
if section scanners; then
check gitleaks "$(want GITLEAKS)" gitleaks version
check trivy "Version: $(want TRIVY)" trivy --version
check semgrep "$(locked semgrep)" semgrep --version
check bandit "bandit $(locked bandit)" bandit --version
fi

# --- Rust: compile and run a crate as this user ------------------------------
if section rust; then
check rustc "rustc $(want RUST)" rustc --version
check cargo "cargo $(want RUST)" cargo --version
check rustfmt "rustfmt" rustfmt --version
check clippy "clippy" cargo clippy --version
check "rustc (parity)" "rustc $(want RUST_PARITY)" env RUSTUP_TOOLCHAIN="$(want RUST_PARITY)" rustc --version
check "clippy (parity)" "clippy" env RUSTUP_TOOLCHAIN="$(want RUST_PARITY)" cargo clippy --version
check cargo-deny "cargo-deny $(want CARGO_DENY)" cargo deny --version
readonly_tree "$RUSTUP_HOME"
(cd "$scratch" && cargo new --vcs none -q hello && cd hello && cargo build -q --offline) \
    || fail "cargo build of a hello crate failed"
check "cargo run" "Hello, world!" "$scratch/hello/target/debug/hello"
fi

if [ "$only" = all ]; then
    echo "toolchain-check: all tools present and usable as uid $(id -u)"
else
    echo "toolchain-check: sections [$only] present and usable as uid $(id -u)"
fi

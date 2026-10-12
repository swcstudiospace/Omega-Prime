#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Turnkey 1-Click Launcher for Grok Bot Native Omega Prime Integration.
#
#   grokbot-1click.sh [ARGS...]                  run oneclick with the best Python found
#   grokbot-1click.sh --bootstrap [ARGS...]      bootstrap once, then serve stdio
#   grokbot-1click.sh --bootstrap-only           bootstrap, then exit 0 (setup pre-run)
#   grokbot-1click.sh --verify [--json]          bootstrap, then run the plugin verifier
#
# The repository root is this script's own repository (symlinks resolved); the
# caller's working directory never selects it. ARGS go to
# `python -m omega_prime.grokbot.oneclick --root ROOT`; after a bootstrap the serve
# also gets `--transport stdio --skip-doctor` ahead of ARGS.
#
# Bootstrap (D-09): an exclusive flock on ROOT/.venv.lock serializes starts. A
# completion stamp ROOT/.venv.omega-prime-bootstrap.json records the lock,
# pyproject, interpreter and prime-agent commit. A matching stamp skips the
# install and runs only the import probe. Otherwise a fresh venv is built in
# ROOT/.venv.build from requirements-lock.txt plus `--no-deps -e .`, probed,
# renamed into place, and stamped last. The stamp also marks ownership: an
# existing ROOT/.venv without a stamp is operator-managed and is never moved,
# deleted or rebuilt; it is used when the import probe passes, else exit 11.
# Only the prime-agent submodule is ever initialized (D-04). The plugin
# catalog `--check` runs on every bootstrap.
#
# Stdout is protocol-only: fd 1 points at stderr until the final exec, and
# every bootstrap child reads /dev/null. Verify mode may print the verifier's
# `--json` report on stdout; it is not a protocol stream.
#
# Exit classes (one JSON line on stderr):
#   10 missing_prerequisite  11 install_failed  12 catalog_drift
#   13 host_unreachable (verify mode: any non-zero verifier exit)
# 2 is usage. oneclick's own codes 1 and 3 are never produced here.

set -euo pipefail

# fd 3 keeps the caller's stdout for the final exec; everything else is stderr.
exec 3>&1 1>&2
unset CDPATH
export GIT_TERMINAL_PROMPT=0 PIP_NO_INPUT=1 PIP_DISABLE_PIP_VERSION_CHECK=1

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
ROOT="$(cd -P "$SCRIPT_DIR/../.." && pwd)"

VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python"
BUILD="$VENV.build"
LOCK="$VENV.lock"
STAMP="$VENV.omega-prime-bootstrap.json"

log() {
    printf 'grokbot-1click: %s\n' "$*" >&2
}

# classify CODE CLASS [EXTRA_JSON_MEMBERS]: one JSON class line, then exit.
classify() {
    printf '{"class":"%s","exit":%d%s}\n' "$2" "$1" "${3:+,$3}" >&2
    exit "$1"
}

usage() {
    log "usage: $*"
    exit 2
}

BOOTSTRAP=0
BOOTSTRAP_ONLY=0
VERIFY=0
PASS=()
for arg in "$@"; do
    case "$arg" in
        --bootstrap) BOOTSTRAP=1 ;;
        --bootstrap-only) BOOTSTRAP_ONLY=1 ;;
        --verify) VERIFY=1 ;;
        *) PASS+=("$arg") ;;
    esac
done

if ((BOOTSTRAP_ONLY && VERIFY)); then
    usage "--bootstrap-only and --verify are exclusive"
fi
if ((BOOTSTRAP_ONLY)) && ((${#PASS[@]})); then
    usage "--bootstrap-only takes no other arguments"
fi
VERIFY_JSON=0
if ((VERIFY)); then
    for arg in ${PASS[@]+"${PASS[@]}"}; do
        case "$arg" in
            --json) VERIFY_JSON=1 ;;
            *) usage "--verify accepts only --json" ;;
        esac
    done
fi

# Python snippets run by the base interpreter or the venv, never reading stdin.
PY_VERSION_CODE='import sys; print("%d.%d.%d" % sys.version_info[:3])'
PY_VENV_CODE='import venv, ensurepip'
PROBE_CODE='import omega_prime, mcp'
STAMP_CODE='
import hashlib, json, os, sys
root, commit = sys.argv[1], sys.argv[2]
def digest(name):
    with open(os.path.join(root, name), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()
print(json.dumps({
    "lock_sha256": digest("requirements-lock.txt"),
    "prime_agent_commit": commit,
    "pyproject_sha256": digest("pyproject.toml"),
    "python": "%d.%d.%d" % sys.version_info[:3],
    "python_executable": os.path.realpath(sys.executable),
}, sort_keys=True))
'
# Point the fresh venv's own scripts (shebangs, activate) at the final path.
RELOCATE_CODE='
import os, sys
build, final = sys.argv[1].encode(), sys.argv[2].encode()
bindir = os.path.join(sys.argv[1], "bin")
for name in os.listdir(bindir):
    path = os.path.join(bindir, name)
    if os.path.islink(path) or not os.path.isfile(path):
        continue
    with open(path, "rb") as fh:
        data = fh.read()
    if build not in data or not (data.startswith(b"#!") or name.startswith("activate")):
        continue
    mode = os.stat(path).st_mode
    with open(path, "wb") as fh:
        fh.write(data.replace(build, final))
    os.chmod(path, mode)
'

BASE_PY=""

check_prerequisites() {
    local missing=() cand path version major minor modern=0
    if ((BASH_VERSINFO[0] < 4)); then
        missing+=('"bash>=4"')
    fi
    command -v git >/dev/null 2>&1 || missing+=('"git"')
    command -v flock >/dev/null 2>&1 || missing+=('"flock"')
    for cand in python3.12 python3 python3.13 python3.11; do
        path="$(command -v "$cand" 2>/dev/null)" || continue
        version="$("$path" -c "$PY_VERSION_CODE" </dev/null 2>/dev/null)" || continue
        major="${version%%.*}"
        minor="${version#*.}"
        minor="${minor%%.*}"
        [[ $major =~ ^[0-9]+$ && $minor =~ ^[0-9]+$ ]] || continue
        if ((major != 3 || minor < 11)); then
            continue
        fi
        modern=1
        if "$path" -c "$PY_VENV_CODE" </dev/null >/dev/null 2>&1; then
            BASE_PY="$path"
            [ "$major.$minor" = "3.12" ] || log "warning: using Python $version; requirements-lock.txt was recorded on 3.12"
            break
        fi
    done
    if [ -z "$BASE_PY" ]; then
        if ((modern)); then
            missing+=('"python3-venv"')
        else
            missing+=('"python3>=3.11"')
        fi
    fi
    [ -f "$ROOT/pyproject.toml" ] || missing+=('"pyproject.toml"')
    [ -f "$ROOT/requirements-lock.txt" ] || missing+=('"requirements-lock.txt"')
    if ((${#missing[@]})); then
        local joined
        joined="$(IFS=,; printf '%s' "${missing[*]}")"
        classify 10 missing_prerequisite "\"missing\":[$joined]"
    fi
}

init_prime_agent() {
    if [ ! -e "$ROOT/.git" ]; then
        log "warning: $ROOT is not a git checkout; prime-agent is not initialized (Prime families stay off)"
        return 0
    fi
    [ -e "$ROOT/prime-agent/.git" ] && return 0
    log "initializing the prime-agent submodule"
    if ! git -C "$ROOT" submodule update --init prime-agent </dev/null; then
        log "warning: prime-agent could not be initialized; Prime families stay off"
    fi
    return 0
}

probe() {
    "$1" -I -c "$PROBE_CODE" </dev/null
}

fail_build() {
    rm -rf -- "$BUILD"
    classify 11 install_failed "\"step\":\"$1\""
}

build_venv() {
    local old
    for old in "$VENV".old.[0-9]*; do
        [ -e "$old" ] || [ -L "$old" ] || continue
        rm -rf -- "$old"
    done
    rm -rf -- "$BUILD" || fail_build venv
    log "building a fresh environment from requirements-lock.txt (progress on stderr)"
    "$BASE_PY" -m venv "$BUILD" </dev/null || fail_build venv
    "$BUILD/bin/python" -m pip install -r "$ROOT/requirements-lock.txt" </dev/null ||
        fail_build pip_lock
    "$BUILD/bin/python" -m pip install --no-deps -e "$ROOT" </dev/null ||
        fail_build pip_editable
    probe "$BUILD/bin/python" || fail_build probe
    "$BASE_PY" -c "$RELOCATE_CODE" "$BUILD" "$VENV" </dev/null || fail_build relocate

    # Swap only a bootstrap-owned venv. The stamp sidecar is the ownership
    # marker, so a placeholder that never matches a real stamp replaces it
    # first: a start killed mid-swap still sees an owned venv and rebuilds.
    write_stamp '{"owner":"omega-prime-bootstrap","state":"swapping"}'
    old="$VENV.old.$$"
    if [ -e "$VENV" ] || [ -L "$VENV" ]; then
        mv -- "$VENV" "$old" || fail_build swap
    fi
    mv -- "$BUILD" "$VENV" || classify 11 install_failed '"step":"swap"'
    rm -rf -- "$old"
    probe "$VENV_PY" || classify 11 install_failed '"step":"probe"'
}

write_stamp() {
    local tmp="$STAMP.tmp.$$"
    if ! (umask 077 && printf '%s\n' "$1" >"$tmp") || ! mv -f -- "$tmp" "$STAMP"; then
        rm -f -- "$tmp"
        classify 11 install_failed '"step":"stamp"'
    fi
}

bootstrap() {
    check_prerequisites

    local saved_umask
    saved_umask="$(umask)"
    umask 077
    if ! exec 9>>"$LOCK"; then
        umask "$saved_umask"
        classify 11 install_failed '"step":"lock"'
    fi
    umask "$saved_umask"
    if ! flock -n 9; then
        log "another bootstrap holds $LOCK; waiting"
        flock 9 || classify 11 install_failed '"step":"lock"'
    fi

    init_prime_agent

    local commit=none want have=""
    if [ -e "$ROOT/prime-agent/.git" ]; then
        commit="$(git -C "$ROOT/prime-agent" rev-parse HEAD </dev/null 2>/dev/null)" || commit=none
    fi
    want="$("$BASE_PY" -c "$STAMP_CODE" "$ROOT" "$commit" </dev/null)" ||
        classify 11 install_failed '"step":"stamp"'
    [ -f "$STAMP" ] && have="$(<"$STAMP")"

    local warm=0
    if [ ! -e "$STAMP" ] && { [ -e "$VENV" ] || [ -L "$VENV" ]; }; then
        # No stamp: the bootstrap did not create this venv. Never move,
        # delete or rebuild it.
        if ! probe "$VENV_PY"; then
            log "error: $VENV was not created by this bootstrap and fails the import probe; fix that venv or move it away, then rerun"
            classify 11 install_failed '"step":"operator_venv"'
        fi
        warm=1
        log "using operator-managed .venv (not created by bootstrap; left unchanged)"
    elif [ "$have" = "$want" ] && [ -x "$VENV_PY" ]; then
        if probe "$VENV_PY"; then
            warm=1
            log "environment is up to date (stamp matches)"
        else
            log "stamped environment fails the import probe; rebuilding"
        fi
    fi
    if ((!warm)); then
        build_venv
        write_stamp "$want"
        log "environment ready"
    fi

    log "checking the generated plugin catalog"
    "$VENV_PY" -m omega_prime.tooling.plugin --root "$ROOT" --check </dev/null ||
        classify 12 catalog_drift

    exec 9>&-

    # Host state stays outside a replaceable plugin root; caller values win.
    if [ -z "${OMEGA_PRIME_STATE_DIR:-}" ] && [ -n "${XDG_STATE_HOME:-${HOME:-}}" ]; then
        export OMEGA_PRIME_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omega-prime"
    fi
    if [ -z "${OMEGA_PRIME_WORK_ROOT:-}" ]; then
        if [ -d /workspace ]; then
            export OMEGA_PRIME_WORK_ROOT=/workspace
        elif [ -n "${HOME:-}" ]; then
            export OMEGA_PRIME_WORK_ROOT="$HOME"
        fi
    fi
}

if ((BOOTSTRAP || BOOTSTRAP_ONLY || VERIFY)); then
    bootstrap
    cd "$ROOT"
    if ((BOOTSTRAP_ONLY)); then
        log "bootstrap complete"
        exit 0
    fi
    if ((VERIFY)); then
        rc=0
        if ((VERIFY_JSON)); then
            "$VENV_PY" -m omega_prime.tooling.plugin --root "$ROOT" --verify --json \
                </dev/null 1>&3 3>&- || rc=$?
        else
            "$VENV_PY" -m omega_prime.tooling.plugin --root "$ROOT" --verify \
                </dev/null 3>&- || rc=$?
        fi
        ((rc == 0)) && exit 0
        classify 13 host_unreachable "\"verifier_exit\":$rc"
    fi
    exec "$VENV_PY" -m omega_prime.grokbot.oneclick --root "$ROOT" \
        --transport stdio --skip-doctor ${PASS[@]+"${PASS[@]}"} 1>&3 3>&-
fi

if [ -x "$VENV_PY" ]; then
    PYTHON_BIN="$VENV_PY"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
else
    PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi
[ -n "$PYTHON_BIN" ] || classify 10 missing_prerequisite '"missing":["python3>=3.11"]'

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
exec "$PYTHON_BIN" -m omega_prime.grokbot.oneclick --root "$ROOT" \
    ${PASS[@]+"${PASS[@]}"} 1>&3 3>&-

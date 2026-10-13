#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
#
# Local image-digest rollback drill (Phase 80-02).
#
# Exercises promote -> rollback -> healthy ENTIRELY against local docker:
# two locally-imported images stand in for the previous and current digests,
# each takes a docker save/load round-trip with checksum-before-load, the
# current digest is promoted through `deploy.py promote`, then the
# previous_digest read from the newest promotion evidence file is re-promoted
# (never a moving tag, never a rebuild), and healthcheck re-runs against a
# local stub server on the rendered port.
#
# Local-only by construction: no push, no ssh, no remote registry, no
# production contact. Aborts with a clear message when docker is unavailable.
set -euo pipefail

PREV_TAG="omp-rollback-drill:previous"
CUR_TAG="omp-rollback-drill:current"
PORT="18081"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "rollback-drill: required command '$1' not found; aborting (docker unavailable or python3 missing)" >&2
    exit 1
  fi
}

need docker
need python3
if ! docker info >/dev/null 2>&1; then
  echo "rollback-drill: docker daemon unreachable; aborting (no local docker, nothing touched)" >&2
  exit 1
fi
# Prefer the repo venv when launched from the repository root.
PY="python3"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/omp80-rollback-XXXXXX")"
EVDIR="$WORK/evidence"
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
  docker rmi -f "$PREV_TAG" "$CUR_TAG" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$EVDIR"
echo "rollback-drill: workdir $WORK"

# 1. Two local images via `docker import` (no base pull, no registry).
printf 'omp-rollback-drill previous\n' > "$WORK/previous.txt"
tar -C "$WORK" -cf "$WORK/previous.tar" previous.txt
docker import "$WORK/previous.tar" "$PREV_TAG" >/dev/null
printf 'omp-rollback-drill current\n' > "$WORK/current.txt"
tar -C "$WORK" -cf "$WORK/current.tar" current.txt
docker import "$WORK/current.tar" "$CUR_TAG" >/dev/null
PREV_DIGEST="$(docker inspect --format='{{.Id}}' "$PREV_TAG")"
CUR_DIGEST="$(docker inspect --format='{{.Id}}' "$CUR_TAG")"
echo "rollback-drill: previous=$PREV_DIGEST current=$CUR_DIGEST"

# 2. Save/load round-trip with checksum-before-load for each digest.
for tag in "$PREV_TAG" "$CUR_TAG"; do
  safe="$(echo "$tag" | tr ':/' '__')"
  docker save "$tag" -o "$WORK/$safe.tar"
  (cd "$WORK" && sha256sum "$safe.tar" > "$safe.tar.sha256")
  (cd "$WORK" && sha256sum -c "$safe.tar.sha256")
  docker load -i "$WORK/$safe.tar" >/dev/null
  loaded="$(docker inspect --format='{{.Id}}' "$tag")"
  echo "rollback-drill: round-trip ok $tag ($loaded)"
done
if [ "$(docker inspect --format='{{.Id}}' "$PREV_TAG")" != "$PREV_DIGEST" ]; then
  echo "rollback-drill: previous image ID changed across round-trip; aborting" >&2
  exit 1
fi
if [ "$(docker inspect --format='{{.Id}}' "$CUR_TAG")" != "$CUR_DIGEST" ]; then
  echo "rollback-drill: current image ID changed across round-trip; aborting" >&2
  exit 1
fi

# 3. Evidence dir: rendered k8s target plus staging/approval records.
"$PY" -m omega_prime.grokbot.deploy render --target k8s --out "$EVDIR" --port "$PORT" >/dev/null
printf '{"ok": true, "target": "staging"}\n' > "$EVDIR/staging-receipt.json"
printf '{"approver": "rollback-drill"}\n' > "$EVDIR/approval.json"

# 4. Baseline promote of the previous digest, then promote of the current one.
printf '%s\n' "$PREV_DIGEST" > "$EVDIR/image.digest"
"$PY" -m omega_prime.grokbot.deploy promote --digest "$PREV_DIGEST" --dir "$EVDIR" --approval "$EVDIR/approval.json"
printf '%s\n' "$CUR_DIGEST" > "$EVDIR/image.digest"
"$PY" -m omega_prime.grokbot.deploy promote --digest "$CUR_DIGEST" --dir "$EVDIR" --approval "$EVDIR/approval.json"
echo "rollback-drill: PASS promote (current digest promoted with previous chained)"

# 5. Rollback: re-promote previous_digest from the newest evidence file.
ROLLBACK_DIGEST="$("$PY" -c "import json,glob; print(json.load(open(sorted(glob.glob('$EVDIR/*promotion*evidence*.json'))[-1]))['previous_digest'])")"
if [ -z "$ROLLBACK_DIGEST" ] || [ "$ROLLBACK_DIGEST" = "None" ]; then
  echo "rollback-drill: no previous_digest in the newest evidence; aborting" >&2
  exit 1
fi
printf '%s\n' "$ROLLBACK_DIGEST" > "$EVDIR/image.digest"
"$PY" -m omega_prime.grokbot.deploy promote --digest "$ROLLBACK_DIGEST" --dir "$EVDIR" --approval "$EVDIR/approval.json"
echo "rollback-drill: PASS rollback (re-promoted previous_digest $ROLLBACK_DIGEST, recorded as new evidence)"

# 6. Healthy: stub server on the rendered port, then healthcheck.
cat > "$WORK/stub.py" <<EOF
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/healthz", "/readyz"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", $PORT), Handler).serve_forever()
EOF
"$PY" "$WORK/stub.py" &
SERVER_PID="$!"
for _ in $(seq 1 50); do
  if "$PY" -c "import socket; socket.create_connection(('127.0.0.1', $PORT), timeout=1).close()" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
"$PY" -m omega_prime.grokbot.deploy healthcheck --target k8s --dir "$EVDIR"
echo "rollback-drill: PASS healthy (healthcheck exit 0 after rollback)"

echo "rollback-drill: evidence files:"
ls "$EVDIR"/*promotion*evidence*.json
echo "rollback-drill: DRILL OK (local docker only; production never contacted)"

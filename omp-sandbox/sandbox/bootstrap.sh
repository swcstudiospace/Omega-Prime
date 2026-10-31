#!/usr/bin/env bash
# First-boot install inside an OpenShell sandbox (Ubuntu 24.04 base image). Cached: re-runs are no-ops.
# Installs pinned Node 24, Bun, omp 18.8.4, sandbox-agent and GSD-Core 1.18.0 under /sandbox/opt.
# Status: NOT exercised live (MicroVM driver cannot boot on this host).
set -euo pipefail
OPT=/sandbox/opt; MARK=$OPT/.bootstrap-v1; mkdir -p "$OPT" /sandbox/work /sandbox/home
[ -f "$MARK" ] && { echo "bootstrap: cached"; exit 0; }
NODE_V=24.21.0
curl -fsSL "https://nodejs.org/dist/v$NODE_V/node-v$NODE_V-linux-x64.tar.xz" | tar -xJ -C "$OPT" && mv "$OPT/node-v$NODE_V-linux-x64" "$OPT/node"
export PATH="$OPT/node/bin:$PATH" npm_config_cache=/sandbox/home/.npm
npm i -g --prefix "$OPT/bun" bun@1.4.2
npm i -g --prefix "$OPT/omp" @oh-my-pi/pi-coding-agent@18.8.4 sandbox-agent@0.5.3
mkdir -p "$OPT/bin"; ln -sf "$OPT/node/bin/node" "$OPT/bin/node"; ln -sf "$OPT/bun/bin/bun" "$OPT/bin/bun"; ln -sf "$OPT/omp/bin/omp" "$OPT/bin/omp"
cd /sandbox/work && npx -y @opengsd/gsd-core@1.18.0 --claude --local --no-legacy-cleanup < /dev/null
echo "export PATH=$OPT/bin:\$PATH HOME=/sandbox/home" > /sandbox/home/.profile
touch "$MARK"; echo "bootstrap: done"

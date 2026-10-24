#!/bin/sh
# Single-ID rootless podman for the omega-ci pool.
#
# The runner is an unprivileged Railway container. A single-ID user namespace
# works (`unshare --map-root-user`). The subordinate range in /etc/subuid does
# not: setuid newuidmap gets EPERM writing uid_map (CapEff 0). Podman treats a
# configured range as mandatory and exits instead of falling back.
#
# Shims named newuidmap and newgidmap, placed first on PATH, write the one
# mapping the kernel allows the job user to write and ignore the extra range.
# Storage uses ignore_chown_errors and fuse-overlayfs or vfs, under this job's
# RUNNER_TEMP so concurrent jobs do not share a graph. Builds use chroot
# isolation. A libc-free LD_PRELOAD makes chown succeed during image build,
# because a single mapped uid cannot also be uid 10001 while the build is root.
#
# vfs layer apply remounts / with MS_REC|MS_PRIVATE, then buildah bind-mounts
# the build root onto itself (MS_BIND). Both return EPERM: the container's
# root mount is locked and CapBnd has no CAP_SYS_ADMIN. Shims cannot invent
# a mount. The runner image has to `mount --make-rslave /` at start.
#
# `setup` is what the workflow runs. `install-shims DEST` only writes the two
# shims (the unit test drives those directly).
set -eu

write_idmap_shim() {
  shim_path="$1"
  kind="$2"
  cat > "$shim_path" << EOF
#!/bin/sh
# ${kind}: single-ID map. The pool cannot apply a subordinate range.
set -eu
pid=\$1
shift
mine=\$(id -${kind})
line=""
while [ "\$#" -ge 3 ]; do
  ns=\$1
  host=\$2
  count=\$3
  shift 3
  if [ "\$host" = "\$mine" ] && [ "\$count" = "1" ]; then
    line="\$ns \$host \$count"
    break
  fi
done
if [ -z "\$line" ]; then
  line="0 \$mine 1"
fi
if [ "${kind}" = "g" ]; then
  printf 'deny\n' > "/proc/\$pid/setgroups" 2>/dev/null || true
  printf '%s\n' "\$line" > "/proc/\$pid/gid_map"
else
  printf '%s\n' "\$line" > "/proc/\$pid/uid_map"
fi
EOF
  chmod 0755 "$shim_path"
}

install_shims() {
  shim_dir="$1"
  mkdir -p "$shim_dir"
  write_idmap_shim "$shim_dir/newuidmap" u
  write_idmap_shim "$shim_dir/newgidmap" g
}

# The workflow's docker CLI is podman. Call the real binary so a PATH shim
# named podman cannot recurse.
write_engine_shim() {
  shim_path="$1"
  cat > "$shim_path" << 'EOF'
#!/bin/sh
set -eu
exec /usr/bin/podman "$@"
EOF
  chmod 0755 "$shim_path"
}

compile_fakechown() {
  out="$1"
  src="${out}.c"
  cat > "$src" << 'EOF'
/* libc-free: safe to preload into the Debian build image from an Ubuntu host.
 * This makes chown return 0. It does not record uid 10001 in the image.
 * A publishable image needs a real chown, which needs both uids mapped.
 * This pool cannot do that until mounts and a subordinate range work. */
int chown(const char *path, unsigned int owner, unsigned int group) {
  (void)path; (void)owner; (void)group; return 0;
}
int lchown(const char *path, unsigned int owner, unsigned int group) {
  (void)path; (void)owner; (void)group; return 0;
}
int fchown(int fd, unsigned int owner, unsigned int group) {
  (void)fd; (void)owner; (void)group; return 0;
}
int fchownat(int fd, const char *path, unsigned int owner, unsigned int group, int flag) {
  (void)fd; (void)path; (void)owner; (void)group; (void)flag; return 0;
}
EOF
  gcc -shared -fPIC -nostdlib -o "$out" "$src"
  rm -f "$src"
}

write_storage_conf() {
  conf="$1"
  graph="$2"
  runroot="$3"
  driver="$4"
  mount_program="$5"
  cat > "$conf" << EOF
[storage]
driver = "${driver}"
graphroot = "${graph}"
runroot = "${runroot}"

[storage.options]
ignore_chown_errors = "true"
EOF
  if [ -n "$mount_program" ]; then
    cat >> "$conf" << EOF
mount_program = "${mount_program}"

[storage.options.overlay]
ignore_chown_errors = "true"
mount_program = "${mount_program}"
EOF
  fi
}

write_containers_conf() {
  cat > "$1" << 'EOF'
[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
EOF
}

diagnose() {
  echo "single-ID podman setup failed" >&2
  grep -E '^(NoNewPrivs|CapEff|CapBnd):' /proc/self/status || true
  id || true
  grep -E "^$(id -un):" /etc/subuid /etc/subgid || echo "no subordinate ID ranges for $(id -un)"
  echo "PATH=$PATH"
  command -v newuidmap || true
  command -v newgidmap || true
}

setup() {
  : "${RUNNER_TEMP:?RUNNER_TEMP is required}"
  : "${GITHUB_ENV:?GITHUB_ENV is required}"
  : "${GITHUB_PATH:?GITHUB_PATH is required}"
  root="${RUNNER_TEMP}/podman-single-id"
  bin="${root}/bin"
  runtime="${root}/run"
  graph="${root}/graph"
  conf_dir="${root}/conf"
  mkdir -p "$bin" "$runtime" "$graph" "$conf_dir"
  chmod 700 "$runtime"
  install_shims "$bin"
  write_engine_shim "${bin}/docker"
  write_engine_shim "${bin}/podman"
  compile_fakechown "${root}/ci-fakechown.so"

  driver="vfs"
  mount_program=""
  if [ -c /dev/fuse ] && [ -x /usr/bin/fuse-overlayfs ]; then
    driver="overlay"
    mount_program="/usr/bin/fuse-overlayfs"
  fi
  write_storage_conf "${conf_dir}/storage.conf" "$graph" "$runtime" "$driver" "$mount_program"
  write_containers_conf "${conf_dir}/containers.conf"

  # GITHUB_PATH entries are prepended, so this directory wins over /usr/bin.
  printf '%s\n' "$bin" >> "$GITHUB_PATH"
  {
    printf 'XDG_RUNTIME_DIR=%s\n' "$runtime"
    printf 'CONTAINERS_STORAGE_CONF=%s\n' "${conf_dir}/storage.conf"
    printf 'CONTAINERS_CONF=%s\n' "${conf_dir}/containers.conf"
    printf 'BUILDAH_ISOLATION=chroot\n'
    printf 'OMEGA_CI_FAKECHOWN=%s\n' "${root}/ci-fakechown.so"
  } >> "$GITHUB_ENV"

  export XDG_RUNTIME_DIR="$runtime"
  export CONTAINERS_STORAGE_CONF="${conf_dir}/storage.conf"
  export CONTAINERS_CONF="${conf_dir}/containers.conf"
  export BUILDAH_ISOLATION=chroot
  export PATH="${bin}:${PATH}"

  if ! podman info --format 'rootless={{.Host.Security.Rootless}} graph={{.Store.GraphDriverName}} cgroups={{.Host.CgroupsVersion}} network={{.Host.NetworkBackend}}'; then
    diagnose
    exit 1
  fi
  echo "single-ID podman: driver=${driver} fakechown=${root}/ci-fakechown.so"
  # Mounts are a property of the container, not of the single-ID map. Say so
  # in the log before the build spends a pull discovering it again.
  cap_bnd=$(awk '/^CapBnd:/ { print $2 }' /proc/self/status)
  echo "mount note: CapBnd=${cap_bnd} (needs bit 21, CAP_SYS_ADMIN, to mount --make-rslave /). Measured on this pool: MS_REC|MS_PRIVATE on / and MS_BIND of a build root are both EPERM, including inside podman's user namespace. deploy/ci-runner-desktop must gain that capability and run the rslave mount before the job user starts. /dev/fuse is absent, so fuse-overlayfs is not a way around it."
}

case "${1:-}" in
  install-shims)
    install_shims "${2:?destination required}"
    ;;
  install-engine)
    write_engine_shim "${2:?destination required}"
    ;;
  setup)
    setup
    ;;
  *)
    echo "usage: ci-podman-single-id.sh setup | install-shims DEST" >&2
    exit 2
    ;;
esac

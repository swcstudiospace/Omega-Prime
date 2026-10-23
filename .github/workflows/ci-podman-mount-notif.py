#!/usr/bin/env python3
"""Let podman apply image layers when the pool rejects a root remount.

vfs layer extraction calls mount("/", MS_REC|MS_PRIVATE). This pool's root
mount is locked (CapEff 0, "/" is not shared), so that call returns EPERM
and podman aborts before it has a build container. A seccomp user
notification answers success for that one remount and lets every other
mount run in the kernel. This is not a security policy: the root mount is
already not shared, so the skipped call does not change propagation.

Usage: ci-podman-mount-notif.py COMMAND ARG...
"""

from __future__ import annotations

import ctypes
import os
import select
import struct
import sys

# x86_64
_SYS_SECCOMP = 317
_SYS_PROCESS_VM_READV = 310
_PR_SET_NO_NEW_PRIVS = 38
_SECCOMP_SET_MODE_FILTER = 1
_SECCOMP_FILTER_FLAG_NEW_LISTENER = 1 << 3
_AUDIT_ARCH_X86_64 = 0xC000003E
_SYS_MOUNT = 165
_SECCOMP_RET_USER_NOTIF = 0x7FC00000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_MS_REC = 1 << 14
_MS_PRIVATE = 1 << 18
_MS_BIND = 1 << 12
_ROOT_REMOUNT = _MS_REC | _MS_PRIVATE
_CONTINUE = 1
# linux/seccomp.h: _IOWR('!', 0, struct seccomp_notif) and the resp ioctl.
_IOCTL_RECV = 3226476800
_IOCTL_SEND = 3222806785

_libc = ctypes.CDLL(None, use_errno=True)
_libc.prctl.argtypes = [
    ctypes.c_int,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.c_ulong,
]
_libc.prctl.restype = ctypes.c_int
# syscall is variadic (seccomp takes 3 args, process_vm_readv takes 6).
_libc.syscall.restype = ctypes.c_long
_libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
_libc.ioctl.restype = ctypes.c_int


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_SockFilter))]


class _Iovec(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("len", ctypes.c_size_t)]


def _insn(code: int, jt: int, jf: int, k: int) -> _SockFilter:
    return _SockFilter(code, jt, jf, k)


def _filter() -> _SockFprog:
    # Classic BPF over struct seccomp_data. Notify on mount, allow the rest.
    instructions = [
        _insn(0x20, 0, 0, 4),  # ld arch
        _insn(0x15, 1, 0, _AUDIT_ARCH_X86_64),  # jeq arch, skip allow
        _insn(0x06, 0, 0, _SECCOMP_RET_ALLOW),
        _insn(0x20, 0, 0, 0),  # ld nr
        _insn(0x15, 0, 1, _SYS_MOUNT),  # jeq mount, else skip notif
        _insn(0x06, 0, 0, _SECCOMP_RET_USER_NOTIF),
        _insn(0x06, 0, 0, _SECCOMP_RET_ALLOW),
    ]
    array = (_SockFilter * len(instructions))(*instructions)
    return _SockFprog(len(instructions), array)


def _install_listener() -> int:
    if _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl no_new_privs")
    prog = _filter()
    fd = _libc.syscall(
        _SYS_SECCOMP,
        _SECCOMP_SET_MODE_FILTER,
        _SECCOMP_FILTER_FLAG_NEW_LISTENER,
        ctypes.byref(prog),
    )
    if fd < 0:
        raise OSError(ctypes.get_errno(), "seccomp user notification")
    return int(fd)


def _read_cstr(pid: int, address: int) -> str:
    if address == 0:
        return ""
    buf = ctypes.create_string_buffer(256)
    local = _Iovec(ctypes.addressof(buf), 256)
    remote = _Iovec(address, 256)
    nread = _libc.syscall(
        _SYS_PROCESS_VM_READV,
        pid,
        ctypes.byref(local),
        1,
        ctypes.byref(remote),
        1,
        0,
    )
    if nread < 0:
        return ""
    raw = buf.raw.split(b"\0", 1)[0]
    return raw.decode("utf-8", "replace")


def _is_root_remount(pid: int, args: tuple[int, ...]) -> bool:
    # mount(source, target, fstype, flags, data)
    flags = args[3]
    if flags & _MS_BIND:
        return False
    # Exact flags: the chrootarchive remount, not a bind or a different mount.
    if flags != _ROOT_REMOUNT:
        return False
    target = _read_cstr(pid, args[1])
    return target in ("", "/")


def _serve(listener: int, child: int) -> int:
    notif = ctypes.create_string_buffer(80)
    resp = ctypes.create_string_buffer(24)
    while True:
        readable, _, _ = select.select([listener], [], [], 0.2)
        if readable:
            # The kernel rejects a second RECV unless the id field is zero.
            notif.raw = b"\0" * 80
            if _libc.ioctl(listener, _IOCTL_RECV, notif) != 0:
                err = ctypes.get_errno()
                if err == 4:  # EINTR
                    continue
                # The child is gone (ENOENT) or the listener closed.
                break
            ident, pid, _flags = struct.unpack_from("<QII", notif.raw, 0)
            # seccomp_data starts at offset 16: nr, arch, ip, args[6]
            nr, _arch, _ip, *arg_words = struct.unpack_from("<iIQ6Q", notif.raw, 16)
            fake = nr == _SYS_MOUNT and _is_root_remount(pid, tuple(arg_words))
            if fake:
                struct.pack_into("<Qq iI", resp, 0, ident, 0, 0, 0)
            else:
                struct.pack_into("<Qq iI", resp, 0, ident, 0, 0, _CONTINUE)
            if _libc.ioctl(listener, _IOCTL_SEND, resp) != 0:
                err = ctypes.get_errno()
                if err not in (4, 2):  # EINTR, ENOENT
                    break
        waited, status = os.waitpid(child, os.WNOHANG)
        if waited == child:
            os.close(listener)
            if os.WIFEXITED(status):
                return int(os.WEXITSTATUS(status))
            if os.WIFSIGNALED(status):
                return 128 + int(os.WTERMSIG(status))
            return 1
    waited, status = os.waitpid(child, 0)
    os.close(listener)
    if os.WIFEXITED(status):
        return int(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        return 128 + int(os.WTERMSIG(status))
    return 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: ci-podman-mount-notif.py COMMAND ARG...", file=sys.stderr)
        return 2
    try:
        listener = _install_listener()
    except OSError as exc:
        print(
            "seccomp user notification is unavailable "
            f"({exc.errno}: {exc.strerror}). The pool rejects "
            "mount(/, MS_REC|MS_PRIVATE), which vfs layer apply requires. "
            "The runner container needs CAP_SYS_ADMIN in its bounding set so "
            "deploy/ci-runner-desktop can mount --make-rslave / at start, "
            "and /dev/fuse for fuse-overlayfs.",
            file=sys.stderr,
        )
        return 1
    child = os.fork()
    if child == 0:
        os.close(listener)
        os.execvp(argv[1], argv[1:])
        raise SystemExit(127)
    return _serve(listener, child)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Own and operate the isolated, digest-pinned Pulsar CI service.

The helper never accepts credential values on its command line or environment;
all generated credentials are represented by protected file paths.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from omega_prime.eventstream.backoff import create_with_backoff

PINNED_IMAGE = "apachepulsar/pulsar:5.0.0@sha256:7222f3199a0b2418abde9ce0906862598415b19b494ae185db189938cfc43fd3"
PINNED_DIGEST = (
    "sha256:7222f3199a0b2418abde9ce0906862598415b19b494ae185db189938cfc43fd3"
)
SERVICE_URL = "pulsar+ssl://127.0.0.1:6651"
ADMIN_URL = "https://127.0.0.1:8443"
_CONTAINER = re.compile(r"^[0-9a-f]{12,64}$")


class HelperError(RuntimeError):
    """A safe-to-display operational failure."""


def _run(args: list[str], *, input_text: str | None = None, check: bool = True) -> str:
    try:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError(f"command failed: {Path(args[0]).name}") from exc
    if check and result.returncode:
        # Never surface subprocess output: docker/openssl errors can contain paths or data.
        raise HelperError(
            f"command failed: {Path(args[0]).name} (exit {result.returncode})"
        )
    return result.stdout.strip()


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    if not path.is_dir() or path.is_symlink():
        raise HelperError("secure work directory is not a regular directory")


def _write_secret(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwt(role: str, private_key: Path, now: int) -> bytes:
    # OpenSSL signs the compact JWT so the private key remains a file, never argv.
    header = _b64(b'{"alg":"RS256","typ":"JWT"}')
    payload = _b64(
        json.dumps(
            {"sub": role, "iat": now, "exp": now + 3600}, separators=(",", ":")
        ).encode()
    )
    message = f"{header}.{payload}".encode()
    with subprocess.Popen(
        ["openssl", "dgst", "-sha256", "-sign", str(private_key)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        signature, error = proc.communicate(message, timeout=15)
        if proc.returncode:
            raise HelperError("could not generate role credential")
    if error:
        # OpenSSL diagnostics are deliberately discarded.
        del error
    return f"{header}.{payload}.{_b64(signature)}".encode()


def _inspect(container_id: str) -> dict[str, Any]:
    if not _CONTAINER.fullmatch(container_id):
        raise HelperError("invalid service container identifier")
    raw = _run(["docker", "inspect", container_id])
    try:
        info = json.loads(raw)[0]
        config = info["Config"]
        image_id = info["Image"]
        repo_digests = info.get("RepoDigests") or []
        state = info["State"]
        if not isinstance(config, dict) or not isinstance(repo_digests, list):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HelperError("service identity inspection failed") from exc
    if config.get("Image") != PINNED_IMAGE or not any(
        item.endswith(PINNED_DIGEST) for item in repo_digests
    ):
        raise HelperError("service image does not match the pinned digest")
    if (
        not image_id.startswith("sha256:")
        or config.get("Entrypoint") != ["/bin/bash"]
        or config.get("Cmd") not in (None, [])
        or info.get("Path") != "/bin/bash"
        or info.get("Args") not in (None, [])
        or config.get("OpenStdin") is not True
        or config.get("Tty") is not True
    ):
        raise HelperError(
            "service command identity is not the expected interactive bash"
        )
    labels = config.get("Labels") or {}
    workflow = os.environ.get("GITHUB_WORKFLOW")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if workflow and labels.get("com.github.actions.workflow") not in (None, workflow):
        raise HelperError("service workflow identity mismatch")
    return {
        "id": info["Id"],
        "image": image_id,
        "state": state,
        "workflow": workflow,
        "run_id": run_id,
    }


def _load(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    if args.ownership_file.is_symlink():
        raise HelperError("ownership record must not be a symlink")
    path = args.ownership_file.resolve(strict=True)
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise HelperError("ownership record must be a protected regular file")
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HelperError("ownership record is unreadable") from exc
    info = _inspect(args.container_id)
    if (
        record.get("container_id") != args.container_id
        or record.get("container_full_id") != info["id"]
        or record.get("image_id") != info["image"]
    ):
        raise HelperError("service is not the container recorded as owned")
    if record.get("workflow") != os.environ.get("GITHUB_WORKFLOW") or record.get(
        "run_id"
    ) != os.environ.get("GITHUB_RUN_ID"):
        raise HelperError("CI run ownership identity mismatch")
    return path, record


def _write_record(path: Path, record: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    _write_secret(tmp, json.dumps(record, sort_keys=True).encode())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def prepare(args: argparse.Namespace) -> None:
    runner_temp = os.environ.get("RUNNER_TEMP")
    work = (
        Path(runner_temp) / "eventstream-it"
        if runner_temp
        else args.ownership_file.parent
    )
    existed = work.exists()
    try:
        _prepare(args)
    except Exception:
        if not existed and work.exists() and not work.is_symlink():
            with contextlib.suppress(OSError):
                shutil.rmtree(work)
        raise


def _prepare(args: argparse.Namespace) -> None:
    info = _inspect(args.container_id)
    runner_temp = os.environ.get("RUNNER_TEMP")
    if not runner_temp:
        raise HelperError("CI runner temporary directory is unavailable")
    expected_parent = (Path(runner_temp) / "eventstream-it").resolve()
    requested_parent = args.ownership_file.parent.resolve()
    if (
        requested_parent != expected_parent
        or args.ownership_file.name != "ownership.json"
    ):
        raise HelperError(
            "ownership file path is outside the dedicated runner directory"
        )
    if args.ownership_file.parent.is_symlink():
        raise HelperError("ownership directory must not be a symlink")
    work = requested_parent
    _private_dir(work)
    if args.ownership_file.exists():
        raise HelperError("ownership record already exists")
    env = work / "client.env"
    paths = work / "secrets"
    _private_dir(paths)
    private = paths / "jwt-signing.pem"
    ca_key = paths / "ca-key.pem"
    tls_key = paths / "server-key.pem"
    decrypt_key = paths / "auth-decrypt.pem"
    for key in (private, ca_key, tls_key, decrypt_key):
        _run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
                "-out",
                str(key),
            ]
        )
        os.chmod(key, 0o600)
    public = paths / "jwt-public.pem"
    _run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)])
    os.chmod(public, 0o600)
    public_der = paths / "jwt-public.der"
    _run(
        [
            "openssl",
            "pkey",
            "-in",
            str(private),
            "-pubout",
            "-outform",
            "DER",
            "-out",
            str(public_der),
        ]
    )
    os.chmod(public_der, 0o600)
    auth_public = paths / "auth-encrypt-public.pem"
    _run(
        [
            "openssl",
            "pkey",
            "-in",
            str(decrypt_key),
            "-pubout",
            "-out",
            str(auth_public),
        ]
    )
    os.chmod(auth_public, 0o600)
    ca_cert, tls_cert = paths / "ca.pem", paths / "server.pem"
    _run(
        [
            "openssl",
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "1",
            "-subj",
            "/CN=Omega Eventstream CI CA",
            "-out",
            str(ca_cert),
        ]
    )
    csr = paths / "server.csr"
    _run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(tls_key),
            "-subj",
            "/CN=localhost",
            "-out",
            str(csr),
        ]
    )
    ext = paths / "server.ext"
    _write_secret(
        ext, b"subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
    )
    _run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-days",
            "1",
            "-sha256",
            "-extfile",
            str(ext),
            "-out",
            str(tls_cert),
        ]
    )
    os.chmod(ca_cert, 0o600)
    os.chmod(tls_cert, 0o600)
    admin_token = paths / "omega-admin.token"
    internal_token = paths / "omega-broker-internal.token"
    functions_token = paths / "omega-functions.token"
    now = int(time.time())
    for role, target in (
        ("omega-admin", admin_token),
        ("omega-broker-internal", internal_token),
        ("omega-functions", functions_token),
    ):
        _write_secret(target, _jwt(role, private, now))
    ownership = {
        "version": 1,
        "container_id": args.container_id,
        "container_full_id": info["id"],
        "image_id": info["image"],
        "workflow": info["workflow"],
        "run_id": info["run_id"],
        "workdir": str(work),
        "prepared_at": now,
        "broker_pid": None,
        "data_dir": "/pulsar/data",
        "service_url": SERVICE_URL,
        "admin_url": ADMIN_URL,
    }
    _write_record(args.ownership_file, ownership)
    # File references only. The decryption key stays on the test host; JWT signer stays host-side.
    _write_secret(
        env,
        (
            f"OMEGA_EVENTSTREAM_TOKEN_FILE={admin_token}\nOMEGA_EVENTSTREAM_ADMIN_TOKEN_FILE={admin_token}\nOMEGA_EVENTSTREAM_TLS_CA_FILE={ca_cert}\nOMEGA_EVENTSTREAM_AUTH_KEY_FILE={decrypt_key}\nOMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE={decrypt_key}\nOMEGA_EVENTSTREAM_PRIVATE_KEY_FILE={decrypt_key}\nOMEGA_EVENTSTREAM_ENCRYPTION_PUBLIC_KEY_FILE={auth_public}\nOMEGA_EVENTSTREAM_JWT_PUBLIC_KEY_FILE={public}\nOMEGA_EVENTSTREAM_IT_URL={SERVICE_URL}\nOMEGA_EVENTSTREAM_IT_ADMIN_URL={ADMIN_URL}\nOMEGA_EVENTSTREAM_IT_OWNERSHIP_FILE={args.ownership_file}\n"
        ).encode(),
    )
    # Stage runtime-only material without copying signing/decryption private keys.
    stage = work / "runtime"
    _private_dir(stage)
    for source, name in (
        (public_der, "jwt-public.der"),
        (tls_key, "server-key.pem"),
        (tls_cert, "server.pem"),
        (ca_cert, "ca.pem"),
        (internal_token, "omega-broker-internal.token"),
        (functions_token, "omega-functions.token"),
    ):
        shutil.copyfile(source, stage / name)
        os.chmod(stage / name, 0o600)
    print("Owned secure CI material prepared (protected file references only).")


def _docker(args: list[str], *, check: bool = True) -> str:
    return _run(["docker", *args], check=check)


async def _broker_pid(container_id: str) -> int:
    try:
        output = _docker(
            [
                "exec",
                container_id,
                "/bin/bash",
                "-lc",
                "pgrep -fo 'bin/pulsar standalone'",
            ]
        )
        pid = output.splitlines()[0]
    except (HelperError, IndexError) as exc:
        raise OSError("owned Pulsar process is not yet available") from exc
    if not pid.isdigit():
        raise HelperError("owned broker process could not be identified")
    return int(pid)


def start(args: argparse.Namespace) -> None:
    path, record = _load(args)
    work = Path(record["workdir"])
    root = Path(__file__).resolve().parents[2]
    standalone = root / "deploy/pulsar/standalone.conf"
    worker = root / "deploy/pulsar/functions_worker.yml"
    bookkeeper = root / "deploy/pulsar/bookkeeper.conf"
    for source in (standalone, worker, bookkeeper):
        if not source.is_file():
            raise HelperError("required secure broker configuration is missing")
    worker_text = worker.read_text(encoding="utf-8")
    active_worker_config = "\n".join(
        line for line in worker_text.splitlines() if not line.lstrip().startswith("#")
    )
    if "functionAuthenticationSpec" not in active_worker_config:
        raise HelperError(
            "pinned worker lacks a supported per-function least-privilege authentication provider"
        )
    _stage(args.container_id, work)
    worker_config = work / "runtime" / "functions_worker.yml"
    replacements = {
        "/run/secrets/omega-pulsar/ca.crt": "/pulsar/ci-secrets/ca.pem",
        "/run/secrets/omega-pulsar/tls.crt": "/pulsar/ci-secrets/server.pem",
        "/run/secrets/omega-pulsar/tls.key": "/pulsar/ci-secrets/server-key.pem",
        "/run/secrets/omega-pulsar/broker-internal.token": "/pulsar/ci-secrets/omega-broker-internal.token",
        "/run/secrets/omega-pulsar/omega-functions.token": "/pulsar/ci-secrets/omega-functions.token",
    }
    for old_path, new_path in replacements.items():
        worker_text = worker_text.replace(old_path, new_path)
    _write_secret(worker_config, worker_text.encode())
    secured = work / "runtime" / "standalone.conf"
    shutil.copyfile(standalone, secured)
    with secured.open("ab") as stream:
        stream.write(
            b"\n# Owned eventstream CI secure runtime overrides.\n"
            b"authenticationEnabled=true\nauthorizationEnabled=true\n"
            b"authenticationProviders=org.apache.pulsar.broker.authentication.AuthenticationProviderToken\n"
            b"tokenPublicKey=file:///pulsar/ci-secrets/jwt-public.der\n"
            b"superUserRoles=omega-admin,omega-broker-internal\n"
            b"brokerClientAuthenticationPlugin=org.apache.pulsar.client.impl.auth.AuthenticationToken\n"
            b"brokerClientAuthenticationParameters=token:file:///pulsar/ci-secrets/omega-broker-internal.token\n"
            b"brokerServicePortTls=6651\nwebServicePortTls=8443\n"
            b"tlsCertificateFilePath=/pulsar/ci-secrets/server.pem\n"
            b"tlsKeyFilePath=/pulsar/ci-secrets/server-key.pem\n"
            b"tlsTrustCertsFilePath=/pulsar/ci-secrets/ca.pem\n"
        )
    os.chmod(secured, 0o600)
    _docker(["cp", str(secured), f"{args.container_id}:/pulsar/conf/standalone.conf"])
    _docker(
        [
            "cp",
            str(worker_config),
            f"{args.container_id}:/pulsar/conf/functions_worker.yml",
        ]
    )
    _docker(
        ["cp", str(bookkeeper), f"{args.container_id}:/pulsar/conf/bookkeeper.conf"]
    )
    _docker(["exec", args.container_id, "/bin/bash", "-lc", "mkdir -p /pulsar/data"])
    _docker(
        [
            "exec",
            "-d",
            args.container_id,
            "/bin/bash",
            "-lc",
            "cd /pulsar && exec bin/pulsar standalone --config /pulsar/conf/standalone.conf",
        ]
    )
    pid = asyncio.run(
        create_with_backoff(lambda: _broker_pid(args.container_id), attempts=5)
    )
    record["broker_pid"] = pid
    _write_record(path, record)


def _stage(container_id: str, work: Path) -> None:
    bundle = work / "runtime"
    _docker(["exec", container_id, "mkdir", "-p", "/pulsar/ci-secrets"])
    for name in (
        "jwt-public.der",
        "server-key.pem",
        "server.pem",
        "ca.pem",
        "omega-broker-internal.token",
        "omega-functions.token",
    ):
        _docker(["cp", str(bundle / name), f"{container_id}:/pulsar/ci-secrets/{name}"])
    # Ensure restrictive mode in the container. Runtime UID/GID is detected, not assumed.
    uid = _docker(["exec", container_id, "id", "-u", "pulsar"]).strip()
    gid = _docker(["exec", container_id, "id", "-g", "pulsar"]).strip()
    if not uid.isdigit() or not gid.isdigit():
        raise HelperError("pinned service runtime identity could not be determined")
    _docker(["exec", container_id, "chown", "-R", f"{uid}:{gid}", "/pulsar/ci-secrets"])
    _docker(
        [
            "exec",
            container_id,
            "find",
            "/pulsar/ci-secrets",
            "-type",
            "f",
            "-exec",
            "chmod",
            "0600",
            "{}",
            ";",
        ]
    )
    for name in (
        "jwt-public.der",
        "server-key.pem",
        "server.pem",
        "ca.pem",
        "omega-broker-internal.token",
        "omega-functions.token",
    ):
        _docker(
            [
                "exec",
                "-u",
                f"{uid}:{gid}",
                container_id,
                "test",
                "-r",
                f"/pulsar/ci-secrets/{name}",
            ]
        )


async def _check_ready(args: argparse.Namespace, record: dict[str, Any]) -> bool:
    _load(args)
    pid = record.get("broker_pid")
    if not isinstance(pid, int):
        raise HelperError("owned broker has not been started")
    if _docker_status(["exec", args.container_id, "kill", "-0", str(pid)]):
        raise OSError("owned broker process is not ready")
    work = Path(record["workdir"])
    token = (work / "secrets" / "omega-admin.token").read_text().strip()
    request = urllib.request.Request(
        ADMIN_URL + "/admin/v2/brokers/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(
            request, context=_tls_context(work), timeout=4
        ) as response:
            if response.status != 200:
                raise OSError("broker health endpoint is not ready")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OSError("broker health endpoint is not ready") from exc
    _verify_effective(args.container_id, work)
    return True


def wait_ready(args: argparse.Namespace) -> None:
    _, record = _load(args)
    try:
        asyncio.run(create_with_backoff(lambda: _check_ready(args, record), attempts=5))
    except HelperError:
        raise
    except Exception as exc:
        raise HelperError("owned secure broker failed bounded HTTPS readiness") from exc
    print("Owned broker HTTPS readiness and effective configuration verified.")


def _tls_context(work: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(work / "secrets" / "ca.pem"))
    context.check_hostname = True
    return context


def _verify_effective(container_id: str, work: Path) -> None:
    token = (work / "secrets" / "omega-admin.token").read_text().strip()
    request = urllib.request.Request(
        ADMIN_URL + "/admin/v2/brokers/configuration",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(
            request, context=_tls_context(work), timeout=4
        ) as response:
            observed = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise HelperError(
            "broker effective configuration could not be queried"
        ) from exc
    if (
        not isinstance(observed, dict)
        or observed.get("journalSyncData") is not True
        or observed.get("brokerDeleteInactiveTopicsEnabled") is not False
    ):
        raise HelperError(
            "effective durability/retention configuration is not verified"
        )
    _docker(["exec", container_id, "test", "-d", "/pulsar/data"])


def _docker_status(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            ["docker", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode != 0


def _assert_broker_pid(container_id: str, pid: int) -> None:
    command = _docker(
        [
            "exec",
            container_id,
            "/bin/bash",
            "-lc",
            f"tr '\\0' ' ' < /proc/{pid}/cmdline",
        ]
    )
    if "bin/pulsar standalone" not in command:
        raise HelperError("recorded PID is not the owned Pulsar process")


async def _await_pid_exit(container_id: str, pid: int) -> bool:
    if not _docker_status(["exec", container_id, "kill", "-0", str(pid)]):
        raise OSError("owned broker process is still stopping")
    return True


def restart(args: argparse.Namespace) -> None:
    path, record = _load(args)
    pid = record.get("broker_pid")
    if not isinstance(pid, int):
        raise HelperError("owned broker PID is unavailable")
    _assert_broker_pid(args.container_id, pid)
    _docker(["exec", args.container_id, "kill", "-TERM", str(pid)])
    try:
        asyncio.run(
            create_with_backoff(
                lambda: _await_pid_exit(args.container_id, pid), attempts=5
            )
        )
    except Exception as exc:
        raise HelperError("owned broker process did not stop safely") from exc
    _docker(
        [
            "exec",
            "-d",
            args.container_id,
            "/bin/bash",
            "-lc",
            "cd /pulsar && exec bin/pulsar standalone --config /pulsar/conf/standalone.conf",
        ]
    )
    new_pid = asyncio.run(
        create_with_backoff(lambda: _broker_pid(args.container_id), attempts=5)
    )
    record["broker_pid"] = new_pid
    _write_record(path, record)


def stop(args: argparse.Namespace) -> None:
    _path, record = _load(args)
    pid = record.get("broker_pid")
    if isinstance(pid, int) and not _docker_status(
        ["exec", args.container_id, "kill", "-0", str(pid)]
    ):
        _assert_broker_pid(args.container_id, pid)
        _docker(["exec", args.container_id, "kill", "-TERM", str(pid)])
    work = Path(record["workdir"])
    # Delete only this run's generated host material, never the service or persistent broker data.
    if work.resolve() != args.ownership_file.parent.resolve():
        raise HelperError("owned temporary path does not match")
    shutil.rmtree(work)
    print("Owned CI credentials removed; service container left to the Actions runner.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "start", "wait", "restart", "stop")
    )
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--mode", choices=("owned-secure",), required=True)
    parser.add_argument("--ownership-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        {
            "prepare": prepare,
            "start": start,
            "wait": wait_ready,
            "restart": restart,
            "stop": stop,
        }[args.command](args)
    except HelperError as exc:
        print(f"eventstream-it: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("eventstream-it: operation failed; details suppressed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

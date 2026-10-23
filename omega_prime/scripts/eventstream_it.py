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


_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]+-----.*?(?:-----END [A-Z0-9 ]+-----|$)", re.DOTALL
)
_JWT_LIKE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_BEARER = re.compile(r"(?i)(bearer\s+|token[:=]\s*)\S+")
_STDERR_LIMIT = 2000


def _redact(text: str) -> str:
    """Mask credential-shaped material before a diagnostic reaches CI logs.

    The helper only passes file *paths* on argv, but stderr from docker/openssl
    is still scrubbed of PEM blocks, compact JWTs and bearer/token values.
    """
    text = _PEM_BLOCK.sub("[redacted pem]", text)
    text = _JWT_LIKE.sub("[redacted jwt]", text)
    return _BEARER.sub(lambda m: f"{m.group(1)}[redacted]", text)


def _describe_failure(args: list[str], returncode: int, stderr: str | None) -> str:
    argv = " ".join(_redact(item) for item in args)
    detail = _redact((stderr or "").strip())
    if len(detail) > _STDERR_LIMIT:
        detail = "..." + detail[-_STDERR_LIMIT:]
    message = f"command failed: {Path(args[0]).name} (exit {returncode}): argv=[{argv}]"
    if detail:
        message += f" stderr=[{detail}]"
    return message


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
        argv = " ".join(_redact(item) for item in args)
        raise HelperError(
            f"command failed: {Path(args[0]).name} ({type(exc).__name__}): argv=[{argv}]"
        ) from exc
    if check and result.returncode:
        # argv carries container ids and file paths only (never credential
        # values); stderr is redacted and bounded so failures stay diagnosable.
        raise HelperError(_describe_failure(args, result.returncode, result.stderr))
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
    # GHA service containers often report Config.Image as the bare digest
    # (sha256:…) with empty RepoDigests; accept any of the pinned identities.
    image_ref = config.get("Image")
    digest_matched = (
        image_ref in (PINNED_IMAGE, PINNED_DIGEST)
        or image_id == PINNED_DIGEST
        or any(
            isinstance(item, str) and item.endswith(PINNED_DIGEST)
            for item in repo_digests
        )
    )
    if not digest_matched:
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
            # Python >= 3.13 verifies with VERIFY_X509_STRICT: a CA must carry
            # critical basicConstraints and keyUsage(keyCertSign).
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-addext",
            "subjectKeyIdentifier=hash",
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
        ext,
        (
            b"basicConstraints=critical,CA:FALSE\n"
            b"keyUsage=critical,digitalSignature,keyEncipherment\n"
            b"extendedKeyUsage=serverAuth\n"
            b"subjectAltName=DNS:localhost,IP:127.0.0.1\n"
            b"subjectKeyIdentifier=hash\n"
            b"authorityKeyIdentifier=keyid,issuer\n"
        ),
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
    # ENCRYPTION_KEY_FILE is the *public* key the producer's CryptoKeyReader
    # encrypts with; PRIVATE_KEY_FILE is the consumer-side decryption key.
    _write_secret(
        env,
        (
            f"OMEGA_EVENTSTREAM_TOKEN_FILE={admin_token}\nOMEGA_EVENTSTREAM_ADMIN_TOKEN_FILE={admin_token}\nOMEGA_EVENTSTREAM_TLS_CA_FILE={ca_cert}\nOMEGA_EVENTSTREAM_AUTH_KEY_FILE={decrypt_key}\nOMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE={auth_public}\nOMEGA_EVENTSTREAM_PRIVATE_KEY_FILE={decrypt_key}\nOMEGA_EVENTSTREAM_ENCRYPTION_PUBLIC_KEY_FILE={auth_public}\nOMEGA_EVENTSTREAM_JWT_PUBLIC_KEY_FILE={public}\nOMEGA_EVENTSTREAM_IT_URL={SERVICE_URL}\nOMEGA_EVENTSTREAM_IT_ADMIN_URL={ADMIN_URL}\nOMEGA_EVENTSTREAM_IT_OWNERSHIP_FILE={args.ownership_file}\n"
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


# ``bin/pulsar`` is a shell launcher that ``exec``s the JVM in the same PID,
# so the durable identity of the owned broker is the Java main class plus its
# owned config path, not the launcher's argv (which disappears after exec).
_BROKER_MAIN = "org.apache.pulsar.PulsarStandaloneStarter"
_BROKER_ARGV = (
    f"{_BROKER_MAIN} --no-functions-worker --config /pulsar/conf/standalone.conf"
)
_BROKER_PATTERN = re.escape(_BROKER_ARGV)
_BROKER_OUTPUT = "/pulsar/logs/omega-ci-standalone.out"
_BROKER_LAUNCH = (
    "cd /pulsar && exec bin/pulsar standalone --no-functions-worker "
    f"--config /pulsar/conf/standalone.conf >>{_BROKER_OUTPUT} 2>&1"
)


async def _broker_pid(container_id: str) -> int:
    try:
        output = _docker(["exec", container_id, "pgrep", "-fo", _BROKER_PATTERN])
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
    # Broker IT starts standalone with --no-functions-worker. The worker pin is
    # still staged for later Function deploys, but the process under test is the
    # broker only — so do not require worker-only auth keys here. When a future
    # lane enables the functions worker, require functionAuthProviderClassName
    # on a non-comment line (keyword-in-comment is not a real gate).
    functions_worker_enabled = False
    active_worker_config = "\n".join(
        line for line in worker_text.splitlines() if not line.lstrip().startswith("#")
    )
    if (
        functions_worker_enabled
        and "functionAuthProviderClassName:" not in active_worker_config
    ):
        raise HelperError(
            "functions worker enabled but functionAuthProviderClassName "
            "is missing from active (non-comment) functions_worker.yml"
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
    # The repo files are sparse Omega Prime overrides, not complete configs.
    # They are merged key-by-key into the pinned image's stock file: the
    # embedded bookie loads standalone.conf through Commons Configuration,
    # which turns a repeated key into a list (journalSyncData=[false, true]
    # reads as false), so appending duplicates is not a safe override.
    _layer_config(
        args.container_id,
        "standalone.conf",
        [standalone.read_text(encoding="utf-8"), _CI_STANDALONE_OVERRIDES],
        secured,
    )
    layered_bookkeeper = work / "runtime" / "bookkeeper.conf"
    _layer_config(
        args.container_id,
        "bookkeeper.conf",
        [bookkeeper.read_text(encoding="utf-8")],
        layered_bookkeeper,
    )
    os.chmod(secured, 0o600)
    _install_configs(
        args.container_id,
        {
            "standalone.conf": secured,
            "functions_worker.yml": worker_config,
            "bookkeeper.conf": layered_bookkeeper,
        },
    )
    _docker(["exec", args.container_id, "/bin/bash", "-lc", "mkdir -p /pulsar/data"])
    _docker(
        [
            "exec",
            "-d",
            args.container_id,
            "/bin/bash",
            "-lc",
            _BROKER_LAUNCH,
        ]
    )
    pid = asyncio.run(
        create_with_backoff(lambda: _broker_pid(args.container_id), attempts=5)
    )
    record["broker_pid"] = pid
    _write_record(path, record)


_RUNTIME_USER = "pulsar"
_STAGED_SECRETS = (
    "jwt-public.der",
    "server-key.pem",
    "server.pem",
    "ca.pem",
    "omega-broker-internal.token",
    "omega-functions.token",
)
_STAGED_CONFIGS = ("standalone.conf", "functions_worker.yml", "bookkeeper.conf")


def _root_exec(container_id: str, *command: str) -> str:
    """Run a one-shot setup command as root inside the owned service container.

    The pinned image runs as the unprivileged ``pulsar`` user (uid 10000) and
    ``/pulsar`` itself is root-owned 0755, so creating ``/pulsar/ci-secrets``
    and re-owning ``docker cp`` output (which lands as root:root) both need
    root. Only staging uses this; the broker itself still runs as ``pulsar``.
    """
    return _docker(["exec", "-u", "0", container_id, *command])


def _runtime_identity(container_id: str) -> tuple[str, str]:
    uid = _docker(["exec", container_id, "id", "-u", _RUNTIME_USER]).strip()
    gid = _docker(["exec", container_id, "id", "-g", _RUNTIME_USER]).strip()
    if not uid.isdigit() or not gid.isdigit():
        raise HelperError("pinned service runtime identity could not be determined")
    return uid, gid


def _assert_runtime_readable(
    container_id: str, uid: str, gid: str, paths: list[str]
) -> None:
    for target in paths:
        _docker(["exec", "-u", f"{uid}:{gid}", container_id, "test", "-r", target])


def _stage(container_id: str, work: Path) -> None:
    bundle = work / "runtime"
    _root_exec(container_id, "mkdir", "-p", "/pulsar/ci-secrets")
    for name in _STAGED_SECRETS:
        _docker(["cp", str(bundle / name), f"{container_id}:/pulsar/ci-secrets/{name}"])
    # Runtime UID/GID is detected, not assumed; ownership is handed to it as root.
    uid, gid = _runtime_identity(container_id)
    _root_exec(container_id, "chown", "-R", f"{uid}:{gid}", "/pulsar/ci-secrets")
    _root_exec(container_id, "chmod", "0700", "/pulsar/ci-secrets")
    _root_exec(
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
    )
    _assert_runtime_readable(
        container_id,
        uid,
        gid,
        [f"/pulsar/ci-secrets/{name}" for name in _STAGED_SECRETS],
    )


_CI_STANDALONE_OVERRIDES = """
# Owned eventstream CI secure runtime overrides.
advertisedAddress=127.0.0.1
authenticationEnabled=true
authorizationEnabled=true
authenticationProviders=org.apache.pulsar.broker.authentication.AuthenticationProviderToken
tokenPublicKey=file:///pulsar/ci-secrets/jwt-public.der
superUserRoles=omega-admin,omega-broker-internal
brokerClientAuthenticationPlugin=org.apache.pulsar.client.impl.auth.AuthenticationToken
brokerClientAuthenticationParameters=file:///pulsar/ci-secrets/omega-broker-internal.token
brokerServicePortTls=6651
webServicePortTls=8443
tlsCertificateFilePath=/pulsar/ci-secrets/server.pem
tlsKeyFilePath=/pulsar/ci-secrets/server-key.pem
tlsTrustCertsFilePath=/pulsar/ci-secrets/ca.pem
"""
_PROPERTY = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*[=:]")


def _merge_properties(stock: str, overrides: list[str]) -> str:
    """Merge ``key=value`` overrides into ``stock`` so every key occurs once.

    Existing assignments are replaced in place (keeping the stock comments and
    ordering); keys the stock file lacks are appended. Later override sources
    win over earlier ones.
    """
    wanted: dict[str, str] = {}
    for text in overrides:
        for line in text.splitlines():
            if line.lstrip().startswith(("#", "!")):
                continue
            match = _PROPERTY.match(line)
            if match:
                wanted[match.group(1)] = line.strip()
    merged: list[str] = []
    placed: set[str] = set()
    for line in stock.splitlines():
        match = None if line.lstrip().startswith(("#", "!")) else _PROPERTY.match(line)
        if match and match.group(1) in wanted:
            key = match.group(1)
            if key not in placed:
                merged.append(wanted[key])
                placed.add(key)
            continue
        merged.append(line)
    extra = [wanted[key] for key in wanted if key not in placed]
    if extra:
        merged.extend(["", "# Omega Prime overrides not present in the stock file."])
        merged.extend(extra)
    return "\n".join(merged) + "\n"


def _layer_config(
    container_id: str, name: str, overrides: list[str], target: Path
) -> None:
    """Write ``target`` as the image's stock ``name`` merged with ``overrides``.

    The pristine stock file is preserved once in-container as
    ``<name>.omega-stock`` so a re-run of ``start`` merges over the original,
    never over a previously layered copy.
    """
    stock_path = f"/pulsar/conf/{name}.omega-stock"
    _root_exec(
        container_id,
        "/bin/bash",
        "-c",
        f'test -f "$1" || cp -p "/pulsar/conf/{name}" "$1"',
        "layer-config",
        stock_path,
    )
    stock = target.with_name(f"{name}.omega-stock")
    stock.unlink(missing_ok=True)
    _docker(["cp", f"{container_id}:{stock_path}", str(stock)])
    try:
        stock_text = stock.read_text(encoding="utf-8")
    finally:
        stock.unlink(missing_ok=True)
    target.unlink(missing_ok=True)
    _write_secret(target, _merge_properties(stock_text, overrides).encode())


def _install_configs(container_id: str, sources: dict[str, Path]) -> None:
    """Copy broker configs in and hand them to the runtime user.

    ``docker cp`` creates files as root:root while preserving the source mode
    (0600 for the secured standalone.conf), which the ``pulsar`` user could
    not read; ownership is therefore corrected as root and then verified as
    the runtime identity before the broker starts.
    """
    for name in _STAGED_CONFIGS:
        _docker(["cp", str(sources[name]), f"{container_id}:/pulsar/conf/{name}"])
    uid, gid = _runtime_identity(container_id)
    targets = [f"/pulsar/conf/{name}" for name in _STAGED_CONFIGS]
    _root_exec(container_id, "chown", f"{uid}:{gid}", *targets)
    _root_exec(container_id, "chmod", "0600", *targets)
    _assert_runtime_readable(container_id, uid, gid, targets)


async def _check_ready(args: argparse.Namespace, record: dict[str, Any]) -> bool:
    _load(args)
    pid = record.get("broker_pid")
    if not isinstance(pid, int):
        raise HelperError("owned broker has not been started")
    if _docker_status(["exec", args.container_id, "kill", "-0", str(pid)]):
        # An exited broker never becomes ready: fail now with its last error.
        raise HelperError(
            "owned broker exited before readiness: "
            + _broker_last_error(args.container_id)
        )
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


def _broker_last_error(container_id: str) -> str:
    lines = _docker(
        [
            "exec",
            container_id,
            "/bin/bash",
            "-c",
            "grep -aE 'ERROR|Exception' \"$1\" | tail -n 3 || true",
            "broker-error",
            _BROKER_OUTPUT,
        ],
        check=False,
    )
    detail = _redact(lines.strip())[-_STDERR_LIMIT:]
    return detail or "no broker output captured"


def _readiness_delay(low: float, high: float) -> float:
    # Use the top of each decorrelated-jitter window (3s, 9s, 27s, 81s): a
    # bounded ~2 minute budget that covers a cold JVM + embedded bookie on a
    # shared hosted runner, instead of a random budget that can be ~4s.
    del low
    return high


def wait_ready(args: argparse.Namespace) -> None:
    _, record = _load(args)
    try:
        asyncio.run(
            create_with_backoff(
                lambda: _check_ready(args, record), attempts=5, rng=_readiness_delay
            )
        )
    except HelperError:
        raise
    except Exception as exc:
        cause = exc.__cause__ or exc
        raise HelperError(
            "owned secure broker failed bounded HTTPS readiness: "
            + _redact(f"{type(cause).__name__}: {cause}")[:_STDERR_LIMIT]
        ) from exc
    print("Owned broker HTTPS readiness and effective configuration verified.")


def _tls_context(work: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(work / "secrets" / "ca.pem"))
    context.check_hostname = True
    return context


_EXPECTED_BROKER_RUNTIME = {
    "brokerDeleteInactiveTopicsEnabled": "false",
    "allowAutoTopicCreation": "false",
    "brokerDeduplicationEnabled": "true",
    "authenticationEnabled": "true",
    "authorizationEnabled": "true",
}


def _verify_effective(container_id: str, work: Path) -> None:
    token = (work / "secrets" / "omega-admin.token").read_text().strip()
    # ``/brokers/configuration`` only lists dynamic-config *names*; the loaded
    # values live under ``/brokers/configuration/runtime`` (string-valued).
    request = urllib.request.Request(
        ADMIN_URL + "/admin/v2/brokers/configuration/runtime",
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
    if not isinstance(observed, dict):
        raise HelperError("broker effective configuration has an unexpected shape")
    drift = sorted(
        key
        for key, value in _EXPECTED_BROKER_RUNTIME.items()
        if str(observed.get(key)).lower() != value
    )
    if drift:
        raise HelperError(
            "effective retention/security configuration is not verified: "
            + ", ".join(drift)
        )
    _verify_bookie_fsync(container_id)
    _docker(["exec", container_id, "test", "-d", "/pulsar/data"])


def _verify_bookie_fsync(container_id: str) -> None:
    """Prove the embedded bookie *loaded* journalSyncData=true.

    journalSyncData is a BookKeeper setting, absent from the broker REST
    config. The pinned standalone loader builds the embedded bookie from
    standalone.conf and logs the loaded map on "Starting Bookie server"; the
    latest such line is the effective value (a duplicated key would show as a
    list and is rejected). conf/bookkeeper.conf must also carry exactly one
    active ``journalSyncData=true``.
    """
    loaded = _docker(
        [
            "exec",
            container_id,
            "/bin/bash",
            "-c",
            "grep -a 'Starting Bookie server' \"$1\" | tail -n 1 "
            "| grep -o 'journalSyncData=[^,}]*' || true",
            "verify-bookie",
            _BROKER_OUTPUT,
        ]
    ).splitlines()
    if loaded != ["journalSyncData=true"]:
        raise HelperError(
            "embedded bookie effective journalSyncData is not true: "
            + (", ".join(loaded) or "no bookie startup evidence")
        )
    declared = _docker(
        [
            "exec",
            container_id,
            "grep",
            "-E",
            r"^[[:space:]]*journalSyncData[[:space:]]*[=:]",
            "/pulsar/conf/bookkeeper.conf",
        ]
    ).splitlines()
    if [line.strip() for line in declared] != ["journalSyncData=true"]:
        raise HelperError(
            "conf/bookkeeper.conf does not declare a single journalSyncData=true"
        )


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
    if _BROKER_ARGV not in command:
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
            _BROKER_LAUNCH,
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

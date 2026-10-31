"""Validated, broker-independent event-stream settings and secret file access."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from omega_prime.credentials.redact import redact_value

from .errors import ConfigurationError

_SECRET_ENV_NAME = re.compile(
    r"(?:TOKEN|SECRET|PRIVATE_KEY|ENCRYPTION_KEY)$", re.IGNORECASE
)
_LOCAL_BROKER = "pulsar://127.0.0.1:26650"
_LOCAL_ADMIN = "http://127.0.0.1:28080"


def _safe_error(message: str) -> ConfigurationError:
    """Create a configuration exception after applying the shared redactor."""
    redacted = redact_value(message)
    return ConfigurationError(str(redacted))


def _validate_url(value: str, *, kind: str) -> str:
    if not isinstance(value, str) or not value:
        raise _safe_error(f"{kind} URL must be nonempty")
    try:
        parsed = urlsplit(value)
        valid = (
            bool(parsed.hostname)
            and parsed.port != 0
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
        parsed = None
    if not valid or parsed is None:
        raise _safe_error(f"invalid {kind} URL")
    if kind == "broker" and parsed.scheme not in {"pulsar", "pulsar+ssl"}:
        raise _safe_error("invalid broker URL scheme")
    if kind == "admin" and parsed.scheme not in {"http", "https"}:
        raise _safe_error("invalid admin URL scheme")
    return value.rstrip("/")


def _positive_int(value: str | None, default: str, name: str) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        raise _safe_error(f"{name} must be a positive integer") from None
    if parsed <= 0:
        raise _safe_error(f"{name} must be a positive integer")
    return parsed


def _validate_path(
    path: str | os.PathLike[str] | None, *, required: bool = False
) -> Path | None:
    if path is None or str(path) == "":
        if required:
            raise _safe_error("required configuration file path is missing")
        return None
    try:
        result = Path(path).expanduser()
        info = result.stat()
    except (OSError, TypeError, ValueError):
        raise _safe_error("configuration file path is unavailable") from None
    if not stat.S_ISREG(info.st_mode):
        raise _safe_error("configuration file path must name a regular file")
    return result


@dataclass(frozen=True, slots=True, repr=False)
class EventStreamConfig:
    """Immutable event-stream settings; secret contents are read only on request."""

    broker_url: str = _LOCAL_BROKER
    admin_url: str = _LOCAL_ADMIN
    tls_ca_file: Path | None = None
    token_file: Path | None = field(default=None, repr=False)
    encryption_key_file: Path | None = field(default=None, repr=False)
    private_key_file: Path | None = field(default=None, repr=False)
    outbox_max_bytes: int = 1_073_741_824
    max_concurrency: int = 16

    def __post_init__(self) -> None:
        broker_url = _validate_url(self.broker_url, kind="broker")
        admin_url = _validate_url(self.admin_url, kind="admin")
        local_broker = broker_url == _LOCAL_BROKER
        local_admin = admin_url == _LOCAL_ADMIN
        ca_path = _validate_path(self.tls_ca_file)
        token_path = _validate_path(self.token_file)
        encryption_path = _validate_path(self.encryption_key_file)
        private_path = _validate_path(self.private_key_file)
        remote_broker = not local_broker
        remote_admin = not local_admin
        if remote_broker and broker_url.startswith("pulsar://"):
            raise _safe_error("remote broker requires TLS")
        if remote_admin and admin_url.startswith("http://"):
            raise _safe_error("remote admin endpoint requires TLS")
        if (remote_broker or remote_admin) and ca_path is None:
            raise _safe_error("remote endpoints require a TLS CA file")
        if (remote_broker or remote_admin) and token_path is None:
            raise _safe_error("remote endpoints require a credential file")
        if (
            isinstance(self.outbox_max_bytes, bool)
            or not isinstance(self.outbox_max_bytes, int)
            or self.outbox_max_bytes <= 0
        ):
            raise _safe_error("outbox_max_bytes must be a positive integer")
        if (
            isinstance(self.max_concurrency, bool)
            or not isinstance(self.max_concurrency, int)
            or self.max_concurrency <= 0
        ):
            raise _safe_error("max_concurrency must be a positive integer")
        object.__setattr__(self, "broker_url", broker_url)
        object.__setattr__(self, "admin_url", admin_url)
        object.__setattr__(self, "tls_ca_file", ca_path)
        object.__setattr__(self, "token_file", token_path)
        object.__setattr__(self, "encryption_key_file", encryption_path)
        object.__setattr__(self, "private_key_file", private_path)

    def __repr__(self) -> str:
        return (
            "EventStreamConfig("
            f"broker_url={self.broker_url!r}, admin_url={self.admin_url!r}, "
            f"tls_ca_file={'<configured>' if self.tls_ca_file else None!r}, "
            f"credentials={'<configured>' if any((self.token_file, self.encryption_key_file, self.private_key_file)) else None!r}, "
            f"outbox_max_bytes={self.outbox_max_bytes!r}, max_concurrency={self.max_concurrency!r})"
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        argv: tuple[str, ...] | list[str] = (),
    ) -> EventStreamConfig:
        """Load settings while rejecting secret values outside *_FILE variables."""
        values = os.environ if env is None else env
        for name in values:
            key = name.upper()
            if (
                key.startswith("OMEGA_EVENTSTREAM_")
                and _SECRET_ENV_NAME.search(key)
                and not key.endswith("_FILE")
            ):
                raise _safe_error(f"secret environment variable {name} must use *_FILE")
        args = tuple(argv)
        for index, item in enumerate(args):
            flag, separator, _value = item.partition("=")
            normalized_flag = flag.lstrip("-").replace("-", "_").upper()
            if (
                _SECRET_ENV_NAME.search(normalized_flag)
                and not normalized_flag.endswith("_FILE")
                and (separator or index + 1 < len(args))
            ):
                raise _safe_error(
                    "credential values are not accepted in command-line arguments"
                )

        def get(name: str, default: str | None = None) -> str | None:
            return values.get(f"OMEGA_EVENTSTREAM_{name}", default)

        def get_path(name: str) -> Path | None:
            value = get(name)
            return Path(value) if value else None

        return cls(
            broker_url=get("URL", _LOCAL_BROKER) or _LOCAL_BROKER,
            admin_url=get("ADMIN_URL", _LOCAL_ADMIN) or _LOCAL_ADMIN,
            tls_ca_file=get_path("TLS_CA_FILE"),
            token_file=get_path("TOKEN_FILE"),
            encryption_key_file=get_path("ENCRYPTION_KEY_FILE"),
            private_key_file=get_path("PRIVATE_KEY_FILE"),
            outbox_max_bytes=_positive_int(
                get("OUTBOX_MAX_BYTES"), "1073741824", "outbox_max_bytes"
            ),
            max_concurrency=_positive_int(
                get("MAX_CONCURRENCY"), "16", "max_concurrency"
            ),
        )

    def read_secret(self, name: str) -> str:
        """Read a configured secret securely; each call reopens and revalidates it."""
        attribute = {
            "token": self.token_file,
            "encryption_key": self.encryption_key_file,
            "private_key": self.private_key_file,
        }.get(name)
        if attribute is None:
            raise _safe_error("requested credential file is not configured")
        descriptor = -1
        try:
            descriptor = os.open(
                attribute,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise _safe_error(
                    "credential file must be a regular file with mode 0600"
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8").strip()
        except ConfigurationError:
            raise
        except (OSError, UnicodeError):
            raise _safe_error("credential file could not be read safely") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def redact(self, value: object, *, known_secrets: tuple[str, ...] = ()) -> object:
        """Return a recursively redacted copy using the credentials redactor."""
        return redact_value(value, extra=known_secrets)


__all__ = ["EventStreamConfig"]

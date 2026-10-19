"""Credential broker: keys from the environment, only for approved hosts.

The OpenShell credential shape without a control plane: the agent never
handles a key string. :class:`ProviderModel` asks the broker for the key of
one request URL; the broker checks the seat policy's host allowlist first,
then resolves the provider's env vars. Resolved keys are cached for
:meth:`redact` only and are never exposed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from omega_prime.credentials.redact import redact_text
from omega_prime.integrations.binding import secret_value
from omega_prime.providers.base import ProviderError


class CredentialBroker:
    """Resolve provider keys per approved endpoint."""

    def __init__(self, policy: Any, env: Mapping[str, str] | None = None) -> None:
        self._policy = policy
        self._env = env if env is not None else os.environ
        self._resolved: list[str] = []

    def key_for(self, provider: Any, url: str) -> str:
        """Return the key for one request URL, or raise ``ProviderError``."""
        host = _host_of(url)
        if host is None:
            raise ProviderError(f"cannot parse credential host from {url!r}")
        if not self._policy.allows_host(host):
            raise ProviderError(f"no credential for host {host}")
        if not getattr(provider, "requires_key", True):
            return ""
        names = list(getattr(provider, "env_vars", ()) or ())
        for name in names:
            try:
                value = secret_value(self._env, name)
            except ValueError as exc:
                raise ProviderError(str(exc)) from None
            if value:
                if value not in self._resolved:
                    self._resolved.append(value)
                return value
        wanted = " or ".join(names) if names else "a key"
        raise ProviderError(f"no credential for {provider.name} (set {wanted})")

    def redact(self, text: Any) -> Any:
        """Redact resolved keys and secret shapes in ``text``."""
        return redact_text(text, tuple(self._resolved))


def _host_of(url: str) -> str | None:
    if not isinstance(url, str) or url == "":
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


__all__ = ["CredentialBroker"]

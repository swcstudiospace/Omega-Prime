"""Typed errors shared by the event-stream contract and runtime."""

from __future__ import annotations


class EventStreamError(Exception):
    """Base class for event-stream failures."""


class ContractError(EventStreamError, ValueError):
    """An event or payload violates the event-stream contract."""


class EventStreamConfigError(EventStreamError, ValueError):
    """Event-stream configuration is invalid."""


class ConfigurationError(EventStreamConfigError):
    """Compatibility-facing name for configuration validation failures."""


ConfigError = ConfigurationError


class SchemaError(ContractError):
    """A schema reference or schema resource is invalid."""


class RetryableError(EventStreamError):
    """A transient event-stream operation failed."""


class PermanentError(EventStreamError):
    """An event-stream operation cannot succeed by retrying."""


class AuthenticationError(PermanentError):
    """An event-stream authentication operation failed."""


__all__ = [
    "AuthenticationError",
    "ConfigError",
    "ConfigurationError",
    "ContractError",
    "EventStreamConfigError",
    "EventStreamError",
    "PermanentError",
    "RetryableError",
    "SchemaError",
]

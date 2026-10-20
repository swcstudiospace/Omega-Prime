"""Suite-wide setup. PyRIT creates its data dir at import; keep it in tmp.

`pyrit.common.path` mkdirs `user_data_dir("dbdata")` and touches logs.txt on
first import, which breaks read-only sandboxes when it resolves under HOME.
`XDG_DATA_HOME` redirects appdirs on Linux (CI, containers). This must run
before any `pyrit` import, so it lives in conftest and not in a test module.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator

import pytest


def _isolate_pyrit() -> None:
    root = os.environ.setdefault(
        "XDG_DATA_HOME", os.path.join(tempfile.gettempdir(), "omega-prime-pyrit")
    )
    os.makedirs(root, exist_ok=True)


_isolate_pyrit()

# Loggers the host reconfigures for the whole process (`configure_logging`,
# `install_access_log_filter`). A test that starts a host or calls those helpers
# must not change what the next test sees: a leaked `propagate=False` makes pytest
# attach its own capture handlers to the logger and breaks `caplog` assertions.
_PROCESS_LOGGERS = ("omega_prime", "omega_prime.access", "uvicorn.access")


@pytest.fixture(autouse=True)
def _restore_process_loggers() -> Iterator[None]:
    saved = []
    for name in _PROCESS_LOGGERS:
        logger = logging.getLogger(name)
        saved.append(
            (
                logger,
                list(logger.handlers),
                list(logger.filters),
                logger.level,
                logger.propagate,
            )
        )
    yield
    for logger, handlers, filters, level, propagate in saved:
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers = handlers
        logger.filters = filters
        logger.setLevel(level)
        logger.propagate = propagate


# Tests must not touch the network. With the engine on "auto", a test that
# reads os.environ and runs a live provider model could route its turn through
# the real omp binary: host plan opt-ins (OMEGA_PRIME_OMP_ALLOW_*) or host API
# keys (XAI_API_KEY, ZAI_API_KEY, ...) would then spend real quota and replace
# scripted results with live replies. So the engine is off by default, host
# plan, model and login-store settings are dropped, and host provider keys are
# removed. Engine tests opt back in by passing their own env mapping (or
# monkeypatching OMEGA_PRIME_ENGINE and the keys they need).
_HOST_PLAN_PREFIXES = ("OMEGA_PRIME_OMP_", "PI_CODING_AGENT_DIR", "PI_CONFIG_DIR")
_HOST_PROVIDER_KEYS = (
    "ZAI_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "META_API_KEY",
    "MUSE_API_KEY",
    "CURSOR_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_host_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(_HOST_PLAN_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    for name in _HOST_PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    monkeypatch.setenv("OMEGA_PRIME_ENGINE", "off")

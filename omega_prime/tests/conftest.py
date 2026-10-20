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


# Plan opt-ins on the host (a dev box with OMEGA_PRIME_OMP_ALLOW_ZAI or
# OMEGA_PRIME_OMP_ALLOW_CHATGPT set) would let a test that reads os.environ
# route a turn through the real omp engine and spend that plan's quota. Tests
# that need a plan pass their own env mapping or monkeypatch it back in.
_HOST_PLAN_PREFIXES = ("OMEGA_PRIME_OMP_ALLOW_", "OMEGA_PRIME_OMP_MODEL_")


@pytest.fixture(autouse=True)
def _no_host_plan_opt_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(_HOST_PLAN_PREFIXES) or name == "OMEGA_PRIME_OMP_HOME":
            monkeypatch.delenv(name, raising=False)

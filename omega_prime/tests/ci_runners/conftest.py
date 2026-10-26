"""Fixtures for the phase 120 tests; helpers live in ``_support``."""

from __future__ import annotations

import pytest

from ._support import FakeClock, Ids


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ids() -> Ids:
    return Ids()

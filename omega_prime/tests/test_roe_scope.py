"""RoE multi-scope grant composition: narrowing a merged allowlist.

Covers :func:`omega_prime.redteam.roe.grant_scope_filter`, used when an
operator restricts a multi-artifact union (see ``authorize_all``) to a
specific engagement type before registering tools.
"""

from __future__ import annotations

from omega_prime.redteam.roe import grant_scope_filter
from omega_prime.tools.redteam import REDTEAM_TOOL_NAMES

ALLOWLIST = set(REDTEAM_TOOL_NAMES)


def test_allowlist_fixture_is_the_twenty_three_registered_tools():
    assert len(ALLOWLIST) == 23


def test_scope_coverage_keeps_only_cover_tools():
    assert (
        grant_scope_filter(ALLOWLIST, "coverage")
        == {tool for tool in ALLOWLIST if "cover" in tool}
        == {"tempest_black_cover", "tempest_blue_coverage"}
    )


def test_scope_gaps_keeps_only_gaps_tools():
    assert (
        grant_scope_filter(ALLOWLIST, "gaps")
        == {tool for tool in ALLOWLIST if "gaps" in tool}
        == {"tempest_blue_gaps"}
    )


def test_scope_without_match_returns_empty_set():
    assert grant_scope_filter(ALLOWLIST, "audit") == set()


def test_unrestricted_allowlist_passes_through_as_none():
    assert grant_scope_filter(None, "coverage") is None


def test_blank_scope_fails_closed_to_empty():
    assert grant_scope_filter(ALLOWLIST, "  ") == set()


def test_matching_is_case_insensitive():
    assert grant_scope_filter(ALLOWLIST, "Coverage") == {
        "tempest_black_cover",
        "tempest_blue_coverage",
    }

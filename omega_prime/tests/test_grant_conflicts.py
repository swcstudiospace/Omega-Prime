# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""R5 multi-grant conflict detector (``grant_conflicts`` + ``conflicts`` CLI)."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omega_prime.grokbot.roe_lifecycle import (
    EXPIRY_NOTICE_STATES,
    STATES,
    grant_conflicts,
    main,
)

BASE = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _grant(
    name: str | None = "grant-a",
    *,
    start: datetime = BASE - timedelta(days=30),
    end: datetime = BASE,
    **overrides: Any,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "operator": "test-operator",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": _iso(start),
        "effective_until": _iso(end),
        "case_classes": ["prompt-injection"],
    }
    if name is not None:
        document["artifact_id"] = name
    document.update(overrides)
    return document


def _write(tmp_path: Path, name: str, document: dict[str, Any]) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _kinds(rows: list[dict[str, Any]]) -> list[str]:
    return [row["kind"] for row in rows]


# --- tool-scope-contradiction -------------------------------------------------


def test_tool_scope_unrestricted_vs_restricted_is_flagged():
    unrestricted = _grant(
        "grant-a", authorized_targets=[{"host": "127.0.0.1", "tools": []}]
    )
    restricted = _grant("grant-b")
    rows = grant_conflicts([unrestricted, restricted])
    assert rows == [
        {
            "kind": "tool-scope-contradiction",
            "grants": ["grant-a", "grant-b"],
            "detail": (
                "host 127.0.0.1: grant-a unrestricted, grant-b restricted to "
                "tempest_status; overlap "
                f"{_iso(BASE - timedelta(days=30))}..{_iso(BASE)}"
            ),
        }
    ]


def test_tool_scope_absent_tools_key_is_unrestricted():
    open_grant = _grant("grant-a", authorized_targets=[{"host": "127.0.0.1"}])
    rows = grant_conflicts([open_grant, _grant("grant-b")])
    assert _kinds(rows) == ["tool-scope-contradiction"]


def test_tool_scope_order_independent_of_which_is_unrestricted():
    restricted = _grant("grant-a")
    unrestricted = _grant(
        "grant-b", authorized_targets=[{"host": "127.0.0.1", "tools": []}]
    )
    rows = grant_conflicts([restricted, unrestricted])
    assert _kinds(rows) == ["tool-scope-contradiction"]
    assert "grant-b unrestricted, grant-a restricted" in rows[0]["detail"]


def test_tool_scope_differing_nonempty_sets_is_not_a_conflict():
    first = _grant(
        "grant-a", authorized_targets=[{"host": "127.0.0.1", "tools": ["x"]}]
    )
    second = _grant(
        "grant-b", authorized_targets=[{"host": "127.0.0.1", "tools": ["y"]}]
    )
    assert grant_conflicts([first, second]) == []


def test_tool_scope_both_unrestricted_is_not_a_conflict():
    targets = [{"host": "127.0.0.1", "tools": []}]
    first = _grant("grant-a", authorized_targets=targets)
    second = _grant("grant-b", authorized_targets=targets)
    assert grant_conflicts([first, second]) == []


def test_tool_scope_compares_per_host_not_globally():
    first = _grant("grant-a", authorized_targets=[{"host": "host-a", "tools": []}])
    second = _grant("grant-b", authorized_targets=[{"host": "host-b", "tools": ["x"]}])
    assert grant_conflicts([first, second]) == []

    mixed = _grant(
        "grant-a",
        authorized_targets=[
            {"host": "host-a", "tools": []},
            {"host": "host-b", "tools": ["x"]},
        ],
    )
    other = _grant(
        "grant-b",
        authorized_targets=[
            {"host": "HOST-A", "tools": ["y", "z"]},
            {"host": "host-b", "tools": ["x"]},
        ],
    )
    rows = grant_conflicts([mixed, other])
    assert _kinds(rows) == ["tool-scope-contradiction"]
    assert (
        "host host-a: grant-a unrestricted, grant-b restricted to y,z"
        in (rows[0]["detail"])
    )
    assert "host-b" not in rows[0]["detail"]


def test_tool_scope_missing_targets_is_unknown_not_unrestricted():
    bare = _grant("grant-a")
    del bare["authorized_targets"]
    assert grant_conflicts([bare, _grant("grant-b")]) == []


# --- tracker-cover-contradiction ----------------------------------------------


def test_tracker_covers_scoped_vs_all_is_flagged():
    scoped = _grant("grant-a", covers=["Slack"])
    rows = grant_conflicts([scoped, _grant("grant-b")])
    assert _kinds(rows) == ["tracker-cover-contradiction"]
    assert "grant-a covers slack; grant-b covers all trackers" in rows[0]["detail"]


def test_tracker_covers_empty_counts_as_all():
    rows = grant_conflicts(
        [_grant("grant-a", covers=["jira"]), _grant("grant-b", covers=[])]
    )
    assert _kinds(rows) == ["tracker-cover-contradiction"]


def test_tracker_covers_disjoint_is_flagged():
    rows = grant_conflicts(
        [_grant("grant-a", covers=["slack"]), _grant("grant-b", covers=["jira"])]
    )
    assert _kinds(rows) == ["tracker-cover-contradiction"]
    assert "grant-a covers slack; grant-b covers jira" in rows[0]["detail"]


def test_tracker_covers_intersecting_or_both_all_is_not_a_conflict():
    assert (
        grant_conflicts(
            [
                _grant("grant-a", covers=["slack", "jira"]),
                _grant("grant-b", covers=[" JIRA ", "github"]),
            ]
        )
        == []
    )
    assert grant_conflicts([_grant("grant-a"), _grant("grant-b")]) == []


# --- operator-mismatch --------------------------------------------------------


def test_operator_mismatch_is_flagged_with_exact_detail():
    rows = grant_conflicts(
        [_grant("grant-a", operator="op-a"), _grant("grant-b", operator="op-b")]
    )
    assert rows == [
        {
            "kind": "operator-mismatch",
            "grants": ["grant-a", "grant-b"],
            "detail": (
                "operator op-a vs op-b; overlap "
                f"{_iso(BASE - timedelta(days=30))}..{_iso(BASE)}"
            ),
        }
    ]


def test_operator_same_or_blank_or_missing_is_not_a_conflict():
    assert grant_conflicts([_grant("grant-a"), _grant("grant-b")]) == []
    blank = _grant("grant-b", operator="   ")
    assert grant_conflicts([_grant("grant-a"), blank]) == []
    missing = _grant("grant-b")
    del missing["operator"]
    assert grant_conflicts([_grant("grant-a"), missing]) == []


# --- duplicate-id-divergent ---------------------------------------------------


def test_duplicate_id_with_divergent_content_is_flagged():
    first = _grant("same-id")
    second = _grant("same-id", operator="someone-else")
    rows = grant_conflicts([first, second])
    kinds = _kinds(rows)
    assert "duplicate-id-divergent" in kinds
    row = rows[kinds.index("duplicate-id-divergent")]
    assert row["grants"] == ["same-id", "same-id"]
    assert row["detail"] == "id same-id has divergent content"


def test_duplicate_id_flagged_without_usable_windows():
    first = {"id": "x", "operator": "a"}
    second = {"id": "x", "operator": "b"}
    assert _kinds(grant_conflicts([first, second])) == ["duplicate-id-divergent"]


def test_duplicate_id_identical_content_is_not_a_conflict():
    document = _grant("same-id")
    assert grant_conflicts([document, dict(document)]) == []


def test_duplicate_id_ignores_operator_until_fallback_ids():
    # No artifact_id/id: both fall back to "operator|until" and collide.
    first = _grant(None, authorized_targets=[{"host": "127.0.0.1", "tools": ["x"]}])
    second = _grant(None, authorized_targets=[{"host": "127.0.0.1", "tools": ["y"]}])
    assert first != second
    assert grant_conflicts([first, second]) == []


def test_duplicate_id_distinct_paths_are_distinct_grants(tmp_path):
    first = _write(tmp_path, "one.json", _grant("same-id"))
    second = _write(tmp_path, "two.json", _grant("same-id", operator="other"))
    rows = grant_conflicts([first, second])
    # Path wins as the grant id, so these are different grants: only the
    # operator mismatch remains.
    assert _kinds(rows) == ["operator-mismatch"]
    assert rows[0]["grants"] == sorted([first, second])


def test_duplicate_id_path_versus_dict_share_no_identity(tmp_path):
    path = _write(tmp_path, "one.json", _grant("same-id"))
    rows = grant_conflicts([path, _grant("same-id", covers=["slack"])])
    assert "duplicate-id-divergent" not in _kinds(rows)


# --- overlap / now ------------------------------------------------------------


def _pair(gap: timedelta) -> list[dict[str, Any]]:
    first = _grant("grant-a", start=BASE - timedelta(days=10), end=BASE)
    second = _grant(
        "grant-b",
        start=BASE + gap,
        end=BASE + gap + timedelta(days=10),
        operator="other-operator",
    )
    return [first, second]


def test_overlap_touching_windows_overlap():
    assert _kinds(grant_conflicts(_pair(timedelta(0)))) == ["operator-mismatch"]


def test_overlap_one_second_apart_does_not_overlap():
    assert grant_conflicts(_pair(timedelta(seconds=1))) == []


def test_overlap_unparseable_or_missing_windows_are_skipped():
    for broken in ({"effective_from": "soon"}, {"effective_until": None}):
        other = _grant("grant-b", operator="other-operator", **broken)
        assert grant_conflicts([_grant("grant-a"), other]) == []
    no_window = {"artifact_id": "grant-b", "operator": "other-operator"}
    assert grant_conflicts([_grant("grant-a"), no_window]) == []


def test_overlap_inverted_window_is_skipped():
    inverted = _grant(
        "grant-b", start=BASE, end=BASE - timedelta(days=1), operator="other"
    )
    assert grant_conflicts([_grant("grant-a"), inverted]) == []


def test_now_filters_out_expired_grants():
    old = _grant(
        "grant-a", start=BASE - timedelta(days=60), end=BASE - timedelta(days=1)
    )
    current = _grant(
        "grant-b", start=BASE - timedelta(days=30), end=BASE + timedelta(days=5)
    )
    old["operator"] = "op-old"
    # Overlap-only (no now): the windows overlap, so the mismatch is reported.
    assert _kinds(grant_conflicts([old, current])) == ["operator-mismatch"]
    # At BASE the old grant is expired: nothing to compare.
    assert grant_conflicts([old, current], now=BASE) == []
    # Inside both windows the conflict is reported again.
    assert _kinds(grant_conflicts([old, current], now=BASE - timedelta(days=5))) == [
        "operator-mismatch"
    ]


def test_now_bounds_are_inclusive():
    pair = _pair(timedelta(0))
    assert _kinds(grant_conflicts(pair, now=BASE)) == ["operator-mismatch"]
    assert grant_conflicts(pair, now=BASE + timedelta(days=11)) == []
    assert grant_conflicts(pair, now=BASE - timedelta(days=11)) == []


def test_now_accepts_iso_text_and_unparseable_now_falls_back_to_overlap_only():
    pair = _pair(timedelta(0))
    assert _kinds(grant_conflicts(pair, now=_iso(BASE))) == ["operator-mismatch"]
    assert grant_conflicts(pair, now=_iso(BASE + timedelta(days=30))) == []
    assert _kinds(grant_conflicts(pair, now="not-a-time")) == ["operator-mismatch"]


def test_now_drops_expired_duplicate_id_but_keeps_unwindowed():
    old = _grant("dup", start=BASE - timedelta(days=60), end=BASE - timedelta(days=1))
    current = _grant("dup", operator="other")
    assert "duplicate-id-divergent" in _kinds(grant_conflicts([old, current]))
    assert grant_conflicts([old, current], now=BASE) == []
    unwindowed_a = {"id": "dup", "operator": "a"}
    unwindowed_b = {"id": "dup", "operator": "b"}
    assert _kinds(grant_conflicts([unwindowed_a, unwindowed_b], now=BASE)) == [
        "duplicate-id-divergent"
    ]


# --- determinism --------------------------------------------------------------


def _busy_grants() -> list[dict[str, Any]]:
    return [
        _grant(
            "grant-c",
            operator="op-c",
            covers=["slack"],
            authorized_targets=[{"host": "127.0.0.1", "tools": []}],
        ),
        _grant("grant-a", operator="op-a"),
        _grant("grant-b", operator="op-b", covers=["jira"]),
        _grant("grant-a", operator="op-a", covers=["github"]),
    ]


def test_output_is_deterministic_and_sorted_for_any_input_order():
    grants = _busy_grants()
    expected = grant_conflicts(grants)
    assert expected, "busy fixture must produce conflicts"
    keys = [(row["kind"], row["grants"]) for row in expected]
    assert keys == sorted(keys)
    for row in expected:
        assert row["grants"] == sorted(row["grants"])
        assert set(row) == {"kind", "grants", "detail"}
    for ordering in itertools.permutations(grants):
        assert grant_conflicts(list(ordering)) == expected


def test_output_is_deterministic_across_repeated_calls():
    assert grant_conflicts(_busy_grants()) == grant_conflicts(_busy_grants())


def test_duplicate_listing_of_one_grant_adds_no_rows():
    grants = [_grant("grant-a", operator="op-a"), _grant("grant-b", operator="op-b")]
    assert grant_conflicts([*grants, grants[0]]) == grant_conflicts(grants)


# --- robustness ---------------------------------------------------------------


def test_fewer_than_two_readable_grants_is_empty(tmp_path):
    assert grant_conflicts([]) == []
    assert grant_conflicts(None) == []
    assert grant_conflicts(_grant("grant-a")) == []
    assert grant_conflicts([_grant("grant-a")]) == []
    assert grant_conflicts([_grant("grant-a"), None, 7, str(tmp_path / "absent")]) == []


def test_junk_inputs_never_raise(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2]", encoding="utf-8")
    junk: list[Any] = [
        None,
        7,
        3.5,
        True,
        "",
        "   ",
        b"bytes",
        object(),
        {},
        {"authorized_targets": "nope", "covers": 5, "operator": 9},
        {"effective_from": 1, "effective_until": [], "id": 3},
        str(tmp_path / "absent.json"),
        str(malformed),
        str(not_object),
        str(tmp_path),
        {"artifact_id": "x", "when": datetime(2026, 1, 1, tzinfo=UTC)},
    ]
    assert isinstance(grant_conflicts(junk), list)
    assert isinstance(grant_conflicts(junk, now="zzz"), list)
    for item in junk:
        assert grant_conflicts(item) == []
        assert isinstance(grant_conflicts([item, item], now=object()), list)
    assert grant_conflicts(junk, now=BASE) == []
    # Junk beside two real grants is skipped silently; the pair still reports.
    pair = [_grant("grant-a", operator="op-a"), _grant("grant-b", operator="op-b")]
    assert _kinds(grant_conflicts([*junk, *pair])) == ["operator-mismatch"]


def test_accepts_paths_and_dicts_mixed(tmp_path):
    path = _write(tmp_path, "one.json", _grant("grant-a", operator="op-a"))
    rows = grant_conflicts([Path(path), _grant("grant-b", operator="op-b")])
    assert _kinds(rows) == ["operator-mismatch"]
    assert rows[0]["grants"] == sorted([path, "grant-b"])


def test_detail_has_no_control_characters():
    first = _grant(
        "grant\x1b[31m-a\n",
        operator="op\r\nA\x00",
        covers=["sl\x07ack"],
        authorized_targets=[{"host": "ho\nst", "tools": []}],
    )
    second = _grant(
        "grant-b\t",
        operator="op\tB",
        covers=["ji\x1bra"],
        authorized_targets=[{"host": "ho\nst", "tools": ["to\x00ol\n"]}],
    )
    rows = grant_conflicts([first, second])
    assert {row["kind"] for row in rows} == {
        "tool-scope-contradiction",
        "tracker-cover-contradiction",
        "operator-mismatch",
    }
    for row in rows:
        for text in (row["kind"], row["detail"], *row["grants"]):
            assert text.isprintable(), text
    assert any("tool" in row["detail"] for row in rows)


def test_does_not_mutate_inputs():
    grants = _busy_grants()
    snapshot = json.dumps(grants, sort_keys=True)
    grant_conflicts(grants, now=BASE)
    assert json.dumps(grants, sort_keys=True) == snapshot


def test_lifecycle_vocabulary_is_unchanged():
    assert STATES == (
        "valid",
        "expiring-7d",
        "expiring-1d",
        "expired",
        "missing",
        "invalid",
    )
    assert EXPIRY_NOTICE_STATES == ("expiring-7d", "expiring-1d", "expired")


# --- CLI ----------------------------------------------------------------------


def test_cli_exit_zero_when_no_conflicts(tmp_path, capsys):
    first = _write(tmp_path, "a.json", _grant("grant-a"))
    second = _write(tmp_path, "b.json", _grant("grant-b"))
    rc = main(["conflicts", "--artifact", first, "--artifact", second])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "roe conflicts: none"


def test_cli_exit_three_and_human_lines(tmp_path, capsys):
    first = _write(tmp_path, "a.json", _grant("grant-a", operator="op-a"))
    second = _write(tmp_path, "b.json", _grant("grant-b", operator="op-b"))
    rc = main(["conflicts", "--artifact", first, "--artifact", second])
    assert rc == 3
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    id_a, id_b = sorted([first, second])
    assert lines[0].startswith(f"operator-mismatch: {id_a} vs {id_b} - operator op-a")


def test_cli_json_prints_the_conflict_list(tmp_path, capsys):
    first = _write(tmp_path, "a.json", _grant("grant-a", operator="op-a"))
    second = _write(tmp_path, "b.json", _grant("grant-b", operator="op-b"))
    rc = main(["conflicts", "--artifact", first, "--artifact", second, "--json"])
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload == grant_conflicts([first, second])
    assert payload[0]["grants"] == sorted([first, second])
    assert set(payload[0]) == {"kind", "grants", "detail"}


def test_cli_json_empty_list_exit_zero(tmp_path, capsys):
    first = _write(tmp_path, "a.json", _grant("grant-a"))
    rc = main(["conflicts", "--artifact", first, "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_now_filters_expired_grants(tmp_path, capsys):
    old = _grant(
        "grant-a",
        start=BASE - timedelta(days=60),
        end=BASE - timedelta(days=1),
        operator="op-a",
    )
    current = _grant("grant-b", operator="op-b")
    first = _write(tmp_path, "a.json", old)
    second = _write(tmp_path, "b.json", current)
    args = ["conflicts", "--artifact", first, "--artifact", second]
    assert main(args) == 3
    capsys.readouterr()
    assert main([*args, "--now", _iso(BASE)]) == 0


def test_cli_usage_errors_exit_two(tmp_path, capsys):
    first = _write(tmp_path, "a.json", _grant("grant-a"))
    assert main(["conflicts", "--artifact", first, "--now", "nope"]) == 2
    assert "--now" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["conflicts"])
    assert exc.value.code == 2


def test_cli_missing_and_malformed_artifacts_are_skipped(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    first = _write(tmp_path, "a.json", _grant("grant-a"))
    args = ["conflicts", "--artifact", first, "--artifact", str(bad)]
    args += ["--artifact", str(tmp_path / "absent.json")]
    assert main(args) == 0


def test_cli_writes_nothing(tmp_path):
    first = _write(tmp_path, "a.json", _grant("grant-a", operator="op-a"))
    second = _write(tmp_path, "b.json", _grant("grant-b", operator="op-b"))
    before = sorted(p.name for p in tmp_path.iterdir())
    contents = {name: (tmp_path / name).read_bytes() for name in before}
    main(["conflicts", "--artifact", first, "--artifact", second])
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert {name: (tmp_path / name).read_bytes() for name in before} == contents

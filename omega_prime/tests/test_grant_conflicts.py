# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""R5 multi-grant conflict detector (``grant_conflicts`` + ``conflicts`` CLI)."""

from __future__ import annotations

import itertools
import json
import random
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omega_prime.grokbot.roe_lifecycle import (
    EXPIRY_NOTICE_STATES,
    MAX_GRANT_BYTES,
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


# --- scaling, output cap and input cap ----------------------------------------

_FAR_END = BASE + timedelta(days=3650)


def _wide(
    index: int,
    tools: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """A long-lived grant on host ``h.example`` (``tools=None`` = no key)."""
    target: dict[str, Any] = {"host": "h.example"}
    if tools is not None:
        target["tools"] = tools
    document = _grant(
        f"g{index:05d}",
        end=_FAR_END,
        authorized_targets=[target],
        operator="alice",
    )
    document.update(overrides)
    return document


def _reference_pairs(
    documents: list[dict[str, Any]],
) -> set[tuple[str, tuple[str, str]]]:
    """Brute-force ``(kind, ids)`` pairs for unique-id single-host grants."""
    expected: set[tuple[str, tuple[str, str]]] = set()
    ordered = sorted(documents, key=lambda doc: doc["artifact_id"])
    for idx, first in enumerate(ordered):
        for second in ordered[idx + 1 :]:
            if not (
                first["effective_from"] <= second["effective_until"]
                and second["effective_from"] <= first["effective_until"]
            ):
                continue
            ids = (first["artifact_id"], second["artifact_id"])
            tools_a = first["authorized_targets"][0].get("tools") or None
            tools_b = second["authorized_targets"][0].get("tools") or None
            if first["authorized_targets"][0]["host"].lower() == second[
                "authorized_targets"
            ][0]["host"].lower() and (tools_a is None) != (tools_b is None):
                expected.add(("tool-scope-contradiction", ids))
            cover_a, cover_b = first.get("covers") or None, second.get("covers") or None
            if (cover_a is None) != (cover_b is None) or (
                cover_a is not None
                and cover_b is not None
                and not set(cover_a) & set(cover_b)
            ):
                expected.add(("tracker-cover-contradiction", ids))
            if first.get("operator") != second.get("operator"):
                expected.add(("operator-mismatch", ids))
    return expected


def test_sweep_matches_bruteforce_reference_on_random_fixtures():
    rng = random.Random(1801)
    cover_choices: list[list[str] | None] = [
        None,
        ["slack"],
        ["jira"],
        ["slack", "jira"],
    ]
    conflicted = 0
    for _trial in range(300):
        documents = []
        for index in range(rng.randint(2, 8)):
            start = rng.randint(0, 12)
            end = start + rng.randint(0, 6)
            document = _grant(
                f"r{index}",
                start=BASE + timedelta(days=start),
                end=BASE + timedelta(days=end),
                operator=rng.choice(["alice", "bob"]),
                authorized_targets=[
                    {
                        "host": rng.choice(["H.example", "h.example"]),
                        **(
                            {"tools": rng.choice([[], ["t1"], ["t2"]])}
                            if rng.random() < 0.8
                            else {}
                        ),
                    }
                ],
            )
            cover = rng.choice(cover_choices)
            if cover is not None:
                document["covers"] = cover
            documents.append(document)
        rows = grant_conflicts(documents)
        assert len(rows) < 100
        actual = {(row["kind"], tuple(row["grants"])) for row in rows}
        assert len(actual) == len(rows)
        assert actual == _reference_pairs(documents)
        conflicted += bool(rows)
    assert conflicted > 100


def test_rows_beyond_the_cap_become_one_sentinel_with_the_exact_remainder():
    # 20 "alice" grants x 8 "bob" grants -> 160 operator-mismatch pairs.
    documents = [_wide(i) for i in range(20)] + [
        _wide(100 + i, operator="bob") for i in range(8)
    ]
    rows = grant_conflicts(documents)
    assert len(rows) == 101
    assert rows[-1] == {
        "kind": "conflicts-truncated",
        "grants": [],
        "detail": "60 more conflicts not listed",
    }
    reference = sorted(_reference_pairs(documents))
    assert [(row["kind"], tuple(row["grants"])) for row in rows[:-1]] == reference[:100]
    assert all(row["kind"] == "operator-mismatch" for row in rows[:-1])
    assert rows[:-1] == sorted(
        rows[:-1], key=lambda row: (row["kind"], row["grants"], row["detail"])
    )


def test_exactly_the_cap_has_no_sentinel_and_one_over_adds_it():
    exactly = [_wide(i) for i in range(10)] + [
        _wide(100 + i, operator="bob") for i in range(10)
    ]
    rows = grant_conflicts(exactly)
    assert len(rows) == 100
    assert "conflicts-truncated" not in _kinds(rows)
    one_over = [_wide(0)] + [_wide(100 + i, operator="bob") for i in range(101)]
    rows = grant_conflicts(one_over)
    assert len(rows) == 101
    assert rows[-1]["kind"] == "conflicts-truncated"
    assert rows[-1]["detail"] == "1 more conflicts not listed"


def test_truncated_listing_is_deterministic_for_any_input_order():
    documents = [_wide(i) for i in range(15)] + [
        _wide(100 + i, operator="bob") for i in range(15)
    ]
    expected = grant_conflicts(documents)
    assert expected[-1]["kind"] == "conflicts-truncated"
    for seed in range(5):
        shuffled = list(documents)
        random.Random(seed).shuffle(shuffled)
        assert grant_conflicts(shuffled) == expected


def test_mixed_unrestricted_and_restricted_grants_stay_capped_and_bounded():
    # 400 unrestricted x 400 restricted on one host: 160k tool-scope pairs.
    documents = [_wide(i, [] if i % 2 == 0 else ["tempest_a"]) for i in range(800)]
    tracemalloc.start()
    started = time.monotonic()
    rows = grant_conflicts(documents)
    elapsed = time.monotonic() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert elapsed < 30
    assert peak < 10_000_000
    assert len(rows) == 101
    assert _kinds(rows[:-1]) == ["tool-scope-contradiction"] * 100
    assert rows[-1]["detail"] == f"{400 * 400 - 100} more conflicts not listed"


def test_colliding_labels_count_each_distinct_row_once():
    # 60 grants share one id (divergent operators): one duplicate-id row
    # plus one operator row per unordered pair of distinct operators.
    documents = [
        _wide(0, artifact_id="same", operator=f"op{index:03d}") for index in range(60)
    ]
    rows = grant_conflicts(documents)
    total = 1 + 60 * 59 // 2
    assert len(rows) == 101
    assert rows[-1]["detail"] == f"{total - 100} more conflicts not listed"
    assert _kinds(rows).count("duplicate-id-divergent") == 1
    assert len({json.dumps(row, sort_keys=True) for row in rows}) == 101


def test_two_thousand_non_conflicting_grants_scale():
    documents = [_wide(i, ["tempest_a"]) for i in range(2000)]
    started = time.monotonic()
    assert grant_conflicts(documents) == []
    assert time.monotonic() - started < 10


def test_many_targets_per_grant_scale():
    documents = [
        _wide(
            index,
            authorized_targets=[
                {"host": f"h{host}.example", "tools": ["t"]} for host in range(50)
            ],
        )
        for index in range(500)
    ]
    started = time.monotonic()
    assert grant_conflicts(documents) == []
    assert time.monotonic() - started < 10


def test_non_overlapping_grants_are_never_compared():
    documents = [
        _grant(
            f"w{index:04d}",
            start=BASE + timedelta(days=index * 2),
            end=BASE + timedelta(days=index * 2 + 1),
            operator=f"op{index}",
        )
        for index in range(600)
    ]
    assert grant_conflicts(documents) == []


def _padded_grant_file(tmp_path: Path, name: str, size: int) -> str:
    document = _grant("padded", operator="someone-else")
    document["padding"] = ""
    overhead = len(json.dumps(document).encode("utf-8"))
    document["padding"] = "x" * (size - overhead)
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    assert path.stat().st_size == size
    return str(path)


def test_oversized_grant_file_is_ignored_but_the_limit_itself_is_read(tmp_path):
    other = _grant("other", operator="test-operator")
    at_limit = _padded_grant_file(tmp_path, "at-limit.json", MAX_GRANT_BYTES)
    over = _padded_grant_file(tmp_path, "over.json", MAX_GRANT_BYTES + 1)
    assert _kinds(grant_conflicts([at_limit, other])) == ["operator-mismatch"]
    assert grant_conflicts([over, other]) == []
    # an in-memory document is never size-capped by the file limit
    assert grant_conflicts([over, other, _grant("third", operator="x")]) != []


def test_oversized_file_does_not_hide_the_remaining_grants(tmp_path):
    over = _padded_grant_file(tmp_path, "over.json", MAX_GRANT_BYTES + 10)
    rows = grant_conflicts(
        [over, _grant("a", operator="one"), _grant("b", operator="two")]
    )
    assert _kinds(rows) == ["operator-mismatch"]
    assert rows[0]["grants"] == ["a", "b"]


def test_cli_json_prints_the_capped_list_with_sentinel(tmp_path, capsys):
    paths = [
        _write(tmp_path, f"a{index}.json", _wide(index, artifact_id=f"a{index:03d}"))
        for index in range(11)
    ] + [
        _write(
            tmp_path,
            f"b{index}.json",
            _wide(100 + index, artifact_id=f"b{index:03d}", operator="bob"),
        )
        for index in range(11)
    ]
    argv = ["conflicts"]
    for path in paths:
        argv += ["--artifact", path]
    rc = main([*argv, "--json"])
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 101
    assert payload[-1] == {
        "kind": "conflicts-truncated",
        "grants": [],
        "detail": "21 more conflicts not listed",
    }
    rc = main(argv)
    assert rc == 3
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 101
    assert lines[-1] == "conflicts-truncated: 21 more conflicts not listed"

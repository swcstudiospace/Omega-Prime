# SPDX-License-Identifier: AGPL-3.0-only
"""Protocol default 1: per-task staleness with injectable evidence sources."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.poller import DashboardPoller
from omega_prime.dashboard.stale import (
    DEFAULT_STALE_MINUTES,
    REASONS,
    StaleChecker,
    last_signal,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _row(**over):
    row = {
        "id": "gb:one",
        "title": "one",
        "owner": {"kind": "human", "ref": "o"},
        "state": "working",
    }
    row.update(over)
    return row


def _lister(mapping):
    return lambda: mapping


def _ago(minutes):
    return (NOW - timedelta(minutes=minutes)).isoformat()


def test_reasons_order_and_default_minutes():
    assert DEFAULT_STALE_MINUTES == 20.0
    assert REASONS == ("pid_dead", "pane_gone", "pane_exited", "no_heartbeat")


def test_pid_dead_flags_and_live_pid_falls_through():
    checker = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: False)
    verdict = checker.stale_reason(_row(pid=4242, last_activity_at=_ago(1)))
    assert verdict == {"reason": "pid_dead", "since": _ago(1)}

    live = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: True)
    assert live.stale_reason(_row(pid=4242, last_activity_at=_ago(1))) is None


def test_pane_gone_when_map_omits_the_pane():
    checker = StaleChecker(
        clock=lambda: NOW,
        pane_lister=_lister({"w2:p9": {"pane_id": "w2:p9"}}),
        pid_alive=lambda pid: True,
    )
    verdict = checker.stale_reason(
        _row(herdr_pane={"pane_id": "w2:p4"}, last_activity_at=_ago(1))
    )
    assert verdict == {"reason": "pane_gone", "since": _ago(1)}


def test_pane_exited_via_flag_and_status_string():
    for info in ({"exited": True}, {"status": "Exited"}):
        checker = StaleChecker(
            clock=lambda: NOW,
            pane_lister=_lister({"w2:p4": {"pane_id": "w2:p4", **info}}),
            pid_alive=lambda pid: True,
        )
        verdict = checker.stale_reason(
            _row(herdr_pane={"pane_id": "w2:p4"}, last_activity_at=_ago(1))
        )
        assert verdict == {"reason": "pane_exited", "since": _ago(1)}


def test_unavailable_lister_suppresses_pane_reasons():
    checker = StaleChecker(
        clock=lambda: NOW,
        pane_lister=lambda: None,
        pid_alive=lambda pid: True,
    )
    row = _row(herdr_pane={"pane_id": "w2:p4"}, last_activity_at=_ago(45))
    assert checker.stale_reason(row) == {"reason": "no_heartbeat", "since": _ago(45)}


def test_no_heartbeat_boundary_is_inclusive():
    checker = StaleChecker(
        clock=lambda: NOW,
        pane_lister=lambda: None,
        pid_alive=lambda pid: True,
        stale_minutes=10.0,
    )
    row = _row(last_heartbeat_at=_ago(10))
    verdict = checker.stale_reason(row)
    assert verdict is not None and verdict["reason"] == "no_heartbeat"
    row_under = _row(
        last_heartbeat_at=(NOW - timedelta(minutes=10, seconds=-1)).isoformat()
    )
    assert checker.stale_reason(row_under) is None


def test_default_minutes_twenty_custom_five():
    checker_default = StaleChecker(
        clock=lambda: NOW, pane_lister=lambda: None, pid_alive=lambda pid: True
    )
    row = _row(last_heartbeat_at=_ago(10))
    assert checker_default.stale_reason(row) is None
    checker_five = StaleChecker(
        clock=lambda: NOW,
        pane_lister=lambda: None,
        pid_alive=lambda pid: True,
        stale_minutes=5.0,
    )
    verdict_five = checker_five.stale_reason(row)
    assert verdict_five is not None and verdict_five["reason"] == "no_heartbeat"


def test_fresh_activity_wins_over_old_heartbeat():
    checker = StaleChecker(
        clock=lambda: NOW, pane_lister=lambda: None, pid_alive=lambda pid: True
    )
    row = _row(last_heartbeat_at=_ago(90), last_activity_at=_ago(2))
    assert checker.stale_reason(row) is None


def test_non_working_states_return_none_even_with_dead_pid():
    checker = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: False)
    for state in ("queued", "done", "failed", "waiting_on_ove"):
        assert checker.stale_reason(_row(state=state, pid=4242)) is None


def test_missing_evidence_is_none_but_dead_pid_without_timestamps_flags():
    checker = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: True)
    assert checker.stale_reason(_row()) is None
    dead = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: False)
    verdict = dead.stale_reason(_row(pid=7))
    assert verdict == {"reason": "pid_dead", "since": None}


def test_since_is_reference_timestamp_never_now():
    checker = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: False)
    heartbeat = _ago(33)
    verdict = checker.stale_reason(_row(pid=7, last_heartbeat_at=heartbeat))
    assert verdict is not None
    assert verdict["since"] == heartbeat != NOW.isoformat()
    assert last_signal(_row(pid=7)) is None


def test_stale_map_calls_lister_once_and_keys_by_identity():
    calls = []

    def lister():
        calls.append(1)
        return {"w2:p4": {"pane_id": "w2:p4"}}

    checker = StaleChecker(
        clock=lambda: NOW,
        pane_lister=lister,
        pid_alive=lambda pid: True,
        stale_minutes=5.0,
    )
    rows = [
        _row(id="bare", herdr_pane={"pane_id": "w2:p1"}, last_activity_at=_ago(30)),
        _row(id="gb:gone", herdr_pane={"pane_id": "w9:p1"}),
        _row(id="gb:fresh", state="queued"),
    ]
    out = checker.stale_map(rows, now=NOW)
    assert len(calls) == 1
    assert out == {
        "tasks:bare": {"reason": "pane_gone", "since": _ago(30)},
        "gb:gone": {"reason": "pane_gone", "since": None},
        "gb:fresh": None,
    }


def _feed_poller(poller, rows):
    poller._results["tasks_json"] = SourceResult(
        "tasks_json", "ok", extra={"tasks": rows, "waiting_on_ove": [], "finished": []}
    )
    poller._build_snapshot()


def test_snapshot_tasks_carry_stale_inside_task_objects(tmp_path):
    checker = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: False)
    poller = DashboardPoller([], stale_checker=checker, clock=lambda: NOW)
    rows = [
        _row(id="gb:dead", pid=4242, last_activity_at=_ago(30)),
        _row(id="gb:fresh", last_activity_at=_ago(1)),
        _row(id="gb:queued", state="queued", pid=4242),
    ]
    _feed_poller(poller, rows)
    snapshot = poller.snapshot()
    assert snapshot is not None
    assert set(snapshot) == {
        "generated_at",
        "sources",
        "active",
        "pr_gate",
        "waiting_on_ove",
        "finished",
        "idle_sessions",
    }
    by_id = {task["id"]: task for task in snapshot["active"]}
    assert by_id["gb:dead"]["stale"] == {"reason": "pid_dead", "since": _ago(30)}
    assert by_id["gb:fresh"]["stale"] is None
    assert by_id["gb:queued"]["stale"] is None


def test_stale_survives_numbering(tmp_path):
    checker = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: False)
    poller = DashboardPoller(
        [],
        task_ids_path=tmp_path / "tasks.json",
        stale_checker=checker,
        clock=lambda: NOW,
    )
    _feed_poller(poller, [_row(id="gb:dead", pid=4242, last_activity_at=_ago(30))])
    built = poller.snapshot()
    assert built is not None
    task = built["active"][0]
    assert task["stale"] == {"reason": "pid_dead", "since": _ago(30)}
    assert task["seq"] == 1


def test_default_checker_degrades_without_herdr_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    poller = DashboardPoller([], clock=lambda: NOW)
    _feed_poller(
        poller,
        [_row(id="gb:pane", herdr_pane={"pane_id": "w2:p4"}, last_activity_at=_ago(1))],
    )
    built = poller.snapshot()
    assert built is not None
    task = built["active"][0]
    assert task["stale"] is None
    assert poller.sources_status().get("normalization") is None


def test_unchanged_evidence_is_digest_stable_across_clock_moves():
    clocks = iter([NOW, NOW + timedelta(hours=1)])
    checker = StaleChecker(clock=lambda: NOW, pid_alive=lambda pid: False)
    poller = DashboardPoller([], stale_checker=checker, clock=lambda: next(clocks))
    rows = [_row(id="gb:dead", pid=4242, last_activity_at=_ago(30))]

    _feed_poller(poller, rows)
    first = poller.snapshot()
    assert poller._changed.is_set()
    assert asyncio.run(poller.wait_for_update(timeout=0.01)) is True

    _feed_poller(poller, rows)
    second = poller.snapshot()
    assert poller._changed.is_set() is False

    def strip(snap):
        return {k: v for k, v in snap.items() if k != "generated_at"}

    assert json.dumps(strip(first), sort_keys=True) == json.dumps(
        strip(second), sort_keys=True
    )


# --- Greptile round 1 regressions -------------------------------------------

from omega_prime.dashboard.stale import normalize_pane_list  # noqa: E402


def test_unusable_pane_entries_are_unavailable_not_empty():
    assert normalize_pane_list({"panes": [{}]}) is None
    assert normalize_pane_list({"panes": [{"workspace": "x"}, "junk"]}) is None
    assert normalize_pane_list([{"pane_id": ""}]) is None


def test_genuinely_empty_pane_list_is_an_empty_map():
    assert normalize_pane_list({"panes": []}) == {}
    assert normalize_pane_list([]) == {}


def test_nested_result_panes_envelope_is_unwrapped():
    payload = {"result": {"panes": [{"pane_id": "w1:p1", "status": "running"}]}}
    assert normalize_pane_list(payload) == {
        "w1:p1": {"pane_id": "w1:p1", "status": "running"}
    }


def test_bad_pane_response_never_marks_mapped_tasks_pane_gone():
    checker = StaleChecker(
        clock=lambda: NOW,
        pid_alive=lambda pid: True,
        pane_lister=lambda: normalize_pane_list({"panes": [{}]}),
    )
    row = _row(
        id="gb:mapped",
        herdr_pane={"pane_id": "w1:p1"},
        last_activity_at=NOW.isoformat(),
    )
    assert checker.stale_reason(row, now=NOW) is None


def test_poller_collects_stale_evidence_off_the_event_loop():
    import threading

    seen: list[bool] = []

    def lister():
        seen.append(threading.current_thread() is threading.main_thread())
        return {}

    checker = StaleChecker(
        clock=lambda: NOW, pid_alive=lambda pid: True, pane_lister=lister
    )
    poller = DashboardPoller([], stale_checker=checker, clock=lambda: NOW)
    poller._results["tasks_json"] = SourceResult(
        "tasks_json",
        "ok",
        extra={
            "tasks": [
                _row(
                    id="gb:mapped",
                    herdr_pane={"pane_id": "w1:p1"},
                    last_activity_at=NOW.isoformat(),
                )
            ],
            "waiting_on_ove": [],
            "finished": [],
        },
    )
    asyncio.run(poller._maybe_build())
    assert seen == [False]
    snapshot = poller.snapshot()
    assert snapshot is not None
    stale = {task["id"]: task.get("stale") for task in snapshot["active"]}
    assert stale["gb:mapped"] == {"reason": "pane_gone", "since": NOW.isoformat()}

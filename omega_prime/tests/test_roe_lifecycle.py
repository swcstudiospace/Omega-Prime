"""RoE lifecycle watcher tests: thresholds, dedup, CLI, surfaces (PLAN-F6).

Hermetic: every tick and check takes an injected UTC ``now``; no
wall-clock reads. The watcher never verifies signatures, so fixtures are
unsigned — warnings must fire even for an unsigned artifact.
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from omega_prime.cron.heartbeat import (
    HEARTBEAT_KIND,
    ROE_EXPIRY_KIND,
    ROE_RENEWAL_KIND,
    clear_roe_expiry,
    heartbeat_roe_renewals,
    list_roe_expiries,
    schedule_heartbeat,
    schedule_roe_expiry,
    tick_heartbeats,
    tick_roe_expiry,
)
from omega_prime.cron.scheduler import (
    ROE_WATCHDOG_KIND,
    JobStore,
    clear_roe_watchdog,
    list_roe_watchdogs,
    schedule_roe_watchdog,
    tick_roe_watchdog,
)
from omega_prime.grokbot.roe_lifecycle import (
    RENEWAL_THRESHOLDS_DAYS,
    ROE_REVOKED_KIND,
    STATES,
    WATCHDOG_ACTIONS,
    check_readiness,
    check_roe_lifecycle,
    clear_renewal_reminders,
    gateway_reachable,
    grant_trackers,
    main,
    read_grant_usage,
    record_grant_usage,
    renewal_reminders,
    suite_counts,
    watchdog_check,
    watchdog_check_all,
)
from omega_prime.tools.durable_surface import register_durable_surface_tools
from omega_prime.tools.registry import ToolRegistry

END = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _document(**overrides) -> dict:
    start = (END - timedelta(days=30)).isoformat(timespec="seconds")
    document = {
        "schema_version": 1,
        "operator": "test-operator",
        "authorized_targets": [{"host": "127.0.0.1", "tools": ["tempest_status"]}],
        "effective_from": start,
        "effective_until": END.isoformat(timespec="seconds"),
        "case_classes": ["prompt-injection"],
    }
    document.update(overrides)
    return document


def _write_artifact(tmp_path, document=None):
    artifact = tmp_path / "roe.json"
    artifact.write_text(
        json.dumps(document if document is not None else _document()),
        encoding="utf-8",
    )
    return artifact


def _store(tmp_path):
    return JobStore(tmp_path / "cron" / "jobs.json")


def _checker(artifact, now):
    return check_roe_lifecycle(artifact, now)


def _schedule_due(store, artifact) -> str:
    return schedule_roe_expiry(store, str(artifact), interval_seconds=86400, due_at=0)


def test_schedule_marks_kind_and_persists(tmp_path):
    store = _store(tmp_path)
    artifact = _write_artifact(tmp_path)
    job_id = schedule_roe_expiry(store, str(artifact), interval_seconds=60, due_at=1000)
    jobs = list_roe_expiries(store)
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["kind"] == ROE_EXPIRY_KIND
    assert jobs[0]["artifact_path"] == str(artifact)
    assert len(list_roe_expiries(_store(tmp_path))) == 1


def test_schedule_validates_inputs(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="artifact_path"):
        schedule_roe_expiry(store, "   ", due_at=0)
    with pytest.raises(ValueError, match="interval"):
        schedule_roe_expiry(store, "roe.json", interval_seconds=0, due_at=0)


def test_roe_lifecycle_thresholds(tmp_path):
    store = _store(tmp_path)
    artifact = _write_artifact(tmp_path)
    job_id = _schedule_due(store, artifact)

    def job():
        return next(j for j in store.jobs if j["id"] == job_id)

    # T-8d: silent, healthy.
    now = END - timedelta(days=8)
    assert tick_roe_expiry(store, now, _checker) == []
    assert job()["last_result"]["state"] == "valid"
    assert job()["last_result"]["days_remaining"] == pytest.approx(8.0, abs=1e-6)

    # T-7d: fires expiring-7d exactly once.
    now = END - timedelta(days=7)
    fired = tick_roe_expiry(store, now, _checker)
    assert [j["id"] for j in fired] == [job_id]
    assert job()["last_result"]["state"] == "expiring-7d"
    assert job()["last_result"]["days_remaining"] == pytest.approx(7.0, abs=1e-6)
    assert job()["last_result"]["cause"] == "ok"

    # Same UTC day, forced due again: silent (dedup), bookkeeping refreshes.
    later_same_day = now + timedelta(hours=5)
    job()["due_at"] = later_same_day.timestamp()
    store._save()
    assert tick_roe_expiry(store, later_same_day, _checker) == []
    assert job()["last_result"]["state"] == "expiring-7d"
    expected = (END - later_same_day).total_seconds() / 86400
    assert job()["last_result"]["days_remaining"] == pytest.approx(expected, abs=1e-6)

    # T-1d: fires expiring-1d exactly once.
    now = END - timedelta(days=1)
    fired = tick_roe_expiry(store, now, _checker)
    assert [j["id"] for j in fired] == [job_id]
    assert job()["last_result"]["state"] == "expiring-1d"
    assert job()["last_result"]["days_remaining"] == pytest.approx(1.0, abs=1e-6)

    # Post-window: fires expired exactly once per day.
    now = END + timedelta(seconds=1)
    fired = tick_roe_expiry(store, now, _checker)
    assert [j["id"] for j in fired] == [job_id]
    assert job()["last_result"]["state"] == "expired"
    assert job()["last_result"]["cause"] == "roe-expired"


def test_midnight_boundary_fires_independently(tmp_path):
    store = _store(tmp_path)
    artifact = _write_artifact(tmp_path)
    job_id = _schedule_due(store, artifact)
    first = datetime(2026, 5, 25, 23, 59, tzinfo=UTC)
    second = datetime(2026, 5, 26, 0, 1, tzinfo=UTC)
    assert [j["id"] for j in tick_roe_expiry(store, first, _checker)] == [job_id]
    job = next(j for j in store.jobs if j["id"] == job_id)
    job["due_at"] = second.timestamp()
    store._save()
    assert [j["id"] for j in tick_roe_expiry(store, second, _checker)] == [job_id]


def test_offset_effective_until_parses(tmp_path):
    artifact = _write_artifact(
        tmp_path,
        _document(effective_until="2026-06-01T14:00:00+02:00"),
    )
    result = check_roe_lifecycle(str(artifact), datetime(2026, 5, 24, 12, tzinfo=UTC))
    assert result["state"] == "valid"
    assert result["days_remaining"] == pytest.approx(8.0, abs=1e-6)


def test_history_capped_at_twenty(tmp_path):
    store = _store(tmp_path)
    artifact = _write_artifact(tmp_path)
    job_id = _schedule_due(store, artifact)
    base = END + timedelta(days=1)
    for step in range(25):
        now = base + timedelta(minutes=step)
        job = next(j for j in store.jobs if j["id"] == job_id)
        job["due_at"] = now.timestamp()
        store._save()
        tick_roe_expiry(store, now, _checker)
    job = next(j for j in store.jobs if j["id"] == job_id)
    assert len(job["history"]) == 20


def test_generic_tick_skips_watcher_kind(tmp_path):
    store = _store(tmp_path)
    artifact = _write_artifact(tmp_path)
    schedule_roe_expiry(store, str(artifact), interval_seconds=60, due_at=1000)
    plain_id = store.schedule("plain job", 1000)
    ran = store.tick(1000, lambda prompt: "ok", skip_kind=ROE_EXPIRY_KIND)
    assert [j["id"] for j in ran] == [plain_id]
    watcher = list_roe_expiries(store)[0]
    assert watcher["last_result"] is None
    assert watcher["due_at"] == 1000
    # The dedicated tick runs the watcher (window far in the future: silent).
    now = datetime.fromtimestamp(1000, tz=UTC)
    assert tick_roe_expiry(store, now, _checker) == []
    assert list_roe_expiries(store)[0]["last_result"]["state"] == "valid"


def test_missing_file_is_missing_not_expired(tmp_path):
    result = check_roe_lifecycle(str(tmp_path / "absent.json"), END)
    assert result["state"] == "missing"
    assert result["cause"] == "roe-missing"
    assert result["days_remaining"] is None
    assert result["effective_until"] is None
    result = check_roe_lifecycle("", END)
    assert result["state"] == "missing"


def test_garbage_json_is_invalid(tmp_path):
    artifact = tmp_path / "roe.json"
    artifact.write_text("{not json", encoding="utf-8")
    result = check_roe_lifecycle(str(artifact), END)
    assert result["state"] == "invalid"
    assert result["cause"] == "roe-invalid"


def test_schema_mismatch_is_invalid(tmp_path):
    document = _document()
    del document["operator"]
    result = check_roe_lifecycle(str(_write_artifact(tmp_path, document)), END)
    assert result["state"] == "invalid"
    assert result["cause"] == "roe-invalid"


def test_unparseable_date_is_invalid(tmp_path):
    result = check_roe_lifecycle(
        str(_write_artifact(tmp_path, _document(effective_until="someday"))), END
    )
    assert result["state"] == "invalid"


def test_clear_unknown_id_returns_error(tmp_path):
    store = _store(tmp_path)
    result = clear_roe_expiry(store, "nope")
    assert "error" in result


def test_clear_round_trip(tmp_path):
    store = _store(tmp_path)
    job_id = _schedule_due(store, _write_artifact(tmp_path))
    assert clear_roe_expiry(store, job_id) == {"cleared": job_id}
    assert list_roe_expiries(store) == []


def test_cli_json_matches_schema_all_states(tmp_path, capsys):
    artifact = _write_artifact(tmp_path)
    cases = [
        (END - timedelta(days=30), "valid", 0),
        (END - timedelta(days=7), "expiring-7d", 0),
        (END - timedelta(days=1), "expiring-1d", 0),
        (END + timedelta(seconds=1), "expired", 3),
    ]
    for now, state, code in cases:
        rc = main(
            [
                "check",
                "--artifact",
                str(artifact),
                "--now",
                now.isoformat(),
                "--json",
            ]
        )
        assert rc == code
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"state", "days_remaining", "cause", "effective_until"}
        assert payload["state"] == state
        # --now reproduces the checker verdict.
        assert payload == check_roe_lifecycle(str(artifact), now)

    rc = main(["check", "--artifact", str(tmp_path / "absent.json"), "--json"])
    assert rc == 3
    assert json.loads(capsys.readouterr().out)["state"] == "missing"

    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    rc = main(["check", "--artifact", str(bad), "--json"])
    assert rc == 3
    assert json.loads(capsys.readouterr().out)["state"] == "invalid"


def test_cli_human_line_and_usage_error(tmp_path, capsys):
    artifact = _write_artifact(tmp_path)
    # The CLI defaults to the wall clock, so pin --now inside the window.
    rc = main(
        [
            "check",
            "--artifact",
            str(artifact),
            "--now",
            (END - timedelta(days=30)).isoformat(),
        ]
    )
    assert rc == 0
    assert "roe: valid" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["check"])
    assert exc.value.code == 2


def test_durable_status_gains_roe_block(tmp_path, monkeypatch):
    for var in (
        "OMEGA_PRIME_REDTEAM_ROE_FILE",
        "OMEGA_PRIME_REDTEAM_ENABLED",
        "OMEGA_PRIME_REDTEAM_ROE_REQUIRED",
    ):
        monkeypatch.delenv(var, raising=False)
    # durable_status reads the wall clock, so the fixture window is relative
    # to now (a fixed 2026-06-01 window would read expired after that date).
    live = datetime.now(UTC)
    artifact = _write_artifact(
        tmp_path,
        _document(
            effective_from=(live - timedelta(days=1)).isoformat(timespec="seconds"),
            effective_until=(live + timedelta(days=30)).isoformat(timespec="seconds"),
        ),
    )
    (tmp_path / "omega-prime.json").write_text(
        json.dumps({"redteam": {"roe_file": str(artifact)}}), encoding="utf-8"
    )
    registry = ToolRegistry()
    register_durable_surface_tools(registry, tmp_path)
    body = json.loads(registry.dispatch("durable_status", {}))
    # Old keys unchanged, roe block additive.
    assert body["journal"] == "absent"
    for key in (
        "journal",
        "path",
        "open_runs",
        "entry_count",
        "last_run_id",
        "workflow_checkpoints",
    ):
        assert key in body
    assert set(body["roe"]) == {"state", "days_remaining", "effective_until"}
    assert body["roe"]["state"] == "valid"

    # No config: artifact absent ⇒ missing.
    registry = ToolRegistry()
    register_durable_surface_tools(registry, tmp_path / "empty-root")
    body = json.loads(registry.dispatch("durable_status", {}))
    assert body["roe"]["state"] == "missing"


def _write_named_artifact(tmp_path, name, document=None):
    artifact = tmp_path / name
    artifact.write_text(
        json.dumps(document if document is not None else _document()),
        encoding="utf-8",
    )
    return artifact


def test_lifecycle_all_reports_worst_state(tmp_path):
    """H7 happy: valid + expiring + expired grants aggregate to expired."""
    from omega_prime.grokbot.roe_lifecycle import check_roe_lifecycle_all

    healthy = _write_named_artifact(
        tmp_path,
        "roe-healthy.json",
        _document(effective_until=(END + timedelta(days=30)).isoformat()),
    )
    expiring = _write_named_artifact(
        tmp_path,
        "roe-expiring.json",
        _document(effective_until=(END + timedelta(hours=12)).isoformat()),
    )
    stale = _write_named_artifact(
        tmp_path,
        "roe-stale.json",
        _document(effective_until=(END - timedelta(days=1)).isoformat()),
    )
    result = check_roe_lifecycle_all([healthy, expiring, stale], END)
    assert result["state"] == "expired"
    assert result["cause"] == "roe-expired"
    assert [row["path"] for row in result["artifacts"]] == [
        str(healthy),
        str(expiring),
        str(stale),
    ]
    assert [row["state"] for row in result["artifacts"]] == [
        "valid",
        "expiring-1d",
        "expired",
    ]
    # Worst remaining window drives the aggregate countdown.
    assert result["days_remaining"] == pytest.approx(-1.0, abs=1e-6)


def test_lifecycle_all_single_matches_single(tmp_path):
    """H7 compat: one artifact aggregates to its own lifecycle state."""
    from omega_prime.grokbot.roe_lifecycle import (
        check_roe_lifecycle,
        check_roe_lifecycle_all,
    )

    artifact = _write_named_artifact(tmp_path, "roe.json")
    single = check_roe_lifecycle(artifact, END)
    aggregate = check_roe_lifecycle_all([artifact], END)
    assert aggregate["state"] == single["state"] == "expiring-1d"
    assert aggregate["days_remaining"] == single["days_remaining"]
    assert aggregate["artifacts"] == [{"path": str(artifact), **single}]
    empty = check_roe_lifecycle_all([], END)
    assert empty["state"] == "missing"
    assert empty["artifacts"] == []


def test_cli_multi_artifact_reports_worst(tmp_path, capsys):
    """H7 CLI: repeated --artifact prints the worst-of summary plus rows."""
    healthy = _write_named_artifact(
        tmp_path,
        "roe-healthy.json",
        _document(effective_until=(END + timedelta(days=30)).isoformat()),
    )
    stale = _write_named_artifact(
        tmp_path,
        "roe-stale.json",
        _document(effective_until=(END - timedelta(days=1)).isoformat()),
    )
    rc = main(
        [
            "check",
            "--artifact",
            str(healthy),
            "--artifact",
            str(stale),
            "--now",
            END.isoformat(),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 3
    assert "roe: expired (worst of 2 artifacts" in out
    assert f"  - {healthy}: valid" in out
    assert f"  - {stale}: expired" in out


# Readiness check (batch-04 Feature H2) --------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


def _open_listener():
    """Bound loopback TCP listener; caller must close."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv


def _closed_port():
    """An ephemeral loopback port with nothing listening (probe must fail)."""
    srv = _open_listener()
    port = srv.getsockname()[1]
    srv.close()
    return port


def test_readiness_keys_and_ready_when_open(tmp_path):
    """H2 happy: open window + reachable gateway + loaded suites ⇒ ready."""
    artifact = _write_artifact(tmp_path)
    srv = _open_listener()
    try:
        port = srv.getsockname()[1]
        result = check_readiness(
            str(artifact),
            END - timedelta(days=30),
            gateway_url=f"http://127.0.0.1:{port}",
        )
    finally:
        srv.close()
    assert set(result) == {
        "roe_state",
        "gateway_reachable",
        "suites_loaded",
        "suites_total",
        "ready_for_live",
    }
    assert result["roe_state"] == "IDLE"
    assert result["gateway_reachable"] is True
    assert result["suites_loaded"] > 0
    assert result["suites_loaded"] == result["suites_total"]
    assert result["ready_for_live"] is True


def test_readiness_blocked_states_never_ready(tmp_path):
    """H2: expired/missing/invalid artifacts report BLOCKED, never ready."""
    port = _closed_port()
    url = f"http://127.0.0.1:{port}"
    stale = _write_artifact(
        tmp_path, _document(effective_until=(END - timedelta(days=1)).isoformat())
    )
    assert check_readiness(str(stale), END, gateway_url=url)["roe_state"] == "BLOCKED"
    missing = check_readiness(str(tmp_path / "absent.json"), END, gateway_url=url)
    assert missing["roe_state"] == "BLOCKED"
    assert missing["ready_for_live"] is False
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert check_readiness(str(bad), END, gateway_url=url)["roe_state"] == "BLOCKED"


def test_readiness_unreachable_gateway_not_ready(tmp_path):
    """H2: open window but closed gateway port ⇒ not ready for live."""
    artifact = _write_artifact(tmp_path)
    result = check_readiness(
        str(artifact),
        END - timedelta(days=30),
        gateway_url=f"http://127.0.0.1:{_closed_port()}",
    )
    assert result["roe_state"] == "IDLE"
    assert result["gateway_reachable"] is False
    assert result["ready_for_live"] is False


def test_readiness_expiring_window_stays_idle(tmp_path):
    """H2: expiring windows are still authorized (harness IDLE semantics)."""
    artifact = _write_artifact(tmp_path)
    result = check_readiness(
        str(artifact),
        END - timedelta(days=1),
        gateway_url=f"http://127.0.0.1:{_closed_port()}",
    )
    assert result["roe_state"] == "IDLE"
    assert result["ready_for_live"] is False  # gateway closed


def test_readiness_suite_counts_exclude_drafts(tmp_path):
    """H2: stray draft copies in the cases dir count toward neither total."""
    cases = tmp_path / "cases"
    cases.mkdir()
    body = (REPO_ROOT / "sidecar" / "cases" / "abuse-01.yaml").read_text(
        encoding="utf-8"
    )
    (cases / "abuse-01.yaml").write_text(body, encoding="utf-8")
    (cases / "draft-copy.yaml").write_text(
        body + "\nstatus: draft\n",
        encoding="utf-8",
    )
    loaded, total = suite_counts(cases)
    assert (loaded, total) == (1, 1)


def test_readiness_malformed_url_never_reachable():
    """H2: a gateway URL with no host probes False instead of raising."""
    assert gateway_reachable("not-a-url") is False
    assert gateway_reachable("") is False


def test_readiness_writes_nothing(tmp_path, monkeypatch):
    """H2: the check never starts an engagement — no state files appear."""
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(reports))
    artifact = _write_artifact(tmp_path)
    check_readiness(
        str(artifact),
        END - timedelta(days=30),
        gateway_url=f"http://127.0.0.1:{_closed_port()}",
    )
    assert list(reports.iterdir()) == []


def test_readiness_cli_json_and_exit_codes(tmp_path, capsys):
    """H2 CLI: JSON payload with five keys; 0 when ready, 3 otherwise."""
    artifact = _write_artifact(tmp_path)
    now = (END - timedelta(days=30)).isoformat()
    srv = _open_listener()
    try:
        port = srv.getsockname()[1]
        rc = main(
            [
                "readiness",
                "--artifact",
                str(artifact),
                "--now",
                now,
                "--gateway-url",
                f"http://127.0.0.1:{port}",
            ]
        )
    finally:
        srv.close()
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["roe_state"] == "IDLE"
    assert payload["ready_for_live"] is True

    rc = main(
        [
            "readiness",
            "--artifact",
            str(artifact),
            "--now",
            now,
            "--gateway-url",
            f"http://127.0.0.1:{_closed_port()}",
        ]
    )
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready_for_live"] is False

    rc = main(
        [
            "readiness",
            "--artifact",
            str(tmp_path / "absent.json"),
            "--gateway-url",
            f"http://127.0.0.1:{_closed_port()}",
        ]
    )
    assert rc == 3
    assert json.loads(capsys.readouterr().out)["roe_state"] == "BLOCKED"


# G11 renewal reminders -------------------------------------------------------


@pytest.fixture
def fresh_renewals():
    """Isolate the in-memory per-threshold fire record per G11 test."""
    clear_renewal_reminders()
    yield
    clear_renewal_reminders()


def _renewal_document(days_left, operator="g11-operator"):
    """RoE document whose window ends ``days_left`` after END."""
    until = (END + timedelta(days=days_left)).isoformat(timespec="seconds")
    return _document(operator=operator, effective_until=until)


def test_renewal_thresholds_match_spec(fresh_renewals):
    """G11: reminder thresholds are T-14d/T-7d/T-3d/T-1d."""
    assert RENEWAL_THRESHOLDS_DAYS == (14.0, 7.0, 3.0, 1.0)


def test_renewal_fires_widest_threshold_only(fresh_renewals):
    """G11: 10 days left fires 14d alone."""
    reminders = renewal_reminders(_renewal_document(10), now=END)
    assert [r["threshold"] for r in reminders] == ["14d"]
    assert reminders[0]["threshold_days"] == 14.0
    assert reminders[0]["days_remaining"] == pytest.approx(10.0, abs=1e-6)
    assert "14d" in reminders[0]["detail"]


def test_renewal_fires_each_threshold_once(fresh_renewals):
    """G11: each (artifact, threshold) fires once; repeats are silent."""
    document = _renewal_document(10)
    assert len(renewal_reminders(document, now=END)) == 1
    assert renewal_reminders(document, now=END) == []
    # A nearer clock crosses 7d/3d/1d; 14d stays silent (already fired).
    later = END + timedelta(days=9, hours=12)
    labels = [r["threshold"] for r in renewal_reminders(document, now=later)]
    assert labels == ["7d", "3d", "1d"]
    assert renewal_reminders(document, now=later) == []


def test_renewal_imminent_fires_all_thresholds(fresh_renewals):
    """G11: half a day left fires every threshold at once."""
    reminders = renewal_reminders(_renewal_document(0.5), now=END)
    assert [r["threshold"] for r in reminders] == ["14d", "7d", "3d", "1d"]


def test_renewal_silent_when_far_or_expired(fresh_renewals):
    """G11: healthy windows and expired grants emit nothing."""
    assert renewal_reminders(_renewal_document(30), now=END) == []
    assert renewal_reminders(_renewal_document(-1), now=END) == []


def test_renewal_fail_closed(fresh_renewals):
    """G11: missing/malformed effective_until means no reminders, no crash."""
    document = _document()
    del document["effective_until"]
    assert renewal_reminders(document, now=END) == []
    assert renewal_reminders(_document(effective_until="not-a-date"), now=END) == []
    assert renewal_reminders(_document(effective_until=None), now=END) == []
    assert renewal_reminders(None, now=END) == []
    assert renewal_reminders("   ", now=END) == []
    assert renewal_reminders(["not", "a", "dict"], now=END) == []


def test_renewal_accepts_artifact_path(tmp_path, fresh_renewals):
    """G11: a file path artifact resolves like an inline document."""
    artifact = _write_artifact(tmp_path, _renewal_document(2))
    reminders = renewal_reminders(str(artifact), now=END)
    assert [r["threshold"] for r in reminders] == ["14d", "7d", "3d"]
    assert renewal_reminders(str(artifact), now=END) == []
    assert renewal_reminders(str(tmp_path / "absent.json"), now=END) == []


def test_heartbeat_digest_emits_roe_renewal_kind(tmp_path, fresh_renewals):
    """G11: a heartbeat carrying a RoE artifact digests roe_renewal entries."""
    store = _store(tmp_path)
    job_id = schedule_heartbeat(
        store, "sess-G11", "ping", interval_seconds=60, due_at=1000
    )
    job = next(j for j in store.jobs if j["id"] == job_id)
    job["context"] = {"roe_artifact": _renewal_document(2)}
    store._save()
    ran = tick_heartbeats(store, 1000, lambda s, p: "ok", roe_now=END)
    assert [j["id"] for j in ran] == [job_id]
    digest = job["roe_renewal_digest"]
    assert [e["kind"] for e in digest] == [ROE_RENEWAL_KIND] * 3
    assert [e["threshold"] for e in digest] == ["14d", "7d", "3d"]
    assert all(e["threshold"] in e["detail"] for e in digest)
    # Per-threshold dedupe: the next tick adds nothing new.
    tick_heartbeats(store, 1060, lambda s, p: "ok", roe_now=END)
    assert len(job["roe_renewal_digest"]) == 3


def test_heartbeat_without_artifact_unchanged(tmp_path, fresh_renewals):
    """G11: heartbeats without RoE context gain no digest key at all."""
    store = _store(tmp_path)
    job_id = schedule_heartbeat(
        store, "sess-plain", "ping", interval_seconds=60, due_at=1000
    )
    ran = tick_heartbeats(store, 1000, lambda s, p: "ran", roe_now=END)
    assert ran[0]["last_result"] == "ran"
    assert ran[0]["kind"] == HEARTBEAT_KIND
    assert "roe_renewal_digest" not in ran[0]
    assert heartbeat_roe_renewals({"kind": HEARTBEAT_KIND}) == []
    assert heartbeat_roe_renewals(None) == []


def test_heartbeat_renewal_fail_closed(tmp_path, fresh_renewals):
    """G11: a malformed RoE context never breaks the heartbeat tick."""
    store = _store(tmp_path)
    job_id = schedule_heartbeat(
        store, "sess-bad", "ping", interval_seconds=60, due_at=1000
    )
    job = next(j for j in store.jobs if j["id"] == job_id)
    job["roe_artifact"] = {"operator": "bad", "effective_until": "nope"}
    store._save()
    ran = tick_heartbeats(store, 1000, lambda s, p: "ran", roe_now=END)
    assert ran[0]["last_result"] == "ran"
    assert "roe_renewal_digest" not in ran[0]


# I11 Graph-delivered renewal reminders ----------------------------------------


class _FakeGraphRunner:
    """Fake GhRunner answering the token fetch + chatMessage POSTs."""

    def __init__(self, fail_token=False):
        self.calls: list = []
        self.token = "fake-access-token"
        self.fail_token = fail_token

    def __call__(self, argv, data):
        self.calls.append((list(argv), dict(data)))
        if argv[2].startswith("https://login.microsoftonline.com/"):
            if self.fail_token:
                return SimpleNamespace(returncode=1, stdout="", stderr="denied")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"access_token": self.token, "expires_in": 3600}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


_GRAPH_CREDS = {
    "tenant_id": "tenant-123",
    "client_id": "client-123",
    "client_secret": "secret-123",
    "team_id": "team-123",
    "channel_id": "channel-123",
}


def test_heartbeat_renewal_graph_delivers_and_digests(fresh_renewals):
    """I11: a graph-configured job posts each reminder and still digests."""
    fake = _FakeGraphRunner()
    job = {
        "context": {"roe_artifact": _renewal_document(2), "graph": dict(_GRAPH_CREDS)}
    }
    entries = heartbeat_roe_renewals(job, now=END, runner=fake)
    assert [e["threshold"] for e in entries] == ["14d", "7d", "3d"]
    assert len(fake.calls) == 4  # one token fetch + three message POSTs
    assert fake.calls[0][0][2].startswith("https://login.microsoftonline.com/")
    contents = []
    for argv, data in fake.calls[1:]:
        assert argv[2].startswith("https://graph.microsoft.com/v1.0/teams/")
        assert f"Authorization: Bearer {fake.token}" in argv
        assert data["body"]["contentType"] == "text"
        contents.append(data["body"]["content"])
    assert all("g11-operator" in content for content in contents)
    for label in ("14d", "7d", "3d"):
        assert any(label in content for content in contents)


def test_heartbeat_renewal_graph_absent_stays_digest_only(fresh_renewals):
    """I11: no graph config means digest-only; the runner is untouched."""
    fake = _FakeGraphRunner()
    entries = heartbeat_roe_renewals(
        {"context": {"roe_artifact": _renewal_document(2)}}, now=END, runner=fake
    )
    assert [e["threshold"] for e in entries] == ["14d", "7d", "3d"]
    assert fake.calls == []


def test_heartbeat_renewal_graph_incomplete_stays_digest_only(fresh_renewals):
    """I11: a partial graph mapping never delivers and never raises."""
    creds = dict(_GRAPH_CREDS)
    del creds["client_secret"]
    fake = _FakeGraphRunner()
    entries = heartbeat_roe_renewals(
        {"context": {"roe_artifact": _renewal_document(2), "graph": creds}},
        now=END,
        runner=fake,
    )
    assert [e["threshold"] for e in entries] == ["14d", "7d", "3d"]
    assert fake.calls == []


def test_heartbeat_renewal_graph_failure_still_digests(fresh_renewals):
    """I11: a delivery failure still returns the digest and never raises."""
    fake = _FakeGraphRunner(fail_token=True)
    entries = heartbeat_roe_renewals(
        {
            "context": {
                "roe_artifact": _renewal_document(2),
                "graph": dict(_GRAPH_CREDS),
            }
        },
        now=END,
        runner=fake,
    )
    assert [e["threshold"] for e in entries] == ["14d", "7d", "3d"]


# J11 RoE --revoke CLI + reminder suppression -------------------------------


def _revoked_log(tmp_path, artifact_id):
    """Audit log holding a single grant-revoked row for ``artifact_id``."""
    from omega_prime.redteam import roe_audit

    log = tmp_path / "roe-audit.jsonl"
    roe_audit.log_revoked(log, artifact_id, "test-operator", "key compromised")
    return log


def test_revoke_cli_removes_sig_and_audits(tmp_path, capsys):
    """J11: revoke removes the .sig, appends grant-revoked, exits 0."""
    from omega_prime.redteam import roe_audit

    artifact = _write_artifact(tmp_path, _renewal_document(2))
    sig = Path(str(artifact) + ".sig")
    sig.write_text("deadbeef\n", encoding="utf-8")
    log = tmp_path / "roe-audit.jsonl"
    code = main(
        [
            "revoke",
            "--revoke",
            str(artifact),
            "--reason",
            "key compromised",
            "--audit-log",
            str(log),
        ]
    )
    assert code == 0
    assert not sig.exists()
    assert "revoked" in capsys.readouterr().out
    rows = roe_audit.read_log(log)
    assert [r["event"] for r in rows] == [roe_audit.EVENT_REVOKED]
    assert rows[0]["artifact_path"] == str(artifact)
    assert rows[0]["detail"] == "key compromised"


def test_revoke_cli_usage_error(tmp_path, capsys):
    """J11: a missing --reason (or blank pair) is usage error exit 2."""
    with pytest.raises(SystemExit) as exc:
        main(["revoke", "--revoke", str(tmp_path / "roe.json")])
    assert exc.value.code == 2
    assert main(["revoke", "--revoke", " ", "--reason", " "]) == 2
    capsys.readouterr()


def test_renewal_suppressed_when_revoked(tmp_path, fresh_renewals):
    """J11: a revoked grant yields one roe_revoked marker, then silence."""
    document = _renewal_document(2)
    document["artifact_id"] = "j11-grant"
    log = _revoked_log(tmp_path, "j11-grant")
    marker = renewal_reminders(document, now=END, audit_log=log)
    assert len(marker) == 1
    assert marker[0]["kind"] == ROE_REVOKED_KIND == "roe_revoked"
    assert marker[0]["artifact_id"] == "j11-grant"
    assert "revoked" in marker[0]["detail"]
    assert renewal_reminders(document, now=END, audit_log=log) == []


def test_renewal_no_suppression_without_log(tmp_path, fresh_renewals):
    """J11: revocation elsewhere never suppresses; unreadable log is safe."""
    document = _renewal_document(2)
    document["artifact_id"] = "j11-grant"
    _revoked_log(tmp_path, "j11-grant")
    assert [r["threshold"] for r in renewal_reminders(document, now=END)] == [
        "14d",
        "7d",
        "3d",
    ]
    assert [
        r["threshold"]
        for r in renewal_reminders(
            _renewal_document(2), now=END, audit_log=tmp_path / "absent.jsonl"
        )
    ] == ["14d", "7d", "3d"]


def test_revoke_cli_suppresses_path_reminders(tmp_path, fresh_renewals, capsys):
    """J11 end-to-end: revoke a file grant, its path reminders go silent."""
    artifact = _write_artifact(tmp_path, _renewal_document(2))
    Path(str(artifact) + ".sig").write_text("deadbeef\n", encoding="utf-8")
    log = tmp_path / "roe-audit.jsonl"
    assert (
        main(
            [
                "revoke",
                "--revoke",
                str(artifact),
                "--reason",
                "rotation",
                "--audit-log",
                str(log),
            ]
        )
        == 0
    )
    capsys.readouterr()
    marker = renewal_reminders(str(artifact), now=END, audit_log=log)
    assert [m["kind"] for m in marker] == [ROE_REVOKED_KIND]
    assert renewal_reminders(str(artifact), now=END, audit_log=log) == []


def test_heartbeat_revoked_marker_digest_only(tmp_path, fresh_renewals):
    """J11: heartbeats digest one roe_revoked entry; Graph stays silent."""
    document = _renewal_document(2)
    document["artifact_id"] = "j11-grant"
    log = _revoked_log(tmp_path, "j11-grant")
    job = {"context": {"roe_artifact": document, "roe_audit_log": str(log)}}
    fake = _FakeGraphRunner()
    entries = heartbeat_roe_renewals(job, now=END, runner=fake, audit_log=str(log))
    assert [e["kind"] for e in entries] == [ROE_REVOKED_KIND]
    assert "revoked" in entries[0]["detail"]
    assert fake.calls == []
    assert heartbeat_roe_renewals(job, now=END, audit_log=str(log)) == []


# K11 RoE expiry watchdog ------------------------------------------------------


def _watchdog_document(days_left):
    """RoE document whose window ends ``days_left`` after END."""
    until = (END + timedelta(days=days_left)).isoformat(timespec="seconds")
    return _document(effective_until=until)


def test_watchdog_states_reuse_lifecycle_vocabulary(tmp_path):
    """K11: watchdog states reuse STATES; actions name the operator step."""
    artifact = _write_artifact(tmp_path, _watchdog_document(30))
    assert watchdog_check(str(artifact), now=END)["state"] == "valid"
    artifact.write_text(json.dumps(_watchdog_document(5)), encoding="utf-8")
    assert watchdog_check(str(artifact), now=END)["state"] == "expiring-7d"
    artifact.write_text(json.dumps(_watchdog_document(0.5)), encoding="utf-8")
    assert watchdog_check(str(artifact), now=END)["state"] == "expiring-1d"
    artifact.write_text(json.dumps(_watchdog_document(-1)), encoding="utf-8")
    result = watchdog_check(str(artifact), now=END)
    assert result["state"] == "expired"
    assert result["action"] == "archive"
    for result_state in (
        watchdog_check(str(artifact), now=END)["state"],
        watchdog_check(None, now=END)["state"],
    ):
        assert result_state in STATES


def test_watchdog_actions_per_state(tmp_path):
    """K11: each state maps to its required operator step."""
    assert watchdog_check(_watchdog_document(30), now=END)["action"] == "none"
    assert watchdog_check(_watchdog_document(5), now=END)["action"] == "re-sign"
    assert watchdog_check(_watchdog_document(0.5), now=END)["action"] == "re-sign"
    assert watchdog_check(_watchdog_document(-2), now=END)["action"] == "archive"
    assert (
        watchdog_check(str(tmp_path / "absent.json"), now=END)["action"]
        == "provide artifact"
    )
    assert WATCHDOG_ACTIONS["missing"] == "provide artifact"
    assert WATCHDOG_ACTIONS["invalid"] == "fix artifact"


def test_watchdog_reports_ids_and_until(tmp_path):
    """K11: watchdog returns artifact_id + effective_until for triage."""
    document = _watchdog_document(5)
    result = watchdog_check(document, now=END)
    assert result["artifact_id"]
    assert result["effective_until"] == document["effective_until"]
    artifact = _write_artifact(tmp_path, document)
    path_result = watchdog_check(str(artifact), now=END)
    assert path_result["artifact_id"] == str(artifact)
    assert path_result["effective_until"] == document["effective_until"]
    assert set(path_result) >= {
        "state",
        "artifact_id",
        "effective_until",
        "action",
    }


def test_watchdog_fail_closed(tmp_path):
    """K11: missing/invalid/garbage inputs never raise; missing is missing."""
    assert watchdog_check(None, now=END)["state"] == "missing"
    assert watchdog_check("   ", now=END)["state"] == "missing"
    assert watchdog_check("   ", now=END)["action"] == "provide artifact"
    assert watchdog_check(str(tmp_path / "absent.json"), now=END)["state"] == (
        "missing"
    )
    garbage = tmp_path / "roe.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert watchdog_check(str(garbage), now=END)["state"] == "invalid"
    assert watchdog_check(str(garbage), now=END)["action"] == "fix artifact"
    assert watchdog_check({"no": "window"}, now=END)["state"] == "invalid"
    assert watchdog_check(12345, now=END)["state"] == "invalid"
    assert watchdog_check(["not", "a", "dict"], now=END)["state"] == "invalid"


def test_watchdog_defaults_to_wall_clock():
    """K11: omitted now reads the wall clock instead of raising."""
    far = (datetime.now(UTC) + timedelta(days=60)).isoformat(timespec="seconds")
    result = watchdog_check(_document(effective_until=far))
    assert result["state"] == "valid"
    assert result["action"] == "none"


def test_watchdog_kind_distinct_from_existing_kinds():
    """K11: the watchdog kind is new; existing kinds are unchanged."""
    assert ROE_WATCHDOG_KIND == "roe_watchdog"
    assert ROE_WATCHDOG_KIND not in {HEARTBEAT_KIND, ROE_EXPIRY_KIND}
    assert ROE_WATCHDOG_KIND != "desk_lead_pass"


def test_schedule_watchdog_marks_kind_and_validates(tmp_path):
    """K11: schedule stamps kind + path; bad inputs raise ValueError."""
    store = JobStore(tmp_path / "cron" / "jobs.json")
    artifact = _write_artifact(tmp_path, _watchdog_document(30))
    job_id = schedule_roe_watchdog(store, str(artifact), due_at=0)
    jobs = list_roe_watchdogs(store)
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["kind"] == ROE_WATCHDOG_KIND
    assert jobs[0]["artifact_path"] == str(artifact)
    assert len(list_roe_watchdogs(JobStore(tmp_path / "cron" / "jobs.json"))) == 1
    with pytest.raises(ValueError, match="artifact_path"):
        schedule_roe_watchdog(store, "   ", due_at=0)
    with pytest.raises(ValueError, match="positive interval"):
        schedule_roe_watchdog(store, str(artifact), due_at=0, interval_seconds=0)


def test_clear_watchdog_round_trip(tmp_path):
    """K11: clear removes one watchdog; unknown id is a structured error."""
    store = JobStore(tmp_path / "cron" / "jobs.json")
    artifact = _write_artifact(tmp_path, _watchdog_document(30))
    job_id = schedule_roe_watchdog(store, str(artifact), due_at=0)
    assert clear_roe_watchdog(store, "nope")["error"]
    assert clear_roe_watchdog(store, job_id) == {"cleared": job_id}
    assert list_roe_watchdogs(store) == []


def test_watchdog_tick_persists_transitions_without_heartbeat(tmp_path):
    """K11: each tick runs watchdog_check; valid->expiring->expired digests."""
    store = JobStore(tmp_path / "cron" / "jobs.json")
    artifact = _write_artifact(tmp_path, _watchdog_document(30))
    job_id = schedule_roe_watchdog(
        store, str(artifact), due_at=0, interval_seconds=86400
    )

    def job():
        return next(j for j in store.jobs if j["id"] == job_id)

    first = END
    ran = tick_roe_watchdog(store, first)
    assert [j["id"] for j in ran] == [job_id]
    assert job()["last_result"]["state"] == "valid"
    assert job()["roe_watchdog_state"] == "valid"
    assert len(job()["roe_watchdog_digest"]) == 1

    second = END + timedelta(days=26)
    tick_roe_watchdog(store, second)
    assert job()["last_result"]["state"] == "expiring-7d"
    assert job()["last_result"]["action"] == "re-sign"
    assert [e["to"] for e in job()["roe_watchdog_digest"]] == [
        "valid",
        "expiring-7d",
    ]

    third = END + timedelta(days=32)
    tick_roe_watchdog(store, third)
    assert job()["last_result"]["state"] == "expired"
    assert [e["to"] for e in job()["roe_watchdog_digest"]] == [
        "valid",
        "expiring-7d",
        "expired",
    ]
    assert job()["roe_watchdog_digest"][-1]["action"] == "archive"


def test_watchdog_tick_repeats_same_state_without_digest_growth(tmp_path):
    """K11: steady-state ticks update last_result but add no digest entry."""
    store = JobStore(tmp_path / "cron" / "jobs.json")
    artifact = _write_artifact(tmp_path, _watchdog_document(30))
    job_id = schedule_roe_watchdog(
        store, str(artifact), due_at=0, interval_seconds=86400
    )
    tick_roe_watchdog(store, END)
    before = list(
        next(j for j in store.jobs if j["id"] == job_id)["roe_watchdog_digest"]
    )
    tick_roe_watchdog(store, END + timedelta(hours=12))
    after = next(j for j in store.jobs if j["id"] == job_id)
    assert after["roe_watchdog_digest"] == before
    assert after["last_result"]["state"] == "valid"


def test_watchdog_tick_missing_artifact_is_fail_closed(tmp_path):
    """K11: a missing artifact ticks missing (provide artifact), never raises."""
    store = JobStore(tmp_path / "cron" / "jobs.json")
    job_id = schedule_roe_watchdog(
        store, str(tmp_path / "absent.json"), due_at=0, interval_seconds=60
    )
    ran = tick_roe_watchdog(store, END)
    assert [j["id"] for j in ran] == [job_id]
    result = next(j for j in store.jobs if j["id"] == job_id)["last_result"]
    assert result["state"] == "missing"
    assert result["action"] == "provide artifact"


def test_watchdog_tick_skips_not_due_and_generic_tick_respects_skip(tmp_path):
    """K11: future-due watchdogs wait; generic tick can leave them alone."""
    store = JobStore(tmp_path / "cron" / "jobs.json")
    artifact = _write_artifact(tmp_path, _watchdog_document(30))
    schedule_roe_watchdog(store, str(artifact), due_at=10**12)
    assert tick_roe_watchdog(store, END) == []
    assert store.tick(END.timestamp(), lambda prompt: "generic") == []
    skipped = store.tick(
        END.timestamp(), lambda prompt: "generic", skip_kind=ROE_WATCHDOG_KIND
    )
    assert skipped == []


# L11 multi-grant support -------------------------------------------------------


def test_watchdog_check_all_returns_per_artifact_states():
    """L11: one state dict per artifact, same shape as watchdog_check."""
    documents = [
        _watchdog_document(30),
        _watchdog_document(3),
        _watchdog_document(-1),
    ]
    results = watchdog_check_all(documents, now=END)
    assert [r["state"] for r in results] == ["valid", "expiring-7d", "expired"]
    assert [r["action"] for r in results] == ["none", "re-sign", "archive"]
    for result in results:
        assert set(result) >= {
            "state",
            "artifact_id",
            "effective_until",
            "action",
        }


def test_watchdog_check_all_accepts_paths_and_dicts(tmp_path):
    """L11: paths and inline dicts mix; order is preserved."""
    document = _watchdog_document(30)
    artifact = _write_named_artifact(tmp_path, "grant-a.json", document)
    results = watchdog_check_all([str(artifact), _watchdog_document(-1)], now=END)
    assert [r["state"] for r in results] == ["valid", "expired"]
    assert results[0]["artifact_id"] == str(artifact)
    assert results[0]["effective_until"] == document["effective_until"]


def test_watchdog_check_all_matches_single_checks():
    """L11: a list of one behaves exactly like the single-artifact call."""
    document = _watchdog_document(5)
    assert watchdog_check_all([document], now=END) == [
        watchdog_check(document, now=END)
    ]
    assert watchdog_check_all(document, now=END) == [watchdog_check(document, now=END)]


def test_watchdog_check_all_fail_closed(tmp_path):
    """L11: empty/None/garbage inputs never raise."""
    assert watchdog_check_all([], now=END) == []
    assert watchdog_check_all(None, now=END) == []
    assert watchdog_check_all((), now=END) == []
    results = watchdog_check_all([None, "   ", {"no": "window"}, 12345], now=END)
    assert [r["state"] for r in results] == [
        "missing",
        "missing",
        "invalid",
        "invalid",
    ]
    assert all(r["action"] for r in results)


def test_renewal_list_form_merges_across_grants(fresh_renewals):
    """L11: concurrent grants each remind; merged in artifact order."""
    first = _renewal_document(2, operator="op-a")
    second = _renewal_document(10, operator="op-b")
    reminders = renewal_reminders([first, second], now=END)
    assert [r["threshold"] for r in reminders] == ["14d", "7d", "3d", "14d"]
    assert reminders[0]["artifact_id"] != reminders[-1]["artifact_id"]
    assert all("re-sign to renew" in r["detail"] for r in reminders)


def test_renewal_single_item_list_matches_single(fresh_renewals):
    """L11: a one-grant list behaves exactly like the single call."""
    document = _renewal_document(2)
    single = renewal_reminders(document, now=END)
    assert single != []
    clear_renewal_reminders()
    assert renewal_reminders([document], now=END) == single


def test_renewal_list_dedupes_same_artifact(fresh_renewals):
    """L11: the same grant listed twice reminds once, not per check run."""
    document = _renewal_document(2)
    reminders = renewal_reminders([document, document], now=END)
    assert [r["threshold"] for r in reminders] == ["14d", "7d", "3d"]


def test_renewal_list_mixes_revoked_and_healthy(tmp_path, fresh_renewals):
    """L11: a revoked grant yields one marker; a healthy one still reminds."""
    revoked = _renewal_document(2, operator="op-revoked")
    revoked["artifact_id"] = "l11-revoked"
    log = _revoked_log(tmp_path, "l11-revoked")
    healthy = _renewal_document(10, operator="op-healthy")
    reminders = renewal_reminders([revoked, healthy], now=END, audit_log=log)
    assert reminders[0]["kind"] == ROE_REVOKED_KIND
    assert reminders[0]["artifact_id"] == "l11-revoked"
    assert [r["threshold"] for r in reminders[1:]] == ["14d"]
    assert renewal_reminders([revoked, healthy], now=END, audit_log=log) == []


def test_heartbeat_list_artifact_merges_digests(tmp_path, fresh_renewals):
    """L11: a heartbeat carrying several grants digests every reminder."""
    store = _store(tmp_path)
    job_id = schedule_heartbeat(
        store, "sess-L11", "ping", interval_seconds=60, due_at=1000
    )
    job = next(j for j in store.jobs if j["id"] == job_id)
    job["context"] = {
        "roe_artifact": [
            _renewal_document(2, operator="op-a"),
            _renewal_document(10, operator="op-b"),
        ]
    }
    store._save()
    ran = tick_heartbeats(store, 1000, lambda s, p: "ok", roe_now=END)
    digest = ran[0]["roe_renewal_digest"]
    assert [e["kind"] for e in digest] == [ROE_RENEWAL_KIND] * 4
    assert [e["threshold"] for e in digest] == ["14d", "7d", "3d", "14d"]


def test_heartbeat_plural_artifacts_key(tmp_path, fresh_renewals):
    """L11: the roe_artifacts key is read like a list-form roe_artifact."""
    job = {"context": {"roe_artifacts": [_renewal_document(10, operator="op-c")]}}
    entries = heartbeat_roe_renewals(job, now=END)
    assert [e["threshold"] for e in entries] == ["14d"]
    assert entries[0]["kind"] == ROE_RENEWAL_KIND


def test_heartbeat_single_item_list_matches_single(fresh_renewals):
    """L11: a one-grant heartbeat list digests like the single grant."""
    document = _renewal_document(10, operator="op-d")
    single = heartbeat_roe_renewals({"context": {"roe_artifact": document}}, now=END)
    assert single != []
    clear_renewal_reminders()
    listed = heartbeat_roe_renewals({"context": {"roe_artifact": [document]}}, now=END)
    assert listed == single


# M11 per-grant tracker allowlists ------------------------------------------------


def test_grant_trackers_reads_covers_list():
    """M11: a covers list scopes dispatch to exactly those trackers."""
    assert grant_trackers({"covers": ["slack", "jira"]}) == {"slack", "jira"}


def test_grant_trackers_missing_covers_allows_all():
    """M11: no covers key means every tracker is allowed (backward compatible)."""
    assert grant_trackers(_document()) is None
    assert grant_trackers({}) is None


def test_grant_trackers_empty_covers_allows_all():
    """M11: an empty covers list means every tracker is allowed."""
    assert grant_trackers({"covers": []}) is None
    assert grant_trackers({"covers": None}) is None


def test_grant_trackers_normalizes_names():
    """M11: covers match case-insensitively; blanks/non-strings are ignored."""
    assert grant_trackers({"covers": [" Slack ", "JIRA", "", 7, None]}) == {
        "slack",
        "jira",
    }


def test_grant_trackers_non_list_covers_allows_all():
    """M11: a malformed (non-list) covers value never scopes dispatch."""
    assert grant_trackers({"covers": "slack"}) is None
    assert grant_trackers({"covers": {"slack": True}}) is None


def test_grant_trackers_accepts_artifact_path(tmp_path):
    """M11: a file-path artifact resolves like an inline document."""
    artifact = tmp_path / "roe.json"
    artifact.write_text(json.dumps({"covers": ["github"]}), encoding="utf-8")
    assert grant_trackers(str(artifact)) == {"github"}
    assert grant_trackers(artifact) == {"github"}


def test_grant_trackers_unreadable_artifact_allows_all(tmp_path):
    """M11: missing/garbage/unreadable artifacts fail open, never raise."""
    assert grant_trackers(str(tmp_path / "absent.json")) is None
    garbage = tmp_path / "garbage.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert grant_trackers(str(garbage)) is None
    assert grant_trackers(None) is None
    assert grant_trackers(["not", "a", "dict"]) is None
    assert grant_trackers([]) is None


def test_grant_trackers_list_form_unions_grants():
    """M11: concurrent grants merge as a union — any cover allows."""
    assert grant_trackers([{"covers": ["slack"]}, {"covers": ["github"]}]) == {
        "slack",
        "github",
    }


def test_grant_trackers_list_with_open_grant_allows_all():
    """M11: one grant without covers opens dispatch for every tracker."""
    assert grant_trackers([{"covers": ["slack"]}, _document()]) is None


def test_grant_trackers_single_item_list_matches_single():
    """M11: a one-grant list behaves exactly like the single call."""
    document = {"covers": ["teams"]}
    assert grant_trackers([document]) == grant_trackers(document) == {"teams"}


# N11 grant usage ledger ---------------------------------------------------------


def test_record_grant_usage_row_shape_and_sort_keys(tmp_path):
    """N11: one JSONL row with ts/artifact_id/tracker/verdict, keys sorted."""
    ledger = tmp_path / "grant-usage.jsonl"
    record_grant_usage(ledger, artifact_id="grant-1", tracker="github", verdict="allow")
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0] == json.dumps(json.loads(lines[0]), sort_keys=True)
    row = json.loads(lines[0])
    assert set(row) == {"ts", "artifact_id", "tracker", "verdict"}
    assert row["artifact_id"] == "grant-1"
    assert row["tracker"] == "github"
    assert row["verdict"] == "allow"
    assert datetime.fromisoformat(row["ts"]).tzinfo is not None


def test_record_grant_usage_appends_rows(tmp_path):
    """N11: repeated calls append one row each, oldest first."""
    ledger = tmp_path / "grant-usage.jsonl"
    record_grant_usage(ledger, artifact_id="a", tracker="github", verdict="allow")
    record_grant_usage(ledger, artifact_id="a", tracker="slack", verdict="skip")
    rows = read_grant_usage(ledger)
    assert [r["tracker"] for r in rows] == ["github", "slack"]
    assert [r["verdict"] for r in rows] == ["allow", "skip"]


def test_record_grant_usage_never_raises(tmp_path):
    """N11: blank/unwritable/bad paths are silently skipped."""
    record_grant_usage("", artifact_id="a", tracker="github", verdict="allow")
    record_grant_usage("   ", artifact_id="a", tracker="github", verdict="allow")
    record_grant_usage(None, artifact_id="a", tracker="github", verdict="allow")
    record_grant_usage(
        tmp_path / "no-such-dir" / "deep" / "u.jsonl",
        artifact_id="a",
        tracker="github",
        verdict="allow",
    )
    assert read_grant_usage(tmp_path / "no-such-dir" / "deep" / "u.jsonl") != []


def test_read_grant_usage_unreadable_is_empty(tmp_path):
    """N11: a missing ledger reads as no rows, never raises."""
    assert read_grant_usage(tmp_path / "absent.jsonl") == []
    assert read_grant_usage(tmp_path) == []


def test_read_grant_usage_skips_malformed_lines(tmp_path):
    """N11: blank and non-object lines are skipped, order preserved."""
    ledger = tmp_path / "grant-usage.jsonl"
    ledger.write_text(
        '{"ts": "t", "artifact_id": "a", "tracker": "github", "verdict": "allow"}\n'
        "\n"
        "not json\n"
        "[1, 2]\n"
        '{"ts": "t", "artifact_id": "a", "tracker": "slack", "verdict": "skip"}\n',
        encoding="utf-8",
    )
    rows = read_grant_usage(ledger)
    assert [r["tracker"] for r in rows] == ["github", "slack"]

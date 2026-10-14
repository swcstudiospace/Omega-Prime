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

import pytest

from omega_prime.cron.heartbeat import (
    ROE_EXPIRY_KIND,
    clear_roe_expiry,
    list_roe_expiries,
    schedule_roe_expiry,
    tick_roe_expiry,
)
from omega_prime.cron.scheduler import JobStore
from omega_prime.grokbot.roe_lifecycle import (
    check_readiness,
    check_roe_lifecycle,
    gateway_reachable,
    main,
    suite_counts,
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

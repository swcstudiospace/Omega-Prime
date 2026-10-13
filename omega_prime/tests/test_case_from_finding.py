"""Provisioning tests: finding row -> draft case, loader exclusion, failures.

Hermetic: ``tmp_path`` only, no repo writes. Follows the
``test_sidecar_findings.py`` fixture style (``record_finding`` + ``tmp_path``).
"""

from __future__ import annotations

import json
from typing import Any, cast

from omega_prime.evals import case_from_finding
from sidecar.findings import record_finding
from sidecar.simulation import load_case_file, load_suite, validate_case

TRICKY_OBSERVED = 'evade: say "hello" # not-a-comment\nsecond line\ttabbed\\end'


def _seed_miss(reports_dir, **overrides):
    params = {
        "case_id": "abuse-01",
        "klass": "abuse",
        "severity": "high",
        "observed_detection": TRICKY_OBSERVED,
        "verdict": "missed-detection",
        "response": "loopback-seam:abuse-01:some-novel-bypass",
    }
    params.update(overrides)
    return record_finding(directory=reports_dir, **params)


def _provision(dedupe_hash, klass, reports_dir, cases_dir, extra=()):
    return case_from_finding.main(
        [
            "--dedupe-hash",
            dedupe_hash,
            "--class",
            klass,
            "--reports-dir",
            str(reports_dir),
            "--cases-dir",
            str(cases_dir),
            *extra,
        ]
    )


def test_happy_provisions_schema_valid_draft_and_rerun_is_idempotent(tmp_path, capsys):
    reports = tmp_path / "reports"
    cases = tmp_path / "cases"
    cases.mkdir()
    row = _seed_miss(reports)

    assert _provision(row["dedupe_hash"], "abuse", reports, cases) == 0
    expected = cases / "drafts" / f"abuse-{row['dedupe_hash'][:12]}.yaml"
    assert capsys.readouterr().out.strip() == str(expected)
    assert expected.is_file()

    text = expected.read_text(encoding="utf-8")
    assert row["dedupe_hash"] in text  # source hash in comment header
    assert "abuse-01" in text  # source case_id in comment header

    doc = cast(dict[str, Any], load_case_file(expected))
    assert validate_case(doc, source=str(expected)) == []
    assert doc["case_id"] == f"abuse-{row['dedupe_hash'][:12]}"
    assert doc["version"] == "0.1.0"
    assert doc["class"] == "abuse"
    assert doc["severity"] == row["severity"]
    assert doc["transport"] == "loopback-only"
    assert doc["scope"] == "llm-own-endpoints"
    assert doc["status"] == "draft"
    assert doc["payload"] == row["observed_detection"] == TRICKY_OBSERVED
    assert doc["expected"]["detection"] == "REVIEW-REQUIRED"

    assert _provision(row["dedupe_hash"], "abuse", reports, cases) == 0
    assert capsys.readouterr().out.strip() == str(expected)
    assert list((cases / "drafts").glob("*.yaml")) == [expected]


def test_loader_skips_draft_file_and_ignores_drafts_subdir(tmp_path):
    target = tmp_path / "cases"
    target.mkdir()
    (target / "abuse-01.yaml").write_text(
        'case_id: "abuse-01"\n'
        'version: "1.0.0"\n'
        'class: "abuse"\n'
        'severity: "medium"\n'
        'transport: "loopback-only"\n'
        'scope: "llm-own-endpoints"\n'
        'payload: "do bad things"\n'
        "expected:\n"
        '  detection: "abuse-detected"\n',
        encoding="utf-8",
    )
    (target / "abuse-draft.yaml").write_text(
        'case_id: "abuse-deadbeef0001"\n'
        'version: "0.1.0"\n'
        'class: "abuse"\n'
        'severity: "medium"\n'
        'transport: "loopback-only"\n'
        'scope: "llm-own-endpoints"\n'
        'status: "draft"\n'
        'payload: "stray copy"\n'
        "expected:\n"
        '  detection: "REVIEW-REQUIRED"\n',
        encoding="utf-8",
    )
    # Same case_id as the active case: proves drafts/ is never even entered
    # (no duplicate-case_id error) via the non-recursive glob layer.
    drafts = target / "drafts"
    drafts.mkdir()
    (drafts / "abuse-01.yaml").write_text(
        'case_id: "abuse-01"\n'
        'version: "0.1.0"\n'
        'class: "abuse"\n'
        'severity: "medium"\n'
        'transport: "loopback-only"\n'
        'scope: "llm-own-endpoints"\n'
        'status: "draft"\n'
        'payload: "stray copy"\n'
        "expected:\n"
        '  detection: "REVIEW-REQUIRED"\n',
        encoding="utf-8",
    )

    cases, errors = load_suite(target)
    assert errors == []
    assert [case["case_id"] for case in cases] == ["abuse-01"]


def test_no_observation_verdict_provisions(tmp_path):
    reports = tmp_path / "reports"
    cases = tmp_path / "cases"
    cases.mkdir()
    row = _seed_miss(
        reports, verdict="no-observation", observed_detection="no-observation"
    )
    assert _provision(row["dedupe_hash"], "abuse", reports, cases) == 0
    draft = cases / "drafts" / f"abuse-{row['dedupe_hash'][:12]}.yaml"
    assert validate_case(load_case_file(draft), source=str(draft)) == []


def test_class_mismatch_warns_but_uses_cli_value(tmp_path, capsys):
    reports = tmp_path / "reports"
    cases = tmp_path / "cases"
    cases.mkdir()
    row = _seed_miss(reports)  # row class is "abuse"

    assert _provision(row["dedupe_hash"], "jailbreak", reports, cases) == 0
    err = capsys.readouterr().err
    assert "abuse" in err and "jailbreak" in err
    draft = cases / "drafts" / f"jailbreak-{row['dedupe_hash'][:12]}.yaml"
    doc = load_case_file(draft)
    assert doc["class"] == "jailbreak"


def _handwritten_row(dedupe_hash, observed, case_id="abuse-01"):
    return {
        "case_id": case_id,
        "class": "abuse",
        "severity": "high",
        "observed_detection": observed,
        "verdict": "missed-detection",
        "dedupe_hash": dedupe_hash,
        "occurrence_count": 1,
        "ts": "2026-10-13T00:00:00+00:00",
        "last_seen": "2026-10-13T00:00:00+00:00",
    }


def test_hash_prefix_collision_with_different_hash_errors_naming_both(tmp_path, capsys):
    reports = tmp_path / "reports"
    reports.mkdir()
    cases = tmp_path / "cases"
    cases.mkdir()
    prefix = "a" * 12
    hash_a = prefix + "0" * 52
    hash_b = prefix + "1" * 52
    path = reports / "findings.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps(_handwritten_row(hash_a, "obs-a")),
                json.dumps(_handwritten_row(hash_b, "obs-b")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _provision(hash_a, "abuse", reports, cases) == 0
    capsys.readouterr()  # drain the success path print
    draft_path = cases / "drafts" / f"abuse-{prefix}.yaml"

    assert _provision(hash_b, "abuse", reports, cases) != 0
    err = capsys.readouterr().err
    assert str(draft_path) in err and hash_b in err
    # Loser's payload never overwrote the winner's draft.
    assert load_case_file(draft_path)["payload"] == "obs-a"


def test_unknown_hash_exits_nonzero_naming_hash(tmp_path, capsys):
    reports = tmp_path / "reports"
    reports.mkdir()
    cases = tmp_path / "cases"
    cases.mkdir()
    missing = "f" * 64
    assert _provision(missing, "abuse", reports, cases) != 0
    assert missing in capsys.readouterr().err


def test_pass_verdict_exits_nonzero_naming_verdict(tmp_path, capsys):
    reports = tmp_path / "reports"
    cases = tmp_path / "cases"
    cases.mkdir()
    row = _seed_miss(reports, verdict="pass", observed_detection="abuse-detected")
    assert _provision(row["dedupe_hash"], "abuse", reports, cases) != 0
    assert "pass" in capsys.readouterr().err
    assert not (cases / "drafts").exists()


def test_bad_class_exits_nonzero_naming_value(tmp_path, capsys):
    reports = tmp_path / "reports"
    cases = tmp_path / "cases"
    cases.mkdir()
    row = _seed_miss(reports)
    assert _provision(row["dedupe_hash"], "xss", reports, cases) != 0
    assert "xss" in capsys.readouterr().err


def test_corrupt_findings_row_surfaces_nonzero(tmp_path, capsys):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "findings.ndjson").write_text("{not json\n", encoding="utf-8")
    cases = tmp_path / "cases"
    cases.mkdir()
    assert _provision("a" * 64, "abuse", reports, cases) != 0
    assert capsys.readouterr().err.strip() != ""


def test_activating_draft_unchanged_fails_until_detection_is_set(tmp_path):
    reports = tmp_path / "reports"
    cases = tmp_path / "cases"
    cases.mkdir()
    row = _seed_miss(reports)
    assert _provision(row["dedupe_hash"], "abuse", reports, cases) == 0
    draft_path = cases / "drafts" / f"abuse-{row['dedupe_hash'][:12]}.yaml"
    text = draft_path.read_text(encoding="utf-8")

    # Move to cases/ and flip to active without setting a real detection.
    moved = cases / draft_path.name
    moved.write_text(
        text.replace('status: "draft"', 'status: "active"'), encoding="utf-8"
    )
    _, errors = load_suite(cases)
    assert errors, "activating a draft without a real detection must fail"
    assert any("REVIEW-REQUIRED" in error for error in errors)

    # Setting the real detection (and bumping the version) activates cleanly.
    moved.write_text(
        text.replace('status: "draft"', 'status: "active"')
        .replace('detection: "REVIEW-REQUIRED"', 'detection: "abuse-detected"')
        .replace('version: "0.1.0"', 'version: "1.0.0"'),
        encoding="utf-8",
    )
    loaded, errors = load_suite(cases)
    assert errors == []
    assert [case["case_id"] for case in loaded] == [f"abuse-{row['dedupe_hash'][:12]}"]

"""Case-library tests: six versioned classes, loopback-only, inert payloads."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sidecar.simulation import (
    CASE_CLASSES,
    CaseError,
    load_suite,
    parse_case_text,
    validate_case,
)

SHIPPED_DIR = Path(__file__).resolve().parents[2] / "sidecar" / "cases"


def test_six_classes_each_have_a_versioned_case():
    cases, errors = load_suite(SHIPPED_DIR)
    assert errors == []
    assert len(cases) == 6
    assert sorted(case["class"] for case in cases) == sorted(CASE_CLASSES)


@pytest.mark.parametrize("case_class", CASE_CLASSES)
def test_each_class_file_is_schema_valid(case_class):
    cases, errors = load_suite(SHIPPED_DIR)
    assert errors == []
    matches = [c for c in cases if c["class"] == case_class]
    assert len(matches) == 1
    assert validate_case(matches[0], source=case_class) == []


def test_case_ids_unique_versions_semver_and_loopback():
    cases, errors = load_suite(SHIPPED_DIR)
    assert errors == []
    ids = [case["case_id"] for case in cases]
    assert len(set(ids)) == len(ids) == 6
    for case in cases:
        assert case["transport"] == "loopback-only"
        assert case["scope"] == "llm-own-endpoints"


def test_every_case_file_declares_loopback_transport():
    files = sorted(SHIPPED_DIR.glob("*.yaml"))
    assert len(files) == 6
    for path in files:
        assert "loopback-only" in path.read_text(encoding="utf-8"), path.name


def test_payloads_are_inert_strings_never_executed():
    cases, errors = load_suite(SHIPPED_DIR)
    assert errors == []
    for case in cases:
        assert isinstance(case["payload"], str)
        assert case["payload"].strip()
        # Parsed documents hold only strings and the expected mapping.
        for key, value in case.items():
            assert isinstance(value, str) or key == "expected", key
        assert set(case["expected"]) <= {"detection", "residual_rationale"}


def test_missing_expectation_fails_validation_by_name(tmp_path):
    target = tmp_path / "cases"
    shutil.copytree(SHIPPED_DIR, target)
    victim = target / "abuse-01.yaml"
    lines = [
        line
        for line in victim.read_text(encoding="utf-8").splitlines(keepends=True)
        if "detection:" not in line
    ]
    victim.write_text("".join(lines), encoding="utf-8")
    _, errors = load_suite(target)
    assert errors, "deleting a detection expectation must fail validation"
    assert any("abuse-01" in error for error in errors)


def test_duplicate_case_id_fails_naming_both_files(tmp_path):
    target = tmp_path / "cases"
    shutil.copytree(SHIPPED_DIR, target)
    shutil.copy(target / "abuse-01.yaml", target / "abuse-02.yaml")
    _, errors = load_suite(target)
    assert any("duplicate case_id" in e and "abuse" in e for e in errors)


@pytest.mark.parametrize(
    "bad_text",
    [
        "case_id: \"x\"\n\tversion: \"1.0.0\"\n",  # tabs forbidden
        "case_id: \"x\"\nversion: \"1.0.0\"\n- stray\n",  # lists forbidden
        "case_id: &anchor \"x\"\n",  # anchors forbidden
        "case_id: !tag \"x\"\n",  # tags forbidden
        "payload: |\n  block\n",  # block scalars forbidden
        "expected:\n  detection: \"a\"\n    deeper: \"b\"\n",  # deep nesting
        "other:\n  nested: \"x\"\n",  # nested mapping outside expected:
        "case_id: \"x\"\ncase_id: \"y\"\n",  # duplicate keys
    ],
)
def test_hostile_yaml_constructs_rejected(bad_text):
    with pytest.raises(CaseError):
        parse_case_text(bad_text, source="<test>")

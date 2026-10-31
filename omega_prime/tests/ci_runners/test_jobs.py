"""Workflow job parsing and the pool matching policy."""

from __future__ import annotations

import pytest

from omega_prime.ci_runners.jobs import (
    JobStatus,
    PayloadError,
    is_pool_runner,
    matches,
    parse_workflow_job,
)
from omega_prime.ci_runners.settings import DEFAULT_LABELS

from ._support import job_payload


def test_parse_queued() -> None:
    job = parse_workflow_job(job_payload(repo="Acme/Widgets"))
    assert job is not None
    assert job.status is JobStatus.QUEUED and job.repo == "acme/widgets"
    assert job.private and job.labels == DEFAULT_LABELS


@pytest.mark.parametrize("action", ["waiting", "requested", "something_new"])
def test_unhandled_actions_are_ignored(action: str) -> None:
    assert parse_workflow_job(job_payload(action)) is None


def test_runner_fields() -> None:
    job = parse_workflow_job(
        job_payload("in_progress", runner_name="omega-ci-1-abcd", runner_id=55)
    )
    assert job is not None and job.runner_name == "omega-ci-1-abcd"
    assert job.runner_id == 55 and job.status is JobStatus.IN_PROGRESS


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(action=3),
        lambda p: p.pop("repository"),
        lambda p: p["repository"].update(full_name="noslash"),
        lambda p: p["workflow_job"].update(labels="self-hosted"),
        lambda p: p["workflow_job"].update(labels=[1]),
        lambda p: p["workflow_job"].update(runner_name=5),
        lambda p: p["workflow_job"].update(runner_id="5"),
        lambda p: p["workflow_job"].update(id=True),
        lambda p: p["workflow_job"].update(run_id=0),
    ],
)
def test_malformed_payloads(mutate) -> None:  # type: ignore[no-untyped-def]
    payload = job_payload()
    mutate(payload)
    with pytest.raises(PayloadError):
        parse_workflow_job(payload)


def _job(**kw):  # type: ignore[no-untyped-def]
    job = parse_workflow_job(job_payload(**kw))
    assert job is not None
    return job


def test_matching_policy() -> None:
    pool = DEFAULT_LABELS
    assert matches(_job(), pool, ())
    assert matches(_job(labels=("self-hosted", "omega-ci")), pool, ())
    assert not matches(_job(labels=("self-hosted", "gpu")), pool, ())
    assert not matches(_job(labels=()), pool, ())
    assert not matches(_job(private=False), pool, ())
    assert not matches(_job(visibility="public"), pool, ())
    assert matches(_job(visibility="internal"), pool, ())
    assert matches(_job(), pool, {"acme/widgets"})
    assert not matches(_job(), pool, {"acme/other"})


def test_private_requires_explicit_true() -> None:
    payload = job_payload()
    payload["repository"]["private"] = "true"
    job = parse_workflow_job(payload)
    assert job is not None and not job.private


def test_is_pool_runner() -> None:
    assert is_pool_runner("omega-ci-1-abcd")
    assert not is_pool_runner("vps-runner-1")
    assert not is_pool_runner(None)

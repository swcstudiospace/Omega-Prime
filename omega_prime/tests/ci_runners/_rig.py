"""Shared controller rig: real adapters over a scripted fake transport."""

from __future__ import annotations

from typing import Any

from omega_prime.ci_runners.controller import Controller
from omega_prime.ci_runners.desktop import DesktopClient
from omega_prime.ci_runners.github import GitHubClient
from omega_prime.ci_runners.jobs import WorkflowJob, parse_workflow_job
from omega_prime.ci_runners.scaler import Scaler
from omega_prime.ci_runners.settings import DEFAULT_LABELS
from omega_prime.tests.ci_runners._secrets import placeholder

from ._fakes import FakeDelayed, FakeHttp, ListPublisher, StaticToken, resp
from ._support import FakeClock, Ids, job_payload

ORG = "/orgs/acme/actions/runners"
URL = "https://desktop.example.test"
TOKEN = placeholder("desktop-token")
JIT = placeholder("jit-config")


def job(action: str = "queued", **kw: Any) -> WorkflowJob:
    parsed = parse_workflow_job(job_payload(action, **kw))
    assert parsed is not None
    return parsed


def routes(**overrides: Any) -> FakeHttp:
    http = FakeHttp()
    http.on("GET", ORG, resp(200, {"runners": []}))
    http.on("GET", "/v1/runners", resp(200, []))
    http.on(
        "POST",
        f"{ORG}/generate-jitconfig",
        resp(201, {"runner": {"id": 42}, "encoded_jit_config": JIT}),
    )
    http.on("POST", "/v1/runners", resp(201))
    http.on(
        "GET", f"{ORG}/42", resp(200, {"id": 42, "name": "omega-ci-x", "busy": False})
    )
    http.on("DELETE", f"{ORG}/42", resp(204))
    return http


class Rig:
    def __init__(self, http: FakeHttp, **kw: Any) -> None:
        self.http = http
        self.clock = FakeClock()
        self.ids = Ids()
        self.publisher: Any = kw.pop("publisher", None) or ListPublisher()
        self.delayed = FakeDelayed()
        self.scaler = Scaler(
            labels=DEFAULT_LABELS,
            max_runners=kw.pop("max_runners", 8),
            idle_grace_s=30,
            new_generation=self.ids.generation,
            new_suffix=self.ids.suffix,
        )
        self.ctrl = Controller(
            scaler=self.scaler,
            github=GitHubClient(
                scope="org",
                org="acme",
                repo=None,
                runner_group_id=7,
                labels=DEFAULT_LABELS,
                auth=kw.pop("auth", None) or StaticToken(),
                transport=http,
                clock=self.clock,
                wall=self.clock,
                wait=self.clock.wait,
            ),
            desktop=DesktopClient(
                URL,
                lambda: TOKEN,
                http,
                clock=self.clock,
                wait=self.clock.wait,
                jitter=lambda: 1.0,
                **kw.pop("desktop", {}),
            ),
            publisher=self.publisher,
            delayed=self.delayed,
            extra_secrets=lambda: (TOKEN,),
            **kw,
        )

    def only(self) -> Any:
        [runner] = self.scaler.runners.values()
        return runner

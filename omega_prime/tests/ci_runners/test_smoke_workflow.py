"""The omega-ci end-to-end smoke workflow stays minimal and safe.

It runs on the self-hosted pool, so it must be manual-only (no schedule,
PD-12; no pull_request trigger), read-only, secret-free and checkout-free.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from omega_prime.ci_runners.settings import DEFAULT_LABELS

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "omega-ci-smoke.yml"


def test_smoke_workflow_is_manual_read_only_and_targets_the_pool() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    triggers = doc.get(True, doc.get("on"))  # YAML 1.1 reads bare `on` as True
    assert triggers == {"workflow_dispatch": None}
    assert doc["permissions"] == {"contents": "read"}
    [(name, job)] = doc["jobs"].items()
    assert name == "smoke"
    assert tuple(job["runs-on"]) == DEFAULT_LABELS
    assert job["timeout-minutes"] == 10
    assert "permissions" not in job and "container" not in job
    runs = [step["run"] for step in job["steps"]]
    assert runs == ["uname -a", "whoami", "echo omega-ci ok"]
    assert all("uses" not in step for step in job["steps"])  # no checkout
    assert "${{ secrets" not in text and "secrets:" not in text
    assert "GITHUB_TOKEN" not in text

# v19 Milestone Audit - Installer bindings

**Audited:** 2026-10-15
**Verdict:** PASS for INST-01 through INST-07.

| Requirement | Result | Evidence |
| --- | --- | --- |
| INST-01 resumable setup order | pass | omega_prime/integrations/setup.py and test_installer_bindings.py |
| INST-02 Notion schema contract | pass | omega_prime/integrations/contract.py and docs/notion-task-dashboard.md |
| INST-03 own Linear team | pass | omega_prime/integrations/linear.py |
| INST-04 findings sync uses that client | pass | omega_prime/grokbot/findings_sync.py |
| INST-05 optional gateways | pass | omega_prime/integrations/gateways.py and tools/redteam.py |
| INST-06 empty prime-agent fails setup | pass | omega_prime/integrations/submodules.py and setup_check.py |
| INST-07 no secret in the binding | pass | test_installer_bindings.py |

Phase 77.1 live import UAT remains user-owned and is not a v19 gap.

**Not added as submodules:** hermes-agent, oh-my-pi, programming-desk, agent-substrate, claude-ultrathink, vps-agent-bus, and hermes-linear-connector. Setup does not import them. The reasons are in docs/setup.md. prime-agent and redteam were already pinned.

**Template:** docs/notion-task-dashboard.md is the duplicate-by-hand contract, version 1. No Notion gallery URL exists in this repo. The Tasks property set is the schema the checker enforces. It is not the Agent Task Graph.

**Untracked and left out:** omega_prime/sessions.db and uv.lock. Neither is a v19 deliverable.

**Verification:** ruff, ruff format, and mypy clean (441 files). Pytest 4679 passed, 3 skipped. Evals 26 passed. Catalog, plugin, prompt format, assemble, provenance, and readiness passed.

**Cleanup:** phase 96 stays under .planning/phases. The template and setup docs stay.

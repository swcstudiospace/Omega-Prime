# Phase 96 - Plan

**Files:** `omega_prime/integrations/` (binding, contract, linear, notion, gateways, setup, submodules, transport), `omega_prime/commands/__init__.py`, `omega_prime/grokbot/findings_sync.py`, `omega_prime/setup_check.py`, `omega_prime/tools/redteam.py`, `omega_prime/prompts/bot-00-omega-prime.xml`, `docs/notion-task-dashboard.md`, `docs/setup.md`, `omega_prime/grokbot/SETUP.md`, `omega_prime/tests/test_installer_bindings.py`.

**States:** unconfigured, awaiting-user, validating, ready, invalid, degraded, skipped. Skipped is only for gateways. Notion dashboard states: template-not-duplicated, shared-but-unchecked, schema-mismatch, ready.

**Verify:** `pytest omega_prime/tests/test_installer_bindings.py`, then ruff, ruff format, and mypy on `omega_prime/`.

**Non-goals:** New submodules, a vps-agent-bus client, Agent Task Graph as the schema, marking phase 77.1 passed.

**Safe stop:** A failed check does not mark the phase complete.

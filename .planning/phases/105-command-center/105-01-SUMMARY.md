# Phase 105-01 Summary

## Implemented

- `status_file.py` retains the v1 task bridge while validating positive monotone `next_seq`, per-row `seq`, and a SHA-256-keyed `identity_registry` with redacted title, observed `{at,state}` timeline, and `last_state`. A restrictive persistent sidecar lock spans validation through same-directory fsync/replace; legacy writes merge durable allocations and keep assigned row numbers.
- `task_ids.py` provides minimum-five-width display labels, non-consuming previews (including pending bridge backfill), durable idempotent single assignment, bridge-order bulk backfill, and deterministic numbering of normalized task/waiting/finished rows. Initial state and actual state changes are timestamped once; repeated observations do not append events.
- `Task`, `WaitingItem`, and `FinishedItem` expose nullable `seq`/`display_id`, empty-default `timeline`, and (where applicable) nullable structured `wiring`; blocked-pane waits carry an actual related task identity. `tasks_json` passes validated counter/registry metadata.
- The poller accepts optional `task_ids_path`, keeps test pollers filesystem-isolated by default, recursively redacts only the bounded GitHub `command_center` envelope, and retains prior GitHub rows with error/stale health after a timeout. CLI `next-id`, `assign-id`, and `assign-ids` dispatch before collectors/PID/server startup.

## Verification

| Command | Result |
|---|---|
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_task_ids.py omega_prime/tests/test_dashboard_redaction.py` | Initial run failed (missing helper import and outdated lock/identity assertions); corrected rerun: exit 0, 28 passed. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_model.py omega_prime/tests/test_dashboard_file_collectors.py omega_prime/tests/test_dashboard_task_ids.py` | Initial run failed (missing waiting key and fixture used the wrong bridge seam); corrected rerun: exit 0, 34 passed. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_task_ids_cli.py omega_prime/tests/test_dashboard_snapshot_contract.py omega_prime/tests/test_dashboard_poller_fixes.py` | Two initial runs exposed test fixture issues; corrected rerun: exit 0, 13 passed. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard*.py` | Exit 0, 120 passed, one existing Starlette/httpx deprecation warning. |

## Evidence and remaining limits

The published snapshot still has exactly six top-level keys: `generated_at`, `sources`, `active`, `pr_gate`, `waiting_on_ove`, and `finished`. The nested GitHub extra is allowlisted to `repositories`, `ci_cd`, `recent_merges`, and `meta`; unknown siblings are dropped before publication. Allocation, bridge migration, concurrent subprocess assignment, lock permissions, corrupted-file preservation, and timeout retention are tested locally. Live GitHub parity, browser presentation, and end-to-end HTTP/SSE runtime behavior remain for their later waves; this summary makes no claim about them.
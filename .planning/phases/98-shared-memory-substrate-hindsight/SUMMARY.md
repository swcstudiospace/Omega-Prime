# Phase 98 — Shared memory through substrate, Hindsight fallback

## Outcome

Implementation and local verification completed on `fix/wiring-p2-shared-memory`, using the current base as directed by the operator's parallel-mode decision. The phase is not marked integrated: rebase and source-contract alignment remain in the operator's strict merge order after phase 97. No milestone audit, completion, cleanup, push, PR, deployment, connector restart, or live-service test was performed.

## Implemented

- Added a local/substrate/Hindsight selector, local-record codec, independent recall fan-out with canonical-content deduplication and source provenance, and local-first retain with a stable operation identity and explicit remote write results.
- Composed the shared coordinator into lead and growth runtime paths; wired approved `/omega-recall` and `/omega-retain` behavior and read-only doctor reporting for all three backends.
- Wired the configured Hindsight service behind explicit URL configuration and brokered credentials; removed the obsolete service-first bridge. Updated readiness consumers and generated command/tool-catalog documentation.
- Added fake-backend unit and production-path tests, including registry, command, doctor, growth, source merging, authorization, partial write failure, and local persistence paths.
- Kept the SubstrateClient seam narrow: explicit optional `repo` and `idempotency_key` parameters are passed where available. Phase 97 transport, settings loading, and source-aware search errors were not reimplemented.

## Verification

| Command | Result |
|---|---|
| `ruff check omega_prime/` | Exit 0 — all checks passed |
| `ruff format --check omega_prime/` | Exit 0 — 595 files already formatted |
| `mypy omega_prime/` | Exit 0 — no issues in 468 source files |
| `pytest omega_prime/tests -q` | Exit 1 — 27 failed, 4847 passed, 11 skipped, 15 warnings in 228.59s; failures match the 27 pre-change baseline tests enumerated in `VALIDATION.md` |
| `python -m omega_prime.tooling.plugin --check` | Exit 0 — 66 generated files up to date |
| `python -m omega_prime.tooling.catalog --check` | Exit 0 — tool catalog up to date |
| `pytest omega_prime/tests/test_grokbot_readiness.py::test_cli_full_local_profile -q` | Exit 0 — 1 passed after migrating the readiness retain scenario |

The prompt files did not change; prompt-format and prompt-assembly checks were not applicable. The full suite had no failures beyond the known baseline set.

## Integration boundary and operator actions

- Rebase this branch onto phase 97 only in the operator's required merge order. Re-read its endpoint resolver and adopt its authoritative configuration and source-aware `memory_search` contract. On this base, `SubstrateClient.memory_search` converts transport failures to `[]`, so a substrate outage cannot yet be distinguished from a valid empty result. The Phase 98 implementation intentionally did not replace Phase 97's transport behavior.
- Configure `HINDSIGHT_URL` through the phase 97 settings path, supply `HINDSIGHT_API_KEY` only through the approved environment/secret broker, and restart the connector when Ove authorizes the live change. None of these live operations was attempted.
- Re-run the full gates after ordered rebase/integration. The phase 77.1 live UAT remains separately operator-owned and unchanged.

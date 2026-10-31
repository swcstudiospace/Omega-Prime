---
phase: 112-sbx3-engine-backend
plan: '01'
subsystem: omp-sandbox folded-ops test coverage
tags: [omp-sandbox, tests, seed_repo, export_commits, run_check, py_deps, runStage]
requires: []
provides:
  - tests/ops.test.ts folded-ops suite (8 tests) registered in npm test
  - test-only Core.attachTierForTests(tier, seed?) seed seam
affects: []
tech-stack:
  added: []
  patterns:
    - path-mapping fake tier (sandbox-side /sandbox argv+cwd rewritten onto a per-tier tmp dir; real git/tar/sh/base64 inside tmp only)
key-files:
  created:
    - omp-sandbox/tests/ops.test.ts
  modified:
    - omp-sandbox/src/core/core.ts (test-only seed seam ONLY)
    - omp-sandbox/tests/fake-omp.mjs (prompt-log content enrichment ONLY)
    - omp-sandbox/package.json (tests/ops.test.ts appended to the test script ONLY)
decisions:
  - seam mirrors #up's seeded parts (allowedDir pre-check, then #seedRepo/#stagePyDeps/#stageDir) and returns the up_ stub shape; no-seed path stays byte-identical for guards.test.ts callers
  - fake tier has NO upload so Core.#put exercises its exec-stdin base64 fallback; download copies the mapped bundle
metrics:
  duration: ~35 min
  completed: 2026-10-21
  tasks: 3
  commits: 3
status: complete
---

# Phase 112 Plan 01: omp-sandbox folded-ops coverage Summary

**One-liner:** Folded standalone ops (seed_repo, export_commits, run_check, py_deps, runStage routing + the exact GSD prompt) proven end-to-end through a new test-only `attachTierForTests(tier, seed?)` seed seam against a path-mapping fake tier running real git/tar/sh confined to tmp.

## Tasks

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| T1 | seed_repo + export_commits through the test seed seam | 77cf274 | src/core/core.ts (seam), tests/ops.test.ts (new), package.json |
| T2 | run_check gate/receipt + py_deps staging | f434b76 | tests/ops.test.ts |
| T3 | runStage routing + exact GSD prompt | da27928 | tests/fake-omp.mjs, tests/ops.test.ts |

## Verification (parent-owned commands, run by the executor per task)

- `cd omp-sandbox && npx tsc --noEmit` → exit 0 at every task commit and at HEAD.
- `cd omp-sandbox && npm test` tail at HEAD (27/27 = the 19 pre-existing tests unmodified + 8 new ops tests):

```
✔ runStage routes plan=zai to the zai model with no fallback and sends the exact GSD prompt (60.38529ms)
✔ runStage routes execute=zai to glm-5.3-flash with no fallback (57.799157ms)
✔ runStage keeps the chatgpt default carrying the zai fallback; an explicit model wins (117.180699ms)
ℹ tests 27
ℹ pass 27
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 1724.366898
```

New ops.test.ts coverage: seed_repo bundle round trip (real checkout in the mapped workdir, `up.seeded`/`core.repo` pins, per-tier bundle under `<state>/seeds`), PathRefused gate before any git runs, size-verified export bundle + exact `no seeded repo` refusal without side effects, CHECK_BINS refusal (no receipt), `..` cwd refusal, receipt bodies (label/argv/cwd/code/ms/stdoutTail/stderrTail + startedAt/endedAt/tier) at 0600, exit-7 reported and receipted, py_deps stubs staged with the PYTHONPATH export in `.omp-sandbox.env` and a genuine stub import passing the check, zai/chatgpt/explicit-model routing pins, and the byte-for-byte `/{gsdCommand} ${MAGIC}\n\n${task}` prompt at the fake omp boundary.

## Seed-seam diff summary (proving test-only)

The **entire** src diff across the plan (`git diff 97c2877..HEAD -- omp-sandbox/src/`) is one hunk in `core.ts`, 17 insertions / 2 deletions, replacing the one-line `attachTierForTests` with its async seeded form:

- No-seed path: `this.tier = tier; this.#ready = true; this.up_ = { tier: tier.name };` then `return this.up_` — byte-identical behaviour for the existing guards.test.ts callers (attach still happens synchronously; they ignore the returned promise).
- Seeded path (`seed !== undefined` only): the same `allowedDir(seed_repo.path, WORKSPACE_ROOTS, "seed_repo.path")` pre-check `#up` applies (:73), then `#seedRepo` / `#stagePyDeps` / `#stageDir`, folded into `up_` as `seeded`/`pyDeps`/`staged` exactly like `#up`'s :92 fold.
- It boots no broker/tier/VM, spawns nothing itself, is documented "Not reachable over the API" (as the :68 seam was), and adds no API-reachable surface (server.ts op list untouched).
- No failing test forced any other src change: every assertion passed against current src behaviour once the test-side bugs below were fixed. package.json changed only by appending `tests/ops.test.ts` to the test script's file list; fake-omp.mjs changed only on line 14 (prompt log now appends `m.message + "\n"` instead of the bare `"prompt"` marker — knob name, trigger and guards.test.ts's existence-only assertion untouched).

## Test-side fixes made during execution (not src changes)

1. **Fake-tier argv mapping bug (caught by the threat model, fixed before commit):** the first draft mapped only `cwd`, not the exec argv/sh -c script bodies, so "sandbox" paths hit the real host `/sandbox/home` (which exists in this environment) — the round trip "passed" through real host state, and the export download ENOENT'd on the mapped path. Fixed by mapping `argv.slice(1)` through the same `/sandbox/` rewrite; the leaked host files (`/sandbox/home/seed.bundle`, `/sandbox/home/export.bundle`) were deleted immediately. Post-fix runs verified: no `seed.bundle`/`export.bundle`/`pydeps.tgz`/`stage.tgz` reappear under `/sandbox/home`.
2. Refusal test asserted a fresh `seeds/` dir; test 1 creates it first. Fixed to assert the refusing tier's own bundle path is absent.
3. A redundant `split("\n")` log-shape assertion was dropped (the message itself contains `\n\n`; the byte-for-byte `equal` assertion is the contract).
4. Project lint rules while writing: no `as any` (named structural types + reasoned casts), `import type` at top level, unguarded `clearTimeout`, executor-form promises (tsconfig pins lib ES2023, so no `Promise.withResolvers`), `in`-narrowing for `status()`'s union return.

## Deviations from Plan

None — plan executed as written. The environment produced no auth gates; no package installs were needed (`node_modules` already present).

## Known Stubs

None. No stub patterns in the new/modified files; every assertion exercises real behaviour (real git bundle/fetch/checkout, real tar/base64, real node/python3 child processes — all confined to tmp dirs).

## Threat Flags

None — no new network endpoints, auth paths, file-access patterns or schema changes. The fake tier's mapping was hardened in-flight (item 1 above), matching T-112-01's mitigation.

## Notes for the parent verifier

- Working tree after the summary commit: the only non-clean entries are the sibling executor's in-flight `omega_prime/engine/omp_driver.py` / `omega_prime/engine/sandbox_omp.py` edits (wave-1 plan 112-02's files) — untouched by this plan.
- Nothing was pushed; no remotes touched; no podman/openshell/bwrap/herdr/model calls anywhere in the suite.

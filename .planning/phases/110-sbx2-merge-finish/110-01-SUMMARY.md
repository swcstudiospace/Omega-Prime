---
phase: 110-sbx2-merge-finish
plan: '01'
subsystem: infra
tags: [omp-sandbox, podman, openshell, bwrap, sandbox, typescript, path-guard, seed, greptile]

requires:
  - phase: sbx-2-workspace-mount (40338ec) + sbx-0 round-1 fixes (3ada70b)
    provides: the merged tree 3e7be33 whose follow-ups this plan finished
provides:
  - omp-sandbox typechecks clean (npx tsc --noEmit exits 0)
  - all six still-applying Greptile PR #55 comments fixed, each with a fake-only regression test
  - both parents' behavior re-proven in the three merge-resolved paths (W110-02 evidence below)
affects: [sbx-3-engine-backend, sbx-4-routines-visibility, sbx-5-e2e]

actuals:
  tokens: 21600
  tasks: 4
  commits: 4

tech-stack:
  added: []
  patterns:
    - "pure exported seam + ESM main-module boot guard for stdio server modules (server.ts errorReply)"
    - "generated per-variant config files from one checked-in source with minimal substitution (podmanPolicyYaml)"

key-files:
  created: []
  modified:
    - omp-sandbox/src/core/providers/openshell-podman.ts
    - omp-sandbox/src/core/paths.ts
    - omp-sandbox/src/core/core.ts
    - omp-sandbox/src/core/gsd.ts
    - omp-sandbox/src/core/server.ts
    - omp-sandbox/src/core/vm.ts
    - omp-sandbox/tests/paths.test.ts
    - omp-sandbox/tests/guards.test.ts

key-decisions:
  - "W110-01: only the call target changed (os -> this.#os); the two TS7006s were cascades and vanished, so no parameter annotations were added"
  - "W110-04: policy run user derived from host identity (process.getuid/getgid) into a generated policy-podman.yaml in the variant's gateway state dir; no OpenShell uidmap TOML keys invented; default variant untouched at 1000/1000"
  - "W110-05: exported pure errorReply seam plus main-module guard; production entry (core_child.rs spawning node src/core/server.ts from the package root) boots identically — verified both directions"
  - "W110-07: seedFiles returns rel + absolute source path (array); bytes read one file at a time in Core.#seed at copy time"

patterns-established:
  - "Regression tests use fake tier/vm objects and dynamic imports after OMP_SANDBOX_STATE is set; no real podman/openshell/bwrap spawn"

requirements-completed: [W110-01, W110-02, W110-03, W110-04, W110-05, W110-06, W110-07, W110-08]

coverage:
  - id: D1
    description: "tsc clean: #otherSandboxes calls this.#os; unknown-gateway branch and filter chain byte-identical"
    requirement: W110-01
    verification:
      - kind: other
        ref: "cd omp-sandbox && npx tsc --noEmit (exit 0; baseline was exactly the 3 W110-01 errors)"
        status: pass
    human_judgment: false
  - id: D2
    description: "Merge preservation: package.json/core.ts/tiers.ts carry both parents' behavior, re-diffed against 40338ec and 3ada70b"
    requirement: W110-02
    verification:
      - kind: other
        ref: "git diff 40338ec..HEAD / git diff 3ada70b..HEAD on the three merged paths + git grep marker counts (see W110-02 evidence)"
        status: pass
    human_judgment: false
  - id: D3
    description: "GSD files target the mounted workspace on mount tiers; gsdCommandsOnHost follows; hostRoot/work untouched"
    requirement: W110-03
    verification:
      - kind: unit
        ref: "tests/guards.test.ts#ensureGsd and gsdCommandsOnHost target the mounted workspace, never the hidden hostRoot/work"
        status: pass
    human_judgment: false
  - id: D4
    description: "Mount-variant policy run-as derives from host identity via podmanPolicyYaml; default stays 1000/1000; keep-id kept"
    requirement: W110-04
    verification:
      - kind: unit
        ref: "tests/paths.test.ts#podmanPolicyYaml substitutes only the run-as values; the checked-in policy stays 1000/1000"
        status: pass
    human_judgment: false
  - id: D5
    description: "path_refused reaches stdio callers through the pure errorReply seam; server import is side-effect-free; main-module boot unchanged"
    requirement: W110-05
    verification:
      - kind: unit
        ref: "tests/guards.test.ts#errorReply maps path_refused distinctly from stale_answer and generic errors"
        status: pass
      - kind: other
        ref: "node --experimental-transform-types --no-warnings src/core/server.ts boots (ready event, exit 0); import from another module runs nothing"
        status: pass
    human_judgment: false
  - id: D6
    description: "vmRoundTrip probe lives in the tier's private home; mounted workdir never touched; all round-trip checks kept"
    requirement: W110-06
    verification:
      - kind: unit
        ref: "tests/paths.test.ts#vmRoundTrip probes only the tier's private home, never the mounted workdir"
        status: pass
    human_judgment: false
  - id: D7
    description: "seedFiles streams (rel + absolute source path, no buffered bytes); Core.#seed reads at copy time with same summary shape"
    requirement: W110-07
    verification:
      - kind: unit
        ref: "tests/paths.test.ts#seedFiles carries rel + absolute source path only (no bytes buffered during the walk)"
        status: pass
      - kind: unit
        ref: "tests/paths.test.ts#seed is binary-safe and skips .git, .venv, node_modules and symlinks (migrated to the new contract, assertions kept)"
        status: pass
    human_judgment: false
  - id: D8
    description: "allowedDir accepts strictly-inside dotdot names while every real traversal stays refused (exact Greptile predicate)"
    requirement: W110-08
    verification:
      - kind: unit
        ref: "tests/paths.test.ts#allowedDir accepts strictly-inside dotdot names; every real traversal stays refused"
        status: pass
    human_judgment: false

duration: 25min
completed: 2026-10-21
status: complete
---

# Phase 110: omp-sandbox sbx-2 merge finish Summary

**omp-sandbox typechecks clean and all six still-applying Greptile PR #55 comments are fixed with fake-only regression tests: mounted tiers get GSD files and a host-identity podman policy, path_refused reaches the Anda stdio protocol, the VM probe moved out of the job workdir, seeding streams, and dotdot-prefixed inside names are accepted.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-10-21T15:17:09Z
- **Tasks:** 4/4
- **Files modified:** 8 (all under `omp-sandbox/`)

## Acceptance Results

| Criterion | Result |
|---|---|
| `cd omp-sandbox && npm ci --ignore-scripts --no-audit --no-fund` | ✓ exit 0 (178 packages; plain `npm ci` still fails on better-sqlite3 node-gyp egress, as planned for) |
| `cd omp-sandbox && npx tsc --noEmit` | ✓ exit 0 (baseline at 3e7be33: exactly the 3 W110-01 errors) |
| `cd omp-sandbox && npm test` | ✓ 18 pass, 0 fail (12 existing + 6 new dedicated blocks, one per W110-03..W110-08) |
| No file outside `omp-sandbox/` touched by execution commits | ✓ (diff `fd784af..HEAD` names exactly the 8 plan-owned files) |
| No existing test weakened, skipped, or deleted | ✓ (only the seed test's contract migration: `files[1].data.equals(bin)` → `readFileSync(files[1].path).equals(bin)` + path assertion) |
| All new tests fake-only | ✓ (fake tiers/vm objects, recording execs, no podman/openshell/bwrap/herdr spawn, no network, no models) |
| `git status --porcelain` clean at end | ✓ (verified after the final docs commit) |

## Task Commits

1. **110-01-T1 tsc clean + parent re-verify (W110-01, W110-02)** — `1a3cd5e`
2. **110-01-T2 dotdot names + streaming seed (W110-08, W110-07)** — `8fbfb06`
3. **110-01-T3 mounted-workspace GSD + host-identity policy (W110-03, W110-04)** — `2753699`
4. **110-01-T4 path_refused seam + private-home probe (W110-05, W110-06)** — `bec0300`

## The Fixes

- **W110-01** (`openshell-podman.ts:215`): bare `os(["sandbox","list"],…)` → `this.#os(…)`. Unknown-gateway branch, empty-array return, and the stdout split/map/trim/filter chain stay verbatim; the two TS7006s were cascades and disappeared with the call fix, so no annotations were added.
- **W110-03** (`gsd.ts`): `ensureGsd` computes `hostWork = tier.workspaceHostPath ?? (tier.hostRoot ? join(tier.hostRoot,"work") : null)` — warm-cache copy, cold-path check, and cache fill all key off that one value; `gsdCommandsOnHost` resolves the same way (fixed, not deleted).
- **W110-04** (`openshell-podman.ts`): new exported pure `podmanPolicyYaml(runAsUser, runAsGroup)` reads the checked-in `sandbox/policy-podman.yaml` at call time and substitutes exactly the two run-as values. The mount variant writes it into `this.#gwState` (like `gateway.toml`) and passes that path to `sandbox create --policy`; `info.workspace` records policy path + run uid/gid. Default variant keeps the static 1000/1000 policy; `gatewayToml` is byte-identical (`userns = "keep-id"`, no new keys — function-body diff vs base: identical).
- **W110-05** (`server.ts`): exported pure `errorReply(e)` maps `PathRefused`→`path_refused`, `StaleAnswerError`→`stale_answer`, else `error`, message as before; the readline catch uses it (wire shape unchanged). All boot side effects (Core, event forwarder, console reroute, readline, signal handlers, ready event) sit behind the ESM main-module check.
- **W110-06** (`vm.ts`): probe relocated to `SANDBOX_MOUNT/home/.agentos-probe.txt` with cat at `${tier.sandboxRoot}/home/.agentos-probe.txt`; ls/readdir target the private home. `/sbx/home` (bwrap) and `/sandbox/home` (openshell-podman) are never the mounted workdir. All round-trip checks and the return shape unchanged; core.ts `#up` gate untouched.
- **W110-07** (`paths.ts`, `core.ts`): `seedFiles` returns `{ files: Array<{rel, path}>, skipped }` with no byte reads during the walk; `Core.#seed` reads each file at copy time, accumulates the byte total, same summary shape.
- **W110-08** (`paths.ts:31`): predicate is exactly `rel !== "" && rel !== ".." && !rel.startsWith("../") && !isAbsolute(rel)`; line 23's `'..'`-component refusal and the symlink-escape refusal untouched.

## New Regression Tests (file::name)

1. `tests/guards.test.ts` :: "ensureGsd and gsdCommandsOnHost target the mounted workspace, never the hidden hostRoot/work" (W110-03; cold install into workspace + cache fill + warm copy with exec-throwing tier + counts mounted vs hostRoot/work vs null)
2. `tests/paths.test.ts` :: "podmanPolicyYaml substitutes only the run-as values; the checked-in policy stays 1000/1000" (W110-04; endpoints + read_write byte-identical, exactly two run-as lines differ)
3. `tests/guards.test.ts` :: "errorReply maps path_refused distinctly from stale_answer and generic errors" (W110-05; importing server.ts inside a test runs nothing — a boot regression hangs the suite)
4. `tests/paths.test.ts` :: "vmRoundTrip probes only the tier's private home, never the mounted workdir" (W110-06; /sbx and /sandbox tiers with workspaceHostPath set)
5. `tests/paths.test.ts` :: "seedFiles carries rel + absolute source path only (no bytes buffered during the walk)" (W110-07)
6. `tests/paths.test.ts` :: "allowedDir accepts strictly-inside dotdot names; every real traversal stays refused" (W110-08)

Migrated (not one of the six): `tests/paths.test.ts` :: "seed is binary-safe and skips .git, .venv, node_modules and symlinks" — rel list, skip list, and binary-safety assertions kept under the new contract.

## W110-02 Parent-Diff Evidence (task 110-01-T1)

Merge `3e7be33` parents: `40338ec` (sbx-2 workspace mount, first) + `3ada70b` (sbx-0 round-1 fixes, second).

- vs **40338ec**: `git diff 40338ec..HEAD` on the three paths shows only the guards-suite union in `package.json`'s test script (`core.test.ts paths.test.ts guards.test.ts`) and the sbx-0 additions to `core.ts`/`tiers.ts`. Markers present at both 40338ec and HEAD: `workspace_host_path` (core.ts 4→5 hits, tiers.ts 1→1), `MOUNT_TIERS` (tiers.ts 2→2).
- vs **3ada70b**: markers present at both 3ada70b and HEAD: `validateTiers` (tiers.ts 2→2), `TIER_NAMES` (3→3), `#starting` (core.ts 4→4), `TERMINAL` (5→5), teardown (3→3). The stat diff on the trio is 3 files / +35−23 — the union, not a rewrite.
- Pinned by still-green existing tests: `guards.test.ts` tier-name refusal + one-stage-at-a-time (sbx-0), `paths.test.ts` mount gateway config + mount-skip (sbx-2).

## Decisions Made

- Followed the plan's assumption deltas verbatim (call-target-only W110-01, host-identity W110-04, pure-seam W110-05, private-home W110-06, path-only-array W110-07).
- `process.getuid/getgid` are optional in @types/node: captured once with non-null assertions plus a comment (tier is Linux-only rootless Podman).
- `RoundTripVm`/`WireRequest` structural types replaced the previous `any`-typed boundaries in `vm.ts`/`server.ts` without behavior change.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- `vmRoundTrip`'s structural exec return initially declared `exitCode: number | null`; the library's `CodeExecutionResult` uses `number | undefined`, which broke assignability — widened the parameter type (return-shape field flows through unchanged; `core.ts` compares `!== 0`).
- First draft of the W110-06 test asserted `/home` membership on the uname host-fn command string and on the bare `cat` argv word; both checks are wrong (uname writes nothing; the cat *target* is already pinned by `deepEqual`) — scoped the assertions to the actual probe accesses.

## Blocked

None. All in-sandbox acceptance criteria are met. Out-of-scope box-only items (per plan): the live e2e mount smoke and receipts under `/workspace/sbx-runs/pr2` (no podman/openshell in this sandbox), pip-audit on the PRs, and anything requiring a push — no push, no PR, no remote was touched.

## Next Phase Readiness

- `feat/sbx-2-workspace-mount` is clean, typechecks, and is fully green: ready for Ove's merge yes and for sbx-3 (engine backend) to build on the same mount variant and reply codes (`path_refused` is now observable by the Rust wrapper).
- The `errorReply` seam and the private-home probe contract are the interfaces sbx-3's Anda stdio client and sbx-5's e2e will rely on.

---
*Phase: 110-sbx2-merge-finish*
*Completed: 2026-10-21*

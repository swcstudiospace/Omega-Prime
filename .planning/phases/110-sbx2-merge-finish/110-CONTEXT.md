# Phase 110: omp-sandbox sbx-2 merge finish — Context

**Gathered:** 2026-10-21
**Status:** Planning complete (this stage); one execution plan (110-01) staged, nothing implemented.
**Milestone:** v21 Sandbox wiring (task 00030), registered in `.planning/ROADMAP.md` the same day.
**Requirements:** W110-01..W110-08 (below; also in the ROADMAP v21 section).
**Contract:** `/sandbox/home/refs/uplift2.xml` (task 00030, node n2 steps 1–2 and 5), `/sandbox/home/refs/got.json`, `/sandbox/home/refs/AUDIT.md` (C6), `/sandbox/home/refs/PR55-greptile-comments.md`.
**Tree:** `feat/sbx-2-workspace-mount` @ 3e7be33 (merge of 3ada70b into 40338ec; parents verified: `git log -1 --format=%P 3e7be33` → `40338ec… 3ada70b…`). Working tree clean before planning edits.

<goal>
## Goal

Make the merged sbx-2 tree typecheck clean (`npx tsc --noEmit` → 0 errors) and fix every Greptile PR #55 inline comment that still applies at 3e7be33 — each fix with its own regression test — while preserving both parents' behavior: sbx-0's round-1 guards and sbx-2's workspace-mount variant. Acceptance for the execution stage: `npx tsc --noEmit` = 0 errors and `npm test` all green in `omp-sandbox/`.
</goal>

<requirements>
## Requirements (locked labels)

- **W110-01 tsc clean.** `omp-sandbox/src/core/providers/openshell-podman.ts:215` — `#otherSandboxes` calls bare `os(...)`, an undefined name; it must call `this.#os(...)`. Lines 217 col 38 and 84: two implicit-any parameters (`x`, `n`) in the same expression. Verified current output: `npx tsc --noEmit` exits 2 with exactly these 3 errors (TS2304 at 215:21; TS7006 at 217:38 and 217:84). `execCapture` (providers/util.ts:18-19) returns `{ code: number | null; stdout: string; stderr: string }`, so the two TS7006s are cascades of the unknown `l` and disappear once the call is `this.#os`; add explicit parameter types only if any implicit-any remains after that.
- **W110-02 merge preservation.** The three conflicted paths resolved in 3e7be33 must keep both sides: `omp-sandbox/package.json` (test script runs `core.test.ts paths.test.ts guards.test.ts` — union of sbx-2's paths suite and sbx-0's guards suite), `src/core/core.ts` (sbx-0 single-flight `up`/`#ready`/`TERMINAL`/teardown-on-failure + sbx-2 `workspace_host_path`/`allowedDir`/mount-aware `startTier`), `src/core/providers/tiers.ts` (sbx-0 `validateTiers`/`TIER_NAMES` + sbx-2 `MOUNT_TIERS`/`TierOptions` skip-with-reason). Verified at HEAD by diffing both parents; execution re-checks before committing.
- **W110-03 mounted bwrap misses GSD files** (Greptile P1, bwrap.ts:41). `bwrap #argv` binds `workspaceHostPath` over `workdir`, hiding `hostRoot/work` inside the sandbox; `ensureGsd` (src/core/gsd.ts:11-25) still copies/installs/checks `join(tier.hostRoot, "work")`. Warm cache → commands land in the hidden dir (later `#probeOmp` command check fails); cold → installs into the workspace then throws checking the wrong dir. `gsdCommandsOnHost` (gsd.ts:27-31) has the same `hostRoot/work` assumption. Both must use the tier's `workspaceHostPath` when present (`SandboxTier.workspaceHostPath?: string | null` exists at providers/types.ts:17).
- **W110-04 other host users cannot write** (Greptile P1, openshell-podman.ts:41). Mount gateway TOML sets `userns = "keep-id"` (openshell-podman.ts:41) so container uid = host uid, but `sandbox/policy-podman.yaml:14` pins `run_as_user: "1000"`. A 0755 workspace owned by host uid ≠ 1000 is not writable by the workload. Fix by deriving the policy's run user from the host identity for the mount variant (or mapping the host user onto the policy uid); default (unmounted) behavior stays uid 1000.
- **W110-05 path error codes disappear** (Greptile P2, paths.ts:15). `PathRefused` carries `code = "path_refused"` (paths.ts:14-16) but `server.ts:37` maps every exception except `StaleAnswerError` to `code: "error"`, so a refused `up` path is indistinguishable from a startup failure. Add `PathRefused` to the reply mapping and test that reply.
- **W110-06 startup changes job files** (Greptile P2, core.ts:49 → vm.ts:26-35). `vmRoundTrip` writes a fixed-name `.agentos-probe.txt` into the sandbox workdir via the VM mount and never removes it; with a mounted workspace the workdir IS the host job directory, so every startup leaves/overwrites a diagnostic file there. Run the probe in the sandbox's private area instead (e.g. under the tier's private home dir), leaving the mounted workdir untouched.
- **W110-07 seeding holds every file** (Greptile P2, paths.ts:47). `seedFiles` (paths.ts:37-53) materializes every file's bytes (`readFileSync` per file into one array) before the first write; seeds may come from workspace roots (`SEED_ROOTS` includes `WORKSPACE_ROOTS`), so large assets can exhaust core memory. Keep only paths (or return an iterator) and read each file when it is copied in `core.ts #seed` (core.ts:87-98 consumes `files` and computes total bytes).
- **W110-08 valid directory names refused** (Greptile P2, paths.ts:31). `allowedDir` line 31 uses `!rel.startsWith("..")`, which also rejects strictly-inside names like `..job` (no parent component). Accept exactly Greptile's suggested predicate: `rel !== "" && rel !== ".." && !rel.startsWith("../") && !isAbsolute(rel)`. Line 23's separate `..`-component check (`p.split("/").includes("..")`) keeps refusing real `a/../b` inputs; root itself (`rel === ""`) stays refused.
</requirements>

<acceptance>
## Acceptance criteria (execution stage)

1. `cd omp-sandbox && npx tsc --noEmit` → 0 errors (W110-01).
2. `cd omp-sandbox && npm test` → all green, with one new regression test per W110-03..W110-08 (six new tests minimum).
3. The three merged paths still carry both parents' behavior, re-diffed against 40338ec and 3ada70b during execution (W110-02).
4. No existing test weakened, skipped, or deleted; the `seedFiles` contract change updates its direct test to the new streaming contract rather than dropping assertions (W110-07).
5. Tests use fakes only: no podman, openshell, bwrap spawn, herdr, network, or model calls.
</acceptance>

<source_map>
## Source map (verified at 3e7be33 in this worktree, 2026-10-21)

- `omp-sandbox/src/core/providers/openshell-podman.ts` — `:41` mount TOML `userns = "keep-id"`; `:121` `#os()` helper; `:138` `sandbox create --policy join(ROOT,"sandbox","policy-podman.yaml")`; `:214-218` `#otherSandboxes` (bare `os` at 215, implicit-any `x`/`n` at 217); `:30-32` `mountDriverConfig` (single rw bind at `/sandbox/work`).
- `omp-sandbox/sandbox/policy-podman.yaml` — `:14` `run_as_user: "1000"`.
- `omp-sandbox/src/core/gsd.ts` — `:9` host cache `STATE/cache/gsd-core-<ver>`; `:12` `hostWork = join(tier.hostRoot,"work")`; `:13-16` warm-cache copy into `hostWork/.claude`; `:17-18` npx install in sandbox; `:19-23` cold-path check + cache fill (checks `hostWork`); `:27-31` `gsdCommandsOnHost` reads `hostRoot/work/.claude/commands` (currently no callers in src/tests/e2e/crates — fix its assumption alongside `ensureGsd`, do not delete).
- `omp-sandbox/src/core/providers/bwrap.ts` — `:29` constructor `(hostRoot?, workspaceHostPath?)`; `:39-41` `--bind hostRoot /sbx` then workspace bind over workdir (later binds win); `:57-71` `start` (probe, sandbox-agent).
- `omp-sandbox/src/core/paths.ts` — `:14-16` `PathRefused { code = "path_refused" }`; `:19-34` `allowedDir` (refusals at 20-23, predicate at 31); `:37-53` `seedFiles` buffering all bytes.
- `omp-sandbox/src/core/server.ts` — `:16-29` op dispatch (`up` → `core.up(a)`); `:33-38` readline loop, catch at 37: `code: e instanceof StaleAnswerError ? "stale_answer" : "error"` (module has import side effects: instantiate-on-import + stdin readline — regression test needs an exported seam or equivalent, not a full server boot).
- `omp-sandbox/src/core/vm.ts` — `:26-35` `vmRoundTrip`: `writeFile(${SANDBOX_MOUNT}/${rel}/.agentos-probe.txt)`, `tier.exec cat`, host-fn uname, ls/readdir; `rel` = workdir relative to sandboxRoot (`work` on both mount tiers).
- `omp-sandbox/src/core/core.ts` — `:58-84` `#up` (mutual-exclusion + `allowedDir` at 59-61, `vmRoundTrip` at 68, `ensureGsd` at 70, seed at 71); `:87-98` `#seed` consumes `seedFiles(src)` result; `:100-110` `#probeOmp` asserts `gsd-plan-phase`/`gsd-execute-phase` visible to sandboxed omp.
- `omp-sandbox/src/core/providers/tiers.ts` — sbx-0 `validateTiers`/`TIER_NAMES`/`makeTier` + sbx-2 `MOUNT_TIERS`/`TierOptions` all present at HEAD.
- `omp-sandbox/src/core/providers/types.ts` — `:17` `workspaceHostPath?: string | null` on `SandboxTier`; `:32` `TierOptions`.
- `omp-sandbox/src/core/providers/util.ts` — `:18-19` `execCapture` → `{ code, stdout: string, stderr: string }`.
- Tests: `tests/core.test.ts` (3 tests: ask/stale RPC against `fake-omp.mjs`, mock attestation), `tests/paths.test.ts` (4: allowedDir matrix incl. `escape` symlink, seed binary-safety/skips, `gatewayToml`/`mountDriverConfig` mount-only assertions, mount-skips-non-mount-tiers), `tests/guards.test.ts` (5: tier-name refusal, overlapping stages/redaction, abort-during-setup, redactSecrets; uses `attachTierForTests` + fake tier + `fake-omp.mjs`, sets `OMP_SANDBOX_STATE` temp). `npm test` = `node --test --experimental-transform-types --no-warnings tests/core.test.ts tests/paths.test.ts tests/guards.test.ts` (12 tests at HEAD, all pass).
- Merge scope fact: `git diff 40338ec..3e7be33 -- omp-sandbox/` touches 11 files; `bwrap.ts`, `gsd.ts`, `paths.ts`, `server.ts`, `vm.ts`, `policy-podman.yaml` are untouched by the merge, so all six Greptile comments filed on 40338ec still apply verbatim at HEAD.
</source_map>

<environment>
## Execution environment facts (verified 2026-10-21)

- Node on PATH here is v24.21.0 (`node --version`) — satisfies `engines.node >=24`.
- `npm ci --no-audit --no-fund` FAILS in this sandbox: transitive `better-sqlite3@12.11.1` falls back to node-gyp, which cannot fetch headers (`connect EACCES` to nodejs.org — outside the allowlisted registry.npmjs.org) and no make/gcc exists on the box. `npm ci --ignore-scripts --no-audit --no-fund` exits 0 and is sufficient: unit tests never boot the agentOS VM (`attachTierForTests`/fakes only), so the unbuilt native module is never loaded. tsc needs declarations only.
- Baseline at 3e7be33 (with `--ignore-scripts` install): `npx tsc --noEmit` exit 2 with exactly the 3 errors named in W110-01; `npm test` 12/12 pass.
- podman, openshell, herdr, gh are NOT available here; the live 00025 containers from the audit do not exist in this sandbox. All new tests must be pure-unit with fakes (existing suite already is).
</environment>

<decisions>
## Locked scope and operator decisions

- Planning only in this stage: no product code, no commits under `omp-sandbox/`, no push/PR (outer driver exports commits as a git bundle). Execution happens in stage 110-01 later.
- One plan only: `110-01-PLAN.md`. No RESEARCH.md (research disabled in `.planning/config.json`), no VALIDATION.md (Nyquist needs research artifacts; not applicable this run), no discuss phase (`discuss_mode: skip`).
- All six Greptile comments re-checked at HEAD and all six still apply (see source map merge-scope fact); none may be closed as already-fixed.
- The `seedFiles` API change (W110-07) updates `tests/paths.test.ts`'s direct seed test to the new contract — that is a contract migration, not a weakening; skip/symlink/binary-safety assertions must remain, moved to the layer that now reads bytes.
- W110-04 picks the "policy user from host identity" option (Greptile offers either); do not invent unverified OpenShell gateway uidmap keys — the static policy file's `run_as_user` is already ours to generate, mirroring how `gateway.toml` is generated per variant into `GW_STATE`.
- Tracker identifiers (SPE-18302 n2, SPE-18315/18316/18319 subissues, Notion URLs) live in commit trailers and the PR body only, never in committed planning guidance files.
- No secrets or token values anywhere; receipts must pass the dashboard redaction check.
</decisions>

<pitfalls>
## Pitfalls for the execution planner

- `npm test` file list is fixed in `package.json`; if a new test module is added it must be registered there — prefer extending the existing three modules.
- `server.ts` runs side effects on import (Core constructor, readline on stdin, ready event on stdout); the reply-mapping regression needs a small exported pure seam rather than importing the module as-is in a test.
- `#seed`/`#probeOmp` are private; seed/probe regressions assert at the exported layer (`seedFiles`, `vmRoundTrip`, `ensureGsd`) with fake tier/vm objects, per the `fakeCore` pattern in guards.test.ts.
- `vmRoundTrip`'s `cat` read-back and ls assertions must keep working after the probe moves — the probe still proves VM↔sandbox round trip, just through a private path.
- bwrap's private home is `/sbx/home` (under the hostRoot bind, host-visible but not the job output); podman's is `/sandbox/home` (container-private, created in `start`). The probe relocation must work for both mount tiers without writing into either's mounted workdir.
- Do not "fix" `#otherSandboxes`'s unknown-gateway branch semantics (`["<unknown: sandbox list failed>"]` when the gateway is still alive) — only the call target and types.
- The `..job` fix must not weaken line 23 (`p.split("/").includes("..")` refuses any real `..` component in the input) or the `escape` symlink case (realpath lands outside root → `../` prefix or absolute → refused).
- Keep the default (unmounted) podman policy at uid 1000; only the mount variant derives from the host identity. Preserve `userns = "keep-id"`.
- `gsdCommandsOnHost` currently has no callers — fix its path assumption, do not delete it.
</pitfalls>

<out_of_scope>
## Out of scope

- sbx-3 engine backend, sbx-4 routines/visibility, sbx-5 e2e (uplift nodes n3/n4/n7), block surfacing (n5) — later phases.
- The e2e mount smoke and receipts under `/workspace/sbx-runs/pr2` (box-only paths; this sandbox has no podman).
- pip-audit red on #53/#54/#55, Greptile on #54, pushing/merging anything, OMEGA_PRIME_OMP_ALLOW_ZAI.
- Any change outside `omp-sandbox/` in the execution stage, and any product change in this planning stage.
</out_of_scope>

<tracker>
## Tracker

Task 00030 (Notion `400bc1a0…9b582`); node n2 Linear SPE-18302; relevant subissues SPE-18315 (step 1), SPE-18316 (step 2), SPE-18319 (step 5). Kept out of this file per repo rule; cited in commit trailers by the parent.
</tracker>

<no_implementation>
## No implementation claim

This stage wrote planning artifacts only (`.planning/ROADMAP.md` v21 section, this context file, `110-01-PLAN.md`). No `omp-sandbox/` source, tests, or docs were modified; no push, PR, or remote was touched. Read-only evidence commands run: `git log/diff/status`, `npm ci --ignore-scripts`, `npx tsc --noEmit` (3 known errors), `npm test` (12/12 pass).
</no_implementation>

# Purple-Team Sidecar Audit (SPE-10593)

- Date: 2026-10-13, HEAD df14512.
- Evidence: `.planning/recon-evidence.md` rows R1-R8 (verified 2026-10-13, HEAD df14512). Citations below are row IDs + file:line from that log.
- Scope: audit of the EXISTING partial sidecar (`redteam` submodule + adapter), not greenfield — R6. The sidecar exists today as a pinned submodule, a default-off tool surface, and a vendored declarative pack.

## 1. Process/isolation

**Exists today**
- `redteam` submodule is quarantined: NEVER bootstrapped; test asserts redteam absent from bootstrap args and dir empty — R1, test_grokbot_bootstrap.py:756-757.
- Excluded from deploy build context — R2, deploy.py:274.
- Adapter is a pure HTTP proxy; NEVER imports `redteam/` at runtime — R6.
- Gateway is loopback-only by default (http://127.0.0.1:3333) — R6, mcp_server.py:747-776.

**Missing**
- No dedicated process identity/service account for a running sidecar.
- No resource caps (CPU/memory/time) on tempest missions.
- No own output/artifact area separate from main-repo outputs.

**Severity**: P1 — isolation from the host runtime is real, but nothing bounds a live engagement once enabled.
**Remediation**: dedicated service identity; resource limits at the gateway/sidecar boundary; isolated artifacts directory.
**Owner**: phase 79.

## 2. Authorization

**Exists today**
- Default-off gating: config default `redteam.enabled: False`; redteam_enabled() requires explicit True; env override OMEGA_PRIME_REDTEAM_ENABLED — R6, config.py:52-56; config.py:126-129; config.py:100-115.
- tempest_mission_start requires approval — R6, docs/tool-catalog.md:1246-1249.

**Missing**
- NO RoE/authorization artifact — R6 (gap g).
- No expiry/renewal semantics for authorization.
- No fail-closed BLOCKED start state.
- NO kill switch — R6 (gap g).

**Severity**: P0 — with only a config flag and env override between disabled and 11 live tempest_* tools, an enablement without an authorization boundary permits unsafe operation.
**Remediation**: signed RoE artifact with expiry; boot BLOCKED unless a valid, unexpired RoE is present; kill switch that halts missions and forces BLOCKED; all transitions logged (dimension 7).
**Owner**: phase 79.

## 3. Simulation library

**Exists today**
- Declarative pack vendored verbatim at omega_prime/redteam/pack/ (markdown/JSON only) — R6; pinned via submodule pin `831a6b0` — R6 (git submodule status).

**Missing**
- NO cases-as-data simulation library — R6 (gap g).
- No per-case versioning.
- No technique/severity/expected-behavior tags.
- Vendored pack ↔ upstream equivalence unchecked — R6 (gap e).

**Severity**: P1 — data-driven artifacts exist but are neither structured nor tagged for validation use.
**Remediation**: case schema (id, technique, severity, expected behavior); versioned case store; equivalence check on bump.
**Owner**: phase 79.

## 4. Detection validation

**Exists today**
- Pattern precedent in the main product: deterministic scripted-turn evals (golden.json + redteam.json wired into ci.yml verify) and hermetic PyRIT battery — R3, omega_prime/evals/runner.py; tests/test_pyrit_target.py. This is precedent, not sidecar detection validation.

**Missing**
- NO detection-validation pass criteria — R6 (gap g).
- No expected-control-per-case comparison.
- No rule that a case passes only with validated detection.

**Severity**: P1 — a mission could return success with no validated control, producing false assurance.
**Remediation**: per-case expected-control map; pass requires observed behavior matching the case's expected detection; otherwise FAIL/INCONCLUSIVE.
**Owner**: phase 79.

## 5. Findings pipeline

**Exists today**
- Adjacent only: audit chain records hashed tool-call args — R5 (audit.py); metrics interceptors count calls but enforce nothing — R8. No sidecar findings handling exists.

**Missing**
- NO findings pipeline/dedupe — R6 (gap g).
- No structured reports.
- No tracked work items for findings.

**Severity**: P1 — findings from engagements would be unstructured, duplicable, and untracked.
**Remediation**: structured findings schema; dedupe keying; export to tracked work items.
**Owner**: phase 79.

## 6. Upgrade resilience

**Exists today**
- Submodule pinned `831a6b0` (= v1.2~1), local checkout clean/detached at pin — R6 (git submodule status).
- Consistency triangle INTACT across gated set, FAMILIES, tool catalog, roster, policies; feature-disabled rows derived honestly — R6, setup_check.py:125-138; catalog.py:39,77; docs/tool-catalog.md:1238-1315; roster yaml:189-200; policies/omega-prime.json:95-105; local_validation.py:1084-1106.

**Missing** (all freshness gaps from R6)
- (a) no runtime/CI check of checkout vs pin; (b) no Dependabot `gitsubmodule` ecosystem (only pip/actions/docker in .github/dependabot.yml); (c) no supply-chain audit of redteam Node deps (supply-chain.yml is pip-only; no cargo-deny analog); (d) no documented bump procedure (VENDOR.md:84-89 + two provenance docstrings are hand-edited); (e) vendored pack ↔ upstream equivalence unchecked; (f) adapter hand-mirrors upstream tool surface — upstream API drift breaks it silently; NO drift→DEGRADED reporting.
- Pin is ~2 releases behind upstream (v1.2 AND v1.3 published, upstream main `06c11a5`) — R6 (git submodule status).

**Severity**: P0 — upstream API drift breaks the adapter silently (R6, gap f); silent breakage is exactly the failure class the drift→DEGRADED requirement exists to prevent.
**Remediation**: checkout-vs-pin CI/runtime assertion; `gitsubmodule` Dependabot ecosystem; redteam Node dep audit job; mechanical documented bump procedure with generated provenance; pack equivalence check; interface contract tests against upstream tool surface; any drift signal → DEGRADED state with surfaced status.
**Owner**: phase 79.

## 7. State machine

**Exists today**
- Nothing. The only gate is the boolean enable flag + env override — R6, config.py:52-56; config.py:100-115; config.py:126-129.

**Missing**
- NO state machine (IDLE/RUNNING/DEGRADED/BLOCKED/ERROR) and no logged transitions — R6 (gap g).

**Severity**: P1 — without it there is no runtime visibility, no transition audit trail, and no durable BLOCKED semantics (BLOCKED enforcement itself is the P0 in dimension 2).
**Remediation**: implement the five-state machine with persisted, audit-logged transitions; RoE validity and drift signals drive BLOCKED/DEGRADED entry.
**Owner**: phase 79.

## 8. Credential/network posture

**Exists today**
- Loopback-only gateway default (http://127.0.0.1:3333) with bearer via T3MP3ST_BEARER_TOKEN/_FILE or bearer_token_file — R6, mcp_server.py:747-776.
- Platform-wide: env-var creds only for policy-allowlisted hosts — R4 (credentials/broker.py); redaction everywhere + SecretLeakError diagnostics gate — R4 (credentials/redact.py; grokbot/secret_guard.py); mounted secret file, deploy artifacts never embed secrets — R2; unauthenticated /metrics 401s asserted — R5, verify.py:557-567.

**Missing**
- No dedicated sidecar credential identity with TTL/rotation for the tempest bearer (rotation is MANUAL CLI, platform-wide — R4).
- No egress restriction of the sidecar to the bot's own endpoints.

**Severity**: P1 — transport is loopback and tokens are file-based, but a live engagement has no dedicated identity and no egress allowlist.
**Remediation**: dedicated credential minted via the existing token store pattern (R4, tokens.py:256-285,345-426,495-523) with TTL; egress allowlist limited to the bot's own endpoints; violations → ERROR.
**Owner**: phase 79.

## Gap table

| ID | Gap | Severity | Remediation | Owner phase | Remediation status |
|----|-----|----------|-------------|-------------|--------------------|
| GAP-01 | Freshness (a): no runtime/CI check of checkout vs pin (R6) | P0 | CI + startup assertion comparing submodule SHA and clean status; mismatch → DEGRADED | 79 | scheduled-phase-79 |
| GAP-02 | Freshness (b): no Dependabot `gitsubmodule` ecosystem (R6, .github/dependabot.yml) | P2 | Add `gitsubmodule` ecosystem alongside pip/actions/docker | 79 | scheduled-phase-79 |
| GAP-03 | Freshness (c): no supply-chain audit of redteam Node deps; supply-chain.yml is pip-only (R6) | P1 | Add Node dep audit job for redteam (pip-audit / cargo-deny analog) | 79 | scheduled-phase-79 |
| GAP-04 | Freshness (d): no documented bump procedure; VENDOR.md:84-89 + two provenance docstrings hand-edited (R6) | P2 | Mechanical bump procedure doc; generate provenance instead of hand-editing | 79 | scheduled-phase-79 |
| GAP-05 | Freshness (e): vendored pack ↔ upstream equivalence unchecked (R6) | P1 | Hash/diff equivalence check in bump flow and CI | 79 | scheduled-phase-79 |
| GAP-06 | Freshness (f): adapter hand-mirrors upstream tool surface; upstream API drift breaks it silently (R6) | P0 | Interface contract tests against upstream mcp-server surface; drift detection wired to DEGRADED | 79 | scheduled-phase-79 |
| GAP-07 | Freshness (g) / dim 2: no RoE/authorization artifact, no expiry, no fail-closed BLOCKED start (R6) | P0 | Signed RoE artifact with expiry; boot BLOCKED unless valid and unexpired | 79 | scheduled-phase-79 |
| GAP-08 | Freshness (g) / dim 2: no kill switch (R6) | P0 | Kill switch halts missions, forces BLOCKED, logs transition | 79 | scheduled-phase-79 |
| GAP-09 | Freshness (g) / dim 7: no IDLE/RUNNING/DEGRADED/BLOCKED/ERROR state machine, no logged transitions (R6) | P1 | Five-state machine with persisted audit-logged transitions | 79 | scheduled-phase-79 |
| GAP-10 | Freshness (g) / dim 3: no cases-as-data simulation library, versioning, technique/severity/expected-behavior tags (R6) | P1 | Case schema + versioned library; tags required per case | 79 | scheduled-phase-79 |
| GAP-11 | Freshness (g) / dim 4: no detection-validation pass criteria, no expected-control-per-case comparison (R6) | P1 | Expected-control map per case; pass only on validated detection | 79 | scheduled-phase-79 |
| GAP-12 | Freshness (g) / dim 5: no findings pipeline, dedupe, or tracked work items (R6) | P1 | Structured findings schema, dedupe keys, work-item export | 79 | scheduled-phase-79 |
| GAP-13 | Freshness (g) / dim 6: no drift→DEGRADED reporting (R6) | P0 | Wire pin/drift signals to DEGRADED state and surfaced status | 79 | scheduled-phase-79 |
| GAP-14 | Dim 1: no resource caps, dedicated sidecar identity, or own output area | P1 | Service identity; resource limits at gateway/sidecar boundary; isolated artifacts dir | 79 | scheduled-phase-79 |
| GAP-15 | Dim 8: no dedicated sidecar credential lifecycle (TTL/rotation for tempest bearer); no egress restriction to bot's own endpoints (R4 rotation is manual) | P1 | Dedicated cred via token store pattern with TTL; egress allowlist; violation → ERROR | 79 | scheduled-phase-79 |

## Ship decision

Per user decision 2026-10-13: the purple-team sidecar ships **BLOCKED**, with no live engagements, until the user signs a rules-of-engagement artifact. **No RUNNING state exists until then.** GAP-07/GAP-08 are the gating P0s for any future unblock.

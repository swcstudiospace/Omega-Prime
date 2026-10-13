---
plan: 79-03
status: complete
phase: 79
---

# 79-03 Summary: Pin-freshness automation

## Tasks completed

- Task 1: `sidecar/provenance.py` (stdlib-only; check/pack-diff/surface; `--root`/`--gitlink` hermetic overrides; exit 0 agree / 1 drift / 2 environment). Brings-up defect fixed: VENDOR.md pin extraction is scoped to the `## T3MP3ST red team pack` section (file-wide grep matched the Hermes pin); regression test added.
- Task 2: `.github/dependabot.yml` `gitsubmodule` entry (weekly, directory /); `redteam-provenance` job in `ci.yml` (submodules:true, contents:read, runs check/pack-diff/surface, fail-closed); `redteam-node-audit` job in `supply-chain.yml` (redteam-only submodule init, npm ci --ignore-scripts, npm audit --omit=dev --audit-level=high, dated TRIAGED block mirroring the PYSEC-2026-4114 precedent, fails on untriaged high+).
- Task 3: `docs/red-team.md` "Bumping the redteam submodule" 6-step runbook (+ relocation note for plan-01's `omega_prime/redteam/provenance.py` → `sidecar/provenance.py`, checker advisory 4; dry-run framed as rehearsal, not attestation); `test_sidecar_packaging.py` asserting the `/sidecar` Docker-context exclusion (already applied by parent in deploy.py/.dockerignore — subsumes the planned reports-only rule) and bootstrap path-reference absence.

## Verification

`test_sidecar_provenance.py` 18 passed (incl. multi-pin section-scoping); `test_sidecar_packaging.py` 4 passed; `provenance.py check|pack-diff|surface` exit 0 at pin 831a6b0 on the live checkout; all three workflow files parse as YAML with no write-permission scopes added (strengthened verify per checker advisory 1); included in the 2642-passed full run.

# PLAN-F5: Delta advisory scanner on pin changes

## Problem

Pin bumps to the `redteam/` gitlink (upstream
`https://github.com/swcstudiospace/purple-team.git`, currently pinned at
`831a6b0ee09f12c0f069f51d62b2f0945d505980` per `VENDOR.md` and the
`Upstream commit:` docstring in `omega_prime/redteam/__init__.py`) are
security events nobody can review. The weekly `redteam-node-audit` job in
`.github/workflows/supply-chain.yml` runs `npm audit --json` inside
`redteam/` and gates on `scripts/redteam-audit-triage.py`, but triage is a
single-pin pass/fail: it reports untriaged high-or-above advisories for
the new pin only, with no comparison against the old pin. A reviewer on a
Dependabot bump PR (`.github/dependabot.yml`, `gitsubmodule` ecosystem)
cannot answer "did this bump introduce, fix, or rescind advisories, or
change any severity?" without manually diffing two audit payloads. The
`docs/red-team.md` bump runbook (step 6, "Open a PR with CI proof")
requires the audit result but defines no delta artifact, so regressions
hide behind an unchanged pass/fail verdict.

## User-facing behavior

A new stdlib-only script, `scripts/redteam-audit-delta.py`, diffs two
audit inputs (old pin vs new pin) and emits a Markdown report plus a
one-line verdict:

- Inputs may each be either an `npm audit --json` payload (the schema
  already handled by `scripts/redteam-audit-triage.py`: `vulnerabilities`
  map with `severity` + `via`, legacy `advisories` map, fail-closed on the
  `"error"` member or a document missing both keys) or an
  `npm-shrinkwrap`/`package-lock.json` file carrying already-resolved
  advisory metadata — the script sniffs the top-level keys and parses
  accordingly, failing closed (nonzero + reason on stderr) on unknown
  shapes, exactly like the triage script's fail-closed contract.
- The report (`--out report.md`) contains four sections: **advisories new**
  (keys in new, absent in old), **fixed** (keys in old, absent in new),
  **rescinded** (keys whose advisory record disappeared upstream while the
  package range is unchanged — distinguished from fixed by a marker the
  parser derives from the payload), and **severity drift** (same key, new
  severity != old severity, e.g. `moderate -> high`), each row naming the
  finding key, severity pair, and package (`module_name` / map key).
- The final verdict line is exactly one of `improved` (fixed non-empty,
  new empty), `regressed` (new non-empty, or any severity drift upward),
  or `unchanged` (neither), printed to stdout and appended to the report
  footer so the PR-comment step can quote it verbatim.
- Wired as a PR-comment step in the `docs/red-team.md` bump runbook: the
  runbook gains a step 6b instructing the bumper to run the delta script
  on the old-pin and new-pin audits and paste the verdict + report link
  into the bump PR body. No CI gate changes; the existing
  `redteam-node-audit` pass/fail and `redteam-provenance` (`check`,
  `pack-diff`, `surface` via `sidecar/provenance.py`) jobs are untouched.

## Interface

Exact command syntax (mirrors the triage script's plain-`python3`
invocation; no new config nodes, no `OMEGA_PRIME_*` env vars, no
`omega_prime/commands/` slash command, no `omega_prime/cron/heartbeat.py`
kind — this is a repo script, not a host surface):

```bash
python3 scripts/redteam-audit-delta.py \
  --old <audit-json|lock> --new <audit-json|lock> --out report.md
```

- `--old`, `--new` (both required): paths to either `npm audit --json`
  output or lockfiles. Format auto-detected per file; mixing formats
  (old lock vs new audit JSON) is allowed.
- `--out` (required): Markdown report destination; parent dirs are not
  created — missing parent is a usage error (exit 2).
- Exit codes: `0` verdict computed (any verdict, including `regressed` —
  the script informs, it does not gate); `1` unreadable input, unparsable
  JSON, unknown shape, or npm `"error"` member (names the file + reason
  on stderr, same style as `redteam-node-audit FAILED: ...`); `2` CLI
  usage error (missing flag, missing `--out` parent).
- Reused parser contract from `scripts/redteam-audit-triage.py::triage`:
  finding key extraction (`via` scan for `GHSA`/`CVE`/URL falling back to
  the map name; legacy `advisories` via `url or cves or module_name or
  id`) and severity normalization (`str(...).lower()`). The delta script
  imports nothing from the triage script (kept decoupled so either file
  runs standalone via `python3`), but duplicates the key function under a
  `_finding_key()` helper with identical semantics, noted in a comment.
- Runbook wiring: `docs/red-team.md` step 6 gains sub-step text naming
  the exact command above with `--old` = old-pin audit artifact and
  `--new` = new-pin audit artifact.

## Non-goals

- Auto-triage: no writes to the `TRIAGED` block in
  `scripts/redteam-audit-triage.py`, no automatic rationale generation,
  no changes to the `redteam-node-audit` pass/fail threshold.
- OSV queries: no network access of any kind; the script is hermetic and
  runs offline against the two local input files (same offline posture as
  the `sidecar/provenance.py` subcommands).
- No lockfile mutation, no `npm ci`/`npm audit` invocation, no submodule
  moves — the script reads inputs only.
- No CI workflow edits (`.github/workflows/supply-chain.yml`,
  `.github/workflows/ci.yml`, `.github/dependabot.yml` unchanged), no new
  pytest plugins or dependencies (`pyproject.toml` `testpaths =
  ["omega_prime/tests"]` suite only).

## Done-criteria

Each independently verifiable:

1. `python3 scripts/redteam-audit-delta.py --old <a> --new <b> --out
   report.md` exits `0` and `report.md` contains the four section headers
   plus a `Verdict: <one-of-three>` footer line.
2. Hermetic pytest (`omega_prime/tests/test_redteam_audit_delta.py`,
   stdlib + pytest only, no network, no `redteam/` checkout) passes with
   committed fixture lockfiles covering all four report classes: at least
   one new advisory, one fixed advisory, one rescinded advisory, and one
   severity-drift row each assertable from the parsed report.
3. A recorded run on the real pin-`831a6b0` audit vs the upstream v1.3
   lock (fixtures committed under `omega_prime/tests/data/`, e.g.
   `redteam-audit-831a6b0.json` + `redteam-lock-v1.3.json`) produces a
   report whose verdict line matches the hand-computed expectation
   documented in the test module docstring.
4. `docs/red-team.md` bump runbook step 6 contains the PR-comment
   sub-step with the exact command syntax from Interface.
5. Unknown-shape input (JSON missing both `vulnerabilities` and
   `advisories`, or an npm `"error"` payload) exits nonzero naming the
   file, mirroring the triage fail-closed messages.

## Test plan

At the repo's pytest level (`pyproject.toml`, `testpaths =
["omega_prime/tests"]`, Python `>= 3.11`):

- Happy: old/new audit-JSON fixture pair with disjoint advisory keys
  yields new + fixed sections populated, drift empty, verdict computed;
  mixed-format pair (old lockfile + new audit JSON) parses both sides.
- Edge: identical inputs -> all sections empty, verdict `unchanged`;
  same key with `high -> critical` severity -> single drift row, verdict
  `regressed`; legacy `advisories`-shaped payloads parse via the
  `url/cves/module_name/id` key rule.
- Failure: malformed JSON, missing file, unknown top-level shape, and
  `"error"`-member payload each exit nonzero with the filename + reason
  on stderr; missing `--out` parent exits `2`. All failure tests use
  `tmp_path` outputs and committed fixtures only — no network, no live
  `npm audit`, no submodule checkout.

## Docs surfaces

- `docs/red-team.md`: step 6 ("Open a PR with CI proof") gains the delta
  PR-comment sub-step (command + verdict-paste instruction). Only
  user-facing docs change.
- `scripts/redteam-audit-delta.py` module docstring: usage + verdict
  semantics (mirrors the triage script's docstring style).
- No changes to `VENDOR.md`, `omega_prime/redteam/__init__.py` pin
  records, `docs/tool-catalog.md`, or workflow files.

## Rollback

Single revert: `git rm scripts/redteam-audit-delta.py
omega_prime/tests/test_redteam_audit_delta.py <fixture files> && git
checkout -- docs/red-team.md` (or one `git revert` of the feature
commit). No config, workflow, or pin state to unwind; the triage job and
provenance checker behave exactly as before.

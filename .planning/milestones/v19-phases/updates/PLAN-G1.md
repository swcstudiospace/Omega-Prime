# PLAN-G1: Pin-bump automation

## Problem

The `redteam/` gitlink is the only moving part of the red-team module,
and bumping it is a 6-step manual runbook in `docs/red-team.md:235-319`
("Bumping the redteam submodule"): move the pin (`git submodule update
--init redteam`, checkout, `git add redteam`), run the three
`sidecar/provenance.py` subcommands (`check`, `pack-diff`, `surface`
per `sidecar/provenance.py:362-370`), update the two pin records (the
`Commit:` line in `VENDOR.md:85` and the `Upstream commit:` docstring in
`omega_prime/redteam/__init__.py:5`), re-vendor the pack on divergence
(`redteam/grokbot/` over `omega_prime/redteam/pack/`, LICENSE-only
delta skipped), refresh the tool surface on legitimate drift, and open
a PR with CI proof plus the `scripts/redteam-audit-delta.py` verdict.
Every hand bump risks skipping or misordering a step — a stale
`VENDOR.md` line or a half-copied pack fails `check_pins` in
`sidecar/provenance.py:124` far from the cause.

The audit that gates the bump is currently RED: the pinned upstream
(`831a6b0ee09f12c0f069f51d62b2f0945d505980` per `VENDOR.md:85` and the
`__init__.py:5` docstring) carries one critical (`proxy-addr`) plus 7
high advisories, also present at upstream v1.3 (PLAN-F1). The bump
itself must therefore prove the new pin's audit state mechanically,
not by pasting console output.

## User-facing behavior

One CLI performs the whole runbook in order and stops loudly on the
first failure — no partial pin records left behind:

1. `python -m omega_prime.grokbot.pin_bump --to <ref>` validates the
   ref (40-hex SHA or `v`-tag resolvable via `git ls-remote`; anything
   else is a usage error), fetches upstream inside `redteam/`, checks
   out the ref, and stages the gitlink (`git add redteam`).
2. It re-vendors the pack: copies `redteam/grokbot/` over
   `omega_prime/redteam/pack/` byte-verbatim (same relative paths),
   preserving `pack/LICENSE` (no upstream counterpart, skipped by
   `pack-diff`) and copying binary assets (`pack/avatars/*.png`) when
   upstream changed them — the `docs/red-team.md:278-285` rule.
3. It rewrites the `Commit:` line (`VENDOR.md:85`) and the
   `Upstream commit:` docstring (`omega_prime/redteam/__init__.py:5`)
   to the resolved SHA, then runs `check` and `pack-diff` from
   `sidecar/provenance.py` until both are green (re-copy loop, max 2
   passes; still diverged = hard failure, exit 1).
4. It runs `npm audit --omit=dev --audit-level=high --json` at the new
   pin (same chain as `supply-chain.yml` and the `redteam-remediation`
   workflow), triaged by `scripts/redteam-audit-triage.py::triage`
   against its dated `TRIAGED` block (`redteam-audit-triage.py:27`).
5. It emits the delta report: `scripts/redteam-audit-delta.py --old
   <old-pin audit> --new <new-pin audit> --out report.md`, quoting the
   single-word verdict (`improved` / `regressed` / `unchanged`) on
   stdout. `--old` defaults to the pre-bump pin's saved audit artifact
   when present, else requires an explicit `--old-audit` path.
6. With `--open-pr`, it creates branch `pin-bump-<short-sha>`, commits
   (gitlink + `VENDOR.md` + `__init__.py` + pack), pushes, and runs
   `gh pr create` with the delta verdict and `report.md` link in the
   body (the `docs/red-team.md:307-319` post-verdict step).

`--skip-audit` skips steps 4–5 (offline/provenance-only bump; stdout
says `audit-skipped` so the PR body cannot quote a verdict).

## Interface

- New module `omega_prime/grokbot/pin_bump.py`, CLI pattern mirrors
  `omega_prime/grokbot/roe_lifecycle.py:147-186` (`argparse` +
  `main(argv) -> int`, `if __name__ == "__main__": sys.exit(main())`):

  ```bash
  python -m omega_prime.grokbot.pin_bump --to <sha|tag> \
    [--old-audit <path>] [--skip-audit] [--open-pr] [--json]
  ```

  `--to` is required; `--skip-audit` and `--open-pr` are flags;
  `--json` prints machine-readable `{pin, verdict, report}`.
- Exit codes (documented in `--help` and `docs/red-team.md`):
  `0` = bump complete, provenance green, audit green-or-`--skip-audit`;
  `1` = audit regressed / untriaged high+ at new pin / provenance still
  red after the re-copy loop; `2` = usage error (bad ref, missing
  `--old-audit` when no saved artifact, `gh` missing under
  `--open-pr`); `3` = degraded — `node`/`npm` absent or the submodule
  unreachable, with the completed prefix steps named on stderr.
- Config keys: none new. The CLI takes no `OMEGA_PRIME_*` env vars and
  reads no `omega-prime.json` tree — it follows the
  `omega_prime/config.py:16-17` `_ENV_PREFIX` convention by adding
  nothing to it (bump pins are git state, not family flags; the
  existing `OMEGA_PRIME_REDTEAM_*` keys in `config.py:60-340` are
  untouched). Any future cadence knob would be
  `OMEGA_PRIME_REDTEAM_PIN_BUMP_*`, reserved but not created here.
- Heartbeat kinds: none new. `HEARTBEAT_KIND` (`"session_heartbeat"`)
  and `ROE_EXPIRY_KIND` (`"roe_expiry"`,
  `omega_prime/cron/heartbeat.py:23,125`) are untouched — the bumper is
  an operator-invoked CLI, not a scheduled job; the weekly
  `redteam-remediation.yml` workflow keeps its own cadence.
- Subprocess seam: `run(cmd, cwd)` injectable runner (default
  `subprocess.run`) so `git`, `npm`, and `gh` calls are hermetic in
  tests; the `node`-present check (`node --version`, absent = exit 3)
  runs before any audit step.

## Non-goals

- Auto-merging the bump PR or approving upstream PRs (operator
  reviews; the remediation workflow never merges either).
- Triaging advisories: the CLI never writes the `TRIAGED` block in
  `scripts/redteam-audit-triage.py:27` — triage stays a dated human
  entry with rationale, mirroring the `PYSEC-2026-4114` precedent.
- Touching the tool surface: on `surface` DRIFT the CLI reports and
  stops; updating `REDTEAM_TOOL_NAMES` /
  `bot-interface.json tool_names` stays a manual legitimacy decision
  per `docs/red-team.md:287-297`.
- Scheduling: no cron job, no Dependabot change, no workflow edits.

## Done-criteria

- [ ] `--to <sha>` on a fixture pair moves the gitlink, re-vendors the
      pack byte-identical (`pack-diff` green), rewrites both pin
      records, and `check` passes — verifiable by running the two
      `sidecar/provenance.py` subcommands after the CLI exits 0.
- [ ] Green path exits 0 with a `report.md` whose footer verdict
      (`improved`/`unchanged`) is quoted verbatim on stdout.
- [ ] Regressed path (new untriaged high+ vs old audit) exits 1 and
      leaves the pre-bump pin records restorable via the single revert
      below; nothing is pushed.
- [ ] Degraded path (`node` absent from `PATH`) exits 3 naming the
      missing binary and the completed steps; `--skip-audit` exits 0
      with `audit-skipped` on stdout and no verdict quoted.
- [ ] `--open-pr` creates `pin-bump-<short-sha>`, commits exactly the
      bump set, and calls `gh pr create` with the delta verdict in the
      body (proven with a fake `gh` on `PATH`).
- [ ] Usage errors (bare ref, unresolvable tag, missing `gh`) exit 2.

## Test plan

Hermetic suite `omega_prime/tests/test_pin_bump.py` (no network, no
real `npm`/`gh` — fake `run()` runner + fixture trees):

- Happy: fixture upstream/pack pair with one changed `SKILL.md`;
  assert re-vendor copies it, `LICENSE` preserved, both pin records
  rewritten, exit 0, verdict `improved` quoted from a canned delta.
- Edge: already-identical pack (no copy needed, still exit 0);
  `--skip-audit` with `node` absent (exit 0, `audit-skipped`);
  tag-form `--to v1.4` resolved via canned `ls-remote`.
- Failure: canned new-pin audit with one extra high advisory (exit 1);
  `node` missing without `--skip-audit` (exit 3); bad ref (exit 2);
  `pack-diff` still diverged after 2 passes (exit 1); `--open-pr`
  without `gh` (exit 2). Each asserts stdout/stderr contract lines.
- Vendoring correctness: binary fixture (`avatars/*.png` changed
  upstream) copied; `MISSING-UPSTREAM`/`EXTRA-UPSTREAM` cases from
  `pack-diff` vocabulary covered by fixture pairs.

## Docs surfaces

- `docs/red-team.md` "Bumping the redteam submodule": replace steps
  1–4 + 6b with the one-command form (keeping the manual sequence as
  the fallback the CLI implements), plus the exit-code table.
- Module docstring in `pin_bump.py` carries the 7-step order so
  `--help` and the doc page cannot drift.

## Rollback

Single revert: `git reset --hard HEAD~1 && git submodule update
--init redteam` restores the old gitlink; the bump commit is atomic
(one commit holds gitlink + `VENDOR.md` + `__init__.py` + pack), so no
partial pin records survive. `--open-pr` pushes only after local green.

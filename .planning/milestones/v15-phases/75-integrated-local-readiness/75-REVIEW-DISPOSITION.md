---
phase: 75
review: 75-REVIEW.md
titles: json
findings:
  - id: WR-09
    severity: warning
    disposition: fixed
    title: "`validate_report` still leaves `summary.problems` shape unchecked"
  - id: WR-10
    severity: warning
    disposition: fixed
    title: "`EventStore` allocates `seq` from in-memory count across processes"
  - id: IN-07
    severity: info
    disposition: skipped
    title: "Generated command catalog is cached for process lifetime"
  - id: WR-01
    severity: warning
    disposition: fixed
    title: "stdio serve silently drops `--work-root`"
  - id: WR-02
    severity: warning
    disposition: fixed
    title: "Bearer token left in server process env for whole SSE serve"
  - id: WR-03
    severity: warning
    disposition: fixed
    title: "Unvalidated git refs in mobile VCS seam allow option injection"
  - id: WR-04
    severity: warning
    disposition: fixed
    title: "`RosterStore._save` is non-atomic where `IntakeStore` is atomic"
  - id: WR-05
    severity: warning
    disposition: fixed
    title: "`EventStore.records` chokes on a single torn line"
  - id: WR-06
    severity: warning
    disposition: fixed
    title: "`memory_retain` secret refusal covers content but not metadata"
  - id: WR-07
    severity: warning
    disposition: fixed
    title: "`validate_report` leaves `summary`/`checks` shapes unchecked"
  - id: WR-08
    severity: warning
    disposition: fixed
    title: "Operator doc still promises `--json` on stdout unconditionally"
  - id: IN-01
    severity: info
    disposition: fixed
    title: "`run_preflight_doctor` is dead code with a latent framing hazard"
  - id: IN-02
    severity: info
    disposition: skipped
    title: "Screenshot names derive from salted `hash()`"
  - id: IN-03
    severity: info
    disposition: skipped
    title: "Tap coordinates admit `bool`"
  - id: IN-04
    severity: info
    disposition: skipped
    title: "`artifact_size_delta` OSError escapes the handler wrapper"
  - id: IN-05
    severity: info
    disposition: skipped
    title: "Blocking sync HTTP calls inside the async loopback session"
  - id: IN-06
    severity: info
    disposition: skipped
    title: "Git provenance subprocesses inherit the full parent env"
open: 0
total: 17
recorded: 2026-10-12T20:08:24.228Z
---

# Phase 75: Code Review Disposition

| Finding | Severity | Disposition | Source |
|---------|----------|-------------|--------|
| WR-09 | warning | fixed | 2c938b6: validate_report refuses a summary.problems that is not a list of strings with the existing ValueError; regression test in test_grokbot_readiness.py |
| WR-10 | warning | fixed | c9be6d1: append takes the next seq from the file's last complete record inside the flock; two-store regression test in test_lead.py |
| IN-07 | info | skipped | Info severity; the generated catalog is built once per host process by design, and a new skill or routine is served after the documented host restart |
| WR-01 | warning | fixed | c26305f (PR #18; pre-squash 079f026): stdio serve forwards --work-root; the re-review at c26305f confirmed it resolved (not in the current review) |
| WR-02 | warning | fixed | c26305f (PR #18; pre-squash 079f026): the launcher no longer keeps the bearer token in the serve environment; the re-review at c26305f confirmed it resolved (not in the current review) |
| WR-03 | warning | fixed | e4158d7: refs validated and `--end-of-options` added; regression tests in test_mobile.py; full suite 2360 passed (not in the current review) |
| WR-04 | warning | fixed | f466e26: RosterStore saves through the shared atomic_write_text; regression tests in test_lead.py (not in the current review) |
| WR-05 | warning | fixed | bd6b550: only a torn trailing NDJSON line is skipped; earlier corruption still raises; regression tests in test_lead.py (not in the current review) |
| WR-06 | warning | fixed | 1319633: the unchanged redact gate now covers metadata too; regression test in test_lead.py (not in the current review) |
| WR-07 | warning | fixed | 039ab10 + 8126de2: summary/checks shapes validated, raising the existing ValueError; regression test in test_grokbot_readiness.py; mypy clean (not in the current review) |
| WR-08 | warning | fixed | c26305f (PR #18; pre-squash 4191235): the docs now say oneclick --json writes its JSON object to stderr under served stdio, where stdout carries MCP frames; the re-review at c26305f confirmed it resolved (not in the current review) |
| IN-01 | info | fixed | c26305f (PR #18; pre-squash 079f026): dead run_preflight_doctor removed from oneclick.py; the re-review at c26305f confirmed it resolved (not in the current review) |
| IN-02 | info | skipped | Info severity, outside the Critical+Warning fix scope (not in the current review) |
| IN-03 | info | skipped | Info severity, outside the Critical+Warning fix scope (not in the current review) |
| IN-04 | info | skipped | Info severity, outside the Critical+Warning fix scope (not in the current review) |
| IN-05 | info | skipped | Info severity, outside the Critical+Warning fix scope (not in the current review) |
| IN-06 | info | skipped | Info severity, outside the Critical+Warning fix scope (not in the current review) |

Dispositions: `open` (recorded, not yet triaged), `fixed`, `skipped`, `deferred`.
Set `deferred` by hand and put the reason in the Source cell; both are preserved. A `|` in the reason is kept as prose and escaped on the next run.
Re-running the gate keeps every row it can. A row the current review no longer reports is kept and its Source cell flagged, so a finding does not leave this record silently. ONE exception: when a finding id is REUSED by a different finding, the earlier decision cannot keep a row — the id is taken — and it is dropped. A RECORDED decision (anything but `open`) is named on the console when that happens; a row still at `open` is replaced silently, because `open` records no decision to lose.

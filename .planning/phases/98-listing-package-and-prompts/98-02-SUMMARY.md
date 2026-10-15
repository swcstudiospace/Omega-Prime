---
phase: 98-listing-package-and-prompts
plan: 02
subsystem: grokbot-listing
status: complete
requirements:
  - INS-01
  - INS-02
  - INS-03
tags: [grok-bot, listing, install-prompt, follow-up, drift-test]
key-files:
  created:
    - omega_prime/grokbot/listing/prompts/install.md
    - omega_prime/grokbot/listing/prompts/follow-up/01-verify-install.md
    - omega_prime/grokbot/listing/prompts/follow-up/02-help-tour.md
    - omega_prime/grokbot/listing/prompts/follow-up/03-roster-health-check.md
    - omega_prime/grokbot/listing/prompts/follow-up/04-first-real-task-code-review.md
    - omega_prime/grokbot/listing/prompts/follow-up/05-troubleshooting.md
    - omega_prime/grokbot/listing/prompts/follow-up/INDEX.md
    - omega_prime/tests/test_grokbot_listing.py
  modified: []
decisions:
  - "Follow-up mapping overridden by the user: 01 /omega-roster, 02 /omega-help, 03 /omega-doctor, 04 /omega-code-review, 05 /omega-doctor (triage). The pinned mapping test enforces it."
  - "Install prompt fence is spliced from SETUP.md section 1 by script and confirmed with cmp, not retyped."
  - "Live Grok Bot import is UNPROVEN in every listing markdown file."
metrics:
  tasks: 3
  commits: 4
  completed: 2026-10-15
commits:
  - b89fd37 test(98-02): add listing drift, command, index, licence and claim tests
  - a2b446d feat(98-02): add install prompt copied from SETUP.md section 1
  - bde18fd feat(98-02): add five follow-up prompts and their index
  - 3ce0fa6 test(98-02): shorten follow-up prompt test name
---

# Phase 98 Plan 02: Install prompt, follow-up prompts and listing test Summary

The install prompt is a byte-identical copy of SETUP.md section 1, five follow-up prompts each send one existing `/omega-*` command under the user's override mapping, and a 13-test file pins the drift, command, index, licence, claim and link rules for the whole listing package. Live Grok Bot import is UNPROVEN throughout.

## What Was Built

- **Install prompt (INS-01):** `prompts/install.md` holds a single `text` fence. Its body is the SETUP.md slice from `<task name="setup-omega-prime" version="1">` through the first `</task>`, spliced in by a script. `cmp` against a slice extracted from SETUP.md reports identical bytes. The stdio command in the prompt is the `mcp.json` entry with `${CURSOR_PLUGIN_ROOT}` replaced by `/workspace/omega-prime`.
- **Follow-up prompts (INS-02):** five files, each with one `text` fence holding one command, a link to its command file, an evidence level, a triage or purpose section where needed, and a `## What this does not prove` section with at least two bullets.
  - `01-verify-install.md` sends `/omega-roster`.
  - `02-help-tour.md` sends `/omega-help`.
  - `03-roster-health-check.md` sends `/omega-doctor`.
  - `04-first-real-task-code-review.md` sends `/omega-code-review <file, diff or branch to review>`.
  - `05-troubleshooting.md` sends `/omega-doctor`, with a `## Triage steps` section: show the full result, stop at the first check whose `green` is `false`, send no other command.
- **Index (INS-03):** `prompts/follow-up/INDEX.md` has the header `| Order | Prompt | Sends | Use it when |` and one row per prompt. Each prompt file is linked exactly once.
- **Test file:** `omega_prime/tests/test_grokbot_listing.py` has 13 tests covering the install drift, the mcp.json stdio command, command-file existence for every `/omega-*` name, the one-command rule and limits section, the index, the pinned mapping, the root LICENSE pin and git HEAD match, the licence notice, the changelog heading, UNPROVEN and forbidden-claim checks, emoji and secret checks, relative links and anchors, and the README claims table.

## Files

| File | Change |
| --- | --- |
| `omega_prime/tests/test_grokbot_listing.py` | created |
| `omega_prime/grokbot/listing/prompts/install.md` | created |
| `omega_prime/grokbot/listing/prompts/follow-up/01-verify-install.md` | created |
| `omega_prime/grokbot/listing/prompts/follow-up/02-help-tour.md` | created |
| `omega_prime/grokbot/listing/prompts/follow-up/03-roster-health-check.md` | created |
| `omega_prime/grokbot/listing/prompts/follow-up/04-first-real-task-code-review.md` | created |
| `omega_prime/grokbot/listing/prompts/follow-up/05-troubleshooting.md` | created |
| `omega_prime/grokbot/listing/prompts/follow-up/INDEX.md` | created |

Not changed: root `README.md`, `CHANGELOG.md`, `LICENSE`; `omega_prime/grokbot/SETUP.md`; `mcp.json`; `omega_prime/grokbot/plugin/commands/`; `.planning/STATE.md`; `.planning/ROADMAP.md`; `omega_prime/sessions.db`; `uv.lock`. No `.planning/` file was committed. The README from plan 98-01 was not edited, because its row 12 already names the four commands the override uses.

## Verify Results

Run from the repository root after all four commits.

| Check | Command | Exit |
| --- | --- | --- |
| Lint | `.venv/bin/ruff check omega_prime/tests/test_grokbot_listing.py` | 0 |
| Format | `.venv/bin/ruff format --check omega_prime/tests/test_grokbot_listing.py` | 0 |
| Types | `.venv/bin/python -m mypy omega_prime/tests/test_grokbot_listing.py` | 0 |
| Listing tests | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_listing.py -q` | 0 (13 passed) |
| Plugin generator | `.venv/bin/python -m omega_prime.tooling.plugin --check` | 0 (65 files up to date) |
| Prompt XML gate | `.venv/bin/python -m omega_prime.tooling.prompt_format --check` | 0 |
| Docs and public repo, incl. `test_setup_prompts_run_the_declared_stdio_command` | `.venv/bin/python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q` | 0 (31 passed) |
| Phase 97 listing assets | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_listing_assets.py -q` | 0 (14 passed) |
| Root LICENSE pin | `sha256sum LICENSE` equals pinned hash and `git show HEAD:LICENSE` | match |

### Drift drills

Each drill ran alone, with a `/tmp` backup, one edit, the failing listing test, a restore, and a `cmp` check.

| Drill | Edit | Test that failed | Restore `cmp` |
| --- | --- | --- | --- |
| 1 install fence | `on your own computer.` to `on your own computer!` | `test_install_prompt_is_verbatim_setup_section_one` (exit 1) | 0 |
| 2 command name | `/omega-help` to `/omega-helpx` in 02 | `test_each_follow_up_prompt_sends_one_command_and_lists_limits` (exit 1) | 0 |
| 3 pinned mapping | `/omega-doctor` to `/omega-roster` in 05 | `test_follow_up_commands_match_pinned_mapping` (exit 1) | 0 |

After the three drills, the full listing test file passed again (13 passed) and `git status` showed no change to the listing folder or the test file.

## Deviations from Plan

### Mapping override (user decision)

- The plan had 03 send `/omega-roster`. The user moved the health check to `/omega-doctor`, so 03 sends `/omega-doctor`. 05 sends `/omega-doctor` with triage, as the user asked. Verify-install (01) stays `/omega-roster`.
- Consequence: the follow-up prompts name four distinct commands, not five. The plan's test assertion "at least five distinct names" was replaced with an exact-set check against the pinned mapping's values.
- The plan's open item (01 and 03 both sending `/omega-roster`) no longer applies and is not in any prompt.

### File name kept

- `03-roster-health-check.md` keeps the planned file name although it now sends `/omega-doctor`. The name says "roster" and is misleading. I kept it because the plan, CONTEXT and must-have artifacts name it. Renaming to `03-health-check.md` is a small follow-up if you want it; the pinned mapping test and INDEX would change with it.

### `/omega-doctor` is not strictly read-only

- The brief describes the health check as read-only. In `omega_prime/tools/lead.py`, the `check` action records its outcome through `roster.doctor_result` when a roster store exists. It does not install, register or repair anything, but it writes state. Prompts 03 and 05 say so, rather than claiming it is read-only.

### Test-name commit

- The test file was committed in `b89fd37` with the longer function name, because a rename issued in the same batch as the commit had not yet applied. Commit `3ce0fa6` renames it. The test body is unchanged, and the drill selectors still match.

### Forbidden-claim list

- The plan refers to the forbidden-claim list in plan 98-01 Task 3, which I did not have. I used the list described in the 98-02 plan: connected bot, connected to a bot, published listing, marketplace listing live, live on Grok, successfully imported/attached/connected, number followed by a time unit, number followed by a percent sign, and screenshot images. A reviewer with the 98-01 list should diff the two.

### Process note on the RED state

- I ran `--collect-only` on the new test file before writing the listing files, not a full RED run. The full run was done after the prompts were written.

### Branch note from plan 98-01

- The 98-01 summary records its commits as being on `main`. At the start of this run, `main` and `v20-grok-bot-launch-kit` both pointed at `bdbaef1`. I committed only to `v20-grok-bot-launch-kit` and did not touch `main`. Whether `main` should keep the 98-01 commits is for you to decide.

## Commit Hashes

- `b89fd37` test(98-02): add listing drift, command, index, licence and claim tests
- `a2b446d` feat(98-02): add install prompt copied from SETUP.md section 1
- `bde18fd` feat(98-02): add five follow-up prompts and their index
- `3ce0fa6` test(98-02): shorten follow-up prompt test name

## Self-Check

- Every listing file named in the file table exists and is tracked in one of the four commits above.
- Live Grok Bot import is labelled UNPROVEN in every listing markdown file, and the test checks that.
- No emoji, forbidden claim or secret-shaped assignment appears in the listing folder.

## Self-Check: PASSED

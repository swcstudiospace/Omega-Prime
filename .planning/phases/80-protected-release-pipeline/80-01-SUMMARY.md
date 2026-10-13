---
plan: 80-01
status: complete
phase: 80
---

# 80-01 Summary: Protected environment + publish binding

## Tasks completed

- Task 1: `.github/grokbot-image-publish-environment.json` desired-configuration record (owner derived from `git remote get-url origin` = swcstudiospace/omes-bot; jq projection in `docs/deploy.md` builds the exact API body so record-keeping keys never reach the API) + `docs/deploy.md` "Protected publish environment" subsection with operator fallback.
- Task 2: publish-job additions in `.github/workflows/grokbot-image.yml` — outputs (archive_sha256, image_id), `id: transfer` wiring, "Record the publication binding" step, sha256sums.txt in the publication-record artifact; all five gate refusals untouched.
- Task 3: 5 new `test_binding_*` cases in `omega_prime/tests/test_grokbot_image_gates.py` asserting full (allowed, reason-code, named-detail) triples incl. digest-mismatch refusal and allow path.

## Parent action (applied 2026-10-13)

The `grokbot-image-publish` GitHub environment was created live via `gh api` (environment id 24134127283, created 2026-10-13T02:17:32Z): required reviewer `SomeRandmGuyy` (User, id 127457986 = repo owner), protected-branch deployment policy, prevent_self_review=false, wait_timer=0. First PUT attempt failed HTTP 422 (`protected_branches` and `custom_branch_policies` cannot share a value); corrected to protected_branches:true + custom_branch_policies:false and applied. The workflow's refuse-closed gate now has its prerequisite.

## Verification

Environment JSON parses; workflow YAML parses with gate lines intact (confirm_ref, inputs.publish==true, required_reviewers, publish-refused greps); binding tests pass (in the 193-passed deploy/gates run); live environment state recorded above via `gh api` read-back.

# Phase 106 Plan 03 Summary

Implemented bounded default-branch merged-PR association and truthful Greptile review states, plus the CI/CD section-notice contract migration. This is a source-ready handoff for Plan 05; generated assets were not built or changed.

## Task commits

1. Task 1 — merged PR association, review provenance, and exact card wording: `a737509`.
2. Task 2 — CI/CD `githubMeta`/Idle wiring and section-failure freshness regression: recorded in the following local commit.

## Lookup bounds and validation

- Association uses the existing GitHub GET helper, shared 1,000-call budget, 12-second deadline, 1,000,000-byte response cap, TTL/cache, API-only host policy, no-redirect opener, and token redaction.
- Association requests enumerate at most 3 pages of 100 results (300 candidates maximum). A full third page is treated as truncated/unavailable. No retry or alternate transport was added.
- A candidate is accepted only with a positive integer PR number below `2**31` (booleans rejected), safe repository/ref values, a valid head SHA, merged evidence, `merge_commit_sha` equal to the queried default-branch SHA, matching base repository, and matching default branch. Exactly one matching merged PR is required.
- Successful empty association publishes `source: not_applicable`, `evidence_source: not_applicable`, and null `pr_number`; nonempty, malformed, ambiguous, truncated, forbidden, offline, or review-evidence failures publish `source: unavailable`/`evidence_source: unavailable` on that row only. These failures do not call CI section failure/truncation callbacks.
- Associated rows publish `source: merged_pr`, `evidence_source` (`body`, `review`, or `comment`), validated `pr_number`, score, and only the SHA explicitly reported by Greptile review evidence. A body marker alone leaves `reviewed_sha` null. The merge SHA is never presented as the reviewed SHA; PR/review URLs pass `_safe_url`.
- Existing open-PR Greptile failure behavior, 01 merge bounds/cache semantics, and 02 repository+branch deduplication are preserved. CI/CD uses `githubMeta={cc?.meta}` unchanged, retains aggregate freshness separately, and includes Idle sessions for wiring correlation.

## Regression coverage and verification status

Added the named Python regressions:

- `test_branch_uses_merged_pr_score_and_actual_reviewed_sha`
- `test_direct_commit_is_not_applicable_and_lookup_failure_is_row_local`

Added the named frontend regressions:

- `renders merged PR score, no PR, unavailable and unreported reviewed SHA`
- `shows recent_merges failure with original successful section freshness`

`python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_github_greptile_polish.py omega_prime/tests/test_dashboard_github_command_center.py omega_prime/tests/test_dashboard_github_cache.py omega_prime/tests/test_dashboard_github_merges_polish.py` — PASS, 15 tests.

`npm test -- --run src/views/CiCd.test.tsx src/views/CiCd.dedup.test.tsx src/views/CiCd.greptile.test.tsx` — PASS, 3 files / 5 tests. These include the named tests above.

No baseline transplant was run, so no FAIL-on-base result is claimed. The broad required `-k dashboard` suite was attempted by the integration owner and failed during collection (exit 2) because `mcp` and `apscheduler` are unavailable in the environment. Live GitHub and host screenshots were not exercised.

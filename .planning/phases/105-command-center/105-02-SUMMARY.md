# Phase 105-02 Summary

## Implemented

The default GitHub collector now reads bounded `swcstudiospace` repository inventory plus open-PR/check evidence for `omes-bot` and `coding-fleet`; explicit `repo=` remains available for focused consumers. Inventory rows include repository identity, URL/visibility, default branch, push/language metadata, exact enumerated open-PR count, and default-branch CI evidence where available. Counts use open-pull enumeration, never `open_issues_count`; capped or failed enumerations report null rather than a false exact total.

`SourceResult.extra.command_center` contains only the agreed fields:

- `repositories`: `{id, full_name, name, url, visibility, default_branch, pushed_at, language, open_pr_count, latest_main_ci}`. `latest_main_ci` is null or `{branch, head_sha, ci, checks}`.
- `ci_cd`: bounded branch and pull-request records. PR records contain `{id, kind, repo, number, title, url, branch, base_branch, head_sha, draft, mergeable, ci, checks, greptile_review}`. Checks contain `{id, name, status, conclusion, started_at, completed_at, duration_s, details_url, head_sha}`. Score evidence is `{score, reviewed_sha, source, submitted_at, url, matches_head}`; body/comment scores without commit evidence retain a null reviewed SHA. Legacy `github_pr` items, `ci`, and string-valued `greptile` remain compatible.
- `recent_merges`: only closed-PR records with actual nonempty `merged_at`, with `{id, repo, number, title, url, merged_at, merge_commit_sha}`.
- `meta`: `{fetched_at, last_attempt_at, cache_ttl_s, stale, partial, reason, errors, truncated, sections}`; each section has `{fetched_at, stale, partial, reason}`. Reasons use safe categories and no response body/credentials.

## Bounds and degradation

- Default cache TTL: 90 seconds. A valid cache hit performs no transport requests.
- Inventory, open/closed PRs, checks, reviews, and comments are limited to 3 pages of 100 rows; inventory is capped at 300 repositories, target CI/CD at 300 rows per target repository, checks at 300 per ref, recent merges at 100, and published errors at 20.
- The refresh uses a 1,000-request ceiling, a 12-second internal deadline, up to 8 concurrent target-PR projections / 16 org-count projections, and 2.5-second network request timeout. Redirect following is disabled; requests are constructed only for validated `api.github.com` paths. External `Link` URLs are not followed.
- Open counts for the organization are enumerated independently of the two detailed target repositories. Incomplete counts are null and truncation is surfaced.
- Cached sections retain their original timestamps on failed refresh; a failed target repo retains its own prior CI/CD records while another target can publish fresh rows. Failed comment/review fetches also mark the target CI/CD section partial with a safe category rather than presenting absence as a successful no-review result. Failed inventory/counts and merge sections retain prior usable data. Cold no-token returns empty/unavailable metadata without transport; no-token after success retains cached data and labels it unauthenticated/stale. HTTP 403 and 429 are distinguished; malformed/offline failures do not get mislabeled as rate limits. Cached content does not contain a ticking age field.
- Collector output and the W1 poller-published snapshot are covered for credential redaction. No W1 publication files were changed.

## Verification

| Command | Exit | Result |
|---|---:|---|
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_remote_collectors.py omega_prime/tests/test_dashboard_github_command_center.py` | 0 | 13 passed (Task 1 run). |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_github_cache.py omega_prime/tests/test_dashboard_github_command_center.py omega_prime/tests/test_dashboard_remote_collectors.py omega_prime/tests/test_dashboard_snapshot_contract.py` | 0 | 21 passed (final Task 2 run, including Greptile failure classification and published-snapshot redaction coverage). |

These tests use fake transports only; no live GitHub requests were made.

## Explicitly unverified

Live a2 parity for current authenticated PR checks, Greptile score/reviewed-SHA evidence and live a3 parity for inventory, open-PR counts, default-branch CI, and recent merges remain unverified. Fixtures demonstrate projection, bounds, cache/error semantics, and publication redaction only; the outer driver must compare authenticated GitHub data at capture time and must not treat unknown review-commit evidence as a match.

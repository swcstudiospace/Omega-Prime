# Phase 105: Omega Prime Command Center — Pattern Map

**Mapped:** 2026-10-20  
**Scope:** Planning only; D-01 through D-11 are locked. No product implementation or verification was performed by this mapper.  
**Files classified:** 64 concrete proposed paths plus 2 generated-output families (66 entries). Optional helper and suggested frontend filenames below are **proposed**, not existing repository facts.  
**Analog coverage:** 19 exact/self matches, 7 role matches, 11 partial data-flow matches, 29 without a tracked-source analog. Five primary Python analogs provide the strongest patterns; do not manufacture a React precedent.  
**Ownership:** The sole output `.planning/phases/105-command-center/105-PATTERNS.md` matches `omega_prime/ownership.yaml:16-17`, owner `bot-00-omega-prime`; no later rule overrides it. Parent owns the receipt, context, state, plans and commit.

## Authority and evidence boundaries

Inputs: `105-CONTEXT.md`, `105-UPLIFT.xml`, `/sandbox/home/refs/README-REFS.md`, and both source-evidence reports. Research is disabled, so no RESEARCH.md is required. Repository guidance and ownership apply to all planning artifacts.

Current code is **Starlette**, not the older stdlib description in the uplift. The parent integration contract supersedes the scouts' earlier interface proposals:

- **W1 owns all model/poller/CLI numbering integration**, including an optional `task_ids_path` constructor argument implemented before CLI use, plus the GitHub metadata seam and poller-level timeout preservation.
- **W2 owns only `collectors/github.py` and an optional pure helper**, plus its tests. It does not edit model.py, poller.py, __main__.py or app.py. Its rich data travels through `SourceResult.extra['command_center']` to `sources.github.command_center`, not new top-level keys or API routes.
- W3 frontend/build → W4 server/package/security → W5 docs/final handoff follow W1 → W2 horizontally (D-09). No tracer-first reordering or auto-advance.
- Every quoted code block below is existing source, with exact path/range. Proposed fields/interfaces are described as additions, not quoted as implemented symbols.
- All repository analog paths named below were confirmed by nonempty `git ls-files -- <path>` output. No install/runtime mirrors are analogs. External supplied components are reference evidence only, not tracked repository analogs or commit-ready redistributable files.

## File Classification

Paths abbreviated only in this table: **D/** = `omega_prime/dashboard/`; **F/** = `omega_prime/dashboard/frontend/`; **T/** = `omega_prime/tests/`. Expand these prefixes in executable plans. “Exact” includes the existing file being modified; “flow” means partial, not a shared framework convention. References P1–P5 and S1–S5 identify excerpts below.

| New/Modified File | Owner wave | Role | Data Flow | Closest tracked analog | Match |
|---|---|---|---|---|---|
| `D/status_file.py` | W1 | model/utility | file-I/O | same file, P1 | exact |
| `D/task_ids.py` (new) | W1 | service/utility | file-I/O | `D/status_file.py`, P1 | role |
| `D/model.py` | W1 | model | transform | same file, P2 | exact |
| `D/poller.py` | W1 | service | event-driven/batch | same file, P3 | exact |
| `D/__main__.py` | W1 | controller/config | request-response/file-I/O | same file, S1 | exact |
| `D/collectors/tasks_json.py` | W1 | service | file-I/O | same file, S1 | exact |
| `T/test_dashboard_model.py` | W1 | test | transform | same file, S4 | exact |
| `T/test_dashboard_redaction.py` | W1 | test | file-I/O/transform | same file, S4 | exact |
| `T/test_dashboard_file_collectors.py` | W1 | test | file-I/O | same file; scout evidence | exact |
| `T/test_dashboard_poller_fixes.py` | W1 | test | event-driven | same file, S4 | exact |
| `T/test_dashboard_task_ids.py` (new) | W1 | test | file-I/O | `T/test_dashboard_redaction.py`, S4 | role |
| `T/test_dashboard_task_ids_cli.py` (new) | W1 | test | request-response | `T/test_dashboard_app.py:221-317`, S4 | role |
| `T/test_dashboard_snapshot_contract.py` (new) | W1 | test | event-driven/transform | `T/test_dashboard_poller_fixes.py`, S4 | role |
| `D/collectors/github.py` | W2 | service | request-response/batch | same file, P4 | exact |
| `D/github_command_center.py` (optional new pure helper) | W2 | utility | transform | `D/collectors/github.py`, P4 | role |
| `T/test_dashboard_remote_collectors.py` | W2 | test | request-response | same file, S4 | exact |
| `T/test_dashboard_github_command_center.py` (new) | W2 | test | request-response/batch | `T/test_dashboard_remote_collectors.py`, S4 | role |
| `F/package.json` (new) | W3 | config | batch | none for production React/Vite | none |
| `F/package-lock.json` (new generated) | W3 | config | batch | none; generate with actual npm resolution | none |
| `F/tsconfig.json` (new) | W3 | config | batch | none | none |
| `F/tsconfig.node.json` (new if toolchain needs it) | W3 | config | batch | none | none |
| `F/vite.config.ts` (new) | W3 | config | batch/file-I/O | none | none |
| `F/index.html` (new) | W3 | component | request-response | none for Vite entry | none |
| `F/src/main.tsx` (new) | W3 | provider | event-driven | none | none |
| `F/src/App.tsx` (new) | W3 | component | event-driven | `D/static/app.js:204-270`, S3 | flow |
| `F/src/types.ts` (new) | W3 | model | transform | `D/model.py:67-168`, P2 | flow |
| `F/src/styles.css` (new) | W3 | config/component | transform | none for locked new tokens/Tailwind | none |
| `F/src/lib/utils.ts` (new) | W3 | utility | transform | none for React class merging | none |
| `F/src/lib/links.ts` (new) | W3 | utility | transform | `D/static/app.js:124-133` (text-only safety baseline) | flow |
| `F/src/lib/time.ts` (new) | W3 | utility | transform | `D/static/app.js:22-46`, S3 | flow |
| `F/src/hooks/useDashboard.ts` (new) | W3 | hook | streaming/request-response | `D/static/app.js:217-270`, S3 | flow |
| `F/src/views/Overview.tsx` (new) | W3 | component | transform | `D/static/app.js:54-78,204-211`, S3 | flow |
| `F/src/views/Tasks.tsx` (new) | W3 | component | event-driven/transform | `D/static/app.js:80-139,168-202`, S3 | flow |
| `F/src/views/CiCd.tsx` (new) | W3 | component | transform | `D/static/app.js:141-165` aggregate PR display | flow |
| `F/src/views/Repositories.tsx` (new) | W3 | component | transform | none for repository inventory | none |
| `F/src/components/CommandSidebar.tsx` (new) | W3 | component | event-driven | none tracked; adapt supplied sidebar | none |
| `F/src/components/TaskDetail.tsx` (new) | W3 | component | transform | `D/static/app.js:98-135`, S3 | flow |
| `F/src/components/SourceHealth.tsx` (new) | W3 | component | transform | `D/static/app.js:64-77`, S3 | flow |
| `F/src/components/ViewState.tsx` (new) | W3 | component | event-driven | `D/static/app.js:52,217-227`, S3 | flow |
| `F/src/components/NumberTicker.tsx` (new) | W3 | component | event-driven | none tracked; supplied Magic UI reference | none |
| `F/src/components/BlurFade.tsx` (new) | W3 | component | event-driven | none tracked; supplied Magic UI reference | none |
| `F/src/components/BorderBeam.tsx` (new) | W3 | component | event-driven | none tracked; supplied Magic UI reference | none |
| `F/src/components/AnimatedShinyText.tsx` (new) | W3 | component | event-driven | none tracked; supplied Magic UI reference | none |
| `F/src/components/SummaryCards.tsx` (new) | W3 | component | event-driven | none tracked; adapt supplied card hover | none |
| `F/src/test/setup.ts` (new) | W3 | test/config | batch | none for frontend harness | none |
| `F/src/App.test.tsx` (new) | W3 | test | event-driven | none for React DOM tests | none |
| `F/src/hooks/useDashboard.test.tsx` (new) | W3 | test | streaming/request-response | none for frontend transport mocks | none |
| `F/src/components/motion.test.tsx` (new) | W3 | test | event-driven | none | none |
| `F/src/views/Repositories.test.tsx` (new) | W3 | test | transform | none | none |
| `F/public/licenses/Anybody-OFL.txt` (new) | W3 | config/notice | file-I/O | none; actual package licence | none |
| `F/public/licenses/DM-Sans-OFL.txt` (new) | W3 | config/notice | file-I/O | none; actual package licence | none |
| `F/public/licenses/Space-Mono-OFL.txt` (new) | W3 | config/notice | file-I/O | none; actual package licence | none |
| `F/public/licenses/Magic-UI-MIT.txt` (new) | W3 | config/notice | file-I/O | none; actual upstream notice | none |
| `D/static/cc/index.html` (new generated) | W3 | component | request-response | none; actual Vite output | none |
| `D/static/cc/assets/*` (new generated family) | W3 | config/component | file-I/O | none; actual hashed build output | none |
| `D/static/cc/licenses/*` (new generated family) | W3 | config/notice | file-I/O | none; copied distributed notices | none |
| `D/app.py` | W4 | controller/middleware | streaming/request-response | same file, P5 | exact |
| `pyproject.toml` | W4 | config | batch/file-I/O | same file, S2 | exact |
| `T/test_dashboard_app.py` | W4 | test | streaming/request-response | same file, S4 | exact |
| `T/test_dashboard_app_fixes.py` | W4 | test | request-response | same file; scout evidence | exact |
| `T/test_dashboard_live_fixes.py` | W4 | test | request-response/transform | same file; scout evidence | exact |
| `T/test_dashboard_integration.py` | W4 | test | file-I/O/request-response | same file; scout evidence | exact |
| `T/test_dashboard_command_center_static.py` (new) | W4 | test | request-response/file-I/O | `T/test_dashboard_app.py:75-127`, S4 | role |
| `docs/dashboard.md` | W5 | config/documentation | transform | same file, S5 | exact |
| `CHANGELOG.md` | W5 | config/documentation | transform | same file, S5 | exact |

The file count includes the optional helper and optional split TS config so neither can appear as an unassigned surprise. Planner may omit an unnecessary optional file explicitly; it must not silently omit required behavior. Runtime `~/.omega-prime/dashboard/tasks.json` and its restrictive sidecar lock are **user data**, never repository fixtures or files_modified entries.

### Existing test custody, once per file

W1 owns the four existing W1 test paths above and is the sole regression custodian for intentionally unchanged `omega_prime/tests/test_dashboard_sessions_fixes.py`, `test_dashboard_wiring_fixes.py`, `test_dashboard_jobs_model_fixes.py`, and `test_dashboard_process_collectors.py`. These four need no planned edits unless the new model genuinely changes a behavior they exercise. W2 owns the existing remote-collectors file; W4 owns the four server-facing files. W1 must pass existing tests before W2, including unchanged W4 consumers; therefore no constructor argument may be merely promised for a later wave. No test file is co-owned.

## Pattern Assignments

### P1 — `omega_prime/dashboard/status_file.py` and new `task_ids.py` (file-I/O)

**Analog:** `omega_prime/dashboard/status_file.py`. Copy validation and durable-file mechanics, not its absence of locking.

**Imports** (`status_file.py:7-16`):
```python
import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.redact import redact_value
```

**Explicit schema projection** (`status_file.py:76-87`):
```python
def _validated(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("version") != 1:
        raise ValueError("version must equal 1")
    out: dict[str, Any] = {
        "version": 1,
        "updated_at": _string(data, "updated_at", "root"),
    }
    for category, required in (
        ("tasks", ("id", "title")),
        ("waiting_on_ove", ("id", "ask")),
        ("finished", ("id", "title")),
    ):
```

Unknown fields currently disappear. Adding seq only in a caller cannot survive this projector. Extend the allowlist for numbering, counter, durable identity registry/tombstones and observed transition history. Existing strict integer handling for progress (`44-57`) rejects bool; use the same rule for seq/next_seq. Validate duplicate/conflicting allocations across the whole document; same logical identity repeated across arrays is one allocation, not a duplicate task incarnation.

**Atomic write core** (`status_file.py:131-149`):
```python
def write_tasks(data: dict[str, Any], *, path: Path) -> Path:
    """Validate and atomically persist a redacted tasks.json document."""
    safe = redact_value(_validated(data))
    payload = (json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
```

**Error pattern:** `150-153` removes temporary files and re-raises; `157-176` returns missing/unreadable structured reads with redacted errors. A directory-fsync exception can occur after replacement: recovery must inspect persistent state, never roll back by reusing a number.

**Required additions (D-01, a1):**

1. One sidecar `flock` transaction guards read/validate/merge/allocate/write across the helper, legacy writer and CLI. Lock the stable sidecar, not the replaceable destination inode. Keep parent/file/lock restrictive. Split internal unlocked serialization from public transaction orchestration if necessary; avoid recursive lock acquisition.
2. Preserve version 1 and existing writer callers; carry durable counter/registry allocations forward when an old caller supplies only legacy fields. No unrelated unknown-key passthrough.
3. Backfill original bridge order **tasks → waiting_on_ove → finished**, not the sorted snapshot. Honor existing finished numbers. Then allocate previously unseen collector-derived technical IDs deterministically. Counter never shrinks or reuses after deletion/re-add/title change; retain tombstones.
4. Persist a positive integer seq, reject bool, and display `f'{seq:05d} - {title}'` (waiting label uses ask). Five digits is a **minimum width**, not a 99999 exhaustion cap.
5. Preserve current namespaced technical IDs as identity/React selection keys; same ID in active and finished shares seq. Do not redesign source incarnations or synthesize random IDs.
6. Timeline records only actual observations from allocation/observed transitions onward, with explicit observation scope; started_at/last_activity_at are not proof of intervening state changes.
7. No persistence on HTTP GET handlers and no unstable in-memory numbering on invalid data. Poller builds use the shared transaction before publication; unchanged allocations/history should not rewrite the file on each local tick. Expose failure honestly and retain a last safe snapshot where available.

### P2 — `omega_prime/dashboard/model.py` and frontend `types.ts` (transform)

**Analog:** `omega_prime/dashboard/model.py`.

**Existing task serializer** (`model.py:81-98`):
```python
    def to_dict(self) -> dict[str, Any]:
        return _safe(
            {
                "id": self.id,
                "title": self.title,
                "source": self.source,
                "owner": self.owner.to_dict(),
                "state": self.state,
                "started_at": self.started_at,
                "last_activity_at": self.last_activity_at,
                "progress": None
                if self.progress is None
                else {"done": self.progress[0], "total": self.progress[1]},
                "model": self.model,
                "detail": self.detail,
                "links": list(self.links),
            }
        )
```

**Collector envelope** (`model.py:101-108`):
```python
@dataclass
class SourceResult:
    source: str
    status: str
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None
```

**Proposed W1 extensions:** serializable seq/display_id and observed timeline for every task-like sidebar row, preserving existing fields/callers. Waiting rows are `ask/since/blocks`, not `title`; finished rows are `title/finished_at/result/receipt/summary`. Do not type all four arrays as Task. Task.progress serializes as an object, not tuple. PR rows remain a legacy-compatible aggregate (`ci` and `greptile` strings).

**Identity traps:** `model.py:268,294,334,352,378,399,413,428,444,465,491,529,599` constructs current Herdr/session/wiring/sandbox/job/fleet/process/manual IDs. `612-620` duplicates a manual done task into finished; `621-640` preserves already-namespaced waiting/finished IDs. Numbering must cover derived tasks and avoid double allocation. `667-677` sorts and caps the display; it cannot be the migration ordering source.

**Wiring evidence addition to freeze in W1:** current `345-389` keeps branch/SHA only in detail prose and drops all wiring metadata on done rows. Recommend a proposed nullable structured `wiring` field on Task and FinishedItem containing existing `phase`, `branch`, `head_sha`, `stage`, rather than a new collector or parsed prose. Source evidence is `collectors/wiring.py:159-172`:
```python
                items.append(
                    redact_value(
                        {
                            "phase": phase,
                            "branch": branch,
                            "head_sha": head,
                            "stage": stage,
                            "last_log_ts": stamp,
                            "progress": progress,
                            "worktree": worktree,
                        }
                    )
                )
            return SourceResult("wiring", "ok" if items else "empty", items=items)
```
W3 can associate this evidence with W2 PR/merge rows by repository/branch and corroborated SHA prefix. The current head is short (`wiring.py:148-154`). Repository provenance must be established or remain unknown; branch coincidence alone is not a cross-repository match. `stage='done'` is a runner marker, **never evidence merged**. No prerequisite graph currently exists: show unknown prerequisites instead of guessing numeric phase order. The current phase.log link actually points to worktree (`model.py:371-375`); do not relabel it as a collected log URL.

### P3 — `omega_prime/dashboard/poller.py` (publication boundary; W1 only)

**Analog:** same file. Preserve independent scheduling, last-safe normalization behavior, shared redacted JSON/SSE cache and hash stability.

**Existing allowlist seam** (`poller.py:24-27,144-147`):
```python
_STATUS_EXTRA = {
    "agentos": ("routing_summary", "state_entries"),
    "command_log": ("count", "last_exit_codes"),
}
```
```python
        for key in _STATUS_EXTRA.get(spec.source, ()):
            if key in result.extra:
                status[key] = redact_value(result.extra[key])
        self._status[spec.source] = status
```

**W1 addition:** allow **only** the GitHub extra key `command_center` at this boundary, when present. Keep arbitrary extras private. The section itself is bounded by W2's schema projection/page caps; W1 applies recursive redaction. No W2 helper call belongs in poller.py.

**Publication core** (`poller.py:167-185`):
```python
        value = {
            "generated_at": now.isoformat(),
            "sources": self.sources_status(),
            **normalized,
        }
        safe = redact_value(value)
        canonical = dict(safe)
        canonical.pop("generated_at", None)
        canonical_sources = canonical.get("sources", {})
        if isinstance(canonical_sources, dict):
            canonical["sources"] = {
                name: {key: item for key, item in state.items() if key != "took_ms"}
                for name, state in canonical_sources.items()
            }
        digest = hashlib.sha256(
            json.dumps(
                canonical, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()
```

**Numbering seam:** W1 implements optional `task_ids_path: Path | None = None` on DashboardPoller, with CLI supplying the actual bridge path. None preserves existing manually constructed test pollers with no home-directory writes. W1 invokes its implemented numbering helper on the normalized Snapshot **before** `.to_dict()`/full redaction/publication. The helper's exact function name is a new plan API to declare, not an existing symbol.

**Timeout trap:** `poller.py:123-138` currently replaces a timed-out collector with an empty error result, destroying cached GitHub items/extras even if the collector has its own stale cache. W1 must preserve the previous GitHub result's data and fetched timestamp on poller-level failure, publish error/stale health, and test both legacy rows and nested rich data survive. No mutation of a collector cache behind the timeout; no token/error text leakage. A cold timeout remains honestly unavailable/error with no fabricated data.

**Grouping trap:** `151-160` shallow-copies results, then appends grouped items into reused SourceResult buckets. Avoid mutating cached inputs while adding numbering/publication logic; copy grouping buckets as necessary within W1's boundary. W2 must keep its new detail in extra, not invent a source==kind item that appends into its own list. Test repeated refreshes for stable cardinality/hash rather than source-text assertions.

**Age/hash:** use fetched_at plus browser age calculation. Do not inject continuously increasing age_s or running duration counters into canonical snapshot content on every 3-second local rebuild. Completed check duration can be a fixed timestamp difference. Ordinary observation timestamps should change only for actual observations/transitions.

### P4 — `omega_prime/dashboard/collectors/github.py` (W2)

**Analog:** same file; optional pure projection helper copies its import/project style, not its single-repo limitations.

**Imports** (`github.py:12-19`):
```python
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from omega_prime.credentials.broker import CredentialBroker
from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value
```

**Read-only fakeable transport** (`github.py:34-47`):
```python
def _request(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=10.0) as response:
        return response.status, response.read(1_000_000)


def make_github_collector(
    *,
    env: Mapping[str, str] | None = None,
    transport: Callable[[str, Mapping[str, str]], tuple[int, bytes]] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    cache_ttl: float = 60.0,
    repo: str = "swcstudiospace/omes-bot",
) -> Callable[[], SourceResult]:
```

**Credential boundary:** `27-31` permits broker resolution only for api.github.com; `64-89` resolves the env-backed broker key and puts it in request headers. Do not put headers/token/raw responses in any extra. Broker resolution is not redirect enforcement: pagination/redirects must not send Authorization to attacker-selected hosts. Construct/validate read URLs against the fixed API origin. Catch real urllib HTTPError status explicitly so 403/429 are distinguishable from offline; current `get()` collapses non-200 to RuntimeError('http'). Response headers may be added only if needed; preserve existing `(status, body)` fakes or migrate them entirely within W2's owned tests.

**Legacy projection to preserve** (`github.py:173-186`):
```python
                item = {
                    "kind": "github_pr",
                    "repo": repo,
                    "number": number,
                    "title": pr.get("title"),
                    "branch": head.get("ref"),
                    "draft": bool(pr.get("draft")),
                    "mergeable": pr.get("mergeable"),
                    "ci": ci,
                    "greptile": f"{score}/5" if score else "not reviewed",
                    "updated_at": pr.get("updated_at"),
                    "url": pr.get("html_url"),
                }
                return dict(redact_value(item, extra=(token,), env={}))
```

Retain `github_pr` aggregate items and semantics for `/legacy`; rich data is a separately projected/redacted bounded object. Preserve 0/5 correctly. Current cache `80-84,188-206` is an analog for closure state, **not** adequate degradation: no-token currently discards cache, any exception labels cached data rate-limited, refresh is all-or-nothing. Per-section caches should retain usable prior data when one repo/check/review request fails.

#### Proposed additive GitHub contract (D-02; a2/a3/a5)

The **agreed envelope** is `sources.github.command_center = {repositories, ci_cd, recent_merges, meta}`. These names are proposed additions. The planner must freeze detailed nullable row types before W3; suggested fields below are a concrete minimum, not observed current fields.

| Section | Required evidence and suggested fields | Must not infer |
|---|---|---|
| repositories | `id`, `full_name`, `name`, `url`, `visibility`, `default_branch`, `pushed_at`, `language`, `open_pr_count`, `latest_main_ci` with actual branch/head SHA/check summary | `open_issues_count` is not open PR count; unavailable count/status is null, not zero/pass |
| ci_cd | Distinguish PR and branch rows; repo/number/title/url/branch/base_branch/head_sha/draft/mergeable/aggregate ci; checks with id/name/status/conclusion/started_at/completed_at/duration_s/details_url/head_sha | No guessed job/log URL; pending/null conclusions are not passing; default branch is not always main |
| Greptile detail within ci_cd | score, reviewed_sha, source(body/comment/review), submitted_at, url, matches_head with nullable unknown provenance | Body marker or issue comment is not commit-pinned merely because it was fetched for this head |
| recent_merges | Bounded **actual merged-PR collection**; repo/number/title/url/merged_at/merge_commit_sha | closed-unmerged, finished task or wiring done is not a merge |
| meta | fetched_at, last_attempt_at, cache_ttl_s, stale, partial, reason, bounded errors, truncation/page-limit information; preserve section freshness if mixed | No falsely fresh timestamp on retained cache; no continuously increasing hash age; offline != 403/429 |

Scope is org inventory for `swcstudiospace`, open PR details for **both** `omes-bot` and `coding-fleet`, plus actual default-branch CI and actual recent merges. Do not reduce org inventory to just these two repos. Pagination must cap inventories, open/merged PRs, checks and comments/reviews, and mark partial/truncated results. Cache TTL is 60–120 seconds; missing credentials with prior cache still degrade to cache without issuing requests. No token and no cache makes no network request. Bound concurrent work, total request/page count and response bytes to respect the existing 15-second poller timeout (`__main__.py:116`); 10 seconds per HTTP request is not a whole-org budget.

**Greptile provenance:** `github.py:121-172` currently body marker wins; otherwise newest bot comment can beat a head-matching bot review. Preserve aggregate compatibility, but attach the provenance of the **selected** score. Only matching `review.commit_id` (or additional explicit commit evidence) establishes reviewed SHA. A current score with unknown provenance must display “reviewed commit unavailable,” not the head. Human comments remain ignored. Tests should distinguish selected score from any unrelated commit-pinned older review.

**Wiring integration:** W2 collects PR/merged facts; W1 exposes existing local wiring facts; W3 associates them. Do not add a wiring SourceResult argument to the GitHub collector or a late poller/model helper call. Unknown prerequisites remain explicit.

### P5 — `omega_prime/dashboard/app.py` (W4 only)

**Analog:** same Starlette file, retaining outer guards around all routes/assets/errors.

**Security headers** (`app.py:23-27`):
```python
_SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
```

D-08 adds explicit `font-src 'self'` and permits `style-src 'self' 'unsafe-inline'` **only** for motion style attributes. Preserve every existing directive/header; script-src/default-src/connect-src stay self-only. No CDN, data: font exemption, remote scripts or CORS opening. Document why style is the sole exception.

**Host/method guard** (`app.py:148-162`):
```python
        method = scope.get("method", "")
        host_values = [
            value for key, value in scope.get("headers", []) if key.lower() == b"host"
        ]
        host = host_values[0].decode("latin-1").lower() if len(host_values) == 1 else ""
        if method != "GET":
            response = Response(
                "method not allowed: dashboard is read-only",
                status_code=405,
                media_type="text/plain",
            )
        elif host not in self.allowed_hosts:
            response = Response(
                "host not allowed", status_code=403, media_type="text/plain"
            )
```

No HEAD/OPTIONS exemption. Missing/duplicate/foreign/wrong-port Host remains rejected. IPv6 Host bracket handling is at `130-142`. Binding is separately constrained by CLI `_loopback`, not create_app. No remote-user auth/public hosting is in scope.

**Same snapshot for JSON and SSE** (`app.py:228-231,237-254`):
```python
    async def snapshot(_: Any) -> Response:
        value = poller.snapshot() or _empty_snapshot()
        content = json.dumps(value, separators=(",", ":"), default=str)
        return Response(content, media_type="application/json")
```
```python
        async def sse_generator() -> AsyncIterator[str]:
            previous_hash: str | None = None
            yield "retry: 3000\n\n"
            current_hash = poller.snapshot_hash()
            current = poller.snapshot()
            if current is not None and current_hash is not None:
                yield f"event: snapshot\ndata: {json.dumps(current, separators=(',', ':'), default=str)}\n\n"
                previous_hash = current_hash
            while True:
                changed = await poller.wait_for_update(heartbeat_s)
                current_hash = poller.snapshot_hash()
                if current_hash is not None and current_hash != previous_hash:
                    current = poller.snapshot()
                    if current is not None:
                        yield f"event: snapshot\ndata: {json.dumps(current, separators=(',', ':'), default=str)}\n\n"
                        previous_hash = current_hash
                elif not changed:
                    yield ": heartbeat\n\n"
```

Keep `_SSEResponse` disconnect cancellation and removal of Content-Length (`47-124`), SSE no-cache/X-Accel-Buffering headers (`256-259`). No event IDs, replay protocol, deltas or heartbeat JSON exists; do not invent them.

**Current routes:** `app.py:261-275` has `/`, `/static`, `/api/snapshot`, `/api/stream`, `/healthz`. W4 adds only page/asset routes: built default page, `/cc` asset namespace and `/legacy`, while retaining old static assets. No new API routes, action endpoints or blanket SPA catchall. Current `_empty_snapshot` (`193-201`) has exactly six keys: generated_at/sources/active/pr_gate/waiting_on_ove/finished. With the **nested** GitHub seam, this six-key contract need not expand; cold sources remain absent/empty until collected, which W3 must recognize honestly. Mirror agreed row/empty semantics only as needed.

## Shared Patterns

### S1 — CLI dispatch and bridge collection

Existing CLI `omega_prime/dashboard/__main__.py:170-189` accepts injectable home, serve, signal_fn, stdout and poller_factory, with flags `--host`, `--port`, `--once`, `--stop`. Copy its dependency-injection style for offline CLI tests. Add next-id and assignment command dispatch before server/pidfile/collectors; preserve all existing commands, bind checks and PID start-time safeguards. Proposed subcommand syntax/function symbols must be declared by W1, not portrayed as current behavior.

Existing poller construction (`__main__.py:252-257`):
```python
    def build_poller() -> DashboardPoller:
        return DashboardPoller(
            build_default_collectors(home=base_home, config=config),
            quiet_minutes=config.quiet_minutes,
            window_hours=config.window_hours,
        )
```
Add actual path only after W1 implements the optional constructor argument. Runtime bridge path is already `home / '.omega-prime' / 'dashboard' / 'tasks.json'` (`96-102`). CLI allocation must not start a server, touch its pidfile or fetch GitHub.

Bridge collector (`collectors/tasks_json.py:43-51`):
```python
                output = SourceResult(
                    "tasks_json",
                    "ok",
                    extra={
                        "tasks": data["tasks"],
                        "waiting_on_ove": data["waiting_on_ove"],
                        "finished": data["finished"],
                        "age_s": result.age_s,
                    },
                )
```
Propagate explicitly validated numbering/history metadata as required, not arbitrary input. Its cache uses mtime_ns/size (`30-34`) and returns the same cached SourceResult. Avoid mutating that object during normalization. Cached age_s currently freezes; timestamp-based frontend age must not rely on that as a fresh clock.

### S2 — Recursive security and packaged assets

**Redaction source:** `omega_prime/dashboard/redact.py:68-87`; **apply to:** every new text/URL/detail field and persisted history, not only titles.
```python
    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: "[REDACTED]" if _denied_key(key) else walk(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, tuple):
            return tuple(walk(child) for child in item)
        if isinstance(item, str):
            # Exact secret values go first, before shielding can alter them.
            for secret in secrets:
                if secret:
                    item = item.replace(secret, "[REDACTED]")
            item = _ASSIGNMENT_SECRET.sub(r"\1[REDACTED]", item)
            shielded = _INNER_SK.sub(lambda m: m.group(1) + _SK_SHIELD, item)
            out = redact_text(shielded, secrets)
            return out.replace(_SK_SHIELD, "sk-") if isinstance(out, str) else out
        return redact_text(item, secrets)
```
New GitHub data must pass token-explicit collector redaction and W1 publication redaction; app handlers do not independently redact. Do not add new data after the poller boundary. Redaction is not URL-protocol validation: JSX escapes text, but href must reject unsafe protocols; local filesystem references should remain inert code/text unless a real safe link exists. No innerHTML/dangerouslySetInnerHTML.

**Packaging source:** `pyproject.toml:58-67`:
```toml
[tool.setuptools.package-data]
omega_prime = [
  "**/*.md",
  "**/*.yaml",
  "**/*.yml",
  "**/*.json",
  "**/*.xml",
  "**/*.sh",
  "dashboard/static/*",
]
```
Flat static wildcard is not a recursive cc JS/CSS/woff2/OFL solution. W4 explicitly includes nested build output and licence text, while retaining legacy assets. Build output is committed/package-shipped; Python runtime needs no Node. Test packaged manifest/path inclusion, not only source-tree existence.

W3 uses Vite base `/cc/`, emits external module/CSS and font files (disable asset inlining for self-only font-src), does not emit source maps or frontend source into static output, and commits actual package-lock and generated hashed files. Never invent versions/hash filenames. W4 serves only the cc build tree, preserves traversal/symlink containment, does not mount frontend/node_modules, and does not convert missing JS/API paths into HTML. Hashed assets get immutable caching and correct MIME; HTML revalidates; errors/HTML never inherit immutable caching. A missing build needs an explicit honest response/no-JS legacy link, not accidental behavior.

### S3 — Safe frontend consumer and transport lifecycle

**Source:** `omega_prime/dashboard/static/app.js`; it is a vanilla consumer reference, not a React architecture to copy wholesale.

**Safe text and AEDT display** (`app.js:14-18,22-27`):
```javascript
  const node = (tag, value, className) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined) el.textContent = text(value);
    return el;
```
```javascript
  const formatAedtTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return text(value);
    return new Intl.DateTimeFormat('en-AU', {timeZone: 'Australia/Sydney', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short'}).format(date);
  };
```
Use Sydney timezone rather than hand-applying UTC+11 to all dates. Keep missing metadata unavailable. W3 task selection is local component state keyed by technical identity, no reload. Display canonical server display_id rather than numbering current filtered rows.

**Named SSE event** (`app.js:248-262`):
```javascript
  function connectStream() {
    if (eventSource) eventSource.close();
    const source = new EventSource('/api/stream');
    eventSource = source;
    source.addEventListener('snapshot', (event) => {
      try { renderSnapshot(JSON.parse(event.data)); } catch (error) { console.error('Invalid stream snapshot', error); }
    });
    source.onopen = () => {
      if (eventSource !== source) return;
      stopPolling(); setConnection('live');
    };
    source.onerror = () => {
      if (eventSource !== source) return;
      source.close(); eventSource = null;
      setConnection('reconnecting'); startPolling();
```
React hook must listen to **snapshot**, not onmessage; fetch `/api/snapshot` initially and poll on stream error. Existing fallback schedules 5-second polling and 30-second reconnect (`229-267`); reuse those semantics unless consciously documented. Cleanup closes stream, aborts fetches and clears all timers/listeners, including StrictMode setup/cleanup. Retain prior data during reconnect and prevent overlapping loops.

Heartbeat comments are invisible to EventSource snapshot listeners; unchanged hashes may yield no snapshot events indefinitely. Do not label a healthy unchanged stream disconnected merely because data has not changed for 30 seconds. Separate transport state, last response and source fetched_at/stale. Every view needs loading/empty/error+retry/stale-age/partial handling (D-04/D-05), and GitHub failure cannot blank local sections. Retry is read-only refetch/reconnect, not a new write endpoint. Overview feed/timeline uses actual observed events; no fabricated prehistory. Recent merges must consume actual merged PR evidence.

### S4 — Offline test patterns and behavior boundaries

**Remote fake transport:** `omega_prime/tests/test_dashboard_remote_collectors.py:18-27`:
```python
def test_github_missing_token_makes_no_calls() -> None:
    calls: list[Any] = []

    def fake_transport(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        calls.append((url, headers))
        return (200, b"[]")

    result = make_github_collector(env={}, transport=fake_transport)()
    assert result.status == "unavailable"
    assert calls == []
```
Use injected clocks/transports for TTL/pagination/partial failures and actual urllib HTTPError classification. No network. Existing tests `30-70` protect cache/token absence; `73-176,259-266` protect score selection/0/5. Expand fakes for two-repo/org/default-branch/merged endpoints or explicitly focus old single-repo fixtures using supported collector configuration. Do not let incidental call order define correctness; preserve actual token/read-only/provenance guarantees.

**Allowlisted publication:** `omega_prime/tests/test_dashboard_poller_fixes.py:192-217` seeds extras in a SourceResult and asserts both sources_status and snapshot metadata. Copy that **boundary** pattern for command_center: unknown extras remain absent; secret-bearing nested fields redact; repeated refresh stays stable; poller-level timeout retains old detail with stale/error status while local tasks progress. Do not assert that a particular import or function call text exists.

**Persistence security:** `omega_prime/tests/test_dashboard_redaction.py:140-158` checks 0700/0600, no temporary leftovers, redaction, unknown-field stripping and readable output. Its `list(path.parent.iterdir()) == [path]` is incidental and conflicts with the required stable lock sidecar; replace with actual restrictive-lock/no-temp-leak guarantees, not a new hardcoded listing. Add real concurrent-process/restart/delete/re-add/title-change/done-duplication/original-order/derived-task/no-op-write assertions. Test invalid bool/conflicts/failed writes and safe recovery without reused numbers.

**Model:** `omega_prime/tests/test_dashboard_model.py:14-25` constructs fixed-clock SourceResults and compares output. `156-178` proves current order and active/finished same identity; extend semantics rather than renumbering by sort index. The dead conditional at `27-31` proves nothing; delete if touched rather than copying it as coverage.

**HTTP guard test** (`omega_prime/tests/test_dashboard_app.py:118-127`):
```python
        for host in ("127.0.0.1:9999", "evil.example:7421"):
            assert (
                client.get("/api/snapshot", headers={"host": host}).status_code == 403
            )
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            for path in ("/", "/api/snapshot"):
                response = client.request(
                    method, path, headers={"host": "127.0.0.1:7421"}
                )
                assert response.status_code == 405
```
W4 extends this to cc/legacy/assets and tests duplicate Host, loopback CLI, CSP all directives, MIME/caching/traversal/escaping symlink/missing asset, nested packaging and actual streamed secret absence. Raw-ASGI SSE driving at `test_dashboard_app.py:320-412` is the existing pattern because TestClient buffers infinite bodies. Keep no-Content-Length/heartbeat/disconnect tests from existing server regressions.

**Intentional assertion migration:** retain the six top-level API keys because rich GitHub data is nested. Update root title/asset expectations to cc behavior and separately assert `/legacy` old behavior; do not keep old root wording as an acceptance requirement. Preserve legacy JS consumer tests since its route remains supported. Delete source-copy/wording/incidental implementation assertions rather than repinning them; retain actual security guarantees.

### S5 — Documentation and release notes

`docs/dashboard.md:16-28,51-64` already organizes security/API/bridge/troubleshooting. W5 updates those sections in place for stable numbering/helper CLI, payload schema, cache/partial/provenance/observed timeline, local build/package/assets, self-only security and style exception, fonts/notices, component attribution and offline/live evidence limits. Do not create a second unrelated dashboard guide.

`CHANGELOG.md:1-3` begins with `## Unreleased` and concise feature bullets; use that existing section rather than introducing another release header. Both paths were confirmed tracked. Do not write tracker identifiers into general guidance.

## Supplied Component Evidence (not repository analogs)

SurfaceEvidence fully read **all eight** `/sandbox/home/refs/components/src/*.tsx` and both 21st.dev extracts. This satisfies the supplied-reference read through scout evidence; the conclusions here are attributed to that report rather than claimed as new live/registry inspection. All eight lack their own reduced-motion handling and licence notices. Most use `@/lib/utils` cn; establish one local utility/alias in the new frontend, not duplicate conventions. Dependencies must be discovered/resolved compatibly, not guessed.

| Supplied reference | Copy/adapt guidance and concrete hazards |
|---|---|
| `magicui__animated-shiny-text.tsx` | React/cn; inline custom property; `animate-shiny-text` needs registry keyframes (not defined in TSX). CSS reduced-motion must stop it; readable AA text without gradient. |
| `magicui__border-beam.tsx` | motion/react/cn; offsetDistance/offsetPath, masks, inline styles, infinite motion. Requires positioned rounded parent; purple adaptation; disable/unmount decorative moving layer when reduced motion. Do not falsely describe as transform-only. |
| `ui__sidebar.tsx` | React context/state, motion/react, tabler icons/cn. Width animation 60→300 and hidden labels conflict with visible numbered tasks; mobile overlay ignores animate. Adapt into accessible product navigation with actual buttons, keyboard/focus labels, no-reload selection, reduced-motion on desktop AND mobile. No verbatim component-library copy. |
| `ui__card-hover-effect.tsx` | AnimatePresence/layoutId/cn. Adapt into white/purple SummaryCards; keyboard focus equivalent, safe links, disable layout/fade animation without hiding content. |
| `ui__background-beams.tsx` | Fifty infinite SVG gradients, random render values, repeated IDs. Omit: not a locked pick, unnecessary bundle/runtime cost. |
| `ui__glowing-effect.tsx` | Pointer/scroll subscriptions, RAF, imperative motion controls not retained/stopped; CSS cannot stop JS animations. Omit rather than introduce unrelated effects. |
| `ui__card-spotlight.tsx` | CanvasRevealEffect, motion values and mask; excluded by D-07. |
| `ui__canvas-reveal-effect.tsx` | three/@react-three/fiber, WebGL shader/continuous useFrame; excluded dependency chain, incompatible with required restraint/reduced-motion guarantees. |
| `21st-magicui-number-ticker-1282.md` | framer-motion spring/useInView and empty span until spring change; uncleaned timeout. Normalize to **motion/react**, render actual count immediately (including 0), clean timers, stable accessible value, no animation in reduced-motion. |
| `21st-magicui-blur-fade-1079.md` | framer-motion, initial opacity 0/filter blur and optional in-view gating. Normalize to motion/react, never hide data on unavailable observer/animation or reduced motion; remove blur/translation/delay/exit effects. |

D-07 uses one `motion` package, not motion plus framer-motion. Newer Magic UI Tailwind syntax (`bg-linear-to-*`, `border-(length:...)`, `mask-intersect`) requires compatible selected tooling and supporting CSS. Global MotionConfig alone does not disable every opacity/imperative/spring/CSS animation: useReducedMotion branches **and** CSS prefers-reduced-motion overrides with behavior tests. a4 requires at least three visible approved animations in normal mode, all disabled under reduced motion; data appears immediately in either mode. Avoid lazy-loading and target approximately <400 KB gzip without claiming an unmeasured result.

D-06 font evidence is the supplied README's previous marketing-site inspection, **not** a new inspection by this session. Self-host `@fontsource-variable/anybody` (headings/counts), `@fontsource/space-mono` (IDs/code/badges), `@fontsource-variable/dm-sans` (body), selecting needed weights/subsets. Preserve actual distributed OFL 1.1 notices. Magic UI needs actual MIT notice; supplied TSX lacks it. Aceternity is end-product adaptation only: rename/restyle/integrate with attribution; do not commit registry dumps or verbatim library source. White/near-white default, purple primary and neutral AA text; no external font/CDN runtime requests.

## No Analog Found

The 29 “none” entries above are the explicit no-analog list: new frontend toolchain/entry/styles/React-only utilities; repositories inventory UI; accessible sidebar/summary animation components; frontend DOM/motion harness/tests; notices; actual generated output. The only package.json located elsewhere was an unrelated planning spike, not a production frontend convention. Do not use that as an exact React/Vite analog or invent installed versions. Use locked context plus supplied component evidence, freeze the new interface in the plans and generate the lock/output during execution. The 11 flow matches are deliberately partial: vanilla safe text/SSE/AEDT code and Python payload shape do not establish React testing/build conventions.

## Acceptance and Verification Handoff

No tests, builds, linters, formatters, installs, product runtime, commits or branches were run/created here. The only shell command inspected tracked origins. Parent supplies the planning verification receipt, including `rule adherence by future agents` under unverified.

**Observed parent baseline, not mapper execution:**

- Mandated broad `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard` exited **2** during unrelated dependency collection (including mcp/apscheduler). Preserve this mandated gate and honestly report the baseline blocker; do not install Python dependencies or claim it passed.
- Targeted `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard*.py` passed **90 tests**.
- Existing docs/public_repo selection passed **31 tests**. This is baseline, not proof of changed implementation.

Each relevant execution plan includes both mandated broad and targeted offline commands. An automated verify must immediately name observable failure: nonzero exit **or zero relevant tests collected**. W3 commands, from `omega_prime/dashboard/frontend`, are `npm ci --registry=https://registry.npmjs.org`, `npm test -- --run` (test script vitest), `npm run build` (tsc then Vite). Frontend does not exist yet, so no command is claimed run; discover compatible versions and commit the resulting lock and hashed assets, never invent them.

| Criterion | Evidence plan boundary |
|---|---|
| a1 / D-01 | W1 real persisted/restart/concurrent allocation and writer/CLI/API consistency; W3 canonical sidebar/display/selection tests; execution runtime smoke creates/selects a task, restarts with same seq and checks JSON/SSE/UI. No product run now. |
| a2 / D-02 | W2 offline real-shaped checks/duration/links/Greptile commit provenance, both repos/default branches/cached failures; W3 rendered evidence and wiring unknowns; live GitHub parity remains outer-driver evidence, not fake-test proof. |
| a3 / D-02 | W2 bounded org inventory/open-PR counting/partial states; W3 search/sort/null display; real org parity outer driver. |
| a4 / D-03/D-06/D-07 | W3 local fonts/licences/build/motion/reduced-motion/AA behavior; normal count never blank; visible real-data design proof outer driver. |
| a5 / D-05/D-08 | W1/W2 redaction and publication; W3 safe text/links; W4 Host/method/bind/SSE/CSP/static/package security. No security-assertion weakening. |
| a6 / D-10/D-11 | W5 records actual execution commands/results; openshell-podman tier and Anda-signed access receipt produced by outer driver, not mapper/offline suite. |
| a7 / D-04/D-11 | Outer driver captures real-data Overview/Tasks/CI-CD/Repositories screenshots after verified head. W5 hands off explicitly; no generated/mock screenshots claimed. |

Retain all prohibitions: no write actions/CI reruns/merges/comments, no public hosting/remote auth, no unrelated collectors, sandbox-core changes/deploys/restart during planning, no token/argv/secret disclosure, no runtime Node/CDNs, no force-push/merge/auto-merge. Export/push/draft PR stacked on existing unmerged parent, bounded Greptile loop and verified-head restart are outer-driver obligations, not execution blockers for the planning artifact. No research/nyquist/UI-spec blocker is introduced.

## Metadata

**Analog search scope:** tracked dashboard Python/static source, dashboard tests, pyproject.toml, dashboard documentation/changelog; frontend/package discovery; supplied read-only component directory through full SurfaceEvidence.  
**Direct exact-source/document reads:** 18 implementation/test/package/docs files (plus instructions, ownership and mandatory phase/reference inputs).  
**Tracked-origin checks:** 27 repository source/test/package/doc paths returned by git ls-files; no mirror analogs emitted.  
**Primary analogs:** status_file.py, model.py, poller.py, collectors/github.py, app.py. Supplemental sources supply transport, redaction, test, CLI and packaging excerpts.  
**Pattern extraction date:** 2026-10-20.  
**Planning consumer:** Planner105 was messaged with W1/W2 seam, numbering/locking/timeout pitfalls, package/static/SSE/motion warnings and proposed wiring evidence ownership before mapper completion.

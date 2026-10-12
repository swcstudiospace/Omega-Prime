# Phase 77: Maintained docs and knowledge base — Pattern Map

**Mapped:** 2026-10-11  
**Files classified:** 56 candidate surfaces: 35 named existing/generated/supporting files plus the 21 exact published mirror documents. This is a pattern map, not an additional implementation plan; the parent chooses final edit ownership and intentionally unchanged supporting files.  
**File-level analog coverage:** 56 / 56; missing strict-refresh subpatterns are called out separately below.

## Scope and evidence boundary

- Preserve KB-01..03 and DOC-01..05 and all 21 milestone criteria; no deferrals or narrowed client-only deliverable.
- Inputs: `77-CONTEXT.md`, the assignment's scoped source/doc paths, actual `docs/` listing, `local://v15-greptile-published-snapshot.json`, `local://v15-phase77-fetch-receipt.json`, and `local://v15-phase77-framework-selection.json`.
- Parent explicitly superseded the unreachable `agent://DocsDeliveryInventory` context pointer. No decisions are inferred from that missing inventory. No `77-RESEARCH.md` was present in the phase listing. Root `CLAUDE.md` and project-local `.claude/skills/*/SKILL.md` / `.agents/skills/*/SKILL.md` were not found by scoped discovery.
- Fetch receipt reports 21 expected/retrieved/full documents, stable before/after publication, zero truncated documents/failures, 239,020 content UTF-8 bytes and 238,985 Python codepoints. These are collector-reported retrieval facts, not mapper-executed importer or project verification.
- Publication identity: `swcstudiospace/omes-bot`; `namespaceId=3b0877f2-8580-4305-b246-a1cf4cfb31f3`; `versionId=2026-10-08-1791571202911-c82b36`. Historical synthesized evidence predates v14; it is not canonical current architecture or instructions.
- Existing custom Python/stdlib MCP HTTP transport and private filesystem primitives remain the stack. No AI framework, model judge, new provider, retries, telemetry, deployment or publication is part of this map.
- Mapper changed only this file. No source edits, builds, tests, lint/typecheck/format/gates, commits or live service operations were run.

## File Classification

`exact` means a directly reusable existing surface or behavior; `role-match` means a reusable format/role, not proof that the new semantics already exist. Documentation is classified as a component with file-I/O consumption; it is not a controller or service merely because it documents one.

| Candidate file | Role | Data flow | Closest tracked analog | Match |
|---|---|---|---|---|
| `omega_prime/greptile/kb_sync.py` | service/controller | request-response → batch → file-I/O | Same file; `omega_prime/tooling/fs.py` for writes | exact seam; missing strict transaction |
| `omega_prime/tests/test_kb_sync.py` | test | request-response + file-I/O | Same file's `_Peer`, `main`, `sync` consumers | exact |
| `kb/manifest.json` | model | batch → transform → file-I/O | `omega_prime/greptile/kb_sync.py::sync` | role-match; richer provenance required |
| `kb/README.md` | component | file-I/O | Same file; `docs/greptile.md` | exact format; claims need reconciliation |
| `.github/workflows/kb-refresh.yml` | config | event-driven → batch | Same workflow's CLI invocation | exact |
| `docs/architecture.md` | component | file-I/O | Same page's shape/loop/registry sections | exact layout; claims need source evidence |
| `docs/build-aesthetics.md` | component | file-I/O | Same page's contracts/seams/layout | exact; extend existing pattern guide |
| `docs/agent-loop.md` | component | file-I/O | `docs/architecture.md`, `docs/build-aesthetics.md` | role-match |
| `docs/driving-the-bot.md` | component | file-I/O | `docs/user-guide.md`, README configuration | role-match |
| `docs/user-guide.md` | component | file-I/O | Same page's usage/approval/receipts sections | exact format |
| `docs/setup.md` | component | file-I/O | Same page; root README quickstart | exact format |
| `docs/faq.md` | component | file-I/O | Same page's prerequisite and scope answers | exact format |
| `docs/deploy.md` | component | file-I/O | `docs/setup.md`, `docs/gitbook-sync.md` | role-match; no live deployment |
| `docs/tool-host.md` | component | file-I/O | `docs/setup.md`, `SECURITY.md` | role-match |
| `docs/grok-bot-native.md` | component | file-I/O | `SECURITY.md`, root README host pointers | role-match |
| `docs/connectors.md` | component | file-I/O | `docs/architecture.md`, `docs/setup.md` | role-match |
| `docs/substrate.md` | component | file-I/O | `docs/architecture.md` substrate section | role-match |
| `docs/migration.md` | component | file-I/O | Root README configuration/runtime distinctions | role-match |
| `docs/greptile.md` | component | file-I/O | Same page's connection/review/KB sections | exact format |
| `docs/gitbook-sync.md` | component | file-I/O | Same page plus `.gitbook.yaml` | exact format |
| `docs/README.md` | component | file-I/O | Same page's audience landing links | exact |
| `docs/SUMMARY.md` | config | transform → file-I/O | Same heading/link navigation | exact |
| `README.md` | component | file-I/O | Same architecture/quickstart/docs layout | exact |
| `CONTRIBUTING.md` | component | file-I/O | Same setup/agreements/receipts layout | exact format |
| `SECURITY.md` | component | file-I/O | Same reporting/secrets/host boundaries | exact format |
| `CHANGELOG.md` | component | file-I/O | `docs/architecture.md` evidence-first prose | role-match; single parent owner |
| `pyproject.toml` | config | file-I/O | Existing Python declaration and `[project.urls]` | exact; supporting truth source |
| `.gitbook.yaml` | config | file-I/O | Existing docs root/readme/summary mapping | exact; keep mirror excluded |
| `.greptile/config.json` | config | file-I/O | Existing review rules and `kb/**` ignore | exact; no remote configuration claim |
| `docs/tool-catalog.md` | component | transform → file-I/O | `omega_prime/tooling/catalog.py::render` | exact generator; not manual Markdown |
| `assets/icon.svg` | component | file-I/O | `docs/README.md` / root README asset-link convention | role-match; repair references before adding assets |
| `assets/banner.svg` | component | file-I/O | `docs/README.md` banner reference | role-match |
| `omega_prime/tests/test_docs.py` | test | file-I/O | Existing navigation-source comparison | exact deletion candidate, not a repin |
| `omega_prime/tests/test_greptile_config.py` | test | file-I/O | Existing schema/source configuration assertions | exact deletion candidate, not a repin |
| `omega_prime/tests/test_tool_catalog.py` | test | transform + file-I/O | Existing `render` and `main` consumers | exact cleanup boundary; separate behavior from source/copied-default assertions |

### Exact historical document targets (21)

Every row has role **component**, data flow **batch → file-I/O**, closest analog **`omega_prime/greptile/kb_sync.py::_stamped` plus `kb/README.md`**, match **role-match**. Body source is the collector snapshot, not the canonical page of a similar name. Prefix each returned path with `kb/`; preserve relative paths rather than flattening, renaming, or creating another canonical copy.

| Exact mirror target |
|---|
| `kb/index.md` |
| `kb/docs/agent-delegation-and-modes.md` |
| `kb/docs/agent-prompt-and-models.md` |
| `kb/docs/agent-runtime.md` |
| `kb/docs/coding-workspace-and-service-tools.md` |
| `kb/docs/conversation-turn-loop.md` |
| `kb/docs/durable-workflows-and-receipts.md` |
| `kb/docs/evaluation-and-audit.md` |
| `kb/docs/governance-security-and-learning.md` |
| `kb/docs/learning-and-growth.md` |
| `kb/docs/memory-system.md` |
| `kb/docs/model-provider-integrations.md` |
| `kb/docs/native-prime-runtime.md` |
| `kb/docs/operations-integrations-and-quality.md` |
| `kb/docs/policy-credentials-and-contracts.md` |
| `kb/docs/prime-kernel-and-substrate.md` |
| `kb/docs/scheduled-routines.md` |
| `kb/docs/session-persistence-and-search.md` |
| `kb/docs/state-continuity.md` |
| `kb/docs/tool-execution-platform.md` |
| `kb/docs/tool-registry-and-capabilities.md` |

## Pattern Assignments

### Importer and private atomic resource writes

**Primary analogs:** `omega_prime/greptile/kb_sync.py`, `omega_prime/tooling/fs.py`. Modify the importer, reuse filesystem helpers; do not fork a second client or private writer.

**Imports/seam:** `kb_sync.py:16-26` uses future annotations, stdlib modules, `Callable`, `Path`, `Any`. `McpHttpClient.__init__` accepts the injected `opener` and timeout; preserve this request-response seam. Existing transport carries initialization, session id, JSON and SSE handling without a new SDK.

**Tool response boundary, `kb_sync.py:69-86`:**

```python
result = body.get("result", {})
if not isinstance(result, dict):
    raise GreptileError(f"tool {name} returned no result object")
if result.get("isError"):
    raise GreptileError(f"tool {name} failed: {_content_text(result)[:200]}")
if "structuredContent" in result and isinstance(
    result["structuredContent"], dict
):
    return result["structuredContent"]
text = _content_text(result)
try:
    return json.loads(text)
except json.JSONDecodeError as exc:
    raise GreptileError(
        f"tool {name} returned unparsable content: {exc}"
    ) from exc
```

Copy the explicit error propagation and JSON parsing convention, not the permissive domain defaults later in `sync`.

**Authentication/error boundary, `kb_sync.py:124-143`:** bearer authorization and session headers are carried in the HTTP request, and 401 raises `GreptileError("unauthorized: check GREPTILE_API_KEY")`; the token is not interpolated into that error. `main` at lines 197-228 converts `GreptileError` to stderr/exit 1. Preserve no-key/no-network semantics, distinguish skipped/unavailable from successful complete refresh, and reconcile prose with actual exits. There is no authorization middleware to add to this local importer.

**Private writer, `fs.py:43-58`:**

```python
target = Path(path)
ensure_private_dir(target.parent)
fd, tmp_name = tempfile.mkstemp(
    dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
)
try:
    with os.fdopen(fd, "w", encoding=encoding) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp_name, mode)
    os.replace(tmp_name, target)
except BaseException:
    with contextlib.suppress(OSError):
        os.unlink(tmp_name)
    raise
```

`fs.py:21-32` supplies mode `0600` files / `0700` newly created directories; `ensure_private_dir` does not chmod existing directories. `fs.py:59-65` fsyncs the containing directory where supported. `atomic_write_json` at lines 68-78 uses sorted keys, two-space indentation and a trailing newline, delegating to `atomic_write_text`. Use these actual symbols for Markdown resources and manifest serialization. Do not claim they provide a multi-document transaction: they atomically replace one file only.

**Unsafe-path guard, `kb_sync.py:269-273`:**

```python
if not isinstance(path, str) or path == "":
    raise GreptileError("knowledge base returned an empty document path")
if path.startswith("/") or ".." in Path(path).parts:
    raise GreptileError(f"refusing unsafe knowledge base path: {path}")
```

Retain the path boundary while applying the locked complete-fetch contract. Current filtering in `_list_all` must not silently discard invalid list entries and call the reduced set complete.

**Baseline behaviors not to copy:** `sync:154-169` and `_find_namespace:243` use the obsolete `repoNamespaceExternalId`; `sync:171-180` defaults missing content to `""` and writes each document during retrieval; `sync:163,177-179,184-193` rewrites fetch timestamps on every call; `_list_all:252-266` trusts loose totals and empty batches. These are precisely the cutover/preservation/churn defects, not approved patterns.

### namespaceId migration and complete publication contract

**API evidence:** current mounted schemas for `list_knowledge_bases`, `list_knowledge_base_documents`, `get_knowledge_base_document` were read directly. Use `namespaceId` in discovery result consumption and document tool arguments; there is no deprecated-argument alias or fallback. Transport response payloads may carry deprecated fields as upstream evidence, but local client behavior must not depend on them.

- Discovery returns `repositories`, `namespaceId`, `repoName`, `total`, `returned`; advance by `returned`. `truncated` / `repository_scan_cap` means offset cannot recover missing handles.
- Document listing requires `namespaceId`, supports `limit <= 100`, `offset`; returns namespace/repository identity, `indexPresent`, `sectionVersions.docs`, paths, total/returned. Empty total zero is absence of exposed publication, not a complete imported mirror.
- Retrieval requires `namespaceId` and the exact returned path; returns identity, immutable version, full `characterCount`, content, untrusted notice. `response_character_cap` means a prefix only; no continuation argument exists. Do not add retries/chunking or accept that prefix.
- Reconcile complete listing counters, unique exact paths, required index, every returned document identity, expected version, full content/count and truncation indicators before replacing prior complete material. Preserve provenance including repository, namespace, path, version, actual fetch time, counts and completeness. Missing/empty/incomplete/truncated/mixed publication must not produce success or overwrite prior complete material.
- Fetch-all-before-write is the required boundary, not existing behavior. Publication replacement failure also cannot leave a prior complete mirror half updated. Single-file atomic helpers alone do not establish this property; the parent's chosen complete-resource commit boundary needs explicit preservation evidence.
- Compare publication/content/provenance identity excluding a newly generated retrieval clock before writes. An unchanged complete refresh leaves existing document and manifest bytes untouched; do not hide genuine content/version/path changes. Preserve real fetch provenance from the accepted snapshot rather than manufacturing the missing initial listing timestamp.

**Collector facts:** `listingBeforeProvenance.fetchedAt` is null and explicitly documents that the original listing envelope/time was not supplied. Keep this limitation honest. The fetch receipt says Python-codepoint and UTF-16 counts coincide for these 21 documents, so it cannot establish an API unit for non-BMP text. Do not equate UTF-8 byte length with character count.

### Behavioral importer tests

**Analog:** `omega_prime/tests/test_kb_sync.py`.

**Imports, lines 3-12:** stdlib JSON/urllib/email/Path, then pytest, then absolute `omega_prime.greptile.kb_sync` imports. Keep injected requests/local output consumers, not runtime source searches or copied defaults.

**Scripted request boundary, lines 26-48:**

```python
class _Peer:
    """Scripted MCP server: canned (body, headers) per request, requests recorded."""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[dict] = []

    def __call__(self, request, timeout=None):
        envelope = json.loads(request.data.decode("utf-8")) if request.data else {}
        self.requests.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "envelope": envelope,
            }
        )
        if not self._script:
            raise AssertionError("peer script exhausted")
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        body, headers = outcome
        return _Response(body, headers)
```

`_tool:57-60` models MCP text content as JSON; `_init_script:63-71` models initialize/session/notification. `test_full_sync_json_variant:74-149` calls real `sync` and inspects files/manifest/request headers; `test_auth_failure_is_secret_free:231-241` calls the real transport with an injected HTTP denial. Reuse those behavior seams. Cut over fake API fields and expected request arguments to the actual namespaceId contract, not an obsolete alias.

Parent-selected consumers should cover complete21/index import, all counters/content/provenance, JSON/SSE transport, absent/empty/malformed/truncated/mixed responses, partial retrieval and output failure preserving prior complete bytes, unchanged refresh leaving bytes intact, correct CLI status/stderr and no credential disclosure. These are required parent evidence, not checks run by the mapper.

### Historical mirror resources and canonical documentation separation

**Analog:** `kb_sync.py::_stamped` at lines 276-282 and `kb/README.md:1-14`.

```python
header = (
    f"<!-- Greptile-synthesized Mirror of {path} "
    f"(version {version or 'unknown'}, fetched {fetched_at}). "
    f"{NOTICE} -->\n\n"
)
return header + content
```

Reuse explicit untrusted classification and exact source body retention, not the `unknown` version fallback for an accepted complete publication. The single `kb/` tree plus manifest is the historical mirror. Existing `kb/README.md` incorrectly names `swcstudiospace/omega-prime` and describes no publication; reconcile to the observed complete historical snapshot and its limitations.

`docs/greptile.md:36-62` already separates `kb/` from GitBook; its example repository and blanket “clean exit” prose are stale. `.github/workflows/kb-refresh.yml:40-46` already invokes `--repo swcstudiospace/omes-bot --out kb`. Keep one entrypoint; do not add another mirror generator or canonical copy of the fetched bodies. A client patch without materializing all exact 21 targets is incomplete.

### Canonical architecture, execution/state/security diagrams

**Analog:** `docs/architecture.md:10-46,57-85`. Existing diagrams are fenced text in-page, so new diagrams need no separate asset files unless the parent deliberately chooses them.

**Diagram style, lines 12-19:**

```text
prompt ──▶ loop ──▶ registry ──▶ tools ──▶ receipts
             │          │
          skills     policy + broker + journal
             │          │
        memory ◀── substrate surface ──▶ Hindsight / RAGflow
```

**Boundary style, lines 61-67:**

```markdown
- Destination transport (`omega_prime/providers/destination.py`) manages seat policy
  enforcement, DNS resolution, numeric dialing, and TLS validation.
- Composed preview and review orchestration (`omega_prime/tools/webpack.py`) uses
  registered `RootOperation` scopes to prevent SSRF and unmediated DNS rebinding.
- Browser egress confinement (`omega_prime/tools/browser_egress.py`) manages sandbox
  accounting, socket ledger proofs, and terminal close receipts.
```

These are formatting/source-pointer analogs, not mapper-verified runtime claims. Parent canonical writers must source-link the actual symbols and exercised controls, not elevate the old prose or historical KB into proof. Cover execution, shared state/storage and security boundaries explicitly in the existing architecture/loop/host/security surfaces. Distinguish product process, optional native in-process runtime, separately started Red Team backend and external services. The current “no Rust service beside it” blanket wording and fixed 165/117 tool-count claims require reconciliation, not blind copying.

### Existing design-pattern guide and real extension seam

**Analog:** `docs/build-aesthetics.md:25-45,58-71`; extend this existing guide instead of creating a parallel patterns manual.

```markdown
## Seams, fakes, receipts

Every external thing — models, transports, CLIs, runners,
stores — arrives through an injected seam. Tests use fakes only;
no test touches the network or a live CLI (the receipts E2E runs
hermetic local commands). A completion claim cites a command and
its exit code; the quality pack refuses self-approval at both
the receipt and the stamp.
```

Use the seam/contract/receipt structure while correcting stale assertions against the actual current runtime; do not perpetuate universal claims merely because they already appear in prose.

**Source-linked extension example:** [`ExtensionHooks.register_before_model` and `run_before_model`](../../../omega_prime/agent/extensions.py), source lines 21-33:

```python
def register_before_model(self, fn: Any) -> None:
    """Append a ``fn(messages, tools)`` hook."""
    if not callable(fn):
        raise TypeError("hook must be callable")
    self.before_model.append(fn)

def run_before_model(self, messages: list, tools: Any) -> dict | None:
    """Run each hook. The first stop verdict wins; else None."""
    for hook in self.before_model:
        verdict = hook(messages, tools)
        if isinstance(verdict, dict) and verdict.get("stop"):
            return verdict
    return None
```

This module exposes registration-order `before_model` callbacks only, with first stop verdict winning; its docstring explicitly says no plugin loader ships. Do not invent discovery/install/loading APIs. Documentation extension guidance should link this actual symbol and existing roster/policy/registry conventions, not add runtime machinery.

### Usage, prerequisites, supported Python and repository URLs

**Analogs:** root `README.md:65-110,189-208`, `docs/setup.md`, `docs/user-guide.md`, `CONTRIBUTING.md`, `pyproject.toml:8,39-44`.

**Root README quickstart, lines 67-73:**

```bash
git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git omega-prime
cd omega-prime
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest omega_prime/tests -q
.venv/bin/python -m omega_prime.setup_check --root .
```

This is an existing command style, not a mapper-run receipt. Keep runnable commands aligned to parent-exercised CLI/API behavior and optional toolchain prerequisites. Do not claim a model/account/remote attach operation worked merely because setup docs exist. `CONTRIBUTING.md` currently uses the old repository URL and “No Node, Rust” blanket statement; FAQ has the same toolchain simplification, and security prose says secrets never use files despite private token-file support.

**Declared metadata, `pyproject.toml:39-44`:**

```toml
[project.urls]
Homepage = "https://github.com/swcstudiospace/omes-bot"
Repository = "https://github.com/swcstudiospace/omes-bot"
Documentation = "https://github.com/swcstudiospace/omes-bot/tree/main/docs"
Issues = "https://github.com/swcstudiospace/omes-bot/issues"
Changelog = "https://github.com/swcstudiospace/omes-bot/blob/main/CHANGELOG.md"
```

`requires-python = ">=3.11"` is the declared package floor; classifiers list 3.11–3.14. `docs/setup.md` describes retained CI jobs at 3.12–3.14. Do not collapse declared compatibility, CI matrix and exercised interpreter into a single claim or silently change the supported floor. Use actual `omes-bot` URLs consistently; do not create a duplicate compatibility URL convention.

### Landing, navigation, catalog, links and assets

**Analogs:** `docs/README.md`, `docs/SUMMARY.md`, `.gitbook.yaml`, `omega_prime/tooling/catalog.py`.

**Single-copy navigation, `SUMMARY.md:24-32`:**

```markdown
## Build

- [Build aesthetics](build-aesthetics.md)
- [Architecture](architecture.md)
- [ADR 0001: Prime merge architecture](adr/0001-prime-merge-architecture.md)
- [ADR 0002: Prime runtime integration](adr/0002-prime-runtime-integration.md)
- [Tool catalog](tool-catalog.md)
- [Greptile](greptile.md)
- [GitBook sync](gitbook-sync.md)
```

Keep headings and relative page links; every canonical Markdown page appears exactly once. One parent-designated owner updates root README, SUMMARY and CHANGELOG. The mirror is not added as 21 GitBook pages. `.gitbook.yaml:1-5` is the root configuration:

```yaml
# GitBook space config: read the docs/ directory of the mapped repo.
root: ./docs/
structure:
  readme: README.md
  summary: SUMMARY.md
```

`docs/README.md:3` uses `../assets/banner.svg`; root README uses `assets/banner.svg`. Preserve location-relative asset paths and inspect rendered/reference correctness rather than introducing redundant art. Existing landing audience categories are install/use/tools/build/publish.

**Generated catalog, `catalog.py:126-146`:** render reads the tracked roster, constructs `full_inventory_registry`, extracts real registry schemas and walks roster order. `full_inventory_registry:88-123` registers actual family definitions for metadata without dispatching their handlers. The generator fails if a rostered tool has no actual definition; do not substitute invented default metadata or edit generated Markdown manually.

**Core rejection, lines 145-151:**

```python
fn = schemas.get(name)
if fn is None:
    raise SystemExit(
        f"tool catalog: roster names {name!r} with no registered "
        "definition; register its family before regenerating"
    )
```

The parent owns regeneration/check execution. `catalog.main` compares fresh output for `--check`; this mapper did not invoke it.

### Delete source/schema/copied-default tests; preserve behavior consumers

The locked contract supersedes old instructions embedded in docs that say these tests must be retained. Do not repin source text, navigation enumeration, config defaults or schema allowlists to make refreshed prose pass.

- `omega_prime/tests/test_docs.py::test_summary_links_resolve_exactly_once` (15-24) compares Markdown source links against the docs directory. It is a documentation source/wiring assertion, not importer behavior: deletion candidate, not repin. Navigation/link review remains required parent evidence by the approved consumer/review method.
- `omega_prime/tests/test_greptile_config.py` carries a copied `TOP_LEVEL_KEYS` schema and strictness/status/rule-count/scope assertions at 52-73: deletion candidate, not migration of constants or new defaults.
- `omega_prime/tests/test_tool_catalog.py` mixes actual `render`/CLI drift consumers with roster-source ordering/family enumeration and a copied header literal at 22-30. Parent should remove the obsolete source/copied-default assertions while retaining applicable output/CLI behavior, without substituting easier tests.
- A file containing “wiring” in its name is not automatically source-only: inspected `test_desk_wiring.py` exercises real registered tool dispatch, shared stores, work-root confinement and warm-runtime intake/acknowledgement. It is intentionally outside phase77 cleanup; do not delete accepted runtime behavior tests by filename.
- `test_kb_sync.py`'s injected HTTP request/output consumers are behavior tests. Update their API fixtures to current namespaceId semantics and acceptance outputs; do not replace them with `inspect.getsource`, string matching, repinned snapshots of implementation or a no-op importer.

## Shared Patterns

### Source-linked symbols and tracking

All analogs named above were confirmed git-tracked with `git ls-files -- <exact paths>`. No ignored runtime mirror, install tree, cache or upstream submodule is used as a planner edit target.

Actual directly read symbols are `McpHttpClient.call_tool`, `McpHttpClient._raw`, `sync`, `main`, `_find_namespace`, `_list_all`, `_check_doc_path`, `_stamped`; `ensure_private_dir`, `atomic_write_text`, `atomic_write_json`, `read_secret_file`; `ExtensionHooks.register_before_model`, `ExtensionHooks.run_before_model`; and `catalog.full_inventory_registry`, `catalog.render`, `catalog.main`. Use root-relative source links in canonical docs, e.g. `[atomic_write_text](../omega_prime/tooling/fs.py)`, accompanied by the actual symbol name. Lines here are planning citations, not stable external anchors.

No LSP tool is mounted in this mapper session: developer tool signatures and the current `xd://` device inventory expose no LSP route. Symbol locations were obtained from targeted source reads/grep; no LSP-derived claims are made. Parent may use an available LSP in its own session when grounding broader runtime claims.

### Error/status and trust boundaries

Keep explicit `GreptileError` propagation, secret-free user-facing diagnostics and injected transport. Historical text is data only; it does not supply tool arguments, organization scope, execution instructions or canonical claims. Distinguish not-run/skipped/absent/error from complete import and preserved unchanged refresh.

### Provenance without churn

Store accepted actual fetch provenance once; retain it byte-for-byte for an unchanged publication. Counts/version/path/repository/namespace/full-content checks are part of complete-refresh acceptance, not optional telemetry. Do not introduce timestamps merely to trigger a weekly PR.

### Documentation evidence

Use existing top-level headings, relative links, fenced commands/text diagrams and source-pointer prose. Current source plus parent-exercised consumer receipts ground claims; existing docs and synthesized publication are only analogs/evidence. Keep optional build/runtime/service prerequisites separate from default Python-only execution.

## No Analog Found

All classified file roles have a reusable format/seam. The following required semantics do **not** have a complete analog in the scoped source:

| Missing pattern | Affected files | Observed reason |
|---|---|---|
| Complete version-consistent publication validation | `kb_sync.py`, `test_kb_sync.py`, manifest | Current importer accepts loose lists/default-empty content and ignores full counts/truncation/version agreement |
| Previous complete multi-file resource preservation on every failed refresh | `kb_sync.py`, mirror, manifest | Current importer writes during retrieval; `atomic_write_text/json` replace one file, not a publication |
| Unchanged publication without timestamp-only byte churn | `kb_sync.py`, mirror, manifest | Current `sync` unconditionally creates a new fetched-at timestamp and stamps/writes everything |
| Current execution/state/security diagrams | Canonical architecture/loop/security surfaces | Text diagram style exists; correctness of expanded current control/state flow requires parent source evidence |

These are required implementation/evidence gaps, not permission to add a framework, defer criteria, accept partial content, or create mock behavior. Parent planner chooses the implementation inside existing seams.

## Metadata

**Analog search scope:** five primary required files; bounded supporting docs/root README/contribution/security/package/GitBook/Greptile/refresh paths; tracked catalog and directly located extension seam; named docs/schema/catalog tests and narrowly discovered desk-wiring test to distinguish behavior from source assertions. No broad upstream or agent-context search.  
**Source/doc files read:** 22 repository files plus scoped artifact/API contracts; `docs/` listing supplies additional candidate names without claiming their bodies were read.  
**Primary matches:** MCP importer, behavioral peer, private filesystem writer, canonical architecture page, SUMMARY navigation. Supporting excerpts refine those matches, not a new architecture or competing plan.  
**Parent verification, not executed here:** complete21 snapshot-to-mirror body/path/index/provenance reconciliation; namespaceId request/CLI behavior; rejected absent/incomplete/truncated/mixed/partial/publication-failure preservation; unchanged refresh byte stability; canonical source-symbol/usage review; supported Python/URL consistency; navigation/internal links/assets and catalog output; affected runtime and independent review/security/verifier under the parent contract.

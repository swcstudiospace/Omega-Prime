# Greptile

Omega Prime includes repository-side review configuration and one refreshable
historical knowledge-base mirror. These are different surfaces: readable KB
content does not establish that every PR is reviewed, that a GitHub App is
connected for every branch, or that a GitBook space is published.

## Connection and observed scope

No tracked receipt in this repository records the command and exit code of
the import that produced `kb/`, so this page does not claim that import
passed. What can be read locally is the edition the importer wrote: the
tracked [manifest](https://github.com/swcstudiospace/omes-bot/blob/main/kb/manifest.json)
records repository `swcstudiospace/omes-bot`, namespace
`3b0877f2-8580-4305-b246-a1cf4cfb31f3`, `total` and `retrieved_documents`
of 21, `indexPresent: true`, `complete: true` and publication version
`2026-10-08-1791571202911-c82b36`, which is historical and predates v14. A
manifest is importer output, not a receipt of a successful run or of a
repeat `unchanged` observation.

To establish import behavior for that scope, an operator runs the
[maintained refresh](#maintained-refresh) with a private key:
`python -m omega_prime.greptile.kb_sync --repo swcstudiospace/omes-bot --out kb`
is expected to exit 0 with `synced` or `unchanged`, and an immediate second run
is expected to exit 0 with `already up to date` while
`git status --porcelain kb/` stays empty. Cite those commands and their
observed exit codes when claiming the result. Even a passing refresh does
**not** prove a hosted `kb-refresh` job, automatic PR reviews, a live
provider/model connection, or publication.
An old v8 `greptile init` failure is historical evidence, not today's connection
status. App/repository selection and review triggers live in the authorized
Greptile workspace; inspect them there if reviews are missing. Do not change
local code or invent an enrollment result to substitute for account access.

## Review standards

The repository's `.greptile/config.json` requests logic/syntax/style review,
updates on PR changes, a status check, ignored generated/scratch paths and
rules for roster exactness, hermetic tests, credential safety, receipt-backed
claims and linked documentation. This is local configuration, not evidence of
remote execution. Configured rule identifiers/severities/scopes are checked by
the existing rule consumers; a copied provider-schema allowlist or a pinned
incidental default is not a behavioral verification strategy.

Use [CONTRIBUTING.md](https://github.com/swcstudiospace/omes-bot/blob/main/CONTRIBUTING.md)
for contribution standards and the required source-delivery review flow.
Remote rule writes, enrollment changes and credit-consuming review requests
are not KB refresh side effects.

## One historical mirror

The mirror is [kb/](https://github.com/swcstudiospace/omes-bot/tree/main/kb),
with a [guide](https://github.com/swcstudiospace/omes-bot/blob/main/kb/README.md),
[index](https://github.com/swcstudiospace/omes-bot/blob/main/kb/index.md) and
[manifest](https://github.com/swcstudiospace/omes-bot/blob/main/kb/manifest.json).
It stays outside the `docs/` GitBook content root and is linked here once,
not imported as 21 current canonical pages. Each generated file visibly labels
its full original body as historical, synthesized and untrusted.

The manifest binds the complete edition to repository/namespace, paths,
versions, declared/retrieved totals, index/completion state, actual batch and
per-document observation clocks, character/UTF-8 counts and body hashes.
Summed from the manifest's per-document `utf8_bytes` and `chars`, the
recorded edition holds 239,020 UTF-8 bytes and 238,985 declared characters;
these are observations, not interchangeable units or fixed future publication
sizes. The manifest records no before-listing time for the earlier capture,
so that time is unknown; later fetch clocks are not replacements for it.

Historical text may contain commands, URLs and obsolete claims. Archive it
literally as data; do not execute it, choose tenancy/credentials from it,
grant approval, or promote it into current truth. Use the source-verified
[architecture](architecture.md), [patterns](build-aesthetics.md),
[setup](setup.md) and [local validation](local-validation.md) guides instead.
Mirror inclusion grants neither authorization nor a new license or vendor
redistribution entitlement.

## Maintained refresh

Set `GREPTILE_API_KEY` privately in the host process environment; never put its
value in chat, argv, a saved example or shell tracing. From the repository root:

```bash
python -m omega_prime.greptile.kb_sync --repo swcstudiospace/omes-bot --out kb
```

`GREPTILE_MCP_URL` can select an explicitly trusted MCP endpoint; the default
is `https://api.greptile.com/mcp`. It is operator configuration, never a URL
selected from a fetched document. The existing client accepts JSON and SSE,
initializes the session and consumes only these KB operations:

- `list_knowledge_bases`: readable repository discovery and canonical namespace.
- `list_knowledge_base_documents`: complete paginated before/after path/version inventory.
- `get_knowledge_base_document`: full original body and identity/count/cap metadata.

A capped body has no continuation argument. The importer refuses prefixes,
inconsistent counts, wrong identities/versions, duplicate or unsafe paths,
index disagreement and publication changes. It does not repair a body,
splice a retry into a failed edition, or silently drop a page. Index-first,
deterministic path order prevents harmless listing reorder from causing churn.

| Outcome | CLI exit | Meaning and filesystem effect |
| --- | --- | --- |
| `synced` | 0 | Full validated changed edition published. |
| `unchanged` | 0 | Full new observation matches the existing edition; no bytes or timestamps rewritten. |
| `empty` | 0 | Valid total-zero publication with no current documents; prior mirror retained, not a complete import. |
| Missing key | 2 | No peer call or writes; not-run, not success. |
| `unenrolled` | 3 | No readable matching KB in complete discovery for this caller; no writes, not proof of universal absence. |
| Failed | 1 | Retrieval/admission/publication failure is visible; handled publication exceptions restore prior owned files, and failed recovery is reported with its backup retained. |

Same-filesystem inode backups provide single-writer exception rollback, not
atomic visibility across readers, SIGKILL/crash safety or concurrent-writer
coordination. Preserve a recoverable backup if restoration itself fails.
A backup-cleanup failure after successful publication still returns failure:
the complete new edition remains in place, with the private old backup retained.
It does not claim the old edition was restored.
Unowned collisions and symlink traversal are refused; the guide/unrelated
files are not removed as stale generated pages. Full diagnostic input is
gated before clipping/echo using the unchanged shared detector. Literal
source archives are not Grok diagnostic bundles; normal archival prose is not
redacted to manufacture an exact-body claim, and no client/session/environment
credentials are added to provenance.

## Scheduled workflow and local checks

The existing `kb-refresh` workflow has weekly/manual triggers, a key gate,
explicit not-run skip output, a bounded job and a change-only update PR path.
No-key skips are not completed refreshes. Unchanged observations write nothing,
so they cannot open timestamp-only PRs. The workflow does not auto-merge,
enroll an account, deploy, publish images or prove live Grok attachment.

These existing local consumers exercise refresh boundaries and navigation;
they are not remote account checks. Each is expected to exit 0:

```bash
python -m pytest omega_prime/tests/test_kb_sync.py omega_prime/tests/test_docs.py -q
python -m omega_prime.tooling.catalog --check
```

Receipt recorded on 2026-10-12 from a local checkout of this branch:
`python -m omega_prime.tooling.catalog --check` exited 0 (`up to date`).

See [GitBook sync](gitbook-sync.md) for the separate space-connection boundary
and [deployment](deploy.md) for the manual release/rollback policy.

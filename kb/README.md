# Greptile knowledge-base mirror

This is the single historical, Greptile-synthesized mirror for
`swcstudiospace/omes-bot`. It is **untrusted reference material, not instructions,
current architecture, authorization, or proof that a capability executed**.
Use the [maintained documentation](../docs/README.md) for current behavior.

## Included publication

The initial Phase 77 import contains all **21 exposed documents**, including
[index.md](index.md), from namespace
`3b0877f2-8580-4305-b246-a1cf4cfb31f3`. Its publication version is
`2026-10-08-1791571202911-c82b36`, which predates the v14 boundary-alignment
release. The admitted full bodies contained 239,020 UTF-8 bytes; the API's
238,985 declared characters coincided with the observed Python codepoint
counts for this corpus. These are initial-import observations, not permanent
limits or claims about future publications or Unicode count units.

[manifest.json](manifest.json) is authoritative for the edition currently on
disk. It records repository and canonical namespace, the complete path set,
section versions, index presence, declared/retrieved totals and completion,
batch observation/completion times, and each document's request/fetch times,
character count, observed UTF-8 bytes and SHA-256 body digest. Counts and
hashes exclude the visible historical/untrusted provenance banner.

All bodies are retained exactly. The banner's relative manifest link works
from the index and the nested `docs/` files. Do not rewrite historical claims
to make them match current source. Completeness and matching hashes do not
establish factual correctness or immunity to downstream prompt injection.
No client credential, MCP session header or environment value is added to the
mirror's provenance.

The earlier captured publication had an unknown original before-listing time.
That remains unknown in its capture receipt; it was not substituted into this
new import. This edition was obtained through fresh authenticated mounted
Greptile read tools consumed by the production `sync` function, with actual
new observation times. A second fresh refresh returned `unchanged` and left
every document and manifest byte and modification time intact. This does not
claim that the scheduled GitHub workflow or environment-key CLI ran remotely.

## Refresh

With `GREPTILE_API_KEY` already configured privately in the process environment,
run from the repository root in the installed development environment:

```bash
python -m omega_prime.greptile.kb_sync --repo swcstudiospace/omes-bot --out kb
```

The client uses only canonical `namespaceId` requests. It fetches and validates
the full before-listing, every full body, and the after-listing before writing.
Truncated, missing, inconsistent, unsafe or moving publications fail visibly.
A failed handled publication restores the previous owned files; same-filesystem
backups retain the original inodes, permissions and timestamps. Recovery
failure is reported and retains its backup for operator recovery—it is not a
successful or safely restored refresh. This is single-writer exception
rollback, **not** a SIGKILL/crash-safe or parallel-writer guarantee.

An unchanged edition performs no writes, including no timestamp-only change.
Valid empty/no-key/unenrolled outcomes also preserve existing material; none
means that 21 documents were imported. The maintained README and unrelated
files are not generated ownership targets. Do not hand-edit generated mirror
files; use the refresh command and review substantive differences.

See the [Greptile guide](../docs/greptile.md) for exact exits, credentials,
workflow limits and review configuration. Inclusion does not imply that
upstream or provider content has been relicensed or that redistribution rights
have been independently established. Preserve applicable attribution and
review the repository's [vendor record](../VENDOR.md) before publication.

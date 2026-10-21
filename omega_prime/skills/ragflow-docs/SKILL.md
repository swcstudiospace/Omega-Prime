---
name: ragflow-docs
description: When to search the desk document datasets and how to cite them without treating a chunk as a repository fact.
bots: [bot-00-omega-prime]
gates: []
---
<skill name="ragflow-docs" version="1">
# RAGFlow Docs

## Served tools

Use `lead_docs_search` when the topic is known but its file is not. The search may return indexed chunks; inspect the cited file in the relevant checkout before treating a claim as a repository fact. Search availability and coverage are not guaranteed. In Solo mode, `not_configured: solo` is a terminal local outcome, not permission to try an external Desk or agent-bus transport. Use an external Desk tool only when Solo is off and Desk is explicitly configured.

## When this applies (L1)

`lead_docs_search` searches available document sources and may return chunks with source metadata.
**Docs search finds where; the file is the fact.** A chunk is not a repository fact until you
have opened the file it points to. An index can lag the file, and a chunk is a fragment with
its surroundings removed.

```
Need something from the docs?
│
├─ You know the file ──▶ Open it. Search adds nothing. §3
├─ You know the topic, not the file ──▶ lead_docs_search → open the file at the cited commit. §3
├─ The ticket names a repo ──▶ Pass repo: so the product dataset is searched too. §2
├─ About to quote or cite ──▶ Cite path + commit from the result, quote from the file. §4
├─ Search returned nothing ──▶ Absence from the index is not absence from the repo. Grep or open. §5
└─ Empty result with a reason ──▶ Fail-open. Note the reason; fall back to the file. §5
```

Search coverage depends on the configured sources; do not assume a dataset, path, commit, or
document is present. Receipts, secrets and transcripts should not be sought in a docs index.

---

## Method (L2)

### §1 What a result is

Each hit is a chunk: a heading-bounded slice of a Markdown, YAML or XML file, with `source`
(repo-relative path), `commit` (the SHA ingested), `dataset`, and a score. Three consequences:

- The chunk lost its context. A rule in a "Do not" list reads as a rule when the list heading is
  cut off. Open the file before you decide what the chunk means.
- The chunk is as old as its commit. If `origin/main` has moved past it, the file at `main` wins,
  and the chunk can be wrong in the exact line you care about.
- The score ranks similarity, not truth. A high-scoring chunk from a superseded ADR still
  outranks the one-line correction in a newer doc.

### §2 Datasets

The sources available to a search depend on the current configuration. Do not assume that a
particular repository, dataset, or document is included. Pass a repository identifier when the
tool supports it and the question concerns that repository; inspect the returned source metadata.

### §3 Search, open, then use

1. `lead_docs_search {query, repo?, limit?}`. Keep `query` a phrase, not a keyword soup; the
   retriever is semantic plus BM25 and does better with "how a receipt records a failing
   reproduction" than with "receipt fail".
2. Read the hits for `source` and `commit`. Pick the file, not the sentence.
3. Open the file. In the repo checkout, `git show <commit>:<source>` gives you exactly what was
   indexed; the working tree or `origin/main` gives you what is current. Compare when they differ.
4. Use the file's text. The chunk was the map.

If step 3 is skipped, every later claim inherits the chunk's staleness and lost context, and a
reviewer who opens the file sees a quote that is not there.

### §4 Citing

Cite what you opened, in the form the receipt and PR body can be checked against:

`path @ commit` is useful only when returned by search and verified against the file at that
commit (or at `main`, stated as such). Quote text from the opened file, not the chunk.
- `path @ commit` comes from the search result; the quoted text comes from the file at that
  commit (or at `main`, stated as such).
- Never quote a chunk as if it were the current file. If you only have the chunk, say "per the
  indexed chunk at `<commit>`, unopened" and treat the claim as `unverified`.
- If the file at `main` contradicts the chunk, cite `main` and mention the drift so the ingest
  can be checked.

### §5 Misses and fail-open

RAGFlow does not hold: receipts (`.receipts/**` is evidence, not documentation), secrets,
transcripts, uncommitted branches, or anything merged since the last ingest. A miss therefore
means "not indexed", never "does not exist". Fall back to grep on the checkout or to opening the
likely file.

`lead_docs_search` is a `read` tool: on an upstream failure it returns an empty result with a
`reason` ([desk-gateway](../desk-gateway/SKILL.md) §2). Record the reason if it affected the work, and proceed from
the files. The docs plane down is an inconvenience; a claim built on its silence is a defect.

---

## Worked example

No repository-specific worked result is included: no verified local copy of the former
Programming Desk examples or policies is available. For any search result, inspect the returned
source in the relevant checkout before citing it.

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Chunk quoted as file | Reviewer cannot find the quote | Open the file; quote from it |
| Stale commit | Claim contradicts `main` | Compare `git show <commit>:<path>` with `main`; cite `main` |
| Lost context | A list item read as a rule | Read the heading and surrounding section in the file |
| Product repo not searched | Miss on a product question | Pass `repo:` from the ticket |
| Miss read as absence | "No documentation exists" | Grep the checkout; RAGFlow lags merges and skips receipts |
| Keyword-soup query | Irrelevant top hits | Ask the question as a phrase |
| Fail-open ignored | Empty result treated as "nothing written" | Check `reason`; fall back to files |
| Looking for receipts | Nothing found | Receipts are under `.receipts/`, never indexed |

---

## Gate mapping

| Rule | Local guidance | Why |
|---|---|---|
| Open a source before treating a chunk as fact | [docs/substrate.md](../../../docs/substrate.md) | A search result is not a substitute for the underlying source |
| Record unverified results and blockers | [verification-receipts](../verification-receipts/SKILL.md) | Evidence must distinguish observed facts from gaps |

No verified local copy of the former Programming Desk quality-gate, handoff, or upgrade policies is available.
</skill>

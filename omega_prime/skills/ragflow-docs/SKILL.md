---
name: ragflow-docs
description: When to search the desk document datasets and how to cite them without treating a chunk as a repository fact.
bots: [bot-00-omega-prime]
gates: []
---
<skill name="ragflow-docs" version="1">
# RAGFlow Docs

## When this applies (L1)

`desk_docs_search` searches the desk's document datasets in RAGFlow and returns chunks, each with
a source path and the commit it was ingested from. **Docs search finds where; the file is the
fact.** A chunk is not a repository fact until you have opened the file it points to (PD-1). The
index is rebuilt on merge to `main`, so a chunk can lag the file, and a chunk is a fragment with
its surroundings removed.

```
Need something from the docs?
│
├─ You know the file ──▶ Open it. Search adds nothing. §3
├─ You know the topic, not the file ──▶ desk_docs_search → open the file at the cited commit. §3
├─ The ticket names a repo ──▶ Pass repo: so the product dataset is searched too. §2
├─ About to quote or cite ──▶ Cite path + commit from the result, quote from the file. §4
├─ Search returned nothing ──▶ Absence from the index is not absence from the repo. Grep or open. §5
└─ Empty result with a reason ──▶ Fail-open. Note the reason; fall back to the file. §5
```

Datasets: `programming-desk`, `agent-substrate`, `agent-skills`, and one per product repo
(KanbanOS, Desk Lanes, ClippyOS, Auctioning). Receipts, secrets and transcripts are never
ingested, so do not look for them here.

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

| Dataset | Contents | Search when |
|---|---|---|
| `programming-desk` | This repo: `docs/`, `skills/`, `contracts/`, `prompts/`, ADRs | Desk policy, gates, rosters, skills |
| `agent-substrate` | Substrate docs: data planes, tailnet, MCP server, planning | Memory, events, docs plane, VPS services |
| `agent-skills` | The shared skills library other agents mine into | A technique another team already documented |
| `<product>` | One per product repo | Product behaviour, release notes, API docs for the ticket's app |

`desk_docs_search` always searches `programming-desk` and `agent-substrate`. Pass `repo` (for
example `swcstudiospace/kanbanos`) to add the product dataset the ticket names. Without it, a
KanbanOS question is answered from desk docs alone and the miss looks like absence.

### §3 Search, open, then use

1. `desk_docs_search {query, repo?, limit?}`. Keep `query` a phrase, not a keyword soup; the
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

```
docs/quality-gates.md @ 3b9e1c2 §G-5 — "A plan names the command, the recovery time, and the
data implications."
```

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

`desk_docs_search` is a `read` tool: on an upstream failure it returns an empty result with a
`reason` (`skills/desk-gateway` §2). Record the reason if it affected the work, and proceed from
the files. The docs plane down is an inconvenience; a claim built on its silence is a defect.

---

## Worked example

**Good — SYSTEMS needs the intake queue's idempotency window.**

```
desk_docs_search {query: "how long processed idempotency keys are kept for desk intake"}
→ hit: docs/handoff-contracts.md @ a41f… (programming-desk), score 0.82
→ hit: docs/upgrade-plan-desk-v2.md @ c07d… (programming-desk), score 0.79
git show a41f…:docs/handoff-contracts.md   → "processed keys are retained for 30 days"
git show origin/main:docs/handoff-contracts.md → same line
Cites: docs/handoff-contracts.md @ a41f… — "retained for 30 days"
```

**Bad — same question.**

```
desk_docs_search {query: "idempotency"}
→ first hit chunk: a table row mentioning "idempotency_key ... optional"
Seat writes: "Per the docs, the idempotency key is optional for intake."
```

The top chunk was the agent-bus tool table in `skills/trackplan-dispatch`, which describes a
different component's optional argument; the intake contract in `docs/handoff-contracts.md` was
the second hit. The seat quoted a fragment about the wrong system as a fact about the queue.
Opening either file would have shown the mismatch in one glance.

**Bad — a miss read as absence.**

```
desk_docs_search {query: "kanbanos push notification test device labels"}   (no repo passed)
→ 0 hits
Seat writes: "There is no documentation of test device labels."
```

The product dataset was never searched. Pass `repo: swcstudiospace/kanbanos`, and if it is still
empty, grep the checkout before writing "no documentation".

---

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

| Rule | Gate or directive | Why |
|---|---|---|
| A chunk is not a fact until the file is opened | PD-1 | Quoting an index is claiming without observing |
| Cite `path @ commit`; state when only the chunk was seen | G-2, PD-6 | The reviewer must be able to re-open what you cite |
| Do not fetch docs through the browser when the search fails | `skills/desk-gateway` §5 | The checkout is the fallback; it is already the record |
| Receipts and secrets are not in the docs plane | G-3 | Nothing you find here should ever be a credential; if it is, escalate (PD-4) |
</skill>

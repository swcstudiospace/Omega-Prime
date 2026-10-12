# API Coverage — Phase 77: Maintained docs and knowledge base

> Full coverage by default. Opt-outs are explicit, reasoned decisions.

Phase 77 integrates the Greptile knowledge-base MCP read surface (listing,
retrieval, namespace identity, JSON/SSE transport) to maintain one complete
historical mirror. The detector signal is the phase scope itself: KB-01/KB-02
require `list_knowledge_bases`, `list_knowledge_base_documents` and
`get_knowledge_base_document` consumption through the existing direct client.

| capability | decision | reason |
|---|---|---|
| `list_knowledge_bases` | INTEGRATE | Resolve the requested repository's canonical namespace with complete pagination. |
| `list_knowledge_base_documents` | INTEGRATE | Fetch the full path listing, counters, index state and publication versions before and after retrieval. |
| `get_knowledge_base_document` | INTEGRATE | Retrieve the full body and identity/count/truncation metadata for each listed path. |
| `get_me` | OPT-OUT | Account/organization inventory is not a refresh function; configured caller scope stays external to document content. |
| `list_repositories` | OPT-OUT | General repository inventory is not needed to resolve the readable KB namespace. |
| `search_knowledge_base` | OPT-OUT | Search excerpts cannot substitute full exact archival documents. |
| `create_custom_context` | OPT-OUT | Creating persistent review rules is an unauthorized remote write unrelated to the mirror. |
| `get_custom_context` | OPT-OUT | Review-rule evidence is not published KB document content. |
| `list_custom_context` | OPT-OUT | Review-rule inventory is outside the complete-publication refresh contract. |
| `search_custom_context` | OPT-OUT | Review-rule body search is unrelated to full KB retrieval. |
| `get_analytics_overview` | OPT-OUT | Review analytics and credit/account statistics are not mirror provenance. |
| `list_analytics_filter_options` | OPT-OUT | Analytics filter discovery is not needed by this read-only importer. |
| `list_analytics_findings` | OPT-OUT | Review findings are not canonical published KB documents. |
| `get_code_review` | OPT-OUT | Review-run inspection belongs to the separate source-delivery review workflow. |
| `list_code_reviews` | OPT-OUT | Review-run inventory is unrelated to KB publication completeness. |
| `get_merge_request` | OPT-OUT | PR/MR inspection belongs to delivery, not the mirror client. |
| `list_merge_request_comments` | OPT-OUT | PR comments are not KB publication bodies. |
| `list_merge_requests` | OPT-OUT | PR/MR inventory is outside refresh scope. |
| `list_pull_requests` | OPT-OUT | The PR-list alias is outside refresh scope for the same reason. |
| `search_greptile_comments` | OPT-OUT | Review-comment search is not full-document retrieval. |
| `trigger_code_review` | OPT-OUT | Queuing a credit-consuming remote review is not an authorized KB refresh side effect. |

Surface baseline: the 21 exposed Greptile MCP operations in the mounted tool inventory; 3 integrated, 18 explicitly opted out. This is not a claim to implement every separate Greptile website/REST capability.

Local implementation controls remain mandatory: existing JSON/SSE/session transport, complete-publication validation, exact provenance, safe paths, recoverable single-writer publication and no timestamp-only churn. The get-document schema exposes neither a version selector nor continuation argument; capped responses fail, with no invented suffix recovery or retry splicing. No enrollment/account/rule mutation, model inference or source/deployment publication follows refresh.

The full applicable API-coverage hook is carried verbatim in `77-01-PLAN.md`; only decided capability rows belong in this machine-validated matrix.

## Reference oracles status (Code eval 26)

| item | status |
|---|---|
| R01–R26 source-grounded scenarios (`77-AI-SPEC.md` §5) | PROPOSED oracles for parent acceptance — not already-implemented tests |
| Existing 26 structural controls (`golden.json`/`redteam.json`) | Regression coverage only — not KB/import proofs |
| Complete-21 mirror proof | Parent-owned actual import + independent snapshot reconciliation — no child execution |


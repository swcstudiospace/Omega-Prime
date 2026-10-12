# API Coverage — Phase76 CI control plane and existing MCP conformance

Full coverage by default; opt-outs below record the approved phase/milestone boundaries, not newly deferred requirements. This phase adds no general-purpose GitHub or provider client. Its actual new external reads are GitHub environment protection and same-run artifact metadata; official Actions handle checkout/artifact transport. It also consumes the existing first-party MCP host for local conformance. The earlier PLAN contribution said no external API integration; the implemented GitHub control-plane reads require this matrix despite that planning assumption. No detector/config override.

| capability | decision | reason |
|---|---|---|
| GitHub checkout/current source commit and PR merge-SHA binding | INTEGRATE | Existing checkout plus observed git HEAD versus trusted event SHA; no head/merge conflation. |
| GitHub protected-ref observation | INTEGRATE | Trusted github.ref_protected context, exact recorded/current ref; no source booleans substituted. |
| GitHub environment protection GET | INTEGRATE | Live required-reviewer metadata read in isolated publisher; missing/unreadable/ambiguous or unnamed reviewer rules refuse. |
| GitHub same-run Actions artifacts list and immutable ID binding | INTEGRATE | Paginated current-run API list; exact singleton artifact name/id match before archive load. |
| GitHub validation artifact upload | INTEGRATE | Official upload-artifact v7; bytes upload ID bound into separate post-scan evidence, no ID circularity. |
| GitHub validation artifact download | INTEGRATE | Official download-artifact v7 within current run, then trusted metadata/id and actual archive checksum verification. |
| GitHub diagnostics/transport reports/SBOM artifact retention | INTEGRATE | Actual sanitized verifier/diagnostic outputs, SPDX and observed exit evidence; no raw logs fallback. |
| GitHub explicit workflow dispatch/ref confirmation | INTEGRATE | Default-off publisher, manual input treated as data, exact ref, protected environment, isolated noncancelling publication. |
| GitHub OCI registry authentication/tag/push and registry digest recording | INTEGRATE | Functional guarded publisher source only, after exact source/run/artifact/check/byte binding; no execution claimed. |
| GitHub environment create/update/delete, approval-rule mutation or bypass | OPT-OUT | No infrastructure/settings mutation authorized by this milestone; absent protection remains a real future prerequisite, not an invented approval. |
| GitHub deployment reviews/automatic approval APIs | OPT-OUT | Human protected-environment gate must not be self-approved by CI; no review/bypass automation. |
| GitHub workflow dispatch/cancel/rerun administration APIs | OPT-OUT | No orchestration client added; existing event triggers/concurrency only, no retries or remote execution invoked. |
| GitHub repository contents/ref write and branch protection administration | OPT-OUT | CI image job is read-only; repository delivery remains the separately authorized Ultrathink ship flow, not publisher scope. |
| GitHub PR creation/review/merge APIs | OPT-OUT | Existing KB refresh remains kb-only human-reviewed PR; full repository shipping uses its separate required review flow. No new PR client or auto-merge in this phase. |
| GitHub release/tag publication APIs | OPT-OUT | Tags/releases and live delivery operations excluded; package-write belongs only to optional guarded OCI publication source. |
| GitHub issues/projects/discussions/notifications/search APIs | OPT-OUT | Not needed by the approved CI evidence/artifact contract; existing external tracking is separate, no product GitHub integration added. |
| GitHub organization/team/user/access/settings administration APIs | OPT-OUT | No account/access mutation in the milestone; existing authenticated read scope reused, no credentials requested. |
| GitHub hooks/apps/checks/statuses administration APIs | OPT-OUT | Existing Actions outcomes consumed; no new hooks/apps/status writer or external gate service. |
| GitHub security/secret-scanning/dependabot administration APIs | OPT-OUT | Preserve current independent lock/native/license/image scans; do not mutate provider exceptions or security settings. |
| GitHub codespaces/copilot/actions-cache/runner/billing administration APIs | OPT-OUT | No runtime/platform provisioning or generalized client; owned local Docker proof only. |
| GitHub package deletion/retention management APIs | OPT-OUT | No remote artifact/package deletion authorized; same-byte source publisher only, not lifecycle administration. |
| MCP initialization/protocol session | INTEGRATE | Real SDK sessions against the same hardened image on SSE and Streamable HTTP. |
| MCP tools/list and pagination | INTEGRATE | Existing verifier collects complete pages and compares names with manifest, not nonempty/count-only proof. |
| MCP read-only tools/call | INTEGRATE | Actual production dispatch of read-only tool, not mock echo. |
| MCP unknown-tool error and approval refusal | INTEGRATE | Real error results and closed-gate denial on both transports. |
| MCP SSE/messages transport | INTEGRATE | Current supported SSE listener/client exercised against actual container. |
| MCP Streamable HTTP session transport | INTEGRATE | Same listener /mcp route, existing client --transport http; no invented server flag. |
| MCP scoped header authentication and missing/invalid/query-token denial | INTEGRATE | Actual owned read/call/admin tokens, require-auth, distinct call/admin principals and negative auth checks. |
| MCP Host/Origin/request correlation boundaries | INTEGRATE | Existing listener security and verifier Origin/request-id/traceparent checks, no alternate middleware. |
| MCP health/readiness/manifest HTTP surfaces | INTEGRATE | Actual local host health, readiness and full manifest comparison from the production verifier. |
| MCP authenticated metrics HTTP surface | INTEGRATE | Actual auth requirement and production metrics presence check. |
| MCP admin approval GET/POST/DELETE, TTL, grant/revoke/scope denial | INTEGRATE | Actual 201 grant/200 revoke and denial transitions; safe pre-run tool probe, not arbitrary handler execution claim. |
| MCP SIGTERM/drain lifecycle | INTEGRATE | Actual running uid/rootfs/caps/NNP observed; both owned container exits0 after SIGTERM. |
| MCP stdio image transport | OPT-OUT | Existing stdio functionality is preserved by the broader suite; image requirement explicitly targets the two remote transports, no stdio removal or new implementation. |
| MCP prompts/resources/subscription APIs | OPT-OUT | Existing tool-host server exposes tools, not a new generalized prompt/resource server; no capability promised by phase76. |
| MCP sampling/elicitation/root discovery/client-side UI APIs | OPT-OUT | Not used by the existing server/verifier integration; no hidden implementation or fabricated client support. |
| Additional external provider/Grok model APIs and Red Team backend missions | OPT-OUT | Live provider/Grok/backend operations explicitly excluded from this milestone; local/scripted proof cannot attest them, no requirement mapping reduced. |

## Grounding and limits

Sources: `.github/workflows/grokbot-image.yml`, `.github/workflows/ci.yml`, `.github/workflows/kb-refresh.yml`, `omega_prime/grokbot/image_gates.py`, `verify.py`, `remote.py`, `mcp_server.py`, and the five PLAN/SUMMARY pairs. Primary upload outputs: https://raw.githubusercontent.com/actions/upload-artifact/v7/action.yml (`artifact-id`, artifact digest, node24); actual download v7 tag verified at `37930b1c2abaa49bbe596cd826c3c89aef350131`. Provider references: https://docs.github.com/en/rest/actions/artifacts and https://docs.github.com/en/rest/deployments/environments .

Exercised: both17-check actual image reports, same-byte archive rehydration, fixed-CRITICAL scan/SPDX, safe manual/ref guard and real Docker diagnostic publication guard. Hosted artifact upload/download, environment approval, registry push and deployment were NOT executed. Actual repository environment inventory is0; future protected approval configuration remains required. Matrix decisions do not auto-resolve CI-02's unclassified edge row or descriptorless prohibitions; those remain flagged for honest final verification.

---
phase: "75"
slug: integrated-local-readiness
status: verified
threats_open: 0
asvs_level: 1
register_authored_at_plan_time: true
created: "2026-10-10"
---

# Phase75 — Security

## Trust Boundaries

| Boundary | Description | Data crossing |
|---|---|---|
| Caller → disposable child | Explicit environment and vetted export, not caller credentials or mutable state | Source/public resources and owned fixture paths |
| Raw diagnostic → publication | Known/audit redaction and unchanged secret classification must precede every lossy window | Potential credentials and scanner-only token forms |
| Alias/source membership → export | Aliases and resolved targets require the entire public source-set predicate | Private state, learned skills, optional resources |
| Grok host → runtime | Real authentication, targeted approvals, interceptor audit and lifecycle | Tool invocations and owned effects |
| Documentation → operator | Claims must match exercised controls and remaining live prerequisites | Readiness and security claims |

## Threat Register

| Threat ID | Category | Component | Severity | Disposition | Independent evidence | Status |
|---|---|---|---|---|---|---|
| T-75-01-01 | Tampering | IntakeStore._save whole-file replace | high | mitigate | status=closed. Stable sidecar flock and thread lock enclose reload-mutate-atomic-save for every mutation; readers reload the persisted file. Cooperating POSIX writers retain concurrent commits, without any lease-fencing claim. | closed |
| T-75-01-02 | Tampering | Corrupt intake.json | medium | mitigate | status=closed. Only a nonexistent file initializes empty; malformed JSON or a non-list orders member raises instead of erasing persisted orders. | closed |
| T-75-01-03 | Repudiation | Claim/ack attribution | low | mitigate | status=closed against the authored mitigation, not a claim of authenticated attribution or stale-lease fencing. Existing by/claimed_at semantics persist, and code/docs explicitly disclose ordered ack merge without an expected-claim condition. | closed |
| T-75-01-SC | Tampering | package installs | high | mitigate | status=closed. The repair reuses existing stdlib locking/atomic-write utilities and adds no package-install operation. | closed |
| T-75-02-01 | Information Disclosure | diag_bundle outputs | high | mitigate | status=closed. Entire canonical serialized bundle is scanned before output directory/file creation or atomic replacement; token-shaped metadata does not get a path exemption. | closed |
| T-75-02-02 | Information Disclosure | str-vs-PathLike dispatch | medium | mitigate | status=closed. str inputs are scanned as data without filesystem reads; os.PathLike inputs enforce exact 0600 and scan bytes without classifying filename spelling. | closed |
| T-75-02-03 | Information Disclosure | audit/access logs, manifest URLs, exception labels | medium | mitigate | status=closed within this plan's narrow boundary. Audit sanitizer remains intentionally narrower than the diagnostic guard, and whole diagnostic serialization still reaches the stronger scanner without a whitelist/threshold change. This does not close the separate lossy selftest/readiness publication threats. | closed |
| T-75-02-SC | Tampering | package installs | high | mitigate | status=closed. This plan changes only existing test fixtures and uses existing production scanner/collector dependencies. | closed |
| T-75-03-01 | Information Disclosure | child env inheritance | high | mitigate | status=closed. Selftest builds env from layout.env rather than inheriting provider credentials/config selectors; caller globals remain untouched. | closed |
| T-75-03-02 | Tampering | source schedule/state writes | high | mitigate | status=closed for direct schedule/state writes. Host cwd/root/work/home/audit/token paths resolve to the disposable layout, and source schedule bytes remain untouched. Export confidentiality is independently closed under T-75-04-04. | closed |
| T-75-03-03 | Information Disclosure | token/log redaction | medium | mitigate | status=closed for preserved mint-without-print and known-token sentinel handling. Generated caller/admin tokens stay in private files/store; full known-secret replacement precedes clipping. Scanner-only publication closure is recorded under T-75-03-04. | closed |
| T-75-03-04 | Information Disclosure | _log_tail clip-then-redact sink | high | mitigate | status=closed by independent Native75UsageSecurityFinal. oneclick.py:634-660,723-734,820-850,859-879 (re-anchored 2026-10-12: later stdio-framing commit 1f0adf6 shifted the original 629-655,718-729,815-845,854-874 by +5 lines; guarded bodies unchanged) and verify.py:141-262,339-375,947-951,1049-1058 protect full diagnostic input, final report/metadata and parser stderr before lossy publication. Actual verifier and launcher before/after receipts retain failure/exit2, remove classified fragments/candidate echo and preserve clean usage guidance. No scanner relaxation or arbitrary-secret promise. | closed |
| T-75-03-SC | Tampering | package installs | high | mitigate | status=closed. Selftest uses existing interpreter, stdlib, and installed verifier/SDK modules; no package-manager installation is part of this plan. | closed |
| T-75-04-01 | Information Disclosure | child env construction | high | mitigate | status=closed. Fresh owned HOME/XDG/TMP/work/state/native roots and a small explicit interpreter/locale/executable environment replace caller inheritance; sentinel coverage includes provider credentials and endpoint/root selectors. | closed |
| T-75-04-02 | Tampering | fresh-child controller | high | mitigate | status=closed. Controller imports only stdlib/shared safe helpers before spawning one exported product child; caller environment is not rewritten, deadline kills the child process group, and layout cleanup encloses export/yield failures. | closed |
| T-75-04-03 | Information Disclosure | report publication | high | mitigate | status=closed by independent Native75UsageSecurityFinal. Complete Prime/refusal/exception and final serialized-report boundaries remain protected. readiness.py:54-87,117-138,203-252 also guards complete parser/missing-root/roster diagnostic text before stderr using unchanged assert_no_secrets, catches SecretLeakError only and preserves exit2/private output refusal. Actual usage before/after receipts remove recognized candidate echo; current normal165-tool profiles pass and negative scenarios still exit1/okfalse. | closed |
| T-75-04-04 | Information Disclosure | export containment | high | mitigate | status=closed by independent Native75SecurityFinal. Complete admission covers ordinary entries, aliases and targets, with pruned shape and verified provenance for protected resources. Current regressions omit unsafe ordinary credential/excluded-ancestor Python and inventory rows while preserving fresh safe product code; both owned profiles pass and source/session/native preservation was checked. | closed |
| T-75-04-05 | Information Disclosure | loopback audit | medium | mitigate | status=closed. Each run mints local scoped tokens and inspects the actual on-disk audit file after host lifecycle, failing if either token occurs. | closed |
| T-75-04-06 | Spoofing | roster accounting | medium | mitigate | status=closed. Canonical roster and runtime offered names are compared separately; only supported evidence/verified disabled flags/named blocks account rows, while unexplained absences/extras/gaps fail global readiness. | closed |
| T-75-04-SC | Tampering | interpreter packages | high | mitigate | status=closed. Controller/fixture launches reuse sys.executable and existing modules; no package-manager install occurs in the readiness scenarios. | closed |
| T-75-05-01 | Elevation of Privilege | run_terminal/execute_code argv | high | mitigate | status=closed. run_terminal and execute_code receive fixed current-interpreter argv/snippets rooted in owned WORK, with no shell or arbitrary model code; evidence labels the result local-subprocess-proof, not an OS sandbox. | closed |
| T-75-05-02 | Tampering | command-log receipt | high | mitigate | status=closed. Positive receipt derives from reopened command-log rows and validates matching execution; the explicit never-ran receipt is rejected. | closed |
| T-75-05-03 | Spoofing | mcp_call fixture | medium | mitigate | status=closed. Fixture uses installed official MCPServer over the canonical initialized SDK client and verifies reopened HOME-tree file bytes/results; error/timeout outcomes are not counted as positive proof. | closed |
| T-75-05-04 | Information Disclosure | unavailable external clients | medium | mitigate | status=closed. Missing browser/DAP/device/service/provider prerequisites are classified blocked, never success; PATH checks do not execute device tooling and no default external transport is created. | closed |
| T-75-05-SC | Tampering | interpreter packages | high | mitigate | status=closed. Fixed subprocess/fixture operations reuse current interpreter, existing SDK, and local Git fixtures; no installation is performed. | closed |
| T-75-06-01 | Spoofing | doctor caller context | high | mitigate | status=closed. Prepared disposable seat supplies only actual installed prompt digest and installed skill names; missing/invalid/incomplete context stays non-green and is not invented from model/environment. | closed |
| T-75-06-02 | Tampering | warm intake claim/ack | medium | mitigate | status=closed. Existing runtime precedes independent producer, without rebuild; real dispatch effect/public ack and fresh observer confirm persisted done fields. | closed |
| T-75-06-03 | Spoofing | ScriptedModel composition | medium | mitigate | status=closed. Existing model seam is replaced while real parent/registry/intake/file/receipt effects remain; evidence is labeled deterministic-model-double and validates effects independently of final text. | closed |
| T-75-06-04 | Denial of Service | disposable cron journal | medium | mitigate | status=closed. Cron jobs use owned INSTALL/cron/jobs.json and explicit intake path; create/list/remove checks verify cleanup; Python workflow is confined to disposable code/work paths. | closed |
| T-75-06-SC | Tampering | interpreter packages | high | mitigate | status=closed. Slash/cron/desk/learning/durable probes use installed production modules and the existing model seam, with no package installation. | closed |
| T-75-07-01 | Spoofing | Prime flag-off truth | high | mitigate | status=closed. Genuine OFF runtime verifies every family flag false, absence of all gated names and direct-call refusal before owned fixture flags enable the main ON runtime; production defaults stay off. | closed |
| T-75-07-02 | Elevation of Privilege | heartbeat runner binding | medium | mitigate | status=closed. Genuine callable binding receives the topical prompt; list/clear plus stop(wait=True)/close clean up bindings, and scheduling alone is not positive delivery evidence. | closed |
| T-75-07-03 | Spoofing | autonomous turn claims | medium | mitigate | status=closed. Real controller receives two after_turn payloads with asserted continuation and max_turns termination; mere started/running status is not execution proof. | closed |
| T-75-07-04 | Spoofing | factory/child execution | high | mitigate | status=closed. None/unconfigured child runner and missing genuine factory run/spec identifiers remain named per-verb blocks; desk model and local kernel success do not supply child-runner evidence. | closed |
| T-75-07-05 | Tampering | native kernel execution | medium | mitigate | status=closed. Native cells/crate probes require genuine loaded pinned artifact and validate observable results; missing artifact/checkout is a named per-capability block. No upstream rebuild is part of the scenario. | closed |
| T-75-07-SC | Tampering | interpreter packages | high | mitigate | status=closed. Existing production modules/SDK/native artifact are reused; no packages installed and no native rebuild is performed. | closed |
| T-75-08-01 | Spoofing | Local/live readiness claims | high | mitigate | status=closed by independent Native75UsageSecurityFinal. Current165-row accounting, default-off Red Team, full-input report/verifier/launcher/controller diagnostic controls and detector limitations match source plus actual receipts. Current2129-test union, both maintained profiles and realhost22/shutdown0 are distinguished from unproven live Grok/provider/device/browser/mission prerequisites. No waiver, usage-error exclusion or accepted risk. | closed |
| T-75-08-02 | Information Disclosure | Documentation examples and evidence | medium | mitigate | status=closed. Reviewed operator docs/changelog use synthetic identifiers and relative/placeholder paths, with no raw credentials, caller environment dump, sensitive payloads or private host paths. | closed |
| T-75-09-01 | Spoofing | doctor_context validation | high | mitigate | status=closed. Shared validator allows only prompt_sha256 string and installed_skills list[str]; unknown keys or mistyped values become missing, never healthy or fatal. Context is passed only into check arguments. | closed |
| T-75-09-02 | Tampering | run_workflow ok aggregation | high | mitigate | status=closed. Any step error, green=False, ok=False, or nonzero/invalid exit code fails that step and overall workflow; non-stop workflows retain all step results without relabeling failed work. | closed |
| T-75-09-03 | Information Disclosure | installed-state sourcing | medium | mitigate | status=closed. Direct and workflow command paths consume only explicit validated caller context; missing installed state stays unhealthy. Readiness context is verified against disposable installed prompt/skill files, not a live seat. | closed |
| T-75-09-SC | Tampering | package installs | high | mitigate | status=closed. Repair uses existing Mapping/typing/JSON and production dispatch functions; no new package/install operation. | closed |
| T-75-10-01 | Elevation of Privilege | mcp_session_call argv | high | mitigate | status=closed for the authored no-string/no-shell mitigation. Canonical client rejects string/empty/non-string argv before spawning and passes command[0]/args to official SDK. This does not claim arbitrary caller argv is an OS sandbox. | closed |
| T-75-10-02 | Tampering | child cwd confinement | medium | mitigate | status=closed within named cwd validation. Client refuses non-directory cwd; platform binds the public handler cwd to supplied home. Tests/production-readiness fixture use owned HOME-tree effects, not a universal filesystem sandbox. | closed |
| T-75-10-03 | Denial of Service | child timeout/reap | medium | mitigate | status=closed. No-loop uses asyncio.run; active-loop uses a joined non-daemon owning-loop thread. asyncio.wait_for bounds the SDK session and existing cancellation-shielded stdio cleanup reaps children; no early-return/detach path was added. | closed |
| T-75-10-04 | Spoofing | obsolete client removal | medium | mitigate | status=closed. tools/mcp_client.py is intentionally absent; platform imports canonical mcp_session_call under its unchanged roster binding, with no shim/re-export or surviving obsolete-client reference observed. | closed |
| T-75-10-05 | Information Disclosure | SDK child stderr and env | high | mitigate | status=closed. Official SDK default restricted passthrough plus owned temp/XDG/no-bytecode additions avoids provider inheritance; real fileno-bearing devnull stderr and bounded non-propagating discarded SDK logs contain peer bytes. No automatic model/credential callbacks were introduced. | closed |
| T-75-10-SC | Tampering | interpreter packages | high | mitigate | status=closed. No new dependency/installation; official SDK is already available in the supplied runtime and tests replace obsolete hand-framed fixtures with real MCPServer. | closed |
| T-75-11-01 | Spoofing | lint issue evidence | medium | mitigate | status=closed. Boundary-aware issue start-tag matching excludes <issues> and preserves existing fragment API. Real valid-XML regressions cover empty/one/two and metadata-only container changes. | closed |
| T-75-11-SC | Tampering | dependencies | high | mitigate | status=closed. Existing re module and current interpreter implement exact counts; no new package or installation. | closed |

## Reproduced blocking roots and closures

- Existing self-test log-tail, readiness/Prime diagnostic and complete source-admission repairs remain independently verified within their authored boundaries.
- Repaired verifier `_step`/probe full-input classification, final human/JSON publication and verifier argparse stderr controls are proven; prior synthetic before/after receipts remain retained.
- Historical launcher usage route: plain argparse choice/type/unknown rejection preceded the guarded self-test branch. Parent actual three-case before-proof echoed recognized synthetic candidates on exit2; repaired `_GuardedParser` after-proof retains exit2 with no echo and fixed safe metadata.
- Historical readiness usage routes: unknown-argument parser and missing-root diagnostics echoed recognized synthetic candidates. Current full-input usage guard protects both paths before stderr; actual after-proof retains exit2/no echo while benign guidance remains helpful.

Current independent result: Native75UsageSecurityFinal closes the three remaining authored IDs and retains47 prior independent closures:50 closed/0 open, ASVS L1/block high. Parent current2129-test union, both165-tool normal profiles, realhost22/shutdown0, usage before/after proofs and frozen scanner/security/audit/manifest/interceptor/DB/native preservation are retained in `local://v15-native-phase75-usage-verification.json`. Earlier failed and intermediate receipts/audit blocks remain historical.

## Accepted Risks Log

No accepted risks. No signer bypass, signed Swarm evidence, remote delivery or live Grok/provider attestation.

## Security Audit Trail

| Audit date | Authored threats | Closed | Open | Run by |
|---|---|---|---|---|
| 2026-10-10 | 50 | 46 | 4 | Independent Native75Security, native GSD equivalent; parent owned fixture reproduction |
| 2026-10-10 | 50 | 47 | 3 | Independent Native75SecurityClosure; parent real owned residual-route reproduction |

## Sign-Off

- [x] All 50 plan-authored threats have a disposition.
- [x] Independent verification was scoped to the authored register, ASVS L1, block high.
- [x] Blocking threats closed by actual repaired controls.
- [x] `threats_open: 0` confirmed.
- [x] `status: verified` set.

Approval: independently SECURED50/50 at ASVS L1; no blocking threat remains in the authored register. Parent current union and consumer smokes are green; canonical phase verification remains required before transition. No accepted risk, signed-Swarm attestation, live integration claim or phase advancement is implied by this security closure.

## Security Audit 2026-10-10

| Metric | Count |
|---|---|
| Threats found | 50 |
| Closed | 48 |
| Open | 2 |

## Security Audit 2026-10-10

| Metric | Count |
|---|---|
| Threats found | 50 |
| Closed | 47 |
| Open | 3 |

## Security Audit 2026-10-10

| Metric | Count |
|---|---|
| Threats found | 50 |
| Closed | 50 |
| Open | 0 |

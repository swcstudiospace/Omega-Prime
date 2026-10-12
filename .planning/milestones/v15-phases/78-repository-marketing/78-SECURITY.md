---
phase: "78"
slug: "repository-marketing"
status: verified
# threats_open = count of OPEN threats at or above workflow.security_block_on severity (the blocking gate)
threats_open: 0
threats_total: 6
asvs_level: 1
register_authored_at_plan_time: true
created: "2026-10-12"
---

# Phase 78 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| author→reader | Marketing prose crosses to prospective users who may act on capability claims | public capability claims |
| docs→secret-scan | Demo copy crosses the repository no-secrets rule over the docs scope | public prose; no credential values |
| svg→browser | Asset XML crosses into renderers on GitHub, GitBook and social scrapers | public brand art |
| asset→CI | New binary and SVG cross the hermetic-test and docs-linked gates | public brand art |

---

## Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation | Status |
|-----------|----------|-----------|----------|-------------|------------|--------|
| T-78-01 | Spoofing | docs/marketing.md capability claims | high | mitigate | Every use case carries an evidence-level tag (`docs/marketing.md:51,62,74,85` local proof; `:98,110` optional capability; `:120,129` unproven external); the Bot route is labelled UNPROVEN with UAT-anchor links (`:121-122,235-236,357-358`); Claim limits (`:352-368`) restate that no live account, model or provider is attached. Cited sources and exit-code contracts were checked against the code (`setup_check.py:232`, `catalog.py:183-191`, `plugin.py:949-974`, `readiness.py:37-39,267`, `grokbot-1click.sh:38-41,78,388-389`), and the demo ran green in a fresh clone (`/tmp/v15-78-demo-1791836909/logs/`) | closed |
| T-78-02 | Information Disclosure | docs/marketing.md demo narrative | high | mitigate | No token example or placeholder value; the only "token" line is the quoted instruction not to paste secrets in chat (`docs/marketing.md:258`); the secret-handling claim cites the policy without values (`:367-368` → `docs/setup.md:75`). Per-line `assert_no_secrets` hits are only the prose phrases "bearer host"/"bearer auth" (`:41,87,220,277`), the detector false positive recorded in 77.1-SECURITY.md N-02 | closed |
| T-78-03 | Tampering | docs/SUMMARY.md navigation entry | low | accept | Single line `- [Marketing](marketing.md)` (`docs/SUMMARY.md:23`); the exactly-once link test passes | closed |
| T-78-04 | Tampering | assets/social-card.svg markup | high | mitigate | Both SVGs contain no script, href, xlink, image, use, foreignObject or event attribute, and no scheme beyond the namespace declaration; `omega_prime/tests/test_public_repo.py:22-37` guards all three SVGs and checks titles; an injected `<script>` fails the test (78-02-SUMMARY) | closed |
| T-78-05 | Spoofing | social-card factual line and PNG provenance | medium | mitigate | One allowlisted line (`assets/social-card.svg:47`, source `docs/local-validation.md:3-4`); schemeless path (`:48`); the exact generating command is in the header comment (`:1`); the committed PNG is pixel-identical to a fresh render with that command | closed |
| T-78-06 | Denial of Service | assets/social-card.png size | low | accept | 1280x640 RGBA, one frame, 138917 bytes, no ancillary text chunks; the SVG has no external reference | closed |

*Status: open · closed · open — below high threshold (non-blocking)*
*Severity: critical > high > medium > low — only open threats at or above workflow.security_block_on count toward threats_open*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-01 | T-78-03 | A one-line navigation entry in the established format; tampering would surface in review and in the exactly-once link test | plan-time decision (78-01-PLAN threat model) | 2026-10-12 |
| AR-02 | T-78-06 | A fixed single-frame 1280x640 PNG with no external fetch or animation; its size is asserted at generation | plan-time decision (78-02-PLAN threat model) | 2026-10-12 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-10-12 | 6 | 6 | 0 | gsd-security-auditor (Secure78) + secure-phase orchestrator |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-10-12

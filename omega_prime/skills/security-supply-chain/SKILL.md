---
name: security-supply-chain
description: Assessing dependencies before adding them and responding to vulnerabilities. Use before adding any dependency and when a CVE affects the project.
bots: [bot-00-omega-prime]
---
<skill name="security-supply-chain" version="1">
# Supply Chain

## L1 — Summary

**Every dependency is code you ship without reviewing, running with your privileges.** Most are
worth it. The assessment takes two minutes and prevents the category of incident that is very hard
to recover from.

**Decision tree:**

```
Adding a dependency?
├─▶ Do you actually need it? §1 — a 12-line utility is not worth a supply-chain edge
│
├─▶ Assess it. §2 — provenance, maintenance, transitive weight, licence
│
└─▶ Pin and lock it. §3 — commit the lockfile
```

```
CVE reported against something you use?
└─▶ §4. Is the vulnerable path actually reachable from your code?
        Severity score ≠ your exposure.
```

**Typosquatting is the live threat.** `reqeusts`, `lodahs`, `python-dateutils` — all real. Check
the name character by character before installing, especially on a name you typed from memory.

---

## L2 — Method

### §1 Do you need it?

The left-pad question. Weigh against writing it yourself:

| Add it when | Write it yourself when |
|---|---|
| Non-trivial and well-solved — crypto, parsing, HTTP | It is a few lines with no edge cases |
| Actively maintained with a real user base | The library is unmaintained |
| Security-sensitive and you are not an expert | It pulls 40 transitive dependencies for one function |
| It saves substantial work | You need 5% of its surface |

**Never hand-roll cryptography.** That is the clearest case for a dependency, and the one where
writing it yourself is most tempting and most wrong.

### §2 Assessment

Before adding, check:

| Check | Red flag |
|---|---|
| **Name** | Off-by-one from a popular package. Typosquatting is common and effective |
| **Publisher** | Recently transferred ownership; unknown maintainer on a popular name |
| **Maintenance** | No commits in 2+ years; open security issues unaddressed |
| **Adoption** | Very low downloads for something that claims to be general-purpose |
| **Transitive weight** | One function pulling in dozens of packages |
| **Install scripts** | `postinstall` hooks — arbitrary code at install time |
| **Licence** | GPL/AGPL in a proprietary codebase; no licence at all |
| **Known CVEs** | Existing unpatched advisories |

```bash
cargo audit && cargo tree --duplicates
uv pip list --outdated && pip-audit
deno info <module>
npm audit && npm ls <package>
./gradlew dependencies
```

**Licence matters commercially.** AGPL in a hosted product has consequences well beyond
engineering. No licence at all means no rights granted — not "public domain".

### §3 Pinning

- **Commit the lockfile.** `Cargo.lock`, `uv.lock`, `deno.lock`, `package-lock.json`,
  `Package.resolved`, `gradle.lockfile`. Without it the build is not reproducible and a
  compromised patch release arrives silently.
- Exact versions for applications. Ranges are acceptable for libraries.
- Deno: pin remote URLs by version — a bare URL can change under you.
- Update deliberately, on a schedule, reading the changelog. Not automatically on every build.
- Automated update PRs are good; auto-merging them is not. A compromised patch release is
  precisely the thing auto-merge ships fastest.

### §4 Vulnerability response

**A CVE score is not your exposure.** Ask, in order:

1. **Is the vulnerable code path reachable from your code?** A deserialisation CVE in a library
   you only use for formatting may not be.
2. **Is it exploitable in your configuration?** Many require a specific option enabled.
3. **Is the input attacker-controlled?** A parser CVE matters if you parse user input; much less
   if you parse your own config at boot.

Then:

```
Reachable + exploitable + attacker-controlled input  → patch now, out of band
Reachable, not currently exploitable                 → patch in the normal cycle
Not reachable                                        → patch in the normal cycle, document why not urgent
No patch available                                   → assess workarounds; consider vendoring or replacing
```

**Document the reasoning either way.** "We assessed this and it is not reachable because X" is a
defensible position; ignoring it silently is not, and the next person to see the alert re-does the
work.

### §5 Ongoing

- Automated scanning in CI: `cargo audit`, `pip-audit`, `npm audit`, Dependabot or equivalent.
- SBOM generation for anything shipped to customers.
- Review transitive additions in dependency-update PRs — a patch bump can add new packages, and
  that is where the interesting changes hide.
- Watch for ownership transfers on packages you depend on. The classic attack is acquiring a
  popular, unmaintained package and publishing a malicious version.
- Prefer fewer, better-maintained dependencies over many small ones. Each one is an edge.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Typosquatted package | Malicious code running with your privileges | Read the name character by character |
| No lockfile | Non-reproducible builds; silent compromised updates | Commit it |
| Auto-merged updates | Compromised release shipped fastest | Review, do not auto-merge |
| CVE score as exposure | Panic on unreachable issues, complacency on reachable ones | §4 — assess reachability |
| Ignored advisory | Repeated triage by every person who sees it | Document the reasoning |
| Licence unchecked | Legal exposure discovered late | Check before adding |
| Transitive bloat | Large attack surface for one function | Weigh the transitive cost |
| `postinstall` unexamined | Arbitrary code at install time | Check install scripts |
</skill>

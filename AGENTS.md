# Agent guidance

This file is the long form of [.cursor/rules/gotxcot-cloud-agent.mdc](.cursor/rules/gotxcot-cloud-agent.mdc). Cursor cloud agents load that rule with `alwaysApply: true`. Other agent runtimes read this file. Keep the rule as a short summary and put procedure here so the two stay aligned.

The lead-side double-uplift pipeline is defined in [gotxcot-uplift](omega_prime/skills/gotxcot-uplift/SKILL.md) and [trackplan-dispatch](omega_prime/skills/trackplan-dispatch/SKILL.md). A cloud agent executes the plan it was given. It does not re-run that pipeline, and it does not create Linear or Notion rows.

## Detection test

Treat the prompt as a second-uplift spec when all of these are true:

1. The prompt contains one XML root.
2. That root is `BUILD_PROMPT`, `FIX_PROMPT`, `RESEARCH_PROMPT`, `CHANGE_PROMPT`, or `UPLIFTED_PROMPT`.
3. The root has an `ISSUES` child.
4. `ISSUES` contains `TASK`, `ISSUE`, and `SUBISSUE` elements.

A prompt with several XML roots fails the test. A prompt whose `ISSUES` block lacks any of those three element types fails the test. Use the [fallback plan](#fallback-plan) for both.

`TRACKER_GAPS`, missing URLs, or a partial identifier list do not fail the test. Follow the plan and record the gaps in the PR body.

## Follow the uplift

When the detection test passes, execute the graph that is already in the prompt:

1. Read scope, out of scope, constraints, acceptance criteria, clarifications, and tracker gaps before editing.
2. Execute `ISSUE` nodes in topological order. `dependsOn` is the order. A node with an empty `dependsOn` may start first.
3. Execute each node's numbered `SUBISSUE` steps in order. The steps are the plan. Do not replace them with a new graph.
4. Keep tracker identifiers out of committed guidance files. They belong in commit trailers and the PR body.

## Fallback plan

When the detection test fails, write a lightweight plan before the first edit. Put it in the PR body under Plan. Do not create tracker rows, issues, or pages.

The plan has 5 to 8 graph nodes. The first node is `understand`. The last node is `synthesize`. At least one node is `critique`. Every node has 4 to 8 numbered steps.

A trivial change (a typo or a one-line fix) still gets a plan, so the contract stays the same: 5 nodes and 4 short steps each.

```markdown
## Plan

### n1 understand
1. ...
2. ...
3. ...
4. ...

### n2 <task-specific>
1. ...
2. ...
3. ...
4. ...

### n3 <task-specific>
1. ...
2. ...
3. ...
4. ...

### n4 critique
1. ...
2. ...
3. ...
4. ...

### n5 synthesize
1. ...
2. ...
3. ...
4. ...
```

Add nodes between the first and the critique when the task needs them, up to 8 nodes total. The last node remains `synthesize`. Each added node still has 4 to 8 steps.

## Commit convention

Use a subject line, a blank line, and trailers. Fill every identifier from the prompt. Do not invent one.

```text
Add the cloud-agent planning rule

Refs: <linearId>
Node: <nodeId> step <n>
```

Repeat `Refs` once per Linear identifier that commit implements. Map the commit to the node and step it completes. Notion URLs go in the PR body, not in the commit.

When the prompt has no Linear identifiers, do not invent any. Use this trailer instead:

```text
Refs: no tracker IDs provided
```

## Pull request body

Open a draft pull request. Use these sections:

```markdown
## Plan

- [x] n1 step 1 — ...
- [ ] n1 step 2 — ...

## Trackers

Task, Issue, and Sub-Issue identifiers and URLs copied from the prompt.
When the prompt has none, this section is only:

no tracker IDs provided

When some identifiers or URLs are missing, cite the ones that exist and list the gaps here.

## Receipt

.receipts/<bot-id>/<task-id>.json

## Unverified

- rule adherence by future agents
```

The Plan section is the node and step checklist. For a second uplift, check off the prompt's nodes and steps. For a fallback plan, check off the plan written above.

## Receipts

Before any completion claim, write a verification receipt. The format is [verification-receipts](omega_prime/skills/verification-receipts/SKILL.md).

Path: `.receipts/<bot-id>/<task-id>.json`

The bot id is the one the prompt names. When the prompt names none, use the owner in [omega_prime/ownership.yaml](omega_prime/ownership.yaml). The only bot in that file is `bot-00-omega-prime`, so the default path is `.receipts/bot-00-omega-prime/<task-id>.json`.

The receipt records the commands that ran, their exit codes, the claims those commands prove, and an `unverified` list. A claim cites a command that could have failed if the claim were false. `unverified` is mandatory. Include this entry on every receipt written under this guidance:

```text
rule adherence by future agents
```

No test in this repository can prove that a later cloud agent will follow these rules. Name that limit instead of claiming it was verified.

These commands are the ones that show whether a docs or generator change is still current. Run the ones that apply to the edit, and record each exit code:

```bash
python3 -m omega_prime.tooling.catalog --check
python3 -m omega_prime.tooling.plugin --check
python3 -m omega_prime.tooling.prompt_format --check
bash omega_prime/scripts/assemble-prompts.sh --check
python3 -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q
```

`test_docs.py` scans every root `*.md` file for relative links, heading anchors, and documented shell commands. A command in a `bash` fence on this page has to name a real `omega_prime` module or script. `docs/` pages also need a top-level heading. This file is a root page, so its links and fenced commands are checked.

## Ownership and safety

Resolve every path you will create or edit against [omega_prime/ownership.yaml](omega_prime/ownership.yaml) before the first edit. The file is version 1. The last matching pattern wins. The broad pattern `**` is owned by `bot-00-omega-prime`, and the more specific patterns in that file are owned by the same bot. Ownership for this repository lives in that file, not in a root `ownership.yaml`.

Stay on a feature branch. Open a draft pull request. Do not merge, enable auto-merge, or force-push. Do not print or commit secrets, tokens, or private URLs.

Do not hardcode personal or workspace identifiers into repository guidance: no Notion collection ids, no Linear team, project, or issue identifiers, no VPS address, no operator name, and no Railway or Tailscale host. Identifiers that arrive in the prompt are copied into commit trailers and the PR body for that run. They are not copied into this file or into the always-apply rule.

If a generator check or a docs test already fails on the base branch for a reason outside the change, record it as pre-existing and include the base-branch command and exit code. Leave unrelated code unchanged.

## Precedence

An explicit instruction in the prompt overrides this file on scope. It does not override ownership, the no-merge rule, the no-secrets rule, or the requirement to write a receipt before a completion claim.

## Failure modes

- Missing or partial trackers: continue the plan, cite the identifiers that exist, and list the gaps under Trackers. Do not create or invent tracker rows.
- Conflicting instructions: apply [precedence](#precedence).
- Over-planning: shrink a trivial plan to 5 nodes and 4 short steps. Do not skip the plan.
- Unprovable obedience: the receipt lists `rule adherence by future agents` as unverified.

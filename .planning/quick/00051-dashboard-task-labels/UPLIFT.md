# 00051 - Dashboard task labelling (GoTxCoT uplift)

<uplift>
  <ORIGINAL>Now our Dashboard is still creating our tasks incorrectly. We're getting omp processes instead of Title and Description. Now i don't know where these are spawning from and i am not saying these aren't real tasks doing real work i am just saying that you need to label them appropriately in our Dashboard because i am looking at Titles to understand what work is being done. So review the codebase. Isolate the issues, fix it</ORIGINAL>
  <goal>Every Command Center row backed by a live omp agent reads "000NN - Title" with a human description; daemons and helper workers never appear as tasks.</goal>
  <got>
    <node id="G1">Root cause: model.normalize_tasks turns each unclaimed omp pid into Task("proc:{pid}", "omp process {pid}", detail "mode json").</node>
    <node id="G2">Why unclaimed: agentos runs omp with --no-title and --session-dir under agentos/state/omp, so the omp_sessions collector (~/.omp/agent/sessions) never sees them and no session row claims the cwd.</node>
    <node id="G3">Duplicates: omp spawns helper workers (__omp_worker_lsp_mux, comm "omp") in the same cwd; each became its own row.</node>
    <node id="G4">Identity source: the omp prompt header ("You are the GSD executor ... milestone 00046 \"Event Stream\", phase 105, plan 105-04"), --session-dir run id (stage), --model, and the cwd's 000NN.</node>
    <node id="G5">Fallback: sessions without a title event used the JSONL filename as the title.</node>
    <node id="G6">Daemons: auth-broker / openshell-gateway / herdr server were already separated but only in source extra; surface them as an infrastructure list, generalise omp daemon subcommands, keep dead-pid drops.</node>
    <node id="G7">Contract: titles follow taskCard.ts NUMBERED_PREFIX; writes still only via status_file.write_tasks (model stays pure, collectors do the reading).</node>
  </got>
  <cot>
    <step>Collector extracts only short regex-captured fields from argv (never the full prompt, never environ).</step>
    <step>Pure labels module composes title + description from those fields plus 000NN titles known from tasks.json.</step>
    <step>Model groups agent pids by cwd: one row per run, helpers folded into the description, stable id omp-run:&lt;run id&gt; when known.</step>
    <step>Behaviour tests: human title/description, helper folding, daemon classification, session filename fallback gone.</step>
  </cot>
  <tracking>Linear/Notion kickoff not run by this executor (externally visible; left to the orchestrator).</tracking>
</uplift>

# The omp-sandbox engine

The engine backend that runs GSD stages through the omp-sandbox Anda wrapper
instead of bubblewrap. It is selected per run and reached unchanged from both
call paths: the loop's own routine ([ultrathink_turn](agent-loop.md)) and the
delegate path. Everything upstream — prompts, stage routing, plan gating —
behaves exactly as on the bwrap path; only the sandbox tier changes. See
[architecture.md](architecture.md) for where the engine sits in the loop.

## Selecting the backend

`OMEGA_PRIME_OMP_BACKEND` selects this backend only when its value is exactly
`omp-sandbox`. Unset, or any other value, keeps the bwrap path byte-identical —
an explicit choice of a different sandbox is not an escape hatch either.
Selection never falls back to bwrap if the backend fails: a failed stage is a
failed run, not a silent provider switch.

Before any run, the engine probe reports the backend's own readiness: the omp
binary still resolves (it backs the usage read), and the wrapper binary is
resolvable either explicitly or from `PATH`. bwrap readiness never gates the
backend.

## Environment reference

Every name below is read by the shipped wiring. Semantics only — the consent
and API-key entries are presence/truthiness facts, never values.

| Name | Read by | Semantics |
| --- | --- | --- |
| `OMEGA_PRIME_ENGINE` | `attach.engine_mode` | Engine mode gate: `off` disables the engine path; `required` turns a readiness miss into a hard failure; anything else is `auto` (fall back to the Python loop). |
| `OMEGA_PRIME_ENGINE_STAGE` | `attach.engine_stage` | Stage fallback for one engine run when neither the caller nor a saved plan names one; blank means no stage, any other value is passed through so the router refuses it with a clear reason. |
| `OMEGA_PRIME_OMP_BACKEND` | `sandbox_omp.selected` | Selects the omp-sandbox backend only when the value is exactly `omp-sandbox`; otherwise the bwrap path is unchanged. |
| `OMEGA_PRIME_OMP_SANDBOX_ANDA` | `sandbox_anda` binary resolution | Explicit path to the `omp-sandbox-anda` wrapper binary; when unset, `omp-sandbox-anda` is resolved from `PATH`. Neither resolving means the backend refuses before spawning. |
| `OMP_SANDBOX_WORKSPACE_ROOTS` | `sandbox_omp.workspace_roots` | Colon-split allowlist of roots the job worktree may live under. Default: `/workspace/omes-bot-wt` (the same default as the wrapper's own `paths.ts`). Admission refuses anything else with `path_refused` before any spawn. |
| `OMEGA_PRIME_OMP_ALLOW_ZAI` | `plan_router` Z.ai gate | Truthy value opts the Z.ai plan into the allowlist. Absent or not truthy means Z.ai stages refuse with `plan_not_allowed`; a Z.ai API key alone never allows it. |
| `ZAI_API_KEY` | provider credentials | Presence-only credential fact. It authenticates the Z.ai provider; it never widens the plan allowlist — only the consent flag above does that. |
| `OMEGA_PRIME_TASKS_PATH` | `ultrathink_turn`, `attach` | Path of the tasks.json store; together with the task id it switches on the run's visibility wiring (pane, row, question round-trip). |
| `OMEGA_PRIME_TASK_ID` | `ultrathink_turn`, `attach` | The task row the run reports against; both this and the tasks path must be set for visibility to switch on. |
| `OMEGA_PRIME_ENGINE_TMUX` | `jobs`/engine launch | Base tmux gate for engine jobs; the value `0` keeps engine runs off tmux (the hermetic test base). |
| `OMEGA_PRIME_STATE_DIR` | `jobs.state_dir` | State root for per-job artifacts: `<state>/jobs/<job>/session.json` and `events.jsonl`, and the routine's `ultrathink/last-plan.json` probe. Defaults to `~/.omega-prime`. |
| `last-plan.json` `stage` field | `ultrathink_turn.resolve_turn_plan` | A saved plan may name its engine stage (`plan` or `execute`); the routine's stage precedence is explicit stage arg, then this field, then `OMEGA_PRIME_ENGINE_STAGE`. |

Per-stage model overrides also exist: `OMEGA_PRIME_OMP_MODEL_<PLAN>_<STAGE>`
(e.g. the Z.ai plan stage) override the stage defaults below.

## Stage routing

| Stage | ChatGPT model | Z.ai model |
| --- | --- | --- |
| `plan` | `gpt-6.1-sol` | `glm-5.3` |
| `execute` | `gpt-6-luna` | `glm-5.3-flash` |

A staged run tries ChatGPT first, then Z.ai — Z.ai covers ChatGPT when the
ChatGPT plan is gated off or its quota window is exhausted. A plan is
requested per run (the routine's stage via `last-plan.json`, or `engine_plan`
on the delegate/turn paths); it is never a default change to the engine. Z.ai
serves a stage only when the consent flag above is truthy. A requested stage
with no allowlisted plan refuses with `plan_not_allowed` before any wrapper
spawn. When that refusal happens inside `run()` with task context present
(the delegate and turn paths, which call `run()` directly), it raises exactly
one `waiting_on_ove` item in the same failing call. The ultrathink routine is
different: `ultrathink_engine()` checks readiness with `probe()` first and
returns on a miss before it ever calls `run()`, so a routine readiness miss
(`plan_not_allowed`, `sandbox_unavailable`) comes back as the routine's
`engine.reason` and raises no item; its task row is left as it was.

## The workspace allowlist

The job worktree must be an absolute, resolved path strictly inside one
allowlisted root, with no `..` component. The engine re-validates the same
knob the wrapper validates in its own `allowedDir` — defense in depth. A
refused worktree raises `path_refused` before anything is spawned: no
wrapper process, no worktree seeding, no stage.

## Result and export contract

One backend run is one GSD stage:

1. `up` seeds the worktree at the seed ref derived from it, so the export has
   a repo to diff against.
2. `run_gsd_stage` returns immediately; the engine waits for the matching
   `stage_done` event with no deadline (an interrupt aborts the stage
   explicitly).
3. `sandbox_status` is read after the stage ends, giving the stage record
   (`status`, `error`, `modelRequested`, `finalText`, and `lastText`).
   `finalText` holds the full answer; `lastText` is the last 600 characters.
   The engine gets `tier` from the earlier `up` reply.
4. A **completed** stage exports commits (`include_dirty`: edits the stage
   left uncommitted are committed first, listed in `dirtyCommitted`; any
   still left make the run `export_dirty`): head, base, ref, the commit
   list, the remaining dirty lines, git bundle, bundle bytes, and the export
   mechanism ride the run's stats (`omp_sandbox.export`) and the task row's
   receipt.

Every wrapper failure — `ok:false` including `path_refused` and tier errors,
protocol garbage, EOF, non-zero exit — is a refused run (`SandboxUnavailable`),
never a fallback. A stage that ends `failed`/`aborted` returns a not-ok result
with reason `engine` instead of raising. Per-job `session.json` and
`events.jsonl` live under `OMEGA_PRIME_STATE_DIR/jobs/<job>/`, and the result
reports `sandbox: "omp-sandbox"`.

## Visibility

When the run carries task context (tasks path and task id both set):

- **Pane.** Each run opens a herdr pane labelled exactly `{task} {stage}`
  running the watch command; if herdr is missing the launch falls back to a
  recorded tmux session, and a pane failure never fails the run.
- **Row lifecycle.** The tasks.json row goes `working` on start (with
  `started_at`, `last_activity_at`, and `herdr_pane` when a pane opened),
  stays fresh through `touch` on wrapper events, and ends `done`/`failed` via
  `finish` with a recursively redacted `receipt`. A terminal row stays closed
  for the same `job_id`, or when no prior job is known. A later run with a
  different `job_id` reopens it as `working` and drops the old `receipt` and
  `herdr_pane` (the rerun's own record replaces them); only a row the run
  actually started is ever touched.
- **Question round-trip.** A GSD follow-up question raises exactly one
  `waiting_on_ove` item (id embedding the wrapper's request id, ask/blocks
  redacted, owning row `blocked`); the engine polls the store at most once a
  second for its additive `answer` — no wall-clock deadline — and pipes the
  answer back through `answer_question` into the live session. A stale answer
  clears the dead item and the re-ask arrives under a new id.
- **Gate blocks.** When `run()` itself refuses with `plan_not_allowed` or
  `sandbox_unavailable`, it raises one `waiting_on_ove` item each in the same
  failing call, with static ask text; a run that fails before starting its
  row writes no row at all — the block is the visibility. This covers only
  failures that reach `run()`. The ultrathink routine's readiness miss stops
  at `probe()` before `run()` (see Stage routing), raises no item and leaves
  the row as it was, so check the routine's `engine.reason` instead of
  waiting for an approval item.

## Testing it

The hermetic suite runs the whole harness with a fake wrapper, fake stage
work, a fake herdr and a temp store:

```bash
python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_sandbox_e2e.py
```

The suite is fakes-only. The live check against the real wrapper is a
separate host-only script, run by hand on a configured host (it uses the
host's Node, logins and wrapper binary, and the network):

```bash
python3 scripts/sandbox-e2e-real.py
```

It drives the same plan and execute stages through the routine in a
throwaway project and prints pass/fail per stage, never an environment
value.

The routine entrypoint a reader can run and inspect:

```bash
python3 -m omega_prime.routines.ultrathink_turn --state-dir <dir> --help
```

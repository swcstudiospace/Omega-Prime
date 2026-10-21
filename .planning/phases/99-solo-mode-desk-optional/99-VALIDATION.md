# Phase 99 validation map

This is a plan for future implementation proof, not evidence that Phase 99 behavior has been implemented. The entries marked **planned proof** are executable commands the parent runs after the relevant wave; they are not worker-run results. No live endpoint, connector, credential, or saved binding is required: routing proofs use fake substrate/Desk transports and the local tool registry.

Upstream stop: before any Phase 99 wave executes, the parent must re-verify the Phase 97/98 prerequisites, including the Phase 97 `docs_search(question=...)` wire contract; if they are unverified or unmet, execution stops and no Phase 99 completion is claimed.

## Success criteria to plans and proof

| Uplift success criterion | Owning plan(s) / files | Planned executable proof and pass condition |
| --- | --- | --- |
| 1. No code path connects to a literal `127.0.0.1` bus URL. | 99-01 runtime routing (`omega_prime/mcp_server.py`, lead bus wiring/tests); 99-VALIDATION.md | Parent runs the targeted bus/runtime tests in 99-01 and performs a throwaway manual source review/search of the bus-construction branch (recorded in the parent receipt, not a permanent compliance test). Fake transports assert no fallback request for absent or blocked bindings and no request at all for Solo. The unrelated substrate/host loopback transports are explicitly not removed. |
| 2. Deterministic tests prove Solo docs and events use a fake substrate; Solo off preserves configured Desk planes including permitted saved bindings; missing/blocked bindings do not create fallback connections. | 99-01 routing and its runtime/lead tests; 99-02 prompt source only if prompt behavior is in scope | Parent runs the exact targeted runtime test command recorded in 99-01 after Wave 1. Tests invoke the actual registry/lead dispatch seam with fake substrate and Desk transports; assert docs uses substrate `question` wire payload without renaming public `query`, events use substrate emit, Desk routes are retained only with explicit opt-out/configuration and permitted bindings, and missing/blocked cases issue no fallback request. A registry-level smoke dispatches the relevant registered tool(s) against injected fakes; it does not start a live service. |
| 3. Substrate outages are explicit, with no false docs/event success and no Desk fallback. | 99-01 routing/runtime tests; 99-04 docs in `docs/substrate.md` and `omega_prime/tests/test_docs.py` | Parent runs 99-01 fake-substrate outage and registry tests, asserting docs error rather than valid empty result, event error with honest local-mirror status rather than upstream-success claim, and zero Desk/localhost calls in Solo. Then run `PATH="/workspace/omes-bot/.venv/bin:$PATH" PYTHONPATH=/workspace/omes-bot-wt/p3 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q` and manually review docs against the exercised contract (throwaway review). |
| 4. Assembled bot-00 prompt no longer always loads desk-gateway; unrelated prompt text remains intact. | 99-02 prompts, including canonical source prompts and assembled output; parent owns assembler integration | Parent runs `VIRTUAL_ENV=/workspace/omes-bot/.venv bash omega_prime/scripts/assemble-prompts.sh --check` after prompt changes plus 99-02 task 3's targeted tests: `PATH="/workspace/omes-bot/.venv/bin:$PATH" PYTHONPATH=/workspace/omes-bot-wt/p3 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_shell.py omega_prime/tests/test_prompt_format.py -q` and its one-off assembled-XML parse smoke. Manually review (throwaway) the assembled output to ensure gateway is on-demand, only the intended loading directives changed, and unrelated prompt content remains. No worker runs the assembler or generator. |
| 5. Skill link validation finds no dead in-repo references; programming-desk dependencies use an honest local-or-note strategy. | 99-03 skills; 99-04 `omega_prime/tests/test_docs.py` | Parent runs `PATH="/workspace/omes-bot/.venv/bin:$PATH" PYTHONPATH=/workspace/omes-bot-wt/p3 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q`; 99-03 records no separate checker command — its link proof is exactly this run plus its own manual file review. Existing links and anchors resolve across docs and skill pages (ordinary page-relative links and same-page anchors preserved; package-root resolution only for the explicit `skills/`, `routines/`, `contracts/`, `ownership.yaml` prefixes); deterministic negative target/anchor fixtures fail. Parent separately inventories unresolved inline-code references because `_prose` intentionally neutralizes inline code; do not claim the ordinary hyperlink checker validates them. Confirm removed/replaced Programming Desk links point only to verified existing local references or carry an explicit honest note; no assumed hosted dataset or fabricated document. |
| 6. All five optional Desk variables and Solo semantics are documented. | 99-04 `docs/setup.md`, `docs/substrate.md` | Parent manually reviews (throwaway, one-off) exact names and mode/routing text in the docs, then runs the targeted docs/public proof above (`omega_prime/tests/test_docs.py` + `omega_prime/tests/test_public_repo.py`). Coverage is documentation and link structure only: no wording/default tests. Ensure local children/stores are distinguished from optional Desk planes and child provider/model prerequisites are truthful. |
| 7. Exact repository gates are green or only named, evidenced baseline failures. | Parent cross-plan verification after Waves 1 and 2; changes across 99-01/02/03/04 | Parent runs the exact gates below once after the relevant plans land, records command, exit, and output, and identifies baseline-only pytest failures by observed name/error against the committed baseline node-ID list below. Run prompt assembly check when prompt files change. The pre-existing baseline below is labeled with its observed commit and was not verified against `origin/main`; it must not be conflated with post-change proof. |

## Wave ownership and boundaries

- **Wave 1 — 99-01:** runtime mode resolution, routing, bus fallback removal, doctor/effective-plane report, deterministic fake-substrate/Desk and registry-level proof. This wave establishes the contract consumed by the other plans.
- **Wave 2 — 99-02:** source prompt/core-directive changes and parent-owned assembled prompt output, dependent on 99-01.
- **Wave 2 — 99-03:** skill rewrites and skill references, dependent on 99-01.
- **Wave 2 — 99-04:** optional operator docs, skill-aware link-checker coverage, and post-proof changelog entry, dependent on 99-01. The 99-02/99-03/99-04 source scopes are file-disjoint. The parent performs cross-plan verification only after all Wave 2 outputs land.

Future edit workers skip gates, tests, linters, formatters, and generators. The execution orchestrator runs proof after each wave. Conditional catalog/plugin regeneration is a parent integration activity outside worker file scopes: catalog/plugin generated outputs appear in no plan's `files_modified`; the one declared generated file is 99-02's parent-regenerated assembled prompt `omega_prime/prompts-assembled/OMEGA_PRIME.xml`, produced only by the parent via the existing assembler; regeneration runs only if drift actually follows source changes. Never hand-edit generated output. No full env-table generator (Phase 102), new package, live endpoint smoke, or unrelated loopback transport removal.

## Exact parent-run commands

From the worktree root `/workspace/omes-bot-wt/p3`. This worktree has no `.venv`; the interpreter lives in the main checkout at `/workspace/omes-bot/.venv`, so `$PWD/.venv/...` invocations fail here. Gate bodies stay exactly as recorded in 99-CONTEXT.md; only the environment selection is worktree-specific:

```bash
export PATH="/workspace/omes-bot/.venv/bin:$PATH"
export PYTHONPATH=/workspace/omes-bot-wt/p3
ruff check omega_prime/
ruff format --check omega_prime/
mypy omega_prime/
pytest omega_prime/tests -q
```

When prompt sources change, also run (the assembler needs `VIRTUAL_ENV` here: it prefers `$REPO/.venv/bin/python`, which does not exist in this worktree, and otherwise uses `$VIRTUAL_ENV/bin/python`):

```bash
VIRTUAL_ENV=/workspace/omes-bot/.venv bash omega_prime/scripts/assemble-prompts.sh --check
```

Targeted docs/public proof, without live endpoints:

```bash
PATH="/workspace/omes-bot/.venv/bin:$PATH" PYTHONPATH=/workspace/omes-bot-wt/p3 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q
```

The targeted 99-01 runtime command is owned by 99-01's verification section and must be copied exactly from it into the parent execution record. The prompt commands are 99-02 task 3's: the assembler check above, `PATH="/workspace/omes-bot/.venv/bin:$PATH" PYTHONPATH=/workspace/omes-bot-wt/p3 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_shell.py omega_prime/tests/test_prompt_format.py -q`, and its one-off assembled-XML parse smoke. The skill-link command is 99-04's targeted docs/public run above; 99-03 deliberately records no separate checker command and relies on that run plus its own manual file review. The runtime proof must include registry-level dispatch through injected fakes, not just direct helper calls. It must not point the registry at live substrate/Desk services.

## Observed current-worktree baseline (parent-reported)

These are parent-observed results for the current worktree at commit `44b28dfd50be6a63b175227a6f748def6f4bcc9c` (the observed checkout; its relationship to `origin/main` was not verified), not proof of Phase 99 behavior, and not a substitute for the future post-change run:

- `ruff check omega_prime/`: pass.
- `ruff format --check omega_prime/`: pass; 589 files checked.
- `mypy omega_prime/`: pass; 462 source files checked.
- `pytest omega_prime/tests -q`: exit 1; 4,777 passed, 11 skipped, 27 failed. Parent reports all 27 are tied to missing/empty `prime-agent` and/or `rlm` prerequisites. The 27 observed failure node IDs are committed below so this evidence is not ephemeral; the full output remains with the parent receipt (`artifact://16:-90`). Do not rerun, and do not generalize these as source regressions.

The 27 observed baseline failure node IDs, with each observed error (`...` abbreviates the worktree path `/workspace/omes-bot-wt/p3`):

```text
omega_prime/tests/test_grokbot_oneclick.py::test_oneclick_dry_run_sse — assert 3 == 0
omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_skill_list_still_returns_the_skills — KeyError: 'skills'
omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_crates_still_reports_the_workspace — KeyError: 'loaded'
omega_prime/tests/test_prime_kernel_cells.py::test_namespace_persists_across_cells — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_cell_output_is_returned_and_never_written_to_the_process_streams — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_cell_exit_requests_are_cell_errors_and_the_kernel_keeps_working — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_cell_spawn_uses_in_process_host — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_cell_child_answer_visible_only_through_collect — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_factory_help_while_disabled — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_validate_spec_rejects_empty — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_enable_factory_keeps_help — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_cells.py::test_run_bash_echo — ModuleNotFoundError: No module named 'rlm'
omega_prime/tests/test_prime_kernel_host.py::test_prime_kernel_host_round_trip — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_marks_edit_importable_and_factory_markdown_only — FileNotFoundError: .../prime-agent/skills
omega_prime/tests/test_prime_kernel_skills.py::test_load_skill_edit_replaces_a_unique_string — PrimeSkillError: skill 'edit' was not found under .../prime-agent/skills
omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_does_not_import_attach_image — FileNotFoundError: .../prime-agent/skills
omega_prime/tests/test_prime_native.py::test_native_status_accepts_unbuilt_extension — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_goal_bindings_follow_pa_core — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_autonomous_run_uses_pa_core_limits — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_protocol_parse_and_encode_use_the_rust_frames — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_heartbeat_schedule_and_deferral — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_refinement_history_round_trip — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_goal_apply_uses_pa_core_transitions — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_slash_parsers_match_pa_core — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_native_tool_validation_rejects_missing_required_argument — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_prime_native.py::test_kernel_tools_call_the_rust_commands — FileNotFoundError: .../prime-agent/Cargo.toml
omega_prime/tests/test_setup_check.py::test_checks_pass_on_repo_root — assert False is True
```

- Initial attempts with the wrong shell environment returned 127; a pytest attempt with the wrong interpreter exited with code 2 and 63 collection errors (not 2 errors). The parent will record these attempts in its receipt. Do not rerun them as part of this plan.

No check in this section was run by the plan author. Future post-change evidence must state its own observed exit/output separately from the current baseline. Any new failures are not baseline-only without direct evidence that they match the named missing/empty `prime-agent`/`rlm` failure causes.

## Remaining proof distinctions

- Phase 97 docs wire contract (`question`) and Phase 98 completion are upstream prerequisites and remain unverified here. Phase 99 docs and tests target that contract; do not claim prerequisite completion based on milestone registration. Upstream stop: no Phase 99 wave executes until the parent has re-verified these prerequisites; if they are unmet, execution stops and no completion is claimed.
- Registry-level smoke is local and deterministic: instantiate/build the registry with injected fake substrate and Desk seams, dispatch public tool names, and inspect results plus recorded requests. It is not an endpoint health check.
- The ordinary link resolver ignores inline-code references by design. Parent's separate inline-code inventory must be labeled an inventory, not a passing checker result.
- A changelog entry is added only after the parent observes the docs smoke/checker proof. A generated catalog/plugin output is touched only if parent establishes source-caused drift and uses its existing generator.
- Source-text reviews named in the table above are throwaway manual inspections recorded in the parent receipt, not durable automated proof; no permanent wording-compliance test is added.

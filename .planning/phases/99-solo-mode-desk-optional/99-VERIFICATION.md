# Phase 99 verification

## Focused behavior proof

Command:

```bash
PATH="/workspace/omes-bot/.venv/bin:$PATH" PYTHONPATH=/workspace/omes-bot-wt/p3 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_lead.py omega_prime/tests/test_desk_wiring.py omega_prime/tests/test_desk_lead.py omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py omega_prime/tests/test_shell.py omega_prime/tests/test_prompt_format.py omega_prime/tests/test_command_generation.py -q
```

Result: exit 0; 176 passed. Covers solo substrate docs/events, outage handling, Desk-off compatibility, prompt assembly contracts, and docs/link validation.

## Required gates

| Command | Exit | Result |
| --- | ---: | --- |
| `ruff check omega_prime/` | 0 | Passed |
| `ruff format --check omega_prime/` | 0 | 589 files already formatted |
| `mypy omega_prime/` | 0 | Success; no issues in 462 source files |
| `bash omega_prime/scripts/assemble-prompts.sh --check` (with `VIRTUAL_ENV=/workspace/omes-bot/.venv`) | 0 | OMEGA_PRIME up to date |
| `pytest omega_prime/tests -q` | 1 | 4,782 passed, 11 skipped, 27 failed; all 27 match the documented baseline failure set below |

The full-suite failures are the same 27 baseline node IDs recorded in [99-VALIDATION.md](99-VALIDATION.md): one Grok Bot dry-run failure due to empty `prime-agent`; two Prime boundary failures (`KeyError: skills` / `loaded`); 9 Prime kernel tests fail because `rlm` is absent; 14 Prime kernel host/skills/native tests require the absent `prime-agent` checkout; and `test_setup_check.py::test_checks_pass_on_repo_root` fails. The observed full-suite run confirms the same names and causes; no new failing node ID appeared.

## Deliberately unverified

- Phase 97's substrate docs-search wire mapping and SSE integration. This phase did not change `SubstrateClient.docs_search`; the API seam needs verification after rebase.
- Phase 98 integration, live substrate/Desk endpoints, connector restart, and deployment. None are needed or authorized for this local proof.
- LSP's Pyright environment reports unresolved `anyio`/`mcp` imports in `mcp_server.py` and an existing `lead.py` typing diagnostic; the mandated mypy gate passes.
- Rule adherence by future agents.

# Requirements: v21 Task Dashboard

Accepted 2026-10-21 from the task-dashboard uplift (operator-approved at
01:51 AEDT). The full uplift spec — scope, constraints, data sources, task
model, API, security and graceful-degradation rules — is
`.planning/phases/104-task-dashboard/104-UPLIFT.xml`. Requirements TD-01
through TD-08 are the uplift's acceptance criteria a1–a8. This phase plans
and implements the dashboard package; the live run, the PR and the merge
yes are operator steps after the phase.

- [ ] **TD-01**: `ss -ltn` shows the dashboard only on `127.0.0.1:<port>`. Starting with `--host 0.0.0.0` (or any non-loopback host) exits non-zero with a clear message.
- [ ] **TD-02**: With the box as observed, `/api/snapshot` lists the Herdr pane w2:p1 "swarm-smoke", the live wiring phases p1/p2/p3/p4/p6/p7 with branch and stage, the omp-sandbox e2e run with its two podman containers, and recent omega jobs, each with started and last-activity times.
- [ ] **TD-03**: The PR gate lists open omes-bot PRs with CI and Greptile columns, and later shows `fix/wiring-*` PRs once they open, without a restart.
- [ ] **TD-04**: Writing a sample `tasks.json` via the writer helper makes its tasks, `waiting_on_ove` and `finished` items appear in the page within 5s with no reload.
- [ ] **TD-05**: The seeded-secret test passes: no fake token appears in any response. `rg` over the served HTML/JS finds no innerHTML with data.
- [ ] **TD-06**: Killing herdr, stopping podman, or removing the PAT turns that source amber or red in the health strip while the other sections keep updating.
- [ ] **TD-07**: Every non-GET method returns 405, and the Host-header guard rejects a foreign Host.
- [ ] **TD-08**: ruff, mypy and pytest are green or baseline-only (recorded baseline: `test_desk_wiring`, `test_grokbot_oneclick`, `test_omega_commands` doctor tests, `test_prime_kernel_*`), and all new dashboard tests pass. Greptile 5/5 with green CI before the operator's merge yes is the follow-on gate, owned by the operator.

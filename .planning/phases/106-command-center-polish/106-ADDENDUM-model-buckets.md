# Addendum (parent/Ove, 2026-10-21 18:39 AEDT): two bucket bugs in omega_prime/dashboard/model.py, inside the existing plans

Folded into task 00025 / phase 106. No new uplift round. Everything else stays the same.

1) tasks_json path (normalize_tasks, the `task_source` loop): a task with state 'done' or 'failed' is appended to
   `active` AND to `finished`, inflating the Active count. Terminal states must go ONLY to `finished`
   ('done' -> result "ok", 'failed' -> result "failed"; today 'failed' is never added to finished at all).
   Non-terminal states stay in active exactly as now.
2) Herdr panes whose session has exited (the matched omp session has a stop_reason, or the pane has no live agent)
   or whose agent_status is unknown (not idle/working/blocked), plus sandbox_e2e runs that are only 'quiet', currently
   count as Active work. Move them to a new separate `idle_sessions` group on the Snapshot (list of Task, included in
   to_dict()/content_hash and in the /api/snapshot payload), which does NOT count toward Active. The frontend shows
   an "Idle sessions" group (sidebar/overview/tasks list, collapsed by default is fine) that is excluded from the
   Active/Working count and bucket. Blocked panes still raise a waiting item; working/idle/blocked agent panes with a
   live session stay in active. Task-id allocation and any consumer that iterates snapshot.active must still see
   (or deliberately skip) these rows consistently; check poller/app/task_ids before changing.

Regression tests (must FAIL on ad3942b, PASS after):
- a tasks_json task in 'done' and one in 'failed' -> neither in snapshot.active; both in finished with ok/failed.
- a herdr pane with agent_status 'unknown', a pane whose matched session has stop_reason, and a quiet sandbox_e2e
  run -> all in snapshot.idle_sessions, none in snapshot.active; a working pane and a working sandbox_e2e stay active.
- frontend: Active/Working count excludes idle sessions; the Idle sessions group renders them.

Plan placement: Python part in 106-01 (wave 1, model.py + tests; 106-05 later adds herdr_pane on top of it in wave 2).
Frontend part in 106-04 (types.ts, selectors, Overview/sidebar/Tasks).

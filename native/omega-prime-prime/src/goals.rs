// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Spectrum Web Co
//! Bindings for `pa_core::goals` and the `pa_types::goal` wire state.

use pa_core::goals::GoalContextKind;
use pa_core::session_engine::goal_driver::creation_elapsed_seconds;
use pa_core::slash_command_args::GoalCommand;
use pa_types::goal::{GoalState, GoalStatus};
use pyo3::prelude::*;
use serde_json::json;

use crate::convert::{from_py, py_err, to_py};

fn goal_state(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<GoalState> {
    from_py(py, obj)
}

fn context_kind(kind: &str) -> PyResult<GoalContextKind> {
    match kind {
        "continuation" => Ok(GoalContextKind::Continuation),
        "budget_limit" => Ok(GoalContextKind::BudgetLimit),
        "objective_updated" => Ok(GoalContextKind::ObjectiveUpdated),
        other => Err(py_err(format!(
            "unknown goal context kind '{other}' (expected continuation, budget_limit, or objective_updated)"
        ))),
    }
}

/// Trimmed objective, or an error when it is empty or longer than the Prime cap.
#[pyfunction]
pub fn validate_goal_objective(text: &str) -> PyResult<String> {
    pa_core::goals::validate_goal_objective(text).map_err(py_err)
}

/// `None` is allowed. Zero is rejected. Any other budget is returned unchanged.
#[pyfunction]
pub fn validate_goal_budget(budget: Option<u64>) -> PyResult<Option<u64>> {
    pa_core::goals::validate_goal_budget(budget).map_err(py_err)
}

/// `max(input, 0) + max(output, 0)` from `goal_token_delta_for_usage`.
#[pyfunction]
pub fn goal_token_delta(input: i64, output: i64) -> u64 {
    pa_core::goals::goal_token_delta_for_usage(input, output)
}

/// Idle goal (`pa_types::goal::empty_goal_state`), camelCase wire object.
#[pyfunction]
pub fn empty_goal_state(py: Python<'_>) -> PyResult<PyObject> {
    to_py(py, &pa_types::goal::empty_goal_state())
}

/// Derive `active` from the status and backfill `createdAt` the way Prime does.
#[pyfunction]
pub fn normalize_goal_state(py: Python<'_>, goal: Bound<'_, PyAny>) -> PyResult<PyObject> {
    let goal = goal_state(py, &goal)?;
    to_py(py, &pa_core::goals::normalize_goal_state(goal))
}

/// Same state with `timeUsedSeconds` cleared, for the goal-update dedupe.
#[pyfunction]
pub fn goal_update_dedupe_projection(py: Python<'_>, goal: Bound<'_, PyAny>) -> PyResult<PyObject> {
    let goal = goal_state(py, &goal)?;
    to_py(py, &pa_core::goals::goal_update_dedupe_projection(&goal))
}

/// True when the object is a persisted `thread_goal_state` payload.
#[pyfunction]
pub fn is_persisted_goal_state(py: Python<'_>, value: Bound<'_, PyAny>) -> PyResult<bool> {
    let value = from_py::<serde_json::Value>(py, &value)?;
    Ok(pa_core::goals::is_persisted_goal_state(&value))
}

/// Kernel `goal.*` reply. `goal` is snake_case; counters stay on the wire names.
#[pyfunction]
#[pyo3(signature = (goal, include_completion_report=false))]
pub fn goal_host_response(
    py: Python<'_>,
    goal: Bound<'_, PyAny>,
    include_completion_report: bool,
) -> PyResult<PyObject> {
    let goal = goal_state(py, &goal)?;
    to_py(
        py,
        &pa_core::goals::goal_host_response(&goal, include_completion_report),
    )
}

/// `"3 / 10 tokens"`, a seconds line, or `None` when there is nothing to show.
#[pyfunction]
pub fn format_goal_usage(py: Python<'_>, goal: Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let goal = goal_state(py, &goal)?;
    Ok(pa_core::goals::format_goal_usage(&goal))
}

/// Custom `goal_context` message for `continuation`, `budget_limit`, or `objective_updated`.
#[pyfunction]
pub fn create_goal_context_message(
    py: Python<'_>,
    goal: Bound<'_, PyAny>,
    kind: &str,
) -> PyResult<PyObject> {
    let goal = goal_state(py, &goal)?;
    let message =
        pa_core::goals::create_goal_context_message(&goal, context_kind(kind)?, &[])
            .map_err(py_err)?;
    to_py(py, &message)
}

/// Failure text when the newest persisted goal is still active after a terminal provider error.
#[pyfunction]
pub fn stale_active_goal_failure(
    py: Python<'_>,
    entries: Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let entries = from_py::<Vec<pa_types::session::FileEntry>>(py, &entries)?;
    Ok(pa_core::goals::stale_active_goal_failure(&entries))
}

fn now_ms() -> u64 {
    pa_daemon::util::now_ms()
}

fn status_text(state: &GoalState, cleared: bool) -> String {
    if cleared {
        return "Goal cleared.".to_string();
    }
    match &state.objective {
        Some(objective) if state.status != GoalStatus::Idle => {
            format!("Goal {}: {objective}", state.status.slug())
        }
        _ => "No active goal.".to_string(),
    }
}

/// One thread goal. Transitions call the same `pa-core` functions as
/// `GoalDriver`; the session JSONL append stays with the caller.
#[pyclass(name = "Goal", module = "omega_prime_prime.goals")]
pub struct Goal {
    state: GoalState,
}

#[pymethods]
impl Goal {
    #[new]
    fn new() -> Self {
        Self {
            state: pa_types::goal::empty_goal_state(),
        }
    }

    #[staticmethod]
    fn from_wire(py: Python<'_>, value: Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self {
            state: goal_state(py, &value)?,
        })
    }

    fn wire(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_py(py, &self.state)
    }

    /// Account one turn with `goal_token_delta_for_usage`. A non-active goal
    /// returns `"ignored"`. Reaching the budget returns `"budget_reached"`.
    fn accrue(&mut self, input: i64, output: i64) -> String {
        if self.state.status != GoalStatus::Active {
            return "ignored".to_string();
        }
        let token_delta = pa_core::goals::goal_token_delta_for_usage(input, output);
        let next = GoalState {
            tokens_used: self.state.tokens_used + token_delta,
            ..self.state.clone()
        };
        let budget_reached = next
            .token_budget
            .is_some_and(|budget| next.tokens_used >= budget);
        if budget_reached {
            let reason = next
                .token_budget
                .map(|budget| format!("Reached {budget} token goal budget"))
                .unwrap_or_default();
            self.commit(GoalState {
                active: false,
                status: GoalStatus::BudgetLimited,
                last_reason: Some(reason),
                last_error: None,
                ..next
            });
            "budget_reached".to_string()
        } else {
            self.commit(next);
            "accounted".to_string()
        }
    }

    /// Apply the text after `/goal` and return the command, wire state, text, and context.
    fn apply(&mut self, py: Python<'_>, args: &str) -> PyResult<PyObject> {
        let command = pa_core::slash_command_args::parse_goal_command(args).map_err(py_err)?;
        let name = match &command {
            GoalCommand::Status => "status",
            GoalCommand::Clear => "clear",
            GoalCommand::Pause => "pause",
            GoalCommand::Resume => "resume",
            GoalCommand::Start { .. } => "start",
        };
        let mut cleared = false;
        let mut context = None;
        match command {
            GoalCommand::Status => {}
            GoalCommand::Clear => {
                cleared = self.state.objective.is_some() && self.state.status != GoalStatus::Idle;
                self.commit(pa_types::goal::empty_goal_state());
            }
            GoalCommand::Pause => {
                if self.state.status == GoalStatus::Active {
                    self.commit(GoalState {
                        active: false,
                        status: GoalStatus::Paused,
                        last_reason: Some("Paused by user".to_string()),
                        last_error: None,
                        ..self.state.clone()
                    });
                }
            }
            GoalCommand::Resume => {
                context = self.resume()?;
            }
            GoalCommand::Start {
                objective,
                token_budget,
            } => {
                let budget = pa_core::goals::validate_goal_budget(token_budget).map_err(py_err)?;
                let now = now_ms();
                self.commit(GoalState {
                    active: true,
                    status: GoalStatus::Active,
                    goal_id: Some(uuid::Uuid::new_v4().to_string()),
                    objective: Some(objective),
                    token_budget: budget,
                    tokens_used: 0,
                    time_used_seconds: 0,
                    continuations_used: 0,
                    created_at: Some(now),
                    no_progress_streak: Some(0),
                    no_progress_turn_ms: None,
                    updated_at: Some(now),
                    last_reason: None,
                    last_error: None,
                });
                context = Some(
                    pa_core::goals::create_goal_context_message(
                        &self.state,
                        GoalContextKind::Continuation,
                        &[],
                    )
                    .map_err(py_err)?,
                );
            }
        }
        let body = json!({
            "command": name,
            "cleared": cleared,
            "text": status_text(&self.state, cleared),
            "state": serde_json::to_value(&self.state).map_err(py_err)?,
            "context": context,
        });
        to_py(py, &body)
    }
}

impl Goal {
    fn commit(&mut self, next: GoalState) {
        let now = now_ms();
        let normalized = pa_core::goals::normalize_goal_state(GoalState {
            updated_at: Some(now),
            ..next
        });
        self.state = match normalized.created_at {
            Some(created_at) => GoalState {
                time_used_seconds: creation_elapsed_seconds(Some(created_at), now),
                ..normalized
            },
            None => normalized,
        };
    }

    fn resume(&mut self) -> PyResult<Option<pa_types::session::CustomMessage>> {
        if self.state.objective.is_none() {
            return Ok(None);
        }
        if !matches!(
            self.state.status,
            GoalStatus::Paused | GoalStatus::BudgetLimited
        ) {
            return Ok(None);
        }
        let exhausted = self
            .state
            .token_budget
            .is_some_and(|budget| self.state.tokens_used >= budget);
        let next_status = if exhausted {
            GoalStatus::BudgetLimited
        } else {
            GoalStatus::Active
        };
        self.commit(GoalState {
            active: next_status == GoalStatus::Active,
            status: next_status,
            last_reason: exhausted.then(|| "Goal token budget already reached".to_string()),
            last_error: None,
            ..self.state.clone()
        });
        if next_status == GoalStatus::Active {
            return Ok(Some(
                pa_core::goals::create_goal_context_message(
                    &self.state,
                    GoalContextKind::Continuation,
                    &[],
                )
                .map_err(py_err)?,
            ));
        }
        Ok(None)
    }
}

pub fn register(parent: &Bound<'_, PyModule>) -> PyResult<()> {
    let module = PyModule::new(parent.py(), "goals")?;
    module.add_class::<Goal>()?;
    module.add_function(wrap_pyfunction!(validate_goal_objective, &module)?)?;
    module.add_function(wrap_pyfunction!(validate_goal_budget, &module)?)?;
    module.add_function(wrap_pyfunction!(goal_token_delta, &module)?)?;
    module.add_function(wrap_pyfunction!(empty_goal_state, &module)?)?;
    module.add_function(wrap_pyfunction!(normalize_goal_state, &module)?)?;
    module.add_function(wrap_pyfunction!(goal_update_dedupe_projection, &module)?)?;
    module.add_function(wrap_pyfunction!(is_persisted_goal_state, &module)?)?;
    module.add_function(wrap_pyfunction!(goal_host_response, &module)?)?;
    module.add_function(wrap_pyfunction!(format_goal_usage, &module)?)?;
    module.add_function(wrap_pyfunction!(create_goal_context_message, &module)?)?;
    module.add_function(wrap_pyfunction!(stale_active_goal_failure, &module)?)?;
    module.add(
        "MAX_THREAD_GOAL_OBJECTIVE_CHARS",
        pa_core::goals::MAX_THREAD_GOAL_OBJECTIVE_CHARS,
    )?;
    module.add(
        "GOAL_STATE_CUSTOM_TYPE",
        pa_core::goals::GOAL_STATE_CUSTOM_TYPE,
    )?;
    module.add(
        "GOAL_CONTEXT_CUSTOM_TYPE",
        pa_core::goals::GOAL_CONTEXT_CUSTOM_TYPE,
    )?;
    parent.add_submodule(&module)?;
    Ok(())
}

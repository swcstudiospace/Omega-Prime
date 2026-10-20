//! The tool surface exposed by the wrapper. Shaped like Anda Engine tools (name + JSON args);
//! with the `anda` feature these would be registered as anda_core::Tool impls (seam only).
pub const TOOLS: &[(&str, &str)] = &[
    ("up", "Start auth-broker, sandbox tier, agentOS VM, GSD-Core install and omp probe"),
    ("run_gsd_stage", "Run a GSD stage (plan|execute) via omp RPC inside the sandbox"),
    ("answer_question", "Answer a pending omp ask-dialog question (stale answers are rejected)"),
    ("sandbox_status", "Tier, stages, pending questions, models seen"),
    ("abort", "Abort a running stage"),
    ("down", "Abort stages, dispose VM + sandbox, stop broker, report leftovers"),
];

pub fn is_tool(name: &str) -> bool {
    TOOLS.iter().any(|(n, _)| *n == name)
}

//! The sandbox's tool surface, registered as real Anda Engine tools (anda_core::Tool<BaseCtx>).
//! The engine is Private and exports nothing: only its controller (the local client principal)
//! can call these, through signed ICP envelopes verified by anda_engine_server.
use crate::core_child::Core;
use anda_core::{StateFeatures, BoxError, FunctionDefinition, Json, Resource, Tool, ToolOutput};
use anda_engine::context::BaseCtx;
use serde_json::json;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

pub const TOOLS: &[(&str, &str)] = &[
    ("up", "Start auth-broker, sandbox tier, agentOS VM, GSD-Core install and omp probe"),
    ("run_gsd_stage", "Run a GSD stage (plan|execute) via omp RPC inside the sandbox"),
    ("answer_question", "Answer a pending omp ask-dialog question (stale answers are rejected)"),
    ("sandbox_status", "Tier, stages, pending questions, models seen"),
    ("abort", "Abort a running stage"),
    ("run_check", "Run an allowlisted build/test command (npm, npx, node, python3, git) in the sandbox workdir; receipt written"),
    ("export_commits", "Export commits made in the seeded repo since its seed base as a git bundle on the host"),
    ("down", "Abort stages, dispose VM + sandbox, stop broker, report leftovers"),
    ("poll_events", "Drain queued sandbox events (question, stage_done, fallback, ...); waits up to wait_ms"),
];

pub fn is_tool(name: &str) -> bool { TOOLS.iter().any(|(n, _)| *n == name) }

/// Freshness on top of Anda's signed envelopes (which authenticate the body but have no freshness):
/// every call must carry meta {nonce, ts_ms}; stale timestamps and reused nonces are refused.
#[derive(Default)]
pub struct ReplayGuard { seen: Mutex<HashMap<String, u64>> }
pub const MAX_SKEW_MS: u64 = 120_000;

pub fn now_ms() -> u64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64 }

impl ReplayGuard {
    pub fn check(&self, nonce: Option<String>, ts_ms: Option<u64>, now: u64) -> Result<(), BoxError> {
        let nonce = nonce.filter(|n| n.len() >= 16 && n.len() <= 128).ok_or("missing request nonce")?;
        let ts = ts_ms.ok_or("missing request timestamp")?;
        if ts.abs_diff(now) > MAX_SKEW_MS { return Err("stale request timestamp".into()); }
        let mut seen = self.seen.lock().unwrap();
        seen.retain(|_, t| t.abs_diff(now) <= 2 * MAX_SKEW_MS);
        if seen.insert(nonce, ts).is_some() { return Err("replayed request nonce".into()); }
        Ok(())
    }
}

pub struct SandboxTool { pub name: &'static str, pub desc: &'static str, pub core: Arc<Core>, pub guard: Arc<ReplayGuard> }

impl Tool<BaseCtx> for SandboxTool {
    type Args = Json;
    type Output = Json;
    fn name(&self) -> String { self.name.to_string() }
    fn description(&self) -> String { self.desc.to_string() }
    fn definition(&self) -> FunctionDefinition {
        FunctionDefinition { name: self.name(), description: self.description(), parameters: json!({"type": "object"}), strict: None }
    }
    async fn call(&self, ctx: BaseCtx, args: Json, _resources: Vec<Resource>) -> Result<ToolOutput<Json>, BoxError> {
        let meta = ctx.meta();
        self.guard.check(meta.get_extra_as::<String>("nonce"), meta.get_extra_as::<u64>("ts_ms"), now_ms())?;
        let caller = ctx.caller().to_text();
        let out = if self.name == "poll_events" {
            let wait = args.get("wait_ms").and_then(|v| v.as_u64()).unwrap_or(5000).min(30_000);
            json!({"ok": true, "result": {"events": self.core.poll(wait).await}})
        } else {
            self.core.call(self.name, if args.is_null() { json!({}) } else { args }, &caller).await
        };
        let mut out = out;
        if let Some(o) = out.as_object_mut() {
            o.insert("access".into(), json!({"via": "anda_engine_server 0.17.0", "auth": "ICP signed envelope (Ed25519)", "caller": caller, "engine": ctx.engine_id().to_text()}));
            o.insert("attestation".into(), json!("mock"));
            o.insert("tee".into(), json!("none: local mock attestation, no TEE hardware"));
        }
        Ok(ToolOutput::new(out))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const N: &str = "0123456789abcdef0123";

    #[test]
    fn replay_guard() {
        let g = ReplayGuard::default();
        let now = now_ms();
        assert!(g.check(None, Some(now), now).unwrap_err().to_string().contains("nonce"));
        assert!(g.check(Some("short".into()), Some(now), now).is_err());
        assert!(g.check(Some(N.into()), None, now).unwrap_err().to_string().contains("timestamp"));
        assert!(g.check(Some(N.into()), Some(now - 600_000), now).unwrap_err().to_string().contains("stale"));
        assert!(g.check(Some(N.into()), Some(now + 600_000), now).is_err());
        g.check(Some(N.into()), Some(now), now).unwrap();
        assert!(g.check(Some(N.into()), Some(now), now).unwrap_err().to_string().contains("replayed"));
        g.check(Some(format!("{N}x")), Some(now - 1000), now).unwrap();
    }

    #[test]
    fn tool_surface() {
        for t in ["up", "run_gsd_stage", "answer_question", "sandbox_status", "abort", "run_check", "export_commits", "down", "poll_events"] { assert!(is_tool(t)); }
        assert!(!is_tool("anda_auth_probe") && !is_tool("exec") && !is_tool(""));
    }
}

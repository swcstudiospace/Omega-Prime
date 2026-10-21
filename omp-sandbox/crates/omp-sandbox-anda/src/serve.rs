//! `omp-sandbox-anda serve`: Anda Engine (Private, controller = client principal) in front of the
//! TS core, served by anda_engine_server on loopback. Prints one `anda_ready` JSON line on stdout.
use crate::{core_child::Core, keys, tools::{ReplayGuard, SandboxTool, TOOLS}};
use anda_engine::context::Web3SDK;
use anda_engine::engine::{AgentInfo, EchoEngineInfo, Engine};
use anda_engine::management::{BaseManagement, Visibility};
use anda_engine_server::ServerBuilder;
use candid::Principal;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::Ordering;

pub async fn build_engine(core: Arc<Core>, controller: Principal, web3: Arc<anda_web3_client::client::Client>) -> Result<Arc<Engine>, String> {
    let info = AgentInfo {
        handle: "omp_sandbox".into(), name: "omp-sandbox".into(),
        description: "Authenticated access to the omp + GSD-Core sandbox (attestation: mock)".into(),
        endpoint: "https://localhost/default".into() /* nominal: AgentInfo requires https; real transport is loopback HTTP */, ..Default::default()
    };
    let guard = Arc::new(ReplayGuard::default());
    let mut b = Engine::builder()
        .with_info(info.clone())
        .with_web3_client(Arc::new(Web3SDK::from_web3(web3)))
        .with_management(Arc::new(BaseManagement { controller, managers: BTreeSet::new(), visibility: Visibility::Private }));
    for (name, desc) in TOOLS {
        b = b.register_tool(Arc::new(SandboxTool { name, desc, core: core.clone(), guard: guard.clone() })).map_err(|e| e.to_string())?;
    }
    // Nothing is exported: anonymous callers cannot even list the tools.
    let b = b.register_agent(Arc::new(EchoEngineInfo::new(info)), None).map_err(|e| e.to_string())?;
    Ok(Arc::new(b.build("omp_sandbox".into()).await.map_err(|e| e.to_string())?))
}

pub async fn run(root: &Path, addr: &str) -> Result<(), String> {
    let controller = keys::client_principal()?;
    let web3 = keys::engine().await?;
    let (core, mut child) = Core::spawn(root).map_err(|e| format!("spawn TS core: {e}"))?;
    let engine = build_engine(core.clone(), controller, web3).await?;
    let id = engine.id();
    let app = ServerBuilder::new().with_engines(BTreeMap::from([(id, engine)]), None).build_router().map_err(|e| e.to_string())?;
    let listener = tokio::net::TcpListener::bind(addr).await.map_err(|e| e.to_string())?;
    let local = listener.local_addr().map_err(|e| e.to_string())?;
    if !local.ip().is_loopback() { return Err("refusing to bind a non-loopback address".into()); }
    println!("{}", json!({"event": "anda_ready", "addr": format!("http://{local}"), "engine": id.to_text(), "controller": controller.to_text(),
        "server_pid": std::process::id(), "core_pid": core.pid, "attestation": "mock", "anda": {"engine": "0.17.4", "core": "0.17.1", "server": "0.17.0"}}));
    let c2 = core.clone();
    let shutdown = async move {
        // Serve until the core exits (after `down`), then give clients a moment to read the reply.
        if !c2.exited.load(Ordering::SeqCst) { c2.exit_notify.notified().await; }
        tokio::time::sleep(std::time::Duration::from_millis(1500)).await;
    };
    let sig = async {
        let mut t = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).unwrap();
        tokio::select! { _ = t.recv() => {}, _ = tokio::signal::ctrl_c() => {}, _ = parent_gone() => {} }
    };
    tokio::select! {
        r = axum_serve(listener, app, shutdown) => r?,
        _ = sig => { let _ = core.call("down", json!({}), "signal").await; }
    }
    let _ = tokio::time::timeout(std::time::Duration::from_secs(60), child.wait()).await;
    Ok(())
}

/// Env var a spawning `client` sets to its own pid: this server then shuts down (running `down`) if that parent
/// dies without a graceful shutdown (SIGKILL, crash), instead of leaving the core and sandbox job running.
pub const PARENT_ENV: &str = "OMP_SANDBOX_ANDA_PARENT";

/// Resolves once the spawning parent named by `PARENT_ENV` is gone (we were reparented). Never resolves when unset.
async fn parent_gone() {
    let Some(want) = std::env::var(PARENT_ENV).ok().and_then(|v| v.parse::<u32>().ok()) else { return std::future::pending().await };
    loop {
        if std::os::unix::process::parent_id() != want { return; }
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    }
}

async fn axum_serve(l: tokio::net::TcpListener, app: axum::Router, s: impl std::future::Future<Output = ()> + Send + 'static) -> Result<(), String> {
    axum::serve(l, app).with_graceful_shutdown(s).await.map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    //! In-process integration: real Anda engine + anda_engine_server on loopback in front of a stub
    //! core (a tiny JS echo server), exercised with signed and unsigned requests.
    use super::*;
    use crate::client::{Conn, auth_probe, call};

    const STUB: &str = r#"
const rl = require("node:readline").createInterface({ input: process.stdin });
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");
rl.on("line", (l) => { const r = JSON.parse(l); out({ id: r.id, ok: true, result: { op: r.op, access: r.access } }); if (r.op === "down") setTimeout(() => process.exit(0), 50); });
out({ event: "ready" });
"#;

    #[tokio::test(flavor = "multi_thread")]
    async fn signed_controller_allowed_everything_else_refused() {
        if std::process::Command::new(std::env::var("OMP_SANDBOX_NODE").unwrap_or_else(|_| format!("{}/tools/node24/bin/node", std::env::var("HOME").unwrap()))).arg("--version").output().is_err() {
            // CI sets OMP_SANDBOX_REQUIRE_NODE=1 so a missing Node fails the signed-access test instead of skipping it.
            if std::env::var("OMP_SANDBOX_REQUIRE_NODE").as_deref() == Ok("1") { panic!("OMP_SANDBOX_REQUIRE_NODE=1 but node is not runnable (set OMP_SANDBOX_NODE)"); }
            eprintln!("skipping: node not found"); return;
        }
        let root = std::env::temp_dir().join(format!("osa-serve-{}", std::process::id()));
        std::fs::create_dir_all(root.join("src/core")).unwrap();
        std::fs::write(root.join("src/core/server.ts"), STUB).unwrap();
        let me = keys::ephemeral().await.unwrap();
        let eng = keys::ephemeral().await.unwrap();
        let (core, _child) = Core::spawn(&root).unwrap();
        let engine = build_engine(core.clone(), me.get_principal(), eng).await.unwrap();
        let id = engine.id();
        let app = ServerBuilder::new().with_engines(BTreeMap::from([(id, engine)]), None).build_router().unwrap();
        let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", l.local_addr().unwrap());
        tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
        let conn = Conn { http: reqwest::Client::builder().no_proxy().build().unwrap(), url, engine: id };

        let (st, r) = call(&conn, &me, "sandbox_status", json!({})).await;
        let v = r.unwrap_or_else(|e| panic!("controller call refused: {st} {e}"));
        assert_eq!(v["result"]["op"], "sandbox_status");
        assert_eq!(v["result"]["access"]["caller"], me.get_principal().to_text());
        assert_eq!(v["attestation"], "mock");
        assert_eq!(v["access"]["auth"], "ICP signed envelope (Ed25519)");

        let p = auth_probe(&conn, &me).await;
        eprintln!("{}", serde_json::to_string_pretty(&p).unwrap());
        for c in p["result"]["cases"].as_array().unwrap() { assert_eq!(c["pass"], true, "case failed: {c}"); }
        assert_eq!(p["result"]["all_pass"], true);
        std::fs::remove_dir_all(&root).ok();
    }
}

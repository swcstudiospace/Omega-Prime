//! omp-sandbox-anda: the Anda Engine access layer for the omp-sandbox TypeScript core.
//!   keygen                   create local identities (0600, ~/.local/state/omp-sandbox-anda); prints principals only
//!   serve  [--addr A]        Anda engine + anda_engine_server on loopback in front of the TS core
//!   client [--spawn|--addr URL]  stdio JSON-lines -> signed Anda tool_calls (default: --spawn)
//!   --list-tools             print the tool surface
mod attestation;
mod client;
mod core_child;
mod keys;
mod serve;
mod tools;

use serde_json::json;
use std::path::PathBuf;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let flag = |f: &str| args.iter().position(|a| a == f).and_then(|i| args.get(i + 1).cloned());
    let root = std::env::var("OMP_SANDBOX_ROOT").map(PathBuf::from).unwrap_or_else(|_| std::env::current_dir().unwrap());
    let r = match args.first().map(String::as_str) {
        Some("--list-tools") => {
            let list: Vec<_> = tools::TOOLS.iter().map(|(n, d)| json!({"name": n, "description": d})).collect();
            println!("{}", attestation::stamp(json!({"tools": list, "anda": {"engine": "0.17.4", "core": "0.17.1", "server": "0.17.0", "web3_client": "0.17.0"}, "auth": "ICP signed envelope (Ed25519) + nonce/ts; engine Private, controller-only", "tee": false})));
            Ok(())
        }
        Some("keygen") => async {
            let c = keys::client().await?; let e = keys::engine().await?;
            println!("{}", json!({"client_principal": c.get_principal().to_text(), "engine_principal": e.get_principal().to_text(), "dir": keys::state_dir(), "mode": "0600"}));
            Ok(())
        }.await,
        Some("serve") => serve::run(&root, &flag("--addr").unwrap_or_else(|| "127.0.0.1:0".into())).await,
        Some("client") | None => {
            let r = client::run(flag("--addr"), flag("--addr").is_none()).await;
            // tokio's stdin reader is a blocking thread that would keep the runtime alive until EOF.
            if r.is_ok() { std::process::exit(0); }
            r
        }
        Some(o) => Err(format!("unknown command {o}")),
    };
    if let Err(e) = r { eprintln!("omp-sandbox-anda: {e}"); println!("{}", attestation::stamp(json!({"event": "fatal", "error": e}))); std::process::exit(1); }
}

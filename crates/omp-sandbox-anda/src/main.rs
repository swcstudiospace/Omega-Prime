//! omp-sandbox-anda: Rust wrapper around the TS core.
//! stdin : {"id":"1","tool":"run_gsd_stage","args":{...}}   (one per line)
//! stdout: core responses/events, each stamped with "attestation":"mock"
//! `--list-tools` prints the tool surface.
mod attestation;
mod core_child;
mod tools;

use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::sync::mpsc::RecvTimeoutError;
use std::thread;
use std::time::Duration;

fn emit(v: Value) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{}", attestation::stamp(v));
    let _ = out.flush();
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--list-tools") {
        let list: Vec<Value> = tools::TOOLS.iter().map(|(n, d)| json!({"name": n, "description": d})).collect();
        emit(json!({"tools": list, "features": {"anda": cfg!(feature = "anda"), "tee": cfg!(feature = "tee")}}));
        return;
    }
    let root = std::env::var("OMP_SANDBOX_ROOT").map(PathBuf::from).unwrap_or_else(|_| std::env::current_dir().unwrap());
    let mut core = match core_child::CoreChild::spawn(&root) {
        Ok(c) => c,
        Err(e) => {
            emit(json!({"event": "fatal", "error": format!("spawn TS core: {e}")}));
            std::process::exit(1);
        }
    };
    let child_pid = core.child.id();
    emit(json!({"event": "wrapper_ready", "wrapper_pid": std::process::id(), "core_pid": child_pid}));

    // stdin reader thread -> channel, so the main loop can multiplex core output and requests.
    let (tx, rx) = std::sync::mpsc::channel::<Option<String>>();
    thread::spawn(move || {
        for line in std::io::stdin().lock().lines().map_while(Result::ok) {
            if tx.send(Some(line)).is_err() { return; }
        }
        let _ = tx.send(None);
    });

    let mut stdin_open = true;
    loop {
        // Drain core output.
        loop {
            match core.lines.recv_timeout(Duration::from_millis(20)) {
                Ok(l) => match serde_json::from_str::<Value>(&l) {
                    Ok(v) => emit(v),
                    Err(_) => emit(json!({"event": "core_noise", "line": l})),
                },
                Err(RecvTimeoutError::Timeout) => break,
                Err(RecvTimeoutError::Disconnected) => {
                    let status = core.child.wait().ok().and_then(|s| s.code());
                    emit(json!({"event": "core_exit", "code": status}));
                    return;
                }
            }
        }
        if !stdin_open { continue; }
        match rx.try_recv() {
            Ok(Some(line)) => {
                if line.trim().is_empty() { continue; }
                let req: Value = match serde_json::from_str(&line) {
                    Ok(v) => v,
                    Err(e) => { emit(json!({"ok": false, "error": format!("invalid json: {e}")})); continue; }
                };
                let id = req.get("id").cloned().unwrap_or(Value::Null);
                let tool = req.get("tool").and_then(|t| t.as_str()).unwrap_or("");
                if !tools::is_tool(tool) {
                    emit(json!({"id": id, "ok": false, "error": format!("unknown tool {tool:?}"), "code": "unknown_tool"}));
                    continue;
                }
                let fwd = json!({"id": id, "op": tool, "args": req.get("args").cloned().unwrap_or(json!({}))});
                if let Err(e) = core.send(&fwd.to_string()) {
                    emit(json!({"id": id, "ok": false, "error": format!("core write: {e}")}));
                }
            }
            Ok(None) => {
                // Our stdin closed: ask the core for a clean shutdown, then wait for it to exit.
                stdin_open = false;
                let _ = core.send(&json!({"id": "wrapper-eof", "op": "down", "args": {}}).to_string());
            }
            Err(_) => {}
        }
    }
}

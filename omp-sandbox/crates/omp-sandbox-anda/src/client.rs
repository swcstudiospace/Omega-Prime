//! `omp-sandbox-anda client [--spawn]`: speaks the old wrapper stdio protocol
//! (in: {"id","tool","args"} lines; out: {"id","ok","result"|"error","code"} + events) but every
//! request goes to the Anda engine as a signed `tool_call` (ICP envelope over the SHA3-256 of the
//! body, Ed25519 client key) carrying meta {engine, nonce, ts_ms}. Events are fetched with signed
//! `poll_events` calls. `anda_auth_probe` runs the negative auth tests locally.
use crate::{attestation, keys, tools::{is_tool, now_ms}};
use anda_core::{CONTENT_TYPE_JSON, RPCRequest, RPCResponse, RequestMeta, ToolInput, ToolOutput};
use anda_web3_client::client::Client as Web3Client;
use candid::Principal;
use serde_json::{Map, Value, json};
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::sync::Mutex;

pub struct Conn { pub http: reqwest::Client, pub url: String, pub engine: Principal }

fn emit(v: Value) { use std::io::Write; let mut o = std::io::stdout().lock(); let _ = writeln!(o, "{}", attestation::stamp(v)); let _ = o.flush(); }

pub fn body_for(engine: Option<Principal>, tool: &str, args: Value, nonce: Option<String>, ts: Option<u64>) -> Vec<u8> {
    let mut extra = Map::new();
    if let Some(n) = nonce { extra.insert("nonce".into(), json!(n)); }
    if let Some(t) = ts { extra.insert("ts_ms".into(), json!(t)); }
    let mut input = ToolInput::new(tool.to_string(), args);
    input.meta = Some(RequestMeta { engine, user: None, extra });
    serde_json::to_vec(&RPCRequest { method: "tool_call".into(), params: serde_json::to_vec(&(input,)).unwrap().into() }).unwrap()
}

pub fn fresh_nonce() -> String { let mut b = [0u8; 16]; rand::fill(&mut b); b.iter().map(|x| format!("{x:02x}")).collect() }

/// POST a body, optionally signed. Returns (http status, Ok(tool output) | Err(reason)).
pub async fn post(conn: &Conn, body: Vec<u8>, sign_with: Option<&Web3Client>, sign_body: Option<&[u8]>) -> (u16, Result<Value, String>) {
    let mut headers = http::HeaderMap::new();
    if let Some(s) = sign_with {
        let digest = ic_cose_types::cose::sha3_256(sign_body.unwrap_or(&body));
        match s.sign_envelope(digest).await { Ok(env) => { if let Err(e) = env.to_authorization(&mut headers) { return (0, Err(e)); } } Err(e) => return (0, Err(e.to_string())) }
    }
    let r = match conn.http.post(format!("{}/default", conn.url)).headers(headers).header("content-type", CONTENT_TYPE_JSON).body(body).send().await {
        Ok(r) => r, Err(e) => return (0, Err(format!("http: {e}"))),
    };
    let status = r.status().as_u16();
    let bytes = r.bytes().await.unwrap_or_default();
    if status != 200 { return (status, Err(format!("HTTP {status}: {}", String::from_utf8_lossy(&bytes).chars().take(200).collect::<String>()))); }
    match serde_json::from_slice::<RPCResponse>(&bytes) {
        Ok(Ok(b)) => match serde_json::from_slice::<ToolOutput<Value>>(&b) { Ok(o) => (status, Ok(o.output)), Err(e) => (status, Err(format!("decode output: {e}"))) },
        Ok(Err(e)) => (status, Err(e)),
        Err(e) => (status, Err(format!("decode rpc: {e}"))),
    }
}

pub async fn call(conn: &Conn, me: &Web3Client, tool: &str, args: Value) -> (u16, Result<Value, String>) {
    post(conn, body_for(Some(conn.engine), tool, args, Some(fresh_nonce()), Some(now_ms())), Some(me), None).await
}

/// Negative auth tests against the live engine. Each case reports refused=true/false.
pub async fn auth_probe(conn: &Conn, me: &Web3Client) -> Value {
    let other = keys::ephemeral().await.ok();
    let a = json!({});
    let mut cases = vec![];
    let fresh = |t: &str| body_for(Some(conn.engine), t, json!({}), Some(fresh_nonce()), Some(now_ms()));
    let mut rec = |name: &str, r: (u16, Result<Value, String>), expect_ok: bool| {
        let refused = r.1.is_err();
        cases.push(json!({"case": name, "http": r.0, "refused": refused, "expected": if expect_ok {"allowed"} else {"refused"}, "pass": refused != expect_ok,
            "error": r.1.err().map(|e| e.chars().take(160).collect::<String>())}));
    };
    rec("unsigned (anonymous) tool_call", post(conn, fresh("sandbox_status"), None, None).await, false);
    if let Some(o) = &other { rec("signed by an unknown Ed25519 key", post(conn, fresh("sandbox_status"), Some(o), None).await, false); }
    // Valid JSON, valid meta, but the controller's signature covers a *different* body (sandbox_status),
    // while the request asks for `down`: the envelope's digest must not match.
    let signed = fresh("sandbox_status");
    rec("controller signature over a different body (tampered: down)", post(conn, fresh("down"), Some(me), Some(&signed)).await, false);
    rec("bound to another engine id", post(conn, body_for(Some(Principal::management_canister()), "sandbox_status", a.clone(), Some(fresh_nonce()), Some(now_ms())), Some(me), None).await, false);
    rec("missing nonce/timestamp", post(conn, body_for(Some(conn.engine), "sandbox_status", a.clone(), None, None), Some(me), None).await, false);
    rec("stale timestamp (10 min old)", post(conn, body_for(Some(conn.engine), "sandbox_status", a.clone(), Some(fresh_nonce()), Some(now_ms() - 600_000)), Some(me), None).await, false);
    let once = fresh("sandbox_status");
    let first = post(conn, once.clone(), Some(me), None).await;
    rec("controller, signed, fresh (control)", first, true);
    rec("exact replay of the previous signed request", post(conn, once, Some(me), None).await, false);
    let all = cases.iter().all(|c| c["pass"] == json!(true));
    json!({"ok": true, "result": {"all_pass": all, "cases": cases}})
}

pub async fn run(addr: Option<String>, spawn_server: bool) -> Result<(), String> {
    let me = keys::client().await?;
    let mut server = None;
    let (url, engine, ready) = if spawn_server {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        // The child is tied to us: it watches PARENT_ENV and runs `down` if we die without a graceful shutdown.
        let mut c = tokio::process::Command::new(exe).args(["serve", "--addr", "127.0.0.1:0"]).env(crate::serve::PARENT_ENV, std::process::id().to_string()).stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::inherit()).kill_on_drop(true).spawn().map_err(|e| e.to_string())?;
        let mut lines = BufReader::new(c.stdout.take().unwrap()).lines();
        let mut ready = Value::Null;
        while let Ok(Some(l)) = lines.next_line().await {
            if let Ok(v) = serde_json::from_str::<Value>(&l) { if v["event"] == "anda_ready" { ready = v; break; } }
        }
        if ready.is_null() { return Err("anda server did not become ready".into()); }
        tokio::spawn(async move { while let Ok(Some(_)) = lines.next_line().await {} });
        server = Some(c);
        (ready["addr"].as_str().unwrap().to_string(), Principal::from_text(ready["engine"].as_str().unwrap()).map_err(|e| e.to_string())?, ready)
    } else {
        let url = addr.ok_or("--addr or --spawn required")?;
        let http = reqwest::Client::builder().no_proxy().build().map_err(|e| e.to_string())?;
        let info: Value = http.get(format!("{url}/.well-known/information")).send().await.map_err(|e| e.to_string())?.json().await.map_err(|e| e.to_string())?;
        let e = info.pointer("/engines/0/id").or(info.pointer("/default_engine")).and_then(|v| v.as_str()).ok_or("engine id not advertised")?.to_string();
        (url, Principal::from_text(e).map_err(|e| e.to_string())?, Value::Null)
    };
    let conn = Arc::new(Conn { http: reqwest::Client::builder().no_proxy().build().map_err(|e| e.to_string())?, url, engine });
    emit(json!({"event": "wrapper_ready", "wrapper_pid": std::process::id(), "core_pid": ready.get("core_pid"), "server_pid": ready.get("server_pid"),
        "anda": {"addr": conn.url, "engine": engine.to_text(), "controller": me.get_principal().to_text(), "auth": "ICP signed envelope (Ed25519) + nonce/ts"}}));

    let done = Arc::new(Mutex::new(false));
    let inflight = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    // Event pump: signed long-polls. Ends on core_exit or when the server is gone.
    let (c2, m2, d2) = (conn.clone(), me.clone(), done.clone());
    let mut pump = tokio::spawn(async move {
        let mut unreachable = 0u32;
        loop {
            if *d2.lock().await { break; }
            match call(&c2, &m2, "poll_events", json!({"wait_ms": 5000})).await {
                (_, Ok(v)) => { unreachable = 0; for e in v.pointer("/result/events").and_then(|x| x.as_array()).cloned().unwrap_or_default() {
                    let core_exit = e["event"] == "core_exit"; emit(e); if core_exit { return; }
                } },
                (status, Err(_)) => {
                    if status == 0 { unreachable += 1; if unreachable >= 20 { emit(json!({"event": "anda_unreachable"})); return; } }
                    tokio::time::sleep(std::time::Duration::from_millis(500)).await; if *d2.lock().await { break; }
                }
            }
        }
    });

    let mut stdin = BufReader::new(tokio::io::stdin()).lines();
    let mut downed = false;
    let mut pump_done = false;
    // SIGTERM/SIGINT take the same graceful path as stdin closing: `down` the core, then wait for the server.
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).map_err(|e| e.to_string())?;
    let mut int = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt()).map_err(|e| e.to_string())?;
    loop {
        let line = tokio::select! {
            l = stdin.next_line() => match l { Ok(Some(l)) => l, _ => break },
            _ = &mut pump, if !pump_done => { pump_done = true; break; }
            _ = term.recv() => { emit(json!({"event": "wrapper_signal", "signal": "SIGTERM"})); break; }
            _ = int.recv() => { emit(json!({"event": "wrapper_signal", "signal": "SIGINT"})); break; }
        };
        if line.trim().is_empty() { continue; }
        let req: Value = match serde_json::from_str(&line) { Ok(v) => v, Err(e) => { emit(json!({"ok": false, "error": format!("invalid json: {e}")})); continue; } };
        let id = req.get("id").cloned().unwrap_or(Value::Null);
        let tool = req.get("tool").and_then(|t| t.as_str()).unwrap_or("").to_string();
        let args = req.get("args").cloned().unwrap_or(json!({}));
        if tool == "down" { downed = true; }
        let (c, m, inf) = (conn.clone(), me.clone(), inflight.clone());
        inf.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        tokio::spawn(async move {
            let out = if tool == "anda_auth_probe" { auth_probe(&c, &m).await }
                else if !is_tool(&tool) || tool == "poll_events" { json!({"ok": false, "error": format!("unknown tool {tool:?}"), "code": "unknown_tool"}) }
                else {
                    match call(&c, &m, &tool, args).await {
                        (_, Ok(v)) => v,
                        (status, Err(e)) => json!({"ok": false, "error": e, "code": if status == 401 { "unauthenticated" } else { "anda_error" }}),
                    }
                };
            let mut out = out; if let Some(o) = out.as_object_mut() { o.insert("id".into(), id); }
            emit(out);
            inf.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
        });
    }
    // Let in-flight replies (e.g. `down`) land before exiting.
    for _ in 0..600 { if inflight.load(std::sync::atomic::Ordering::SeqCst) == 0 { break; } tokio::time::sleep(std::time::Duration::from_millis(100)).await; }
    if !downed && !pump_done { let _ = call(&conn, &me, "down", json!({})).await; }
    if !pump_done { let _ = tokio::time::timeout(std::time::Duration::from_secs(120), &mut pump).await; }
    *done.lock().await = true;
    if let Some(mut s) = server {
        match tokio::time::timeout(std::time::Duration::from_secs(60), s.wait()).await {
            Ok(Ok(st)) => emit(json!({"event": "anda_server_exit", "code": st.code()})),
            _ => { let _ = s.kill().await; emit(json!({"event": "server_killed"})); }
        }
    }
    Ok(())
}

//! Async bridge to the TypeScript core (src/core/server.ts, newline JSON over stdio).
//! Responses are matched by id; unsolicited events are queued for `poll_events`.
use serde_json::{Value, json};
use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::process::Stdio;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{Mutex, Notify, oneshot};

pub struct Core {
    stdin: Mutex<ChildStdin>,
    pending: Mutex<HashMap<String, oneshot::Sender<Value>>>,
    events: Mutex<VecDeque<Value>>,
    notify: Notify,
    seq: AtomicU64,
    pub exited: AtomicBool,
    pub exit_notify: Notify,
    pub pid: u32,
}

impl Core {
    pub fn spawn(root: &Path) -> std::io::Result<(Arc<Core>, Child)> {
        let node = std::env::var("OMP_SANDBOX_NODE").unwrap_or_else(|_| format!("{}/tools/node24/bin/node", std::env::var("HOME").unwrap_or_default()));
        let mut child = Command::new(node)
            .args(["--experimental-transform-types", "--no-warnings", "src/core/server.ts"])
            .current_dir(root)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()?;
        let core = Arc::new(Core {
            stdin: Mutex::new(child.stdin.take().expect("stdin")),
            pending: Mutex::new(HashMap::new()), events: Mutex::new(VecDeque::new()), notify: Notify::new(),
            seq: AtomicU64::new(0), exited: AtomicBool::new(false), exit_notify: Notify::new(), pid: child.id().unwrap_or(0),
        });
        let stdout = child.stdout.take().expect("stdout");
        let c = core.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(l)) = lines.next_line().await {
                let v: Value = serde_json::from_str(&l).unwrap_or_else(|_| json!({"event": "core_noise", "line": l}));
                let tx = match v.get("id").and_then(|x| x.as_str()) { Some(id) => c.pending.lock().await.remove(id), None => None };
                match tx { Some(tx) => { let _ = tx.send(v); } None => c.push_event(v).await }
            }
            c.exited.store(true, Ordering::SeqCst);
            for (_, tx) in c.pending.lock().await.drain() { let _ = tx.send(json!({"ok": false, "error": "core exited", "code": "core_exit"})); }
            c.push_event(json!({"event": "core_exit"})).await;
            c.exit_notify.notify_waiters();
        });
        Ok((core, child))
    }

    async fn push_event(&self, v: Value) { self.events.lock().await.push_back(v); self.notify.notify_waiters(); }

    /// Send one op and wait for its response. No timeout: GSD sessions may run for hours.
    pub async fn call(&self, op: &str, args: Value, caller: &str) -> Value {
        if self.exited.load(Ordering::SeqCst) { return json!({"ok": false, "error": "core exited", "code": "core_exit"}); }
        let id = format!("a{}", self.seq.fetch_add(1, Ordering::SeqCst));
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id.clone(), tx);
        let line = json!({"id": id, "op": op, "args": args, "access": {"via": "anda", "caller": caller}}).to_string() + "\n";
        if let Err(e) = self.stdin.lock().await.write_all(line.as_bytes()).await {
            self.pending.lock().await.remove(&id);
            return json!({"ok": false, "error": format!("core write: {e}")});
        }
        rx.await.unwrap_or_else(|_| json!({"ok": false, "error": "core dropped the request"}))
    }

    /// Drain queued events, waiting up to `wait_ms` for the first one.
    pub async fn poll(&self, wait_ms: u64) -> Vec<Value> {
        let notified = self.notify.notified();
        if self.events.lock().await.is_empty() && !self.exited.load(Ordering::SeqCst) {
            let _ = tokio::time::timeout(std::time::Duration::from_millis(wait_ms), notified).await;
        }
        self.events.lock().await.drain(..).collect()
    }
}


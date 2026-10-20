//! Spawns and talks to the TypeScript core (src/core/server.ts) over newline JSON.
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::thread;

pub struct CoreChild {
    pub child: Child,
    stdin: ChildStdin,
    pub lines: Receiver<String>,
}

impl CoreChild {
    pub fn spawn(root: &PathBuf) -> std::io::Result<Self> {
        let node = std::env::var("OMP_SANDBOX_NODE").unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_default();
            format!("{home}/tools/node24/bin/node")
        });
        let mut child = Command::new(node)
            .args(["--experimental-transform-types", "--no-warnings", "src/core/server.ts"])
            .current_dir(root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()?;
        let stdin = child.stdin.take().expect("stdin");
        let stdout = child.stdout.take().expect("stdout");
        let (tx, rx) = channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Ok(Self { child, stdin, lines: rx })
    }

    pub fn send(&mut self, line: &str) -> std::io::Result<()> {
        self.stdin.write_all(line.as_bytes())?;
        self.stdin.write_all(b"\n")?;
        self.stdin.flush()
    }
}

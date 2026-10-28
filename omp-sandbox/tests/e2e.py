#!/usr/bin/env python3
"""Real end-to-end run: stdio client -> signed Anda tool_call -> anda_engine_server (Rust) -> TS core -> agentOS VM -> sandbox tier -> omp RPC + GSD-Core.
Drives the wrapper over stdin/stdout JSON lines. Every line in/out is logged to .state/e2e/<ts>.jsonl.
Real inference (Sol plan, Luna execute) through omp's auth-broker; no secrets are printed."""
import json, os, subprocess, sys, threading, time, queue, glob, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TS = time.strftime("%Y%m%d-%H%M%S"); T0 = time.time()
os.makedirs(f"{ROOT}/.state/e2e", exist_ok=True)
LOG = open(f"{ROOT}/.state/e2e/{TS}.jsonl", "w")
BIN = f"{ROOT}/target/release/omp-sandbox-anda"
FAST = "--fast" in sys.argv  # skip the execute stage
ABORT_ONLY = "--abort-only" in sys.argv  # up -> abort stage -> down

def log(kind, obj):
    LOG.write(json.dumps({"t": time.strftime("%H:%M:%S"), "kind": kind, "obj": obj}) + "\n"); LOG.flush()

p = subprocess.Popen([BIN], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(f"{ROOT}/.state/e2e/{TS}.stderr", "w"), text=True, bufsize=1)
q: "queue.Queue[dict]" = queue.Queue()
def reader():
    for line in p.stdout:
        try: o = json.loads(line)
        except Exception: o = {"raw": line}
        log("out", o); q.put(o)
    q.put({"event": "__eof__"})
threading.Thread(target=reader, daemon=True).start()

pending_events = []
n = 0
def call(tool, args=None, timeout=900):
    global n; n += 1; rid = f"r{n}"
    msg = {"id": rid, "tool": tool, "args": args or {}}; log("in", msg)
    p.stdin.write(json.dumps(msg) + "\n"); p.stdin.flush()
    end = time.time() + timeout
    while time.time() < end:
        try: o = q.get(timeout=5)
        except queue.Empty: continue
        if o.get("id") == rid: return o
        pending_events.append(o)
        if o.get("event") == "__eof__": raise SystemExit("wrapper exited")
    raise TimeoutError(tool)

def next_event(pred, timeout=None):
    end = None if timeout is None else time.time() + timeout
    while end is None or time.time() < end:
        for i, e in enumerate(pending_events):
            if pred(e): return pending_events.pop(i)
        try: pending_events.append(q.get(timeout=5))
        except queue.Empty: pass
    raise TimeoutError("event")

R = {"ts": TS}
def check(name, cond, detail=None):
    R.setdefault("checks", []).append({"check": name, "ok": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail is not None else ""), flush=True)

ready = next_event(lambda e: e.get("event") == "wrapper_ready", 30)
check("rust wrapper launched TS child", ready.get("core_pid"), {"wrapper_pid": ready.get("wrapper_pid"), "core_pid": ready.get("core_pid")})
anda = ready.get("anda") or {}
check("Anda engine server up on loopback (real anda_engine)", ready.get("server_pid") and str(anda.get("addr", "")).startswith("http://127.0.0.1:") and anda.get("engine"),
      {"server_pid": ready.get("server_pid"), "addr": anda.get("addr"), "engine": anda.get("engine"), "controller": anda.get("controller"), "auth": anda.get("auth")})

up = call("up", {"seed": "tests/fixtures/demo"}, 900)
u = up.get("result", {})
check("up ok", up.get("ok"), up.get("error"))
check("agentOS VM booted + sandbox round trip", u.get("roundTrip", {}).get("match") and u.get("roundTrip", {}).get("hostFnExit") == 0, {"bootMs": u.get("agentosBootMs"), "hostFn": u.get("roundTrip", {}).get("hostFnStdout", "")[:120]})
check("sandbox tier", u.get("tier"), {"tier": u.get("tier"), "skipped": u.get("skippedTiers")})
check("omp RPC lists GSD-Core 1.18.0 commands", (u.get("omp") or {}).get("gsdCommands", 0) >= 60, u.get("omp"))
check("fixture seeded via VM mount + git in sandbox", (u.get("seeded") or {}).get("gitExit") == 0, u.get("seeded"))
check("up attestation=mock", up.get("attestation") == "mock" and u.get("attestation") == "mock")
acc = up.get("access") or {}
check("up reached the sandbox through Anda (signed, controller principal)", acc.get("auth") == "ICP signed envelope (Ed25519)" and acc.get("caller") == anda.get("controller") and acc.get("engine") == anda.get("engine") and str(up.get("tee", "")).startswith("none"), acc)
# Negative auth against the live engine, with the real sandbox behind it.
probe = call("anda_auth_probe", {}, 120)
R["auth_probe"] = probe
cases = (probe.get("result") or {}).get("cases") or []
check("unauthenticated request through Anda refused", any(c["case"].startswith("unsigned") and c["refused"] for c in cases), [c for c in cases if c["case"].startswith("unsigned")])
check("Anda auth probe: every bad request refused, controller allowed", (probe.get("result") or {}).get("all_pass") and len(cases) >= 8,
      [{"case": c["case"], "http": c["http"], "refused": c["refused"], "pass": c["pass"]} for c in cases])
owned0 = call("sandbox_status").get("result", {}).get("owned") or {}
hostroot = owned0.get("hostRoot")
osbox = owned0.get("sandbox") if u.get("tier") == "openshell-podman" else None  # OpenShell sandbox name
OPENSHELL = os.path.expanduser("~/.local/opt/openshell/0.1.4/bin/openshell")
def sbx(cmd):
    """Run a shell command inside the OpenShell sandbox's workdir (host-side check, not via the wrapper)."""
    r = subprocess.run([OPENSHELL, "sandbox", "exec", "-n", osbox, "--no-tty", "--no-login-shell", "--workdir", "/sandbox/work", "--", "sh", "-c", cmd],
                       capture_output=True, text=True, env={**os.environ, "OPENSHELL_TELEMETRY_ENABLED": "false"})
    return r.stdout
if osbox:
    check("tier is OpenShell on rootless Podman", (owned0.get("info") or {}).get("podman", {}).get("rootless") is True and len((owned0.get("info") or {}).get("containers") or []) == 2,
          {k: (owned0.get("info") or {}).get(k) for k in ("podman", "gateway", "containers")})

def run_stage(stage, cmd, task, timeout, on_question=None, abort_after=None):
    r = call("run_gsd_stage", {"stage": stage, "gsd_command": cmd, "task": task}, 60)
    sid = r["result"]["stageId"]; t0 = time.time(); aborted = False
    while True:
        try:
            e = next_event(lambda e: e.get("stageId") == sid or e.get("event") == "__eof__", 10 if abort_after else timeout)
        except TimeoutError:
            e = {}
        if e.get("event") == "__eof__": raise SystemExit("wrapper exited mid-stage")
        if e.get("event") == "question" and on_question: on_question(sid, e)
        elif e.get("event") == "question":
            call("answer_question", {"stage_id": sid, "request_id": e["requestId"]})
        elif e.get("event") == "stage_done": break
        if abort_after and not aborted and time.time() - t0 > abort_after:
            aborted = True; R["abort_call"] = call("abort", {"stage_id": sid}, 120)
    return sid, e, call("sandbox_status", {"stage_id": sid})["result"]

if ABORT_ONLY: FAST = True
# --- plan stage on Sol with a real ask-dialog question + stale answer
qa = {}
def on_q(sid, e):
    qs = e.get("questions") or []
    if qa or not any(x.get("id") == "lang" for x in qs):
        R.setdefault("other_questions", []).append(e)
        call("answer_question", {"stage_id": sid, "request_id": e["requestId"]}); return
    opts = [o.get("label") for o in (qs[0].get("options") or [])] if qs else e.get("options")
    pick = next((o for o in opts or [] if o and "french" in o.lower()), (opts or [None])[0])
    a = call("answer_question", {"stage_id": sid, "request_id": e["requestId"], "selected": [pick] if pick else None})
    stale = call("answer_question", {"stage_id": sid, "request_id": e["requestId"], "selected": [pick] if pick else None})
    bogus = call("answer_question", {"stage_id": sid, "request_id": "not-a-real-request"})
    qa.update({"question": qs[0].get("question") if qs else e.get("title"), "options": opts, "picked": pick, "answer": a, "stale": stale, "bogus": bogus, "method": e.get("method")})

PLAN_TASK = """1 --skip-research

Disposable e2e fixture (.planning/ already seeded; phase 1 = Greet CLI).
FIRST, before reading or planning anything, call the `ask` tool exactly once with ONE question:
  id "lang", question "Which language should greet.sh greet in?", options "English" and "French", recommended "English".
Then plan phase 1 using the chosen language (e.g. French -> `Bonjour, NAME !`; adjust GREET-01 text accordingly).
Exactly one plan (01-01). No research, no plan-checker loop, no further questions of any kind (never ask in plain text).
Commit the plan locally. Never push, never open a PR, no network installs."""
if not ABORT_ONLY:
    sid, done, st = run_stage("plan", "gsd-plan-phase", PLAN_TASK, None, on_question=on_q)
    R["plan"] = {"stageId": sid, "done": done, "status": st}
    check("plan stage completed", st["status"] == "completed", {"status": st["status"], "error": st.get("error")})
    check("plan stage model = openai-codex/gpt-6.1-sol", "openai-codex/gpt-6.1-sol" in st["modelsSeen"], st["modelsSeen"])
    check("real ask-dialog question answered", qa.get("answer", {}).get("ok"), {k: qa.get(k) for k in ("method", "question", "options", "picked")})
    check("stale answer rejected", qa.get("stale", {}).get("ok") is False and qa["stale"].get("code") == "stale_answer", qa.get("stale"))
    check("unknown request id rejected", qa.get("bogus", {}).get("ok") is False and qa["bogus"].get("code") == "stale_answer", qa.get("bogus"))
    if hostroot:
        plans = glob.glob(f"{hostroot}/work/.planning/phases/*/*PLAN*.md")
        check("plan file written in sandbox", plans, [p.replace(hostroot, "<ws>") for p in plans])
    elif osbox:
        plans = [l for l in sbx("ls .planning/phases/*/*PLAN*.md 2>/dev/null").splitlines() if l]
        check("plan file written in sandbox", plans, plans)

if not FAST:
    EXEC_TASK = """1

Disposable e2e fixture. Execute phase 1's plan exactly. Inline execution is fine. No questions of any kind.
Commit locally. Never push, never open a PR, no network installs."""
    sid, done, st = run_stage("execute", "gsd-execute-phase", EXEC_TASK, None)
    R["execute"] = {"stageId": sid, "done": done, "status": st}
    check("execute stage completed", st["status"] == "completed", {"status": st["status"], "error": st.get("error")})
    check("execute stage model = openai-codex/gpt-6-luna", "openai-codex/gpt-6-luna" in st["modelsSeen"], st["modelsSeen"])
    if hostroot:
        w = f"{hostroot}/work"
        out = subprocess.run(["sh", "-c", "sh ./greet.sh Ada; echo rc=$?; sh ./greet.sh >/dev/null 2>&1; echo rc_noarg=$?; git log --oneline | head -8"], cwd=w, capture_output=True, text=True)
        check("greet.sh works (executed by Luna in sandbox)", "Ada" in out.stdout and "rc=0" in out.stdout, out.stdout.strip().splitlines())
    elif osbox:
        out = sbx("sh ./greet.sh Ada; echo rc=$?; sh ./greet.sh >/dev/null 2>&1; echo rc_noarg=$?; git log --oneline | head -8")
        check("greet.sh works (executed by Luna in sandbox)", "Ada" in out and "rc=0" in out, out.strip().splitlines())

# --- abort / controlled failure
ABORT_TASK = "Disposable. Run `sleep 240` with the bash tool, then reply DONE. No questions."
sid, done, st = run_stage("execute", "gsd-fast", ABORT_TASK, None, abort_after=25)
R["abort"] = {"stageId": sid, "done": done, "status": st}
check("abort mid-run -> status aborted", st["status"] == "aborted", {"status": st["status"], "abortCall": R.get("abort_call")})

def procs_naming(ids):
    """Pids (never argv) of live processes whose command line contains one of the non-empty literal `ids`.
    No ids means nothing to match: an empty pattern must not match every process on the host."""
    ids = [i for i in ids if i]
    if not ids: return []
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == os.getpid(): continue
        try: cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError: continue
        if any(i in cmd for i in ids): out.append(d)
    return out

assert procs_naming([None, ""]) == [], "empty identifiers match nothing"

down = call("down", {}, 300)
R["down"] = down
check("down: no leftovers", down.get("ok") and down["result"].get("leftovers") == [], down.get("result"))
try: p.wait(30)
except Exception: p.kill()
check("wrapper exited", p.poll() is not None, p.returncode)
# Only processes this run owns: the tier's recorded pids, plus anything naming this run's sandbox.
# (Other agents' host omp sessions on the shared box are not ours and are ignored.)
ob = (down.get("result") or {}).get("ownedBefore") or {}
ownpids = [x for x in [ob.get("gatewayPid"), ob.get("podmanServicePid"), ob.get("relayPid"), ob.get("forwardPid"), *(ob.get("children") or []), ready.get("core_pid"), ready.get("wrapper_pid"), ready.get("server_pid")] if x]
alive = [str(x) for x in ownpids if os.path.exists(f"/proc/{x}")]
named = procs_naming([x for x in (osbox, hostroot) if x])
ps = " ".join(alive + [f"pid {x} (names this sandbox)" for x in named])  # pids only, never argv (it can carry tokens)
check("no sandbox/omp/broker processes left", ps == "", {"checkedPids": ownpids, "alive": ps})
check("workspace dir removed", not hostroot or not os.path.exists(hostroot), hostroot)
if osbox:
    ctr = subprocess.run(["sh", "-c", f"podman ps -a --format '{{{{.Names}}}}' | grep -E 'openshell' || true; podman volume ls --format '{{{{.Name}}}}' | grep -F '{osbox}' || true"], capture_output=True, text=True).stdout.strip()
    check("no OpenShell podman containers/volumes left", ctr == "", ctr.splitlines())
recs = sorted([r for r in glob.glob(f"{ROOT}/.state/receipts/*.json") if os.path.getmtime(r) >= T0 - 1], key=os.path.getmtime)  # this run only
bad = [r for r in recs if json.load(open(r)).get("attestation") != "mock"]
check("every receipt attestation=mock", recs and not bad, [os.path.basename(r) for r in recs])
noacc = [os.path.basename(r) for r in recs if (json.load(open(r)).get("access") or {}).get("caller") != anda.get("controller")]
check("receipts record Anda access (via anda, caller = controller principal)", recs and not noacc, noacc)
lines = open(LOG.name).read()
check("no broker token in transcript", not re.search(r"OMP_AUTH_BROKER_TOKEN\"?\s*[:=]\s*\"?[A-Za-z0-9_\-]{20,}", lines))
json.dump(R, open(f"{ROOT}/.state/e2e/{TS}.summary.json", "w"), indent=2, default=str)
failed = any(not c["ok"] for c in R["checks"])
print("summary:", f".state/e2e/{TS}.summary.json", "FAILED" if failed else "ALL PASS")
sys.exit(1 if failed else 0)

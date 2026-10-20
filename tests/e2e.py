#!/usr/bin/env python3
"""Real end-to-end run: Rust wrapper -> TS core -> agentOS VM -> sandbox tier -> omp RPC + GSD-Core.
Drives the wrapper over stdin/stdout JSON lines. Every line in/out is logged to .state/e2e/<ts>.jsonl.
Real inference (Sol plan, Luna execute) through omp's auth-broker; no secrets are printed."""
import json, os, subprocess, sys, threading, time, queue, glob, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TS = time.strftime("%Y%m%d-%H%M%S")
os.makedirs(f"{ROOT}/.state/e2e", exist_ok=True)
LOG = open(f"{ROOT}/.state/e2e/{TS}.jsonl", "w")
BIN = f"{ROOT}/target/release/omp-sandbox-anda"
FAST = "--fast" in sys.argv  # skip the execute stage

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

def next_event(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
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

up = call("up", {"seed": "tests/fixtures/demo"}, 900)
u = up.get("result", {})
check("up ok", up.get("ok"), up.get("error"))
check("agentOS VM booted + sandbox round trip", u.get("roundTrip", {}).get("match") and u.get("roundTrip", {}).get("hostFnExit") == 0, {"bootMs": u.get("agentosBootMs"), "hostFn": u.get("roundTrip", {}).get("hostFnStdout", "")[:120]})
check("sandbox tier", u.get("tier"), {"tier": u.get("tier"), "skipped": u.get("skippedTiers")})
check("omp RPC lists GSD-Core 1.18.0 commands", (u.get("omp") or {}).get("gsdCommands", 0) >= 60, u.get("omp"))
check("fixture seeded via VM mount + git in sandbox", (u.get("seeded") or {}).get("gitExit") == 0, u.get("seeded"))
check("up attestation=mock", up.get("attestation") == "mock" and u.get("attestation") == "mock")
hostroot = (call("sandbox_status").get("result", {}).get("owned") or {}).get("hostRoot")

def run_stage(stage, cmd, task, timeout, on_question=None, abort_after=None):
    r = call("run_gsd_stage", {"stage": stage, "gsd_command": cmd, "task": task}, 60)
    sid = r["result"]["stageId"]; t0 = time.time(); aborted = False
    while True:
        e = next_event(lambda e: e.get("stageId") == sid or e.get("event") == "__eof__", timeout)
        if e.get("event") == "question" and on_question: on_question(sid, e)
        elif e.get("event") == "question":
            call("answer_question", {"stage_id": sid, "request_id": e["requestId"]})
        elif e.get("event") == "stage_done": break
        if abort_after and not aborted and time.time() - t0 > abort_after:
            aborted = True; R["abort_call"] = call("abort", {"stage_id": sid}, 120)
    return sid, e, call("sandbox_status", {"stage_id": sid})["result"]

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
sid, done, st = run_stage("plan", "gsd-plan-phase", PLAN_TASK, 2400, on_question=on_q)
R["plan"] = {"stageId": sid, "done": done, "status": st}
check("plan stage completed", st["status"] == "completed", {"status": st["status"], "error": st.get("error")})
check("plan stage model = openai-codex/gpt-6.1-sol", "openai-codex/gpt-6.1-sol" in st["modelsSeen"], st["modelsSeen"])
check("real ask-dialog question answered", qa.get("answer", {}).get("ok"), {k: qa.get(k) for k in ("method", "question", "options", "picked")})
check("stale answer rejected", qa.get("stale", {}).get("ok") is False and qa["stale"].get("code") == "stale_answer", qa.get("stale"))
check("unknown request id rejected", qa.get("bogus", {}).get("ok") is False and qa["bogus"].get("code") == "stale_answer", qa.get("bogus"))
if hostroot:
    plans = glob.glob(f"{hostroot}/work/.planning/phases/*/*PLAN*.md")
    check("plan file written in sandbox", plans, [p.replace(hostroot, "<ws>") for p in plans])

if not FAST:
    EXEC_TASK = """1

Disposable e2e fixture. Execute phase 1's plan exactly. Inline execution is fine. No questions of any kind.
Commit locally. Never push, never open a PR, no network installs."""
    sid, done, st = run_stage("execute", "gsd-execute-phase", EXEC_TASK, 2400)
    R["execute"] = {"stageId": sid, "done": done, "status": st}
    check("execute stage completed", st["status"] == "completed", {"status": st["status"], "error": st.get("error")})
    check("execute stage model = openai-codex/gpt-6-luna", "openai-codex/gpt-6-luna" in st["modelsSeen"], st["modelsSeen"])
    if hostroot:
        w = f"{hostroot}/work"
        out = subprocess.run(["sh", "-c", "sh ./greet.sh Ada; echo rc=$?; sh ./greet.sh >/dev/null 2>&1; echo rc_noarg=$?; git log --oneline | head -8"], cwd=w, capture_output=True, text=True)
        check("greet.sh works (executed by Luna in sandbox)", "Ada" in out.stdout and "rc=0" in out.stdout, out.stdout.strip().splitlines())

# --- abort / controlled failure
ABORT_TASK = "Disposable. Run `sleep 240` with the bash tool, then reply DONE. No questions."
sid, done, st = run_stage("execute", "gsd-fast", ABORT_TASK, 600, abort_after=25)
R["abort"] = {"stageId": sid, "done": done, "status": st}
check("abort mid-run -> status aborted", st["status"] == "aborted", {"status": st["status"], "abortCall": R.get("abort_call")})

down = call("down", {}, 300)
R["down"] = down
check("down: no leftovers", down.get("ok") and down["result"].get("leftovers") == [], down.get("result"))
try: p.wait(30)
except Exception: p.kill()
check("wrapper exited", p.poll() is not None, p.returncode)
ps = subprocess.run(["sh", "-c", "ps -eo pid,args | grep -E 'sandbox-agent server|bwrap|omp --mode rpc|auth-broker serve' | grep -v grep || true"], capture_output=True, text=True).stdout.strip()
check("no sandbox/omp/broker processes left", ps == "", ps.splitlines())
check("workspace dir removed", not hostroot or not os.path.exists(hostroot), hostroot)
recs = sorted(glob.glob(f"{ROOT}/.state/receipts/*.json"), key=os.path.getmtime)[-6:]
bad = [r for r in recs if json.load(open(r)).get("attestation") != "mock"]
check("every receipt attestation=mock", recs and not bad, [os.path.basename(r) for r in recs])
lines = open(LOG.name).read()
check("no broker token in transcript", not re.search(r"OMP_AUTH_BROKER_TOKEN\"?\s*[:=]\s*\"?[A-Za-z0-9_\-]{20,}", lines))
json.dump(R, open(f"{ROOT}/.state/e2e/{TS}.summary.json", "w"), indent=2, default=str)
print("summary:", f".state/e2e/{TS}.summary.json", "FAILED" if any(not c["ok"] for c in R["checks"]) else "ALL PASS")

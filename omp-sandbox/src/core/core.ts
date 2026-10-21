// The TS core: auth-broker -> sandbox tier -> agentOS VM -> GSD-Core -> omp RPC stages.
import { chmodSync, mkdirSync, writeFileSync, readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { join } from "node:path";
import { EventEmitter } from "node:events";
import { STATE, STAGE_MODELS, MAGIC, OMP_BIN, VERSIONS, aedt, stageRoute, type Stage } from "./config.ts";
import { AuthBroker } from "./auth.ts";
import { startTier } from "./providers/tiers.ts";
import type { SandboxTier } from "./providers/types.ts";
import { bootVm, vmRoundTrip, SANDBOX_MOUNT } from "./vm.ts";
import { ensureGsd } from "./gsd.ts";
import { OmpRpcClient, StaleAnswerError, textOf, type AnswerInput, type PendingQuestion } from "./omp-rpc.ts";
import { mockAttestation, type AttestationDoc } from "./attest.ts";
import { redactSecrets, rid, execCapture } from "./providers/util.ts";
import { allowedDir, seedFiles, PathRefused, SEED_ROOTS, WORKSPACE_ROOTS } from "./paths.ts";
import { repoSeedScript, CHECK_BINS, SANDBOX_PYDEPS, type SeedRepo } from "./repo.ts";

export type StageStatus = "starting" | "running" | "waiting" | "completed" | "failed" | "aborted";
const TERMINAL: readonly StageStatus[] = ["completed", "failed", "aborted"];
type StageRec = {
  id: string; stage: Stage; plan: string; fallbackModel: string | null; command: string; modelRequested: string; status: StageStatus;
  client: OmpRpcClient | null; startedAt: string; endedAt?: string; error?: string; fallbackFrom?: string;
  questions: Array<{ requestId: string; asked: string; answered?: AnswerInput; answeredAt?: string; method: string }>;
  lastText: string; done: Promise<void>;
};


type UpOpts = { tiers?: string[]; seed?: string; workspace_host_path?: string; seed_repo?: SeedRepo; py_deps?: string; stage?: Array<{ from: string; to: string }> };
export class Core extends EventEmitter {
  broker = new AuthBroker();
  tier: (SandboxTier & { hostRoot?: string }) | null = null;
  skipped: Array<{ tier: string; reason: string }> = [];
  vm: any = null;
  attestation: AttestationDoc | null = null;
  /** Who reached the core: set per request by the Anda access layer ({via:"anda", caller:<principal>}). */
  access: { via: string; caller: string } | null = null;
  up_: Record<string, unknown> = {};
  stages = new Map<string, StageRec>();
  #receipts = join(STATE, "receipts");

  /** Set when `up` seeded a real repo (seed_repo): host path, ref, and the sandbox-side base commit. */
  repo: { path: string; ref: string; base: string } | null = null;
  #starting: Promise<Record<string, unknown>> | null = null;
  #ready = false;

  /**
   * Bring the sandbox up once. Concurrent callers share one startup; a failed startup tears down everything it
   * acquired (broker, tier, VM) so the next `up` retries from scratch. Ready only after every check passed.
   *
   * `workspace_host_path`: an absolute host directory under an allowlisted root (WORKSPACE_ROOTS), bind-mounted
   * read-write at the tier's workdir (/sandbox/work on openshell-podman). Changes land directly in that host tree.
   * `seed`: an absolute fixture directory under SEED_ROOTS, copied (binary-safe, no .git/.venv/node_modules, no
   * symlinks) into an empty workdir. `seed_repo`: a host git repo + ref sent as a git bundle and checked out in the
   * sandbox workdir (results come back with export_commits). The three are mutually exclusive. Paths are checked before anything starts.
   */
  up(opts: UpOpts = {}): Promise<Record<string, unknown>> {
    if (this.#ready) return Promise.resolve(this.up_);
    if (!this.#starting) this.#starting = this.#up(opts).finally(() => { this.#starting = null; });
    return this.#starting;
  }

  /** Test seam: treat an already-started tier as up (no broker, VM or probes). Not reachable over the API. */
  attachTierForTests(tier: SandboxTier & { hostRoot?: string }) { this.tier = tier; this.#ready = true; this.up_ = { tier: tier.name }; }

  async #up(opts: UpOpts) {
    if ([opts.seed, opts.workspace_host_path, opts.seed_repo].filter((x) => x !== undefined).length > 1) throw new PathRefused("seed, seed_repo and workspace_host_path are mutually exclusive");
    const seedRepo = opts.seed_repo !== undefined ? { ...opts.seed_repo, path: allowedDir(opts.seed_repo.path, WORKSPACE_ROOTS, "seed_repo.path") } : null;
    const workspace = opts.workspace_host_path !== undefined ? allowedDir(opts.workspace_host_path, WORKSPACE_ROOTS, "workspace_host_path") : null;
    const seedSrc = opts.seed !== undefined ? allowedDir(opts.seed, SEED_ROOTS, "seed") : null;
    mkdirSync(this.#receipts, { recursive: true, mode: 0o700 });
    try {
      await this.broker.start();
      const { tier, skipped } = await startTier(this.broker.env, opts.tiers as any, { workspaceHostPath: workspace });
      this.tier = tier as any; this.skipped = skipped;
      const { vm, bootMs } = await bootVm(tier); this.vm = vm;
      const roundTrip = await vmRoundTrip(vm, tier);
      if (!roundTrip.match || roundTrip.hostFnExit !== 0) throw new Error(`agentOS VM <-> sandbox round trip failed (match=${roundTrip.match}, hostFnExit=${roundTrip.hostFnExit})`);
      const gsd = await ensureGsd(tier as any);
      const seeded = seedRepo ? await this.#seedRepo(seedRepo) : seedSrc ? await this.#seed(seedSrc) : null;
      if (seeded && "gitExit" in seeded && seeded.gitExit !== 0) throw new Error(`seeding failed: git exit ${seeded.gitExit}`);
      const pyDeps = opts.py_deps ? await this.#stagePyDeps(opts.py_deps) : null;
      const staged = [] as unknown[]; for (const st of opts.stage ?? []) staged.push(await this.#stageDir(st));
      const probe = await this.#probeOmp();
      this.attestation = mockAttestation(tier.name, { isolation: tier.isolation });
      this.up_ = { tier: tier.name, isolation: tier.isolation, skippedTiers: skipped, agentosBootMs: bootMs, roundTrip, gsd, seeded, pyDeps, staged, workspace: workspace ? { hostPath: workspace, sandboxPath: tier.workdir, mode: "bind-rw" } : null, omp: probe, attestation: "mock", attestationDoc: this.attestation };
      this.#ready = true;
      this.#receipt(`up-${tier.id}`, this.up_);
      return this.up_;
    } catch (e) {
      const { leftovers } = await this.#teardown();
      if (leftovers.length) (e as any).leftovers = leftovers;
      throw e;
    }
  }

  /** Copy a fixture project (already allowlist-resolved) into the sandbox workdir THROUGH the agentOS VM mount as raw bytes, then git-init it inside the sandbox. */
  async #seed(src: string) {
    const t = this.tier!;
    const rel = t.workdir.slice(t.sandboxRoot.length).replace(/^\/+/, "");
    const { files, skipped } = seedFiles(src);
    for (const f of files) {
      const dir = f.rel.includes("/") ? f.rel.slice(0, f.rel.lastIndexOf("/")) : "";
      if (dir) await this.vm.filesystem.mkdir(`${SANDBOX_MOUNT}/${rel}/${dir}`, { recursive: true }).catch(() => {});
      await this.vm.filesystem.writeFile(`${SANDBOX_MOUNT}/${rel}/${f.rel}`, new Uint8Array(f.data));
    }
    const g = await t.exec(["sh", "-c", "git init -q && git add -A && git -c user.name=omp-sandbox -c user.email=omp-sandbox@local commit -qm 'seed: disposable e2e fixture' && git log --oneline | head -1"], { timeoutMs: 60_000 });
    return { fixture: src, files: files.map((f) => f.rel), bytes: files.reduce((n, f) => n + f.data.length, 0), skipped, viaVm: true, git: g.stdout.trim(), gitExit: g.code };
  }


  /** Seed a real git repo: host `git bundle` -> base64 over the authenticated exec stdin -> fetch + checkout in the sandbox workdir.
   *  Nothing on the host is mounted; the sandbox only ever sees this one ref. */
  async #seedRepo(s: SeedRepo) {
    const t = this.tier!;
    if (!s?.path || !s?.ref || !/^[A-Za-z0-9._\/-]{1,200}$/.test(s.ref)) throw new Error("seed_repo needs {path, ref} with a plain ref name");
    mkdirSync(join(STATE, "seeds"), { recursive: true });
    const bundle = join(STATE, "seeds", `${t.id}.bundle`);
    const b = await execCapture("git", ["-C", s.path, "bundle", "create", bundle, s.ref], { timeoutMs: 300_000 });
    if (b.code !== 0) throw new Error(`git bundle failed: ${b.stderr.slice(-400)}`);
    await this.#put(bundle, "/sandbox/home/seed.bundle");
    const g = await t.exec(["sh", "-c", repoSeedScript, "sh", s.ref, s.authorName ?? "omp-sandbox", s.authorEmail ?? "omp-sandbox@local"], { timeoutMs: 300_000 });
    if (g.code !== 0) throw new Error(`seed checkout failed (exit ${g.code}): ${g.stderr.slice(-400)}`);
    const [base, dirty] = g.stdout.trim().split("\n").slice(-2);
    this.repo = { path: s.path, ref: s.ref, base: base.trim() };
    return { repo: s.path, ref: s.ref, base: base.trim(), bundleBytes: statSync(bundle).size, untrackedAfterSeed: Number(dirty), via: "git bundle via openshell sandbox upload (verified in-sandbox)" };
  }

  /** Host file -> sandbox path: OpenShell upload (mTLS gateway) when the tier has it, else exec stdin (small files only). */
  async #put(local: string, remote: string) {
    const t = this.tier as any;
    if (typeof t.upload === "function") {
      const r = await t.upload(local, remote);
      if (r.code !== 0) throw new Error(`upload ${remote} failed: ${(r.stderr || r.stdout).slice(-400)}`);
      return;
    }
    const w = await t.exec(["sh", "-c", 'base64 -d > "$1"', "sh", remote], { input: readFileSync(local).toString("base64"), timeoutMs: 600_000 });
    if (w.code !== 0) throw new Error(`stdin copy ${remote} failed: ${w.stderr.slice(-300)}`);
  }

  /** Stage pure-Python test deps (pre-extracted wheels, a host dir) into the sandbox; PyPI is not in the egress policy. */
  async #stagePyDeps(dir: string) {
    const t = this.tier!;
    if (!existsSync(dir)) throw new Error(`py_deps dir not found: ${dir}`);
    const tgz = join(STATE, "seeds", `${t.id}-pydeps.tgz`); mkdirSync(join(STATE, "seeds"), { recursive: true });
    const tar = await execCapture("tar", ["-C", dir, "-czf", tgz, "."], { timeoutMs: 300_000 });
    if (tar.code !== 0) throw new Error(`py_deps tar failed: ${tar.stderr.slice(-300)}`);
    await this.#put(tgz, "/sandbox/home/pydeps.tgz");
    const x = await t.exec(["sh", "-c", `mkdir -p ${SANDBOX_PYDEPS} && tar -xzf /sandbox/home/pydeps.tgz -C ${SANDBOX_PYDEPS} && rm -f /sandbox/home/pydeps.tgz && echo 'export PYTHONPATH=${SANDBOX_PYDEPS}' >> /sandbox/home/.omp-sandbox.env && python3 -c 'import sys; sys.path.insert(0, "${SANDBOX_PYDEPS}"); import pytest, starlette, httpx; print(sys.version.split()[0], pytest.__version__)'`], { timeoutMs: 300_000 });
    if (x.code !== 0) throw new Error(`py_deps staging failed: ${x.stderr.slice(-400)}`);
    return { dir: SANDBOX_PYDEPS, python_pytest: x.stdout.trim() };
  }

  /** Copy a host directory of reference files (read-only inputs such as specs) to /sandbox/home/<name>. */
  async #stageDir(st: { from: string; to: string }) {
    const t = this.tier!;
    if (!existsSync(st.from) || !/^\/sandbox\/home\/[A-Za-z0-9._-]+$/.test(st.to)) throw new Error(`stage: bad from/to (${st.from} -> ${st.to})`);
    const tgz = join(STATE, "seeds", `${t.id}-stage-${st.to.split("/").pop()}.tgz`); mkdirSync(join(STATE, "seeds"), { recursive: true });
    const tar = await execCapture("tar", ["-C", st.from, "-czf", tgz, "."], { timeoutMs: 300_000 });
    if (tar.code !== 0) throw new Error(`stage tar failed: ${tar.stderr.slice(-300)}`);
    await this.#put(tgz, "/sandbox/home/stage.tgz");
    const x = await t.exec(["sh", "-c", 'mkdir -p "$1" && tar -xzf /sandbox/home/stage.tgz -C "$1" && rm -f /sandbox/home/stage.tgz && find "$1" -type f | wc -l', "sh", st.to], { timeoutMs: 300_000 });
    if (x.code !== 0) throw new Error(`stage ${st.to} failed: ${x.stderr.slice(-300)}`);
    return { from: st.from, to: st.to, files: Number(x.stdout.trim()) };
  }

  /** Run one build/test command inside the sandbox workdir (allowlisted binaries only; no shell). */
  async runCheck(a: { argv: string[]; cwd?: string; timeout_ms?: number; label?: string }) {
    const t = this.tier; if (!t) throw new Error("not up");
    if (!Array.isArray(a.argv) || !a.argv.length || !a.argv.every((x) => typeof x === "string")) throw new Error("argv must be a non-empty string array");
    if (!CHECK_BINS.includes(a.argv[0])) throw new Error(`run_check: ${a.argv[0]} is not allowlisted (${CHECK_BINS.join(", ")})`);
    const cwd = a.cwd ? `${t.workdir}/${a.cwd}`.replace(/\/+/g, "/") : t.workdir;
    if (cwd.includes("..")) throw new Error("run_check: cwd must stay inside the workdir");
    const startedAt = aedt(); const t0 = Date.now();
    const r = await t.exec(a.argv, { cwd, env: { PYTHONPATH: SANDBOX_PYDEPS, CI: "1" }, timeoutMs: a.timeout_ms ?? 0 });
    const out = { label: a.label ?? a.argv.join(" ").slice(0, 120), argv: a.argv, cwd, code: r.code, ms: Date.now() - t0, startedAt, endedAt: aedt(), tier: t.name, stdoutTail: r.stdout.slice(-20_000), stderrTail: r.stderr.slice(-8_000) };
    this.#receipt(rid("check"), out);
    return out;
  }

  /** Export the commits made inside the sandbox since the seed base as a git bundle written on the host. */
  async exportCommits(_a: { out?: string } = {}) {
    const t = this.tier; if (!t || !this.repo) throw new Error("no seeded repo");
    const { ref, base } = this.repo;
    const info = await t.exec(["sh", "-c", 'git rev-parse "$1" && git log --format="%h %s" "$2..$1" && echo "--" && git status --porcelain | head -50', "sh", ref, base], { timeoutMs: 120_000 });
    if (info.code !== 0) throw new Error(`export: git log failed: ${info.stderr.slice(-300)}`);
    const [head, ...rest] = info.stdout.trim().split("\n"); const sep = rest.indexOf("--");
    const commits = rest.slice(0, sep); const dirty = rest.slice(sep + 1).filter(Boolean);
    if (!commits.length) return { head, base, commits: [], dirty, bundle: null };
    const mk = await t.exec(["sh", "-c", 'git bundle create -q /sandbox/home/export.bundle "$2..$1" && wc -c < /sandbox/home/export.bundle', "sh", ref, base], { timeoutMs: 300_000 });
    if (mk.code !== 0) throw new Error(`export: git bundle failed: ${mk.stderr.slice(-300)}`);
    const size = Number(mk.stdout.trim());
    const dir = join(STATE, "exports", `${t.id}-${Date.now().toString(36)}`); mkdirSync(dir, { recursive: true });
    const dl = await (t as any).download?.("/sandbox/home/export.bundle", dir);
    if (!dl || dl.code !== 0) throw new Error(`export download failed: ${dl?.stderr?.slice(-300) ?? "tier has no download"}`);
    const out = existsSync(join(dir, "export.bundle")) ? join(dir, "export.bundle") : join(dir, readdirSync(dir)[0]);
    if (statSync(out).size !== size) throw new Error(`export size mismatch ${statSync(out).size} != ${size}`);
    const res = { head, base, ref, commits, dirty, bundle: out, bytes: size, via: "git bundle + openshell sandbox download (gateway mTLS)" };
    this.#receipt(rid("export"), res);
    return res;
  }

  async #probeOmp() {
    const c = this.#spawnOmp(STAGE_MODELS.plan.primary);
    try {
      const state = await c.command("get_state", {}, 60_000);
      const cmds = await c.command("get_available_commands", {}, 60_000);
      const names: string[] = (cmds.data?.commands ?? []).map((x: any) => x.name);
      const gsd = names.filter((n) => n.startsWith("gsd-"));
      if (!gsd.includes("gsd-plan-phase") || !gsd.includes("gsd-execute-phase")) throw new Error(`GSD commands not visible to sandboxed omp (saw ${gsd.length})`);
      return { version: VERSIONS.omp, gsdCommands: gsd.length, sample: gsd.filter((n) => ["gsd-plan-phase", "gsd-execute-phase", "gsd-autonomous", "gsd-new-project"].includes(n)), model: state.data?.model ? `${state.data.model.provider}/${state.data.model.id}` : null };
    } finally { await c.close(); }
  }

  #spawnOmp(model: string, thinking = "high") {
    const t = this.tier!;
    const proc = t.spawnStdio(["omp", "--mode", "rpc-ui", "--no-title", "--no-lsp", "--model", model, "--thinking", thinking, "--approval-mode", "yolo"], { cwd: t.workdir });
    return new OmpRpcClient(proc, `omp[${t.name}]`);
  }

  /** Start a GSD stage. Returns immediately; follow with status()/events. */
  runStage(a: { stage: Stage; gsdCommand: string; task: string; thinking?: string; model?: string; plan?: string }) {
    if (!this.tier || !this.#ready) throw new Error("not up");
    // Every stage runs in the same workdir, so overlapping stages would edit and commit each other's files.
    const active = [...this.stages.values()].find((r) => !TERMINAL.includes(r.status));
    if (active) throw new Error(`stage ${active.id} is still ${active.status}; one stage at a time per workdir (abort it or wait for stage_done)`);
    const route = stageRoute(a.stage, a.plan);
    const id = rid(`st-${a.stage}`);
    const model = a.model ?? route.primary;
    const message = `/${a.gsdCommand} ${MAGIC}\n\n${a.task}`;
    const rec: StageRec = { id, stage: a.stage, plan: route.plan, fallbackModel: route.fallback, command: a.gsdCommand, modelRequested: model, status: "starting", client: null, startedAt: aedt(), questions: [], lastText: "", done: Promise.resolve() };
    this.stages.set(id, rec);
    rec.done = this.#drive(rec, message, model, a.thinking ?? "max").catch((e: any) => { rec.status = rec.status === "aborted" ? "aborted" : "failed"; rec.error = String(e?.message ?? e); }).finally(() => { rec.endedAt = aedt(); this.#stageReceipt(rec); this.emit("event", { event: "stage_done", stageId: id, status: rec.status, error: rec.error }); });
    return { stageId: id, plan: route.plan, model, fallback: route.fallback, command: a.gsdCommand };
  }

  async #drive(rec: StageRec, message: string, model: string, thinking: string): Promise<void> {
    const c = this.#spawnOmp(model, thinking); rec.client = c;
    try { return await this.#driveWith(c, rec, message, model, thinking); }
    // Close on every path (rejected prompt, setup timeout, fallback) so no failed stage keeps a live session.
    finally { await c.close().catch(() => {}); }
  }

  async #driveWith(c: OmpRpcClient, rec: StageRec, message: string, model: string, thinking: string): Promise<void> {
    c.on("question", (q: PendingQuestion) => {
      if (rec.status !== "aborted") rec.status = "waiting";
      const asked = q.questions?.map((x) => x.question).join(" | ") ?? q.title ?? q.method;
      rec.questions.push({ requestId: q.requestId, asked, method: q.method });
      this.emit("event", { event: "question", stageId: rec.id, requestId: q.requestId, method: q.method, questions: q.questions, title: q.title, options: q.options });
    });
    c.on("frame", (f) => {
      if (f.type === "message_end" && f.message?.role === "assistant") { const t = textOf(f.message); if (t) rec.lastText = t.slice(-4000); }
      // Never overwrite a requested abort (frames keep arriving while omp winds down).
      if (f.type === "agent_start" || f.type === "turn_start") if (rec.status === "starting" || rec.status === "running") rec.status = "running";
    });
    await c.command("set_ask_dialog", { enabled: true }, 60_000);
    // An abort that landed during setup wins: never send the prompt after cancellation.
    if (rec.status === "aborted") return;
    rec.status = "running";
    const r = await c.prompt(message);
    const st = (): StageStatus => rec.status;
    const failedEarly = r.status !== "completed" && c.models.size === 0;
    if (failedEarly && !rec.fallbackFrom && rec.fallbackModel && st() !== "aborted") {
      const fb = rec.fallbackModel;
      rec.fallbackFrom = model; rec.modelRequested = fb; await c.close();
      this.emit("event", { event: "fallback", stageId: rec.id, from: model, to: fb, reason: r.error?.message ?? r.status });
      return this.#drive(rec, message, fb, thinking);
    }
    if (r.status === "aborted" && st() !== "aborted") rec.status = "aborted";
    if (st() !== "aborted") rec.status = r.status === "completed" ? "completed" : "failed";
    if (r.status !== "completed" && st() !== "aborted") rec.error = r.error?.message ?? `prompt_result status=${r.status}`;
    await c.close();
  }

  answer(stageId: string, requestId: string, input: AnswerInput) {
    const rec = this.stages.get(stageId);
    if (!rec) throw new StaleAnswerError(requestId, `unknown stage ${stageId}`);
    if (!rec.client || TERMINAL.includes(rec.status)) throw new StaleAnswerError(requestId, `stage ${stageId} is ${rec.status}`);
    const r = rec.client.answer(requestId, input);
    const q = rec.questions.find((x) => x.requestId === requestId); if (q) { q.answered = input; q.answeredAt = aedt(); }
    if (rec.client.pendingQuestions.length === 0 && rec.status === "waiting") rec.status = "running";
    return r;
  }

  async abort(stageId: string) {
    const rec = this.stages.get(stageId);
    if (!rec) throw new Error(`unknown stage ${stageId}`);
    if (TERMINAL.includes(rec.status)) { if (rec.client && !rec.client.closed) await rec.client.close(2000); return { stageId, status: rec.status, note: "already terminal" }; }
    rec.status = "aborted";
    await rec.client?.abort();
    let t: NodeJS.Timeout | undefined;
    await Promise.race([rec.done, new Promise((r) => { t = setTimeout(r, 30_000); })]).finally(() => clearTimeout(t));
    if (rec.client && !rec.client.closed) await rec.client.close(2000);
    return { stageId, status: rec.status };
  }

  status(stageId?: string) {
    const view = (r: StageRec) => ({ stageId: r.id, stage: r.stage, plan: r.plan, command: r.command, status: r.status, modelRequested: r.modelRequested, fallbackFrom: r.fallbackFrom ?? null, modelsSeen: r.client ? [...r.client.models] : [], pendingQuestions: r.client?.pendingQuestions.map((q) => q.requestId) ?? [], questions: r.questions.length, startedAt: r.startedAt, endedAt: r.endedAt ?? null, error: r.error ?? null, lastText: r.lastText.slice(-600) });
    if (stageId) { const r = this.stages.get(stageId); if (!r) throw new Error(`unknown stage ${stageId}`); return view(r); }
    return { tier: this.tier?.name ?? null, isolation: this.tier?.isolation ?? null, owned: this.tier?.owned() ?? null, attestation: "mock", stages: [...this.stages.values()].map(view) };
  }

  #stageReceipt(r: StageRec) {
    // Answers are never persisted (only whether and when a question was answered); free text is redacted.
    const questions = r.questions.map((q) => ({ requestId: q.requestId, method: q.method, asked: redactSecrets(q.asked), answered: q.answered !== undefined, answeredAt: q.answeredAt ?? null }));
    this.#receipt(r.id, { stageId: r.id, stage: r.stage, plan: r.plan, gsdCommand: r.command, magicKeywords: MAGIC, modelRequested: r.modelRequested, fallbackFrom: r.fallbackFrom ?? null, modelsSeen: r.client ? [...r.client.models] : [], status: r.status, error: r.error ? redactSecrets(r.error) : null, questions, startedAt: r.startedAt, endedAt: r.endedAt, tier: this.tier?.name, isolation: this.tier?.isolation, skippedTiers: this.skipped, attestation: "mock", attestationDoc: this.attestation, ompBin: OMP_BIN, lastText: redactSecrets(r.lastText.slice(-1500)) });
  }
  #receipt(name: string, body: Record<string, unknown>) {
    mkdirSync(this.#receipts, { recursive: true, mode: 0o700 });
    const p = join(this.#receipts, `${name}.json`);
    writeFileSync(p, JSON.stringify({ ...body, access: this.access, attestation: "mock", tee: "none: mock attestation, no TEE hardware on this host", writtenAt: aedt() }, null, 2), { mode: 0o600 });
    chmodSync(p, 0o600);
    return p;
  }

  /** Release everything `up` acquired, in reverse order. Safe on a half-started core. */
  async #teardown() {
    const leftovers: string[] = [];
    try { await this.vm?.dispose(); } catch (e) { leftovers.push(`agentOS VM dispose: ${e}`); }
    this.vm = null;
    const ownedBefore = this.tier?.owned() ?? null;
    if (this.tier) { try { const d = await this.tier.dispose(); leftovers.push(...d.leftovers); } catch (e) { leftovers.push(`tier dispose: ${e}`); } }
    this.tier = null; this.#ready = false; this.up_ = {};
    try { await this.broker.stop(); } catch (e) { leftovers.push(`broker stop: ${e}`); }
    return { leftovers, ownedBefore };
  }

  async down() {
    // Let an in-flight startup finish (or roll itself back) before tearing down.
    if (this.#starting) await this.#starting.catch(() => {});
    for (const r of this.stages.values()) if (!TERMINAL.includes(r.status)) await this.abort(r.id).catch(() => {});
    const { leftovers, ownedBefore } = await this.#teardown();
    const out = { leftovers, ownedBefore, brokerStopped: this.broker.proc === null, attestation: "mock" };
    this.#receipt(`down-${Date.now().toString(36)}`, out);
    return out;
  }
}

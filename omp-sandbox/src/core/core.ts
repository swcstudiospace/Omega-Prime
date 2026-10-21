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
import { allowedDir, copySeed, PathRefused, SEED_ROOTS, WORKSPACE_ROOTS } from "./paths.ts";
import { repoSeedScript, CHECK_BINS, CHECK_TIMEOUT_MS, seedRef, sandboxHome, sandboxPath, type SeedRepo } from "./repo.ts";

export type StageStatus = "starting" | "running" | "waiting" | "completed" | "failed" | "aborted";
const TERMINAL: readonly StageStatus[] = ["completed", "failed", "aborted"];
type StageRec = {
  id: string; stage: Stage; plan: string; fallbackModel: string | null; command: string; modelRequested: string; status: StageStatus;
  client: OmpRpcClient | null; startedAt: string; endedAt?: string; error?: string; fallbackFrom?: string;
  questions: Array<{ requestId: string; asked: string; answered?: AnswerInput; answeredAt?: string; method: string }>;
  lastText: string; done: Promise<void>;
  /** True once the stage's omp session has fully wound down (status alone flips to aborted before omp exits). */
  settled: boolean;
};

const PASS_ENV_NAME = /^(ZAI|ZHIPU|OPENROUTER)_API_KEY$/;
/** Opt-in env passthrough: OMP_SANDBOX_PASS_ENV names allowlisted host *_API_KEY vars whose values go into the sandbox's
 *  0600 env file over exec stdin. Only names are ever recorded; values never reach argv, logs or receipts. */
export function passEnv(src: Record<string, string | undefined> = process.env) {
  const names = (src.OMP_SANDBOX_PASS_ENV ?? "").split(",").map((n) => n.trim()).filter((n) => PASS_ENV_NAME.test(n) && !!src[n]);
  return { names, env: Object.fromEntries(names.map((n) => [n, String(src[n])])) as Record<string, string> };
}

type UpOpts = { tiers?: string[]; seed?: string; workspace_host_path?: string; seed_repo?: SeedRepo; py_deps?: string; stage?: Array<{ from: string; to: string }> };

type DownReport = { leftovers: string[]; ownedBefore: Record<string, unknown> | null; brokerStopped: boolean; attestation: string };

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
  /** Set for the whole of `down()`: no new startup or stage is accepted while shutdown is tearing things down. */
  #stopping: Promise<DownReport> | null = null;
  /** Exact values handed to the sandbox (broker env, opted-in API keys): scrubbed from every check result. */
  #secrets: string[] = [];
  /** run_check calls still running; down() waits for them after the tier (and their processes) is gone. */
  #checks = new Set<Promise<unknown>>();
  /** Sandbox-side dir py_deps were staged into (run_check's PYTHONPATH). */
  #pydeps: string | null = null;

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
    if (this.#stopping) return Promise.reject(new Error("shutting down; call up again after down finishes"));
    if (this.#ready) return Promise.resolve(this.up_);
    if (!this.#starting) this.#starting = this.#up(opts).finally(() => { this.#starting = null; });
    return this.#starting;
  }

  /**
   * Test seam: treat an already-started tier as up (no broker, VM or probes). Not reachable over the API.
   * With `seed`, mirrors #up's seeding sequence — the allowedDir(seed_repo.path, WORKSPACE_ROOTS) pre-check, then
   * #seedRepo / #stagePyDeps / #stageDir — because up() short-circuits once #ready is set, leaving those folded ops
   * otherwise unreachable with a fake tier. Seeding is async, so the seeded form returns a promise of the same up_
   * stub shape with seeded/pyDeps/staged folded in. Still boots no broker, tier or VM. Not reachable over the API.
   */
  async attachTierForTests(tier: SandboxTier & { hostRoot?: string }, seed?: { seed_repo?: SeedRepo; py_deps?: string; stage?: Array<{ from: string; to: string }> }, secrets: string[] = []): Promise<Record<string, unknown>> {
    this.tier = tier; this.#ready = true; this.up_ = { tier: tier.name }; this.#secrets = [...secrets];
    if (seed === undefined) return this.up_;
    const seedRepo = seed.seed_repo !== undefined ? { ...seed.seed_repo, path: allowedDir(seed.seed_repo.path, WORKSPACE_ROOTS, "seed_repo.path") } : null;
    const seeded = seedRepo ? await this.#seedRepo(seedRepo) : null;
    const pyDeps = seed.py_deps ? await this.#stagePyDeps(seed.py_deps) : null;
    const staged = [] as unknown[]; for (const st of seed.stage ?? []) staged.push(await this.#stageDir(st));
    this.up_ = { ...this.up_, seeded, pyDeps, staged };
    return this.up_;
  }

  async #up(opts: UpOpts) {
    if ([opts.seed, opts.workspace_host_path, opts.seed_repo].filter((x) => x !== undefined).length > 1) throw new PathRefused("seed, seed_repo and workspace_host_path are mutually exclusive");
    const seedRepo = opts.seed_repo !== undefined ? { ...opts.seed_repo, path: allowedDir(opts.seed_repo.path, WORKSPACE_ROOTS, "seed_repo.path") } : null;
    const workspace = opts.workspace_host_path !== undefined ? allowedDir(opts.workspace_host_path, WORKSPACE_ROOTS, "workspace_host_path") : null;
    const seedSrc = opts.seed !== undefined ? allowedDir(opts.seed, SEED_ROOTS, "seed") : null;
    mkdirSync(this.#receipts, { recursive: true, mode: 0o700 });
    try {
      await this.broker.start();
      const passed = passEnv();
      const tierEnv: Record<string, string> = { ...this.broker.env, ...passed.env };
      this.#secrets = Object.values(tierEnv).filter((v) => typeof v === "string" && v.length >= 8);
      const { tier, skipped } = await startTier(tierEnv, opts.tiers as any, { workspaceHostPath: workspace });
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
      this.up_ = { tier: tier.name, isolation: tier.isolation, skippedTiers: skipped, passedEnv: passed.names, agentosBootMs: bootMs, roundTrip, gsd, seeded, pyDeps, staged, workspace: workspace ? { hostPath: workspace, sandboxPath: tier.workdir, mode: "bind-rw" } : null, omp: probe, attestation: "mock", attestationDoc: this.attestation };
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
    const { files, skipped, bytes } = await copySeed(src, `${SANDBOX_MOUNT}/${rel}`, this.vm.filesystem);
    const g = await t.exec(["sh", "-c", "git init -q && git add -A && git -c user.name=omp-sandbox -c user.email=omp-sandbox@local commit -qm 'seed: disposable e2e fixture' && git log --oneline | head -1"], { timeoutMs: 60_000 });
    return { fixture: src, files, bytes, skipped, viaVm: true, git: g.stdout.trim(), gitExit: g.code };
  }


  /** Seed a real git repo: host `git bundle` -> base64 over the authenticated exec stdin -> fetch + checkout in the sandbox workdir.
   *  Nothing on the host is mounted; the sandbox only ever sees this one ref. */
  async #seedRepo(s: SeedRepo) {
    const t = this.tier!;
    if (!s?.path || !s?.ref) throw new Error("seed_repo needs {path, ref}");
    // A branch name only (refs/heads/x is shortened): the checkout must be on a writable branch so new commits move the exported ref.
    const ref = seedRef(s.ref); s = { ...s, ref };
    const home = sandboxHome(t);
    mkdirSync(join(STATE, "seeds"), { recursive: true });
    const bundle = join(STATE, "seeds", `${t.id}.bundle`);
    const b = await execCapture("git", ["-C", s.path, "bundle", "create", bundle, s.ref], { timeoutMs: 300_000 });
    if (b.code !== 0) throw new Error(`git bundle failed: ${b.stderr.slice(-400)}`);
    await this.#put(bundle, `${home}/seed.bundle`);
    const g = await t.exec(["sh", "-c", repoSeedScript, "sh", s.ref, s.authorName ?? "omp-sandbox", s.authorEmail ?? "omp-sandbox@local", `${home}/seed.bundle`], { timeoutMs: 300_000 });
    if (g.code !== 0) throw new Error(`seed checkout failed (exit ${g.code}): ${g.stderr.slice(-400)}`);
    const [base, dirty] = g.stdout.trim().split("\n").slice(-2);
    this.repo = { path: s.path, ref: s.ref, base: base.trim() };
    return { repo: s.path, ref: s.ref, base: base.trim(), bundleBytes: statSync(bundle).size, untrackedAfterSeed: Number(dirty), via: `git bundle via ${typeof (t as any).upload === "function" ? "tier upload" : "exec stdin"} (verified in-sandbox)` };
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
    const home = sandboxHome(t); const pydeps = `${home}/pydeps`;
    const tgz = join(STATE, "seeds", `${t.id}-pydeps.tgz`); mkdirSync(join(STATE, "seeds"), { recursive: true });
    const tar = await execCapture("tar", ["-C", dir, "-czf", tgz, "."], { timeoutMs: 300_000 });
    if (tar.code !== 0) throw new Error(`py_deps tar failed: ${tar.stderr.slice(-300)}`);
    await this.#put(tgz, `${home}/pydeps.tgz`);
    // Paths ride as positional args ($1 dir, $2 archive, $3 env file), never spliced into the script text.
    const x = await t.exec(["sh", "-c", `mkdir -p "$1" && tar -xzf "$2" -C "$1" && rm -f "$2" && echo "export PYTHONPATH=$1" >> "$3" && python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import pytest, starlette, httpx; print(sys.version.split()[0], pytest.__version__)' "$1"`, "sh", pydeps, `${home}/pydeps.tgz`, `${home}/.omp-sandbox.env`], { timeoutMs: 300_000 });
    if (x.code !== 0) throw new Error(`py_deps staging failed: ${x.stderr.slice(-400)}`);
    this.#pydeps = pydeps;
    return { dir: pydeps, python_pytest: x.stdout.trim() };
  }

  /** Copy a host directory of reference files (read-only inputs such as specs) to <sandbox home>/<name>.
   *  `to` is the logical `/sandbox/home/<name>`; it lands under the selected tier's own home. */
  async #stageDir(st: { from: string; to: string }) {
    const t = this.tier!;
    if (!existsSync(st.from) || !/^\/sandbox\/home\/[A-Za-z0-9._-]+$/.test(st.to)) throw new Error(`stage: bad from/to (${st.from} -> ${st.to})`);
    const home = sandboxHome(t); const to = sandboxPath(t, st.to);
    const tgz = join(STATE, "seeds", `${t.id}-stage-${st.to.split("/").pop()}.tgz`); mkdirSync(join(STATE, "seeds"), { recursive: true });
    const tar = await execCapture("tar", ["-C", st.from, "-czf", tgz, "."], { timeoutMs: 300_000 });
    if (tar.code !== 0) throw new Error(`stage tar failed: ${tar.stderr.slice(-300)}`);
    await this.#put(tgz, `${home}/stage.tgz`);
    const x = await t.exec(["sh", "-c", 'mkdir -p "$1" && tar -xzf "$2" -C "$1" && rm -f "$2" && find "$1" -type f | wc -l', "sh", to, `${home}/stage.tgz`], { timeoutMs: 300_000 });
    if (x.code !== 0) throw new Error(`stage ${st.to} failed: ${x.stderr.slice(-300)}`);
    return { from: st.from, to, files: Number(x.stdout.trim()) };
  }

  /** Run one build/test command inside the sandbox workdir (allowlisted binaries only; no shell).
   *  Every returned and receipted field is scrubbed: the exact values handed to the sandbox, then credential shapes.
   *  The call is tracked so down() can wait for it once the tier has killed its processes; it is bounded by
   *  `timeout_ms` (default CHECK_TIMEOUT_MS). */
  runCheck(a: { argv: string[]; cwd?: string; timeout_ms?: number; label?: string }) {
    if (this.#stopping) return Promise.reject(new Error("shutting down; no new checks"));
    const p = this.#runCheck(a);
    this.#checks.add(p); const forget = () => { this.#checks.delete(p); }; p.then(forget, forget);
    return p;
  }

  /** Exact secret values first (they may not look like credentials), then credential-shaped text. */
  #scrub(text: string) {
    let out = text;
    for (const v of this.#secrets) out = out.split(v).join("[redacted]");
    return redactSecrets(out);
  }

  async #runCheck(a: { argv: string[]; cwd?: string; timeout_ms?: number; label?: string }) {
    const t = this.tier; if (!t) throw new Error("not up");
    if (!Array.isArray(a.argv) || !a.argv.length || !a.argv.every((x) => typeof x === "string")) throw new Error("argv must be a non-empty string array");
    if (!CHECK_BINS.includes(a.argv[0])) throw new Error(`run_check: ${a.argv[0]} is not allowlisted (${CHECK_BINS.join(", ")})`);
    const cwd = a.cwd ? `${t.workdir}/${a.cwd}`.replace(/\/+/g, "/") : t.workdir;
    if (cwd.includes("..")) throw new Error("run_check: cwd must stay inside the workdir");
    const startedAt = aedt(); const t0 = Date.now();
    const env: Record<string, string> = { CI: "1", ...(this.#pydeps ? { PYTHONPATH: this.#pydeps } : {}) };
    const r = await t.exec(a.argv, { cwd, env, timeoutMs: a.timeout_ms ?? CHECK_TIMEOUT_MS });
    const label = a.label ?? a.argv.join(" ").slice(0, 120);
    const out = { label: this.#scrub(label), argv: a.argv.map((x) => this.#scrub(x)), cwd, code: r.code, ms: Date.now() - t0, startedAt, endedAt: aedt(), tier: t.name, stdoutTail: this.#scrub(r.stdout.slice(-20_000)), stderrTail: this.#scrub(r.stderr.slice(-8_000)) };
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
    const remote = `${sandboxHome(t)}/export.bundle`;
    const mk = await t.exec(["sh", "-c", 'git bundle create -q "$3" "$2..$1" && wc -c < "$3"', "sh", ref, base, remote], { timeoutMs: 300_000 });
    if (mk.code !== 0) throw new Error(`export: git bundle failed: ${mk.stderr.slice(-300)}`);
    const size = Number(mk.stdout.trim());
    const dir = join(STATE, "exports", `${t.id}-${Date.now().toString(36)}`); mkdirSync(dir, { recursive: true });
    const dl = await (t as any).download?.(remote, dir);
    if (!dl || dl.code !== 0) throw new Error(`export download failed: ${dl?.stderr?.slice(-300) ?? "tier has no download"}`);
    const out = existsSync(join(dir, "export.bundle")) ? join(dir, "export.bundle") : join(dir, readdirSync(dir)[0]);
    if (statSync(out).size !== size) throw new Error(`export size mismatch ${statSync(out).size} != ${size}`);
    const res = { head, base, ref, commits, dirty, bundle: out, bytes: size, via: `git bundle + ${t.name} download` };
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
  /** `fallbackPlan`: when given, the only plan this stage may fall back to (null = no fallback); it must be the
   *  route's own next plan. Omitted keeps the route's default fallback. */
  runStage(a: { stage: Stage; gsdCommand: string; task: string; thinking?: string; model?: string; plan?: string; fallbackPlan?: string | null }) {
    if (this.#stopping) throw new Error("shutting down; no new stages");
    if (!this.tier || !this.#ready) throw new Error("not up");
    // Every stage runs in the same workdir, so overlapping stages would edit and commit each other's files.
    // An aborted stage is terminal at once but its omp session can still be writing until it settles.
    const active = [...this.stages.values()].find((r) => !r.settled);
    if (active) throw new Error(`stage ${active.id} is still ${TERMINAL.includes(active.status) ? `winding down (${active.status})` : active.status}; one stage at a time per workdir (abort it and wait for stage_done)`);
    const route = stageRoute(a.stage, a.plan);
    let fallbackModel = route.fallback;
    if (a.fallbackPlan !== undefined) {
      if (a.fallbackPlan === null) fallbackModel = null;
      else if (a.fallbackPlan !== route.fallbackPlan) throw new Error(`fallback plan ${JSON.stringify(a.fallbackPlan)} is not this route's fallback (${route.fallbackPlan ?? "none"})`);
    }
    const id = rid(`st-${a.stage}`);
    const model = a.model ?? route.primary;
    const message = `/${a.gsdCommand} ${MAGIC}\n\n${a.task}`;
    const rec: StageRec = { id, stage: a.stage, plan: route.plan, fallbackModel, command: a.gsdCommand, modelRequested: model, status: "starting", client: null, startedAt: aedt(), questions: [], lastText: "", done: Promise.resolve(), settled: false };
    this.stages.set(id, rec);
    rec.done = this.#drive(rec, message, model, a.thinking ?? "max").catch((e: any) => { rec.status = rec.status === "aborted" ? "aborted" : "failed"; rec.error = String(e?.message ?? e); }).finally(() => { rec.endedAt = aedt(); rec.settled = true; this.#stageReceipt(rec); this.emit("event", { event: "stage_done", stageId: id, status: rec.status, error: rec.error }); });
    return { stageId: id, plan: route.plan, model, fallback: fallbackModel, command: a.gsdCommand };
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
      if (f.type === "message_end" && f.message?.role === "assistant") { const t = textOf(f.message); if (t) rec.lastText = t; }
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
    const single = (r: StageRec) => ({ ...view(r), finalText: r.lastText });
    // One stage asked for by id also carries its full final assistant text; listings keep the short preview.
    if (stageId) { const r = this.stages.get(stageId); if (!r) throw new Error(`unknown stage ${stageId}`); return single(r); }
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

  down(): Promise<DownReport> {
    // One shutdown at a time; a second caller waits for the same one.
    if (!this.#stopping) this.#stopping = this.#down().finally(() => { this.#stopping = null; });
    return this.#stopping;
  }

  async #down(): Promise<DownReport> {
    // Refuse new work before the first wait: `#stopping` is already set, and the core is no longer ready.
    this.#ready = false;
    // Let an in-flight startup finish (or roll itself back) before tearing down.
    if (this.#starting) await this.#starting.catch(() => {});
    this.#ready = false;
    for (const r of this.stages.values()) if (!r.settled) await this.abort(r.id).catch(() => {});
    const { leftovers, ownedBefore } = await this.#teardown();
    // The tier is gone, so its check processes are dead; wait (bounded) for the run_check calls to settle.
    if (this.#checks.size) {
      let t: NodeJS.Timeout | undefined;
      const settled = await Promise.race([Promise.allSettled([...this.#checks]).then(() => true), new Promise<boolean>((r) => { t = setTimeout(() => r(false), 15_000); })]).finally(() => clearTimeout(t));
      if (!settled) leftovers.push(`${this.#checks.size} run_check call(s) still running after teardown`);
    }
    this.#secrets = []; this.#pydeps = null;
    const out = { leftovers, ownedBefore, brokerStopped: this.broker.proc === null, attestation: "mock" };
    this.#receipt(`down-${Date.now().toString(36)}`, out);
    return out;
  }
}

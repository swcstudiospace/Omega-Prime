// The TS core: auth-broker -> sandbox tier -> agentOS VM -> GSD-Core -> omp RPC stages.
import { chmodSync, mkdirSync, writeFileSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { EventEmitter } from "node:events";
import { ROOT, STATE, STAGE_MODELS, MAGIC, OMP_BIN, VERSIONS, aedt, stageRoute, type Stage } from "./config.ts";
import { AuthBroker } from "./auth.ts";
import { startTier } from "./providers/tiers.ts";
import type { SandboxTier } from "./providers/types.ts";
import { bootVm, vmRoundTrip, SANDBOX_MOUNT } from "./vm.ts";
import { ensureGsd } from "./gsd.ts";
import { OmpRpcClient, StaleAnswerError, textOf, type AnswerInput, type PendingQuestion } from "./omp-rpc.ts";
import { mockAttestation, type AttestationDoc } from "./attest.ts";
import { redactSecrets, rid } from "./providers/util.ts";

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

  #starting: Promise<Record<string, unknown>> | null = null;
  #ready = false;
  /** Set for the whole of `down()`: no new startup or stage is accepted while shutdown is tearing things down. */
  #stopping: Promise<DownReport> | null = null;
  /** Set when a finished stage's sandbox processes could not be confirmed gone: the workdir may still change. */
  #unsettled: string | null = null;

  /**
   * Bring the sandbox up once. Concurrent callers share one startup; a failed startup tears down everything it
   * acquired (broker, tier, VM) so the next `up` retries from scratch. Ready only after every check passed.
   */
  up(opts: { tiers?: string[]; seed?: string } = {}): Promise<Record<string, unknown>> {
    if (this.#stopping) return Promise.reject(new Error("shutting down; call up again after down finishes"));
    if (this.#ready) return Promise.resolve(this.up_);
    if (!this.#starting) this.#starting = this.#up(opts).finally(() => { this.#starting = null; });
    return this.#starting;
  }

  /** Test seam: treat an already-started tier as up (no broker, VM or probes). Not reachable over the API. */
  attachTierForTests(tier: SandboxTier & { hostRoot?: string }) { this.tier = tier; this.#ready = true; this.up_ = { tier: tier.name }; }

  async #up(opts: { tiers?: string[]; seed?: string }) {
    mkdirSync(this.#receipts, { recursive: true, mode: 0o700 });
    try {
      await this.broker.start();
      const { tier, skipped } = await startTier(this.broker.env, opts.tiers as any);
      this.tier = tier as any; this.skipped = skipped;
      const { vm, bootMs } = await bootVm(tier); this.vm = vm;
      const roundTrip = await vmRoundTrip(vm, tier);
      if (!roundTrip.match || roundTrip.hostFnExit !== 0) throw new Error(`agentOS VM <-> sandbox round trip failed (match=${roundTrip.match}, hostFnExit=${roundTrip.hostFnExit})`);
      const gsd = await ensureGsd(tier as any);
      const seeded = opts.seed ? await this.#seed(opts.seed) : null;
      if (seeded && seeded.gitExit !== 0) throw new Error(`seeding failed: git exit ${seeded.gitExit}`);
      const probe = await this.#probeOmp();
      this.attestation = mockAttestation(tier.name, { isolation: tier.isolation });
      this.up_ = { tier: tier.name, isolation: tier.isolation, skippedTiers: skipped, agentosBootMs: bootMs, roundTrip, gsd, seeded, omp: probe, attestation: "mock", attestationDoc: this.attestation };
      this.#ready = true;
      this.#receipt(`up-${tier.id}`, this.up_);
      return this.up_;
    } catch (e) {
      const { leftovers } = await this.#teardown();
      if (leftovers.length) (e as any).leftovers = leftovers;
      throw e;
    }
  }

  /** Copy a fixture project into the sandbox workdir THROUGH the agentOS VM mount, then git-init it inside the sandbox. */
  async #seed(fixture: string) {
    const t = this.tier!; const src = join(ROOT, fixture);
    const rel = t.workdir.slice(t.sandboxRoot.length).replace(/^\/+/, "");
    const files: string[] = [];
    const walk = (d: string) => { for (const e of readdirSync(join(src, d), { withFileTypes: true })) { const r = d ? `${d}/${e.name}` : e.name; e.isDirectory() ? walk(r) : files.push(r); } };
    walk("");
    for (const f of files) {
      const dir = f.includes("/") ? f.slice(0, f.lastIndexOf("/")) : "";
      if (dir) await this.vm.filesystem.mkdir(`${SANDBOX_MOUNT}/${rel}/${dir}`, { recursive: true }).catch(() => {});
      await this.vm.filesystem.writeFile(`${SANDBOX_MOUNT}/${rel}/${f}`, readFileSync(join(src, f), "utf8"));
    }
    const g = await t.exec(["sh", "-c", "git init -q && git add -A && git -c user.name=omp-sandbox -c user.email=omp-sandbox@local commit -qm 'seed: disposable e2e fixture' && git log --oneline | head -1"], { timeoutMs: 60_000 });
    return { fixture, files, viaVm: true, git: g.stdout.trim(), gitExit: g.code };
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
    } finally { await c.close(); await this.#settle(c); }
  }

  /**
   * Wait until the session's sandbox-side processes are gone, not just the local client. If the tier cannot confirm
   * that, the workdir may still be edited, so the core refuses new stages until it is torn down.
   */
  async #settle(c: OmpRpcClient, rec?: StageRec) {
    try { await this.tier?.settled?.(c.proc); }
    catch (e: any) {
      this.#unsettled = `${rec ? `stage ${rec.id}` : "omp session"}: ${e?.message ?? e}`;
      throw e;
    }
  }

  #spawnOmp(model: string, thinking = "high") {
    const t = this.tier!;
    const proc = t.spawnStdio(["omp", "--mode", "rpc-ui", "--no-title", "--no-lsp", "--model", model, "--thinking", thinking, "--approval-mode", "yolo"], { cwd: t.workdir });
    return new OmpRpcClient(proc, `omp[${t.name}]`);
  }

  /** Start a GSD stage. Returns immediately; follow with status()/events. */
  runStage(a: { stage: Stage; gsdCommand: string; task: string; thinking?: string; model?: string; plan?: string }) {
    if (this.#stopping) throw new Error("shutting down; no new stages");
    if (!this.tier || !this.#ready) throw new Error("not up");
    if (this.#unsettled) throw new Error(`refusing new work until down/up: ${this.#unsettled}`);
    // Every stage runs in the same workdir, so overlapping stages would edit and commit each other's files.
    // An aborted stage is terminal at once but its omp session can still be writing until it settles.
    const active = [...this.stages.values()].find((r) => !r.settled);
    if (active) throw new Error(`stage ${active.id} is still ${TERMINAL.includes(active.status) ? `winding down (${active.status})` : active.status}; one stage at a time per workdir (abort it and wait for stage_done)`);
    const route = stageRoute(a.stage, a.plan);
    const id = rid(`st-${a.stage}`);
    const model = a.model ?? route.primary;
    const message = `/${a.gsdCommand} ${MAGIC}\n\n${a.task}`;
    const rec: StageRec = { id, stage: a.stage, plan: route.plan, fallbackModel: route.fallback, command: a.gsdCommand, modelRequested: model, status: "starting", client: null, startedAt: aedt(), questions: [], lastText: "", done: Promise.resolve(), settled: false };
    this.stages.set(id, rec);
    rec.done = this.#drive(rec, message, model, a.thinking ?? "max").catch((e: any) => { rec.status = rec.status === "aborted" ? "aborted" : "failed"; rec.error = String(e?.message ?? e); }).finally(() => { rec.endedAt = aedt(); rec.settled = true; this.#stageReceipt(rec); this.emit("event", { event: "stage_done", stageId: id, status: rec.status, error: rec.error }); });
    return { stageId: id, plan: route.plan, model, fallback: route.fallback, command: a.gsdCommand };
  }

  async #drive(rec: StageRec, message: string, model: string, thinking: string): Promise<void> {
    const c = this.#spawnOmp(model, thinking); rec.client = c;
    try { return await this.#driveWith(c, rec, message, model, thinking); }
    // Close on every path (rejected prompt, setup timeout, fallback) so no failed stage keeps a live session.
    finally { await c.close().catch(() => {}); await this.#settle(c, rec); }
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
      rec.fallbackFrom = model; rec.modelRequested = fb; await c.close(); await this.#settle(c, rec);
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
    this.tier = null; this.#ready = false; this.up_ = {}; this.#unsettled = null;
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
    const out = { leftovers, ownedBefore, brokerStopped: this.broker.proc === null, attestation: "mock" };
    this.#receipt(`down-${Date.now().toString(36)}`, out);
    return out;
  }
}

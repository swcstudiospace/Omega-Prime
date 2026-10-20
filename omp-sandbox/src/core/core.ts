// The TS core: auth-broker -> sandbox tier -> agentOS VM -> GSD-Core -> omp RPC stages.
import { mkdirSync, writeFileSync } from "node:fs";
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
import { rid } from "./providers/util.ts";
import { allowedDir, seedFiles, PathRefused, SEED_ROOTS, WORKSPACE_ROOTS } from "./paths.ts";

export type StageStatus = "starting" | "running" | "waiting" | "completed" | "failed" | "aborted";
type StageRec = {
  id: string; stage: Stage; plan: string; fallbackModel: string | null; command: string; modelRequested: string; status: StageStatus;
  client: OmpRpcClient | null; startedAt: string; endedAt?: string; error?: string; fallbackFrom?: string;
  questions: Array<{ requestId: string; asked: string; answered?: AnswerInput; answeredAt?: string; method: string }>;
  lastText: string; done: Promise<void>;
};

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

  /**
   * `workspace_host_path`: an absolute host directory under an allowlisted root (WORKSPACE_ROOTS), bind-mounted
   * read-write at the tier's workdir (/sandbox/work on openshell-podman). Changes land directly in that host tree.
   * `seed`: an absolute fixture directory under SEED_ROOTS, copied (binary-safe, no .git/.venv/node_modules, no
   * symlinks) into an empty workdir. The two are mutually exclusive. Paths are checked before anything starts.
   */
  async up(opts: { tiers?: string[]; seed?: string; workspace_host_path?: string } = {}) {
    if (this.tier) return this.up_;
    if (opts.seed !== undefined && opts.workspace_host_path !== undefined) throw new PathRefused("seed and workspace_host_path are mutually exclusive");
    const workspace = opts.workspace_host_path !== undefined ? allowedDir(opts.workspace_host_path, WORKSPACE_ROOTS, "workspace_host_path") : null;
    const seedSrc = opts.seed !== undefined ? allowedDir(opts.seed, SEED_ROOTS, "seed") : null;
    mkdirSync(this.#receipts, { recursive: true });
    await this.broker.start();
    const { tier, skipped } = await startTier(this.broker.env, opts.tiers as any, { workspaceHostPath: workspace });
    this.tier = tier as any; this.skipped = skipped;
    const { vm, bootMs } = await bootVm(tier); this.vm = vm;
    const roundTrip = await vmRoundTrip(vm, tier);
    const gsd = await ensureGsd(tier as any);
    const seeded = seedSrc ? await this.#seed(seedSrc) : null;
    const probe = await this.#probeOmp();
    this.attestation = mockAttestation(tier.name, { isolation: tier.isolation });
    this.up_ = { tier: tier.name, isolation: tier.isolation, skippedTiers: skipped, agentosBootMs: bootMs, roundTrip, gsd, seeded, workspace: workspace ? { hostPath: workspace, sandboxPath: tier.workdir, mode: "bind-rw" } : null, omp: probe, attestation: "mock", attestationDoc: this.attestation };
    this.#receipt(`up-${tier.id}`, this.up_);
    return this.up_;
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
    if (!this.tier) throw new Error("not up");
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
    c.on("question", (q: PendingQuestion) => {
      rec.status = "waiting";
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
    if (!rec.client || ["completed", "failed", "aborted"].includes(rec.status)) throw new StaleAnswerError(requestId, `stage ${stageId} is ${rec.status}`);
    const r = rec.client.answer(requestId, input);
    const q = rec.questions.find((x) => x.requestId === requestId); if (q) { q.answered = input; q.answeredAt = aedt(); }
    if (rec.client.pendingQuestions.length === 0 && rec.status === "waiting") rec.status = "running";
    return r;
  }

  async abort(stageId: string) {
    const rec = this.stages.get(stageId);
    if (!rec) throw new Error(`unknown stage ${stageId}`);
    if (["completed", "failed", "aborted"].includes(rec.status)) return { stageId, status: rec.status, note: "already terminal" };
    rec.status = "aborted";
    await rec.client?.abort();
    await Promise.race([rec.done, new Promise((r) => setTimeout(r, 30_000))]);
    if (rec.client && !rec.client.closed) await rec.client.close(2000);
    return { stageId, status: rec.status };
  }

  status(stageId?: string) {
    const view = (r: StageRec) => ({ stageId: r.id, stage: r.stage, plan: r.plan, command: r.command, status: r.status, modelRequested: r.modelRequested, fallbackFrom: r.fallbackFrom ?? null, modelsSeen: r.client ? [...r.client.models] : [], pendingQuestions: r.client?.pendingQuestions.map((q) => q.requestId) ?? [], questions: r.questions.length, startedAt: r.startedAt, endedAt: r.endedAt ?? null, error: r.error ?? null, lastText: r.lastText.slice(-600) });
    if (stageId) { const r = this.stages.get(stageId); if (!r) throw new Error(`unknown stage ${stageId}`); return view(r); }
    return { tier: this.tier?.name ?? null, isolation: this.tier?.isolation ?? null, owned: this.tier?.owned() ?? null, attestation: "mock", stages: [...this.stages.values()].map(view) };
  }

  #stageReceipt(r: StageRec) {
    this.#receipt(r.id, { stageId: r.id, stage: r.stage, plan: r.plan, gsdCommand: r.command, magicKeywords: MAGIC, modelRequested: r.modelRequested, fallbackFrom: r.fallbackFrom ?? null, modelsSeen: r.client ? [...r.client.models] : [], status: r.status, error: r.error ?? null, questions: r.questions, startedAt: r.startedAt, endedAt: r.endedAt, tier: this.tier?.name, isolation: this.tier?.isolation, skippedTiers: this.skipped, attestation: "mock", attestationDoc: this.attestation, ompBin: OMP_BIN, lastText: r.lastText.slice(-1500) });
  }
  #receipt(name: string, body: Record<string, unknown>) {
    const p = join(this.#receipts, `${name}.json`);
    writeFileSync(p, JSON.stringify({ ...body, access: this.access, attestation: "mock", tee: "none: mock attestation, no TEE hardware on this host", writtenAt: aedt() }, null, 2));
    return p;
  }

  async down() {
    for (const r of this.stages.values()) if (!["completed", "failed", "aborted"].includes(r.status)) await this.abort(r.id).catch(() => {});
    const leftovers: string[] = [];
    try { await this.vm?.dispose(); } catch (e) { leftovers.push(`agentOS VM dispose: ${e}`); }
    this.vm = null;
    if (this.tier) { const d = await this.tier.dispose(); leftovers.push(...d.leftovers); }
    const ownedBefore = this.tier?.owned() ?? null;
    this.tier = null;
    await this.broker.stop();
    const out = { leftovers, ownedBefore, brokerStopped: this.broker.proc === null, attestation: "mock" };
    this.#receipt(`down-${Date.now().toString(36)}`, out);
    return out;
  }
}

// omp RPC client: drives `omp --mode rpc` (newline-delimited JSON over stdio) through whatever
// exec channel the sandbox tier provides. One client = one omp session.
// Protocol facts (omp 18.8.4, src/modes/rpc/rpc-types.ts):
//   in : {id,type:"prompt",message} | {type:"set_ask_dialog",enabled} | {type:"abort"} | {type:"get_state"}
//        {type:"get_available_commands"} | {type:"extension_ui_response",id,answers|value|confirmed|cancelled}
//   out: {type:"response",command,success,...} | {type:"prompt_result",id,status,agentInvoked,sessionSettled}
//        {type:"extension_ui_request",id,method:"ask"|"select"|"confirm"|"input"|"notify"|...} | session events
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { randomBytes } from "node:crypto";

export type RpcFrame = Record<string, any> & { type: string };
export type PendingQuestion = {
  requestId: string;          // omp extension_ui_request id
  method: string;             // ask | select | confirm | input | editor
  questions?: Array<{ id: string; question: string; options: Array<{ label: string }>; recommended?: number }>;
  title?: string;
  options?: string[];
  askedAt: number;
};
export type AnswerInput = { selected?: string[]; text?: string; perQuestion?: Record<string, { selected?: string[]; text?: string }> };

export class StaleAnswerError extends Error {
  constructor(public requestId: string, why: string) { super(`stale answer for ${requestId}: ${why}`); }
}

const UI_INPUT_METHODS = new Set(["ask", "select", "confirm", "input", "editor"]);

export class OmpRpcClient extends EventEmitter {
  #buf = "";
  #waiters = new Map<string, (f: RpcFrame) => void>();
  #pending = new Map<string, PendingQuestion>();
  #answered = new Set<string>();
  #closed = false;
  #inputError: Error | null = null;
  readonly models = new Set<string>();          // provider/model seen on assistant messages
  readonly transcript: RpcFrame[] = [];          // bounded copy of interesting frames
  exitCode: number | null = null;

  constructor(readonly proc: ChildProcessWithoutNullStreams, readonly label = "omp") {
    super();
    proc.stdout.setEncoding("utf8");
    proc.stdout.on("data", (chunk: string) => this.#onData(chunk));
    proc.stderr.setEncoding("utf8");
    proc.stderr.on("data", (c: string) => this.emit("stderr", c));
    // omp or its exec channel can close stdin mid-session; the next write then emits EPIPE on the pipe. Without a
    // listener Node treats that as an uncaught error and kills the core, skipping sandbox and broker cleanup.
    proc.stdin.on("error", (e: NodeJS.ErrnoException) => this.#inputFailed(e));
    proc.on("exit", (code) => {
      this.exitCode = code; this.#closed = true;
      for (const [, w] of this.#waiters) w({ type: "closed", code, reason: `rpc exited (${code})` });
      this.#waiters.clear();
      this.#pending.clear();
      this.emit("exit", code);
    });
  }

  get closed() { return this.#closed; }
  /** Set once writing to omp failed (e.g. EPIPE); the session can no longer be driven. */
  get inputError() { return this.#inputError; }

  #inputFailed(e: NodeJS.ErrnoException) {
    if (this.#inputError || this.#closed) return;
    this.#inputError = new Error(`${this.label}: rpc input pipe failed (${e.code ?? e.message})`);
    // Fail every request through the normal path, then stop the child so its exit drives the usual cleanup.
    for (const [, w] of this.#waiters) w({ type: "closed", code: null, reason: this.#inputError.message });
    this.#waiters.clear();
    this.#pending.clear();
    this.emit("input_error", this.#inputError);
    try { this.proc.kill("SIGTERM"); } catch { /* already gone */ }
  }
  get pendingQuestions(): PendingQuestion[] { return [...this.#pending.values()]; }

  #onData(chunk: string) {
    this.#buf += chunk;
    let nl: number;
    while ((nl = this.#buf.indexOf("\n")) >= 0) {
      const line = this.#buf.slice(0, nl).trim();
      this.#buf = this.#buf.slice(nl + 1);
      if (!line) continue;
      let f: RpcFrame;
      try { f = JSON.parse(line); } catch { this.emit("noise", line); continue; }
      this.#route(f);
    }
  }

  #route(f: RpcFrame) {
    if (f.type === "message_end" || f.type === "message_start") {
      const m = f.message ?? {};
      if (m.role === "assistant" && (m.model || m.provider)) this.models.add(`${m.provider ?? "?"}/${m.model ?? "?"}`);
    }
    if (f.type === "extension_ui_request") {
      if (f.method === "cancel" && f.targetId) {
        this.#pending.delete(f.targetId); this.#answered.add(f.targetId);
        this.emit("question_cancelled", f.targetId);
      } else if (UI_INPUT_METHODS.has(f.method)) {
        const q: PendingQuestion = { requestId: f.id, method: f.method, questions: f.questions, title: f.title, options: f.options, askedAt: Date.now() };
        this.#pending.set(f.id, q);
        this.emit("question", q);
      }
    }
    if (["response", "prompt_result", "extension_ui_request", "agent_end", "notice", "session_settled", "ready"].includes(f.type)
        || (f.type === "message_end" && f.message?.role === "assistant")) {
      this.transcript.push(f.type === "message_end" ? { type: f.type, role: "assistant", model: f.message?.model, provider: f.message?.provider, text: textOf(f.message).slice(0, 2000) } : f);
      if (this.transcript.length > 500) this.transcript.shift();
    }
    const key = f.type === "prompt_result" ? `prompt_result:${f.id}` : f.type === "response" ? `response:${f.id}` : null;
    if (key && this.#waiters.has(key)) { const w = this.#waiters.get(key)!; this.#waiters.delete(key); w(f); }
    this.emit("frame", f);
  }

  send(obj: Record<string, unknown>) {
    if (this.#closed) throw new Error(`${this.label}: rpc process is closed`);
    if (this.#inputError) throw this.#inputError;
    this.proc.stdin.write(JSON.stringify(obj) + "\n");
  }

  #wait(key: string, timeoutMs: number): Promise<RpcFrame> {
    return new Promise((resolve, reject) => {
      const t = timeoutMs > 0 ? setTimeout(() => { this.#waiters.delete(key); reject(new Error(`${this.label}: timeout waiting for ${key}`)); }, timeoutMs) : null;
      this.#waiters.set(key, (f) => { if (t) clearTimeout(t); f.type === "closed" ? reject(new Error(`${this.label}: ${f.reason} while waiting for ${key}`)) : resolve(f); });
    });
  }

  async command(type: string, extra: Record<string, unknown> = {}, timeoutMs = 30_000) {
    const id = `c-${randomBytes(4).toString("hex")}`;
    const p = this.#wait(`response:${id}`, timeoutMs);
    this.send({ id, type, ...extra });
    const r = await p;
    if (r.success === false) throw new Error(`${this.label}: ${type} failed: ${r.error ?? JSON.stringify(r).slice(0, 300)}`);
    return r;
  }

  /** Submit a prompt; resolves with the terminal prompt_result frame. */
  async prompt(message: string, timeoutMs = 0) {
    const id = `p-${randomBytes(4).toString("hex")}`;
    const done = this.#wait(`prompt_result:${id}`, timeoutMs);
    // Observe `done` now: if the child exits before admission both waiters reject, and an unobserved
    // rejection would crash the core even though the caller handles the admission error.
    done.catch(() => {});
    const accepted = this.#wait(`response:${id}`, 60_000);
    try {
      this.send({ id, type: "prompt", message });
      const acc = await accepted;
      if (acc.success === false) throw new Error(`${this.label}: prompt rejected: ${acc.error ?? ""}`);
    } catch (e) {
      this.#waiters.delete(`prompt_result:${id}`); this.#waiters.delete(`response:${id}`);
      accepted.catch(() => {});
      throw e;
    }
    return done;
  }

  /** Answer a live question. Throws StaleAnswerError for unknown / already answered / cancelled ids. */
  answer(requestId: string, input: AnswerInput) {
    const q = this.#pending.get(requestId);
    if (!q) throw new StaleAnswerError(requestId, this.#answered.has(requestId) ? "already answered or cancelled" : this.#closed ? "session closed" : "no such pending question");
    let frame: Record<string, unknown>;
    if (q.method === "ask") {
      const answers = (q.questions ?? []).map((qq) => {
        const per = input.perQuestion?.[qq.id] ?? input;
        const sel = per.selected ?? (per.text ? [] : [qq.options[qq.recommended ?? 0]?.label].filter(Boolean) as string[]);
        return { id: qq.id, selectedOptions: sel, ...(per.text ? { customInput: per.text } : {}) };
      });
      frame = { type: "extension_ui_response", id: requestId, answers };
    } else if (q.method === "confirm") {
      frame = { type: "extension_ui_response", id: requestId, confirmed: !/^(no|n|false|0)$/i.test(input.text ?? input.selected?.[0] ?? "yes") };
    } else {
      frame = { type: "extension_ui_response", id: requestId, value: input.text ?? input.selected?.[0] ?? q.options?.[0] ?? "" };
    }
    this.#pending.delete(requestId); this.#answered.add(requestId);
    this.send(frame);
    return { requestId, method: q.method };
  }

  /** Recommended-option answer, used by unattended stages. */
  answerRecommended(q: PendingQuestion) { return this.answer(q.requestId, {}); }

  async abort() { try { await this.command("abort", {}, 30_000); } catch { /* process may already be gone */ } }

  async close(graceMs = 5000) {
    if (this.#closed) return;
    try { this.proc.stdin.end(); } catch { /* ignore */ }
    const exited = new Promise((r) => this.proc.once("exit", r));
    const t = setTimeout(() => { try { this.proc.kill("SIGTERM"); } catch {} }, graceMs);
    const t2 = setTimeout(() => { try { this.proc.kill("SIGKILL"); } catch {} }, graceMs * 2);
    await exited; clearTimeout(t); clearTimeout(t2);
  }
}

export function textOf(m: any): string {
  if (!m) return "";
  if (typeof m.content === "string") return m.content;
  if (Array.isArray(m.content)) return m.content.filter((c: any) => c?.type === "text").map((c: any) => c.text).join("");
  return "";
}

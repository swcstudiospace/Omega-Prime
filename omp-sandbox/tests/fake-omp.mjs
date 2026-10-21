// Minimal stand-in for `omp --mode rpc-ui` used by unit tests (no inference).
import { createInterface } from "node:readline";
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");
out({ type: "ready" });
let promptId = null;
createInterface({ input: process.stdin }).on("line", async (l) => {
  const m = JSON.parse(l);
  if (m.type === "set_ask_dialog") {
    const ack = () => out({ type: "response", id: m.id, command: m.type, success: true });
    const delay = Number(process.env.FAKE_OMP_ASK_DELAY_MS ?? 0);
    return delay ? setTimeout(ack, delay) : ack();
  }
  if (m.type === "abort") return out({ type: "response", id: m.id, command: "abort", success: true });
  if (m.type === "prompt" && process.env.FAKE_OMP_PROMPT_LOG) (await import("node:fs")).appendFileSync(process.env.FAKE_OMP_PROMPT_LOG, "prompt\n");
  if (m.type === "prompt" && process.env.FAKE_OMP_EXIT_ON_PROMPT) process.exit(3);
  if (m.type === "prompt") {
    promptId = m.id; out({ type: "response", id: m.id, command: "prompt", success: true });
    out({ type: "message_start", message: { role: "assistant", provider: "fake", model: "m1", content: [] } });
    out({ type: "extension_ui_request", id: "q1", method: "ask", questions: [{ id: "lang", question: "Lang?", options: [{ label: "English" }, { label: "French" }], recommended: 0 }] });
    out({ type: "extension_ui_request", id: "q2", method: "confirm", title: "ok?" });
    out({ type: "extension_ui_request", id: "q2", method: "cancel", targetId: "q2" });
    return;
  }
  if (m.type === "extension_ui_response" && m.id === "q1") {
    out({ type: "message_end", message: { role: "assistant", provider: "fake", model: "m1", content: [{ type: "text", text: "picked " + m.answers[0].selectedOptions[0] }] } });
    out({ type: "prompt_result", id: promptId, status: "completed", agentInvoked: true, sessionSettled: true });
  }
});

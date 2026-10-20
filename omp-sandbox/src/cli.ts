// omp-sandbox CLI (thin, for humans). The Rust wrapper uses src/core/server.ts directly.
//   omp-sandbox up                         boot broker + tier + agentOS VM + GSD, print the up report, then down
//   omp-sandbox run <plan|execute> <gsd-command> <task-file>   one stage end to end (auto-answers questions with the recommended option)
import { readFileSync } from "node:fs";
import { Core } from "./core/core.ts";
const [cmd, ...rest] = process.argv.slice(2);
const core = new Core();
try {
  const up = await core.up();
  console.error(JSON.stringify(up, null, 2));
  if (cmd === "run") {
    const [stage, gsdCommand, file] = rest;
    core.on("event", (e) => { console.error(JSON.stringify(e)); if (e.event === "question") core.answer(e.stageId, e.requestId, {}); });
    const { stageId } = core.runStage({ stage: stage as any, gsdCommand, task: readFileSync(file, "utf8") });
    await new Promise<void>((r) => core.on("event", (e) => e.event === "stage_done" && e.stageId === stageId && r()));
    console.log(JSON.stringify(core.status(stageId), null, 2));
  }
} finally { console.error(JSON.stringify(await core.down())); }

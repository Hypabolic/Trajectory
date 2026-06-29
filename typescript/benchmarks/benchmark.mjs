import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";

import {
  normalizeToIR,
  projectCanonical,
  projectHypabolic,
  projectLetta,
  projectMinimalJsonl,
  projectOpenAI,
  serializeProjection,
} from "@hypabolic/trajectory";
import { projectOpenTelemetry } from "@hypabolic/trajectory-otel";

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const transcriptBytes = await readFile(
  resolve(workspace, "../conformance/cases/pi/unicode-boundaries/input.jsonl"),
);
const request = { source: "pi", transcriptBytes };
const iterations = 250;

for (let index = 0; index < 10; index++) normalizeToIR(request);
const heapBefore = process.memoryUsage().heapUsed;
const started = performance.now();
let trajectory;
for (let index = 0; index < iterations; index++) {
  trajectory = normalizeToIR(request);
}
const elapsed = performance.now() - started;
const heapDelta = process.memoryUsage().heapUsed - heapBefore;
const outputs = {
  "letta-trajectory-v1": serializeProjection(projectLetta(trajectory)),
  "letta-canonical-v1": serializeProjection(projectCanonical(trajectory)),
  "hypabolic-trajectory-v1": serializeProjection(projectHypabolic(trajectory)),
  "openai-chat-messages": serializeProjection(projectOpenAI(trajectory)),
  "jsonl-minimal": projectMinimalJsonl(trajectory),
  "otel-genai-spans-v1": serializeProjection(projectOpenTelemetry(trajectory)),
};

console.log(JSON.stringify({
  runtime: "typescript",
  iterations,
  elapsed_ms: elapsed,
  operations_per_second: iterations / (elapsed / 1_000),
  heap_delta_bytes: heapDelta,
  input_bytes: transcriptBytes.byteLength,
  records: trajectory.records.length,
  output_bytes: Object.fromEntries(
    Object.entries(outputs).map(([name, output]) => [
      name,
      Buffer.byteLength(output),
    ]),
  ),
}));

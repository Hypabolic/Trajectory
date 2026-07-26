import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";

import {
  normalizeToIR,
  TrajectoryNormalizationError,
} from "@hypabolic/trajectory";

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repository = resolve(workspace, "..");

test("TypeScript runner passes every advertised shared operation deterministically", () => {
  const output = execFileSync(
    process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3"),
    [
      resolve(repository, "conformance/verify.py"),
      "--repository-root",
      repository,
      "--",
      process.execPath,
      resolve(workspace, "packages/trajectory-testing/dist/cli.js"),
    ],
    { encoding: "utf8" },
  );
  assert.deepEqual(JSON.parse(output), {
    protocol_version: "1",
    status: "success",
    cases: 17,
    operations: 29,
  });
});

test("whole mode reports typed missing-assistant failure", () => {
  const input = [
    '{"type":"session","id":"fatal"}',
    '{"type":"message","id":"user","timestamp":"2026-01-01T00:00:00Z","message":{"role":"user","content":"hello"}}',
    "",
  ].join("\n");
  assert.throws(
    () => normalizeToIR({ source: "pi", transcript: input }),
    (error) => error instanceof TrajectoryNormalizationError &&
      error.code === "missing_assistant_records",
  );
});

test("partial mode accepts a cross-chunk result and applies baseByteOffset", () => {
  const input = [
    '{"type":"message","id":"result","timestamp":"2026-01-01T00:00:01Z","message":{"role":"toolResult","toolCallId":"prior","content":"ok"}}',
    "",
  ].join("\n");
  const trajectory = normalizeToIR({
    source: "pi",
    transcript: input,
    sourceContext: { partial: true, baseByteOffset: 9n, groupId: "chunk" },
  });
  assert.equal(trajectory.records.length, 2);
  assert.equal(trajectory.records[1].toolCallId, "prior");
  assert.equal(trajectory.records[1].provenance.sourceOffset, 0n);
});

test("null bounds mean unbounded rather than defaults", () => {
  const long = "x".repeat(3_000);
  const input = [
    '{"type":"session","id":"unbounded"}',
    '{"type":"message","id":"user","timestamp":"2026-01-01T00:00:00Z","message":{"role":"user","content":"go"}}',
    '{"type":"message","id":"call","timestamp":"2026-01-01T00:00:01Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"c","name":"n","arguments":{}}]}}',
    JSON.stringify({ type: "message", id: "result", timestamp: "2026-01-01T00:00:02Z", message: { role: "toolResult", toolCallId: "c", content: long } }),
    '{"type":"message","id":"assistant","timestamp":"2026-01-01T00:00:03Z","message":{"role":"assistant","content":"done"}}',
    "",
  ].join("\n");
  const trajectory = normalizeToIR({
    source: "pi",
    transcript: input,
    options: { bounds: { toolResults: { maxCharacters: null } } },
  });
  assert.equal(trajectory.records.find((record) => record.kind === "tool_result")?.content, long);
  assert.equal(trajectory.diagnostics.some((item) => item.code === "tool_result_truncated"), false);
});

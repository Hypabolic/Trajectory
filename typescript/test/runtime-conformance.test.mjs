import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";

import {
  normalizeToIR,
  projectMinimalJsonl,
  TrajectoryNormalizationError,
  writeMinimalJsonl,
} from "@hypabolic/trajectory";
import { projectOpenTelemetry } from "@hypabolic/trajectory-otel";

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
    cases: 27,
    operations: 46,
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

test("minimal JSONL writer preserves exact projection chunks", () => {
  const trajectory = normalizeToIR({
    source: "pi",
    transcriptBytes: readFileSync(
      resolve(repository, "conformance/cases/pi/unicode-boundaries/input.jsonl"),
    ),
  });
  const chunks = [];
  writeMinimalJsonl(trajectory, (chunk) => chunks.push(chunk));
  assert.equal(chunks.length, trajectory.records.length);
  assert.equal(chunks.join(""), projectMinimalJsonl(trajectory));
  assert.ok(chunks.every((chunk) => chunk.endsWith("\n")));
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

test("source-native invocation metadata preserves producer and signed 64-bit usage", () => {
  const claude = normalizeToIR({
    source: "claude-code",
    transcriptBytes: readFileSync(
      resolve(repository, "conformance/cases/claude-code/mixed-version/input.jsonl"),
    ),
  });
  assert.equal(claude.execution.modelInvocations.length, 2);
  assert.deepEqual(
    claude.execution.modelInvocations.map((invocation) => ({
      model: invocation.responseModel,
      response: invocation.responseId,
      stop: invocation.stopReason,
      producer: invocation.producerVersion,
      input: invocation.usage?.inputTokens,
      output: invocation.usage?.outputTokens,
      cacheRead: invocation.usage?.cacheReadTokens,
      cacheWrite: invocation.usage?.cacheWriteTokens,
    })),
    [
      {
        model: "claude-sonnet-4",
        response: "msg_legacy",
        stop: "tool_use",
        producer: "2.1.139",
        input: 100n,
        output: 20n,
        cacheRead: 10n,
        cacheWrite: 5n,
      },
      {
        model: "claude-sonnet-4-5",
        response: "msg_modern",
        stop: "end_turn",
        producer: "2.1.206",
        input: 140n,
        output: 18n,
        cacheRead: 20n,
        cacheWrite: 0n,
      },
    ],
  );

  const pi = normalizeToIR({
    source: "pi",
    transcriptBytes: readFileSync(
      resolve(repository, "conformance/cases/pi/unicode-boundaries/input.jsonl"),
    ),
  });
  assert.equal(
    pi.execution.modelInvocations[0]?.usage?.inputTokens,
    9_223_372_036_854_775_807n,
  );

  const invocation = pi.execution.modelInvocations[0];
  assert.ok(invocation);
  const telemetry = projectOpenTelemetry({
    ...pi,
    execution: {
      ...pi.execution,
      modelInvocations: [{
        ...invocation,
        startedAt: Date.parse("2025-12-31T18:30:01.500Z"),
        startedAtPrecise: "2025-12-31T18:30:01.5000000+00:00",
      }],
    },
  });
  const modelSpan = telemetry.spans.find((span) =>
    span.attributes.some((attribute) =>
      attribute.key === "gen_ai.operation.name" &&
      attribute.string_value === "chat"
    )
  );
  assert.equal(modelSpan?.kind, "CLIENT");
  assert.equal(
    modelSpan?.attributes.find((attribute) =>
      attribute.key === "gen_ai.usage.input_tokens"
    )?.integer_value,
    9_223_372_036_854_775_807n,
  );
});

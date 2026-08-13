/**
 * LS-03 / LS-04: stream state, snapshot apply, delta-apply equivalence.
 * Mirrors python/tests/test_stream_core.py and StreamingCoreTests.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  TrajectoryStream,
  anyLineTooLong,
  applyAhpActions,
  applyAhpSnapshot,
  applyAppend,
  applyDeltaToSnapshot,
  applySnapshot,
  applyStream,
  createStream,
  cursorToDict,
  finishStream,
  JSON_SAFE_INTEGER_MAX,
  TrajectoryNormalizationError,
  int64ToJson,
  resetStream,
  snapshotToDict,
  splitCompleteLines,
} from "@hypabolic/trajectory";

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const fixtures = resolve(workspace, "../conformance/cases/streaming");

async function readCase(caseId, name) {
  return new Uint8Array(await readFile(resolve(fixtures, caseId, name)));
}

test("splitCompleteLines holds unterminated tail", () => {
  const { committed, pending } = splitCompleteLines(
    new TextEncoder().encode('{"a":1}\n{"b":'),
  );
  assert.equal(new TextDecoder().decode(committed), '{"a":1}\n');
  assert.equal(new TextDecoder().decode(pending), '{"b":');
  const empty = splitCompleteLines(new Uint8Array(0));
  assert.equal(empty.committed.length, 0);
  assert.equal(empty.pending.length, 0);
  const none = splitCompleteLines(new TextEncoder().encode("no-newline"));
  assert.equal(none.committed.length, 0);
  assert.equal(new TextDecoder().decode(none.pending), "no-newline");
});

test("empty prefix produces empty snapshot and idempotent replay", () => {
  let state = createStream({ source: "pi", groupId: "stream-empty-prefix" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  const update = result.update;
  assert.equal(update.kind, "updated");
  assert.ok(update.snapshot);
  assert.ok(update.delta);
  assert.equal(update.snapshot.records.length, 0);
  assert.equal(update.snapshot.complete, false);
  assert.equal(update.cursor.groupId, "stream-empty-prefix");
  assert.equal(update.cursor.position.nextByteOffset, 0n);
  assert.equal(update.provisional.include, true);
  assert.equal(update.consumed.completeRecords, 0n);

  const result2 = applySnapshot(state, new Uint8Array(0), "gen-0");
  assert.equal(result2.update.kind, "unchanged");
  assert.equal(result2.state.cursor.prefixSha256, state.cursor.prefixSha256);
});

test("snapshot-delta equivalence holds", async () => {
  const a = await readCase("snapshot-delta-equivalence", "step-a.jsonl");
  const b = await readCase("snapshot-delta-equivalence", "step-b.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-snapshot-delta-equivalence",
  });
  let result = applySnapshot(state, a, "gen-0");
  state = result.state;
  const u1 = result.update;
  assert.equal(u1.kind, "updated");
  assert.ok(u1.snapshot && u1.delta);
  const recon0 = applyDeltaToSnapshot(null, {
    operations: u1.delta.operations,
    revision: {
      revision: Number(u1.revision.revision),
      revision_id: u1.revision.revisionId,
      parent_revision_id: u1.revision.parentRevisionId,
      complete: u1.revision.complete,
      generation: Number(u1.revision.generation),
    },
  });
  assert.deepEqual(
    recon0.records,
    snapshotToDict(u1.snapshot).records,
  );

  result = applySnapshot(state, b, "gen-0");
  const u2 = result.update;
  assert.equal(u2.kind, "updated");
  assert.ok(u2.snapshot && u2.delta);
  const prior = snapshotToDict(u1.snapshot);
  const delta = {
    schema_id: u2.delta.schemaId,
    base_revision_id: u2.delta.baseRevisionId,
    revision: {
      revision: Number(u2.revision.revision),
      revision_id: u2.revision.revisionId,
      parent_revision_id: u2.revision.parentRevisionId,
      complete: u2.revision.complete,
      generation: Number(u2.revision.generation),
    },
    operations: u2.delta.operations,
  };
  const recon = applyDeltaToSnapshot(prior, delta);
  assert.deepEqual(recon.records, snapshotToDict(u2.snapshot).records);
  assert.deepEqual(recon.diagnostics, snapshotToDict(u2.snapshot).diagnostics);
});

test("source group conflict returns reset-required atomically", async () => {
  const m1 = await readCase("source-group-conflict", "step-matching.jsonl");
  const m2 = await readCase("source-group-conflict", "step-foreign-group.jsonl");
  let state = createStream({ source: "pi", groupId: "stream-expected-group" });
  let result = applySnapshot(state, m1, "gen-0");
  state = result.state;
  assert.equal(result.update.kind, "updated");
  const priorOffset = state.cursor.position.nextByteOffset;
  result = applySnapshot(state, m2, "gen-0");
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "group-changed");
  assert.equal(result.state.cursor.position.nextByteOffset, priorOffset);
});

test("file truncate returns source-truncated", async () => {
  const longBytes = await readCase("file-truncate-reset", "step-long.jsonl");
  const shortBytes = await readCase("file-truncate-reset", "step-truncated.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-file-truncate-reset",
  });
  let result = applySnapshot(state, longBytes, "gen-0");
  state = result.state;
  assert.equal(result.update.kind, "updated");
  result = applySnapshot(state, shortBytes, "gen-0");
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "source-truncated");
  assert.equal(result.state.cursor.prefixSha256, state.cursor.prefixSha256);
});

test("cursor mismatch is atomic", () => {
  let state = createStream({ source: "pi", groupId: "g" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  const bad = {
    ...state.cursor,
    position: {
      kind: "byte",
      nextByteOffset: 99n,
      pendingByteLength: 0n,
    },
  };
  result = applySnapshot(state, new Uint8Array(0), "gen-0", bad);
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "cursor-mismatch");
  assert.equal(result.state.cursor.position.nextByteOffset, 0n);
});

test("maxLineBytes returns stream_buffer_limit", () => {
  const state = createStream({
    source: "pi",
    groupId: "g",
    maxLineBytes: 4n,
  });
  const material = new TextEncoder().encode('{"a":1}\n');
  const { update } = applySnapshot(state, material, "gen-0");
  assert.equal(update.kind, "error");
  assert.equal(update.error?.code, "stream_buffer_limit");
  assert.equal(update.error?.message, "Stream buffer limit exceeded.");
});

test("anyLineTooLong detects oversize complete lines", () => {
  const data = new TextEncoder().encode("abcd\n");
  assert.equal(anyLineTooLong(data, 4n), true);
  assert.equal(anyLineTooLong(data, 5n), false);
});

test("int64ToJson uses JSON safe integer domain and overflows", () => {
  assert.equal(int64ToJson(42n), 42);
  assert.equal(int64ToJson(JSON_SAFE_INTEGER_MAX), Number(JSON_SAFE_INTEGER_MAX));
  const big = JSON_SAFE_INTEGER_MAX + 1n;
  assert.throws(
    () => int64ToJson(big),
    (err) => err instanceof TrajectoryNormalizationError && err.code === "invalid_input",
  );
  const cursor = {
    cursorVersion: 1,
    source: "pi",
    groupId: "g",
    generation: 0n,
    position: {
      kind: "byte",
      nextByteOffset: big,
      pendingByteLength: 0n,
    },
    sourceRevision: null,
    prefixSha256: null,
  };
  assert.throws(
    () => cursorToDict(cursor),
    (err) => err instanceof TrajectoryNormalizationError && err.code === "invalid_input",
  );
});

test("truncation compare uses bigint (not Number coercion)", () => {
  // Simulate a state whose cursor offset is beyond Number.MAX_SAFE_INTEGER
  // while material is short — must still report source-truncated.
  let state = createStream({ source: "pi", groupId: "g" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  // Force an artificially large nextByteOffset after a committed snapshot.
  state = {
    ...state,
    cursor: {
      ...state.cursor,
      position: {
        kind: "byte",
        nextByteOffset: BigInt(Number.MAX_SAFE_INTEGER) + 100n,
        pendingByteLength: 0n,
      },
    },
  };
  result = applySnapshot(state, new Uint8Array(0), "gen-1");
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "source-truncated");
});

test("resetStream without material emits updated empty snapshot", () => {
  let state = createStream({ source: "pi", groupId: "g" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  result = resetStream(state, { reason: "manual" });
  assert.equal(result.update.kind, "updated");
  assert.equal(result.state.generation, 1n);
  assert.equal(result.state.cursor.generation, 1n);
  assert.ok(result.update.snapshot);
  assert.equal(result.update.snapshot.records.length, 0);
  assert.ok(result.update.reset);
  assert.equal(result.update.reset.reason, "manual");
  assert.equal(result.update.reset.requiresSnapshot, true);
});

test("resetStream with material attaches reset envelope", async () => {
  const longBytes = await readCase("file-truncate-reset", "step-long.jsonl");
  const shortBytes = await readCase("file-truncate-reset", "step-truncated.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-file-truncate-reset",
  });
  let result = applySnapshot(state, longBytes, "gen-0");
  state = result.state;
  result = resetStream(state, {
    reason: "source-truncated",
    generation: 1n,
    sourceRevision: "gen-1",
    material: shortBytes,
  });
  assert.equal(result.update.kind, "updated");
  assert.equal(result.state.generation, 1n);
  assert.equal(result.state.cursor.generation, 1n);
  assert.ok(result.update.reset);
  assert.equal(result.update.reset.reason, "source-truncated");
  assert.equal(result.update.reset.requiresSnapshot, false);
});

test("negative maxLineBytes is invalid_input", () => {
  const state = createStream({
    source: "pi",
    groupId: "g",
    maxLineBytes: -1n,
  });
  const { update } = applySnapshot(state, new TextEncoder().encode('{"a":1}\n'), "gen-0");
  assert.equal(update.kind, "error");
  assert.equal(update.error?.code, "invalid_input");
});

test("cursor offsets above int64 max are invalid_input", () => {
  let state = createStream({ source: "pi", groupId: "g" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  const tooBig = 0x8000000000000000n; // 2^63
  const bad = {
    ...state.cursor,
    position: {
      kind: "byte",
      nextByteOffset: tooBig,
      pendingByteLength: 0n,
    },
  };
  result = applySnapshot(state, new Uint8Array(0), "gen-0", bad);
  assert.equal(result.update.kind, "error");
  assert.equal(result.update.error?.code, "invalid_input");
});

test("negative nextByteOffset is invalid_input not cursor-mismatch", () => {
  let state = createStream({ source: "pi", groupId: "g" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  const bad = {
    ...state.cursor,
    position: {
      kind: "byte",
      nextByteOffset: -1n,
      pendingByteLength: 0n,
    },
  };
  result = applySnapshot(state, new Uint8Array(0), "gen-0", bad);
  assert.equal(result.update.kind, "error");
  assert.equal(result.update.error?.code, "invalid_input");
  assert.equal(result.state.cursor.position.nextByteOffset, state.cursor.position.nextByteOffset);
});

test("applyStream append-bytes validates cursor before framing", () => {
  let state = createStream({ source: "pi", groupId: "g" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  const bad = {
    ...state.cursor,
    position: {
      kind: "byte",
      nextByteOffset: 99n,
      pendingByteLength: 0n,
    },
  };
  result = applyStream(state, {
    kind: "append-bytes",
    data: new TextEncoder().encode('{"type":"message"}\n'),
    cursor: bad,
  });
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "cursor-mismatch");
  assert.equal(result.state.cursor.position.nextByteOffset, state.cursor.position.nextByteOffset);
});

test("applyAppend pending-only advances cursor on state and update", async () => {
  const incomplete = await readCase("unterminated-line-held", "step-incomplete.txt");
  let state = createStream({
    source: "pi",
    groupId: "stream-unterminated-line-held",
  });
  let result = applyAppend(state, incomplete, undefined, "gen-0");
  assert.equal(result.update.kind, "unchanged");
  assert.equal(result.state.cursor.position.pendingByteLength, BigInt(incomplete.length));
  assert.equal(result.update.cursor.position.pendingByteLength, BigInt(incomplete.length));
  assert.equal(result.state.pendingBytes.length, incomplete.length);

  const partial = await readCase("utf8-byte-boundary", "step-partial-utf8.bin");
  const tail = await readCase("utf8-byte-boundary", "step-utf8-tail.bin");
  assert.equal(partial.length, 125);
  assert.equal(tail.length, 6);
  assert.equal(tail[tail.length - 1], 0x0a);
  assert.ok(!partial.includes(0x0d) && !tail.includes(0x0d));
  state = createStream({ source: "pi", groupId: "stream-utf8-byte-boundary" });
  result = applyAppend(state, partial, undefined, "gen-0");
  assert.equal(result.update.kind, "unchanged");
  assert.equal(result.update.cursor.position.pendingByteLength, BigInt(partial.length));
  result = applyAppend(result.state, tail, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  assert.equal(result.update.cursor.position.pendingByteLength, 0n);
  assert.equal(result.state.cursor.position.pendingByteLength, 0n);
  assert.equal(result.update.cursor.position.nextByteOffset, 131n);
  assert.equal(result.update.consumed.bytes, 131n);
});

test("applyAppend enforces maxPendingBytes and maxLineBytes", () => {
  let state = createStream({ source: "pi", groupId: "g", maxPendingBytes: 5n });
  let result = applyAppend(state, new TextEncoder().encode('{"a":1'), undefined, "gen-0");
  assert.equal(result.update.kind, "error");
  assert.equal(result.update.error?.code, "stream_buffer_limit");
  assert.equal(result.state.cursor.position.nextByteOffset, 0n);

  state = createStream({ source: "pi", groupId: "g", maxLineBytes: 4n });
  result = applyAppend(state, new TextEncoder().encode('{"a":1}\n'), undefined, "gen-0");
  assert.equal(result.update.kind, "error");
  assert.equal(result.update.error?.code, "stream_buffer_limit");
});

test("applyStream ahp on non-ahp source is invalid; hermes requires hermes source", () => {
  const state = createStream({ source: "pi", groupId: "g" });
  for (const kind of ["ahp-actions", "ahp-snapshot"]) {
    const { update } = applyStream(state, { kind, data: new Uint8Array(0) });
    assert.equal(update.kind, "error");
    assert.equal(update.error?.code, "invalid_input");
    assert.match(update.error?.message ?? "", /source ahp/);
  }
  const hermes = applyStream(state, { kind: "hermes-export", data: new TextEncoder().encode("[]") });
  assert.equal(hermes.update.kind, "error");
  assert.equal(hermes.update.error?.code, "invalid_input");
  assert.match(hermes.update.error?.message ?? "", /source hermes/);
});

test("finishStream marks complete", () => {
  let state = createStream({ source: "pi", groupId: "g" });
  let result = applySnapshot(state, new Uint8Array(0), "gen-0");
  state = result.state;
  result = finishStream(state);
  assert.equal(result.update.kind, "updated");
  assert.equal(result.state.finished, true);
  assert.equal(result.update.revision.complete, true);
});

test("TrajectoryStream facade applySnapshot", () => {
  const stream = TrajectoryStream.create({
    source: "pi",
    groupId: "stream-empty-prefix",
  });
  const update = stream.applySnapshot(new Uint8Array(0), "gen-0");
  assert.equal(update.kind, "updated");
  assert.equal(stream.cursor.groupId, "stream-empty-prefix");
  const finished = stream.finish();
  assert.equal(finished.kind, "updated");
  assert.equal(stream.state.finished, true);
});

// ---- LS-05: append apply + JSONL sources ----

test("append equals prefix oracle", async () => {
  const c1 = await readCase("append-equals-prefix-oracle", "step-chunk-1.jsonl");
  const c2 = await readCase("append-equals-prefix-oracle", "step-chunk-2.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-append-equals-prefix-oracle",
  });
  let result = applyAppend(state, c1, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  result = applyAppend(result.state, c2, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  const appendIds = result.update.snapshot.records.map((r) => r.record.id);
  const appendOffset = result.state.cursor.position.nextByteOffset;

  const full = new Uint8Array(c1.length + c2.length);
  full.set(c1, 0);
  full.set(c2, c1.length);
  const oracle = applySnapshot(
    createStream({ source: "pi", groupId: "stream-append-equals-prefix-oracle" }),
    full,
    "gen-0",
  );
  assert.equal(oracle.update.kind, "updated");
  assert.deepEqual(
    oracle.update.snapshot.records.map((r) => r.record.id),
    appendIds,
  );
  assert.equal(oracle.state.cursor.position.nextByteOffset, appendOffset);
  assert.equal(oracle.state.cursor.prefixSha256, result.state.cursor.prefixSha256);
});

test("file compaction returns source-compacted", async () => {
  const original = await readCase("file-compaction-reset", "step-original.jsonl");
  const compacted = await readCase("file-compaction-reset", "step-compacted.jsonl");
  let state = createStream({
    source: "grok-build",
    groupId: "stream-file-compaction-reset",
  });
  let result = applySnapshot(state, original, "gen-0");
  assert.equal(result.update.kind, "updated");
  const prior = result.state.cursor.position.nextByteOffset;
  result = applySnapshot(result.state, compacted, "gen-compact");
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "source-compacted");
  assert.equal(result.state.cursor.position.nextByteOffset, prior);
});

test("file source-replaced returns source-replaced", async () => {
  const original = await readCase("file-source-replaced-reset", "step-original.jsonl");
  const replaced = await readCase("file-source-replaced-reset", "step-replaced.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-file-source-replaced-reset",
  });
  let result = applySnapshot(state, original, "gen-0");
  assert.equal(result.update.kind, "updated");
  const prior = result.state.cursor.position.nextByteOffset;
  result = applySnapshot(result.state, replaced, "gen-replaced");
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "source-replaced");
  assert.equal(result.state.cursor.position.nextByteOffset, prior);
});

test("duplicate append input is idempotent", async () => {
  const line = await readCase("duplicate-input-idempotent", "step-line.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-duplicate-input-idempotent",
  });
  const preCursor = state.cursor;
  let result = applyAppend(state, line, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  const prior = result.state.cursor.position.nextByteOffset;
  // True replay requires the pre-apply cursor; content alone is not enough.
  result = applyAppend(result.state, line, preCursor, "gen-0");
  assert.equal(result.update.kind, "unchanged");
  assert.equal(result.state.cursor.position.nextByteOffset, prior);
});

test("identical successive appends both commit", async () => {
  const line = await readCase("identical-successive-appends", "step-line.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-identical-successive-appends",
  });
  let result = applyAppend(state, line, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  result = applyAppend(result.state, line, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  assert.equal(result.state.committedPrefix.length, line.length * 2);
  assert.equal(result.state.cursor.position.nextByteOffset, BigInt(line.length * 2));
});

test("per-source append oracle parity", async () => {
  const cases = [
    { source: "pi", caseId: "pi-append-sequence", groupId: "stream-pi-append-sequence", steps: 3 },
    {
      source: "claude-code",
      caseId: "claude-code-append-sequence",
      groupId: "stream-claude-code-append-sequence",
      steps: 2,
    },
    { source: "codex", caseId: "codex-append-sequence", groupId: "stream-codex-append", steps: 3 },
    {
      source: "openclaw",
      caseId: "openclaw-append-sequence",
      groupId: "stream-openclaw-append",
      steps: 3,
    },
    {
      source: "grok-build",
      caseId: "grok-build-append-sequence",
      groupId: "stream-grok-build-append-sequence",
      steps: 3,
    },
  ];
  for (const { source, caseId, groupId, steps } of cases) {
    const chunks = [];
    for (let i = 1; i <= steps; i++) {
      chunks.push(await readCase(caseId, `step-${i}.jsonl`));
    }
    let state = createStream({ source, groupId });
    for (const chunk of chunks) {
      const result = applyAppend(state, chunk, undefined, "gen-0");
      assert.equal(result.update.kind, "updated", `${source} append failed`);
      state = result.state;
    }
    const appendIds = state.snapshot.records.map((r) => r.record.id);
    let fullLen = 0;
    for (const c of chunks) fullLen += c.length;
    const full = new Uint8Array(fullLen);
    let off = 0;
    for (const c of chunks) {
      full.set(c, off);
      off += c.length;
    }
    const oracle = applySnapshot(createStream({ source, groupId }), full, "gen-0");
    assert.deepEqual(
      oracle.update.snapshot.records.map((r) => r.record.id),
      appendIds,
      `${source} oracle mismatch`,
    );
  }
});

test("grok backend tool provisional then stable", async () => {
  const step1 = await readCase("grok-build-backend-provisional", "step-1.jsonl");
  const step2 = await readCase("grok-build-backend-provisional", "step-2.jsonl");
  let state = createStream({
    source: "grok-build",
    groupId: "stream-grok-build-backend-provisional",
  });
  let result = applyAppend(state, step1, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  const provisional = result.update.snapshot.records.filter((r) => r.status === "provisional");
  assert.equal(provisional.length, 1);
  assert.ok(String(provisional[0].record.content).startsWith("[backend "));
  result = applyAppend(result.state, step2, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  assert.ok(result.update.snapshot.records.every((r) => r.status === "stable"));
  const tool = result.update.snapshot.records.filter((r) => r.record.role === "tool");
  assert.equal(tool.length, 1);
  assert.equal(tool[0].record.content, "real later result");
});

// ─── LS-06 / LS-07 AHP stream parity ─────────────────────────────────────────

test("ahp snapshot provisional activeTurn maps and finalizes", async () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000b1";
  let state = createStream({
    source: "ahp",
    groupId: chat,
    ahpProtocolVersion: "0.7.0",
  });
  let result = applyAhpSnapshot(
    state,
    await readCase("provisional-to-stable", "step-provisional.json"),
    "ahp-rev-1",
  );
  state = result.state;
  const u1 = result.update;
  assert.equal(u1.kind, "updated");
  assert.deepEqual([...u1.provisional.provisionalIds], ["prov-active:part-md-active-1"]);
  assert.equal(u1.cursor.position.kind, "snapshot-revision");
  assert.equal(u1.cursor.position.revision, "ahp-rev-1");
  assert.ok(u1.snapshot.records.some((r) => r.status === "provisional"));

  const dup = applyAhpSnapshot(
    state,
    await readCase("provisional-to-stable", "step-provisional.json"),
    "ahp-rev-1",
  );
  assert.equal(dup.update.kind, "unchanged");

  result = applyAhpSnapshot(
    state,
    await readCase("provisional-to-stable", "step-stable.json"),
    "ahp-rev-2",
  );
  const u2 = result.update;
  assert.equal(u2.kind, "updated");
  assert.deepEqual([...u2.provisional.provisionalIds], []);
  assert.ok(u2.provisional.finalizedIds.includes("prov-active:part-md-active-1"));
  const recon = applyDeltaToSnapshot(snapshotToDict(u1.snapshot), {
    schema_id: u2.delta.schemaId,
    base_revision_id: u2.delta.baseRevisionId,
    revision: {
      revision: Number(u2.revision.revision),
      revision_id: u2.revision.revisionId,
      parent_revision_id: u2.revision.parentRevisionId,
      complete: u2.revision.complete,
      generation: Number(u2.revision.generation),
    },
    operations: u2.delta.operations,
  });
  assert.deepEqual(recon.records, snapshotToDict(u2.snapshot).records);
});

test("ahp action turn flow and sequence gap freezes cursor", async () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
  let state = createStream({
    source: "ahp",
    groupId: chat,
    ahpProtocolVersion: "0.7.0",
  });
  let result = applyAhpActions(
    state,
    await readCase("ahp-action-turn-flow", "step-actions.jsonl"),
  );
  state = result.state;
  const u = result.update;
  assert.equal(u.kind, "updated");
  assert.equal(u.cursor.position.kind, "ahp-server-seq");
  assert.equal(u.cursor.position.lastServerSeq, 5n);
  assert.equal(u.cursor.position.nextServerSeq, 6n);
  const roles = u.snapshot.records.map((r) => r.record.role);
  assert.ok(roles.includes("user"));
  assert.ok(roles.includes("assistant"));
  assert.ok(
    u.snapshot.records
      .filter((r) => r.record.role !== "meta")
      .every((r) => r.status === "stable"),
  );

  const prior = state.cursor;
  const gap = applyAhpActions(
    state,
    await readCase("ahp-action-sequence-gap", "step-gap.jsonl"),
  );
  assert.equal(gap.update.kind, "reset-required");
  assert.equal(gap.update.reset.reason, "sequence-gap");
  assert.equal(gap.state.cursor.position.kind, prior.position.kind);
  assert.equal(gap.state.cursor.position.lastServerSeq, prior.position.lastServerSeq);
  assert.equal(gap.update.cursor.position.lastServerSeq, prior.position.lastServerSeq);
});

test("ahp unknown and foreign channel diagnostics are content-safe", async () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
  let state = createStream({
    source: "ahp",
    groupId: chat,
    ahpProtocolVersion: "0.7.0",
  });
  state = applyAhpActions(
    state,
    await readCase("ahp-action-unknown-foreign", "step-baseline.jsonl"),
  ).state;
  const result = applyAhpActions(
    state,
    await readCase("ahp-action-unknown-foreign", "step-mixed.jsonl"),
  );
  assert.equal(result.update.kind, "updated");
  const codes = new Set(result.update.diagnostics.map((d) => d.code));
  assert.ok(codes.has("ahp_unknown_action"));
  assert.ok(codes.has("ahp_foreign_channel"));
  for (const d of result.update.diagnostics) {
    assert.ok(!d.message.includes("notARealAction"));
    assert.ok(!d.message.includes("SECRET"));
  }
});

test("ahp action path equals independent snapshot path", async () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
  const actions = await readCase("ahp-action-equals-snapshot", "step-actions.jsonl");
  const snapshot = await readCase("ahp-action-equals-snapshot", "step-snapshot.json");

  const uAct = applyAhpActions(
    createStream({ source: "ahp", groupId: chat, ahpProtocolVersion: "0.7.0" }),
    actions,
  ).update;
  assert.equal(uAct.kind, "updated");
  assert.ok(uAct.snapshot);

  const uSnap = applyAhpSnapshot(
    createStream({ source: "ahp", groupId: chat, ahpProtocolVersion: "0.7.0" }),
    snapshot,
    "ahp-equiv-1",
  ).update;
  assert.equal(uSnap.kind, "updated");
  assert.ok(uSnap.snapshot);

  const actIds = uAct.snapshot.records.map((r) => [r.record.id, r.status]);
  const snapIds = uSnap.snapshot.records.map((r) => [r.record.id, r.status]);
  assert.deepEqual(actIds, snapIds);

  const nonMeta = (records) =>
    records
      .filter((r) => r.record.role !== "meta")
      .map((r) => [r.record.role, r.record.content]);
  assert.deepEqual(nonMeta(uAct.snapshot.records), nonMeta(uSnap.snapshot.records));
});

test("ahp action true-replay is idempotent", async () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
  const data = await readCase("ahp-action-turn-flow", "step-actions.jsonl");
  let state = createStream({
    source: "ahp",
    groupId: chat,
    ahpProtocolVersion: "0.7.0",
  });
  const pre = state.cursor;
  const first = applyAhpActions(state, data);
  assert.equal(first.update.kind, "updated");
  const second = applyAhpActions(first.state, data, pre);
  assert.equal(second.update.kind, "unchanged");
});

test("ahp action batch rejects non-monotonic reorder and mixed sequencing", () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c2";
  const enc = new TextEncoder();
  const run = (lines) => {
    const state = createStream({ source: "ahp", groupId: chat });
    const data = enc.encode(lines.join("\n") + "\n");
    return applyAhpActions(state, data);
  };
  let r = run([
    `{"channel":"${chat}","serverSeq":2,"action":{"type":"chat/activityChanged","activity":"a"}}`,
    `{"channel":"${chat}","serverSeq":1,"action":{"type":"chat/activityChanged","activity":"b"}}`,
  ]);
  assert.equal(r.update.kind, "error");
  assert.equal(r.state.ahpLastServerSeq, null);
  r = run([
    `{"channel":"${chat}","serverSeq":1,"action":{"type":"chat/activityChanged","activity":"a"}}`,
    `{"channel":"${chat}","serverSeq":1,"action":{"type":"chat/activityChanged","activity":"b"}}`,
  ]);
  assert.equal(r.update.kind, "error");
  r = run([
    `{"channel":"${chat}","serverSeq":1,"action":{"type":"chat/activityChanged","activity":"a"}}`,
    `{"channel":"${chat}","action":{"type":"chat/activityChanged","activity":"b"}}`,
  ]);
  assert.equal(r.update.kind, "error");
  assert.match(r.update.error.message, /mix sequenced/);
});

test("ahp activeTurn provisional ids stay stable across multi-part growth", async () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000b2";
  let state = createStream({
    source: "ahp",
    groupId: chat,
    ahpProtocolVersion: "0.7.0",
  });
  let r = applyAhpSnapshot(
    state,
    await readCase("ahp-snapshot-active-turn-multipart", "step-1.json"),
    "ahp-mp-1",
  );
  state = r.state;
  assert.ok(r.update.provisional.provisionalIds.includes("prov-active:part-md-multi-1"));
  r = applyAhpSnapshot(
    state,
    await readCase("ahp-snapshot-active-turn-multipart", "step-2.json"),
    "ahp-mp-2",
  );
  state = r.state;
  assert.ok(r.update.provisional.provisionalIds.includes("prov-active:part-md-multi-1"));
  assert.ok(r.update.provisional.provisionalIds.includes("prov-active:tool-call-multi-1"));
  r = applyAhpSnapshot(
    state,
    await readCase("ahp-snapshot-active-turn-multipart", "step-3.json"),
    "ahp-mp-3",
  );
  assert.ok(r.update.provisional.finalizedIds.includes("prov-active:part-md-multi-1"));
  assert.ok(r.update.provisional.finalizedIds.includes("prov-active:tool-call-multi-1"));
});

test("stream diagnostics content-safe sentinels (H2)", () => {
  const secretTool = "SECRET_TOOL_ID_xyzzy_do_not_leak";
  const secretPath = "/Users/SECRET_PATH_xyzzy/private.jsonl";
  const secretAhp = "SECRET_AHP_BODY_xyzzy_do_not_leak";
  const enc = new TextEncoder();

  const session = enc.encode(
    '{"type":"session","version":3,"id":"g","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}\n',
  );
  const user = enc.encode(
    '{"type":"message","id":"m1","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hi"}],"timestamp":"2026-01-01T00:00:01.000Z"}}\n',
  );
  const toolCall = (mid, tid) =>
    enc.encode(
      `{"type":"message","id":"${mid}","timestamp":"2026-01-01T00:00:02.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"${tid}","name":"read","arguments":{"path":"/tmp/x"}}],"timestamp":"2026-01-01T00:00:02.000Z"}}\n`,
    );

  const assertDiagSafe = (update, ...sentinels) => {
    for (const d of update.diagnostics ?? []) {
      for (const s of sentinels) assert.ok(!d.message.includes(s), d.message);
    }
    for (const d of update.snapshot?.diagnostics ?? []) {
      for (const s of sentinels) assert.ok(!d.message.includes(s), d.message);
    }
    for (const op of update.delta?.operations ?? []) {
      if (op.diagnostic) {
        const msg = op.diagnostic.message ?? "";
        for (const s of sentinels) assert.ok(!msg.includes(s), msg);
      }
    }
    if (update.error) {
      for (const s of sentinels) assert.ok(!update.error.message.includes(s));
    }
  };

  let r = applySnapshot(
    createStream({ source: "pi", groupId: "g" }),
    concatBytes(session, user, toolCall("a1", secretTool), toolCall("a2", secretTool)),
    "gen-0",
  );
  assert.equal(r.update.kind, "updated");
  assert.ok(r.update.diagnostics.some((d) => d.code === "duplicate_tool_call_id"));
  assertDiagSafe(r.update, secretTool);
  for (const d of r.update.diagnostics) {
    assert.equal(d.message.includes(secretTool), false);
  }

  const badLine = enc.encode(`{not-json contains ${secretPath} and ${secretTool}}\n`);
  r = applySnapshot(
    createStream({ source: "pi", groupId: "g" }),
    concatBytes(session, user, badLine),
    "gen-0",
  );
  assert.equal(r.update.kind, "updated");
  assert.ok(r.update.diagnostics.some((d) => d.code === "invalid_json_line"));
  const wire = JSON.stringify(snapshotToDict(r.update.snapshot));
  assert.ok(!wire.includes(secretPath));
  assert.ok(!wire.includes(secretTool));
  assertDiagSafe(r.update, secretTool, secretPath);

  r = applyAhpSnapshot(
    createStream({ source: "ahp", groupId: "g" }),
    enc.encode(`{"not-valid":"${secretAhp}"}`),
    "gen-0",
  );
  assert.equal(r.update.kind, "error");
  assert.ok(r.update.error);
  assert.ok(!r.update.error.message.includes(secretAhp));
  assert.ok(!JSON.stringify(r.update.error).includes(secretAhp));
});

test("default reset policy returns reset-required on truncate", async () => {
  const longBytes = await readCase("file-truncate-reset", "step-long.jsonl");
  const shortBytes = await readCase("file-truncate-reset", "step-truncated.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-file-truncate-reset",
  });
  let result = applySnapshot(state, longBytes, "gen-0");
  state = result.state;
  const priorGen = state.cursor.generation;
  result = applySnapshot(state, shortBytes, "gen-1");
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "source-truncated");
  assert.equal(result.state.cursor.generation, priorGen);
});

test("auto-reset with replacement material installs a new generation", async () => {
  const longBytes = await readCase("file-truncate-reset", "step-long.jsonl");
  const shortBytes = await readCase("file-truncate-reset", "step-truncated.jsonl");
  let state = createStream({
    source: "pi",
    groupId: "stream-file-truncate-reset",
    resetPolicy: "auto-reset",
  });
  let result = applySnapshot(state, longBytes, "gen-0");
  state = result.state;
  result = applySnapshot(state, shortBytes, "gen-1");
  assert.equal(result.update.kind, "updated");
  assert.equal(result.state.generation, 1n);
  assert.equal(result.state.cursor.generation, 1n);
  assert.equal(result.update.reset?.reason, "source-truncated");
  assert.equal(result.update.reset?.requiresSnapshot, false);
  assert.equal(result.state.cursor.sourceRevision, "gen-1");
  assert.equal(result.state.cursor.position.nextByteOffset, BigInt(shortBytes.length));
});

test("auto-reset without replacement material still returns reset-required", async () => {
  const chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
  let state = createStream({
    source: "ahp",
    groupId: chat,
    resetPolicy: "auto-reset",
  });
  let result = applyAhpActions(
    state,
    await readCase("ahp-action-turn-flow", "step-actions.jsonl"),
  );
  assert.equal(result.update.kind, "updated");
  state = result.state;
  const priorGen = state.cursor.generation;
  result = applyAhpActions(
    state,
    await readCase("ahp-action-sequence-gap", "step-gap.jsonl"),
  );
  assert.equal(result.update.kind, "reset-required");
  assert.equal(result.update.reset?.reason, "sequence-gap");
  assert.equal(result.state.cursor.generation, priorGen);
});

test("unknown delta op is invalid_input and leaves prior snapshot unchanged", () => {
  const prior = {
    schema_id: "trajectory-stream-v1",
    source: "pi",
    group_id: "g",
    revision: {
      revision: 1,
      revision_id: "rev-1",
      parent_revision_id: null,
      complete: false,
      generation: 0,
    },
    records: [],
    diagnostics: [],
    complete: false,
  };
  const before = JSON.stringify(prior);
  const delta = {
    schema_id: "trajectory-stream-v1",
    base_revision_id: "rev-1",
    revision: prior.revision,
    operations: [{ op: "merge", record_id: "x" }],
  };
  assert.throws(
    () => applyDeltaToSnapshot(prior, delta),
    (err) => err instanceof TrajectoryNormalizationError && err.code === "invalid_input",
  );
  assert.equal(JSON.stringify(prior), before);
});

function concatBytes(...parts) {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

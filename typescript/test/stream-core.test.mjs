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
  applyAppend,
  applyDeltaToSnapshot,
  applySnapshot,
  applyStream,
  createStream,
  cursorToDict,
  finishStream,
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

test("int64ToJson preserves values outside MAX_SAFE_INTEGER", () => {
  const big = BigInt(Number.MAX_SAFE_INTEGER) + 1n;
  assert.equal(typeof int64ToJson(big), "string");
  assert.equal(int64ToJson(big), big.toString());
  assert.equal(int64ToJson(42n), 42);
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
  const dict = cursorToDict(cursor);
  assert.equal(dict.position.next_byte_offset, big.toString());
  assert.equal(dict.position.pending_byte_length, 0);
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
  state = createStream({ source: "pi", groupId: "stream-utf8-byte-boundary" });
  result = applyAppend(state, partial, undefined, "gen-0");
  assert.equal(result.update.kind, "unchanged");
  assert.equal(result.update.cursor.position.pendingByteLength, BigInt(partial.length));
  result = applyAppend(result.state, tail, undefined, "gen-0");
  assert.equal(result.update.kind, "updated");
  assert.equal(result.update.cursor.position.pendingByteLength, 0n);
  assert.equal(result.state.cursor.position.pendingByteLength, 0n);
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

test("applyStream ahp/hermes kinds return stream_resync_required", () => {
  const state = createStream({ source: "pi", groupId: "g" });
  for (const kind of ["ahp-actions", "ahp-snapshot"]) {
    const { update } = applyStream(state, { kind });
    assert.equal(update.kind, "error");
    assert.equal(update.error?.code, "stream_resync_required");
    assert.match(update.error?.message ?? "", /AHP stream apply/);
  }
  const hermes = applyStream(state, { kind: "hermes-export" });
  assert.equal(hermes.update.kind, "error");
  assert.equal(hermes.update.error?.code, "stream_resync_required");
  assert.match(hermes.update.error?.message ?? "", /Hermes export/);
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

import assert from "node:assert/strict";
import { chmod, mkdir, rename, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import {
  FileStreamHostError,
  FileTrajectoryStream,
  HOST_PATH_OUTSIDE_ROOT,
  HOST_ROOT_REQUIRED,
  splitCompleteLines,
} from "@hypabolic/trajectory-node";

const SESSION_LINE =
  '{"type":"session","version":3,"id":"stream-file-io-ts","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}\n';
const USER_LINE =
  '{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hello"}]},"sessionId":"stream-file-io-ts"}\n';

async function tempRoot() {
  const root = join(tmpdir(), `traj-file-io-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  await mkdir(root, { recursive: true });
  return root;
}

test("splitCompleteLines holds incomplete tail", () => {
  const data = new TextEncoder().encode("abc\ndef");
  const { complete, pending } = splitCompleteLines(data);
  assert.equal(new TextDecoder().decode(complete), "abc\n");
  assert.equal(new TextDecoder().decode(pending), "def");
});

test("file growth and incomplete line", async () => {
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  await writeFile(path, "");

  const stream = FileTrajectoryStream.open({
    root,
    path,
    source: "pi",
    groupId: "stream-file-io-ts",
  });

  const u0 = await stream.poll();
  assert.ok(u0);
  assert.equal(u0.kind, "updated");
  assert.equal(u0.snapshot?.records.length, 0);

  await writeFile(path, SESSION_LINE + USER_LINE.slice(0, 40));
  const u1 = await stream.poll();
  assert.ok(u1);
  assert.equal(u1.kind, "updated");
  // Session meta committed; incomplete user line held at host — not materialized.
  const recordsAfterPartial = u1.snapshot?.records.length ?? 0;
  assert.ok(recordsAfterPartial >= 1);
  assert.ok(!(u1.snapshot?.records ?? []).some((r) => r.record?.role === "user"));

  await writeFile(path, SESSION_LINE + USER_LINE);
  const u2 = await stream.poll();
  assert.ok(u2);
  assert.equal(u2.kind, "updated");
  assert.ok((u2.snapshot?.records.length ?? 0) > recordsAfterPartial);
  assert.ok((u2.snapshot?.records ?? []).some((r) => r.record?.role === "user"));
  for (const d of u2.diagnostics) {
    assert.ok(!d.message.includes(path));
  }
  stream.close();
});

test("finish flushes host pending incomplete line", async () => {
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  const incompleteUser = USER_LINE.trimEnd();
  await writeFile(path, SESSION_LINE + incompleteUser);

  const stream = FileTrajectoryStream.open({
    root,
    path,
    source: "pi",
    groupId: "stream-file-io-ts",
  });
  const u0 = await stream.poll();
  assert.ok(u0);
  assert.equal(u0.kind, "updated");
  assert.ok(!(u0.snapshot?.records ?? []).some((r) => r.record?.role === "user"));
  const recordsBeforeFinish = u0.snapshot?.records.length ?? 0;

  const finished = stream.finish();
  assert.ok(finished.kind === "updated" || finished.kind === "unchanged");
  assert.equal(stream.state.finished, true);
  assert.ok((finished.snapshot?.records.length ?? 0) > recordsBeforeFinish);
  assert.ok((finished.snapshot?.records ?? []).some((r) => r.record?.role === "user"));
  stream.close();
});

test("coalesced growth", async () => {
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  await writeFile(path, SESSION_LINE);

  const stream = FileTrajectoryStream.open({
    root,
    path,
    source: "pi",
    groupId: "stream-file-io-ts",
  });
  assert.ok(await stream.poll());

  await writeFile(path, SESSION_LINE + USER_LINE);
  const update = await stream.poll();
  assert.ok(update);
  assert.equal(update.kind, "updated");
  assert.ok((update.snapshot?.records.length ?? 0) >= 1);
  stream.close();
});

test("truncation surfaces core reset-required", async () => {
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  await writeFile(path, SESSION_LINE + USER_LINE);

  const stream = FileTrajectoryStream.open({
    root,
    path,
    source: "pi",
    groupId: "stream-file-io-ts",
  });
  const first = await stream.poll();
  assert.ok(first);
  assert.equal(first.kind, "updated");

  await writeFile(path, SESSION_LINE);
  const update = await stream.poll();
  assert.ok(update);
  assert.equal(update.kind, "reset-required");
  assert.ok(update.reset);
  stream.close();
});

test("path outside root is host error", async () => {
  const root = await tempRoot();
  const other = join(tmpdir(), `traj-out-${Date.now()}`);
  await mkdir(other, { recursive: true });
  const outside = join(other, "x.jsonl");
  await writeFile(outside, "\n");

  assert.throws(
    () =>
      FileTrajectoryStream.open({
        root,
        path: outside,
        source: "pi",
      }),
    (error) =>
      error instanceof FileStreamHostError && error.code === HOST_PATH_OUTSIDE_ROOT,
  );
});

test("root required", () => {
  assert.throws(
    () =>
      FileTrajectoryStream.open({
        root: "",
        path: "/tmp/x.jsonl",
        source: "pi",
      }),
    (error) => error instanceof FileStreamHostError && error.code === HOST_ROOT_REQUIRED,
  );
});

test("permission denied is host error", async (t) => {
  if (process.platform === "win32") {
    t.skip("chmod permission model differs");
    return;
  }
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  await writeFile(path, SESSION_LINE);
  await chmod(path, 0);
  try {
    const stream = FileTrajectoryStream.open({
      root,
      path,
      source: "pi",
      groupId: "x",
    });
    await assert.rejects(
      () => stream.poll(),
      (error) =>
        error instanceof FileStreamHostError &&
        (error.code === "io_permission" || error.code === "io_error") &&
        !error.message.includes(path),
    );
  } finally {
    await chmod(path, 0o600);
  }
});

test("finish failed pending flush retains host buffer (H4)", async () => {
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  await writeFile(path, "");

  const stream = FileTrajectoryStream.open({
    root,
    path,
    source: "pi",
    groupId: "stream-file-io-ts",
    stream: {
      source: "pi",
      groupId: "stream-file-io-ts",
      maxPendingBytes: 16n,
      maxLineBytes: 16n,
    },
  });
  const u0 = await stream.poll();
  assert.ok(u0);
  assert.equal(u0.kind, "updated");
  const cursorBefore = stream.state.cursor;
  assert.equal(stream.state.finished, false);

  const incomplete = '{"type":"message","id":"pending-too-long","x":"' + "y".repeat(80);
  await writeFile(path, incomplete);
  assert.equal(await stream.poll(), null);

  const finished = stream.finish();
  assert.equal(finished.kind, "error");
  assert.equal(finished.error?.code, "stream_buffer_limit");
  assert.equal(stream.state.finished, false);
  assert.equal(stream.state.cursor.generation, cursorBefore.generation);
  // Pending retained: a second finish still fails the same way.
  const again = stream.finish();
  assert.equal(again.kind, "error");
  assert.equal(again.error?.code, "stream_buffer_limit");
  assert.equal(stream.state.finished, false);
  stream.close();
});

test("same-size in-place replace is detected with default reconcileEvery=0", async () => {
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  const original = SESSION_LINE + USER_LINE;
  const replaced = SESSION_LINE + USER_LINE.replace('"hello"', '"hallo"');
  assert.equal(original.length, replaced.length);
  await writeFile(path, original);

  const stream = FileTrajectoryStream.open({
    root,
    path,
    source: "pi",
    groupId: "stream-file-io-ts",
  });
  const first = await stream.poll();
  assert.ok(first);
  assert.equal(first.kind, "updated");

  await writeFile(path, replaced);
  const update = await stream.poll();
  assert.ok(update);
  assert.ok(update.kind === "updated" || update.kind === "reset-required");
  if (update.kind === "updated") {
    const texts = (update.snapshot?.records ?? []).map((r) => r.record?.content);
    assert.ok(texts.some((t) => typeof t === "string" && t.includes("hallo")));
    assert.ok(!texts.some((t) => typeof t === "string" && t.includes("hello")));
  }
  stream.close();
});

test("same-size atomic replace is detected", async () => {
  const root = await tempRoot();
  const path = join(root, "session.jsonl");
  const original = SESSION_LINE + USER_LINE;
  const replaced = SESSION_LINE + USER_LINE.replace('"hello"', '"hallo"');
  assert.equal(original.length, replaced.length);
  await writeFile(path, original);

  const stream = FileTrajectoryStream.open({
    root,
    path,
    source: "pi",
    groupId: "stream-file-io-ts",
  });
  assert.ok(await stream.poll());

  const tmp = join(root, "session.jsonl.tmp");
  await writeFile(tmp, replaced);
  await rename(tmp, path);
  const update = await stream.poll();
  assert.ok(update);
  assert.ok(update.kind === "updated" || update.kind === "reset-required");
  stream.close();
});

// Silence unused import when bundlers analyze (pathToFileURL available for debug).
void pathToFileURL;

import assert from "node:assert/strict";
import { chmod, mkdir, writeFile } from "node:fs/promises";
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

  await writeFile(path, SESSION_LINE + USER_LINE);
  const u2 = await stream.poll();
  assert.ok(u2);
  assert.equal(u2.kind, "updated");
  assert.ok((u2.snapshot?.records.length ?? 0) >= 1);
  for (const d of u2.diagnostics) {
    assert.ok(!d.message.includes(path));
  }
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

// Silence unused import when bundlers analyze (pathToFileURL available for debug).
void pathToFileURL;

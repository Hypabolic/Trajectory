/**
 * LS-11 sample CLI stream / ahp-stream (temp stores + FakeAhpHost only).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, writeFile, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLI = join(__dirname, "../packages/trajectory-cli/dist/cli.js");
const REPO = join(__dirname, "../..");
const ACTIONS = join(
  REPO,
  "conformance/cases/streaming/ahp-action-turn-flow/step-actions.jsonl",
);
const CHAT = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";
const CURSOR_ID = "019f0000-0000-7000-8000-00000000c0a1";

const SESSION_LINE =
  '{"type":"session","version":3,"id":"ls11-stream-ts","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}\n';
const USER_LINE =
  '{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hello"}]},"sessionId":"ls11-stream-ts"}\n';
const CURSOR_LINE =
  '{"role":"user","message":{"content":[{"type":"text","text":"hello"}]}}\n' +
  '{"role":"assistant","message":{"content":[{"type":"text","text":"done"}]}}\n';

function runCli(args, { timeoutMs = 15000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CLI, ...args], {
      cwd: REPO,
      env: process.env,
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`CLI timed out: ${args.join(" ")}`));
    }, timeoutMs);
    child.stdout.on("data", (c) => {
      stdout += c;
    });
    child.stderr.on("data", (c) => {
      stderr += c;
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code: code ?? 1, stdout, stderr });
    });
  });
}

test("help mentions stream and not a daemon", async () => {
  const { code, stdout } = await runCli(["help"]);
  assert.equal(code, 0);
  assert.match(stdout, /stream/);
  assert.match(stdout, /ahp-stream/);
  assert.match(stdout.toLowerCase(), /not a daemon/);
  assert.match(stdout, /--watch/);
});

test("stream temp file emits snapshot+delta, privacy default", async () => {
  const root = await mkdtemp(join(tmpdir(), "traj-ls11-"));
  const path = join(root, "session.jsonl");
  await writeFile(path, SESSION_LINE + USER_LINE);
  const { code, stdout, stderr } = await runCli([
    "stream",
    "--source",
    "pi",
    "--root",
    root,
    "--path",
    path,
    "--emit",
    "snapshot+delta",
    "--max-updates",
    "1",
  ]);
  assert.equal(code, 0, stderr);
  assert.match(stdout, /stream update/);
  assert.match(stdout, /snapshot/);
  assert.match(stdout, /delta/);
  assert.match(stdout, /live tail/);
  assert.match(stdout, /Content omitted/);
  assert.match(stdout.toLowerCase(), /not a daemon/);
  assert.doesNotMatch(stdout, /hello/);
});

test("stream rejects ahp source", async () => {
  const { code, stderr } = await runCli([
    "stream",
    "--source",
    "ahp",
    "--path",
    "/tmp/x.jsonl",
  ]);
  assert.equal(code, 2);
  assert.match(stderr, /invalid_input/);
});

test("ahp-stream fake host actions", async () => {
  const { code, stdout, stderr } = await runCli([
    "ahp-stream",
    "--url",
    "fake://demo",
    "--chat",
    CHAT,
    "--actions-path",
    ACTIONS,
    "--emit",
    "snapshot+delta",
    "--max-updates",
    "1",
  ]);
  assert.equal(code, 0, stderr);
  assert.match(stdout, /stream update|ready/i);
  assert.match(stdout, /snapshot|delta/);
  assert.match(stdout, /Content omitted/);
  assert.doesNotMatch(stdout, /test-token/);
});

test("browse --watch listed session", async () => {
  const root = await mkdtemp(join(tmpdir(), "traj-ls11-browse-"));
  const dir = join(root, "sessions", "demo");
  await mkdir(dir, { recursive: true });
  const path = join(dir, "watch-me.jsonl");
  await writeFile(path, SESSION_LINE + USER_LINE);
  const { code, stdout, stderr } = await runCli([
    "browse",
    "--source",
    "pi",
    "--root",
    root,
    "--id",
    "watch-me",
    "--watch",
    "--emit",
    "snapshot+delta",
    "--max-updates",
    "1",
  ]);
  assert.equal(code, 0, stderr);
  assert.match(stdout, /stream update/);
  assert.match(stdout, /snapshot/);
  assert.match(stdout, /delta/);
  assert.match(stdout, /live tail/);
  assert.match(stdout.toLowerCase(), /not a daemon/);
  assert.doesNotMatch(stdout, /hello/);
});

test("cursor list, show, and browse --watch use the listed session id", async () => {
  const root = await mkdtemp(join(tmpdir(), "traj-ls11-cursor-"));
  const path = join(
    root,
    "projects",
    "%2Fworkspace%2Fdemo",
    "agent-transcripts",
    CURSOR_ID,
    `${CURSOR_ID}.jsonl`,
  );
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, CURSOR_LINE);

  const listed = await runCli([
    "list",
    "--source",
    "cursor",
    "--root",
    root,
    "--limit",
    "5",
  ]);
  assert.equal(listed.code, 0, listed.stderr);
  assert.match(listed.stdout, new RegExp(CURSOR_ID));

  const shown = await runCli([
    "show",
    "--source",
    "cursor",
    "--root",
    root,
    "--id",
    CURSOR_ID,
  ]);
  assert.equal(shown.code, 0, shown.stderr);
  assert.match(shown.stdout, new RegExp(CURSOR_ID));
  assert.match(shown.stdout, /Content omitted/);

  const browsed = await runCli([
    "browse",
    "--source",
    "cursor-agent",
    "--root",
    root,
    "--id",
    CURSOR_ID,
    "--watch",
    "--emit",
    "snapshot+delta",
    "--max-updates",
    "1",
  ]);
  assert.equal(browsed.code, 0, browsed.stderr);
  assert.match(browsed.stdout, /stream update/);
  assert.match(browsed.stdout, /snapshot/);
  assert.match(browsed.stdout, /delta/);
  assert.match(browsed.stdout, /Content omitted/);
  assert.doesNotMatch(browsed.stdout, /hello/);
});

test("browse --watch rejects ahp", async () => {
  const { code, stderr } = await runCli([
    "browse",
    "--source",
    "ahp",
    "--root",
    "/tmp",
    "--id",
    "x",
    "--watch",
  ]);
  assert.equal(code, 2);
  assert.match(stderr, /invalid_input/);
});

test("stream without path or id requires tty", async () => {
  const { code, stderr } = await runCli(["stream", "--source", "pi"]);
  assert.equal(code, 2);
  assert.match(stderr, /invalid_input/);
});

test("ahp-stream rejects ws url", async () => {
  const { code, stderr } = await runCli([
    "ahp-stream",
    "--url",
    "ws://localhost:9999",
    "--chat",
    CHAT,
  ]);
  assert.equal(code, 2);
  assert.match(stderr, /fake:\/\//);
});

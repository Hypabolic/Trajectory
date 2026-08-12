#!/usr/bin/env node
/**
 * Local sample CLI for browsing agent sessions with Trajectory.
 * Not a published package — workspace-only dependency on core packages.
 * Consumer process only — not a Trajectory daemon.
 */
import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { homedir } from "node:os";
import { dirname, join, basename, resolve as resolvePath } from "node:path";
import { readFile, access } from "node:fs/promises";
import { constants as fsConstants, existsSync } from "node:fs";

import {
  normalizeToIR,
  projectHypabolic,
  projectLetta,
  TrajectoryNormalizationError,
  type StreamOptions,
  type StreamUpdate,
  type TrajectoryIR,
  type TrajectorySource,
} from "@hypabolic/trajectory";
import {
  FileStreamHostError,
  FileTrajectoryStream,
  listAhpTrajectories,
  listClaudeCodeTrajectories,
  listCodexTrajectories,
  listGrokBuildTrajectories,
  listHermesTrajectories,
  listOpenClawTrajectories,
  listPiTrajectories,
  type TrajectoryListing,
  type TrajectoryListingPage,
} from "@hypabolic/trajectory-node";
import {
  AhpStreamClient,
  FakeAhpHost,
  InMemoryAhpTransportPair,
  type AhpClientEvent,
  type AhpTransport,
} from "@hypabolic/trajectory-ahp";

const SOURCES = ["pi", "claude-code", "codex", "openclaw", "hermes", "ahp", "grok-build"] as const;
type SourceName = (typeof SOURCES)[number];
const STREAM_FILE_SOURCES = ["pi", "claude-code", "codex", "openclaw", "grok-build"] as const;
type StreamFileSource = (typeof STREAM_FILE_SOURCES)[number];
type EmitMode = "snapshot+delta" | "snapshot" | "delta";

const DIM = "\u001b[2m";
const BOLD = "\u001b[1m";
const RED = "\u001b[31m";
const YELLOW = "\u001b[33m";
const CYAN = "\u001b[36m";
const RESET = "\u001b[0m";

interface CliArgs {
  command: "browse" | "list" | "show" | "stream" | "ahp-stream" | "help";
  source?: SourceName;
  root?: string;
  path?: string;
  id?: string;
  limit: number;
  showContent: boolean;
  format: "both" | "messages" | "hypabolic";
  emit: EmitMode;
  follow: boolean;
  interval: number;
  maxUpdates?: number;
  url?: string;
  chat?: string;
  fromSeq?: number;
  token?: string;
  snapshotPath?: string;
  actionsPath?: string;
}

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2));
  if (args.command === "help") {
    printHelp();
    return 0;
  }

  try {
    if (args.command === "list") return await runList(args);
    if (args.command === "show") return await runShow(args);
    if (args.command === "stream") return await runStream(args);
    if (args.command === "ahp-stream") return await runAhpStream(args);
    return await runBrowse(args);
  } catch (error) {
    return handleError(error);
  }
}

function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = {
    command: "browse",
    limit: 50,
    showContent: false,
    format: "both",
    emit: "snapshot+delta",
    follow: false,
    interval: 0.05,
  };

  if (argv.length === 0) return args;

  const head = argv[0];
  let i = 0;
  if (
    head === "list" ||
    head === "show" ||
    head === "browse" ||
    head === "stream" ||
    head === "ahp-stream" ||
    head === "help" ||
    head === "-h" ||
    head === "--help"
  ) {
    args.command = head === "-h" || head === "--help" ? "help" : (head as CliArgs["command"]);
    i = 1;
  }

  while (i < argv.length) {
    const token = argv[i]!;
    const next = argv[i + 1];
    if ((token === "-s" || token === "--source") && next) {
      args.source = parseSource(next);
      i += 2;
      continue;
    }
    if ((token === "-r" || token === "--root") && next) {
      args.root = expandHome(next);
      i += 2;
      continue;
    }
    if ((token === "-p" || token === "--path") && next) {
      args.path = expandHome(next);
      i += 2;
      continue;
    }
    if (token === "--id" && next) {
      args.id = next;
      i += 2;
      continue;
    }
    if (token === "--limit" && next) {
      args.limit = Number(next);
      i += 2;
      continue;
    }
    if (token === "--format" && next) {
      if (next === "letta" || next === "messages" || next === "hypabolic" || next === "both")
        args.format = next === "letta" ? "messages" : next;
      else throw new TrajectoryNormalizationError("invalid_input", `Unknown format '${next}'.`);
      i += 2;
      continue;
    }
    if (token === "--show-content") {
      args.showContent = true;
      i += 1;
      continue;
    }
    if (token === "--emit" && next) {
      args.emit = parseEmit(next);
      i += 2;
      continue;
    }
    if (token === "--follow") {
      args.follow = true;
      i += 1;
      continue;
    }
    if (token === "--interval" && next) {
      args.interval = Number(next);
      i += 2;
      continue;
    }
    if (token === "--max-updates" && next) {
      args.maxUpdates = Number(next);
      i += 2;
      continue;
    }
    if (token === "--url" && next) {
      args.url = next;
      i += 2;
      continue;
    }
    if (token === "--chat" && next) {
      args.chat = next;
      i += 2;
      continue;
    }
    if (token === "--from-seq" && next) {
      args.fromSeq = Number(next);
      i += 2;
      continue;
    }
    if (token === "--token" && next) {
      args.token = next;
      i += 2;
      continue;
    }
    if (token === "--snapshot-path" && next) {
      args.snapshotPath = expandHome(next);
      i += 2;
      continue;
    }
    if (token === "--actions-path" && next) {
      args.actionsPath = expandHome(next);
      i += 2;
      continue;
    }
    if (token === "-h" || token === "--help") {
      args.command = "help";
      i += 1;
      continue;
    }
    throw new TrajectoryNormalizationError("invalid_input", `Unknown argument '${token}'.`);
  }

  return args;
}

function parseEmit(value: string): EmitMode {
  const normalized = value.trim().toLowerCase().replaceAll("_", "+").replaceAll(" ", "");
  if (normalized === "snapshot+delta" || normalized === "both" || normalized === "snapshotdelta") {
    return "snapshot+delta";
  }
  if (normalized === "snapshot") return "snapshot";
  if (normalized === "delta") return "delta";
  throw new TrajectoryNormalizationError(
    "invalid_input",
    `Unknown --emit '${value}'. Expected snapshot+delta, snapshot, or delta.`,
  );
}

function emitToDelivery(emit: EmitMode): NonNullable<StreamOptions["delivery"]> {
  return emit === "snapshot+delta" ? "both" : emit;
}

function parseSource(value: string): SourceName {
  const normalized = value.trim().toLowerCase();
  if ((SOURCES as readonly string[]).includes(normalized)) return normalized as SourceName;
  if (normalized === "claude" || normalized === "claudecode") return "claude-code";
  if (normalized === "grok") return "grok-build";
  throw new TrajectoryNormalizationError(
    "unknown_source",
    `Unknown source '${value}'. Expected one of: ${SOURCES.join(", ")} (alias: grok).`,
  );
}

function expandHome(path: string): string {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return join(homedir(), path.slice(2));
  return path;
}

function defaultRoot(source: SourceName): string {
  const envMap: Record<SourceName, string> = {
    pi: "TRAJECTORY_PI_ROOT",
    "claude-code": "TRAJECTORY_CLAUDE_CODE_ROOT",
    codex: "TRAJECTORY_CODEX_ROOT",
    openclaw: "TRAJECTORY_OPENCLAW_ROOT",
    hermes: "TRAJECTORY_HERMES_ROOT",
    ahp: "TRAJECTORY_AHP_ROOT",
    "grok-build": "TRAJECTORY_GROK_BUILD_ROOT",
  };
  const fromTrajectory = process.env[envMap[source]]?.trim();
  if (fromTrajectory) return expandHome(fromTrajectory);

  const home = homedir();
  switch (source) {
    case "pi":
      return expandHome(process.env.PI_CODING_AGENT_DIR?.trim() || join(home, ".pi", "agent"));
    case "claude-code":
      return join(home, ".claude", "projects");
    case "codex":
      return join(home, ".codex", "sessions");
    case "openclaw": {
      const openclaw = process.env.OPENCLAW_STATE_DIR?.trim() || process.env.CLAWDBOT_STATE_DIR?.trim();
      if (openclaw) return expandHome(openclaw);
      // Prefer ~/.openclaw when present; otherwise fall back to legacy ~/.clawdbot.
      const openclawHome = join(home, ".openclaw");
      if (existsSync(openclawHome)) return openclawHome;
      return join(home, ".clawdbot");
    }
    case "hermes":
      return join(home, ".hermes");
    case "ahp":
      return home;
    case "grok-build": {
      const grokHome = process.env.GROK_HOME?.trim();
      if (grokHome) return join(expandHome(grokHome), "sessions");
      return join(home, ".grok", "sessions");
    }
  }
}

function describeDefault(source: SourceName): string {
  switch (source) {
    case "pi":
      return "~/.pi/agent (or PI_CODING_AGENT_DIR)";
    case "claude-code":
      return "~/.claude/projects";
    case "codex":
      return "~/.codex/sessions";
    case "openclaw":
      return "~/.openclaw if present, else ~/.clawdbot (or OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR)";
    case "hermes":
      return "~/.hermes/state.db";
    case "ahp":
      return "explicit export root only (no home default)";
    case "grok-build":
      return "$GROK_HOME/sessions or ~/.grok/sessions (or TRAJECTORY_GROK_BUILD_ROOT)";
  }
}

async function listForSource(
  source: SourceName,
  root: string,
  limit: number,
): Promise<TrajectoryListingPage> {
  const options = { root, limit };
  switch (source) {
    case "pi":
      return listPiTrajectories(options);
    case "claude-code":
      return listClaudeCodeTrajectories(options);
    case "codex":
      return listCodexTrajectories(options);
    case "openclaw":
      return listOpenClawTrajectories(options);
    case "hermes":
      return listHermesTrajectories(options);
    case "ahp":
      return listAhpTrajectories(options);
    case "grok-build":
      return listGrokBuildTrajectories(options);
  }
}

async function runList(args: CliArgs): Promise<number> {
  const source = args.source ?? (await promptSource());
  const root = args.root ?? defaultRoot(source);
  console.log(`${DIM}Source${RESET} ${source}  ${DIM}root${RESET} ${root}`);
  const page = await listForSource(source, root, args.limit);
  if (page.items.length === 0) {
    printEmpty(source);
    return 0;
  }
  printListingTable(page.items);
  if (page.nextCursor) {
    console.log(`${DIM}More sessions available. Showing first ${page.items.length}.${RESET}`);
  }
  return 0;
}

async function runShow(args: CliArgs): Promise<number> {
  const source = args.source ?? "pi";
  const root = args.root ?? defaultRoot(source);
  const resolved = await resolveListingPath(source, root, args.path, args.id, args.limit);
  if (!resolved) return 2;
  return printSummary(source, resolved.path, args.showContent, args.format, resolved.groupId);
}

async function runStream(args: CliArgs): Promise<number> {
  const source = (args.source ?? "pi") as SourceName;
  if (!(STREAM_FILE_SOURCES as readonly string[]).includes(source)) {
    throw new TrajectoryNormalizationError(
      "invalid_input",
      `stream supports file JSONL sources only: ${STREAM_FILE_SOURCES.join(", ")}. Use ahp-stream for AHP.`,
    );
  }
  const target = await resolveStreamTarget(source, args);
  const delivery = emitToDelivery(args.emit);
  console.log(`${BOLD}${CYAN}Trajectory stream${RESET}  ${DIM}sample file follow (not a daemon)${RESET}`);
  console.log(`${DIM}source${RESET}   ${source}`);
  console.log(`${DIM}root${RESET}     ${target.root}`);
  console.log(`${DIM}path${RESET}     ${target.path}`);
  console.log(`${DIM}emit${RESET}     ${args.emit} (delivery=${delivery})`);
  console.log(`${DIM}follow${RESET}   ${args.follow}`);
  if (!args.showContent) {
    console.log(`${DIM}Privacy: content hidden unless --show-content.${RESET}`);
  }
  console.log();

  const fs = FileTrajectoryStream.open({
    root: target.root,
    path: target.path,
    source: source as StreamFileSource,
    ...(target.groupId === undefined ? {} : { groupId: target.groupId }),
    stream: {
      source: source as TrajectorySource,
      ...(target.groupId === undefined ? {} : { groupId: target.groupId }),
      delivery,
    },
    pollInterval: Math.max(0, args.interval),
  });

  try {
    return await consumeFileStream(fs, args);
  } finally {
    fs.close();
  }
}

async function resolveStreamTarget(
  source: SourceName,
  args: CliArgs,
): Promise<{ root: string; path: string; groupId?: string }> {
  if (args.path) {
    const path = args.path;
    const root = args.root ?? dirname(resolvePath(path));
    return { root, path, ...(args.id === undefined ? {} : { groupId: args.id }) };
  }
  if (!args.id) {
    throw new TrajectoryNormalizationError(
      "invalid_input",
      "stream requires --path or --id (with --root for listing resolution).",
    );
  }
  if (!args.root) {
    throw new TrajectoryNormalizationError(
      "invalid_input",
      "stream --id requires an explicit --root (no implicit home multi-session watch).",
    );
  }
  const resolved = await resolveListingPath(source, args.root, undefined, args.id, args.limit);
  if (!resolved) {
    throw new TrajectoryNormalizationError("invalid_input", `Session id '${args.id}' not found.`);
  }
  return { root: args.root, path: resolved.path, groupId: args.id };
}

async function consumeFileStream(fs: FileTrajectoryStream, args: CliArgs): Promise<number> {
  let seen = 0;
  for (;;) {
    const update = await fs.poll();
    if (update !== null && update.kind !== "unchanged") {
      seen += 1;
      printStreamUpdate(update, args.showContent, args.emit, seen);
      if (args.maxUpdates !== undefined && seen >= args.maxUpdates) break;
    }
    if (!args.follow) break;
    if (args.maxUpdates !== undefined && seen >= args.maxUpdates) break;
    if (args.interval > 0) {
      await new Promise((r) => setTimeout(r, args.interval * 1000));
    }
  }
  if (seen === 0) {
    console.log(`${DIM}No stream updates (empty or unchanged prefix).${RESET}`);
  } else {
    console.log(`${DIM}Emitted ${seen} update(s). Process exit ends follow (not a daemon).${RESET}`);
  }
  return 0;
}

async function runAhpStream(args: CliArgs, transport?: AhpTransport): Promise<number> {
  const chat = args.chat?.trim();
  if (!chat) {
    throw new TrajectoryNormalizationError("invalid_input", "ahp-stream requires --chat <ahp-chat:/…>.");
  }
  const url = (args.url ?? "fake://demo").trim();
  const delivery = emitToDelivery(args.emit);
  console.log(`${BOLD}${CYAN}Trajectory ahp-stream${RESET}  ${DIM}sample client demo (not a daemon)${RESET}`);
  console.log(`${DIM}url${RESET}      ${url}`);
  console.log(`${DIM}chat${RESET}     ${chat}`);
  console.log(`${DIM}emit${RESET}     ${args.emit} (delivery=${delivery})`);
  if (args.fromSeq !== undefined) console.log(`${DIM}from-seq${RESET} ${args.fromSeq}`);
  if (!args.showContent) {
    console.log(`${DIM}Privacy: content hidden unless --show-content.${RESET}`);
  }
  console.log();

  let host: FakeAhpHost | undefined;
  let clientTransport = transport;
  if (clientTransport === undefined) {
    const opened = await openAhpDemoTransport(url, chat, args);
    clientTransport = opened.transport;
    host = opened.host;
  }

  const token = args.token ?? process.env.TRAJECTORY_AHP_TOKEN;
  let updatesSeen = 0;
  let ready = false;
  const onEvent = (event: AhpClientEvent): void => {
    if (event.kind === "stream-update" && event.update) {
      updatesSeen += 1;
      printStreamUpdate(event.update, args.showContent, args.emit, updatesSeen);
    } else if (event.kind === "ready") {
      ready = true;
      console.log(`${DIM}AHP client ready (subscribe complete).${RESET}`);
    } else if (
      event.kind === "auth-required" ||
      event.kind === "auth-failed" ||
      event.kind === "resync-required" ||
      event.kind === "backpressure" ||
      event.kind === "error" ||
      event.kind === "disconnected"
    ) {
      console.log(`${YELLOW}${event.code ?? event.kind}${RESET}  ${event.message ?? event.kind}`);
    }
  };

  const client = new AhpStreamClient(
    clientTransport,
    {
      chatChannel: chat,
      ...(token
        ? {
            auth: () => ({ token }),
          }
        : {}),
      streamOptions: { source: "ahp", groupId: chat, delivery },
      ...(args.fromSeq === undefined ? {} : { fromServerSeq: args.fromSeq }),
    },
    onEvent,
  );

  try {
    client.start();
    if (args.maxUpdates !== undefined) {
      const deadline = Date.now() + 2000;
      while (updatesSeen < args.maxUpdates && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 10));
      }
    } else {
      await new Promise((r) => setTimeout(r, 50));
    }
    if (updatesSeen === 0 && !ready) {
      console.log(`${YELLOW}No AHP ready/update events. Check --url / --chat / fixtures.${RESET}`);
    } else {
      console.log(
        `${DIM}Emitted ${updatesSeen} stream update(s). Cancel leaves last cursor valid; not a daemon.${RESET}`,
      );
    }
    return 0;
  } finally {
    client.cancel();
    host?.close();
  }
}

async function openAhpDemoTransport(
  url: string,
  chat: string,
  args: CliArgs,
): Promise<{ transport: AhpTransport; host: FakeAhpHost }> {
  const scheme = url.includes(":") ? url.split(":", 1)[0]!.toLowerCase() : url.toLowerCase();
  if (scheme !== "fake" && scheme !== "memory" && scheme !== "test") {
    throw new TrajectoryNormalizationError(
      "invalid_input",
      "Sample ahp-stream supports url scheme fake:// (in-memory FakeAhpHost) only. " +
        "Wire AhpStreamClient with your WebSocket AhpTransport for live hosts " +
        "(see docs/ahp-client.md). Example: --url fake://demo",
    );
  }
  const pair = new InMemoryAhpTransportPair();
  let snapshot: Record<string, unknown> | undefined;
  let actions: Record<string, unknown>[] = [];
  if (args.snapshotPath) {
    snapshot = JSON.parse(await readFile(args.snapshotPath, "utf8")) as Record<string, unknown>;
  }
  if (args.actionsPath) {
    const text = await readFile(args.actionsPath, "utf8");
    for (const line of text.split("\n")) {
      if (line.trim()) actions.push(JSON.parse(line) as Record<string, unknown>);
    }
  }
  if (snapshot === undefined && actions.length === 0) {
    snapshot = {
      ahpProtocolVersion: "0.7.0",
      chat: { id: chat, turns: [], activeTurn: null },
    };
  }
  const token = args.token ?? process.env.TRAJECTORY_AHP_TOKEN;
  const host = new FakeAhpHost(
    pair.host,
    {
      ...(snapshot === undefined ? {} : { initialSnapshot: snapshot }),
      ...(actions.length === 0 ? {} : { initialActions: actions }),
      requireAuth: Boolean(token),
      acceptToken: token ?? "test-token",
    },
    chat,
  );
  return { transport: pair.client, host };
}

function printStreamUpdate(
  update: StreamUpdate,
  showContent: boolean,
  emit: EmitMode,
  index: number,
): void {
  console.log(`${BOLD}── stream update #${index} ──${RESET}`);
  console.log(`${DIM}kind${RESET}       ${update.kind}`);
  console.log(
    `${DIM}revision${RESET}   ${update.revision.revision} id=${update.revision.revisionId} gen=${update.revision.generation}`,
  );
  const cursor = update.cursor;
  const posKind = cursor.position.kind;
  console.log(
    `${DIM}cursor${RESET}     source=${cursor.source} group=${truncate(cursor.groupId, 40)} gen=${cursor.generation} pos=${posKind}`,
  );
  if (update.snapshot) {
    console.log(
      `${DIM}snapshot${RESET}   records=${update.snapshot.records.length} complete=${update.snapshot.complete}`,
    );
  } else if (emit === "snapshot+delta" || emit === "snapshot") {
    console.log(`${DIM}snapshot${RESET}   (omitted by delivery)`);
  }
  if (update.delta) {
    const opNames = new Map<string, number>();
    for (const op of update.delta.operations) {
      opNames.set(op.op, (opNames.get(op.op) ?? 0) + 1);
    }
    const summary = [...opNames.entries()]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([name, count]) => `${name}=${count}`)
      .join(", ");
    console.log(
      `${DIM}delta${RESET}      ops=${update.delta.operations.length}` +
        (summary ? ` (${summary})` : ""),
    );
  } else if (emit === "snapshot+delta" || emit === "delta") {
    console.log(`${DIM}delta${RESET}      (omitted by delivery)`);
  }
  console.log(`${DIM}diagnostics${RESET} ${update.diagnostics.length}`);
  for (const diagnostic of update.diagnostics.slice(0, 8)) {
    console.log(`  ${DIM}${diagnostic.code}${RESET}  ${diagnostic.message}`);
  }
  if (update.reset) {
    console.log(`${YELLOW}reset${RESET}      reason=${update.reset.reason}`);
  }
  if (update.error) {
    console.log(`${RED}error${RESET}      ${update.error.code}: ${update.error.message}`);
  }
  if (showContent && update.snapshot) {
    console.log(
      `\n${RED}${BOLD}WARNING${RESET}${RED}: --show-content prints transcript-derived text. Treat as private.${RESET}`,
    );
    let i = 0;
    for (const streamRec of update.snapshot.records.slice(0, 40)) {
      i += 1;
      const rec = streamRec.record as Record<string, unknown>;
      const role = String(rec.role ?? "?");
      const kind = String(rec.kind ?? rec.type ?? "?");
      const content = typeof rec.content === "string" ? rec.content : JSON.stringify(rec).slice(0, 120);
      console.log(
        `  ${String(i).padStart(3)}  ${streamRec.status.padEnd(12)} ${role.padEnd(10)} ${kind.padEnd(20)} ${truncate(content, 80)}`,
      );
    }
  } else if (!showContent) {
    console.log(`${DIM}Content omitted (privacy). Re-run with --show-content for snippets.${RESET}`);
  }
  console.log();
}

async function runBrowse(args: CliArgs): Promise<number> {
  console.log(`${BOLD}${CYAN}Trajectory${RESET}  ${DIM}local sample TUI (unpublished)${RESET}`);
  console.log(`${DIM}Privacy: content is hidden unless --show-content.${RESET}\n`);

  const source = args.source ?? (await promptSource());
  const root = args.root ?? defaultRoot(source);
  console.log(`${DIM}Default for ${source}:${RESET} ${describeDefault(source)}`);
  console.log(`${DIM}Using root${RESET} ${root}\n`);

  const page = await listForSource(source, root, args.limit);
  if (page.items.length === 0) {
    printEmpty(source);
    const rl = createInterface({ input, output });
    try {
      const answer = (await rl.question("Normalize a transcript file by path instead? [y/N] ")).trim().toLowerCase();
      if (answer === "y" || answer === "yes") {
        const filePath = expandHome((await rl.question("Path to transcript: ")).trim());
        return printSummary(source, filePath, args.showContent, args.format);
      }
    } finally {
      rl.close();
    }
    return 0;
  }

  const selected = await promptSession(page.items);
  if (!selected) return 0;
  console.log();
  return printSummary(source, selected.path, args.showContent, args.format, selected.id);
}

async function resolveListingPath(
  source: SourceName,
  root: string,
  path: string | undefined,
  id: string | undefined,
  limit: number,
): Promise<{ path: string; groupId?: string } | undefined> {
  if (path) return { path, ...(id ? { groupId: id } : {}) };
  if (!id) {
    console.error(`${RED}Provide --path or --id.${RESET}`);
    return undefined;
  }
  const page = await listForSource(source, root, limit);
  const match = page.items.find((item: TrajectoryListing) => item.id === id);
  if (!match) {
    console.error(`${RED}Session id '${id}' not found under ${root}.${RESET}`);
    return undefined;
  }
  return { path: match.path, groupId: match.id };
}

async function printSummary(
  source: SourceName,
  path: string,
  showContent: boolean,
  format: CliArgs["format"],
  groupId?: string,
): Promise<number> {
  try {
    await access(path, fsConstants.R_OK);
  } catch {
    console.error(`${RED}File not found or unreadable:${RESET} ${path}`);
    return 2;
  }

  let transcript: string;
  try {
    transcript = await readFile(path, "utf8");
  } catch (error) {
    console.error(`${RED}Could not read transcript:${RESET}`, error instanceof Error ? error.message : error);
    return 2;
  }

  let ir: TrajectoryIR;
  try {
    ir = normalizeToIR({
      source: source as TrajectorySource,
      transcript,
      ...(groupId === undefined ? {} : { sourceContext: { groupId } }),
    });
  } catch (error) {
    return handleError(error);
  }

  console.log(`${BOLD}── ${source} ${basename(path)} ──${RESET}`);
  console.log(`${DIM}path${RESET}     ${path}`);
  console.log(`${DIM}group${RESET}    ${ir.groupId}`);
  console.log(`${DIM}source${RESET}   ${ir.source}`);
  console.log(`${DIM}records${RESET}  ${ir.records.length}`);
  console.log(`${DIM}partial${RESET}  ${ir.config.sourceContext.partial}`);

  const roleCounts = new Map<string, number>();
  const toolNames: string[] = [];
  for (const record of ir.records) {
    roleCounts.set(record.role, (roleCounts.get(record.role) ?? 0) + 1);
    if (record.kind === "assistant_tool_calls" && record.toolCalls) {
      for (const call of record.toolCalls) toolNames.push(call.name);
    }
  }
  const roles = [...roleCounts.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([role, count]) => `${role}=${count}`)
    .join(", ");
  const uniqueTools = [...new Set(toolNames)].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));

  console.log(`${BOLD}Roles${RESET}       ${roles || "(none)"}`);
  console.log(`${BOLD}Tool calls${RESET}  ${toolNames.length} total, ${uniqueTools.length} unique`);
  if (uniqueTools.length > 0) {
    const shown = uniqueTools.slice(0, 12).join(", ") + (uniqueTools.length > 12 ? "…" : "");
    console.log(`${BOLD}Tools${RESET}       ${shown}`);
  }
  console.log(`${BOLD}Diagnostics${RESET} ${ir.diagnostics.length}`);

  for (const diagnostic of ir.diagnostics.slice(0, 12)) {
    console.log(`  ${DIM}${diagnostic.code}${RESET}  ${diagnostic.message}`);
  }
  if (ir.diagnostics.length > 12) {
    console.log(`${DIM}…and ${ir.diagnostics.length - 12} more diagnostics${RESET}`);
  }

  if (format === "both" || format === "hypabolic") {
    try {
      // Project the already-normalized IR so sourceContext.groupId is preserved.
      const hypabolic = projectHypabolic(ir) as Record<string, unknown>;
      const trajectoryId =
        (hypabolic.trajectory_id as string | undefined) ??
        (hypabolic.trajectoryId as string | undefined) ??
        "?";
      const schemaId =
        (hypabolic.schema_id as string | undefined) ??
        (hypabolic.schemaId as string | undefined) ??
        "?";
      const schemaVersion =
        (hypabolic.schema_version as number | undefined) ??
        (hypabolic.schemaVersion as number | undefined) ??
        "?";
      const records = Array.isArray(hypabolic.records) ? hypabolic.records.length : "?";
      console.log(
        `\n${BOLD}Hypabolic${RESET} trajectoryId=${trajectoryId} schema=${schemaId} v${schemaVersion} records=${records}`,
      );
    } catch (error) {
      console.log(`${YELLOW}Hypabolic projection skipped:${RESET}`, error instanceof Error ? error.message : error);
    }
  }

  if (format === "both" || format === "messages") {
    try {
      // Project the already-normalized IR so sourceContext.groupId is preserved.
      const messages = projectLetta(ir) as {
        records?: unknown[];
        diagnostics?: unknown[];
      };
      console.log(
        `${BOLD}Messages${RESET} records=${messages.records?.length ?? "?"} diagnostics=${messages.diagnostics?.length ?? "?"}`,
      );
    } catch (error) {
      console.log(`${YELLOW}Message trajectory skipped:${RESET}`, error instanceof Error ? error.message : error);
    }
  }

  if (showContent) {
    console.log(`\n${RED}${BOLD}WARNING${RESET}${RED}: --show-content prints transcript-derived text. Treat as private.${RESET}`);
    let order = 0;
    for (const record of ir.records) {
      order += 1;
      const snippet = snippetFor(record);
      console.log(`  ${String(order).padStart(3)}  ${record.role.padEnd(10)} ${record.kind.padEnd(20)} ${snippet}`);
      if (order >= 40) break;
    }
    if (ir.records.length > 40) {
      console.log(`${DIM}Showing first 40 of ${ir.records.length} records.${RESET}`);
    }
  } else {
    console.log(`${DIM}Content omitted (privacy). Re-run with --show-content to include snippets.${RESET}`);
  }

  return 0;
}

function snippetFor(record: TrajectoryIR["records"][number]): string {
  if (record.kind === "assistant_tool_calls" && record.toolCalls) {
    return truncate(
      record.toolCalls.map((call) => `${call.name}(${truncate(call.argumentsJson, 40)})`).join(", "),
      120,
    );
  }
  if (record.kind === "meta") {
    return truncate(`source=${record.sourceName ?? "?"} model=${record.model ?? "—"}`, 120);
  }
  return truncate(record.content ?? "—", 120);
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, Math.max(0, max - 1))}…`;
}

function printListingTable(items: readonly TrajectoryListing[]): void {
  const idW = Math.min(36, Math.max(8, ...items.map((i) => i.id.length)));
  const header = `${"Id".padEnd(idW)}  ${"Updated (UTC)".padEnd(24)}  ${"Size".padStart(8)}  Path`;
  console.log(header);
  console.log("-".repeat(Math.min(100, header.length + 20)));
  for (const item of items) {
    console.log(
      `${item.id.padEnd(idW)}  ${item.updatedAt.padEnd(24)}  ${formatBytes(item.sizeBytes).padStart(8)}  ${item.path}`,
    );
  }
}

function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${bytes} B` : `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

function printEmpty(source: SourceName): void {
  console.log(`${YELLOW}No sessions found.${RESET} Empty or missing store is not an error.`);
  if (source === "hermes") {
    console.log(
      `${DIM}Hermes core listing is SQLite-free and returns empty pages. Export message JSON and use show --path.${RESET}`,
    );
  }
}

async function promptSource(): Promise<SourceName> {
  console.log("Sources:");
  SOURCES.forEach((name, index) => console.log(`  ${index + 1}) ${name}`));
  const rl = createInterface({ input, output });
  try {
    while (true) {
      const answer = (await rl.question("Select source [1-5 or name]: ")).trim().toLowerCase();
      const asNumber = Number(answer);
      if (Number.isInteger(asNumber) && asNumber >= 1 && asNumber <= SOURCES.length) {
        return SOURCES[asNumber - 1]!;
      }
      try {
        return parseSource(answer);
      } catch {
        console.log(`${YELLOW}Invalid choice.${RESET}`);
      }
    }
  } finally {
    rl.close();
  }
}

async function promptSession(items: readonly TrajectoryListing[]): Promise<TrajectoryListing | undefined> {
  console.log(`Sessions (${items.length}):`);
  items.forEach((item, index) => {
    console.log(
      `  ${String(index + 1).padStart(2)}) ${item.id}  ${DIM}${item.updatedAt}${RESET}  ${formatBytes(item.sizeBytes)}  ${item.path}`,
    );
  });
  console.log(`   0) quit`);

  const rl = createInterface({ input, output });
  try {
    while (true) {
      const answer = (await rl.question(`Select session [0-${items.length}]: `)).trim();
      const asNumber = Number(answer);
      if (asNumber === 0) return undefined;
      if (Number.isInteger(asNumber) && asNumber >= 1 && asNumber <= items.length) {
        return items[asNumber - 1];
      }
      const byId = items.find((item) => item.id === answer);
      if (byId) return byId;
      console.log(`${YELLOW}Invalid choice.${RESET}`);
    }
  } finally {
    rl.close();
  }
}

function handleError(error: unknown): number {
  if (error instanceof TrajectoryNormalizationError) {
    console.error(`${RED}${error.code}:${RESET} ${error.message}`);
    return 2;
  }
  if (error instanceof FileStreamHostError) {
    console.error(`${RED}${error.code}:${RESET} ${error.message}`);
    return 2;
  }
  console.error(`${RED}error:${RESET}`, error instanceof Error ? error.message : error);
  return 1;
}

function printHelp(): void {
  console.log(`trajectory — local sample TUI for Hypabolic Trajectory (unpublished)

Not a daemon. The calling process owns lifetime, store roots, and AHP transport.

Usage:
  trajectory [browse] [--source <src>] [--root <path>] [--limit N] [--show-content]
  trajectory list --source <src> [--root <path>] [--limit N]
  trajectory show --source <src> (--path <file> | --id <id>) [--root <path>] [--show-content]
  trajectory stream --source <src> (--path <file> | --id <id> --root <store>) \\
                    [--emit snapshot+delta|snapshot|delta] [--follow] [--max-updates N]
  trajectory ahp-stream --url fake://demo --chat <ahp-chat:/…> \\
                        [--from-seq N] [--token T] [--snapshot-path F] [--actions-path F]
  trajectory help

Sources: ${SOURCES.join(", ")}
File stream sources: ${STREAM_FILE_SOURCES.join(", ")}

Default roots:
  pi           ~/.pi/agent
  claude-code  ~/.claude/projects
  codex        ~/.codex/sessions
  openclaw     ~/.openclaw if present, else ~/.clawdbot
  hermes       ~/.hermes
  ahp          explicit export root only (use show --path)
  grok-build   $GROK_HOME/sessions or ~/.grok/sessions (alias: grok)

Root overrides: --root or TRAJECTORY_<SOURCE>_ROOT (e.g. TRAJECTORY_PI_ROOT).
OpenClaw also honors OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR.
Grok Build also honors GROK_HOME (sessions under GROK_HOME/sessions).
Privacy: content is omitted unless --show-content (prints a warning).
Stream delivery default is snapshot+delta. Sample ahp-stream uses fake:// only.
`);
}

const code = await main();
process.exitCode = code;

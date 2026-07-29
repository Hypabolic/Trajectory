#!/usr/bin/env node
/**
 * Local sample CLI for browsing agent sessions with Trajectory.
 * Not a published package — workspace-only dependency on core packages.
 */
import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { homedir } from "node:os";
import { join, basename } from "node:path";
import { readFile, access } from "node:fs/promises";
import { constants as fsConstants, existsSync } from "node:fs";

import {
  normalizeToHypabolic,
  normalizeToIR,
  normalizeToLetta as normalizeToMessages,
  TrajectoryNormalizationError,
  type TrajectoryIR,
  type TrajectorySource,
} from "@hypabolic/trajectory";
import {
  listClaudeCodeTrajectories,
  listCodexTrajectories,
  listHermesTrajectories,
  listOpenClawTrajectories,
  listPiTrajectories,
  type TrajectoryListing,
  type TrajectoryListingPage,
} from "@hypabolic/trajectory-node";

const SOURCES = ["pi", "claude-code", "codex", "openclaw", "hermes"] as const;
type SourceName = (typeof SOURCES)[number];

const DIM = "\u001b[2m";
const BOLD = "\u001b[1m";
const RED = "\u001b[31m";
const YELLOW = "\u001b[33m";
const CYAN = "\u001b[36m";
const RESET = "\u001b[0m";

interface CliArgs {
  command: "browse" | "list" | "show" | "help";
  source?: SourceName;
  root?: string;
  path?: string;
  id?: string;
  limit: number;
  showContent: boolean;
  format: "both" | "messages" | "hypabolic";
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
  };

  if (argv.length === 0) return args;

  const head = argv[0];
  let i = 0;
  if (head === "list" || head === "show" || head === "browse" || head === "help" || head === "-h" || head === "--help") {
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
      if (next === "letta" || next === "messages" || next === "hypabolic" || next === "both") args.format = next === "letta" ? "messages" : next;
      else throw new TrajectoryNormalizationError("invalid_input", `Unknown format '${next}'.`);
      i += 2;
      continue;
    }
    if (token === "--show-content") {
      args.showContent = true;
      i += 1;
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

function parseSource(value: string): SourceName {
  const normalized = value.trim().toLowerCase();
  if ((SOURCES as readonly string[]).includes(normalized)) return normalized as SourceName;
  if (normalized === "claude" || normalized === "claudecode") return "claude-code";
  throw new TrajectoryNormalizationError(
    "unknown_source",
    `Unknown source '${value}'. Expected one of: ${SOURCES.join(", ")}.`,
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
  const path = await resolvePath(source, root, args.path, args.id, args.limit);
  if (!path) return 2;
  return printSummary(source, path, args.showContent, args.format);
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
  return printSummary(source, selected.path, args.showContent, args.format);
}

async function resolvePath(
  source: SourceName,
  root: string,
  path: string | undefined,
  id: string | undefined,
  limit: number,
): Promise<string | undefined> {
  if (path) return path;
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
  return match.path;
}

async function printSummary(
  source: SourceName,
  path: string,
  showContent: boolean,
  format: CliArgs["format"],
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
    ir = normalizeToIR({ source: source as TrajectorySource, transcript });
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
      const hypabolic = normalizeToHypabolic({ source: source as TrajectorySource, transcript }) as Record<
        string,
        unknown
      >;
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
      const messages = normalizeToMessages({ source: source as TrajectorySource, transcript }) as {
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
  console.error(`${RED}error:${RESET}`, error instanceof Error ? error.message : error);
  return 1;
}

function printHelp(): void {
  console.log(`trajectory — local sample TUI for Hypabolic Trajectory (unpublished)

Usage:
  trajectory [browse] [--source <src>] [--root <path>] [--limit N] [--show-content]
  trajectory list --source <src> [--root <path>] [--limit N]
  trajectory show --source <src> (--path <file> | --id <id>) [--root <path>] [--show-content]
  trajectory help

Sources: ${SOURCES.join(", ")}

Default roots:
  pi           ~/.pi/agent
  claude-code  ~/.claude/projects
  codex        ~/.codex/sessions
  openclaw     ~/.openclaw if present, else ~/.clawdbot
  hermes       ~/.hermes

Root overrides: --root or TRAJECTORY_<SOURCE>_ROOT (e.g. TRAJECTORY_PI_ROOT).
OpenClaw also honors OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR.
Privacy: content is omitted unless --show-content (prints a warning).
`);
}

const code = await main();
process.exitCode = code;

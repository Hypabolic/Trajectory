import { createReadStream } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { basename, isAbsolute, join, resolve } from "node:path";
import { createInterface } from "node:readline";

import { TrajectoryNormalizationError } from "@hypabolic/trajectory";

const TITLE_SCAN_MAX_BYTES = 64 * 1024;
const TITLE_SCAN_MAX_LINES = 200;
const TITLE_MAX_SCALARS = 120;

const NOISE_MARKERS = [
  "# agents.md",
  "<instructions>",
  "</instructions>",
  "<environment_context>",
  "<skills_instructions>",
  "<skills>",
  "<permissions instructions>",
  "<user_instructions>",
  "<turn_context>",
  "<collaboration",
  "filesystem sandboxing",
  "<cwd>",
  "you are a coding agent",
  "you are chatgpt",
  "# claude.md",
  "agenthub instructions",
  "<command-name>",
  "<local-command-caveat>",
  "<task-notification",
] as const;

export interface ListingOptions {
  readonly root: string;
  readonly cursor?: string;
  readonly limit?: number;
}

export type PiListingOptions = ListingOptions;

export interface TrajectoryListing {
  readonly id: string;
  readonly path: string;
  readonly updatedAt: string;
  readonly title?: string;
  readonly sizeBytes: number;
}

export interface TrajectoryListingPage {
  readonly items: readonly TrajectoryListing[];
  readonly nextCursor: string | null;
}

export async function listPiTrajectories(options: PiListingOptions): Promise<TrajectoryListingPage> {
  return listDiscovered(options, discoverPi);
}

export async function listClaudeCodeTrajectories(
  options: ListingOptions,
): Promise<TrajectoryListingPage> {
  return listDiscovered(options, discoverClaudeCode);
}

export async function listCodexTrajectories(
  options: ListingOptions,
): Promise<TrajectoryListingPage> {
  return listDiscovered(options, discoverCodex);
}

export async function listOpenClawTrajectories(
  options: ListingOptions,
): Promise<TrajectoryListingPage> {
  return listDiscovered(options, discoverOpenClaw);
}

/**
 * Hermes sessions live in `~/.hermes/state.db`. Core listing stays SQLite-free:
 * a missing store yields an empty page. Full sessions-table enumeration is
 * optional/provider-side; pass either the database path or its parent directory.
 */
export async function listHermesTrajectories(
  options: ListingOptions,
): Promise<TrajectoryListingPage> {
  return listDiscovered(options, discoverHermes);
}

/**
 * Grok Build sessions: `<sessions-root>/<cwd-dir>/<session-uuid>/chat_history.jsonl`.
 */
export async function listGrokBuildTrajectories(
  options: ListingOptions,
): Promise<TrajectoryListingPage> {
  return listDiscovered(options, discoverGrokBuild);
}

async function discoverHermes(root: string): Promise<TrajectoryListing[]> {
  // SQLite-backed session discovery is intentionally not implemented in the
  // core Node listing package so package dependencies stay free of native
  // SQLite bindings. Missing stores list empty; callers export message rows
  // for normalize. Optional provider packages may replace this discoverer.
  void root;
  return [];
}

async function discoverGrokBuild(root: string): Promise<TrajectoryListing[]> {
  const absoluteRoot = isAbsolute(root) ? root : resolve(root);
  const items: TrajectoryListing[] = [];
  let cwdDirs;
  try {
    cwdDirs = await readdir(absoluteRoot, { withFileTypes: true });
  } catch (error) {
    if (isMissingOrDenied(error)) return items;
    throw error;
  }
  for (const cwdDir of cwdDirs) {
    if (!cwdDir.isDirectory()) continue;
    const cwdPath = join(absoluteRoot, cwdDir.name);
    let sessionDirs;
    try {
      sessionDirs = await readdir(cwdPath, { withFileTypes: true });
    } catch (error) {
      if (isMissingOrDenied(error)) continue;
      throw error;
    }
    for (const sessionDir of sessionDirs) {
      if (!sessionDir.isDirectory()) continue;
      const historyPath = join(cwdPath, sessionDir.name, "chat_history.jsonl");
      try {
        const info = await stat(historyPath);
        if (!info.isFile()) continue;
        const meta = await readGrokBuildSummaryMeta(
          join(cwdPath, sessionDir.name, "summary.json"),
          info.mtime,
        );
        items.push({
          id: sessionDir.name,
          path: historyPath,
          updatedAt: meta.updatedAt,
          ...(meta.title === undefined ? {} : { title: meta.title }),
          sizeBytes: info.size,
        });
      } catch (error) {
        if (!isMissingOrDenied(error)) throw error;
      }
    }
  }
  return items;
}

async function readGrokBuildSummaryMeta(
  summaryPath: string,
  fallbackMtime: Date,
): Promise<{ updatedAt: string; title?: string }> {
  try {
    const raw = await readFile(summaryPath, "utf8");
    const parsed: unknown = JSON.parse(raw);
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      const summary = parsed as Record<string, unknown>;
      let title: string | undefined;
      if (typeof summary.generated_title === "string" && summary.generated_title) {
        title = summary.generated_title;
      } else if (typeof summary.session_summary === "string" && summary.session_summary) {
        title = summary.session_summary;
      }
      const lastActive = summary.last_active_at;
      const updated = summary.updated_at;
      if (typeof lastActive === "string" && lastActive) {
        const date = new Date(lastActive);
        if (!Number.isNaN(date.valueOf())) {
          return { updatedAt: date.toISOString(), ...(title === undefined ? {} : { title }) };
        }
      }
      if (typeof updated === "string" && updated) {
        const date = new Date(updated);
        if (!Number.isNaN(date.valueOf())) {
          return { updatedAt: date.toISOString(), ...(title === undefined ? {} : { title }) };
        }
      }
      return { updatedAt: fallbackMtime.toISOString(), ...(title === undefined ? {} : { title }) };
    }
  } catch {
    // missing or unreadable summary → history mtime
  }
  return { updatedAt: fallbackMtime.toISOString() };
}

async function listDiscovered(
  options: ListingOptions,
  discover: (root: string) => Promise<TrajectoryListing[]>,
): Promise<TrajectoryListingPage> {
  const limit = options.limit ?? 50;
  if (limit < 1 || limit > 1_000) {
    throw new TrajectoryNormalizationError("invalid_input", "Listing limit must be between 1 and 1000.");
  }
  const items = await discover(options.root);
  items.sort((left, right) =>
    right.updatedAt.localeCompare(left.updatedAt) || utf16Compare(left.id, right.id),
  );
  let start = 0;
  if (options.cursor !== undefined) {
    const state = decodeCursor(options.cursor);
    const current = items.findIndex((item) => item.id === state.id);
    start = current >= 0 ? current + 1 : Math.min(state.index + 1, items.length);
  }
  const page = items.slice(start, start + limit);
  const end = start + page.length;
  return {
    items: page,
    nextCursor: end < items.length && page.length > 0
      ? encodeCursor(page.at(-1)!.id, end - 1)
      : null,
  };
}

async function discoverPi(root: string): Promise<TrajectoryListing[]> {
  const items: TrajectoryListing[] = [];
  let projects;
  try {
    projects = await readdir(join(root, "sessions"), { withFileTypes: true });
  } catch (error) {
    if (isMissingOrDenied(error)) return items;
    throw error;
  }
  for (const project of projects) {
    if (!project.isDirectory()) continue;
    const directory = join(root, "sessions", project.name);
    let files;
    try {
      files = await readdir(directory, { withFileTypes: true });
    } catch (error) {
      if (isMissingOrDenied(error)) continue;
      throw error;
    }
    for (const file of files) {
      if (!file.isFile() || !file.name.endsWith(".jsonl")) continue;
      const path = join(directory, file.name);
      try {
        await addListing(items, path, deriveGenericUserTitle);
      } catch (error) {
        if (!isMissingOrDenied(error)) throw error;
      }
    }
  }
  return items;
}

async function discoverOpenClaw(root: string): Promise<TrajectoryListing[]> {
  const items: TrajectoryListing[] = [];
  let agents;
  try {
    agents = await readdir(join(root, "agents"), { withFileTypes: true });
  } catch (error) {
    if (isMissingOrDenied(error)) return items;
    throw error;
  }
  for (const agent of agents) {
    if (!agent.isDirectory()) continue;
    const sessionsDirectory = join(root, "agents", agent.name, "sessions");
    let files;
    try {
      files = await readdir(sessionsDirectory, { withFileTypes: true });
    } catch (error) {
      if (isMissingOrDenied(error)) continue;
      throw error;
    }
    for (const file of files) {
      if (!file.isFile() || !file.name.endsWith(".jsonl")) continue;
      const path = join(sessionsDirectory, file.name);
      try {
        await addListing(items, path, deriveGenericUserTitle);
      } catch (error) {
        if (!isMissingOrDenied(error)) throw error;
      }
    }
  }
  return items;
}

async function discoverClaudeCode(root: string): Promise<TrajectoryListing[]> {
  const items: TrajectoryListing[] = [];
  let projects;
  try {
    projects = await readdir(root, { withFileTypes: true });
  } catch (error) {
    if (isMissingOrDenied(error)) return items;
    throw error;
  }
  for (const project of projects) {
    if (!project.isDirectory()) continue;
    const directory = join(root, project.name);
    let files;
    try {
      files = await readdir(directory, { withFileTypes: true });
    } catch (error) {
      if (isMissingOrDenied(error)) continue;
      throw error;
    }
    for (const file of files) {
      if (file.isFile() && file.name.endsWith(".jsonl")) {
        await addListing(items, join(directory, file.name), deriveClaudeTitle);
      }
    }
  }
  return items;
}

async function discoverCodex(root: string): Promise<TrajectoryListing[]> {
  const items: TrajectoryListing[] = [];
  await collectCodex(root, 4, items);
  return items;
}

async function collectCodex(
  directory: string,
  remainingDepth: number,
  items: TrajectoryListing[],
): Promise<void> {
  if (remainingDepth < 0) return;
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (isMissingOrDenied(error)) return;
    throw error;
  }
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isFile() && entry.name.endsWith(".jsonl")) {
      await addListing(items, path, deriveCodexTitle);
    } else if (entry.isDirectory() && remainingDepth > 0) {
      await collectCodex(path, remainingDepth - 1, items);
    }
  }
}

async function addListing(
  items: TrajectoryListing[],
  path: string,
  titleFn?: (path: string) => Promise<string | undefined>,
): Promise<void> {
  try {
    const info = await stat(path);
    const title = titleFn === undefined ? undefined : await titleFn(path);
    items.push({
      id: basename(path, ".jsonl"),
      path,
      updatedAt: info.mtime.toISOString(),
      ...(title === undefined ? {} : { title }),
      sizeBytes: info.size,
    });
  } catch (error) {
    if (!isMissingOrDenied(error)) throw error;
  }
}

async function deriveCodexTitle(path: string): Promise<string | undefined> {
  let sessionId: string | undefined;
  for await (const row of scanJsonLines(path)) {
    const recordType = stringField(row.type);
    const payload = isRecord(row.payload) ? row.payload : undefined;
    if (recordType === "session_meta") {
      const id = payload === undefined ? undefined : stringField(payload.id);
      if (id) sessionId = id;
      continue;
    }
    if (recordType === "response_item") {
      const role = payload === undefined ? undefined : stringField(payload.role);
      if (role === "developer" || role === "system") continue;
      if (role === "user") {
        const text = blocksToText(payload?.content) ?? "";
        const title = titleFromUserText(text);
        if (title !== undefined) return title;
      }
      continue;
    }
    if (recordType === "event_msg") {
      const eventType = payload === undefined ? undefined : stringField(payload.type);
      if (eventType === "user_message" || eventType === "user_prompt" || eventType === "message") {
        const text =
          blocksToText(payload?.message) ??
          blocksToText(payload?.content) ??
          (payload === undefined ? undefined : stringField(payload.text)) ??
          "";
        const title = titleFromUserText(text);
        if (title !== undefined) return title;
      }
    }
  }
  return sessionId === undefined ? undefined : formatTitle(shortSessionId(sessionId));
}

async function deriveClaudeTitle(path: string): Promise<string | undefined> {
  let customTitle: string | undefined;
  let aiTitle: string | undefined;
  let summary: string | undefined;
  let firstUser: string | undefined;
  for await (const row of scanJsonLines(path)) {
    const recordType = stringField(row.type);
    if (recordType === "custom-title" && customTitle === undefined) {
      customTitle = formatTitle(stringField(row.customTitle) ?? stringField(row.title));
    } else if (recordType === "ai-title" && aiTitle === undefined) {
      aiTitle = formatTitle(stringField(row.aiTitle) ?? stringField(row.title));
    } else if (recordType === "summary" && summary === undefined) {
      summary = formatTitle(stringField(row.summary) ?? stringField(row.title));
    } else if (recordType === "user" && firstUser === undefined) {
      if (row.isMeta === true || row.isSidechain === true) continue;
      const message = isRecord(row.message) ? row.message : undefined;
      let text = blocksToText(message?.content) ?? blocksToText(row.content) ?? "";
      if (text.includes("tool_use_id")) continue;
      firstUser = titleFromUserText(text);
    }
  }
  return customTitle ?? aiTitle ?? summary ?? firstUser;
}

async function deriveGenericUserTitle(path: string): Promise<string | undefined> {
  for await (const row of scanJsonLines(path)) {
    const message = isRecord(row.message) ? row.message : undefined;
    const role = stringField(message?.role) ?? stringField(row.role);
    if (role !== "user") continue;
    const text = blocksToText(message?.content) ?? blocksToText(row.content) ?? "";
    const title = titleFromUserText(text);
    if (title !== undefined) return title;
  }
  return undefined;
}

async function* scanJsonLines(path: string): AsyncGenerator<Record<string, unknown>> {
  const stream = createReadStream(path, {
    encoding: "utf8",
    start: 0,
    end: TITLE_SCAN_MAX_BYTES - 1,
  });
  const rl = createInterface({ input: stream, crlfDelay: Infinity });
  let lines = 0;
  try {
    for await (const line of rl) {
      lines += 1;
      const trimmed = line.trim();
      if (trimmed) {
        try {
          const parsed: unknown = JSON.parse(trimmed);
          if (isRecord(parsed)) yield parsed;
        } catch {
          // skip malformed line
        }
      }
      if (lines >= TITLE_SCAN_MAX_LINES) break;
    }
  } catch {
    // missing/unreadable transcript → no title
  } finally {
    rl.close();
    stream.destroy();
  }
}

function titleFromUserText(text: string): string | undefined {
  return isListingNoise(text) ? undefined : formatTitle(text);
}

function formatTitle(text: string | undefined): string | undefined {
  if (text === undefined) return undefined;
  const collapsed = text.trim().replace(/\s+/g, " ");
  if (!collapsed) return undefined;
  return [...collapsed].slice(0, TITLE_MAX_SCALARS).join("");
}

function shortSessionId(id: string): string {
  const dash = id.indexOf("-");
  if (dash >= 8) return id.slice(0, 8);
  return id.length <= 8 ? id : id.slice(0, 8);
}

function isListingNoise(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return true;
  const lower = trimmed.toLowerCase();
  for (const marker of NOISE_MARKERS) {
    if (lower.includes(marker)) return true;
  }
  return countXmlishTags(trimmed) >= 3 && trimmed.length > 80;
}

function countXmlishTags(text: string): number {
  let count = 0;
  for (let index = 0; index < text.length; index++) {
    if (text[index] !== "<") continue;
    const start = index + 1;
    if (start >= text.length) break;
    const first = text[start]!;
    if (!(/[A-Za-z/_-]/.test(first))) continue;
    const end = text.indexOf(">", start);
    if (end < 0) break;
    const name = text.slice(start, end);
    if (/^[A-Za-z0-9/_-]+$/.test(name)) {
      count += 1;
      index = end;
    }
  }
  return count;
}

function blocksToText(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    const parts: string[] = [];
    for (const item of value) {
      if (typeof item === "string") {
        parts.push(item);
      } else if (isRecord(item)) {
        if (typeof item.text === "string") parts.push(item.text);
        else if (typeof item.input_text === "string") parts.push(item.input_text);
        else if (item.type === "input_text" && typeof item.text === "string") parts.push(item.text);
      }
    }
    return parts.length === 0 ? undefined : parts.join("\n");
  }
  if (isRecord(value)) {
    if (typeof value.text === "string") return value.text;
    if (value.content !== undefined) return blocksToText(value.content);
  }
  return undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringField(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

export const listTrajectories = listPiTrajectories;

function encodeCursor(id: string, index: number): string {
  return Buffer.from(`1\n${index}\n${id}`).toString("base64url");
}

function decodeCursor(cursor: string): { id: string; index: number } {
  try {
    const parts = Buffer.from(cursor, "base64url").toString("utf8").split("\n");
    const index = Number(parts[1]);
    if (parts.length !== 3 || parts[0] !== "1" || !Number.isInteger(index) || index < 0) {
      throw new Error("invalid");
    }
    return { id: parts[2]!, index };
  } catch {
    throw new TrajectoryNormalizationError(
      "invalid_input",
      "Cursor is not a valid trajectory-listing cursor.",
    );
  }
}

function isMissingOrDenied(error: unknown): boolean {
  return error instanceof Error &&
    "code" in error &&
    (error.code === "ENOENT" || error.code === "EACCES" || error.code === "EPERM");
}

function utf16Compare(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

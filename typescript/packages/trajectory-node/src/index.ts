import { readdir, readFile, stat } from "node:fs/promises";
import { basename, isAbsolute, join, resolve } from "node:path";

import { TrajectoryNormalizationError } from "@hypabolic/trajectory";

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
        await addListing(items, path);
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
        await addListing(items, path);
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
        await addListing(items, join(directory, file.name));
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
      await addListing(items, path);
    } else if (entry.isDirectory() && remainingDepth > 0) {
      await collectCodex(path, remainingDepth - 1, items);
    }
  }
}

async function addListing(
  items: TrajectoryListing[],
  path: string,
): Promise<void> {
  try {
    const info = await stat(path);
    items.push({
      id: basename(path, ".jsonl"),
      path,
      updatedAt: info.mtime.toISOString(),
      sizeBytes: info.size,
    });
  } catch (error) {
    if (!isMissingOrDenied(error)) throw error;
  }
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

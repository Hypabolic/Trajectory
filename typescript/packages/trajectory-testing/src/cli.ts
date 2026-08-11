#!/usr/bin/env node

import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  utimes,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";

import {
  normalizeToIR,
  projectCanonical,
  projectHypabolic,
  projectLetta,
  projectMinimalJsonl,
  projectOpenAI,
  serializeProjection,
  TrajectoryNormalizationError,
  type TrajectorySource,
} from "@hypabolic/trajectory";
import {
  listAhpTrajectories,
  listClaudeCodeTrajectories,
  listCodexTrajectories,
  listGrokBuildTrajectories,
  listHermesTrajectories,
  listOpenClawTrajectories,
  listPiTrajectories,
} from "@hypabolic/trajectory-node";
import { projectOpenTelemetry } from "@hypabolic/trajectory-otel";

interface Request {
  protocol_version: string;
  case: string;
  operation: string;
  repository_root: string;
}

interface Manifest {
  id: string;
  source: string;
  transcript: string;
  operation: Record<string, unknown>;
  store?: string;
  listing?: { limit?: number; all_pages?: boolean };
  source_context: {
    group_id?: string;
    base_byte_offset?: number;
    partial?: boolean;
    include_encrypted_reasoning?: boolean | string;
  };
  bounds: {
    tool_arguments?: { max_characters?: number | null };
    tool_results?: { max_characters?: number | null; strategy?: "head" | "head-tail" };
  };
  filters: { tool_results?: "include" | "omit" };
}

try {
  const request = await readRequest();
  const response = await execute(request);
  process.stdout.write(JSON.stringify(response));
} catch (error) {
  process.stdout.write(JSON.stringify({
    case: "",
    operation: "",
    status: "protocol-error",
    output_text: null,
    diagnostics: [],
    fatal_error: {
      code: "invalid_request",
      message: error instanceof Error ? error.message : String(error),
    },
  }));
  process.exitCode = 2;
}

async function readRequest(): Promise<Request> {
  const text = process.argv.length === 3
    ? await readFile(process.argv[2]!, "utf8")
    : await new Promise<string>((resolveInput, reject) => {
      let value = "";
      process.stdin.setEncoding("utf8");
      process.stdin.on("data", (chunk: string) => { value += chunk; });
      process.stdin.on("end", () => { resolveInput(value); });
      process.stdin.on("error", reject);
    });
  const parsed: unknown = JSON.parse(text);
  if (!isObject(parsed)) throw new Error("The request must be a JSON object.");
  for (const key of ["protocol_version", "case", "operation", "repository_root"]) {
    if (typeof parsed[key] !== "string") throw new Error(`Request property '${key}' must be a string.`);
  }
  if (parsed.protocol_version !== "1") {
    throw new Error(`Unsupported protocol version '${String(parsed.protocol_version)}'.`);
  }
  return parsed as unknown as Request;
}

async function execute(request: Request): Promise<unknown> {
  const repositoryRoot = resolve(request.repository_root);
  const casesRoot = join(repositoryRoot, "conformance", "cases");
  const caseDirectory = safeResolve(casesRoot, request.case);
  const manifest = JSON.parse(await readFile(join(caseDirectory, "case.json"), "utf8")) as Manifest;
  if (manifest.id !== request.case) throw new Error("The requested case does not match its manifest ID.");
  if (!(request.operation in manifest.operation)) {
    throw new Error(`Case '${request.case}' does not declare operation '${request.operation}'.`);
  }
  if (!["pi", "claude-code", "codex", "openclaw", "hermes", "ahp", "grok-build"].includes(manifest.source)) {
    throw new Error(`TypeScript does not support source '${manifest.source}'.`);
  }
  try {
    let outputText: string;
    let diagnostics: TrajectoryIRDiagnostics = [];
    if (request.operation === "list-trajectories") {
      outputText = await executeListing(repositoryRoot, manifest);
    } else {
      const transcript = await readFile(safeResolve(caseDirectory, manifest.transcript));
      const includeEncrypted = manifest.source_context.include_encrypted_reasoning === true
        || manifest.source_context.include_encrypted_reasoning === "true";
      const trajectory = normalizeToIR({
        source: manifest.source as TrajectorySource,
        transcriptBytes: transcript,
        sourceContext: {
          ...(manifest.source_context.group_id === undefined ? {} : { groupId: manifest.source_context.group_id }),
          ...(manifest.source_context.base_byte_offset === undefined ? {} : { baseByteOffset: BigInt(manifest.source_context.base_byte_offset) }),
          partial: manifest.source_context.partial ?? false,
          ...(includeEncrypted ? { includeEncryptedReasoning: true } : {}),
        },
        options: {
          bounds: {
            ...(manifest.bounds.tool_arguments === undefined ? {} : {
              toolArguments: {
                ...(manifest.bounds.tool_arguments.max_characters === undefined
                  ? {}
                  : { maxCharacters: manifest.bounds.tool_arguments.max_characters }),
              },
            }),
            ...(manifest.bounds.tool_results === undefined ? {} : {
              toolResults: {
                ...(manifest.bounds.tool_results.max_characters === undefined
                  ? {}
                  : { maxCharacters: manifest.bounds.tool_results.max_characters }),
                ...(manifest.bounds.tool_results.strategy === undefined
                  ? {}
                  : { strategy: manifest.bounds.tool_results.strategy }),
              },
            }),
          },
          filters: {
            ...(manifest.filters.tool_results === undefined
              ? {}
              : { toolResults: manifest.filters.tool_results }),
          },
        },
      });
      diagnostics = trajectory.diagnostics;
      outputText = request.operation === "normalize-letta"
        ? serializeProjection(projectLetta(trajectory))
        : request.operation === "normalize-canonical"
          ? serializeProjection(projectCanonical(trajectory))
          : request.operation === "normalize-hypabolic"
            ? serializeProjection(projectHypabolic(trajectory))
            : request.operation === "project-openai"
              ? serializeProjection(projectOpenAI(trajectory))
              : request.operation === "project-minimal-jsonl"
                ? projectMinimalJsonl(trajectory)
                : request.operation === "project-otel"
                  ? serializeProjection(projectOpenTelemetry(trajectory) as never)
                  : unsupported(request.operation);
    }
    return {
      case: request.case,
      operation: request.operation,
      status: "success",
      output_text: outputText,
      diagnostics,
      fatal_error: null,
    };
  } catch (error) {
    if (!(error instanceof TrajectoryNormalizationError)) throw error;
    return {
      case: request.case,
      operation: request.operation,
      status: "fatal-error",
      output_text: null,
      diagnostics: [],
      fatal_error: { code: error.code, message: error.message },
    };
  }
}

type TrajectoryIRDiagnostics = readonly {
  readonly code: string;
  readonly message: string;
  readonly inputLine?: number;
  readonly recordIndex?: number;
  readonly count?: number;
}[];

async function executeListing(repositoryRoot: string, manifest: Manifest): Promise<string> {
  if (!manifest.store) throw new Error("Listing case requires a declarative store.");
  const storePath = safeResolve(join(repositoryRoot, "conformance", "stores"), join(manifest.store, "store.json"));
  const store = JSON.parse(await readFile(storePath, "utf8")) as {
    files: { path: string; content: string; updated_at?: string }[];
  };
  const root = await mkdtemp(join(tmpdir(), "trajectory-conformance-"));
  try {
    for (const fixture of store.files) {
      const destination = safeResolve(root, fixture.path);
      await mkdir(dirname(destination), { recursive: true });
      await writeFile(destination, fixture.content);
      if (fixture.updated_at) {
        const timestamp = new Date(fixture.updated_at);
        await utimes(destination, timestamp, timestamp);
      }
    }
    const listingRoot = manifest.source === "pi" || manifest.source === "openclaw"
      ? root
      : join(root, "store");
    const pages: unknown[] = [];
    let cursor: string | undefined;
    do {
      const list = manifest.source === "claude-code"
        ? listClaudeCodeTrajectories
        : manifest.source === "codex"
          ? listCodexTrajectories
          : manifest.source === "openclaw"
            ? listOpenClawTrajectories
            : manifest.source === "hermes"
              ? listHermesTrajectories
              : manifest.source === "ahp"
                ? listAhpTrajectories
              : manifest.source === "grok-build"
                ? listGrokBuildTrajectories
                : listPiTrajectories;
      const page = await list({
        root: listingRoot,
        limit: manifest.listing?.limit ?? 50,
        ...(cursor === undefined ? {} : { cursor }),
      });
      pages.push({
        items: page.items.map((item) => ({
          id: item.id,
          path: item.path.replace(root, "$ROOT").replaceAll("\\", "/"),
          updated_at: item.updatedAt,
          ...(item.title === undefined ? {} : { title: item.title }),
          size_bytes: item.sizeBytes,
        })),
        next_cursor: page.nextCursor,
      });
      cursor = page.nextCursor ?? undefined;
    } while (manifest.listing?.all_pages && cursor !== undefined);
    return JSON.stringify(manifest.listing?.all_pages ? pages : pages[0]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function safeResolve(root: string, path: string): string {
  if (isAbsolute(path)) throw new Error("Fixture path must be relative.");
  const output = resolve(root, path);
  const rel = relative(root, output);
  if (rel === ".." || rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) || isAbsolute(rel)) {
    throw new Error("Fixture path escapes its declared root.");
  }
  return output;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function unsupported(operation: string): never {
  throw new Error(`Unsupported operation '${operation}'.`);
}

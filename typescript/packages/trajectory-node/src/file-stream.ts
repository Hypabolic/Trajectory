/**
 * Optional file I/O for live session streaming (LS-09).
 * Poll helpers that only call core apply APIs. Explicit root required.
 */
import { open, stat } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";

import {
  applyAppend,
  applySnapshot,
  createStream,
  finishStream,
  splitCompleteLines as splitCompleteLinesCore,
  type StreamOptions,
  type StreamUpdate,
  type TrajectorySource,
} from "@hypabolic/trajectory";

export const HOST_ROOT_REQUIRED = "root_required" as const;
export const HOST_PATH_REQUIRED = "path_required" as const;
export const HOST_PATH_OUTSIDE_ROOT = "path_outside_root" as const;
export const HOST_IO_PERMISSION = "io_permission" as const;
export const HOST_IO_NOT_FOUND = "io_not_found" as const;
export const HOST_IO_ERROR = "io_error" as const;

const MSG_ROOT_REQUIRED = "File stream root is required.";
const MSG_PATH_REQUIRED = "File stream path is required.";
const MSG_PATH_OUTSIDE_ROOT = "File stream path is outside the explicit root.";
const MSG_IO_PERMISSION = "File stream could not read the path (permission denied).";
const MSG_IO_NOT_FOUND = "File stream path was not found.";
const MSG_IO_ERROR = "File stream I/O failed.";

export type FileStreamHostCode =
  | typeof HOST_ROOT_REQUIRED
  | typeof HOST_PATH_REQUIRED
  | typeof HOST_PATH_OUTSIDE_ROOT
  | typeof HOST_IO_PERMISSION
  | typeof HOST_IO_NOT_FOUND
  | typeof HOST_IO_ERROR;

export class FileStreamHostError extends Error {
  readonly code: FileStreamHostCode;
  /** For the calling process only; never copy into StreamUpdate diagnostics. */
  readonly path?: string;

  constructor(code: FileStreamHostCode, message: string, path?: string) {
    super(message);
    this.name = "FileStreamHostError";
    this.code = code;
    if (path !== undefined) this.path = path;
  }
}

export interface FileStreamOptions {
  readonly root: string;
  readonly path: string;
  readonly source: TrajectorySource;
  readonly groupId?: string;
  readonly stream?: StreamOptions;
  /** Poll interval seconds for followFile (default 0.05). */
  readonly pollInterval?: number;
  /** Full-prefix reconcile every N polls (0 = disabled). */
  readonly reconcileEvery?: number;
  readonly sourceRevision?: string;
}

/**
 * Host-edge complete-line split (same framing as core).
 * Property names `complete`/`pending` match the file-I/O package convention;
 * core exports `committed`/`pending`.
 */
export function splitCompleteLines(data: Uint8Array): {
  complete: Uint8Array;
  pending: Uint8Array;
} {
  const { committed, pending } = splitCompleteLinesCore(data);
  return { complete: committed, pending };
}

function isUnderRoot(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

function mapIoError(error: unknown, path: string): FileStreamHostError {
  if (error instanceof FileStreamHostError) return error;
  if (error instanceof Error && "code" in error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return new FileStreamHostError(HOST_IO_NOT_FOUND, MSG_IO_NOT_FOUND, path);
    }
    if (code === "EACCES" || code === "EPERM") {
      return new FileStreamHostError(HOST_IO_PERMISSION, MSG_IO_PERMISSION, path);
    }
  }
  return new FileStreamHostError(HOST_IO_ERROR, MSG_IO_ERROR, path);
}

export class FileTrajectoryStream {
  readonly root: string;
  readonly path: string;
  #state: ReturnType<typeof createStream>;
  #fileOffset = 0;
  #hostPending: Uint8Array = new Uint8Array(0);
  #first = true;
  #polls = 0;
  #closed = false;
  #identity: { dev: number; ino: number; mtimeNs: bigint } | null = null;
  #reconcileEvery: number;
  #sourceRevision: string;
  #pollInterval: number;

  private constructor(
    root: string,
    path: string,
    state: ReturnType<typeof createStream>,
    pollInterval: number,
    reconcileEvery: number,
    sourceRevision: string,
  ) {
    this.root = root;
    this.path = path;
    this.#state = state;
    this.#pollInterval = pollInterval;
    this.#reconcileEvery = reconcileEvery;
    this.#sourceRevision = sourceRevision;
  }

  static open(options: FileStreamOptions): FileTrajectoryStream {
    if (!options.root || !options.root.trim()) {
      throw new FileStreamHostError(HOST_ROOT_REQUIRED, MSG_ROOT_REQUIRED);
    }
    if (!options.path || !options.path.trim()) {
      throw new FileStreamHostError(HOST_PATH_REQUIRED, MSG_PATH_REQUIRED);
    }
    const root = resolve(options.root);
    const path = resolve(options.path);
    if (!isUnderRoot(root, path)) {
      throw new FileStreamHostError(HOST_PATH_OUTSIDE_ROOT, MSG_PATH_OUTSIDE_ROOT, path);
    }
    let streamOpts: StreamOptions;
    if (options.stream !== undefined) {
      streamOpts =
        options.groupId !== undefined && options.stream.groupId === undefined
          ? { ...options.stream, groupId: options.groupId }
          : options.stream;
    } else if (options.groupId !== undefined) {
      streamOpts = { source: options.source, groupId: options.groupId };
    } else {
      streamOpts = { source: options.source };
    }
    return new FileTrajectoryStream(
      root,
      path,
      createStream(streamOpts),
      options.pollInterval ?? 0.05,
      options.reconcileEvery ?? 0,
      options.sourceRevision ?? "file-0",
    );
  }

  get cursor() {
    return this.#state.cursor;
  }

  get state() {
    return this.#state;
  }

  async poll(): Promise<StreamUpdate | null> {
    if (this.#closed) return null;
    const { size, identity } = await this.#statIdentity();
    if (size < this.#fileOffset) {
      return this.#snapshotFull(size, identity);
    }
    if (this.#first) {
      return this.#snapshotFull(size, identity);
    }
    if (this.#identityChanged(identity, size)) {
      return this.#snapshotFull(size, identity);
    }
    if (size > this.#fileOffset) {
      return this.#appendGrowth(size, identity);
    }
    this.#polls += 1;
    if (this.#reconcileEvery > 0 && this.#polls % this.#reconcileEvery === 0) {
      return this.#reconcileSnapshot(size, identity);
    }
    this.#identity = identity;
    return null;
  }

  /**
   * Yield non-empty updates until closed or AbortSignal aborts.
   * Abort/cancel stops the follow loop only; it does not call `finish()`.
   * Call `finish()` explicitly for end-of-stream (flush host pending + core finish).
   */
  async *follow(signal?: AbortSignal): AsyncGenerator<StreamUpdate, void, void> {
    const waitMs = Math.max(0, this.#pollInterval * 1000);
    while (!this.#closed && !(signal?.aborted ?? false)) {
      const update = await this.poll();
      if (update !== null && update.kind !== "unchanged") {
        yield update;
      }
      if (waitMs > 0) {
        await sleep(waitMs, signal);
      }
    }
  }

  /**
   * Finish the underlying core stream. Forwards host-held incomplete line into
   * core pending first so finish can commit a final unterminated line.
   * Host pending is retained until core apply succeeds; non-success returns
   * without calling finish. Distinct from AbortSignal/follow cancellation and
   * from `close()`.
   */
  finish(): StreamUpdate {
    if (this.#hostPending.length > 0) {
      const result = applyAppend(
        this.#state,
        this.#hostPending,
        undefined,
        this.#sourceRevision,
      );
      this.#state = result.state;
      if (result.update.kind !== "updated" && result.update.kind !== "unchanged") {
        return result.update;
      }
      this.#hostPending = new Uint8Array(0);
    }
    const finished = finishStream(this.#state);
    this.#state = finished.state;
    return finished.update;
  }

  close(): void {
    this.#closed = true;
  }

  async #snapshotFull(
    size: number,
    identity: { dev: number; ino: number; mtimeNs: bigint },
  ): Promise<StreamUpdate> {
    const material = await this.#readRange(0, size);
    this.#fileOffset = size;
    const { complete, pending } = splitCompleteLines(material);
    this.#hostPending = copyBytes(pending);
    this.#first = false;
    this.#polls += 1;
    this.#identity = identity;
    const result = applySnapshot(this.#state, complete, this.#sourceRevision);
    this.#state = result.state;
    return result.update;
  }

  async #reconcileSnapshot(
    size: number,
    identity: { dev: number; ino: number; mtimeNs: bigint },
  ): Promise<StreamUpdate | null> {
    const material = await this.#readRange(0, size);
    const { complete, pending } = splitCompleteLines(material);
    this.#hostPending = copyBytes(pending);
    this.#fileOffset = size;
    this.#identity = identity;
    const result = applySnapshot(this.#state, complete, this.#sourceRevision);
    this.#state = result.state;
    if (result.update.kind === "unchanged") return null;
    return result.update;
  }

  async #appendGrowth(
    size: number,
    identity: { dev: number; ino: number; mtimeNs: bigint },
  ): Promise<StreamUpdate | null> {
    const chunk = await this.#readRange(this.#fileOffset, size);
    this.#fileOffset = size;
    const buf = concatBytes(this.#hostPending, chunk);
    const { complete, pending } = splitCompleteLines(buf);
    this.#hostPending = copyBytes(pending);
    this.#polls += 1;
    this.#identity = identity;
    if (complete.length === 0) return null;
    const result = applyAppend(this.#state, complete, undefined, this.#sourceRevision);
    this.#state = result.state;
    if (result.update.kind === "unchanged") return null;
    return result.update;
  }

  #identityChanged(
    identity: { dev: number; ino: number; mtimeNs: bigint },
    size: number,
  ): boolean {
    if (this.#identity === null) return false;
    if (identity.dev !== this.#identity.dev || identity.ino !== this.#identity.ino) {
      return true;
    }
    return size === this.#fileOffset && identity.mtimeNs !== this.#identity.mtimeNs;
  }

  async #statIdentity(): Promise<{
    size: number;
    identity: { dev: number; ino: number; mtimeNs: bigint };
  }> {
    try {
      const info = await stat(this.path);
      const ns = (info as { mtimeNs?: bigint }).mtimeNs;
      const mtimeNs =
        typeof ns === "bigint" ? ns : BigInt(Math.round(info.mtimeMs * 1e6));
      return {
        size: info.size,
        identity: { dev: info.dev, ino: info.ino, mtimeNs },
      };
    } catch (error) {
      throw mapIoError(error, this.path);
    }
  }

  async #readRange(start: number, end: number): Promise<Uint8Array> {
    if (end <= start) return new Uint8Array(0);
    try {
      const handle = await open(this.path, "r");
      try {
        const length = end - start;
        const buffer = Buffer.alloc(length);
        const { bytesRead } = await handle.read(buffer, 0, length, start);
        return new Uint8Array(buffer.buffer, buffer.byteOffset, bytesRead);
      } finally {
        await handle.close();
      }
    } catch (error) {
      throw mapIoError(error, this.path);
    }
  }
}

export function openFileStream(options: FileStreamOptions): FileTrajectoryStream {
  return FileTrajectoryStream.open(options);
}

export async function* followFile(
  options: FileStreamOptions,
  signal?: AbortSignal,
): AsyncGenerator<StreamUpdate, void, void> {
  const stream = FileTrajectoryStream.open(options);
  try {
    yield* stream.follow(signal);
  } finally {
    stream.close();
  }
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function copyBytes(data: Uint8Array): Uint8Array {
  const out = new Uint8Array(data.length);
  out.set(data);
  return out;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolvePromise, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new Error("aborted"));
      return;
    }
    const timer = setTimeout(() => resolvePromise(), ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason ?? new Error("aborted"));
      },
      { once: true },
    );
  });
}

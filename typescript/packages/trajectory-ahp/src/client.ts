/**
 * AHP stream client: auth callback + subscribe + feed core applyAhp_*.
 * Transport only; auth never enters stream snapshots/deltas/diagnostics.
 */

import {
  applyAhpActions,
  applyAhpSnapshot,
  createStream,
  resetStream,
  type StreamCursor,
  type StreamOptions,
  type StreamState,
  type StreamUpdate,
} from "@hypabolic/trajectory";

import {
  ERR_AUTH_FAILED,
  ERR_AUTH_REQUIRED,
  ERR_BACKPRESSURE,
  ERR_CANCELLED,
  ERR_PROTOCOL,
  ERR_RESYNC_REQUIRED,
  ERR_TRANSPORT,
  authenticateParams,
  encodeRequest,
  initializeParams,
  parseMessage,
  resyncParams,
  safeErrorMessage,
  subscribeParams,
  type JsonObject,
} from "./protocol.js";
import type { AhpTransport } from "./transport.js";

export type AhpAuthCredentials = { token: string };
export type AhpAuthCallback = (
  challenge: JsonObject | null,
) => AhpAuthCredentials | null | Promise<AhpAuthCredentials | null>;

export type AhpClientEventKind =
  | "stream-update"
  | "auth-required"
  | "auth-failed"
  | "resync-required"
  | "backpressure"
  | "disconnected"
  | "error"
  | "ready";

export interface AhpClientEvent {
  readonly kind: AhpClientEventKind;
  readonly update?: StreamUpdate;
  readonly code?: string;
  readonly message?: string;
  readonly buffered?: number;
}

export interface AhpClientOptions {
  readonly chatChannel: string;
  readonly auth?: AhpAuthCallback;
  readonly streamOptions?: StreamOptions;
  readonly autoResync?: boolean;
  readonly maxBufferedActions?: number;
  readonly fromServerSeq?: number;
  readonly protocolVersion?: string;
}

export class AhpStreamClient {
  private state: StreamState;
  private nextId = 1;
  private readonly pending = new Map<number, string>();
  private actionBuffer: JsonObject[] = [];
  private paused = false;
  private cancelled = false;
  private resyncInflight = false;
  private authTokenHeld: string | null = null;
  private readonly autoResync: boolean;
  private readonly maxBufferedActions: number;
  private readonly protocolVersion: string;
  private readonly onEvent: (event: AhpClientEvent) => void;

  constructor(
    readonly transport: AhpTransport,
    readonly options: AhpClientOptions,
    onEvent: (event: AhpClientEvent) => void = () => undefined,
  ) {
    this.onEvent = onEvent;
    this.autoResync = options.autoResync ?? true;
    this.maxBufferedActions = options.maxBufferedActions ?? 256;
    this.protocolVersion = options.protocolVersion ?? "0.7.0";
    const base = options.streamOptions;
    const streamOptions: StreamOptions = {
      source: "ahp",
      groupId: base?.groupId ?? options.chatChannel,
      ...(base?.delivery !== undefined ? { delivery: base.delivery } : {}),
      ...(base?.includeProvisional !== undefined
        ? { includeProvisional: base.includeProvisional }
        : {}),
      ...(base?.requireCompleteLines !== undefined
        ? { requireCompleteLines: base.requireCompleteLines }
        : {}),
      ...(base?.finalizeOnClose !== undefined
        ? { finalizeOnClose: base.finalizeOnClose }
        : {}),
      ...(base?.maxPendingBytes !== undefined
        ? { maxPendingBytes: base.maxPendingBytes }
        : {}),
      ...(base?.maxLineBytes !== undefined ? { maxLineBytes: base.maxLineBytes } : {}),
      ...(base?.normalize !== undefined ? { normalize: base.normalize } : {}),
      ahpProtocolVersion: base?.ahpProtocolVersion ?? this.protocolVersion,
    };
    this.state = createStream(streamOptions);
    this.transport.setHandler((raw) => this.onFrame(raw));
  }

  get cursor(): StreamCursor {
    return this.state.cursor;
  }

  get streamState(): StreamState {
    return this.state;
  }

  get isCancelled(): boolean {
    return this.cancelled;
  }

  /** @internal test helper for backpressure */
  setPausedForTest(value: boolean): void {
    this.paused = value;
  }

  start(): void {
    if (this.cancelled) {
      this.emitError(ERR_CANCELLED);
      return;
    }
    this.request("initialize", initializeParams(this.protocolVersion));
  }

  cancel(): void {
    this.cancelled = true;
    this.actionBuffer = [];
    this.authTokenHeld = null;
    try {
      this.transport.close();
    } catch {
      // ignore
    }
    this.onEvent({
      kind: "disconnected",
      code: ERR_CANCELLED,
      message: safeErrorMessage(ERR_CANCELLED),
    });
  }

  resume(): void {
    this.paused = false;
    this.flushActions();
  }

  private request(method: string, params: JsonObject): number {
    const id = this.nextId++;
    this.pending.set(id, method);
    try {
      this.transport.send(encodeRequest(id, method, params));
    } catch {
      this.emitError(ERR_TRANSPORT);
    }
    return id;
  }

  private onFrame(raw: string): void {
    if (this.cancelled) return;
    let msg: JsonObject;
    try {
      msg = parseMessage(raw);
    } catch {
      this.emitError(ERR_PROTOCOL);
      return;
    }
    if ("method" in msg && !("id" in msg)) {
      this.handleNotification(msg);
      return;
    }
    if ("id" in msg) {
      this.handleResponse(msg);
      return;
    }
    this.emitError(ERR_PROTOCOL);
  }

  private handleResponse(msg: JsonObject): void {
    let rawId = msg.id;
    if (typeof rawId === "string") rawId = Number(rawId);
    if (typeof rawId !== "number" || !Number.isFinite(rawId)) {
      this.emitError(ERR_PROTOCOL);
      return;
    }
    const method = this.pending.get(rawId);
    this.pending.delete(rawId);
    if (method === undefined) return;

    if ("error" in msg) {
      const err = msg.error;
      const errMsg =
        err !== null && typeof err === "object" && !Array.isArray(err)
          ? String((err as JsonObject).message ?? "")
          : "";
      const lower = errMsg.toLowerCase();
      if (method === "authenticate" || lower.includes("auth")) {
        this.authTokenHeld = null;
        this.onEvent({
          kind: "auth-failed",
          code: ERR_AUTH_FAILED,
          message: safeErrorMessage(ERR_AUTH_FAILED),
        });
        return;
      }
      if (method === "initialize" && (lower.includes("auth") || lower.includes("unauthor"))) {
        this.beginAuth(null);
        return;
      }
      this.emitError(ERR_PROTOCOL);
      return;
    }

    const result = msg.result;
    if (method === "initialize") {
      if (
        result !== null &&
        typeof result === "object" &&
        !Array.isArray(result) &&
        (result as JsonObject).authRequired === true
      ) {
        const challenge = (result as JsonObject).authChallenge;
        this.beginAuth(
          challenge !== null && typeof challenge === "object" && !Array.isArray(challenge)
            ? (challenge as JsonObject)
            : null,
        );
        return;
      }
      this.sendSubscribe();
      return;
    }
    if (method === "authenticate") {
      this.authTokenHeld = null;
      this.sendSubscribe();
      return;
    }
    if (method === "subscribe") {
      this.onEvent({ kind: "ready" });
      if (result !== null && typeof result === "object" && !Array.isArray(result)) {
        this.ingestSubscribeResult(result as JsonObject);
      }
      return;
    }
    if (method === "resync") {
      // Keep resyncInflight true until reset + snapshot apply finish so
      // re-entrant action notifications drop mid-resync.
      if (result !== null && typeof result === "object" && !Array.isArray(result)) {
        this.applyResyncSnapshot(result as JsonObject);
      } else {
        this.resyncInflight = false;
      }
    }
  }

  private handleNotification(msg: JsonObject): void {
    const method = msg.method;
    const params =
      msg.params !== null && typeof msg.params === "object" && !Array.isArray(msg.params)
        ? (msg.params as JsonObject)
        : {};
    if (method === "auth/required" || method === "authRequired") {
      this.beginAuth(Object.keys(params).length ? params : null);
      return;
    }
    if (method === "action" || method === "channel/action") {
      if (!this.notificationChannelOk(params)) return;
      const envelope =
        "envelope" in params &&
        params.envelope !== null &&
        typeof params.envelope === "object" &&
        !Array.isArray(params.envelope)
          ? (params.envelope as JsonObject)
          : params;
      if ("action" in envelope) this.bufferAction(envelope);
      return;
    }
    if (method === "snapshot" || method === "channel/snapshot") {
      if (!this.notificationChannelOk(params)) return;
      this.applyHostSnapshot(params);
    }
  }

  /** Ignore action/snapshot noise for a channel we did not subscribe to. */
  private notificationChannelOk(params: JsonObject): boolean {
    const channel = params.channel;
    if (typeof channel !== "string") {
      // Protocol requires channel on notifications; treat missing as foreign noise.
      return false;
    }
    return channel === this.options.chatChannel;
  }

  private beginAuth(challenge: JsonObject | null): void {
    this.onEvent({
      kind: "auth-required",
      code: ERR_AUTH_REQUIRED,
      message: safeErrorMessage(ERR_AUTH_REQUIRED),
    });
    if (!this.options.auth) {
      this.onEvent({
        kind: "auth-failed",
        code: ERR_AUTH_FAILED,
        message: safeErrorMessage(ERR_AUTH_FAILED),
      });
      return;
    }
    let result: AhpAuthCredentials | null | Promise<AhpAuthCredentials | null>;
    try {
      result = this.options.auth(challenge);
    } catch {
      this.onEvent({
        kind: "auth-failed",
        code: ERR_AUTH_FAILED,
        message: safeErrorMessage(ERR_AUTH_FAILED),
      });
      return;
    }
    if (result !== null && typeof result === "object" && "then" in result) {
      void (result as Promise<AhpAuthCredentials | null>).then(
        (creds) => this.finishAuth(creds),
        () => {
          // Late rejection after cancel must not emit transport/auth events.
          if (this.cancelled) return;
          this.onEvent({
            kind: "auth-failed",
            code: ERR_AUTH_FAILED,
            message: safeErrorMessage(ERR_AUTH_FAILED),
          });
        },
      );
      return;
    }
    this.finishAuth(result as AhpAuthCredentials | null);
  }

  private finishAuth(creds: AhpAuthCredentials | null): void {
    // Post-async entry: cancel must stop auth completion (no re-hold token /
    // authenticate / transport error after cancel).
    if (this.cancelled) return;
    if (!creds?.token) {
      this.onEvent({
        kind: "auth-failed",
        code: ERR_AUTH_FAILED,
        message: safeErrorMessage(ERR_AUTH_FAILED),
      });
      return;
    }
    this.authTokenHeld = creds.token;
    this.request("authenticate", authenticateParams(creds.token));
  }

  private sendSubscribe(): void {
    this.request(
      "subscribe",
      subscribeParams(this.options.chatChannel, this.options.fromServerSeq),
    );
  }

  private ingestSubscribeResult(result: JsonObject): void {
    if ("snapshot" in result) this.applyHostSnapshot(result);
    const actions = result.actions;
    if (Array.isArray(actions)) {
      for (const item of actions) {
        if (item !== null && typeof item === "object" && !Array.isArray(item)) {
          this.bufferAction(item as JsonObject);
        }
      }
      this.flushActions();
    }
  }

  private bufferAction(envelope: JsonObject): void {
    if (this.resyncInflight) return;
    if (this.actionBuffer.length >= this.maxBufferedActions) {
      this.paused = true;
      this.onEvent({
        kind: "backpressure",
        code: ERR_BACKPRESSURE,
        message: safeErrorMessage(ERR_BACKPRESSURE),
        buffered: this.actionBuffer.length,
      });
      return;
    }
    this.actionBuffer.push(envelope);
    if (!this.paused) this.flushActions();
  }

  private flushActions(): void {
    if (this.cancelled || this.resyncInflight || this.actionBuffer.length === 0) return;
    const batch = this.actionBuffer;
    this.actionBuffer = [];
    const lines = batch.map((env) => JSON.stringify(env));
    const data = new TextEncoder().encode(`${lines.join("\n")}\n`);
    const { state, update } = applyAhpActions(this.state, data);
    this.state = state;
    this.emitUpdate(update);
    if (update.kind === "reset-required" && update.reset?.reason === "sequence-gap") {
      this.handleSequenceGap(update);
    }
  }

  private applyHostSnapshot(params: JsonObject): void {
    let snapshotObj = params.snapshot ?? params.chat;
    if (snapshotObj === undefined && "ahpProtocolVersion" in params) {
      snapshotObj = params;
    }
    if (snapshotObj === null || typeof snapshotObj !== "object" || Array.isArray(snapshotObj)) {
      return;
    }
    let materialObj = snapshotObj as JsonObject;
    if (!("chat" in materialObj) && "turns" in materialObj) {
      materialObj = {
        ahpProtocolVersion: params.ahpProtocolVersion ?? this.protocolVersion,
        chat: materialObj,
      };
    } else if (!("ahpProtocolVersion" in materialObj)) {
      materialObj = {
        ...materialObj,
        ahpProtocolVersion: params.ahpProtocolVersion ?? this.protocolVersion,
      };
    }
    const revisionRaw = params.revision ?? params.sourceRevision ?? "host-snapshot";
    const revision = typeof revisionRaw === "string" ? revisionRaw : String(revisionRaw);
    const material = new TextEncoder().encode(JSON.stringify(materialObj));
    const { state, update } = applyAhpSnapshot(this.state, material, revision);
    this.state = state;
    this.emitUpdate(update);
  }

  private handleSequenceGap(update: StreamUpdate): void {
    this.onEvent({
      kind: "resync-required",
      update,
      code: ERR_RESYNC_REQUIRED,
      message: safeErrorMessage(ERR_RESYNC_REQUIRED),
    });
    if (!this.autoResync) return;
    this.resyncInflight = true;
    this.actionBuffer = [];
    this.request("resync", resyncParams(this.options.chatChannel));
  }

  private applyResyncSnapshot(result: JsonObject): void {
    const prior = this.state.cursor;
    const { state } = resetStream(this.state, {
      reason: "sequence-gap",
      priorCursor: prior,
      sourceRevision: String(result.revision ?? "resync"),
    });
    this.state = state;
    this.applyHostSnapshot(result);
    this.resyncInflight = false;
  }

  private emitUpdate(update: StreamUpdate): void {
    this.onEvent({ kind: "stream-update", update });
  }

  private emitError(code: string): void {
    this.onEvent({
      kind: "error",
      code,
      message: safeErrorMessage(code),
    });
  }
}

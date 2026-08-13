/** Programmable fake AHP host for CI (no real network). */

import {
  AHP_ROOT_CHANNEL,
  encodeError,
  encodeNotification,
  encodeResult,
  parseMessage,
  type JsonObject,
} from "./protocol.js";
import type { MemoryAhpTransport } from "./transport.js";

export interface FakeAhpHostScript {
  requireAuth?: boolean;
  acceptToken?: string | null;
  initialSnapshot?: JsonObject | null;
  initialRevision?: string;
  initialActions?: JsonObject[];
}

export class FakeAhpHost {
  private closed = false;
  authAttempts = 0;
  subscribeCount = 0;
  resyncCount = 0;
  readonly receivedMethods: string[] = [];

  constructor(
    readonly transport: MemoryAhpTransport,
    readonly script: FakeAhpHostScript,
    readonly chatChannel: string,
  ) {
    this.transport.setHandler((raw) => this.onFrame(raw));
  }

  close(): void {
    this.closed = true;
    this.transport.close();
  }

  pushAction(envelope: JsonObject): void {
    this.transport.send(
      encodeNotification("action", {
        channel: this.chatChannel,
        envelope,
      }),
    );
  }

  pushActions(envelopes: JsonObject[]): void {
    for (const env of envelopes) this.pushAction(env);
  }

  pushSnapshot(snapshot: JsonObject, revision = "rev-push"): void {
    this.transport.send(
      encodeNotification("snapshot", {
        channel: this.chatChannel,
        revision,
        snapshot,
      }),
    );
  }

  private onFrame(raw: string): void {
    if (this.closed) return;
    let msg: JsonObject;
    try {
      msg = parseMessage(raw);
    } catch {
      return;
    }
    const method = msg.method;
    const reqId = msg.id;
    const params =
      msg.params !== null && typeof msg.params === "object" && !Array.isArray(msg.params)
        ? (msg.params as JsonObject)
        : {};
    if (typeof method !== "string" || reqId === undefined || reqId === null) return;
    this.receivedMethods.push(method);

    if (method === "initialize") {
      const result: JsonObject = {
        channel: AHP_ROOT_CHANNEL,
        protocolVersion: "0.7.0",
      };
      if (this.script.requireAuth) result.authRequired = true;
      this.transport.send(encodeResult(reqId as number | string, result));
      return;
    }

    if (method === "authenticate") {
      this.authAttempts += 1;
      const token = params.token;
      if (
        this.script.acceptToken !== undefined &&
        this.script.acceptToken !== null &&
        token === this.script.acceptToken
      ) {
        this.transport.send(encodeResult(reqId as number | string, { ok: true }));
      } else if (this.script.acceptToken === undefined && token === "test-token") {
        this.transport.send(encodeResult(reqId as number | string, { ok: true }));
      } else {
        this.transport.send(
          encodeError(reqId as number | string, -32001, "authentication failed"),
        );
      }
      return;
    }

    if (method === "subscribe") {
      this.subscribeCount += 1;
      const channel = typeof params.channel === "string" ? params.channel : this.chatChannel;
      const result: JsonObject = { channel };
      if (this.script.initialSnapshot) {
        result.revision = this.script.initialRevision ?? "rev-1";
        result.snapshot = this.script.initialSnapshot;
      }
      if (this.script.initialActions?.length) {
        result.actions = this.script.initialActions;
      }
      this.transport.send(encodeResult(reqId as number | string, result));
      return;
    }

    if (method === "resync") {
      this.resyncCount += 1;
      const snap =
        this.script.initialSnapshot ??
        ({
          ahpProtocolVersion: "0.7.0",
          chat: { id: this.chatChannel, turns: [], activeTurn: null },
        } as JsonObject);
      this.transport.send(
        encodeResult(reqId as number | string, {
          channel: this.chatChannel,
          revision: `resync-${this.resyncCount}`,
          snapshot: snap,
        }),
      );
      return;
    }

    this.transport.send(encodeError(reqId as number | string, -32601, "method not found"));
  }
}

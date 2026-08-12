/** Minimal AHP JSON-RPC framing (protocol pin 0.7.x). */

export const AHP_ROOT_CHANNEL = "ahp-root://";
export const PROTOCOL_VERSION = "0.7.0";
export const CLIENT_NAME = "hypabolic-trajectory-ahp";

export const ERR_AUTH_FAILED = "ahp_auth_failed";
export const ERR_AUTH_REQUIRED = "ahp_auth_required";
export const ERR_TRANSPORT = "ahp_transport_error";
export const ERR_PROTOCOL = "ahp_protocol_error";
export const ERR_BACKPRESSURE = "ahp_backpressure";
export const ERR_CANCELLED = "ahp_cancelled";
export const ERR_RESYNC_REQUIRED = "ahp_resync_required";

export type JsonObject = Record<string, unknown>;

export function encodeRequest(id: number, method: string, params: JsonObject): string {
  return JSON.stringify({ jsonrpc: "2.0", id, method, params });
}

export function encodeNotification(method: string, params: JsonObject): string {
  return JSON.stringify({ jsonrpc: "2.0", method, params });
}

export function encodeResult(id: number | string, result: unknown): string {
  return JSON.stringify({ jsonrpc: "2.0", id, result });
}

export function encodeError(id: number | string | null, code: number, message: string): string {
  const body: JsonObject = {
    jsonrpc: "2.0",
    error: { code, message },
  };
  if (id !== null) body.id = id;
  return JSON.stringify(body);
}

export function parseMessage(raw: string): JsonObject {
  const value: unknown = JSON.parse(raw);
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(ERR_PROTOCOL);
  }
  return value as JsonObject;
}

export function initializeParams(protocolVersion = PROTOCOL_VERSION): JsonObject {
  return {
    channel: AHP_ROOT_CHANNEL,
    protocolVersion,
    clientInfo: { name: CLIENT_NAME, version: "0.1.2" },
  };
}

export function authenticateParams(token: string): JsonObject {
  return { channel: AHP_ROOT_CHANNEL, token };
}

export function subscribeParams(channel: string, fromSeq?: number): JsonObject {
  const params: JsonObject = { channel };
  if (fromSeq !== undefined) params.fromSeq = fromSeq;
  return params;
}

export function resyncParams(channel: string): JsonObject {
  return { channel };
}

export function safeErrorMessage(code: string): string {
  switch (code) {
    case ERR_AUTH_FAILED:
      return "AHP authentication failed.";
    case ERR_AUTH_REQUIRED:
      return "AHP authentication is required.";
    case ERR_TRANSPORT:
      return "AHP transport error.";
    case ERR_PROTOCOL:
      return "AHP protocol error.";
    case ERR_BACKPRESSURE:
      return "AHP client backpressure limit reached.";
    case ERR_CANCELLED:
      return "AHP client cancelled.";
    case ERR_RESYNC_REQUIRED:
      return "AHP sequence gap requires resync.";
    default:
      return "AHP client error.";
  }
}

/**
 * Optional AHP live-host client (LS-10).
 * Not part of @hypabolic/trajectory core; import this package explicitly.
 */

export {
  AhpStreamClient,
  type AhpAuthCallback,
  type AhpAuthCredentials,
  type AhpClientEvent,
  type AhpClientEventKind,
  type AhpClientOptions,
} from "./client.js";
export { FakeAhpHost, type FakeAhpHostScript } from "./fake-host.js";
export {
  AHP_ROOT_CHANNEL,
  CLIENT_NAME,
  ERR_AUTH_FAILED,
  ERR_AUTH_REQUIRED,
  ERR_BACKPRESSURE,
  ERR_CANCELLED,
  ERR_PROTOCOL,
  ERR_RESYNC_REQUIRED,
  ERR_TRANSPORT,
  PROTOCOL_VERSION,
  safeErrorMessage,
} from "./protocol.js";
export {
  InMemoryAhpTransportPair,
  MemoryAhpTransport,
  type AhpTransport,
  type MessageHandler,
} from "./transport.js";

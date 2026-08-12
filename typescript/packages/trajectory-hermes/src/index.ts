/**
 * Optional Hermes provider streaming (LS-07h).
 * Not part of @hypabolic/trajectory core; import this package explicitly.
 */

export {
  HermesProviderStream,
  MemoryHermesStore,
  SqliteHermesProvider,
  computeChangeToken,
  exportSessionJson,
  HOST_DB_ERROR,
  HOST_SESSION_NOT_FOUND,
  HOST_STORE_REQUIRED,
  HermesHostError,
  type HermesProviderOptions,
  type HermesSessionInfo,
  type HermesStore,
} from "./provider.js";

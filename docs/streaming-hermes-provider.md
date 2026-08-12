# Hermes optional provider streaming (LS-07h)

Optional packages that **query** Hermes SQLite rows (or an in-memory fixture store)
and feed pure core `apply_hermes_export`. Core stays SQLite-free and does **not**
byte-tail `state.db`.

Normative product design: [`live-session-streaming.md`](live-session-streaming.md).  
Wire contract: [`contracts/spec/streaming.md`](../contracts/spec/streaming.md).

## Package boundary

| In core (all four runtimes) | Optional Hermes provider packages |
| --- | --- |
| `apply_hermes_export` | List sessions when SQLite available |
| Hermes decode (batch) | Query ordered active rows in a read transaction |
| `hermes-row` cursor | Change token / database generation |
| Snapshot + delta, soft-delete reset law | Memory fixture store for CI |

**Core packages must not import these providers.** Capability
`stream-hermes-provider` is advertised only on these optional packages’
`package-capabilities.json` (LS-12) — never on core.

### Package names

| Runtime | Package |
| --- | --- |
| .NET | `Hypabolic.Trajectory.Hermes` |
| TypeScript | `@hypabolic/trajectory-hermes` |
| Rust | `hypabolic-trajectory-hermes` |
| Python | `hypabolic_trajectory.hermes_provider` (`[hermes]` extra; stdlib `sqlite3`) |

## Responsibilities

1. **List** sessions from a store locator (file path or directory + `state.db`).
2. **Export** one session as Hermes JSON: message array or
   `{ "session": …, "messages": […] }` with soft-deleted (`active = 0`) rows
   excluded from the active ordered export.
3. **Change token** = SHA-256 over ordered active-row fingerprints (id, role,
   content, tool fields, active). Opaque to core.
4. **Database generation** = opaque open/token identity; change forces
   `reset-required` / new generation.
5. **Feed core** only through `apply_hermes_export`.
6. **Prior-row mutation / soft-delete:** core detects when committed ordered
   fingerprints are not a pure prefix of the new export → `reset-required`
   (`source-replaced`) with cursor unchanged. Caller/provider installs a new
   generation via `reset` + full export.
7. **Never byte-tail** the SQLite file as JSONL.

## Core apply law (`apply_hermes_export`)

1. Require source `hermes`.
2. Parse export; fingerprint ordered active rows; full re-normalize via existing
   Hermes decoder.
3. Cursor family `hermes-row`: `database_generation`, optional `last_row_id`
   (when all active ids are numeric), `change_token`.
4. Idempotent when generation + token + content SHA match.
5. Default delivery is **both** snapshot and delta.
6. Nonnumeric ids: `last_row_id` is null; fingerprint sequence still governs
   prefix safety.

## Privacy

- No paths, secrets, raw SQL, or transcript prose in stream diagnostics.
- Host errors (`store_required`, `session_not_found`, `db_error`) are distinct
  from stream diagnostics.

## Tests (shared behavioral scenarios)

| Scenario | Expectation |
| --- | --- |
| Snapshot export | `updated`; `hermes-row` cursor advances |
| Insert growth | Prefix fingerprints stable; new records via delta |
| Soft-delete | `reset-required` / `source-replaced`; cursor frozen |
| Nonnumeric ids | `last_row_id` null; records still normalize |
| Without provider package | Core still applies export when caller supplies JSON |
| Core package graph | No SQLite import in core streaming modules |

## Capability advertising

Claim `stream-hermes-provider` only on optional Hermes provider packages
(`package-capabilities.json`). Core `runtime-capabilities.json` must not list
it. Unimplemented stream names must not be claimed.

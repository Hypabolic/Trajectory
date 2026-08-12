# Optional AHP live-host client (LS-10)

Transport-only packages that connect to an Agent Host Protocol (AHP) host,
authenticate via an **injected callback**, subscribe to a chat channel, and feed
pure core `apply_ahp_snapshot` / `apply_ahp_actions`.

Normative product design: [`live-session-streaming.md`](live-session-streaming.md).  
Core reducer (not this package): [`ahp-action-streaming.md`](ahp-action-streaming.md).  
Wire contract: [`contracts/spec/streaming.md`](../contracts/spec/streaming.md).

## Package boundary

| In core (all four runtimes) | Optional AHP client packages |
| --- | --- |
| `apply_ahp_snapshot` | Connect, JSON-RPC framing, subscribe |
| `apply_ahp_actions` + Shape B reducer | Auth **callback** (app-supplied) |
| Cursors, snapshot+delta, diagnostics | Reconnect / resync policy |
| | Fake-host test doubles |

**Core packages must not import these clients.** Capability
`stream-ahp-client` is not advertised until LS-12.

### Package names

| Runtime | Package |
| --- | --- |
| .NET | `Hypabolic.Trajectory.Ahp` |
| TypeScript | `@hypabolic/trajectory-ahp` |
| Rust | `hypabolic-trajectory-ahp` |
| Python | `hypabolic_trajectory.ahp_client` (import path; same distro as core, not imported by root `__init__`) |

## Responsibilities

1. **Connect** over an injected `AhpTransport` (text JSON-RPC frames). Real
   WebSocket adapters are consumer-owned wrappers of the same interface.
2. **Authenticate** only via app-supplied callback when the host requires it.
   Tokens never enter `StreamState`, stream snapshots, deltas, or diagnostics.
3. **Subscribe** to one `ahp-chat:/…` channel; accept initial snapshot and/or
   action batch; accept subsequent `action` / `snapshot` notifications.
4. **Feed core** exclusively through `apply_ahp_*`.
5. **Sequence gap:** core returns `reset-required` / `reason=sequence-gap` with
   cursor **unchanged**. Client emits `resync-required` and, when
   `auto_resync` is true, requests a host resync snapshot, installs a new
   generation via core `reset`, then applies the snapshot.
6. **Cancel** stops transport; last committed stream cursor remains valid.
7. **Backpressure:** bounded action buffer; excess emits a host-level
   `backpressure` event (not a transcript diagnostic).

## Privacy

- No tokens, cookies, WebSocket URLs with secrets, paths, raw action bodies, or
  transcript prose in stream diagnostics or client error messages.
- Client events use fixed codes (`ahp_auth_failed`, `ahp_resync_required`, …)
  with fixed safe messages.
- Auth material is held only long enough to send `authenticate`, then scrubbed.

## Fake-host CI

Each package ships an in-memory duplex + `FakeAhpHost` used by unit tests for:

| Scenario | Expectation |
| --- | --- |
| Subscribe + action feed | Core `updated`; `ahp-server-seq` cursor advances |
| Auth failure | `auth-required` then `auth-failed`; no `ready` |
| Auth success | Token absent from stream update JSON |
| Sequence gap | `resync-required`; resync RPC when auto-resync on |
| Duplicate / replay | No crash; core idempotency / gap rules apply |
| Backpressure | `backpressure` when buffer limit hit while paused |
| Cancel | Cursor generation + last seq preserved |

No real network sockets are required for CI.

## Minimal wire subset (protocol pin 0.7.x)

Commands (client → host), params always include `channel`:

| Method | Channel | Notes |
| --- | --- | --- |
| `initialize` | `ahp-root://` | `protocolVersion`, `clientInfo` |
| `authenticate` | `ahp-root://` | `token` from callback only |
| `subscribe` | chat URI | optional `fromSeq` |
| `resync` | chat URI | full snapshot after gap |

Host → client:

| Shape | Notes |
| --- | --- |
| RPC results | `authRequired`, subscribe `{ snapshot?, actions? }`, resync snapshot |
| `action` notification | `{ channel, envelope }` ActionEnvelope |
| `snapshot` notification | `{ channel, revision, snapshot }` Shape A |
| `auth/required` | Triggers callback |

This is a **Trajectory transport subset**, not a full AHP SDK.

## Usage sketch

```text
transport = consumer WebSocket or InMemoryAhpTransportPair.client
client = AhpStreamClient(transport, options={
  chat_channel: "ahp-chat:/…",
  auth: () => ({ token: load_secret() }),  # never pass into stream APIs
  auto_resync: true,
}, on_event)
client.start()
# on_event: stream-update | auth-* | resync-required | backpressure | …
client.cancel()  # cursor still readable
```

## Non-goals

- Daemon or global multi-session supervisor
- Loading `auth.json` inside Trajectory packages
- Advertising `stream-ahp-client` before LS-12
- Replacing official language AHP SDKs for full host control surfaces

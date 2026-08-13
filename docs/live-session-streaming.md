# Live session streaming — product and technical specification

Status: **shipped** (library stream engines, optional I/O / AHP / Hermes
packages, sample CLI follow). Contract family: `trajectory-stream-v1`.

Related:

- [Architecture](architecture.md)
- [Adapter authoring](adapter-authoring.md)
- [Normative streaming contract](../contracts/spec/streaming.md)
- [Normative normalization](../contracts/spec/normalization.md)
- [Identity](../contracts/spec/identity.md)
- [Diagnostics](../contracts/spec/diagnostics.md)
- [Listing](../contracts/spec/listing.md)
- [AHP source](ahp-source-spec.md)
- [Grok Build source](grok-build-source-spec.md)
- [Streaming core API](streaming-core-api.md)
- [File I/O](streaming-file-io.md)

---

## 1. Product intent

Trajectory remains a **library**. Consumer applications already have a process
and event loop. Streaming means those apps can **observe an active coding-agent
session as it grows** and receive deterministic, versioned trajectory updates
suitable for UI, memory, evaluation, and observability.

Trajectory does **not**:

- run a daemon or global home-directory watcher;
- own WebSocket credentials, reconnect policy, or OS session buses;
- replace one-shot normalize/list APIs;
- treat existing `ProjectToStream` / minimal JSONL writers as live input.

Trajectory **does** own:

- complete-line / complete-action framing once bytes are supplied;
- stateful stream accumulation (`StreamState` / session object);
- committed **cursors** (source position checkpoints);
- re-normalize and incremental-apply semantics;
- stable record identity, provisional → final lifecycle;
- **snapshot + delta** envelopes for every accepted revision;
- reset / compaction / sequence-gap detection rules;
- content-safe stream diagnostics;
- shared contracts and multi-runtime conformance.

### Locked product answers

| # | Decision | Lock |
| --- | --- | --- |
| 1 | Session kinds | **Both** file-backed JSONL active sessions **and** AHP active sessions (snapshot + action-log) |
| 2 | Delivery | **Both** full snapshot **and** ordered record deltas on every accepted update (callers may request a subset; the algorithm computes both) |
| 3 | Package home | Pure algorithm in **all four core packages**; optional I/O and AHP client packages; samples compose them |
| 4 | Runtimes | **All four** (.NET, TypeScript, Rust, Python) with shared contracts/conformance |
| 5 | Cursor | First-class, versioned, source-family-tagged; not deferred |

### Mental model

```text
consumer I/O / host transport
        │  complete lines, full prefix, snapshot, or action batch
        ▼
┌──────────────────────────────┐
│ StreamState + apply          │  pure library (core)
│ framing · normalize · diff   │
└──────────────┬───────────────┘
               │  StreamUpdate
               │  (snapshot + delta + cursor + revision)
               ▼
        consumer app runtime
```

---

## 2. Framing revisions (from multi-agent review)

Agent review of the initial framing produced these **locked corrections**:

1. **Cursor ≠ full state.** A cursor is the committed source position. The
   algorithm retains `StreamState` (parser buffer, open tool links, active turn,
   last emitted snapshot, revision chain). APIs expose either a stateful
   `TrajectoryStream` façade or a pure `apply(state, input) → (state, update)`.
2. **AHP Shape B reduction is core; network is not.** Active AHP requires a
   deterministic action-log reducer in every core package. WebSocket/JSON-RPC/
   auth remain optional packages or samples.
3. **One-shot `partial` is not streaming.** Existing `source_context.partial` /
   `base_byte_offset` remain segment primitives. Streaming is a new contract
   layered on top.
4. **Deltas need a public application law.** `trajectory-stream-v1` defines
   ordered operations such that applying delta to prior snapshot yields the new
   snapshot.
5. **Provisional records are first-class.** Half-lines are never provisional
   records; they are pending bytes. AHP `activeTurn` and other in-progress
   semantics are provisional with stable provisional IDs and explicit finalize.
6. **Core is pull-based and failure-atomic.** No background threads in core.
   Backpressure = consumer chooses when to call `apply`. Optional I/O may be
   async.
7. **Prefix re-normalize is the correctness oracle.** Append/incremental is a
   required fast path that must equal prefix re-normalize on every fixture.
8. **Hermes is not JSONL file-tail.** Full feature includes an **optional
   SQLite/provider** stream mode that queries rows and feeds the existing Hermes
   export shape. Core stays SQLite-free.

---

## 3. Architecture and package split

### Layers

| Layer | Responsibility | Packages |
| --- | --- | --- |
| Core stream algorithm | Framing, state, apply, cursor, snapshot, delta, reset, AHP reducer | Existing cores |
| Optional file I/O | Poll/watch path → complete segments / prefixes | New or extended IO packages |
| Optional Hermes provider | SQLite query / change token → Hermes export batches | Optional provider packages |
| Optional AHP client | Connect, auth callback, subscribe, resync → core inputs | Optional AHP packages |
| Samples | `stream` / `ahp-stream` CLI demos | Existing sample CLIs |

### Package names (locked)

| Runtime | Core (stream algorithm) | File I/O | AHP client | Hermes provider |
| --- | --- | --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | `Hypabolic.Trajectory.IO` | `Hypabolic.Trajectory.Ahp` | `Hypabolic.Trajectory.Hermes` (or provider under IO) |
| TypeScript | `@hypabolic/trajectory` | `@hypabolic/trajectory-node` (extend) or `@hypabolic/trajectory-streaming-node` | `@hypabolic/trajectory-ahp` | optional node provider |
| Rust | `hypabolic-trajectory` | `hypabolic-trajectory-io` | `hypabolic-trajectory-ahp` | optional feature/crate |
| Python | `hypabolic-trajectory` | `hypabolic-trajectory[io]` / `…-io` | `hypabolic-trajectory[ahp]` / `…-ahp` | optional provider extra |

Core packages must remain free of filesystem watchers, network stacks, and
SQLite. Capability manifests must not claim I/O capabilities for core.

### Relationship to existing pipeline

```text
                    ┌─ one-shot NormalizeRequest ──► IR ──► projections
native material ────┤
                    └─ stream apply* ──► StreamState ──► StreamUpdate
                                              │
                                              └─ (internally uses decode + normalize)
```

Existing batch schemas (`hypabolic-trajectory-v1`, canonical, etc.) stay
unchanged. Streaming introduces **wrapper** schemas; it does not force
`records.minItems: 1` on batch Hypabolic documents for empty live prefixes.

---

## 4. Stream model

### 4.1 Operations (observable parity)

Every runtime exposes the same observable operations (names may be idiomatic):

```text
create(options) → StreamState | TrajectoryStream
apply_snapshot(state, source_material, source_revision, cursor?) → StreamUpdate
apply_append(state, complete_segment_bytes, cursor) → StreamUpdate
apply_ahp_snapshot(state, shape_a_bytes, source_revision, cursor?) → StreamUpdate
apply_ahp_actions(state, action_batch, cursor) → StreamUpdate
apply_hermes_export(state, export_json, change_token, cursor?) → StreamUpdate  # when provider present
finish(state) → StreamUpdate
reset(state, request) → StreamUpdate
```

- **Snapshot apply:** caller supplies the current complete committed prefix or
  host snapshot. Core reconciles against prior state, may reset, emits update.
- **Append apply:** caller supplies only newly completed lines/actions after the
  committed cursor. Core advances state; must match snapshot-oracle for same
  history.
- **Finish:** end-of-stream; may commit a final unterminated JSONL line once;
  runs whole-mode validation policy when configured.
- **Reset:** explicit install of new generation after `reset-required` or manual
  restart.

### 4.2 Input kinds

```text
StreamInputKind =
  | append-bytes          # JSONL complete-line segment
  | snapshot-bytes        # full current file prefix or export
  | ahp-actions           # ActionEnvelope batch (typed or JSONL bytes)
  | ahp-snapshot          # Shape A { ahpProtocolVersion, chat, session? }
  | hermes-export         # array or { session, messages } from provider
```

Do **not** infer append vs snapshot from length alone; the input kind is
explicit.

### 4.3 `StreamCursor` (public, serializable)

Cursor version `1`. Tagged by position kind. Distinct from listing pagination
cursors.

```text
StreamCursor = {
  cursor_version: 1,
  source: wire source name,
  group_id: string,
  generation: uint64,              # increments on reset
  position:
    | { kind: "byte",
        next_byte_offset: int64,   # after last committed complete record
        pending_byte_length: int64 }
    | { kind: "ahp-server-seq",
        next_server_seq: int64,
        last_server_seq: int64,
        next_byte_offset?: int64 } # optional action-log byte provenance
    | { kind: "snapshot-revision",
        revision: string,
        content_sha256?: string }
    | { kind: "hermes-row",
        database_generation: string,
        last_row_id?: int64,
        change_token?: string },
  source_revision: string | null,  # file generation / host revision
  prefix_sha256: string | null     # optional committed-prefix fingerprint
}
```

Rules:

- `next_byte_offset` never advances past a half-line or incomplete UTF-8 sequence.
- Pending bytes live in **state**, not necessarily in the public cursor; cursor
  may report `pending_byte_length` for diagnostics without embedding content.
- Failed/cancelled apply leaves the prior cursor unchanged (atomicity).
- Replay of already-committed input is idempotent (`unchanged`).

### 4.4 `StreamState` (runtime-local)

Not a cross-language wire format. May include:

- last committed cursor and generation;
- pending incomplete line/UTF-8 buffer;
- last full stream snapshot (or enough to diff);
- open tool-call plan / cross-chunk link memory;
- AHP reducer chat state / active turn;
- last revision id and parent chain head;
- normalize options snapshot.

Consumers may keep a live object in memory. Portable resume across processes is
**cursor + re-apply source material from origin** (or provider snapshot), not
serialized private IR.

### 4.5 `StreamUpdate` envelope

Every accepted apply that changes visible state returns:

```text
StreamUpdate = {
  kind: "updated" | "unchanged" | "reset-required" | "error",
  revision: {
    revision: uint64,              # stream-local monotonic
    revision_id: string,           # deterministic hash
    parent_revision_id: string | null,
    complete: boolean,             # stream finished
    generation: uint64
  },
  cursor: StreamCursor,
  snapshot: StreamSnapshot | null, # always set when kind=updated (full feature default)
  delta: StreamDelta | null,       # always set when kind=updated (full feature default)
  diagnostics: StreamDiagnostic[],
  provisional: {
    include: boolean,
    provisional_ids: string[],
    finalized_ids: string[]
  },
  consumed: {
    complete_records: uint64,
    bytes: uint64,
    first_source_position?: int64,
    last_source_position?: int64
  },
  reset?: StreamReset,
  error?: { code: string, message: string }
}
```

Default delivery is **both** snapshot and delta. Options may omit one from the
serialized response for bandwidth (`delivery: "snapshot"` or `"delta"`); the
wire schema accepts snapshot-only or delta-only `kind=updated` results. Conformance
goldens use both and require **delta-apply equivalence**: applying `delta` to
prior snapshot yields new snapshot.

### 4.6 `StreamSnapshot`

```text
StreamSnapshot = {
  schema_id: "trajectory-stream-v1",
  source: string,
  group_id: string,
  revision: { ... },
  records: StreamRecord[],
  diagnostics: StreamDiagnostic[],
  complete: boolean
}

StreamRecord = {
  status: "provisional" | "stable" | "final",
  record: <canonical-identity field semantics>,
  provisional_id?: string,
  replaces_provisional_id?: string,
  finalizes_provisional_id?: string
}
```

Empty sessions are valid stream snapshots (`records: []`). Do not force batch
Hypabolic `minItems: 1` onto live empty prefixes.

**Projection layering:** the normative stream substrate is stream records with
canonical identity semantics. Existing projections (Hypabolic, OpenAI chat,
minimal JSONL, OTEL) are **optional per-update attachments** or separate
`project(snapshot)` calls — not required inside every envelope.

### 4.7 `StreamDelta`

```text
StreamDelta = {
  schema_id: "trajectory-stream-v1",
  base_revision_id: string | null,
  revision: { ... },
  operations: StreamDeltaOperation[]
}

StreamDeltaOperation =
  | { op: "upsert", record: StreamRecord }
  | { op: "remove", record_id: string, reason: "retracted" | "reset" | "source-rewrite" }
  | { op: "finalize", provisional_id: string, record: StreamRecord }
  | { op: "state_change", record_id: string, status: "provisional" | "stable" | "final" }
  | { op: "diagnostic_add", diagnostic: StreamDiagnostic }
  | { op: "diagnostic_remove", diagnostic_key: string }
  | { op: "reset", reset: StreamReset }
```

**Match key:** if `StreamRecord.provisional_id` is set, upsert/remove/state_change
key that; else `record.record.id`. **Diagnostic key:** recomputed as
`code|input_line|record_index` with `-` sentinels for omitted fields; `count` and
`message` are not part of the key (see normative [streaming.md](../contracts/spec/streaming.md)
§7). Ordering: snapshot order, then operation kind, then stable id. `reset`
invalidates prior generation record ids.

### 4.8 Reset

Response metadata on `reset-required` / delta `op: "reset"`:

```text
StreamReset = {
  reason: "source-truncated" | "source-replaced" | "source-compacted" |
          "cursor-mismatch" | "group-changed" | "sequence-gap" |
          "prefix-hash-mismatch" | "manual",
  prior_cursor: StreamCursor | null,
  requires_snapshot: boolean,
  dropped_record_ids: string[]
}
```

Caller payload for `reset(state, request)` (distinct from `StreamReset`):

```text
StreamResetRequest = {
  reason: <same reason enum>,
  generation?: uint64,
  source_revision?: string | null,
  prior_cursor?: StreamCursor | null,
  material?: string,       # case vectors: path
  inline_utf8?: string,    # case vectors: synthetic prefix
  change_token?: string
}
```

Normative detail: [contracts/spec/streaming.md](../contracts/spec/streaming.md)
§8. Default policy: return `kind = "reset-required"` **without** advancing the
cursor. Caller supplies a full snapshot and calls `reset` / snapshot apply with
a new generation. Optional `resetPolicy = "auto-reset"` only when the caller
opts in and supplies replacement material.

### 4.9 Record lifecycle (finality)

| Status | Meaning |
| --- | --- |
| `provisional` | May be replaced or removed; AHP active turn, open tool args, synthetic backend tool result awaiting real result |
| `stable` | Content and id currently deterministic for this generation; session may still grow |
| `final` | Terminal for this record: source terminal signal, or `finish()` policy closed the stream |

- Incomplete JSONL lines are **not** records.
- `partial` normalize mode ≠ provisional status.
- Grok synthetic backend tool results are provisional until a later real
  `tool_result` or `finish`.
- AHP: `activeTurn` provisional until turn terminal action / snapshot without
  that active turn / explicit finish.
- Sources without native terminal signals: records become `stable` at commit;
  `final` only on `finish()` if options say `finalize_on_close = true`
  (default **true** for file streams, **false** for pure append while open —
  locked: **`finalize_on_close` default true** so consumers get a closed
  checkpoint; while open, completed JSONL records are `stable`).

### 4.10 Framing rules (JSONL)

- Ordinary apply commits only LF-terminated lines (CRLF → strip CR).
- Split UTF-8 code points remain in the pending buffer; no U+FFFD, no diagnostic.
- Malformed **complete** lines → diagnostic (`invalid_json_line`), do not advance
  past them as valid records (consistent with existing decoders).
- `finish` may accept one final non-empty unterminated line once.
- Blank / whitespace-only lines ignored as today.
- Configurable `max_line_bytes` / `max_pending_bytes`; overflow → typed error,
  cursor unchanged.

### 4.11 Options

```text
StreamOptions = {
  source: TrajectorySource,
  group_id?: string,
  delivery: "both" | "snapshot" | "delta",   # default "both"
  include_provisional: boolean,              # default true
  require_complete_lines: boolean,           # default true
  finalize_on_close: boolean,                # default true
  reorder: "reject",                         # core default; no silent reorder
  reset_policy: "return-reset-required" | "auto-reset",
  max_pending_bytes?: int64,
  max_line_bytes?: int64,
  normalize: NormalizeOptions,               # bounds, filters, …
  ahp_protocol_version?: string
}
```

---

## 5. Source matrix (full feature)

| Source | Stream modes | Cursor family | Reset triggers | Provisional signals | Core vs optional |
| --- | --- | --- | --- | --- | --- |
| `pi` | snapshot-bytes, append-bytes | byte | truncate, replace, generation change | open tool links (stream layer) | core |
| `claude-code` | snapshot-bytes, append-bytes | byte | truncate, replace | same; transport/sidechain filtered | core |
| `codex` | snapshot-bytes, append-bytes | byte | truncate, replace (despite append-only contract) | open function calls | core |
| `openclaw` | snapshot-bytes, append-bytes | byte | truncate, replace | delivery-mirror not provisional | core |
| `grok-build` | snapshot-bytes, append-bytes | byte | **compaction**, truncate, replace | synthetic backend tool result | core |
| `cursor` | snapshot-bytes, append-bytes | byte | truncate, replace | none / no provisional records | core |
| `ahp` | ahp-snapshot, ahp-actions | snapshot-revision / ahp-server-seq | revision conflict, sequence gap, protocol change | `activeTurn`, incomplete tools | core reducer; optional client |
| `hermes` | hermes-export via provider | hermes-row | soft-delete, db generation, change-token invalidation | none native | **optional provider**; core decode only |

### Explicit non-goals (locked)

- Reconstruct Grok turns from `updates.jsonl` or `events.jsonl`.
- Byte-tail Hermes `state.db`.
- Put AHP WebSocket client or credentials in core.
- Treat listing mtime as an event cursor.
- Silent merge of conflicting `group_id`.
- Cloud multi-tenant ingestion product.
- Stable serialized private IR across languages.
- Token-level model streaming unless a source actually persists it (most JSONL
  harnesses flush per record, not per token).

### Grok Build

Authoritative transcript is `chat_history.jsonl` only. Append-until-compaction;
post-compaction file is truth → full reset + snapshot. `summary.json` may drive
listing/change **hints** only.

### AHP

- **Shape A snapshots:** already decode in core; streaming applies successive
  snapshots with provisional `activeTurn`.
- **Shape B action log:** implement reducer in core (pinned protocol); cursor
  authority is `serverSeq`; gaps require resync snapshot, not silent skip.
- **Transport:** optional packages only.

### Hermes

Full feature **includes** optional provider packages that:

1. list sessions (when SQLite available);
2. query ordered active rows in a read transaction;
3. emit initial `hermes-export` snapshot then deltas only when change-token
   proves no prior-row mutation; else reset + full export.

Without the provider, runtimes report capability unsupported — not a fake
file-tail.

---

## 6. Public API sketches (idiomatic, same observables)

### .NET (`Hypabolic.Trajectory`)

```csharp
public sealed class TrajectoryStreamSession
{
    public StreamUpdate ApplyAppend(ReadOnlyMemory<byte> segment, StreamCursor cursor);
    public StreamUpdate ApplySnapshot(ReadOnlyMemory<byte> prefix, string sourceRevision, StreamCursor? cursor = null);
    public StreamUpdate ApplyAhpSnapshot(ReadOnlyMemory<byte> snapshot, string sourceRevision, StreamCursor? cursor = null);
    public StreamUpdate ApplyAhpActions(ReadOnlyMemory<byte> actionsJsonl, StreamCursor cursor);
    public StreamUpdate Finish();
    public StreamUpdate Reset(StreamResetRequest request);
    public StreamCursor Cursor { get; }
}

public static class TrajectoryStream
{
    public static TrajectoryStreamSession Create(StreamOptions options);
    public static (StreamState State, StreamUpdate Update) Apply(StreamState state, StreamInput input);
}
```

Optional `Hypabolic.Trajectory.IO`: path follow → `IAsyncEnumerable<StreamUpdate>`
with `CancellationToken`.

### TypeScript (`@hypabolic/trajectory`)

```ts
export function createStream(options: StreamOptions): TrajectoryStream;
export function applyStream(state: StreamState, input: StreamInput):
  { state: StreamState; update: StreamUpdate };

export class TrajectoryStream {
  apply(input: StreamInput): StreamUpdate;
  finish(): StreamUpdate;
  reset(request: StreamResetRequest): StreamUpdate;
  readonly cursor: StreamCursor;
}
```

Bytes: `Uint8Array`. Offsets/sequences: `bigint` where needed for int64 safety.
Node helpers live outside the pure package.

### Rust (`hypabolic-trajectory`)

```rust
pub fn apply_stream(state: StreamState, input: StreamInput<'_>)
    -> Result<(StreamState, StreamUpdate), TrajectoryError>;

pub struct TrajectoryStream { /* mutable façade */ }
```

Optional `hypabolic-trajectory-io` with explicit std/Tokio features.

### Python (`hypabolic_trajectory`)

```python
class TrajectoryStream:
    def apply_append(self, data: bytes, *, cursor: StreamCursor) -> StreamUpdate: ...
    def apply_snapshot(self, data: bytes, *, source_revision: str, cursor: StreamCursor | None = None) -> StreamUpdate: ...
    def apply_ahp_snapshot(self, data: bytes, *, source_revision: str, cursor: StreamCursor | None = None) -> StreamUpdate: ...
    def apply_ahp_actions(self, data: bytes, *, cursor: StreamCursor) -> StreamUpdate: ...
    def finish(self) -> StreamUpdate: ...
    def reset(self, request: StreamResetRequest) -> StreamUpdate: ...

def apply_stream(state: StreamState, input: StreamInput) -> tuple[StreamState, StreamUpdate]: ...
```

Stream inputs are **`bytes` only** (no str offsets). Optional `[io]` / `[ahp]` extras.

---

## 7. Capabilities

### Core (required when feature is advertised)

| Capability | Meaning |
| --- | --- |
| `stream-core` | Stream state machine present |
| `stream-cursor-v1` | Cursor schema v1 |
| `stream-jsonl-framing` | Complete-line / UTF-8 pending rules |
| `stream-apply-snapshot` | Full prefix/snapshot apply |
| `stream-apply-append` | Append segment apply (= oracle) |
| `stream-full-snapshot` | Snapshot in update |
| `stream-record-delta` | Delta ops in update |
| `stream-reset` | Reset-required / reset ops |
| `stream-provisional-records` | Provisional lifecycle |
| `stream-deterministic-replay` | Idempotent replay |
| `stream-file-jsonl` | Pi, Claude Code, Codex, OpenClaw, Grok Build, Cursor Agent |
| `stream-ahp-snapshot` | Shape A successive snapshots |
| `stream-ahp-action-log` | Shape B reducer + apply |

### Optional packages

| Capability | Package family |
| --- | --- |
| `stream-file-io` | poll path |
| `stream-file-watch` | OS watcher wake-up |
| `stream-ahp-client` | network client |
| `stream-ahp-list-sessions` | host listSessions |
| `stream-hermes-provider` | SQLite/query provider |
| `stream-async-iterator` | async iteration helpers |

Do not mark streaming capabilities implemented in manifests until the full
matrix for that capability is green on **all four** runtimes (or the optional
package is explicitly per-ecosystem for I/O only).

---

## 8. Conformance

### Protocol

Keep protocol v1 for one-shot normalize/list. Add stream operations (protocol
extension or v2 alongside):

- `stream-apply-append`
- `stream-apply-snapshot`
- `stream-apply-ahp-actions`
- `stream-apply-ahp-snapshot`
- `stream-finish`
- `stream-reset`
- `stream-sequence` (synonym: `stream-replay`)

Cases are **ordered sequences** of inputs with expected update arrays.

### Comparison modes

- `stream-json-exact` — ordered updates
- `stream-cursor-exact`
- `stream-delta-apply` — delta reconstructs snapshot
- `stream-diagnostics-by-step`
- `stream-idempotence` — double-apply
- `stream-oracle-parity` — append path vs full snapshot path

Verifier rules:

- invoke each step twice for determinism;
- final snapshot equals fresh run over full committed source;
- privacy scan on goldens.

### Fixture corpus (minimum names)

Generic: `empty-prefix`, `append-one-line`, `unterminated-line-held`,
`duplicate-input-idempotent`, `utf8-byte-boundary`, `cross-chunk-tool-result`,
`provisional-to-stable`, `stable-to-final`, `record-replacement`,
`record-removal`, `diagnostic-add-remove`, `file-truncate-reset`,
`file-compaction-reset`, `cursor-conflict`, `source-group-conflict`,
`delta-ordering`, `snapshot-delta-equivalence`, `append-equals-prefix-oracle`.

Per-source append sequences: `pi-*`, `claude-code-*`, `codex-*`, `openclaw-*`,
`grok-build-*` (including compaction and synthetic tool replacement).

AHP: `ahp-snapshot-*`, `ahp-action-*` (including sequence gap, foreign channel,
unknown action, equals-snapshot).

Hermes provider feed (core `apply_hermes_export`): shared
`hermes-provider-append`, `hermes-provider-soft-delete`,
`hermes-provider-invalidation` under `conformance/cases/streaming/`. Optional
SQLite/query I/O (`stream-hermes-provider`) remains **package-test-gated** —
see [`streaming-hermes-provider.md`](streaming-hermes-provider.md).

---

## 9. Privacy and security

Streaming extends [diagnostics](../contracts/spec/diagnostics.md) content-safety:

- No transcript prose, raw JSON lines, tool payloads, AHP action bodies,
  tokens, cookies, WebSocket URLs with secrets, or filesystem paths in
  diagnostics or stream error messages.
- Paths may exist in **consumer I/O state** only.
- Group/native IDs appear in normalized record payloads as today; diagnostics
  must not echo them.
- Operational outcomes (`stream_source_reset`, `stream_cursor_conflict`,
  `stream_sequence_gap`, `stream_resync_required`, `stream_buffer_limit`) are
  typed results, not fake transcript malformation codes.
- Optional AHP clients take auth via injected callbacks; never load
  `auth.json` into core state.
- Fixtures remain synthetic; no production paths or real transcripts.
- Sample CLIs default to content-hidden summaries; `--show-content` opt-in with
  warning (same spirit as current browse CLIs).

---

## 10. Optional I/O behavior (non-core)

File helpers:

1. Open path with shared read; track size + optional file identity.
2. Read only through last complete newline; retain pending tail.
3. On growth: prefer append segment to core; periodic full-prefix reconciliation.
4. On shrink / generation change / prefix hash fail: signal reset; re-read full file.
5. Watcher events are **wake-ups only**; size + cursor remain authoritative.
6. Never default-watch `~` stores without an explicit root from the caller.

AHP clients:

1. Connect with app-supplied auth.
2. Subscribe / poll; on gap request snapshot resync.
3. Feed core `apply_ahp_*` only.
4. Cancellation leaves last committed cursor valid.

---

## 11. Sample CLI shape (documentation contract)

```text
trajectory browse --source <name> [--watch] [--show-content]
trajectory stream --source <name> --path <file> --emit snapshot+delta [--follow]
trajectory stream --source <name> --id <listing-id> --root <store> --follow
trajectory ahp-stream --url <ws> --chat <chat-uri> [--from-seq N]
```

Privacy defaults and explicit roots apply. Samples are not the wire contract.

---

## 12. Relationship to current AHP deferrals

This specification **schedules** work previously listed as deferred:

| Prior deferral | This feature |
| --- | --- |
| Shape B action-log reduce | Core stream slices |
| Live host / WebSocket | Optional client packages |
| Action-log partial fixtures | Streaming conformance |
| Export-directory listing | Still listing contract; may ship with AHP client |

Export-directory listing and a first-class live WebSocket host remain
caller-owned (optional AHP client packages take an injected transport).

---

## 13. Out of scope for this feature

- Changing normalizer contract `0.2.0` identity formulas without a version bump
- Shared FFI core
- Browser/Wasm first-class streaming host
- Treating Minimal JSONL output writers as the stream protocol
- Automatic home-directory multi-session supervisor in core

---

## 14. Acceptance definition of done

The feature is complete when:

1. Contracts and schemas for `trajectory-stream-v1` are authoritative.
2. All four cores implement snapshot + append + AHP snapshot + AHP action-log
   with shared goldens green.
3. Append path equals prefix oracle on the full corpus.
4. Optional file I/O packages exist for all four ecosystems.
5. Optional AHP client packages exist for all four ecosystems (fake-host tested).
6. Optional Hermes provider path is implemented or explicitly capability-gated
   with shared “unsupported” behavior.
7. Sample CLIs demonstrate file follow and AHP stream.
8. Capability manifests honestly match the matrix.
9. Privacy gates pass; no secrets in diagnostics/fixtures.
10. Batch normalize/list conformance remains unchanged and green.

See [streaming-core-api.md](streaming-core-api.md) for the apply surface and
[streaming-file-io.md](streaming-file-io.md) for optional path follow.

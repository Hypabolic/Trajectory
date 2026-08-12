# Live session streaming contract

Contract family: `trajectory-stream-v1`  
Cursor version: `1`  
Status: **normative wire contract** (implementations land in later LS slices)

Related product design: [docs/live-session-streaming.md](../../docs/live-session-streaming.md)  
Schemas:

- [`trajectory-stream-v1.schema.json`](../schemas/trajectory-stream-v1.schema.json) — `StreamUpdate` / `StreamSnapshot`
- [`streaming-cursor-v1.schema.json`](../schemas/streaming-cursor-v1.schema.json) — `StreamCursor`
- [`streaming-delta-v1.schema.json`](../schemas/streaming-delta-v1.schema.json) — `StreamDelta`
- [`streaming-case-v1.schema.json`](../schemas/streaming-case-v1.schema.json) — multi-step conformance cases

Batch contracts remain authoritative for one-shot normalize/list:

- [normalization.md](normalization.md)
- [identity.md](identity.md)
- [diagnostics.md](diagnostics.md)
- [listing.md](listing.md)

Streaming is a **library** surface. Core implementations must not open
filesystem watchers, network sockets, or SQLite databases. Consumers own I/O
and scheduling; core owns framing, state, apply, cursor, snapshot, delta,
reset, and the AHP Shape B action-log reducer.

---

## 1. Mental model

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

- **`StreamCursor`** is the committed source position checkpoint. It is public,
  serializable, and versioned.
- **`StreamState`** is the algorithm’s full local state (pending buffers, open
  tool links, last snapshot, AHP reducer memory, revision head). It is
  **runtime-local**, not a cross-language wire format.
- Cursor alone is **insufficient** to resume: portable resume is
  `cursor + re-apply source material from origin` (or a host snapshot), not
  serialized private IR.
- Existing one-shot `source_context.partial` / `base_byte_offset` remain
  **segment** primitives. Streaming is a separate contract layered on top.

---

## 2. Operations (observable parity)

Every runtime exposes the same observables (names may be idiomatic):

```text
create(options) → StreamState | TrajectoryStream
apply_snapshot(state, source_material, source_revision, cursor?) → StreamUpdate
apply_append(state, complete_segment_bytes, cursor) → StreamUpdate
apply_ahp_snapshot(state, shape_a_bytes, source_revision, cursor?) → StreamUpdate
apply_ahp_actions(state, action_batch, cursor) → StreamUpdate
apply_hermes_export(state, export_json, change_token, cursor?) → StreamUpdate
finish(state) → StreamUpdate
reset(state, request) → StreamUpdate
```

| Operation | Meaning |
| --- | --- |
| Snapshot apply | Caller supplies the current complete committed prefix or host snapshot. Core reconciles, may signal reset, emits update. |
| Append apply | Caller supplies only newly completed lines/actions after the committed cursor. Must match the prefix-oracle path for the same history. |
| AHP snapshot | Successive Shape A `{ ahpProtocolVersion, chat, session? }` documents. |
| AHP actions | Shape B action-log batch; reducer is **core**. |
| Hermes export | When an optional provider is present; core stays SQLite-free. |
| Finish | End-of-stream; may commit one final unterminated JSONL line once; applies `finalize_on_close`. |
| Reset | Explicit install of a new generation after `reset-required` or manual restart. |

Failed or cancelled apply is **atomic**: the prior cursor and visible snapshot
are unchanged. Replay of already-committed input is idempotent
(`kind = "unchanged"`).

### Input kinds

```text
StreamInputKind =
  | append-bytes          # JSONL complete-line segment
  | snapshot-bytes        # full current file prefix or export
  | ahp-actions           # ActionEnvelope batch (typed or JSONL bytes)
  | ahp-snapshot          # Shape A { ahpProtocolVersion, chat, session? }
  | hermes-export         # array or { session, messages } from provider
```

Do **not** infer append vs snapshot from length alone; the input kind is
explicit on the call or request.

---

## 3. `StreamCursor` (public, serializable)

Cursor version `1`. Distinct from listing pagination cursors.

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
        next_byte_offset?: int64 }
    | { kind: "snapshot-revision",
        revision: string,
        content_sha256?: string }
    | { kind: "hermes-row",
        database_generation: string,
        last_row_id?: int64,
        change_token?: string },
  source_revision: string | null,
  prefix_sha256: string | null
}
```

Rules:

1. `next_byte_offset` never advances past a half-line or incomplete UTF-8
   sequence.
2. Pending bytes live in **state**. The public cursor may report
   `pending_byte_length` for diagnostics without embedding content.
3. Failed/cancelled apply leaves the prior cursor unchanged.
4. Replay of already-committed input is idempotent (`unchanged`).
5. Offsets and sequences are lossless signed 64-bit integers on the wire
   (JSON numbers that fit `int64`). Generations and stream-local revisions are
   non-negative integers (uint64 domain; JSON number when lossless).

Wire schema: [`streaming-cursor-v1.schema.json`](../schemas/streaming-cursor-v1.schema.json).

---

## 4. `StreamState` (runtime-local)

Not a cross-language wire format. Implementations may retain:

- last committed cursor and generation;
- pending incomplete line / UTF-8 buffer;
- last full stream snapshot (or enough to diff);
- open tool-call plan / cross-chunk link memory;
- AHP reducer chat state / active turn;
- last revision id and parent chain head;
- normalize options snapshot.

Consumers may keep a live object in memory. Portable resume across processes is
**cursor + re-apply source material**, not serialized private IR.

---

## 5. `StreamUpdate` envelope

Every apply returns a `StreamUpdate`:

```text
StreamUpdate = {
  kind: "updated" | "unchanged" | "reset-required" | "error",
  revision: StreamRevision,
  cursor: StreamCursor,
  snapshot: StreamSnapshot | null,   # non-null for delivery=both|snapshot when kind=updated
  delta: StreamDelta | null,         # non-null for delivery=both|delta when kind=updated
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

StreamRevision = {
  revision: uint64,              # stream-local monotonic within generation
  revision_id: string,           # deterministic hash / stable id
  parent_revision_id: string | null,
  complete: boolean,             # stream finished
  generation: uint64
}
```

### Delivery defaults

| Option | Default | Meaning |
| --- | --- | --- |
| `delivery` | `"both"` | Compute and include snapshot **and** delta on every `updated` result |
| `include_provisional` | `true` | Provisional records appear in snapshot/delta |
| `require_complete_lines` | `true` | Ordinary apply commits only LF-terminated lines |
| `finalize_on_close` | `true` | `finish()` marks stable records `final` |
| `reorder` | `"reject"` | Core does not silently reorder out-of-order input |
| `reset_policy` | `"return-reset-required"` | Do not auto-reset without caller opt-in |

Callers may request `delivery: "snapshot"` or `"delta"` for bandwidth; the
algorithm still computes both when needed for correctness. On the wire,
`kind=updated` must include at least one of `snapshot` or `delta` non-null:

| delivery | snapshot | delta |
| --- | --- | --- |
| `"both"` (default) | non-null | non-null |
| `"snapshot"` | non-null | `null` |
| `"delta"` | `null` | non-null |

Conformance goldens use **both** and require **delta-apply equivalence**.

Wire schema: [`trajectory-stream-v1.schema.json`](../schemas/trajectory-stream-v1.schema.json).

---

## 6. `StreamSnapshot`

```text
StreamSnapshot = {
  schema_id: "trajectory-stream-v1",
  source: string,
  group_id: string,
  revision: StreamRevision,
  records: StreamRecord[],
  diagnostics: StreamDiagnostic[],
  complete: boolean
}

StreamRecord = {
  status: "provisional" | "stable" | "final",
  record: StreamRecordBody,   # canonical-identity field semantics
  provisional_id?: string,
  replaces_provisional_id?: string,
  finalizes_provisional_id?: string
}
```

Empty sessions are valid (`records: []`). Do **not** apply batch Hypabolic
`records.minItems: 1` to live empty prefixes.

**Projection layering:** the normative stream substrate is stream records with
canonical identity semantics (`id`, kind, role, order, timestamps, provenance,
hashes, content/tool fields as applicable). Existing projections (Hypabolic,
OpenAI chat, minimal JSONL, OTEL) are optional per-update attachments or
separate `project(snapshot)` calls — not required inside every envelope.

### Record lifecycle (finality)

| Status | Meaning |
| --- | --- |
| `provisional` | May be replaced or removed (AHP active turn, open tool args, synthetic backend tool result awaiting real result) |
| `stable` | Content and id deterministic for this generation; session may still grow |
| `final` | Terminal for this record: source terminal signal, or `finish()` with `finalize_on_close` |

Rules:

- Incomplete JSONL lines are **not** records; they are pending bytes in state.
- Batch `partial` normalize mode is not the same as provisional status.
- Grok synthetic backend tool results are provisional until a later real
  `tool_result` or `finish`.
- AHP `activeTurn` is provisional until a turn-terminal action, a snapshot
  without that active turn, or explicit finish.
- While a file stream is open, completed JSONL records are `stable`. They become
  `final` on `finish()` when `finalize_on_close` is true (default **true**).

---

## 7. `StreamDelta` and the delta-apply law

```text
StreamDelta = {
  schema_id: "trajectory-stream-v1",
  base_revision_id: string | null,
  revision: StreamRevision,
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

Wire schema: [`streaming-delta-v1.schema.json`](../schemas/streaming-delta-v1.schema.json).

### Delta-apply law (normative)

Given prior snapshot `S0` and an `updated` result with snapshot `S1` and
delta `D` where `D.base_revision_id` equals `S0.revision.revision_id` (or both
null for the first revision):

1. Start from a deep copy of `S0` (records and diagnostics).
2. Apply each operation in `D.operations` **in array order**:
   - `upsert` — insert or replace by durable `record.record.id` (or by
     `provisional_id` when the body id is not yet durable and the op carries
     provisional linkage). Resulting status comes from the op’s `StreamRecord`.
   - `remove` — drop the record with matching `record_id` (or matching
     provisional id when only provisional).
   - `finalize` — remove any record keyed by `provisional_id`; upsert `record`
     as the terminal body (status typically `stable` or `final`).
   - `state_change` — set `status` on the matching record; identity unchanged.
   - `diagnostic_add` — append diagnostic; de-dupe by stable diagnostic key
     (`code` + structural fields `input_line` / `record_index` when present).
   - `diagnostic_remove` — remove diagnostics matching `diagnostic_key`.
   - `reset` — clear records and diagnostics for the prior generation; install
     `reset` metadata; subsequent ops (if any) belong to the new generation.
3. Set `revision` and `complete` from `D.revision`.
4. The resulting snapshot **must equal** `S1` under conformance comparison
   (`stream-delta-apply` / structural equality of records, diagnostics,
   revision, source, group_id, complete).

### Operation ordering for producers

When emitting a delta for a non-reset update, producers SHOULD order
operations as:

1. removals (stable id order);
2. finalizations (provisional_id order);
3. upserts (snapshot record order);
4. state changes (stable id order);
5. diagnostic removes then diagnostic adds (key order).

A `reset` op, when present, appears first and invalidates prior-generation
record ids. Conformance may also accept any order that still satisfies the
delta-apply law against the golden snapshot.

---

## 8. Reset

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

Default policy (`reset_policy = "return-reset-required"`):

1. Return `kind = "reset-required"` **without** advancing the cursor.
2. Include `reset` describing why.
3. Caller supplies a full snapshot and calls `reset` / snapshot apply with a
   new `generation`.

Optional `reset_policy = "auto-reset"` only when the caller opts in **and**
supplies replacement material in the same call.

Silent merge of conflicting `group_id` is forbidden (`group-changed` /
fatal `source_group_conflict` per batch rules).

---

## 9. Framing rules (JSONL file sources)

1. Ordinary apply commits only LF-terminated lines (CRLF → strip trailing CR).
2. Split UTF-8 code points remain in the pending buffer; no U+FFFD substitution
   and no diagnostic for incomplete sequences alone.
3. Malformed **complete** lines → diagnostic (`invalid_json_line`); do not
   advance past them as valid records (consistent with existing decoders).
4. `finish` may accept one final non-empty unterminated line once.
5. Blank / whitespace-only lines are ignored as in batch decode.
6. Configurable `max_line_bytes` / `max_pending_bytes`; overflow → typed error
   (`stream_buffer_limit` / fatal stream error), cursor unchanged.
7. Never consume past the last complete line on ordinary apply.

### Correctness oracle

**Prefix re-normalize is the correctness oracle.** Append/incremental paths are
required for performance but must equal full snapshot re-normalize of the
committed prefix on every shared fixture (`stream-oracle-parity`).

---

## 10. Options

```text
StreamOptions = {
  source: TrajectorySource,
  group_id?: string,
  delivery: "both" | "snapshot" | "delta",   # default "both"
  include_provisional: boolean,              # default true
  require_complete_lines: boolean,           # default true
  finalize_on_close: boolean,                # default true
  reorder: "reject",                         # core default
  reset_policy: "return-reset-required" | "auto-reset",
  max_pending_bytes?: int64,
  max_line_bytes?: int64,
  normalize: NormalizeOptions,               # bounds, filters, …
  ahp_protocol_version?: string
}
```

`normalize` inherits batch [normalization.md](normalization.md) bounds and
filters. Streaming does not change normalizer contract `0.2.0` identity
formulas.

---

## 11. Source matrix

| Source | Stream modes | Cursor family | Reset triggers | Provisional signals | Core vs optional |
| --- | --- | --- | --- | --- | --- |
| `pi` | snapshot-bytes, append-bytes | byte | truncate, replace, generation change | open tool links (stream layer) | core |
| `claude-code` | snapshot-bytes, append-bytes | byte | truncate, replace | same; transport/sidechain filtered | core |
| `codex` | snapshot-bytes, append-bytes | byte | truncate, replace | open function calls | core |
| `openclaw` | snapshot-bytes, append-bytes | byte | truncate, replace | delivery-mirror not provisional | core |
| `grok-build` | snapshot-bytes, append-bytes | byte | **compaction**, truncate, replace | synthetic backend tool result | core |
| `ahp` | ahp-snapshot, ahp-actions | snapshot-revision / ahp-server-seq | revision conflict, sequence gap, protocol change | `activeTurn`, incomplete tools | core reducer; optional client |
| `hermes` | hermes-export via provider | hermes-row | soft-delete, db generation, change-token invalidation | none native | **optional provider**; core decode only |

### Locked non-goals

- Reconstruct Grok turns from `updates.jsonl` or `events.jsonl`.
- Byte-tail Hermes `state.db` in core.
- AHP WebSocket client or credentials in core.
- Treat listing mtime as an event cursor.
- Silent merge of conflicting `group_id`.
- Cloud multi-tenant ingestion product.
- Stable serialized private IR across languages.
- Token-level model streaming unless a source persists it.

---

## 12. Diagnostics and privacy

Streaming extends [diagnostics.md](diagnostics.md) content-safety.

### Stream operational outcomes (typed)

These are stream-layer outcomes, not fake transcript malformation codes:

| Code | Meaning |
| --- | --- |
| `stream_source_reset` | Source truncated, replaced, or compacted |
| `stream_cursor_conflict` | Supplied cursor does not match state |
| `stream_sequence_gap` | AHP serverSeq gap; resync required |
| `stream_resync_required` | Host/provider requires full snapshot |
| `stream_buffer_limit` | Pending or line byte limit exceeded |

Decode/normalize codes from batch diagnostics remain valid when emitted during
stream apply (`invalid_json_line`, tool-link cleanup, etc.).

### Forbidden in diagnostics and stream error messages

- transcript prose, reasoning, prompts, tool arguments, or tool results;
- raw source JSON lines or parser excerpts;
- tokens, cookies, WebSocket URLs with secrets;
- developer or user filesystem paths;
- source-native identifiers or group identifiers in diagnostic text (group/native
  IDs may appear in normalized **record** payloads as today).

Paths may exist only in **consumer I/O state**. Fixtures are synthetic.
Stream diagnostic objects use `additionalProperties: false` and only structural
optional fields (`input_line`, `record_index`, `count`) — never `path`,
`raw_line`, or payload fields.

---

## 13. Capabilities (vocabulary)

Known stream capability names (for manifests and case requirements):

### Core (required when the full stream feature is advertised)

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
| `stream-file-jsonl` | Pi, Claude Code, Codex, OpenClaw, Grok Build |
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

**Do not** mark `stream-*` capabilities implemented in runtime
`runtime-capabilities.json` or claim them in `compatibility.json` until the
matrix for that capability is green on all four runtimes (or the optional
package is explicitly per-ecosystem for I/O only). The compatibility manifest
schema **allows** these names so later slices can advertise them without a
schema break.

---

## 14. Conformance (case shape)

Stream conformance cases are ordered sequences of steps. Wire shape:
[`streaming-case-v1.schema.json`](../schemas/streaming-case-v1.schema.json).

Comparison modes (protocol detail lands in LS-02):

| Mode | Checks |
| --- | --- |
| `stream-json-exact` | Ordered updates |
| `stream-cursor-exact` | Cursor fields |
| `stream-delta-apply` | Delta reconstructs snapshot |
| `stream-diagnostics-by-step` | Per-step diagnostics |
| `stream-idempotence` | Double-apply of each step |
| `stream-oracle-parity` | Append path vs full snapshot path |

Verifier expectations (when implemented):

- invoke each step twice for determinism;
- final snapshot equals a fresh run over the full committed source;
- privacy scan on goldens and diagnostics.

Minimum case ids are listed in the product plan; goldens fill in as
implementations land (LS-04+).

---

## 15. Package split (contract reminder)

| Layer | Responsibility | Core? |
| --- | --- | --- |
| Stream algorithm | Framing, state, apply, cursor, snapshot, delta, reset, AHP reducer | Yes — all four cores |
| File I/O | Poll/watch path → segments / prefixes | Optional packages |
| AHP client | Connect, auth callback, subscribe, resync | Optional packages |
| Hermes provider | SQLite query / change token → export batches | Optional packages |
| Samples | CLI demos | Samples only |

AHP Shape B reduction is **core**. WebSocket/JSON-RPC/auth are never core.

---

## 16. Versioning

- Contract family id: `trajectory-stream-v1` (`schema_id` on snapshot and delta).
- Cursor `cursor_version: 1`.
- Breaking changes require a new family / cursor version and updated
  conformance.
- Additive diagnostic codes and optional fields are allowed without a version
  bump when documented here.

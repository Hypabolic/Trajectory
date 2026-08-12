# Streaming core API (LS-03 / LS-04 / LS-05 / LS-06 / LS-07)

Pure library surface for live session streaming. Callers own I/O and scheduling;
core owns framing, state, snapshot + append apply, AHP snapshot/action-log
apply, stable-id diff, and cursors.

Normative contracts: [`contracts/spec/streaming.md`](../contracts/spec/streaming.md),
product design: [`live-session-streaming.md`](live-session-streaming.md).

## Package locations

| Runtime | Module |
| --- | --- |
| .NET | `Hypabolic.Trajectory.Streaming` (`dotnet/src/Trajectory/Streaming/`) |
| TypeScript | `@hypabolic/trajectory` → `streaming` exports |
| Rust | `hypabolic_trajectory::streaming` |
| Python | `hypabolic_trajectory.streaming` (also re-exported from package root) |

Core packages **must not** open filesystem watchers, network sockets, or SQLite.
Do **not** advertise `stream-*` capabilities in runtime manifests until LS-12.

## Mental model

```text
create(options) → StreamState | TrajectoryStream
apply_snapshot(state, material, source_revision, cursor?) → (state, StreamUpdate)
apply_append(state, segment, cursor?, source_revision?) → (state, StreamUpdate)
apply_ahp_snapshot(state, shape_a_bytes, source_revision, cursor?) → (state, StreamUpdate)
apply_ahp_actions(state, action_batch, cursor?) → (state, StreamUpdate)
```

- **`StreamCursor`** — public, serializable committed position (version 1).
- **`StreamState`** — runtime-local algorithm state (pending buffer, last snapshot,
  generation, locked group). Not a cross-language wire format.
- Portable resume is **cursor + re-apply source material**, not serialized IR.

## Create

Options (defaults):

| Field | Default |
| --- | --- |
| `source` | required |
| `group_id` | optional; `"default"` until material resolves |
| `delivery` | `"both"` (snapshot + delta) |
| `include_provisional` | `true` |
| `require_complete_lines` | `true` |
| `finalize_on_close` | `true` |
| `normalize` | batch normalize defaults |
| `ahp_protocol_version` | optional; Shape B → Shape A pin (default `"0.7.0"`) |

## `apply_snapshot`

1. Frame material: only LF-terminated lines are committed; trailing incomplete
   bytes become pending (`pending_byte_length` on the byte cursor).
2. Empty committed prefix is valid → `records: []` (no batch `minItems: 1`).
3. Non-empty prefix is fully re-normalized with **`partial=true`** through existing
   source adapters + normalizer.
4. Stream records use hypabolic identity field semantics + `status: "stable"`.
5. Stable-id diff against the prior snapshot produces a `StreamDelta`.
6. Default delivery includes **both** snapshot and delta.
7. Atomic failures leave the prior cursor/state unchanged.

### Outcomes

| `kind` | When |
| --- | --- |
| `updated` | Visible snapshot changed (or first revision) |
| `unchanged` | Same `source_revision` + prefix fingerprint (+ pending) |
| `reset-required` | Truncation, group conflict, cursor mismatch (cursor unchanged) |
| `error` | Typed failure (e.g. buffer limit); cursor unchanged |

### Reset reasons (LS-04 / LS-05)

- `source-truncated` — shorter material that is a pure byte-prefix of prior committed bytes
- `source-compacted` — shorter non-prefix rewrite on `grok-build` (compaction)
- `source-replaced` — shorter non-prefix rewrite on other JSONL sources
- `group-changed` — native group disagrees with locked stream group
- `cursor-mismatch` — supplied cursor disagrees with state

## `apply_append` (LS-05)

File JSONL sources (`pi`, `claude-code`, `codex`, `openclaw`, `grok-build`):

1. Frame `pending + segment` into complete lines + new pending tail.
2. Incomplete / mid-UTF-8 only → `kind=unchanged` with updated `pending_byte_length`.
3. On complete lines: extend committed prefix and **re-normalize full prefix**
   (oracle path via `apply_snapshot`).
4. Append result must equal a fresh snapshot apply of the same committed prefix.
5. `consumed.bytes` counts newly framed complete-segment bytes only.

See [`streaming-file-sources.md`](streaming-file-sources.md) for per-source notes,
Grok provisional backend tools, and the performance bound.

## Delta-apply law

`apply_delta_to_snapshot(prior, delta)` must reconstruct the new snapshot
(records, diagnostics, revision). Producers order: removals → upserts/state
changes → diagnostic removes/adds. Match key: `provisional_id` if set, else
`record.id`. Diagnostic key: `code|input_line|record_index` (`-` sentinels).

## Framing

- Ordinary apply commits only LF-terminated lines (CRLF handled by source decode).
- Incomplete UTF-8 / half-lines stay in the pending buffer; no synthetic U+FFFD.
- `max_pending_bytes` / `max_line_bytes` → `stream_buffer_limit` error.

## Privacy

Stream diagnostics and error messages must not include paths, secrets, raw
JSON lines, or group/native ids. Operational codes:
`stream_source_reset`, `stream_cursor_conflict`, `stream_buffer_limit`,
`stream_sequence_gap`, `stream_resync_required`.

## AHP streaming (LS-06 / LS-07)

See [`ahp-action-streaming.md`](ahp-action-streaming.md) for Shape A successive
snapshots, Shape B reducer, provisional `activeTurn`, and serverSeq gaps.

## Not in this slice

- File I/O packages (LS-09)
- Optional AHP network clients (LS-10)
- Capability advertising (LS-12)

## Idiomatic entry points

### Python

```python
from hypabolic_trajectory import StreamOptions, create_stream, apply_snapshot

state = create_stream(StreamOptions(source="pi", group_id="session-1"))
state, update = apply_snapshot(state, b"", source_revision="gen-0")
```

### TypeScript

```ts
import { createStream, applySnapshot } from "@hypabolic/trajectory";

let { state, update } = applySnapshot(
  createStream({ source: "pi", groupId: "session-1" }),
  new Uint8Array(),
  "gen-0",
);
```

### Rust

```rust
use hypabolic_trajectory::{StreamOptions, TrajectorySource, apply_snapshot, create_stream};

let state = create_stream(StreamOptions::new(TrajectorySource::Pi).with_group_id("session-1"));
let (state, update) = apply_snapshot(&state, b"", "gen-0", None)?;
```

### .NET

```csharp
using Hypabolic.Trajectory.Streaming;

var state = TrajectoryStream.Create(new StreamOptions {
    Source = TrajectorySource.Pi,
    GroupId = "session-1",
});
var (next, update) = TrajectoryStream.ApplySnapshot(state, ReadOnlyMemory<byte>.Empty, "gen-0");
```

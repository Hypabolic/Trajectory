# Streaming file JSONL sources (LS-05)

Pure-core `apply_append` for file-backed coding-agent transcripts:
`pi`, `claude-code`, `codex`, `openclaw`, and `grok-build`.

Normative contracts: [`contracts/spec/streaming.md`](../contracts/spec/streaming.md),
product design: [`live-session-streaming.md`](live-session-streaming.md),
core API: [`streaming-core-api.md`](streaming-core-api.md).

## Scope

| Layer | Responsibility |
| --- | --- |
| Core | Complete-line / UTF-8 pending framing, `StreamState`, `apply_append` / `apply_snapshot`, cursor, snapshot+delta, reset discrimination |
| Caller / optional I/O | Open path, read growth, supply append segments or full prefixes, handle `reset-required` |

Core packages **must not** open filesystem watchers, network sockets, or SQLite.

## Framing

On every ordinary apply (append or snapshot):

1. Only **LF-terminated** lines are committed (CRLF → strip trailing CR in source decode).
2. Incomplete lines and mid-UTF-8 sequences stay in the **pending buffer** (state), never as records.
3. Cursor `next_byte_offset` advances only past committed complete-line bytes.
4. Cursor may report `pending_byte_length` without embedding content.
5. `max_pending_bytes` / `max_line_bytes` → `stream_buffer_limit` error; cursor unchanged.
6. `finish` may commit one final non-empty unterminated line once.

## Normalize context

All stream re-normalizes pass:

```text
source_context.partial = true
source_context.base_byte_offset = 0   # full committed prefix from origin
source_context.group_id = locked stream group (or options hint)
```

Partial mode keeps open-tool and source-specific partial semantics aligned with
batch adapters. Identity formulas are unchanged (`normalization` contract 0.2.0).

## `apply_append` algorithm

```text
pending + segment → frame complete lines
if no complete lines:
  update pending_byte_length only → kind=unchanged (records unchanged)
else:
  committed_prefix := prior_committed_prefix + complete_lines
  apply_snapshot(committed_prefix)   # oracle path
  restore pending tail onto state + update.cursor
  consumed.bytes := length of newly framed complete segment
```

### Correctness oracle

**Prefix re-normalize is the correctness oracle.** The append path in this slice
**is** full re-normalize of the committed prefix (via `apply_snapshot`). Shared
fixtures with `stream-oracle-parity` / `append_equals_prefix` require append
snapshots, deltas, diagnostics, and finality to match a fresh snapshot apply of
the same committed prefix.

### Performance bound

Steady-state append work is **O(size of committed prefix)** because the
implementation always re-normalizes the full prefix. That bound is intentional
for correctness parity. There is no separate incremental decoder in LS-05, so no
automatic “fallback to snapshot” branch is needed—the oracle path *is* the
implementation. Future incremental decode may be added only when it preserves
oracle parity and may emit an observable diagnostic when it falls back to full
re-normalize.

## Reset discrimination (shrink / rewrite)

When a **snapshot** supplies committed material shorter than the prior
`next_byte_offset` (same generation, after group checks):

| Condition | `reset.reason` |
| --- | --- |
| New material is a pure byte-prefix of prior `committed_prefix` | `source-truncated` |
| Non-prefix rewrite and source is `grok-build` | `source-compacted` |
| Non-prefix rewrite and other JSONL sources | `source-replaced` |

Default policy returns `kind = "reset-required"` **without** advancing the
cursor. Caller supplies a full snapshot via `reset` / snapshot apply with a new
generation.

Mid-file rewrites of **equal-or-longer** snapshots remain valid full
re-normalizes (delta shows upserts/removes). Append alone never shortens the
prefix; shrink is observed on snapshot reconciliation (or optional I/O that
re-reads the file).

### Grok Build compaction

Authoritative transcript is `chat_history.jsonl` only. Sessions append until
compaction rewrites the file; post-compaction content is truth →
`source-compacted` + full resync snapshot. See
[`grok-build-source-spec.md`](grok-build-source-spec.md) §5.5.

## Provisional records

| Source | Provisional signal |
| --- | --- |
| All JSONL | Completed lines → `stable` while stream open; `final` on `finish` when `finalize_on_close` (default true) |
| `grok-build` | Synthetic backend tool results (`backend_tool_result_synthesized`, content prefix `[backend …]`) → `provisional` until a later real `tool_result` re-normalize or `finish` |

Incomplete JSONL lines are **never** provisional records—they are pending bytes.

## Cross-chunk tool results

Tool call and matching tool result may arrive in successive append segments.
Full-prefix re-normalize on each commit preserves native linking and identity
across chunks (same as one-shot normalize of the joined prefix).

## Per-source notes

| Source | Cursor | Reset triggers | Notes |
| --- | --- | --- | --- |
| `pi` | byte | truncate, replace, generation | Session `id` locks group |
| `claude-code` | byte | truncate, replace | Transport/sidechain filtered as batch |
| `codex` | byte | truncate, replace | Append-only contract; still detect shrink |
| `openclaw` | byte | truncate, replace | Delivery-mirror not provisional |
| `grok-build` | byte | **compaction**, truncate, replace | Synthetic backend tools provisional |

## Fixtures

Generic under `conformance/cases/streaming/`:

- `append-one-line`, `append-equals-prefix-oracle`, `unterminated-line-held`,
  `utf8-byte-boundary`, `cross-chunk-tool-result`, `file-truncate-reset`,
  `file-compaction-reset`, `file-source-replaced-reset`,
  `duplicate-input-idempotent`, …

Per-source append sequences:

- `pi-append-sequence`, `claude-code-append-sequence`, `codex-append-sequence`,
  `openclaw-append-sequence`, `grok-build-append-sequence`,
  `grok-build-backend-provisional`

## Privacy

Stream diagnostics and error messages must not include paths, secrets, raw JSON
lines, or group/native ids. Operational codes: `stream_source_reset`,
`stream_cursor_conflict`, `stream_buffer_limit`, `stream_sequence_gap`,
`stream_resync_required`.

## Not in this slice

- AHP Shape A/B streaming (LS-06 / LS-07)
- Optional file I/O packages (LS-09)
- Capability advertising for `stream-*` (LS-12)

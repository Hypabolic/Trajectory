# AHP action-log streaming (LS-06 / LS-07)

Pure core algorithm for live AHP sessions. Callers own transport; core owns
Shape A snapshot apply, Shape B action-log reduction, cursors, provisional
`activeTurn` records, and content-safe diagnostics.

Normative product design: [`live-session-streaming.md`](live-session-streaming.md).  
Wire contracts: [`contracts/spec/streaming.md`](../contracts/spec/streaming.md),  
AHP source: [`contracts/spec/sources/ahp.md`](../contracts/spec/sources/ahp.md).

## Package boundary

| In core (all four runtimes) | Optional / out of core |
| --- | --- |
| `apply_ahp_snapshot` | WebSocket / JSON-RPC client |
| `apply_ahp_actions` + Shape B reducer | Auth, reconnect, listSessions |
| snapshot-revision and ahp-server-seq cursors | Host credentials |

Core packages must not open network sockets. Capability manifests must not claim
`stream-ahp-snapshot` / `stream-ahp-action-log` until LS-12 acceptance is met on
all four runtimes.

## Operations

```text
apply_ahp_snapshot(state, shape_a_bytes, source_revision, cursor?) → StreamUpdate
apply_ahp_actions(state, action_batch_bytes, cursor?) → StreamUpdate
```

### `apply_ahp_snapshot` (LS-06)

1. Decode Shape A `{ ahpProtocolVersion?, chat, session? }` with **partial**
   normalize so `activeTurn` is included.
2. Mark records whose native ids belong to `chat.activeTurn` as
   `status: provisional` with stable ids `prov-active-turn-{n}` (1-based in
   snapshot order).
3. Diff against prior stream snapshot (both snapshot + delta by default).
4. Commit cursor `position.kind = snapshot-revision` with host
   `source_revision` and content SHA-256.
5. Duplicate `(source_revision, content_sha256)` → `kind=unchanged`.

### `apply_ahp_actions` (LS-07)

1. Parse action batch as JSONL envelopes, a JSON array, or a single object.
2. Detect `serverSeq` gaps on the target chat channel →
   `kind=reset-required`, `reason=sequence-gap`, cursor **unchanged**.
3. Reduce known `chat/*` actions into ChatState (minimal complete subset).
4. Unknown `action.type` → non-fatal diagnostic `ahp_unknown_action` (fixed
   message; no action body).
5. Foreign / non-chat channels → ignore + `ahp_foreign_channel`.
6. Serialize reduced ChatState as Shape A and run the snapshot path.
7. Commit cursor `position.kind = ahp-server-seq` with
   `last_server_seq` / `next_server_seq`.

## Cursor families

| Kind | Fields | Authority |
| --- | --- | --- |
| `snapshot-revision` | `revision`, optional `content_sha256` | Host Shape A revision token |
| `ahp-server-seq` | `next_server_seq`, `last_server_seq`, optional `next_byte_offset` | Totally ordered action log |

Gaps never silently advance the serverSeq cursor. After gap, callers must
resync with a full Shape A snapshot (and typically `reset` / new generation).

## Provisional lifecycle

- Incomplete JSONL lines are **not** records.
- AHP `activeTurn` content is provisional until a turn-terminal action, a
  later snapshot without that active turn, or stream `finish`.
- When a provisional id disappears from a later snapshot, it appears in
  `provisional.finalized_ids`.

## Action subset (reducer)

Minimum complete set for offline / streaming reduce (AHP 0.7.x names):

| Action | Effect |
| --- | --- |
| `chat/turnStarted` | Open `activeTurn` |
| `chat/responsePart` | Append part |
| `chat/delta` / `chat/reasoning` | Append text to part |
| `chat/toolCallStart` … `Complete` (+ confirm / content / auth family) | Tool state machine |
| `chat/usage` | Attach usage on active turn |
| `chat/turnComplete` / `turnCancelled` / `error` | Finalize into `turns[]` |
| `chat/truncated` | Drop turns after `turnId` (or all) |

Session/root/terminal/MCP channel actions are foreign for a chat stream.

## Action ≡ snapshot oracle

Reducing an action-log prefix and applying the equivalent Shape A document
must produce the same stream record identities, statuses, and content for the
committed chat state. Shared fixture:
`conformance/cases/streaming/ahp-action-equals-snapshot/`.

## Diagnostics (content-safe)

| Code | Meaning |
| --- | --- |
| `ahp_unknown_action` | Unknown action type ignored |
| `ahp_foreign_channel` | Non-target channel ignored |
| `stream_sequence_gap` | serverSeq hole; resync required |
| `stream_cursor_conflict` | Supplied cursor disagrees with state |

Messages must not include action bodies, channel URIs with secrets, paths, or
transcript prose.

## Fixtures

Under `conformance/cases/streaming/`:

- `ahp-snapshot-empty`, `ahp-snapshot-active-turn`, `ahp-snapshot-growth`,
  `ahp-snapshot-duplicate-revision`
- `provisional-to-stable` (AHP activeTurn → completed)
- `ahp-action-turn-flow`, `ahp-action-sequence-gap`,
  `ahp-action-unknown-foreign`, `ahp-action-equals-snapshot`

## Runtime entry points

| Runtime | API |
| --- | --- |
| Python | `apply_ahp_snapshot`, `apply_ahp_actions`, `TrajectoryStream.apply_ahp_*` |
| TypeScript | `applyAhpSnapshot`, `applyAhpActions` |
| Rust | `apply_ahp_snapshot`, `apply_ahp_actions` |
| .NET | `ApplyAhpSnapshot`, `ApplyAhpActions` |

## Related docs

- [`streaming-core-api.md`](streaming-core-api.md) — snapshot/append file path
- [`ahp-source-spec.md`](ahp-source-spec.md) — offline Shape A mapping
- [`ahp-ingest-status.md`](ahp-ingest-status.md) — shipped vs planned status
- Protocol pin: `conformance/vendor/ahp/PROTOCOL_VERSION` (`0.7.0`)

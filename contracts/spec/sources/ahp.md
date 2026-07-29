# Source: Agent Host Protocol (`ahp`)

Contract version: AHP source decode `0.1.0` (Phase 1 — Shape A snapshot decode
on .NET, TypeScript, and Rust).

Wire source name: **`ahp`** (no aliases on the wire).

Related:

- [AHP design (Trajectory)](../../../docs/ahp-source-spec.md)
- [Normalization](../normalization.md)
- [Identity](../identity.md)
- [Listing](../listing.md)
- AHP reference: <https://microsoft.github.io/agent-host-protocol/>
- Vendor pin: [`conformance/vendor/ahp/PROTOCOL_VERSION`](../../../conformance/vendor/ahp/PROTOCOL_VERSION)

AHP is a multi-client session coordination protocol (JSON-RPC, reducers,
action envelopes). It is **not** a harness JSONL transcript. Trajectory
ingests the agent-agnostic chat surface only.

---

## 1. Scope (Phase 0 / Phase 1)

| In scope | Deferred |
| --- | --- |
| **Shape A** snapshot export (`input.json`) | **Shape B** action-log reduce (`input.jsonl`) |
| One chat per normalize | Live host / WebSocket client |
| Completed turns + optional `activeTurn` policy | Terminals, changesets, MCP channel as primary transcripts |
| Snapshot decode on all runtimes (Phase 1) | Official reducer parity (Phase 2) |
| Export directory listing (Phase 3) | ACP session logs as a separate source |

`ahp` is advertised in `contracts/compatibility.json` →
`implemented.sources` once multi-runtime snapshot conformance passes (AHP-1).

---

## 2. Protocol version pin

| Field | Value |
| --- | --- |
| Vendor pin file | `conformance/vendor/ahp/PROTOCOL_VERSION` |
| Current pin | `0.7.0` (AHP `PROTOCOL_VERSION` at authoring) |
| Compatible allow-list (decode) | All `0.7.x` once runtime ships; expand only with reviewed fixture updates |
| Export field | `ahpProtocolVersion` on Trajectory envelopes |

Rules:

- Incompatible major/minor → fatal `invalid_input` (version string appears only
  in the exception message; there is no separate diagnostic/fatal code). Aligns
  with [diagnostics.md](../diagnostics.md) fatal set.
- Missing `ahpProtocolVersion` on a snapshot: assume the vendor pin used to
  author the fixture; emit non-fatal diagnostic `ahp_version_missing`.
- Unknown AHP action types (action-log path, Phase 2): ignore + non-fatal
  `ahp_unknown_action`.

Trajectory does **not** rewrite AHP. The export schema
[`ahp-export-v1.schema.json`](../../schemas/ahp-export-v1.schema.json) is a
Trajectory-owned envelope around ChatState-like data.

---

## 3. Accepted containers

### 3.1 Shape A — Snapshot (normative for Phase 1)

Preferred offline export. Conformance cases under
`conformance/cases/ahp/**` use this shape as `input.json`.

```jsonc
{
  "ahpProtocolVersion": "0.7.0",
  "chat": { /* ChatState-like */ },
  "session": { /* optional SessionState / summary fields */ }
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `ahpProtocolVersion` | Recommended | SemVer string; missing → `ahp_version_missing` |
| `chat` | **Yes** | ChatState-like object (§4) |
| `session` | No | Provider, title, project, working directories for provenance only |

`native_container` for conformance: `ahp-export-snapshot-v1`.

### 3.2 Shape B — Action log (deferred)

One JSON object per line: `ActionEnvelope` or bare action with optional
envelope fields, ordered by `serverSeq` ascending. Adapter reduces the
target chat channel into ChatState, then decodes as Shape A.

Not required for Phase 0–1. Spec hooks only; no fixtures yet.

### 3.3 Shape C — Combined multi-chat export (deferred)

Session-level unpack helper yields one Shape A/B input per chat. Core
normalize remains **one chat per call**.

---

## 4. ChatState subset (decode input)

Minimum fields the snapshot decoder MUST understand (names match AHP 0.7
state schema; extra properties are ignored unless noted):

| Path | Role |
| --- | --- |
| `chat.resource` | Chat URI; preferred `GroupId` (full URI, e.g. `ahp-chat:/…`) |
| `chat.title` | Provenance only |
| `chat.status` | SessionStatus bitset; provenance |
| `chat.modifiedAt` | Listing / provenance |
| `chat.origin` | Fork / side-chat / tool provenance when present |
| `chat.turns[]` | Completed turns (§5) |
| `chat.activeTurn` | In-progress turn; whole vs partial policy (§5.4) |
| `chat.workingDirectories` | Provenance URIs (synthetic in fixtures) |

Optional `session` (when present):

| Path | Role |
| --- | --- |
| `session.provider` | Provider provenance |
| `session.title` | Provenance |
| `session.workingDirectories` | Provenance |
| `session.project` | Provenance |

---

## 5. Mapping: ChatState → decoded events

Source adapter responsibility ends at **decoded session events** for the
shared normalizer (native ids, timestamps, tool link inputs, usage, model).
Shared bounds, linking, hashes, and diagnostics follow normalizer `0.2.0`.

### 5.1 Turn order

1. Sort `chat.turns` by `startedAt` ascending (**nulls last** — missing
   `startedAt` sorts after any present timestamp), then `id` with
   **lexicographic compare of UTF-8 bytes** for `id` ties (matches listing
   sort style for opaque ids). JavaScript-style UTF-16 code unit order is
   **not** required.
2. If partial mode and `activeTurn` is present, append it after completed
   turns (do not re-sort with completed turns).
3. Within each turn, emit in order:
   1. Initiating message (`turn.message`)
   2. Each `responseParts[i]` in array order
   3. Turn-level usage if not already applied
   4. Terminal turn state is meta/diagnostic only when needed (cancel/error)

### 5.2 Message origin → role

| `Message.origin.kind` | Trajectory role |
| --- | --- |
| `user` | `user` |
| `agent` | `assistant` |
| `tool` | Do not emit free-standing user/assistant; tool outputs come from tool call parts |
| `systemNotification` | `system` if IR supports system; else assistant + `ahp_system_as_assistant` |
| unknown | drop + `ahp_unknown_message_origin` |

User/agent **text** → message content. Attachments (v1): record metadata in
provenance / extras; do not fetch binary content-by-reference
(`ahp_unresolved_content_ref` when a ref is required and unresolved).

### 5.3 Response parts

| Part `kind` | Decode |
| --- | --- |
| `markdown` | Assistant text. **Concatenate contiguous markdown parts within a turn** for compact message-trajectory; retain part boundaries only in rich projections when schema allows. |
| `reasoning` | **First-class reasoning** (IR `reasoning` role/kind, Codex-like). Empty/whitespace content is dropped without a diagnostic. `ahp_reasoning_omitted` remains reserved if a runtime lacks a reasoning slot. |
| `toolCall` | Tool call + optional result from `toolCall` state (§5.4) |
| `resource` | No body fetch; stub / diagnostic |
| `systemNotification` | Diagnostic-only or system message (non-identity) |
| `inputRequest` | Skip content for trajectory v1; may emit `ahp_input_request_skipped` |

### 5.4 Tool calls

From `ToolCallState` on `kind: "toolCall"` parts:

| Field | Mapping |
| --- | --- |
| `toolCallId` | Native tool call id (required for linking) |
| `toolName` | Tool name; missing → normalizer `unknown_tool` |
| Arguments | Prefer structured parameters when present; else parse `toolInput` string as JSON object; invalid/non-object → `_raw` wrapper per normalization contract |
| Result | Prefer text `content` blocks, then `structuredContent`, then `pastTenseMessage`; stringify deterministically |
| `status: completed` + `success: true` | Linked result, success |
| `status: completed` + `success: false` | Linked result with error text from content / past-tense / `error.message`; **do not invent success**. Fallback content is `"error"` when none of those are present |
| `status: cancelled` / denied | Result with success=false; prefer `reasonMessage`, then `reason`; fallback content is `"cancelled"`; **do not invent success** |
| Permissions / auth pauses | Not separate IR events in v1; provenance only |

Linking: AHP pairs call and result by `toolCallId`. Feed normalizer as
ordered call then result. Duplicate-id policy remains the normalizer’s.

### 5.5 Active turn policy

| Mode | Behaviour |
| --- | --- |
| **Whole** | Incomplete `activeTurn` is not required for success. Either finalize into a synthetic completed turn when complete enough, or drop with non-fatal `ahp_active_turn_omitted`. Prefer drop when tools are mid-flight. |
| **Partial** | Decode `activeTurn` as an open turn after completed turns. |

Whole mode still requires ≥1 normalized user and assistant-role record after
decode (shared normalizer rule).

### 5.6 Usage and model

| AHP | Trajectory |
| --- | --- |
| `UsageInfo.inputTokens` / `outputTokens` / `cacheReadTokens` | Usage when present; never invent |
| `UsageInfo.model` or `Message.model.id` | Model string when present |
| `session.provider` | Provider provenance |

### 5.7 Identity anchors

| Concept | Preferred native id |
| --- | --- |
| Session group (`GroupId`) | Full chat URI (`chat.resource`), e.g. `ahp-chat:/00000000-…` |
| Turn | `turn.id` |
| Tool call | `toolCallId` |
| Response part | markdown/reasoning `id` / `partId` when present |
| Ordering | Turn order + part index |

Byte offsets (Shape A): **non-source-byte** synthetic anchors derived from
`(turnId, partIndex)` (sequential or hashed). Identity-bearing goldens use
canonical projection rules, not raw export file offsets.

Shape B (Phase 2): UTF-8 offsets into the action-log file (line anchors),
with `source_context.group_id` = chat URI and monotonic `base_byte_offset`
over the log.

Content / location fallbacks follow [identity.md](identity.md) when native
ids are missing.

### 5.8 Diagnostics (AHP-prefixed, non-exhaustive)

| Code | Severity | When |
| --- | --- | --- |
| *(fatal `invalid_input`)* | fatal | Protocol version outside allow-list (not a diagnostic code) |
| `ahp_version_missing` | non-fatal | Snapshot lacks `ahpProtocolVersion` |
| `ahp_active_turn_omitted` | non-fatal | Whole mode drops incomplete active turn |
| `ahp_unknown_message_origin` | non-fatal | Unknown message origin kind |
| `ahp_unknown_action` | non-fatal | Shape B unknown action type (Phase 2) |
| `ahp_foreign_channel` | non-fatal / debug | Shape B envelope for other channel |
| `ahp_unresolved_content_ref` | non-fatal | Content-by-reference not fetched |
| `ahp_input_request_skipped` | non-fatal | Input request part skipped |
| `ahp_reasoning_omitted` | non-fatal | Reasoning dropped by policy |
| `ahp_system_as_assistant` | non-fatal | System mapped to assistant |

Shared normalizer codes (`orphan_tool_result`, bounds, etc.) apply after
decode.

---

## 6. Conformance fixtures (Phase 0)

| Case id | Input intent |
| --- | --- |
| `ahp/tool-calls` | One completed turn: user + markdown + completed toolCall |
| `ahp/multi-turn` | Two completed turns (ordering, ids) |
| `ahp/cancelled-turn` | Turn `state: cancelled` and/or cancelled tool; no invented success |

All inputs are **synthetic** (fixed UUIDs, fake titles, no real paths or
secrets). Expected projection goldens are filled when snapshot decoders land
(AHP-1+); Phase 0 may ship reviewed stubs only.

Privacy: see [conformance README](../../../conformance/README.md) and
release-readiness fixture sanitization.

---

## 7. Listing (Phase 3 — sketch only)

Explicit root only (no home default):

```text
<root>/
  sessions/<session-uuid>/
    session.json
    chats/<chat-uuid>.json      # ChatState snapshot
    chats/<chat-uuid>.actions.jsonl
```

`list` yields one row per chat snapshot. Missing root → empty page.

---

## 8. Implementation notes

- Prefer published AHP client types for validation where licensing allows.
- Snapshot decode first on **.NET, TypeScript, and Rust**; action-log reduce
  where official reducers exist (Phase 2).
- Runtime capability manifests and sample CLIs advertise `ahp` only after
  shared cases pass on every claiming runtime.

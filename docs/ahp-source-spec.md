# Spec: Agent Host Protocol (AHP) support in Trajectory

Status: **AHP-1 landed** (Shape A snapshot decode on .NET, TypeScript, and Rust)  
Target product slice: post-v1 source family  
AHP reference: [microsoft.github.io/agent-host-protocol](https://microsoft.github.io/agent-host-protocol/)  
AHP schemas: [github.com/microsoft/agent-host-protocol/schema](https://github.com/microsoft/agent-host-protocol/tree/main/schema)  
Pinned protocol family: **0.7.x** (vendor pin `conformance/vendor/ahp/PROTOCOL_VERSION`; pre-1.0; breaking changes expected)

**AHP-1 deliverables:** Shape A snapshot decoder → IR on all three runtimes,
shared conformance goldens, wire source `ahp` in runners/CLIs, and
`compatibility.json` → `implemented.sources`.  
**Not yet:** action-log reduce (Shape B), live WebSocket host, export listing.

Related Trajectory docs:

- [Architecture](architecture.md)
- [Adapter authoring](adapter-authoring.md)
- [Normalization](../contracts/spec/normalization.md)
- [Listing](../contracts/spec/listing.md)
- [Identity](../contracts/spec/identity.md)

---

## 1. Executive summary

The **Agent Host Protocol (AHP)** is a multi-client session coordination
protocol: a host owns authoritative state; clients speak JSON-RPC; mutations
flow as totally ordered **action envelopes** through pure **reducers**. It is
**not** a filesystem JSONL transcript format like Pi / Claude Code / Codex.

Trajectory should treat AHP as a **new source family** whose native unit of
conversation is a **chat** (`ahp-chat:/…`), materialised either as:

1. a **`ChatState` snapshot** (completed history + optional `activeTurn`), or  
2. an **ordered `ActionEnvelope` stream** (reducible to the same state).

**v1 goal:** offline / export ingest that maps AHP chat history into the
existing private IR and all public projections (Hypabolic, canonical, message
trajectory, OpenAI, minimal JSONL, OTEL GenAI).

**Non-goals for v1:** running an AHP host, full multi-client reconciliation,
live WebSocket client inside core packages, terminal/changeset as primary
transcripts, or ACP agent backends.

```text
AHP ChatState / action log  →  source adapter "ahp"  →  shared normalizer  →  IR  →  projections
```

---

## 2. What AHP is (and is not)

### 2.1 Problem AHP solves

An agent session is usually stuck in one app. AHP makes a session a **shared
resource**: many clients (IDE, web, CLI, mobile) see one synchronized view via
immutable state, pure reducers, and write-ahead reconciliation.

### 2.2 Layering (AHP vs ACP vs harness JSONL)

| Layer | Role | Trajectory relationship today |
| --- | --- | --- |
| **Harness files** (Pi, Claude Code, Codex, …) | On-disk session JSONL | Existing sources |
| **ACP** (Agent Client Protocol) | 1:1 client ↔ agent | Not a Trajectory source; host-internal |
| **AHP** | N clients ↔ host (state + sequencing) | **Proposed source** |

AHP hosts often *use* ACP (or vendor APIs) *below* the host; clients never see
agent-private wire formats. Trajectory should ingest the **agent-agnostic AHP
surface** (chat turns, response parts, tool call state), not ACP.

### 2.3 Wire shape (normative for this design)

- Transport: JSON-RPC 2.0 (typically WebSocket).
- Routing key: every command/notification params carry `channel: URI`.
- State channels: `ahp-root://`, `ahp-session:/<uuid>`, `ahp-chat:/<uuid>`,
  terminals, changesets.
- Mutations: `ActionEnvelope { channel, action, serverSeq, origin, … }`.
- Chat content model:
  - **`ChatState.turns[]`**: completed turns.
  - **`ChatState.activeTurn`**: in-progress turn (streaming).
  - **`Turn` / `ActiveTurn`**: `id`, `startedAt`, initiating `Message`,
    `responseParts[]`, `usage`, terminal `state` (`complete` | `cancelled` |
    `error`).
  - **`ResponsePart`**: markdown | toolCall | reasoning | resource | system
    notification | input request.
  - **Tool lifecycle actions**: `chat/toolCallStart` → deltas → ready →
    approve/deny → complete (+ optional result confirmation / auth).

AHP is **under active development** (pre-1.0 SemVer). Trajectory MUST pin a
protocol minor, vendor schema digests used by conformance, and treat unknown
action `type` values as ignore-with-diagnostic (mirror AHP client guidance).

---

## 3. Product fit

| Trajectory need | AHP capability |
| --- | --- |
| Cross-harness experience aggregation | Host already unifies Copilot / Claude / Codex / ACP agents into one chat model |
| Eval / train / replay | Completed turns + tool call results are high-signal trajectories |
| Observability | AHP OTLP channels (`otlp/export*`) complement Trajectory’s OTEL GenAI **projection**; do not conflate the two |
| Local listing | Not native AHP (imperative `listSessions` + network). Export folders or optional host client instead |
| Deterministic identity | Prefer AHP ids (`turnId`, `toolCallId`, `partId`, chat URI); synthetic fallbacks only when missing |

**Wedge vs existing sources:** AHP is the **host-level** transcript, not a
single-harness log. One chat may already be the result of mapping several
backends. That is a feature for multi-client / multi-agent environments (e.g.
VS Code Agent Sessions).

---

## 4. Design decisions

### D1 — Source wire name

| Field | Value |
| --- | --- |
| Wire source | `ahp` |
| Display | Agent Host Protocol |
| Enum / vocabulary | Add to compatibility, case schema, runners, sample CLIs |

Reject aliases (`agent-host`, `vscode-ahp`) on the wire; document them only as
user-facing synonyms in docs if needed.

### D2 — Trajectory unit = chat, not session

AHP **sessions** contain a **catalog of chats** (default chat + side chats /
worker chats). Trajectory’s unit of normalize remains one ordered event stream
→ one IR document.

| Input | Trajectory mapping |
| --- | --- |
| One `ChatState` | One normalize call; `GroupId` = chat URI |
| Whole `SessionState` export | **N** normalize calls (one per chat), or a batch helper that returns N results — not a single flattened IR without group boundaries |
| Multi-chat session for analytics | Caller iterates chats; optional future batch API |

Side-chat / fork `origin` metadata is preserved in IR provenance when present;
it does not merge into the parent chat.

### D3 — Two accepted container shapes (v1)

#### Shape A — Snapshot (`input.json`)

Canonical offline export. Preferred for conformance goldens.

```jsonc
{
  "ahpProtocolVersion": "0.7.0",
  "chat": { /* ChatState */ },
  "session": { /* optional SessionState or SessionSummary fields */ }
}
```

Rules:

- Required: `chat` object valid against pinned AHP `ChatState` (or a Trajectory
  subset schema — see §6).
- Optional `session` for provider/title/project/workingDirectories provenance.
- `activeTurn` MAY be present; in **whole** mode it is either finalized into a
  synthetic completed turn (if complete enough) or dropped with diagnostic
  `ahp_active_turn_omitted`. In **partial** mode it is decoded as an open turn.

#### Shape B — Action log (`input.jsonl`)

One JSON object per line: either a full `ActionEnvelope` or a bare action with
optional envelope fields. Lines ordered by `serverSeq` ascending.

```jsonc
{"channel":"ahp-chat:/…","serverSeq":41,"origin":{"kind":"server"},"action":{"type":"chat/turnStarted",…}}
```

Rules:

- Adapter **reduces** envelopes for the target chat channel into ChatState using
  the **pinned AHP reducer semantics** (or an equivalent minimal reducer
  covering the action subset in §5).
- Envelopes for other channels are ignored (diagnostic `ahp_foreign_channel`
  only if noisy / debug).
- Unknown `action.type`: ignore + `ahp_unknown_action` (non-fatal).
- After reduction, decode as Shape A.

#### Shape C — Combined export (optional convenience)

```jsonc
{
  "ahpProtocolVersion": "0.7.0",
  "session": { /* SessionState */ },
  "chats": [
    { "chat": { /* ChatState */ }, "actions": [ /* optional ActionEnvelope[] */ ] }
  ]
}
```

Not a single-shot `NormalizeInput` container for v1 core API. Provided via a
documented **unpack helper** (or sample CLI) that yields one Shape A/B input per
chat. Keeps the source adapter’s contract simple: **one chat per normalize**.

### D4 — Live host is out of core packages

| Package | Responsibility |
| --- | --- |
| Core (`Hypabolic.Trajectory` / `@hypabolic/trajectory` / `hypabolic-trajectory`) | Decode Shape A/B only; no network |
| Optional future `*-ahp` / sample | Connect to host, subscribe, export snapshot/log, call core normalize |

Rationale: matches Hermes (core free of SQLite), OTEL (optional), and AOT /
dependency hygiene. Live subscribe needs WebSocket, reconnect, auth, capability
negotiation — wrong default for library consumers who only have exports.

### D5 — Partial / chunked mode

| Mode | Behaviour |
| --- | --- |
| Whole | Require ≥1 completed user+assistant cycle per existing whole-mode rules after decode; incomplete `activeTurn` not required for success |
| Partial | Allow open `activeTurn`; tool results may lack calls in-chunk if call appeared earlier (Codex-like); identity uses stable AHP ids across chunks |

Chunking unit for streaming consumers: **prefix of action log by `serverSeq`**,
with `SourceContext.GroupId = chat URI` and monotonic `BaseByteOffset` over the
UTF-8 action log (or explicit sequence range in `_meta` if we add
`source_context.ahp_server_seq_base` later).

v1 partial identity MUST be defined against **action-log bytes** when Shape B is
used; snapshot-only partial is best-effort.

### D6 — Protocol version pin

- Conformance fixtures declare `ahpProtocolVersion`.
- Adapter accepts a small allow-list of compatible versions (e.g. all `0.7.x`
  once Phase 1 ships; expand only with reviewed fixture updates).
- Incompatible major/minor → fatal `invalid_input` (message may mention the
  unsupported version; no separate fatal/diagnostic code).
- Missing version on snapshot: assume pin used to author the fixture; warn
  diagnostic `ahp_version_missing` (non-fatal) so real-world dumps still work.

---

## 5. Mapping: AHP → decoded events → IR

Source adapter responsibility stops at **decoded session events** for the
shared normalizer (native ids, timestamps, tool link inputs, usage, model).
Shared bounds, linking policy, hashes, and diagnostics codes follow normalizer
`0.2.0` (or successor).

### 5.1 Turn order

1. Sort `chat.turns` by `startedAt` ascending (**nulls last**), then `id`
   with lexicographic UTF-8 byte compare (see `contracts/spec/sources/ahp.md`).
2. If partial and `activeTurn` present, append after completed turns.
3. Emit events **within** each turn in this order:
   1. Initiating message (`turn.message`)
   2. Each `responseParts[i]` in array order
   3. Turn terminal usage if not already applied
   4. Turn completion / cancel / error marker (meta only if needed)

### 5.2 Message origin → role

| `Message.origin.kind` (AHP) | Trajectory role |
| --- | --- |
| `user` | `user` |
| `agent` | `assistant` |
| `tool` | Do not emit as free-standing user/assistant; tool outputs come from tool call parts |
| `system` | `system` if IR supports system; else assistant with diagnostic `ahp_system_as_assistant` |
| unknown | drop + `ahp_unknown_message_origin` |

User/agent message **text** → message content. Attachments:

- v1: record attachment metadata in provenance / extras; inline text ranges
  optional; binary content by reference is **not** fetched (content-ref
  unresolved → diagnostic `ahp_unresolved_content_ref`).

### 5.3 Response parts

| Part kind | Decode |
| --- | --- |
| `markdown` (and text deltas reduced into it) | Assistant text segment (concatenate contiguous markdown parts or emit one message per part — **prefer concatenate within turn** for message-trajectory cleanliness, with part boundaries retained only in rich Hypabolic projection if needed) |
| `reasoning` | Reasoning/thinking record when IR has a slot; else omit + `ahp_reasoning_omitted` **or** map to assistant with tagged channel (decide at implement; recommendation: **first-class reasoning field** if IR already has it from Codex) |
| `toolCall` | Tool call + optional result from `ToolCallState` |
| `resource` | Skip body fetch; diagnostic or stub reference record |
| `systemNotification` | Diagnostic-only or system message (non-identity) |
| `inputRequest` | Skip content for trajectory v1; may emit meta diagnostic `ahp_input_request_skipped` |

### 5.4 Tool calls

From `ToolCallState` / tool actions:

| Field | Mapping |
| --- | --- |
| `toolCallId` | Native tool call id (required for linking) |
| `toolName` | Tool name; missing → `unknown_tool` after normalizer |
| Arguments | Object from completed parameter stream; invalid → `_raw` wrapper per normalization contract |
| Result | Prefer text content, `structuredContent`, `pastTenseMessage`; on failure also `reasonMessage` / `reason` / `error.message`; status-appropriate fallback (`"cancelled"` vs `"error"`) |
| Status denied/cancelled/error | Result record with success=false / error text; do not invent success |
| Permissions / auth pauses | Not separate IR events in v1; may appear in provenance |

Linking: AHP already pairs call and result by id. Feed normalizer as ordered
call then result. Duplicate id policy remains normalizer’s.

### 5.5 Usage and model

| AHP | Trajectory |
| --- | --- |
| `UsageInfo.inputTokens` / `outputTokens` / `cacheReadTokens` | Usage metadata when present; never invent |
| `UsageInfo.model` or `Message.model` | Model string when present |
| `session.provider` / agent selection | Provider / agent provenance |

### 5.6 Identity anchors

| Concept | Preferred native id |
| --- | --- |
| Session group | Chat URI (`ahp-chat:/…`) as `GroupId` |
| Turn | `turn.id` |
| Tool call | `toolCallId` |
| Response part | `partId` when present |
| Ordering | Turn order + part index; action log uses `serverSeq` |

Byte offsets:

- Shape B: UTF-8 offsets into the action-log file (line anchors).
- Shape A: synthetic stable anchors derived from `(turnId, partIndex)` hashed or
  sequential — document as **non-source-byte** anchors; identity-bearing
  goldens use canonical projection rules, not raw file offsets.

### 5.7 Action subset the reducer must implement (v1)

Minimum for offline action-log reduce (names as of AHP 0.3 docs):

| Action | Effect |
| --- | --- |
| `chat/turnStarted` | Open active turn |
| `chat/responsePart` | Create part |
| `chat/delta` | Append text to part |
| `chat/reasoning` | Append reasoning |
| `chat/toolCallStart` / `Delta` / `Ready` / `Approved` / `Denied` / `Complete` / result confirmation family | Tool state machine (subset OK if final state is complete/cancelled) |
| `chat/usage` | Attach usage |
| `chat/turnComplete` / `turnCancelled` / `error` | Finalize turn into `turns[]` |
| `chat/truncated` | Record truncation diagnostic |

Defer: draft/pending/queue reordering, multi-chat session catalog actions,
terminals, changesets, MCP channel, comments, root agents — unless they appear
inside a chat export we must not crash on (ignore).

**Implementation note:** Prefer calling into published AHP client reducers
where licensing and deps allow (`@microsoft/agent-host-protocol`, crates
`ahp` / `ahp-types`). Where .NET has no official client, either:

1. port a minimal reducer from the TypeScript/Rust reference + fixture corpus
   under `types/test-cases/reducers/`, or  
2. accept **snapshot-only** on .NET for v1 and action-log on TS/Rust first
   (capability gap — document; do not advertise full `ahp` on .NET until
   parity).

**Recommendation:** ship **snapshot decode on all three runtimes** first
(ML-A1), then action-log reduce where official reducers exist (ML-A2).

---

## 6. Trajectory-owned contracts

Do **not** rewrite AHP. Add Trajectory-side contracts:

| Artifact | Purpose |
| --- | --- |
| `contracts/spec/sources/ahp.md` | Normative decode + mapping rules (this doc condensed) |
| `contracts/schemas/ahp-export-v1.schema.json` | Trajectory export envelope (Shapes A/C), refs or embeds ChatState subset |
| `contracts/compatibility.json` | Add `ahp` only when multi-runtime bar is met |
| Conformance cases | `conformance/cases/ahp/...` |
| Vendor pin | `conformance/vendor/ahp/PROTOCOL_VERSION` + schema hash or submodule note |

Privacy: synthetic URIs, titles, paths; no real workspace contents in fixtures.

---

## 7. Listing

### 7.1 v1 — export directory listing (optional but useful)

Explicit root only (no home default — AHP is not a single well-known path):

```text
<root>/
  sessions/<session-uuid>/
    session.json
    chats/<chat-uuid>.json      # ChatState snapshot
    chats/<chat-uuid>.actions.jsonl
```

Listing API:

- `list` yields one row per chat snapshot (id = chat uuid or URI, path =
  snapshot path, updatedAt from `modifiedAt`).
- Missing root → empty page.
- Conformance store under `conformance/stores/ahp-...`.

### 7.2 Live `listSessions`

Out of core. Sample CLI / optional package may call host `listSessions` and
write export layout for core normalize.

### 7.3 VS Code host storage

If a stable on-disk layout is documented by VS Code agent host, add a
**discoverer** later. Until then, do not guess paths in core.

---

## 8. Sample CLI / DX

Extend sample CLIs:

```bash
trajectory list --source ahp --root ./ahp-export
trajectory show --source ahp --path ./ahp-export/sessions/.../chats/....json
```

Optional:

```bash
trajectory ahp export --url wss://host --session ahp-session:/… --out ./ahp-export
```

(export command lives in optional tooling, not core library.)

---

## 9. Outputs

No new output schema required for v1. Existing projections apply:

| Output | Notes |
| --- | --- |
| Message trajectory / OpenAI chat | Primary consumer of turn/part mapping |
| Canonical / Hypabolic | IDs from turn/toolCall/chat URI |
| OTEL GenAI | Spans from turns + tools; distinct from AHP `otlp/export*` live channel |
| Minimal JSONL | Streaming-friendly line form of message projection |

**Future (not v1):** AHP **output** adapter (IR → action log) for round-trip
experiments — explicitly deferred; Trajectory remains ingest-first.

---

## 10. Phased delivery

### Phase 0 — Spec freeze (this document + contract draft) — **done (AHP-0)**

- [x] Agree D1–D6 (baseline; open questions §13 still for Phase 1 code)
- [x] Pin AHP protocol version (`conformance/vendor/ahp/PROTOCOL_VERSION` → 0.7.0)
- [x] Author `contracts/spec/sources/ahp.md` + `ahp-export-v1.schema.json`
- [x] Sketch 3 synthetic fixtures (`ahp/tool-calls`, `ahp/multi-turn`, `ahp/cancelled-turn`)

### Phase 1 — Snapshot source (all runtimes) — **done (AHP-1)**

- [x] Shape A decoder → IR events
- [x] Conformance: `ahp/tool-calls`, `ahp/multi-turn`, `ahp/cancelled-turn`
- [x] Wire `ahp` in runners; add to `compatibility.implemented.sources` after
  all three runtimes pass
- [x] Sample CLI `show --source ahp --path …`

### Phase 2 — Action-log reduce

- [ ] Shape B reducer (reuse official AHP reducers where possible)
- [ ] Partial-mode case over action log prefixes
- [ ] .NET strategy: port minimal reducer **or** temporary capability gap

### Phase 3 — Export listing + unpack helpers

- [ ] Directory layout + `list` explicit root
- [ ] Combined export unpack helper
- [ ] Docs: how to capture exports from VS Code / AHPX

### Phase 4 — Optional live package (post-core)

- [ ] Subscribe + snapshot export
- [ ] Auth / reconnect policy documented
- [ ] Still calls Phase 1–2 core APIs

### Phase 5 — Hardening

- [ ] Protocol bump process (when AHP 0.4 / 1.0 lands)
- [ ] Performance (large chats, many tool calls)
- [ ] Identity baseline updates

---

## 11. Conformance case plan (minimum)

| Case | Input | Asserts |
| --- | --- | --- |
| `ahp/tool-calls` | Snapshot with user turn + markdown + toolCall complete | Tool link, args/result, projections |
| `ahp/multi-turn` | Two completed turns | Ordering, ids |
| `ahp/turn-cancelled` | Turn state cancelled mid tools | No invented success; diagnostics |
| `ahp/reasoning` | Reasoning parts present | Mapping policy locked |
| `ahp/action-log-replay` | Shape B log reduces same identity as snapshot | Reducer equivalence |
| `ahp/partial-prefix` | First N envelopes partial=true | Partial identity rules |
| `ahp/list-export` | Store fixture | Listing pagination |

Goldens: hand-reviewed; never CI regenerate-and-accept.

---

## 12. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| AHP pre-1.0 breaks wire types | Pin version; vendor schema; ignore unknown actions; version field on exports |
| No official .NET AHP client | Snapshot-first on all runtimes; reducer only where deps exist |
| Content-by-reference | Don’t fetch in core; diagnostic for unresolved refs |
| Multi-chat sessions | One chat per normalize; unpack helper for sessions |
| Confusion with ACP / harness logs | Docs table §2.2; source name `ahp` only |
| Live host auth & security | Keep network out of core |
| Over-large tool outputs | Existing bounds/truncation apply after decode |

---

## 13. Open questions (resolve before Phase 1 code)

1. **Concatenate vs split** assistant markdown parts within a turn for
   message-trajectory — recommendation: concatenate for compact outputs;
   preserve part list in Hypabolic rich form if the schema allows extras.
2. **Reasoning visibility** in compact message trajectory — include, omit, or
   separate channel?
3. **Export layout** — adopt §7.1 as normative or wait for an official AHP
   export format from Microsoft/VS Code?
4. **.NET reducer** — port vs snapshot-only gap for Phase 2?
5. Should **`GroupId`** be the full URI (`ahp-chat:/uuid`) or bare uuid?
   Recommendation: **full URI** for global uniqueness across hosts.
6. Do we need a distinct source for **ACP session/update logs** later, or is
   AHP sufficient whenever a host is present?

---

## 14. Success criteria

AHP support is **done** for product advertising when:

1. Shared conformance cases for Phase 1 pass on **.NET, TypeScript, and Rust**.
2. `ahp` appears in `contracts/compatibility.json` → `implemented.sources`.
3. Runtime capability manifests advertise `ahp` with the same protocol pin.
4. README / adapter-authoring document export → list → normalize.
5. At least one real-world export path is documented (VS Code host or AHPX).

Live subscribe package is a separate success metric and must not block (1)–(5).

---

## 15. Suggested issue breakdown

| ID | Title | Depends |
| --- | --- | --- |
| AHP-0 | Land `contracts/spec/sources/ahp.md` + export schema + fixtures | — |
| AHP-1 | TS snapshot decoder + conformance | AHP-0 |
| AHP-2 | Rust snapshot decoder + conformance | AHP-0 |
| AHP-3 | .NET snapshot decoder + conformance | AHP-0 |
| AHP-4 | Advertise `ahp` in compatibility + CLIs + README | AHP-1..3 |
| AHP-5 | Action-log reduce (TS/Rust) + equivalence case | AHP-4 |
| AHP-6 | Export directory listing | AHP-4 |
| AHP-7 | Optional live export tool | AHP-6 |

---

## 16. References

- AHP docs: <https://microsoft.github.io/agent-host-protocol/>
- Spec overview, chat/session channels, transport, lifecycle, versioning
- Guide: state model, actions, reconciliation, AHP and ACP
- Schemas: `schema/state.schema.json`, `schema/actions.schema.json`
- Reducer fixtures: `types/test-cases/reducers/`
- Clients: TypeScript `@microsoft/agent-host-protocol`, Rust `ahp` / `ahp-types`,
  Go, Kotlin, Swift; servers: VS Code agent host
- Trajectory: [adapter-authoring.md](adapter-authoring.md), normalizer `0.2.0`

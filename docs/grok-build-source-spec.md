# Spec: Grok Build source in Trajectory

Status: **normative for implementation**  
Wire source: `grok-build`  
Native container: `grok-build-chat-history-jsonl`  
Reference tree: SpaceXAI Grok Build (`grok` CLI) session store under `~/.grok`

Related:

- [Architecture](architecture.md)
- [Adapter authoring](adapter-authoring.md)
- [Listing](../contracts/spec/listing.md)
- [Identity](../contracts/spec/identity.md)
- [Timestamps](../contracts/spec/timestamps.md)
- [Diagnostics](../contracts/spec/diagnostics.md)

---

## 1. Executive summary

**Grok Build** persists each agent session under:

```text
$GROK_HOME/sessions/<encode_cwd_dirname(cwd)>/<session-uuid>/
  chat_history.jsonl   # primary transcript (ConversationItem JSONL)
  summary.json         # listing metadata
  updates.jsonl        # ACP session/update stream (not v1 normalize input)
  events.jsonl         # telemetry (out of scope)
  …
```

Trajectory v1 treats **one session** as **one `chat_history.jsonl` byte stream**.
Listing discovers history files under an explicit sessions root and surfaces
session UUIDs, paths, titles, and update times.

```text
chat_history.jsonl  →  source adapter "grok-build"  →  shared normalizer  →  IR  →  projections
```

---

## 2. Wire vocabulary

| Field | Value |
| --- | --- |
| Wire source | `grok-build` |
| Display | Grok Build |
| CLI aliases (non-wire) | `grok` → `grok-build` |
| Native container | `grok-build-chat-history-jsonl` |
| Default list root | `$GROK_HOME/sessions` or `~/.grok/sessions` |
| Env override (samples) | `TRAJECTORY_GROK_BUILD_ROOT`, `GROK_HOME` |

Do not accept `xai`, `spacexai`, or bare `grok` on the wire `source` field.

---

## 3. Accepted container (v1)

### Shape — history JSONL only

Normalize input is the **exact UTF-8 bytes** of `chat_history.jsonl`:

- One JSON object per line (optional final newline).
- Each object is a `ConversationItem` tagged with `"type"` (`snake_case`).
- `chat_format_version` in `summary.json` is `1` for this pin; unknown future
  history fields must be ignored (forward compatible), not fatal.

**Not accepted as normalize input in v1:** session directory packs, `updates.jsonl`,
`events.jsonl`, or `ChatStateSnapshot` JSON.

Callers that list sessions should pass:

```text
source_context.group_id = <session-uuid from listing.id>
```

History lines do not embed the session id; without a supplied group id the
group falls through to the `default` sentinel (see identity contract).

---

## 4. Listing

See [listing.md](../contracts/spec/listing.md). Summary of Grok Build rules:

| Field | Rule |
| --- | --- |
| Discovery | `sessions/*/*/chat_history.jsonl` (cwd dir → session dir) |
| `id` | Session directory name (UUID string) |
| `path` | Absolute path to `chat_history.jsonl` |
| `updated_at` | `summary.json` `last_active_at` else `updated_at`; else history mtime UTC |
| `title` | `generated_title` else `session_summary` when present |
| `size_bytes` | History file length |
| Missing root | Empty page |
| Non-history files | Ignored |

CWD directory names may be URL-encoded paths or long-path `slug-blake3` forms;
listing does not need to decode CWD for identity.

---

## 5. Decode mapping

### 5.1 Session meta

Emit synthetic session meta with:

- `source` / source name: `grok-build`
- `model`: first non-empty `assistant.model_id` in file order (else omit)
- `cwd` / `git_branch`: omit from history-only input (not present on items)

### 5.2 Item types

| `type` | IR emission |
| --- | --- |
| `system` | `Message` with role **meta**, content = system string (recoverable prompt text) |
| `user` without `synthetic_reason` | `Message` role **user**; join text `ContentPart`s with `\n`; image parts retained when the runtime IR supports image content, else drop images with diagnostic `image_content_dropped` once per item |
| `user` with `synthetic_reason` | `Message` role **meta**, content = same joined text (not a human turn) |
| `assistant` | If non-empty `content`: `Message` role **assistant**. Then for each `tool_calls[]` entry: tool-call component with native `id`, `name`, `arguments` (JSON text as stored). Capture `model_id` for meta / model-invocation provenance when present. Do not invent usage or timing. |
| `tool_result` | Tool result linked by `tool_call_id`; content string; optional `images` same policy as user images |
| `reasoning` | `Message` role **reasoning**. Text = join `summary[].text` for `summary_text` parts (and any plain text content fields if present). Native id = reasoning `id` when non-empty. |
| `backend_tool_call` | Tool call with name from `kind.tool_type` (`web_search`, `x_search`, `code_interpreter`); id from nested `kind.id`; `status` is under `kind` (`kind.status`). Arguments = compact JSON `{"action": <kind.action>}` when `action` is present (else query/input/code fields). If `status` is `completed` or absent and no **later** matching `tool_result` (higher input line) exists for that id, emit a synthetic tool result with a short text summary of the backend call and diagnostic `backend_tool_result_synthesized` (content-safe message; **no** native tool-call id in the message). |
| missing / empty `type` | Ignore the line (not a diagnostic) |
| unknown non-empty `type` | Diagnostic `unknown_semantic_record`, skip line |
| invalid JSON / non-object | Diagnostic `invalid_json_line` / `non_object_json_line`, skip line |
| blank lines | Ignore |

Empty message content after trim is dropped (shared normalizer policy).

### 5.3 Encrypted reasoning (`encrypted_content`)

Default: **omit** encrypted blobs from projected content (privacy).

Optional include when `source_context.include_encrypted_reasoning` is JSON
boolean `true` (or string `"true"`):

- Append to reasoning text, after summary text:

  ```text
  <summary…>

  <encrypted_content>
  <raw encrypted string>
  </encrypted_content>
  ```

- If summary is empty and only encrypted is present, the body is the
  `<encrypted_content>…</encrypted_content>` block alone.
- Emit diagnostic `encrypted_reasoning_included` with `count` = number of
  reasoning items that included encrypted payloads (one aggregate diagnostic).

When the flag is false/absent and encrypted would have been the only content,
still drop the item if summary text is empty (no empty reasoning records).

### 5.4 Identity anchors

| Preference | Source |
| --- | --- |
| Native id | reasoning `id`; tool call `id`; tool_result uses call id via linking |
| Location | UTF-8 **byte** offset of the JSONL line start in the input buffer |
| Sequence | none required |

No per-line timestamps exist on history items. Shared timestamp policy
synthesizes body record times (`timestamps_synthesized`) from session start or
the contract default epoch when no anchors exist.

### 5.5 Partial mode

`chat_history.jsonl` is append-only until compaction rewrites the file. Partial
normalize accepts a byte slice with `source_context.partial = true` and optional
`base_byte_offset`. Compaction is not multi-file; the current file is the
post-compaction truth.

### 5.5.1 Live session streaming (LS-05)

Core `apply_append` / `apply_snapshot` for Grok Build:

- Stream re-normalize uses `partial=true` and `base_byte_offset=0` over the full
  committed complete-line prefix (oracle path).
- Incomplete lines remain in the stream pending buffer; they are not records.
- Post-compaction file that is shorter and not a pure prefix of the prior
  committed bytes → `reset-required` with reason `source-compacted`.
- Pure prefix shrink → `source-truncated`.
- Synthetic backend tool results (`backend_tool_result_synthesized`, content
  prefix `[backend …]`) are stream-record `status: "provisional"` until a later
  real `tool_result` re-normalize or `finish`.
- See [`streaming-file-sources.md`](streaming-file-sources.md).

### 5.6 Model invocations

When an `assistant` line carries `model_id` (and optional `model_fingerprint`,
`reasoning_effort`), record a model-invocation provenance entry for Hypabolic /
OTEL projections. Never invent token usage from history alone.

---

## 6. Diagnostics (source-local codes)

| Code | When |
| --- | --- |
| `invalid_json_line` | Line is not valid JSON |
| `non_object_json_line` | JSON value is not an object |
| `unknown_semantic_record` | Unknown `type` |
| `image_content_dropped` | Image `ContentPart` omitted |
| `backend_tool_result_synthesized` | Synthetic result for backend tool (message must not embed native ids) |
| `encrypted_reasoning_included` | Optional encrypted payloads projected |
| plus shared codes | `timestamps_synthesized`, tool linking, bounds, etc. |

---

## 7. Non-goals (v1)

- Reconstructing turns solely from `updates.jsonl`
- Live ACP / WebSocket client inside core packages
- Reading `auth.json`, credentials, or `ChatStateSnapshot.credentials`
- Worktree / memory / marketplace stores as trajectory sources
- Nesting subagent sessions into one IR (each session dir is its own trajectory)

---

## 8. Conformance

Minimum shared cases under `conformance/cases/grok-build/`:

| Case | Intent |
| --- | --- |
| `full` | system, user, reasoning (encrypted dropped), assistant+tools, tool_result, model |
| `tool-calls` | multiple tool calls / results |
| `backend-tools` | `backend_tool_call` + synthetic result |
| `synthetic-user` | `synthetic_reason` → meta messages |
| `encrypted-include` | `include_encrypted_reasoning: true` |
| `cleanup` | invalid / unknown lines |
| `listing` | declarative store pagination |
| `partial-chunk` | partial mode + base offset identity |

Privacy: synthetic ids/paths only; no real home directories or live transcripts.

---

## 9. Implementation checklist

- [x] Vocabulary in conformance + compatibility schemas
- [x] Listing rules in `contracts/spec/listing.md`
- [x] Shared cases + store fixture
- [x] .NET / TypeScript / Rust decode + list
- [x] CLI aliases and default roots
- [x] `compatibility.json` + runtime capability manifests only when all claiming runtimes pass
- [x] README source list

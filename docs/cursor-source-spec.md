# Spec: Cursor Agent source in Trajectory

Status: **normative for implementation**  
Wire source: `cursor`  
Native container: `cursor-agent-transcript-jsonl`  
Reference tree: Cursor Agent CLI (`agent` / `cursor-agent`, 2026.08.11-e8db854) under `~/.cursor`

Related:

- [Architecture](architecture.md)
- [Adapter authoring](adapter-authoring.md)
- [Listing](../contracts/spec/listing.md)
- [Identity](../contracts/spec/identity.md)
- [Timestamps](../contracts/spec/timestamps.md)
- [Diagnostics](../contracts/spec/diagnostics.md)
- [Streaming file sources](streaming-file-sources.md)
- Peer source: [Grok Build](grok-build-source-spec.md)

---

## 1. Executive summary

**Cursor Agent** (the `agent` / `cursor-agent` CLI, also used by Cursor IDE
Agent) persists each session as a JSONL transcript:

```text
~/.cursor/
  projects/<encoded-cwd>/
    repo.json
    agent-transcripts/<session-uuid>/<session-uuid>.jsonl   # primary transcript
  chats/<md5(cwd)>/<session-uuid>/
    meta.json     # listing title / timestamps (optional join)
    store.db      # encrypted blob store — not v1 normalize input
```

Trajectory v1 treats **one session** as **one agent-transcript JSONL byte
stream**. Listing discovers those files under an explicit Cursor home root and
surfaces session UUIDs, paths, titles, and update times.

```text
<session-uuid>.jsonl  →  source adapter "cursor"  →  shared normalizer  →  IR  →  projections
```

---

## 2. Wire vocabulary

| Field | Value |
| --- | --- |
| Wire source | `cursor` |
| Display | Cursor Agent |
| CLI aliases (non-wire) | `cursor-agent` → `cursor` |
| Native container | `cursor-agent-transcript-jsonl` |
| Default list root | `$CURSOR_HOME` when non-empty, else `~/.cursor` |
| Env override (samples) | `TRAJECTORY_CURSOR_ROOT`, `CURSOR_HOME` |

Do not accept `cursor-cli`, `anysphere`, or `cursor-composer` on the wire
`source` field.

---

## 3. Accepted container (v1)

### Shape — agent transcript JSONL only

Normalize input is the **exact UTF-8 bytes** of
`projects/<encoded-cwd>/agent-transcripts/<id>/<id>.jsonl`:

- One JSON object per line (optional final newline).
- Two observed top-level shapes (both valid in one file):

  1. **Turn line** — `{ "role": "user" | "assistant", "message": { "content": [ ...parts ] } }`
  2. **Control line** — `{ "type": "turn_ended", "status": "success" | "error", "error"?: string }`

- Content parts (v1 pin, observed on CLI 2026.08.11):

  | `type` | Fields |
  | --- | --- |
  | `text` | `text` (string) |
  | `tool_use` | `name` (string), `input` (object). **No native id.** |

**Not accepted as normalize input in v1:** `store.db` (encrypted),
`prompt_history.json`, `worker.log`, `repo.json`, MCP caches, terminals,
canvases, or Cursor IDE composer databases under
`~/Library/Application Support/Cursor`.

Callers that list sessions should pass:

```text
source_context.group_id = <session-uuid from listing.id>
```

Transcript lines do not embed the session id; without a supplied group id the
group falls through to the `default` sentinel (see identity contract).

### Observed v1 limitations (do not invent around them)

- **No tool results** in the JSONL. `tool_use` parts are call-only. Do not
  synthesize fake results. Shared linking will leave those calls unmatched
  (existing orphan/unmatched-call diagnostics apply).
- **No native record or tool-call ids.** Identity is location-based
  (JSONL line start byte + component index).
- **No per-line timestamps.** Shared timestamp policy synthesizes body times.
- **No model id** on transcript lines. Session `lastUsedModel` lives only in
  encrypted `store.db` / CLI meta — omit model unless a future transcript
  field appears. Never invent provider/model from filenames.

Unknown future fields on known objects are ignored (forward compatible), not
fatal.

---

## 4. Listing

See [listing.md](../contracts/spec/listing.md). Cursor rules:

| Field | Rule |
| --- | --- |
| Discovery | `projects/*/agent-transcripts/<session-id>/<session-id>.jsonl` under the Cursor home root (two extra directory levels after `projects/`) |
| `id` | Session directory name (UUID string). Filename stem must equal the directory name; otherwise skip the file |
| `path` | Absolute path to the `.jsonl` |
| `updated_at` | Matching `chats/*/<id>/meta.json` `updatedAtMs` (ms epoch → UTC); else transcript mtime |
| `title` | Matching `meta.json` `title` when non-empty; else bounded peek of first `role=user` text part (listing contract 64 KiB / 200 lines / 120 scalars) |
| `size_bytes` | Transcript file length |
| Missing root | Empty page |
| Non-transcript files | Ignored (`repo.json`, `worker.log`, `store.db`, terminals, MCP caches, …) |

`chats/<hash>/` is `md5(cwd)` on this CLI pin, but listing **must not** require
decoding CWD. Join meta by scanning `chats/*/<session-id>/meta.json` when that
tree exists under the same explicit root. A fixture that only has `projects/`
is valid — title then comes from the bounded peek.

Default sample-CLI root: `$CURSOR_HOME` or `~/.cursor`. Conformance **never**
reads the developer home; it uses a declarative store fixture whose root is
the temporary `$ROOT` (typically containing `projects/` and optional `chats/`).

---

## 5. Decode mapping

### 5.1 Session meta

Emit synthetic session meta with:

- `source` / source name: `cursor`
- `model`: omit (not present on the JSONL)
- `cwd` / `git_branch`: omit from transcript-only input

### 5.2 Line types

| Line | IR emission |
| --- | --- |
| `role=user` | `Message` role **user**. Join `text` parts with `\n`. Then ignore non-text user parts per §5.3 |
| `role=assistant` | If any non-empty joined `text`: `Message` role **assistant**. Then for each `tool_use` part: tool-call component with `name`, `arguments` = compact JSON of `input` (empty object `{}` if `input` missing/non-object). Native tool id is **absent** → location identity (`tool-call` component index) |
| `type=turn_ended` | **Skip** (control record). If `status` is `error`, emit diagnostic `turn_ended_error` once per such line (content-safe; **do not** embed `error` text, paths, or raw JSON) |
| missing both `role` and `type` | Ignore the line (not a diagnostic) |
| unknown non-empty `type` (and no `role`) | Diagnostic `unknown_semantic_record`, skip line |
| unknown `role` | Diagnostic `unknown_semantic_record`, skip line |
| invalid JSON / non-object | Diagnostic `invalid_json_line` / `non_object_json_line`, skip line |
| blank lines | Ignore |

Empty message content after trim is dropped (shared normalizer policy). An
assistant line that is only `tool_use` parts still emits the tool-call
components.

### 5.3 Content parts

| `type` | Rule |
| --- | --- |
| `text` | Include in the joined message body |
| `tool_use` | Tool-call as §5.2. `name` missing/empty → skip that part with diagnostic `tool_use_missing_name` |
| `image` / image-like | Drop with diagnostic `image_content_dropped` (once per source occurrence) |
| unknown non-empty part `type` | Diagnostic `unknown_content_part`, skip part |
| missing / empty part `type` | Ignore part |

### 5.4 Identity anchors

| Preference | Source |
| --- | --- |
| Native id | none on this pin |
| Location | UTF-8 **byte** offset of the JSONL line start in the input buffer; tool-call `component_index` distinguishes multiple `tool_use` parts on one line |
| Sequence | none required |

### 5.5 Partial mode

The transcript is append-only in normal use. Partial normalize accepts a byte
slice with `source_context.partial = true` and optional `base_byte_offset`.

### 5.5.1 Live session streaming (LS-05)

Core `apply_append` / `apply_snapshot` for Cursor:

- Stream re-normalize uses `partial=true` and `base_byte_offset=0` over the
  full committed complete-line prefix (oracle path).
- Incomplete lines remain in the stream pending buffer; they are not records.
- Pure prefix shrink → `source-truncated`.
- Non-prefix shorter rewrite → `source-replaced` (not `source-compacted`;
  compaction is Grok Build-only).
- Completed lines are `stable` while the stream is open; `final` on `finish`
  when `finalize_on_close` (default true). No Cursor-specific provisional
  records in v1 (there are no synthetic tool results).
- See [`streaming-file-sources.md`](streaming-file-sources.md).

Lock `group_id` from listing `id` (session UUID) when following from the sample
CLI, same reason as Grok Build: the filename is the session id and lines do
not carry it.

### 5.6 Model invocations

Do **not** invent model-invocation provenance from this container.

---

## 6. Diagnostics (source-local codes)

| Code | When |
| --- | --- |
| `invalid_json_line` | Line is not valid JSON |
| `non_object_json_line` | JSON value is not an object |
| `unknown_semantic_record` | Unknown `type` or `role` |
| `unknown_content_part` | Unknown content-part `type` |
| `tool_use_missing_name` | `tool_use` without a name |
| `image_content_dropped` | Image part omitted |
| `turn_ended_error` | `turn_ended` with `status=error` |
| plus shared codes | `timestamps_synthesized`, unmatched tool calls, bounds, etc. |

---

## 7. Non-goals (v1)

- Decoding encrypted `store.db` blobs
- Cursor IDE composer / bubble chats that are not `agent-transcripts` JSONL
- Reconstructing tool results that the JSONL does not persist
- Live Cursor API / ACP client inside core packages
- Reading `cli-config.json`, `authInfo`, API keys, or MCP server configs
- Nesting `Task` subagent sessions into one IR (each transcript file is its
  own trajectory)

---

## 8. Conformance

Minimum shared cases under `conformance/cases/cursor/`:

| Case | Intent |
| --- | --- |
| `full` | user text, assistant text + tools, `turn_ended` success |
| `tool-calls` | multiple `tool_use` on one assistant line; unmatched calls |
| `cleanup` | invalid JSON, non-object, unknown type/role/part, missing tool name, `turn_ended` error |
| `listing` | declarative store pagination (projects + optional chats meta) |
| `partial-chunk` | partial mode + base offset identity |
| `byte-identity` | location identity stable under prefix / offset |

Streaming (under `conformance/cases/streaming/`):

| Case | Intent |
| --- | --- |
| `cursor-append-sequence` | successive append-bytes ≡ prefix oracle (`stream-oracle-parity`) |

Listing store: `conformance/stores/cursor-pagination/` (and title variant if
needed) with synthetic UUIDs and `$ROOT` paths only.

Privacy: synthetic ids/paths only; no real home directories, emails, or live
transcripts.

---

## 9. Implementation slices

Clone the Grok Build / Claude Code adapter seams. Do not share IR types across
languages.

| Slice | Deliverable |
| --- | --- |
| CU-01 | This spec + vocabulary in schemas (`conformance-case-v1`, streaming schemas, compatibility-manifest). **Do not** claim `cursor` in `compatibility.json` / runtime-capabilities until CU-06 |
| CU-02 | Shared cases + listing store fixture + streaming append sequence (goldens generated from a trusted local build, **hand-reviewed**) |
| CU-03 | Decode in all four runtimes (Python `sources/cursor.py`, Rust `normalize.rs`, TS `internal.ts`, .NET `Adapters/Cursor/`) |
| CU-04 | Listing in all four runtimes + `contracts/spec/listing.md` Cursor bullet |
| CU-05 | Stream source switch + sample CLIs (`--source cursor`, alias `cursor-agent`, default `~/.cursor`, file-stream allow-list) + `streaming-file-sources.md` / `streaming-file-io.md` rows |
| CU-06 | Advertise: `compatibility.json`, runtime-capabilities, README, CHANGELOG Unreleased, `EXPECTED_SOURCES`, docs index — **only after** four-runtime conformance is green |

### File map (expected)

- `docs/cursor-source-spec.md` (this file)
- `docs/adapter-authoring.md`, `docs/architecture.md`, `docs/streaming-file-sources.md`, `docs/streaming-file-io.md`, `docs/streaming-core-api.md`
- `contracts/spec/listing.md`, `contracts/schemas/*`, `contracts/compatibility.json` (CU-06)
- `conformance/cases/cursor/**`, `conformance/stores/cursor-*/`, `conformance/cases/streaming/cursor-append-sequence/`
- Python: `sources/cursor.py`, `listing/cursor.py`, enums/exports/tests
- Rust: `normalize.rs` / `listing.rs` / `streaming.rs` / `model.rs`
- TypeScript: `internal.ts`, `index.ts`, `streaming.ts`, `trajectory-node` listing, CLIs
- .NET: `Adapters/Cursor/*`, `TrajectoryIR.cs`, stream switch, sample CLI, tests
- Sample CLIs in all four languages; `tools/validate_release_metadata.py`

---

## 10. Definition of done

A Cursor source is complete when:

1. Spec + schema vocabulary updated.
2. Shared normalize/list/stream cases pass on **every** runtime that advertises it.
3. Capability manifests agree (`cursor` in `implemented.sources` only after that).
4. Sample CLIs can `list` / `show` / `browse --watch` / `stream` with `--source cursor`.
5. Existing batch and stream conformance stays green; identity baseline updated only for new reviewed goldens.
6. No paths, secrets, raw lines, or native ids in diagnostics.

## 11. Implementation checklist

- [x] Vocabulary in conformance + compatibility schemas
- [x] Listing rules in `contracts/spec/listing.md`
- [x] Shared cases + store fixture + stream append sequence
- [x] .NET / TypeScript / Rust / Python decode + list
- [x] CLI aliases and default roots; file-stream allow-list
- [x] `compatibility.json` + runtime capability manifests only when all claiming runtimes pass
- [x] README source list + Unreleased changelog

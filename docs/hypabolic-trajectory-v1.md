# Hypabolic trajectory v1

Status: proposed contract for the first Trajectory.NET release.

Schema ID: `hypabolic-trajectory-v1`

## Purpose

The Hypabolic format is the loss-minimizing, provenance-rich output of Trajectory.NET. It is intended for Hypabolic memory, Context Compiler, Evidence Graph, replay, evaluation, and experience-processing pipelines.

It is deliberately separate from the internal C# IR:

- the IR may evolve through normal library versioning;
- the Hypabolic wire contract changes only through an explicit schema version;
- consumers do not need to reference Trajectory.NET assemblies;
- a format upgrade can be implemented as another output adapter without destabilizing source decoding.

The format retains the semantic records expected by Letta compatibility outputs while adding the provenance, identity, configuration, and diagnostics needed by downstream systems.

## Envelope

```json
{
  "schema_id": "hypabolic-trajectory-v1",
  "schema_version": 1,
  "trajectory_id": "sha256-hex",
  "source": {
    "type": "codex",
    "name": "codex",
    "group_id": "019...",
    "producer_version": "0.144.1"
  },
  "segment": {
    "partial": false,
    "base_byte_offset": 0
  },
  "normalizer": {
    "name": "Hypabolic.Trajectory",
    "version": "0.1.0"
  },
  "config": {
    "bounds": {
      "tool_arguments": { "max_characters": 20000 },
      "tool_results": {
        "max_characters": 2500,
        "strategy": "head-tail"
      }
    },
    "filters": { "tool_results": "include" }
  },
  "records": [],
  "diagnostics": []
}
```

### Envelope rules

- `schema_id` and `schema_version` are required constants.
- `trajectory_id` is deterministic for a logical source group and does not depend on transcript content, chunk boundaries, transport arrival order, or synthesized timestamps.
- `source.type` is the normalized source family.
- `source.name` is the adapter-reported source name and allows compatible aliases or future source variants.
- `source.group_id` is required in this format. Sources without a native group use the normalization core's deterministic group sentinel/derivation policy.
- `producer_version` is present only when the source exposes it.
- `segment` records the caller's chunking context. A non-zero `base_byte_offset` implies `partial: true`.
- `config` contains the fully resolved output-affecting configuration, including defaults.
- `records` preserve normalized semantic order. Consumers that combine chunks use each record's `source_order_id` and `component_index`.
- `diagnostics` is always present, including when empty.

## Record contract

Every record contains a common header:

```json
{
  "id": "sha256-hex",
  "kind": "message",
  "role": "assistant",
  "order": 12,
  "source_timestamp": "2026-07-24T12:00:00.000Z",
  "timestamp": "2026-07-24T12:00:00.000Z",
  "provenance": {
    "stable_source_record_id": "native-or-derived-id",
    "source_identity_kind": "native",
    "source_order_id": "1|2026-07-24T12:00:00.000Z|00000000000000000012|...",
    "component_key": "message:0",
    "component_index": 0,
    "component_type_ordinal": 0,
    "native_record_id": "optional-native-id",
    "source_sequence": 12,
    "source_offset": 8342,
    "source_anchor_kind": "byte"
  },
  "hashes": {
    "content_sha256": "sha256-hex",
    "record_sha256": "sha256-hex"
  }
}
```

Required common fields:

| Field | Meaning |
| --- | --- |
| `id` | Deterministic semantic record identity. Uses the same identity tuple as Letta canonical `record_id`. |
| `kind` | `meta`, `message`, `assistant_tool_calls`, or `tool_result`. |
| `role` | `meta`, `user`, `reasoning`, `assistant`, or `tool`. |
| `order` | Normalized order within the emitted segment. Descriptive, not a durable cross-chunk cursor. |
| `source_timestamp` | Source-native time when present; otherwise `null`. |
| `timestamp` | Final normalized time, including deterministic interpolation/synthesis; `null` only for meta. |
| `provenance` | Source identity, ordering, and component information. |
| `hashes` | Semantic content hash and complete record hash. |

Optional provenance fields are omitted rather than serialized as `null`. Required provenance fields are `stable_source_record_id`, `source_identity_kind`, `source_order_id`, `component_key`, `component_index`, and `component_type_ordinal`.

## Record variants

### Meta

```json
{
  "id": "...",
  "kind": "meta",
  "role": "meta",
  "order": -1,
  "source_timestamp": null,
  "timestamp": null,
  "source_name": "codex",
  "cwd": "/workspace",
  "git_branch": "main",
  "model": "gpt-5.6-codex",
  "producer_version": "0.144.1",
  "provenance": { "...": "..." },
  "hashes": { "...": "..." }
}
```

### Message

```json
{
  "id": "...",
  "kind": "message",
  "role": "reasoning",
  "order": 4,
  "source_timestamp": "2026-07-24T12:00:02.000Z",
  "timestamp": "2026-07-24T12:00:02.000Z",
  "content": "Inspect the repository structure.",
  "provenance": { "...": "..." },
  "hashes": { "...": "..." }
}
```

### Assistant tool calls

```json
{
  "id": "...",
  "kind": "assistant_tool_calls",
  "role": "assistant",
  "order": 5,
  "source_timestamp": "2026-07-24T12:00:03.000Z",
  "timestamp": "2026-07-24T12:00:03.000Z",
  "content": null,
  "tool_calls": [
    {
      "id": "call_1",
      "name": "exec_command",
      "arguments_json": "{\"cmd\":\"pwd\"}"
    }
  ],
  "provenance": { "...": "..." },
  "hashes": { "...": "..." }
}
```

Built-in adapters emit one semantic call per record in v1. The array is retained so compatible source events can be represented without a schema break and output adapters can regroup records where required.

### Tool result

```json
{
  "id": "...",
  "kind": "tool_result",
  "role": "tool",
  "order": 6,
  "source_timestamp": "2026-07-24T12:00:04.000Z",
  "timestamp": "2026-07-24T12:00:04.000Z",
  "tool_call_id": "call_1",
  "tool_name": "exec_command",
  "content": "/workspace",
  "is_error": false,
  "provenance": { "...": "..." },
  "hashes": { "...": "..." }
}
```

## Identity and hashing

`id` is computed as:

```text
sha256(canonical_json([
  source_group_id,
  stable_source_record_id,
  component_key
]))
```

`content_sha256` hashes a canonical semantic payload that excludes timestamps, normalized order, source offsets, transport metadata, and diagnostics.

`record_sha256` hashes the complete canonical JSON representation of the record except the `hashes` object itself. This avoids self-reference while detecting any other material record change.

Canonical JSON rules:

- UTF-8;
- object keys sorted by Unicode ordinal order;
- arrays retain semantic order;
- no insignificant whitespace;
- optional absent values are omitted rather than represented as `null`, except fields whose nullability is part of the contract;
- numbers use invariant JSON formatting;
- strings use standard JSON escaping without HTML-specific escaping.

## Diagnostics

```json
{
  "code": "tool_result_truncated",
  "message": "Truncated a tool result to the configured maximum.",
  "input_line": 18,
  "record_index": 7,
  "count": 1
}
```

Diagnostic messages never include transcript text, tool payloads, source identifiers, or sensitive paths. Code values are stable and additive.

## Relationship to Letta outputs

- `letta-trajectory-v1` is the strict compatibility projection and intentionally drops provenance, hashes, resolved configuration, and most source metadata.
- `letta-canonical-v1` retains canonical identity and flattened fields for Letta ingestion compatibility.
- `hypabolic-trajectory-v1` is the preferred Hypabolic interchange format and retains normalized semantic records directly rather than embedding a second serialized Letta record in `record_json`.

All three outputs are projected from the same IR. No output adapter reparses another output format.

## Evolution rules

- Additive optional fields may be introduced without changing `schema_version` only when existing consumers remain valid and canonical hashing rules are unaffected.
- New required fields, changed identity semantics, changed hashing semantics, or changed record variants require a new schema version.
- Diagnostic codes are additive within a schema version.
- Unknown `kind` values must be rejected by strict readers; readers may expose an explicit forward-compatible mode.
- A JSON Schema and generated C# serializer context are release requirements for v1.
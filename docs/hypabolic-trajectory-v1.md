# Hypabolic trajectory v1

Schema ID: `hypabolic-trajectory-v1`  
Schema file: [`contracts/schemas/hypabolic-trajectory-v1.schema.json`](../contracts/schemas/hypabolic-trajectory-v1.schema.json)

Hypabolic trajectory is Trajectory’s **provenance-rich** output: the same
normalized semantics and identity basis as the compact message and canonical
projections, plus fields needed for Hypabolic memory, Context Compilers,
Evidence Graphs, replay, evaluation, and experience pipelines.

## Design goals

1. Preserve tool calls, results, reasoning, and message structure after
   normalization.
2. Expose stable identity and component grouping without requiring consumers to
   re-derive them.
3. Retain source and producer metadata omitted by minimal compatibility
   projections.
4. Stay deterministic under the same normalizer contract version.

## Envelope (conceptual)

```json
{
  "schema_id": "hypabolic-trajectory-v1",
  "schema_version": 1,
  "trajectory_id": "…",
  "source": {
    "type": "pi",
    "name": "pi",
    "group_id": "session-id",
    "producer_version": "…"
  },
  "segment": {
    "partial": false,
    "base_byte_offset": 0
  },
  "normalizer": {
    "name": "Hypabolic.Trajectory",
    "version": "0.1.0"
  },
  "config": { },
  "records": [ ],
  "diagnostics": [ ]
}
```

## Records

Each record includes:

| Field | Purpose |
| --- | --- |
| `id` | Stable record identity |
| `kind` / `role` | Semantic kind and role |
| `order` | Normalized order |
| `content` / tool fields | Message or tool payload (policy-bounded) |
| `provenance` | Source identity, component index, anchors |
| `hashes` | Content and record digests where applicable |

Optional provenance fields are omitted rather than serialized as `null`.

## Relationship to other outputs

| Output | Relationship |
| --- | --- |
| Message trajectory array | Compact role/message view; less provenance |
| Canonical identity | Same identity basis; flatter identity-focused shape |
| OpenAI chat / minimal JSONL | Convenience projections for other ecosystems |
| OTEL GenAI spans | Observability projection; not a transcript substitute |

All identity-bearing projections share normalization policy and the normalizer
contract version. Changing identity under the same contract version is
forbidden.

## Validation

Validate instances against the checked-in JSON Schema. Conformance goldens under
`conformance/cases/**/expected.hypabolic.json` are the behavioural fixtures.

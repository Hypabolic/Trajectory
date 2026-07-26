# Identity and deterministic ordering

Contract version: normalizer `0.2.0`.

## Group resolution

The detected source group and `source_context.group_id` are compared with exact
ordinal string equality. If both exist and differ, normalization fails with
`source_group_conflict`. Otherwise the detected group wins, then the supplied
group, then the literal sentinel `default`.

`source_group_resolved` is true only when a detected or supplied group existed;
using `default` does not count. Canonical Codex projection fails with
`source_group_required` when this flag is false.

## Stable source record identity

The first applicable identity source wins:

1. non-empty native record ID: kind `native`;
2. source location: kind `location`;
3. source sequence: kind `location`;
4. deterministic semantic content fallback: kind `content`.

Byte locations are zero-based offsets in the original UTF-8 input. Only byte
anchors add `base_byte_offset`; ordinal, row, and sequence anchors do not.
Addition is checked signed 64-bit arithmetic. A non-zero base offset implies
partial mode.

Content fallback hashes:

```text
group + "|content|" + record_type + "|" + semantic_content_hash
  + "|" + decimal_component_index
```

Synthetic meta uses stable ID `meta`, component key `meta`, and identity kind
`synthetic`.

## Components and records

One source occurrence can emit multiple semantic components. Components retain
their source `component_index`. `component_type_ordinal` counts components of
the same semantic bucket within one source occurrence, starting at zero.

Component keys are:

- `meta`
- `message:<type-ordinal>`
- `reasoning:<type-ordinal>`
- `tool-call:<final-call-id>`
- `tool-result:<final-call-id>`
- `model-invocation`

The public record ID is the canonical-JSON SHA-256 tuple defined in
[canonical-json.md](canonical-json.md).

## Source order

Body source order IDs have this exact text shape:

```text
1|<source-time>|<20-column-sequence>|<stable-source-record-id>
```

Missing source time is `0000-00-00T00:00:00.001Z`. Missing sequence is zero.
Sequence is invariant decimal, left-padded with zeroes to at least 20
characters. Source time is normalized to UTC millisecond text. Meta uses:

```text
0|0000-00-00T00:00:00.000Z|00000000000000000000|meta
```

Normalized `order` is descriptive segment order (`-1` for meta, then zero
upward). It is not a durable cursor and is excluded from durable identity.

## Hashes

`content_sha256` hashes canonical JSON:

```json
{"content": <semantic-content>, "type": "<record-type>"}
```

`record_sha256` hashes the canonical Letta record JSON, excluding the hashes
object and all provenance. Timestamps therefore affect record hashes but not
content hashes. Transport offsets, arrival order, and diagnostics affect
neither.

Deterministic reruns must preserve record IDs, source order IDs, content
hashes, record hashes, projected ordering, and output bytes.

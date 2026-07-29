# Source decoding and normalization

Contract version: normalizer `0.2.0`.

## Input boundary

The normative input is an exact UTF-8 byte sequence. The current .NET string API
first encodes the string as UTF-8; future byte-oriented APIs must produce the
same bytes. JSONL line anchors are counted from the original bytes, including
each preceding line terminator. Lines are decoded independently. Empty or
whitespace-only lines are ignored.

Pi accepts native pi-coding-agent session, configuration, and message wrapper
rows. Claude Code accepts top-level user/assistant rows and documented content
blocks, with malformed, sidechain, injected, and transport-only cleanup.
Codex accepts rollout session metadata and response/event item rows, including
messages, reasoning, function/custom tools, outputs, web search, and tool-search
events. Source adapters preserve native ID, group, sequence, timestamp, byte
anchor, component position, producer, model, invocation, and usage metadata
when present. They do not apply shared bounds or project output fields.

JSON numbers used as source sequences, byte offsets, epoch timestamps, and
token counts must be lossless signed 64-bit integers. Fractional timestamps are
accepted only in timestamp formats documented by the source adapter.

## Modes and validation

Partial mode is active when `source_context.partial` is true or
`base_byte_offset` is non-zero. Whole mode requires at least one normalized
user and assistant-role record. Partial mode permits either to be absent and
permits a result whose call is in another chunk.

All results begin with synthetic meta in the private IR. Canonical output omits
meta when `base_byte_offset` is non-zero. message-trajectory and Hypabolic projections retain
the current implemented behavior.

## Tool linking

Tool calls are planned before results are emitted. Missing call IDs synthesize
`call_<one-based-decoded-event-index>`. Duplicate IDs are renamed in call order
to `<id>__2`, `<id>__3`, and so on. Results for an original duplicate ID consume
the planned calls in call order, independent of whether result rows arrive
before call rows.

A whole-mode result with no call is dropped as `orphan_tool_result`. Results
beyond the number of matching calls are dropped as `duplicate_tool_result`.
Partial mode retains a non-empty result ID as a cross-chunk result only when no
matching call exists in the chunk. `filters.tool_results = omit` removes linked
result records after link resolution.

Missing tool names become `unknown_tool`. Empty or invalid/non-object arguments
are represented as a JSON object with `_raw` content and the appropriate
diagnostic. Output call arguments are always a valid JSON object string.

## Bounds and truncation

Defaults are 20,000 Unicode scalar values for tool arguments and 2,500 for tool
results. `null` disables a bound. Positive integers are required; argument
limit 1 is invalid because `{}` must remain representable.

Lengths and slices count Unicode scalar values, never UTF-16 code units or
UTF-8 bytes. No Unicode normalization is performed.

Result truncation uses the marker:

```text
\n… [truncated, N more chars]
```

The marker is included in the configured limit. `head` keeps the maximum
possible prefix. `head-tail` gives an odd retained payload's extra scalar to
the head, then retains the suffix. `N` is the number of omitted input scalars.

Object argument shrinking operates deterministically over string leaves,
retains valid JSON, and falls back to a bounded `_raw` wrapper if the object
cannot fit. Existing behavior, including the 2,000-scalar preferred leaf floor,
is normative for version `0.2.0`.

## Noise and ordering

Known user noise prefixes are `<local-command-caveat>`, `<command-name>`,
`<command-message>`, `<local-command-stdout>`,
`<local-command-stderr>`, and `<task-notification`. Those records are removed
with `noise_record_dropped`.

Model selection for meta is the most frequent non-empty model across decoded
events, with ordinal model-name tie-break. Records retain decoded semantic
order. Durable source identity and `source_order_id` never use normalized
arrival order or synthesized timestamps.

Projection is a side-effect-free deterministic mapping from normalized IR and
options. No output adapter reparses another output.

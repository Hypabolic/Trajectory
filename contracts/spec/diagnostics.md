# Diagnostics and fatal errors

Contract version: `1`.

Diagnostics describe recoverable cleanup. Fatal errors abort an operation.
Both are typed compatibility surfaces.

## Diagnostic shape

Every diagnostic has `code` and `message`. `inputLine`, `recordIndex`, and
`count` are optional positive structural values. Output adapters may use the
documented casing of their target schema; the meanings do not change.

Stable diagnostic codes currently are:

- `invalid_json_line`
- `non_object_json_line`
- `injected_context_dropped`
- `noise_record_dropped`
- `sidechain_record_dropped`
- `unknown_semantic_record`
- `unknown_content_block`
- `tool_call_id_synthesized`
- `duplicate_tool_call_id`
- `orphan_tool_result`
- `duplicate_tool_result`
- `unknown_tool_name`
- `tool_arguments_reshaped`
- `tool_arguments_truncated`
- `tool_result_truncated`
- `timestamps_synthesized`
- `timestamps_interpolated`
- `image_content_dropped`
- `backend_tool_result_synthesized`
- `encrypted_reasoning_included`

Codes are additive and are never repurposed. Diagnostic ordering is source
decode order followed by normalization order. Counts and indexes are decimal
integers. Messages are stable explanatory text but consumers branch on codes.

## Content safety

A diagnostic or fatal error must not contain:

- transcript prose, reasoning, prompts, tool arguments, or tool results;
- raw source JSON or a parser excerpt;
- source-native identifiers or group identifiers, except deterministic
  normalization-generated tool-call IDs already present in the public output;
- developer or user filesystem paths;
- secret values from malformed records.

Line numbers, record indexes, counts, enum/source names, option names, configured
numeric limits, and fixed policy text are safe. Tests use sentinel secrets and
require that no diagnostic string or exception representation contains them.

## Fatal errors

The version-1 typed codes are:

- `invalid_input`
- `unknown_source`
- `unknown_output_schema`
- `missing_user_records`
- `missing_assistant_records`
- `invalid_normalized_transcript`
- `listing_unavailable`
- `source_group_conflict`
- `source_group_required`

Whole transcripts require at least one normalized user record and one
assistant-role record. Partial transcripts relax those two invariants and may
retain cross-chunk tool results. Canonical Codex projection requires a resolved
source group. A detected/provided group mismatch is always fatal.

Conformance responses report fatal errors as `{ "code", "message" }` and never
serialize a runtime exception type or stack trace.

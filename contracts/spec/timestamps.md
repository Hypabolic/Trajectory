# Timestamp contract

Contract version: normalizer `0.2.0`.

Source adapters parse documented ISO-8601/RFC-3339 text and documented integer
epoch values into an instant with an offset. Invalid source timestamps are
treated according to the source decoder's diagnostic policy. Offset-bearing
inputs identify the same instant regardless of their textual offset.

## Normalized timestamps

Source timestamps are preserved separately. Public Letta, canonical,
Hypabolic, minimal JSONL, and OpenTelemetry timestamps format normalized
instants in UTC with exactly three fractional digits:

```text
yyyy-MM-dd'T'HH:mm:ss.fff'Z'
```

Sub-millisecond precision is truncated by conversion to Unix milliseconds;
it is not rounded. Runtime locale and timezone never affect output.

Given body records and source-native anchors:

- no anchors: start at the source session creation instant, or
  `2026-01-01T00:00:00.000Z` when absent; assign records at 15-second steps and
  emit one `timestamps_synthesized` diagnostic;
- before the first anchor: assign one-second reverse steps;
- between anchors: linearly interpolate Unix milliseconds and truncate each
  result toward zero to a signed 64-bit integer;
- after the last anchor: assign one-second forward steps;
- emit one `timestamps_interpolated` diagnostic when any non-anchor record was
  filled.

Meta has null source and normalized timestamps.

Timestamp synthesis and interpolation do not participate in durable record ID
or source order identity. They do participate in the canonical Letta
`record_json` and therefore in `record_sha256`.

OpenTelemetry spans are emitted only for defensible source-native boundaries.
The projection does not fabricate model or workflow intervals from normalized
adjacent timestamps. Tool and agent intervals use linked record source times
and clamp end to start if the source end precedes the start.

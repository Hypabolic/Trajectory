# Canonical JSON contract

Contract version: normalizer `0.2.0`.

This document defines the JSON bytes used by Trajectory identity and hashing.
It is not RFC 8785/JCS. Changing these rules requires a new normalizer and
identity contract version.

## Algorithm

1. Parse the value as JSON without converting numbers through a binary
   floating-point type when the source decoder promises lossless integer
   handling.
2. Recursively sort object members by their property names using unsigned
   UTF-16 code-unit lexicographic order. This is JavaScript string ordering and
   .NET `StringComparer.Ordinal`. Rust implementations must compare each
   Unicode scalar's UTF-16 encoding, not UTF-8 bytes or scalar values.
3. Preserve array element order.
4. Emit compact UTF-8 JSON with no insignificant whitespace.
5. Emit JSON strings with the escaping produced by `System.Text.Json` and
   `JavaScriptEncoder.UnsafeRelaxedJsonEscaping`: quote, reverse solidus, and
   control characters are escaped; non-ASCII characters and HTML-sensitive
   characters are otherwise written as UTF-8.
6. Preserve explicit JSON `null`. Omit a value only where the containing wire
   contract says that the property is optional and absent.
7. Emit finite JSON numbers using invariant JSON formatting. Trajectory
   sequence, offset, timestamp, and token-count integers are signed 64-bit
   values and must be parsed losslessly. Values outside
   `[-9223372036854775808, 9223372036854775807]` are invalid input.

Canonical JSON never normalizes Unicode, replaces combining sequences, escapes
solidus, adds a byte-order mark, or appends a newline.

## Hash inputs

SHA-256 always consumes the exact UTF-8 bytes of the declared string or
canonical JSON value and is formatted as 64 lower-case hexadecimal characters.

Record identity is:

```text
sha256(utf8(canonical_json([
  source_group_id,
  stable_source_record_id,
  component_key
])))
```

Location fallback identity is the SHA-256 of the literal UTF-8 string:

```text
source_group_id + "|" + lower_case_anchor_kind + "|" + decimal_offset
```

Content and record hash envelopes are defined in
[identity.md](identity.md). Public JSON projections have their own declared
field order; canonical object sorting is used only where a specification says
`canonical_json`.

## Adversarial requirements

Conformance vectors must include:

- keys whose UTF-16 order differs from Unicode scalar and UTF-8 order;
- supplementary characters represented by surrogate pairs in UTF-16;
- combining sequences that must not be normalized;
- quote, reverse-solidus, newline, tab, NUL, and U+2028/U+2029 string content;
- signed 64-bit integer boundaries;
- explicit null and omitted optional fields;
- final-newline and no-final-newline projections.

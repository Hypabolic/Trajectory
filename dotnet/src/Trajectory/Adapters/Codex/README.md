# Codex source adapter

The Codex adapter consumes native append-only rollout JSONL and preserves each
semantic row's absolute UTF-8 byte anchor as its location identity. A rollout
session ID is resolved from `session_meta.payload.id` or caller-supplied source
context; canonical normalization rejects missing or conflicting group context.

The default lister walks `~/.codex/sessions/YYYY/MM/DD/*.jsonl` and returns
newest-first file metadata. Optional `title` is derived from a bounded early
scan that skips harness-injection user rows (see `contracts/spec/listing.md`).

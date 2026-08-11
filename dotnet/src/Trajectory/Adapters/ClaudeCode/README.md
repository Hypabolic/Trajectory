# Claude Code source adapter

The adapter accepts native Claude Code session JSONL from:

```text
~/.claude/projects/<project>/<sessionId>.jsonl
```

It structurally decodes user and assistant rows across the audited producer
families, including string content and `text`, `thinking`, `tool_use`,
`tool_result`, and image blocks. A resumed file may contain more than one
producer version; decoding does not select a version-specific parser.

Source-native line `uuid` values anchor identity. Rows without a UUID use their
absolute UTF-8 byte location, including the caller's `baseByteOffset` for
continuation chunks. Session IDs provide the source group.

The adapter silently drops documented transport/UI records and fallback
metadata. Sidechain and harness-noise rows produce the same diagnostics as the
shared Trajectory contracts. Unknown semantic rows and blocks produce
content-safe diagnostics so future source drift is visible.

Hypabolic output retains the earliest producer version as session metadata and
the originating producer version on each record's provenance. Compact message-trajectory outputs
omit this additional metadata.

Listing scans only top-level `*.jsonl` session files under each project
directory, newest first. Nested subagent files are excluded. Optional `title`
prefers `custom-title` / `ai-title` / `summary` records, else the first non-meta
user text (bounded peek; see `contracts/spec/listing.md`).

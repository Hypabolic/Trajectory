# Hermes source adapter

Hermes (`hermes-agent`) persists sessions in a SQLite store
(`~/.hermes/state.db`). Callers export one session as JSON: either the
message-row array for a session, or an envelope
`{"session": <sessions row>, "messages": [<message rows>]}`.

The adapter accepts both raw column values (JSON-string `tool_calls`,
`\u0000json:`-prefixed multimodal content, epoch-second timestamps) and their
decoded forms. Soft-deleted (`active = 0` / `false`) rows are excluded.
Rows are ordered by AUTOINCREMENT `id` when every row has a numeric id.

Wire source name is always `hermes`.

## Listing

`ListTrajectoriesAsync(TrajectorySource.Hermes)` resolves
`~/.hermes/state.db` (or a caller-supplied file/directory root). A missing
store yields an empty page. Full SQLite sessions-table enumeration is
optional and provider-side so the core package stays BCL-only and free of
SQLite dependencies. Normalization always operates on exported JSON bytes.

# hypabolic-trajectory-io

Optional file path poll/follow helpers for live session streaming (LS-09).

- Explicit `root` + `path` required (path must resolve under root)
- Polls growth; incomplete lines held at the host edge
- Calls core `apply_snapshot` / `apply_append` only
- Host I/O errors are `HostError` — not stream diagnostics
- Optional Cargo feature `watch` is **reserved / unimplemented**; poll-based
  follow is the supported path. When implemented, OS watchers will be wake-ups
  only (size + cursor remain authoritative).

See [docs/streaming-file-io.md](../../../docs/streaming-file-io.md).

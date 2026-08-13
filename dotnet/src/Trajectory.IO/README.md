# Hypabolic.Trajectory.IO

Optional file path poll/follow helpers for live session streaming (LS-09).

- Explicit `root` + `path` required (path must resolve under root)
- Polls growth; incomplete lines held at the host edge
- Calls core `ApplySnapshot` / `ApplyAppend` only
- Host I/O errors are `FileStreamHostException` — not stream diagnostics

See [docs/streaming-file-io.md](../../../docs/streaming-file-io.md).

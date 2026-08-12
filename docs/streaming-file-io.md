# Streaming file I/O (LS-09)

Optional packages that **poll or watch a single transcript path** and feed only
complete-line material into core stream apply APIs. Core packages remain free of
filesystem watchers, network, and SQLite.

Normative design: [`live-session-streaming.md`](live-session-streaming.md) §3, §10.  
Core apply API: [`streaming-core-api.md`](streaming-core-api.md).  
File JSONL semantics: [`streaming-file-sources.md`](streaming-file-sources.md).  
Wire contract: [`contracts/spec/streaming.md`](../contracts/spec/streaming.md).

## Packages

| Runtime | Package | Notes |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory.IO` | `FileTrajectoryStream`, `IAsyncEnumerable` follow |
| TypeScript | `@hypabolic/trajectory-node` | Extended with file follow helpers |
| Rust | `hypabolic-trajectory-io` | Poll by default; optional `watch` feature reserved/unimplemented |
| Python | `hypabolic-trajectory[io]` | Stdlib-only module `hypabolic_trajectory.io` |

Capability names `stream-file-io` / `stream-file-watch` / `stream-async-iterator`
are **vocabulary only** until LS-12. Do not advertise them in runtime manifests yet.

## Responsibility split

| Layer | Owns |
| --- | --- |
| Optional I/O | Explicit root + path validation; open/stat/read; host pending incomplete bytes; truncation detection; poll/watch wake-ups; cancellation |
| Core | Framing validation, `StreamState`, `apply_append` / `apply_snapshot`, cursor, snapshot+delta, reset discrimination, diagnostics |

I/O **only** calls core apply APIs. It never invents transcript diagnostics.

## Required options

| Field | Rule |
| --- | --- |
| `root` | **Required.** Absolute or resolvable directory that bounds the session file. No implicit `~` multi-session watch. |
| `path` | **Required.** File to follow. After resolve, must be **under** `root` (same-root containment). |
| `source` / stream options | JSONL file sources: `pi`, `claude-code`, `codex`, `openclaw`, `grok-build`. Passed through to core `StreamOptions`. |

Missing root or path → **host error** (not a stream update).

## Host algorithm

```text
open(options):
  validate root + path containment
  create core StreamState / TrajectoryStream
  file_offset = 0
  host_pending = empty
  first = true

poll():
  size = stat(path)                    # host I/O errors → HostError
  if size < file_offset:               # shrink / rewrite observed
    material = read(0..size)
    (complete, host_pending) = split_complete_lines(material)
    file_offset = size
    first = false
    return core.apply_snapshot(complete, source_revision=...)
  if first:
    material = read(0..size)
    (complete, host_pending) = split_complete_lines(material)
    file_offset = size
    first = false
    return core.apply_snapshot(complete, source_revision=...)
  if size > file_offset:
    chunk = read(file_offset..size)
    file_offset = size
    buf = host_pending + chunk
    (complete, host_pending) = split_complete_lines(buf)
    if complete is empty:
      return None / unchanged           # incomplete line held at host
    return core.apply_append(complete, source_revision=...)
  # optional periodic full-prefix reconcile → apply_snapshot
  return None
```

### Framing at the host edge

- Only **LF-terminated** bytes are forwarded to core on ordinary growth.
- Incomplete tails stay in **host** pending until the next complete line (or
  finish, which is caller-owned).
- Core still applies its own framing / buffer limits when material arrives.
- Incomplete lines are **never** turned into stream records by the I/O package.

### Truncation and rewrite

When `size < file_offset`, the helper re-reads the full file and calls
`apply_snapshot`. Core may return `kind=reset-required` (`source-truncated`,
`source-compacted`, `source-replaced`, …). The I/O package surfaces that
`StreamUpdate` unchanged; it does not auto-reset unless the caller configures
a higher-level policy later.

### Watchers

OS watchers (when enabled) are **wake-ups only**. Size + cursor remain
authoritative. Multiple events may coalesce into one `poll`.

### Follow / async iteration

`follow` / `FollowAsync` loops: wait (poll interval and/or watch wake-up) →
`poll` → yield non-empty updates until cancelled. Cancellation leaves the last
committed core cursor valid.

## Host errors vs stream diagnostics

| Outcome | Channel |
| --- | --- |
| Path outside root, missing root/path | Host error code (`path_outside_root`, `root_required`, `path_required`) |
| Permission denied, not found, other I/O | Host error code (`io_permission`, `io_not_found`, `io_error`) |
| Buffer limits, group conflict, source reset | Core `StreamUpdate` (`error` / `reset-required`) with content-safe diagnostics |

Host errors **must not** be written into `StreamUpdate.diagnostics` and **must
not** embed filesystem paths, secrets, or raw line content in messages intended
for transcript diagnostics. Host exception types may carry a path for the
**calling process** only (consumer UI / logs), never into stream wire objects.

Fixed host message strings used across runtimes:

| Code | Message (content-safe for shared tests) |
| --- | --- |
| `root_required` | File stream root is required. |
| `path_required` | File stream path is required. |
| `path_outside_root` | File stream path is outside the explicit root. |
| `io_permission` | File stream could not read the path (permission denied). |
| `io_not_found` | File stream path was not found. |
| `io_error` | File stream I/O failed. |

## API sketches

### Python (`hypabolic_trajectory.io`)

```python
from hypabolic_trajectory.io import FileStreamOptions, FileTrajectoryStream

fs = FileTrajectoryStream.open(
    FileStreamOptions(root=root, path=path, source="pi", group_id="s1")
)
update = fs.poll()          # StreamUpdate | None
for update in fs.follow(interval=0.05):
    ...
```

Install intent: `pip install hypabolic-trajectory[io]` (stdlib-only extra).

### TypeScript (`@hypabolic/trajectory-node`)

```ts
import { openFileStream, followFile } from "@hypabolic/trajectory-node";

const stream = openFileStream({ root, path, source: "pi", groupId: "s1" });
const update = await stream.poll();
for await (const update of followFile({ root, path, source: "pi" })) {
  ...
}
```

### .NET (`Hypabolic.Trajectory.IO`)

```csharp
await using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
{
    Root = root,
    Path = path,
    Source = TrajectorySource.Pi,
    GroupId = "s1",
});
var update = await stream.PollAsync(ct);
await foreach (var u in stream.FollowAsync(ct)) { ... }
```

### Rust (`hypabolic-trajectory-io`)

```rust
let mut stream = FileTrajectoryStream::open(FileStreamOptions {
    root, path, source: TrajectorySource::Pi, group_id: Some("s1".into()),
    ..Default::default()
})?;
if let Some(update) = stream.poll()? { ... }
```

## Privacy

- Stream diagnostics from core remain path-free.
- Fixtures use temporary directories only.
- Sample CLIs (LS-11) must default to content-hidden summaries.

## Tests (acceptance)

- Temp root: growth produces `updated` snapshots/deltas via core apply only.
- Incomplete line held: no new records until LF.
- Truncation: `reset-required` or coherent snapshot re-apply from core.
- Path outside root → host error.
- Permission denied → host error (not transcript diagnostic).
- Coalesced growth (multiple appends before poll) → single apply of complete segment.
- Core packages still have zero FS-watcher / network / SQLite imports for streaming modules.

## Not in this slice

- AHP network clients (LS-10)
- Sample `stream` CLI commands (LS-11)
- Capability advertising (LS-12)
- Hermes SQLite provider (LS-07h — see `docs/streaming-hermes-provider.md`)
- Multi-file home-directory supervisor

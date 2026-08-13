# Trajectory.Cli (.NET sample)

Local sample TUI for browsing agent sessions with [`Hypabolic.Trajectory`](../../src/Trajectory/).
**Not a published package** — depends on the workspace project reference.

## What it does

- Lists sessions from local agent stores (Pi, Claude Code, Codex, OpenClaw, Hermes, Grok Build)
- Normalizes AHP Shape A offline snapshots via `show --path` (no default store)
- Interactively pick a session, then Watch live or Show snapshot
- Prints privacy-safe summaries (record counts, roles, tool calls, diagnostics)
- Optional `--show-content` (prints a clear privacy warning)
- **`stream`**: follow a JSONL session file (optional file I/O → core stream apply)
- **`ahp-stream`**: demo optional AHP client with in-memory `fake://` FakeAhpHost

This sample is a **consumer process**, not a Trajectory daemon.

Default store roots (override with `--root` or env):

| Source | Default root | Env override |
| --- | --- | --- |
| `pi` | `~/.pi/agent` | `TRAJECTORY_PI_ROOT` or `PI_CODING_AGENT_DIR` |
| `claude-code` | `~/.claude/projects` | `TRAJECTORY_CLAUDE_CODE_ROOT` |
| `codex` | `~/.codex/sessions` | `TRAJECTORY_CODEX_ROOT` |
| `openclaw` | `~/.openclaw` if present, else `~/.clawdbot` | `TRAJECTORY_OPENCLAW_ROOT`, `OPENCLAW_STATE_DIR`, or `CLAWDBOT_STATE_DIR` |
| `hermes` | `~/.hermes` | `TRAJECTORY_HERMES_ROOT` |
| `ahp` | _(none — export file only)_ | _(not applicable)_ |
| `grok-build` (alias `grok`) | `$GROK_HOME/sessions` or `~/.grok/sessions` | `TRAJECTORY_GROK_BUILD_ROOT` or `GROK_HOME` |

Hermes core listing is SQLite-free and returns empty pages. Export message JSON from Hermes and use `show --path`.

AHP listing is Phase 3 (empty stub). Normalize Shape A snapshots with
`show --source ahp --path …` (same pattern as Hermes exports). AHP is in-tree /
next package version after published `0.1.0`.

## Prerequisites

- .NET SDK 10 (`net10.0`)
- Build the solution once so `Hypabolic.Trajectory` is available as a project reference

## Install / run

From the repository root:

```bash
dotnet run --project dotnet/samples/Trajectory.Cli -- list --source pi
dotnet run --project dotnet/samples/Trajectory.Cli -- show --source pi --path path/to/session.jsonl
dotnet run --project dotnet/samples/Trajectory.Cli -- browse
# or default interactive command:
dotnet run --project dotnet/samples/Trajectory.Cli

# Watch a live local session (pick, then Watch live)
dotnet run --project dotnet/samples/Trajectory.Cli -- browse --source grok-build --watch --show-content

# File stream (one-shot poll; default emit snapshot+delta)
dotnet run --project dotnet/samples/Trajectory.Cli -- stream \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl \
  --max-updates 1

# AHP stream demo (FakeAhpHost only in this sample)
dotnet run --project dotnet/samples/Trajectory.Cli -- ahp-stream \
  --url fake://demo \
  --chat ahp-chat:/00000000-0000-4000-8000-0000000000c1 \
  --actions-path conformance/cases/streaming/ahp-action-turn-flow/step-actions.jsonl \
  --max-updates 1
```

Against a conformance fixture:

```bash
dotnet run --project dotnet/samples/Trajectory.Cli -- show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl

dotnet run --project dotnet/samples/Trajectory.Cli -- show \
  --source ahp \
  --path conformance/cases/ahp/tool-calls/input.json
```

With content snippets (private data warning applies):

```bash
dotnet run --project dotnet/samples/Trajectory.Cli -- show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl \
  --show-content
```

## Commands

| Command | Purpose |
| --- | --- |
| `browse` (default) | Interactive source → session → Watch live or Show snapshot |
| `list` | Print a table of discovered sessions |
| `show` | Normalize one path/id and print summary |
| `stream` | Follow a JSONL file via optional file I/O + core stream |
| `ahp-stream` | Optional AHP client demo (`fake://` FakeAhpHost) |

Shared flags: `--source`, `--root`, `--limit`, `--show-content`.
`browse` accepts `--watch` (follow immediately), `--id`, `--emit`, `--interval`,
`--max-updates`.

Stream flags: `--emit snapshot+delta|snapshot|delta` (default `snapshot+delta`),
`--follow`, `--interval`, `--max-updates`, `--path` / `--id` (+ explicit `--root`
for listing). File stream sources: `pi`, `claude-code`, `codex`, `openclaw`,
`grok-build`.

AHP stream flags: `--url` (sample: `fake://…`), `--chat`, `--from-seq`,
`--token` / `TRAJECTORY_AHP_TOKEN`, `--snapshot-path`, `--actions-path`.

## Notes

- Empty stores print a friendly message and exit 0.
- Normalization failures surface typed `TrajectoryNormalizationException` codes without panicking.
- Stream follow is **not** a background daemon; process exit stops it.
- Sample `ahp-stream` uses in-memory FakeAhpHost only. Live WebSocket hosts:
  inject `IAhpTransport` in your app (`docs/ahp-client.md`).
- Sample is intentionally unpublished (`IsPackable=false`).

## Related

- Product overview and multi-runtime CLI matrix: [root README](../../../README.md#sample-clis-try-your-local-sessions)
- Contributing: [docs/contributing.md](../../../docs/contributing.md)

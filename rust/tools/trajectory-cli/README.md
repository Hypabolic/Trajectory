# trajectory-cli (Rust sample)

Local sample TUI for browsing agent sessions with
[`hypabolic-trajectory`](../../crates/hypabolic-trajectory/).

**Not published** (`publish = false`). Depends on the workspace crate path.

## What it does

- Lists sessions from local agent stores (Pi, Claude Code, Codex, OpenClaw, Hermes, Grok Build)
- Normalizes AHP Shape A offline snapshots via `show --path` (no default store)
- Interactive browse with `dialoguer` selection prompts
- Privacy-safe summaries (record counts, roles, tool calls, diagnostics)
- Optional `--show-content` with an explicit privacy warning
- **`stream`**: follow a JSONL session file (optional file I/O → core stream apply)
- **`ahp-stream`**: demo optional AHP client with in-memory `fake://` FakeAhpHost

This sample is a **consumer process**, not a Trajectory daemon.

| Source | Default root | Env override |
| --- | --- | --- |
| `pi` | `~/.pi/agent` | `TRAJECTORY_PI_ROOT` or `PI_CODING_AGENT_DIR` |
| `claude-code` | `~/.claude/projects` | `TRAJECTORY_CLAUDE_CODE_ROOT` |
| `codex` | `~/.codex/sessions` | `TRAJECTORY_CODEX_ROOT` |
| `openclaw` | `~/.openclaw` if present, else `~/.clawdbot` | `TRAJECTORY_OPENCLAW_ROOT`, `OPENCLAW_STATE_DIR`, or `CLAWDBOT_STATE_DIR` |
| `hermes` | `~/.hermes` | `TRAJECTORY_HERMES_ROOT` |
| `ahp` | _(none — export file only)_ | _(not applicable)_ |
| `grok-build` (alias `grok`) | `$GROK_HOME/sessions` or `~/.grok/sessions` | `TRAJECTORY_GROK_BUILD_ROOT` or `GROK_HOME` |

Hermes core listing is SQLite-free and returns empty pages. Export message JSON
from Hermes and use `show --path`.

AHP listing is Phase 3 (empty stub / not shipped). Normalize Shape A snapshots
with `show --source ahp --path …`. AHP is in-tree / next package version after
published `0.1.0`.

## Prerequisites

- Rust 1.85+ (see `rust/rust-toolchain.toml`)

## Install / run

From the repository `rust/` directory:

```bash
cargo run -p trajectory-cli -- list --source pi
cargo run -p trajectory-cli -- show \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl
cargo run -p trajectory-cli -- show \
  --source ahp \
  --path ../conformance/cases/ahp/tool-calls/input.json
cargo run -p trajectory-cli -- browse

# File stream (one-shot poll; default emit snapshot+delta)
cargo run -p trajectory-cli -- stream \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl \
  --max-updates 1

# AHP stream demo (FakeAhpHost only in this sample)
cargo run -p trajectory-cli -- ahp-stream \
  --url fake://demo \
  --chat 'ahp-chat:/00000000-0000-4000-8000-0000000000c1' \
  --actions-path ../conformance/cases/streaming/ahp-action-turn-flow/step-actions.jsonl \
  --max-updates 1
```

Release binary:

```bash
cargo build -p trajectory-cli --release
./target/release/trajectory list --source claude-code
```

With content snippets:

```bash
cargo run -p trajectory-cli -- show \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl \
  --show-content
```

## Commands

| Command | Purpose |
| --- | --- |
| `browse` (default) | Interactive source → session → summary |
| `list` | Print discovered sessions |
| `show` | Normalize one path/id |
| `stream` | Follow a JSONL file via optional file I/O + core stream |
| `ahp-stream` | Optional AHP client demo (`fake://` FakeAhpHost) |

Shared flags: `--source`, `--root`, `--limit`, `--show-content`.

Stream flags: `--emit snapshot+delta|snapshot|delta` (default `snapshot+delta`),
`--follow`, `--interval`, `--max-updates`, `--path` / `--id` (+ explicit `--root`
for listing). File stream sources: `pi`, `claude-code`, `codex`, `openclaw`,
`grok-build`.

AHP stream flags: `--url` (sample: `fake://…`), `--chat`, `--from-seq`,
`--token` / `TRAJECTORY_AHP_TOKEN`, `--snapshot-path`, `--actions-path`.

## Notes

- Empty stores exit 0 with a clear message.
- Typed `TrajectoryError` codes are printed without panicking.
- Stream follow is **not** a background daemon; process exit stops it.
- Sample `ahp-stream` uses in-memory FakeAhpHost only. Live WebSocket hosts:
  inject `AhpTransport` in your app (`docs/ahp-client.md`).
- Built with `clap` + `dialoguer` over the workspace library.

## Related

- Product overview and CLI matrix: [root README](../../../README.md#sample-clis-try-your-local-sessions)
- Contributing: [docs/contributing.md](../../../docs/contributing.md)

# @hypabolic/trajectory-cli (TypeScript sample)

Local sample CLI/TUI for browsing agent sessions with
[`@hypabolic/trajectory`](../trajectory/) and
[`@hypabolic/trajectory-node`](../trajectory-node/).

**Private / unpublished** (`"private": true`). Depends only on workspace packages.

## What it does

- Lists sessions from local agent stores (Pi, Claude Code, Codex, OpenClaw, Hermes, Grok Build)
- Normalizes AHP Shape A offline snapshots via `show --path` (no default store)
- Interactive browse: pick source → pick session → Watch live or Show snapshot
- Summary includes record counts, roles, tool calls, diagnostics, and message/Hypabolic projections
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

Hermes listing in the core Node package is SQLite-free and returns empty pages.
Export message JSON from Hermes and use `show --path`.

AHP listing is Phase 3 (empty stub). Normalize Shape A snapshots with
`show --source ahp --path …`. AHP is in-tree / next package version after
published `0.1.0` (not in registry `0.1.0`).

## Prerequisites

- Node.js 22+
- Workspace install from `typescript/`

## Install / run

From the repository `typescript/` directory:

```bash
npm install
npm run build
node packages/trajectory-cli/dist/cli.js list --source pi
node packages/trajectory-cli/dist/cli.js show \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl
node packages/trajectory-cli/dist/cli.js show \
  --source ahp \
  --path ../conformance/cases/ahp/tool-calls/input.json
node packages/trajectory-cli/dist/cli.js browse

# Watch a live local session (pick, then Watch live)
node packages/trajectory-cli/dist/cli.js browse --source grok-build --watch --show-content

# File stream (one-shot poll; default emit snapshot+delta)
node packages/trajectory-cli/dist/cli.js stream \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl \
  --max-updates 1

# AHP stream demo (FakeAhpHost only in this sample)
node packages/trajectory-cli/dist/cli.js ahp-stream \
  --url fake://demo \
  --chat 'ahp-chat:/00000000-0000-4000-8000-0000000000c1' \
  --actions-path ../conformance/cases/streaming/ahp-action-turn-flow/step-actions.jsonl \
  --max-updates 1
```

Or via the package script after build:

```bash
npm run trajectory -w @hypabolic/trajectory-cli -- list --source claude-code
```

With content snippets:

```bash
node packages/trajectory-cli/dist/cli.js show \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl \
  --show-content
```

## Commands

| Command | Purpose |
| --- | --- |
| `browse` (default) | Interactive source → session → Watch live or Show snapshot |
| `list` | Print discovered sessions |
| `show` | Normalize one path/id |
| `stream` | Follow a JSONL file via optional file I/O + core stream |
| `ahp-stream` | Optional AHP client demo (`fake://` FakeAhpHost) |

Shared flags: `--source`, `--root`, `--limit`, `--show-content`, `--format`.
`browse` accepts `--watch` (follow immediately), `--id`, `--emit`, `--interval`,
`--max-updates`.

Stream flags: `--emit snapshot+delta|snapshot|delta` (default `snapshot+delta`),
`--follow`, `--interval`, `--max-updates`, `--path` / `--id` (+ explicit `--root`
for listing). File stream sources: `pi`, `claude-code`, `codex`, `openclaw`,
`grok-build`.

AHP stream flags: `--url` (sample: `fake://…`), `--chat`, `--from-seq`,
`--token` / `TRAJECTORY_AHP_TOKEN`, `--snapshot-path`, `--actions-path`.

## Dependencies

Kept light on purpose: Node built-ins (`readline`, `fs`, ANSI escapes) plus the
workspace Trajectory packages (`trajectory`, `trajectory-node`, `trajectory-ahp`).
No ink/ink-heavy UI stack.

## Notes

- Empty stores exit 0 with a clear message.
- Typed `TrajectoryNormalizationError` codes are printed without stack dumps by default.
- Stream follow is **not** a background daemon; process exit stops it.
- Sample `ahp-stream` uses in-memory FakeAhpHost only. Live WebSocket hosts:
  inject `AhpTransport` in your app (`docs/ahp-client.md`).
- Not part of the published npm surface for v1.

## Related

- Product overview and CLI matrix: [root README](../../../README.md#sample-clis-try-your-local-sessions)
- Contributing: [docs/contributing.md](../../../docs/contributing.md)

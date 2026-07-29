# @hypabolic/trajectory-cli (TypeScript sample)

Local sample CLI/TUI for browsing agent sessions with
[`@hypabolic/trajectory`](../trajectory/) and
[`@hypabolic/trajectory-node`](../trajectory-node/).

**Private / unpublished** (`"private": true`). Depends only on workspace packages.

## What it does

- Lists sessions from local agent stores (Pi, Claude Code, Codex, OpenClaw, Hermes)
- Interactive browse: pick source → pick session → privacy-safe summary
- Summary includes record counts, roles, tool calls, diagnostics, and Letta/Hypabolic projections
- Optional `--show-content` with an explicit privacy warning

| Source | Default root | Env override |
| --- | --- | --- |
| `pi` | `~/.pi/agent` | `TRAJECTORY_PI_ROOT` or `PI_CODING_AGENT_DIR` |
| `claude-code` | `~/.claude/projects` | `TRAJECTORY_CLAUDE_CODE_ROOT` |
| `codex` | `~/.codex/sessions` | `TRAJECTORY_CODEX_ROOT` |
| `openclaw` | `~/.openclaw` | `TRAJECTORY_OPENCLAW_ROOT` or `OPENCLAW_STATE_DIR` |
| `hermes` | `~/.hermes` | `TRAJECTORY_HERMES_ROOT` |

Hermes listing in the core Node package is SQLite-free and returns empty pages.
Export message JSON from Hermes and use `show --path`.

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
node packages/trajectory-cli/dist/cli.js browse
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
| `browse` (default) | Interactive source → session → summary |
| `list` | Print discovered sessions |
| `show` | Normalize one path/id |

Shared flags: `--source`, `--root`, `--limit`, `--show-content`, `--format`.

## Dependencies

Kept light on purpose: Node built-ins (`readline`, `fs`, ANSI escapes) plus the
workspace Trajectory packages. No ink/ink-heavy UI stack.

## Notes

- Empty stores exit 0 with a clear message.
- Typed `TrajectoryNormalizationError` codes are printed without stack dumps by default.
- Not part of the published npm surface for v1.

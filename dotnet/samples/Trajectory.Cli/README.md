# Trajectory.Cli (.NET sample)

Local sample TUI for browsing agent sessions with [`Hypabolic.Trajectory`](../../src/Trajectory/).
**Not a published package** — depends on the workspace project reference.

## What it does

- Lists sessions from local agent stores (Pi, Claude Code, Codex, OpenClaw, Hermes, Grok Build)
- Interactively pick a session and normalize it
- Prints privacy-safe summaries (record counts, roles, tool calls, diagnostics)
- Optional `--show-content` (prints a clear privacy warning)

Default store roots (override with `--root` or env):

| Source | Default root | Env override |
| --- | --- | --- |
| `pi` | `~/.pi/agent` | `TRAJECTORY_PI_ROOT` or `PI_CODING_AGENT_DIR` |
| `claude-code` | `~/.claude/projects` | `TRAJECTORY_CLAUDE_CODE_ROOT` |
| `codex` | `~/.codex/sessions` | `TRAJECTORY_CODEX_ROOT` |
| `openclaw` | `~/.openclaw` if present, else `~/.clawdbot` | `TRAJECTORY_OPENCLAW_ROOT`, `OPENCLAW_STATE_DIR`, or `CLAWDBOT_STATE_DIR` |
| `hermes` | `~/.hermes` | `TRAJECTORY_HERMES_ROOT` |
| `grok-build` (alias `grok`) | `$GROK_HOME/sessions` or `~/.grok/sessions` | `TRAJECTORY_GROK_BUILD_ROOT` or `GROK_HOME` |

Hermes core listing is SQLite-free and returns empty pages. Export message JSON from Hermes and use `show --path`.

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
```

Against a conformance fixture:

```bash
dotnet run --project dotnet/samples/Trajectory.Cli -- show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl
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
| `browse` (default) | Interactive source → session → summary flow |
| `list` | Print a table of discovered sessions |
| `show` | Normalize one path/id and print summary |

Shared flags: `--source`, `--root`, `--limit`, `--show-content`.

## Notes

- Empty stores print a friendly message and exit 0.
- Normalization failures surface typed `TrajectoryNormalizationException` codes without panicking.
- Sample is intentionally unpublished (`IsPackable=false`).

## Related

- Product overview and multi-runtime CLI matrix: [root README](../../../README.md#sample-clis-try-your-local-sessions)
- Contributing: [docs/contributing.md](../../../docs/contributing.md)

# trajectory_cli (Python sample)

Local sample TUI for browsing agent sessions with
[`hypabolic-trajectory`](../../src/hypabolic_trajectory/).

**Not published** — not a console script and not installed by the wheel.
`samples/` is excluded from sdist. Depends on `PYTHONPATH` (or an editable
install of the core package).

## What it does

- Lists sessions from local agent stores (Pi, Claude Code, Codex, OpenClaw, Hermes, Grok Build, Cursor Agent)
- Normalizes AHP Shape A offline snapshots via `show --path` (no default store)
- Interactive browse with numbered selection prompts, then Watch live or Show snapshot
- Privacy-safe summaries (record counts, roles, tool calls, diagnostics)
- Optional `--show-content` with an explicit privacy warning
- **`stream`**: follow a JSONL session file (optional file I/O → core stream apply)
- **`ahp-stream`**: demo optional AHP client with in-memory `fake://` FakeAhpHost

This sample is a **consumer process**, not a Trajectory daemon. The library owns
pure stream apply; the CLI owns lifetime, explicit roots, and AHP transport.

| Source | Default root | Env override |
| --- | --- | --- |
| `pi` | `~/.pi/agent` | `TRAJECTORY_PI_ROOT` or `PI_CODING_AGENT_DIR` |
| `claude-code` | `~/.claude/projects` | `TRAJECTORY_CLAUDE_CODE_ROOT` |
| `codex` | `~/.codex/sessions` | `TRAJECTORY_CODEX_ROOT` |
| `openclaw` | `~/.openclaw` if present, else `~/.clawdbot` | `TRAJECTORY_OPENCLAW_ROOT`, `OPENCLAW_STATE_DIR`, or `CLAWDBOT_STATE_DIR` |
| `hermes` | `~/.hermes` | `TRAJECTORY_HERMES_ROOT` |
| `ahp` | _(none — export file only)_ | _(not applicable)_ |
| `grok-build` (alias `grok`) | `$GROK_HOME/sessions` or `~/.grok/sessions` | `TRAJECTORY_GROK_BUILD_ROOT` or `GROK_HOME` |
| `cursor` (alias `cursor-agent`) | `$CURSOR_HOME` or `~/.cursor` | `TRAJECTORY_CURSOR_ROOT` or `CURSOR_HOME` |

Hermes core listing is SQLite-free and returns empty pages. Export message JSON
from Hermes and use `show --path`.

AHP listing is Phase 3 (empty stub). Normalize Shape A snapshots with
`show --source ahp --path …`.

## Prerequisites

- Python 3.11+
- Core package importable (`pip install -e './python[dev]'` or set `PYTHONPATH`)

## Install / run

From the repository root:

```bash
# Editable install (recommended for development):
python -m pip install -e './python[dev]'

PYTHONPATH=python/samples python -m trajectory_cli list --source pi
PYTHONPATH=python/samples python -m trajectory_cli show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl
PYTHONPATH=python/samples python -m trajectory_cli show \
  --source ahp \
  --path conformance/cases/ahp/tool-calls/input.json
PYTHONPATH=python/samples python -m trajectory_cli browse

# Watch a live local session (pick, then Watch live)
PYTHONPATH=python/samples python -m trajectory_cli browse --source grok-build --watch --show-content

# File stream (one-shot poll; default emit snapshot+delta)
PYTHONPATH=python/samples python -m trajectory_cli browse \
  --source grok-build --watch --show-content
PYTHONPATH=python/samples python -m trajectory_cli stream \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl \
  --max-updates 1

# AHP stream demo (FakeAhpHost only in this sample)
PYTHONPATH=python/samples python -m trajectory_cli ahp-stream \
  --url fake://demo \
  --chat 'ahp-chat:/00000000-0000-4000-8000-0000000000c1' \
  --actions-path conformance/cases/streaming/ahp-action-turn-flow/step-actions.jsonl \
  --max-updates 1
```

Without editable install, put both `src` and `samples` on `PYTHONPATH`:

```bash
PYTHONPATH=python/src:python/samples python -m trajectory_cli list --source codex
```

With content snippets:

```bash
PYTHONPATH=python/samples python -m trajectory_cli show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl \
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

Shared flags: `--source`, `--root`, `--limit`, `--show-content`.
`show` also accepts `--format both|messages|hypabolic` and `--path` / `--id`.
`browse` accepts `--watch` (follow immediately), `--id`, `--emit`, `--interval`,
`--max-updates`.

Stream flags: `--emit snapshot+delta|snapshot|delta` (default `snapshot+delta`),
`--follow`, `--interval`, `--max-updates`, `--path` / `--id` (+ explicit `--root`
for listing resolution). File stream sources: `pi`, `claude-code`, `codex`,
`openclaw`, `grok-build`, `cursor`.

AHP stream flags: `--url` (sample: `fake://…`), `--chat`, `--from-seq`,
`--token` / `TRAJECTORY_AHP_TOKEN`, `--snapshot-path`, `--actions-path`.

## Notes

- Empty stores exit 0 with a clear message.
- Typed `TrajectoryError` codes are printed without a traceback.
- Default-home discovery is sample-CLI only; library `list_trajectories` always
  takes an explicit root.
- Stream follow is **not** a background daemon; Ctrl-C / process exit stops it.
- Sample `ahp-stream` uses in-memory FakeAhpHost only. Live WebSocket hosts:
  inject `AhpTransport` in your app (`docs/ahp-client.md`).
- Intentionally unpublished (no `[project.scripts]` entry).

## Related

- Product overview and CLI matrix: [root README](../../../README.md#sample-clis-try-your-local-sessions)
- Contributing: [docs/contributing.md](../../../docs/contributing.md)

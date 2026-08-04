# trajectory_cli (Python sample)

Local sample TUI for browsing agent sessions with
[`hypabolic-trajectory`](../../src/hypabolic_trajectory/).

**Not published** — not a console script and not installed by the wheel.
`samples/` is excluded from sdist. Depends on `PYTHONPATH` (or an editable
install of the core package).

## What it does

- Lists sessions from local agent stores (Pi, Claude Code, Codex, OpenClaw, Hermes)
- Normalizes AHP Shape A offline snapshots via `show --path` (no default store)
- Interactive browse with numbered selection prompts
- Privacy-safe summaries (record counts, roles, tool calls, diagnostics)
- Optional `--show-content` with an explicit privacy warning

| Source | Default root | Env override |
| --- | --- | --- |
| `pi` | `~/.pi/agent` | `TRAJECTORY_PI_ROOT` or `PI_CODING_AGENT_DIR` |
| `claude-code` | `~/.claude/projects` | `TRAJECTORY_CLAUDE_CODE_ROOT` |
| `codex` | `~/.codex/sessions` | `TRAJECTORY_CODEX_ROOT` |
| `openclaw` | `~/.openclaw` if present, else `~/.clawdbot` | `TRAJECTORY_OPENCLAW_ROOT`, `OPENCLAW_STATE_DIR`, or `CLAWDBOT_STATE_DIR` |
| `hermes` | `~/.hermes` | `TRAJECTORY_HERMES_ROOT` |
| `ahp` | _(none — export file only)_ | _(not applicable)_ |

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
| `browse` (default) | Interactive source → session → summary |
| `list` | Print discovered sessions |
| `show` | Normalize one path/id |

Shared flags: `--source`, `--root`, `--limit`, `--show-content`.
`show` also accepts `--format both|messages|hypabolic` and `--path` / `--id`.

## Notes

- Empty stores exit 0 with a clear message.
- Typed `TrajectoryError` codes are printed without a traceback.
- Default-home discovery is sample-CLI only; library `list_trajectories` always
  takes an explicit root.
- Intentionally unpublished (no `[project.scripts]` entry).

## Related

- Product overview and CLI matrix: [root README](../../../README.md#sample-clis-try-your-local-sessions)
- Contributing: [docs/contributing.md](../../../docs/contributing.md)

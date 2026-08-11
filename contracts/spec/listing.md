# Listing contract

Contract version: `1`.

Listing is an explicit operation separate from transcript decoding.

## Request and result

Inputs are source, optional root, optional opaque cursor, and limit. The default
limit is 50; valid values are 1 through 1000 inclusive. An invalid limit or
cursor is `invalid_input`.

Each item contains a source-native ID, native filesystem locator, optional UTC
update time, optional title, and optional signed 64-bit byte size. Results sort
by update time descending and then ID using ordinal comparison. Paths remain
native locators; they are not slash-normalized for identity.

The cursor is opaque to callers. Version 1 encodes the previous item ID and
zero-based absolute index. If the cursor item disappeared, pagination resumes
at `min(previous_index + 1, current_count)`. A page has a next cursor only when
additional items remain.

## Source discovery

- Pi: `<agent-root>/sessions/<project>/*.jsonl`, one project-directory level.
  The default agent root is `PI_CODING_AGENT_DIR` when non-empty, otherwise
  `~/.pi/agent`.
- Claude Code: `<root>/<project>/*.jsonl`, one project-directory level. The
  default root is `~/.claude/projects`.
- Codex: `*.jsonl` recursively under the root to four directory levels. The
  default root is `~/.codex/sessions`.
- OpenClaw: `<state-root>/agents/<agentId>/sessions/*.jsonl`, one sessions
  directory level under each agent. The default state root is
  `OPENCLAW_STATE_DIR` or legacy `CLAWDBOT_STATE_DIR` when non-empty; otherwise
  `~/.openclaw` when present, else legacy `~/.clawdbot`.
- Hermes: SQLite store at `~/.hermes/state.db` (or a caller-supplied `.db` path
  or directory containing `state.db`). Item IDs are session IDs from the
  `sessions` table; `path` is the store locator used when exporting message
  rows. Core packages stay SQLite-free, so missing stores list as empty and
  full sessions-table enumeration is optional/provider-side.
- Grok Build: `<sessions-root>/<cwd-dir>/<session-id>/chat_history.jsonl`, two
  directory levels under the sessions root (URL-encoded or slug-hash CWD dir,
  then session UUID). The default sessions root is `$GROK_HOME/sessions` when
  `GROK_HOME` is non-empty, otherwise `~/.grok/sessions`. Item IDs are the
  session directory name (UUID). Prefer `summary.json` fields for `title`
  (`generated_title` then `session_summary`) and `updated_at`
  (`last_active_at` then `updated_at`); otherwise use the history file mtime.
  Ignore non-session files (locks, `events.jsonl`, `updates.jsonl`, etc.).

## Optional title derivation

Title is optional. When a lister can derive one cheaply it should populate
`title`; otherwise omit it. Derivation is a **bounded peek** (at most the first
64 KiB or 200 lines of a transcript, whichever comes first) and must not run a
full normalize. Titles are single-line, whitespace-collapsed, and truncated to
at most 120 Unicode scalars. They may contain user text (local picker metadata);
do not put secrets into diagnostics.

Per-source rules:

- **Grok Build:** `summary.json` `generated_title`, else `session_summary`
  (unchanged; no transcript scan).
- **Codex:** scan early rollout records. Prefer the first `response_item` with
  `payload.role === "user"` whose extracted text is not harness injection.
  Noise includes markers such as `# AGENTS.md`, `<INSTRUCTIONS>`,
  `<environment_context>`, `<skills_instructions>`, dense XML tag blocks, and
  developer/system roles. Fallback: short `session_meta.payload.id` when present.
- **Claude Code:** prefer the first non-empty of `custom-title` (`customTitle`),
  `ai-title` (`aiTitle`), or `summary` (`summary`) within the bound; otherwise
  the first non-meta, non-sidechain `user` message text that is not harness noise.
- **Pi / OpenClaw (optional):** first non-noise user message text within the
  bound when cheaply available.

Missing stores return an empty page. Inaccessible or concurrently removed
subtrees are skipped. A source without an installed lister fails with
`listing_unavailable`.

## Conformance safety

Every listing case supplies a declarative store fixture. The verifier creates
a fresh temporary root, writes only declared files, applies declared UTC
timestamps, invokes listing with that explicit root, and removes the root.
Runners must never read a developer or CI user's home directory in
conformance mode.

Expected paths use `$ROOT` as a portable placeholder. A runner replaces the
actual temporary root prefix before comparison.

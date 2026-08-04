"""Local sample CLI for browsing agent sessions with Trajectory.

Not published — depends on an editable / PYTHONPATH install of
``hypabolic_trajectory``. Default home discovery lives here only; the library
listing API always requires an explicit root.

Commands match peer sample CLIs (.NET / TypeScript / Rust):
  browse (default)  interactive source → session → summary
  list              table of sessions for one source
  show              normalize one --path or listing --id
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Final, Literal, Sequence

from hypabolic_trajectory import (
    NormalizeRequest,
    TrajectoryError,
    TrajectoryListing,
    list_trajectories,
    normalize_to_ir,
    project_hypabolic,
    project_letta,
)
from hypabolic_trajectory.ir import IrRecord, RecordKind, TrajectoryIR

SOURCES: Final[tuple[str, ...]] = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
)

FormatName = Literal["both", "messages", "hypabolic"]

DIM: Final[str] = "\033[2m"
BOLD: Final[str] = "\033[1m"
RED: Final[str] = "\033[31m"
YELLOW: Final[str] = "\033[33m"
CYAN: Final[str] = "\033[36m"
RESET: Final[str] = "\033[0m"

_SOURCE_ENV: Final[dict[str, str]] = {
    "pi": "TRAJECTORY_PI_ROOT",
    "claude-code": "TRAJECTORY_CLAUDE_CODE_ROOT",
    "codex": "TRAJECTORY_CODEX_ROOT",
    "openclaw": "TRAJECTORY_OPENCLAW_ROOT",
    "hermes": "TRAJECTORY_HERMES_ROOT",
    "ahp": "TRAJECTORY_AHP_ROOT",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m trajectory_cli``."""
    try:
        args = parse_args(list(argv if argv is not None else sys.argv[1:]))
        if args.command == "help":
            print_help()
            return 0
        if args.command == "list":
            return run_list(args)
        if args.command == "show":
            return run_show(args)
        return run_browse(args)
    except TrajectoryError as error:
        print(f"{RED}{error.code}:{RESET} {error.message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse argv into a namespace. Raises SystemExit on --help from argparse."""
    parser = argparse.ArgumentParser(
        prog="trajectory",
        description=(
            "Local sample TUI for Hypabolic Trajectory (unpublished). "
            "Browse local agent session stores, normalize a selected transcript, "
            "and print privacy-safe summaries."
        ),
        add_help=False,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="browse",
        choices=("browse", "list", "show", "help"),
        help="Command (default: browse).",
    )
    parser.add_argument(
        "-s",
        "--source",
        dest="source",
        default=None,
        help="Transcript source: pi, claude-code, codex, openclaw, hermes, ahp.",
    )
    parser.add_argument(
        "-r",
        "--root",
        dest="root",
        default=None,
        help="Override local store root (also TRAJECTORY_<SOURCE>_ROOT).",
    )
    parser.add_argument(
        "-p",
        "--path",
        dest="path",
        default=None,
        help="show: path to a session transcript file.",
    )
    parser.add_argument(
        "--id",
        dest="id",
        default=None,
        help="show: session id from listing (resolved under the store root).",
    )
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=50,
        help="Maximum sessions to list (1-1000; default 50).",
    )
    parser.add_argument(
        "--format",
        dest="format",
        default="both",
        choices=("both", "messages", "hypabolic", "letta"),
        help="show: summary projection (both | messages | hypabolic).",
    )
    parser.add_argument(
        "--show-content",
        dest="show_content",
        action="store_true",
        help="Include record content snippets (WARNING: may contain private data).",
    )
    parser.add_argument(
        "-h",
        "--help",
        dest="help_flag",
        action="store_true",
        help="Show help and exit.",
    )

    # Accept -h/--help as a command synonym even when placed first.
    if argv and argv[0] in ("-h", "--help"):
        ns = argparse.Namespace(
            command="help",
            source=None,
            root=None,
            path=None,
            id=None,
            limit=50,
            format="both",
            show_content=False,
            help_flag=False,
        )
        return ns

    args = parser.parse_args(argv)
    if args.help_flag or args.command == "help":
        args.command = "help"
        return args

    if args.source is not None:
        args.source = parse_source(args.source)
    if args.root is not None:
        args.root = expand_home(args.root)
    if args.path is not None:
        args.path = expand_home(args.path)
    if args.format == "letta":
        args.format = "messages"
    return args


def parse_source(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in SOURCES:
        return normalized
    if normalized in ("claude", "claudecode"):
        return "claude-code"
    raise TrajectoryError(
        "unknown_source",
        f"Unknown source '{value}'. Expected one of: {', '.join(SOURCES)}.",
    )


# ---------------------------------------------------------------------------
# Roots (sample-CLI only default-home discovery)
# ---------------------------------------------------------------------------


def home_dir() -> Path:
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home) if home else Path.cwd()


def expand_home(path: str) -> str:
    if path == "~":
        return str(home_dir())
    if path.startswith("~/"):
        return str(home_dir() / path[2:])
    return path


def default_root(source: str) -> str:
    """Resolve sample-CLI default store root for *source*."""
    env_key = _SOURCE_ENV[source]
    from_env = os.environ.get(env_key, "").strip()
    if from_env:
        return expand_home(from_env)

    home = home_dir()
    if source == "pi":
        pi_env = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
        if pi_env:
            return expand_home(pi_env)
        return str(home / ".pi" / "agent")
    if source == "claude-code":
        return str(home / ".claude" / "projects")
    if source == "codex":
        return str(home / ".codex" / "sessions")
    if source == "openclaw":
        openclaw_env = (
            os.environ.get("OPENCLAW_STATE_DIR", "").strip()
            or os.environ.get("CLAWDBOT_STATE_DIR", "").strip()
        )
        if openclaw_env:
            return expand_home(openclaw_env)
        openclaw_home = home / ".openclaw"
        if openclaw_home.exists():
            return str(openclaw_home)
        return str(home / ".clawdbot")
    if source == "hermes":
        return str(home / ".hermes")
    # ahp — no home default store; listing needs an explicit export root.
    return str(home)


def describe_default(source: str) -> str:
    return {
        "pi": "~/.pi/agent (or PI_CODING_AGENT_DIR)",
        "claude-code": "~/.claude/projects",
        "codex": "~/.codex/sessions",
        "openclaw": (
            "~/.openclaw if present, else ~/.clawdbot "
            "(or OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR)"
        ),
        "hermes": "~/.hermes/state.db",
        "ahp": "explicit export root only (no home default)",
    }[source]


def resolve_root(source: str, root_override: str | None) -> str:
    if root_override:
        return expand_home(root_override)
    return default_root(source)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def run_list(args: argparse.Namespace) -> int:
    source = args.source if args.source is not None else prompt_source()
    root = resolve_root(source, args.root)
    print(f"{DIM}Source{RESET} {source}  {DIM}root{RESET} {root}")
    page = list_trajectories(source=source, root=root, limit=args.limit)
    if not page.items:
        print_empty(source)
        return 0
    print_listing(page.items)
    if page.next_cursor is not None:
        print(f"{DIM}More sessions available. Showing first {len(page.items)}.{RESET}")
    return 0


def run_show(args: argparse.Namespace) -> int:
    source = args.source if args.source is not None else "pi"
    root = resolve_root(source, args.root)
    path = resolve_path(source, root, args.path, args.id, args.limit)
    return print_summary(source, path, args.show_content, args.format)


def run_browse(args: argparse.Namespace) -> int:
    print(f"{BOLD}{CYAN}Trajectory{RESET}  {DIM}local sample TUI (unpublished){RESET}")
    print(f"{DIM}Privacy: content is hidden unless --show-content.{RESET}\n")

    source = args.source if args.source is not None else prompt_source()
    root = resolve_root(source, args.root)
    print(f"{DIM}Default for {source}:{RESET} {describe_default(source)}")
    print(f"{DIM}Using root{RESET} {root}\n")

    page = list_trajectories(source=source, root=root, limit=args.limit)
    if not page.items:
        print_empty(source)
        answer = input("Normalize a transcript file by path instead? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            file_path = expand_home(input("Path to transcript: ").strip())
            return print_summary(source, file_path, args.show_content, args.format)
        return 0

    selected = prompt_session(page.items)
    if selected is None:
        return 0
    print()
    return print_summary(source, selected.path, args.show_content, args.format)


def resolve_path(
    source: str,
    root: str,
    path: str | None,
    listing_id: str | None,
    limit: int,
) -> str:
    if path:
        return path
    if not listing_id:
        raise TrajectoryError("invalid_input", "Provide --path or --id.")
    page = list_trajectories(source=source, root=root, limit=limit)
    match = next((item for item in page.items if item.id == listing_id), None)
    if match is None:
        raise TrajectoryError(
            "invalid_input",
            f"Session id '{listing_id}' not found under {root}.",
        )
    return match.path


# ---------------------------------------------------------------------------
# Summary / display
# ---------------------------------------------------------------------------


def print_summary(
    source: str,
    path: str,
    show_content: bool,
    format_name: FormatName,
) -> int:
    file_path = Path(path)
    if not file_path.is_file():
        raise TrajectoryError("invalid_input", f"File not found: {path}")

    try:
        transcript = file_path.read_bytes()
    except OSError as error:
        raise TrajectoryError(
            "invalid_input",
            f"Could not read transcript: {error}",
        ) from None

    request = NormalizeRequest(source=source, transcript=transcript)
    ir = normalize_to_ir(request)

    print(f"{BOLD}── {source} {file_path.name} ──{RESET}")
    print(f"{DIM}path{RESET}     {path}")
    print(f"{DIM}group{RESET}    {ir.group_id}")
    print(f"{DIM}source{RESET}   {ir.source_name}")
    print(f"{DIM}records{RESET}  {len(ir.records)}")
    partial = ir.config.partial or ir.config.base_byte_offset > 0
    print(f"{DIM}partial{RESET}  {partial}")

    role_counts = Counter(str(record.role) for record in ir.records)
    tool_names: list[str] = []
    for record in ir.records:
        if record.kind == RecordKind.ASSISTANT_TOOL_CALLS:
            tool_names.extend(call.name for call in record.tool_calls)
    unique_tools = sorted(set(tool_names))
    roles = ", ".join(f"{role}={count}" for role, count in sorted(role_counts.items()))
    print(f"{BOLD}Roles{RESET}       {roles or '(none)'}")
    print(
        f"{BOLD}Tool calls{RESET}  {len(tool_names)} total, {len(unique_tools)} unique"
    )
    if unique_tools:
        shown = ", ".join(unique_tools[:12])
        if len(unique_tools) > 12:
            shown += "…"
        print(f"{BOLD}Tools{RESET}       {shown}")
    print(f"{BOLD}Diagnostics{RESET} {len(ir.diagnostics)}")
    for diagnostic in ir.diagnostics[:12]:
        print(f"  {DIM}{diagnostic.code}{RESET}  {diagnostic.message}")
    if len(ir.diagnostics) > 12:
        print(f"{DIM}…and {len(ir.diagnostics) - 12} more diagnostics{RESET}")

    if format_name in ("both", "hypabolic"):
        _print_hypabolic(ir)
    if format_name in ("both", "messages"):
        _print_messages(ir)

    if show_content:
        print(
            f"\n{RED}{BOLD}WARNING{RESET}{RED}: --show-content prints "
            f"transcript-derived text. Treat as private.{RESET}"
        )
        for index, record in enumerate(ir.records[:40], start=1):
            snippet = snippet_for(record)
            print(
                f"  {index:>3}  {str(record.role):<10} "
                f"{str(record.kind):<20} {snippet}"
            )
        if len(ir.records) > 40:
            print(f"{DIM}Showing first 40 of {len(ir.records)} records.{RESET}")
    else:
        print(
            f"{DIM}Content omitted (privacy). "
            f"Re-run with --show-content to include snippets.{RESET}"
        )
    return 0


def _print_hypabolic(ir: TrajectoryIR) -> None:
    try:
        doc = project_hypabolic(ir)
    except TrajectoryError as error:
        print(f"{YELLOW}Hypabolic projection skipped:{RESET} {error.message}")
        return
    trajectory_id = doc.get("trajectoryId") or doc.get("trajectory_id") or "?"
    schema_id = doc.get("schemaId") or doc.get("schema_id") or "?"
    schema_version = doc.get("schemaVersion")
    if schema_version is None:
        schema_version = doc.get("schema_version", "?")
    records = doc.get("records")
    n_records = len(records) if isinstance(records, list) else "?"
    print(
        f"\n{BOLD}Hypabolic{RESET} trajectoryId={trajectory_id} "
        f"schema={schema_id} v{schema_version} records={n_records}"
    )


def _print_messages(ir: TrajectoryIR) -> None:
    try:
        doc = project_letta(ir)
    except TrajectoryError as error:
        print(f"{YELLOW}Message trajectory skipped:{RESET} {error.message}")
        return
    records = doc.get("records")
    diagnostics = doc.get("diagnostics")
    n_records = len(records) if isinstance(records, list) else "?"
    n_diag = len(diagnostics) if isinstance(diagnostics, list) else "?"
    print(f"{BOLD}Messages{RESET} records={n_records} diagnostics={n_diag}")


def snippet_for(record: IrRecord) -> str:
    if record.kind == RecordKind.ASSISTANT_TOOL_CALLS:
        text = ", ".join(
            f"{call.name}({truncate(call.arguments_json, 40)})"
            for call in record.tool_calls
        )
        return truncate(text, 120)
    if record.kind == RecordKind.META:
        return truncate(
            f"source={record.source_name or '?'} model={record.model or '—'}",
            120,
        )
    return truncate(record.content or "—", 120)


def truncate(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    if max_len <= 1:
        return "…"
    return value[: max_len - 1] + "…"


def print_listing(items: Sequence[TrajectoryListing]) -> None:
    id_w = min(36, max(8, max((len(item.id) for item in items), default=8)))
    header = f"{'Id':<{id_w}}  {'Updated (UTC)':<24}  {'Size':>8}  Path"
    print(header)
    print("-" * min(100, len(header) + 20))
    for item in items:
        updated = item.updated_at or "—"
        size = format_bytes(item.size_bytes) if item.size_bytes is not None else "—"
        print(f"{item.id:<{id_w}}  {updated:<24}  {size:>8}  {item.path}")


def format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    unit = 0
    while value >= 1024 and unit < len(units) - 1:
        value /= 1024
        unit += 1
    if unit == 0:
        return f"{size} B"
    if value >= 10:
        return f"{value:.0f} {units[unit]}"
    return f"{value:.1f} {units[unit]}"


def print_empty(source: str) -> None:
    print(f"{YELLOW}No sessions found.{RESET} Empty or missing store is not an error.")
    if source == "hermes":
        print(
            f"{DIM}Hermes core listing is SQLite-free and returns empty pages. "
            f"Export message JSON and use show --path.{RESET}"
        )
    if source == "ahp":
        print(
            f"{DIM}AHP listing is Phase 3; use show --path with a Shape A "
            f"snapshot export.{RESET}"
        )


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def prompt_source() -> str:
    print("Sources:")
    for index, name in enumerate(SOURCES, start=1):
        print(f"  {index}) {name}")
    while True:
        answer = input(f"Select source [1-{len(SOURCES)} or name]: ").strip().lower()
        if answer.isdigit():
            n = int(answer)
            if 1 <= n <= len(SOURCES):
                return SOURCES[n - 1]
        try:
            return parse_source(answer)
        except TrajectoryError:
            print(f"{YELLOW}Invalid choice.{RESET}")


def prompt_session(items: Sequence[TrajectoryListing]) -> TrajectoryListing | None:
    print(f"Sessions ({len(items)}):")
    for index, item in enumerate(items, start=1):
        updated = item.updated_at or "—"
        size = (
            format_bytes(item.size_bytes) if item.size_bytes is not None else "—"
        )
        print(
            f"  {index:>2}) {item.id}  {DIM}{updated}{RESET}  {size}  {item.path}"
        )
    print("   0) quit")
    while True:
        answer = input(f"Select session [0-{len(items)}]: ").strip()
        if answer == "0":
            return None
        if answer.isdigit():
            n = int(answer)
            if 1 <= n <= len(items):
                return items[n - 1]
        by_id = next((item for item in items if item.id == answer), None)
        if by_id is not None:
            return by_id
        print(f"{YELLOW}Invalid choice.{RESET}")


def print_help() -> None:
    print(
        """trajectory — local sample TUI for Hypabolic Trajectory (unpublished)

Usage:
  trajectory [browse] [--source <src>] [--root <path>] [--limit N] [--show-content]
  trajectory list --source <src> [--root <path>] [--limit N]
  trajectory show --source <src> (--path <file> | --id <id>) [--root <path>] \\
                  [--format both|messages|hypabolic] [--show-content]
  trajectory help

Sources: """
        + ", ".join(SOURCES)
        + """

Default roots:
  pi           ~/.pi/agent
  claude-code  ~/.claude/projects
  codex        ~/.codex/sessions
  openclaw     ~/.openclaw if present, else ~/.clawdbot
  hermes       ~/.hermes
  ahp          explicit export root only (use show --path)

Root overrides: --root or TRAJECTORY_<SOURCE>_ROOT (e.g. TRAJECTORY_PI_ROOT).
OpenClaw also honors OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR.
Privacy: content is omitted unless --show-content (prints a warning).

Run (from repo root):
  PYTHONPATH=python/src:python/samples python -m trajectory_cli list --source pi
"""
    )


__all__ = [
    "SOURCES",
    "default_root",
    "describe_default",
    "expand_home",
    "format_bytes",
    "main",
    "parse_args",
    "parse_source",
    "resolve_path",
    "resolve_root",
    "snippet_for",
    "truncate",
]

"""Local sample CLI for browsing agent sessions with Trajectory.

Not published — depends on an editable / PYTHONPATH install of
``hypabolic_trajectory``. Default home discovery lives here only; the library
listing API always requires an explicit root.

Commands match peer sample CLIs (.NET / TypeScript / Rust):
  browse (default)  interactive source → session → summary
  list              table of sessions for one source
  show              normalize one --path or listing --id
  stream            follow a JSONL session file (optional file I/O + core stream)
  ahp-stream        optional AHP live-host client demo (fake host or injected transport)

This sample is a **consumer process**, not a Trajectory daemon. The library
owns pure stream apply only; the CLI owns lifetime, roots, and transport.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Final, Literal, Sequence

from hypabolic_trajectory import (
    NormalizeRequest,
    StreamOptions,
    TrajectoryError,
    TrajectoryListing,
    list_trajectories,
    normalize_to_ir,
    project_hypabolic,
    project_letta,
)
from hypabolic_trajectory.ahp_client import (
    AhpClientEvent,
    AhpClientOptions,
    AhpStreamClient,
    AhpTransport,
    FakeAhpHost,
    FakeAhpHostScript,
    InMemoryAhpTransportPair,
)
from hypabolic_trajectory.io import FileStreamHostError, FileStreamOptions, FileTrajectoryStream
from hypabolic_trajectory.ir import IrRecord, RecordKind, TrajectoryIR
from hypabolic_trajectory.streaming.types import StreamUpdate

SOURCES: Final[tuple[str, ...]] = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
    "grok-build",
)

# File JSONL sources supported by optional stream file I/O (not hermes/ahp).
STREAM_FILE_SOURCES: Final[tuple[str, ...]] = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "grok-build",
)

EmitMode = Literal["snapshot+delta", "snapshot", "delta"]

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
    "grok-build": "TRAJECTORY_GROK_BUILD_ROOT",
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
        if args.command == "stream":
            return run_stream(args)
        if args.command == "ahp-stream":
            return run_ahp_stream(args)
        return run_browse(args)
    except TrajectoryError as error:
        print(f"{RED}{error.code}:{RESET} {error.message}", file=sys.stderr)
        return 2
    except FileStreamHostError as error:
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
            "follow a live JSONL file, or demo an AHP stream client. "
            "Not a daemon — the calling process owns lifetime."
        ),
        add_help=False,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="browse",
        choices=("browse", "list", "show", "stream", "ahp-stream", "help"),
        help="Command (default: browse).",
    )
    parser.add_argument(
        "-s",
        "--source",
        dest="source",
        default=None,
        help="Transcript source: pi, claude-code, codex, openclaw, hermes, ahp, grok-build.",
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
        help="show/stream: path to a session transcript file.",
    )
    parser.add_argument(
        "--id",
        dest="id",
        default=None,
        help="show/stream: session id from listing (resolved under the store root).",
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
        "--emit",
        dest="emit",
        default="snapshot+delta",
        choices=("snapshot+delta", "snapshot", "delta"),
        help="stream/ahp-stream: delivery (default snapshot+delta).",
    )
    parser.add_argument(
        "--follow",
        dest="follow",
        action="store_true",
        help="stream: keep polling until Ctrl-C or --max-updates.",
    )
    parser.add_argument(
        "--interval",
        dest="interval",
        type=float,
        default=0.05,
        help="stream: poll interval seconds when --follow (default 0.05).",
    )
    parser.add_argument(
        "--max-updates",
        dest="max_updates",
        type=int,
        default=None,
        help="stream/ahp-stream: stop after N stream updates (tests/demos).",
    )
    parser.add_argument(
        "--url",
        dest="url",
        default=None,
        help="ahp-stream: host URL. Sample supports fake:// (in-memory FakeAhpHost).",
    )
    parser.add_argument(
        "--chat",
        dest="chat",
        default=None,
        help="ahp-stream: AHP chat channel URI (ahp-chat:/…).",
    )
    parser.add_argument(
        "--from-seq",
        dest="from_seq",
        type=int,
        default=None,
        help="ahp-stream: optional subscribe fromSeq.",
    )
    parser.add_argument(
        "--token",
        dest="token",
        default=None,
        help="ahp-stream: auth token for callback (never stored on stream state).",
    )
    parser.add_argument(
        "--snapshot-path",
        dest="snapshot_path",
        default=None,
        help="ahp-stream (fake://): Shape A snapshot JSON for FakeAhpHost.",
    )
    parser.add_argument(
        "--actions-path",
        dest="actions_path",
        default=None,
        help="ahp-stream (fake://): ActionEnvelope JSONL for FakeAhpHost.",
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
        return _empty_namespace(command="help")

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
    if args.snapshot_path is not None:
        args.snapshot_path = expand_home(args.snapshot_path)
    if args.actions_path is not None:
        args.actions_path = expand_home(args.actions_path)
    if args.format == "letta":
        args.format = "messages"
    args.emit = parse_emit(args.emit)
    return args


def _empty_namespace(*, command: str) -> argparse.Namespace:
    return argparse.Namespace(
        command=command,
        source=None,
        root=None,
        path=None,
        id=None,
        limit=50,
        format="both",
        show_content=False,
        emit="snapshot+delta",
        follow=False,
        interval=0.05,
        max_updates=None,
        url=None,
        chat=None,
        from_seq=None,
        token=None,
        snapshot_path=None,
        actions_path=None,
        help_flag=False,
    )


def parse_source(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in SOURCES:
        return normalized
    if normalized in ("claude", "claudecode"):
        return "claude-code"
    if normalized in ("grok", "grokbuild"):
        return "grok-build"
    raise TrajectoryError(
        "unknown_source",
        f"Unknown source '{value}'. Expected one of: {', '.join(SOURCES)}.",
    )


def parse_emit(value: str) -> EmitMode:
    normalized = value.strip().lower().replace("_", "+").replace(" ", "")
    if normalized in ("snapshot+delta", "both", "snapshotdelta"):
        return "snapshot+delta"
    if normalized == "snapshot":
        return "snapshot"
    if normalized == "delta":
        return "delta"
    raise TrajectoryError(
        "invalid_input",
        f"Unknown --emit '{value}'. Expected snapshot+delta, snapshot, or delta.",
    )


def emit_to_delivery(emit: EmitMode) -> str:
    if emit == "snapshot+delta":
        return "both"
    return emit


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
    if source == "grok-build":
        grok_home = os.environ.get("GROK_HOME", "").strip()
        if grok_home:
            return str(Path(expand_home(grok_home)) / "sessions")
        return str(home / ".grok" / "sessions")
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
        "grok-build": "$GROK_HOME/sessions or ~/.grok/sessions (or TRAJECTORY_GROK_BUILD_ROOT)",
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


def run_stream(args: argparse.Namespace) -> int:
    """Follow a JSONL session via optional file I/O + core stream apply.

    Caller owns process lifetime (Ctrl-C / --max-updates). Not a daemon.
    """
    source = args.source if args.source is not None else "pi"
    if source not in STREAM_FILE_SOURCES:
        raise TrajectoryError(
            "invalid_input",
            f"stream supports file JSONL sources only: {', '.join(STREAM_FILE_SOURCES)}. "
            f"Use ahp-stream for AHP; Hermes uses the optional provider path.",
        )

    root, path, group_id = resolve_stream_target(source, args)
    delivery = emit_to_delivery(args.emit)
    stream_opts = StreamOptions(source=source, group_id=group_id, delivery=delivery)

    print(f"{BOLD}{CYAN}Trajectory stream{RESET}  {DIM}sample file follow (not a daemon){RESET}")
    print(f"{DIM}source{RESET}   {source}")
    print(f"{DIM}root{RESET}     {root}")
    print(f"{DIM}path{RESET}     {path}")
    print(f"{DIM}emit{RESET}     {args.emit} (delivery={delivery})")
    print(f"{DIM}follow{RESET}   {bool(args.follow)}")
    if not args.show_content:
        print(f"{DIM}Privacy: content hidden unless --show-content.{RESET}")
    print()

    fs = FileTrajectoryStream.open(
        FileStreamOptions(
            root=root,
            path=path,
            source=source,
            group_id=group_id,
            stream=stream_opts,
            poll_interval=max(0.0, float(args.interval)),
        )
    )
    try:
        return _consume_file_stream(
            fs,
            follow=bool(args.follow),
            interval=max(0.0, float(args.interval)),
            max_updates=args.max_updates,
            show_content=bool(args.show_content),
            emit=args.emit,
        )
    finally:
        fs.close()


def resolve_stream_target(
    source: str, args: argparse.Namespace
) -> tuple[str, str, str | None]:
    """Resolve (root, path, group_id) for stream. Explicit root bounds the path."""
    if args.path:
        path = expand_home(args.path)
        if args.root:
            root = expand_home(args.root)
        else:
            # Parent directory as explicit root (no home multi-session watch).
            root = str(Path(path).resolve().parent)
        group_id = args.id
        return root, path, group_id

    if not args.id:
        raise TrajectoryError(
            "invalid_input",
            "stream requires --path or --id (with --root for listing resolution).",
        )
    if not args.root:
        raise TrajectoryError(
            "invalid_input",
            "stream --id requires an explicit --root (no implicit home multi-session watch).",
        )
    root = expand_home(args.root)
    path = resolve_path(source, root, None, args.id, args.limit)
    return root, path, args.id


def _consume_file_stream(
    fs: FileTrajectoryStream,
    *,
    follow: bool,
    interval: float,
    max_updates: int | None,
    show_content: bool,
    emit: EmitMode,
) -> int:
    seen = 0
    # Always poll at least once for the initial snapshot / current prefix.
    while True:
        update = fs.poll()
        if update is not None and update.kind != "unchanged":
            seen += 1
            print_stream_update(update, show_content=show_content, emit=emit, index=seen)
            if max_updates is not None and seen >= max_updates:
                break
        if not follow:
            break
        if max_updates is not None and seen >= max_updates:
            break
        if interval > 0:
            time.sleep(interval)

    if seen == 0:
        print(f"{DIM}No stream updates (empty or unchanged prefix).{RESET}")
    else:
        print(f"{DIM}Emitted {seen} update(s). Process exit ends follow (not a daemon).{RESET}")
    return 0


def run_ahp_stream(
    args: argparse.Namespace,
    *,
    transport: AhpTransport | None = None,
) -> int:
    """Demo optional AHP client. Sample default is FakeAhpHost (fake://).

    Real WebSocket hosts: inject a consumer ``AhpTransport`` (tests pass
    ``transport=``) — Trajectory does not own reconnect policy.
    """
    if not args.chat:
        raise TrajectoryError("invalid_input", "ahp-stream requires --chat <ahp-chat:/…>.")
    chat = str(args.chat).strip()
    if not chat:
        raise TrajectoryError("invalid_input", "ahp-stream requires --chat <ahp-chat:/…>.")

    url = (args.url or "fake://demo").strip()
    delivery = emit_to_delivery(args.emit)
    stream_opts = StreamOptions(source="ahp", group_id=chat, delivery=delivery)

    print(f"{BOLD}{CYAN}Trajectory ahp-stream{RESET}  {DIM}sample client demo (not a daemon){RESET}")
    print(f"{DIM}url{RESET}      {url}")
    print(f"{DIM}chat{RESET}     {chat}")
    print(f"{DIM}emit{RESET}     {args.emit} (delivery={delivery})")
    if args.from_seq is not None:
        print(f"{DIM}from-seq{RESET} {args.from_seq}")
    if not args.show_content:
        print(f"{DIM}Privacy: content hidden unless --show-content.{RESET}")
    print()

    host: FakeAhpHost | None = None
    owns_transport = transport is None
    if transport is None:
        transport, host = _open_ahp_demo_transport(url, chat, args)

    token = args.token or os.environ.get("TRAJECTORY_AHP_TOKEN")
    events: list[AhpClientEvent] = []
    updates_seen = 0
    max_updates = args.max_updates

    def on_event(event: AhpClientEvent) -> None:
        nonlocal updates_seen
        events.append(event)
        if event.kind == "stream-update" and event.update is not None:
            updates_seen += 1
            print_stream_update(
                event.update,
                show_content=bool(args.show_content),
                emit=args.emit,
                index=updates_seen,
            )
        elif event.kind == "ready":
            print(f"{DIM}AHP client ready (subscribe complete).{RESET}")
        elif event.kind in ("auth-required", "auth-failed", "resync-required", "backpressure", "error", "disconnected"):
            code = event.code or event.kind
            msg = event.message or event.kind
            print(f"{YELLOW}{code}{RESET}  {msg}")

    client = AhpStreamClient(
        transport=transport,
        options=AhpClientOptions(
            chat_channel=chat,
            auth=(lambda _challenge: {"token": token}) if token else None,
            stream_options=stream_opts,
            from_server_seq=args.from_seq,
        ),
        on_event=on_event,
    )
    try:
        client.start()
        # Fake/in-memory hosts complete handshake synchronously. For injected
        # transports that may push later, wait briefly when --max-updates set.
        if max_updates is not None:
            deadline = time.monotonic() + 2.0
            while updates_seen < max_updates and time.monotonic() < deadline:
                time.sleep(0.01)
        elif owns_transport and host is not None:
            # Demo: no live feed beyond initial material; exit after handshake.
            pass
        else:
            # Injected live transport without max-updates: short idle then exit.
            # Callers that need long-running follow should set --max-updates or
            # own the process loop in their app.
            time.sleep(0.05)

        if updates_seen == 0 and not any(e.kind == "ready" for e in events):
            print(f"{YELLOW}No AHP ready/update events. Check --url / --chat / fixtures.{RESET}")
        else:
            print(
                f"{DIM}Emitted {updates_seen} stream update(s). "
                f"Cancel leaves last cursor valid; not a daemon.{RESET}"
            )
        return 0
    finally:
        client.cancel()
        if host is not None:
            host.close()


def _open_ahp_demo_transport(
    url: str,
    chat: str,
    args: argparse.Namespace,
) -> tuple[AhpTransport, FakeAhpHost | None]:
    """Build sample transport. ``fake://`` uses FakeAhpHost (CI default)."""
    scheme = url.split(":", 1)[0].lower() if ":" in url else url.lower()
    if scheme not in ("fake", "memory", "test"):
        raise TrajectoryError(
            "invalid_input",
            "Sample ahp-stream supports url scheme fake:// (in-memory FakeAhpHost) only. "
            "Wire AhpStreamClient with your WebSocket AhpTransport for live hosts "
            "(see docs/ahp-client.md). Example: --url fake://demo",
        )

    pair = InMemoryAhpTransportPair()
    snapshot = None
    actions: list[dict[str, Any]] = []
    if args.snapshot_path:
        snapshot = json.loads(Path(args.snapshot_path).read_text(encoding="utf-8"))
    if args.actions_path:
        for line in Path(args.actions_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                actions.append(json.loads(line))
    if snapshot is None and not actions:
        snapshot = {
            "ahpProtocolVersion": "0.7.0",
            "chat": {"id": chat, "turns": [], "activeTurn": None},
        }
    host = FakeAhpHost(
        transport=pair.host,
        script=FakeAhpHostScript(
            initial_snapshot=snapshot,
            initial_actions=actions,
            require_auth=bool(args.token or os.environ.get("TRAJECTORY_AHP_TOKEN")),
            accept_token=args.token or os.environ.get("TRAJECTORY_AHP_TOKEN") or "test-token",
        ),
        chat_channel=chat,
    )
    return pair.client, host


def print_stream_update(
    update: StreamUpdate,
    *,
    show_content: bool,
    emit: EmitMode,
    index: int,
) -> None:
    """Privacy-safe stream update summary. Content opt-in only."""
    print(f"{BOLD}── stream update #{index} ──{RESET}")
    print(f"{DIM}kind{RESET}       {update.kind}")
    print(
        f"{DIM}revision{RESET}   {update.revision.revision} "
        f"id={update.revision.revision_id} gen={update.revision.generation}"
    )
    cursor = update.cursor
    pos = cursor.position
    pos_kind = getattr(pos, "kind", type(pos).__name__)
    print(
        f"{DIM}cursor{RESET}     source={cursor.source} group={truncate(cursor.group_id, 40)} "
        f"gen={cursor.generation} pos={pos_kind}"
    )
    if update.snapshot is not None:
        n = len(update.snapshot.records)
        print(f"{DIM}snapshot{RESET}   records={n} complete={update.snapshot.complete}")
    elif emit in ("snapshot+delta", "snapshot"):
        print(f"{DIM}snapshot{RESET}   (omitted by delivery)")
    if update.delta is not None:
        ops = update.delta.operations
        op_names = Counter(op.op for op in ops)
        ops_summary = ", ".join(f"{name}={count}" for name, count in sorted(op_names.items()))
        print(f"{DIM}delta{RESET}      ops={len(ops)}" + (f" ({ops_summary})" if ops_summary else ""))
    elif emit in ("snapshot+delta", "delta"):
        print(f"{DIM}delta{RESET}      (omitted by delivery)")
    print(f"{DIM}diagnostics{RESET} {len(update.diagnostics)}")
    for diagnostic in update.diagnostics[:8]:
        print(f"  {DIM}{diagnostic.code}{RESET}  {diagnostic.message}")
    if len(update.diagnostics) > 8:
        print(f"{DIM}…and {len(update.diagnostics) - 8} more diagnostics{RESET}")
    if update.reset is not None:
        print(f"{YELLOW}reset{RESET}      reason={update.reset.reason}")
    if update.error is not None:
        print(f"{RED}error{RESET}      {update.error.code}: {update.error.message}")

    if show_content and update.snapshot is not None:
        print(
            f"\n{RED}{BOLD}WARNING{RESET}{RED}: --show-content prints "
            f"transcript-derived text. Treat as private.{RESET}"
        )
        for i, stream_rec in enumerate(update.snapshot.records[:40], start=1):
            rec = stream_rec.record
            role = str(rec.get("role") or "?")
            kind = str(rec.get("kind") or rec.get("type") or "?")
            content = rec.get("content")
            if isinstance(content, str):
                snip = truncate(content, 80)
            else:
                snip = truncate(json.dumps(rec, ensure_ascii=False)[:120], 80)
            print(f"  {i:>3}  {stream_rec.status:<12} {role:<10} {kind:<20} {snip}")
        if len(update.snapshot.records) > 40:
            print(f"{DIM}Showing first 40 of {len(update.snapshot.records)} records.{RESET}")
    elif not show_content:
        print(f"{DIM}Content omitted (privacy). Re-run with --show-content for snippets.{RESET}")
    print()


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

Not a daemon. The calling process owns lifetime, store roots, and AHP transport.

Usage:
  trajectory [browse] [--source <src>] [--root <path>] [--limit N] [--show-content]
  trajectory list --source <src> [--root <path>] [--limit N]
  trajectory show --source <src> (--path <file> | --id <id>) [--root <path>] \\
                  [--format both|messages|hypabolic] [--show-content]
  trajectory stream --source <src> (--path <file> | --id <id> --root <store>) \\
                    [--emit snapshot+delta|snapshot|delta] [--follow] \\
                    [--interval 0.05] [--max-updates N] [--show-content]
  trajectory ahp-stream --url fake://demo --chat <ahp-chat:/…> \\
                        [--from-seq N] [--token T] \\
                        [--snapshot-path FILE] [--actions-path FILE] \\
                        [--emit snapshot+delta] [--max-updates N] [--show-content]
  trajectory help

Sources: """
        + ", ".join(SOURCES)
        + """

File stream sources: """
        + ", ".join(STREAM_FILE_SOURCES)
        + """

Default roots:
  pi           ~/.pi/agent
  claude-code  ~/.claude/projects
  codex        ~/.codex/sessions
  openclaw     ~/.openclaw if present, else ~/.clawdbot
  hermes       ~/.hermes
  ahp          explicit export root only (use show --path)
  grok-build   $GROK_HOME/sessions or ~/.grok/sessions

Root overrides: --root or TRAJECTORY_<SOURCE>_ROOT (e.g. TRAJECTORY_PI_ROOT).
OpenClaw also honors OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR.
Privacy: content is omitted unless --show-content (prints a warning).
Stream delivery default is snapshot+delta (core always computes both).

Sample ahp-stream uses fake:// FakeAhpHost only. Live WebSocket hosts: inject
AhpTransport in your app (docs/ahp-client.md).

Run (from repo root):
  PYTHONPATH=python/src:python/samples python -m trajectory_cli list --source pi
  PYTHONPATH=python/src:python/samples python -m trajectory_cli stream \\
    --source pi --path conformance/cases/pi/tool-calls/input.jsonl --max-updates 1
"""
    )


__all__ = [
    "SOURCES",
    "STREAM_FILE_SOURCES",
    "default_root",
    "describe_default",
    "emit_to_delivery",
    "expand_home",
    "format_bytes",
    "main",
    "parse_args",
    "parse_emit",
    "parse_source",
    "print_stream_update",
    "resolve_path",
    "resolve_root",
    "resolve_stream_target",
    "run_ahp_stream",
    "run_stream",
    "snippet_for",
    "truncate",
]

//! Local sample CLI for browsing agent sessions with Trajectory.
//! Not published — depends on the workspace `hypabolic-trajectory` crate.
//! Consumer process only — not a Trajectory daemon.

#![forbid(unsafe_code)]

use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::io::{self, Write as _};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::rc::Rc;
use std::thread;
use std::time::{Duration, Instant};

use clap::{Parser, Subcommand, ValueEnum};
use dialoguer::{Confirm, Select, theme::ColorfulTheme};
use hypabolic_trajectory::{
    ListingOptions, NormalizeOptions, NormalizeRequest, RecordKind, SourceContext, StreamDelivery,
    StreamOptions, StreamUpdate, Trajectory, TrajectoryError, TrajectoryListing, TrajectorySource,
    list_ahp_trajectories, list_claude_code_trajectories, list_codex_trajectories,
    list_grok_build_trajectories, list_hermes_trajectories, list_openclaw_trajectories,
    list_pi_trajectories, normalize_ahp, normalize_claude_code, normalize_codex,
    normalize_grok_build, normalize_hermes, normalize_openclaw, normalize_pi, project_hypabolic,
    project_letta,
};
use hypabolic_trajectory_ahp::{
    AhpClientEvent, AhpClientEventKind, AhpClientOptions, AhpStreamClient, FakeAhpHost,
    FakeAhpHostScript, InMemoryAhpTransportPair,
};
use hypabolic_trajectory_io::{FileStreamOptions, FileTrajectoryStream, HostError};
use serde_json::{Map, Value};

#[derive(Debug, Clone, Copy, ValueEnum)]
enum SourceArg {
    Pi,
    #[value(name = "claude-code", alias = "claude")]
    ClaudeCode,
    Codex,
    Openclaw,
    Hermes,
    Ahp,
    #[value(name = "grok-build", alias = "grok")]
    GrokBuild,
}

impl SourceArg {
    const ALL: [SourceArg; 7] = [
        SourceArg::Pi,
        SourceArg::ClaudeCode,
        SourceArg::Codex,
        SourceArg::Openclaw,
        SourceArg::Hermes,
        SourceArg::Ahp,
        SourceArg::GrokBuild,
    ];

    fn wire_name(self) -> &'static str {
        match self {
            Self::Pi => "pi",
            Self::ClaudeCode => "claude-code",
            Self::Codex => "codex",
            Self::Openclaw => "openclaw",
            Self::Hermes => "hermes",
            Self::Ahp => "ahp",
            Self::GrokBuild => "grok-build",
        }
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum FormatArg {
    Both,
    Messages,
    Hypabolic,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum EmitArg {
    #[value(name = "snapshot+delta", alias = "both")]
    SnapshotDelta,
    Snapshot,
    Delta,
}

impl EmitArg {
    fn delivery(self) -> StreamDelivery {
        match self {
            Self::SnapshotDelta => StreamDelivery::Both,
            Self::Snapshot => StreamDelivery::Snapshot,
            Self::Delta => StreamDelivery::Delta,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::SnapshotDelta => "snapshot+delta",
            Self::Snapshot => "snapshot",
            Self::Delta => "delta",
        }
    }
}

#[derive(Parser, Debug)]
#[command(
    name = "trajectory",
    about = "Local sample TUI for Hypabolic Trajectory (unpublished)",
    long_about = "Browse local agent session stores, normalize a selected transcript, follow a live JSONL file, or demo an AHP stream client.\n\nNot a daemon — the calling process owns lifetime.\nContent is omitted unless --show-content is passed (with an explicit privacy warning)."
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Transcript source: pi, claude-code, codex, openclaw, hermes, ahp, grok-build.
    #[arg(short, long, global = true, value_enum)]
    source: Option<SourceArg>,

    /// Override the local store root for the selected source.
    #[arg(short, long, global = true, env = "TRAJECTORY_ROOT")]
    root: Option<PathBuf>,

    /// Maximum sessions to list (1-1000).
    #[arg(long, global = true, default_value_t = 50)]
    limit: usize,

    /// Include record content snippets. WARNING: may contain private data.
    #[arg(long, global = true, default_value_t = false)]
    show_content: bool,
}

#[derive(Subcommand, Debug, Clone)]
enum Commands {
    /// Interactive source → session → summary browser (default).
    Browse,
    /// List discovered local sessions.
    List,
    /// Normalize a session file and print a privacy-safe summary.
    Show {
        /// Path to a session transcript file (JSONL or Hermes JSON export).
        #[arg(short, long)]
        path: Option<PathBuf>,
        /// Session id from listing; resolved under the store root when --path is omitted.
        #[arg(long)]
        id: Option<String>,
        /// Summary projection: messages, hypabolic, or both (default).
        #[arg(long, value_enum, default_value_t = FormatArg::Both)]
        format: FormatArg,
    },
    /// Follow a JSONL session file (optional file I/O + core stream; not a daemon).
    Stream {
        /// Path to a JSONL session file.
        #[arg(short, long)]
        path: Option<PathBuf>,
        /// Session id from listing; requires --root.
        #[arg(long)]
        id: Option<String>,
        /// Delivery: snapshot+delta (default), snapshot, or delta.
        #[arg(long, value_enum, default_value_t = EmitArg::SnapshotDelta)]
        emit: EmitArg,
        /// Keep polling until Ctrl-C or --max-updates.
        #[arg(long, default_value_t = false)]
        follow: bool,
        /// Poll interval seconds when --follow.
        #[arg(long, default_value_t = 0.05)]
        interval: f64,
        /// Stop after N stream updates (tests/demos).
        #[arg(long)]
        max_updates: Option<usize>,
    },
    /// Demo optional AHP client with fake:// FakeAhpHost (not a daemon).
    #[command(name = "ahp-stream")]
    AhpStream {
        /// Host URL. Sample supports fake:// only.
        #[arg(long, default_value = "fake://demo")]
        url: String,
        /// AHP chat channel URI (ahp-chat:/…).
        #[arg(long)]
        chat: String,
        /// Optional subscribe fromSeq.
        #[arg(long)]
        from_seq: Option<i64>,
        /// Auth token for callback (never stored on stream state).
        #[arg(long)]
        token: Option<String>,
        /// fake://: Shape A snapshot JSON for FakeAhpHost.
        #[arg(long)]
        snapshot_path: Option<PathBuf>,
        /// fake://: ActionEnvelope JSONL for FakeAhpHost.
        #[arg(long)]
        actions_path: Option<PathBuf>,
        /// Delivery: snapshot+delta (default), snapshot, or delta.
        #[arg(long, value_enum, default_value_t = EmitArg::SnapshotDelta)]
        emit: EmitArg,
        /// Stop after N stream updates (tests/demos).
        #[arg(long)]
        max_updates: Option<usize>,
    },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let command = cli.command.clone().unwrap_or(Commands::Browse);
    let result = match command {
        Commands::Browse => run_browse(&cli),
        Commands::List => run_list(&cli),
        Commands::Show { path, id, format } => run_show(&cli, path, id, format),
        Commands::Stream {
            path,
            id,
            emit,
            follow,
            interval,
            max_updates,
        } => run_stream(&cli, path, id, emit, follow, interval, max_updates),
        Commands::AhpStream {
            url,
            chat,
            from_seq,
            token,
            snapshot_path,
            actions_path,
            emit,
            max_updates,
        } => run_ahp_stream(
            &cli,
            &url,
            &chat,
            from_seq,
            token,
            snapshot_path,
            actions_path,
            emit,
            max_updates,
        ),
    };
    match result {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{}: {}", error.code, error.message);
            ExitCode::from(2)
        }
    }
}

fn host_to_trajectory(error: HostError) -> TrajectoryError {
    TrajectoryError::new(error.code, error.message)
}

fn source_to_trajectory(source: SourceArg) -> TrajectorySource {
    match source {
        SourceArg::Pi => TrajectorySource::Pi,
        SourceArg::ClaudeCode => TrajectorySource::ClaudeCode,
        SourceArg::Codex => TrajectorySource::Codex,
        SourceArg::Openclaw => TrajectorySource::OpenClaw,
        SourceArg::Hermes => TrajectorySource::Hermes,
        SourceArg::Ahp => TrajectorySource::Ahp,
        SourceArg::GrokBuild => TrajectorySource::GrokBuild,
    }
}

fn is_stream_file_source(source: SourceArg) -> bool {
    matches!(
        source,
        SourceArg::Pi
            | SourceArg::ClaudeCode
            | SourceArg::Codex
            | SourceArg::Openclaw
            | SourceArg::GrokBuild
    )
}

fn run_stream(
    cli: &Cli,
    path: Option<PathBuf>,
    id: Option<String>,
    emit: EmitArg,
    follow: bool,
    interval: f64,
    max_updates: Option<usize>,
) -> Result<ExitCode, TrajectoryError> {
    let source = cli.source.unwrap_or(SourceArg::Pi);
    if !is_stream_file_source(source) {
        return Err(TrajectoryError::new(
            "invalid_input",
            "stream supports file JSONL sources only: pi, claude-code, codex, openclaw, grok-build. Use ahp-stream for AHP.",
        ));
    }
    let (root, path, group_id) = resolve_stream_target(source, cli, path, id)?;
    let delivery = emit.delivery();
    let mut stream_opts = StreamOptions::new(source_to_trajectory(source));
    stream_opts.delivery = delivery;
    if let Some(ref g) = group_id {
        stream_opts = stream_opts.with_group_id(g.clone());
    }

    println!("Trajectory stream  sample file follow (not a daemon)");
    println!("source   {}", source.wire_name());
    println!("root     {}", root.display());
    println!("path     {}", path.display());
    println!("emit     {} (delivery={delivery:?})", emit.label());
    println!("follow   {follow}");
    if !cli.show_content {
        println!("Privacy: content hidden unless --show-content.");
    }
    println!();

    let mut fs = FileTrajectoryStream::open(FileStreamOptions {
        root: root.clone(),
        path: path.clone(),
        source: source_to_trajectory(source),
        group_id,
        stream: Some(stream_opts),
        poll_interval: Duration::from_secs_f64(interval.max(0.0)),
        reconcile_every: 0,
        source_revision: "file-0".into(),
    })
    .map_err(host_to_trajectory)?;

    let mut seen = 0usize;
    loop {
        let update = fs.poll().map_err(host_to_trajectory)?;
        if let Some(update) = update {
            if update.kind != "unchanged" {
                seen += 1;
                print_stream_update(&update, cli.show_content, emit, seen);
                if max_updates.is_some_and(|m| seen >= m) {
                    break;
                }
            }
        }
        if !follow {
            break;
        }
        if max_updates.is_some_and(|m| seen >= m) {
            break;
        }
        if interval > 0.0 {
            thread::sleep(Duration::from_secs_f64(interval));
        }
    }

    if seen == 0 {
        println!("No stream updates (empty or unchanged prefix).");
    } else {
        println!("Emitted {seen} update(s). Process exit ends follow (not a daemon).");
    }
    Ok(ExitCode::SUCCESS)
}

fn resolve_stream_target(
    source: SourceArg,
    cli: &Cli,
    path: Option<PathBuf>,
    id: Option<String>,
) -> Result<(PathBuf, PathBuf, Option<String>), TrajectoryError> {
    if let Some(path) = path {
        let path = expand_home_path(&path);
        let root = match cli.root.as_ref() {
            Some(r) => expand_home_path(r),
            None => path.parent().map(Path::to_path_buf).ok_or_else(|| {
                TrajectoryError::new("invalid_input", "Could not derive root from path.")
            })?,
        };
        return Ok((root, path, id));
    }
    let Some(id) = id else {
        return Err(TrajectoryError::new(
            "invalid_input",
            "stream requires --path or --id (with --root for listing resolution).",
        ));
    };
    let Some(root) = cli.root.as_ref() else {
        return Err(TrajectoryError::new(
            "invalid_input",
            "stream --id requires an explicit --root (no implicit home multi-session watch).",
        ));
    };
    let root = expand_home_path(root);
    let (path, _) = resolve_path(source, &root, None, Some(id.clone()), cli.limit)?;
    Ok((root, path, Some(id)))
}

#[allow(clippy::too_many_arguments)]
fn run_ahp_stream(
    cli: &Cli,
    url: &str,
    chat: &str,
    from_seq: Option<i64>,
    token: Option<String>,
    snapshot_path: Option<PathBuf>,
    actions_path: Option<PathBuf>,
    emit: EmitArg,
    max_updates: Option<usize>,
) -> Result<ExitCode, TrajectoryError> {
    let chat = chat.trim();
    if chat.is_empty() {
        return Err(TrajectoryError::new(
            "invalid_input",
            "ahp-stream requires --chat <ahp-chat:/…>.",
        ));
    }
    let delivery = emit.delivery();
    println!("Trajectory ahp-stream  sample client demo (not a daemon)");
    println!("url      {url}");
    println!("chat     {chat}");
    println!("emit     {}", emit.label());
    if let Some(seq) = from_seq {
        println!("from-seq {seq}");
    }
    if !cli.show_content {
        println!("Privacy: content hidden unless --show-content.");
    }
    println!();

    let scheme = url
        .split_once(':')
        .map(|(s, _)| s.to_ascii_lowercase())
        .unwrap_or_else(|| url.to_ascii_lowercase());
    if scheme != "fake" && scheme != "memory" && scheme != "test" {
        return Err(TrajectoryError::new(
            "invalid_input",
            "Sample ahp-stream supports url scheme fake:// (in-memory FakeAhpHost) only. \
             Wire AhpStreamClient with your WebSocket AhpTransport for live hosts \
             (see docs/ahp-client.md). Example: --url fake://demo",
        ));
    }

    let pair = InMemoryAhpTransportPair::new();
    let mut script = FakeAhpHostScript::new();
    if let Some(path) = snapshot_path {
        let text = fs::read_to_string(&path).map_err(|e| {
            TrajectoryError::new("invalid_input", format!("Could not read snapshot: {e}"))
        })?;
        script.initial_snapshot = Some(serde_json::from_str(&text).map_err(|e| {
            TrajectoryError::new("invalid_input", format!("Invalid snapshot JSON: {e}"))
        })?);
    }
    if let Some(path) = actions_path {
        let text = fs::read_to_string(&path).map_err(|e| {
            TrajectoryError::new("invalid_input", format!("Could not read actions: {e}"))
        })?;
        for line in text.lines() {
            if line.trim().is_empty() {
                continue;
            }
            let env: Value = serde_json::from_str(line).map_err(|e| {
                TrajectoryError::new("invalid_input", format!("Invalid action JSONL: {e}"))
            })?;
            script.initial_actions.push(env);
        }
    }
    if script.initial_snapshot.is_none() && script.initial_actions.is_empty() {
        script.initial_snapshot = Some(serde_json::json!({
            "ahpProtocolVersion": "0.7.0",
            "chat": { "id": chat, "turns": [], "activeTurn": null }
        }));
    }
    let token = token.or_else(|| env::var("TRAJECTORY_AHP_TOKEN").ok());
    if let Some(ref t) = token {
        script.require_auth = true;
        script.accept_token = Some(t.clone());
    }

    let _host = FakeAhpHost::new(pair.host, script, chat);
    let updates = Rc::new(RefCell::new(0usize));
    let ready = Rc::new(Cell::new(false));
    let show_content = cli.show_content;
    let mut options = AhpClientOptions::new(chat);
    options.from_server_seq = from_seq;
    let mut stream_opts = StreamOptions::new(TrajectorySource::Ahp).with_group_id(chat);
    stream_opts.delivery = delivery;
    options.stream_options = Some(stream_opts);
    if let Some(t) = token.clone() {
        options.auth = Some(Box::new(move |_challenge: Option<&Map<String, Value>>| {
            Some(hypabolic_trajectory_ahp::AhpAuthCredentials { token: t.clone() })
        }));
    }

    let updates_h = Rc::clone(&updates);
    let ready_h = Rc::clone(&ready);
    let client = AhpStreamClient::new(
        Box::new(pair.client),
        options,
        move |event: AhpClientEvent| match event.kind {
            AhpClientEventKind::StreamUpdate => {
                if let Some(update) = event.update {
                    let mut n = updates_h.borrow_mut();
                    *n += 1;
                    let index = *n;
                    drop(n);
                    print_stream_update(&update, show_content, emit, index);
                }
            }
            AhpClientEventKind::Ready => {
                ready_h.set(true);
                println!("AHP client ready (subscribe complete).");
            }
            AhpClientEventKind::AuthRequired
            | AhpClientEventKind::AuthFailed
            | AhpClientEventKind::ResyncRequired
            | AhpClientEventKind::Backpressure
            | AhpClientEventKind::Error
            | AhpClientEventKind::Disconnected => {
                println!(
                    "{}  {}",
                    event.code.as_deref().unwrap_or("event"),
                    event.message.as_deref().unwrap_or("")
                );
            }
        },
    );
    client.start();

    if let Some(max) = max_updates {
        let deadline = Instant::now() + Duration::from_secs(2);
        while *updates.borrow() < max && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
    } else {
        thread::sleep(Duration::from_millis(50));
    }

    let seen = *updates.borrow();
    if seen == 0 && !ready.get() {
        println!("No AHP ready/update events. Check --url / --chat / fixtures.");
    } else {
        println!("Emitted {seen} stream update(s). Cancel leaves last cursor valid; not a daemon.");
    }
    client.cancel();
    Ok(ExitCode::SUCCESS)
}

fn print_stream_update(update: &StreamUpdate, show_content: bool, emit: EmitArg, index: usize) {
    println!("── stream update #{index} ──");
    println!("kind       {}", update.kind);
    println!(
        "revision   {} id={} gen={}",
        update.revision.revision, update.revision.revision_id, update.revision.generation
    );
    let pos_kind = match &update.cursor.position {
        hypabolic_trajectory::StreamPosition::Byte(_) => "byte",
        hypabolic_trajectory::StreamPosition::AhpServerSeq(_) => "ahp-server-seq",
        hypabolic_trajectory::StreamPosition::SnapshotRevision(_) => "snapshot-revision",
        hypabolic_trajectory::StreamPosition::HermesRow(_) => "hermes-row",
    };
    println!(
        "cursor     source={} group={} gen={} pos={pos_kind}",
        update.cursor.source,
        truncate(&update.cursor.group_id, 40),
        update.cursor.generation
    );
    if let Some(snapshot) = &update.snapshot {
        println!(
            "snapshot   records={} complete={}",
            snapshot.records.len(),
            snapshot.complete
        );
    } else if matches!(emit, EmitArg::SnapshotDelta | EmitArg::Snapshot) {
        println!("snapshot   (omitted by delivery)");
    }
    if let Some(delta) = &update.delta {
        let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
        for op in &delta.operations {
            *counts.entry(op.op.as_str()).or_default() += 1;
        }
        let summary = counts
            .iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect::<Vec<_>>()
            .join(", ");
        if summary.is_empty() {
            println!("delta      ops={}", delta.operations.len());
        } else {
            println!("delta      ops={} ({summary})", delta.operations.len());
        }
    } else if matches!(emit, EmitArg::SnapshotDelta | EmitArg::Delta) {
        println!("delta      (omitted by delivery)");
    }
    println!("diagnostics {}", update.diagnostics.len());
    for diagnostic in update.diagnostics.iter().take(8) {
        println!("  {}  {}", diagnostic.code, diagnostic.message);
    }
    if let Some(reset) = &update.reset {
        println!("reset      reason={}", reset.reason);
    }
    if let Some((code, message)) = &update.error {
        println!("error      {code}: {message}");
    }
    if show_content {
        if let Some(snapshot) = &update.snapshot {
            println!("\nWARNING: --show-content prints transcript-derived text. Treat as private.");
            for (i, stream_rec) in snapshot.records.iter().enumerate().take(40) {
                let role = stream_rec
                    .record
                    .get("role")
                    .and_then(Value::as_str)
                    .unwrap_or("?");
                let kind = stream_rec
                    .record
                    .get("kind")
                    .or_else(|| stream_rec.record.get("type"))
                    .and_then(Value::as_str)
                    .unwrap_or("?");
                let snip = stream_rec
                    .record
                    .get("content")
                    .and_then(Value::as_str)
                    .map_or_else(
                        || truncate(&stream_rec.record.to_string(), 80),
                        |c| truncate(c, 80),
                    );
                println!(
                    "  {:>3}  {:<12} {:<10} {:<20} {snip}",
                    i + 1,
                    stream_rec.status,
                    role,
                    kind
                );
            }
        }
    } else {
        println!("Content omitted (privacy). Re-run with --show-content for snippets.");
    }
    println!();
}

fn run_list(cli: &Cli) -> Result<ExitCode, TrajectoryError> {
    let source = match cli.source {
        Some(value) => value,
        None => prompt_source()?,
    };
    let root = resolve_root(source, cli.root.as_deref());
    println!("Source {}  root {}", source.wire_name(), root.display());
    let page = list_source(source, &root, cli.limit)?;
    if page.items.is_empty() {
        print_empty(source);
        return Ok(ExitCode::SUCCESS);
    }
    print_listing(&page.items);
    if page.next_cursor.is_some() {
        println!(
            "More sessions available. Showing first {}.",
            page.items.len()
        );
    }
    Ok(ExitCode::SUCCESS)
}

fn run_show(
    cli: &Cli,
    path: Option<PathBuf>,
    id: Option<String>,
    format: FormatArg,
) -> Result<ExitCode, TrajectoryError> {
    let source = cli.source.unwrap_or(SourceArg::Pi);
    let root = resolve_root(source, cli.root.as_deref());
    let (path, group_id) = resolve_path(source, &root, path, id, cli.limit)?;
    print_summary(source, &path, group_id.as_deref(), cli.show_content, format)
}

fn run_browse(cli: &Cli) -> Result<ExitCode, TrajectoryError> {
    println!("Trajectory  local sample TUI (unpublished)");
    println!("Privacy: content is hidden unless --show-content.\n");

    let source = match cli.source {
        Some(value) => value,
        None => prompt_source()?,
    };
    let root = resolve_root(source, cli.root.as_deref());
    println!(
        "Default for {}: {}",
        source.wire_name(),
        describe_default(source)
    );
    println!("Using root {}\n", root.display());

    let page = list_source(source, &root, cli.limit)?;
    if page.items.is_empty() {
        print_empty(source);
        let confirm = Confirm::with_theme(&ColorfulTheme::default())
            .with_prompt("Normalize a transcript file by path instead?")
            .default(false)
            .interact()
            .map_err(|error| io_error("Could not read confirmation.", &error))?;
        if confirm {
            let path = dialoguer::Input::<String>::with_theme(&ColorfulTheme::default())
                .with_prompt("Path to transcript")
                .interact_text()
                .map_err(|error| io_error("Could not read path.", &error))?;
            return print_summary(
                source,
                Path::new(&expand_home(&path)),
                None,
                cli.show_content,
                FormatArg::Both,
            );
        }
        return Ok(ExitCode::SUCCESS);
    }

    let mut labels: Vec<String> = page
        .items
        .iter()
        .map(|item| {
            format!(
                "{} | {} | {} | {}",
                item.id,
                item.updated_at,
                format_bytes(item.size_bytes),
                item.path.display()
            )
        })
        .collect();
    labels.push("(quit)".to_owned());

    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt(format!("Select a session ({} shown)", page.items.len()))
        .items(&labels)
        .default(0)
        .interact()
        .map_err(|error| io_error("Could not read selection.", &error))?;

    if selection >= page.items.len() {
        return Ok(ExitCode::SUCCESS);
    }

    println!();
    let selected = &page.items[selection];
    print_summary(
        source,
        &selected.path,
        Some(selected.id.as_str()),
        cli.show_content,
        FormatArg::Both,
    )
}

fn resolve_path(
    source: SourceArg,
    root: &Path,
    path: Option<PathBuf>,
    id: Option<String>,
    limit: usize,
) -> Result<(PathBuf, Option<String>), TrajectoryError> {
    if let Some(path) = path {
        return Ok((path, id));
    }
    let Some(id) = id else {
        return Err(TrajectoryError::new(
            "invalid_input",
            "Provide --path or --id.",
        ));
    };
    let page = list_source(source, root, limit)?;
    page.items
        .into_iter()
        .find(|item| item.id == id)
        .map(|item| (item.path, Some(item.id)))
        .ok_or_else(|| {
            TrajectoryError::new(
                "invalid_input",
                format!("Session id '{id}' not found under {}.", root.display()),
            )
        })
}

fn print_summary(
    source: SourceArg,
    path: &Path,
    group_id: Option<&str>,
    show_content: bool,
    format: FormatArg,
) -> Result<ExitCode, TrajectoryError> {
    if !path.exists() {
        return Err(TrajectoryError::new(
            "invalid_input",
            format!("File not found: {}", path.display()),
        ));
    }

    let bytes = fs::read(path).map_err(|error| {
        TrajectoryError::new(
            "invalid_input",
            format!("Could not read transcript: {error}"),
        )
    })?;

    let trajectory = normalize_bytes(source, &bytes, group_id)?;

    println!("── {} {} ──", source.wire_name(), path_file_name(path));
    println!("path     {}", path.display());
    println!("group    {}", trajectory.group_id);
    println!("source   {}", trajectory.source_name);
    println!("records  {}", trajectory.records.len());
    println!(
        "partial  {}",
        trajectory.config.partial || trajectory.config.base_byte_offset > 0
    );

    let mut role_counts: BTreeMap<&'static str, usize> = BTreeMap::new();
    let mut tool_names: Vec<String> = Vec::new();
    for record in &trajectory.records {
        *role_counts.entry(role_name(record.role)).or_default() += 1;
        if record.kind == RecordKind::AssistantToolCalls {
            for call in &record.tool_calls {
                tool_names.push(call.name.clone());
            }
        }
    }
    let unique_tools: BTreeSet<_> = tool_names.iter().cloned().collect();
    let roles = role_counts
        .iter()
        .map(|(role, count)| format!("{role}={count}"))
        .collect::<Vec<_>>()
        .join(", ");

    println!("Roles       {roles}");
    println!(
        "Tool calls  {} total, {} unique",
        tool_names.len(),
        unique_tools.len()
    );
    if !unique_tools.is_empty() {
        let shown: Vec<_> = unique_tools.iter().take(12).cloned().collect();
        let mut tools = shown.join(", ");
        if unique_tools.len() > 12 {
            tools.push('…');
        }
        println!("Tools       {tools}");
    }
    println!("Diagnostics {}", trajectory.diagnostics.len());
    for diagnostic in trajectory.diagnostics.iter().take(12) {
        println!("  {}  {}", diagnostic.code, diagnostic.message);
    }
    if trajectory.diagnostics.len() > 12 {
        println!(
            "…and {} more diagnostics",
            trajectory.diagnostics.len() - 12
        );
    }

    match format {
        FormatArg::Both | FormatArg::Hypabolic => match project_hypabolic(&trajectory) {
            Ok(json) => match serde_json::from_str::<serde_json::Value>(&json) {
                Ok(value) => {
                    let trajectory_id = value
                        .get("trajectoryId")
                        .or_else(|| value.get("trajectory_id"))
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("?");
                    let schema_id = value
                        .get("schemaId")
                        .or_else(|| value.get("schema_id"))
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("?");
                    let schema_version = value
                        .get("schemaVersion")
                        .or_else(|| value.get("schema_version"))
                        .cloned()
                        .unwrap_or(serde_json::Value::Null);
                    let records = value
                        .get("records")
                        .and_then(serde_json::Value::as_array)
                        .map_or(0, Vec::len);
                    println!(
                        "\nHypabolic trajectoryId={trajectory_id} schema={schema_id} v{schema_version} records={records}"
                    );
                }
                Err(_) => println!("\nHypabolic projection produced {} bytes", json.len()),
            },
            Err(error) => println!("Hypabolic projection skipped: {}", error.message),
        },
        FormatArg::Messages => {}
    }

    match format {
        FormatArg::Both | FormatArg::Messages => match project_letta(&trajectory) {
            Ok(json) => match serde_json::from_str::<serde_json::Value>(&json) {
                Ok(value) => {
                    let records = value
                        .get("records")
                        .and_then(serde_json::Value::as_array)
                        .map_or(0, Vec::len);
                    let diagnostics = value
                        .get("diagnostics")
                        .and_then(serde_json::Value::as_array)
                        .map_or(0, Vec::len);
                    println!("Messages records={records} diagnostics={diagnostics}");
                }
                Err(_) => println!("Message trajectory produced {} bytes", json.len()),
            },
            Err(error) => println!("Message trajectory skipped: {}", error.message),
        },
        FormatArg::Hypabolic => {}
    }

    if show_content {
        println!("\nWARNING: --show-content prints transcript-derived text. Treat as private.");
        for (index, record) in trajectory.records.iter().enumerate().take(40) {
            let snippet = snippet_for(record);
            println!(
                "  {:>3}  {:<10} {:<20} {}",
                index + 1,
                role_name(record.role),
                kind_name(record.kind),
                snippet
            );
        }
        if trajectory.records.len() > 40 {
            println!("Showing first 40 of {} records.", trajectory.records.len());
        }
    } else {
        println!("Content omitted (privacy). Re-run with --show-content to include snippets.");
    }

    let _ = io::stdout().flush();
    Ok(ExitCode::SUCCESS)
}

fn snippet_for(record: &hypabolic_trajectory::IrRecord) -> String {
    match record.kind {
        RecordKind::AssistantToolCalls => {
            let text = record
                .tool_calls
                .iter()
                .map(|call| format!("{}({})", call.name, truncate(&call.arguments_json, 40)))
                .collect::<Vec<_>>()
                .join(", ");
            truncate(&text, 120)
        }
        RecordKind::Meta => truncate(
            &format!(
                "source={} model={}",
                record.source_name.as_deref().unwrap_or("?"),
                record.model.as_deref().unwrap_or("—")
            ),
            120,
        ),
        _ => truncate(record.content.as_deref().unwrap_or("—"), 120),
    }
}

fn normalize_bytes(
    source: SourceArg,
    bytes: &[u8],
    group_id: Option<&str>,
) -> Result<Trajectory, TrajectoryError> {
    let request = NormalizeRequest {
        transcript: bytes,
        source_context: SourceContext {
            group_id,
            ..SourceContext::default()
        },
        options: NormalizeOptions::default(),
    };
    match source {
        SourceArg::Pi => normalize_pi(request),
        SourceArg::ClaudeCode => normalize_claude_code(request),
        SourceArg::Codex => normalize_codex(request),
        SourceArg::Openclaw => normalize_openclaw(request),
        SourceArg::Hermes => normalize_hermes(request),
        SourceArg::Ahp => normalize_ahp(request),
        SourceArg::GrokBuild => normalize_grok_build(request),
    }
}

fn list_source(
    source: SourceArg,
    root: &Path,
    limit: usize,
) -> Result<hypabolic_trajectory::TrajectoryListingPage, TrajectoryError> {
    let options = ListingOptions {
        root,
        cursor: None,
        limit,
    };
    match source {
        SourceArg::Pi => list_pi_trajectories(&options),
        SourceArg::ClaudeCode => list_claude_code_trajectories(&options),
        SourceArg::Codex => list_codex_trajectories(&options),
        SourceArg::Openclaw => list_openclaw_trajectories(&options),
        SourceArg::Hermes => list_hermes_trajectories(&options),
        SourceArg::Ahp => list_ahp_trajectories(&options),
        SourceArg::GrokBuild => list_grok_build_trajectories(&options),
    }
}

fn resolve_root(source: SourceArg, root_override: Option<&Path>) -> PathBuf {
    if let Some(root) = root_override {
        return expand_home_path(root);
    }

    let env_key = match source {
        SourceArg::Pi => "TRAJECTORY_PI_ROOT",
        SourceArg::ClaudeCode => "TRAJECTORY_CLAUDE_CODE_ROOT",
        SourceArg::Codex => "TRAJECTORY_CODEX_ROOT",
        SourceArg::Openclaw => "TRAJECTORY_OPENCLAW_ROOT",
        SourceArg::Hermes => "TRAJECTORY_HERMES_ROOT",
        SourceArg::Ahp => "TRAJECTORY_AHP_ROOT",
        SourceArg::GrokBuild => "TRAJECTORY_GROK_BUILD_ROOT",
    };
    if let Ok(value) = env::var(env_key) {
        if !value.trim().is_empty() {
            return PathBuf::from(expand_home(value.trim()));
        }
    }

    let home = home_dir();
    match source {
        SourceArg::Pi => {
            if let Ok(value) = env::var("PI_CODING_AGENT_DIR") {
                if !value.trim().is_empty() {
                    return PathBuf::from(expand_home(value.trim()));
                }
            }
            home.join(".pi").join("agent")
        }
        SourceArg::ClaudeCode => home.join(".claude").join("projects"),
        SourceArg::Codex => home.join(".codex").join("sessions"),
        SourceArg::Openclaw => {
            if let Ok(value) =
                env::var("OPENCLAW_STATE_DIR").or_else(|_| env::var("CLAWDBOT_STATE_DIR"))
            {
                if !value.trim().is_empty() {
                    return PathBuf::from(expand_home(value.trim()));
                }
            }
            let openclaw = home.join(".openclaw");
            if openclaw.exists() {
                openclaw
            } else {
                home.join(".clawdbot")
            }
        }
        SourceArg::Hermes => home.join(".hermes"),
        SourceArg::Ahp => home,
        SourceArg::GrokBuild => {
            if let Ok(value) = env::var("GROK_HOME") {
                if !value.trim().is_empty() {
                    return PathBuf::from(expand_home(value.trim())).join("sessions");
                }
            }
            home.join(".grok").join("sessions")
        }
    }
}

fn describe_default(source: SourceArg) -> &'static str {
    match source {
        SourceArg::Pi => "~/.pi/agent (or PI_CODING_AGENT_DIR)",
        SourceArg::ClaudeCode => "~/.claude/projects",
        SourceArg::Codex => "~/.codex/sessions",
        SourceArg::Openclaw => {
            "~/.openclaw if present, else ~/.clawdbot (or OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR)"
        }
        SourceArg::Hermes => "~/.hermes/state.db",
        SourceArg::Ahp => "explicit export root only (no home default)",
        SourceArg::GrokBuild => {
            "~/.grok/sessions (or $GROK_HOME/sessions / TRAJECTORY_GROK_BUILD_ROOT)"
        }
    }
}

fn prompt_source() -> Result<SourceArg, TrajectoryError> {
    let labels: Vec<&str> = SourceArg::ALL
        .iter()
        .map(|source| source.wire_name())
        .collect();
    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("Which agent source should we browse?")
        .items(&labels)
        .default(0)
        .interact()
        .map_err(|error| io_error("Could not read source selection.", &error))?;
    Ok(SourceArg::ALL[selection])
}

fn print_listing(items: &[TrajectoryListing]) {
    println!("{:<36}  {:<24}  {:>8}  Path", "Id", "Updated (UTC)", "Size");
    println!("{}", "-".repeat(90));
    for item in items {
        println!(
            "{:<36}  {:<24}  {:>8}  {}",
            truncate(&item.id, 36),
            item.updated_at,
            format_bytes(item.size_bytes),
            item.path.display()
        );
    }
}

fn print_empty(source: SourceArg) {
    println!("No sessions found. Empty or missing store is not an error.");
    if matches!(source, SourceArg::Hermes) {
        println!(
            "Hermes core listing is SQLite-free and returns empty pages. Export message JSON and use show --path."
        );
    }
}

fn format_bytes(bytes: u64) -> String {
    const UNITS: [&str; 4] = ["B", "KB", "MB", "GB"];
    if bytes < 1024 {
        return format!("{bytes} B");
    }
    let mut scaled = bytes;
    let mut unit = 0usize;
    // Keep one fractional decimal via integer tenths to avoid f64 cast warnings.
    while scaled >= 1024 * 1024 && unit < UNITS.len() - 2 {
        scaled /= 1024;
        unit += 1;
    }
    // scaled is in current unit; convert one more step for display.
    let whole = scaled / 1024;
    let tenths = ((scaled % 1024) * 10) / 1024;
    unit += 1;
    if whole >= 10 || tenths == 0 {
        format!("{whole} {}", UNITS[unit])
    } else {
        format!("{whole}.{tenths} {}", UNITS[unit])
    }
}

fn truncate(value: &str, max: usize) -> String {
    if value.chars().count() <= max {
        value.to_owned()
    } else {
        let mut out: String = value.chars().take(max.saturating_sub(1)).collect();
        out.push('…');
        out
    }
}

fn path_file_name(path: &Path) -> String {
    path.file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_else(|| path.to_str().unwrap_or("?"))
        .to_owned()
}

fn home_dir() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn expand_home(path: &str) -> String {
    if path == "~" {
        home_dir().display().to_string()
    } else if let Some(rest) = path.strip_prefix("~/") {
        home_dir().join(rest).display().to_string()
    } else {
        path.to_owned()
    }
}

fn expand_home_path(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    PathBuf::from(expand_home(&text))
}

fn io_error(message: &str, error: &dyn std::fmt::Display) -> TrajectoryError {
    TrajectoryError::new("invalid_input", format!("{message} {error}"))
}

fn role_name(role: hypabolic_trajectory::Role) -> &'static str {
    match role {
        hypabolic_trajectory::Role::Meta => "meta",
        hypabolic_trajectory::Role::User => "user",
        hypabolic_trajectory::Role::Reasoning => "reasoning",
        hypabolic_trajectory::Role::Assistant => "assistant",
        hypabolic_trajectory::Role::Tool => "tool",
    }
}

fn kind_name(kind: RecordKind) -> &'static str {
    match kind {
        RecordKind::Meta => "meta",
        RecordKind::Message => "message",
        RecordKind::AssistantToolCalls => "assistant_tool_calls",
        RecordKind::ToolResult => "tool_result",
    }
}

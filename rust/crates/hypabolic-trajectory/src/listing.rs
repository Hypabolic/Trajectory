use std::fs;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;

use crate::TrajectoryError;
use crate::canonical::utf16_compare;
use crate::projection::format_ms;

/// Options for explicit-root store discovery.
#[derive(Debug, Clone)]
pub struct ListingOptions<'a> {
    /// Store root. The implementation never consults the process home directory.
    pub root: &'a Path,
    /// Opaque cursor from a previous page.
    pub cursor: Option<&'a str>,
    /// Page size from 1 through 1,000.
    pub limit: usize,
}

/// One listed transcript.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrajectoryListing {
    /// File stem.
    pub id: String,
    /// Native filesystem locator (absolute when the store root is absolute or
    /// can be resolved against the process working directory).
    pub path: PathBuf,
    /// UTC millisecond modification time.
    pub updated_at: String,
    /// Optional human title when the store provides one.
    pub title: Option<String>,
    /// File byte length.
    pub size_bytes: u64,
}

/// One deterministic listing page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrajectoryListingPage {
    /// Current page.
    pub items: Vec<TrajectoryListing>,
    /// Opaque continuation cursor.
    pub next_cursor: Option<String>,
}

/// Lists Pi JSONL transcripts below `<root>/sessions`.
pub fn list_pi_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    list_project_store(
        options,
        &options.root.join("sessions"),
        "Pi",
        TitleSource::GenericUser,
    )
}

/// Lists Claude Code JSONL transcripts below the explicit projects root.
pub fn list_claude_code_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    list_project_store(options, options.root, "Claude Code", TitleSource::Claude)
}

/// Lists Codex rollout JSONL transcripts recursively to four directory levels.
pub fn list_codex_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    validate_limit(options.limit)?;
    let mut items = Vec::new();
    collect_codex(options.root, 4, &mut items)?;
    paginate(options, items)
}

/// Lists Hermes sessions from a `SQLite` store locator.
///
/// Core packages stay SQLite-free: a missing store yields an empty page. Full
/// sessions-table enumeration is optional and provider-side. `root` may be the
/// AHP export-directory listing (Phase 3). Phase 1 returns an empty page for any root.
pub fn list_ahp_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    validate_limit(options.limit)?;
    let _ = options.root; // Phase 3 will scan the export directory.
    Ok(TrajectoryListingPage {
        items: Vec::new(),
        next_cursor: None,
    })
}

/// `state.db` path or the directory containing it (default `~/.hermes`).
pub fn list_hermes_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    validate_limit(options.limit)?;
    let _store = resolve_hermes_store_path(options.root);
    // Without an embedded SQLite reader, presence alone cannot yield rows.
    Ok(TrajectoryListingPage {
        items: Vec::new(),
        next_cursor: None,
    })
}

fn resolve_hermes_store_path(root: &Path) -> PathBuf {
    if root
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("db"))
    {
        root.to_path_buf()
    } else {
        root.join("state.db")
    }
}

/// Lists Grok Build `chat_history.jsonl` transcripts under `<root>/*/*/chat_history.jsonl`.
///
/// Discovery walks one CWD-directory level then one session-directory level. Item IDs are
/// session directory names. Prefer `summary.json` `last_active_at` then `updated_at` for
/// `updated_at`; otherwise use the history file mtime.
pub fn list_grok_build_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    validate_limit(options.limit)?;
    let root = absolute_path(options.root);
    let mut items = Vec::new();
    let cwd_dirs = match fs::read_dir(&root) {
        Ok(value) => value,
        Err(error) if missing_or_denied(&error) => {
            return Ok(TrajectoryListingPage {
                items: Vec::new(),
                next_cursor: None,
            });
        }
        Err(error) => {
            return Err(io_error(
                "Could not enumerate the Grok Build sessions root.",
                &error,
            ));
        }
    };
    for cwd_entry in cwd_dirs {
        let cwd_entry = cwd_entry.map_err(|error| {
            io_error("Could not enumerate the Grok Build sessions root.", &error)
        })?;
        let cwd_type = match cwd_entry.file_type() {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => {
                return Err(io_error(
                    "Could not inspect a Grok Build store entry.",
                    &error,
                ));
            }
        };
        if !cwd_type.is_dir() {
            continue;
        }
        let sessions = match fs::read_dir(cwd_entry.path()) {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => {
                return Err(io_error(
                    "Could not enumerate a Grok Build CWD directory.",
                    &error,
                ));
            }
        };
        for session_entry in sessions {
            let session_entry = session_entry.map_err(|error| {
                io_error("Could not enumerate a Grok Build CWD directory.", &error)
            })?;
            let session_type = match session_entry.file_type() {
                Ok(value) => value,
                Err(error) if missing_or_denied(&error) => continue,
                Err(error) => {
                    return Err(io_error(
                        "Could not inspect a Grok Build session entry.",
                        &error,
                    ));
                }
            };
            if !session_type.is_dir() {
                continue;
            }
            let history = session_entry.path().join("chat_history.jsonl");
            let metadata = match fs::metadata(&history) {
                Ok(value) if value.is_file() => value,
                Ok(_) => continue,
                Err(error) if missing_or_denied(&error) => continue,
                Err(error) => {
                    return Err(io_error(
                        "Could not inspect a Grok Build chat history.",
                        &error,
                    ));
                }
            };
            let id = session_entry
                .file_name()
                .to_str()
                .ok_or_else(|| {
                    TrajectoryError::new(
                        "invalid_input",
                        "A Grok Build session directory name is not valid Unicode.",
                    )
                })?
                .to_owned();
            let (updated_at, title) = grok_build_summary_meta(&session_entry.path(), &metadata)?;
            items.push(TrajectoryListing {
                id,
                path: history,
                updated_at,
                title,
                size_bytes: metadata.len(),
            });
        }
    }
    paginate(options, items)
}

/// Lists Cursor Agent transcripts under `projects/*/agent-transcripts/*`.
pub fn list_cursor_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    validate_limit(options.limit)?;
    let root = absolute_path(options.root);
    let mut items = Vec::new();
    let mut meta = Vec::new();
    if let Ok(hashes) = fs::read_dir(root.join("chats")) {
        for hash in hashes.flatten() {
            if !hash.file_type().is_ok_and(|kind| kind.is_dir()) {
                continue;
            }
            if let Ok(sessions) = fs::read_dir(hash.path()) {
                for session in sessions.flatten() {
                    let path = session.path().join("meta.json");
                    let Ok(text) = fs::read_to_string(path) else {
                        continue;
                    };
                    let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
                        continue;
                    };
                    if value.is_object() {
                        meta.push((session.file_name().to_string_lossy().into_owned(), value));
                    }
                }
            }
        }
    }
    let projects = match fs::read_dir(root.join("projects")) {
        Ok(value) => value,
        Err(error) if missing_or_denied(&error) => {
            return Ok(TrajectoryListingPage {
                items,
                next_cursor: None,
            });
        }
        Err(error) => return Err(io_error("Could not enumerate the Cursor store.", &error)),
    };
    for project in projects.flatten() {
        if !project.file_type().is_ok_and(|kind| kind.is_dir()) {
            continue;
        }
        let sessions_root = project.path().join("agent-transcripts");
        let Ok(sessions) = fs::read_dir(sessions_root) else {
            continue;
        };
        for session in sessions.flatten() {
            if !session.file_type().is_ok_and(|kind| kind.is_dir()) {
                continue;
            }
            let id = session.file_name().to_string_lossy().into_owned();
            let path = session.path().join(format!("{id}.jsonl"));
            let Ok(metadata) = fs::metadata(&path) else {
                continue;
            };
            if !metadata.is_file()
                || path.file_stem().and_then(|value| value.to_str()) != Some(id.as_str())
            {
                continue;
            }
            let found = meta
                .iter()
                .find(|(key, _)| key == &id)
                .map(|(_, value)| value);
            let updated = found
                .and_then(|value| value.get("updatedAtMs"))
                .and_then(serde_json::Value::as_i64)
                .and_then(|value| format_ms(value).ok())
                .unwrap_or_else(|| {
                    metadata
                        .modified()
                        .ok()
                        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                        .and_then(|value| i64::try_from(value.as_millis()).ok())
                        .and_then(|value| format_ms(value).ok())
                        .unwrap_or_default()
                });
            let title = found
                .and_then(|value| value.get("title"))
                .and_then(serde_json::Value::as_str)
                .and_then(format_title)
                .or_else(|| derive_cursor_title(&path));
            items.push(TrajectoryListing {
                id,
                path,
                updated_at: updated,
                title,
                size_bytes: metadata.len(),
            });
        }
    }
    paginate(options, items)
}

fn absolute_path(path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    }
}

fn grok_build_summary_meta(
    session_dir: &Path,
    history_metadata: &fs::Metadata,
) -> Result<(String, Option<String>), TrajectoryError> {
    let summary_path = session_dir.join("summary.json");
    let mut title = None;
    if let Ok(text) = fs::read_to_string(&summary_path) {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(object) = value.as_object() {
                title = object
                    .get("generated_title")
                    .and_then(serde_json::Value::as_str)
                    .filter(|value| !value.is_empty())
                    .map(str::to_owned)
                    .or_else(|| {
                        object
                            .get("session_summary")
                            .and_then(serde_json::Value::as_str)
                            .filter(|value| !value.is_empty())
                            .map(str::to_owned)
                    });
                for key in ["last_active_at", "updated_at"] {
                    if let Some(timestamp) = object.get(key).and_then(serde_json::Value::as_str) {
                        if let Ok(parsed) = chrono::DateTime::parse_from_rfc3339(timestamp) {
                            return Ok((format_ms(parsed.timestamp_millis())?, title));
                        }
                    }
                }
            }
        }
    }
    let modified = history_metadata
        .modified()
        .map_err(|error| io_error("Could not read a Grok Build transcript timestamp.", &error))?
        .duration_since(UNIX_EPOCH)
        .map_err(|_| {
            TrajectoryError::new(
                "invalid_input",
                "A Grok Build transcript timestamp precedes the Unix epoch.",
            )
        })?;
    let milliseconds = i64::try_from(modified.as_millis()).map_err(|_| {
        TrajectoryError::new(
            "invalid_input",
            "A Grok Build transcript timestamp is out of range.",
        )
    })?;
    Ok((format_ms(milliseconds)?, title))
}

/// Lists `OpenClaw` JSONL transcripts below `<root>/agents/*/sessions`.
pub fn list_openclaw_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    validate_limit(options.limit)?;
    let mut items = Vec::new();
    let agents_root = options.root.join("agents");
    let agents = match fs::read_dir(&agents_root) {
        Ok(value) => value,
        Err(error) if missing_or_denied(&error) => {
            return Ok(TrajectoryListingPage {
                items: Vec::new(),
                next_cursor: None,
            });
        }
        Err(error) => {
            return Err(io_error("Could not enumerate the OpenClaw store.", &error));
        }
    };
    for agent in agents {
        let agent =
            agent.map_err(|error| io_error("Could not enumerate the OpenClaw store.", &error))?;
        let file_type = match agent.file_type() {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => {
                return Err(io_error(
                    "Could not inspect an OpenClaw store entry.",
                    &error,
                ));
            }
        };
        if !file_type.is_dir() {
            continue;
        }
        let sessions = agent.path().join("sessions");
        let files = match fs::read_dir(&sessions) {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => {
                return Err(io_error(
                    "Could not enumerate an OpenClaw agent sessions directory.",
                    &error,
                ));
            }
        };
        for file in files {
            let file = file.map_err(|error| {
                io_error(
                    "Could not enumerate an OpenClaw agent sessions directory.",
                    &error,
                )
            })?;
            let path = file.path();
            if path.extension().and_then(|value| value.to_str()) != Some("jsonl") {
                continue;
            }
            let metadata = match file.metadata() {
                Ok(value) if value.is_file() => value,
                Ok(_) => continue,
                Err(error) if missing_or_denied(&error) => continue,
                Err(error) => {
                    return Err(io_error(
                        "Could not inspect an OpenClaw transcript.",
                        &error,
                    ));
                }
            };
            items.push(listing_from_file(
                path,
                &metadata,
                "OpenClaw",
                TitleSource::GenericUser,
            )?);
        }
    }
    paginate(options, items)
}

#[derive(Clone, Copy)]
enum TitleSource {
    Codex,
    Claude,
    GenericUser,
}

fn list_project_store(
    options: &ListingOptions<'_>,
    projects_root: &Path,
    source_name: &str,
    title_source: TitleSource,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    validate_limit(options.limit)?;

    let projects = match fs::read_dir(projects_root) {
        Ok(value) => value,
        Err(error) if missing_or_denied(&error) => {
            return Ok(TrajectoryListingPage {
                items: Vec::new(),
                next_cursor: None,
            });
        }
        Err(error) => {
            return Err(io_error(
                &format!("Could not enumerate the {source_name} store."),
                &error,
            ));
        }
    };

    let mut items = Vec::new();
    for project in projects {
        let project = project.map_err(|error| {
            io_error(
                &format!("Could not enumerate the {source_name} store."),
                &error,
            )
        })?;
        let file_type = match project.file_type() {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => {
                return Err(io_error(
                    &format!("Could not inspect a {source_name} store entry."),
                    &error,
                ));
            }
        };
        if !file_type.is_dir() {
            continue;
        }
        let files = match fs::read_dir(project.path()) {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => {
                return Err(io_error(
                    &format!("Could not enumerate a {source_name} project."),
                    &error,
                ));
            }
        };
        for file in files {
            let file = file.map_err(|error| {
                io_error(
                    &format!("Could not enumerate a {source_name} project."),
                    &error,
                )
            })?;
            let path = file.path();
            if path.extension().and_then(|value| value.to_str()) != Some("jsonl") {
                continue;
            }
            let metadata = match file.metadata() {
                Ok(value) if value.is_file() => value,
                Ok(_) => continue,
                Err(error) if missing_or_denied(&error) => continue,
                Err(error) => {
                    return Err(io_error(
                        &format!("Could not inspect a {source_name} transcript."),
                        &error,
                    ));
                }
            };
            items.push(listing_from_file(
                path,
                &metadata,
                source_name,
                title_source,
            )?);
        }
    }

    paginate(options, items)
}

fn collect_codex(
    directory: &Path,
    remaining_depth: usize,
    items: &mut Vec<TrajectoryListing>,
) -> Result<(), TrajectoryError> {
    let entries = match fs::read_dir(directory) {
        Ok(value) => value,
        Err(error) if missing_or_denied(&error) => return Ok(()),
        Err(error) => return Err(io_error("Could not enumerate the Codex store.", &error)),
    };
    for entry in entries {
        let entry =
            entry.map_err(|error| io_error("Could not enumerate the Codex store.", &error))?;
        let file_type = match entry.file_type() {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => return Err(io_error("Could not inspect a Codex store entry.", &error)),
        };
        if file_type.is_dir() {
            if remaining_depth > 0 {
                collect_codex(&entry.path(), remaining_depth - 1, items)?;
            }
            continue;
        }
        let path = entry.path();
        if !file_type.is_file()
            || path.extension().and_then(|value| value.to_str()) != Some("jsonl")
        {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => return Err(io_error("Could not inspect a Codex transcript.", &error)),
        };
        items.push(listing_from_file(
            path,
            &metadata,
            "Codex",
            TitleSource::Codex,
        )?);
    }
    Ok(())
}

const TITLE_SCAN_MAX_BYTES: usize = 64 * 1024;
const TITLE_SCAN_MAX_LINES: usize = 200;
const TITLE_MAX_SCALARS: usize = 120;
const LISTING_NOISE_MARKERS: &[&str] = &[
    "# agents.md",
    "<instructions>",
    "</instructions>",
    "<environment_context>",
    "<skills_instructions>",
    "<skills>",
    "<permissions instructions>",
    "<user_instructions>",
    "<turn_context>",
    "<collaboration",
    "filesystem sandboxing",
    "<cwd>",
    "you are a coding agent",
    "you are chatgpt",
    "# claude.md",
    "agenthub instructions",
    "<command-name>",
    "<local-command-caveat>",
    "<task-notification",
];

fn listing_from_file(
    path: PathBuf,
    metadata: &fs::Metadata,
    source_name: &str,
    title_source: TitleSource,
) -> Result<TrajectoryListing, TrajectoryError> {
    let id = path
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or_else(|| {
            TrajectoryError::new(
                "invalid_input",
                format!("A {source_name} transcript filename is not valid Unicode."),
            )
        })?
        .to_owned();
    let modified = metadata
        .modified()
        .map_err(|error| {
            io_error(
                &format!("Could not read a {source_name} transcript timestamp."),
                &error,
            )
        })?
        .duration_since(UNIX_EPOCH)
        .map_err(|_| {
            TrajectoryError::new(
                "invalid_input",
                format!("A {source_name} transcript timestamp precedes the Unix epoch."),
            )
        })?;
    let milliseconds = i64::try_from(modified.as_millis()).map_err(|_| {
        TrajectoryError::new(
            "invalid_input",
            format!("A {source_name} transcript timestamp is out of range."),
        )
    })?;
    let title = match title_source {
        TitleSource::Codex => derive_codex_title(&path),
        TitleSource::Claude => derive_claude_title(&path),
        TitleSource::GenericUser => derive_generic_user_title(&path),
    };
    Ok(TrajectoryListing {
        id,
        path,
        updated_at: format_ms(milliseconds)?,
        title,
        size_bytes: metadata.len(),
    })
}

fn derive_codex_title(path: &Path) -> Option<String> {
    let mut session_id = None;
    for value in scan_json_lines(path) {
        let record_type = value.get("type").and_then(serde_json::Value::as_str);
        let payload = value.get("payload").and_then(serde_json::Value::as_object);
        if record_type == Some("session_meta") {
            if let Some(id) = payload
                .and_then(|object| object.get("id"))
                .and_then(serde_json::Value::as_str)
                .filter(|id| !id.is_empty())
            {
                session_id = Some(id.to_owned());
            }
            continue;
        }
        if record_type == Some("response_item") {
            let role = payload
                .and_then(|object| object.get("role"))
                .and_then(serde_json::Value::as_str);
            if matches!(role, Some("developer" | "system")) {
                continue;
            }
            if role == Some("user") {
                let text = blocks_to_text(payload.and_then(|object| object.get("content")))
                    .unwrap_or_default();
                if let Some(title) = title_from_user_text(&text) {
                    return Some(title);
                }
            }
            continue;
        }
        if record_type == Some("event_msg") {
            let event_type = payload
                .and_then(|object| object.get("type"))
                .and_then(serde_json::Value::as_str);
            if matches!(event_type, Some("user_message" | "user_prompt" | "message")) {
                let text = blocks_to_text(payload.and_then(|object| object.get("message")))
                    .or_else(|| blocks_to_text(payload.and_then(|object| object.get("content"))))
                    .or_else(|| {
                        payload
                            .and_then(|object| object.get("text"))
                            .and_then(serde_json::Value::as_str)
                            .map(str::to_owned)
                    })
                    .unwrap_or_default();
                if let Some(title) = title_from_user_text(text.as_str()) {
                    return Some(title);
                }
            }
        }
    }
    session_id.and_then(|id| format_title(&short_session_id(&id)))
}

fn derive_claude_title(path: &Path) -> Option<String> {
    let mut custom_title = None;
    let mut ai_title = None;
    let mut summary = None;
    let mut first_user = None;
    for value in scan_json_lines(path) {
        let record_type = value.get("type").and_then(serde_json::Value::as_str);
        match record_type {
            Some("custom-title") => {
                if custom_title.is_none() {
                    custom_title = value
                        .get("customTitle")
                        .or_else(|| value.get("title"))
                        .and_then(serde_json::Value::as_str)
                        .and_then(format_title);
                }
            }
            Some("ai-title") => {
                if ai_title.is_none() {
                    ai_title = value
                        .get("aiTitle")
                        .or_else(|| value.get("title"))
                        .and_then(serde_json::Value::as_str)
                        .and_then(format_title);
                }
            }
            Some("summary") => {
                if summary.is_none() {
                    summary = value
                        .get("summary")
                        .or_else(|| value.get("title"))
                        .and_then(serde_json::Value::as_str)
                        .and_then(format_title);
                }
            }
            Some("user") if first_user.is_none() => {
                if value
                    .get("isMeta")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false)
                    || value
                        .get("isSidechain")
                        .and_then(serde_json::Value::as_bool)
                        .unwrap_or(false)
                {
                    continue;
                }
                let message = value.get("message");
                let text = blocks_to_text(message.and_then(|row| row.get("content")))
                    .or_else(|| blocks_to_text(value.get("content")))
                    .unwrap_or_default();
                if text.contains("tool_use_id") {
                    continue;
                }
                first_user = title_from_user_text(&text);
            }
            _ => {}
        }
    }
    custom_title.or(ai_title).or(summary).or(first_user)
}

fn derive_generic_user_title(path: &Path) -> Option<String> {
    for value in scan_json_lines(path) {
        let message = value.get("message");
        let role = message
            .and_then(|row| row.get("role"))
            .and_then(serde_json::Value::as_str)
            .or_else(|| value.get("role").and_then(serde_json::Value::as_str));
        if role != Some("user") {
            continue;
        }
        let text = blocks_to_text(message.and_then(|row| row.get("content")))
            .or_else(|| blocks_to_text(value.get("content")))
            .unwrap_or_default();
        if let Some(title) = title_from_user_text(&text) {
            return Some(title);
        }
    }
    None
}

fn derive_cursor_title(path: &Path) -> Option<String> {
    for value in scan_json_lines(path) {
        if value.get("role").and_then(serde_json::Value::as_str) != Some("user") {
            continue;
        }
        let Some(parts) = value
            .get("message")
            .and_then(|value| value.get("content"))
            .and_then(serde_json::Value::as_array)
        else {
            continue;
        };
        let text = parts
            .iter()
            .filter_map(|part| {
                (part.get("type").and_then(serde_json::Value::as_str) == Some("text"))
                    .then(|| part.get("text").and_then(serde_json::Value::as_str))
                    .flatten()
            })
            .collect::<Vec<_>>()
            .join("\n");
        return format_title(&text);
    }
    None
}

fn scan_json_lines(path: &Path) -> Vec<serde_json::Value> {
    let Ok(file) = fs::File::open(path) else {
        return Vec::new();
    };
    let mut reader = BufReader::new(file.take(TITLE_SCAN_MAX_BYTES as u64));
    let mut values = Vec::new();
    let mut line_buffer = String::new();
    let mut lines = 0_usize;
    loop {
        line_buffer.clear();
        match reader.read_line(&mut line_buffer) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        lines += 1;
        let trimmed = line_buffer.trim();
        if !trimmed.is_empty() {
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) {
                values.push(value);
            }
        }
        if lines >= TITLE_SCAN_MAX_LINES {
            break;
        }
    }
    values
}

fn title_from_user_text(text: &str) -> Option<String> {
    if is_listing_noise(text) {
        None
    } else {
        format_title(text)
    }
}

fn format_title(text: &str) -> Option<String> {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() {
        return None;
    }
    let truncated = collapsed
        .chars()
        .take(TITLE_MAX_SCALARS)
        .collect::<String>();
    Some(truncated)
}

fn short_session_id(id: &str) -> String {
    if let Some((head, _)) = id.split_once('-') {
        if head.len() >= 8 {
            return head.chars().take(8).collect();
        }
    }
    id.chars().take(8).collect()
}

fn is_listing_noise(text: &str) -> bool {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return true;
    }
    let lower = trimmed.to_ascii_lowercase();
    if LISTING_NOISE_MARKERS
        .iter()
        .any(|marker| lower.contains(marker))
    {
        return true;
    }
    // Dense XML-ish injection: several tags in a long wall of text.
    let xml_tags = count_xmlish_tags(trimmed);
    xml_tags >= 3 && trimmed.len() > 80
}

fn count_xmlish_tags(text: &str) -> usize {
    let bytes = text.as_bytes();
    let mut count = 0_usize;
    let mut index = 0_usize;
    while index < bytes.len() {
        if bytes[index] == b'<' {
            let start = index + 1;
            if start < bytes.len()
                && (bytes[start].is_ascii_alphabetic()
                    || bytes[start] == b'/'
                    || bytes[start] == b'_'
                    || bytes[start] == b'-')
            {
                if let Some(end) = bytes[start..].iter().position(|value| *value == b'>') {
                    let name = &bytes[start..start + end];
                    if name.iter().all(|value| {
                        value.is_ascii_alphanumeric() || matches!(*value, b'/' | b'_' | b'-')
                    }) {
                        count += 1;
                        index = start + end + 1;
                        continue;
                    }
                }
            }
        }
        index += 1;
    }
    count
}

fn blocks_to_text(value: Option<&serde_json::Value>) -> Option<String> {
    let value = value?;
    match value {
        serde_json::Value::String(text) => Some(text.clone()),
        serde_json::Value::Array(items) => {
            let mut parts = Vec::new();
            for item in items {
                match item {
                    serde_json::Value::String(text) => parts.push(text.clone()),
                    serde_json::Value::Object(object) => {
                        if let Some(text) = object.get("text").and_then(serde_json::Value::as_str) {
                            parts.push(text.to_owned());
                        } else if let Some(text) =
                            object.get("input_text").and_then(serde_json::Value::as_str)
                        {
                            parts.push(text.to_owned());
                        } else if object.get("type").and_then(serde_json::Value::as_str)
                            == Some("input_text")
                        {
                            if let Some(text) =
                                object.get("text").and_then(serde_json::Value::as_str)
                            {
                                parts.push(text.to_owned());
                            }
                        }
                    }
                    _ => {}
                }
            }
            if parts.is_empty() {
                None
            } else {
                Some(parts.join("\n"))
            }
        }
        serde_json::Value::Object(object) => {
            if let Some(text) = object.get("text").and_then(serde_json::Value::as_str) {
                Some(text.to_owned())
            } else {
                blocks_to_text(object.get("content"))
            }
        }
        _ => None,
    }
}

fn validate_limit(limit: usize) -> Result<(), TrajectoryError> {
    if !(1..=1_000).contains(&limit) {
        return Err(TrajectoryError::new(
            "invalid_input",
            "Listing limit must be between 1 and 1000.",
        ));
    }
    Ok(())
}

fn paginate(
    options: &ListingOptions<'_>,
    mut items: Vec<TrajectoryListing>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    items.sort_by(|left, right| {
        right
            .updated_at
            .cmp(&left.updated_at)
            .then_with(|| utf16_compare(&left.id, &right.id))
    });

    let start = if let Some(cursor) = options.cursor {
        let state = decode_cursor(cursor)?;
        items
            .iter()
            .position(|item| item.id == state.id)
            .map_or_else(|| (state.index + 1).min(items.len()), |index| index + 1)
    } else {
        0
    };
    let end = start.saturating_add(options.limit).min(items.len());
    let page = items[start..end].to_vec();
    let next_cursor = if end < items.len() {
        page.last().map(|item| encode_cursor(&item.id, end - 1))
    } else {
        None
    };
    Ok(TrajectoryListingPage {
        items: page,
        next_cursor,
    })
}

struct Cursor {
    index: usize,
    id: String,
}

fn encode_cursor(id: &str, index: usize) -> String {
    URL_SAFE_NO_PAD.encode(format!("1\n{index}\n{id}"))
}

fn decode_cursor(value: &str) -> Result<Cursor, TrajectoryError> {
    let decoded = URL_SAFE_NO_PAD
        .decode(value)
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok());
    if let Some(decoded) = decoded {
        let parts = decoded.split('\n').collect::<Vec<_>>();
        if parts.len() == 3 && parts[0] == "1" {
            if let Ok(index) = parts[1].parse::<usize>() {
                return Ok(Cursor {
                    index,
                    id: parts[2].to_owned(),
                });
            }
        }
    }
    Err(TrajectoryError::new(
        "invalid_input",
        "Cursor is not a valid trajectory-listing cursor.",
    ))
}

fn missing_or_denied(error: &std::io::Error) -> bool {
    matches!(
        error.kind(),
        std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
    )
}

fn io_error(message: &str, error: &std::io::Error) -> TrajectoryError {
    TrajectoryError::new("io_error", format!("{message} {error}"))
}

#[cfg(test)]
mod tests {
    use std::fs::{self, File, FileTimes};
    use std::time::{Duration, SystemTime};

    use super::{ListingOptions, list_claude_code_trajectories, list_codex_trajectories};

    #[test]
    fn claude_listing_is_stable_and_paginated_from_an_explicit_root() {
        let root = std::env::temp_dir().join(format!(
            "trajectory-rust-claude-listing-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let older = root.join("project-a").join("older.jsonl");
        let newer = root.join("project-b").join("newer.jsonl");
        fs::create_dir_all(older.parent().expect("older parent")).expect("create older project");
        fs::create_dir_all(newer.parent().expect("newer parent")).expect("create newer project");
        fs::write(&older, "{}\n").expect("write older transcript");
        fs::write(&newer, "{}\n").expect("write newer transcript");
        File::options()
            .write(true)
            .open(&older)
            .expect("open older transcript")
            .set_times(
                FileTimes::new().set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(1_000)),
            )
            .expect("set older timestamp");
        File::options()
            .write(true)
            .open(&newer)
            .expect("open newer transcript")
            .set_times(
                FileTimes::new().set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(2_000)),
            )
            .expect("set newer timestamp");

        let first = list_claude_code_trajectories(&ListingOptions {
            root: &root,
            cursor: None,
            limit: 1,
        })
        .expect("list first page");
        assert_eq!(first.items[0].id, "newer");
        let second = list_claude_code_trajectories(&ListingOptions {
            root: &root,
            cursor: first.next_cursor.as_deref(),
            limit: 1,
        })
        .expect("list second page");
        assert_eq!(second.items[0].id, "older");
        assert!(second.next_cursor.is_none());

        fs::remove_dir_all(&root).expect("remove test store");
    }

    #[test]
    fn codex_listing_recurses_to_the_contract_depth() {
        let root = std::env::temp_dir().join(format!(
            "trajectory-rust-codex-listing-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let included = root
            .join("2026")
            .join("07")
            .join("02")
            .join("rollout.jsonl");
        let ignored = root
            .join("2026")
            .join("07")
            .join("02")
            .join("nested")
            .join("too-deep")
            .join("ignored.jsonl");
        fs::create_dir_all(included.parent().expect("included parent"))
            .expect("create included directory");
        fs::create_dir_all(ignored.parent().expect("ignored parent"))
            .expect("create ignored directory");
        fs::write(&included, "{}\n").expect("write included transcript");
        fs::write(&ignored, "{}\n").expect("write ignored transcript");

        let page = list_codex_trajectories(&ListingOptions {
            root: &root,
            cursor: None,
            limit: 50,
        })
        .expect("list Codex store");
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.items[0].id, "rollout");

        fs::remove_dir_all(&root).expect("remove test store");
    }
}

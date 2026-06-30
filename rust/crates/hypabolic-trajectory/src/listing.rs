use std::fs;
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
    /// Native filesystem locator.
    pub path: PathBuf,
    /// UTC millisecond modification time.
    pub updated_at: String,
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
    list_project_store(options, &options.root.join("sessions"), "Pi")
}

/// Lists Claude Code JSONL transcripts below the explicit projects root.
pub fn list_claude_code_trajectories(
    options: &ListingOptions<'_>,
) -> Result<TrajectoryListingPage, TrajectoryError> {
    list_project_store(options, options.root, "Claude Code")
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

/// Lists OpenClaw JSONL transcripts below `<root>/agents/*/sessions`.
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
            return Err(io_error(
                "Could not enumerate the OpenClaw store.",
                &error,
            ));
        }
    };
    for agent in agents {
        let agent = agent.map_err(|error| {
            io_error("Could not enumerate the OpenClaw store.", &error)
        })?;
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
            items.push(listing_from_file(path, &metadata, "OpenClaw")?);
        }
    }
    paginate(options, items)
}

fn list_project_store(
    options: &ListingOptions<'_>,
    projects_root: &Path,
    source_name: &str,
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
            items.push(listing_from_file(path, &metadata, source_name)?);
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
        items.push(listing_from_file(path, &metadata, "Codex")?);
    }
    Ok(())
}

fn listing_from_file(
    path: PathBuf,
    metadata: &fs::Metadata,
    source_name: &str,
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
    Ok(TrajectoryListing {
        id,
        path,
        updated_at: format_ms(milliseconds)?,
        size_bytes: metadata.len(),
    })
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

use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;

use crate::TrajectoryError;
use crate::canonical::utf16_compare;
use crate::projection::format_ms;

/// Options for explicit-root Pi store discovery.
#[derive(Debug, Clone)]
pub struct ListingOptions<'a> {
    /// Pi store root. The implementation never consults the process home directory.
    pub root: &'a Path,
    /// Opaque cursor from a previous page.
    pub cursor: Option<&'a str>,
    /// Page size from 1 through 1,000.
    pub limit: usize,
}

/// One listed Pi transcript.
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
    if !(1..=1_000).contains(&options.limit) {
        return Err(TrajectoryError::new(
            "invalid_input",
            "Listing limit must be between 1 and 1000.",
        ));
    }

    let sessions = options.root.join("sessions");
    let projects = match fs::read_dir(&sessions) {
        Ok(value) => value,
        Err(error) if missing_or_denied(&error) => {
            return Ok(TrajectoryListingPage {
                items: Vec::new(),
                next_cursor: None,
            });
        }
        Err(error) => return Err(io_error("Could not enumerate the Pi store.", &error)),
    };

    let mut items = Vec::new();
    for project in projects {
        let project =
            project.map_err(|error| io_error("Could not enumerate the Pi store.", &error))?;
        let file_type = match project.file_type() {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => return Err(io_error("Could not inspect a Pi store entry.", &error)),
        };
        if !file_type.is_dir() {
            continue;
        }
        let files = match fs::read_dir(project.path()) {
            Ok(value) => value,
            Err(error) if missing_or_denied(&error) => continue,
            Err(error) => return Err(io_error("Could not enumerate a Pi project.", &error)),
        };
        for file in files {
            let file =
                file.map_err(|error| io_error("Could not enumerate a Pi project.", &error))?;
            let path = file.path();
            if path.extension().and_then(|value| value.to_str()) != Some("jsonl") {
                continue;
            }
            let metadata = match file.metadata() {
                Ok(value) if value.is_file() => value,
                Ok(_) => continue,
                Err(error) if missing_or_denied(&error) => continue,
                Err(error) => return Err(io_error("Could not inspect a Pi transcript.", &error)),
            };
            let id = path
                .file_stem()
                .and_then(|value| value.to_str())
                .ok_or_else(|| {
                    TrajectoryError::new(
                        "invalid_input",
                        "A Pi transcript filename is not valid Unicode.",
                    )
                })?
                .to_owned();
            let modified = metadata
                .modified()
                .map_err(|error| io_error("Could not read a Pi transcript timestamp.", &error))?
                .duration_since(UNIX_EPOCH)
                .map_err(|_| {
                    TrajectoryError::new(
                        "invalid_input",
                        "A Pi transcript timestamp precedes the Unix epoch.",
                    )
                })?;
            let milliseconds = i64::try_from(modified.as_millis()).map_err(|_| {
                TrajectoryError::new(
                    "invalid_input",
                    "A Pi transcript timestamp is out of range.",
                )
            })?;
            items.push(TrajectoryListing {
                id,
                path,
                updated_at: format_ms(milliseconds)?,
                size_bytes: metadata.len(),
            });
        }
    }

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

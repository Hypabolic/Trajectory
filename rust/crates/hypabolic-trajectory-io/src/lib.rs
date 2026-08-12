//! Optional file I/O for live session streaming (LS-09).
//!
//! Poll helpers that only call core apply APIs. Explicit root required.
//! Host errors are distinct from stream diagnostics.

#![forbid(unsafe_code)]

use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

use hypabolic_trajectory::{
    StreamConsumed, StreamOptions, StreamProvisionalInfo, StreamRevision, StreamState,
    StreamUpdate, TrajectoryError, TrajectorySource, TrajectoryStream, split_complete_lines,
};

/// Host error: missing root.
pub const HOST_ROOT_REQUIRED: &str = "root_required";
/// Host error: missing path.
pub const HOST_PATH_REQUIRED: &str = "path_required";
/// Host error: path not under root.
pub const HOST_PATH_OUTSIDE_ROOT: &str = "path_outside_root";
/// Host error: permission denied.
pub const HOST_IO_PERMISSION: &str = "io_permission";
/// Host error: path not found.
pub const HOST_IO_NOT_FOUND: &str = "io_not_found";
/// Host error: other I/O failure.
pub const HOST_IO_ERROR: &str = "io_error";

const MSG_ROOT_REQUIRED: &str = "File stream root is required.";
const MSG_PATH_REQUIRED: &str = "File stream path is required.";
const MSG_PATH_OUTSIDE_ROOT: &str = "File stream path is outside the explicit root.";
const MSG_IO_PERMISSION: &str = "File stream could not read the path (permission denied).";
const MSG_IO_NOT_FOUND: &str = "File stream path was not found.";
const MSG_IO_ERROR: &str = "File stream I/O failed.";

/// Host-side file stream configuration or I/O failure.
#[derive(Debug, Clone)]
pub struct HostError {
    /// Stable host error code.
    pub code: &'static str,
    /// Content-safe fixed message (no raw lines).
    pub message: &'static str,
    /// For the calling process only; never copy into stream wire objects.
    pub path: Option<PathBuf>,
}

impl std::fmt::Display for HostError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.message)
    }
}

impl std::error::Error for HostError {}

/// Explicit-root options for following a single JSONL transcript path.
#[derive(Debug, Clone)]
pub struct FileStreamOptions {
    /// Required directory that bounds `path`.
    pub root: PathBuf,
    /// Required transcript file path (must resolve under `root`).
    pub path: PathBuf,
    /// Source family.
    pub source: TrajectorySource,
    /// Optional group id.
    pub group_id: Option<String>,
    /// Optional full stream options.
    pub stream: Option<StreamOptions>,
    /// Poll interval for follow loops.
    pub poll_interval: Duration,
    /// Full-prefix reconcile every N polls (0 = disabled).
    pub reconcile_every: u32,
    /// Source revision string passed to core apply.
    pub source_revision: String,
}

impl Default for FileStreamOptions {
    fn default() -> Self {
        Self {
            root: PathBuf::new(),
            path: PathBuf::new(),
            source: TrajectorySource::Pi,
            group_id: None,
            stream: None,
            poll_interval: Duration::from_millis(50),
            reconcile_every: 0,
            source_revision: "file-0".to_string(),
        }
    }
}

/// Poll a single JSONL path and apply complete-line segments to core streaming.
#[derive(Debug)]
pub struct FileTrajectoryStream {
    root: PathBuf,
    path: PathBuf,
    stream: TrajectoryStream,
    poll_interval: Duration,
    reconcile_every: u32,
    source_revision: String,
    file_offset: u64,
    host_pending: Vec<u8>,
    first: bool,
    polls: u32,
    closed: bool,
}

impl FileTrajectoryStream {
    /// Open a file stream after validating explicit root containment.
    ///
    /// # Errors
    /// Returns [`HostError`] when root/path are missing or path is outside root.
    pub fn open(options: FileStreamOptions) -> Result<Self, HostError> {
        if options.root.as_os_str().is_empty() {
            return Err(HostError {
                code: HOST_ROOT_REQUIRED,
                message: MSG_ROOT_REQUIRED,
                path: None,
            });
        }
        if options.path.as_os_str().is_empty() {
            return Err(HostError {
                code: HOST_PATH_REQUIRED,
                message: MSG_PATH_REQUIRED,
                path: None,
            });
        }

        let root = canonicalize_or_abs(&options.root);
        let path = canonicalize_or_abs(&options.path);
        if !is_under_root(&root, &path) {
            return Err(HostError {
                code: HOST_PATH_OUTSIDE_ROOT,
                message: MSG_PATH_OUTSIDE_ROOT,
                path: Some(path),
            });
        }

        let stream_opts = match options.stream {
            Some(mut opts) => {
                if options.group_id.is_some() && opts.group_id.is_none() {
                    opts.group_id = options.group_id.clone();
                }
                opts
            }
            None => {
                let mut opts = StreamOptions::new(options.source);
                if let Some(g) = options.group_id {
                    opts = opts.with_group_id(g);
                }
                opts
            }
        };

        Ok(Self {
            root,
            path,
            stream: TrajectoryStream::create(stream_opts),
            poll_interval: options.poll_interval,
            reconcile_every: options.reconcile_every,
            source_revision: options.source_revision,
            file_offset: 0,
            host_pending: Vec::new(),
            first: true,
            polls: 0,
            closed: false,
        })
    }

    /// Root directory.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Followed file path.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Underlying mutable stream façade.
    #[must_use]
    pub fn stream(&self) -> &TrajectoryStream {
        &self.stream
    }

    /// Read growth once. Returns `Ok(None)` when unchanged at the host edge.
    ///
    /// # Errors
    /// Returns [`HostError`] for filesystem failures.
    pub fn poll(&mut self) -> Result<Option<StreamUpdate>, HostError> {
        if self.closed {
            return Ok(None);
        }
        let size = self.stat_size()?;
        if size < self.file_offset {
            return Ok(Some(self.snapshot_full(size)?));
        }
        if self.first {
            return Ok(Some(self.snapshot_full(size)?));
        }
        if size > self.file_offset {
            return self.append_growth(size);
        }
        self.polls = self.polls.saturating_add(1);
        if self.reconcile_every > 0 && self.polls % self.reconcile_every == 0 {
            return self.reconcile_snapshot(size);
        }
        Ok(None)
    }

    /// Yield non-empty updates until `should_stop` returns true.
    ///
    /// Caller owns process lifetime (not a daemon).
    pub fn follow_while<F>(&mut self, should_stop: F) -> FollowIter<'_, F>
    where
        F: FnMut() -> bool,
    {
        FollowIter {
            stream: self,
            should_stop,
        }
    }

    /// Finish the underlying core stream.
    ///
    /// Forwards any host-held incomplete line into core pending first so
    /// finish can commit a final unterminated line (core finish only sees
    /// core `pending_bytes`).
    ///
    /// # Errors
    /// Propagates core [`TrajectoryError`] from finish or the pending flush.
    pub fn finish(&mut self) -> Result<StreamUpdate, TrajectoryError> {
        if !self.host_pending.is_empty() {
            // Retain host pending until core apply succeeds (H4). Incomplete
            // host bytes become core pending (no complete lines).
            let update = self.stream.apply_append(
                &self.host_pending,
                None,
                Some(self.source_revision.as_str()),
            )?;
            if update.kind != "updated" && update.kind != "unchanged" {
                return Ok(update);
            }
            self.host_pending.clear();
        }
        self.stream.finish()
    }

    /// Close host follow loop (does not delete the file).
    pub fn close(&mut self) {
        self.closed = true;
    }

    fn snapshot_full(&mut self, size: u64) -> Result<StreamUpdate, HostError> {
        let material = self.read_range(0, size)?;
        self.file_offset = size;
        let (complete, pending) = split_complete_lines(&material);
        self.host_pending = pending;
        self.first = false;
        self.polls = self.polls.saturating_add(1);
        let result = self
            .stream
            .apply_snapshot(&complete, &self.source_revision, None);
        Ok(match result {
            Ok(update) => update,
            Err(err) => stream_error_from_core(self.stream.state(), err),
        })
    }

    fn reconcile_snapshot(&mut self, size: u64) -> Result<Option<StreamUpdate>, HostError> {
        let material = self.read_range(0, size)?;
        let (complete, pending) = split_complete_lines(&material);
        self.host_pending = pending;
        self.file_offset = size;
        let result = self
            .stream
            .apply_snapshot(&complete, &self.source_revision, None);
        let update = match result {
            Ok(update) => update,
            Err(err) => stream_error_from_core(self.stream.state(), err),
        };
        if update.kind == "unchanged" {
            Ok(None)
        } else {
            Ok(Some(update))
        }
    }

    fn append_growth(&mut self, size: u64) -> Result<Option<StreamUpdate>, HostError> {
        let chunk = self.read_range(self.file_offset, size)?;
        self.file_offset = size;
        let mut buf = std::mem::take(&mut self.host_pending);
        buf.extend_from_slice(&chunk);
        let (complete, pending) = split_complete_lines(&buf);
        self.host_pending = pending;
        self.polls = self.polls.saturating_add(1);
        if complete.is_empty() {
            return Ok(None);
        }
        let result = self
            .stream
            .apply_append(&complete, None, Some(self.source_revision.as_str()));
        let update = match result {
            Ok(update) => update,
            Err(err) => stream_error_from_core(self.stream.state(), err),
        };
        if update.kind == "unchanged" {
            Ok(None)
        } else {
            Ok(Some(update))
        }
    }

    fn stat_size(&self) -> Result<u64, HostError> {
        match fs::metadata(&self.path) {
            Ok(meta) => Ok(meta.len()),
            Err(err) => Err(map_io_error(err, &self.path)),
        }
    }

    fn read_range(&self, start: u64, end: u64) -> Result<Vec<u8>, HostError> {
        if end <= start {
            return Ok(Vec::new());
        }
        let mut file = File::open(&self.path).map_err(|e| map_io_error(e, &self.path))?;
        file.seek(SeekFrom::Start(start))
            .map_err(|e| map_io_error(e, &self.path))?;
        let mut buf = vec![0_u8; (end - start) as usize];
        let mut read = 0;
        while read < buf.len() {
            match file.read(&mut buf[read..]) {
                Ok(0) => break,
                Ok(n) => read += n,
                Err(e) => return Err(map_io_error(e, &self.path)),
            }
        }
        buf.truncate(read);
        Ok(buf)
    }
}

/// Iterator produced by [`FileTrajectoryStream::follow_while`].
pub struct FollowIter<'a, F> {
    stream: &'a mut FileTrajectoryStream,
    should_stop: F,
}

impl<F> Iterator for FollowIter<'_, F>
where
    F: FnMut() -> bool,
{
    type Item = Result<StreamUpdate, HostError>;

    fn next(&mut self) -> Option<Self::Item> {
        if (self.should_stop)() || self.stream.closed {
            return None;
        }
        match self.stream.poll() {
            Ok(Some(update)) if update.kind != "unchanged" => Some(Ok(update)),
            Ok(_) => {
                if !self.stream.poll_interval.is_zero() {
                    thread::sleep(self.stream.poll_interval);
                }
                // Recurse one step via loop style
                loop {
                    if (self.should_stop)() || self.stream.closed {
                        return None;
                    }
                    match self.stream.poll() {
                        Ok(Some(update)) if update.kind != "unchanged" => return Some(Ok(update)),
                        Ok(_) => {
                            if !self.stream.poll_interval.is_zero() {
                                thread::sleep(self.stream.poll_interval);
                            }
                        }
                        Err(e) => return Some(Err(e)),
                    }
                }
            }
            Err(e) => Some(Err(e)),
        }
    }
}

/// Core apply paths normally return `Ok(StreamUpdate)` with `kind=error` /
/// `reset-required`. Rare typed [`TrajectoryError`] values are surfaced as
/// stream error updates (preserving the core code) rather than collapsed into
/// a generic host I/O error.
fn stream_error_from_core(state: &StreamState, err: TrajectoryError) -> StreamUpdate {
    let revision = state.snapshot.as_ref().map_or_else(
        || StreamRevision {
            revision: 0,
            revision_id: "error".into(),
            parent_revision_id: None,
            complete: false,
            generation: state.generation,
        },
        |s| s.revision.clone(),
    );
    StreamUpdate {
        kind: "error".into(),
        revision,
        cursor: state.cursor.clone(),
        snapshot: None,
        delta: None,
        diagnostics: vec![],
        provisional: StreamProvisionalInfo {
            include: state.options.include_provisional,
            provisional_ids: vec![],
            finalized_ids: vec![],
        },
        consumed: StreamConsumed::default(),
        reset: None,
        error: Some((err.code, err.message)),
    }
}

fn map_io_error(err: std::io::Error, path: &Path) -> HostError {
    let code = match err.kind() {
        std::io::ErrorKind::NotFound => HOST_IO_NOT_FOUND,
        std::io::ErrorKind::PermissionDenied => HOST_IO_PERMISSION,
        _ => HOST_IO_ERROR,
    };
    let message = match code {
        HOST_IO_NOT_FOUND => MSG_IO_NOT_FOUND,
        HOST_IO_PERMISSION => MSG_IO_PERMISSION,
        _ => MSG_IO_ERROR,
    };
    HostError {
        code,
        message,
        path: Some(path.to_path_buf()),
    }
}

fn canonicalize_or_abs(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| {
        let abs = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()
                .map(|cwd| cwd.join(path))
                .unwrap_or_else(|_| path.to_path_buf())
        };
        // Canonicalize fails for non-existent paths; still collapse ".." / "." so
        // Path::starts_with cannot treat {root}/../outside as under root (LS-09).
        normalize_lexically(&abs)
    })
}

/// Collapse `.` / `..` without touching the filesystem (parity with GetFullPath / path.resolve).
fn normalize_lexically(path: &Path) -> PathBuf {
    use std::path::Component;
    let mut out: Vec<Component<'_>> = Vec::new();
    for component in path.components() {
        match component {
            Component::Prefix(_) | Component::RootDir => {
                out.push(component);
            }
            Component::CurDir => {}
            Component::ParentDir => match out.last() {
                Some(Component::Normal(_)) => {
                    out.pop();
                }
                // Already at root/prefix: drop `..` (absolute paths cannot escape).
                Some(Component::RootDir) | Some(Component::Prefix(_)) => {}
                // Relative path that still needs a leading `..`.
                Some(Component::ParentDir) | None => out.push(Component::ParentDir),
                Some(Component::CurDir) => unreachable!("CurDir is never retained"),
            },
            Component::Normal(_) => out.push(component),
        }
    }
    out.iter().collect()
}

fn is_under_root(root: &Path, path: &Path) -> bool {
    // Both inputs must already be absolute + lexically normalized (or canonicalized).
    path.starts_with(root)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_root() -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let root = std::env::temp_dir().join(format!("traj-io-rs-{nanos}"));
        fs::create_dir_all(&root).expect("mkdir");
        root
    }

    const SESSION_LINE: &[u8] = br#"{"type":"session","version":3,"id":"stream-file-io-rs","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}
"#;
    const USER_LINE: &[u8] = br#"{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hello"}]},"sessionId":"stream-file-io-rs"}
"#;

    #[test]
    fn growth_and_incomplete_line() {
        let root = temp_root();
        let path = root.join("session.jsonl");
        fs::write(&path, b"").unwrap();

        let mut stream = FileTrajectoryStream::open(FileStreamOptions {
            root: root.clone(),
            path: path.clone(),
            source: TrajectorySource::Pi,
            group_id: Some("stream-file-io-rs".into()),
            ..Default::default()
        })
        .unwrap();

        let u0 = stream.poll().unwrap().expect("first");
        assert_eq!(u0.kind, "updated");
        assert!(u0.snapshot.as_ref().unwrap().records.is_empty());

        let mut partial = SESSION_LINE.to_vec();
        partial.extend_from_slice(&USER_LINE[..40]);
        fs::write(&path, &partial).unwrap();
        let u1 = stream.poll().unwrap().expect("session line");
        assert_eq!(u1.kind, "updated");
        // Session meta committed; incomplete user line held at host — not materialized.
        let records_after_partial = u1.snapshot.as_ref().unwrap().records.len();
        assert!(records_after_partial >= 1);
        assert!(!u1
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .any(|r| r.record.get("role").and_then(|v| v.as_str()) == Some("user")));

        let mut full = SESSION_LINE.to_vec();
        full.extend_from_slice(USER_LINE);
        fs::write(&path, &full).unwrap();
        let u2 = stream.poll().unwrap().expect("user line");
        assert_eq!(u2.kind, "updated");
        assert!(u2.snapshot.as_ref().unwrap().records.len() > records_after_partial);
        assert!(u2
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .any(|r| r.record.get("role").and_then(|v| v.as_str()) == Some("user")));
        for d in &u2.diagnostics {
            assert!(!d.message.contains(path.to_string_lossy().as_ref()));
        }
    }

    #[test]
    fn finish_flushes_host_pending() {
        let root = temp_root();
        let path = root.join("session.jsonl");
        let mut material = SESSION_LINE.to_vec();
        // Incomplete user line (no trailing LF).
        material.extend_from_slice(&USER_LINE[..USER_LINE.len() - 1]);
        fs::write(&path, &material).unwrap();

        let mut stream = FileTrajectoryStream::open(FileStreamOptions {
            root: root.clone(),
            path: path.clone(),
            source: TrajectorySource::Pi,
            group_id: Some("stream-file-io-rs".into()),
            ..Default::default()
        })
        .unwrap();

        let u0 = stream.poll().unwrap().expect("first");
        assert_eq!(u0.kind, "updated");
        assert!(!u0
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .any(|r| r.record.get("role").and_then(|v| v.as_str()) == Some("user")));
        let records_before = u0.snapshot.as_ref().unwrap().records.len();

        let finished = stream.finish().expect("finish");
        assert!(finished.kind == "updated" || finished.kind == "unchanged");
        assert!(stream.stream().state().finished);
        assert!(finished.snapshot.as_ref().unwrap().records.len() > records_before);
        assert!(finished
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .any(|r| r.record.get("role").and_then(|v| v.as_str()) == Some("user")));
    }

    #[test]
    fn finish_failed_pending_flush_retains_host_buffer() {
        let root = temp_root();
        let path = root.join("session.jsonl");
        fs::write(&path, b"").unwrap();

        let mut opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("stream-file-io-rs");
        opts.max_pending_bytes = Some(16);
        opts.max_line_bytes = Some(16);

        let mut stream = FileTrajectoryStream::open(FileStreamOptions {
            root: root.clone(),
            path: path.clone(),
            source: TrajectorySource::Pi,
            group_id: Some("stream-file-io-rs".into()),
            stream: Some(opts),
            ..Default::default()
        })
        .unwrap();

        let u0 = stream.poll().unwrap().expect("empty");
        assert_eq!(u0.kind, "updated");
        let gen_before = stream.stream().state().cursor.generation;
        assert!(!stream.stream().state().finished);

        let incomplete = format!(
            r#"{{"type":"message","id":"pending-too-long","x":"{}"}}"#,
            "y".repeat(80)
        );
        fs::write(&path, incomplete.as_bytes()).unwrap();
        assert!(stream.poll().unwrap().is_none());

        let finished = stream.finish().expect("finish returns update");
        assert_eq!(finished.kind, "error");
        assert_eq!(
            finished.error.as_ref().map(|(c, _)| c.as_str()),
            Some("stream_buffer_limit")
        );
        assert!(!stream.stream().state().finished);
        assert_eq!(stream.stream().state().cursor.generation, gen_before);

        let again = stream.finish().expect("second finish");
        assert_eq!(again.kind, "error");
        assert_eq!(
            again.error.as_ref().map(|(c, _)| c.as_str()),
            Some("stream_buffer_limit")
        );
        assert!(!stream.stream().state().finished);
    }

    #[test]
    fn coalesced_growth() {
        let root = temp_root();
        let path = root.join("session.jsonl");
        fs::write(&path, SESSION_LINE).unwrap();

        let mut stream = FileTrajectoryStream::open(FileStreamOptions {
            root: root.clone(),
            path: path.clone(),
            source: TrajectorySource::Pi,
            group_id: Some("stream-file-io-rs".into()),
            ..Default::default()
        })
        .unwrap();
        assert!(stream.poll().unwrap().is_some());

        let mut full = SESSION_LINE.to_vec();
        full.extend_from_slice(USER_LINE);
        fs::write(&path, &full).unwrap();
        let update = stream.poll().unwrap().expect("growth");
        assert_eq!(update.kind, "updated");
        assert!(!update.snapshot.as_ref().unwrap().records.is_empty());
    }

    #[test]
    fn truncation_surfaces_core_reset() {
        let root = temp_root();
        let path = root.join("session.jsonl");
        let mut full = SESSION_LINE.to_vec();
        full.extend_from_slice(USER_LINE);
        fs::write(&path, &full).unwrap();

        let mut stream = FileTrajectoryStream::open(FileStreamOptions {
            root: root.clone(),
            path: path.clone(),
            source: TrajectorySource::Pi,
            group_id: Some("stream-file-io-rs".into()),
            ..Default::default()
        })
        .unwrap();
        assert_eq!(stream.poll().unwrap().unwrap().kind, "updated");

        fs::write(&path, SESSION_LINE).unwrap();
        let update = stream.poll().unwrap().expect("truncate");
        assert_eq!(update.kind, "reset-required");
        assert!(update.reset.is_some());
    }

    #[test]
    fn path_outside_root_is_host_error() {
        let root = temp_root();
        let other = std::env::temp_dir().join(format!(
            "traj-io-out-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        fs::create_dir_all(&other).unwrap();
        let outside = other.join("x.jsonl");
        fs::write(&outside, b"\n").unwrap();

        let err = FileTrajectoryStream::open(FileStreamOptions {
            root,
            path: outside,
            source: TrajectorySource::Pi,
            ..Default::default()
        })
        .unwrap_err();
        assert_eq!(err.code, HOST_PATH_OUTSIDE_ROOT);
        assert_eq!(err.message, MSG_PATH_OUTSIDE_ROOT);
    }

    #[test]
    fn non_existent_path_with_dotdot_outside_root_is_host_error() {
        // Non-existent intermediate segment so canonicalize fails; fallback must still
        // reject {root}/missing/../../outside via lexical normalize (LS-09).
        let root = temp_root();
        let outside = root
            .join("does-not-exist")
            .join("..")
            .join("..")
            .join(format!(
                "traj-io-escape-{}.jsonl",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_nanos())
                    .unwrap_or(0)
            ));
        let err = FileTrajectoryStream::open(FileStreamOptions {
            root,
            path: outside,
            source: TrajectorySource::Pi,
            ..Default::default()
        })
        .unwrap_err();
        assert_eq!(err.code, HOST_PATH_OUTSIDE_ROOT);
        assert_eq!(err.message, MSG_PATH_OUTSIDE_ROOT);
    }

    #[test]
    fn normalize_lexically_collapses_parent_dirs() {
        let root = PathBuf::from("/tmp/explicit-root");
        let escaped = root.join("sub").join("..").join("..").join("outside.jsonl");
        let normalized = normalize_lexically(&escaped);
        assert!(!is_under_root(&root, &normalized));
        assert_eq!(normalized, PathBuf::from("/tmp/outside.jsonl"));

        let inside = root.join("a").join("..").join("b.jsonl");
        let normalized_in = normalize_lexically(&inside);
        assert!(is_under_root(&root, &normalized_in));
        assert_eq!(normalized_in, root.join("b.jsonl"));
    }

    #[test]
    fn root_required() {
        let err = FileTrajectoryStream::open(FileStreamOptions {
            root: PathBuf::new(),
            path: PathBuf::from("/tmp/x.jsonl"),
            source: TrajectorySource::Pi,
            ..Default::default()
        })
        .unwrap_err();
        assert_eq!(err.code, HOST_ROOT_REQUIRED);
    }

    #[test]
    #[cfg(unix)]
    fn permission_denied_is_host_error() {
        use std::os::unix::fs::PermissionsExt;
        let root = temp_root();
        let path = root.join("session.jsonl");
        fs::write(&path, SESSION_LINE).unwrap();
        let mut perms = fs::metadata(&path).unwrap().permissions();
        perms.set_mode(0o000);
        fs::set_permissions(&path, perms).unwrap();

        let mut stream = FileTrajectoryStream::open(FileStreamOptions {
            root: root.clone(),
            path: path.clone(),
            source: TrajectorySource::Pi,
            group_id: Some("x".into()),
            ..Default::default()
        })
        .unwrap();
        let err = stream.poll().unwrap_err();
        assert!(err.code == HOST_IO_PERMISSION || err.code == HOST_IO_ERROR);
        assert!(!err.message.contains(path.to_string_lossy().as_ref()));

        let mut perms = fs::metadata(&path).unwrap().permissions();
        perms.set_mode(0o600);
        fs::set_permissions(&path, perms).unwrap();
    }

    #[test]
    fn split_complete_lines_holds_incomplete() {
        let (complete, pending) = split_complete_lines(b"abc\ndef");
        assert_eq!(complete, b"abc\n");
        assert_eq!(pending, b"def");
    }

    #[test]
    fn unused_write_import_silenced() {
        // Ensure File is usable for shared-read open path in integration style.
        let root = temp_root();
        let path = root.join("t.jsonl");
        let mut f = File::create(&path).unwrap();
        f.write_all(b"\n").unwrap();
    }
}

//! LS-11 sample CLI stream / ahp-stream coverage (Rust).
//!
//! Spawns the unpublished `trajectory` binary against temp stores and
//! `FakeAhpHost` fixtures only (mirrors `python/tests/test_ls11_sample_cli_stream.py`).

#![allow(missing_docs)]

use std::io::Read as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use std::{env, fs, thread};

const CHAT: &str = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";

const SESSION_LINE: &str = concat!(
    r#"{"type":"session","version":3,"id":"ls11-stream-rs","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}"#,
    "\n"
);
const USER_LINE: &str = concat!(
    r#"{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hello"}]},"sessionId":"ls11-stream-rs"}"#,
    "\n"
);

fn trajectory_bin() -> PathBuf {
    if let Some(path) = env::var_os("CARGO_BIN_EXE_trajectory") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return path;
        }
    }

    // Binary-only packages do not always export CARGO_BIN_EXE_* for integration
    // tests (notably cargo 1.85 on Windows). Fall back to the workspace target
    // dir (debug first, then release), including the platform EXE suffix.
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    // rust/tools/trajectory-cli → rust/
    let rust_root = manifest_dir
        .parent()
        .and_then(Path::parent)
        .expect("rust root from CARGO_MANIFEST_DIR");
    let exe_name = format!("trajectory{}", env::consts::EXE_SUFFIX);
    for profile in ["debug", "release"] {
        let candidate = rust_root.join("target").join(profile).join(&exe_name);
        if candidate.is_file() {
            return candidate;
        }
    }

    panic!(
        "trajectory binary not found (CARGO_BIN_EXE_trajectory unset and no target/{{debug,release}}/{exe_name}). \
         Build with: cargo build -p trajectory-cli --bin trajectory"
    );
}

fn repository_root() -> PathBuf {
    // rust/tools/trajectory-cli → three parents to repo root
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let root = manifest_dir
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .expect("repo root from CARGO_MANIFEST_DIR")
        .to_path_buf();
    assert!(
        root.join("contracts/compatibility.json").is_file(),
        "expected repo root at {}",
        root.display()
    );
    root
}

struct CliOutput {
    code: i32,
    stdout: String,
    stderr: String,
}

fn run_cli(args: &[&str]) -> CliOutput {
    let bin = trajectory_bin();
    let mut child = Command::new(&bin)
        .args(args)
        .current_dir(repository_root())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap_or_else(|e| panic!("spawn {}: {e}", bin.display()));

    let deadline = Instant::now() + Duration::from_secs(30);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => {
                thread::sleep(Duration::from_millis(20));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                panic!("CLI timed out: trajectory {}", args.join(" "));
            }
            Err(e) => panic!("try_wait failed: {e}"),
        }
    };

    let mut stdout = String::new();
    let mut stderr = String::new();
    if let Some(mut out) = child.stdout.take() {
        let _ = out.read_to_string(&mut stdout);
    }
    if let Some(mut err) = child.stderr.take() {
        let _ = err.read_to_string(&mut stderr);
    }

    CliOutput {
        code: status.code().unwrap_or(1),
        stdout,
        stderr,
    }
}

#[test]
fn help_mentions_stream_and_not_a_daemon() {
    let out = run_cli(&["--help"]);
    assert_eq!(out.code, 0, "stderr={}", out.stderr);
    let text = format!("{}\n{}", out.stdout, out.stderr).to_ascii_lowercase();
    assert!(
        text.contains("stream"),
        "help missing stream:\n{}",
        out.stdout
    );
    assert!(
        text.contains("ahp-stream"),
        "help missing ahp-stream:\n{}",
        out.stdout
    );
    assert!(
        text.contains("not a daemon") || text.contains("not a trajectory daemon"),
        "help missing daemon note:\n{}",
        out.stdout
    );
    assert!(
        text.contains("watch"),
        "help missing watch:\n{}",
        out.stdout
    );
}

#[test]
fn stream_temp_file_emits_snapshot_delta_privacy_default() {
    let root = env::temp_dir().join(format!("traj-ls11-rs-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).expect("create temp root");
    let path = root.join("session.jsonl");
    fs::write(&path, format!("{SESSION_LINE}{USER_LINE}")).expect("write session");

    let out = run_cli(&[
        "stream",
        "--source",
        "pi",
        "--root",
        root.to_str().expect("utf8 root"),
        "--path",
        path.to_str().expect("utf8 path"),
        "--emit",
        "snapshot+delta",
        "--max-updates",
        "1",
    ]);
    let _ = fs::remove_dir_all(&root);

    assert_eq!(out.code, 0, "stderr={}\nstdout={}", out.stderr, out.stdout);
    let lower = out.stdout.to_ascii_lowercase();
    assert!(
        lower.contains("stream update"),
        "missing stream update:\n{}",
        out.stdout
    );
    assert!(
        lower.contains("live tail"),
        "missing live tail:\n{}",
        out.stdout
    );
    assert!(
        lower.contains("snapshot"),
        "missing snapshot:\n{}",
        out.stdout
    );
    assert!(lower.contains("delta"), "missing delta:\n{}", out.stdout);
    assert!(
        out.stdout.contains("Content omitted")
            || lower.contains("content omitted")
            || lower.contains("privacy"),
        "missing privacy omission:\n{}",
        out.stdout
    );
    assert!(
        lower.contains("not a daemon"),
        "missing not a daemon:\n{}",
        out.stdout
    );
    assert!(
        !out.stdout.contains("hello"),
        "privacy leak of user prose:\n{}",
        out.stdout
    );
}

#[test]
fn stream_rejects_ahp_source() {
    let out = run_cli(&["stream", "--source", "ahp", "--path", "/tmp/x.jsonl"]);
    assert_eq!(out.code, 2, "stdout={}\nstderr={}", out.stdout, out.stderr);
    let combined = format!("{}\n{}", out.stdout, out.stderr).to_ascii_lowercase();
    assert!(
        combined.contains("invalid_input"),
        "expected invalid_input:\n{combined}"
    );
}

#[test]
fn ahp_stream_fake_host_actions() {
    let actions = repository_root()
        .join("conformance/cases/streaming/ahp-action-turn-flow/step-actions.jsonl");
    assert!(actions.is_file(), "fixture missing: {}", actions.display());

    let out = run_cli(&[
        "ahp-stream",
        "--url",
        "fake://demo",
        "--chat",
        CHAT,
        "--actions-path",
        actions.to_str().expect("utf8 actions"),
        "--emit",
        "snapshot+delta",
        "--max-updates",
        "1",
    ]);

    assert_eq!(out.code, 0, "stderr={}\nstdout={}", out.stderr, out.stdout);
    let lower = out.stdout.to_ascii_lowercase();
    assert!(
        lower.contains("stream update") || lower.contains("ready"),
        "expected stream update or ready:\n{}",
        out.stdout
    );
    assert!(
        lower.contains("snapshot") || lower.contains("delta"),
        "expected snapshot or delta:\n{}",
        out.stdout
    );
    assert!(
        out.stdout.contains("Content omitted")
            || lower.contains("content omitted")
            || lower.contains("privacy"),
        "missing privacy omission:\n{}",
        out.stdout
    );
    assert!(
        !out.stdout.contains("test-token"),
        "auth token leaked:\n{}",
        out.stdout
    );
    assert!(
        !out.stdout.contains("List the files"),
        "action prose leaked:\n{}",
        out.stdout
    );
}

fn write_pi_store(root: &Path, session_id: &str, body: &str) -> PathBuf {
    let dir = root.join("sessions").join("demo");
    fs::create_dir_all(&dir).expect("create pi store");
    let path = dir.join(format!("{session_id}.jsonl"));
    fs::write(&path, body).expect("write session");
    path
}

#[test]
fn browse_watch_listed_session() {
    let root = env::temp_dir().join(format!("traj-ls11-browse-rs-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    write_pi_store(&root, "watch-me", &format!("{SESSION_LINE}{USER_LINE}"));

    let out = run_cli(&[
        "browse",
        "--source",
        "pi",
        "--root",
        root.to_str().expect("utf8 root"),
        "--id",
        "watch-me",
        "--watch",
        "--emit",
        "snapshot+delta",
        "--max-updates",
        "1",
    ]);
    let _ = fs::remove_dir_all(&root);

    assert_eq!(out.code, 0, "stderr={}\nstdout={}", out.stderr, out.stdout);
    let lower = out.stdout.to_ascii_lowercase();
    assert!(
        lower.contains("stream update"),
        "missing stream update:\n{}",
        out.stdout
    );
    assert!(
        lower.contains("live tail"),
        "missing live tail:\n{}",
        out.stdout
    );
    assert!(
        lower.contains("snapshot"),
        "missing snapshot:\n{}",
        out.stdout
    );
    assert!(lower.contains("delta"), "missing delta:\n{}", out.stdout);
    assert!(
        lower.contains("not a daemon"),
        "missing not a daemon:\n{}",
        out.stdout
    );
    assert!(
        !out.stdout.contains("hello"),
        "privacy leak of user prose:\n{}",
        out.stdout
    );
}

#[test]
fn browse_watch_rejects_ahp() {
    let out = run_cli(&[
        "browse", "--source", "ahp", "--root", "/tmp", "--id", "x", "--watch",
    ]);
    assert_eq!(out.code, 2, "stdout={}\nstderr={}", out.stdout, out.stderr);
    let combined = format!("{}\n{}", out.stdout, out.stderr).to_ascii_lowercase();
    assert!(
        combined.contains("invalid_input"),
        "expected invalid_input:\n{combined}"
    );
}

#[test]
fn stream_without_path_or_id_requires_tty() {
    let out = run_cli(&["stream", "--source", "pi"]);
    assert_eq!(out.code, 2, "stdout={}\nstderr={}", out.stdout, out.stderr);
    let combined = format!("{}\n{}", out.stdout, out.stderr).to_ascii_lowercase();
    assert!(
        combined.contains("invalid_input"),
        "expected invalid_input:\n{combined}"
    );
}

#[test]
fn ahp_stream_rejects_ws_url() {
    let out = run_cli(&["ahp-stream", "--url", "ws://localhost:9999", "--chat", CHAT]);
    assert_eq!(out.code, 2, "stdout={}\nstderr={}", out.stdout, out.stderr);
    let combined = format!("{}\n{}", out.stdout, out.stderr);
    assert!(
        combined.contains("fake://"),
        "expected fake:// mention:\n{combined}"
    );
    assert!(
        combined.to_ascii_lowercase().contains("invalid_input"),
        "expected invalid_input:\n{combined}"
    );
}

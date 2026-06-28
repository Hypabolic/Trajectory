# Trajectory for Rust

`hypabolic-trajectory` is the independent native Rust implementation of the
Trajectory contracts. ML3 implements the complete Pi identity path:

- byte-oriented Pi JSONL decoding and normalization;
- typed diagnostics and fatal errors;
- `letta-trajectory-v1`, `letta-canonical-v1`, and
  `hypabolic-trajectory-v1`;
- canonical JSON, deterministic IDs, hashes, ordering, and timestamp policy;
- synchronous Pi listing from an explicit root;
- the private versioned conformance runner.

The workspace uses Rust 2024 with MSRV 1.85. The core crate forbids unsafe code
and has no SQLite, OpenTelemetry, Node, .NET, FFI, or subprocess dependency.
Optional SQLite and OpenTelemetry crates arrive in later roadmap slices.

## Build and verify

From the repository root:

```bash
cargo +1.85.0 test --manifest-path rust/Cargo.toml --workspace --locked
cargo +stable fmt --manifest-path rust/Cargo.toml --all -- --check
cargo +stable clippy --manifest-path rust/Cargo.toml \
  --workspace --all-targets -- -D warnings
cargo +stable doc --manifest-path rust/Cargo.toml \
  --workspace --no-deps
```

Build the runner and execute ML3's applicable shared operations twice:

```bash
cargo +stable build --manifest-path rust/Cargo.toml \
  --release --bin trajectory-conformance
python3 conformance/verify.py --repository-root . --source pi \
  --operation normalize-letta \
  --operation normalize-canonical \
  --operation normalize-hypabolic \
  --operation list-trajectories -- \
  rust/target/release/trajectory-conformance
```

Operation selection is capability-scoped. OpenAI, minimal JSONL, and
OpenTelemetry projection remain ML7 work and are not advertised by ML3.

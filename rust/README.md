# Trajectory for Rust

`hypabolic-trajectory` is the independent native Rust implementation of the
Trajectory contracts.

Published **`0.1.0` / ML13** advertised the complete historical v1 source/output
paths (Pi, Claude Code, Codex, OpenClaw, Hermes + six projections). This tree
additionally implements **AHP** Shape A offline snapshot ingest (wire name
`ahp`; listing deferred). AHP is **not** in registry packages at `0.1.0` and
needs a new tag to publish.

- byte-oriented Pi, Claude Code, Codex, OpenClaw, Hermes, and Grok Build
  decoding and normalization;
- AHP Shape A ChatState snapshot decode (export file / `show --path`; listing
  empty stub);
- Claude Code producer/context and model-invocation metadata retention;
- Hermes array/envelope decode with SQLite-free empty-page listing when no
  store is present;
- typed diagnostics and fatal errors;
- all six shared deterministic projections, including OpenAI chat, streaming
  minimal JSONL, and GenAI span sets;
- canonical JSON, deterministic IDs, hashes, ordering, and timestamp policy;
- synchronous source listing from explicit roots;
- the private versioned conformance runner.

The workspace uses Rust 2024 with MSRV 1.85. The core crate forbids unsafe code
and has no SQLite, OpenTelemetry, Node, .NET, FFI, or subprocess dependency.
`hypabolic-trajectory-opentelemetry` is a separate optional package with an
application-owned SDK sink boundary; it does not add dependencies to core.
Optional SQLite integrations arrive in later roadmap slices.

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

Build the runner and execute every advertised shared operation twice:

```bash
cargo +stable build --manifest-path rust/Cargo.toml \
  --release --bin trajectory-conformance
python3 conformance/verify.py --repository-root . -- \
  rust/target/release/trajectory-conformance
```

`runtime-capabilities.json` is the machine-readable declaration of implemented
sources and six outputs (slice `ML13`). On this tip the source set includes
`ahp` and `grok-build`; published registry `0.1.0` stopped at Hermes. See
[docs/release-readiness.md](../docs/release-readiness.md) and
[docs/ahp-source-spec.md](../docs/ahp-source-spec.md). `write_schema` and
`write_minimal_jsonl` provide `std::io::Write` surfaces; the latter emits one

Run the dependency-free representative benchmark:

```bash
cargo +stable run --manifest-path rust/Cargo.toml \
  -p hypabolic-trajectory --example benchmark --release
```

## Sample CLI

The unpublished `trajectory-cli` tool lists local agent stores and prints
privacy-safe trajectory summaries. See
[tools/trajectory-cli/README.md](tools/trajectory-cli/README.md).

```bash
cargo run --manifest-path rust/Cargo.toml -p trajectory-cli -- list --source pi
cargo run --manifest-path rust/Cargo.toml -p trajectory-cli -- show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl
```

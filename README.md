# Trajectory

Trajectory turns coding-agent session transcripts into deterministic,
versioned trajectory formats for memory, replay, evaluation, search, training,
and observability pipelines.

It is one product with native ecosystem implementations governed by the same
wire contracts and conformance cases:

| Runtime | Package | Status |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | Implemented |
| TypeScript | `@hypabolic/trajectory` | Pi, Claude Code, and Codex baseline implemented; unpublished |
| Rust | `hypabolic-trajectory` | Pi, Claude Code, and Codex baseline implemented; unpublished |

The current .NET runtime supports Pi, Claude Code, and Codex transcripts,
explicit-root local-store listing, trimming and Native AOT, and these
deterministic projections:

- Letta trajectory v1;
- Letta canonical v1;
- Hypabolic trajectory v1;
- OpenAI chat messages;
- minimal JSONL;
- OpenTelemetry GenAI span sets through the optional
  `Hypabolic.Trajectory.OpenTelemetry` package.

Rust and TypeScript packages have not been published. Both are independent
implementations built from this repository's specifications and conformance
cases. TypeScript supports the current .NET Pi, Claude Code, and Codex source
baseline and all deterministic projections. Rust ML6 supports the same Pi,
Claude Code, and Codex source baseline with the three identity-bearing
projections. Both provide explicit-root listing for their advertised sources
and the private conformance protocol. The pinned `letta-ai/trajectory` package
is used only as a black-box compatibility oracle.

## Architecture

```text
native source bytes
  -> source decoder
  -> shared normalization policy
  -> implementation-private trajectory IR
  -> versioned output adapters
```

Source decoding, normalization, identity, output projection, listing, and
optional integrations remain independently testable. Each runtime owns an
idiomatic private IR; Trajectory does not define a serialized public IR or a
shared native implementation.

The .NET core package is BCL-only. OpenTelemetry dependencies remain in the
optional package, and future SQLite/checkpoint dependencies will remain outside
core.

## Repository

```text
contracts/       versioned schemas and normative behavioral specifications
conformance/     shared native fixtures, expected outputs, stores, and protocol
dotnet/          current .NET source, tests, AOT smoke app, and solution
rust/            independent Rust workspace, core crate, and private runner
typescript/      independent TypeScript packages, tests, and private runner
docs/            product architecture, parity baseline, and roadmap
```

`contracts/compatibility.json` records the pinned upstream reference, contract
versions, schema versions, capability vocabulary, and currently implemented
sources and outputs. Contract and conformance files at the repository root are
authoritative. Runtime tests consume them directly.

Implementation-specific unit fixtures stay under the relevant runtime. A
fixture belongs in `conformance/` when another independent implementation must
produce the same observable result.

## Build and test .NET

The .NET projects target `net8.0`, `net9.0`, and `net10.0`; the test and private
conformance executables run on `net10.0`.

```bash
dotnet restore dotnet/Trajectory.sln
dotnet build dotnet/Trajectory.sln -c Release --no-restore
dotnet test dotnet/tests/Trajectory.Tests/Trajectory.Tests.csproj \
  -c Release --no-build
```

Run every shared case through the .NET runner:

```bash
python3 conformance/verify.py --repository-root . -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll
```

Publish and run the Native AOT smoke target:

```bash
dotnet publish dotnet/tests/Trajectory.AotSmoke/Trajectory.AotSmoke.csproj \
  -c Release -r linux-x64 --self-contained true
./dotnet/tests/Trajectory.AotSmoke/bin/Release/net10.0/linux-x64/publish/Hypabolic.Trajectory.AotSmoke
```

The private runner protocol and case-authoring workflow are documented in
[conformance/README.md](conformance/README.md).

## Build and test TypeScript

The TypeScript workspace supports Node.js 22 and newer. It is tested on Node
22 and 24 across Linux, macOS, and Windows, with a Node 26 package smoke gate.

```bash
cd typescript
npm ci
npm run typecheck
npm test
```

Run the shared Pi cases through the TypeScript runner:

```bash
python3 conformance/verify.py --repository-root . --source pi -- \
  node typescript/packages/trajectory-testing/dist/cli.js
```

`@hypabolic/trajectory` is byte-oriented and environment-neutral.
`@hypabolic/trajectory-node` owns filesystem listing,
`@hypabolic/trajectory-otel` owns the optional OpenTelemetry projection, and
`@hypabolic/trajectory-testing` owns the unpublished runner.

## Build and test Rust

The Rust workspace uses Rust 2024, has an MSRV of 1.85, and is tested on MSRV
and stable across Linux, macOS, and Windows.

```bash
cargo +1.85.0 test --manifest-path rust/Cargo.toml --workspace --locked
cargo +stable fmt --manifest-path rust/Cargo.toml --all -- --check
cargo +stable clippy --manifest-path rust/Cargo.toml \
  --workspace --all-targets -- -D warnings
```

Run ML6's identity-bearing source baseline and listing through the Rust runner:

```bash
cargo +stable build --manifest-path rust/Cargo.toml \
  --release --bin trajectory-conformance
python3 conformance/verify.py --repository-root . --source pi \
  --operation normalize-letta \
  --operation normalize-canonical \
  --operation normalize-hypabolic \
  --operation list-trajectories -- \
  rust/target/release/trajectory-conformance
python3 conformance/verify.py --repository-root . --source claude-code \
  --operation normalize-letta \
  --operation normalize-canonical \
  --operation normalize-hypabolic \
  --operation list-trajectories -- \
  rust/target/release/trajectory-conformance
python3 conformance/verify.py --repository-root . --source codex \
  --operation normalize-letta \
  --operation normalize-canonical \
  --operation normalize-hypabolic \
  --operation list-trajectories -- \
  rust/target/release/trajectory-conformance
```

The core crate owns the byte-oriented source and projection traits, typed
models/errors, canonical identity, and synchronous explicit-root listing. It
does not depend on SQLite or OpenTelemetry. See [rust/README.md](rust/README.md)
for the package boundary and full validation commands.

## .NET usage

```csharp
var engine = TrajectoryEngine.CreateDefault();

var input = new NormalizeInput
{
    Source = TrajectorySource.Codex,
    Transcript = transcript,
    SourceContext = new SourceContext
    {
        GroupId = sessionId,
        BaseByteOffset = offset,
        Partial = true,
    },
};

var ir = engine.NormalizeToIR(input);
var canonical = engine.Project<LettaCanonicalResult>(
    ir,
    OutputSchemaIds.LettaCanonicalV1);
var hypabolic = engine.Project<HypabolicTrajectoryV1>(
    ir,
    OutputSchemaIds.HypabolicTrajectoryV1);
```

The API may still evolve before the first NuGet release. Versioned wire
contracts, canonical identity, hashes, diagnostics, and conformance behavior
take precedence over preserving an unpublished pre-release API.

## Compatibility policy

- The compatibility pin changes only in an explicit compatibility update.
- Existing identity-bearing bytes never change under the same contract
  version.
- Canonical JSON is the documented Trajectory algorithm, not RFC 8785/JCS.
- Diagnostics and fatal errors are typed and content-safe.
- New runtime capabilities are advertised only after their shared cases pass.
- Goldens are reviewed artifacts; CI never regenerates and accepts them.
- Pre-1.0 package releases remain synchronized because normalizer version
  participates in canonical output.

See:

- [architecture](docs/architecture.md);
- [compatibility and multi-language roadmap](docs/multi-language-plan.md);
- [detailed .NET behavior baseline](docs/implementation-plan.md);
- [pinned parity baseline](docs/parity-baseline.md);
- [.NET adapter authoring](dotnet/docs/adapter-authoring.md);
- [normative specifications](contracts/spec/normalization.md).

## Roadmap

ML1 established the shared foundation; ML2 and ML3 added independent
TypeScript and Rust Pi vertical paths; ML4 brought TypeScript to the current
.NET Pi, Claude Code, and Codex source baseline; ML5 and ML6 brought Rust to
the same source baseline. ML7 is next: complete output and distribution parity
across all three implementations.

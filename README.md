# Trajectory

**Trajectory** normalizes coding-agent session transcripts into stable, versioned
records you can store, search, replay, evaluate, train on, and observe.

One product. Three native packages. The same wire contracts and conformance
suite in every ecosystem.

| Ecosystem | Package | Install |
| --- | --- | --- |
| .NET | [`Hypabolic.Trajectory`](https://www.nuget.org/packages/Hypabolic.Trajectory) | `dotnet add package Hypabolic.Trajectory` |
| TypeScript | [`@hypabolic/trajectory`](https://www.npmjs.com/package/@hypabolic/trajectory) | `npm install @hypabolic/trajectory` |
| Rust | [`hypabolic-trajectory`](https://crates.io/crates/hypabolic-trajectory) | `cargo add hypabolic-trajectory` |

Optional OpenTelemetry packages: `Hypabolic.Trajectory.OpenTelemetry`,
`@hypabolic/trajectory-otel`, `hypabolic-trajectory-opentelemetry`.

Current version: **0.1.0** (synchronized across NuGet, npm, and crates.io).

## What you get

- **Multi-source ingest** — Pi, Claude Code, Codex, OpenClaw, and Hermes
  transcripts
- **Deterministic normalization** — stable IDs, ordering, hashes, and
  content-safe diagnostics on every run
- **Multiple outputs** from one decode:
  - Hypabolic trajectory (provenance-rich)
  - Canonical identity records
  - Compact message trajectory arrays
  - OpenAI-style chat messages
  - Minimal JSONL
  - OpenTelemetry GenAI span projections (optional packages)
- **Local store listing** — discover sessions under each agent’s default paths
- **Partial / chunked input** — append-only and offset-aware normalization where
  the source supports it
- **Native AOT / trim-friendly .NET**, ESM TypeScript (Node 22+), Rust 2024
  (MSRV 1.85)

## Quick start

### .NET

```csharp
using Hypabolic.Trajectory;

var engine = TrajectoryEngine.CreateDefault();

var input = new NormalizeInput
{
    Source = TrajectorySource.Codex,
    Transcript = transcriptBytes,
    SourceContext = new SourceContext
    {
        GroupId = sessionId,
        Partial = true,
    },
};

var ir = engine.NormalizeToIR(input);
var hypabolic = engine.Project<HypabolicTrajectoryV1>(
    ir,
    OutputSchemaIds.HypabolicTrajectoryV1);
```

```bash
dotnet add package Hypabolic.Trajectory
```

### TypeScript

```ts
import { normalizeToHypabolic } from "@hypabolic/trajectory";

const result = normalizeToHypabolic({
  source: "pi",
  transcript: bytes,
});
```

```bash
npm install @hypabolic/trajectory
# Optional Node listing helpers:
npm install @hypabolic/trajectory-node
```

### Rust

```rust
use hypabolic_trajectory::{normalize_pi, project_hypabolic, NormalizeRequest};

let trajectory = normalize_pi(NormalizeRequest {
    transcript: &bytes,
    ..Default::default()
})?;
let hypabolic = project_hypabolic(&trajectory)?;
```

```bash
cargo add hypabolic-trajectory
```

## Supported sources

| Source | Typical input | Default local store |
| --- | --- | --- |
| Pi | Session JSONL | `~/.pi/agent` |
| Claude Code | Session JSONL | `~/.claude/projects` |
| Codex | Rollout JSONL | `~/.codex/sessions` |
| OpenClaw | Session JSONL | `~/.openclaw` |
| Hermes | Message array or `{ session, messages }` JSON | export from store; listing is optional |

Pass an explicit root when listing; missing stores return an empty page.

## Sample CLIs

Try Trajectory against sessions already on your machine (not published packages):

| Runtime | Location |
| --- | --- |
| .NET | `dotnet/samples/Trajectory.Cli` |
| TypeScript | `typescript/packages/trajectory-cli` |
| Rust | `rust/tools/trajectory-cli` |

```bash
# .NET
dotnet run --project dotnet/samples/Trajectory.Cli -- list --source claude-code
dotnet run --project dotnet/samples/Trajectory.Cli -- browse --source pi

# TypeScript
cd typescript && npm ci && npm run build
node packages/trajectory-cli/dist/cli.js list --source codex

# Rust
cargo run -p trajectory-cli --manifest-path rust/Cargo.toml -- list --source pi
```

Summaries omit transcript content by default. Use `--show-content` only when you
intend to print session text (privacy warning applied).

## How it works

```text
native source bytes
  → source decoder
  → shared normalization policy
  → private intermediate representation
  → versioned output adapters
```

Implementations are independent per language. They do not share a runtime, FFI
bridge, or subprocess. Behaviour is locked by shared contracts and conformance
cases under `contracts/` and `conformance/`.

## Repository layout

```text
contracts/     versioned schemas and behavioural specifications
conformance/   shared fixtures, expected outputs, and verify protocol
dotnet/        .NET libraries, tests, AOT smoke, sample CLI
typescript/    npm packages, tests, sample CLI
rust/          crates, conformance binary, sample CLI
docs/          architecture, formats, publishing
tools/         release and bootstrap helpers
```

## Build from source

### .NET (`net8.0` / `net9.0` / `net10.0`)

```bash
dotnet restore dotnet/Trajectory.sln
dotnet build dotnet/Trajectory.sln -c Release --no-restore
dotnet test dotnet/tests/Trajectory.Tests/Trajectory.Tests.csproj -c Release --no-build
```

### TypeScript (Node 22+)

```bash
cd typescript
npm ci
npm run typecheck
npm test
```

### Rust (1.85+ / stable)

```bash
cargo test --manifest-path rust/Cargo.toml --workspace --locked
```

### Shared conformance

```bash
# .NET runner (after building Trajectory.Conformance)
python3 conformance/verify.py --repository-root . -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll
```

## Compatibility promises

- Identity-bearing output bytes do not change under the same normalizer contract
  version (`0.2.0` today).
- Diagnostics and fatal errors are typed and never include raw transcript
  secrets by contract.
- Capabilities are advertised only after shared conformance cases pass.
- Golden fixtures are reviewed artifacts; CI does not auto-accept regenerations.
- Pre-1.0 package versions stay synchronized across ecosystems because the
  normalizer version participates in canonical identity.

## Documentation

| Doc | Audience |
| --- | --- |
| [Architecture](docs/architecture.md) | How normalization and adapters fit together |
| [Hypabolic trajectory format](docs/hypabolic-trajectory-v1.md) | Provenance-rich output schema |
| [OpenTelemetry GenAI output](docs/otel-genai-output.md) | Span projection and privacy defaults |
| [Publishing](docs/publishing.md) | Registry release process |
| [Release readiness](docs/release-readiness.md) | Privacy, packaging, and 1.0 gates |
| [Normative specs](contracts/spec/normalization.md) | Wire behaviour (identity, timestamps, diagnostics) |
| [.NET adapter authoring](dotnet/docs/adapter-authoring.md) | Extending sources and outputs |

## License

MIT — see [LICENSE](LICENSE).

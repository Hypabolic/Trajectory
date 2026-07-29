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

Releases use the **git tag as the version** (same model as Hypa): push
`v0.1.0` and CI stamps packages, publishes NuGet/npm/crates, and creates a
GitHub Release. See [docs/publishing.md](docs/publishing.md).

## What you get

- **Multi-source ingest** — Pi, Claude Code, Codex, OpenClaw, and Hermes
- **Deterministic normalization** — stable IDs, ordering, hashes, content-safe
  diagnostics
- **Multiple outputs** from one decode: Hypabolic trajectory, canonical
  identity, compact message arrays, OpenAI chat messages, minimal JSONL, and
  optional OpenTelemetry GenAI spans
- **Local store listing** with explicit roots and pagination
- **Partial / chunked input** where the source supports append-only sessions
- **Native AOT–friendly .NET**, ESM TypeScript (Node 22+), Rust 2024 (MSRV 1.85)

## Install

```bash
# .NET
dotnet add package Hypabolic.Trajectory
# optional: dotnet add package Hypabolic.Trajectory.OpenTelemetry

# TypeScript
npm install @hypabolic/trajectory
npm install @hypabolic/trajectory-node   # local listing
# optional: npm install @hypabolic/trajectory-otel

# Rust
cargo add hypabolic-trajectory
# optional: cargo add hypabolic-trajectory-opentelemetry
```

## Usage examples

### .NET — normalize and project

```csharp
using Hypabolic.Trajectory;

byte[] transcript = await File.ReadAllBytesAsync(path);
var engine = TrajectoryEngine.CreateDefault();

var input = new NormalizeInput
{
    Source = TrajectorySource.ClaudeCode,
    Transcript = transcript,
    SourceContext = new SourceContext
    {
        // Optional: group / session id when the source requires it (e.g. Codex)
        GroupId = sessionId,
        BaseByteOffset = 0,
        Partial = false,
    },
};

// Intermediate representation (implementation-private shape; use for multi-project)
var ir = engine.NormalizeToIR(input);

// Provenance-rich Hypabolic document
var hypabolic = engine.Project<HypabolicTrajectoryV1>(
    ir,
    OutputSchemaIds.HypabolicTrajectoryV1);

// Canonical identity (stable record ids / hashes)
var canonical = engine.Project<LettaCanonicalResult>(
    ir,
    OutputSchemaIds.LettaCanonicalV1);

// Compact message-trajectory array
var messages = TrajectoryConverter.NormalizeTranscript(
    TrajectorySource.ClaudeCode,
    transcript);
```

Partial / chunked Codex-style input:

```csharp
var chunk = new NormalizeInput
{
    Source = TrajectorySource.Codex,
    Transcript = chunkBytes,
    SourceContext = new SourceContext
    {
        GroupId = sessionId,
        BaseByteOffset = absoluteUtf8Offset,
        Partial = true,
    },
};
var ir = engine.NormalizeToIR(chunk);
```

### TypeScript — bytes in, projections out

```ts
import { readFileSync } from "node:fs";
import {
  normalizeToIR,
  normalizeToHypabolic,
  normalizeToCanonical,
  normalizeToLetta,
  projectOpenAI,
  projectMinimalJsonl,
} from "@hypabolic/trajectory";

const transcriptBytes = readFileSync(path);

const request = {
  source: "pi" as const,
  transcriptBytes,
  sourceContext: { partial: false },
  options: {
    bounds: {
      toolResults: { maxCharacters: 2500, strategy: "head-tail" as const },
    },
    filters: { toolResults: "include" as const },
  },
};

const ir = normalizeToIR(request);
const hypabolic = normalizeToHypabolic(request);
const canonical = normalizeToCanonical(request);
const messages = normalizeToLetta(request); // compact message trajectory
const openai = projectOpenAI(ir);
const minimalJsonl = projectMinimalJsonl(ir);
```

List local Claude Code sessions (Node):

```ts
import { listClaudeCodeTrajectories } from "@hypabolic/trajectory-node";
import { homedir } from "node:os";
import { join } from "node:path";

const page = await listClaudeCodeTrajectories({
  root: join(homedir(), ".claude", "projects"),
  limit: 20,
});

for (const item of page.items) {
  console.log(item.id, item.path, item.updatedAt ?? "");
}
```

### Rust — source helpers and projectors

```rust
use std::fs;
use hypabolic_trajectory::{
    normalize_codex, normalize_pi, project_canonical, project_hypabolic,
    project_openai, NormalizeRequest, SourceContext,
};

let bytes = fs::read(path)?;

// Pi session file
let pi = normalize_pi(NormalizeRequest {
    transcript: &bytes,
    ..Default::default()
})?;
let hypabolic = project_hypabolic(&pi)?;

// Codex chunk with group id + absolute byte offset
let codex = normalize_codex(NormalizeRequest {
    transcript: &chunk_bytes,
    context: SourceContext {
        group_id: Some(session_id.into()),
        base_byte_offset: Some(offset),
        partial: true,
        ..Default::default()
    },
    ..Default::default()
})?;
let canonical = project_canonical(&codex)?;
let openai = project_openai(&codex)?;
```

List Pi sessions under an explicit root:

```rust
use hypabolic_trajectory::{list_pi_trajectories, ListingOptions};
use std::path::Path;

let page = list_pi_trajectories(&ListingOptions {
    root: Path::new("/path/to/agent-root"),
    limit: 50,
    ..Default::default()
})?;
for item in page.items {
    println!("{} {}", item.id, item.path.display());
}
```

## Supported sources

| Source | Typical input | Default local store |
| --- | --- | --- |
| Pi | Session JSONL | `~/.pi/agent` (`PI_CODING_AGENT_DIR`) |
| Claude Code | Session JSONL | `~/.claude/projects` |
| Codex | Rollout JSONL | `~/.codex/sessions` |
| OpenClaw | Session JSONL | `~/.openclaw` or legacy `~/.clawdbot` |
| Hermes | Message array or `{ session, messages }` JSON | Export file; core listing is SQLite-free |

Override listing roots with `--root` / `TRAJECTORY_<SOURCE>_ROOT` in the sample
CLIs, or pass an explicit root to listing APIs.

## Sample CLIs (try your local sessions)

Unpublished developer tools that list agent stores on disk and normalize a
selected session into a **privacy-safe summary** (counts, roles, tools,
diagnostics—no transcript body by default).

| Runtime | Path | Binary / entry |
| --- | --- | --- |
| .NET | [`dotnet/samples/Trajectory.Cli`](dotnet/samples/Trajectory.Cli/README.md) | `dotnet run --project …` |
| TypeScript | [`typescript/packages/trajectory-cli`](typescript/packages/trajectory-cli/README.md) | `node packages/trajectory-cli/dist/cli.js` |
| Rust | [`rust/tools/trajectory-cli`](rust/tools/trajectory-cli/README.md) | `cargo run -p trajectory-cli` |

### Commands (same shape in all three)

| Command | Purpose |
| --- | --- |
| `browse` (default) | Interactive: pick source → session → print summary |
| `list` | Table of sessions for one source |
| `show` | Normalize one `--path` or listing `--id` |

Shared flags:

| Flag | Meaning |
| --- | --- |
| `--source <name>` | `pi`, `claude-code`, `codex`, `openclaw`, `hermes` |
| `--root <path>` | Override store root |
| `--limit <n>` | Listing page size (default 50) |
| `--format <f>` | `both` (default), `messages`, or `hypabolic` |
| `--show-content` | Include text snippets (**private data**; prints a warning) |
| `--path` / `--id` | `show` only: file path or listing id |

### Run examples

```bash
# .NET — list Claude Code sessions, then show a fixture
dotnet run --project dotnet/samples/Trajectory.Cli -- list --source claude-code --limit 10
dotnet run --project dotnet/samples/Trajectory.Cli -- show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl
dotnet run --project dotnet/samples/Trajectory.Cli -- browse --source codex

# TypeScript
cd typescript && npm ci && npm run build
node packages/trajectory-cli/dist/cli.js list --source pi
node packages/trajectory-cli/dist/cli.js show \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl \
  --format hypabolic
node packages/trajectory-cli/dist/cli.js browse

# Rust
cargo run -p trajectory-cli --manifest-path rust/Cargo.toml -- list --source codex
cargo run -p trajectory-cli --manifest-path rust/Cargo.toml -- show \
  --source hermes \
  --path conformance/cases/hermes/tool-calls/input.json
```

**Notes**

- Empty or missing stores exit successfully with a clear message.
- Hermes listing in core returns empty (no SQLite dependency); export JSON and
  `show --path`.
- These CLIs are **not** published NuGet/npm/crates packages.

## How it works

```text
native source bytes
  → source decoder
  → shared normalization policy
  → private intermediate representation
  → versioned output adapters
```

Implementations are independent per language. Behaviour is locked by shared
contracts (`contracts/`) and executable cases (`conformance/`).

## Repository layout

```text
contracts/     versioned schemas and behavioural specifications
conformance/   shared fixtures, goldens, verify.py, private runners’ protocol
dotnet/        libraries, tests, AOT smoke, sample CLI
typescript/    npm packages, tests, sample CLI
rust/          crates, conformance binary, sample CLI
docs/          architecture, authoring, contributing, publishing
tools/         release and npm bootstrap helpers
```

## Build from source

### .NET

```bash
dotnet restore dotnet/Trajectory.sln
dotnet build dotnet/Trajectory.sln -c Release --no-restore
dotnet test dotnet/tests/Trajectory.Tests/Trajectory.Tests.csproj -c Release --no-build
```

### TypeScript

```bash
cd typescript && npm ci && npm run typecheck && npm test
```

### Rust

```bash
cargo test --manifest-path rust/Cargo.toml --workspace --locked
```

### Shared conformance

```bash
dotnet build dotnet/tests/Trajectory.Conformance/Trajectory.Conformance.csproj -c Release
python3 conformance/verify.py --repository-root . -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll
```

See [conformance/README.md](conformance/README.md) for case authoring and all
runners.

## Contributing

We welcome issues and PRs that improve adapters, fixtures, docs, and packaging.

1. Read **[Contributing](docs/contributing.md)** for setup, PR checklist, and
   fixture privacy rules.
2. For new agent sources or output formats, follow
   **[Authoring sources and outputs](docs/adapter-authoring.md)** (multi-runtime)
   and the [.NET adapter seams](dotnet/docs/adapter-authoring.md) when on C#.
3. Behaviour changes need shared conformance cases reviewed by hand—never
   auto-accept goldens in CI.

## Compatibility promises

- Identity-bearing output bytes do not change under the same normalizer contract
  version (`0.2.0` today).
- Diagnostics are typed and content-safe by contract.
- Capabilities are advertised only after shared cases pass.
- Pre-1.0 package versions stay synchronized across ecosystems.

## Documentation

| Doc | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Pipeline, packages, design principles |
| [Adapter authoring](docs/adapter-authoring.md) | New sources and outputs (all runtimes) |
| [Contributing](docs/contributing.md) | Setup, PR checklist, workflows |
| [Hypabolic trajectory format](docs/hypabolic-trajectory-v1.md) | Provenance-rich output |
| [OpenTelemetry GenAI](docs/otel-genai-output.md) | Span projection and privacy |
| [Publishing](docs/publishing.md) | NuGet / npm / crates release |
| [Release readiness](docs/release-readiness.md) | Privacy, packaging, 1.0 gates |
| [Normative specs](contracts/spec/normalization.md) | Identity, timestamps, diagnostics |
| [Conformance](conformance/README.md) | Shared cases and runners |

## License

MIT — see [LICENSE](LICENSE).

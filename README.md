# Trajectory

<img width="612" height="103" alt="image" src="https://github.com/user-attachments/assets/3286ba53-81f0-48e3-bf1b-c743ed72ed9a" />


**Trajectory** normalizes coding-agent session transcripts into stable, versioned
records you can store, search, replay, evaluate, train on, and observe.

One product. Four native packages. The same wire contracts and conformance
suite in every ecosystem.

| Ecosystem | Package | Install |
| --- | --- | --- |
| .NET | [`Hypabolic.Trajectory`](https://www.nuget.org/packages/Hypabolic.Trajectory) | `dotnet add package Hypabolic.Trajectory` |
| TypeScript | [`@hypabolic/trajectory`](https://www.npmjs.com/package/@hypabolic/trajectory) | `npm install @hypabolic/trajectory` |
| Rust | [`hypabolic-trajectory`](https://crates.io/crates/hypabolic-trajectory) | `cargo add hypabolic-trajectory` |
| Python | [`hypabolic-trajectory`](https://pypi.org/project/hypabolic-trajectory/) (first public cut with next multi-registry tag) | `pip install hypabolic-trajectory==<tag-semver>` |

Optional OpenTelemetry: `Hypabolic.Trajectory.OpenTelemetry`,
`@hypabolic/trajectory-otel`, `hypabolic-trajectory-opentelemetry`, and Python
extra `hypabolic-trajectory[otel]` (SDK sinks only — pure OTEL project is in core).

Releases use the **git tag as the version** (same model as Hypa): push
`vX.Y.Z` and CI stamps packages, publishes NuGet/npm/crates/PyPI, and creates a
GitHub Release. See [docs/publishing.md](docs/publishing.md).

> **Published vs this tree:** NuGet / npm / crates at **`0.1.0`** include Pi, Claude
> Code, Codex, OpenClaw, and Hermes only (**no** AHP, **no** PyPI yet). **AHP**
> Shape A offline snapshot ingest and the **Python** runtime are implemented
> **in this repository tip** and ship under the **next** synchronized package
> version (a new tag after `v0.1.0`). Install unversioned NuGet/npm/crates
> commands resolve to latest published `0.1.0` until that cut.

## What you get

- **Multi-source ingest** — Pi, Claude Code, Codex, OpenClaw, Hermes, AHP
  (Shape A offline ChatState snapshots), and Grok Build (`grok-build`)
- **Deterministic normalization** — stable IDs, ordering, hashes, content-safe
  diagnostics
- **Multiple outputs** from one decode: Hypabolic trajectory, canonical
  identity, compact message arrays, OpenAI chat messages, minimal JSONL, and
  optional OpenTelemetry GenAI spans
- **Local store listing** with explicit roots and pagination
- **Partial / chunked input** where the source supports append-only sessions
- **Native AOT–friendly .NET**, ESM TypeScript (Node 22+), Rust 2024 (MSRV 1.85),
  Python 3.11+

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

# Python (first public PyPI cut ships with the next multi-registry tag after 0.1.0)
pip install hypabolic-trajectory==<tag-semver>
# optional SDK sinks only:
pip install 'hypabolic-trajectory[otel]==<tag-semver>'
# monorepo / pre-publish:
#   python -m pip install -e './python[dev]'
```

## Usage examples

Trajectory is two steps:

1. **Find** sessions in the agent’s local store (listing APIs know default roots)
2. **Normalize** the transcript bytes into projections

You only pass a raw path when you already have one (export, upload, pipe).
For “what’s on this machine?”, use listing first.

### .NET — list, then normalize

```csharp
using Hypabolic.Trajectory;

// Discover Claude Code sessions under the default root (~/.claude/projects)
var page = await TrajectoryConverter.ListClaudeCodeTrajectoriesAsync(limit: 20);
var session = page.Items[0]; // Path, Id, UpdatedAt, …

byte[] transcript = await File.ReadAllBytesAsync(session.Path);
var engine = TrajectoryEngine.CreateDefault();

var ir = engine.NormalizeToIR(new NormalizeInput
{
    Source = TrajectorySource.ClaudeCode,
    Transcript = transcript,
});

var hypabolic = engine.Project<HypabolicTrajectoryV1>(
    ir, OutputSchemaIds.HypabolicTrajectoryV1);
var canonical = engine.Project<LettaCanonicalResult>(
    ir, OutputSchemaIds.LettaCanonicalV1);
var messages = TrajectoryConverter.NormalizeTranscript(
    TrajectorySource.ClaudeCode, transcript);
```

Same idea for any source (`ListPiTrajectoriesAsync`, `ListCodexTrajectoriesAsync`,
or `ListTrajectoriesAsync(TrajectorySource.OpenClaw)`). Pass `root:` to override
the default store. Codex partial/chunked input can still set `GroupId` and
`BaseByteOffset` when you feed append-only slices.

### TypeScript — list (Node), then normalize

```ts
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import {
  normalizeToHypabolic,
  normalizeToCanonical,
  normalizeToLetta,
} from "@hypabolic/trajectory";
import { listClaudeCodeTrajectories } from "@hypabolic/trajectory-node";

// Node listing package — pass the store root (defaults are not assumed)
const page = await listClaudeCodeTrajectories({
  root: join(homedir(), ".claude", "projects"),
  limit: 20,
});
const session = page.items[0]; // id, path, updatedAt, sizeBytes

const transcriptBytes = readFileSync(session.path);
const request = {
  source: "claude-code" as const,
  transcriptBytes,
  sourceContext: { partial: false },
};

const hypabolic = normalizeToHypabolic(request);
const canonical = normalizeToCanonical(request);
const messages = normalizeToLetta(request); // compact message trajectory
```

Also: `listPiTrajectories`, `listCodexTrajectories`, `listOpenClawTrajectories`.

### Rust — list, then normalize

Rust listing always takes an **explicit root** (no home-directory default in
the library; the sample CLI applies the usual `~/.claude/projects` etc.).

```rust
use std::fs;
use std::path::Path;
use hypabolic_trajectory::{
    list_claude_code_trajectories, normalize_claude_code, project_canonical,
    project_hypabolic, ListingOptions, NormalizeRequest,
};

let page = list_claude_code_trajectories(&ListingOptions {
    root: Path::new("/home/you/.claude/projects"),
    limit: 20,
    cursor: None,
})?;
let session = &page.items[0]; // id, path, updated_at, size_bytes

let bytes = fs::read(&session.path)?;
let ir = normalize_claude_code(NormalizeRequest {
    transcript: &bytes,
    ..Default::default()
})?;
let hypabolic = project_hypabolic(&ir)?;
let canonical = project_canonical(&ir)?;
```

Also: `list_pi_trajectories`, `list_codex_trajectories`, `list_openclaw_trajectories`
with matching `normalize_*` helpers.

### Python — list, then normalize

Listing always takes an **explicit root** (no home-directory default in the
library). Pure OTEL GenAI projection needs no OpenTelemetry SDK.

```python
from pathlib import Path
from hypabolic_trajectory import (
    NormalizeRequest,
    TrajectorySource,
    list_trajectories,
    normalize_to_ir,
    project_canonical,
    project_hypabolic,
    project_otel_genai,
    serialize_projection,
)

page = list_trajectories(
    source=TrajectorySource.CLAUDE_CODE,
    root=Path.home() / ".claude" / "projects",
    limit=20,
)
session = page.items[0]

ir = normalize_to_ir(
    NormalizeRequest(
        source=TrajectorySource.CLAUDE_CODE,
        transcript=Path(session.path).read_bytes(),
    )
)
hypabolic = project_hypabolic(ir)
canonical = project_canonical(ir)
spans = project_otel_genai(ir)  # pure; no opentelemetry-* required
wire = serialize_projection(hypabolic)
```

Also: any wire source name (`"pi"`, `"codex"`, `"openclaw"`, `"hermes"`, `"ahp"`,
…). Package docs: [`python/README.md`](python/README.md) (filters, dual
timestamps, identity formulas, OTEL import matrix, filtered conformance argv).

## Supported sources

| Source | Typical input | Default local store |
| --- | --- | --- |
| Pi | Session JSONL | `~/.pi/agent` (`PI_CODING_AGENT_DIR`) |
| Claude Code | Session JSONL | `~/.claude/projects` |
| Codex | Rollout JSONL | `~/.codex/sessions` |
| OpenClaw | Session JSONL | `~/.openclaw` or legacy `~/.clawdbot` |
| Hermes | Message array or `{ session, messages }` JSON | Export file; core listing is SQLite-free |
| AHP | Shape A chat snapshot `{ chat, session? }` JSON | Export file only; listing is Phase 3 (empty stub) |
| Grok Build | `chat_history.jsonl` (alias `grok`) | `$GROK_HOME/sessions` or `~/.grok/sessions` |

Override listing roots with `--root` / `TRAJECTORY_<SOURCE>_ROOT` in the sample
CLIs, or pass an explicit root to listing APIs.

## Sample CLIs (try your local sessions)

Unpublished developer tools that list agent stores on disk and normalize a
selected session into a **privacy-safe summary** (counts, roles, tools,
diagnostics—no transcript body by default).

<img width="607" height="278" alt="image" src="https://github.com/user-attachments/assets/abfa4f1e-273f-4be9-9141-b38f0fa3751f" />
<img width="1471" height="717" alt="image" src="https://github.com/user-attachments/assets/c39d80b3-519d-4513-b988-df285369f4c8" />


| Runtime | Path | Binary / entry |
| --- | --- | --- |
| .NET | [`dotnet/samples/Trajectory.Cli`](dotnet/samples/Trajectory.Cli/README.md) | `dotnet run --project …` |
| TypeScript | [`typescript/packages/trajectory-cli`](typescript/packages/trajectory-cli/README.md) | `node packages/trajectory-cli/dist/cli.js` |
| Rust | [`rust/tools/trajectory-cli`](rust/tools/trajectory-cli/README.md) | `cargo run -p trajectory-cli` |
| Python | [`python/samples/trajectory_cli`](python/samples/trajectory_cli/README.md) | `PYTHONPATH=python/samples python -m trajectory_cli` |

### Commands (same shape across CLIs)

| Command | Purpose |
| --- | --- |
| `browse` (default) | Interactive: pick source → session → print summary |
| `list` | Table of sessions for one source |
| `show` | Normalize one `--path` or listing `--id` |

Shared flags:

| Flag | Meaning |
| --- | --- |
| `--source <name>` | `pi`, `claude-code`, `codex`, `openclaw`, `hermes`, `ahp`, `grok-build` (alias `grok`) |
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
cargo run -p trajectory-cli --manifest-path rust/Cargo.toml -- show \
  --source ahp \
  --path conformance/cases/ahp/tool-calls/input.json

# Python (unpublished sample; requires editable install or PYTHONPATH=python/src:python/samples)
PYTHONPATH=python/samples python -m trajectory_cli list --source pi
PYTHONPATH=python/samples python -m trajectory_cli show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl
PYTHONPATH=python/samples python -m trajectory_cli browse
```

**Notes**

- Empty or missing stores exit successfully with a clear message.
- Hermes listing in core returns empty (no SQLite dependency); export JSON and
  `show --path`.
- AHP listing is Phase 3; normalize Shape A snapshots with `show --path`.
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
python/        PyPI package, tests, unpublished conformance runner
docs/          architecture, authoring, contributing, publishing
tools/         release, packaging, and npm bootstrap helpers
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

### Python

```bash
python -m pip install -e './python[dev]'
python -m pytest python/tests -q
```

### Shared conformance

```bash
dotnet build dotnet/tests/Trajectory.Conformance/Trajectory.Conformance.csproj -c Release
python3 conformance/verify.py --repository-root . -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll

# Python tip suite (protocol v1 runner is monorepo-only, not a PyPI script):
python conformance/verify.py --repository-root . -- \
  env PYTHONPATH=python/src:python/tools python -m trajectory_conformance
```

See [conformance/README.md](conformance/README.md) for case authoring and all
runners. Python package docs (imports, filters, dual timestamps, formulas,
OTEL matrix, filtered argv): [`python/README.md`](python/README.md).

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
| [AHP ingest status](docs/ahp-ingest-status.md) | AHP Phase 0–1 vs deferred work |
| [AHP source design](docs/ahp-source-spec.md) | Agent Host Protocol ingest design |
| [Normative specs](contracts/spec/normalization.md) | Identity, timestamps, diagnostics |
| [Conformance](conformance/README.md) | Shared cases and runners |

## License

MIT — see [LICENSE](LICENSE).

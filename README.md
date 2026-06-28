# Trajectory

Trajectory turns coding-agent session transcripts into deterministic,
versioned trajectory formats for memory, replay, evaluation, search, training,
and observability pipelines.

It is one product with native ecosystem implementations governed by the same
wire contracts and conformance cases:

| Runtime | Package | Status |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | Implemented |
| TypeScript | `@hypabolic/trajectory` | Pi vertical path implemented; unpublished |
| Rust | `hypabolic-trajectory` | Planned |

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

Rust and TypeScript packages have not been published. The TypeScript Pi runtime
is a fresh Hypabolic implementation built from this repository's specifications
and conformance cases. It supports Pi normalization, all deterministic
projections, explicit-root Pi listing, and the private conformance protocol.
Claude Code and Codex remain planned for TypeScript. The pinned
`letta-ai/trajectory` package is used only as a black-box compatibility oracle.

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

ML1 established the shared foundation and ML2 adds the independent TypeScript
Pi vertical path. ML3 is next: a native Rust Pi vertical path governed by the
same contracts and shared cases.

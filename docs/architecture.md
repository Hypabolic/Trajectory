# Trajectory architecture (.NET runtime)

## Purpose

The .NET implementation of Trajectory is a high-performance, Native AOT-compatible C# library for turning native coding-agent session transcripts into a stable internal trajectory representation and projecting that representation into multiple output schemas.

It is a functional and behavioural port of [`letta-ai/trajectory`](https://github.com/letta-ai/trajectory), not a line-for-line translation. The Letta wire contracts remain first-class compatibility targets, while the internal design follows ports and adapters so that source decoding, normalization, identity, listing, and output projection can evolve independently.

Primary uses include:

- memory formation, reflection, dreaming, and continual learning;
- cross-harness experience aggregation;
- evaluation, search, replay, and training-data pipelines;
- feeding structured experience into Hypabolic systems, Context Compilers, Evidence Graphs, or other memory stores.

## Design principles

1. **Decode once, normalize centrally, project many times.** Source adapters decode source-native records. They do not own shared normalization policy or output formatting.
2. **The IR is richer than every compatibility schema.** Output adapters may omit or reshape information, but they must not force source adapters to discard it early.
3. **Wire formats are versioned products.** The internal C# model is not itself a public interchange contract.
4. **Determinism is part of correctness.** Identity, ordering, canonical JSON, hashes, truncation, diagnostics, and synthesized values are testable contracts.
5. **AOT is the default path.** Reflection, runtime assembly scanning, dynamic code generation, and reflection-based JSON serialization are excluded from the core path.
6. **Optional integrations stay optional.** Filesystem listing and SQLite checkpoint support sit behind ports and packages so the normalization core remains BCL-only.

## Target packages

| Package | Responsibility | Runtime dependencies |
| --- | --- | --- |
| `Hypabolic.Trajectory` | IR, normalization, built-in transcript adapters, Letta and Hypabolic projections, listing abstractions | BCL only |
| `Hypabolic.Trajectory.Sqlite` | Post-v1 Deep Agents / LangGraph checkpoint discovery and decoding | SQLite and checkpoint-codec dependencies allowed |
| `Hypabolic.Trajectory.Testing` | Optional fixture/parity helpers for adapter authors | Test-only dependencies allowed |

Target frameworks are `net8.0;net9.0;net10.0`. The core package must build with trimming and Native AOT analyzers enabled and must pass published AOT smoke tests.

## Processing architecture

```text
native transcript / checkpoint
            |
            v
+---------------------------+
| source adapter            |
| source-specific decoding  |
+-------------+-------------+
              |
              v
+---------------------------+
| decoded source session    |
| native identity + events  |
+-------------+-------------+
              |
              v
+---------------------------+
| normalization core        |
| linking, repair, bounds,   |
| timestamps, diagnostics   |
+-------------+-------------+
              |
              v
+---------------------------+
| Internal Trajectory IR    |
| normalized + provenance   |
+-------------+-------------+
              |
      +-------+--------+-------------------+
      |                |                   |
      v                v                   v
 Letta v1        Letta canonical     Hypabolic v1
 output adapter  output adapter      output adapter
```

### Why source adapters must decode rather than normalize

The current prototype has source adapters returning `TrajectoryIR` directly. That makes each adapter responsible for linking tool calls, assigning IDs, applying bounds, synthesizing timestamps, filtering, validation, and diagnostics. Behaviour will drift as more sources are added.

The target boundary is therefore:

```csharp
public interface ISourceAdapter
{
    TrajectorySource Source { get; }

    DecodedSession Decode(
        ReadOnlySpan<byte> transcriptUtf8,
        SourceContext? context,
        DiagnosticSink diagnostics);
}
```

`DecodedSession` is internal. It preserves source-native identity and ordering inputs, while `TrajectoryNormalizer` owns all cross-source policy and produces `TrajectoryIR`.

String overloads remain part of the public API, but UTF-8/span-oriented overloads are the preferred large-transcript path.

## Internal Trajectory IR

`TrajectoryIR` is the stable semantic centre of the library. It is not serialized directly as a public format.

```csharp
public sealed record TrajectoryIR
{
    public required TrajectorySource Source { get; init; }
    public required string SourceName { get; init; }
    public required string? GroupId { get; init; }
    public required IReadOnlyList<IRRecord> Records { get; init; }
    public required IReadOnlyList<TrajectoryDiagnostic> Diagnostics { get; init; }
    public required AppliedNormalizationConfig Config { get; init; }
}
```

Each IR record contains normalized content plus the provenance required to reproduce Letta canonical identity and the richer Hypabolic schema:

```csharp
public abstract record IRRecord
{
    public required string Id { get; init; }
    public required IRRecordKind Kind { get; init; }
    public required int Order { get; init; }
    public required DateTimeOffset? SourceTimestamp { get; init; }
    public required DateTimeOffset? Timestamp { get; init; }
    public required SourceRecordProvenance Provenance { get; init; }
}

public sealed record SourceRecordProvenance
{
    public string? NativeRecordId { get; init; }
    public long? SourceSequence { get; init; }
    public long? SourceOffset { get; init; }
    public SourceAnchorKind? SourceAnchorKind { get; init; }
    public required int ComponentIndex { get; init; }
    public required int ComponentTypeOrdinal { get; init; }
    public required string ComponentKey { get; init; }
}
```

The initial public record family is:

- `MetaIR` — source, working directory, git branch, model, producer version;
- `MessageIR` — user, reasoning, or assistant prose;
- `AssistantToolCallsIR` — assistant tool invocation records;
- `ToolResultIR` — linked tool results;
- `ToolCallIR` — tool-call identity, name, and canonical arguments JSON.

The built-in normalization core emits one semantic tool-call component per IR record even when a source event contains several calls. This gives every call an unambiguous component key and canonical identity. Output adapters may regroup compatible records when their target schema permits it.

## Normalization responsibilities

The shared normalization pipeline owns:

- source event validation and normalized transcript validation;
- tool-call ID synthesis and deterministic duplicate renaming;
- tool call/result linking independent of transport arrival order;
- partial transcript behaviour and cross-chunk orphan handling;
- removal of known harness noise and source-declared non-semantic records;
- argument reshaping into valid JSON objects;
- Unicode-code-point bounds for tool arguments and results;
- `head` and marker-inclusive `head-tail` truncation;
- tool-result inclusion/omission filters;
- timestamp preservation, interpolation, and deterministic synthesis;
- meta selection from source chronology rather than arrival order;
- typed, content-safe diagnostics;
- whole-transcript invariants requiring user and assistant records unless partial mode is active.

Default bounds and filters are resolved before normalization and the complete effective configuration is retained in the IR.

## Output schema adapters

A C# adapter must choose its output type; a method-level `TOutput Project(...)` cannot enforce that relationship. The target API uses a generic adapter plus a non-generic registry abstraction:

```csharp
public interface IOutputSchemaAdapter
{
    string SchemaId { get; }
    string SchemaVersion { get; }
    Type OutputType { get; }
}

public interface IOutputSchemaAdapter<TOutput> : IOutputSchemaAdapter
{
    TOutput Project(TrajectoryIR ir, OutputProjectionOptions? options = null);
    void Write(Utf8JsonWriter writer, TOutput output);
}
```

Built-in schemas for the first release:

| Schema ID | Contract |
| --- | --- |
| `letta-trajectory-v1` | Exact `letta-ai/trajectory` trajectory-v1 record array |
| `letta-canonical-v1` | Exact library-owned Letta canonical result and record fields |
| `hypabolic-trajectory-v1` | Loss-minimizing Hypabolic envelope described in `hypabolic-trajectory-v1.md` |
| `openai-chat-messages` | OpenAI-compatible message list |
| `jsonl-minimal` | Compact role/content/tool JSONL stream |

The engine exposes typed projection and direct UTF-8 JSON writing. A custom output schema can be registered explicitly through the builder; no assembly scanning or dynamic plugin loading is required.

## Public API direction

```csharp
var engine = TrajectoryEngine.CreateDefault();

TrajectoryIR ir = engine.NormalizeToIR(new NormalizeInput
{
    Source = TrajectorySource.Codex,
    Transcript = transcript,
    SourceContext = new SourceContext
    {
        GroupId = sessionId,
        BaseByteOffset = offset,
        Partial = true,
    },
});

LettaNormalizeResult letta = engine.Project<LettaNormalizeResult>(
    ir,
    OutputSchemaIds.LettaTrajectoryV1);

HypabolicTrajectoryV1 hypabolic = engine.Project<HypabolicTrajectoryV1>(
    ir,
    OutputSchemaIds.HypabolicTrajectoryV1);
```

Convenience methods mirror the upstream operations without making Letta the internal model:

- `NormalizeTranscript(...)`;
- `NormalizeToCanonical(...)`;
- `NormalizeToHypabolic(...)`;
- `NormalizeCheckpoint(...)` and `NormalizeCheckpointToCanonical(...)` in the SQLite package;
- `ListTrajectoriesAsync(...)`.

## Letta compatibility contracts

### `letta-trajectory-v1`

The serialized trajectory is a JSON array whose first record is `meta`, followed by role-specific records. It is not the custom `format` / `trajectory_id` / `messages` envelope currently emitted by the prototype.

Compatibility includes exact field names, nullability, omission rules, timestamp formatting, argument JSON strings, ordering, validation behaviour, and diagnostic shape.

### `letta-canonical-v1`

The canonical adapter preserves the upstream library-owned fields, including:

- `source_type`;
- `source_group_id`;
- `stable_source_record_id`;
- `source_identity_kind`;
- `source_order_id`;
- `component_index`;
- `record_type`;
- `record_id`, `record_hash`, and `content_hash`;
- source and normalized timestamps;
- flattened content/tool fields;
- lossless `record_json`;
- normalizer version, canonical schema version, resolved bounds, and filters.

Worker-owned tenancy, upload lineage, ingestion cursors, content versions, and storage indices remain outside this library.

## Determinism and identity

Canonical JSON recursively sorts object keys and omits undefined values. Hashing uses SHA-256 over UTF-8 bytes.

For identity-preserving projections:

```text
record_id = sha256(canonical_json([
  source_group_id,
  stable_source_record_id,
  component_key
]))
```

Source identity kind is one of:

- `native` — source-native record ID;
- `location` — byte, ordinal, row, or sequence anchor;
- `content` — explicit fallback when no stable native/location identity exists;
- `synthetic` — deterministic generated records such as meta.

`source_order_id` is based on source time, native sequence, and stable identity. It never depends on synthesized record timestamps or input arrival order.

Codex canonical normalization requires a resolved source group. Caller-provided and adapter-detected groups must agree. A non-zero base byte offset implies partial mode and is applied only to byte-anchored identities.

## Diagnostics

Diagnostics are typed and stable:

```csharp
public sealed record TrajectoryDiagnostic
{
    public required DiagnosticCode Code { get; init; }
    public required string Message { get; init; }
    public int? InputLine { get; init; }
    public int? RecordIndex { get; init; }
    public int? Count { get; init; }
}
```

Messages never include transcript prose, tool arguments, tool results, paths containing sensitive values, or arbitrary source JSON. Codes are additive and are never repurposed.

Fatal contract failures use `TrajectoryNormalizationException` with a typed error code. Recoverable cleanup remains in `Diagnostics`.

## Listing

Listing is a discovery capability beside normalization, not part of transcript parsing. `ListTrajectoriesAsync` returns source-native IDs and filesystem locators, newest first, with opaque cursor pagination.

A missing default store returns an empty page. Invalid input, an unavailable optional integration, or an unreadable configured store returns a typed error.

Each source implementation owns its default store discovery and metadata extraction while a common paginator owns cursor validation and page semantics.

## JSON and AOT constraints

- `System.Text.Json` source generation is mandatory for every built-in public contract.
- Built-in adapters use concrete generated contexts; reflection serialization is not a fallback.
- Canonical argument JSON and hash JSON use dedicated deterministic writers rather than general serializer defaults.
- Adapter registration is explicit through generated/default registry code or a fluent builder.
- No `Assembly.Load`, `Activator.CreateInstance`, expression compilation, runtime code generation, or dynamic type discovery is permitted in the core path.
- CI publishes and executes a Native AOT smoke application for every supported target framework where the SDK supports publishing.

## Recommended source layout

```text
dotnet/src/
  Hypabolic.Trajectory/
    Abstractions/
    Models/IR/
    Models/Outputs/
    Decoding/
    Normalization/
    Identity/
    Hashing/
    Validation/
    Listing/
    Adapters/Sources/
    Adapters/Outputs/
    Json/
    TrajectoryEngine.cs
  Hypabolic.Trajectory.Sqlite/
    DeepAgents/
    Json/
dotnet/tests/
  Hypabolic.Trajectory.Tests/
  Hypabolic.Trajectory.ParityTests/
  Hypabolic.Trajectory.AotSmoke/
dotnet/benchmarks/
  Hypabolic.Trajectory.Benchmarks/
```

## Non-goals

- runtime loading of arbitrary adapter assemblies;
- reproducing the upstream TypeScript implementation structure;
- cloud ingestion-worker responsibilities;
- making the internal IR a permanent public JSON wire format;
- requiring SQLite, Python, or third-party packages for transcript normalization.

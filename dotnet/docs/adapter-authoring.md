# Adapter authoring (.NET)

For the multi-runtime checklist (shared contracts, conformance, and TypeScript /
Rust sketches), see [docs/adapter-authoring.md](../../docs/adapter-authoring.md).

This page documents **.NET** extension seams. Both use explicit registration on
a `TrajectoryEngine`; no reflection, assembly scanning, or dynamic activation is
used.

## Custom source adapter

Implement `ITrajectorySourceAdapter` when a source is not part of the built-in
decoder set:

```csharp
public sealed class MySourceAdapter : ITrajectorySourceAdapter
{
    public TrajectorySource Source => TrajectorySource.OpenHands;

    public TrajectoryIR Normalize(NormalizeInput input)
    {
        // Decode the transcript and return a fully validated public IR.
        // Preserve native IDs, timestamps, provider/model values, invocation
        // timing, and usage. Do not infer missing telemetry fields.
        return BuildTrajectory(input);
    }
}

var engine = TrajectoryEngine.CreateDefault()
    .AddSourceAdapter(new MySourceAdapter());
```

Registration rejects duplicate source keys. A custom adapter owns its decode
and normalization policy; built-in adapters continue to use the shared internal
normalizer. Use a currently unregistered `TrajectorySource` member. A general
string source key is deferred until the source registry is versioned.

## Custom output adapter

Derive from `OutputSchemaAdapter<TOutput>`. Implement `Project` and `Serialize`;
override `Write` to stream directly without an intermediate string.

```csharp
public sealed class MyOutputAdapter : OutputSchemaAdapter<MyOutput>
{
    public override string SchemaId => "my-output-v1";
    public override string SchemaVersion => "1";

    public override MyOutput Project(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null) =>
        new() { /* deterministic mapping */ };

    public override string Serialize(
        MyOutput output,
        OutputProjectionOptions? options = null) =>
        /* canonical JSON */;

    public override void Write(
        Stream destination,
        MyOutput output,
        OutputProjectionOptions? options = null)
    {
        // Write UTF-8 directly.
    }
}
```

Register and use it through the typed or non-generic bridge:

```csharp
engine.AddOutputAdapter(new MyOutputAdapter());
MyOutput typed = engine.Project<MyOutput>(trajectory, "my-output-v1");
engine.ProjectToStream(trajectory, "my-output-v1", destination);
```

The typed bridge reports the adapter's actual output type when a caller requests
the wrong type. Schema IDs must be unique within one engine.

## Contract checklist

- Make projection side-effect free and deterministic.
- Use only source-native execution metadata.
- Keep diagnostics content-safe.
- Define truncation and privacy behavior as part of the schema contract.
- Use source-generated JSON metadata under Native AOT.
- Test repeated projection byte identity, direct streaming, type mismatch, and
  empty or partial inputs.

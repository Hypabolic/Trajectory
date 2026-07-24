namespace Trajectory;

/// <summary>Registry-backed normalization and schema projection pipeline.</summary>
public sealed class TrajectoryEngine
{
    private readonly Dictionary<TrajectorySource, ISourceAdapter> _sources = new();
    private readonly Dictionary<string, IOutputSchemaAdapter> _outputs =
        new(StringComparer.Ordinal);

    public TrajectoryEngine AddSourceAdapter(ISourceAdapter adapter)
    {
        ArgumentNullException.ThrowIfNull(adapter);
        if (!_sources.TryAdd(adapter.Source, adapter))
        {
            throw new InvalidOperationException(
                $"A source adapter for '{adapter.Source}' is already registered.");
        }

        return this;
    }

    public TrajectoryEngine AddOutputAdapter(IOutputSchemaAdapter adapter)
    {
        ArgumentNullException.ThrowIfNull(adapter);
        ArgumentException.ThrowIfNullOrWhiteSpace(adapter.SchemaId);
        if (!_outputs.TryAdd(adapter.SchemaId, adapter))
        {
            throw new InvalidOperationException(
                $"An output adapter for schema '{adapter.SchemaId}' is already registered.");
        }

        return this;
    }

    public TrajectoryIR NormalizeToIR(NormalizeInput input)
    {
        ArgumentNullException.ThrowIfNull(input);
        ArgumentNullException.ThrowIfNull(input.Transcript);
        if (!_sources.TryGetValue(input.Source, out var adapter))
        {
            throw new KeyNotFoundException(
                $"No source adapter is registered for '{input.Source}'.");
        }

        var options = input.Options ?? new NormalizationOptions();
        return adapter.Parse(
            input.Transcript,
            input.Context ?? options.SourceContext,
            options);
    }

    public TrajectoryIR NormalizeToIR(
        TrajectorySource source,
        string transcript,
        SourceContext? context = null,
        NormalizationOptions? options = null) =>
        NormalizeToIR(new NormalizeInput(source, transcript, context, options));

    public string Project(
        TrajectoryIR trajectory,
        string outputSchemaId,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        ArgumentException.ThrowIfNullOrWhiteSpace(outputSchemaId);
        if (!_outputs.TryGetValue(outputSchemaId, out var adapter))
        {
            throw new KeyNotFoundException(
                $"No output adapter is registered for schema '{outputSchemaId}'.");
        }

        return adapter.Project(trajectory, options);
    }

    public string Project(
        string outputSchemaId,
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null) =>
        Project(trajectory, outputSchemaId, options);

    public string Normalize(
        TrajectorySource source,
        string transcript,
        string outputSchemaId,
        SourceContext? context = null,
        NormalizationOptions? normalizationOptions = null,
        OutputProjectionOptions? projectionOptions = null) =>
        Normalize(
            new NormalizeInput(source, transcript, context, normalizationOptions),
            outputSchemaId,
            projectionOptions);

    public string Normalize(
        NormalizeInput input,
        string outputSchemaId,
        OutputProjectionOptions? projectionOptions = null)
    {
        var trajectory = NormalizeToIR(input);
        if (trajectory.HasErrors)
        {
            throw new TrajectoryNormalizationException(trajectory.Diagnostics);
        }

        return Project(trajectory, outputSchemaId, projectionOptions);
    }

    public TrajectoryEngine RegisterSource(ISourceAdapter adapter) =>
        AddSourceAdapter(adapter);

    public TrajectoryEngine RegisterOutput(IOutputSchemaAdapter adapter) =>
        AddOutputAdapter(adapter);
}

public sealed class TrajectoryNormalizationException : Exception
{
    public TrajectoryNormalizationException(IReadOnlyList<TrajectoryDiagnostic> diagnostics)
        : base("Trajectory normalization failed. Inspect Diagnostics for data-safe details.")
    {
        Diagnostics = diagnostics;
    }

    public IReadOnlyList<TrajectoryDiagnostic> Diagnostics { get; }
}

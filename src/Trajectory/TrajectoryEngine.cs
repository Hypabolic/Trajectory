using System.Text;
using Hypabolic.Trajectory.Adapters.ClaudeCode;
using Hypabolic.Trajectory.Adapters.Hypabolic;
using Hypabolic.Trajectory.Adapters.Letta;
using Hypabolic.Trajectory.Adapters.Pi;
using Hypabolic.Trajectory.Listing;
using Hypabolic.Trajectory.Normalization;

namespace Hypabolic.Trajectory;

public static class TrajectoryVersion
{
    public const string Current = "0.1.0";
}

public sealed class TrajectoryEngine
{
    private readonly Dictionary<TrajectorySource, ISourceAdapter> _sources = new();
    private readonly Dictionary<string, IOutputSchemaAdapter> _outputs = new(StringComparer.Ordinal);
    private readonly Dictionary<TrajectorySource, ITrajectoryLister> _listers = new();
    private readonly TrajectoryNormalizer _normalizer = new();

    public static TrajectoryEngine CreateDefault() => new TrajectoryEngine()
        .AddSource(new ClaudeCodeJsonlSourceAdapter())
        .AddSource(new PiJsonlSourceAdapter())
        .AddOutputAdapter(new LettaTrajectoryV1OutputAdapter())
        .AddOutputAdapter(new LettaCanonicalV1OutputAdapter())
        .AddOutputAdapter(new HypabolicTrajectoryV1OutputAdapter())
        .AddLister(new ClaudeCodeTrajectoryLister())
        .AddLister(new PiTrajectoryLister());

    public TrajectoryEngine AddOutputAdapter<TOutput>(IOutputSchemaAdapter<TOutput> adapter)
    {
        ArgumentNullException.ThrowIfNull(adapter);
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
        if (!_sources.TryGetValue(input.Source, out var sourceAdapter))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.UnknownSource,
                $"No source adapter is registered for '{input.Source}'.");
        }

        var utf8 = Encoding.UTF8.GetBytes(input.Transcript);
        var config = AppliedNormalizationConfig.Resolve(input.Options, input.SourceContext);
        var decoded = sourceAdapter.Decode(utf8);
        return _normalizer.Normalize(decoded, config);
    }

    public TOutput Project<TOutput>(
        TrajectoryIR trajectory,
        string schemaId,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        if (!_outputs.TryGetValue(schemaId, out var adapter))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.UnknownOutputSchema,
                $"No output adapter is registered for schema '{schemaId}'.");
        }

        if (adapter is not IOutputSchemaAdapter<TOutput> typed)
        {
            throw new InvalidOperationException(
                $"Schema '{schemaId}' produces {adapter.OutputType.FullName}, not {typeof(TOutput).FullName}.");
        }

        return typed.Project(trajectory, options);
    }

    public string ProjectJson(
        TrajectoryIR trajectory,
        string schemaId,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        if (!_outputs.TryGetValue(schemaId, out var adapter))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.UnknownOutputSchema,
                $"No output adapter is registered for schema '{schemaId}'.");
        }

        var output = adapter.ProjectUntyped(trajectory, options);
        return adapter.SerializeUntyped(output, options);
    }

    public LettaNormalizeResult NormalizeTranscript(NormalizeInput input)
    {
        var trajectory = NormalizeToIR(input);
        return Project<LettaNormalizeResult>(trajectory, OutputSchemaIds.LettaTrajectoryV1);
    }

    public HypabolicTrajectoryV1 NormalizeToHypabolic(NormalizeInput input)
    {
        var trajectory = NormalizeToIR(input);
        return Project<HypabolicTrajectoryV1>(trajectory, OutputSchemaIds.HypabolicTrajectoryV1);
    }

    public LettaCanonicalResult NormalizeToCanonical(NormalizeInput input)
    {
        var trajectory = NormalizeToIR(input);
        return Project<LettaCanonicalResult>(trajectory, OutputSchemaIds.LettaCanonicalV1);
    }

    public string NormalizeJson(
        NormalizeInput input,
        string schemaId,
        OutputProjectionOptions? options = null) =>
        ProjectJson(NormalizeToIR(input), schemaId, options);

    public ValueTask<TrajectoryListingPage> ListTrajectoriesAsync(
        ListTrajectoriesOptions options,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(options);
        cancellationToken.ThrowIfCancellationRequested();
        if (!_listers.TryGetValue(options.Source, out var lister))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.ListingUnavailable,
                $"No trajectory lister is registered for '{options.Source}'.");
        }

        var items = lister.List(options.Root);
        return ValueTask.FromResult(
            TrajectoryPagination.Paginate(items, options.Cursor, options.Limit));
    }

    private TrajectoryEngine AddSource(ISourceAdapter adapter)
    {
        _sources.Add(adapter.Source, adapter);
        return this;
    }

    private TrajectoryEngine AddLister(ITrajectoryLister lister)
    {
        _listers.Add(lister.Source, lister);
        return this;
    }
}

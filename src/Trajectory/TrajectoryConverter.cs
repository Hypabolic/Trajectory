using Trajectory.Adapters.Letta;
using Trajectory.Adapters.Pi;

namespace Trajectory;

/// <summary>Convenience entry point configured with the built-in adapters.</summary>
public static class TrajectoryConverter
{
    private static readonly Lazy<TrajectoryEngine> DefaultEngine = new(CreateDefaultEngine);

    public static TrajectoryEngine Default => DefaultEngine.Value;

    public static TrajectoryEngine CreateDefaultEngine() =>
        new TrajectoryEngine()
            .AddSourceAdapter(new PiJsonlSourceAdapter())
            .AddOutputAdapter(new LettaTrajectoryV1OutputAdapter());

    public static TrajectoryIR NormalizeToIR(
        string piJsonl,
        NormalizationOptions? options = null,
        SourceContext? context = null) =>
        Default.NormalizeToIR(new NormalizeInput(
            TrajectorySource.Pi,
            piJsonl,
            context,
            options));

    public static string Normalize(
        string piJsonl,
        NormalizationOptions? normalizationOptions = null,
        OutputProjectionOptions? projectionOptions = null) =>
        Default.Normalize(
            TrajectorySource.Pi,
            piJsonl,
            LettaTrajectoryV1OutputAdapter.AdapterName,
            normalizationOptions: normalizationOptions,
            projectionOptions: projectionOptions);
}

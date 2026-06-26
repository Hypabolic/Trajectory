using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory;

public static class TrajectoryConverter
{
    private static readonly Lazy<TrajectoryEngine> DefaultEngine =
        new(TrajectoryEngine.CreateDefault);

    public static TrajectoryEngine Default => DefaultEngine.Value;

    public static TrajectoryIR NormalizeToIR(
        string piTranscript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        Default.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Pi,
            Transcript = piTranscript,
            Options = options,
            SourceContext = sourceContext,
        });

    public static LettaNormalizeResult NormalizeTranscript(
        string piTranscript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        Default.NormalizeTranscript(new NormalizeInput
        {
            Source = TrajectorySource.Pi,
            Transcript = piTranscript,
            Options = options,
            SourceContext = sourceContext,
        });

    public static HypabolicTrajectoryV1 NormalizeToHypabolic(
        string piTranscript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        Default.NormalizeToHypabolic(new NormalizeInput
        {
            Source = TrajectorySource.Pi,
            Transcript = piTranscript,
            Options = options,
            SourceContext = sourceContext,
        });

    public static string NormalizeJson(
        string piTranscript,
        string schemaId = OutputSchemaIds.LettaTrajectoryV1,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null,
        OutputProjectionOptions? projectionOptions = null) =>
        Default.NormalizeJson(
            new NormalizeInput
            {
                Source = TrajectorySource.Pi,
                Transcript = piTranscript,
                Options = options,
                SourceContext = sourceContext,
            },
            schemaId,
            projectionOptions);

    public static ValueTask<TrajectoryListingPage> ListPiTrajectoriesAsync(
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        Default.ListTrajectoriesAsync(
            new ListTrajectoriesOptions
            {
                Source = TrajectorySource.Pi,
                Root = root,
                Cursor = cursor,
                Limit = limit,
            },
            cancellationToken);
}

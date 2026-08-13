using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory;

public static class TrajectoryConverter
{
    private static readonly Lazy<TrajectoryEngine> DefaultEngine =
        new(TrajectoryEngine.CreateDefault);

    public static TrajectoryEngine Default => DefaultEngine.Value;

    public static TrajectoryIR NormalizeToIR(
        TrajectorySource source,
        string transcript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        Default.NormalizeToIR(new NormalizeInput
        {
            Source = source,
            Transcript = transcript,
            Options = options,
            SourceContext = sourceContext,
        });

    public static TrajectoryIR NormalizeToIR(
        string piTranscript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        NormalizeToIR(
            TrajectorySource.Pi,
            piTranscript,
            options,
            sourceContext);

    public static LettaNormalizeResult NormalizeTranscript(
        TrajectorySource source,
        string transcript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        Default.NormalizeTranscript(new NormalizeInput
        {
            Source = source,
            Transcript = transcript,
            Options = options,
            SourceContext = sourceContext,
        });

    public static LettaNormalizeResult NormalizeTranscript(
        string piTranscript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        NormalizeTranscript(
            TrajectorySource.Pi,
            piTranscript,
            options,
            sourceContext);

    public static HypabolicTrajectoryV1 NormalizeToHypabolic(
        TrajectorySource source,
        string transcript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        Default.NormalizeToHypabolic(new NormalizeInput
        {
            Source = source,
            Transcript = transcript,
            Options = options,
            SourceContext = sourceContext,
        });

    public static HypabolicTrajectoryV1 NormalizeToHypabolic(
        string piTranscript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        NormalizeToHypabolic(
            TrajectorySource.Pi,
            piTranscript,
            options,
            sourceContext);

    public static LettaCanonicalResult NormalizeToCanonical(
        TrajectorySource source,
        string transcript,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        Default.NormalizeToCanonical(new NormalizeInput
        {
            Source = source,
            Transcript = transcript,
            Options = options,
            SourceContext = sourceContext,
        });

    public static LettaCanonicalResult NormalizeToCanonical(
        string piSessionJsonl,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null) =>
        NormalizeToCanonical(
            TrajectorySource.Pi,
            piSessionJsonl,
            options,
            sourceContext);

    public static string NormalizeJson(
        TrajectorySource source,
        string transcript,
        string schemaId = OutputSchemaIds.LettaTrajectoryV1,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null,
        OutputProjectionOptions? projectionOptions = null) =>
        Default.NormalizeJson(
            new NormalizeInput
            {
                Source = source,
                Transcript = transcript,
                Options = options,
                SourceContext = sourceContext,
            },
            schemaId,
            projectionOptions);

    public static string NormalizeJson(
        string piTranscript,
        string schemaId = OutputSchemaIds.LettaTrajectoryV1,
        NormalizeOptions? options = null,
        SourceContext? sourceContext = null,
        OutputProjectionOptions? projectionOptions = null) =>
        NormalizeJson(
            TrajectorySource.Pi,
            piTranscript,
            schemaId,
            options,
            sourceContext,
            projectionOptions);

    public static ValueTask<TrajectoryListingPage> ListTrajectoriesAsync(
        TrajectorySource source,
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        Default.ListTrajectoriesAsync(
            new ListTrajectoriesOptions
            {
                Source = source,
                Root = root,
                Cursor = cursor,
                Limit = limit,
            },
            cancellationToken);

    public static ValueTask<TrajectoryListingPage> ListPiTrajectoriesAsync(
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        ListTrajectoriesAsync(
            TrajectorySource.Pi,
            root,
            cursor,
            limit,
            cancellationToken);

    public static ValueTask<TrajectoryListingPage> ListClaudeCodeTrajectoriesAsync(
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        ListTrajectoriesAsync(
            TrajectorySource.ClaudeCode,
            root,
            cursor,
            limit,
            cancellationToken);

    public static ValueTask<TrajectoryListingPage> ListCodexTrajectoriesAsync(
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        ListTrajectoriesAsync(
            TrajectorySource.Codex,
            root,
            cursor,
            limit,
            cancellationToken);

    public static ValueTask<TrajectoryListingPage> ListHermesTrajectoriesAsync(
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        ListTrajectoriesAsync(
            TrajectorySource.Hermes,
            root,
            cursor,
            limit,
            cancellationToken);

    public static ValueTask<TrajectoryListingPage> ListGrokBuildTrajectoriesAsync(
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        ListTrajectoriesAsync(
            TrajectorySource.GrokBuild,
            root,
            cursor,
            limit,
            cancellationToken);

    public static ValueTask<TrajectoryListingPage> ListCursorTrajectoriesAsync(
        string? root = null,
        string? cursor = null,
        int limit = 50,
        CancellationToken cancellationToken = default) =>
        ListTrajectoriesAsync(TrajectorySource.Cursor, root, cursor, limit, cancellationToken);
}

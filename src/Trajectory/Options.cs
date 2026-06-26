namespace Hypabolic.Trajectory;

public sealed record SourceContext
{
    public string? GroupId { get; init; }
    public long? BaseByteOffset { get; init; }
    public bool Partial { get; init; }
}

public enum ToolResultTruncationStrategy
{
    Head = 0,
    HeadTail = 1,
}

public enum ToolResultPolicy
{
    Include = 0,
    Omit = 1,
}

public sealed record ToolArgumentBounds
{
    public int? MaxCharacters { get; init; } = 20_000;
}

public sealed record ToolResultBounds
{
    public int? MaxCharacters { get; init; } = 2_500;
    public ToolResultTruncationStrategy Strategy { get; init; } = ToolResultTruncationStrategy.HeadTail;
}

public sealed record NormalizationBounds
{
    public ToolArgumentBounds? ToolArguments { get; init; }
    public ToolResultBounds? ToolResults { get; init; }
}

public sealed record NormalizationFilters
{
    public ToolResultPolicy ToolResults { get; init; } = ToolResultPolicy.Include;
}

public sealed record NormalizeOptions
{
    public NormalizationBounds? Bounds { get; init; }
    public NormalizationFilters? Filters { get; init; }
    public SourceContext? SourceContext { get; init; }
}

public sealed record ResolvedNormalizationBounds
{
    public required ToolArgumentBounds ToolArguments { get; init; }
    public required ToolResultBounds ToolResults { get; init; }
}

public sealed record AppliedNormalizationConfig
{
    public const int DefaultToolArgumentCharacters = 20_000;
    public const int DefaultToolResultCharacters = 2_500;

    public required ResolvedNormalizationBounds Bounds { get; init; }
    public required NormalizationFilters Filters { get; init; }
    public required SourceContext SourceContext { get; init; }

    public static AppliedNormalizationConfig Resolve(NormalizeOptions? options, SourceContext? context)
    {
        options ??= new NormalizeOptions();
        var bounds = options.Bounds;
        var argumentBounds = bounds?.ToolArguments ?? new ToolArgumentBounds
        {
            MaxCharacters = DefaultToolArgumentCharacters,
        };
        var resultBounds = bounds?.ToolResults ?? new ToolResultBounds
        {
            MaxCharacters = DefaultToolResultCharacters,
            Strategy = ToolResultTruncationStrategy.HeadTail,
        };

        if (argumentBounds.MaxCharacters is <= 0)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "bounds.toolArguments.maxCharacters must be a positive integer or null.");
        }

        if (argumentBounds.MaxCharacters is 1)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "bounds.toolArguments.maxCharacters must be at least 2 so arguments can remain a JSON object.");
        }

        if (resultBounds.MaxCharacters is <= 0)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "bounds.toolResults.maxCharacters must be a positive integer or null.");
        }

        return new AppliedNormalizationConfig
        {
            Bounds = new ResolvedNormalizationBounds
            {
                ToolArguments = argumentBounds,
                ToolResults = resultBounds,
            },
            Filters = options.Filters ?? new NormalizationFilters(),
            SourceContext = context ?? options.SourceContext ?? new SourceContext(),
        };
    }
}

public sealed record NormalizeInput
{
    public required TrajectorySource Source { get; init; }
    public required string Transcript { get; init; }
    public SourceContext? SourceContext { get; init; }
    public NormalizeOptions? Options { get; init; }
}

public sealed record OutputProjectionOptions
{
    public bool WriteIndented { get; init; }
}

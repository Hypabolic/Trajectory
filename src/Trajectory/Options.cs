namespace Trajectory;

public enum TruncationMode
{
    None = 0,
    Head,
    Tail,
    HeadAndTail
}

public enum ToolResultTruncationStrategy
{
    Head = 0,
    HeadTail
}

public sealed record ToolArgumentBounds
{
    public int? MaxBytes { get; init; }
    public TruncationMode Truncation { get; init; } = TruncationMode.HeadAndTail;
}

public sealed record ToolResultBounds
{
    public int? MaxCharacters { get; init; }
    public ToolResultTruncationStrategy Strategy { get; init; } =
        ToolResultTruncationStrategy.HeadTail;
}

public sealed record NormalizationBounds
{
    public long? StartByteOffset { get; init; }
    public long? EndByteOffset { get; init; }
    public ToolArgumentBounds? ToolArguments { get; init; }
    public ToolResultBounds? ToolResults { get; init; }
}

public sealed record NormalizationFilters;

public sealed record NormalizationOptions
{
    public NormalizationBounds? Bounds { get; init; }
    public NormalizationFilters? Filters { get; init; }
    public SourceContext? SourceContext { get; init; }
    public bool Strict { get; init; }
}

public sealed record AppliedNormalizationConfig(
    NormalizationBounds? Bounds,
    NormalizationFilters? Filters,
    SourceContext? SourceContext,
    bool Strict);

public record OutputProjectionOptions
{
    public bool IncludeDiagnostics { get; init; }
    public bool IncludeTimestamps { get; init; } = true;
    public bool OmitToolResults { get; init; }
    public bool WriteIndented { get; init; }
    public bool AppendFinalNewline { get; init; } = true;
}

/// <summary>Compatibility name for the initial projection options type.</summary>
public sealed record ProjectionOptions : OutputProjectionOptions;

public sealed record NormalizeInput(
    TrajectorySource Source,
    string Transcript,
    SourceContext? Context = null,
    NormalizationOptions? Options = null);

using System.Text.Json.Serialization;

namespace Hypabolic.Trajectory;

public static class DiagnosticCodes
{
    public const string InvalidJsonLine = "invalid_json_line";
    public const string NonObjectJsonLine = "non_object_json_line";
    public const string OrphanToolResult = "orphan_tool_result";
    public const string TimestampsSynthesized = "timestamps_synthesized";
}

public sealed record TrajectoryDiagnostic
{
    [JsonPropertyName("code")]
    public required string Code { get; init; }

    [JsonPropertyName("message")]
    public required string Message { get; init; }

    [JsonPropertyName("inputLine")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? InputLine { get; init; }

    [JsonPropertyName("recordIndex")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? RecordIndex { get; init; }

    [JsonPropertyName("count")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? Count { get; init; }
}

public enum NormalizationErrorCode
{
    InvalidInput = 0,
    UnknownSource,
    UnknownOutputSchema,
    MissingUserRecords,
    MissingAssistantRecords,
    InvalidNormalizedTranscript,
    ListingUnavailable,
}

public sealed class TrajectoryNormalizationException : Exception
{
    public TrajectoryNormalizationException(NormalizationErrorCode code, string message)
        : base(message)
    {
        Code = code;
    }

    public NormalizationErrorCode Code { get; }
}

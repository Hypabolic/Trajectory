using System.Text.Json.Serialization;

namespace Hypabolic.Trajectory;

public static class DiagnosticCodes
{
    public const string InvalidJsonLine = "invalid_json_line";
    public const string NonObjectJsonLine = "non_object_json_line";
    public const string InjectedContextDropped = "injected_context_dropped";
    public const string NoiseRecordDropped = "noise_record_dropped";
    public const string SidechainRecordDropped = "sidechain_record_dropped";
    public const string UnknownSemanticRecord = "unknown_semantic_record";
    public const string UnknownContentBlock = "unknown_content_block";
    public const string ToolCallIdSynthesized = "tool_call_id_synthesized";
    public const string DuplicateToolCallId = "duplicate_tool_call_id";
    public const string OrphanToolResult = "orphan_tool_result";
    public const string DuplicateToolResult = "duplicate_tool_result";
    public const string UnknownToolName = "unknown_tool_name";
    public const string ToolArgumentsReshaped = "tool_arguments_reshaped";
    public const string ToolArgumentsTruncated = "tool_arguments_truncated";
    public const string ToolResultTruncated = "tool_result_truncated";
    public const string TimestampsSynthesized = "timestamps_synthesized";
    public const string TimestampsInterpolated = "timestamps_interpolated";

    // AHP source diagnostics (contracts/spec/sources/ahp.md).
    // Unsupported protocol versions are fatal invalid_input (diagnostics.md
    // fatal set); there is no separate unsupported_ahp_version code.
    public const string AhpVersionMissing = "ahp_version_missing";
    public const string AhpActiveTurnOmitted = "ahp_active_turn_omitted";
    public const string AhpUnknownMessageOrigin = "ahp_unknown_message_origin";
    public const string AhpInputRequestSkipped = "ahp_input_request_skipped";
    public const string AhpReasoningOmitted = "ahp_reasoning_omitted";
    public const string AhpSystemAsAssistant = "ahp_system_as_assistant";
    public const string AhpUnresolvedContentRef = "ahp_unresolved_content_ref";
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
    SourceGroupConflict,
    SourceGroupRequired,
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

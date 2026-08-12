namespace Hypabolic.Trajectory.Streaming;

/// <summary>
/// Content-safe stream diagnostic projection (H2).
/// IR/batch normalizer messages may embed source-native IDs; stream wire
/// diagnostics keep code + safe structural fields and a fixed catalog message.
/// </summary>
public static class StreamSafeDiagnostics
{
    private static readonly Dictionary<string, string> DiagMessages = new(StringComparer.Ordinal)
    {
        ["invalid_json_line"] = "Skipped invalid JSON line.",
        ["non_object_json_line"] = "Skipped non-object JSON line.",
        ["injected_context_dropped"] = "Dropped injected context content.",
        ["noise_record_dropped"] = "Dropped a noise record.",
        ["sidechain_record_dropped"] = "Dropped a sidechain record.",
        ["unknown_semantic_record"] = "Dropped an unknown semantic record.",
        ["unknown_content_block"] = "Dropped an unknown content block.",
        ["tool_call_id_synthesized"] = "Synthesized a tool-call ID.",
        ["duplicate_tool_call_id"] = "Renamed a duplicate tool-call ID.",
        ["orphan_tool_result"] = "Dropped a tool result without a preceding call.",
        ["duplicate_tool_result"] = "Dropped a duplicate tool result.",
        ["unknown_tool_name"] = "Substituted a default name for a missing tool name.",
        ["tool_arguments_reshaped"] = "Reshaped tool-call arguments into a JSON object.",
        ["tool_arguments_truncated"] = "Truncated tool-call arguments.",
        ["tool_result_truncated"] = "Truncated a tool result.",
        ["timestamps_synthesized"] = "Synthesized timestamps for normalized records.",
        ["timestamps_interpolated"] = "Interpolated timestamps for normalized records.",
        ["ahp_version_missing"] = "Snapshot lacks ahpProtocolVersion; assumed pinned 0.7.x.",
        ["ahp_active_turn_omitted"] = "Omitted incomplete activeTurn (snapshot whole-mode policy).",
        ["ahp_unknown_message_origin"] = "Dropped a message with an unknown origin kind.",
        ["ahp_input_request_skipped"] = "Skipped an inputRequest response part.",
        ["ahp_reasoning_omitted"] = "Omitted reasoning content.",
        ["ahp_system_as_assistant"] = "Mapped a system message origin to assistant.",
        ["ahp_unresolved_content_ref"] =
            "Dropped a resource response part without fetching content-by-reference.",
        ["ahp_unknown_action"] = "Ignored an unknown AHP action type.",
        ["ahp_foreign_channel"] = "Ignored an AHP action for a non-target channel.",
        ["image_content_dropped"] = "Dropped image content.",
        ["backend_tool_result_synthesized"] = "Synthesized a tool result for a backend tool call.",
        ["encrypted_reasoning_included"] = "Included encrypted reasoning content.",
        ["model_span_omitted"] =
            "Model span omitted because source-native timing or provider/model metadata is incomplete.",
        ["stream_buffer_limit"] = "Stream buffer limit exceeded.",
        ["stream_cursor_conflict"] = "Supplied stream cursor does not match stream state.",
        ["stream_source_reset"] = "Source material changed relative to the active stream.",
        ["stream_resync_required"] = "Stream requires resync.",
        ["stream_sequence_gap"] = "AHP action-log serverSeq gap requires snapshot resync.",
    };

    private static readonly Dictionary<string, string> ErrorMessages = new(StringComparer.Ordinal)
    {
        ["invalid_input"] = "Invalid stream input.",
        ["unknown_source"] = "Unknown or invalid stream source.",
        ["unknown_output_schema"] = "Unknown output schema.",
        ["missing_user_records"] = "Normalized transcript is missing user records.",
        ["missing_assistant_records"] = "Normalized transcript is missing assistant records.",
        ["invalid_normalized_transcript"] = "Normalized transcript is invalid.",
        ["listing_unavailable"] = "Listing is unavailable.",
        ["source_group_conflict"] = "Source group changed relative to the active stream.",
        ["source_group_required"] = "Source group is required.",
        ["stream_buffer_limit"] = "Stream buffer limit exceeded.",
        ["stream_cursor_conflict"] = "Supplied stream cursor does not match stream state.",
        ["stream_source_reset"] = "Source material changed relative to the active stream.",
        ["stream_resync_required"] = "Stream requires resync.",
        ["stream_sequence_gap"] = "AHP action-log serverSeq gap requires snapshot resync.",
    };

    private static readonly HashSet<string> AllowedFixedErrorMessages = new(StringComparer.Ordinal)
    {
        "Stream buffer limit exceeded.",
        "Stream buffer limits must be non-negative int64 values.",
        "Supplied stream cursor does not match stream state.",
        "Source group changed relative to the active stream.",
        "Source material is shorter than the committed cursor.",
        "Source material was compacted relative to the committed cursor.",
        "Source material was replaced relative to the committed cursor.",
        "Committed prefix hash does not match supplied material.",
        "Stream input kind is not supported for this source.",
        "AHP stream apply requires source ahp.",
        "Hermes export stream apply requires source hermes.",
        "Hermes export material is not valid session-export JSON.",
        "Stream is already finished.",
        "Unknown or invalid stream source.",
        "AHP action-log serverSeq gap requires snapshot resync.",
        "AHP action batch could not be parsed.",
        "AHP snapshot material is not valid Shape A JSON.",
        "AHP action batch serverSeq order must be strictly increasing.",
        "AHP action batch must not mix sequenced and unsequenced envelopes.",
        "AHP action batch must be JSONL envelopes or a JSON array.",
        "AHP action envelope is missing a valid serverSeq.",
        "reset input requires a StreamResetRequest.",
        "Stream material length exceeds non-negative int64 domain.",
        "Stream cursor serverSeq positions must be non-negative int64 values.",
    };

    public static string MessageForCode(string code, int? inputLine = null, int? count = null)
    {
        if (code == "invalid_json_line" && inputLine is int line1)
        {
            return $"Skipped invalid JSON on line {line1}.";
        }

        if (code == "non_object_json_line" && inputLine is int line2)
        {
            return $"Skipped non-object JSON on line {line2}.";
        }

        if (code == "sidechain_record_dropped" && inputLine is int line3)
        {
            return $"Dropped a sidechain record on line {line3}.";
        }

        if (code == "timestamps_synthesized" && count is int n1)
        {
            return $"Synthesized timestamps for {n1} normalized records.";
        }

        if (code == "timestamps_interpolated" && count is int n2)
        {
            return $"Interpolated timestamps for {n2} normalized records.";
        }

        return DiagMessages.TryGetValue(code, out var msg) ? msg : "Stream diagnostic.";
    }

    public static string ErrorMessage(string code, string? candidate = null)
    {
        if (candidate is not null && AllowedFixedErrorMessages.Contains(candidate))
        {
            return candidate;
        }

        return ErrorMessages.TryGetValue(code, out var msg) ? msg : "Stream error.";
    }

    public static StreamDiagnostic Project(
        string code,
        string? message = null,
        int? inputLine = null,
        int? recordIndex = null,
        int? count = null)
    {
        _ = message; // never forward raw normalizer text
        return new StreamDiagnostic
        {
            Code = code,
            Message = MessageForCode(code, inputLine, count),
            InputLine = inputLine,
            RecordIndex = recordIndex,
            Count = count,
        };
    }
}

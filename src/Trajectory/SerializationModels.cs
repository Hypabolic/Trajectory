using System.Text.Json.Serialization;

namespace Trajectory;

public sealed record LettaTrajectoryRecord(
    [property: JsonPropertyName("format")] string Format,
    [property: JsonPropertyName("trajectory_id")] string TrajectoryId,
    [property: JsonPropertyName("source")] string Source,
    [property: JsonPropertyName("messages")] IReadOnlyList<LettaMessage> Messages,
    [property: JsonPropertyName("diagnostics")] IReadOnlyList<TrajectoryDiagnostic>? Diagnostics = null);

public sealed record LettaMessage(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("sequence")] long Sequence,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("content")] string? Content,
    [property: JsonPropertyName("timestamp")] DateTimeOffset? Timestamp,
    [property: JsonPropertyName("tool_calls")] IReadOnlyList<LettaToolCall>? ToolCalls,
    [property: JsonPropertyName("tool_result")] LettaToolResult? ToolResult);

public sealed record LettaToolCall(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("arguments_json")] string ArgumentsJson);

public sealed record LettaToolResult(
    [property: JsonPropertyName("tool_call_id")] string ToolCallId,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("content")] string? Content,
    [property: JsonPropertyName("is_error")] bool IsError);

[JsonSourceGenerationOptions(
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    GenerationMode = JsonSourceGenerationMode.Default)]
[JsonSerializable(typeof(TrajectoryIR))]
[JsonSerializable(typeof(IRRecord))]
[JsonSerializable(typeof(MetaIR))]
[JsonSerializable(typeof(MessageIR))]
[JsonSerializable(typeof(AssistantToolCallsIR))]
[JsonSerializable(typeof(ToolResultIR))]
[JsonSerializable(typeof(ToolCallIR))]
[JsonSerializable(typeof(LettaTrajectoryRecord))]
[JsonSerializable(typeof(string))]
public partial class TrajectoryJsonContext : JsonSerializerContext;

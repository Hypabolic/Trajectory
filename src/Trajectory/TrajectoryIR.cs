using System.Text.Json.Serialization;

namespace Trajectory;

/// <summary>The versioned, source-neutral trajectory representation.</summary>
public sealed record TrajectoryIR(
    string Source,
    string? GroupId,
    IReadOnlyList<IRRecord> Records,
    IReadOnlyList<TrajectoryDiagnostic> Diagnostics,
    AppliedNormalizationConfig Config)
{
    public const string CurrentSchemaVersion = "trajectory-ir-v1";

    [JsonIgnore]
    public bool HasErrors => Diagnostics.Any(static d => d.Severity == DiagnosticSeverity.Error);

    public static implicit operator NormalizationResult(TrajectoryIR trajectory) =>
        new(trajectory, trajectory.Diagnostics);
}

/// <summary>The transcript source families recognized by the initial contract.</summary>
public enum TrajectorySource
{
    Pi = 0,
    ClaudeCode,
    Codex,
    LettaCode,
    OpenClaw,
    OpenHands,
    Hermes,
    DeepAgents
}

public sealed record SourceContext(
    string? GroupId = null,
    long? BaseByteOffset = null,
    bool Partial = false);

[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(MetaIR), "meta")]
[JsonDerivedType(typeof(MessageIR), "message")]
[JsonDerivedType(typeof(AssistantToolCallsIR), "assistant_tool_calls")]
[JsonDerivedType(typeof(ToolResultIR), "tool_result")]
public abstract record IRRecord(
    string Id,
    string Role,
    DateTimeOffset? Timestamp,
    int Order);

public sealed record MetaIR(
    string Id,
    DateTimeOffset? Timestamp,
    int Order,
    string SourceName,
    string? Cwd = null,
    string? GitBranch = null,
    string? Model = null)
    : IRRecord(Id, "meta", Timestamp, Order);

public record MessageIR(
    string Id,
    string Role,
    DateTimeOffset? Timestamp,
    int Order,
    string Content)
    : IRRecord(Id, Role, Timestamp, Order);

public sealed record AssistantToolCallsIR(
    string Id,
    DateTimeOffset? Timestamp,
    int Order,
    string Content,
    IReadOnlyList<ToolCallIR> ToolCalls)
    : MessageIR(Id, TrajectoryRoles.Assistant, Timestamp, Order, Content);

public sealed record ToolResultIR(
    string Id,
    DateTimeOffset? Timestamp,
    int Order,
    string ToolCallId,
    string Content,
    string? ToolName = null,
    bool IsError = false)
    : IRRecord(Id, TrajectoryRoles.Tool, Timestamp, Order);

public sealed record ToolCallIR(
    string Id,
    string Name,
    string ArgumentsJson);

public static class TrajectoryRoles
{
    public const string User = "user";
    public const string Assistant = "assistant";
    public const string System = "system";
    public const string Tool = "tool";
    public const string Meta = "meta";
    public const string Unknown = "unknown";
}

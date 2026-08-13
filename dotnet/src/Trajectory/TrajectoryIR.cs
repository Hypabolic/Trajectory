namespace Hypabolic.Trajectory;

public enum TrajectorySource
{
    Pi = 0,
    ClaudeCode,
    Codex,
    LettaCode,
    OpenClaw,
    OpenHands,
    Hermes,
    DeepAgents,
    Ahp,
    GrokBuild,
    Cursor,
}

public enum TrajectoryRole
{
    Meta = 0,
    User,
    Reasoning,
    Assistant,
    Tool,
}

public enum IRRecordKind
{
    Meta = 0,
    Message,
    AssistantToolCalls,
    ToolResult,
}

public enum SourceAnchorKind
{
    Byte = 0,
    Ordinal,
    Row,
    Sequence,
}

public enum SourceIdentityKind
{
    Native = 0,
    Location,
    Content,
    Synthetic,
}

public sealed record SourceRecordProvenance
{
    public required string StableSourceRecordId { get; init; }
    public required SourceIdentityKind SourceIdentityKind { get; init; }
    public required string SourceOrderId { get; init; }
    public required string ComponentKey { get; init; }
    public required int ComponentIndex { get; init; }
    public required int ComponentTypeOrdinal { get; init; }
    public string? ProducerVersion { get; init; }
    public string? NativeRecordId { get; init; }
    public long? SourceSequence { get; init; }
    public long? SourceOffset { get; init; }
    public SourceAnchorKind? SourceAnchorKind { get; init; }
}

public sealed record RecordHashes
{
    public required string ContentSha256 { get; init; }
    public required string RecordSha256 { get; init; }
}

public abstract record IRRecord
{
    public required string Id { get; init; }
    public required IRRecordKind Kind { get; init; }
    public required TrajectoryRole Role { get; init; }
    public required int Order { get; init; }
    public required DateTimeOffset? SourceTimestamp { get; init; }
    public required DateTimeOffset? Timestamp { get; init; }
    public required SourceRecordProvenance Provenance { get; init; }
    public required RecordHashes Hashes { get; init; }
}

public sealed record MetaIR : IRRecord
{
    public required string SourceName { get; init; }
    public string? Cwd { get; init; }
    public string? GitBranch { get; init; }
    public string? Model { get; init; }
    public string? ProducerVersion { get; init; }
}

public sealed record MessageIR : IRRecord
{
    public required string Content { get; init; }
}

public sealed record ToolCallIR
{
    public required string Id { get; init; }
    public required string Name { get; init; }
    public required string ArgumentsJson { get; init; }
}

public sealed record AssistantToolCallsIR : IRRecord
{
    public required IReadOnlyList<ToolCallIR> ToolCalls { get; init; }
}

public sealed record ToolResultIR : IRRecord
{
    public required string ToolCallId { get; init; }
    public required string Content { get; init; }
    public string? ToolName { get; init; }
    public bool IsError { get; init; }
}

public sealed record TrajectoryIR
{
    public required TrajectorySource Source { get; init; }
    public required string SourceName { get; init; }
    public required string GroupId { get; init; }
    public required bool SourceGroupResolved { get; init; }
    public string? ProducerVersion { get; init; }
    public required IReadOnlyList<IRRecord> Records { get; init; }
    public required IReadOnlyList<TrajectoryDiagnostic> Diagnostics { get; init; }
    public required TrajectoryExecutionIR Execution { get; init; }
    public required AppliedNormalizationConfig Config { get; init; }
}

public sealed record TrajectoryExecutionIR
{
    public required IReadOnlyList<ModelInvocationIR> ModelInvocations { get; init; }
    public IReadOnlyList<WorkflowInvocationIR> WorkflowInvocations { get; init; } = [];
}

public sealed record WorkflowInvocationIR
{
    public required string Id { get; init; }
    public string? Name { get; init; }
    public string? NativeRecordId { get; init; }
    public DateTimeOffset? StartedAt { get; init; }
    public DateTimeOffset? CompletedAt { get; init; }
}

public sealed record ModelInvocationIR
{
    public required string Id { get; init; }
    public string? NativeRecordId { get; init; }
    public long? SourceSequence { get; init; }
    public long? SourceOffset { get; init; }
    public string? Provider { get; init; }
    public string? ApiFamily { get; init; }
    public string? RequestedModel { get; init; }
    public string? ResponseModel { get; init; }
    public string? ResponseId { get; init; }
    public string? StopReason { get; init; }
    public string? ProducerVersion { get; init; }
    public ModelTokenUsageIR? Usage { get; init; }
    public DateTimeOffset? StartedAt { get; init; }
    public DateTimeOffset? FirstResponseAt { get; init; }
    public DateTimeOffset? CompletedAt { get; init; }
}

public sealed record ModelTokenUsageIR
{
    public long? InputTokens { get; init; }
    public long? OutputTokens { get; init; }
    public long? CacheReadTokens { get; init; }
    public long? CacheWriteTokens { get; init; }
    public long? TotalTokens { get; init; }
}

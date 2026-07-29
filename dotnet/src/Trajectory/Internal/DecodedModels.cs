namespace Hypabolic.Trajectory.Internal;

internal enum DecodedEventKind
{
    Message = 0,
    Reasoning,
    ToolCall,
    ToolResult,
}

internal sealed record DecodedEvent
{
    public required DecodedEventKind Kind { get; init; }
    public TrajectoryRole? Role { get; init; }
    public string? Content { get; init; }
    public string? ToolCallId { get; init; }
    public string? ToolName { get; init; }
    public string? ToolArgumentsJson { get; init; }
    public bool IsError { get; init; }
    public int? InputLine { get; init; }
    public DateTimeOffset? Timestamp { get; init; }
    public string? Model { get; init; }
    public string? ProducerVersion { get; init; }
    public string? NativeRecordId { get; init; }
    public long? SourceSequence { get; init; }
    public long? SourceOffset { get; init; }
    public SourceAnchorKind? SourceAnchorKind { get; init; }
    public required int ComponentIndex { get; init; }
}

internal sealed record DecodedSessionContext
{
    public required TrajectorySource Source { get; init; }
    public required string SourceName { get; init; }
    public string? SourceGroupId { get; init; }
    public string? Cwd { get; init; }
    public string? GitBranch { get; init; }
    public string? Model { get; init; }
    public string? ProducerVersion { get; init; }
    public DateTimeOffset? CreatedAt { get; init; }
}

internal sealed record DecodedSession
{
    public required DecodedSessionContext Context { get; init; }
    public required IReadOnlyList<DecodedEvent> Events { get; init; }
    public required IReadOnlyList<DecodedModelInvocation> ModelInvocations { get; init; }
    public required IReadOnlyList<TrajectoryDiagnostic> Diagnostics { get; init; }
}

internal sealed record DecodedModelInvocation
{
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
    public long? InputTokens { get; init; }
    public long? OutputTokens { get; init; }
    public long? CacheReadTokens { get; init; }
    public long? CacheWriteTokens { get; init; }
    public long? TotalTokens { get; init; }
    public DateTimeOffset? StartedAt { get; init; }
    public DateTimeOffset? FirstResponseAt { get; init; }
    public DateTimeOffset? CompletedAt { get; init; }
}

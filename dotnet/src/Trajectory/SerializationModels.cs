using System.Text.Json.Serialization;

namespace Hypabolic.Trajectory;

public abstract record LettaRecord
{
    public required string Role { get; init; }
}

public sealed record LettaMetaRecord : LettaRecord
{
    public required string Source { get; init; }
    public string? Cwd { get; init; }
    public string? GitBranch { get; init; }
    public string? Model { get; init; }
}

public sealed record LettaMessageRecord : LettaRecord
{
    public required string Content { get; init; }
    public required DateTimeOffset Timestamp { get; init; }
}

public sealed record LettaToolCall
{
    public required string Id { get; init; }
    public required string Name { get; init; }
    public required string Args { get; init; }
}

public sealed record LettaAssistantToolCallRecord : LettaRecord
{
    public required IReadOnlyList<LettaToolCall> ToolCalls { get; init; }
    public required DateTimeOffset Timestamp { get; init; }
}

public sealed record LettaToolResultRecord : LettaRecord
{
    public required string ToolCallId { get; init; }
    public required string Content { get; init; }
    public required DateTimeOffset Timestamp { get; init; }
}

public sealed record LettaNormalizeResult
{
    public required IReadOnlyList<LettaRecord> Records { get; init; }
    public required IReadOnlyList<TrajectoryDiagnostic> Diagnostics { get; init; }
}

public static class LettaCompatibilityVersion
{
    public const string Normalizer = "0.2.0";
    public const int CanonicalSchema = 1;
}

public sealed record LettaCanonicalResult
{
    public required IReadOnlyList<LettaCanonicalRecord> Records { get; init; }
    public required IReadOnlyList<TrajectoryDiagnostic> Diagnostics { get; init; }
    public required string NormalizerVersion { get; init; }
    public required int CanonicalSchemaVersion { get; init; }
    public required LettaCanonicalConfig Config { get; init; }
}

public sealed record LettaCanonicalConfig
{
    public required ResolvedNormalizationBounds Bounds { get; init; }
    public required NormalizationFilters Filters { get; init; }
}

public sealed record LettaCanonicalRecord
{
    public required string SourceType { get; init; }
    public required string SourceGroupId { get; init; }
    public required string StableSourceRecordId { get; init; }
    public required string SourceIdentityKind { get; init; }
    public required string SourceOrderId { get; init; }
    public required int ComponentIndex { get; init; }
    public required string RecordType { get; init; }
    public required string RecordId { get; init; }
    public required string RecordHash { get; init; }
    public required string ContentHash { get; init; }
    public DateTimeOffset? SourceTimestamp { get; init; }
    public DateTimeOffset? RecordTimestamp { get; init; }
    public string? Content { get; init; }
    public string? ToolCallId { get; init; }
    public string? ToolName { get; init; }
    public string? ToolArgumentsJson { get; init; }
    public string? ToolResultJson { get; init; }
    public required string RecordJson { get; init; }
}

public sealed record HypabolicTrajectoryV1
{
    public required string SchemaId { get; init; }
    public required int SchemaVersion { get; init; }
    public required string TrajectoryId { get; init; }
    public required HypabolicSourceV1 Source { get; init; }
    public required HypabolicSegmentV1 Segment { get; init; }
    public required HypabolicNormalizerV1 Normalizer { get; init; }
    public required HypabolicConfigV1 Config { get; init; }
    public required IReadOnlyList<HypabolicRecordV1> Records { get; init; }
    public required IReadOnlyList<TrajectoryDiagnostic> Diagnostics { get; init; }
}

public sealed record HypabolicSourceV1
{
    public required string Type { get; init; }
    public required string Name { get; init; }
    public required string GroupId { get; init; }
    public string? ProducerVersion { get; init; }
}

public sealed record HypabolicSegmentV1
{
    public required bool Partial { get; init; }
    public required long BaseByteOffset { get; init; }
}

public sealed record HypabolicNormalizerV1
{
    public required string Name { get; init; }
    public required string Version { get; init; }
}

public sealed record HypabolicConfigV1
{
    public required HypabolicBoundsV1 Bounds { get; init; }
    public required HypabolicFiltersV1 Filters { get; init; }
}

public sealed record HypabolicBoundsV1
{
    public required ToolArgumentBounds ToolArguments { get; init; }
    public required ToolResultBounds ToolResults { get; init; }
}

public sealed record HypabolicFiltersV1
{
    public required string ToolResults { get; init; }
}

public sealed record HypabolicRecordV1
{
    public required string Id { get; init; }
    public required string Kind { get; init; }
    public required string Role { get; init; }
    public required int Order { get; init; }
    public required DateTimeOffset? SourceTimestamp { get; init; }
    public required DateTimeOffset? Timestamp { get; init; }
    public string? SourceName { get; init; }
    public string? Cwd { get; init; }
    public string? GitBranch { get; init; }
    public string? Model { get; init; }
    public string? ProducerVersion { get; init; }
    public string? Content { get; init; }
    public IReadOnlyList<HypabolicToolCallV1>? ToolCalls { get; init; }
    public string? ToolCallId { get; init; }
    public string? ToolName { get; init; }
    public bool? IsError { get; init; }
    public required HypabolicProvenanceV1 Provenance { get; init; }
    public required HypabolicHashesV1 Hashes { get; init; }
}

public sealed record HypabolicToolCallV1
{
    public required string Id { get; init; }
    public required string Name { get; init; }
    public required string ArgumentsJson { get; init; }
}

public sealed record HypabolicProvenanceV1
{
    public required string StableSourceRecordId { get; init; }
    public required string SourceIdentityKind { get; init; }
    public required string SourceOrderId { get; init; }
    public required string ComponentKey { get; init; }
    public required int ComponentIndex { get; init; }
    public required int ComponentTypeOrdinal { get; init; }
    public string? ProducerVersion { get; init; }
    public string? NativeRecordId { get; init; }
    public long? SourceSequence { get; init; }
    public long? SourceOffset { get; init; }
    public string? SourceAnchorKind { get; init; }
}

public sealed record HypabolicHashesV1
{
    public required string ContentSha256 { get; init; }
    public required string RecordSha256 { get; init; }
}

[JsonSourceGenerationOptions(
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    GenerationMode = JsonSourceGenerationMode.Default)]
[JsonSerializable(typeof(SourceRecordProvenance))]
[JsonSerializable(typeof(TrajectoryDiagnostic))]
[JsonSerializable(typeof(IReadOnlyList<TrajectoryDiagnostic>))]
[JsonSerializable(typeof(HypabolicTrajectoryV1))]
[JsonSerializable(typeof(HypabolicRecordV1))]
[JsonSerializable(typeof(HypabolicRecordV1[]))]
[JsonSerializable(typeof(LettaNormalizeResult))]
[JsonSerializable(typeof(LettaMetaRecord))]
[JsonSerializable(typeof(LettaMessageRecord))]
[JsonSerializable(typeof(LettaAssistantToolCallRecord))]
[JsonSerializable(typeof(LettaToolResultRecord))]
[JsonSerializable(typeof(LettaToolCall))]
[JsonSerializable(typeof(LettaCanonicalResult))]
[JsonSerializable(typeof(LettaCanonicalRecord))]
[JsonSerializable(typeof(Adapters.OpenAi.OpenAiChatMessage))]
[JsonSerializable(typeof(Adapters.OpenAi.OpenAiChatMessage[]))]
[JsonSerializable(typeof(Adapters.Streaming.MinimalJsonlRecord))]
public partial class TrajectoryJsonContext : JsonSerializerContext;

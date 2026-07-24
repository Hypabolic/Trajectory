using System.Text.Json.Serialization;

namespace Hypabolic.Trajectory.OpenTelemetry;

public static class OtelGenAiConventions
{
    public const string Version = "1.42.0";
    public const string SchemaUrl = "https://opentelemetry.io/schemas/gen-ai/1.42.0";
    public const string InstrumentationScope = "Hypabolic.Trajectory.OpenTelemetry";
}

public sealed record OtelGenAiSpanSetV1
{
    public required string SchemaUrl { get; init; }
    public required string TraceId { get; init; }
    public required string InstrumentationScope { get; init; }
    public required string InstrumentationVersion { get; init; }
    public required IReadOnlyList<OtelGenAiSpanV1> Spans { get; init; }
    public required IReadOnlyList<OtelProjectionDiagnostic> Diagnostics { get; init; }
    public required OtelContentPolicy ContentPolicy { get; init; }
}

public sealed record OtelGenAiSpanV1
{
    public required string TraceId { get; init; }
    public required string SpanId { get; init; }
    public string? ParentSpanId { get; init; }
    public required string Name { get; init; }
    public required string Kind { get; init; }
    public required DateTimeOffset StartTime { get; init; }
    public required DateTimeOffset EndTime { get; init; }
    public required string Status { get; init; }
    public required IReadOnlyList<OtelAttributeV1> Attributes { get; init; }
    public required IReadOnlyList<OtelSpanLinkV1> Links { get; init; }
}

public sealed record OtelSpanLinkV1
{
    public required string TraceId { get; init; }
    public required string SpanId { get; init; }
}

public sealed record OtelAttributeV1
{
    public required string Key { get; init; }
    public string? StringValue { get; init; }
    public long? IntegerValue { get; init; }
    public IReadOnlyList<string>? StringValues { get; init; }
}

public sealed record OtelProjectionDiagnostic
{
    public required string Code { get; init; }
    public required string Message { get; init; }
    public string? RecordId { get; init; }
}

public sealed record OtelContentPolicy
{
    public required bool MessagesIncluded { get; init; }
    public required bool ToolArgumentsIncluded { get; init; }
    public required bool ToolResultsIncluded { get; init; }
    public required int MaximumCharacters { get; init; }
}

public interface IOtelContentRedactor
{
    string Redact(string content);
}

public sealed record OtelGenAiProjectionOptions
{
    public bool IncludeMessages { get; init; }
    public bool IncludeToolArguments { get; init; }
    public bool IncludeToolResults { get; init; }
    public int MaximumContentCharacters { get; init; } = 1_024;
    public IOtelContentRedactor? Redactor { get; init; }

    internal void Validate()
    {
        if (MaximumContentCharacters <= 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(MaximumContentCharacters),
                "The telemetry content bound must be positive.");
        }

        if ((IncludeMessages || IncludeToolArguments || IncludeToolResults) && Redactor is null)
        {
            throw new InvalidOperationException(
                "A redactor is required when OpenTelemetry content capture is enabled.");
        }
    }
}

[JsonSourceGenerationOptions(
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    GenerationMode = JsonSourceGenerationMode.Default)]
[JsonSerializable(typeof(OtelGenAiSpanSetV1))]
public partial class OtelGenAiJsonContext : JsonSerializerContext;

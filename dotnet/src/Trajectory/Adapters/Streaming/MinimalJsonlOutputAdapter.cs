using System.Buffers;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Hypabolic.Trajectory.Adapters.Streaming;

public sealed record MinimalJsonlProjection
{
    public required IReadOnlyList<MinimalJsonlRecord> Records { get; init; }
}

public sealed record MinimalJsonlRecord
{
    [JsonPropertyName("id")]
    public required string Id { get; init; }

    [JsonPropertyName("order")]
    public required int Order { get; init; }

    [JsonPropertyName("kind")]
    public required string Kind { get; init; }

    [JsonPropertyName("role")]
    public required string Role { get; init; }

    [JsonPropertyName("timestamp")]
    public DateTimeOffset? Timestamp { get; init; }

    [JsonPropertyName("content")]
    public string? Content { get; init; }

    [JsonPropertyName("tool_calls")]
    public IReadOnlyList<HypabolicToolCallV1>? ToolCalls { get; init; }

    [JsonPropertyName("tool_call_id")]
    public string? ToolCallId { get; init; }

    [JsonPropertyName("tool_name")]
    public string? ToolName { get; init; }

    [JsonPropertyName("is_error")]
    public bool? IsError { get; init; }
}

public sealed class MinimalJsonlOutputAdapter : OutputSchemaAdapter<MinimalJsonlProjection>
{
    private static readonly byte[] Newline = "\n"u8.ToArray();

    public override string SchemaId => OutputSchemaIds.JsonlMinimal;
    public override string SchemaVersion => "1";

    public override MinimalJsonlProjection Project(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        return new MinimalJsonlProjection
        {
            Records = trajectory.Records.Select(Map).ToArray(),
        };
    }

    public override string Serialize(
        MinimalJsonlProjection output,
        OutputProjectionOptions? options = null)
    {
        var buffer = new ArrayBufferWriter<byte>();
        WriteLines(buffer, output, options);
        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    public override void Write(
        Stream destination,
        MinimalJsonlProjection output,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(destination);
        foreach (var record in output.Records)
        {
            using (var writer = new Utf8JsonWriter(destination, WriterOptions(options)))
            {
                JsonSerializer.Serialize(writer, record, TrajectoryJsonContext.Default.MinimalJsonlRecord);
            }

            destination.Write(Newline);
        }
    }

    private static void WriteLines(
        IBufferWriter<byte> destination,
        MinimalJsonlProjection output,
        OutputProjectionOptions? options)
    {
        foreach (var record in output.Records)
        {
            using (var writer = new Utf8JsonWriter(destination, WriterOptions(options)))
            {
                JsonSerializer.Serialize(writer, record, TrajectoryJsonContext.Default.MinimalJsonlRecord);
            }

            destination.Write(Newline);
        }
    }

    private static MinimalJsonlRecord Map(IRRecord record) => record switch
    {
        MessageIR message => Base(record) with { Content = message.Content },
        AssistantToolCallsIR calls => Base(record) with
        {
            ToolCalls = calls.ToolCalls.Select(static call => new HypabolicToolCallV1
            {
                Id = call.Id,
                Name = call.Name,
                ArgumentsJson = call.ArgumentsJson,
            }).ToArray(),
        },
        ToolResultIR result => Base(record) with
        {
            ToolCallId = result.ToolCallId,
            ToolName = result.ToolName,
            Content = result.Content,
            IsError = result.IsError,
        },
        _ => Base(record),
    };

    private static MinimalJsonlRecord Base(IRRecord record) => new()
    {
        Id = record.Id,
        Order = record.Order,
        Kind = record.Kind.ToString().ToLowerInvariant(),
        Role = record.Role.ToString().ToLowerInvariant(),
        Timestamp = record.Timestamp,
    };

    private static JsonWriterOptions WriterOptions(OutputProjectionOptions? options) => new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        // JSONL remains one JSON value per physical line by contract.
        Indented = false,
    };
}

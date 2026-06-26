using System.Buffers;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace Hypabolic.Trajectory.Adapters.OpenAi;

public sealed record OpenAiChatProjection
{
    public required IReadOnlyList<OpenAiChatMessage> Messages { get; init; }
    public required OpenAiReasoningPolicy ReasoningPolicy { get; init; }
}

public enum OpenAiReasoningPolicy
{
    Omit = 0,
}

public sealed record OpenAiChatMessage
{
    public required string Role { get; init; }
    public string? Content { get; init; }
    public IReadOnlyList<OpenAiToolCall>? ToolCalls { get; init; }
    public string? ToolCallId { get; init; }
    public string? Name { get; init; }
}

public sealed record OpenAiToolCall
{
    public required string Id { get; init; }
    public required string Type { get; init; }
    public required OpenAiFunctionCall Function { get; init; }
}

public sealed record OpenAiFunctionCall
{
    public required string Name { get; init; }
    public required string Arguments { get; init; }
}

public sealed class OpenAiChatMessagesOutputAdapter : OutputSchemaAdapter<OpenAiChatProjection>
{
    public override string SchemaId => OutputSchemaIds.OpenAiChatMessages;
    public override string SchemaVersion => "1";

    public override OpenAiChatProjection Project(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        var messages = new List<OpenAiChatMessage>();
        foreach (var record in trajectory.Records)
        {
            switch (record)
            {
                case MessageIR { Role: TrajectoryRole.User } message:
                    messages.Add(new OpenAiChatMessage
                    {
                        Role = "user",
                        Content = message.Content,
                    });
                    break;
                case MessageIR { Role: TrajectoryRole.Assistant } message:
                    messages.Add(new OpenAiChatMessage
                    {
                        Role = "assistant",
                        Content = message.Content,
                    });
                    break;
                case AssistantToolCallsIR calls:
                    messages.Add(new OpenAiChatMessage
                    {
                        Role = "assistant",
                        ToolCalls = calls.ToolCalls.Select(static call => new OpenAiToolCall
                        {
                            Id = call.Id,
                            Type = "function",
                            Function = new OpenAiFunctionCall
                            {
                                Name = call.Name,
                                Arguments = call.ArgumentsJson,
                            },
                        }).ToArray(),
                    });
                    break;
                case ToolResultIR result:
                    messages.Add(new OpenAiChatMessage
                    {
                        Role = "tool",
                        Content = result.Content,
                        ToolCallId = result.ToolCallId,
                        Name = result.ToolName,
                    });
                    break;
            }
        }

        return new OpenAiChatProjection
        {
            Messages = messages,
            ReasoningPolicy = OpenAiReasoningPolicy.Omit,
        };
    }

    public override string Serialize(
        OpenAiChatProjection output,
        OutputProjectionOptions? options = null)
    {
        var buffer = new ArrayBufferWriter<byte>();
        WriteJson(buffer, output, options);
        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    public override void Write(
        Stream destination,
        OpenAiChatProjection output,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(destination);
        using var writer = new Utf8JsonWriter(destination, WriterOptions(options));
        JsonSerializer.Serialize(writer, output.Messages, TrajectoryJsonContext.Default.OpenAiChatMessageArray);
    }

    private static void WriteJson(
        IBufferWriter<byte> destination,
        OpenAiChatProjection output,
        OutputProjectionOptions? options)
    {
        using var writer = new Utf8JsonWriter(destination, WriterOptions(options));
        JsonSerializer.Serialize(writer, output.Messages, TrajectoryJsonContext.Default.OpenAiChatMessageArray);
    }

    private static JsonWriterOptions WriterOptions(OutputProjectionOptions? options) => new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        Indented = options?.WriteIndented ?? false,
    };
}

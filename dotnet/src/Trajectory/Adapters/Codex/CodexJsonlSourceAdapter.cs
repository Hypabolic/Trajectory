using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Adapters.Codex;

internal sealed class CodexJsonlSourceAdapter : ISourceAdapter
{
    private static readonly string[] InjectedPrefixes =
    [
        "<environment_context>",
        "<user_instructions>",
        "<permissions instructions>",
        "<turn_context>",
    ];

    public TrajectorySource Source => TrajectorySource.Codex;

    public DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8, SourceContext sourceContext)
    {
        _ = sourceContext;
        var diagnostics = new List<TrajectoryDiagnostic>();
        var events = new List<DecodedEvent>();
        string? cwd = null;
        string? gitBranch = null;
        string? model = null;
        string? producerVersion = null;
        string? sessionId = null;
        DateTimeOffset? createdAt = null;

        foreach (var line in ParseJsonLines(transcriptUtf8, diagnostics))
        {
            using var document = line.Document;
            var row = document.RootElement;
            var recordType = GetString(row, "type");
            var timestamp = ParseTimestamp(row, "timestamp");
            var payload = row.TryGetProperty("payload", out var payloadElement) &&
                payloadElement.ValueKind == JsonValueKind.Object
                    ? payloadElement
                    : default;
            var payloadType = GetString(payload, "type");

            void Emit(DecodedEvent decoded)
            {
                events.Add(decoded with
                {
                    ProducerVersion = producerVersion,
                    SourceOffset = line.ByteOffset,
                    SourceAnchorKind = SourceAnchorKind.Byte,
                    ComponentIndex = 0,
                });
            }

            if (recordType == "session_meta")
            {
                cwd ??= NonEmpty(GetString(payload, "cwd"));
                createdAt ??= ParseTimestamp(payload, "timestamp") ?? timestamp;
                if (gitBranch is null &&
                    payload.ValueKind == JsonValueKind.Object &&
                    payload.TryGetProperty("git", out var git) &&
                    git.ValueKind == JsonValueKind.Object)
                {
                    gitBranch = NonEmpty(GetString(git, "branch"));
                }

                sessionId ??= NonEmpty(GetString(payload, "id"));
                producerVersion ??= NonEmpty(
                    ReadScalarAsString(payload, "cli_version"));
                continue;
            }

            if (recordType == "turn_context")
            {
                cwd ??= NonEmpty(GetString(payload, "cwd"));
                model ??= NonEmpty(GetString(payload, "model"));
                continue;
            }

            if (recordType == "event_msg")
            {
                var reasoning = GetString(payload, "text");
                if (payloadType == "agent_reasoning" &&
                    !string.IsNullOrWhiteSpace(reasoning))
                {
                    Emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.Reasoning,
                        Role = TrajectoryRole.Reasoning,
                        Content = reasoning,
                        InputLine = line.Line,
                        Timestamp = timestamp,
                        Model = model,
                        ComponentIndex = 0,
                    });
                }

                continue;
            }

            if (recordType != "response_item")
            {
                continue;
            }

            if (payloadType == "message")
            {
                var role = GetString(payload, "role");
                var content = payload.TryGetProperty("content", out var contentElement)
                    ? BlocksText(contentElement)
                    : string.Empty;
                if (role == "user")
                {
                    var head = content.TrimStart();
                    if (InjectedPrefixes.Any(prefix =>
                        head.StartsWith(prefix, StringComparison.Ordinal)))
                    {
                        diagnostics.Add(new TrajectoryDiagnostic
                        {
                            Code = DiagnosticCodes.InjectedContextDropped,
                            Message = $"Dropped Codex system-injected user content on line {line.Line}.",
                            InputLine = line.Line,
                        });
                    }
                    else
                    {
                        Emit(MessageEvent(
                            TrajectoryRole.User,
                            content,
                            line.Line,
                            timestamp,
                            model));
                    }
                }
                else if (role == "assistant")
                {
                    Emit(MessageEvent(
                        TrajectoryRole.Assistant,
                        content,
                        line.Line,
                        timestamp,
                        model));
                }

                continue;
            }

            if (payloadType == "function_call")
            {
                Emit(ToolCallEvent(
                    GetString(payload, "call_id"),
                    GetString(payload, "name"),
                    NonEmpty(GetString(payload, "arguments")) ?? "{}",
                    line.Line,
                    timestamp,
                    model));
                continue;
            }

            if (payloadType == "custom_tool_call")
            {
                var input = payload.TryGetProperty("input", out var customInput)
                    ? customInput
                    : default;
                Emit(ToolCallEvent(
                    GetString(payload, "call_id"),
                    GetString(payload, "name"),
                    SerializeInputObject(input),
                    line.Line,
                    timestamp,
                    model));
                continue;
            }

            if (payloadType == "web_search_call")
            {
                Emit(ToolCallEvent(
                    GetString(payload, "call_id"),
                    "web_search",
                    SerializeFilteredObject(
                        payload,
                        "type",
                        "call_id",
                        "status"),
                    line.Line,
                    timestamp,
                    model));
                continue;
            }

            if (payloadType == "tool_search_call")
            {
                var arguments = payload.TryGetProperty(
                    "arguments",
                    out var argumentsElement)
                        ? argumentsElement
                        : default;
                Emit(ToolCallEvent(
                    GetString(payload, "call_id"),
                    "tool_search",
                    arguments.ValueKind == JsonValueKind.String &&
                        !string.IsNullOrEmpty(arguments.GetString())
                            ? arguments.GetString()!
                            : JsonString(arguments),
                    line.Line,
                    timestamp,
                    model));
                continue;
            }

            if (payloadType is "function_call_output" or
                "custom_tool_call_output" or
                "tool_search_output")
            {
                var content = payloadType == "tool_search_output"
                    ? SerializeTools(payload)
                    : payload.TryGetProperty("output", out var output)
                        ? OutputText(output)
                        : string.Empty;
                Emit(new DecodedEvent
                {
                    Kind = DecodedEventKind.ToolResult,
                    Role = TrajectoryRole.Tool,
                    ToolCallId = GetString(payload, "call_id"),
                    Content = content,
                    InputLine = line.Line,
                    Timestamp = timestamp,
                    Model = model,
                    ComponentIndex = 0,
                });
            }
        }

        return new DecodedSession
        {
            Context = new DecodedSessionContext
            {
                Source = TrajectorySource.Codex,
                SourceName = "codex",
                SourceGroupId = sessionId,
                Cwd = cwd,
                GitBranch = gitBranch,
                ProducerVersion = producerVersion ?? "unknown",
                CreatedAt = createdAt,
            },
            Events = events,
            ModelInvocations = [],
            Diagnostics = diagnostics,
        };
    }

    private static DecodedEvent MessageEvent(
        TrajectoryRole role,
        string content,
        int line,
        DateTimeOffset? timestamp,
        string? model) =>
        new()
        {
            Kind = DecodedEventKind.Message,
            Role = role,
            Content = content,
            InputLine = line,
            Timestamp = timestamp,
            Model = model,
            ComponentIndex = 0,
        };

    private static DecodedEvent ToolCallEvent(
        string? id,
        string? name,
        string arguments,
        int line,
        DateTimeOffset? timestamp,
        string? model) =>
        new()
        {
            Kind = DecodedEventKind.ToolCall,
            Role = TrajectoryRole.Assistant,
            ToolCallId = id,
            ToolName = name,
            ToolArgumentsJson = arguments,
            InputLine = line,
            Timestamp = timestamp,
            Model = model,
            ComponentIndex = 0,
        };

    private static string SerializeInputObject(JsonElement input)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = CreateWriter(buffer))
        {
            writer.WriteStartObject();
            writer.WritePropertyName("input");
            if (input.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
            {
                writer.WriteStringValue(string.Empty);
            }
            else
            {
                input.WriteTo(writer);
            }

            writer.WriteEndObject();
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static string SerializeFilteredObject(
        JsonElement element,
        params string[] excluded)
    {
        var excludedSet = excluded.ToHashSet(StringComparer.Ordinal);
        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = CreateWriter(buffer))
        {
            writer.WriteStartObject();
            foreach (var property in element.EnumerateObject())
            {
                if (excludedSet.Contains(property.Name))
                {
                    continue;
                }

                property.WriteTo(writer);
            }

            writer.WriteEndObject();
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static string SerializeTools(JsonElement payload)
    {
        if (!payload.TryGetProperty("tools", out var tools) ||
            tools.ValueKind == JsonValueKind.Null)
        {
            return "[]";
        }

        return CompactJson(tools);
    }

    private static string OutputText(JsonElement output) =>
        output.ValueKind switch
        {
            JsonValueKind.String => output.GetString() ?? string.Empty,
            JsonValueKind.Array => BlocksText(output) is { Length: > 0 } text
                ? text
                : CompactJson(output),
            JsonValueKind.Object => NonEmpty(GetString(output, "content")) ??
                CompactJson(output),
            JsonValueKind.Null or JsonValueKind.Undefined => string.Empty,
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Number => output.GetRawText(),
            _ => output.GetRawText(),
        };

    private static string BlocksText(JsonElement content)
    {
        if (content.ValueKind == JsonValueKind.String)
        {
            return content.GetString() ?? string.Empty;
        }

        if (content.ValueKind != JsonValueKind.Array)
        {
            return string.Empty;
        }

        var parts = new List<string>();
        foreach (var item in content.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            var type = GetString(item, "type");
            if (type is "text" or "input_text" or "output_text" ||
                type is null && item.TryGetProperty("text", out _))
            {
                var text = GetString(item, "text");
                if (!string.IsNullOrEmpty(text))
                {
                    parts.Add(text);
                }
            }
            else if (type == "image")
            {
                parts.Add("[image]");
            }
        }

        return string.Join("\n", parts);
    }

    private static string JsonString(JsonElement value) =>
        value.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null
            ? "{}"
            : CompactJson(value);

    private static string CompactJson(JsonElement element)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = CreateWriter(buffer))
        {
            element.WriteTo(writer);
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static Utf8JsonWriter CreateWriter(ArrayBufferWriter<byte> buffer) =>
        new(buffer, new JsonWriterOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            Indented = false,
        });

    private static IReadOnlyList<JsonLine> ParseJsonLines(
        ReadOnlyMemory<byte> transcript,
        List<TrajectoryDiagnostic> diagnostics)
    {
        var parsed = new List<JsonLine>();
        var bytes = transcript.Span;
        var offset = 0;
        var lineNumber = 1;
        while (offset <= bytes.Length)
        {
            var remaining = bytes[offset..];
            var newline = remaining.IndexOf((byte)'\n');
            var length = newline >= 0 ? newline : remaining.Length;
            var lineMemory = transcript.Slice(offset, length);
            var lineSpan = lineMemory.Span;
            if (!lineSpan.IsEmpty && lineSpan[^1] == (byte)'\r')
            {
                lineMemory = lineMemory[..^1];
                lineSpan = lineMemory.Span;
            }

            if (!IsWhitespace(lineSpan))
            {
                try
                {
                    var document = JsonDocument.Parse(lineMemory);
                    if (document.RootElement.ValueKind != JsonValueKind.Object)
                    {
                        document.Dispose();
                        diagnostics.Add(new TrajectoryDiagnostic
                        {
                            Code = DiagnosticCodes.NonObjectJsonLine,
                            Message = $"Skipped non-object JSON on line {lineNumber}.",
                            InputLine = lineNumber,
                        });
                    }
                    else
                    {
                        parsed.Add(new JsonLine(document, lineNumber, offset));
                    }
                }
                catch (JsonException)
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.InvalidJsonLine,
                        Message = $"Skipped invalid JSON on line {lineNumber}.",
                        InputLine = lineNumber,
                    });
                }
            }

            if (newline < 0)
            {
                break;
            }

            offset += length + 1;
            lineNumber++;
        }

        return parsed;
    }

    private static bool IsWhitespace(ReadOnlySpan<byte> value)
    {
        foreach (var item in value)
        {
            if (item != (byte)' ' &&
                item != (byte)'\t' &&
                item != (byte)'\r')
            {
                return false;
            }
        }

        return true;
    }

    private static string? NonEmpty(string? value) =>
        string.IsNullOrEmpty(value) ? null : value;

    private static string? GetString(JsonElement element, string propertyName) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(propertyName, out var property) &&
        property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;

    private static string? ReadScalarAsString(
        JsonElement element,
        string propertyName)
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(propertyName, out var property))
        {
            return null;
        }

        return property.ValueKind switch
        {
            JsonValueKind.String => property.GetString(),
            JsonValueKind.Number => property.GetRawText(),
            _ => null,
        };
    }

    private static DateTimeOffset? ParseTimestamp(
        JsonElement element,
        string propertyName)
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(propertyName, out var property))
        {
            return null;
        }

        if (property.ValueKind == JsonValueKind.Number &&
            property.TryGetInt64(out var milliseconds) &&
            milliseconds > 100_000_000_000L)
        {
            try
            {
                return DateTimeOffset.FromUnixTimeMilliseconds(milliseconds);
            }
            catch (ArgumentOutOfRangeException)
            {
                return null;
            }
        }

        if (property.ValueKind != JsonValueKind.String)
        {
            return null;
        }

        var text = property.GetString();
        return DateTimeOffset.TryParse(
            text,
            CultureInfo.InvariantCulture,
            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
            out var parsed)
                ? parsed
                : null;
    }

    private sealed record JsonLine(
        JsonDocument Document,
        int Line,
        long ByteOffset);
}

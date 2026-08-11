using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Adapters.OpenClaw;

internal sealed class OpenClawJsonlSourceAdapter : ISourceAdapter
{
    public TrajectorySource Source => TrajectorySource.OpenClaw;

    public DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8, SourceContext sourceContext)
    {
        var diagnostics = new List<TrajectoryDiagnostic>();
        var events = new List<DecodedEvent>();
        var modelInvocations = new List<DecodedModelInvocation>();
        string? cwd = null;
        string? sessionId = null;
        string? producerVersion = null;
        string? requestedProvider = null;
        string? requestedModel = null;
        DateTimeOffset? createdAt = null;
        var sawMessageRow = false;

        foreach (var line in ParseJsonLines(transcriptUtf8, diagnostics))
        {
            using var document = line.Document;
            var row = document.RootElement;
            var type = GetString(row, "type");
            if (type == "session")
            {
                cwd ??= GetString(row, "cwd");
                sessionId ??= GetString(row, "id");
                createdAt ??= ParseTimestamp(row, "timestamp");
                producerVersion ??= ReadScalarAsString(row, "version");
                continue;
            }

            if (type == "model_change")
            {
                requestedProvider = GetString(row, "provider");
                requestedModel = GetString(row, "modelId");
                continue;
            }

            if (type != "message" ||
                !row.TryGetProperty("message", out var message) ||
                message.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            sawMessageRow = true;
            var nativeRecordId = GetString(row, "id");
            var timestamp = ParseTimestamp(row, "timestamp") ?? ParseTimestamp(message, "timestamp");
            var rawModel = GetString(message, "model");
            // OpenClaw delivery-mirror placeholders keep assistant prose but must not
            // contribute model metadata (meta.model majority or invocation response model).
            var model = IsExcludedModel(rawModel) ? null : rawModel;
            var role = GetString(message, "role");
            var componentIndex = 0;

            void Emit(DecodedEvent decoded)
            {
                events.Add(decoded with
                {
                    NativeRecordId = nativeRecordId,
                    SourceSequence = line.Line - 1L,
                    SourceOffset = line.ByteOffset,
                    SourceAnchorKind = SourceAnchorKind.Byte,
                    ComponentIndex = componentIndex++,
                });
            }

            if (role == "user")
            {
                var content = ReadBlocksText(message, "content");
                if (!string.IsNullOrEmpty(content))
                {
                    Emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.Message,
                        Role = TrajectoryRole.User,
                        Content = content,
                        InputLine = line.Line,
                        Timestamp = timestamp,
                        ComponentIndex = 0,
                    });
                }

                continue;
            }

            if (role == "assistant")
            {
                JsonElement usage = default;
                var hasUsage = message.TryGetProperty("usage", out usage) &&
                    usage.ValueKind == JsonValueKind.Object;
                modelInvocations.Add(new DecodedModelInvocation
                {
                    NativeRecordId = nativeRecordId,
                    SourceSequence = line.Line - 1L,
                    SourceOffset = line.ByteOffset,
                    Provider = GetString(message, "provider") ?? requestedProvider,
                    ApiFamily = GetString(message, "api"),
                    RequestedModel = requestedModel,
                    ResponseModel = model,
                    ResponseId = GetString(message, "responseId"),
                    StopReason = GetString(message, "stopReason"),
                    InputTokens = hasUsage ? GetInt64(usage, "input") : null,
                    OutputTokens = hasUsage ? GetInt64(usage, "output") : null,
                    CacheReadTokens = hasUsage ? GetInt64(usage, "cacheRead") : null,
                    CacheWriteTokens = hasUsage ? GetInt64(usage, "cacheWrite") : null,
                    TotalTokens = hasUsage ? GetInt64(usage, "totalTokens") : null,
                    StartedAt = ParseTimestamp(message, "startTimestamp") ??
                        ParseTimestamp(message, "requestTimestamp"),
                    FirstResponseAt = ParseTimestamp(message, "firstResponseTimestamp"),
                    CompletedAt = ParseTimestamp(message, "timestamp") ?? timestamp,
                });

                if (message.TryGetProperty("content", out var contentElement) &&
                    contentElement.ValueKind == JsonValueKind.String)
                {
                    var content = contentElement.GetString();
                    if (!string.IsNullOrEmpty(content))
                    {
                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.Message,
                            Role = TrajectoryRole.Assistant,
                            Content = content,
                            InputLine = line.Line,
                            Timestamp = timestamp,
                            Model = model,
                            ComponentIndex = 0,
                        });
                    }

                    continue;
                }

                if (contentElement.ValueKind != JsonValueKind.Array)
                {
                    continue;
                }

                foreach (var part in contentElement.EnumerateArray())
                {
                    if (part.ValueKind != JsonValueKind.Object)
                    {
                        continue;
                    }

                    var partType = GetString(part, "type");
                    if (partType == "thinking")
                    {
                        var thinking = GetString(part, "thinking");
                        if (!string.IsNullOrEmpty(thinking))
                        {
                            Emit(new DecodedEvent
                            {
                                Kind = DecodedEventKind.Reasoning,
                                Role = TrajectoryRole.Reasoning,
                                Content = thinking,
                                InputLine = line.Line,
                                Timestamp = timestamp,
                                Model = model,
                                ComponentIndex = 0,
                            });
                        }
                    }
                    else if (partType == "text")
                    {
                        var text = GetString(part, "text");
                        if (!string.IsNullOrEmpty(text))
                        {
                            Emit(new DecodedEvent
                            {
                                Kind = DecodedEventKind.Message,
                                Role = TrajectoryRole.Assistant,
                                Content = text,
                                InputLine = line.Line,
                                Timestamp = timestamp,
                                Model = model,
                                ComponentIndex = 0,
                            });
                        }
                    }
                    else if (partType == "toolCall")
                    {
                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.ToolCall,
                            Role = TrajectoryRole.Assistant,
                            ToolCallId = GetString(part, "id"),
                            ToolName = GetString(part, "name"),
                            ToolArgumentsJson = part.TryGetProperty("arguments", out var arguments)
                                ? CompactJson(arguments)
                                : "{}",
                            InputLine = line.Line,
                            Timestamp = timestamp,
                            Model = model,
                            ComponentIndex = 0,
                        });
                    }
                }

                continue;
            }

            if (role is "toolResult" or "tool")
            {
                var content = ReadBlocksText(message, "content");
                var isError = GetBoolean(message, "isError");
                if (isError && !content.StartsWith("error", StringComparison.OrdinalIgnoreCase))
                {
                    content = $"Error: {content}";
                }

                Emit(new DecodedEvent
                {
                    Kind = DecodedEventKind.ToolResult,
                    Role = TrajectoryRole.Tool,
                    ToolCallId = GetString(message, "toolCallId"),
                    ToolName = GetString(message, "toolName"),
                    Content = content,
                    IsError = isError,
                    InputLine = line.Line,
                    Timestamp = timestamp,
                    ComponentIndex = 0,
                });
            }
        }

        if (!sawMessageRow && sessionId is null)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "OpenClaw transcript must be session JSONL containing a session header or message entries.");
        }

        return new DecodedSession
        {
            Context = new DecodedSessionContext
            {
                Source = TrajectorySource.OpenClaw,
                SourceName = "openclaw",
                SourceGroupId = sessionId,
                Cwd = cwd,
                ProducerVersion = producerVersion,
                CreatedAt = createdAt,
            },
            Events = events,
            ModelInvocations = modelInvocations,
            Diagnostics = diagnostics,
        };
    }

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
            if (item != (byte)' ' && item != (byte)'\t' && item != (byte)'\r')
            {
                return false;
            }
        }

        return true;
    }

    private static string? GetString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property) && property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;

    private static bool GetBoolean(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property) &&
            property.ValueKind == JsonValueKind.True;

    private static long? GetInt64(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property) &&
        property.ValueKind == JsonValueKind.Number &&
        property.TryGetInt64(out var value)
            ? value
            : null;

    private static string? ReadScalarAsString(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property))
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

    private static DateTimeOffset? ParseTimestamp(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property))
        {
            return null;
        }

        if (property.ValueKind == JsonValueKind.Number && property.TryGetInt64(out var milliseconds) &&
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

    private static string ReadBlocksText(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var content))
        {
            return string.Empty;
        }

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
            if (type is "text" or "input_text" or "output_text" or null)
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


    private static bool IsExcludedModel(string? model) =>
        string.Equals(model, "delivery-mirror", StringComparison.Ordinal);

    private static string CompactJson(JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.String)
        {
            return element.GetString() ?? "{}";
        }

        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = new Utf8JsonWriter(buffer, new JsonWriterOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            Indented = false,
        }))
        {
            element.WriteTo(writer);
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private sealed record JsonLine(JsonDocument Document, int Line, long ByteOffset);
}

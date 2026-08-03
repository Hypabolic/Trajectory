using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Adapters.ClaudeCode;

internal sealed class ClaudeCodeJsonlSourceAdapter : ISourceAdapter
{
    private static readonly HashSet<string> TransportTypes =
    [
        "progress",
        "queue-operation",
        "file-history-snapshot",
        "summary",
        "system",
        "pr-link",
        "last-prompt",
        "custom-title",
        "ai-title",
        "agent-name",
        "permission-mode",
        "attachment",
        "mode",
    ];

    public TrajectorySource Source => TrajectorySource.ClaudeCode;

    public DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8, SourceContext sourceContext)
    {
        _ = sourceContext;
        var diagnostics = new List<TrajectoryDiagnostic>();
        var events = new List<DecodedEvent>();
        var modelInvocations = new List<DecodedModelInvocation>();
        var sessionIds = new HashSet<string>(StringComparer.Ordinal);
        ContextCandidate? cwdCandidate = null;
        ContextCandidate? branchCandidate = null;
        ContextCandidate? versionCandidate = null;

        foreach (var line in ParseJsonLines(transcriptUtf8, diagnostics))
        {
            using var document = line.Document;
            var row = document.RootElement;
            var rowType = GetString(row, "type");
            if (GetBoolean(row, "isSidechain"))
            {
                diagnostics.Add(new TrajectoryDiagnostic
                {
                    Code = DiagnosticCodes.SidechainRecordDropped,
                    Message = $"Dropped a Claude Code sidechain record on line {line.Line}.",
                    InputLine = line.Line,
                });
                continue;
            }

            if (rowType is not null && TransportTypes.Contains(rowType))
            {
                continue;
            }

            var timestamp = ParseTimestamp(row, "timestamp");
            var nativeRecordId = GetString(row, "uuid");
            var contextTimestamp = timestamp?.ToUnixTimeMilliseconds() ?? long.MaxValue;
            var contextTie = nativeRecordId ?? $"@{line.ByteOffset}";
            cwdCandidate = Earlier(
                cwdCandidate,
                GetString(row, "cwd"),
                contextTimestamp,
                contextTie);
            branchCandidate = Earlier(
                branchCandidate,
                GetString(row, "gitBranch"),
                contextTimestamp,
                contextTie);
            var producerVersion = ReadScalarAsString(row, "version");
            versionCandidate = Earlier(
                versionCandidate,
                producerVersion,
                contextTimestamp,
                contextTie);

            var sessionId = GetString(row, "sessionId");
            if (!string.IsNullOrEmpty(sessionId))
            {
                sessionIds.Add(sessionId);
            }

            if (rowType is not ("user" or "assistant"))
            {
                if (!string.IsNullOrEmpty(rowType))
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.UnknownSemanticRecord,
                        Message = $"Skipped an unknown Claude Code semantic record on line {line.Line}.",
                        InputLine = line.Line,
                    });
                }

                continue;
            }

            if (!row.TryGetProperty("message", out var message) ||
                message.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            var model = GetString(message, "model");
            var content = message.TryGetProperty("content", out var contentElement)
                ? contentElement
                : default;
            var componentIndex = 0;

            void Emit(DecodedEvent decoded)
            {
                events.Add(decoded with
                {
                    ProducerVersion = producerVersion,
                    NativeRecordId = nativeRecordId,
                    SourceOffset = line.ByteOffset,
                    SourceAnchorKind = SourceAnchorKind.Byte,
                    ComponentIndex = componentIndex++,
                });
            }

            if (rowType == "user")
            {
                DecodeUserContent(
                    content,
                    line.Line,
                    timestamp,
                    diagnostics,
                    Emit);
                continue;
            }

            modelInvocations.Add(DecodeInvocation(
                message,
                nativeRecordId,
                producerVersion,
                line.ByteOffset,
                timestamp,
                model));

            if (content.ValueKind == JsonValueKind.String)
            {
                var text = content.GetString();
                if (!string.IsNullOrWhiteSpace(text))
                {
                    Emit(MessageEvent(
                        TrajectoryRole.Assistant,
                        text,
                        line.Line,
                        timestamp,
                        model));
                }

                continue;
            }

            if (content.ValueKind != JsonValueKind.Array)
            {
                continue;
            }

            foreach (var block in content.EnumerateArray())
            {
                if (block.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }

                var blockType = GetString(block, "type");
                switch (blockType)
                {
                    case "thinking":
                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.Reasoning,
                            Role = TrajectoryRole.Reasoning,
                            Content = GetString(block, "thinking") ?? string.Empty,
                            InputLine = line.Line,
                            Timestamp = timestamp,
                            Model = model,
                            ComponentIndex = 0,
                        });
                        break;
                    case "text":
                        Emit(MessageEvent(
                            TrajectoryRole.Assistant,
                            GetString(block, "text") ?? string.Empty,
                            line.Line,
                            timestamp,
                            model));
                        break;
                    case "tool_use":
                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.ToolCall,
                            Role = TrajectoryRole.Assistant,
                            ToolCallId = GetString(block, "id"),
                            ToolName = GetString(block, "name"),
                            ToolArgumentsJson = block.TryGetProperty("input", out var input)
                                ? CompactJson(input)
                                : "{}",
                            InputLine = line.Line,
                            Timestamp = timestamp,
                            Model = model,
                            ComponentIndex = 0,
                        });
                        break;
                    case "fallback":
                        break;
                    default:
                        diagnostics.Add(new TrajectoryDiagnostic
                        {
                            Code = DiagnosticCodes.UnknownContentBlock,
                            Message = $"Skipped an unknown Claude Code assistant content block on line {line.Line}.",
                            InputLine = line.Line,
                        });
                        break;
                }
            }
        }

        if (sessionIds.Count > 1)
        {
            var formatted = string.Join(
                ", ",
                sessionIds.Order(StringComparer.Ordinal).Select(Quote));
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.SourceGroupConflict,
                $"Claude Code transcript contains multiple session ids: {formatted}.");
        }

        return new DecodedSession
        {
            Context = new DecodedSessionContext
            {
                Source = TrajectorySource.ClaudeCode,
                SourceName = "claude-code",
                SourceGroupId = sessionIds.SingleOrDefault(),
                Cwd = cwdCandidate?.Value,
                GitBranch = branchCandidate?.Value,
                ProducerVersion = versionCandidate?.Value ?? "unknown",
            },
            Events = events,
            ModelInvocations = modelInvocations,
            Diagnostics = diagnostics,
        };
    }

    private static void DecodeUserContent(
        JsonElement content,
        int line,
        DateTimeOffset? timestamp,
        List<TrajectoryDiagnostic> diagnostics,
        Action<DecodedEvent> emit)
    {
        if (content.ValueKind == JsonValueKind.String)
        {
            emit(MessageEvent(
                TrajectoryRole.User,
                content.GetString() ?? string.Empty,
                line,
                timestamp));
            return;
        }

        if (content.ValueKind != JsonValueKind.Array)
        {
            return;
        }

        var textParts = new List<string>();
        foreach (var block in content.EnumerateArray())
        {
            if (block.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            var blockType = GetString(block, "type");
            switch (blockType)
            {
                case "tool_result":
                    emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.ToolResult,
                        Role = TrajectoryRole.Tool,
                        ToolCallId = GetString(block, "tool_use_id"),
                        Content = block.TryGetProperty("content", out var resultContent)
                            ? BlocksText(resultContent)
                            : string.Empty,
                        IsError = GetBoolean(block, "is_error"),
                        InputLine = line,
                        Timestamp = timestamp,
                        ComponentIndex = 0,
                    });
                    break;
                case "text":
                    var text = GetString(block, "text");
                    if (!string.IsNullOrEmpty(text))
                    {
                        textParts.Add(text);
                    }
                    break;
                case "image":
                    textParts.Add("[image]");
                    break;
                default:
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.UnknownContentBlock,
                        Message = $"Skipped an unknown Claude Code user content block on line {line}.",
                        InputLine = line,
                    });
                    break;
            }
        }

        if (textParts.Count > 0)
        {
            emit(MessageEvent(
                TrajectoryRole.User,
                string.Join("\n", textParts),
                line,
                timestamp));
        }
    }

    private static DecodedEvent MessageEvent(
        TrajectoryRole role,
        string content,
        int line,
        DateTimeOffset? timestamp,
        string? model = null) =>
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

    private static DecodedModelInvocation DecodeInvocation(
        JsonElement message,
        string? nativeRecordId,
        string? producerVersion,
        long byteOffset,
        DateTimeOffset? timestamp,
        string? model)
    {
        JsonElement usage = default;
        var hasUsage = message.TryGetProperty("usage", out usage) &&
            usage.ValueKind == JsonValueKind.Object;
        return new DecodedModelInvocation
        {
            NativeRecordId = nativeRecordId,
            SourceOffset = byteOffset,
            ResponseModel = model,
            ResponseId = GetString(message, "id"),
            StopReason = GetString(message, "stop_reason") ??
                GetString(message, "stopReason"),
            ProducerVersion = producerVersion,
            InputTokens = hasUsage
                ? GetInt64(usage, "input_tokens") ?? GetInt64(usage, "input")
                : null,
            OutputTokens = hasUsage
                ? GetInt64(usage, "output_tokens") ?? GetInt64(usage, "output")
                : null,
            CacheReadTokens = hasUsage
                ? GetInt64(usage, "cache_read_input_tokens") ??
                    GetInt64(usage, "cacheRead")
                : null,
            CacheWriteTokens = hasUsage
                ? GetInt64(usage, "cache_creation_input_tokens") ??
                    GetInt64(usage, "cacheWrite")
                : null,
            TotalTokens = hasUsage ? GetInt64(usage, "total_tokens") : null,
            CompletedAt = timestamp,
        };
    }

    private static ContextCandidate? Earlier(
        ContextCandidate? current,
        string? value,
        long timestamp,
        string tie)
    {
        if (string.IsNullOrEmpty(value))
        {
            return current;
        }

        var next = new ContextCandidate(value, timestamp, tie);
        if (current is null ||
            next.Timestamp < current.Timestamp ||
            (next.Timestamp == current.Timestamp &&
                StringComparer.Ordinal.Compare(next.Tie, current.Tie) < 0))
        {
            return next;
        }

        return current;
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
            if (item != (byte)' ' &&
                item != (byte)'\t' &&
                item != (byte)'\r')
            {
                return false;
            }
        }

        return true;
    }

    private static string? GetString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property) &&
        property.ValueKind == JsonValueKind.String
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

    private static string? ReadScalarAsString(
        JsonElement element,
        string propertyName)
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

    private static DateTimeOffset? ParseTimestamp(
        JsonElement element,
        string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property))
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

    private static string CompactJson(JsonElement element)
    {
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

    private static string Quote(string value) =>
        CanonicalJson.Serialize(JsonValue.Create(value));

    private sealed record JsonLine(
        JsonDocument Document,
        int Line,
        long ByteOffset);

    private sealed record ContextCandidate(
        string Value,
        long Timestamp,
        string Tie);
}

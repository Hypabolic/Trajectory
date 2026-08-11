using System.Buffers;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Adapters.GrokBuild;

/// <summary>
/// Decodes Grok Build <c>chat_history.jsonl</c> ConversationItem streams.
/// </summary>
internal sealed class GrokBuildJsonlSourceAdapter : ISourceAdapter
{
    public TrajectorySource Source => TrajectorySource.GrokBuild;

    public DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8, SourceContext sourceContext)
    {
        var diagnostics = new List<TrajectoryDiagnostic>();
        var events = new List<DecodedEvent>();
        var modelInvocations = new List<DecodedModelInvocation>();
        string? firstModel = null;
        var encryptedIncluded = 0;

        var lines = ParseJsonLines(transcriptUtf8, diagnostics);
        var toolResultLines = CollectToolResultLines(lines);

        foreach (var line in lines)
        {
            using var document = line.Document;
            var row = document.RootElement;
            var rowType = GetString(row, "type");
            // Empty / missing type is ignored (non-empty unknown types diagnose).
            if (string.IsNullOrEmpty(rowType))
            {
                continue;
            }

            var componentIndex = 0;

            void Emit(DecodedEvent decoded)
            {
                events.Add(decoded with
                {
                    SourceOffset = line.ByteOffset,
                    SourceAnchorKind = SourceAnchorKind.Byte,
                    ComponentIndex = componentIndex++,
                    InputLine = line.Line,
                });
            }

            switch (rowType)
            {
                case "system":
                    {
                        var content = GetString(row, "content") ?? string.Empty;
                        if (!string.IsNullOrWhiteSpace(content))
                        {
                            Emit(new DecodedEvent
                            {
                                Kind = DecodedEventKind.Message,
                                Role = TrajectoryRole.Meta,
                                Content = content,
                                ComponentIndex = 0,
                            });
                        }

                        break;
                    }
                case "user":
                    {
                        var synthetic = GetString(row, "synthetic_reason");
                        var (text, droppedImage) = JoinContentParts(row, "content");
                        if (droppedImage)
                        {
                            diagnostics.Add(new TrajectoryDiagnostic
                            {
                                Code = DiagnosticCodes.ImageContentDropped,
                                Message =
                                    $"Dropped image content on Grok Build user record on line {line.Line}.",
                                InputLine = line.Line,
                            });
                        }

                        if (string.IsNullOrWhiteSpace(text))
                        {
                            break;
                        }

                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.Message,
                            Role = string.IsNullOrEmpty(synthetic)
                                ? TrajectoryRole.User
                                : TrajectoryRole.Meta,
                            Content = text,
                            ComponentIndex = 0,
                        });
                        break;
                    }
                case "assistant":
                    {
                        var modelId = NonEmpty(GetString(row, "model_id"));
                        if (modelId is not null)
                        {
                            firstModel ??= modelId;
                            modelInvocations.Add(new DecodedModelInvocation
                            {
                                SourceOffset = line.ByteOffset,
                                ResponseModel = modelId,
                            });
                        }

                        var content = ContentAsText(row, "content");
                        if (!string.IsNullOrWhiteSpace(content))
                        {
                            Emit(new DecodedEvent
                            {
                                Kind = DecodedEventKind.Message,
                                Role = TrajectoryRole.Assistant,
                                Content = content,
                                Model = modelId,
                                ComponentIndex = 0,
                            });
                        }

                        if (row.TryGetProperty("tool_calls", out var toolCalls) &&
                            toolCalls.ValueKind == JsonValueKind.Array)
                        {
                            foreach (var call in toolCalls.EnumerateArray())
                            {
                                if (call.ValueKind != JsonValueKind.Object)
                                {
                                    continue;
                                }

                                Emit(new DecodedEvent
                                {
                                    Kind = DecodedEventKind.ToolCall,
                                    Role = TrajectoryRole.Assistant,
                                    ToolCallId = GetString(call, "id"),
                                    ToolName = GetString(call, "name"),
                                    ToolArgumentsJson = ReadArgumentsAsStored(call),
                                    Model = modelId,
                                    ComponentIndex = 0,
                                });
                            }
                        }

                        break;
                    }
                case "tool_result":
                    {
                        var callId = GetString(row, "tool_call_id");
                        var content = GetString(row, "content") ?? string.Empty;
                        if (row.TryGetProperty("images", out var images) &&
                            images.ValueKind == JsonValueKind.Array &&
                            images.GetArrayLength() > 0)
                        {
                            diagnostics.Add(new TrajectoryDiagnostic
                            {
                                Code = DiagnosticCodes.ImageContentDropped,
                                Message =
                                    $"Dropped image content on Grok Build tool result on line {line.Line}.",
                                InputLine = line.Line,
                            });
                        }

                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.ToolResult,
                            Role = TrajectoryRole.Tool,
                            ToolCallId = callId,
                            Content = content,
                            ComponentIndex = 0,
                        });
                        break;
                    }
                case "reasoning":
                    {
                        var summaryText = ReasoningSummaryText(row);
                        var encrypted = GetString(row, "encrypted_content");
                        var includeEncrypted = sourceContext.IncludeEncryptedReasoning &&
                            !string.IsNullOrEmpty(encrypted);
                        string? body = null;
                        if (!string.IsNullOrWhiteSpace(summaryText) && includeEncrypted)
                        {
                            body = summaryText +
                                "\n\n<encrypted_content>\n" +
                                encrypted +
                                "\n</encrypted_content>";
                            encryptedIncluded++;
                        }
                        else if (!string.IsNullOrWhiteSpace(summaryText))
                        {
                            body = summaryText;
                        }
                        else if (includeEncrypted)
                        {
                            body = "<encrypted_content>\n" +
                                encrypted +
                                "\n</encrypted_content>";
                            encryptedIncluded++;
                        }

                        if (string.IsNullOrWhiteSpace(body))
                        {
                            break;
                        }

                        var reasoningId = NonEmpty(GetString(row, "id"));
                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.Reasoning,
                            Role = TrajectoryRole.Reasoning,
                            Content = body,
                            NativeRecordId = reasoningId,
                            ComponentIndex = 0,
                        });
                        break;
                    }
                case "backend_tool_call":
                    {
                        if (!row.TryGetProperty("kind", out var kind) ||
                            kind.ValueKind != JsonValueKind.Object)
                        {
                            break;
                        }

                        var toolType = GetString(kind, "tool_type") ?? "unknown_tool";
                        var callId = GetString(kind, "id");
                        var args = BackendArguments(kind);
                        var status = GetString(kind, "status");

                        Emit(new DecodedEvent
                        {
                            Kind = DecodedEventKind.ToolCall,
                            Role = TrajectoryRole.Assistant,
                            ToolCallId = callId,
                            ToolName = toolType,
                            ToolArgumentsJson = args,
                            ComponentIndex = 0,
                        });

                        // Synthesize immediately so ordering matches transcript position.
                        // Spec: only when no *later* matching tool_result exists for this id.
                        var completed = status is null or "completed";
                        var hasLater = false;
                        if (!string.IsNullOrEmpty(callId) &&
                            toolResultLines.TryGetValue(callId, out var linesForId))
                        {
                            foreach (var resultLine in linesForId)
                            {
                                if (resultLine > line.Line)
                                {
                                    hasLater = true;
                                    break;
                                }
                            }
                        }

                        if (completed && !string.IsNullOrEmpty(callId) && !hasLater)
                        {
                            var summary = BackendResultSummary(toolType, kind);
                            Emit(new DecodedEvent
                            {
                                Kind = DecodedEventKind.ToolResult,
                                Role = TrajectoryRole.Tool,
                                ToolCallId = callId,
                                Content = summary,
                                ComponentIndex = 0,
                            });
                            diagnostics.Add(new TrajectoryDiagnostic
                            {
                                Code = DiagnosticCodes.BackendToolResultSynthesized,
                                // Content-safe: no source-native tool-call IDs.
                                Message = "Synthesized a tool result for a backend tool call.",
                                InputLine = line.Line,
                            });
                        }

                        break;
                    }
                default:
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.UnknownSemanticRecord,
                        Message =
                            $"Skipped an unknown Grok Build semantic record on line {line.Line}.",
                        InputLine = line.Line,
                    });
                    break;
            }
        }

        if (encryptedIncluded > 0)
        {
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.EncryptedReasoningIncluded,
                Message =
                    $"Included encrypted reasoning content for {encryptedIncluded} item(s).",
                Count = encryptedIncluded,
            });
        }

        return new DecodedSession
        {
            Context = new DecodedSessionContext
            {
                Source = TrajectorySource.GrokBuild,
                SourceName = "grok-build",
                Model = firstModel,
            },
            Events = events,
            ModelInvocations = modelInvocations,
            Diagnostics = diagnostics,
        };
    }

    private static Dictionary<string, List<int>> CollectToolResultLines(
        IReadOnlyList<JsonLine> lines)
    {
        var map = new Dictionary<string, List<int>>(StringComparer.Ordinal);
        foreach (var line in lines)
        {
            var row = line.Document.RootElement;
            if (GetString(row, "type") != "tool_result")
            {
                continue;
            }

            var callId = GetString(row, "tool_call_id");
            if (string.IsNullOrEmpty(callId))
            {
                continue;
            }

            if (!map.TryGetValue(callId, out var list))
            {
                list = [];
                map[callId] = list;
            }

            list.Add(line.Line);
        }

        return map;
    }

    private static string BackendArguments(JsonElement kind)
    {
        if (kind.TryGetProperty("action", out var action) &&
            action.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined))
        {
            var buffer = new ArrayBufferWriter<byte>();
            using (var writer = new Utf8JsonWriter(buffer, CompactWriterOptions()))
            {
                writer.WriteStartObject();
                writer.WritePropertyName("action");
                action.WriteTo(writer);
                writer.WriteEndObject();
            }

            return Encoding.UTF8.GetString(buffer.WrittenSpan);
        }

        var fields = new List<(string Name, JsonElement Value)>();
        foreach (var name in new[] { "query", "input", "code" })
        {
            if (kind.TryGetProperty(name, out var value) &&
                value.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined))
            {
                fields.Add((name, value));
            }
        }

        if (fields.Count == 0)
        {
            return "{}";
        }

        var fallback = new ArrayBufferWriter<byte>();
        using (var writer = new Utf8JsonWriter(fallback, CompactWriterOptions()))
        {
            writer.WriteStartObject();
            foreach (var (name, value) in fields)
            {
                writer.WritePropertyName(name);
                value.WriteTo(writer);
            }

            writer.WriteEndObject();
        }

        return Encoding.UTF8.GetString(fallback.WrittenSpan);
    }

    private static string BackendResultSummary(string toolType, JsonElement kind)
    {
        string? detail = null;
        if (kind.TryGetProperty("action", out var action) &&
            action.ValueKind == JsonValueKind.Object)
        {
            var actionType = GetString(action, "type") ?? "action";
            var query = GetString(action, "query") ??
                GetString(action, "input") ??
                GetString(action, "code");
            detail = query is null ? actionType : $"{actionType}: {query}";
        }
        else
        {
            detail = GetString(kind, "query") ??
                GetString(kind, "input") ??
                GetString(kind, "code");
        }

        return detail is null
            ? $"[backend {toolType}]"
            : $"[backend {toolType}] {detail}";
    }

    private static string ReasoningSummaryText(JsonElement row)
    {
        var parts = new List<string>();
        if (row.TryGetProperty("summary", out var summary) &&
            summary.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in summary.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }

                var type = GetString(item, "type");
                if (type is "summary_text" or "text" or null)
                {
                    var text = GetString(item, "text");
                    if (!string.IsNullOrEmpty(text))
                    {
                        parts.Add(text);
                    }
                }
            }
        }

        var content = ContentAsText(row, "content");
        if (!string.IsNullOrWhiteSpace(content))
        {
            parts.Add(content);
        }

        return string.Join("\n", parts);
    }

    private static (string Text, bool DroppedImage) JoinContentParts(
        JsonElement row,
        string propertyName)
    {
        if (!row.TryGetProperty(propertyName, out var content))
        {
            return (string.Empty, false);
        }

        if (content.ValueKind == JsonValueKind.String)
        {
            return (content.GetString() ?? string.Empty, false);
        }

        if (content.ValueKind != JsonValueKind.Array)
        {
            return (string.Empty, false);
        }

        var parts = new List<string>();
        var droppedImage = false;
        foreach (var part in content.EnumerateArray())
        {
            if (part.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            var type = GetString(part, "type");
            if (type is "text" or "input_text" or "output_text" or null)
            {
                var text = GetString(part, "text");
                if (!string.IsNullOrEmpty(text))
                {
                    parts.Add(text);
                }
            }
            else if (type == "image")
            {
                droppedImage = true;
            }
        }

        return (string.Join("\n", parts), droppedImage);
    }

    private static string ContentAsText(JsonElement row, string propertyName)
    {
        if (!row.TryGetProperty(propertyName, out var content))
        {
            return string.Empty;
        }

        return content.ValueKind switch
        {
            JsonValueKind.String => content.GetString() ?? string.Empty,
            JsonValueKind.Array => JoinContentParts(row, propertyName).Text,
            _ => string.Empty,
        };
    }

    private static string ReadArgumentsAsStored(JsonElement call)
    {
        if (!call.TryGetProperty("arguments", out var arguments))
        {
            return "{}";
        }

        if (arguments.ValueKind == JsonValueKind.String)
        {
            return arguments.GetString() ?? "{}";
        }

        return CompactJson(arguments);
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
            if (item is not ((byte)' ' or (byte)'\t' or (byte)'\r'))
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

    private static string? NonEmpty(string? value) =>
        string.IsNullOrEmpty(value) ? null : value;

    private static string CompactJson(JsonElement element)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = new Utf8JsonWriter(buffer, CompactWriterOptions()))
        {
            element.WriteTo(writer);
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static JsonWriterOptions CompactWriterOptions() => new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        Indented = false,
    };

    private static string Quote(string value) =>
        CanonicalJson.Serialize(JsonValue.Create(value));

    private sealed record JsonLine(JsonDocument Document, int Line, long ByteOffset);
}

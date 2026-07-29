using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Adapters.Ahp;

/// <summary>
/// Decodes Agent Host Protocol Shape A snapshot exports
/// (<c>{ ahpProtocolVersion?, chat, session? }</c>).
/// One chat per normalize; GroupId = chat URI.
/// </summary>
internal sealed class AhpJsonSourceAdapter : ISourceAdapter
{
    private static readonly UTF8Encoding Utf8 = new(encoderShouldEmitUTF8Identifier: false);

    public TrajectorySource Source => TrajectorySource.Ahp;

    public DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8)
    {
        var diagnostics = new List<TrajectoryDiagnostic>();
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(transcriptUtf8);
        }
        catch (JsonException)
        {
            throw InvalidAhpSnapshot();
        }

        using (document)
        {
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                throw InvalidAhpSnapshot();
            }

            ValidateProtocolVersion(root, diagnostics);

            if (!root.TryGetProperty("chat", out var chat) ||
                chat.ValueKind != JsonValueKind.Object)
            {
                throw InvalidAhpSnapshot();
            }

            JsonElement? session = null;
            if (root.TryGetProperty("session", out var sessionElement) &&
                sessionElement.ValueKind == JsonValueKind.Object)
            {
                session = sessionElement;
            }

            return DecodeChat(chat, session, diagnostics);
        }
    }

    private static DecodedSession DecodeChat(
        JsonElement chat,
        JsonElement? session,
        List<TrajectoryDiagnostic> diagnostics)
    {
        var events = new List<DecodedEvent>();
        var modelInvocations = new List<DecodedModelInvocation>();
        var groupId = NonEmpty(GetString(chat, "resource"));
        var cwd = FirstWorkingDirectoryPath(chat, session);
        string? model = null;
        DateTimeOffset? createdAt = null;

        var turns = CollectTurns(chat, diagnostics);
        foreach (var turn in turns)
        {
            EmitTurn(turn, events, modelInvocations, ref model, ref createdAt, diagnostics);
        }

        if (session is { } sessionElement)
        {
            // Session provider is provenance only; model stays from chat usage/message.
            _ = GetString(sessionElement, "provider");
        }

        return new DecodedSession
        {
            Context = new DecodedSessionContext
            {
                Source = TrajectorySource.Ahp,
                SourceName = "ahp",
                SourceGroupId = groupId,
                Cwd = cwd,
                Model = model,
                CreatedAt = createdAt,
            },
            Events = events,
            ModelInvocations = modelInvocations,
            Diagnostics = diagnostics,
        };
    }

    private static List<JsonElement> CollectTurns(
        JsonElement chat,
        List<TrajectoryDiagnostic> diagnostics)
    {
        var turns = new List<(JsonElement Element, DateTimeOffset? StartedAt, string Id)>();
        if (chat.TryGetProperty("turns", out var turnsElement) &&
            turnsElement.ValueKind == JsonValueKind.Array)
        {
            foreach (var turn in turnsElement.EnumerateArray())
            {
                if (turn.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }

                var id = GetString(turn, "id") ?? string.Empty;
                var startedAt = ParseTimestamp(GetProperty(turn, "startedAt"));
                turns.Add((turn.Clone(), startedAt, id));
            }
        }

        turns.Sort(static (left, right) =>
        {
            var byTime = Nullable.Compare(left.StartedAt, right.StartedAt);
            if (byTime != 0)
            {
                return byTime;
            }

            var byId = CompareUtf8(left.Id, right.Id);
            return byId != 0 ? byId : 0;
        });

        // Shape A decode has no partial flag on ISourceAdapter. Phase 1 drops
        // incomplete activeTurn with a non-fatal diagnostic (whole-mode policy).
        // Partial/activeTurn streaming is deferred with Shape B.
        if (chat.TryGetProperty("activeTurn", out var activeTurn) &&
            activeTurn.ValueKind == JsonValueKind.Object)
        {
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.AhpActiveTurnOmitted,
                Message = "Omitted incomplete activeTurn (snapshot whole-mode policy).",
            });
        }

        return turns.Select(static item => item.Element).ToList();
    }

    private static void EmitTurn(
        JsonElement turn,
        List<DecodedEvent> events,
        List<DecodedModelInvocation> modelInvocations,
        ref string? model,
        ref DateTimeOffset? createdAt,
        List<TrajectoryDiagnostic> diagnostics)
    {
        var turnId = GetString(turn, "id");
        var timestamp = ParseTimestamp(GetProperty(turn, "startedAt"));
        if (createdAt is null && timestamp is not null)
        {
            createdAt = timestamp;
        }

        var componentIndex = 0;

        void Emit(DecodedEvent decoded)
        {
            events.Add(decoded with { ComponentIndex = componentIndex++ });
        }

        if (turn.TryGetProperty("message", out var message) &&
            message.ValueKind == JsonValueKind.Object)
        {
            EmitMessage(message, turnId, timestamp, Emit, diagnostics, ref model);
        }

        if (turn.TryGetProperty("responseParts", out var parts) &&
            parts.ValueKind == JsonValueKind.Array)
        {
            EmitResponseParts(parts, turnId, timestamp, Emit, diagnostics);
        }

        if (turn.TryGetProperty("usage", out var usage) &&
            usage.ValueKind == JsonValueKind.Object)
        {
            var usageModel = NonEmpty(GetString(usage, "model"));
            if (usageModel is not null)
            {
                model ??= usageModel;
            }

            modelInvocations.Add(new DecodedModelInvocation
            {
                NativeRecordId = turnId,
                RequestedModel = usageModel ?? model,
                ResponseModel = usageModel ?? model,
                InputTokens = GetInt64(usage, "inputTokens"),
                OutputTokens = GetInt64(usage, "outputTokens"),
                CacheReadTokens = GetInt64(usage, "cacheReadTokens"),
                StartedAt = timestamp,
                CompletedAt = timestamp,
            });
        }
    }

    private static void EmitMessage(
        JsonElement message,
        string? turnId,
        DateTimeOffset? timestamp,
        Action<DecodedEvent> emit,
        List<TrajectoryDiagnostic> diagnostics,
        ref string? model)
    {
        var originKind = OriginKind(message);
        if (originKind is null)
        {
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.AhpUnknownMessageOrigin,
                Message = "Dropped a message with an unknown origin kind.",
            });
            return;
        }

        if (originKind == "tool")
        {
            // Tool outputs are carried by toolCall response parts.
            return;
        }

        TrajectoryRole role;
        if (originKind == "user")
        {
            role = TrajectoryRole.User;
        }
        else if (originKind is "agent" or "assistant")
        {
            role = TrajectoryRole.Assistant;
        }
        else if (originKind is "system" or "systemNotification")
        {
            role = TrajectoryRole.Assistant;
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.AhpSystemAsAssistant,
                Message = "Mapped a system message origin to assistant.",
            });
        }
        else
        {
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.AhpUnknownMessageOrigin,
                Message = $"Dropped a message with unknown origin kind '{originKind}'.",
            });
            return;
        }

        var text = GetString(message, "text") ?? string.Empty;
        if (string.IsNullOrEmpty(text))
        {
            return;
        }

        if (message.TryGetProperty("model", out var messageModel) &&
            messageModel.ValueKind == JsonValueKind.Object)
        {
            var modelId = NonEmpty(GetString(messageModel, "id"));
            if (modelId is not null)
            {
                model ??= modelId;
            }
        }

        emit(new DecodedEvent
        {
            Kind = DecodedEventKind.Message,
            Role = role,
            Content = text,
            Timestamp = timestamp,
            NativeRecordId = turnId,
            Model = model,
            ComponentIndex = 0,
        });
    }

    private static void EmitResponseParts(
        JsonElement parts,
        string? turnId,
        DateTimeOffset? timestamp,
        Action<DecodedEvent> emit,
        List<TrajectoryDiagnostic> diagnostics)
    {
        var markdownBuffer = new List<(string Id, string Content)>();

        void FlushMarkdown()
        {
            if (markdownBuffer.Count == 0)
            {
                return;
            }

            var content = string.Concat(markdownBuffer.Select(static part => part.Content));
            var nativeId = NonEmpty(markdownBuffer[0].Id) ?? turnId;
            markdownBuffer.Clear();
            if (string.IsNullOrEmpty(content))
            {
                return;
            }

            emit(new DecodedEvent
            {
                Kind = DecodedEventKind.Message,
                Role = TrajectoryRole.Assistant,
                Content = content,
                Timestamp = timestamp,
                NativeRecordId = nativeId,
                ComponentIndex = 0,
            });
        }

        foreach (var part in parts.EnumerateArray())
        {
            if (part.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            var kind = GetString(part, "kind");
            if (kind == "markdown")
            {
                var id = GetString(part, "id") ?? GetString(part, "partId") ?? string.Empty;
                var content = GetString(part, "content") ?? string.Empty;
                markdownBuffer.Add((id, content));
                continue;
            }

            FlushMarkdown();

            if (kind == "reasoning")
            {
                var id = GetString(part, "id") ?? GetString(part, "partId") ?? turnId;
                var content = GetString(part, "content") ?? string.Empty;
                if (!string.IsNullOrWhiteSpace(content))
                {
                    emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.Reasoning,
                        Role = TrajectoryRole.Reasoning,
                        Content = content,
                        Timestamp = timestamp,
                        NativeRecordId = id,
                        ComponentIndex = 0,
                    });
                }

                continue;
            }

            if (kind == "toolCall")
            {
                EmitToolCall(part, timestamp, emit);
                continue;
            }

            if (kind == "inputRequest")
            {
                diagnostics.Add(new TrajectoryDiagnostic
                {
                    Code = DiagnosticCodes.AhpInputRequestSkipped,
                    Message = "Skipped an inputRequest response part.",
                });
                continue;
            }

            if (kind is "resource" or "systemNotification")
            {
                // Non-identity meta; ignore body for v1.
                continue;
            }
        }

        FlushMarkdown();
    }

    private static void EmitToolCall(
        JsonElement part,
        DateTimeOffset? timestamp,
        Action<DecodedEvent> emit)
    {
        if (!part.TryGetProperty("toolCall", out var toolCall) ||
            toolCall.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        var toolCallId = GetString(toolCall, "toolCallId");
        var toolName = GetString(toolCall, "toolName");
        var argumentsJson = ToolArgumentsJson(toolCall);

        emit(new DecodedEvent
        {
            Kind = DecodedEventKind.ToolCall,
            Role = TrajectoryRole.Assistant,
            ToolCallId = toolCallId,
            ToolName = toolName,
            ToolArgumentsJson = argumentsJson,
            Timestamp = timestamp,
            NativeRecordId = toolCallId,
            ComponentIndex = 0,
        });

        var status = GetString(toolCall, "status");
        var success = GetBoolean(toolCall, "success");
        var isTerminal = status is "completed" or "cancelled" or "denied" or "error";
        if (!isTerminal && success is null)
        {
            return;
        }

        var isError = success == false ||
            status is "cancelled" or "denied" or "error";
        var resultContent = ToolResultContent(toolCall, isError);
        // Always emit a result for terminal tool states so cancelled/denied
        // never look like successful completion by omission.
        if (isTerminal || success is not null)
        {
            emit(new DecodedEvent
            {
                Kind = DecodedEventKind.ToolResult,
                Role = TrajectoryRole.Tool,
                ToolCallId = toolCallId,
                ToolName = toolName,
                Content = resultContent,
                IsError = isError,
                Timestamp = timestamp,
                NativeRecordId = toolCallId,
                ComponentIndex = 0,
            });
        }
    }

    private static string ToolArgumentsJson(JsonElement toolCall)
    {
        if (toolCall.TryGetProperty("parameters", out var parameters) &&
            parameters.ValueKind is JsonValueKind.Object or JsonValueKind.Array)
        {
            return CompactJson(parameters);
        }

        var toolInput = GetString(toolCall, "toolInput");
        if (!string.IsNullOrEmpty(toolInput))
        {
            return toolInput;
        }

        return "{}";
    }

    private static string ToolResultContent(JsonElement toolCall, bool isError)
    {
        if (toolCall.TryGetProperty("content", out var content) &&
            content.ValueKind == JsonValueKind.Array)
        {
            var parts = new List<string>();
            foreach (var block in content.EnumerateArray())
            {
                if (block.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }

                var type = GetString(block, "type");
                if (type is "text" or null)
                {
                    var text = GetString(block, "text");
                    if (!string.IsNullOrEmpty(text))
                    {
                        parts.Add(text);
                    }
                }
            }

            if (parts.Count > 0)
            {
                return string.Join("\n", parts);
            }
        }

        if (toolCall.TryGetProperty("structuredContent", out var structured) &&
            structured.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined))
        {
            return CompactJson(structured);
        }

        var pastTense = GetString(toolCall, "pastTenseMessage");
        if (!string.IsNullOrEmpty(pastTense))
        {
            return pastTense;
        }

        if (isError)
        {
            var reasonMessage = GetString(toolCall, "reasonMessage");
            if (!string.IsNullOrEmpty(reasonMessage))
            {
                return reasonMessage;
            }

            var reason = GetString(toolCall, "reason");
            if (!string.IsNullOrEmpty(reason))
            {
                return reason;
            }

            return "cancelled";
        }

        return string.Empty;
    }

    private static void ValidateProtocolVersion(
        JsonElement root,
        List<TrajectoryDiagnostic> diagnostics)
    {
        if (!root.TryGetProperty("ahpProtocolVersion", out var versionElement) ||
            versionElement.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
        {
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.AhpVersionMissing,
                Message = "Snapshot lacks ahpProtocolVersion; assumed pinned 0.7.x.",
            });
            return;
        }

        if (versionElement.ValueKind != JsonValueKind.String)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "AHP ahpProtocolVersion must be a string.");
        }

        var version = versionElement.GetString() ?? string.Empty;
        if (!IsCompatibleAhpVersion(version))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                $"Unsupported AHP protocol version '{version}'. Expected 0.7.x.");
        }
    }

    internal static bool IsCompatibleAhpVersion(string version)
    {
        // Allow 0.7.x (optional pre-release suffix).
        if (string.IsNullOrEmpty(version))
        {
            return false;
        }

        var core = version.Split('-', 2)[0];
        var parts = core.Split('.');
        return parts.Length >= 2 &&
            parts[0] == "0" &&
            parts[1] == "7" &&
            parts.All(static part => part.Length > 0 && part.All(char.IsDigit));
    }

    private static string? OriginKind(JsonElement message)
    {
        if (!message.TryGetProperty("origin", out var origin) ||
            origin.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        return GetString(origin, "kind");
    }

    private static string? FirstWorkingDirectoryPath(JsonElement chat, JsonElement? session)
    {
        foreach (var source in new[] { chat, session })
        {
            if (source is not { } element)
            {
                continue;
            }

            if (!element.TryGetProperty("workingDirectories", out var dirs) ||
                dirs.ValueKind != JsonValueKind.Array)
            {
                continue;
            }

            foreach (var dir in dirs.EnumerateArray())
            {
                if (dir.ValueKind != JsonValueKind.String)
                {
                    continue;
                }

                var uri = dir.GetString();
                if (string.IsNullOrEmpty(uri))
                {
                    continue;
                }

                if (uri.StartsWith("file://", StringComparison.Ordinal))
                {
                    var path = uri["file://".Length..];
                    // file:///workspace/demo → /workspace/demo
                    return path.Length > 0 ? path : uri;
                }

                return uri;
            }
        }

        return null;
    }

    private static DateTimeOffset? ParseTimestamp(JsonElement? value)
    {
        if (value is null || value.Value.ValueKind != JsonValueKind.String)
        {
            return null;
        }

        var text = value.Value.GetString();
        return DateTimeOffset.TryParse(
            text,
            CultureInfo.InvariantCulture,
            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
            out var parsed)
            ? parsed
            : null;
    }

    private static int CompareUtf8(string left, string right)
    {
        var leftBytes = Utf8.GetBytes(left);
        var rightBytes = Utf8.GetBytes(right);
        return leftBytes.AsSpan().SequenceCompareTo(rightBytes);
    }

    private static JsonElement? GetProperty(JsonElement element, string name) =>
        element.TryGetProperty(name, out var property) ? property : null;

    private static string? GetString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property) &&
        property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;

    private static bool? GetBoolean(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property))
        {
            return null;
        }

        return property.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => null,
        };
    }

    private static long? GetInt64(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property) ||
            property.ValueKind != JsonValueKind.Number ||
            !property.TryGetInt64(out var value))
        {
            return null;
        }

        return value;
    }

    private static string? NonEmpty(string? value) =>
        string.IsNullOrEmpty(value) ? null : value;

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

    private static TrajectoryNormalizationException InvalidAhpSnapshot() =>
        new(
            NormalizationErrorCode.InvalidInput,
            "AHP snapshot must be a JSON object with a chat object (Shape A export).");
}

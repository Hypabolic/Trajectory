using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Adapters.Hermes;

/// <summary>
/// Decodes Hermes session exports (message-row arrays or session envelopes).
/// Transcript normalization is byte-oriented and has no SQLite dependency.
/// </summary>
internal sealed class HermesJsonSourceAdapter : ISourceAdapter
{
    private const string ContentJsonPrefix = "\u0000json:";

    public TrajectorySource Source => TrajectorySource.Hermes;

    public DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8)
    {
        var diagnostics = new List<TrajectoryDiagnostic>();
        var events = new List<DecodedEvent>();
        var parsed = ParseTranscript(transcriptUtf8);

        // Soft-deleted rows (active = 0/false) are rewound history Hermes itself
        // excludes from replay; drop them before ordering and call/result linking.
        var rows = OrderRows(parsed.Messages
            .Where(static row => !IsInactive(row))
            .ToArray());
        var callsByRow = PlanToolCalls(rows, diagnostics);

        for (var index = 0; index < rows.Count; index++)
        {
            var row = rows[index];
            var timestamp = HermesTimestamp(GetProperty(row, "timestamp"));
            var nativeId = RowId(row);
            var componentIndex = 0;

            void Emit(DecodedEvent decoded)
            {
                events.Add(decoded with
                {
                    NativeRecordId = nativeId is null ? null : nativeId.Value.Text,
                    SourceSequence = nativeId?.Numeric,
                    SourceOffset = nativeId is null ? index : null,
                    SourceAnchorKind = nativeId is null ? SourceAnchorKind.Ordinal : null,
                    ComponentIndex = componentIndex++,
                });
            }

            var role = GetString(row, "role");
            if (role == "user")
            {
                var content = ContentText(GetProperty(row, "content"));
                if (!string.IsNullOrEmpty(content))
                {
                    Emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.Message,
                        Role = TrajectoryRole.User,
                        Content = content,
                        Timestamp = timestamp,
                        ComponentIndex = 0,
                    });
                }

                continue;
            }

            if (role == "assistant")
            {
                var reasoning = ReasoningText(row);
                if (!string.IsNullOrEmpty(reasoning))
                {
                    Emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.Reasoning,
                        Role = TrajectoryRole.Reasoning,
                        Content = reasoning,
                        Timestamp = timestamp,
                        ComponentIndex = 0,
                    });
                }

                var content = ContentText(GetProperty(row, "content"));
                if (!string.IsNullOrEmpty(content))
                {
                    Emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.Message,
                        Role = TrajectoryRole.Assistant,
                        Content = content,
                        Timestamp = timestamp,
                        ComponentIndex = 0,
                    });
                }

                foreach (var call in callsByRow.GetValueOrDefault(index) ?? [])
                {
                    Emit(new DecodedEvent
                    {
                        Kind = DecodedEventKind.ToolCall,
                        Role = TrajectoryRole.Assistant,
                        ToolCallId = call.Id,
                        ToolName = call.Name,
                        ToolArgumentsJson = call.Args,
                        Timestamp = timestamp,
                        ComponentIndex = 0,
                    });
                }

                continue;
            }

            if (role == "tool")
            {
                Emit(new DecodedEvent
                {
                    Kind = DecodedEventKind.ToolResult,
                    Role = TrajectoryRole.Tool,
                    ToolCallId = GetString(row, "tool_call_id"),
                    ToolName = GetString(row, "tool_name"),
                    Content = ContentText(GetProperty(row, "content")),
                    Timestamp = timestamp,
                    ComponentIndex = 0,
                });
            }
            // Other roles (e.g. injected system rows) are harness transport noise.
        }

        var session = parsed.Session;
        string? model = null;
        string? cwd = null;
        DateTimeOffset? createdAt = null;
        if (session is { } sessionElement)
        {
            model = NonEmpty(GetString(sessionElement, "model"));
            cwd = NonEmpty(GetString(sessionElement, "cwd"));
            createdAt = HermesTimestamp(GetProperty(sessionElement, "started_at"));
        }

        return new DecodedSession
        {
            Context = new DecodedSessionContext
            {
                Source = TrajectorySource.Hermes,
                SourceName = "hermes",
                SourceGroupId = ResolveGroupId(session, parsed.Messages),
                Cwd = cwd,
                Model = model,
                CreatedAt = createdAt,
            },
            Events = events,
            ModelInvocations = [],
            Diagnostics = diagnostics,
        };
    }

    private static ParsedHermesTranscript ParseTranscript(ReadOnlyMemory<byte> transcriptUtf8)
    {
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(transcriptUtf8);
        }
        catch (JsonException)
        {
            throw InvalidHermesTranscript();
        }

        using (document)
        {
            var root = document.RootElement;
            if (root.ValueKind == JsonValueKind.Array)
            {
                var messages = new List<JsonElement>();
                foreach (var item in root.EnumerateArray())
                {
                    if (item.ValueKind != JsonValueKind.Object)
                    {
                        throw InvalidHermesTranscript();
                    }

                    messages.Add(item.Clone());
                }

                return new ParsedHermesTranscript(null, messages);
            }

            if (root.ValueKind == JsonValueKind.Object &&
                root.TryGetProperty("messages", out var messagesElement) &&
                messagesElement.ValueKind == JsonValueKind.Array)
            {
                var messages = new List<JsonElement>();
                foreach (var item in messagesElement.EnumerateArray())
                {
                    if (item.ValueKind != JsonValueKind.Object)
                    {
                        throw InvalidHermesTranscript();
                    }

                    messages.Add(item.Clone());
                }

                JsonElement? session = null;
                if (root.TryGetProperty("session", out var sessionElement) &&
                    sessionElement.ValueKind == JsonValueKind.Object)
                {
                    session = sessionElement.Clone();
                }

                return new ParsedHermesTranscript(session, messages);
            }
        }

        throw InvalidHermesTranscript();
    }

    private static IReadOnlyList<JsonElement> OrderRows(IReadOnlyList<JsonElement> rows)
    {
        if (!rows.All(static row =>
                row.TryGetProperty("id", out var id) &&
                id.ValueKind == JsonValueKind.Number))
        {
            return rows;
        }

        return rows
            .Select(static (row, index) => (row, index))
            .OrderBy(static item => item.row.GetProperty("id").GetInt64())
            .ThenBy(static item => item.index)
            .Select(static item => item.row)
            .ToArray();
    }

    private static Dictionary<int, List<HermesToolCall>> PlanToolCalls(
        IReadOnlyList<JsonElement> rows,
        List<TrajectoryDiagnostic> diagnostics)
    {
        var plan = new Dictionary<int, List<HermesToolCall>>();
        for (var index = 0; index < rows.Count; index++)
        {
            var row = rows[index];
            if (GetString(row, "role") != "assistant")
            {
                continue;
            }

            var calls = RowToolCalls(row, index, diagnostics);
            if (calls.Count == 0)
            {
                continue;
            }

            var idless = calls.Where(static call => call.Id is null).ToArray();
            if (idless.Length > 0)
            {
                var claimed = calls
                    .Where(static call => call.Id is not null)
                    .Select(static call => call.Id!)
                    .ToHashSet(StringComparer.Ordinal);
                var available = new List<string>();
                for (var cursor = index + 1; cursor < rows.Count; cursor++)
                {
                    var next = rows[cursor];
                    if (GetString(next, "role") != "tool")
                    {
                        break;
                    }

                    var toolCallId = GetString(next, "tool_call_id");
                    if (!string.IsNullOrEmpty(toolCallId) && !claimed.Contains(toolCallId))
                    {
                        available.Add(toolCallId);
                    }
                }

                if (available.Count == idless.Length)
                {
                    for (var position = 0; position < idless.Length; position++)
                    {
                        idless[position].Id = available[position];
                    }
                }
            }

            plan[index] = calls;
        }

        return plan;
    }

    private static List<HermesToolCall> RowToolCalls(
        JsonElement row,
        int index,
        List<TrajectoryDiagnostic> diagnostics)
    {
        if (!row.TryGetProperty("tool_calls", out var raw) ||
            raw.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
        {
            return [];
        }

        JsonElement toolCalls = raw;
        if (raw.ValueKind == JsonValueKind.String)
        {
            var text = raw.GetString();
            if (string.IsNullOrEmpty(text))
            {
                return [];
            }

            try
            {
                using var document = JsonDocument.Parse(text);
                toolCalls = document.RootElement.Clone();
            }
            catch (JsonException)
            {
                diagnostics.Add(new TrajectoryDiagnostic
                {
                    Code = DiagnosticCodes.InvalidJsonLine,
                    Message = $"Skipped undecodable tool_calls on message {index + 1}.",
                    InputLine = index + 1,
                });
                return [];
            }
        }

        if (toolCalls.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        var calls = new List<HermesToolCall>();
        foreach (var entry in toolCalls.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            JsonElement? fn = null;
            if (entry.TryGetProperty("function", out var function) &&
                function.ValueKind == JsonValueKind.Object)
            {
                fn = function;
            }

            var name = FirstString(
                fn is { } functionElement ? GetString(functionElement, "name") : null,
                GetString(entry, "name"));
            // Codex Responses providers persist call_id alongside or instead of id.
            var id = FirstString(GetString(entry, "id"), GetString(entry, "call_id"));
            JsonElement? argsElement = null;
            if (fn is { } fnElement && fnElement.TryGetProperty("arguments", out var fnArgs))
            {
                argsElement = fnArgs;
            }
            else if (entry.TryGetProperty("arguments", out var entryArgs))
            {
                argsElement = entryArgs;
            }

            string args;
            if (argsElement is { } argsValue &&
                argsValue.ValueKind == JsonValueKind.String &&
                !string.IsNullOrEmpty(argsValue.GetString()))
            {
                args = argsValue.GetString()!;
            }
            else if (argsElement is { } present)
            {
                args = CompactJson(present);
            }
            else
            {
                args = "{}";
            }

            calls.Add(new HermesToolCall
            {
                Id = id,
                Name = name,
                Args = args,
            });
        }

        return calls;
    }

    private static string ContentText(JsonElement? content)
    {
        if (content is null || content.Value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
        {
            return string.Empty;
        }

        var element = content.Value;
        if (element.ValueKind == JsonValueKind.String)
        {
            var text = element.GetString() ?? string.Empty;
            if (text.StartsWith(ContentJsonPrefix, StringComparison.Ordinal))
            {
                var encoded = text[ContentJsonPrefix.Length..];
                try
                {
                    using var document = JsonDocument.Parse(encoded);
                    return ContentText(document.RootElement.Clone());
                }
                catch (JsonException)
                {
                    return encoded;
                }
            }

            return text;
        }

        if (element.ValueKind == JsonValueKind.Array)
        {
            return BlocksText(element);
        }

        if (element.ValueKind == JsonValueKind.Object)
        {
            return CompactJson(element);
        }

        return element.GetRawText();
    }

    private static string BlocksText(JsonElement content)
    {
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

    private static string ReasoningText(JsonElement row)
    {
        var reasoningContent = GetString(row, "reasoning_content");
        if (!string.IsNullOrWhiteSpace(reasoningContent))
        {
            return reasoningContent;
        }

        var reasoning = GetString(row, "reasoning");
        return string.IsNullOrWhiteSpace(reasoning) ? string.Empty : reasoning;
    }

    private static DateTimeOffset? HermesTimestamp(JsonElement? value)
    {
        if (value is null)
        {
            return null;
        }

        var element = value.Value;
        if (element.ValueKind == JsonValueKind.Number && element.TryGetDouble(out var number) &&
            number > 0 && !double.IsNaN(number) && !double.IsInfinity(number))
        {
            var milliseconds = number > 1e11 ? number : number * 1_000d;
            try
            {
                return DateTimeOffset.FromUnixTimeMilliseconds(
                    (long)Math.Round(milliseconds, MidpointRounding.AwayFromZero));
            }
            catch (ArgumentOutOfRangeException)
            {
                return null;
            }
        }

        if (element.ValueKind != JsonValueKind.String)
        {
            return null;
        }

        var text = element.GetString();
        return DateTimeOffset.TryParse(
            text,
            CultureInfo.InvariantCulture,
            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
            out var parsed)
            ? parsed
            : null;
    }

    private static RowIdentity? RowId(JsonElement row)
    {
        if (!row.TryGetProperty("id", out var id))
        {
            return null;
        }

        if (id.ValueKind == JsonValueKind.Number && id.TryGetInt64(out var numeric))
        {
            return new RowIdentity(numeric.ToString(CultureInfo.InvariantCulture), numeric);
        }

        if (id.ValueKind == JsonValueKind.String)
        {
            var text = id.GetString();
            if (!string.IsNullOrEmpty(text))
            {
                return new RowIdentity(text, null);
            }
        }

        return null;
    }

    private static string? ResolveGroupId(
        JsonElement? session,
        IReadOnlyList<JsonElement> messages)
    {
        if (session is { } sessionElement)
        {
            var sessionId = GetString(sessionElement, "id");
            if (!string.IsNullOrEmpty(sessionId))
            {
                return sessionId;
            }
        }

        foreach (var row in messages)
        {
            var sessionId = GetString(row, "session_id");
            if (!string.IsNullOrEmpty(sessionId))
            {
                return sessionId;
            }
        }

        return null;
    }

    private static bool IsInactive(JsonElement row)
    {
        if (!row.TryGetProperty("active", out var active))
        {
            return false;
        }

        return active.ValueKind switch
        {
            JsonValueKind.False => true,
            JsonValueKind.Number => active.TryGetInt64(out var value) && value == 0,
            _ => false,
        };
    }

    private static JsonElement? GetProperty(JsonElement element, string name) =>
        element.TryGetProperty(name, out var property) ? property : null;

    private static string? GetString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property) &&
        property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;

    private static string? FirstString(params string?[] values)
    {
        foreach (var value in values)
        {
            if (!string.IsNullOrEmpty(value))
            {
                return value;
            }
        }

        return null;
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

    private static TrajectoryNormalizationException InvalidHermesTranscript() =>
        new(
            NormalizationErrorCode.InvalidInput,
            "Hermes transcript must be a JSON array of session-store message rows or an object with a messages array.");

    private sealed class HermesToolCall
    {
        public string? Id { get; set; }
        public string? Name { get; init; }
        public required string Args { get; init; }
    }

    private sealed record ParsedHermesTranscript(
        JsonElement? Session,
        IReadOnlyList<JsonElement> Messages);

    private readonly record struct RowIdentity(string Text, long? Numeric);
}

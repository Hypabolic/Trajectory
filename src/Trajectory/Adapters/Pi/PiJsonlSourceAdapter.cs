using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Json;

namespace Trajectory.Adapters.Pi;

/// <summary>Normalizes Pi session JSONL, including nested messages and tool activity.</summary>
public sealed class PiJsonlSourceAdapter : ISourceAdapter
{
    public const string AdapterName = "pi-jsonl";
    private const string InvalidJsonMessage = "A JSONL record could not be parsed.";
    private const string InvalidTimestampMessage = "A message timestamp could not be parsed.";

    public TrajectorySource Source => TrajectorySource.Pi;

    public TrajectoryIR Parse(
        string transcript,
        SourceContext? context,
        NormalizationOptions options)
    {
        ArgumentNullException.ThrowIfNull(transcript);
        ArgumentNullException.ThrowIfNull(options);

        var diagnostics = new List<TrajectoryDiagnostic>();
        var candidates = new List<MessageCandidate>();
        var effectiveContext = context ?? options.SourceContext;
        string? groupId = effectiveContext?.GroupId;
        DateTimeOffset? sessionTimestamp = null;
        string? cwd = null;
        string? gitBranch = null;
        string? model = null;
        var lineNumber = 0;
        var sourceOrder = 0;

        using var reader = new StringReader(transcript);
        while (reader.ReadLine() is { } line)
        {
            lineNumber++;
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            try
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                if (root.ValueKind == JsonValueKind.Array)
                {
                    foreach (var item in root.EnumerateArray())
                    {
                        AddMessage(item, lineNumber, sourceOrder++, candidates, diagnostics);
                    }
                }
                else if (root.ValueKind == JsonValueKind.Object)
                {
                    ReadContainer(
                        root,
                        ref groupId,
                        ref sessionTimestamp,
                        ref cwd,
                        ref gitBranch,
                        ref model);
                    if (TryGetArray(root, out var messageElements, "messages") ||
                        TryGetNestedArray(root, out messageElements, "session", "messages") ||
                        TryGetNestedArray(root, out messageElements, "data", "messages"))
                    {
                        foreach (var item in messageElements.EnumerateArray())
                        {
                            AddMessage(item, lineNumber, sourceOrder++, candidates, diagnostics);
                        }
                    }
                    else if (IsMessageRecord(root))
                    {
                        AddMessage(root, lineNumber, sourceOrder++, candidates, diagnostics);
                    }
                }
            }
            catch (JsonException)
            {
                diagnostics.Add(new TrajectoryDiagnostic(
                    "PI001",
                    options.Strict ? DiagnosticSeverity.Error : DiagnosticSeverity.Warning,
                    InvalidJsonMessage,
                    lineNumber));
            }
        }

        var trajectorySeed = groupId ?? DeterministicIdentity.Create(
            "pi_session",
            candidates.Select(CandidateFingerprint).ToArray());
        groupId ??= DeterministicIdentity.Create("trajectory", trajectorySeed);
        var messageRecords = new List<IRRecord>(candidates.Count);
        for (var index = 0; index < candidates.Count; index++)
        {
            var candidate = candidates[index];
            var absoluteOffset = checked((effectiveContext?.BaseByteOffset ?? 0L) + index);
            if (options.Bounds?.StartByteOffset is { } start && absoluteOffset < start ||
                options.Bounds?.EndByteOffset is { } end && absoluteOffset >= end)
            {
                continue;
            }

            var calls = candidate.ToolCalls.Count == 0
                ? null
                : options.Bounds?.ToolArguments is { } toolArgumentBounds
                    ? candidate.ToolCalls.Select(call => ApplyBounds(call, toolArgumentBounds)).ToArray()
                    : candidate.ToolCalls.ToArray();
            var id = candidate.Id ?? DeterministicIdentity.Create(
                "pi_message",
                trajectorySeed,
                index.ToString(CultureInfo.InvariantCulture),
                CandidateFingerprint(candidate));
            var order = index;
            if (candidate.ToolResult is { } toolResult)
            {
                messageRecords.Add(new ToolResultIR(
                    id,
                    candidate.Timestamp,
                    order,
                    toolResult.ToolCallId,
                    options.Bounds?.ToolResults is { } toolResultBounds
                        ? TruncateToolResult(toolResult.Content ?? string.Empty, toolResultBounds)
                        : toolResult.Content ?? string.Empty,
                    toolResult.Name,
                    toolResult.IsError));
            }
            else if (calls is not null)
            {
                messageRecords.Add(new AssistantToolCallsIR(
                    id,
                    candidate.Timestamp,
                    order,
                    candidate.Content ?? string.Empty,
                    calls));
            }
            else
            {
                messageRecords.Add(new MessageIR(
                    id,
                    candidate.Role,
                    candidate.Timestamp,
                    order,
                    candidate.Content ?? string.Empty));
            }
        }

        var startedAt = messageRecords.Select(static m => m.Timestamp).Where(static t => t.HasValue)
            .Select(static t => t!.Value).DefaultIfEmpty().Min();
        var hasTimestamps = messageRecords.Any(static m => m.Timestamp.HasValue);
        model ??= candidates.Select(static candidate =>
                candidate.Metadata is not null &&
                candidate.Metadata.TryGetValue("model", out var value) ? value : null)
            .FirstOrDefault(static value => value is not null);

        if (options.Strict)
        {
            for (var index = 0; index < diagnostics.Count; index++)
            {
                if (diagnostics[index].Severity == DiagnosticSeverity.Warning)
                {
                    diagnostics[index] = diagnostics[index] with { Severity = DiagnosticSeverity.Error };
                }
            }
        }

        var records = new List<IRRecord>(messageRecords.Count + 1)
        {
            new MetaIR(
                DeterministicIdentity.Create("meta", groupId),
                hasTimestamps ? startedAt : sessionTimestamp,
                -1,
                AdapterName,
                cwd,
                gitBranch,
                model)
        };
        records.AddRange(messageRecords);
        return new TrajectoryIR(
            AdapterName,
            groupId,
            records,
            diagnostics,
            new AppliedNormalizationConfig(
                options.Bounds,
                options.Filters,
                effectiveContext,
                options.Strict));
    }

    public NormalizationResult Normalize(
        string transcript,
        NormalizationOptions? options = null,
        SourceContext? context = null)
    {
        var trajectory = Parse(transcript, context, options ?? new NormalizationOptions());
        return new NormalizationResult(trajectory, trajectory.Diagnostics);
    }

    private static void ReadContainer(
        JsonElement root,
        ref string? groupId,
        ref DateTimeOffset? timestamp,
        ref string? cwd,
        ref string? gitBranch,
        ref string? model)
    {
        var type = GetString(root, "type");
        var isContainer = HasProperty(root, "messages") ||
            type is "session" or "session_start" or "sessionStart" ||
            HasProperty(root, "session");
        if (!isContainer)
        {
            return;
        }

        var container = TryGetProperty(root, out var nestedSession, "session") &&
            nestedSession.ValueKind == JsonValueKind.Object
            ? nestedSession
            : root;
        groupId ??= GetString(container, "session_id", "sessionId", "conversation_id", "conversationId", "id");
        timestamp ??= ParseTimestamp(container, null, 0);
        cwd ??= GetString(container, "cwd", "working_directory", "workingDirectory");
        gitBranch ??= GetString(container, "git_branch", "gitBranch", "branch");
        model ??= GetString(container, "model", "model_id", "modelId");
    }

    private static bool IsMessageRecord(JsonElement element)
    {
        var type = GetString(element, "type", "event");
        return HasProperty(element, "role") ||
            HasProperty(element, "message") ||
            type is "message" or "user" or "assistant" or "system" or "tool" or "toolResult" or "tool_result";
    }

    private static void AddMessage(
        JsonElement record,
        int line,
        int sourceOrder,
        List<MessageCandidate> candidates,
        List<TrajectoryDiagnostic> diagnostics)
    {
        if (record.ValueKind != JsonValueKind.Object)
        {
            diagnostics.Add(new TrajectoryDiagnostic(
                "PI002",
                DiagnosticSeverity.Warning,
                "A message record had an unsupported JSON shape.",
                line));
            return;
        }

        var envelope = record;
        var message = record;
        if (TryGetProperty(record, out var nested, "message") && nested.ValueKind == JsonValueKind.Object)
        {
            message = nested;
        }
        else if (TryGetProperty(record, out nested, "data") && nested.ValueKind == JsonValueKind.Object &&
                 IsMessageRecord(nested))
        {
            message = nested;
        }

        var roleValue = GetString(message, "role", "sender", "author");
        if (roleValue is null &&
            TryGetProperty(message, out var author, "author") &&
            author.ValueKind == JsonValueKind.Object)
        {
            roleValue = GetString(author, "role", "name");
        }

        var role = NormalizeRole(roleValue
            ?? GetString(envelope, "role", "sender", "type")
            ?? TrajectoryRoles.Unknown);
        var contentParts = new List<string>();
        var toolCalls = new List<ToolCallIR>();
        ToolResultCandidate? toolResult = null;

        if (TryGetProperty(message, out var content, "content", "text"))
        {
            ReadContent(content, sourceOrder, toolCalls, contentParts, ref toolResult);
        }

        ReadOpenAiToolCalls(message, sourceOrder, toolCalls);
        if (TryGetProperty(message, out var functionCall, "function_call", "functionCall") &&
            functionCall.ValueKind == JsonValueKind.Object)
        {
            ReadFunctionCall(functionCall, sourceOrder, toolCalls);
        }
        if (role == TrajectoryRoles.Tool || IsToolResult(message))
        {
            role = TrajectoryRoles.Tool;
            toolResult ??= new ToolResultCandidate(
                GetString(message, "toolCallId", "tool_call_id", "call_id") ??
                    DeterministicIdentity.Create(
                        "tool_call",
                        sourceOrder.ToString(CultureInfo.InvariantCulture)),
                GetString(message, "toolName", "tool_name", "name"),
                JoinContent(contentParts),
                GetBoolean(message, "isError", "is_error", "error"));
        }

        var timestamp = ParseTimestamp(message, diagnostics, line)
            ?? ParseTimestamp(envelope, diagnostics, line);
        var metadata = ReadMetadata(envelope, message);
        candidates.Add(new MessageCandidate(
            GetString(envelope, "id", "message_id", "messageId") ??
                GetString(message, "id", "message_id", "messageId"),
            role,
            JoinContent(contentParts),
            timestamp,
            toolCalls,
            toolResult,
            metadata));
    }

    private static void ReadContent(
        JsonElement content,
        int sourceOrder,
        List<ToolCallIR> calls,
        List<string> text,
        ref ToolResultCandidate? result)
    {
        if (content.ValueKind == JsonValueKind.String)
        {
            text.Add(content.GetString()!);
            return;
        }

        if (content.ValueKind == JsonValueKind.Object)
        {
            ReadContentBlock(content, sourceOrder, calls, text, ref result);
            return;
        }

        if (content.ValueKind != JsonValueKind.Array)
        {
            return;
        }

        foreach (var block in content.EnumerateArray())
        {
            if (block.ValueKind == JsonValueKind.String)
            {
                text.Add(block.GetString()!);
            }
            else if (block.ValueKind == JsonValueKind.Object)
            {
                ReadContentBlock(block, sourceOrder, calls, text, ref result);
            }
        }
    }

    private static void ReadContentBlock(
        JsonElement block,
        int sourceOrder,
        List<ToolCallIR> calls,
        List<string> text,
        ref ToolResultCandidate? result)
    {
        var type = GetString(block, "type") ?? string.Empty;
        if (type is "toolCall" or "tool_call" or "function_call" || HasProperty(block, "toolName"))
        {
            var name = GetString(block, "name", "toolName", "tool_name") ?? "unknown";
            var arguments = TryGetProperty(block, out var args, "arguments", "input", "parameters")
                ? Canonicalize(args)
                : "{}";
            var id = GetString(block, "id", "toolCallId", "tool_call_id")
                ?? DeterministicIdentity.Create(
                    "tool_call",
                    sourceOrder.ToString(CultureInfo.InvariantCulture),
                    calls.Count.ToString(CultureInfo.InvariantCulture),
                    name,
                    arguments);
            calls.Add(new ToolCallIR(id, name, arguments));
            return;
        }

        if (type is "toolResult" or "tool_result" || HasProperty(block, "toolCallId"))
        {
            var resultText = GetString(block, "text", "content", "output");
            result = new ToolResultCandidate(
                GetString(block, "toolCallId", "tool_call_id", "call_id")
                    ?? DeterministicIdentity.Create("tool_call", type),
                GetString(block, "toolName", "tool_name", "name"),
                resultText,
                GetBoolean(block, "isError", "is_error", "error"));
            if (!string.IsNullOrEmpty(resultText))
            {
                text.Add(resultText);
            }

            return;
        }

        var value = GetString(block, "text", "content", "thinking");
        if (!string.IsNullOrEmpty(value))
        {
            text.Add(value);
        }
    }

    private static void ReadOpenAiToolCalls(
        JsonElement message,
        int sourceOrder,
        List<ToolCallIR> calls)
    {
        if (!TryGetArray(message, out var toolCalls, "tool_calls", "toolCalls"))
        {
            return;
        }

        foreach (var call in toolCalls.EnumerateArray())
        {
            if (call.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            ReadFunctionCall(call, sourceOrder, calls);
        }
    }

    private static void ReadFunctionCall(
        JsonElement call,
        int sourceOrder,
        List<ToolCallIR> calls)
    {
        var function = TryGetProperty(call, out var nested, "function") &&
            nested.ValueKind == JsonValueKind.Object
            ? nested
            : call;
        var name = GetString(function, "name", "toolName", "tool_name") ?? "unknown";
        var arguments = TryGetProperty(function, out var args, "arguments", "input", "parameters")
            ? args.ValueKind == JsonValueKind.String
                ? CanonicalizeJsonString(args.GetString())
                : Canonicalize(args)
            : "{}";
        var id = GetString(call, "id", "toolCallId", "tool_call_id")
            ?? DeterministicIdentity.Create(
                "tool_call",
                sourceOrder.ToString(CultureInfo.InvariantCulture),
                calls.Count.ToString(CultureInfo.InvariantCulture),
                name,
                arguments);
        calls.Add(new ToolCallIR(id, name, arguments));
    }

    private static IReadOnlyDictionary<string, string>? ReadMetadata(
        JsonElement envelope,
        JsonElement message)
    {
        var metadata = new Dictionary<string, string>(StringComparer.Ordinal);
        AddMetadata(metadata, "model", GetString(message, "model"));
        AddMetadata(metadata, "parent_id", GetString(envelope, "parentId", "parent_id"));
        AddMetadata(metadata, "stop_reason", GetString(message, "stopReason", "stop_reason"));
        return metadata.Count == 0 ? null : metadata;
    }

    private static void AddMetadata(Dictionary<string, string> metadata, string key, string? value)
    {
        if (!string.IsNullOrWhiteSpace(value))
        {
            metadata.Add(key, value);
        }
    }

    private static string NormalizeRole(string role) => role.ToLowerInvariant() switch
    {
        "user" or "human" => TrajectoryRoles.User,
        "assistant" or "agent" or "ai" => TrajectoryRoles.Assistant,
        "system" or "developer" => TrajectoryRoles.System,
        "tool" or "toolresult" or "tool_result" or "function" => TrajectoryRoles.Tool,
        _ => TrajectoryRoles.Unknown
    };

    private static bool IsToolResult(JsonElement message)
    {
        var type = GetString(message, "type");
        return type is "toolResult" or "tool_result" || HasProperty(message, "toolCallId") ||
            HasProperty(message, "tool_call_id");
    }

    private static DateTimeOffset? ParseTimestamp(
        JsonElement element,
        List<TrajectoryDiagnostic>? diagnostics,
        int line)
    {
        if (!TryGetProperty(element, out var value, "timestamp", "created_at", "createdAt", "time"))
        {
            return null;
        }

        DateTimeOffset parsed;
        if (value.ValueKind == JsonValueKind.String &&
            DateTimeOffset.TryParse(
                value.GetString(),
                CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                out parsed))
        {
            return parsed;
        }

        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var unix))
        {
            try
            {
                return unix > 10_000_000_000
                    ? DateTimeOffset.FromUnixTimeMilliseconds(unix)
                    : DateTimeOffset.FromUnixTimeSeconds(unix);
            }
            catch (ArgumentOutOfRangeException)
            {
                // Report the generic timestamp diagnostic below.
            }
        }

        diagnostics?.Add(new TrajectoryDiagnostic(
            "PI003",
            DiagnosticSeverity.Warning,
            InvalidTimestampMessage,
            line));
        return null;
    }

    private static string? JoinContent(List<string> parts) =>
        parts.Count == 0 ? null : string.Join("\n", parts);

    private static string CandidateFingerprint(MessageCandidate candidate)
    {
        var builder = new StringBuilder()
            .Append(candidate.Role).Append('\n')
            .Append(candidate.Content).Append('\n')
            .Append(candidate.Timestamp?.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture));
        foreach (var call in candidate.ToolCalls)
        {
            builder.Append('\n').Append(call.Id).Append('\n').Append(call.Name).Append('\n')
                .Append(call.ArgumentsJson);
        }

        if (candidate.ToolResult is { } result)
        {
            builder.Append('\n').Append(result.ToolCallId).Append('\n').Append(result.Name)
                .Append('\n').Append(result.Content).Append('\n').Append(result.IsError);
        }

        return builder.ToString();
    }

    private static ToolCallIR ApplyBounds(ToolCallIR call, ToolArgumentBounds bounds) =>
        call with
        {
            ArgumentsJson = TruncateUtf8(
                call.ArgumentsJson,
                bounds.MaxBytes,
                bounds.Truncation) ?? string.Empty
        };

    private static string TruncateToolResult(string value, ToolResultBounds bounds)
    {
        if (bounds.MaxCharacters is null || value.Length <= bounds.MaxCharacters.Value)
        {
            return value;
        }

        if (bounds.MaxCharacters <= 0)
        {
            return string.Empty;
        }

        if (bounds.Strategy == ToolResultTruncationStrategy.Head)
        {
            return value[..bounds.MaxCharacters.Value];
        }

        var headLength = (bounds.MaxCharacters.Value + 1) / 2;
        var tailLength = bounds.MaxCharacters.Value - headLength;
        return string.Concat(value.AsSpan(0, headLength), value.AsSpan(value.Length - tailLength));
    }

    private static string? TruncateUtf8(
        string? value,
        int? maxBytes,
        TruncationMode mode)
    {
        if (value is null || maxBytes is null || mode == TruncationMode.None ||
            Encoding.UTF8.GetByteCount(value) <= maxBytes.Value)
        {
            return value;
        }

        if (maxBytes <= 0)
        {
            return string.Empty;
        }

        return mode switch
        {
            TruncationMode.Head => TakeHead(value, maxBytes.Value),
            TruncationMode.Tail => TakeTail(value, maxBytes.Value),
            TruncationMode.HeadAndTail => string.Concat(
                TakeHead(value, maxBytes.Value / 2),
                TakeTail(value, maxBytes.Value - maxBytes.Value / 2)),
            _ => value
        };
    }

    private static string TakeHead(string value, int maxBytes)
    {
        var length = value.Length;
        while (length > 0 && Encoding.UTF8.GetByteCount(value.AsSpan(0, length)) > maxBytes)
        {
            length--;
            if (length > 0 && char.IsHighSurrogate(value[length - 1]))
            {
                length--;
            }
        }

        return value[..length];
    }

    private static string TakeTail(string value, int maxBytes)
    {
        var start = 0;
        while (start < value.Length &&
               Encoding.UTF8.GetByteCount(value.AsSpan(start)) > maxBytes)
        {
            start++;
            if (start < value.Length && char.IsLowSurrogate(value[start]))
            {
                start++;
            }
        }

        return value[start..];
    }

    private static string CanonicalizeJsonString(string? json)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            return "{}";
        }

        try
        {
            using var document = JsonDocument.Parse(json);
            return Canonicalize(document.RootElement);
        }
        catch (JsonException)
        {
            var buffer = new ArrayBufferWriter<byte>();
            using (var writer = new Utf8JsonWriter(buffer))
            {
                writer.WriteStringValue(json);
            }

            return Encoding.UTF8.GetString(buffer.WrittenSpan);
        }
    }

    private static string Canonicalize(JsonElement element)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = new Utf8JsonWriter(buffer))
        {
            WriteCanonical(writer, element);
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static void WriteCanonical(Utf8JsonWriter writer, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (var property in element.EnumerateObject().OrderBy(static p => p.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    WriteCanonical(writer, property.Value);
                }
                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (var item in element.EnumerateArray())
                {
                    WriteCanonical(writer, item);
                }
                writer.WriteEndArray();
                break;
            default:
                element.WriteTo(writer);
                break;
        }
    }

    private static bool HasProperty(JsonElement element, string name) =>
        element.TryGetProperty(name, out _);

    private static bool TryGetArray(JsonElement element, out JsonElement value, params string[] names)
    {
        if (TryGetProperty(element, out value, names) && value.ValueKind == JsonValueKind.Array)
        {
            return true;
        }

        value = default;
        return false;
    }

    private static bool TryGetNestedArray(
        JsonElement element,
        out JsonElement value,
        string objectName,
        string arrayName)
    {
        if (TryGetProperty(element, out var nested, objectName) &&
            nested.ValueKind == JsonValueKind.Object &&
            TryGetArray(nested, out value, arrayName))
        {
            return true;
        }

        value = default;
        return false;
    }

    private static bool TryGetProperty(
        JsonElement element,
        out JsonElement value,
        params string[] names)
    {
        foreach (var name in names)
        {
            if (element.TryGetProperty(name, out value))
            {
                return true;
            }
        }

        value = default;
        return false;
    }

    private static string? GetString(JsonElement element, params string[] names)
    {
        if (!TryGetProperty(element, out var value, names))
        {
            return null;
        }

        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Number => value.GetRawText(),
            _ => null
        };
    }

    private static bool GetBoolean(JsonElement element, params string[] names)
    {
        if (!TryGetProperty(element, out var value, names))
        {
            return false;
        }

        return value.ValueKind == JsonValueKind.True ||
            value.ValueKind == JsonValueKind.String &&
            bool.TryParse(value.GetString(), out var parsed) && parsed;
    }

    private sealed record MessageCandidate(
        string? Id,
        string Role,
        string? Content,
        DateTimeOffset? Timestamp,
        List<ToolCallIR> ToolCalls,
        ToolResultCandidate? ToolResult,
        IReadOnlyDictionary<string, string>? Metadata);

    private sealed record ToolResultCandidate(
        string ToolCallId,
        string? Name,
        string? Content,
        bool IsError);
}

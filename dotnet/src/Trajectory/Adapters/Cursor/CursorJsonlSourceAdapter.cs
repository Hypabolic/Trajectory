using System.Text.Json;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Adapters.Cursor;

internal sealed class CursorJsonlSourceAdapter : ISourceAdapter
{
    public TrajectorySource Source => TrajectorySource.Cursor;

    public DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8, SourceContext sourceContext)
    {
        _ = sourceContext;
        var diagnostics = new List<TrajectoryDiagnostic>();
        var events = new List<DecodedEvent>();
        foreach (var line in ParseJsonLines(transcriptUtf8, diagnostics))
        {
            using var document = line.Document;
            var row = document.RootElement;
            var role = GetString(row, "role");
            var type = GetString(row, "type");
            var componentIndex = 0;
            void Emit(DecodedEvent decoded) => events.Add(decoded with
            {
                SourceOffset = line.ByteOffset,
                SourceAnchorKind = SourceAnchorKind.Byte,
                InputLine = line.Line,
                ComponentIndex = componentIndex++,
            });

            if (role is "user" or "assistant")
            {
                var textParts = new List<string>();
                var tools = new List<JsonElement>();
                if (row.TryGetProperty("message", out var message) && message.ValueKind == JsonValueKind.Object &&
                    message.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.Array)
                {
                    foreach (var part in content.EnumerateArray())
                    {
                        if (part.ValueKind != JsonValueKind.Object) continue;
                        var partType = GetString(part, "type");
                        if (partType == "text")
                        {
                            var text = GetString(part, "text");
                            if (text is not null) textParts.Add(text);
                        }
                        else if (partType == "tool_use" && role == "assistant") tools.Add(part.Clone());
                        else if (partType == "tool_use") { }
                        else if (partType is "image" or "image_url" or "input_image" or "output_image")
                        {
                            diagnostics.Add(new TrajectoryDiagnostic { Code = DiagnosticCodes.ImageContentDropped, Message = $"Dropped image content on a Cursor record on line {line.Line}.", InputLine = line.Line });
                        }
                        else if (!string.IsNullOrEmpty(partType))
                        {
                            diagnostics.Add(new TrajectoryDiagnostic { Code = DiagnosticCodes.UnknownContentPart, Message = $"Skipped an unknown Cursor content part on line {line.Line}.", InputLine = line.Line });
                        }
                    }
                }
                var textBody = string.Join("\n", textParts);
                if (!string.IsNullOrWhiteSpace(textBody))
                {
                    Emit(new DecodedEvent { Kind = DecodedEventKind.Message, Role = role == "user" ? TrajectoryRole.User : TrajectoryRole.Assistant, Content = textBody, ComponentIndex = 0 });
                }
                if (role == "assistant")
                {
                    foreach (var part in tools)
                    {
                        var name = GetString(part, "name");
                        if (string.IsNullOrWhiteSpace(name))
                        {
                            diagnostics.Add(new TrajectoryDiagnostic { Code = DiagnosticCodes.ToolUseMissingName, Message = $"Skipped a Cursor tool_use part without a name on line {line.Line}.", InputLine = line.Line });
                            continue;
                        }
                        var args = part.TryGetProperty("input", out var input) && input.ValueKind == JsonValueKind.Object
                            ? CompactJson(input) : "{}";
                        Emit(new DecodedEvent { Kind = DecodedEventKind.ToolCall, Role = TrajectoryRole.Assistant, ToolName = name, ToolArgumentsJson = args, ComponentIndex = 0 });
                    }
                }
                continue;
            }

            if (type == "turn_ended")
            {
                if (GetString(row, "status") == "error") diagnostics.Add(new TrajectoryDiagnostic { Code = DiagnosticCodes.TurnEndedError, Message = "A Cursor turn ended with an error.", InputLine = line.Line });
                continue;
            }
            if (!string.IsNullOrEmpty(role) || !string.IsNullOrEmpty(type)) diagnostics.Add(new TrajectoryDiagnostic { Code = DiagnosticCodes.UnknownSemanticRecord, Message = $"Skipped an unknown Cursor semantic record on line {line.Line}.", InputLine = line.Line });
        }

        return new DecodedSession
        {
            Context = new DecodedSessionContext { Source = Source, SourceName = "cursor" },
            Events = events,
            ModelInvocations = [],
            Diagnostics = diagnostics,
        };
    }

    private static IReadOnlyList<JsonLine> ParseJsonLines(ReadOnlyMemory<byte> transcript, List<TrajectoryDiagnostic> diagnostics)
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
            if (!lineMemory.Span.IsEmpty && lineMemory.Span[^1] == (byte)'\r') lineMemory = lineMemory[..^1];
            if (!lineMemory.Span.IsEmpty && !lineMemory.Span.ToArray().All(static value => value is (byte)' ' or (byte)'\t' or (byte)'\r'))
            {
                try
                {
                    var document = JsonDocument.Parse(lineMemory);
                    if (document.RootElement.ValueKind != JsonValueKind.Object)
                    {
                        document.Dispose();
                        diagnostics.Add(new TrajectoryDiagnostic { Code = DiagnosticCodes.NonObjectJsonLine, Message = $"Skipped non-object JSON on line {lineNumber}.", InputLine = lineNumber });
                    }
                    else parsed.Add(new JsonLine(document, lineNumber, offset));
                }
                catch (JsonException)
                {
                    diagnostics.Add(new TrajectoryDiagnostic { Code = DiagnosticCodes.InvalidJsonLine, Message = $"Skipped invalid JSON on line {lineNumber}.", InputLine = lineNumber });
                }
            }
            if (newline < 0) break;
            offset += length + 1;
            lineNumber++;
        }
        return parsed;
    }

    private static string? GetString(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static string CompactJson(JsonElement value)
        => CanonicalJson.Relaxed(value);

    private sealed record JsonLine(JsonDocument Document, int Line, long ByteOffset);
}

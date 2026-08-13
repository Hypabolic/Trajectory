using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Hypabolic.Trajectory.Listing;

/// <summary>
/// Cheap, bounded title derivation for listing items (no full normalize).
/// </summary>
internal static class ListingTitle
{
    internal const int MaxScanBytes = 64 * 1024;
    internal const int MaxScanLines = 200;
    internal const int MaxTitleScalars = 120;

    private static readonly string[] NoiseMarkers =
    [
        "# agents.md",
        "<instructions>",
        "</instructions>",
        "<environment_context>",
        "<skills_instructions>",
        "<skills>",
        "<permissions instructions>",
        "<user_instructions>",
        "<turn_context>",
        "<collaboration",
        "filesystem sandboxing",
        "<cwd>",
        "you are a coding agent",
        "you are chatgpt",
        "# claude.md",
        "agenthub instructions",
        "<command-name>",
        "<local-command-caveat>",
        "<task-notification",
    ];

    internal static string? DeriveCodexTitle(string path)
    {
        string? sessionId = null;
        foreach (var row in ScanJsonLines(path))
        {
            using var document = row;
            var root = document.RootElement;
            var recordType = GetString(root, "type");
            var payload = root.TryGetProperty("payload", out var payloadElement) &&
                payloadElement.ValueKind == JsonValueKind.Object
                    ? payloadElement
                    : default;

            if (recordType == "session_meta")
            {
                var id = GetString(payload, "id");
                if (!string.IsNullOrEmpty(id))
                {
                    sessionId = id;
                }

                continue;
            }

            if (recordType == "response_item")
            {
                var role = GetString(payload, "role");
                if (role is "developer" or "system")
                {
                    continue;
                }

                if (role == "user")
                {
                    var text = BlocksToText(payload.ValueKind == JsonValueKind.Object &&
                        payload.TryGetProperty("content", out var content)
                            ? content
                            : default);
                    var title = TitleFromUserText(text);
                    if (title is not null)
                    {
                        return title;
                    }
                }

                continue;
            }

            if (recordType == "event_msg")
            {
                var eventType = GetString(payload, "type");
                if (eventType is "user_message" or "user_prompt" or "message")
                {
                    var text = BlocksToText(GetProperty(payload, "message")) ??
                        BlocksToText(GetProperty(payload, "content")) ??
                        GetString(payload, "text") ??
                        string.Empty;
                    var title = TitleFromUserText(text);
                    if (title is not null)
                    {
                        return title;
                    }
                }
            }
        }

        return sessionId is null ? null : FormatTitle(ShortSessionId(sessionId));
    }

    internal static string? DeriveClaudeTitle(string path)
    {
        string? customTitle = null;
        string? aiTitle = null;
        string? summary = null;
        string? firstUser = null;

        foreach (var row in ScanJsonLines(path))
        {
            using var document = row;
            var root = document.RootElement;
            var recordType = GetString(root, "type");
            switch (recordType)
            {
                case "custom-title":
                    customTitle ??= FormatTitle(
                        GetString(root, "customTitle") ?? GetString(root, "title"));
                    break;
                case "ai-title":
                    aiTitle ??= FormatTitle(
                        GetString(root, "aiTitle") ?? GetString(root, "title"));
                    break;
                case "summary":
                    summary ??= FormatTitle(
                        GetString(root, "summary") ?? GetString(root, "title"));
                    break;
                case "user" when firstUser is null:
                    {
                        if (GetBoolean(root, "isMeta") || GetBoolean(root, "isSidechain"))
                        {
                            break;
                        }

                        var text = string.Empty;
                        if (root.TryGetProperty("message", out var message) &&
                            message.ValueKind == JsonValueKind.Object)
                        {
                            text = BlocksToText(
                                message.TryGetProperty("content", out var content)
                                    ? content
                                    : default) ?? string.Empty;
                        }

                        if (string.IsNullOrEmpty(text))
                        {
                            text = BlocksToText(GetProperty(root, "content")) ?? string.Empty;
                        }

                        if (text.Contains("tool_use_id", StringComparison.Ordinal))
                        {
                            break;
                        }

                        firstUser = TitleFromUserText(text);
                        break;
                    }
            }
        }

        return customTitle ?? aiTitle ?? summary ?? firstUser;
    }

    internal static string? DeriveGenericUserTitle(string path)
    {
        foreach (var row in ScanJsonLines(path))
        {
            using var document = row;
            var root = document.RootElement;
            JsonElement message = default;
            var hasMessage = root.TryGetProperty("message", out message) &&
                message.ValueKind == JsonValueKind.Object;
            var role = hasMessage ? GetString(message, "role") : null;
            role ??= GetString(root, "role");
            if (role != "user")
            {
                continue;
            }

            var text = string.Empty;
            if (hasMessage)
            {
                text = BlocksToText(
                    message.TryGetProperty("content", out var content)
                        ? content
                        : default) ?? string.Empty;
            }

            if (string.IsNullOrEmpty(text))
            {
                text = BlocksToText(GetProperty(root, "content")) ?? string.Empty;
            }

            var title = TitleFromUserText(text);
            if (title is not null)
            {
                return title;
            }
        }

        return null;
    }

    internal static string? DeriveCursorTitle(string path)
    {
        foreach (var row in ScanJsonLines(path))
        {
            using var document = row;
            var root = document.RootElement;
            if (GetString(root, "role") != "user" ||
                !root.TryGetProperty("message", out var message) ||
                message.ValueKind != JsonValueKind.Object ||
                !message.TryGetProperty("content", out var content) ||
                content.ValueKind != JsonValueKind.Array)
            {
                continue;
            }

            var text = string.Join("\n", content.EnumerateArray()
                .Where(static part => part.ValueKind == JsonValueKind.Object && GetString(part, "type") == "text")
                .Select(static part => GetString(part, "text"))
                .Where(static value => value is not null));
            var title = TitleFromUserText(text);
            if (title is not null) return title;
        }

        return null;
    }

    private static IEnumerable<JsonDocument> ScanJsonLines(string path)
    {
        FileStream stream;
        try
        {
            stream = new FileStream(
                path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            yield break;
        }

        using (stream)
        using (var reader = new StreamReader(
                   new LimitedReadStream(stream, MaxScanBytes),
                   Encoding.UTF8,
                   detectEncodingFromByteOrderMarks: true,
                   bufferSize: 4096,
                   leaveOpen: false))
        {
            var lines = 0;
            while (lines < MaxScanLines)
            {
                string? line;
                try
                {
                    line = reader.ReadLine();
                }
                catch (IOException)
                {
                    yield break;
                }

                if (line is null)
                {
                    yield break;
                }

                lines++;
                if (string.IsNullOrWhiteSpace(line))
                {
                    continue;
                }

                JsonDocument document;
                try
                {
                    document = JsonDocument.Parse(line);
                }
                catch (JsonException)
                {
                    continue;
                }

                yield return document;
            }
        }
    }

    private static string? TitleFromUserText(string? text) =>
        text is null || IsListingNoise(text) ? null : FormatTitle(text);

    internal static string? FormatTitle(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return null;
        }

        var collapsed = Regex.Replace(text.Trim(), @"\s+", " ");
        if (collapsed.Length == 0)
        {
            return null;
        }

        var builder = new StringBuilder();
        var count = 0;
        foreach (var rune in collapsed.EnumerateRunes())
        {
            if (count >= MaxTitleScalars)
            {
                break;
            }

            builder.Append(rune);
            count++;
        }

        return builder.ToString();
    }

    private static string ShortSessionId(string id)
    {
        var dash = id.IndexOf('-');
        if (dash >= 8)
        {
            return id[..8];
        }

        return id.Length <= 8 ? id : id[..8];
    }

    internal static bool IsListingNoise(string text)
    {
        var trimmed = text.Trim();
        if (trimmed.Length == 0)
        {
            return true;
        }

        var lower = trimmed.ToLowerInvariant();
        foreach (var marker in NoiseMarkers)
        {
            if (lower.Contains(marker, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return CountXmlishTags(trimmed) >= 3 && trimmed.Length > 80;
    }

    private static int CountXmlishTags(string text)
    {
        var count = 0;
        for (var index = 0; index < text.Length; index++)
        {
            if (text[index] != '<')
            {
                continue;
            }

            var start = index + 1;
            if (start >= text.Length)
            {
                break;
            }

            var first = text[start];
            if (!(char.IsAsciiLetter(first) || first is '/' or '_' or '-'))
            {
                continue;
            }

            var end = text.IndexOf('>', start);
            if (end < 0)
            {
                break;
            }

            var valid = true;
            for (var i = start; i < end; i++)
            {
                var ch = text[i];
                if (!(char.IsAsciiLetterOrDigit(ch) || ch is '/' or '_' or '-'))
                {
                    valid = false;
                    break;
                }
            }

            if (valid)
            {
                count++;
                index = end;
            }
        }

        return count;
    }

    private static string? BlocksToText(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Undefined ||
            value.ValueKind == JsonValueKind.Null)
        {
            return null;
        }

        if (value.ValueKind == JsonValueKind.String)
        {
            return value.GetString();
        }

        if (value.ValueKind == JsonValueKind.Array)
        {
            var parts = new List<string>();
            foreach (var item in value.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.String)
                {
                    var text = item.GetString();
                    if (!string.IsNullOrEmpty(text))
                    {
                        parts.Add(text);
                    }
                }
                else if (item.ValueKind == JsonValueKind.Object)
                {
                    var text = GetString(item, "text") ?? GetString(item, "input_text");
                    if (string.IsNullOrEmpty(text) &&
                        GetString(item, "type") == "input_text")
                    {
                        text = GetString(item, "text");
                    }

                    if (!string.IsNullOrEmpty(text))
                    {
                        parts.Add(text);
                    }
                }
            }

            return parts.Count == 0 ? null : string.Join("\n", parts);
        }

        if (value.ValueKind == JsonValueKind.Object)
        {
            var text = GetString(value, "text");
            if (!string.IsNullOrEmpty(text))
            {
                return text;
            }

            return value.TryGetProperty("content", out var nested)
                ? BlocksToText(nested)
                : null;
        }

        return null;
    }

    private static JsonElement GetProperty(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(name, out var property)
            ? property
            : default;

    private static string? GetString(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(name, out var property) &&
        property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;

    private static bool GetBoolean(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(name, out var property) &&
        property.ValueKind is JsonValueKind.True or JsonValueKind.False &&
        property.GetBoolean();

    /// <summary>Limits how many bytes a reader can pull from an underlying stream.</summary>
    private sealed class LimitedReadStream(Stream inner, long maxBytes) : Stream
    {
        private long _remaining = maxBytes;

        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position
        {
            get => throw new NotSupportedException();
            set => throw new NotSupportedException();
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            if (_remaining <= 0)
            {
                return 0;
            }

            var toRead = (int)Math.Min(count, _remaining);
            var read = inner.Read(buffer, offset, toRead);
            _remaining -= read;
            return read;
        }

        public override int Read(Span<byte> buffer)
        {
            if (_remaining <= 0)
            {
                return 0;
            }

            var toRead = (int)Math.Min(buffer.Length, _remaining);
            var read = inner.Read(buffer[..toRead]);
            _remaining -= read;
            return read;
        }

        public override void Flush()
        {
        }

        public override long Seek(long offset, SeekOrigin origin) =>
            throw new NotSupportedException();

        public override void SetLength(long value) => throw new NotSupportedException();

        public override void Write(byte[] buffer, int offset, int count) =>
            throw new NotSupportedException();

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                inner.Dispose();
            }

            base.Dispose(disposing);
        }
    }
}

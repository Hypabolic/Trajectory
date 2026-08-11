using System.Globalization;
using System.Text.Json;
using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.GrokBuild;

/// <summary>
/// Lists Grok Build sessions under a sessions root:
/// <c>&lt;root&gt;/&lt;cwd-dir&gt;/&lt;session-uuid&gt;/chat_history.jsonl</c>.
/// </summary>
internal sealed class GrokBuildTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.GrokBuild;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        var sessionsRoot = string.IsNullOrWhiteSpace(root)
            ? DefaultSessionsRoot()
            : root;
        if (!Directory.Exists(sessionsRoot))
        {
            return [];
        }

        var items = new List<TrajectoryListing>();
        IEnumerable<string> cwdDirectories;
        try
        {
            cwdDirectories = Directory.EnumerateDirectories(sessionsRoot).ToArray();
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return [];
        }

        foreach (var cwdDirectory in cwdDirectories)
        {
            IEnumerable<string> sessionDirectories;
            try
            {
                sessionDirectories = Directory.EnumerateDirectories(cwdDirectory).ToArray();
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                continue;
            }

            foreach (var sessionDirectory in sessionDirectories)
            {
                var historyPath = Path.Combine(sessionDirectory, "chat_history.jsonl");
                if (!File.Exists(historyPath))
                {
                    continue;
                }

                try
                {
                    var info = new FileInfo(historyPath);
                    var summaryPath = Path.Combine(sessionDirectory, "summary.json");
                    var (updatedAt, title) = ReadSummaryMeta(summaryPath);
                    items.Add(new TrajectoryListing
                    {
                        Id = Path.GetFileName(sessionDirectory)!,
                        Path = Path.GetFullPath(historyPath),
                        UpdatedAt = updatedAt ?? info.LastWriteTimeUtc,
                        Title = title,
                        SizeBytes = info.Length,
                    });
                }
                catch (Exception error) when (
                    error is IOException or UnauthorizedAccessException or JsonException)
                {
                    // Store can change mid-enumeration; skip inaccessible sessions.
                }
            }
        }

        return items.OrderByDescending(static item => item.UpdatedAt)
            .ThenBy(static item => item.Id, StringComparer.Ordinal)
            .ToArray();
    }

    internal static string DefaultSessionsRoot()
    {
        var grokHome = Environment.GetEnvironmentVariable("GROK_HOME")?.Trim();
        if (!string.IsNullOrEmpty(grokHome))
        {
            return Path.Combine(Expand(grokHome), "sessions");
        }

        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        return Path.Combine(home, ".grok", "sessions");
    }

    private static (DateTimeOffset? UpdatedAt, string? Title) ReadSummaryMeta(string path)
    {
        if (!File.Exists(path))
        {
            return (null, null);
        }

        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                return (null, null);
            }

            var title = GetString(root, "generated_title");
            if (string.IsNullOrWhiteSpace(title))
            {
                title = GetString(root, "session_summary");
            }

            if (string.IsNullOrWhiteSpace(title))
            {
                title = null;
            }

            var updatedAt = ParseTimestamp(root, "last_active_at") ??
                ParseTimestamp(root, "updated_at");
            return (updatedAt, title);
        }
        catch (Exception error) when (
            error is IOException or UnauthorizedAccessException or JsonException)
        {
            return (null, null);
        }
    }

    private static DateTimeOffset? ParseTimestamp(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property) ||
            property.ValueKind != JsonValueKind.String)
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

    private static string? GetString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property) &&
        property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;

    private static string Expand(string path) =>
        path.StartsWith("~/") || path == "~"
            ? Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                path == "~" ? string.Empty : path[2..])
            : path;
}
